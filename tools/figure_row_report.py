"""What the Word export does with every saved question that has 2+ figures.

Read-only: walks the published questions, feeds their figure sizes through
both the old rule (raw pixels) and the current one (the row the browser wraps
them onto, then a shrink to fit the text area), and prints only the questions
whose arrangement actually moves — with the size each picture ends up at on
paper.

Also cross-checks against BROWSER_ROWS below, which holds the row counts a
real Chrome produced for this bank on 2026-10-15, so a future change to the
CSS or the exporter shows up here as a mismatch instead of silently shipping.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, r"C:\Users\17321\.minimax\sessions\mvs_fc946281665f4565a3d0ce7af01bd838\workspace\qbj-src\backend")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
import django

django.setup()

from PIL import Image

from core.library_export import (
    PREVIEW_FIGURE_GAP_PX,
    PREVIEW_PX_PER_INCH,
    _figure_display_size,
    _figure_row_layout,
)

DB = r"C:\Users\17321\AppData\Local\QuestionBankCard\db.sqlite3"
ROOT = r"C:\Users\17321\AppData\Local\QuestionBankCard\data\library"

# Rows the real browser produced for each of these questions in a 1100px
# question panel (the expanded card width), measured on 2026-10-15 by laying
# the real PNGs out under the real stylesheet. Empty = not measured.
BROWSER_ROWS = {
    "47f1368ef1a747efb39893106fec2578": [1, 1],
    "3efc927e1df74671b58d769b4c118a17": [1, 2],
    "1ef667e56d7047f4a9a135c0bbfb1e2f": [1, 2],
    "5f9f5a4e270b4fbabb6ce65a004d1450": [1, 2],
    "cf9a55e6b4fe45ceb35a3e572d8516be": [1, 1],
    "26d05a6c01bc47eca12d13bfc1dee819": [1, 2],
    "9eb7678a863946a4b170572e7b5e39b9": [1, 2],
    "c9c3c9cea0144256b2d3f4d71b9532de": [1, 2],
    "439ad845a43f48c8b5d1cda71fcc9330": [2, 2],
    "814b1e69f5d7441396dd3f380e6d6bc6": [2, 3],
    "e14ec9deaaa74f6b8ed9ae1995c85689": [1, 1],
    "4616fbcda8964d4081f3b91b309cbb69": [1, 1],
    "33de480e99bc4cedb7766812978246d8": [1, 2],
}


def old_columns(sizes):
    widths = [float(w) for w, _ in sizes if w > 0]
    if len(widths) < 2:
        return 1
    container = 178 / 25.4 * PREVIEW_PX_PER_INCH
    best, used, count = 1, widths[0], 1
    for width in widths[1:]:
        if used + PREVIEW_FIGURE_GAP_PX + width <= container + 0.5:
            used += PREVIEW_FIGURE_GAP_PX + width
            count += 1
        else:
            best = max(best, count)
            used, count = width, 1
    return int(max(best, count))


def dashed(snapshot_id):
    """Pictures live under the dashed form of the snapshot id."""
    value = str(snapshot_id)
    return (f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:32]}"
            if len(value) == 32 else value)


def picture_sizes(snapshot_id, figures):
    folder = os.path.join(ROOT, dashed(snapshot_id))
    out = []
    for figure in figures:
        path = os.path.join(folder, str(figure.get("file") or ""))
        try:
            with Image.open(path) as image:
                out.append(image.size)
        except Exception:
            return None
    return out or None


con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
rows = con.execute(
    "SELECT id, number, source_filename, content FROM core_publishedquestion"
    " WHERE status = 'published'"
).fetchall()

multi = 0
changed = []
for row in rows:
    try:
        content = json.loads(row["content"] or "{}")
    except Exception:
        continue
    figures = [f for f in (content.get("figures") or []) if isinstance(f, dict)
               and f.get("slot") == "stem"]
    if len(figures) < 2:
        continue
    sizes = picture_sizes(row["id"], figures)
    if not sizes:
        continue
    multi += 1
    before = old_columns(sizes)
    after, shrink = _figure_row_layout([{"size": s} for s in sizes])
    if after != before:
        shown = [_figure_display_size(s) for s in sizes]
        mm = [round(w * shrink * 25.4 / PREVIEW_PX_PER_INCH, 1) for w, _ in shown]
        changed.append({
            "id": row["id"],
            "number": row["number"],
            "paper": row["source_filename"],
            "figures": len(sizes),
            "files": [figure["file"] for figure in figures],
            "sizes": [list(size) for size in sizes],
            "browser_rows": BROWSER_ROWS.get(row["id"], []),
            "before": f"{before} per row",
            "after": f"{after} per row" + (f" (shrunk {shrink:.0%})" if shrink != 1.0 else ""),
            "mm": mm,
        })

print(f"published questions with 2+ stem figures: {multi}")
print(f"their Word layout changes:                {len(changed)}")
agree = disagree = 0
for item in changed:
    measured = item["browser_rows"][0] if item["browser_rows"] else None
    predicted = -(-item["figures"] // int(item["after"].split()[0]))
    verdict = ""
    if measured is not None:
        if measured == predicted:
            agree += 1
            verdict = "  matches the preview"
        else:
            disagree += 1
            verdict = f"  MISMATCH (preview shows {measured})"
    print(f"  第 {item['number']} 题  {item['figures']} 图 {item['sizes']}  "
          f"{item['before']} -> {item['after']}  {item['mm']} mm{verdict}")
    print(f"      [{item['paper']}] {item['id']}")
print(f"\nagrees with the measured preview: {agree}, disagrees: {disagree}")
