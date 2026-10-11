"""题面的格式整理：拆出题源、中文引号。只挪格式、不改字。

新读出来的题、人改字保存的题、老版本读过的题（启动时整理）都走这里，
所以三条路的结果一样。三项都可以在设置的“功能开关”里关掉。

题源只在“存得下”的时候才从题干里拿掉：题卡还没有题源，或者题干开头那段
就是已有的题源。否则（已有另一个题源）这段留在题干里，一个字也不丢；
因此反复整理结果不变。
"""

from __future__ import annotations

import re

from . import features, textnorm

# A printed “（多选）” right after the source note names the type, as it does
# at the very start of a stem (see textnorm.strip_type_label).
_LABEL_SETS_TYPE = {"unknown", "", "single_choice", "multiple_choice"}
_ORIGIN_KEY = re.compile(r"[\s·・.．,，、:：;；()（）\[\]【】]")

# MinerU sometimes writes a column banner as markdown bold, and a chapter
# number as an HTML superscript.  Both are markup that should have been
# rendered: they reach the card as literal characters, so the student paper
# shows “**做一做**” and “<sup>2</sup> 矩形的性质与判定”.  Nothing is lost by
# unwrapping them — the banner text itself is what the reader wrote.
_MARKUP = re.compile(r"</?(?:sup|sub)>|\*\*")

# The same paragraph also carries MinerU's picture placeholder as an HTML
# comment and its line break as a lone ``<br>``.  Both are layout instructions
# that were never rendered, so the student paper prints
# “<! — — Image(268, 493, 650, 637) —>图 3-7”.  The figure the comment points at
# is attached separately (or was honestly missed and flagged); the comment
# itself carries nothing the reader wrote.  Bounded so an unclosed ``<!--``
# cannot swallow the rest of the stem.
_COMMENT = re.compile(r"<!--.{0,200}?-->", re.S)
_BREAK = re.compile(r"<br\s*/?>", re.I)

# Half-width punctuation in Chinese prose: “优级品率.如果”, “结论:”, “为菱形;”.
# Only the punctuation that sits directly against a Chinese character counts,
# so decimals (0.5, 1.65), LaTeX (f(x,y), \sqrt2), option letters (A.) and
# ratios (1:2) are untouched by construction.  Formula spans are passed through
# verbatim — see ``_outside_math``.
_CJK = "\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
_HALF_TO_FULL = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！", ".": "。"}
_HALF_PUNCT = re.compile(
    f"(?<=[{_CJK}])[{''.join(_HALF_TO_FULL)}]|[{''.join(_HALF_TO_FULL)}](?=[{_CJK}])"
)
_MATH = re.compile(r"(?<!\\)(\${1,2})(.*?)(?<!\\)\1|\\\(.+?\\\)|\\\[.+?\\\]", re.S)
# A run of half-width stops after Chinese is one sentence, not several: the OCR
# often leaves “正数..”.  Collapsing keeps the paper from showing “正数。.”
# — a full stop cannot appear inside LaTeX, so this one is safe to do globally.
_DOUBLE_STOP = re.compile(r"。(?:\.|．)+")


def _swap(match: re.Match) -> str:
    return _HALF_TO_FULL[match.group(0)]


def _outside_math(text: str, transform) -> str:
    """Apply ``transform`` to everything that is not a formula span.

    Same shape as ``textnorm.fix_symbols``: the math goes through untouched so
    LaTeX keeps every character it needs.
    """
    result: list[str] = []
    start = 0
    for match in _MATH.finditer(text):
        result.append(transform(text[start:match.start()]))
        result.append(match.group(0))
        start = match.end()
    result.append(transform(text[start:]))
    return "".join(result)


def plain_markup(text: str) -> str:
    """Drop markup that was never rendered: ``**``, ``<sup>``/``<sub>``, HTML comments."""
    value = _MARKUP.sub("", str(text or ""))
    value = _COMMENT.sub("", value)
    return _BREAK.sub(" ", value)


def full_width_punctuation(text: str) -> str:
    """Half-width punctuation standing against Chinese becomes full-width."""
    widened = _outside_math(str(text or ""),
                            lambda part: _HALF_PUNCT.sub(_swap, part))
    return _DOUBLE_STOP.sub("。", widened)


def same_origin(first: str, second: str) -> bool:
    return _ORIGIN_KEY.sub("", str(first or "")) == _ORIGIN_KEY.sub("", str(second or ""))


def tidy_stem(stem: str, *, origin: str = "", switches: dict | None = None) -> tuple[str, str, str | None]:
    """(stem, origin to keep, type named by a printed label or None).

    ``origin`` is the card's current source note (a person's, or one found
    earlier).  A note at the front of the stem is taken off only when it can
    be kept: there is no origin yet, or it is that same origin.
    """
    switches = switches if switches is not None else features.load()
    text = str(stem or "")
    kept = str(origin or "").strip()
    labelled = None
    if switches.get("origin_split"):
        rest, found = textnorm.split_origin(text)
        if found and (not kept or same_origin(found, kept)):
            text, kept = rest, kept or found
            rest, labelled = textnorm.strip_type_label(text)
            if labelled:
                text = rest.lstrip()
    if switches.get("chinese_quotes"):
        text = textnorm.chinese_quotes(text)
    if switches.get("full_width_punctuation"):
        text = full_width_punctuation(text)
    return plain_markup(text), kept, labelled


def tidy_value(value: str, *, switches: dict | None = None) -> str:
    switches = switches if switches is not None else features.load()
    text = textnorm.chinese_quotes(value) if switches.get("chinese_quotes") else str(value or "")
    if switches.get("full_width_punctuation"):
        text = full_width_punctuation(text)
    return plain_markup(text)


def tidy_fields(fields: dict, *, origin: str = "", switches: dict | None = None) -> dict:
    """The tidied stem/options/answer/analysis, the origin to keep and maybe the type."""
    switches = switches if switches is not None else features.load()
    stem, kept, labelled = tidy_stem(fields.get("stem", ""), origin=origin, switches=switches)
    result: dict = {"stem": stem, "origin": kept}
    if labelled and str(fields.get("question_type") or "unknown") in _LABEL_SETS_TYPE:
        result["question_type"] = labelled
    if isinstance(fields.get("options"), dict):
        result["options"] = {key: tidy_value(value, switches=switches) for key, value in fields["options"].items()}
    for key in ("answer", "analysis"):
        if isinstance(fields.get(key), str):
            result[key] = tidy_value(fields[key], switches=switches)
    return result


def tidy_reading(reading, *, origin: str = "", switches: dict | None = None):
    """A reading record tidied like the card's text, so the review page compares like with like."""
    if not isinstance(reading, dict) or not isinstance(reading.get("stem"), str):
        return reading
    switches = switches if switches is not None else features.load()
    changed = dict(reading)
    changed["stem"] = tidy_stem(reading["stem"], origin=origin, switches=switches)[0]
    if isinstance(reading.get("options"), dict):
        changed["options"] = {key: tidy_value(value, switches=switches) if isinstance(value, str) else value
                              for key, value in reading["options"].items()}
    return changed


def clean_origin(value) -> str:
    """A source note as typed by a person: one line, at most 120 characters."""
    text = " ".join(str(value or "").split())
    for opening, closing in (("[", "]"), ("【", "】"), ("（", "）"), ("(", ")")):
        if text.startswith(opening) and text.endswith(closing):
            text = text[1:-1].strip()
            break
    return text[:120]
