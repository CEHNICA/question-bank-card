"""题面文字的规范化：判断两次识读是否"说的是同一件事"。

只用于比较，不改动保存的文字。空格、全半角、$ 与花括号、\\dfrac/\\frac、
\\vec/\\overrightarrow、\\le/\\leqslant 这类写法差异都视为相同。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

SYMBOLS = {
    "angle": "∠",
    "parallel": "∥",
    "perp": "⊥",
    "bot": "⊥",
    "triangle": "△",
    "bigtriangleup": "△",
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "pm": "±",
    "mp": "∓",
    "le": "≤",
    "leq": "≤",
    "leqslant": "≤",
    "leqq": "≤",
    "ge": "≥",
    "geq": "≥",
    "geqslant": "≥",
    "geqq": "≥",
    "ne": "≠",
    "neq": "≠",
    "approx": "≈",
    "equiv": "≡",
    "cong": "≅",
    "sim": "∽",
    "backsim": "∽",
    "pi": "π",
    "infty": "∞",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "subsetneqq": "⊊",
    "supset": "⊃",
    "supseteq": "⊇",
    "cup": "∪",
    "cap": "∩",
    "emptyset": "∅",
    "varnothing": "∅",
    "forall": "∀",
    "exists": "∃",
    "neg": "¬",
    "because": "∵",
    "therefore": "∴",
    "circ": "°",
    "degree": "°",
    "prime": "'",
    "cdots": "…",
    "ldots": "…",
    "dots": "…",
    "mid": "|",
    "vert": "|",
    "lvert": "|",
    "rvert": "|",
    "rightarrow": "→",
    "to": "→",
    "Rightarrow": "⇒",
    "Leftrightarrow": "⇔",
    "square": "□",
    "Box": "□",
    "lbrace": "{",
    "rbrace": "}",
    "{": "{",
    "}": "}",
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "theta": "θ",
    "lambda": "λ",
    "mu": "μ",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "phi": "φ",
    "varphi": "φ",
    "omega": "ω",
    "Delta": "Δ",
    "Omega": "Ω",
    "odot": "⊙",
    "boldsymbol": "",
    "mathbb": "",
    "mathrm": "",
    "mathbf": "",
    "mathit": "",
    "text": "",
    "textbf": "",
    "operatorname": "",
    "overrightarrow": "→",
    "vec": "→",
    "dfrac": "frac",
    "tfrac": "frac",
}
PUNCT = {
    "。": ".",
    "．": ".",
    "，": ",",
    "：": ":",
    "；": ";",
    "、": ",",
    "“": '"',
    "”": '"',
    "‘": "'",
    "’": "'",
    "（": "(",
    "）": ")",
    "【": "[",
    "】": "]",
    "－": "-",
    "−": "-",
    "–": "-",
    "—": "-",
    "～": "~",
    "∶": ":",
    "﹒": ".",
    "▱": "□",
    "丄": "⊥",
    "⩽": "≤",
    "⩾": "≥",
    "≦": "≤",
    "≧": "≥",
}
SPACING = re.compile(
    r"\\(?:left|right|big|Big|bigg|Bigg|displaystyle|textstyle|limits|nolimits|quad|qquad)(?![A-Za-z])|\\[,;:! ]"
)
COMMAND = re.compile(r"\\([A-Za-z]+|[{}])")
SCORE = re.compile(r"[(（]\s*\d{1,2}\s*分\s*[)）]")
BLANK = re.compile(r"_{2,}|\\underline\{\s*(?:\\quad|\\qquad|\\hspace\*?\{[^{}]{0,12}\}|~|\s)*\}|(?:\\_)+")
# 魔搭 Qwen writes a fill-in blank as $\underline{\hspace{2em}}$: shown as ____.  Only a
# span that is nothing but the blank: inside a formula, _ would start a subscript.
_BLANK_ONLY = re.compile(r"\s*\\underline\{\s*(?:\\hspace\*?\{[^{}]{0,12}\}|\\quad|\\qquad|~|\\ |\s)*\}\s*")
SUPERSCRIPTS = str.maketrans(
    {"⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9"}
)


def canon(
    value: str,
    *,
    strip_trailing_punct: bool = True,
    collapse_empty_brackets: bool = True,
    preserve_parallelogram: bool = False,
) -> str:
    # NFKC alone turns cm² into cm2 while LaTeX remains cm^2.  Preserve the
    # exponent marker before normalization so typographic and LaTeX forms
    # compare as the same reading without conflating x² with x2.
    text = unicodedata.normalize("NFKC", str(value or "").translate(SUPERSCRIPTS))
    text = SCORE.sub("", text)
    text = BLANK.sub("_", text)
    text = SPACING.sub("", text)
    text = COMMAND.sub(lambda m: SYMBOLS.get(m.group(1), "\\" + m.group(1)), text)
    text = "".join(ch if preserve_parallelogram and ch == "▱" else PUNCT.get(ch, ch) for ch in text)
    text = text.replace("//", "∥").replace("^\\circ", "°").replace("^°", "°")
    text = re.sub(r"[\s$\\{}]", "", text)
    # Spacing and braces hid these pairs from the replacements above
    # (“$B C / / A D$”, “60^{\\circ}”); apply them once more on the bare text.
    text = text.replace("//", "∥").replace("^°", "°")
    if collapse_empty_brackets:
        text = re.sub(r"\(\)|\[\]", "()", text)
    # √3 and \sqrt{3} print the same; so do ≌ and \cong.
    text = text.replace("√", "sqrt").replace("≌", "≅")
    return text.rstrip(".,;:") if strip_trailing_punct else text


def same_reading(first: dict, second: dict) -> bool:
    if canon(first.get("stem", "")) != canon(second.get("stem", "")):
        return False
    keys = set(first.get("options") or {}) | set(second.get("options") or {})
    return all(
        canon((first.get("options") or {}).get(k, "")) == canon((second.get("options") or {}).get(k, "")) for k in keys
    )


def _reading_key(reading: dict) -> str:
    options = reading.get("options") or {}
    return canon(reading.get("stem", "")) + "".join(f"\u241f{key}:{canon(options[key])}" for key in sorted(options))


def _differing_spans(source: str, target: str) -> list[tuple[int, int]]:
    """Spans of ``target`` that differ from ``source``; an insertion point is (j, j)."""
    matcher = SequenceMatcher(None, source, target, autojunk=False)
    return [(j1, j2) for op, _i1, _i2, j1, j2 in matcher.get_opcodes() if op != "equal"]


def spotwise_majority(first: dict, second: dict, judge: dict) -> bool:
    """Whether every spot of the judge's reading agrees with at least one reader.

    The arbiter sees both readings and decides each disputed spot.  When it
    takes the first reader's word in one place and the second reader's in
    another, every character still has two of three votes.  Only a spot where
    it differs from both readers (something neither of them read) is a real
    three-way disagreement.
    """
    target = _reading_key(judge)
    if not target:
        return False
    spans_first = _differing_spans(_reading_key(first), target)
    spans_second = _differing_spans(_reading_key(second), target)
    if not spans_first or not spans_second:
        return not spans_first or not spans_second
    # Spots a character or two apart are one spot: “已知点” / “知识点” judged
    # “知点” dropped a character from each reader, and neither reads that.
    for start_a, end_a in spans_first:
        for start_b, end_b in spans_second:
            if start_a <= end_b + 2 and start_b <= end_a + 2:
                return False
    return True


# 平行四边形符号 ▱：模型有时写成 \square、\Box、\parallelogram 或方框字符。
# 只在后面紧跟 2–5 个顶点字母（如 ABCD）时才改；填空框和运算中的 \square 不动。
_PARALLELOGRAM_TOKEN = (
    r"(?:\\(?:square|Box|parallelogram)(?![A-Za-z])|[□◻⬜]|"
    r"\\(?:text|mathrm|mathbf)\{\s*(?:[□◻⬜▱]|\\(?:square|Box|parallelogram)(?![A-Za-z]))\s*\})"
)
_VERTICES = r"((?:[A-Z]\s*){2,5})"
_TOKEN_BEFORE_VERTICES = re.compile(_PARALLELOGRAM_TOKEN + r"\s*(?:\{\s*\}\s*)?(?=(?:[A-Z]\s*){2})")
_ONLY_PARALLELOGRAM = re.compile(r"^\s*" + _PARALLELOGRAM_TOKEN + r"\s*(?:\{\s*\}\s*)?" + _VERTICES + r"\s*$")
_MATH_SPAN = re.compile(r"(?<!\\)(\${1,2})(.*?)(?<!\\)\1", re.S)
_TOKEN_BEFORE_MATH_VERTICES = re.compile(_PARALLELOGRAM_TOKEN + r"\s*(?=\$\s*(?:[A-Z]\s*){2,5}\$)")


def fix_symbols(value: str) -> str:
    """把误写的平行四边形方框改回 ▱；原始填空框及乘法占位符保持不变。"""
    text = str(value or "")
    # □$ABCD$ 这类符号和顶点被分别包裹的写法先处理。
    text = _TOKEN_BEFORE_MATH_VERTICES.sub("▱", text)
    result: list[str] = []
    start = 0
    for match in _MATH_SPAN.finditer(text):
        result.append(_TOKEN_BEFORE_VERTICES.sub("▱", text[start : match.start()]))
        delimiter, body = match.group(1), match.group(2)
        whole = _ONLY_PARALLELOGRAM.fullmatch(body)
        if _BLANK_ONLY.fullmatch(body):
            result.append("____")
        elif whole:
            result.append("▱" + re.sub(r"\s+", "", whole.group(1)))
        else:
            body = _TOKEN_BEFORE_VERTICES.sub(r"\\text{▱}", body)
            result.append(f"{delimiter}{body}{delimiter}")
        start = match.end()
    result.append(_TOKEN_BEFORE_VERTICES.sub("▱", text[start:]))
    return "".join(result)


def fix_reading_symbols(value: dict) -> dict:
    """规范结构化识读结果；保留 raw 原文，便于追查模型当时实际返回了什么。"""
    if not isinstance(value, dict):
        return value
    fixed = dict(value)
    if isinstance(fixed.get("stem"), str):
        fixed["stem"] = fix_symbols(fixed["stem"])
    if isinstance(fixed.get("options"), dict):
        fixed["options"] = {
            key: fix_symbols(option) if isinstance(option, str) else option for key, option in fixed["options"].items()
        }
    return fixed


NUMBER_PREFIX = re.compile(r"^\s*(\d{1,2})\s*[.．、]+\s*")
# “14 如图，…”: a printed number whose dot was never there or not seen.
BARE_NUMBER_PREFIX = re.compile(r"^\s*(\d{1,2})\s+(?=[\u4e00-\u9fff])")


_SCORE_MARK = r"[(（]\s*(?:本小?题)?\s*(?:满分)?\s*(?:共)?\s*\d{1,2}\s*分\s*[)）]"
# 分区标题被一起截进题卡时，读者会把它抄在题面末尾（“[选修 4-5：不等式选讲]”、
# “（二）选考题：共 10 分……”、“三、解答题……”）。
_TRAILING_SECTION = re.compile(
    r"(?:\n\s*(?:[\[【(（]?\s*选修\s*\d+\s*[-－—–]\s*\d+[^\n]*"
    r"|[(（]\s*[一二三]\s*[)）]\s*(?:必考|选考)题[^\n]*"
    r"|[一二三四五六七八九十]{1,2}\s*[、.．]\s*(?:单项|多项|不定项)?(?:选择|填空|解答|计算|判断|作图)题[^\n]*))+\s*$"
)


# A textbook example's printed label: “例1”“例 2．”“例题3”“【例4】”“**例5**”.
# It names the card, like a question number, and is never part of the task
# (the card itself records that it is an example).  “例2中的函数……” is an
# exercise that refers back to an example: that label is content and stays.
_EXAMPLE_LABEL = re.compile(
    r"^\s*(?:\*{1,2}\s*)?[【\[]?\s*(?:例题|例)\s*(?:\d{1,3}|[一二三四五六七八九十]{1,3})(?!\d)"
    r"\s*[】\]]?\s*(?:\*{1,2}\s*)?(?:[.．、:：，,]\s*)?"
)
_EXAMPLE_REFERENCE = re.compile(r"^(?:中(?:的|命题)?|的(?:结果|证明|解法|结论)|参见|见上?例|[和与及]\s*例)")


def strip_example_label(text: str) -> str:
    """Drop a leading example label; anything else is returned unchanged."""
    value = str(text or "")
    match = _EXAMPLE_LABEL.match(value)
    if not match:
        return value
    rest = value[match.end() :]
    if not rest.strip() or _EXAMPLE_REFERENCE.match(rest):
        return value
    return rest


# A printed type note in front of the task: “（多项选择题）函数……”, “【多选】”,
# “(单选题)”.  It says what kind of question this is; it is not task text.
_TYPE_LABEL = re.compile(
    r"^\s*[(（【\[]\s*(多项选择题|多项选择|多选题|多选|不定项选择题|不定项选择|不定项"
    r"|单项选择题|单项选择|单选题|单选)\s*[)）】\]]\s*"
)


def strip_type_label(text: str) -> tuple[str, str | None]:
    """Drop a leading type note; return the text and the type it names."""
    value = str(text or "")
    match = _TYPE_LABEL.match(value)
    if not match or not value[match.end() :].strip():
        return value, None
    kind = "single_choice" if match.group(1).startswith("单") else "multiple_choice"
    return value[match.end() :], kind


# 教辅、汇编常在题目前印上出处：“[2026山东枣庄滕州二中月考]”“（2025·北京海淀·期中）”
# “【2024新课标Ⅰ卷】”。它说的是题从哪里来，不是题目本身；放在题干里，组卷打印时
# 会被印出来，也没法按出处找题。只认题干最开头、成对括号里的一段，并且要像出处：
# 有年份或考试名称，有汉字，没有公式。
_ORIGIN_LEAD = re.compile(r"^[ \t　]*([\[【(（])([^\[\]【】()（）\n$\\]{4,60})([\]】)）])[ \t　]*")
_ORIGIN_PAIRS = {"[": "]", "【": "】", "(": ")）", "（": ")）"}
_ORIGIN_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_ORIGIN_WORDS = re.compile(
    r"月考|期中|期末|模拟|联考|调研|质检|质量检测|统考|中考|高考|真题|[一二三四]模|学年|学期|"
    r"检测|竞赛|期初|开学考|会考|学考|学业水平|适应性|诊断|摸底|新课标|新高考|全国[甲乙丙]?卷|"
    r"阶段练|考试|测试|测评"
)
_ORIGIN_SCORE = re.compile(r"满分|\d\s*分(?![校类析])")
# A year alone is not a source: “（2022年北京冬奥会期间）某商店……” is task text.
# With a year, a source also names its paper: “·”-separated parts, a 卷, or a school.
_ORIGIN_PAPER = re.compile(r"·|卷|[一二三四五六七八九十\d]中|中学|附中|高中|学校|外国语|实验学校")
_ORIGIN_CJK = re.compile(r"[一-鿿]")
# 全国卷的几种写法：全国甲、全国甲理、全国甲文。带年份才认，光一个“全国甲”不算。
_ORIGIN_NATIONAL = re.compile(r"全国[甲乙丙][理文数]?")
# 出处常写成“年份+卷别/考试名，题号，分值，难度”：
#   （2021新高考Ⅰ，7，5分，中）  （2021全国甲理，13，5分，易）
# 后三段是题号、分值、难度，不是出处本身。以前整段里一看见“N分”就放过，
# 这类标记一个都认不出来 —— 标记留在题干里，既没法按出处找题，组卷打印时
# 还会印在学生卷子上。现在只拿第一段判定，后几段不参与否决。
_ORIGIN_SEGMENT = re.compile(r"[，,、]")


# 比出处时不算的东西：空格和标点。「2021新高考Ⅰ，7，5分，中」跟「2021新高考Ⅰ」
# 差的就是这些。
_SOURCE_KEY = re.compile(r"[\s，,、。．.：:；;（）()【】\[\]·・\-—_～~]")


def _source_head(value: str) -> str:
    """一段出处里说话的那一截：第一个逗号之前。后面几段是题号、分值、难度。"""
    text = str(value or "").strip()
    for separator in ("，", ","):
        if separator in text:
            text = text.split(separator)[0]
    return _SOURCE_KEY.sub("", text)


def split_origin_note(text: str) -> tuple[str, str]:
    """把题干开头那道出处摘下来（老师手动指定出处时用）。

    自动识别只认最开头那一段 —— 但出处常常前面还压着一道栏目名
    （「（创新考法）（2024江苏南通一中月考，6）……」）。老师点名要改出处时，
    那一段在哪儿都找得到：栏目名留在题干里，出处那一段摘走。

    返回 (剩下的题干, 出处)；没找到就是 (原样, "")。
    """
    value = str(text or "")
    stem, origin = split_origin(value)
    if origin:
        return stem, origin
    lead = _ORIGIN_LEAD.match(value)
    if not lead:
        return value, ""
    stem2, origin2 = split_origin(value[lead.end() :])
    if not origin2:
        return value, ""
    return value[: lead.end()] + stem2, origin2


def same_source_note(printed: str, origin: str) -> bool:
    """题干开头印的那段出处，和卡上记的，是不是同一件事。

    是同一件事才把那段括号从题干搬走 —— 否则老师什么都没改、随手点一下保存，
    题干里另一段出处就被悄悄删掉了。
    """
    left = _source_head(printed)
    right = _source_head(origin)
    if not left or not right:
        return False
    return left.startswith(right) or right.startswith(left)


def split_origin(text: str) -> tuple[str, str]:
    """Take a printed source note off the front of a stem: (stem, origin) or (text, "")."""
    value = str(text or "")
    match = _ORIGIN_LEAD.match(value)
    if not match:
        return value, ""
    opening, inner, closing = match.groups()
    rest = value[match.end() :]
    inner = inner.strip()
    if closing not in _ORIGIN_PAIRS[opening] or not rest.strip():
        return value, ""
    if len(_ORIGIN_CJK.findall(inner)) < 2:
        return value, ""
    # 只拿第一段判定：后几段是题号、分值、难度。
    head = _ORIGIN_SEGMENT.split(inner)[0].strip()
    if not head or _ORIGIN_SCORE.search(head):
        return value, ""
    if not (
        _ORIGIN_WORDS.search(head)
        or (_ORIGIN_YEAR.search(head) and (_ORIGIN_PAPER.search(head) or _ORIGIN_NATIONAL.search(head)))
    ):
        return value, ""
    return rest.lstrip(), inner


# 中文句子里的英文双引号："∀x∈B, x∈A"是真命题 → “∀x∈B, x∈A”是真命题。
# 公式、HTML 表格标记里的引号不动；英文句子（引号两边都不是汉字）不动；
# 引号个数是单数、或一对引号跨了行，说明认不准，整段都不动。
_QUOTE_SKIP = re.compile(r"\$\$.+?\$\$|\$[^$\n]+?\$|\\\(.+?\\\)|\\\[.+?\\\]|<[^<>\n]+>", re.S)
_CJK_OR_MARK = re.compile(r"[　-〿＀-￯一-鿿]")


def chinese_quotes(text: str) -> str:
    value = str(text or "")
    if '"' not in value:
        return value
    skipped = [(match.start(), match.end()) for match in _QUOTE_SKIP.finditer(value)]
    positions = [
        index
        for index, character in enumerate(value)
        if character == '"' and not any(start <= index < end for start, end in skipped)
    ]
    if not positions or len(positions) % 2:
        return value
    characters = list(value)

    def neighbour(index: int, step: int) -> str:
        index += step
        while 0 <= index < len(value) and value[index] in " \t　":
            index += step
        return value[index] if 0 <= index < len(value) else ""

    for opening, closing in zip(positions[0::2], positions[1::2]):
        inner = value[opening + 1 : closing]
        if "\n" in inner:
            return value
        chinese = (
            _CJK_OR_MARK.match(neighbour(opening, -1) or " ")
            or _CJK_OR_MARK.match(neighbour(closing, 1) or " ")
            or _ORIGIN_CJK.search(inner)
        )
        if chinese:
            characters[opening], characters[closing] = "“", "”"
    return "".join(characters)


def _is_own_number(value: str, number: int | None) -> bool:
    """Whether a printed number is this card's, allowing a binding edge that
    clipped its leading digits (“9.” for 19, “0.” for 20)."""
    if number is None:
        return True
    return int(value) == number or (len(value) < len(str(number)) and str(number).endswith(value))


# 行首的印刷题号：``2.`` ``3．`` ``*5.`` ``※3.``。教材用星号和 ※ 标难题与拓展题，
# 那些也是独立的一道题。
_OTHER_QUESTION_NUMBER = re.compile(r"^\s*[*※＊▲]?\s*(\d{1,3})\s*[.．、]")


def merged_question_number(stem: str, number: int | None = None) -> int | None:
    """题干里印着另一道题的题号时返回那个号，否则 None。

    两道题挨得太近时 MinerU 会把它们并成同一个版面块，第二道题的题号就不在
    块开头，切题认不出来，它的正文被并进上一张卡 —— 这张卡看上去只有一道题，
    却是绿的。判据只看纸上印着的形状，不依赖切题结果。

    （1）这种小问不需要单独豁免：小问以括号开头，本来就匹配不上题号。
    但一道题里若用「1. 2.」列小项，那些项会被当成另一道题报出来。真实题库
    （14 份资料、700 多张带数学写法的卡）里这种写法一次都没出现。
    """
    for index, line in enumerate(str(stem or "").splitlines()):
        if index == 0:
            continue
        matched = _OTHER_QUESTION_NUMBER.match(line)
        if matched and not _is_own_number(matched.group(1), number):
            return int(matched.group(1))
    return None


def printed_question_numbers(witness: str, number: int | None = None) -> list[int]:
    """这张卡的范围里印着哪几道别的题。

    切题把每道题的范围切出来，最后一道常常一路罩到本页末尾、下一页开头，后面
    那几道题就跟着进了它的范围 —— 书上印着，卡上没有，而这几道题在库里也没有
    别的卡（八下习题 7.1 的第 2 题、九上第 63 页的 ※16～※18 都是这样没的）。
    判据只读纸上印着的形状，不依赖切题结果。

    只在教材上用。一张试卷分几道大题、各自从 1 重新编号，卷内第 23 题面上印的
    是「3.」，照这个判据会把整张卷都报一遍。
    """
    found: list[int] = []
    for line in str(witness or "").splitlines():
        matched = _OTHER_QUESTION_NUMBER.match(line)
        if not matched:
            continue
        if _is_own_number(matched.group(1), number):
            continue
        value = int(matched.group(1))
        if value not in found:
            found.append(value)
    return found


# 教材的栏目名和章节名。范围一旦把它们罩进去，成品卷上这张卡就带着一整块不相干的
# 内容——八下 id=811 第 1 题罩住了第 1、2、3、※4 四道题外加「数学理解」整栏；九上
# id=1386 第 23 题罩住两道题外加「用公式法解一元二次方程」「二次根式的乘除」两节新课。
# 名单是从两本教材的块里量出来的（短、不含数字标点、全书反复出现），逐个裁图看过：
# 「即」「所以」「山东教育出版社」「I」那几类同形状的短块不是标题，没有收进来。
SECTION_HEADINGS = frozenset(
    {
        "数学理解",
        "问题解决",
        "联系拓广",
        "议一议",
        "做一做",
        "想一想",
        "读一读",
        "回顾与思考",
        "综合与实践",
        "直觉的误导",
        "随堂练习",
        "知识技能",
        "学习目标",
        "复习题",
        "总复习题",
        "习题",
        # 这两本书的章节名。MinerU 常把「第X章」和标题拆成两块分开印，
        # 所以只靠 ``_SECTION_HEADING`` 的「第X章…」会漏掉单独印的那一半。
        "特殊平行四边形",
        "图形的相似",
        "一元二次方程",
        "二次函数",
        "二次根式的乘除",
        "菱形的性质与判定",
        "矩形的性质与判定",
        "相似三角形的性质",
        "二次函数的应用",
        "一元二次方程的应用",
    }
)

# 卷面自己印的大题/部分标题：一、选择题、二、填空题、第X部分/第X章。
_SECTION_HEADING = re.compile(r"^(?:第[一二三四五六七八九十]+(?:部分|章)|[一二三四五六七八九十]+、)")


def _heading_text(block: dict) -> str:
    return re.sub(r"\s+", "", str(block.get("text") or "")).strip("　 ")


def _block_position(block: dict) -> tuple[int, float] | None:
    try:
        return (int(block.get("page_idx") or 0), float((block.get("bbox") or [0, 0])[1]))
    except (TypeError, ValueError, IndexError):
        return None


def section_headings_after_anchor(blocks, anchor_seq, text: str = "") -> list[str]:
    """这道题的范围往后罩住了哪些栏目名/章节名。

    判据只读纸上印着的形状：块文字**恰好**是一个栏目标题，或印成「第X章」「一、」
    这种大题标题，而题干里没有这句话。

    只看印在这道题**之后**的那些块。范围上方印着的小节标题是正常的——八下「知识技能」
    就正好压在第 1 题范围的上边界外，第一版没做这个区分，六张样本里四张是这么虚报的。

    「知识技能」「随堂练习」「学习目标」这些是给习题当小节标题的，本该在题的上方；
    印在题**下方**只有一个解释：范围罩过了下一节。

    锚点块不在范围内时（人工补的卡）整张放弃这条判据——宁可漏报，也不要把整页标题
    都算成越界。
    """
    body = re.sub(r"\s+", "", str(text or ""))
    anchor = None
    prepared = []
    for block in blocks or []:
        if not isinstance(block, dict):
            continue
        position = _block_position(block)
        if position is None:
            continue
        if anchor_seq is not None and block.get("seq") == anchor_seq:
            anchor = position
        heading = _heading_text(block)
        if not heading or heading in body:
            continue
        if heading not in SECTION_HEADINGS and not _SECTION_HEADING.match(heading):
            continue
        prepared.append((position, heading))
    if anchor is None:
        return []
    found = []
    for position, heading in sorted(prepared):
        if position > anchor and heading not in found:
            found.append(heading)
    return found


# 小问号：(1)(2)(3)。只认这个形状，「图（1）」「第 2 题」「（第 1 题）」不是小问，
# 后者带汉字，直接被下面的正则排除。
SUB_QUESTION_MARK = re.compile(r"[（(]\s*(\d{1,2})\s*[)）]")
_SOLUTION_LABEL = re.compile(r"^\s*(?:解|答|分析|证明)\s*[:：]", re.M)
_SUB_QUESTION_LIMIT = 12


def missing_sub_questions(stem: str, blocks) -> list[int]:
    """正文的小问编号在整段里跳号，而那些号在本题范围内确实印着内容时，返回缺的号。

    九上 id=1152（第 1 题）就是这样丢了一道：纸上印着
    「(1)…试比较 y1 与 y2；(2)…试比较 y3 与 y4；(3)…试比较 y5 与 y6」，
    卡上只剩 (1) 和 (3)。切题吞掉一道小问，成品卷上就是 (1)(3)，学生一眼就看出来。

    两道判据缺一不可，少一条都会教用户忽略这条提示：

    1. **跳号且那个号整段正文里一次都没出现过**。只看相邻标记会把教材把「做一做」和
       例题并成一张的卡误判成跳号（(1)(2)…(1)(3)，四张样本全是这种）。
    2. **范围内印着这个号，而且后面跟着实打实的内容**。北师大版的例题与习题题面只印
       单数小题（例 1 的题面就是「(1)…(3)」，(2)(4) 只出现在下面的「解」里），
       这种不是丢题，是书上就这么印的；14 张跳号样本里有 13 张属于这一类。

    「解：」之后的内容不算题面，所以先按它把块文字截断。
    """
    marks = [int(match.group(1)) for match in SUB_QUESTION_MARK.finditer(stem or "")]
    if len(marks) < 2:
        return []
    present = set(marks)
    missing: set[int] = set()
    for before, after in zip(marks, marks[1:]):
        if after > before + 1:
            missing.update(n for n in range(before + 1, min(after, _SUB_QUESTION_LIMIT + 1)) if n not in present)
    if not missing:
        return []

    printed = []
    for block in blocks or []:
        if isinstance(block, dict):
            position = _block_position(block)
            text = str(block.get("text") or "")
            if position is None:
                continue
            printed.append((position, text))
    if not printed:
        return []
    # 按阅读顺序接起来，再从第一个「解：」处截断——答案区里的小问不算题面。
    flat = "\n".join(text for _position, text in sorted(printed))
    label = _SOLUTION_LABEL.search(flat)
    if label is not None:
        flat = flat[: label.start()]
    return sorted(
        n
        for n in missing
        if any(
            match.group(1) and int(match.group(1)) == n and flat[match.end() : match.end() + 40].strip()
            for match in SUB_QUESTION_MARK.finditer(flat)
        )
    )


# 判「读出来的题是不是另一道」用的取样：只认连续 6 个以上的汉字，公式和标点不算。
_ANCHOR_HAN = re.compile(r"[一-鿿]{6,}")
_ANCHOR_PROSE = re.compile(r"如图所示|求证|计算下列|已知如图|如图，|试求|求下列|下列各式")
_ANCHOR_TAIL_NUMBER = re.compile(r"\d{1,3}\s*[.．、]")
_ANCHOR_WINDOW = 4

# 教辅在题前印的出处：「(2025届江苏盐城射阳中学开学考,6)」「（2024全国甲理，6，5分，易）」。
# 它说的是这道题从哪来，不是题目本身 —— 识读时就被单独拆进「题源」字段了。
# 这里判它比 ``split_origin`` 宽一点：``split_origin`` 把带分值的段落（「(2024全国甲理,
# 6,5分,易)」「（本小题满分12分）」）一律当题面的一部分，两种混在一起分不开；
# 这里只把「含满分」当题面，其余含年份/考试名/学校名的都当出处。
_SOURCE_NOTE_LEAD = re.compile(r"^[ \t　]*[\[【(（]([^\[\]【】()（）\n$\\]{4,60})[\]】)）]")
_SOURCE_NOTE_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_SOURCE_NOTE_PAPER = re.compile(r"·|卷|[一二三四五六七八九十\d]中|中学|附中|高中|学校|外国语|实验学校|全国[甲乙丙]")
_SOURCE_NOTE_WORDS = re.compile(
    r"月考|期中|期末|模拟|联考|调研|质检|质量检测|统考|中考|高考|真题|模考|学年|学期|"
    r"开学考|会考|学考|学业水平|适应性|诊断|摸底|新课标|新高考|全国[甲乙丙]|考试|测试|测评"
)
# 一段字里有多少是那句出处的。出处那句通常整段都在「题源」里，题干不会。
_SOURCE_NOTE_OVERLAP = 0.6


def _source_note(printed: str) -> str:
    """印刷行开头成对括号里的出处那一段；认不出来返回空串。

    要的是「这一段是出处」这件事，不是要把出处拆下来存 —— 拆是 ``split_origin``
    的活，这里只是别把出处当题目文字去取样。
    """
    body = _OTHER_QUESTION_NUMBER.sub("", str(printed or "").strip(), count=1)
    match = _SOURCE_NOTE_LEAD.match(body)
    if not match:
        return ""
    inner = match.group(1).strip()
    if len(_ORIGIN_CJK.findall(inner)) < 2 or "满分" in inner:
        return ""
    if (_SOURCE_NOTE_YEAR.search(inner) and _SOURCE_NOTE_PAPER.search(inner)) or _SOURCE_NOTE_WORDS.search(inner):
        return inner
    return ""


def _is_source_note(run: str, notes: list[str]) -> bool:
    """run 这段字是不是就是那句出处。

    同一个字在出处里出现过就算数：认字可能有出入（「届」认成「眉」），但出处那句
    的用词高度重合，题干不会。
    """
    if not run or not notes:
        return False
    for note in notes:
        pool = Counter(note)
        hit = 0
        for char in run:
            if pool.get(char):
                pool[char] -= 1
                hit += 1
        if hit / len(run) >= _SOURCE_NOTE_OVERLAP:
            return True
    return False


def anchor_reading_mismatch(printed: str, reading: str, origin: str = "") -> str | None:
    """卡上题号那一行印的话，读出来的内容里一点都认不出时，返回那句话。

    教材最后一道题的范围常常一路罩到下一节正文，识读就可能把正文读成题干 ——
    八下第 4 题印着「4. 如果一个三角形两边的长分别等于一元二次方程…」，卡上写的
    却是「在一块长 16 m、宽 12 m 的矩形土地上，要建造一个花园…」。这类卡看上去
    很正常、旗标也是空的，老师根本看不出来。

    判据两边都只用文字：纸上印的那一行，和识读写出来的题干/选项/解析。不依赖
    切题结果，也不依赖模型的自述。

    比对必须容得下**改写**：模型会把「化去根号内的分母」读成「化简下列各式根号
    内的分母」。所以取一段最长的实词，再看有没有任何 4 个字原样出现在读出来的
    内容里；一个都没有，才是读成了别的东西。

    ⚠️ **出处那一句不能拿来比**（2026-10-24 实测）。教辅在题前印出处
    「(2025届江苏盐城射阳中学开学考,6)」，这一句比题干还长，原来被当题目文字取样；
    可它早就被单独拆进「题源」字段了，压根不在题干里 —— 于是每一张带出处的题都被
    报成「认不出这句话，卡上可能是另一道题」。真库 10 张里 9 张是这么误报的
    （#1085~#1099，同一份卷子）。现在先把出处剔掉，probe 落在真正的题干上：误报
    没了，判据本身也没削弱 —— 卡片真装了另一道题时，probe 照样认不出（#1100 就是
    这种，它锚在第 7 题、卡上装的是第 1 题）。
    """
    body = _ANCHOR_PROSE.sub("", str(printed or "").strip())
    body = _OTHER_QUESTION_NUMBER.sub("", body, count=1)
    # 一个版面块里可能塞进了下一道题（八下第 2 题那块就带着「3. 分别说说…」）。
    # 只看本题题号之后、下一道题号之前的那一段，否则取样取的是别人的话。
    body = _ANCHOR_TAIL_NUMBER.split(body, maxsplit=1)[0]
    runs = _ANCHOR_HAN.findall(body)
    if not runs:
        return None
    # 出处那句不算题目内容：它不在题干里，拿它去比对必然认不出。两处都认：
    # 卡片上的「题源」字段，和印刷行开头括号里那一段（两者可能对不上）。
    notes = [note for note in (origin, _source_note(printed)) if note]
    candidates = [run for run in runs if not _is_source_note(run, notes)]
    if not candidates:
        # 这一行除了出处没有别的可取样的文字 —— 没有可比的题目内容，不报。
        return None
    probe = max(candidates, key=len)[:10]
    squeezed = re.sub(r"\s+", "", str(reading or ""))
    for index in range(len(probe) - _ANCHOR_WINDOW + 1):
        if probe[index : index + _ANCHOR_WINDOW] in squeezed:
            return None
    return probe


# 印刷解答的起点。全库量过：带冒号的是「解：」71 处、「证明：」11 处（只在八下）、
# 「答：」4 处（只在胜利初四答案版本）。只认这三种。
# 「解析」「解答」两种写法全库一次没出现过，不写进去。
# ⚠️ 【】这层壳也不认：`【答案】`24 + `【详解】`16 + `【分析】`25 = 65 处全是印刷解答，
# 但**一处都没进过任何卡的题干**（扫描 tmp/scan_bracket_two_families.py）；
# 同一副壳还有 `【深入探究】`【问题背景】`【初步感知】`【运用】`【点睛】` 9 处小节标题，
# 其中 4 处真的进了题干。认它就换来 0 收益 + 一批误报，不认。
SOLUTION_LABEL_RE = re.compile(r"^\s*(?:解|证明|答)\s*[：:]")


def printed_solution_in_stem(printed_anchor: str, stem: str) -> str | None:
    """题干里混进了别处那道题的印刷解答时，返回那一行的开头，否则 None。

    要分开两件长得一样的事：

      * ``1.证明：对角线相等的菱形是正方形。`` —— 题干本身就是一道证明题，
        「证明：」是它的动词，不是答案。八下有 5 张这样，守门放过它们。
      * ``4. 如图，在菱形 ABCD 中…有多少个等腰三角形？`` 后面跟着
        ``证明：∵ 四边形 ABCD 是平行四边形，∴ …`` —— 那是别的题的印刷解答，
        成品卷上学生做这道题时答案就摆在题后面。

    守门：锚点那一行本身带解答标签时，题干里出现的就不算混进。防的是答案版
    把卡锚在解答上那种形状 —— 库里目前 0 样本（那 4 处「答：」都不是任何卡的锚点），
    留着是为了它真出现时不误报，不是靠它现在挡住什么。
    """
    for line in str(printed_anchor or "").splitlines():
        body = _OTHER_QUESTION_NUMBER.sub("", line, count=1)
        if SOLUTION_LABEL_RE.match(body):
            return None
    for line in str(stem or "").splitlines():
        if SOLUTION_LABEL_RE.match(line):
            return line.strip()[:40]
    return None


def clean_stem(stem: str, number: int | None = None) -> str:
    """去掉模型偶尔带上的题号、例题标号和分值。"""
    text = strip_example_label(str(stem or "").strip()).strip()
    # 截图顶部带进了上一段的尾巴（“合题目要求的。\n1．经过点……”）：
    # 从本题自己的题号那一行开始。
    if number is not None:
        own_line = re.search(rf"\n\s*{number}\s*[.．、]+(?!\d)\s*", text[:120])
        if (
            own_line
            and "\n" in text[: own_line.start() + 1]
            and len(text[: own_line.start()].strip()) <= 60
            and not re.search(r"[(（]\s*\d\s*[)）]", text[: own_line.start()])
        ):
            text = text[own_line.start() :].strip()
    match = NUMBER_PREFIX.match(text)
    if match and _is_own_number(match.group(1), number) and not text[match.end() : match.end() + 1].isdigit():
        text = text[match.end() :]
    else:
        bare = BARE_NUMBER_PREFIX.match(text)
        if bare and number is not None and int(bare.group(1)) == number:
            text = text[bare.end() :]
    text = re.sub(rf"^\s*{_SCORE_MARK}\s*", "", text)
    # 誊录规则明写「分值不要写进题干」，可原来只去题首那一个。识读把整句抄
    # 进来时（「计算（7分）：」），题首根本没有分值，这条就漏了——汶源第 16 题
    # 是全库唯一一处，照原样进了组卷卷面。数学题干里括号包着的「N分」不会是
    # 内容，整段连同两侧空格一起去掉。
    text = re.sub(rf"\s*{_SCORE_MARK}\s*", "", text)
    # A reader that dropped “本题满分10分” sometimes leaves its opening bracket:
    # “（（1）如图1…”.
    text = re.sub(r"^\s*[(（]\s*(?=[(（]\s*\d{1,2}\s*[)）])", "", text)
    # “2..如图” on the paper: the reader drops the number but keeps a dot.
    text = re.sub(r"^\s*[.．、]+\s*(?=[\u4e00-\u9fff（(])", "", text)
    # An emptied score bracket: “（ ）如图，在▱ABCD中…”.
    text = re.sub(r"^\s*[(（]\s*[)）]\s*(?=[\u4e00-\u9fff])", "", text)
    text = _TRAILING_SECTION.sub("", text)
    return text.strip()


def clean_option(value: str, key: str) -> str:
    text = str(value or "").strip()
    text = re.sub(rf"^\s*{key}\s*[.．、:：]\s*", "", text)
    return text.strip()


# ---------------------------------------------------------------- 跨引擎旁证
#
# MinerU already returns its own OCR text for every block it found.  It is a
# different engine from the vision reader, so when the two agree on every
# mathematical mark and character, the card has cross-engine support.  A
# formatting difference may cause another vision read; a false green card is
# worse than that extra call.

# MinerU marks a *real* sub/superscript with HTML only when it is glued to
# the symbol it belongs to (x<sub>2</sub>).  On digital PDFs it also wraps
# whole runs of a line that sits off the baseline, question number and
# Chinese text included (“<sub>12.</sub> <sub>已知b是</sub>”); those tags are
# layout, not meaning.
_HTML_SCRIPT = re.compile(r"<(sub|sup)\b[^>]*>(.*?)</\1\s*>", re.I | re.S)
_SCRIPT_BASE = re.compile(r"[A-Za-z0-9)\]}'′]")
_CJK_OR_CJK_PUNCT = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]")
_INLINE_TAG = re.compile(r"</?(?:span|b|i|u|em|strong)\b[^>]*>", re.I)
_WITNESS_SCORE = re.compile(_SCORE_MARK)
_WITNESS_LEAD = re.compile(r"^\s*(?:\d{1,3}\s*[.．、]+(?!\d)|\d{1,3}\s+(?=[\u4e00-\u9fff]))\s*")
WITNESS_MIN_LENGTH = 6

# Spellings that print identically.  Each pair is an equivalence, never a
# removal: a decoration, bracket or mark that one engine has and the other
# lacks still makes the texts differ.
_BAR = re.compile(r"\\bar(?![A-Za-z])")
_STYLE = re.compile(r"\\(?:scriptscriptstyle|scriptstyle)(?![A-Za-z])")
_CASES_OPEN = re.compile(r"\\left\s*\\\{(?:\s|\{)*\\begin\s*\{\s*array\s*\}\s*\{\s*[lcr]+\s*\}")
_CASES_CLOSE = re.compile(r"\\end\s*\{\s*array\s*\}(?:\s|\})*\\right\s*\.")
_MATH_SEGMENT = re.compile(r"\$[^$]*\$")


# A student's answer letter written into the printed answer brackets
# (“是（C）个”).  The letter must not follow a Latin letter, so P(A) stays.
_WITNESS_ANSWER = re.compile(r"(?<![A-Za-z])[(（]\s*[A-E]{1,5}\s*[)）]")


def _html_script(match: re.Match) -> str:
    tag, inner = match.group(1).lower(), match.group(2)
    before = match.string[: match.start()]
    glued = bool(before) and bool(_SCRIPT_BASE.fullmatch(before[-1]))
    layout = (
        bool(_CJK_OR_CJK_PUNCT.search(inner))
        or not before.strip()
        or bool(_CJK_OR_CJK_PUNCT.fullmatch(before.rstrip()[-1]))
    )
    if layout and not glued or _CJK_OR_CJK_PUNCT.search(inner):
        return inner
    return ("_" if tag == "sub" else "^") + "{" + inner + "}"


def witness_key(value: str) -> str:
    text = _HTML_SCRIPT.sub(_html_script, str(value or ""))
    text = _INLINE_TAG.sub("", text)
    text = _WITNESS_ANSWER.sub("（ ）", text)
    text = _WITNESS_LEAD.sub("", strip_example_label(text), count=1)
    text = strip_type_label(text)[0]
    text = _WITNESS_SCORE.sub("", text)
    text = _BAR.sub(r"\\overline", text)
    text = _STYLE.sub(" ", text)
    text = _CASES_OPEN.sub(r"\\begin{cases}", text)
    text = _CASES_CLOSE.sub(r"\\end{cases}", text)
    # Inside math, ~ is only a non-breaking space; in prose it is a mark.
    text = _MATH_SEGMENT.sub(lambda m: m.group(0).replace("~", " "), text)
    text = canon(text, strip_trailing_punct=False, collapse_empty_brackets=False, preserve_parallelogram=True)
    text = re.sub(r"\.{3,}|⋯", "…", text)
    # ΔABC is the triangle; ≌ and \cong are both congruence; √3 and \sqrt{3}.
    text = re.sub(r"Δ(?=[A-Z]{3}(?![A-Za-z]))", "△", text)
    return text.replace("≌", "≅").replace("√", "sqrt")


def reading_witness_text(reading: dict) -> str:
    """The prose MinerU's text can vouch for.  MinerU keeps tables apart from
    the text around them, so a table the reader wrote out is left out here;
    its cells are compared with MinerU's own table instead."""
    from .tables import without_tables

    options = reading.get("options") or {}
    return without_tables(str(reading.get("stem") or "")) + "".join(
        f"{key}.{options[key]}" for key in sorted(options) if str(options[key]).strip()
    )


