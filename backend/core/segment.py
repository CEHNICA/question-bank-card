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
BOOK_MAX_CARD_PAGES = 4

# Textbooks use a different visual grammar from exam papers.  In particular,
# ``3. 单调性`` is a subsection title while ``例 3 ...`` is a real worked
# example even though it has no dot after the number.  Keep these expressions
# separate from NUMBER_RE so the mature exam-paper path remains unchanged.
BOOK_EXAMPLE_RE = re.compile(
    r"^\s*(?:\*{1,2}\s*)?(?:例题|例|Example|Ex\.?)\s*"
    r"([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*(?:[.．、:：])?\s*(.*)$",
    re.I | re.S,
)
BOOK_EXAMPLE_REFERENCE_RE = re.compile(
    r"^(?:中(?:的|命题)?|的(?:结果|证明|解法|结论)|参见|见上?例)"
)
BOOK_NUMBERED_RE = re.compile(r"^\s*(\d{1,3})\s*[.．、]\s*(.*)$", re.S)
BOOK_PARENTHESIZED_RE = re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[)）]\s*(.*)$", re.S)
BOOK_EMBEDDED_NUMBERED_RE = re.compile(
    # ``在 4.2.1 的问题 1 中`` contains dots but no new printed question.
    # At a genuine sentence boundary the dot is not immediately preceded by
    # another digit, so reject only that narrow decimal/section-reference case.
    r"(?<=[？?。．.!！；;])(?<!\d\.)\s*(\d{1,3})\s*[.．、]\s*(.+)$",
    re.S,
)
BOOK_CHOICE_BUNDLE_RE = re.compile(
    r"^\s*\d{1,3}\s*[.．、]\s*(?:单项?选择题|多项选择题|选择题)\s*$"
)
BOOK_PRACTICE_RE = re.compile(
    r"^\s*(?:课后)?(?:练习|习题|巩固练习|综合练习|复习题|复习参考题)"
    r"(?:\s*\d+(?:\.\d+)*)?\s*[：:]?\s*$"
)
# A worked example ends before its printed solution.  Keep this deliberately
# label-shaped: ``证明下列恒等式`` is part of a question, while ``证明：`` or a
# standalone ``证明`` is a solution heading.  English textbook labels are
# included for the same deterministic, no-extra-model-call behaviour.
BOOK_ANALYSIS_RE = re.compile(
    r"^\s*(?:(?:分析|解析|解答?|证明|(?:解法|证法)(?:\s*[一二三四五六七八九十0-9]+)?)"
    r"\s*(?:[：:]|$)|"
    r"(?:Analysis|Solution|Proof)\s*(?:[.:：]|$))",
    re.I,
)
BOOK_EMBEDDED_ANALYSIS_RE = re.compile(
    r"(?:^|\n|(?<=[。．.!?！？；;]))\s*"
    r"(?:(?:分析|解析|解答?|证明|(?:解法|证法)(?:\s*[一二三四五六七八九十0-9]+)?)"
    r"\s*(?:[：:]|$)|"
    r"(?:Analysis|Solution|Proof)\s*(?:[.:：]|$))",
    re.I,
)
BOOK_DECIMAL_HEADING_RE = re.compile(r"^\s*\d+(?:\.\d+){1,3}\s+\S")
BOOK_NAMED_BOUNDARY_RE = re.compile(
    r"^\s*(?:第[一二三四五六七八九十0-9]+[章节篇单元]|"
    r"思考(?:\s*[一二三四五六七八九十0-9]+)?|"
    r"探究(?:\s*[一二三四五六七八九十0-9]+)?|探究与发现|"
    r"阅读与思考|信息技术应用|本章小结|小结|补集|"
    r"部分中英文词汇索引|中英文词汇索引|索引|后记|附录)\s*[：:]?\s*$"
)
BOOK_PROCEDURE_INTRO_RE = re.compile(
    r"(?:一般)?步骤如下\s*[：:]?\s*$|过程可以概括为\s*[：:]?\s*$"
)
BOOK_PROCEDURE_TRANSITION_RE = re.compile(
    r"^\s*下面在探究\s*[一二三四五六七八九十0-9]+\s*的基础上继续探究\s*[。．.]?\s*$"
)
# These are numbered document fields or exposition stages, not independent
# exercises.  The expressions intentionally require a highly distinctive
# prefix; ordinary imperatives such as ``1. 求……`` are left untouched.
BOOK_NON_CARD_NUMBERED_RE = re.compile(
    r"^(?:(?:标题|提要或前言|正文|参考文献)\s*[：:]|"
    r"观察实际情景\s*[，,]?\s*发现和提出问题\s*$|"
    r"(?:收集数据|分析数据|建立模型|检验模型|求解问题|交流展示|"
    r"研究结果|收获与体会)\s*$|"
    r"对此研究的评价(?:\s*[（(].*?[)）])?\s*$)"
)
BOOK_INSTRUCTION_SECTION_RE = re.compile(r"信息技术应用")
BOOK_STRONG_NUMBERED_QUESTION_RE = re.compile(
    r"[？?]|[（(]\s*[）)]|_{2,}|"
    r"(?:求|求证|证明|计算|判断|填空|填表|回答|为什么|为何)"
)
BOOK_QUESTION_CUE_RE = re.compile(
    r"(?:[？?]|[（(]\s*[）)]|_{2,}|"
    r"已知|若|设|求|证明|求证|判断|计算|选择|填空|填表|画出|作出|写出|"
    r"下列|如图|观察|说明|比较|解答|研究|借助|利用|回答|表示|举出|列出|"
    r"指出|估计|设计|构建|完成|讨论|化简|解方程|为什么|为何)"
)
BOOK_CONTEXT_PROBLEM_HEADING_RE = re.compile(
    r"^\s*观察实际情景\s*[，,]?\s*发现和提出问题\s*$"
)
BOOK_REFERENCED_CONTEXT_QUESTION_RE = re.compile(
    r"问题\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*中"
)
BOOK_NUMBERED_VISUAL_RE = re.compile(
    r"(?:如|见|观察|根据|依据|结合|参照|参考)?\s*"
    r"(?:图|表)\s*[0-9一二三四五六七八九十]+(?:\.[0-9]+)*(?:\s*[-－—]\s*[0-9]+)?",
    re.I,
)
BOOK_LOCAL_FIGURE_REFERENCE_RE = re.compile(r"(?:如|见)?\s*图\s*[（(]\s*(\d{1,2})\s*[)）]")
BOOK_LOCAL_FIGURE_CAPTION_RE = re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[)）]\s*$")
BOOK_QUESTION_FIGURE_CAPTION_RE = re.compile(r"^\s*[（(]\s*第\s*(\d{1,3})\s*题\s*[)）]\s*$")

# MinerU 偶尔能读到第一题的完整正文，却漏掉行首很淡的 ``1.``。
# 这里的词只用于判断“第 2 题以前是否确实还有一道题”，不是用来理解题目。
# 阈值有意保守：宁可提示人工核对，也不能把一份本来就从第 2 题开始的节选
# 凭空补成第 1 题。
_LEADING_STEM_RE = re.compile(
    r"^(?:已知|若|设|下列|如图|根据|阅读|观察|判断|计算|证明|求|关于|对于|在\S{0,24}(?:中|上))"
)
_LEADING_SOURCE_RE = re.compile(
    r"^\s*[\[【（(]\s*(?:20\d{2}|\d{2,4}\s*年|[^\]】）)]{0,32}(?:高考|中考|联考|期中|期末|月考|模拟))",
    re.I,
)
_LEADING_SUBQUESTION_RE = re.compile(r"^\s*[（(]\s*([12])\s*[)）]", re.M)
_LEADING_OPTION_RE = re.compile(r"(?:^|\s)[A-DＡ-Ｄ]\s*[.．、:]", re.I)
_LEADING_QUESTION_MARK_RE = re.compile(r"[？?]|[（(]\s*[）)]|(?:求|证明|计算|判断|选择|填空)")
# Numbered exam instructions (“注意事项：1．答题前…”) are not questions.  Both
# a notice header and instruction vocabulary are required, so an ordinary
# question that merely mentions “考试” is never discarded.
_NOTICE_HEADER_RE = re.compile(r"^\s*(?:注意事项|考生须知|答题须知|考试须知|答卷须知)\s*[：:]?")
_INSTRUCTION_RE = re.compile(
    r"答题前|答卷前|答题卡|答题纸|准考证|考生号|考籍号|条形码|考试结束|签字笔|2B\s*铅笔|"
    r"试题卷|试卷上|本试卷|考试时间|草稿纸|选涂|涂黑|作答无效|答题无效|交回"
)
# MinerU occasionally drops the full-width dot of the very first number
# (“1．设 z=…” comes back as “1 设 z=…”).
_BARE_LEADING_ONE_RE = re.compile(r"^\s*1\s+(?=[\u4e00-\u9fff])")
_LEADING_NON_QUESTION_RE = re.compile(
    r"(?:注意事项|答卷前|考试时间|满分|姓名|班级|考号|密封线|请将答案|"
    r"本题共\s*\d+\s*小题|每小题\s*\d+\s*分|选择题|填空题|解答题|参考公式)"
)


