"""Where one question's crop ends and the next begins, judged by the ink on the page.

The segmenter cuts at the top of the next question's MinerU box: the question
above ends 2 units over it, the next one starts 9 units over it, so both crops
share a thin band.  On a photo the box is often loose — it starts inside the
last line of the question above (fraction denominators, subscripts, the
underline of a blank, handwriting) — and then the question above lost the
bottom of that line while the next crop began with it (2025级高一质量检测一：
第 6 题选项的分母被切掉、出现在第 7 题顶上；第 14 题最后一行压在切线上).

Two corrections, each only where the page shows the cut is wrong:

* **The bottom moves down** past ink that touches it, into the white below,
  never past the next question's printed number (or 8 units when the number
  cannot be seen).  The last line of a question is never cut; at worst the
  crop shows a sliver more.
* **The next top moves down** only when it slices dense print that runs
  unbroken from above down to that white; it then starts just below that
  ink, never below the number.  A small isolated mark over the next line —
  the numerator of “−1/2024” in “2. −1/2024 的相反数是” (胜利初一第 2 题) —
  stays with the next question, because a row profile cannot tell whose it
  is, and thin strokes can look white.

A clean cut (white rows where the two crops overlap) is left exactly where it
was.  Coordinates are page units 0–1000.
"""

from __future__ import annotations

from PIL import Image

INK_LEVEL = 170       # 灰度低于此值算有墨迹（与 imaging.trim_regions 一致）
CLEAN = 0.010         # 一行里有墨迹的像素不超过 1% 算空白（容得下页边一两道竖笔）
DENSE = 0.03          # 至少 3%：印刷文字行里的一行
MIN_RUN = 1.5         # 空白至少这么高才算行间空隙（分式线和分子之间的缝更窄）
BELOW = 8.0           # 看不到下一题题号时，下沿最多挪到原起点以下这么远
NUMBER_WIDTH = 30.0   # 题号所在的窄条宽度（从文字框左边算）
NUMBER_DENSE = 0.08   # 窄条里这么黑的一行，算是题号那一行
NUMBER_REACH = 30.0   # 题号（标题）的字可能比 MinerU 的框低这么多
LINE_DENSE = 0.05     # 连续这么黑、至少 LINE_MIN 高的几行，是一行字
LINE_MIN = 3.0
START_PAD = 9.0       # segment.START_PAD：题号上方原本留的白
SNAPPABLE_SOURCES = {"mineru", "repaired", "inferred"}


def row_ink(page: Image.Image, x0: float, x1: float, y0: float, y1: float) -> list[tuple[float, float]]:
    """[(y, share of dark pixels)] for every pixel row between y0 and y1 (page units)."""
    width, height = page.size
    y0, y1 = max(0.0, y0), min(1000.0, y1)
    left = max(0, min(width - 1, int(x0 * width / 1000)))
    right = max(left + 1, min(width, int(round(x1 * width / 1000))))
    top = max(0, min(height - 1, int(y0 * height / 1000)))
    bottom = max(top + 1, min(height, int(round(y1 * height / 1000))))
    ink = page.crop((left, top, right, bottom)).convert("L").point(lambda value: 255 if value < INK_LEVEL else 0)
    means = ink.resize((1, ink.height), Image.Resampling.BOX)
    return [((top + row + 0.5) * 1000 / height, means.getpixel((0, row)) / 255) for row in range(means.height)]


def first_gap(rows: list[tuple[float, float]], after: float, before: float) -> tuple[float, float] | None:
    """The first white run (≥ MIN_RUN tall) below the stroke at ``after`` that starts above ``before``.

    When the edge at ``after`` runs through ink (an underline, a denominator
    just below it), the gap must lie below that ink; otherwise a run that
    already contains ``after`` counts, and the cut stays in the white it
    was near.
    """
    touching = [y for y, value in rows if after - 0.5 <= y <= after + 2 and value > CLEAN]
    if touching:
        after = max(touching)
        rows = [row for row in rows if row[0] > after]
    run_start = None
    previous_y = None
    for y, value in rows + [(float("inf"), 1.0)]:
        if value <= CLEAN and y != float("inf"):
            if run_start is None:
                run_start = y
            previous_y = y
            continue
        if run_start is not None:
            end = previous_y
            if end >= after and run_start < before and end - run_start >= MIN_RUN:
                return run_start, end
            if run_start >= before:
                return None
        run_start = None
    return None