# MinerU's OCR often drops sentence punctuation, answer blanks and empty
# answer brackets that the vision reader kept (“T_n.” / “T_n”, “为____.” /
# “为”).  A mark that only the *reading* has is tolerated where it cannot
# change mathematics: next to a Chinese character, at the very end, before a
# sub-question label “(1)”, or as the dot after an option letter.  The
# opposite gap is not tolerated: when MinerU saw a mark the reader left out,
# the reader may have dropped a printed comma, so the card is read again.
# Swapping one mark for another (，/；) or a mark between two symbols
# (x,y / xy, P(12,3) / P(1,23), a.b) always disagrees.
_CJK = re.compile(r"[\u4e00-\u9fff]")
_SUBQUESTION = re.compile(r"\((?:\d{1,2}|[ⅠⅡⅢⅣⅤⅰⅱⅲⅳ]|i{1,3}|iv)\)")
_GAP_MARKS = re.compile(r"[,.;:]{1,2}|_+[,.;:]?|\(\)")


def _harmless_gap(text: str, start: int, end: int) -> bool:
    piece = text[start:end]
    if not _GAP_MARKS.fullmatch(piece):
        return False
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if not after or _CJK.match(before or " ") or _CJK.match(after):
        return True
    if piece[0] in ",.;:" and _SUBQUESTION.match(text, end):
        return True
    # “A.2√3” / “A 2√3”: the dot after an option letter.
    return (
        piece == "."
        and before in "ABCDE"
        and start >= 1
        and (start < 2 or not text[start - 2].isalnum() or bool(_CJK.match(text[start - 2])))
    )