@dataclass
class Start:
    number: int
    page: int
    x: float
    y: float
    seq: int | None = None        # 来源文字框；None 表示由视觉模型定位
    at_start: bool = True
    score: float = 1.0
    source: str = "mineru"        # mineru | repaired | inferred | located | manual
    col: int = 0
    source_kind: str = "question"  # question | example | exercise
    anchor_seq: int | None = None
    marker_text: str = ""

    def key(self) -> tuple:
        return (self.page, self.col, self.y)


@dataclass
class Layout:
    page_count: int
    splits: dict[int, list[float]]                 # 每页的分栏线 x
    slots: list[dict]                              # 阅读顺序的 (页, 栏) 版面及其内容上下沿
    headings: list[dict] = field(default_factory=list)
    candidates: list[Start] = field(default_factory=list)
    # Extra non-card stops used by textbook segmentation. Structural dividers
    # stop every card; analysis/solution labels stop worked examples only.
    boundaries: list[dict] = field(default_factory=list)


@dataclass
class BookMarker:
    kind: str                       # example_start | exercise_start | section_heading | ...
    page: int
    x: float
    y: float
    seq: int | None
    text: str
    number: int | None = None
    col: int = 0
    question_shaped: bool = False

    def key(self) -> tuple:
        return (self.page, self.col, self.y)


@dataclass(frozen=True)
class LeadingQuestionCheck:
    """本地检查“首题题号漏读”的结果。

    ``status`` 只有 ``repaired``、``suspected`` 或 ``none``。调用方可把
    ``message`` 放进任务说明，让低置信情形也不会静默显示为完整。
    """

    status: str = "none"
    first_detected: int | None = None
    candidate_seq: int | None = None
    message: str = ""


def _center(bbox: list[float]) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def column_of(splits: list[float], x: float) -> int:
    return sum(1 for split in splits if x >= split)


def _column_bounds(splits: list[float], col: int) -> tuple[float, float]:
    edges = [0.0, *splits, 1000.0]
    return edges[col], edges[col + 1]


# MinerU sometimes wraps runs of a line in inline HTML (“<sub>12.</sub> <sub>已知…”),
# which hides the printed number from the start-of-line test.
_INLINE_TAG_RE = re.compile(r"</?(?:sub|sup|span|b|i|u|em|strong)\b[^>]*>", re.I)


def _plain_block_text(value: object) -> str:
    return _INLINE_TAG_RE.sub("", str(value or ""))


def _candidates(blocks: list[dict]) -> list[Start]:
    found: list[Start] = []
    for block in blocks:
        bbox = block.get("bbox")
        text = _plain_block_text(block.get("text"))
        if not bbox or not text.strip() or block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        stripped = text.lstrip(" $　")
        lead = len(text) - len(stripped)
        for match in NUMBER_RE.finditer(text):
            number = int(match.group(1))
            at_start = match.start() <= lead
            if number == 0:
                # A scan that clipped the binding edge turns “20.” into “0.”.
                # Keep it only as evidence for gap repair; it never joins a chain.
                if at_start:
                    found.append(Start(
                        number=0, page=int(block["page_idx"]), x=float(bbox[0]), y=float(bbox[1]),
                        seq=block.get("seq"), at_start=True, score=0.2,
                    ))
                continue
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


_UNNUMBERED_START_RE = re.compile(r"^\s*[（(]\s*(?:本题)?满分\s*\d{1,2}\s*分\s*[)）]")


def _unnumbered_starts(blocks: list[dict]) -> list[Start]:
    """Blocks that open like a question but lost their printed number entirely."""
    found = []
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox or block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        if _UNNUMBERED_START_RE.match(_plain_block_text(block.get("text"))):
            found.append(Start(
                number=0, page=int(block["page_idx"]), x=float(bbox[0]), y=float(bbox[1]),
                seq=block.get("seq"), at_start=True, score=0.5, source="unnumbered",
            ))
    return found


_BARE_NUMBER_START_RE = re.compile(r"^\s*(\d{1,2})\s+(?=[\u4e00-\u9fff])")


def _bare_number_starts(blocks: list[dict]) -> list[Start]:
    """“14 如图，…”: a printed number whose dot MinerU dropped.

    These are used only to fill an exact gap in the numbering (13 → ? → 15),
    never to start or extend a chain on their own.
    """
    found = []
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox or block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        match = _BARE_NUMBER_START_RE.match(_plain_block_text(block.get("text")).lstrip(" $　"))
        if match and int(match.group(1)) > 0:
            found.append(Start(
                number=int(match.group(1)), page=int(block["page_idx"]), x=float(bbox[0]), y=float(bbox[1]),
                seq=block.get("seq"), at_start=True, score=0.5, source="bare",
            ))
    return found


def _drop_instruction_candidates(candidates: list[Start], blocks: list[dict]) -> list[Start]:
    """Remove the numbered items of an exam's notice section (注意事项).

    Only a leading run is removed: it must follow a notice header block (or
    share its block) and every removed item must use instruction vocabulary.
    """
    if not candidates:
        return candidates
    headers = [
        (int(block["page_idx"]), float(block["bbox"][1]))
        for block in blocks
        if block.get("bbox") and _NOTICE_HEADER_RE.match(str(block.get("text") or ""))
    ]
    if not headers:
        return candidates
    text_by_seq = {block.get("seq"): str(block.get("text") or "") for block in blocks}
    ordered = sorted(candidates, key=lambda item: (item.page, item.y))
    first_header = min(headers)
    leading: list[Start] = []
    for candidate in ordered:
        if (candidate.page, candidate.y) < first_header:
            continue
        text = text_by_seq.get(candidate.seq, "")
        if _INSTRUCTION_RE.search(text) or _NOTICE_HEADER_RE.match(text):
            leading.append(candidate)
            continue
        break
    if not leading or len(leading) == len(candidates):
        return candidates
    removed = {id(item) for item in leading}
    return [item for item in candidates if id(item) not in removed]


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
    candidates = [item for item in candidates if item.number > 0]
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


def _repair_gaps(
    chain: list[Start], candidates: list[Start], unnumbered: list[Start] | None = None,
) -> list[Start]:
    """补缺号。

    1. 两题之间恰有一个候选、其数字是缺号的末位（把“23.”读成“3.”，或扫描裁掉
       装订边把“20.”读成“0.”），按缺号采用。
    2. 仍缺的号，若两题之间恰好有同样数量的“（本题满分 N 分）”开头、题号被整个
       裁掉的块，并且按阅读顺序排进去题号仍递增，就依次补上。
    """
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
        repaired: list[Start] = []
        for number in missing:
            fits = [c for c in between if str(number).endswith(str(c.number)) and c.number != number]
            if len(fits) == 1:
                fixed = fits[0]
                repaired.append(Start(number=number, page=fixed.page, x=fixed.x, y=fixed.y, seq=fixed.seq,
                                      at_start=True, score=fixed.score, source="repaired", col=fixed.col))
                between = [c for c in between if c is not fixed and c.key() > fixed.key()]
        remaining = [number for number in missing if number not in {item.number for item in repaired}]
        used = {item.seq for item in repaired}
        for number in list(remaining):
            exact = [
                item for item in (unnumbered or [])
                if item.source == "bare" and item.number == number and item.seq not in used
                and start.key() < item.key() < following.key()
            ]
            if len(exact) == 1:
                item = exact[0]
                repaired.append(Start(number=number, page=item.page, x=item.x, y=item.y, seq=item.seq,
                                      at_start=True, score=item.score, source="repaired", col=item.col))
                used.add(item.seq)
                remaining.remove(number)
        repaired.sort(key=Start.key)
        loose = [
            item for item in (unnumbered or [])
            if item.source == "unnumbered" and start.key() < item.key() < following.key()
            and item.seq not in used
        ]
        if remaining and len(loose) == len(remaining):
            trial = sorted(
                repaired + [
                    Start(number=number, page=item.page, x=item.x, y=item.y, seq=item.seq, at_start=True,
                          score=item.score, source="repaired", col=item.col)
                    for number, item in zip(remaining, sorted(loose, key=Start.key))
                ],
                key=Start.key,
            )
            if all(a.number < b.number for a, b in zip(trial, trial[1:])):
                repaired = trial
        result.extend(repaired)
    return sorted(result, key=Start.key)


