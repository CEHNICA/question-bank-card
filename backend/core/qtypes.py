"""题型：唯一的一份题型表、题型词表，以及“题型未定”的提醒和兜底判断。

题型以前在三处各写一份（读题、切题、网页），词表也不一样：读题只认
“单选/多选/选择/填空/解答”，切题还认“计算/证明/简答/应用”。模型写了
“计算题”，读题这边就落成“未定”，题卡照样是绿卡，一键通过后带着
“题型待核对”进了题库，而题库页又改不了题型。

这里集中三件事：
- 一份题型表（含 1.10 新增的判断题）和一份共用的词表；
- 兜底判断：只在证据很明确时补上题型（有 A–D 选项是选择题；没有选项、
  从 (1)(2) 起分小问的是解答题），其余仍是“未定”，交给人选；
- “题型未定”的提醒：题卡标黄，并且不能通过、不能入库，直到选好题型。
"""

from __future__ import annotations

import re

TYPE_LABELS = {
    "single_choice": "单选题",
    "multiple_choice": "多选题",
    "fill_blank": "填空题",
    "true_false": "判断题",
    "free_response": "解答题",
    "unknown": "题型未定",
}
TYPES = frozenset(TYPE_LABELS)
DECIDED_TYPES = frozenset(TYPES - {"unknown"})
CHOICE_TYPES = frozenset({"single_choice", "multiple_choice"})

# 词 → 题型。顺序有意义：“多项选择”要先于“选择”，“不定项”是多选。
# 读题模型写的【题型】、试卷上的大题标题（“三、解答题”）都用这一份。
TYPE_WORDS: tuple[tuple[str, str], ...] = (
    ("多选", "multiple_choice"), ("多项选择", "multiple_choice"), ("不定项", "multiple_choice"),
    ("单选", "single_choice"), ("单项选择", "single_choice"), ("选择", "single_choice"),
    ("填空", "fill_blank"),
    ("判断", "true_false"),
    ("解答", "free_response"), ("计算", "free_response"), ("证明", "free_response"),
    ("应用", "free_response"), ("简答", "free_response"), ("作图", "free_response"),
)

FLAG_TYPE_UNKNOWN = "题型没读出来，请在题卡上选一下题型（单选、多选、填空、判断或解答）"
BLOCK_MESSAGE = "题型还没定，请先在题卡上选题型"

# 小问标号只认行首的 (1)(2)…，“f(1)”“$x(2)$”不算。
_SUBQUESTION_LINE = re.compile(r"(?:^|\n)[ \t　]*[（(]\s*(\d{1,2})\s*[)）]")
_MULTIPLE_CUE = re.compile(r"多选|多项选择|不定项|有多项符合|多个选项")


# What a section heading says about every question in it.  “选择题” alone says
# nothing about one answer or several; the instruction under it does.
_SECTION_SINGLE = re.compile(r"只有一[项个]|单选题|单项选择")
# Not a bare “多选”: single-choice rules say “多选、错选、不选均不得分”.
_SECTION_MULTIPLE = re.compile(r"有多项符合|多选题|多项选择|不定项|部分选对|有多个选项")
# “第 1~8 题只有一项…，第 9~11 题有多项…”: the rule depends on the number, and
# a heading cut short (headings are kept to 80 characters) may show one half.
_SECTION_NUMBER_RANGE = re.compile(r"第\s*\$?\s*\d|\d+\s*\$?\s*(?:[~～—–-]|\\sim|至|到)\s*\$?\s*\d+\s*\$?\s*题")


def section_kind(text: str | None) -> str:
    """single_choice / multiple_choice when a heading fixes it unambiguously, else unknown.

    “二、选择题：…在每小题给出的选项中，有多项符合题目要求…” is multiple
    choice although its name is only “选择题”.  A heading that mentions both,
    or ties the rule to question numbers, fixes nothing.
    """
    value = str(text or "")
    if _SECTION_NUMBER_RANGE.search(value):
        return "unknown"
    single, multiple = bool(_SECTION_SINGLE.search(value)), bool(_SECTION_MULTIPLE.search(value))
    if multiple and not single:
        return "multiple_choice"
    if single and not multiple:
        return "single_choice"
    return "unknown"


def with_section(kind: str | None, section: str | None) -> str:
    """A choice question's type as its section heading states it.

    Readers judge single or multiple from one question and often say 单选 for
    every choice question; the printed instruction of the section is the
    better evidence.  Only choice types move; a section never turns a fill-in
    or free-response reading into a choice question.
    """
    current = str(kind or "unknown")
    fixed = section_kind(section)
    if fixed != "unknown" and current in CHOICE_TYPES:
        return fixed
    return current


def label(kind: str | None) -> str:
    return TYPE_LABELS.get(str(kind or "unknown"), str(kind or ""))


def from_words(text: str | None) -> str:
    """The type a printed or written name means (“解答题”“计算”“（多选）”), or unknown."""
    value = str(text or "")
    for word, kind in TYPE_WORDS:
        if word in value:
            return kind
    return "unknown"


def decided(kind: str | None) -> bool:
    return str(kind or "") in DECIDED_TYPES


def consensus(kinds) -> str:
    """The type every reading that named one agrees on; unknown when none or they differ.

    An arbiter's output has no 【题型】: when its text wins, the readers'
    shared type is still known and must not be thrown away.
    """
    named = {str(kind) for kind in kinds if decided(kind)}
    return named.pop() if len(named) == 1 else "unknown"


def subquestion_labels(stem: str | None) -> list[int]:
    """Sub-question numbers printed at the start of a line, in order: [1, 2, 3]."""
    return [int(value) for value in _SUBQUESTION_LINE.findall(str(stem or ""))]


def subquestion_count(stem: str | None) -> int:
    """How many sub-questions (1)(2)… the stem has, counted only when they run 1, 2, 3…"""
    labels = subquestion_labels(stem)
    count = 0
    for value in labels:
        if value == count + 1:
            count = value
    return count if count >= 2 else 0


def infer(kind: str | None, stem: str | None, options: dict | None) -> str:
    """Fill in an undecided type only where the text leaves no doubt.

    * printed options A–D → a choice question (multiple when the stem says so);
    * no options and sub-questions that start at (1), (2) → free response.

    Anything else stays unknown and is flagged for a person.  A decided type
    is never changed here.
    """
    current = str(kind or "unknown")
    if decided(current):
        return current
    text = str(stem or "")
    if any(str(value or "").strip() for value in (options or {}).values()):
        return "multiple_choice" if _MULTIPLE_CUE.search(text) else "single_choice"
    if subquestion_count(text) >= 2:
        return "free_response"
    return "unknown"


def with_flag(flags, kind: str | None) -> list[str]:
    """Flags with the “题型没读出来” reminder present exactly when the type is undecided."""
    kept = [flag for flag in (flags or []) if flag != FLAG_TYPE_UNKNOWN]
    if not decided(kind):
        kept.append(FLAG_TYPE_UNKNOWN)
    return kept


def sync(flags, state: str, kind: str | None) -> tuple[list[str], str]:
    """Flags and state after adding/removing the type reminder on a readable card.

    Only a change of the reminder itself moves the state, so a card's other
    flags keep deciding its colour exactly as before.
    """
    current = list(flags or [])
    if state not in {"green", "yellow"}:
        return current, state
    updated = with_flag(current, kind)
    if updated == current:
        return current, state
    return updated, "yellow" if updated else "green"
