"""Original-page question bodies: real ordered crops, never OCR placeholders."""
from __future__ import annotations

import hashlib
import json
import math
import threading
import uuid
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from PIL import Image

from . import imaging


def is_image(question) -> bool:
    return question.body_mode == "source_image"


def valid_regions(paper, regions) -> bool:
    if not isinstance(regions, list) or not 1 <= len(regions) <= 12:
        return False
    pages = {item["page_idx"] for item in paper.pages}
    for region in regions:
        if not isinstance(region, dict) or type(region.get("page_idx")) is not int or region["page_idx"] not in pages:
            return False
        box = region.get("bbox")
        if not isinstance(box, list) or len(box) != 4 or any(
            type(v) not in {float, int} or not math.isfinite(v) or not 0 <= v <= 1000 for v in box
        ) or box[2] - box[0] < 3 or box[3] - box[1] < 3:
            return False
    return True


# The source file's own sha256, remembered against its path, size and mtime.
# Hashing it once per card made every paper detail load re-read the whole PDF
# or photo: a 25-card paper read the same 20 MB twenty-five times.  The stamp
# changes the moment the file does, so a replaced original is re-hashed.
_SOURCE_HASHES: dict[tuple[str, int, int, int], str] = {}
_SOURCE_HASHES_LOCK = threading.Lock()
_SOURCE_HASHES_LIMIT = 512


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(data)
    return digest.hexdigest()


def source_identity(paper) -> tuple[Path, str, str]:
    source = Path(paper.render_path or paper.source_path)
    stamp = None
    try:
        stat = source.stat()
        # The inode matters: two test runs can reuse one path for different
        # content of the same size written inside one clock tick.
        stamp = (str(source), stat.st_mtime_ns, stat.st_size, stat.st_ino)
    except OSError:
        pass
    if stamp is not None:
        with _SOURCE_HASHES_LOCK:
            held = _SOURCE_HASHES.get(stamp)
        if held is not None:
            return source, "pdf" if source.suffix.lower() == ".pdf" else paper.kind, held
    digest = _file_sha256(source)
    if stamp is not None:
        with _SOURCE_HASHES_LOCK:
            _SOURCE_HASHES[stamp] = digest
            while len(_SOURCE_HASHES) > _SOURCE_HASHES_LIMIT:
                _SOURCE_HASHES.pop(next(iter(_SOURCE_HASHES)))
    return source, "pdf" if source.suffix.lower() == ".pdf" else paper.kind, digest


# A card's crops do not change until its ranges or its original change, but
# building them decodes every PNG and re-hashes every file.  Remembering the
# finished descriptor turns a second visit to the same paper into a lookup.
# The cache is only a shortcut: each crop's size and mtime are re-checked, so a
# deleted or damaged crop is rebuilt (and fails again) exactly as before.
_ASSETS: "OrderedDict[tuple, tuple[tuple[tuple[str, int, int], ...], list[dict]]]" = OrderedDict()
_ASSETS_LIMIT = 4_000
_ASSETS_LOCK = threading.Lock()


def _stamp(path: Path) -> tuple[str, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (path.name, stat.st_size, stat.st_mtime_ns)


def _crops_unchanged(stamps, folder: Path) -> bool:
    return all(_stamp(folder / stamp[0]) == stamp for stamp in stamps)


def assets(question) -> list[dict]:
    if not valid_regions(question.paper, question.regions):
        raise ValueError("原图题需要有效的原卷范围")
    source, kind, source_hash = source_identity(question.paper)
    folder = Path(settings.DATA_ROOT) / str(question.paper_id) / "question-images"
    cache_key = (question.pk, question.content_revision, source_hash,
                 json.dumps(question.regions, sort_keys=True))
    with _ASSETS_LOCK:
        held = _ASSETS.get(cache_key)
    if held is not None and _crops_unchanged(held[0], folder):
        with _ASSETS_LOCK:
            _ASSETS.move_to_end(cache_key)
        return deepcopy(held[1])
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    stamps = []
    for index, region in enumerate(question.regions):
        key = hashlib.sha256(json.dumps([source_hash, region], sort_keys=True).encode()).hexdigest()[:32]
        target = folder / f"q{question.pk}-{key}.png"
        if not target.is_file():
            image = imaging.render_source_page(source, kind, region["page_idx"])
            cropped = image.crop(imaging.to_pixels(region["bbox"], image.size))
            # Concurrent previews must not read a partially written PNG.
            temporary = folder / f".{key}-{uuid.uuid4().hex}.png"
            try:
                cropped.save(temporary, format="PNG", optimize=True)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        with Image.open(target) as image:
            image.load()  # A readable PNG header does not prove intact pixels.
            width, height = image.size
        image_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        stamp = _stamp(target)
        if stamp is not None:
            stamps.append(stamp)
        result.append({"page_idx": region["page_idx"], "bbox": list(region["bbox"]), "order": index,
                       "source": "manual", "render_sha256": source_hash, "image_sha256": image_hash,
                       "width": width, "height": height, "file": target.name,
                       "url": f"/api/questions/{question.pk}/question-images/{index}?v={image_hash[:16]}"})
    if len(stamps) == len(result):
        with _ASSETS_LOCK:
            _ASSETS[cache_key] = (tuple(stamps), deepcopy(result))
            _ASSETS.move_to_end(cache_key)
            while len(_ASSETS) > _ASSETS_LIMIT:
                _ASSETS.popitem(last=False)
    return result


def asset_file(question, index: int) -> Path:
    items = assets(question)
    if type(index) is not int or not 0 <= index < len(items):
        raise IndexError("原图题裁片不存在")
    return Path(settings.DATA_ROOT) / str(question.paper_id) / "question-images" / items[index]["file"]


def body_valid(question) -> bool:
    if not is_image(question):
        return bool(question.stem.strip())
    try:
        return bool(assets(question))
    except (OSError, ValueError, IndexError, RuntimeError):
        return False


def review(question) -> dict:
    if is_image(question):
        return {"status": "ok", "source": "manual", "reason": "正文保留所选原卷范围的完整图像，请对照原卷确认范围完整。"}
    from .figure_policy import stored_or_derived_review
    return stored_or_derived_review(question)