def analyse(pages: list[dict], blocks: list[dict]) -> tuple[Layout, list[Start]]:
    """返回版面与题号起点（尚未补缺号）。"""
    candidates = _drop_instruction_candidates(_candidates(blocks), blocks)
    unnumbered = _unnumbered_starts(blocks) + _bare_number_starts(blocks)
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
    assign(unnumbered)
    chain = _repair_gaps(chain, candidates, unnumbered)
    layout = Layout(page_count=len(pages), splits=splits, slots=_slots(pages, blocks, splits),
                    headings=headings, candidates=candidates)
    return layout, chain


def _book_number(value: str) -> int | None:
    """Parse the small Arabic/Chinese numbers used in textbook example labels."""
    value = value.strip()
    if value.isdigit():
        number = int(value)
        return number if number > 0 else None
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value)


def _book_block_position(block: dict) -> tuple[int, float, float] | None:
    bbox = block.get("bbox")
    try:
        return int(block["page_idx"]), float(bbox[0]), float(bbox[1])
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _raw_book_markers(blocks: list[dict]) -> list[BookMarker]:
    """Extract typed textbook markers without deciding numbered-line context yet."""
    markers: list[BookMarker] = []
    textual = [
        block for block in blocks
        if block.get("type") not in NON_CONTENT | FIGURE_TYPES | {"equation"}
        and _book_block_position(block) is not None
        and str(block.get("text") or "").strip()
        and isinstance(block.get("seq"), int)
    ]
    textual.sort(key=lambda item: int(item["seq"]))
    neighbours: dict[int, tuple[dict | None, dict | None]] = {}
    for index, item in enumerate(textual):
        neighbours[int(item["seq"])] = (
            textual[index - 1] if index else None,
            textual[index + 1] if index + 1 < len(textual) else None,
        )
    for block in blocks:
        if block.get("type") in NON_CONTENT | FIGURE_TYPES | {"equation"}:
            continue
        position = _book_block_position(block)
        text = str(block.get("text") or "").strip()
        if position is None or not text:
            continue
        plain = text.replace("**", "").strip()
        page, x, y = position
        seq = block.get("seq") if type(block.get("seq")) is int else None

        # Chapter openings and ``思考`` panels often revisit “问题 1/2” from a
        # previous section and ask a new, explicit question.  The decimal
        # section reference (for example 4.2.1) is not its printed number; the
        # referenced problem number is the stable local identity.  A question
        # mark is mandatory so the worked explanation that follows is not
        # promoted as another card.
        previous_block, next_block = neighbours.get(seq, (None, None))
        previous_text = str((previous_block or {}).get("text") or "").replace("**", "").strip()
        next_text = str((next_block or {}).get("text") or "").replace("**", "").strip()
        context_panel = bool(
            re.match(r"^\s*思考(?:\s*[一二三四五六七八九十0-9]+)?\s*[：:]?\s*$", previous_text)
            or BOOK_DECIMAL_HEADING_RE.match(next_text)
        )
        if context_panel and not BOOK_NUMBERED_RE.match(plain) \
                and ("?" in plain or "？" in plain):
            referenced = BOOK_REFERENCED_CONTEXT_QUESTION_RE.search(plain)
            referenced_number = _book_number(referenced.group(1)) if referenced else None
            if referenced_number is not None and BOOK_QUESTION_CUE_RE.search(plain):
                markers.append(BookMarker(
                    kind="context_question", page=page, x=x, y=y, seq=seq,
                    text=text, number=referenced_number, question_shaped=True,
                ))
                markers.append(BookMarker(
                    kind="embedded_question_end", page=page, x=x,
                    y=float(block["bbox"][3]) + END_GAP, seq=seq,
                    text="引用情境问题文字块结束",
                ))

        # MinerU can join the final line of one exercise and the next printed
        # top-level number into one text block.  Recover one embedded anchor
        # only when the block does not itself start with a number and the tail
        # has unmistakable question language.  One marker per source block
        # preserves the stable-anchor uniqueness required by re-segmentation.
        if not BOOK_NUMBERED_RE.match(plain):
            embedded_numbered = BOOK_EMBEDDED_NUMBERED_RE.search(plain)
            if embedded_numbered is not None:
                body = embedded_numbered.group(2).strip()
                number = int(embedded_numbered.group(1))
                if number > 0 and BOOK_QUESTION_CUE_RE.search(body):
                    bbox = block["bbox"]
                    embedded_y = y + (float(bbox[3]) - float(bbox[1])) * (
                        embedded_numbered.start(1) / max(1, len(plain))
                    )
                    markers.append(BookMarker(
                        kind="numbered", page=page, x=x, y=embedded_y,
                        seq=seq, text=plain[embedded_numbered.start(1):],
                        number=number, question_shaped=True,
                    ))
                    if body.endswith(("。", "．", ".", "？", "?")) \
                            and not re.search(r"[（(]\s*[）)]", body):
                        markers.append(BookMarker(
                            kind="embedded_question_end", page=page, x=x,
                            y=float(bbox[3]), seq=seq, text="合并文字块内题目结束",
                        ))

        # MinerU normally emits the coloured “分析/解” label as its own block,
        # but it can merge several printed lines. Preserve the same boundary in
        # that case by estimating the label's vertical position within the box.
        embedded_solution = BOOK_EMBEDDED_ANALYSIS_RE.search(plain)
        if embedded_solution is not None and embedded_solution.start() > 0:
            bbox = block["bbox"]
            solution_y = y + (float(bbox[3]) - float(bbox[1])) * (
                embedded_solution.start() / max(1, len(plain))
            )
            markers.append(BookMarker(
                kind="analysis_solution", page=page, x=x, y=solution_y,
                seq=seq, text=plain[embedded_solution.start():][:160],
            ))

        example = BOOK_EXAMPLE_RE.match(plain)
        if example:
            number = _book_number(example.group(1))
            remainder = example.group(2).lstrip()
            if number is not None and not BOOK_EXAMPLE_REFERENCE_RE.match(remainder):
                markers.append(BookMarker(
                    kind="example_start", page=page, x=x, y=y, seq=seq,
                    text=text, number=number, question_shaped=True,
                ))
            else:
                markers.append(BookMarker(
                    kind="example_reference", page=page, x=x, y=y, seq=seq,
                    text=text, number=number,
                ))
            continue

        if BOOK_PRACTICE_RE.match(plain):
            markers.append(BookMarker(
                kind="practice_header", page=page, x=x, y=y, seq=seq, text=text,
            ))
            continue

        if BOOK_CHOICE_BUNDLE_RE.match(plain):
            markers.append(BookMarker(
                kind="choice_bundle_header", page=page, x=x, y=y, seq=seq, text=text,
            ))
            continue

        if BOOK_ANALYSIS_RE.match(plain):
            markers.append(BookMarker(
                kind="analysis_solution", page=page, x=x, y=y, seq=seq, text=text,
            ))
            continue

        if HEADING_RE.match(plain) or BOOK_DECIMAL_HEADING_RE.match(plain) or \
                BOOK_NAMED_BOUNDARY_RE.match(plain) or \
                BOOK_PROCEDURE_INTRO_RE.search(plain) or \
                BOOK_PROCEDURE_TRANSITION_RE.match(plain):
            markers.append(BookMarker(
                kind=("procedure_intro" if BOOK_PROCEDURE_INTRO_RE.search(plain)
                      else "section_heading"),
                page=page, x=x, y=y, seq=seq, text=text,
            ))
            continue

        parenthesized = BOOK_PARENTHESIZED_RE.match(plain)
        if parenthesized:
            number = int(parenthesized.group(1))
            body = parenthesized.group(2).strip()
            # Bare ``(1)`` labels below a printed figure are captions, not
            # card anchors.  A choice-bundle subquestion always has a stem.
            if body:
                markers.append(BookMarker(
                    kind="parenthesized", page=page, x=x, y=y, seq=seq,
                    text=text, number=number,
                    question_shaped=bool(BOOK_QUESTION_CUE_RE.search(body)),
                ))
            continue

        numbered = BOOK_NUMBERED_RE.match(plain)
        if numbered:
            number = int(numbered.group(1))
            if number <= 0:
                continue
            body = numbered.group(2).strip()
            markers.append(BookMarker(
                kind=("non_card_numbered" if BOOK_NON_CARD_NUMBERED_RE.match(body)
                      else "numbered"),
                page=page, x=x, y=y, seq=seq, text=text,
                number=number, question_shaped=bool(BOOK_QUESTION_CUE_RE.search(body)),
            ))

    # In modelling examples a numbered workflow label such as
    # ``1. 观察实际情景，发现和提出问题`` is not itself a card, but the immediately
    # following paragraph can contain the real problem statement.  Recover
    # only this named workflow shape and require an actual question mark; broad
    # promotion of arbitrary unnumbered prose would create many false cards.
    marker_seqs = sorted(
        marker.seq for marker in markers if isinstance(marker.seq, int)
    )
    block_by_seq = {
        block.get("seq"): block for block in blocks
        if isinstance(block.get("seq"), int)
    }
    recovered_context: list[BookMarker] = []
    for marker in markers:
        numbered = BOOK_NUMBERED_RE.match(marker.text.replace("**", "").strip())
        body = numbered.group(2).strip() if numbered else ""
        if marker.kind != "non_card_numbered" or marker.number is None \
                or not BOOK_CONTEXT_PROBLEM_HEADING_RE.match(body) \
                or not isinstance(marker.seq, int):
            continue
        next_marker_seq = next((seq for seq in marker_seqs if seq > marker.seq), None)
        for seq in sorted(value for value in block_by_seq if value > marker.seq):
            if next_marker_seq is not None and seq >= next_marker_seq:
                break
            block = block_by_seq[seq]
            position = _book_block_position(block)
            text = str(block.get("text") or "").strip()
            if position is None or int(block.get("page_idx", -1)) != marker.page:
                break
            if block.get("type") in NON_CONTENT | FIGURE_TYPES | {"equation"} or not text:
                continue
            if ("?" not in text and "？" not in text) or not BOOK_QUESTION_CUE_RE.search(text):
                continue
            page, x, y = position
            recovered_context.append(BookMarker(
                kind="context_question", page=page, x=x, y=y, seq=seq,
                text=text, number=marker.number, question_shaped=True,
            ))
            # The following paragraph is the textbook author's modelling
            # guidance, not part of the question.  Stop exactly at the bottom
            # of the recovered question block; adding END_GAP compensates for
            # ``question_regions`` subtracting the same padding.
            recovered_context.append(BookMarker(
                kind="embedded_question_end", page=page, x=x,
                y=float(block["bbox"][3]) + END_GAP, seq=seq,
                text="情境问题文字块结束",
            ))
            break
    markers.extend(recovered_context)
    return markers


