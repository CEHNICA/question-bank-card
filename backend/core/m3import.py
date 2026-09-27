"""从 M3 导入已解析的试卷：只复制原卷和 MinerU 解析包，不再调用 MinerU。

M3 的草稿、人工修订不会带过来——题有据从原卷重新切题、重新读题，
这样可以直接对比两种做法在同一份卷上的效果。M3 目录只读，不会被改动。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from django.conf import settings

from .models import Paper


def m3_backend() -> Path | None:
    configured = os.environ.get("QB_M3_BACKEND", "").strip()
    root = settings.BASE_DIR.parent
    candidates = [Path(configured)] if configured else []
    candidates += [root.parent / "题库M3" / "backend", root.parent / "question-bank-m3" / "backend"]
    for candidate in candidates:
        if (candidate / "db.sqlite3").is_file() and (candidate / "data").is_dir():
            return candidate
    return None


def list_m3_papers() -> list[dict]:
    backend = m3_backend()
    if backend is None:
        return []
    uri = (backend / "db.sqlite3").resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            rows = connection.execute(
                "SELECT id, filename, kind, created_at FROM core_document WHERE status='ready' ORDER BY created_at"
            ).fetchall()
    except sqlite3.Error:
        return []
    imported = set(Paper.objects.exclude(imported_from="").values_list("imported_from", flat=True))
    result = []
    for raw_id, filename, kind, created in rows:
        document_id = str(uuid.UUID(str(raw_id)))
        folder = backend / "data" / document_id
        source = next((p for p in folder.glob("source.*") if p.is_file()), None)
        if source is None or not (folder / "mineru_result.zip").is_file():
            continue
        result.append({"id": document_id, "filename": filename, "kind": kind, "created_at": created,
                       "imported": document_id in imported})
    return result


def import_m3_paper(document_id: str) -> Paper:
    entry = next((item for item in list_m3_papers() if item["id"] == document_id), None)
    if entry is None:
        raise ValueError("在 M3 里没有找到这份已解析的试卷")
    existing = Paper.objects.filter(imported_from=document_id).first()
    if existing is not None:
        return existing
    folder = m3_backend() / "data" / document_id
    source = next(p for p in folder.glob("source.*") if p.is_file())
    paper = Paper(filename=entry["filename"], kind=entry["kind"], imported_from=document_id)
    target = settings.DATA_ROOT / str(paper.id)
    target.mkdir(parents=True, exist_ok=False)
    copied = target / f"source{source.suffix.lower()}"
    shutil.copyfile(source, copied)
    shutil.copyfile(folder / "mineru_result.zip", target / "mineru_result.zip")
    if entry["kind"] == "docx" and (folder / "preview.pdf").is_file():
        shutil.copyfile(folder / "preview.pdf", target / "converted.pdf")
        paper.render_path = str(target / "converted.pdf")
    paper.source_path = str(copied)
    paper.zip_path = str(target / "mineru_result.zip")
    paper.sha256 = hashlib.sha256(copied.read_bytes()).hexdigest()
    paper.save()
    return paper