# MinerU repeats or strands option letters with nothing after them: “B. 48/5
# B.C. 4” and, for picture options, a bare “A. B. C. D.” at the end.
_BARE_LABELS = re.compile(r"(?:[A-E]\.)+")


def _bare_option_labels(text: str, start: int, end: int) -> bool:
    if not _BARE_LABELS.fullmatch(text[start:end]):
        return False
    rest = text[end:]
    return not rest or bool(re.match(r"[A-E]\.", rest))


def _keys_agree(reading_key: str, witness_key_: str) -> bool:
    if reading_key == witness_key_:
        return True
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, reading_key, witness_key_, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "delete" and _harmless_gap(reading_key, i1, i2)):
            continue
        if tag == "insert" and _bare_option_labels(witness_key_, j1, j2):
            continue
        return False
    return True


def witness_agrees(reading: dict | None, witness: str) -> bool:
    """Whether MinerU's own text independently supports a vision reading."""
    if not reading or not reading.get("stem") or reading.get("unclear"):
        return False
    expected = witness_key(witness)
    if len(expected) < WITNESS_MIN_LENGTH:
        return False
    # parse_reading() fixes a model's raw □ABCD into ▱ABCD for display.  The
    # witness must still see that the model did not actually transcribe the
    # printed symbol: otherwise MinerU's ▱ would falsely make the card green.
    raw = str(reading.get("raw") or "")
    if "▱" in expected and raw and (_TOKEN_BEFORE_VERTICES.search(raw) or _TOKEN_BEFORE_MATH_VERTICES.search(raw)):
        return False
    # MinerU has no ▱: it prints the parallelogram sign as □.  When the reader
    # itself wrote ▱ (not a □ that parse_reading() turned into ▱), that □ in
    # front of the vertices supports it.
    if (
        raw
        and "▱" in raw
        and "□" in expected
        and not (_TOKEN_BEFORE_VERTICES.search(raw) or _TOKEN_BEFORE_MATH_VERTICES.search(raw))
    ):
        expected = re.sub(r"□(?=[A-Z]{2,5})", "▱", expected)
    return _keys_agree(witness_key(reading_witness_text(reading)), expected)


