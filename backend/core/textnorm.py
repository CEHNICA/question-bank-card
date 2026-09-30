"""题面文字的规范化：判断两次识读是否"说的是同一件事"。

只用于比较，不改动保存的文字。空格、全半角、$ 与花括号、\\dfrac/\\frac、
\\vec/\\overrightarrow、\\le/\\leqslant 这类写法差异都视为相同。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

SYMBOLS = {
    "angle": "∠", "parallel": "∥", "perp": "⊥", "bot": "⊥", "triangle": "△", "bigtriangleup": "△",
    "times": "×", "div": "÷", "cdot": "·", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "leqslant": "≤",
    "leqq": "≤", "ge": "≥", "geq": "≥", "geqslant": "≥", "geqq": "≥", "ne": "≠", "neq": "≠",
    "approx": "≈", "equiv": "≡", "cong": "≅", "sim": "∽", "backsim": "∽", "pi": "π", "infty": "∞",
    "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆", "subsetneqq": "⊊", "supset": "⊃",
    "supseteq": "⊇", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅", "forall": "∀",
    "exists": "∃", "neg": "¬", "because": "∵", "therefore": "∴", "circ": "°", "degree": "°",
    "prime": "'", "cdots": "…", "ldots": "…", "dots": "…", "mid": "|", "vert": "|", "lvert": "|",
    "rvert": "|", "rightarrow": "→", "to": "→", "Rightarrow": "⇒", "Leftrightarrow": "⇔",
    "square": "□", "Box": "□", "lbrace": "{", "rbrace": "}", "{": "{", "}": "}",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ",
    "varphi": "φ", "omega": "ω", "Delta": "Δ", "Omega": "Ω", "odot": "⊙", "boldsymbol": "",
    "mathbb": "", "mathrm": "", "mathbf": "", "mathit": "", "text": "", "textbf": "", "operatorname": "",
    "overrightarrow": "→", "vec": "→", "dfrac": "frac", "tfrac": "frac",
}
PUNCT = {"。": ".", "．": ".", "，": ",", "：": ":", "；": ";", "、": ",", "“": '"', "”": '"', "‘": "'",
         "’": "'", "（": "(", "）": ")", "【": "[", "】": "]", "－": "-", "−": "-", "–": "-", "—": "-",
         "～": "~", "∶": ":", "﹒": ".", "▱": "□", "丄": "⊥", "⩽": "≤", "⩾": "≥", "≦": "≤", "≧": "≥"}
SPACING = re.compile(r"\\(?:left|right|big|Big|bigg|Bigg|displaystyle|textstyle|limits|nolimits|quad|qquad)(?![A-Za-z])|\\[,;:! ]")
COMMAND = re.compile(r"\\([A-Za-z]+|[{}])")
SCORE = re.compile(r"[(（]\s*\d{1,2}\s*分\s*[)）]")
BLANK = re.compile(r"_{2,}|\\underline\{\s*(?:\\quad|\\qquad|~|\s)*\}|(?:\\_)+")
SUPERSCRIPTS = str.maketrans({
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
    "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
})


def canon(
    value: str, *, strip_trailing_punct: bool = True, collapse_empty_brackets: bool = True,
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
    return all(canon((first.get("options") or {}).get(k, "")) == canon((second.get("options") or {}).get(k, ""))
               for k in keys)


def _reading_key(reading: dict) -> str:
    options = reading.get("options") or {}
    return canon(reading.get("stem", "")) + "".join(
        f"\u241f{key}:{canon(options[key])}" for key in sorted(options)
    )


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
_TOKEN_BEFORE_VERTICES = re.compile(
    _PARALLELOGRAM_TOKEN + r"\s*(?:\{\s*\}\s*)?(?=(?:[A-Z]\s*){2})"
)
_ONLY_PARALLELOGRAM = re.compile(
    r"^\s*" + _PARALLELOGRAM_TOKEN + r"\s*(?:\{\s*\}\s*)?" + _VERTICES + r"\s*$"
)
_MATH_SPAN = re.compile(r"(?<!\\)(\${1,2})(.*?)(?<!\\)\1", re.S)
_TOKEN_BEFORE_MATH_VERTICES = re.compile(
    _PARALLELOGRAM_TOKEN + r"\s*(?=\$\s*(?:[A-Z]\s*){2,5}\$)"
)


def fix_symbols(value: str) -> str:
    """把误写的平行四边形方框改回 ▱；原始填空框及乘法占位符保持不变。"""
    text = str(value or "")
    # □$ABCD$ 这类符号和顶点被分别包裹的写法先处理。
    text = _TOKEN_BEFORE_MATH_VERTICES.sub("▱", text)
    result: list[str] = []
    start = 0
    for match in _MATH_SPAN.finditer(text):
        result.append(_TOKEN_BEFORE_VERTICES.sub("▱", text[start:match.start()]))
        delimiter, body = match.group(1), match.group(2)
        whole = _ONLY_PARALLELOGRAM.fullmatch(body)
        if whole:
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
            key: fix_symbols(option) if isinstance(option, str) else option
            for key, option in fixed["options"].items()
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


def _is_own_number(value: str, number: int | None) -> bool:
    """Whether a printed number is this card's, allowing a binding edge that
    clipped its leading digits (“9.” for 19, “0.” for 20)."""
    if number is None:
        return True
    return int(value) == number or (len(value) < len(str(number)) and str(number).endswith(value))


def clean_stem(stem: str, number: int | None = None) -> str:
    """去掉模型偶尔带上的题号和分值。"""
    text = str(stem or "").strip()
    # 截图顶部带进了上一段的尾巴（“合题目要求的。\n1．经过点……”）：
    # 从本题自己的题号那一行开始。
    if number is not None:
        own_line = re.search(rf"\n\s*{number}\s*[.．、]+(?!\d)\s*", text[:120])
        if own_line and "\n" in text[:own_line.start() + 1] and len(text[:own_line.start()].strip()) <= 60 \
                and not re.search(r"[(（]\s*\d\s*[)）]", text[:own_line.start()]):
            text = text[own_line.start():].strip()
    match = NUMBER_PREFIX.match(text)
    if match and _is_own_number(match.group(1), number) and not text[match.end():match.end() + 1].isdigit():
        text = text[match.end():]
    else:
        bare = BARE_NUMBER_PREFIX.match(text)
        if bare and number is not None and int(bare.group(1)) == number:
            text = text[bare.end():]
    text = re.sub(rf"^\s*{_SCORE_MARK}\s*", "", text)
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
_WITNESS_ANSWER = re.compile(r"(?<![A-Za-z])[(（]\s*[A-D]{1,4}\s*[)）]")


def _html_script(match: re.Match) -> str:
    tag, inner = match.group(1).lower(), match.group(2)
    before = match.string[:match.start()]
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
    text = _WITNESS_LEAD.sub("", text, count=1)
    text = _WITNESS_SCORE.sub("", text)
    text = _BAR.sub(r"\\overline", text)
    text = _STYLE.sub(" ", text)
    text = _CASES_OPEN.sub(r"\\begin{cases}", text)
    text = _CASES_CLOSE.sub(r"\\end{cases}", text)
    # Inside math, ~ is only a non-breaking space; in prose it is a mark.
    text = _MATH_SEGMENT.sub(lambda m: m.group(0).replace("~", " "), text)
    text = canon(text, strip_trailing_punct=False, collapse_empty_brackets=False,
                 preserve_parallelogram=True)
    text = re.sub(r"\.{3,}|⋯", "…", text)
    # ΔABC is the triangle; ≌ and \cong are both congruence; √3 and \sqrt{3}.
    text = re.sub(r"Δ(?=[A-Z]{3}(?![A-Za-z]))", "△", text)
    return text.replace("≌", "≅").replace("√", "sqrt")


def reading_witness_text(reading: dict) -> str:
    options = reading.get("options") or {}
    return str(reading.get("stem") or "") + "".join(
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
        piece == "." and before in "ABCD" and start >= 1
        and (start < 2 or not text[start - 2].isalnum() or bool(_CJK.match(text[start - 2])))
    )


# MinerU repeats or strands option letters with nothing after them: “B. 48/5
# B.C. 4” and, for picture options, a bare “A. B. C. D.” at the end.
_BARE_LABELS = re.compile(r"(?:[A-D]\.)+")


def _bare_option_labels(text: str, start: int, end: int) -> bool:
    if not _BARE_LABELS.fullmatch(text[start:end]):
        return False
    rest = text[end:]
    return not rest or bool(re.match(r"[A-D]\.", rest))


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
    if "▱" in expected and raw and (
        _TOKEN_BEFORE_VERTICES.search(raw) or _TOKEN_BEFORE_MATH_VERTICES.search(raw)
    ):
        return False
    # MinerU has no ▱: it prints the parallelogram sign as □.  When the reader
    # itself wrote ▱ (not a □ that parse_reading() turned into ▱), that □ in
    # front of the vertices supports it.
    if raw and "▱" in raw and "□" in expected and not (
        _TOKEN_BEFORE_VERTICES.search(raw) or _TOKEN_BEFORE_MATH_VERTICES.search(raw)
    ):
        expected = re.sub(r"□(?=[A-Z]{2,5})", "▱", expected)
    return _keys_agree(witness_key(reading_witness_text(reading)), expected)


# A spot where both vision readings agree but MinerU printed something else
# one-for-one (“x^3” / “x^2”, “销售单价” / “销售定价”).  The same model reading
# twice tends to repeat its own slip, so such a spot is worth a third, focused
# look.  Only clean substitutions count: short, made of letters, digits,
# Chinese or maths symbols, anchored by identical text on both sides —
# handwriting mixed into MinerU's text shows up as insertions, not these.
_OBJECTION_CHARS = re.compile(r"^[0-9A-Za-z\u4e00-\u9fffα-ωΑ-Ω△∠⊥∥≤≥≠±×÷°π∞]+$")
# MinerU's usual confusions: a disagreement between these proves nothing
# (in testing MinerU was wrong at every α/a and △/V spot).
_OCR_LOOKALIKES = (
    {"O", "0"}, {"o", "0"}, {"l", "1"}, {"I", "1"}, {"I", "l"},
    {"α", "a"}, {"β", "B"}, {"γ", "y"}, {"ρ", "p"}, {"ω", "w"}, {"ν", "v"}, {"τ", "t"}, {"χ", "x"},
    {"μ", "u"}, {"△", "V"}, {"∠", "L"},
    # Letters whose capital is the small letter drawn larger: MinerU's case
    # is no evidence (胜利初二 #23 “边长为 c” / C, 胜利初四 #23 “v” / V).
    *({letter, letter.upper()} for letter in "cosuvwxzpk"),
)


def witness_objections(reading: dict | None, witness: str, *, context: int = 3) -> list[dict]:
    if not reading or not reading.get("stem") or not witness:
        return []
    ka = witness_key(reading_witness_text(reading))
    kw = witness_key(witness)
    if len(kw) < WITNESS_MIN_LENGTH:
        return []
    spots = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, ka, kw, autojunk=False).get_opcodes():
        if tag != "replace":
            continue
        read, seen = ka[i1:i2], kw[j1:j2]
        if len(read) > 3 or len(seen) > 3 or {read, seen} in _OCR_LOOKALIKES:
            continue
        if not _OBJECTION_CHARS.match(read) or not _OBJECTION_CHARS.match(seen):
            continue
        left, right = ka[i1 - context:i1] if i1 >= context else "", ka[i2:i2 + context]
        if len(left) < context or len(right) < context:
            continue
        if kw[j1 - context:j1] != left or kw[j2:j2 + context] != right:
            continue
        spots.append({"reading": read, "mineru": seen, "before": left, "after": right})
    return spots


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
        left, right = ka[max(0, i1 - context):i1], ka[i2:i2 + context]
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
