"""题面的格式整理：拆出题源、中文引号。只挪格式、不改字。

新读出来的题、人改字保存的题、老版本读过的题（启动时整理）都走这里，
所以三条路的结果一样。两项都可以在设置的“功能开关”里关掉。

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
    return text, kept, labelled


def tidy_value(value: str, *, switches: dict | None = None) -> str:
    switches = switches if switches is not None else features.load()
    return textnorm.chinese_quotes(value) if switches.get("chinese_quotes") else str(value or "")


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