def number_top(page: Image.Image, x: float | None, lo: float, hi: float, earliest: float) -> float | None:
    """Top of the next question's number in the narrow strip where it is printed.

    Option labels of the line above (“C.”) sit right over the next number,
    and a heading's box can start below its ink: any mark that begins above
    ``earliest`` is passed over and the search goes on below it.
    """
    if x is None:
        return None
    mark_start = None
    for y, value in row_ink(page, max(0.0, x - 2), min(1000.0, x + NUMBER_WIDTH), lo, hi):
        if value <= CLEAN:
            mark_start = None
            continue
        if mark_start is None:
            mark_start = y
        if value >= NUMBER_DENSE and mark_start >= earliest:
            return mark_start
    return None


def _ink_touches(rows: list[tuple[float, float]], y: float) -> bool:
    return any(value > CLEAN for row_y, value in rows if y - 0.5 <= row_y <= y + 2)


def _slices_line_from_above(rows: list[tuple[float, float]], top: float, gap_start: float) -> bool:
    """Whether a crop starting at ``top`` would begin with the tail of a printed line above it.

    True when the rows at ``top`` are dense print and the ink runs unbroken
    from just above ``top`` down to the white gap.
    """
    near = [value for y, value in rows if top - 1.5 <= y <= top + 1.5]
    if not near or max(near) < DENSE:
        return False
    band = [value for y, value in rows if top - 1.5 <= y < gap_start]
    return bool(band) and all(value > CLEAN for value in band)


def _holds_a_line(rows: list[tuple[float, float]], y0: float, y1: float) -> bool:
    """Whether rows between y0 and y1 contain a line of print (not just strokes or working).

    The box of the next question normally starts on its first line.  When
    a cut would move below that box top, the rows given away must not be a
    line of text: a short first line bridged to the line above by working
    would otherwise go to the question above.
    """
    run_start = None
    for y, value in rows:
        if not (y0 <= y < y1) or value < LINE_DENSE:
            run_start = None
            continue
        if run_start is None:
            run_start = y
        if y - run_start >= LINE_MIN:
            return True
    return False


def _same_column(a: list[float], b: list[float]) -> bool:
    overlap = min(a[2], b[2]) - max(a[0], b[0])
    return overlap > 0.5 * min(a[2] - a[0], b[2] - b[0])


def _snappable(start: dict) -> bool:
    return start.get("source") in SNAPPABLE_SOURCES and start.get("at_start") is not False


def _new_top(rows, top: float, nominal: float, printed: float | None, limit: float) -> float | None:
    """Where the next crop should start instead of ``top``, or None to keep it."""
    gap = first_gap(rows, top, limit)
    if gap is None or not _slices_line_from_above(rows, top, gap[0]):
        return None
    # Just below the ink coming from above: thin strokes of the next line
    # (a numerator, an accent) can sit in what looks like white.
    moved = round(gap[0] + 0.5, 1)
    ceiling = printed - 0.5 if printed is not None else nominal
    if moved <= top or moved > ceiling or _holds_a_line(rows, nominal, moved):
        return None
    return moved


def _snap_pair(previous: dict, following: dict, page_loader) -> bool:
    start = following.get("start") or {}
    if not _snappable(start) or not previous.get("regions") or not following.get("regions"):
        return False
    above, below = previous["regions"][-1], following["regions"][0]
    if above["page_idx"] != below["page_idx"] or above["page_idx"] != start.get("page"):
        return False
    a, b = above["bbox"], below["bbox"]
    if not _same_column(a, b) or b[1] > a[3] + 1 or a[3] - b[1] > 30:
        return False
    page = page_loader(above["page_idx"])
    x0, x1 = max(a[0], b[0]), min(a[2], b[2])
    nominal = float(start.get("y", b[1]))
    bottom, top = float(a[3]), float(b[1])
    limit = min(nominal + BELOW, b[3] - 8)
    printed = number_top(page, start.get("x"), nominal - 12, nominal + NUMBER_REACH, nominal - 2)
    if printed is not None:
        limit = min(limit, printed)
    rows = row_ink(page, x0, x1, top - 4, max(limit, bottom + 3) + 4)
    # A clean overlap: nothing to fix.
    if not any(value > CLEAN for y, value in rows if top <= y <= bottom + 2):
        return False
    changed = False
    gap = first_gap(rows, bottom, limit)
    if gap is not None and _ink_touches(rows, bottom):
        cut = round((gap[0] + min(gap[1], limit)) / 2, 1)
        if cut > bottom and not _holds_a_line(rows, nominal, cut):
            above["bbox"] = [a[0], a[1], a[2], cut]
            changed = True
    moved = _new_top(rows, top, nominal, printed, limit)
    if moved is not None:
        below["bbox"] = [b[0], moved, b[2], b[3]]
        changed = True
    return changed


