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


def canon(value: str) -> str:
    # NFKC alone turns cm² into cm2 while LaTeX remains cm^2.  Preserve the
    # exponent marker before normalization so typographic and LaTeX forms
    # compare as the same reading without conflating x² with x2.
    text = unicodedata.normalize("NFKC", str(value or "").translate(SUPERSCRIPTS))
    text = SCORE.sub("", text)
    text = BLANK.sub("_", text)
    text = SPACING.sub("", text)
    text = COMMAND.sub(lambda m: SYMBOLS.get(m.group(1), "\\" + m.group(1)), text)
    text = "".join(PUNCT.get(ch, ch) for ch in text)
    text = text.replace("//", "∥").replace("^\\circ", "°").replace("^°", "°")
    text = re.sub(r"[\s$\\{}]", "", text)
    # Spacing and braces hid these pairs from the replacements above
    # (“$B C / / A D$”, “60^{\\circ}”); apply them once more on the bare text.
    text = text.replace("//", "∥").replace("^°", "°")
    text = re.sub(r"\(\)|\[\]", "()", text)
    return text.rstrip(".,;:")


def same_reading(first: dict, second: dict) -> bool:
    if canon(first.get("stem", "")) != canon(second.get("stem", "")):
        return False
    keys = set(first.get("options") or {}) | set(second.get("options") or {})
    return all(canon((first.get("options") or {}).get(k, "")) == canon((second.get("options") or {}).get(k, ""))
               for k in keys)


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


def clean_stem(stem: str, number: int | None = None) -> str:
    """去掉模型偶尔带上的题号和分值。"""
    text = str(stem or "").strip()
    match = NUMBER_PREFIX.match(text)
    if match and (number is None or int(match.group(1)) == number):
        text = text[match.end():]
    else:
        bare = BARE_NUMBER_PREFIX.match(text)
        if bare and number is not None and int(bare.group(1)) == number:
            text = text[bare.end():]
    text = re.sub(r"^\s*[(（]\s*(?:本题)?(?:满分)?\s*(?:共)?\s*\d{1,2}\s*分\s*[)）]\s*", "", text)
    # A reader that dropped “本题满分10分” sometimes leaves its opening bracket:
    # “（（1）如图1…”.
    text = re.sub(r"^\s*[(（]\s*(?=[(（]\s*\d{1,2}\s*[)）])", "", text)
    # “2..如图” on the paper: the reader drops the number but keeps a dot.
    text = re.sub(r"^\s*[.．、]+\s*(?=[\u4e00-\u9fff（(])", "", text)
    return text.strip()


def clean_option(value: str, key: str) -> str:
    text = str(value or "").strip()
    text = re.sub(rf"^\s*{key}\s*[.．、:：]\s*", "", text)
    return text.strip()


# ---------------------------------------------------------------- 跨引擎旁证
#
# MinerU already returns its own OCR text for every block it found.  It is a
# different engine from the vision reader, so when the two agree on every
# character that carries meaning, the card has genuine cross-engine support
# and a second vision call adds nothing.  The comparison deliberately ignores
# punctuation, blanks and LaTeX layout commands, which the two engines format
# differently, but keeps every digit, letter, operator and Chinese character.

_INLINE_TAG = re.compile(r"</?(?:sub|sup|span|b|i|u|em|strong)\b[^>]*>", re.I)
_WITNESS_SCORE = re.compile(r"[(（]\s*(?:本题)?(?:满分)?\s*(?:共)?\s*\d{1,2}\s*分\s*[)）]")
_WITNESS_LEAD = re.compile(r"^\s*(?:\d{1,3}\s*[.．、]+|\d{1,3}\s+(?=[\u4e00-\u9fff]))\s*")
_WITNESS_LAYOUT = re.compile(
    r"begin(?:cases|array[lcr]*|aligned|matrix)|end(?:cases|array|aligned|matrix)|"
    r"overline|widehat|hat|bar|stackrel|scriptscriptstyle|scriptstyle|displaystyle|underline|~"
)
_WITNESS_PUNCT = re.compile(r"[,.;:!?\"'_()\[\]、·…|&]")
WITNESS_MIN_LENGTH = 6


# A student's answer letter written into the printed answer brackets
# (“是（C）个”).  The letter must not follow a Latin letter, so P(A) stays.
_WITNESS_ANSWER = re.compile(r"(?<![A-Za-z])[(（]\s*[A-D]{1,4}\s*[)）]")


def witness_key(value: str) -> str:
    text = _INLINE_TAG.sub("", str(value or ""))
    text = _WITNESS_ANSWER.sub("（ ）", text)
    text = _WITNESS_LEAD.sub("", text, count=1)
    text = _WITNESS_SCORE.sub("", text)
    text = canon(text)
    text = _WITNESS_LAYOUT.sub("", text)
    text = re.sub(r"(?<=\d)\.(?=\d)", "٫", text)   # keep decimal points
    return _WITNESS_PUNCT.sub("", text)


def reading_witness_text(reading: dict) -> str:
    options = reading.get("options") or {}
    return str(reading.get("stem") or "") + "".join(
        f"{key}.{options[key]}" for key in sorted(options) if str(options[key]).strip()
    )


def witness_agrees(reading: dict | None, witness: str) -> bool:
    """Whether MinerU's own text independently supports a vision reading."""
    if not reading or not reading.get("stem") or reading.get("unclear"):
        return False
    expected = witness_key(witness)
    if len(expected) < WITNESS_MIN_LENGTH:
        return False
    return witness_key(reading_witness_text(reading)) == expected


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
