"""List every published question with 2+ stem figures, with its figure sizes."""

import json
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src\backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
import django

django.setup()

from PIL import Image

from core.library_export import PREVIEW_FIGURE_GAP_PX, PREVIEW_PX_PER_INCH, _figure_paper_size, _figure_row_lines

DB = r"C:\Users\17321\AppData\Local\QuestionBankCard\db.sqlite3"
ROOT = r"C:\Users\17321\AppData\Local\QuestionBankCard\data\library"


def dashed(value):
    text = str(value)
    return f"{text[:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}" if len(text) == 32 else text


connection = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
connection.row_factory = sqlite3.Row
rows = connection.execute(
    "SELECT id, number, source_filename, content FROM core_publishedquestion WHERE status = 'published' ORDER BY number"
).fetchall()

out = []
for row in rows:
    try:
        content = json.loads(row["content"] or "{}")
    except Exception:
        continue
    figures = [f for f in (content.get("figures") or []) if isinstance(f, dict) and f.get("slot") == "stem"]
    if len(figures) < 2:
        continue
    folder = os.path.join(ROOT, dashed(row["id"]))
    sizes, ok = [], True
    for figure in figures:
        try:
            with Image.open(os.path.join(folder, str(figure.get("file")))) as image:
                sizes.append(image.size)
        except Exception:
            ok = False
    if not ok:
        continue
    lines = _figure_row_lines([{"size": s} for s in sizes])
    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH
    widths = [_figure_paper_size(size)[0] for size in sizes]
    rows, start = [], 0
    for count in lines:
        chunk = widths[start : start + count]
        start += count
        rows.append(round(sum(chunk) + gap * (len(chunk) - 1), 1))
    out.append(
        {
            "id": row["id"],
            "number": row["number"],
            "paper": row["source_filename"],
            "sizes": sizes,
            "previewLines": lines,
            "lineWidthsMm": rows,
        }
    )

for item in out:
    print(
        f"{item['id']}  第{item['number']}题  {item['paper']}  {item['sizes']}  "
        f"分行 {item['previewLines']} 行宽 {item['lineWidthsMm']}mm"
    )
print()
print(json.dumps([item["id"] for item in out], ensure_ascii=False))