def _assign_book_columns(markers: list[BookMarker], splits: dict[int, list[float]]) -> None:
    for marker in markers:
        marker.col = column_of(splits.get(marker.page, []), marker.x + 1)


def _recover_book_gap_questions(
    markers: list[BookMarker],
    blocks: list[dict],
) -> list[BookMarker]:
    """Recover a missing printed number between two consecutive exercises.

    MinerU can omit a faint ``2.`` printed beside a table.  Recovery requires
    neighbouring exercise numbers ``N`` and ``N+2`` on the same page/column,
    an unnumbered interrogative text block between them, and a table directly
    before that text.  The table becomes the stable source anchor so it belongs
    to the recovered card instead of the preceding one.
    """

    exercises = sorted(
        (marker for marker in markers if marker.kind == "exercise_start"),
        key=BookMarker.key,
    )
    ordered_blocks = sorted(
        (block for block in blocks if isinstance(block.get("seq"), int)),
        key=lambda block: int(block["seq"]),
    )
    recovered: list[BookMarker] = []
    for previous, following in zip(exercises, exercises[1:]):
        if not (
            isinstance(previous.seq, int)
            and isinstance(following.seq, int)
            and previous.page == following.page
            and previous.col == following.col
            and following.number == previous.number + 2
            and 1 < following.seq - previous.seq <= 40
        ):
            continue
        between = [
            block for block in ordered_blocks
            if previous.seq < int(block["seq"]) < following.seq
            and int(block.get("page_idx", -1)) == previous.page
        ]
        question_blocks = [
            block for block in between
            if block.get("type") not in NON_CONTENT | FIGURE_TYPES | {"equation"}
            and ("?" in str(block.get("text") or "") or "？" in str(block.get("text") or ""))
            and BOOK_QUESTION_CUE_RE.search(str(block.get("text") or ""))
            and not BOOK_NUMBERED_RE.match(str(block.get("text") or "").strip())
        ]
        if len(question_blocks) != 1:
            continue
        question = question_blocks[0]
        tables = [
            block for block in between
            if block.get("type") == "table" and int(block["seq"]) < int(question["seq"])
        ]
        if not tables:
            continue
        anchor = tables[-1]
        position = _book_block_position(anchor)
        if position is None:
            continue
        page, x, y = position
        recovered.append(BookMarker(
            kind="exercise_start", page=page, x=x, y=y,
            seq=int(anchor["seq"]), text=str(question.get("text") or "").strip(),
            number=previous.number + 1, col=previous.col, question_shaped=True,
        ))
    return recovered


def _book_marker_stream(
    pages: list[dict],
    blocks: list[dict],
) -> tuple[Layout, list[BookMarker]]:
    """Return a context-aware marker stream for a textbook.

    A numbered line is a card inside an explicit practice area.  Outside that
    area it must look like a question; otherwise it is a subsection boundary.
    Explicit worked examples are always cards and also end a preceding exercise.
    """
    raw = _raw_book_markers(blocks)
    provisional = [
        Start(
            number=marker.number or 1, page=marker.page, x=marker.x, y=marker.y,
            seq=marker.seq, source_kind=("example" if marker.kind == "example_start" else "exercise"),
        )
        for marker in raw
        if marker.kind == "example_start" or marker.kind == "numbered"
    ]
    splits = _splits_from(provisional, pages, blocks)
    _assign_book_columns(raw, splits)
    ordered = sorted(raw, key=BookMarker.key)

    practice = False
    choice_bundle = False
    procedure = False
    instruction_section = False
    resolved: list[BookMarker] = []
    for marker in ordered:
        if marker.kind == "practice_header":
            practice = True
            choice_bundle = procedure = instruction_section = False
            resolved.append(marker)
            continue
        if marker.kind == "choice_bundle_header":
            practice = False
            choice_bundle = True
            procedure = instruction_section = False
            marker.kind = "section_heading"
            resolved.append(marker)
            continue
        if marker.kind == "procedure_intro":
            practice = choice_bundle = instruction_section = False
            procedure = True
            marker.kind = "section_heading"
            resolved.append(marker)
            continue
        if marker.kind == "example_start":
            practice = choice_bundle = procedure = instruction_section = False
            resolved.append(marker)
            continue
        if marker.kind == "section_heading":
            practice = choice_bundle = procedure = False
            instruction_section = bool(BOOK_INSTRUCTION_SECTION_RE.search(marker.text))
            resolved.append(marker)
            continue
        if marker.kind == "non_card_numbered":
            marker.kind = "section_heading"
            practice = choice_bundle = False
            resolved.append(marker)
            continue
        if marker.kind == "context_question":
            marker.kind = "exercise_start"
            practice = choice_bundle = procedure = instruction_section = False
            resolved.append(marker)
            continue
        if marker.kind == "parenthesized":
            if choice_bundle and marker.question_shaped:
                marker.kind = "exercise_start"
                resolved.append(marker)
            # Parenthesized lines elsewhere are subquestions inside a card.
            continue
        if marker.kind == "numbered":
            numbered = BOOK_NUMBERED_RE.match(marker.text.replace("**", "").strip())
            body = numbered.group(2).strip() if numbered else ""
            # A new top-level number always ends a preceding ``1. 选择题``
            # bundle.  Its parenthesized children have already become their
            # own stable, source-anchored cards.
            choice_bundle = False
            short_title = bool(
                body
                and len(body) <= 24
                and not marker.question_shaped
                and not body.endswith(("：", ":", "？", "?"))
            )
            if procedure:
                marker.kind = "section_heading"
            elif instruction_section and not BOOK_STRONG_NUMBERED_QUESTION_RE.search(body):
                # ``信息技术应用`` often prints numbered操作说明 before the
                # actual interrogative tasks.  Keep those instructions as
                # source boundaries, then leave the mode at the first genuine
                # question (normally marked by a question mark).
                marker.kind = "section_heading"
            elif practice:
                # Inside an explicit exercise area even terse fill-in items are
                # questions; MinerU often escapes the printed blank so the
                # usual question-shaped cues are unavailable.
                marker.kind = "exercise_start"
            elif short_title:
                marker.kind = "section_heading"
                practice = False
            elif body:
                # Long numbered textbook problems are often declarative and
                # contain neither a question mark nor a cue verb in the first
                # MinerU block; their subquestions follow in later blocks.
                # Known workflow/procedure fields were already excluded above.
                marker.kind = "exercise_start"
                instruction_section = False
            else:
                marker.kind = "section_heading"
                practice = False
            resolved.append(marker)
            continue
        # Example references are evidence/body. Analysis/solution labels are
        # retained as typed boundaries and applied only to worked examples by
        # ``build_questions`` below; exercises must keep their old behaviour.
        resolved.append(marker)

    # Column assignment above gives the gap detector the same geometric frame
    # as normal exercise anchors.  Add only unambiguous missing-number cards,
    # then re-sort before building starts and boundaries.
    resolved.extend(_recover_book_gap_questions(resolved, blocks))
    resolved.sort(key=BookMarker.key)

    starts: list[Start] = []
    boundaries: list[dict] = []
    headings = _headings(blocks)
    for marker in resolved:
        if marker.kind in {"example_start", "exercise_start"} and marker.number is not None:
            starts.append(Start(
                number=marker.number,
                page=marker.page,
                x=marker.x,
                y=marker.y,
                seq=marker.seq,
                at_start=True,
                score=5.0 if marker.kind == "example_start" else 4.0,
                source="mineru",
                col=marker.col,
                source_kind="example" if marker.kind == "example_start" else "exercise",
                anchor_seq=marker.seq,
                marker_text=marker.text[:160],
            ))
        elif marker.kind in {
                "practice_header", "section_heading", "analysis_solution",
                "embedded_question_end"}:
            boundary = {
                "page": marker.page, "col": marker.col, "y": marker.y,
                "text": marker.text[:160], "seq": marker.seq, "kind": marker.kind,
            }
            boundaries.append(boundary)
            # Treat textbook structural markers as headings for both stopping
            # and the human-readable section label attached to later cards.
            if marker.kind not in {"analysis_solution", "embedded_question_end"}:
                headings.append(boundary)

    # Re-estimate columns from accepted card starts, just like the exam path,
    # then update every typed marker consistently.
    if starts:
        splits = _splits_from(starts, pages, blocks)
        _assign_book_columns(resolved, splits)
        for start in starts:
            start.col = column_of(splits.get(start.page, []), start.x + 1)
        for boundary in boundaries:
            matching = next((item for item in resolved if item.seq == boundary.get("seq")), None)
            if matching is not None:
                boundary["col"] = matching.col

    # ``_headings`` returns geometric headings without a column.  A textbook
    # scope may legitimately contain headings but no accepted card starts, so
    # this invariant must not depend on the ``if starts`` branch above.
    for heading in headings:
        heading["col"] = (
            column_of(splits.get(heading["page"], []), heading["x"] + 1)
            if "x" in heading else int(heading.get("col", 0))
        )

    layout = Layout(
        page_count=len(pages),
        splits=splits,
        slots=_slots(pages, blocks, splits),
        headings=sorted(headings, key=lambda item: (item["page"], item.get("col", 0), item["y"])),
        candidates=starts,
        boundaries=boundaries,
    )
    return layout, resolved