# A spot where the vision reading and MinerU printed text differ one-for-one
# (“x^3” / “x^2”, “销售单价” / “销售定价”). Only clean substitutions count:
# short, made of letters, digits, Chinese or maths symbols, anchored by
# identical text on both sides — handwriting mixed into MinerU's text shows up
# as insertions, not these. The comparison is local and deterministic; it does
# not trigger another model request.
_OBJECTION_CHARS = re.compile(r"^[0-9A-Za-z\u4e00-\u9fffα-ωΑ-Ω△∠⊥∥≤≥≠±×÷°π∞]+$")
# How many characters a spot may hold on each side. The merge below consults it
# too, so joining two stretches never costs a spot the cap would have allowed.
_OBJECTION_MAX = 3
# A word read as a different word often shares one character with the original
# (“树根” → “根据” both contain 根), and that alone is enough for the diff to
# shred a single mistake into an insertion plus a substitution whose contexts
# then differ from the paper's. So two stretches separated by no more than this
# many matching characters are still one disagreement.
_OBJECTION_GAP = 1
# MinerU's usual confusions: a disagreement between these proves nothing
# (in testing MinerU was wrong at every α/a and △/V spot).
_OCR_LOOKALIKES = (
    {"O", "0"},
    {"o", "0"},
    {"l", "1"},
    {"I", "1"},
    {"I", "l"},
    {"α", "a"},
    {"β", "B"},
    {"γ", "y"},
    {"ρ", "p"},
    {"ω", "w"},
    {"ν", "v"},
    {"τ", "t"},
    {"χ", "x"},
    {"μ", "u"},
    {"△", "V"},
    {"∠", "L"},
    # Letters whose capital is the small letter drawn larger: MinerU's case
    # is no evidence (胜利初二 #23 “边长为 c” / C, 胜利初四 #23 “v” / V).
    *({letter, letter.upper()} for letter in "cosuvwxzpk"),
)


