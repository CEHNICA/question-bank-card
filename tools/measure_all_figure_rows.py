"""Measure how each line of stem figures lands in the real Word export.

Builds one document through the export's own figure writers for every question
in the bank that carries two or more diagrams, then hands the file to Word.
Three questions wrap 2+1 and one wraps 2+2; those are the ones a single Word
grid used to get wrong, so the report lists the line width every line is
supposed to have next to the one Word reports.
"""
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src")
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
import django

django.setup()

from docx import Document
from docx.shared import Mm

from core.library_export import (
    PREVIEW_FIGURE_GAP_PX,
    PREVIEW_PX_PER_INCH,
    PREVIEW_TEXT_WIDTH_MM,
    _figure_paper_size,
    _figure_row_lines,
    _write_images,
)

DB = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard", "db.sqlite3")
DATA = os.path.join(os.environ["LOCALAPPDATA"], "QuestionBankCard", "data", "library")
GAP = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH


def dashed(value):
    text = str(value)
    return (f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"
            if len(text) == 32 else text)


def load_items():
    connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    items = []
    for row in connection.execute(
            "SELECT id, number, source_filename, content FROM core_publishedquestion"
            " WHERE status = 'published' ORDER BY number"):
        try:
            content = json.loads(row["content"] or "{}")
        except Exception:
            continue
        figures = [f for f in (content.get("figures") or []) if isinstance(f, dict)
                   and f.get("slot") == "stem"]
        if len(figures) < 2:
            continue
        folder = os.path.join(DATA, dashed(row["id"]))
        images, ok = [], True
        for figure in figures:
            try:
                with open(os.path.join(folder, str(figure.get("file"))), "rb") as handle:
                    images.append({"bytes": handle.read(), "slot": "stem"})
            except OSError:
                ok = False
        if ok:
            items.append({"label": f"{row['source_filename']} 第{row['number']}题", "images": images})
    return items


def build(path):
    document = Document()
    for section in document.sections:
        section.page_width = Mm(210)          # A4, the paper the app prints on
        section.page_height = Mm(297)
        section.left_margin = section.right_margin = Mm(16)
    report = []
    for item in load_items():
        from PIL import Image
        sized = []
        for image in item["images"]:
            with Image.open(io.BytesIO(image["bytes"])) as picture:
                sized.append({**image, "size": picture.size})
        # A question's stem, so two questions never end up as back-to-back tables
        # — the real export puts text between them and Word merges those.
        document.add_paragraph(f"（{item['label']}）")
        lines = _figure_row_lines(sized)
        _write_images(document, {"images": sized}, "stem", lines)
        widths = [_figure_paper_size(picture["size"])[0] for picture in sized]
        expected, cursor = [], 0
        for count in lines:
            chunk = widths[cursor:cursor + count]
            expected.append(round(sum(chunk) + GAP * (len(chunk) - 1), 1))
            cursor += count
        report.append({"label": item["label"], "lines": lines, "expectedMm": expected})
    document.save(path)
    return report


if __name__ == "__main__":
    target = Path(tempfile.gettempdir()) / "qb-all-figure-rows.docx"
    entries = build(str(target))
    print(f"built {target} with {len(entries)} questions")
    for entry in entries:
        print(f"  {entry['label']}: lines {entry['lines']} "
              f"expected {entry['expectedMm']} mm")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(ROOT / "tools" / "measure_word_pictures.ps1"), str(target)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    print()
    print(result.stdout or result.stderr)