def _snap_heading_bottom(item: dict, headings: list[dict], page_loader) -> bool:
    """A question that ends at a section heading: never cut its last line, never take the heading."""
    if not item.get("regions"):
        return False
    last = item["regions"][-1]
    a = last["bbox"]
    heading = next((h for h in headings
                    if int(h.get("page", -1)) == last["page_idx"] and abs(float(h["y"]) - 2 - a[3]) < 0.6
                    and a[0] - 1 <= float(h.get("x", a[0])) < a[2]), None)
    if heading is None:
        return False
    page = page_loader(last["page_idx"])
    stop = float(heading["y"])
    x = heading.get("x")
    if x is not None and _ink_touches(
            [(y, v) for y, v in row_ink(page, max(0.0, x - 2), min(1000.0, x + NUMBER_WIDTH), a[3] - 1, a[3] + 3)
             if v >= NUMBER_DENSE], a[3]):
        return False  # 下沿碰到的是标题本身的字（标题框比字低）：不动
    printed = number_top(page, x, stop - 12, stop + NUMBER_REACH, stop - 2)
    # Without the heading's own first line in sight, do not go past its box.
    limit = min(stop + BELOW, printed) if printed is not None else stop
    rows = row_ink(page, a[0], a[2], a[3] - 4, max(limit, a[3] + 3) + 4)
    if not _ink_touches(rows, a[3]):
        return False
    gap = first_gap(rows, a[3], limit)
    if gap is None:
        return False
    cut = round((gap[0] + min(gap[1], limit)) / 2, 1)
    if cut <= a[3] or _holds_a_line(rows, stop, cut):
        return False
    last["bbox"] = [a[0], a[1], a[2], cut]
    return True


def _snap_top(item: dict, page_loader) -> bool:
    """A question whose crop starts inside working or print above it (top of a column)."""
    start = item.get("start") or {}
    if not _snappable(start) or not item.get("regions"):
        return False
    first = item["regions"][0]
    b = first["bbox"]
    nominal = float(start.get("y", b[1]))
    if first["page_idx"] != start.get("page") or abs(b[1] - (nominal - START_PAD)) > 0.6:
        return False  # 不是“题号上方留白”定的上沿（栏顶、标题下沿……）：不动
    page = page_loader(first["page_idx"])
    top = float(b[1])
    limit = min(nominal + BELOW, b[3] - 8)
    printed = number_top(page, start.get("x"), top, nominal + NUMBER_REACH, nominal - 2)
    if printed is not None:
        limit = min(limit, printed)
    rows = row_ink(page, b[0], b[2], top - 4, max(limit, top + 3) + 4)
    moved = _new_top(rows, top, nominal, printed, limit)
    if moved is None:
        return False
    first["bbox"] = [b[0], moved, b[2], b[3]]
    return True


def snap_cuts(items: list[dict], page_loader, layout=None) -> int:
    """Correct cuts that run through ink.  Returns how many cuts changed.

    ``items`` are segment.build_questions results; their regions change in
    place.  ``layout`` supplies the section headings that end a question.
    Any failure to read a page leaves that cut as it was.
    """
    moved = 0
    ordered = sorted(items, key=lambda item: (
        (item.get("start") or {}).get("page", 0), (item.get("start") or {}).get("col", 0),
        (item.get("start") or {}).get("y", 0)))
    paired: set[int] = set()
    for position, (previous, following) in enumerate(zip(ordered, ordered[1:])):
        try:
            if _snap_pair(previous, following, page_loader):
                moved += 1
                paired.add(position)
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    headings = [h for h in (getattr(layout, "headings", None) or []) if "y" in h and "page" in h]
    for position, item in enumerate(ordered):
        try:
            if position - 1 not in paired and _snap_top(item, page_loader):
                moved += 1
            if headings and position not in paired and _snap_heading_bottom(item, headings, page_loader):
                moved += 1
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            continue
    return moved