def _clean_key(key: str) -> tuple[str, list[int]]:
    """The key's letters, digits, Chinese and maths symbols, and where each
    kept character sat in the key.

    Punctuation and fillers (``,``。``,``_``) are what a reading and MinerU
    disagree about for free, and they are never evidence. Keeping them in the
    comparison lets one stray comma split a disagreement in two, and each half
    then fails the identical-context test — the real mistake goes unreported.
    """
    chars, index = [], []
    for position, char in enumerate(key):
        if _OBJECTION_CHARS.match(char):
            chars.append(char)
            index.append(position)
    return "".join(chars), index


def _trimmed(ka: str, kw: str, i1: int, i2: int, j1: int, j2: int):
    """A disagreement span, pulled back off everything the two sides agree on.

    Punctuation goes first: a comma where the paper has a period is not a
    difference.  Then the characters both sides already spell the same way —
    joining two stretches carries the matching characters between them into
    the span, and those must not be reported as part of the difference.  What
    is left empty was never a disagreement at all.
    """
    while i1 < i2 and not _OBJECTION_CHARS.match(ka[i1]):
        i1 += 1
    while i2 > i1 and not _OBJECTION_CHARS.match(ka[i2 - 1]):
        i2 -= 1
    while j1 < j2 and not _OBJECTION_CHARS.match(kw[j1]):
        j1 += 1
    while j2 > j1 and not _OBJECTION_CHARS.match(kw[j2 - 1]):
        j2 -= 1
    while i1 < i2 and j1 < j2 and ka[i1] == kw[j1]:
        i1, j1 = i1 + 1, j1 + 1
    while i2 > i1 and j2 > j1 and ka[i2 - 1] == kw[j2 - 1]:
        i2, j2 = i2 - 1, j2 - 1
    if i1 >= i2 or j1 >= j2:
        return None
    return (i1, i2, j1, j2)


