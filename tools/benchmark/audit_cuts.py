"""Audit predicted question crops against the page image. No ground truth needed.

This is the regression net for the cutting algorithm.  It answers the three
questions that decide whether a change made things better or worse, and none of
them needs a human-labelled set:

  numbering  is the printed sequence one clean 1..N run, and are the gaps
             already handed to the gap-filler
  coverage   how much of each page's text falls inside some crop (the remainder
             is normally section headings and the title block, not lost content)
  cuts       does a crop edge run through a line of *print*, and does a crop
             contain another question's number

Ink is read through ``cuts.row_ink`` — the same reader the cutter uses — so the
audit and the code under test cannot disagree about where a line of print is.
The judgement is still independent: a line counts as print only at
``PRINT_PEAK`` density and only when a visible share of it falls outside, so
pencil, underlines and figure edges are never counted as lost text.

    python tools\\benchmark\\audit_cuts.py --papers D:\\qb-bench\\papers --pred D:\\qb-bench\\pred
    python tools\\benchmark\\audit_cuts.py --papers D:\\qb-bench\\papers --expected-only

The input directory is the one ``qb_bench.py`` writes per paper:
``meta.json``, ``blocks.json`` and ``pages/p001.png`` …  Exit code 1 when a cut
loses printed text or a crop swallows another question, so it can gate a build.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO / "backend") not in sys.path:
    sys.path.insert(0, str(REPO / "backend"))

from PIL import Image  # noqa: E402

from core import cuts  # noqa: E402
from core.figure_policy import has_figure_cue  # noqa: E402

NUMBER_LINE = re.compile(r"^\s*(\d{1,2})\s*[.．、]")
TEXT_TYPES = {"text", "equation", "table"}
LINE_GAP = 2.0        # page units of blank that separate two printed lines
LINE_DENSE = 0.05     # rows this black belong to a line of print; the cutter
                      # uses the same figure to tell a line from a hairline
SLICE_SHARE = 0.45   # share of the line that must fall outside the crop
SLICE_UNITS = 4.0    # ...and that share must be this many page units, or the
                      # clipped sliver is under a pixel and not worth reporting
PRINT_PEAK = 0.20    # what a line of print reaches; pencil and rules do not
PAD = 30             # page units of context read around every cut
BOOK_PAPERS = {"jiaocai7a"}


def crossing_run(rows: list[tuple[float, float]], edge: float) -> tuple[float, float, float] | None:
    """(top, bottom, peak ink) of the line of print that crosses ``edge``.

    Membership uses ``LINE_DENSE``, not ``CLEAN``: a printed line is a run of
    genuinely black rows, and the pale rows between strokes and between lines
    must not bridge two of them into one blob.  A run that reaches the edge of
    the window is a measurement artefact rather than a line, and is refused.
    """
    low, high = edge - PAD * 2, edge + PAD * 2
    window = [(y, v) for y, v in rows if low <= y <= high]
    if not window:
        return None
    start = None
    for index, (y, value) in enumerate(window):
        if value > LINE_DENSE:
            if start is None:
                start = index
        elif start is not None and y - window[start][0] > LINE_GAP:
            top, bottom = window[start][0], window[start - 1][0]
            if top - LINE_GAP <= edge <= bottom + LINE_GAP and bottom - top <= PAD:
                return top, bottom, max(v for _, v in window[start:index])
            start = None
    if start is not None:
        top, bottom = window[start][0], window[-1][0]
        if (top - LINE_GAP <= edge <= bottom + LINE_GAP
                and window[-1][0] < high - 1 and bottom - top <= PAD):
            return top, bottom, max(v for _, v in window[start:])
    return None


def slices_print(rows: list[tuple[float, float]], edge: float, inward: int) -> dict | None:
    """The cut runs through a line of print and loses a visible share of it."""
    line = crossing_run(rows, edge)
    if line is None or line[2] < PRINT_PEAK:
        return None
    top, bottom, peak = line
    inside = (bottom - edge) if inward > 0 else (edge - top)
    beyond = (edge - top) if inward > 0 else (bottom - edge)
    total = inside + beyond
    lost = beyond / total if inward > 0 else inside / total
    lost_units = beyond if inward > 0 else inside
    if lost < SLICE_SHARE or lost_units < SLICE_UNITS:
        return None
    return {"side": "head" if inward > 0 else "tail", "inside": round(inside, 1),
            "beyond": round(beyond, 1), "lost": round(lost, 2),
            "lost_units": round(lost_units, 1), "peak": round(peak, 3)}


def blocks_in(regions: list[dict], by_page: dict[int, list[dict]]) -> list[dict]:
    """Blocks a crop really covers.

    Overlap, not the centre: a 解答题's MinerU box can span half a column, so its
    centre sits far below the line that matters and a centre test would call the
    crop clean while the question's own first line is inside it.
    """
    found = []
    for region in regions:
        x0, y0, x1, y1 = region["bbox"]
        for block in by_page.get(region["page_idx"], []):
            bx0, by0, bx1, by1 = block["bbox"]
            if min(y1, by1) - max(y0, by0) < 0.4 * max(1.0, by1 - by0):
                continue
            if min(x1, bx1) - max(x0, bx0) < 0.5 * max(1.0, bx1 - bx0):
                continue
            found.append(block)
    return found


def audit_paper(slug: str, work: Path, pred: dict) -> dict:
    blocks = json.loads((work / "blocks.json").read_text(encoding="utf-8"))
    by_page: dict[int, list[dict]] = {}
    text_by_page: dict[int, list[dict]] = {}
    for block in blocks:
        if not block.get("bbox"):
            continue
        by_page.setdefault(block["page_idx"], []).append(block)
        if block.get("type") in TEXT_TYPES:
            text_by_page.setdefault(block["page_idx"], []).append(block)

    cache: dict[int, Image.Image] = {}

    def page_image(index: int) -> Image.Image:
        if index not in cache:
            with Image.open(work / "pages" / f"p{index + 1:03d}.png") as raw:
                cache[index] = raw.convert("RGB")
        return cache[index]

    sliced, foreign, no_figure = [], [], []
    for card in pred["cards"]:
        # 题干说了「如图」而这张卡没有配图 → 用户拿到的是一道做不出来的题。
        # 判定用产品自己的 has_figure_cue：被拦下来的正是这个条件，
        # 用另一份正则去测就等于在测另一件事。
        # 记为只报不判失败，和 gaps 同理——这些卡本来就会被 blocked_missing
        # 拦下来等人工处理，不是切线的回归；但必须看得见，否则无人守它。
        stem = " ".join((b.get("text") or "") for b in blocks_in(card["regions"], text_by_page))
        for region in card["regions"]:
            page = region["page_idx"]
            if not has_figure_cue(stem):
                break
            if any(f.get("page_idx") == page for f in card.get("figures") or []):
                break
            no_figure.append({"card": card["group_sequence"], "number": card["number"],
                              "page": page + 1, "text": stem[:60]})
            break
        for region in card["regions"]:
            try:
                rows = cuts.row_ink(page_image(region["page_idx"]), region["bbox"][0],
                                    region["bbox"][2], max(0.0, region["bbox"][1] - PAD),
                                    min(1000.0, region["bbox"][3] + PAD))
            except (OSError, ValueError):
                continue
            for edge, inward in ((region["bbox"][1], +1), (region["bbox"][3], -1)):
                hit = slices_print(rows, edge, inward)
                if hit:
                    sliced.append({"card": card["group_sequence"], "number": card["number"],
                                   "page": region["page_idx"] + 1, "edge": round(edge), **hit})
        for block in blocks_in(card["regions"], by_page):
            for raw in (block.get("text") or "").split("\n"):
                match = NUMBER_LINE.match(raw)
                if not match or match.group(1) == str(card["number"]):
                    continue
                # Only the *next* question's number is a defect here.  A crop
                # holding “4. 6 1 3” or “0. - k + 1)” is a decimal or a formula
                # that merely starts with a digit, and a source scan that
                # printed “9.” for question 19 must not be reported forever.
                # The one shape that is a real cut bug is this card's successor
                # landing inside it: card N holding “N+1.”.
                if int(match.group(1)) == int(card["number"]) + 1:
                    foreign.append({"card": card["group_sequence"], "number": card["number"],
                                    "foreign": match.group(1), "page": block["page_idx"] + 1,
                                    "text": raw.strip()[:40]})
                break

    coverage = []
    for page in range(pred["pages"]):
        page_text = text_by_page.get(page, [])
        if not page_text:
            continue
        page_regions = [region for card in pred["cards"] for region in card["regions"]
                        if region["page_idx"] == page]
        spans = []
        for region in sorted(page_regions, key=lambda r: r["bbox"][1]):
            low, high = region["bbox"][1], region["bbox"][3]
            if spans and low <= spans[-1][1] + 1:
                spans[-1] = (spans[-1][0], max(spans[-1][1], high))
            else:
                spans.append((low, high))
        covered = sum(1 for block in page_text
                      if any(low - 2 <= (block["bbox"][1] + block["bbox"][3]) / 2 <= high + 2
                             for low, high in spans))
        coverage.append({"page": page, "covered": covered, "total": len(page_text),
                         "ratio": round(covered / len(page_text), 3)})

    numbers = [card["number"] for card in pred["cards"]]
    expected = list(range(1, len(numbers) + 1)) if numbers else []
    gaps = [n for n in expected if n not in numbers] if numbers else []
    return {"cards": len(pred["cards"]), "numbers": numbers, "gaps": gaps,
            "sliced": sliced, "foreign": foreign, "coverage": coverage,
            "no_figure": no_figure,
            "notes": pred.get("notes") or []}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--papers", required=True, help="directory of per-paper folders")
    parser.add_argument("--pred", help="directory of <slug>.json predictions; "
                                        "defaults to <papers>")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--expected-only", action="store_true",
                        help="print the per-paper summary without the detail lists")
    args = parser.parse_args()

    papers = Path(args.papers)
    pred_root = Path(args.pred) if args.pred else papers
    failures = 0
    warned = 0
    print(f"{'paper':18} {'cards':>5} {'gaps':>5} {'sliced':>7} {'foreign':>8} "
          f"{'nofig':>6} {'cover':>7}")
    for work in sorted(p for p in papers.iterdir() if p.is_dir()):
        if args.only and work.name not in set(args.only):
            continue
        pred_path = pred_root / f"{work.name}.json"
        if not (work / "blocks.json").exists() or not pred_path.exists():
            continue
        result = audit_paper(work.name, work, json.loads(pred_path.read_text(encoding="utf-8")))
        total = sum(row["total"] for row in result["coverage"])
        covered = sum(row["covered"] for row in result["coverage"])
        ratio = covered / total if total else 0.0
        print(f"{work.name:18} {result['cards']:5} {len(result['gaps']):5} "
              f"{len(result['sliced']):7} {len(result['foreign']):8} "
              f"{len(result['no_figure']):6} {ratio:6.1%}")
        for note in result["notes"]:
            print(f"    note: {note}")
        if not args.expected_only:
            for item in result["sliced"]:
                print(f"    sliced  card#{item['card']} Q{item['number']} p{item['page']} "
                      f"{item['side']:4} edge={item['edge']} lost={item['lost']} "
                      f"({item['lost_units']} units)")
            for item in result["foreign"]:
                print(f"    foreign card#{item['card']} Q{item['number']} holds "
                      f"{item['foreign']}. at p{item['page']}: {item['text']!r}")
            for item in result["no_figure"]:
                print(f"    no-figure card#{item['card']} Q{item['number']} p{item['page']} "
                      f"says 如图 but carries none: {item['text']!r}")
        failures += len(result["sliced"]) + len(result["foreign"])
        warned += len(result["no_figure"])

    print(f"\nproblems: {failures}   cards awaiting a figure: {warned}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
