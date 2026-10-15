"""What the Word export does with every saved question that has 2+ figures.

Read-only: walks the published questions, feeds their real picture sizes
through ``_figure_paper_size`` / ``_figure_row_lines`` — the same two functions
the export uses — and prints the arrangement and the paper size each question
lands on.

``before_lines`` is the naive rule this replaced: treat every crop as 96 dpi
screen pixels and wrap them against the text area. Comparing against it shows
which questions the 150 dpi rule actually moves, and the ``over`` counter flags
any line that would still run off the page (there should never be one).

Every line is reported on its own. Word writes one centred table per line now,
so a lone picture on a second line is a real case to look at, not an average.
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
    PREVIEW_TEXT_WIDTH_MM,
    _figure_paper_size,
    _figure_row_lines,
)

DB = r"C:\Users\17321\AppData\Local\QuestionBankCard\db.sqlite3"
ROOT = r"C:\Users\17321\AppData\Local\QuestionBankCard\data\library"


def before_lines(sizes):
    """The naive rule: 96 dpi pixels, wrapped against the text area."""
    widths = [float(w) for w, _ in sizes if w > 0]
    if len(widths) < 2:
        return [1]
    container = PREVIEW_TEXT_WIDTH_MM / 25.4 * PREVIEW_PX_PER_INCH
    lines, used, count = [1], widths[0], 1
    for width in widths[1:]:
        if used + PREVIEW_FIGURE_GAP_PX + width <= container + 0.5:
            used += PREVIEW_FIGURE_GAP_PX + width
            count += 1
        else:
            lines.append(count)
            used, count = width, 1
    lines.append(count)
    return lines


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

gap_mm = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH
multi = changed = over = 0
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
    lines = _figure_row_lines([{"size": s} for s in sizes])
    was = before_lines(sizes)
    paper = [_figure_paper_size(s) for s in sizes]
    mm = [round(w, 1) for w, _ in paper]
    print(f"  第 {row['number']} 题  {len(sizes)} 图 {sizes}")
    print(f"      分行 {lines}  图宽 {mm} mm")
    start = 0
    for number, count in enumerate(lines, 1):
        chunk = paper[start:start + count]
        start += count
        line_mm = sum(w for w, _ in chunk) + gap_mm * max(count - 1, 0)
        slack = (PREVIEW_TEXT_WIDTH_MM - line_mm) / 2
        if slack < -0.1:
            over += 1
            print(f"      第 {number} 行 {count} 图  内容宽 {line_mm:.1f}mm"
                  f"  <-- 排不下，会超出版心")
        else:
            print(f"      第 {number} 行 {count} 图  内容宽 {line_mm:.1f}mm"
                  f"  左留 {slack:.1f}mm")
    if lines != was:
        changed += 1
        print(f"      (96 dpi would have made it {was})")
    print(f"      [{row['source_filename']}] {row['id']}")

print(f"\npublished questions with 2+ stem figures: {multi}")
print(f"arrangement differs from the naive 96 dpi rule: {changed}")
print(f"rows that would run off the page:              {over}")