def _clean_before(key: str, index: int, count: int) -> str:
    """The ``count`` characters before ``index``, skipping punctuation."""
    out = []
    for position in range(index - 1, -1, -1):
        if not _OBJECTION_CHARS.match(key[position]):
            continue
        out.append(key[position])
        if len(out) == count:
            break
    return "".join(reversed(out))


def _clean_after(key: str, index: int, count: int) -> str:
    out = []
    for position in range(index, len(key)):
        if not _OBJECTION_CHARS.match(key[position]):
            continue
        out.append(key[position])
        if len(out) == count:
            break
    return "".join(out)


def _disagreement_spans(reading_key: str, paper_key: str) -> list[tuple[int, int, int, int]]:
    """The stretches where the two keys differ, one pair per disagreement.

    Matching characters up to ``_OBJECTION_GAP`` long inside a disagreement do
    not end it (see ``_OBJECTION_GAP``); a longer run of them does.  Two
    stretches join only while the joined pair still fits ``_OBJECTION_MAX``:
    a punctuation slip just before the real mistake must not drag it out of
    range and lose it altogether.  A joined stretch is trimmed back to its
    letters, digits, Chinese and maths symbols, so the punctuation that made
    them neighbours is not part of the reported difference either.
    """
    spans: list[tuple[int, int, int, int]] = []
    run: tuple[int, int, int, int] | None = None
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, reading_key, paper_key, autojunk=False).get_opcodes():
        if tag == "equal":
            if run and (i2 - i1) > _OBJECTION_GAP:
                spans.append(run)
                run = None
            continue
        if run is not None:
            joined = _trimmed(reading_key, paper_key, run[0], i2, run[2], j2)
            if joined and joined[1] - joined[0] <= _OBJECTION_MAX and joined[3] - joined[2] <= _OBJECTION_MAX:
                run = joined
                continue
            spans.append(run)
        run = _trimmed(reading_key, paper_key, i1, i2, j1, j2) or (i1, i2, j1, j2)
    if run:
        spans.append(run)
    return spans


