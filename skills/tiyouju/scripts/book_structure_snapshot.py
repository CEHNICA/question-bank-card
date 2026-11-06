#!/usr/bin/env python3
"""Export one TiYouJu paper and its source pages without mutating question data.

Uses only the standard library. Cached OCR exports retain archive-local page
indices: the public API does not expose a trustworthy chunk-to-paper mapping.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

MAX_JSON = 50 * 1024 * 1024
MAX_IMAGE = 30 * 1024 * 1024
PAPER_FIELDS = (
    "id",
    "name",
    "filename",
    "original_filename",
    "kind",
    "status",
    "material_type",
    "layout_revision",
    "layout_page_epoch",
    "pages",
    "structure",
    "question_groups",
    "pages_version",
    "counts",
    "created_at",
)
QUESTION_FIELDS = (
    "id",
    "number",
    "source_key",
    "source_anchor_seq",
    "source_kind",
    "start_source",
    "content_revision",
    "layout_fingerprint",
    "body_mode",
    "processing_mode",
    "group",
    "section",
    "question_type",
    "regions",
    "regions_changed",
    "question_images",
    "figure_candidates",
    "figures",
    "figure_review",
    "stem",
    "options",
    "text_source",
    "state",
    "flags",
    "edited",
    "approved",
    "approval_valid",
    "approved_by",
    "approval_agent",
    "approved_at",
    "answer",
    "analysis",
    "origin",
    "publication",
)


class SnapshotError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def canonical_id(value):
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise SnapshotError("paper must be a complete UUID") from None


def local_url(value):
    parsed = urllib.parse.urlsplit(value)
    host = (parsed.hostname or "").lower()
    try:
        loopback = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if (
        parsed.scheme != "http"
        or not loopback
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SnapshotError("url must be an HTTP loopback origin without credentials or a path")
    try:
        port = parsed.port
    except ValueError:
        raise SnapshotError("invalid local port") from None
    if port is not None and not 1 <= port <= 65535:
        raise SnapshotError("invalid local port")
    return value.rstrip("/")


def get(opener, url, limit, timeout):
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json,image/*"})
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        raise SnapshotError(f"local API returned HTTP {error.code}; redirects are not followed") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise SnapshotError("cannot read the local API; verify TiYouJu is running and --url is correct") from None
    if len(data) > limit:
        raise SnapshotError("response exceeds the snapshot size limit")
    return data


def selected_pages(spec, available):
    if not spec:
        return sorted(available)
    if not available:
        raise SnapshotError("the paper has no source pages")
    result = set()
    for token in spec.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", token)
        if not match:
            raise SnapshotError("pages must be 1-based numbers or ranges, for example 3-8,40-43")
        first = int(match[1])
        last = int(match[2] or first)
        if first < 1 or last < first or last > max(available) + 1:
            raise SnapshotError("requested page range is outside this paper")
        requested = set(range(first - 1, last))
        if not requested.issubset(available):
            raise SnapshotError("requested page does not exist in this paper")
        result.update(requested)
    return sorted(result)


def inside(root, path):
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise SnapshotError("cache path leaves the explicitly selected paper directory")
    return resolved


def read_limited(path, limit):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise SnapshotError(f"cached file exceeds the size limit: {path.name}")
    return data


def image_extension(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    raise SnapshotError("source page is not a PNG or JPEG image")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(piece)
    return digest.hexdigest()


def cached_archives(root, out):
    archives = []
    whole = root / "mineru_result.zip"
    if whole.is_file():
        archives.append(whole)
    chunks = root / "chunks"
    if chunks.is_dir():
        inside(root, chunks)
        archives.extend(sorted(chunks.glob("*.zip")))
    records = []
    for ordinal, candidate in enumerate(archives, 1):
        path = inside(root, candidate)
        with zipfile.ZipFile(path) as archive:
            entries = [
                entry
                for entry in archive.infolist()
                if entry.filename.endswith("_content_list.json")
                and not entry.filename.endswith("_content_list_v2.json")
            ]
            if len(entries) != 1 or entries[0].file_size > MAX_JSON:
                raise SnapshotError(f"cache has no unique bounded content_list: {path.name}")
            with archive.open(entries[0]) as stream:
                data = stream.read(MAX_JSON + 1)
            if len(data) > MAX_JSON:
                raise SnapshotError(f"cached content exceeds the size limit: {path.name}")
        raw = json.loads(data)
        if not isinstance(raw, list):
            raise SnapshotError(f"cached content is not a list: {path.name}")
        blocks = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict) or type(item.get("page_idx")) is not int or item["page_idx"] < 0:
                continue
            bbox = item.get("bbox")
            if not (
                isinstance(bbox, list)
                and len(bbox) == 4
                and all(type(v) in {int, float} for v in bbox)
                and 0 <= bbox[0] < bbox[2] <= 1000
                and 0 <= bbox[1] < bbox[3] <= 1000
            ):
                bbox = None
            content = item.get("text") or item.get("content") or ""
            block = {
                "archive_seq": index,
                "local_page_idx": item["page_idx"],
                "type": str(item.get("type") or "unknown")[:40],
                "bbox": bbox,
                "text": content if isinstance(content, str) else "",
            }
            if item.get("type") == "table" and isinstance(item.get("table_body"), str):
                block["html"] = item["table_body"]
            blocks.append(block)
        target = out / "cached-blocks" / f"archive-{ordinal:03d}.json"
        target.parent.mkdir(exist_ok=True)
        export = {
            "source_archive": path.relative_to(root).as_posix(),
            "content_sha256": sha256(data),
            "global_page_mapping": None,
            "mapping_status": "unverified_archive_local_coordinates",
            "blocks": blocks,
        }
        write_json(target, export)
        records.append(
            {
                "source": path.relative_to(root).as_posix(),
                "file": target.relative_to(out).as_posix(),
                "block_count": len(blocks),
                "sha256": sha256(target.read_bytes()),
                "global_page_mapping": None,
            }
        )
    return records


def run(args):
    paper_id = canonical_id(args.paper)
    base = local_url(args.url)
    out = Path(args.out).expanduser()
    if not out.is_absolute():
        raise SnapshotError("out must be an absolute path")
    out = out.resolve()
    cache = None
    if args.data_dir:
        supplied = Path(args.data_dir).expanduser()
        if not supplied.is_absolute() or not supplied.is_dir():
            raise SnapshotError("data-dir must be the explicitly authorized absolute paper directory")
        cache = supplied.resolve()
        if cache.name != paper_id:
            raise SnapshotError("data-dir must be named with the selected complete paper UUID")
        if out.is_relative_to(cache) or cache.is_relative_to(out):
            raise SnapshotError("snapshot output must be separate from the paper data directory")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise SnapshotError("out must be a new or empty directory")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    raw = get(opener, f"{base}/api/papers/{paper_id}", MAX_JSON, args.timeout)
    detail = json.loads(raw)
    paper = detail.get("paper") if isinstance(detail, dict) else None
    if not isinstance(paper, dict) or canonical_id(paper.get("id")) != paper_id:
        raise SnapshotError("API response does not identify the requested paper")
    pages = paper.get("pages")
    questions = detail.get("questions")
    if not isinstance(pages, list) or not isinstance(questions, list):
        raise SnapshotError("paper detail has no valid pages/questions lists")
    if any(not isinstance(p, dict) or type(p.get("page_idx")) is not int or p["page_idx"] < 0 for p in pages):
        raise SnapshotError("paper pages have invalid indices")
    available = {p["page_idx"] for p in pages}
    if len(available) != len(pages):
        raise SnapshotError("paper pages contain duplicate indices")
    selected = selected_pages(args.pages, available)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": 1,
        "status": "incomplete",
        "paper_id": paper_id,
        "exported_at_utc": datetime.now(UTC).isoformat(),
        "api_origin": base,
        "page_numbering": "API indices are 0-based; display pages are 1-based",
        "selected_pdf_pages": [p + 1 for p in selected],
        "pages": [],
        "blocks_public_api": False,
        "cached_archives": [],
        "notes": [
            "No question, approval, publication, or cloud-reading mutation was requested.",
            "The local preview API may create derived page image caches.",
            "Cached OCR blocks retain archive-local coordinates; global page mapping is not inferred.",
        ],
    }
    write_json(out / "manifest.json", manifest)
    try:
        exported = {
            "paper": {k: paper[k] for k in PAPER_FIELDS if k in paper},
            "questions": [{k: q[k] for k in QUESTION_FIELDS if k in q} for q in questions if isinstance(q, dict)],
        }
        write_json(out / "paper-detail.json", exported)
        manifest["detail_sha256"] = sha256((out / "paper-detail.json").read_bytes())
        manifest["question_count"] = len(exported["questions"])
        (out / "pages").mkdir(exist_ok=True)
        for page_idx in selected:
            data = None
            origin = "local_api_preview"
            if cache:
                for name in (f"preview_{page_idx}.jpg", f"page_{page_idx}.png"):
                    path = inside(cache, cache / "pages" / name)
                    if path.is_file():
                        data = read_limited(path, MAX_IMAGE)
                        origin = "existing_cache:" + path.relative_to(cache).as_posix()
                        break
            if data is None:
                data = get(opener, f"{base}/api/papers/{paper_id}/pages/{page_idx}/preview", MAX_IMAGE, args.timeout)
            extension = image_extension(data)
            target = out / "pages" / f"page-{page_idx + 1:04d}{extension}"
            target.write_bytes(data)
            manifest["pages"].append(
                {
                    "page_idx": page_idx,
                    "pdf_page": page_idx + 1,
                    "file": target.relative_to(out).as_posix(),
                    "source": origin,
                    "bytes": len(data),
                    "sha256": sha256(data),
                }
            )
            write_json(out / "manifest.json", manifest)
        if cache:
            source = inside(cache, cache / "source.pdf")
            if source.is_file():
                manifest["source_pdf"] = {
                    "file": "source.pdf",
                    "copied_to_snapshot": False,
                    "note": "Source metadata only; original PDF is not copied.",
                    "bytes": source.stat().st_size,
                    "sha256": file_sha256(source),
                }
            manifest["cached_archives"] = cached_archives(cache, out)
        manifest["status"] = "complete"
        write_json(out / "manifest.json", manifest)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        manifest["error"] = str(error)
        write_json(out / "manifest.json", manifest)
        raise
    return {
        "paper_id": paper_id,
        "out": str(out),
        "status": "complete",
        "question_count": manifest["question_count"],
        "exported_pages": len(selected),
        "cached_archives": len(manifest["cached_archives"]),
        "blocks_global_mapping_verified": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper", required=True, help="complete UUID of the explicitly selected paper")
    parser.add_argument("--out", required=True, help="new or empty absolute output directory")
    parser.add_argument("--pages", help="1-based PDF pages/ranges, such as 3-8,40-43; default: all")
    parser.add_argument(
        "--url",
        default=os.environ.get("TIYOUJU_URL", "http://127.0.0.1:8768"),
        help="HTTP loopback origin of the running app",
    )
    parser.add_argument(
        "--data-dir", help="optional explicitly authorized paper UUID directory; reads existing caches only"
    )
    parser.add_argument("--timeout", type=float, default=30, help="per-request timeout in seconds (1-120)")
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 120:
        parser.error("timeout must be between 1 and 120 seconds")
    try:
        result = run(args)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        print(json.dumps({"error": str(error), "status": "failed"}, ensure_ascii=True), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