def analyse_book(pages: list[dict], blocks: list[dict]) -> tuple[Layout, list[Start]]:
    """Find textbook examples/exercises using only existing MinerU text blocks."""
    layout, _markers = _book_marker_stream(pages, blocks)
    return layout, sorted(layout.candidates, key=Start.key)


def _block_key(layout: Layout, block: dict) -> tuple[int, int, float] | None:
    bbox = block.get("bbox")
    try:
        page = int(block["page_idx"])
        x = (float(bbox[0]) + float(bbox[2])) / 2
        y = float(bbox[1])
    except (KeyError, TypeError, ValueError, IndexError):
        return None
    return page, column_of(layout.splits.get(page, []), x), y


def _without_source_prefix(text: str) -> str:
    """Remove one leading exam/source citation before looking for a stem verb."""
    return re.sub(r"^\s*[\[【（(][^\]】）)]{1,96}[\]】）)]\s*", "", text, count=1).lstrip()


def _leading_candidate_score(block: dict, following_text: str) -> tuple[int, bool, bool]:
    """Return a conservative score and whether the evidence has question shape."""
    text = str(block.get("text") or "").strip()
    if not text or block.get("type") in NON_CONTENT | FIGURE_TYPES | {"equation"}:
        return -100, False, False
    if NUMBER_RE.search(text) or HEADING_RE.match(text):
        return -100, False, False

    source = bool(_LEADING_SOURCE_RE.search(text))
    bare_one = bool(_BARE_LEADING_ONE_RE.match(text))
    body = _without_source_prefix(_BARE_LEADING_ONE_RE.sub("", text, count=1))
    # MinerU sometimes separates ``[2026某地联考]`` and ``已知……`` into two
    # adjacent text blocks.  The citation is still the correct top boundary;
    # borrow only the immediately following non-empty line as scoring evidence.
    if source and not _LEADING_STEM_RE.search(body):
        support = next((line.strip() for line in following_text.splitlines()[1:] if line.strip()), "")
        if support:
            body = f"{body}\n{_without_source_prefix(support)}".strip()
    stem = bool(_LEADING_STEM_RE.search(body))
    question_mark = bool(_LEADING_QUESTION_MARK_RE.search(body))
    options = bool(_LEADING_OPTION_RE.search(text))
    subquestions = set(_LEADING_SUBQUESTION_RE.findall(following_text))
    score = (5 if source else 0) + (2 if stem else 0) + (2 if question_mark else 0) + \
        (2 if options else 0) + (1 if len(body) >= 18 else 0) + (2 if bare_one else 0)
    if "1" in subquestions:
        score += 1
    if {"1", "2"}.issubset(subquestions):
        score += 1
    if _LEADING_NON_QUESTION_RE.search(text):
        score -= 8
    # A source citation plus a normal stem, or an ordinary stem with a blank,
    # options, or numbered subquestions is materially stronger than prose that
    # merely happens to contain “已知”.
    shaped = (source and stem) or (stem and (question_mark or options or bool(subquestions)))
    return score, shaped, source


def repair_leading_question(
    layout: Layout,
    starts: list[Start],
    blocks: list[dict],
) -> tuple[list[Start], LeadingQuestionCheck]:
    """Locally recover a missing printed ``1.`` before the first detected card.

    This deliberately makes no model call.  An automatic repair is limited to
    the unambiguous ``missing 1 before 2`` shape and requires a high-scoring
    question-stem block.  A possible orphan before any other first number is
    surfaced as a note instead of inventing cards in material that intentionally
    starts partway through an exercise.
    """
    ordered = sorted(starts, key=Start.key)
    if not ordered or ordered[0].number <= 1:
        return ordered, LeadingQuestionCheck()
    first = ordered[0]
    preceding_headings = [
        ((int(item["page"]), int(item.get("col", 0)), float(item["y"])), str(item.get("text") or ""))
        for item in layout.headings
        if (int(item["page"]), int(item.get("col", 0)), float(item["y"])) < first.key()
    ]
    preceding_headings.sort(key=lambda item: item[0])
    first_section = next(
        (item for item in preceding_headings if re.match(r"^\s*一\s*[、.．]", item[1])),
        None,
    )
    # A first-section heading is a stable front-matter boundary.  Without one,
    # do not pull evidence from before the nearest later section heading: that
    # text may belong to a previous chapter/exercise.
    heading_cutoff = first_section[0] if first_section else (
        preceding_headings[-1][0] if preceding_headings else None
    )
    prefix: list[tuple[tuple[int, int, float], dict]] = []
    for block in blocks:
        key = _block_key(layout, block)
        if key is None or key >= first.key():
            continue
        if heading_cutoff is not None and key <= heading_cutoff:
            continue
        if block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        if not str(block.get("text") or "").strip():
            continue
        prefix.append((key, block))
    prefix.sort(key=lambda item: item[0])
    if not prefix:
        return ordered, LeadingQuestionCheck()

    scored: list[tuple[int, bool, bool, tuple[int, int, float], dict]] = []
    for index, (key, block) in enumerate(prefix):
        # Subquestions/answers following the stem are corroborating evidence,
        # but are never themselves selected as the inferred top boundary.  A
        # heading also stops this supporting window, so evidence cannot be
        # assembled from two different sections.
        next_heading = next((item[0] for item in preceding_headings if item[0] > key), first.key())
        support = [item for item in prefix[index:index + 10] if item[0] < next_heading]
        following_text = "\n".join(
            str(item.get("text") or "") for _, item in support
        )
        score, shaped, source = _leading_candidate_score(block, following_text)
        scored.append((score, shaped, source, key, block))
    score, shaped, source, key, candidate = max(
        scored,
        key=lambda item: (item[0], -item[3][0], -item[3][1], -item[3][2]),
    )

    candidate_seq = candidate.get("seq") if type(candidate.get("seq")) is int else None
    first_question_after_section = next(
        (item[3] for item in scored if item[1] and item[0] >= 0),
        None,
    ) if first_section else None
    has_boundary_evidence = source or (first_section is not None and key == first_question_after_section)
    if first.number == 2 and shaped and has_boundary_evidence and score >= 7:
        bbox = candidate["bbox"]
        inferred = Start(
            number=1,
            page=int(candidate["page_idx"]),
            x=float(bbox[0]),
            y=float(bbox[1]),
            seq=candidate_seq,
            at_start=True,
            score=float(score),
            source="inferred",
            col=key[1],
        )
        return sorted([inferred, *ordered], key=Start.key), LeadingQuestionCheck(
            status="repaired",
            first_detected=first.number,
            candidate_seq=candidate_seq,
            message=(
                "第 2 题前检测到完整题干；MinerU 漏读了第 1 题题号，已用本地版面规则补出第 1 题"
                "。漏题定位仅用本地规则，未增加额外模型调用；新增题卡仍按正常流程识读。"
                "题号由本地规则补出，请对照原卷核对。"
            ),
        )

    # Only warn when there is actual question-like evidence.  A clean excerpt
    # that simply begins with “2.” has no prefix candidate and remains untouched.
    if shaped or score >= 4 or _LEADING_SUBQUESTION_RE.search("\n".join(
        str(item.get("text") or "") for _, item in prefix
    )):
        return ordered, LeadingQuestionCheck(
            status="suspected",
            first_detected=first.number,
            candidate_seq=candidate_seq,
            message=(
                f"首个检测到的题号是第 {first.number} 题，前方还有疑似题目正文，但证据不足，"
                "可能漏了组首题；请对照原卷，必要时使用“漏了一题？手动框出”。"
            ),
        )
    return ordered, LeadingQuestionCheck()


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
    # A candidate the gap repair already re-read as a clipped number (“9.” used
    # as 19) is part of the main run, not the start of a new numbering scope.
    repaired_seqs = {item.seq for item in selected if item.source == "repaired" and item.seq is not None}
    reliable = sorted(
        (item for item in layout.candidates
         if item.at_start and item.score >= 3.0 and item.number > 0 and item.seq not in repaired_seqs),
        key=Start.key,
    )
    if not reliable:
        reliable = sorted(selected, key=Start.key)
    if not reliable:
        return []
    # MinerU sometimes emits the same line twice (a printed line plus an
    # overlapping re-read that includes handwriting).  Two identical numbers a
    # line apart in the same column are one question, not a restart.
    deduped: list[Start] = []
    for item in reliable:
        previous = deduped[-1] if deduped else None
        if (previous is not None and previous.number == item.number and previous.page == item.page
                and previous.col == item.col and abs(item.y - previous.y) < 60):
            continue
        deduped.append(item)
    reliable = deduped

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

    # A genuine restart begins a new numbering (1, 2 or 3).  A lone stray such
    # as a misread “9.” in the middle of a paper is noise, not a new scope.
    merged: list[list[Start]] = []
    for run in runs:
        if merged and len(run) == 1 and run[0].number > 3:
            merged[-1].extend(run)
            continue
        merged.append(run)
    runs = merged

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


