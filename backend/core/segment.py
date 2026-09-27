"""按题切分：题号起点 + 分栏几何。

这里不以 MinerU 文字框为归题单位。文字框只用来找三样东西：
印刷题号的起点、大题标题（"一、选择题"）和候选配图。
每道题的范围是"本题题号起点 → 下一题（或下一个大题标题）起点"之间的整块版面，
按阅读顺序（页 → 栏 → 自上而下）连续，可以跨栏、跨页。

所以一个横跨两题的文字框、框外漏读的印刷行、混在框里的手写，都不影响切题：
切的是原卷图像，不是文字框。

坐标一律是页面归一化 0–1000（与 MinerU content_list 相同）。
本模块是纯函数，不依赖 Django，便于离线验证。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NUMBER_RE = re.compile(r"(?<![\d.．A-Za-z\\^_{])(\d{1,2})\s*[.．、]{1,2}(?!\d)")
HEADING_RE = re.compile(r"^\s*(?:[一二三四五六七八九十]{1,3}\s*[、.．]|第[一二三四五六七八九十]+部分|[ⅠⅡⅢⅣ]+\s*[、.．])")
NON_CONTENT = {"header", "footer", "page_number", "page_footnote", "aside_text"}
FIGURE_TYPES = {"image", "table", "chart"}
SECTION_TYPES = (
    ("多选", "multiple_choice"), ("多项选择", "multiple_choice"), ("不定项", "multiple_choice"),
    ("单选", "single_choice"), ("单项选择", "single_choice"), ("选择", "single_choice"),
    ("填空", "fill_blank"), ("解答", "free_response"), ("计算", "free_response"),
    ("证明", "free_response"), ("应用", "free_response"), ("简答", "free_response"),
)
CJK = re.compile(r"[一-鿿（(【]")
START_PAD = 9      # 题号上方留白
END_GAP = 2        # 下一题起点上方留白
COLUMN_GAP = 120   # 两栏左边距至少相差多少才算不同栏


@dataclass
class Start:
    number: int
    page: int
    x: float
    y: float
    seq: int | None = None        # 来源文字框；None 表示由视觉模型定位
    at_start: bool = True
    score: float = 1.0
    source: str = "mineru"        # mineru | repaired | located | manual
    col: int = 0

    def key(self) -> tuple:
        return (self.page, self.col, self.y)


@dataclass
class Layout:
    page_count: int
    splits: dict[int, list[float]]                 # 每页的分栏线 x
    slots: list[dict]                              # 阅读顺序的 (页, 栏) 版面及其内容上下沿
    headings: list[dict] = field(default_factory=list)
    candidates: list[Start] = field(default_factory=list)


def _center(bbox: list[float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def column_of(splits: list[float], x: float) -> int:
    return sum(1 for split in splits if x >= split)


def _column_bounds(splits: list[float], col: int) -> tuple[float, float]:
    edges = [0.0, *splits, 1000.0]
    return edges[col], edges[col + 1]


def _candidates(blocks: list[dict]) -> list[Start]:
    found: list[Start] = []
    for block in blocks:
        bbox = block.get("bbox")
        text = str(block.get("text") or "")
        if not bbox or not text.strip() or block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        stripped = text.lstrip(" $　")
        lead = len(text) - len(stripped)
        for match in NUMBER_RE.finditer(text):
            number = int(match.group(1))
            if number == 0:
                continue
            at_start = match.start() <= lead
            after = text[match.end():match.end() + 6].lstrip(" $")
            looks_like_question = bool(after) and (CJK.match(after) is not None or after[:1] in "如已设若在下对")
            if not at_start and not looks_like_question:
                continue
            score = 1.0
            if at_start:
                score += 2.0
            if looks_like_question:
                score += 1.0
            if re.match(r"\s*[（(]\s*\d+\s*分", text[match.end():match.end() + 10]):
                score += 1.5
            if block.get("type") == "equation":
                score -= 1.5
            height = bbox[3] - bbox[1]
            y = bbox[1]
            if not at_start and height > 40 and len(text) > 0:
                # 题号在框中间（常见于手写与题号挤在同一框）：按字符位置估算所在行。
                y = bbox[1] + height * match.start() / max(1, len(text))
                y = max(bbox[1], y - 6)
            found.append(Start(
                number=number, page=int(block["page_idx"]), x=float(bbox[0]), y=float(y),
                seq=block.get("seq"), at_start=at_start, score=score,
            ))
    return found


def _headings(blocks: list[dict]) -> list[dict]:
    result = []
    for block in blocks:
        text = str(block.get("text") or "")
        if block.get("bbox") and HEADING_RE.match(text) and block.get("type") not in NON_CONTENT | FIGURE_TYPES:
            result.append({"page": int(block["page_idx"]), "x": block["bbox"][0], "y": block["bbox"][1],
                           "text": text.strip()[:80], "seq": block.get("seq")})
    return result


def _cluster_margins(xs: list[float]) -> list[float]:
    clusters: list[list[float]] = []
    for x in sorted(xs):
        if clusters and x - clusters[-1][-1] <= COLUMN_GAP:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    # 只有一道题的栏也算（例如右栏只剩最后一题）；是否真是分栏由 _valid_split 逐页验证。
    return [min(cluster) for cluster in clusters]


def _valid_split(page_idx: int, split: float, blocks: list[dict]) -> bool:
    """这条竖线真的是两栏之间的空隙吗？

    必须在页面中部（排除装订边距造成的"假分栏"：单栏卷的单双页左边距不同），
    两侧都有内容，而且几乎没有文字块横跨它。
    """
    if not 250 <= split <= 750:
        return False
    content = [b["bbox"] for b in blocks
               if b.get("bbox") and int(b["page_idx"]) == page_idx and b.get("type") not in NON_CONTENT]
    if len(content) < 3:
        return False
    left = sum(1 for bbox in content if _center(bbox)[0] < split)
    right = len(content) - left
    crossing = sum(1 for bbox in content if bbox[0] < split - 8 and bbox[2] > split + 8)
    return left >= 1 and right >= 1 and crossing <= max(2, 0.15 * len(content))


def _splits_from(starts: list[Start], pages: list[dict], blocks: list[dict]) -> dict[int, list[float]]:
    """逐页判断分栏。本页题号只有一列时，借用全卷的候选分栏线，但仍要逐页验证。"""
    global_margins = _cluster_margins([start.x for start in starts])
    result = {}
    for page in pages:
        page_idx = page["page_idx"]
        own = _cluster_margins([start.x for start in starts if start.page == page_idx])
        margins = own if len(own) > 1 else global_margins
        result[page_idx] = [split for split in (max(0.0, m - 15) for m in margins[1:])
                            if _valid_split(page_idx, split, blocks)]
    return result


def _slots(pages: list[dict], blocks: list[dict], splits: dict[int, list[float]]) -> list[dict]:
    slots = []
    for page in pages:
        page_idx = page["page_idx"]
        page_splits = splits.get(page_idx, [])
        last = len(page_splits)
        for col in range(last + 1):
            x0, x1 = _column_bounds(page_splits, col)
            inside = [
                block["bbox"] for block in blocks
                if block.get("bbox") and int(block["page_idx"]) == page_idx
                and block.get("type") not in NON_CONTENT
                and column_of(page_splits, _center(block["bbox"])[0]) == col
            ]
            if not inside:
                continue
            # 栏与栏之间按分栏线切，不按文字框收窄：框外漏读的字（如被扫描压淡的题号"2"）也要留在图里。
            slots.append({
                "page": page_idx, "col": col,
                "x0": x0 if col > 0 else max(0.0, min(b[0] for b in inside) - 20),
                "x1": x1 if col < last else min(1000.0, max(b[2] for b in inside) + 20),
                "top": max(0.0, min(b[1] for b in inside) - 4), "bottom": min(1000.0, max(b[3] for b in inside) + 4),
            })
    return slots


def _chain(candidates: list[Start]) -> list[Start]:
    """在阅读顺序里挑出最可信的一串递增题号（允许缺号，缺号扣分）。"""
    if not candidates:
        return []
    order = sorted(candidates, key=Start.key)
    best: list[tuple[float, int]] = []
    for i, current in enumerate(order):
        value, previous = current.score - min(3.0, 0.15 * (current.number - 1)), -1
        for j in range(i):
            if order[j].number >= current.number:
                continue
            gap = current.number - order[j].number - 1
            candidate = best[j][0] + current.score - 1.2 * gap
            if candidate > value:
                value, previous = candidate, j
        best.append((value, previous))
    index = max(range(len(order)), key=lambda k: (best[k][0], -k))
    chain = []
    while index >= 0:
        chain.append(order[index])
        index = best[index][1]
    return list(reversed(chain))


def _repair_gaps(chain: list[Start], candidates: list[Start]) -> list[Start]:
    """缺号时，若两题之间恰有一个候选、其数字是缺号的末位（如把"23."读成"3."），按缺号采用。"""
    result: list[Start] = []
    for index, start in enumerate(chain):
        result.append(start)
        if index + 1 >= len(chain):
            continue
        following = chain[index + 1]
        missing = list(range(start.number + 1, following.number))
        if not missing:
            continue
        between = [c for c in candidates if start.key() < c.key() < following.key() and c.at_start]
        for number in missing:
            fits = [c for c in between if str(number).endswith(str(c.number)) and c.number != number]
            if len(fits) == 1:
                fixed = fits[0]
                result.append(Start(number=number, page=fixed.page, x=fixed.x, y=fixed.y, seq=fixed.seq,
                                    at_start=True, score=fixed.score, source="repaired", col=fixed.col))
                between = [c for c in between if c is not fixed and c.key() > fixed.key()]
    return sorted(result, key=Start.key)


def analyse(pages: list[dict], blocks: list[dict]) -> tuple[Layout, list[Start]]:
    """返回版面与题号起点（尚未补缺号）。"""
    candidates = _candidates(blocks)
    headings = _headings(blocks)
    strong = [c for c in candidates if c.at_start and c.score >= 3]
    splits = _splits_from(strong or candidates, pages, blocks)

    def assign(items: list[Start]) -> None:
        for item in items:
            item.col = column_of(splits.get(item.page, []), item.x + 1)

    assign(candidates)
    for heading in headings:
        heading["col"] = column_of(splits.get(heading["page"], []), heading["x"] + 1)
    # 说明文字里的"1. 答卷前……"：第一个"一、"大题标题之前的候选不算题目，只要标题之后另有第 1 题。
    first_section = next((h for h in sorted(headings, key=lambda h: (h["page"], h["col"], h["y"]))
                          if h["text"].startswith("一")), None)
    if first_section is not None:
        section_key = (first_section["page"], first_section["col"], first_section["y"])
        if any(c.number == 1 and c.key() > section_key for c in candidates):
            candidates = [c for c in candidates if c.key() > section_key]
    chain = _chain(candidates)
    # 用选中的题号重新估计分栏，再选一次（手写数字可能在第一次估计时混入）。
    if len(chain) >= 3:
        splits = _splits_from(chain, pages, blocks)
        assign(candidates)
        for heading in headings:
            heading["col"] = column_of(splits.get(heading["page"], []), heading["x"] + 1)
        chain = _chain(candidates)
    chain = _repair_gaps(chain, candidates)
    layout = Layout(page_count=len(pages), splits=splits, slots=_slots(pages, blocks, splits),
                    headings=headings, candidates=candidates)
    return layout, chain


def numbering_scopes(pages: list[dict], blocks: list[dict]) -> list[dict]:
    """Find monotonic question-number runs without discarding later restarts.

    ``analyse`` intentionally returns the single strongest increasing chain for
    one exam.  A book can contain several exercises whose numbering restarts on
    the very same page (for example ``1, 2, 1, 2``).  This helper keeps those
    runs as separate source scopes using only MinerU's existing blocks; it adds
    no model call and therefore no recognition time.
    """

    if not pages or not blocks:
        return []
    layout, selected = analyse(pages, blocks)
    reliable = sorted(
        (item for item in layout.candidates if item.at_start and item.score >= 3.0),
        key=Start.key,
    )
    if not reliable:
        reliable = sorted(selected, key=Start.key)
    if not reliable:
        return []

    runs: list[list[Start]] = [[]]
    seen: set[int] = set()
    maximum: int | None = None
    for current in reliable:
        restart = bool(
            runs[-1]
            and maximum is not None
            and current.number <= maximum
            and (current.number in seen or current.number <= 3)
        )
        if restart:
            runs.append([])
            seen = set()
            maximum = None
        runs[-1].append(current)
        seen.add(current.number)
        maximum = current.number if maximum is None else max(maximum, current.number)

    first_page = min(int(page["page_idx"]) for page in pages)
    last_page = max(int(page["page_idx"]) for page in pages)
    scopes: list[dict] = []
    for index, run in enumerate(runs):
        first = run[0]
        following = runs[index + 1][0] if index + 1 < len(runs) else None
        page_start = first_page if index == 0 else first.page
        # A restart on a later page is a clean page boundary. Only a restart on
        # the same physical page needs that page to belong to both source scopes.
        page_end = (
            following.page if following is not None and following.page == run[-1].page
            else following.page - 1 if following is not None
            else last_page
        )
        scopes.append({
            "sequence": index,
            "pages": list(range(page_start, page_end + 1)),
            # Keep headings/front matter in the first scope. Later scopes begin
            # exactly at the restart block, so two runs on one page stay apart.
            "seq_start": None if index == 0 else first.seq,
            "seq_end": (following.seq - 1) if following is not None
            and isinstance(following.seq, int) else None,
            "first_number": first.number,
            "start_page": first.page,
        })
    return scopes


def missing_numbers(starts: list[Start]) -> list[tuple[int, Start]]:
    """缺号及其所在的前一题（缺号的题目内容此时被并在前一题的范围里）。"""
    ordered = sorted(starts, key=Start.key)
    result = []
    for current, following in zip(ordered, ordered[1:]):
        for number in range(current.number + 1, following.number):
            result.append((number, current))
    return result


def _slot_index(layout: Layout, page: int, col: int) -> int | None:
    for index, slot in enumerate(layout.slots):
        if slot["page"] == page and slot["col"] == col:
            return index
    return None


def _section_type(text: str) -> str:
    for word, kind in SECTION_TYPES:
        if word in text:
            return kind
    return "unknown"


def question_regions(layout: Layout, start: Start, stop: tuple | None) -> list[dict]:
    """从 start 到 stop（阅读顺序中的下一个起点或标题；None 表示卷末）之间的版面矩形。"""
    begin = _slot_index(layout, start.page, start.col)
    if begin is None:
        return []
    regions = []
    for index in range(begin, len(layout.slots)):
        slot = layout.slots[index]
        slot_key = (slot["page"], slot["col"])
        if stop is not None and slot_key > (stop[0], stop[1]):
            break
        top = max(slot["top"], start.y - START_PAD) if index == begin else slot["top"]
        bottom = slot["bottom"]
        if stop is not None and slot_key == (stop[0], stop[1]):
            bottom = min(bottom, stop[2] - END_GAP)
        if bottom - top >= 8:
            regions.append({"page_idx": slot["page"], "bbox": [round(slot["x0"], 1), round(top, 1),
                                                               round(slot["x1"], 1), round(bottom, 1)]})
        if stop is not None and slot_key == (stop[0], stop[1]):
            break
    return regions


def overlaps_regions(page_idx: int, bbox: list[float], regions: list[dict], share: float = 0.2) -> bool:
    """配图有至少两成面积落在本题范围里，就作为本题的候选图（图常跨过下一题的起点）。"""
    area = max(1e-6, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
    for region in regions:
        if region["page_idx"] != page_idx:
            continue
        x0, y0, x1, y1 = region["bbox"]
        w = min(x1, bbox[2]) - max(x0, bbox[0])
        h = min(y1, bbox[3]) - max(y0, bbox[1])
        if w > 0 and h > 0 and w * h / area >= share:
            return True
    return False


def text_blocks_in(blocks: list[dict], regions: list[dict]) -> frozenset:
    """范围内的文字块编号集合：用来判断重新切题后一道题的内容有没有变。"""
    found = set()
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox or block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        cx, cy = _center(bbox)
        if any(r["page_idx"] == int(block["page_idx"]) and r["bbox"][0] <= cx <= r["bbox"][2]
               and r["bbox"][1] <= cy <= r["bbox"][3] for r in regions):
            found.add(block.get("seq"))
    return frozenset(found)


def _fallback_regions(layout: Layout, start: Start, stop: tuple | None) -> list[dict]:
    """按栏没切出范围时（版面特殊），退回整页宽度：从本题题号到下一题或本页末尾。"""
    same_page = [slot for slot in layout.slots if slot["page"] == start.page]
    if not same_page:
        return []
    top = max(0.0, start.y - START_PAD)
    bottom = max(slot["bottom"] for slot in same_page)
    if stop is not None and stop[0] == start.page and stop[2] > start.y:
        bottom = min(bottom, stop[2] - END_GAP)
    if bottom - top < 8:
        bottom = min(1000.0, top + 60)
    x0 = min(slot["x0"] for slot in same_page)
    x1 = max(slot["x1"] for slot in same_page)
    return [{"page_idx": start.page, "bbox": [round(x0, 1), round(top, 1), round(x1, 1), round(bottom, 1)]}]


def build_questions(layout: Layout, starts: list[Start], blocks: list[dict]) -> list[dict]:
    ordered = sorted(starts, key=Start.key)
    headings = sorted(layout.headings, key=lambda h: (h["page"], h["col"], h["y"]))
    stops = sorted(
        [(s.page, s.col, s.y) for s in ordered] + [(h["page"], h["col"], h["y"]) for h in headings]
    )
    figures = [b for b in blocks if b.get("bbox") and b.get("type") in FIGURE_TYPES]
    questions = []
    for start in ordered:
        here = (start.page, start.col, start.y)
        stop = next((item for item in stops if item > here), None)
        regions = question_regions(layout, start, stop) or _fallback_regions(layout, start, stop)
        section = ""
        for heading in headings:
            if (heading["page"], heading["col"], heading["y"]) < here:
                section = heading["text"]
        figure_blocks = [{"seq": block.get("seq"), "page_idx": int(block["page_idx"]), "bbox": list(block["bbox"])}
                         for block in figures if overlaps_regions(int(block["page_idx"]), block["bbox"], regions)]
        questions.append({
            "number": start.number,
            "start": {"page": start.page, "col": start.col, "y": round(start.y, 1), "source": start.source},
            "regions": regions,
            "figure_candidates": figure_blocks,
            "section": section,
            "question_type": _section_type(section),
        })
    return questions


def segment(pages: list[dict], blocks: list[dict]) -> dict:
    """一次性切题（不补缺号）。缺号由调用方用视觉模型定位后再调用 build_questions。"""
    layout, starts = analyse(pages, blocks)
    return {
        "layout": layout,
        "starts": starts,
        "missing": missing_numbers(starts),
        "questions": build_questions(layout, starts, blocks),
    }


def region_regions_between(layout: Layout, start: Start, following: Start | None) -> list[dict]:
    stop = (following.page, following.col, following.y) if following else None
    return question_regions(layout, start, stop)