def witness_objections(reading: dict | None, witness: str, *, context: int = 3) -> list[dict]:
    if not reading or not reading.get("stem") or not witness:
        return []
    ka = witness_key(reading_witness_text(reading))
    kw = witness_key(witness)
    if len(kw) < WITNESS_MIN_LENGTH:
        return []
    # Each replacement on its own, then the joined stretches: joining is there to
    # catch the mistake the diff shredded, not to replace the rule that has been
    # reporting single substitutions all along.
    spans = [
        (i1, i2, j1, j2)
        for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kw, autojunk=False).get_opcodes()
        if tag == "replace"
    ]
    for span in _disagreement_spans(ka, kw):
        if span not in spans:
            spans.append(span)
    spots = []
    for i1, i2, j1, j2 in spans:
        read, seen = ka[i1:i2], kw[j1:j2]
        if len(read) > _OBJECTION_MAX or len(seen) > _OBJECTION_MAX or {read, seen} in _OCR_LOOKALIKES:
            continue
        if not _OBJECTION_CHARS.match(read) or not _OBJECTION_CHARS.match(seen):
            continue
        left = ka[i1 - context : i1] if i1 >= context else ""
        right = ka[i2 : i2 + context]
        # Either spelling of the context anchors the spot.  The raw one is what
        # this has always used; the clean one sees past a comma or a bracket a
        # neighbour disagreement left behind.  Reporting is for a person, so
        # either way of being sure is good enough.
        raw = (
            len(left) >= context
            and len(right) >= context
            and kw[j1 - context : j1] == left
            and kw[j2 : j2 + context] == right
        )
        clean_left, clean_right = _clean_before(ka, i1, context), _clean_after(ka, i2, context)
        clean = (
            len(clean_left) >= context
            and len(clean_right) >= context
            and _clean_before(kw, j1, context) == clean_left
            and _clean_after(kw, j2, context) == clean_right
        )
        if not (raw or clean):
            continue
        # Report the context that anchored the spot, raw whenever it could:
        # that is the text the card searches to mark the spot, and the clean
        # spelling exists only to decide.
        spots.append(
            {
                "reading": read,
                "mineru": seen,
                "before": left if raw else clean_left,
                "after": right if raw else clean_right,
                "at": i1,
            }
        )
    return spots


def _key_offset(text: str, key_index: int) -> int:
    """The raw index where ``witness_key(text)`` reaches ``key_index`` characters.

    The key is not built character by character, so this searches prefixes
    (it is close enough: a mark inside a formula covers the whole formula).
    """
    low, high = 0, len(text)
    while low < high:
        middle = (low + high) // 2
        if len(witness_key(text[:middle])) < key_index:
            low = middle + 1
        else:
            high = middle
    return low