def book_numbering_scopes(pages: list[dict], blocks: list[dict]) -> list[dict]:
    """Build stable textbook runs from typed example/exercise starts.

    Example numbering and exercise numbering routinely restart independently.
    A kind change or a repeated/small number therefore begins a new source
    scope, while every card remains anchored to its original MinerU block.
    """
    if not pages or not blocks:
        return []
    layout, markers = _book_marker_stream(pages, blocks)
    starts = sorted(layout.candidates, key=Start.key)
    if not starts:
        return []

    runs: list[list[Start]] = [[]]
    seen: set[int] = set()
    maximum: int | None = None
    previous_kind: str | None = None
    for current in sorted(starts, key=Start.key):
        restart = bool(
            runs[-1]
            and (
                current.source_kind != previous_kind
                or (
                    maximum is not None
                    and current.number <= maximum
                    and (current.number in seen or current.number <= 3)
                )
            )
        )
        if restart:
            runs.append([])
            seen = set()
            maximum = None
        runs[-1].append(current)
        seen.add(current.number)
        maximum = current.number if maximum is None else max(maximum, current.number)
        previous_kind = current.source_kind

    first_page = min(int(page["page_idx"]) for page in pages)
    last_page = max(int(page["page_idx"]) for page in pages)
    # Preserve the practice header inside an exercise scope.  Otherwise the
    # later per-scope pass would lose the state that makes long, declarative
    # textbook exercises (for example a table followed by its question) valid.
    structural = [
        marker for marker in markers
        if marker.kind in {"practice_header", "section_heading", "example_start"}
    ]
    effective_starts: list[int | None] = []
    for index, run in enumerate(runs):
        first = run[0]
        effective = first.anchor_seq
        if index > 0 and first.source_kind == "exercise":
            preceding = [marker for marker in structural if marker.key() < first.key()]
            previous_anchor = runs[index - 1][-1].anchor_seq
            if preceding and preceding[-1].kind == "practice_header" and (
                not isinstance(previous_anchor, int)
                or not isinstance(preceding[-1].seq, int)
                or preceding[-1].seq > previous_anchor
            ):
                effective = preceding[-1].seq
        effective_starts.append(effective)

    scopes: list[dict] = []
    for index, run in enumerate(runs):
        first = run[0]
        following = runs[index + 1][0] if index + 1 < len(runs) else None
        page_start = first_page if index == 0 else first.page
        page_end = (
            following.page if following is not None and following.page == run[-1].page
            else following.page - 1 if following is not None
            else last_page
        )
        scopes.append({
            "sequence": index,
            "pages": list(range(page_start, page_end + 1)),
            "seq_start": None if index == 0 else effective_starts[index],
            "seq_end": (effective_starts[index + 1] - 1) if following is not None
            and isinstance(effective_starts[index + 1], int) else None,
            "scope_anchor_seq": first.anchor_seq,
            "first_number": first.number,
            "start_page": first.page,
            "source_kind": first.source_kind,
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


def center_in_regions(page_idx: int, bbox: list[float], regions: list[dict]) -> bool:
    """Whether the centre of a source box lies inside one of the crop regions."""

    cx, cy = _center(bbox)
    return any(
        region["page_idx"] == page_idx
        and region["bbox"][0] <= cx <= region["bbox"][2]
        and region["bbox"][1] <= cy <= region["bbox"][3]
        for region in regions
    )


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


def _text_in_regions(blocks: list[dict], regions: list[dict]) -> str:
    values: list[str] = []
    for block in blocks:
        bbox = block.get("bbox")
        if not bbox or block.get("type") in NON_CONTENT | FIGURE_TYPES:
            continue
        if center_in_regions(int(block["page_idx"]), bbox, regions):
            value = str(block.get("text") or "").strip()
            if value:
                values.append(value)
    return "\n".join(values)


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


def _limit_book_regions(regions: list[dict]) -> tuple[list[dict], list[str]]:
    """Bound a runaway textbook crop and make the intervention explicit."""
    page_order: list[int] = []
    for region in regions:
        page = int(region["page_idx"])
        if page not in page_order:
            page_order.append(page)
    if len(page_order) <= BOOK_MAX_CARD_PAGES:
        return regions, []
    allowed = set(page_order[:BOOK_MAX_CARD_PAGES])
    return [region for region in regions if int(region["page_idx"]) in allowed], [
        f"书本切题范围超过 {BOOK_MAX_CARD_PAGES} 页，已在安全上限停止；请对照原书调整范围",
    ]


def _tighten_book_local_left_column(
    layout: Layout,
    start: Start,
    regions: list[dict],
    blocks: list[dict],
) -> list[dict]:
    """Trim one unambiguous mixed-layout left-column exercise.

    Some textbook pages are full-width above and below a short two-column
    band.  Page-wide split detection correctly refuses to call the whole page
    two columns, but an exercise in the left band can then absorb explanatory
    prose on the right and the full-width paragraph below it.  Tighten only
    when all of the following are visible in the existing MinerU geometry:

    * the page has no global column split;
    * the source anchor itself is a narrow left block;
    * at least two left blocks form the exercise;
    * a long right-hand paragraph vertically overlaps those blocks; and
    * a later full-width block gives an explicit bottom boundary.

    Without every condition the original crop is returned unchanged.
    """

    if not regions or layout.splits.get(start.page):
        return regions
    first = next((item for item in regions if item.get("page_idx") == start.page), None)
    anchor = next(
        (block for block in blocks if start.seq is not None and block.get("seq") == start.seq),
        None,
    )
    if first is None or anchor is None or not isinstance(anchor.get("bbox"), list):
        return regions
    anchor_box = anchor["bbox"]
    if float(anchor_box[0]) >= 350 or float(anchor_box[2]) > 600:
        return regions
    page_blocks = [
        block for block in blocks
        if int(block.get("page_idx", -1)) == start.page
        and isinstance(block.get("bbox"), list)
        and block.get("type") not in NON_CONTENT | FIGURE_TYPES
        and str(block.get("text") or "").strip()
    ]
    right = [
        block for block in page_blocks
        if float(block["bbox"][0]) >= float(anchor_box[2]) + 120
        and float(block["bbox"][1]) <= float(anchor_box[3]) + 80
        and float(block["bbox"][3]) >= float(anchor_box[1]) - 80
        and len(str(block.get("text") or "").strip()) >= 45
    ]
    if not right:
        return regions
    right_x = min(float(block["bbox"][0]) for block in right)
    left = [
        block for block in page_blocks
        if float(block["bbox"][1]) >= start.y - START_PAD
        and float(block["bbox"][0]) < float(anchor_box[0]) + 90
        and float(block["bbox"][2]) <= right_x - 20
        and float(block["bbox"][1]) < float(first["bbox"][3])
    ]
    if len(left) < 2 or not any(
            float(candidate["bbox"][1]) < float(item["bbox"][3])
            and float(candidate["bbox"][3]) > float(item["bbox"][1])
            for candidate in right for item in left):
        return regions
    left_right = max(float(block["bbox"][2]) for block in left)
    divider = (left_right + right_x) / 2
    last_left_bottom = max(float(block["bbox"][3]) for block in left)
    crossing = sorted([
        block for block in page_blocks
        if float(block["bbox"][1]) >= last_left_bottom
        and float(block["bbox"][0]) < divider - 20
        and float(block["bbox"][2]) > divider + 20
    ], key=lambda block: float(block["bbox"][1]))
    if not crossing:
        return regions
    bottom = min(float(first["bbox"][3]), float(crossing[0]["bbox"][1]) - END_GAP)
    if bottom < last_left_bottom:
        return regions
    tightened = {
        "page_idx": start.page,
        "bbox": [float(first["bbox"][0]), float(first["bbox"][1]), round(divider, 1), round(bottom, 1)],
    }
    return [tightened]


def build_questions(
    layout: Layout,
    starts: list[Start],
    blocks: list[dict],
    *,
    textbook: bool = False,
) -> list[dict]:
    ordered = sorted(starts, key=Start.key)
    headings = sorted(layout.headings, key=lambda h: (h["page"], h["col"], h["y"]))
    universal_stops = sorted(
        [(s.page, s.col, s.y) for s in ordered]
        + [(h["page"], h["col"], h["y"]) for h in headings]
        + [
            (b["page"], b.get("col", 0), b["y"])
            for b in layout.boundaries
            if b.get("kind") != "analysis_solution"
        ]
    )
    example_solution_boundaries = sorted(
        (
            (
                (b["page"], b.get("col", 0), b["y"]),
                b.get("seq"),
            )
            for b in layout.boundaries
            if b.get("kind") == "analysis_solution"
        ),
        key=lambda item: item[0],
    )
    figures = [b for b in blocks if b.get("bbox") and b.get("type") in FIGURE_TYPES]
    questions = []
    for start in ordered:
        here = (start.page, start.col, start.y)
        structural_stop = next((item for item in universal_stops if item > here), None)
        stop = structural_stop
        solution_trimmed = False
        solution_boundary_seq = None
        if start.source_kind == "example":
            solution_boundary = next(
                (item for item in example_solution_boundaries if item[0] > here),
                None,
            )
            if solution_boundary is not None and (
                stop is None or solution_boundary[0] < stop
            ):
                stop = solution_boundary[0]
                solution_trimmed = True
                solution_boundary_seq = solution_boundary[1]
        regions = question_regions(layout, start, stop) or _fallback_regions(layout, start, stop)
        if start.source_kind in {"example", "exercise"}:
            regions = _tighten_book_local_left_column(layout, start, regions, blocks)
        segmentation_flags: list[str] = []
        if textbook:
            regions, segmentation_flags = _limit_book_regions(regions)
        section = ""
        for heading in headings:
            if (heading["page"], heading["col"], heading["y"]) < here:
                section = heading["text"]
        figure_blocks = [{"seq": block.get("seq"), "page_idx": int(block["page_idx"]), "bbox": list(block["bbox"])}
                         for block in figures if overlaps_regions(int(block["page_idx"]), block["bbox"], regions)]
        if solution_trimmed:
            # The general 20%-overlap rule is intentionally generous around a
            # next-question boundary.  At an explicit ``分析/解`` boundary it
            # can instead pull a solution diagram backwards into the question.
            # Requiring the image centre to remain above the solution keeps a
            # genuine side-by-side input figure (whose centre is in the stem)
            # while excluding answer-only diagrams below the label.
            figure_blocks = [
                block for block in figure_blocks
                if center_in_regions(block["page_idx"], block["bbox"], regions)
                and not (
                    block["bbox"][1] >= 945
                    and block["bbox"][3] - block["bbox"][1] <= 60
                )
            ]
        recovered_input_figures: list[dict] = []
        if solution_trimmed and not figure_blocks \
                and BOOK_NUMBERED_VISUAL_RE.search(_text_in_regions(blocks, regions)):
            # Some textbooks print the input chart beside the worked solution
            # even though the question refers to it explicitly (for example
            # “如图 4.2-7”).  Keep only the first substantive visual before the
            # next structural/card boundary, as its own image-only crop.  If
            # several visuals compete, do not guess: leave the existing yellow
            # warning for a person to resolve.  This preserves the unambiguous
            # input without bringing answer text back into view.
            full_regions = question_regions(layout, start, structural_stop) \
                or _fallback_regions(layout, start, structural_stop)
            tail = sorted(
                (
                    block for block in figures
                    if overlaps_regions(int(block["page_idx"]), block["bbox"], full_regions)
                    and int(block["page_idx"]) <= stop[0] + 1
                    and not center_in_regions(int(block["page_idx"]), block["bbox"], regions)
                    and not (
                        block["bbox"][1] >= 945
                        and block["bbox"][3] - block["bbox"][1] <= 60
                    )
                    and (block["bbox"][2] - block["bbox"][0]) >= 80
                    and (block["bbox"][3] - block["bbox"][1]) >= 60
                ),
                key=lambda block: (int(block["page_idx"]), block["bbox"][1], block["bbox"][0]),
            )
            if len(tail) == 1:
                recovered = tail[0]
                recovered_region = {
                    "page_idx": int(recovered["page_idx"]),
                    "bbox": list(recovered["bbox"]),
                }
                regions.append(recovered_region)
                recovered_input_figures.append({
                    "seq": recovered.get("seq"),
                    **recovered_region,
                    "recovered_input": True,
                })
                figure_blocks.extend(recovered_input_figures)
        questions.append({
            "number": start.number,
            "start": {
                "page": start.page,
                "col": start.col,
                "y": round(start.y, 1),
                "source": start.source,
                "source_kind": start.source_kind,
                "source_anchor_seq": start.anchor_seq if start.anchor_seq is not None else start.seq,
            },
            "source_kind": start.source_kind,
            "source_anchor_seq": start.anchor_seq if start.anchor_seq is not None else start.seq,
            "source_marker": start.marker_text,
            "segmentation_flags": segmentation_flags,
            "segmentation": {
                "source_kind": start.source_kind,
                "source_anchor_seq": start.anchor_seq if start.anchor_seq is not None else start.seq,
                "range_limited": bool(segmentation_flags),
                "solution_trimmed": solution_trimmed,
                **({"solution_boundary_seq": solution_boundary_seq}
                   if solution_trimmed and solution_boundary_seq is not None else {}),
                **({"recovered_input_figure_seqs": [
                    figure.get("seq") for figure in recovered_input_figures
                ]} if recovered_input_figures else {}),
            },
            "regions": regions,
            "figure_candidates": figure_blocks,
            "section": section,
            "question_type": _section_type(section),
        })
    return questions


def _captioned_book_visual_owners(
    questions: list[dict],
    blocks: list[dict],
) -> dict[int, dict]:
    """Map an unambiguous captioned visual to the question that names it."""

    visuals = [
        block for block in blocks
        if isinstance(block.get("seq"), int)
        and isinstance(block.get("bbox"), list)
        and block.get("type") in FIGURE_TYPES
    ]
    owners: dict[int, dict] = {}
    for caption in blocks:
        if not isinstance(caption.get("bbox"), list):
            continue
        text = str(caption.get("text") or "").strip()
        local_match = BOOK_LOCAL_FIGURE_CAPTION_RE.match(text)
        question_match = BOOK_QUESTION_FIGURE_CAPTION_RE.match(text)
        if local_match is None and question_match is None:
            continue
        page = int(caption.get("page_idx", -1))
        box = caption["bbox"]
        caption_x = (float(box[0]) + float(box[2])) / 2
        eligible = [
            visual for visual in visuals
            if int(visual.get("page_idx", -2)) == page
            and 0 <= float(box[1]) - float(visual["bbox"][3]) <= 90
            and float(visual["bbox"][0]) - 30 <= caption_x <= float(visual["bbox"][2]) + 30
        ]
        if not eligible:
            continue
        visual = min(
            eligible,
            key=lambda item: (
                float(box[1]) - float(item["bbox"][3]),
                abs(caption_x - (float(item["bbox"][0]) + float(item["bbox"][2])) / 2),
            ),
        )
        targets: list[dict]
        if local_match is not None:
            number = local_match.group(1)
            targets = [
                question for question in questions
                if re.search(
                    rf"(?:如|见)?\s*图\s*[（(]\s*{re.escape(number)}\s*[)）]",
                    str(question.get("source_marker") or ""),
                )
            ]
        else:
            number = int(question_match.group(1))
            targets = [question for question in questions if question.get("number") == number]
        if not targets:
            continue
        caption_position = page * 1000 + float(box[1])
        target = min(
            targets,
            key=lambda question: abs(
                caption_position
                - (
                    int((question.get("start") or {}).get("page", 0)) * 1000
                    + float((question.get("start") or {}).get("y", 0))
                )
            ),
        )
        owners[int(visual["seq"])] = target
    return owners


def _exclude_foreign_visual_from_regions(regions: list[dict], visual: dict) -> list[dict]:
    """Keep neighbouring text while removing a right-side visual owned elsewhere."""

    page = int(visual["page_idx"])
    box = visual["bbox"]
    adjusted: list[dict] = []
    for region in regions:
        current = {"page_idx": region["page_idx"], "bbox": list(region["bbox"])}
        if current["page_idx"] != page or not overlaps_regions(page, box, [current]):
            adjusted.append(current)
            continue
        region_box = current["bbox"]
        if float(box[0]) - float(region_box[0]) >= 300:
            region_box[2] = min(float(region_box[2]), float(box[0]) - END_GAP)
        elif float(box[1]) - float(region_box[1]) >= 45:
            region_box[3] = min(float(region_box[3]), float(box[1]) - END_GAP)
        adjusted.append(current)
    return [
        region for region in adjusted
        if region["bbox"][2] - region["bbox"][0] >= 20
        and region["bbox"][3] - region["bbox"][1] >= 10
    ]


def _book_marker_mentions_supplied_visual(question: dict, visual_type: str) -> bool:
    """Match only explicit references to a supplied image/table."""

    text = str(question.get("source_marker") or "")
    if visual_type == "table":
        return bool(re.search(
            r"(?:如|见|根据|观察)?\s*(?:下|上)?\s*表(?:\s*所示|中|如下)?|表\s*中",
            text,
        ))
    return bool(re.search(
        r"(?:如|见|根据|观察)\s*(?:下|上|左|右)?\s*图|"
        r"(?:图像|图象|图形|示意图|简图)\s*(?:如图|所示)",
        text,
    ))


def _uncaptioned_book_visual_owners(
    questions: list[dict], figures: list[dict], claimed: set[int],
) -> dict[int, dict]:
    """Recover a right-side visual whose centre fell into the next card.

    The rule is intentionally narrow: the current centre-owner must not refer
    to that visual type, while a preceding same-page card must explicitly say
    it is using a supplied figure/table and overlap at least 20% of the crop.
    This resolves common two-column textbook layouts without guessing between
    two questions that both say ``如图``.
    """

    owners: dict[int, dict] = {}
    ordered = sorted(
        questions,
        key=lambda item: (
            int((item.get("start") or {}).get("page", 0)),
            float((item.get("start") or {}).get("y", 0)),
        ),
    )
    for visual in figures:
        sequence = visual.get("seq")
        if not isinstance(sequence, int) or sequence in claimed:
            continue
        current = [
            question for question in ordered
            if any(candidate.get("seq") == sequence
                   for candidate in (question.get("figure_candidates") or []))
        ]
        if len(current) != 1:
            continue
        current_owner = current[0]
        visual_type = str(visual.get("type") or "image")
        if _book_marker_mentions_supplied_visual(current_owner, visual_type):
            continue
        current_start = current_owner.get("start") or {}
        page = int(visual.get("page_idx", -1))
        eligible = [
            question for question in ordered
            if int((question.get("start") or {}).get("page", -2)) == page
            and float((question.get("start") or {}).get("y", 0))
            < float(current_start.get("y", 0))
            and _book_marker_mentions_supplied_visual(question, visual_type)
            and overlaps_regions(page, visual["bbox"], question.get("regions") or [])
        ]
        if not eligible:
            continue
        target = max(eligible, key=lambda item: float((item.get("start") or {}).get("y", 0)))
        owners[sequence] = target
    return owners


def build_book_questions(layout: Layout, starts: list[Start], blocks: list[dict]) -> list[dict]:
    # Deliberately call the long-standing public builder with its original
    # three-argument contract.  Existing integrations/tests replace that
    # function to supply source items, so book support must remain composable.
    questions = build_questions(layout, starts, blocks)
    figures = [block for block in blocks if block.get("bbox") and block.get("type") in FIGURE_TYPES]
    for question in questions:
        regions, flags = _limit_book_regions(question.get("regions") or [])
        question["regions"] = regions
        # For books the next printed question can begin against the bottom of a
        # large chart.  The exam path's deliberately generous 20%-overlap rule
        # then assigns the chart to both cards.  A textbook source already has
        # stable typed anchors, so require the visual centre to belong to this
        # crop.  Explicit recovered-input crops remain valid because their own
        # image-only region contains that centre.
        question["figure_candidates"] = [
            candidate for candidate in (question.get("figure_candidates") or [])
            if center_in_regions(candidate["page_idx"], candidate["bbox"], regions)
        ]
        if flags:
            question["figure_candidates"] = [
                {"seq": block.get("seq"), "page_idx": int(block["page_idx"]), "bbox": list(block["bbox"])}
                for block in figures
                if center_in_regions(int(block["page_idx"]), block["bbox"], regions)
            ]
        question["segmentation_flags"] = flags
        metadata = dict(question.get("segmentation") or {})
        metadata["range_limited"] = bool(flags)
        question["segmentation"] = metadata
    owners = _captioned_book_visual_owners(questions, blocks)
    owners.update(_uncaptioned_book_visual_owners(
        questions,
        figures,
        set(owners),
    ))
    if owners:
        figures_by_seq = {
            int(block["seq"]): block for block in figures
            if isinstance(block.get("seq"), int)
        }
        for sequence, owner in owners.items():
            visual = figures_by_seq.get(sequence)
            if visual is None:
                continue
            for question in questions:
                if question is owner:
                    continue
                question["figure_candidates"] = [
                    candidate for candidate in (question.get("figure_candidates") or [])
                    if candidate.get("seq") != sequence
                ]
                question["regions"] = _exclude_foreign_visual_from_regions(
                    question.get("regions") or [],
                    {"page_idx": int(visual["page_idx"]), "bbox": list(visual["bbox"])},
                )
            candidate = {
                "seq": sequence,
                "page_idx": int(visual["page_idx"]),
                "bbox": list(visual["bbox"]),
                "recovered_input": True,
            }
            if not any(item.get("seq") == sequence for item in owner.get("figure_candidates") or []):
                owner.setdefault("figure_candidates", []).append(candidate)
            # Merely containing the visual centre is enough for ownership, but
            # not enough for rendering: a question boundary can bisect a tall
            # right-side image while leaving its centre inside the target crop.
            # Add the exact image-only crop unless one existing region already
            # contains the complete visual.
            fully_visible = any(
                region.get("page_idx") == candidate["page_idx"]
                and float(region["bbox"][0]) <= float(candidate["bbox"][0])
                and float(region["bbox"][1]) <= float(candidate["bbox"][1])
                and float(region["bbox"][2]) >= float(candidate["bbox"][2])
                and float(region["bbox"][3]) >= float(candidate["bbox"][3])
                for region in (owner.get("regions") or [])
            )
            if not fully_visible:
                owner.setdefault("regions", []).append({
                    "page_idx": candidate["page_idx"], "bbox": list(candidate["bbox"]),
                })
                owner["regions"].sort(key=lambda item: (item["page_idx"], item["bbox"][1], item["bbox"][0]))
            metadata = dict(owner.get("segmentation") or {})
            recovered = list(metadata.get("recovered_input_figure_seqs") or [])
            if sequence not in recovered:
                recovered.append(sequence)
            metadata["recovered_input_figure_seqs"] = recovered
            owner["segmentation"] = metadata
    return questions


def segment(pages: list[dict], blocks: list[dict]) -> dict:
    """一次性切题（不补缺号）。缺号由调用方用视觉模型定位后再调用 build_questions。"""
    layout, starts = analyse(pages, blocks)
    starts, leading = repair_leading_question(layout, starts, blocks)
    return {
        "layout": layout,
        "starts": starts,
        "leading": leading,
        "missing": missing_numbers(starts),
        "questions": build_questions(layout, starts, blocks),
    }


def segment_book(pages: list[dict], blocks: list[dict]) -> dict:
    """One-pass deterministic textbook segmentation; performs no model call."""
    layout, starts = analyse_book(pages, blocks)
    return {
        "layout": layout,
        "starts": starts,
        "missing": [],
        "questions": build_book_questions(layout, starts, blocks),
    }


def region_regions_between(layout: Layout, start: Start, following: Start | None) -> list[dict]:
    stop = (following.page, following.col, following.y) if following else None
    return question_regions(layout, start, stop)
