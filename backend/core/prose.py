"""题面的格式整理：拆出题源、中文引号。只挪格式、不改字。

新读出来的题、人改字保存的题、老版本读过的题（启动时整理）都走这里，
所以三条路的结果一样。两项都可以在设置的“功能开关”里关掉。
"""

from __future__ import annotations

from . import features, textnorm

# A printed “（多选）” right after the source note names the type, as it does
# at the very start of a stem (see textnorm.strip_type_label).
_LABEL_SETS_TYPE = {"unknown", "", "single_choice", "multiple_choice"}


def tidy_stem(stem: str, *, switches: dict | None = None) -> tuple[str, str, str | None]:
    """(stem, origin taken off the front or "", type named by a printed label or None)."""
    switches = switches if switches is not None else features.load()
    text = str(stem or "")
    origin, labelled = "", None
    if switches.get("origin_split"):
        rest, found = textnorm.split_origin(text)
        if found:
            text, origin = rest, found
            rest, labelled = textnorm.strip_type_label(text)
            if labelled:
                text = rest.lstrip()
    if switches.get("chinese_quotes"):
        text = textnorm.chinese_quotes(text)
    return text, origin, labelled


def tidy_value(value: str, *, switches: dict | None = None) -> str:
    switches = switches if switches is not None else features.load()
    return textnorm.chinese_quotes(value) if switches.get("chinese_quotes") else str(value or "")


def tidy_fields(fields: dict, *, origin: str = "", switches: dict | None = None) -> dict:
    """The tidied stem/options (and origin, type) for a card being saved.

    ``origin`` is the card's current source note: a person's entry is kept,
    a found note only fills an empty one.
    """
    switches = switches if switches is not None else features.load()
    stem, found, labelled = tidy_stem(fields.get("stem", ""), switches=switches)
    result: dict = {"stem": stem}
    if found and not str(origin or "").strip():
        result["origin"] = found
    if labelled and str(fields.get("question_type") or "unknown") in _LABEL_SETS_TYPE:
        result["question_type"] = labelled
    if isinstance(fields.get("options"), dict):
        result["options"] = {key: tidy_value(value, switches=switches) for key, value in fields["options"].items()}
    for key in ("answer", "analysis"):
        if isinstance(fields.get(key), str):
            result[key] = tidy_value(fields[key], switches=switches)
    return result


def clean_origin(value) -> str:
    """A source note as typed by a person: one line, at most 120 characters."""
    text = " ".join(str(value or "").split())
    for opening, closing in (("[", "]"), ("【", "】"), ("（", "）"), ("(", ")")):
        if text.startswith(opening) and text.endswith(closing):
            text = text[1:-1].strip()
            break
    return text[:120]