def _find_all(haystack: str, needle: str, limit: int = 20) -> list[int]:
    hits, at = [], haystack.find(needle)
    while at >= 0 and len(hits) < limit:
        hits.append(at)
        at = haystack.find(needle, at + 1)
    return hits


def _raw_span(value: str, first: int, last: int, reading: str) -> tuple[int, int] | None:
    """The raw characters whose key is ``key[first:last]`` (== ``reading``), checked.

    The prefix search is only close (a prefix's key is not always a prefix of
    the key, e.g. inside a formula), so the result must key back to the
    reading; otherwise the reading's own characters nearest to that key
    position are taken (“3” in “x^{3}”, “定” in “销售定价”).
    """
    start = max(0, _key_offset(value, first + 1) - 1)
    end = min(len(value), max(start + 1, _key_offset(value, last)))
    if start < end and witness_key(value[start:end]) == reading:
        return start, end
    best = None
    for hit in _find_all(value, reading):
        drift = abs(len(witness_key(value[:hit])) - first)
        if drift <= 3 and (best is None or drift < best[0]):
            best = (drift, hit)
    return (best[1], best[1] + len(reading)) if best else None


def spot_range(text: str, spot: dict, *, key_at: int | None = None) -> tuple[int, int] | None:
    """Where the reading's side of an objection spot is in ``text`` (raw, with LaTeX).

    Returns (start, end) of the characters to mark, or None when the spot is
    not in this text (another field, or the text changed since).  When the
    same context occurs more than once, the one nearest ``key_at`` (the
    reading's position in the key) is taken.
    """
    value = str(text or "")
    before, reading, after = str(spot.get("before") or ""), str(spot.get("reading") or ""), str(spot.get("after") or "")
    if not value or not reading:
        return None
    hits = _find_all(witness_key(value), before + reading + after)
    if not hits:
        return None
    if key_at is not None and len(hits) > 1:
        hits.sort(key=lambda hit: abs(hit + len(before) - key_at))
    first = hits[0] + len(before)
    return _raw_span(value, first, first + len(reading), reading)


def spot_place(stem: str, options: dict, spot: dict) -> tuple[str, int, int] | None:
    """The field (“stem”, “A”…) and raw range of a spot's reading in a card's text.

    The spots were found in the stem and options read as one text (“…（ ）
    A.3B.2…”), so a spot at the start of an option has the label and the
    stem's end as its context: it is looked up in that same text and mapped
    back.  A stem with a table is compared without it, so its prose is then
    searched on its own.
    """
    from .tables import without_tables

    stem = str(stem or "")
    options = options or {}
    combined, spans = "", []

    def add(field: str, label: str, value: str) -> None:
        nonlocal combined
        combined += label
        spans.append((field, len(combined), len(combined) + len(value)))
        combined += value

    has_table = without_tables(stem) != stem
    if not has_table:
        add("stem", "", stem)
    for key in sorted(options):
        value = str(options[key] or "")
        if value.strip():
            add(key, f"{key}.", value)
    at = spot.get("at") if isinstance(spot.get("at"), int) and not has_table else None
    found = spot_range(combined, spot, key_at=at)
    if found:
        for field, low, high in spans:
            if low <= found[0] and found[1] <= high:
                return field, found[0] - low, found[1] - low
    if has_table:
        found = spot_range(stem, spot)
        if found:
            return "stem", found[0], found[1]
    return None


def spot_block(spot: dict, blocks: list[dict]) -> dict | None:
    """The MinerU box whose text holds MinerU's side of a spot: where to look on the paper."""
    before, seen, after = str(spot.get("before") or ""), str(spot.get("mineru") or ""), str(spot.get("after") or "")
    keyed = [
        (witness_key(str(block.get("text") or "")), block)
        for block in blocks or []
        if isinstance(block.get("bbox"), list) and len(block["bbox"]) == 4
    ]
    for needle in (before + seen + after, before + seen, seen + after):
        if len(needle) < 3:
            continue
        for key, block in keyed:
            if needle in key:
                return {"page_idx": int(block["page_idx"]), "bbox": _spot_line(block, needle)}
    return None


def _spot_line(block: dict, needle: str) -> list[float]:
    """The block's box, narrowed to the line holding the spot when MinerU kept line breaks.

    (“2. 设全集…为（C.）\nA. {0,1,3,4} …”: the spot is in the first of two lines.)
    """
    x0, y0, x1, y1 = (float(value) for value in block["bbox"])
    lines = [line for line in str(block.get("text") or "").split("\n") if line.strip()]
    hits = [index for index, line in enumerate(lines) if needle in witness_key(line)]
    if len(lines) > 1 and len(hits) == 1:
        step = (y1 - y0) / len(lines)
        y0, y1 = y0 + step * hits[0] - 2, y0 + step * (hits[0] + 1) + 2
    return [round(x0, 1), round(max(0.0, y0), 1), round(x1, 1), round(min(1000.0, y1), 1)]


def witness_choice(first: dict | None, second: dict | None, witness: str, *, context: int = 3) -> str | None:
    """Settle a disagreement between two vision readings with MinerU's text.

    Every place where the two readings differ is looked up in the witness
    together with a few characters of shared context.  Returns ``"a"`` or
    ``"b"`` only when the witness supports the same reading at every
    difference; any undecided or split difference returns ``None``.

    Real failures this catches: the vision model "correcting" the paper
    (inserting 的/是/于, turning FE into EF, 发出 into 出发) or misreading a
    repeating decimal, while MinerU copied the printed characters.  Extra
    handwriting in the witness does not matter: only the disputed spots are
    compared.
    """
    if not first or not second or not first.get("stem") or not second.get("stem"):
        return None
    ka = witness_key(reading_witness_text(first))
    kb = witness_key(reading_witness_text(second))
    kw = witness_key(witness)
    if not kw or ka == kb:
        return None
    # Neighbouring edits a character or two apart are one difference (a swap
    # such as EF/FE shows up as an insert plus a delete).
    regions: list[list[int]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if regions and i1 - regions[-1][1] <= 2 and j1 - regions[-1][3] <= 2:
            regions[-1][1], regions[-1][3] = i2, j2
        else:
            regions.append([i1, i2, j1, j2])
    votes: set[str] = set()
    for i1, i2, j1, j2 in regions:
        left, right = ka[max(0, i1 - context) : i1], ka[i2 : i2 + context]
        # Both sides need anchoring text.  At the very start or end a missing
        # passage “matches” trivially (a reading that dropped the opening
        # sentence would win), so such differences go to the arbiter.
        if len(left) < min(2, context) or len(right) < min(2, context):
            return None
        a_hit = (left + ka[i1:i2] + right) in kw
        b_hit = (left + kb[j1:j2] + right) in kw
        if a_hit == b_hit:
            return None
        votes.add("a" if a_hit else "b")
        if len(votes) > 1:
            return None
    return votes.pop() if votes else None


# ---------------------------------------------------------------------------
# 概念身份键
#
# 「中位线定理」和「中位线 定理」是同一个概念，「中位线定理」和「中位线性质」不是。
# 所以归一化只动三种东西：全半角、大小写、空白；再加上中文标点 —— 那是排版噪声。
#
# 数学符号一律不动：「a-b」不能等于「ab」，「f(x)」不能等于「fx」。
# 想靠去掉括号把「中位线定理（性质）」并进「中位线定理」，那是替用户做数学判断，不干。
# ---------------------------------------------------------------------------

_CONCEPT_NOISE = re.compile(r"[，,。、；;：:！!？?·…—–\"'“”‘’《》〈〉]")


def concept_key(value: str) -> str:
    """规范名的归一化形式。概念身份、搜索匹配、别名归并都用这一份。

    放在 textnorm 里是因为它就是一个归一化函数：迁移回填和运行时必须算出
    同一个结果，否则迁移后新旧两套对不上号。迁移文件里另写了一份等价实现
    （历史迁移不该依赖会变的应用代码），测试断言两边一致。
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"\s+", "", text)
    return _CONCEPT_NOISE.sub("", text)
