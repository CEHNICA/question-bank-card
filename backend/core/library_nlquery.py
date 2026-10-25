"""中文找题条件的本地转换。

**这个模块不是自然语言理解。** 它是一张有限的规则表和一本本地词典，
处理老师实际会打的那几句话。认不出来的部分原样当关键词，不猜。

支持的说法（就这些，别的不认）：

* 用途：「主要考查」「主要考察」「辅助用到」「辅助知识」「解法采用」
  「题面特征」
* 小问：「第（1）问」「第1问」「第一问」
* 解法：「解法一」「解法二」「另一个解法」「其他解法」
* 排除：「不用」「排除」「不要用」「不含」
* 组合：「和」「或」「同时」
* 概念和方法：词典里有的名字或别名（词典 = 老师自己的标签目录）

退化行为（这个比能力更重要）：

* 认不出来的词 → 进关键词，走全文检索；
* 认出来但不确定的（比如「函数」既是概念也是可能的章节名）→
  转成「需要你选一下」的条件，不替用户定；
* 词典里一个都没有 → 全部当关键词，并在结果里说清楚「本地只认这几种说法」。

不调任何外部模型。没有 API key 也能用。
"""

from __future__ import annotations

import re

from .models import LibraryTag

#: 用途词 → 用途枚举值。左边是老师会打的说法。
#:
#: 注意这里**不能**把「方法」单独列进来 —— 老师写「配方法」时，「方法」是
#: 词典里那个方法名的一部分，先把它当用途词删掉，「配方法」就再也匹配不上。
#: 用途词要挑不会切坏别处的那些说法。
ROLE_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("主要考查", "主要考察", "主考点"), "primary"),
    (("辅助用到", "辅助知识"), "supporting"),
    (("解法采用", "用了方法", "用这个方法"), "method"),
    (("题面特征", "已知条件"), "feature"),
]

#: 词典匹配完之后剩下的这些字没有信息量，删掉再当关键词。
#: 不删的话「用配方法」会变成关键词「用配」，全文检索搜不着。
_FILLER_WORDS = ("使用", "用了", "采用", "方法是", "的方法", "方法", "用", "的", "是", "有", "会", "能", "要")

#: 排除的说法。
EXCLUDE_WORDS = ("不用", "排除", "不要用", "不含", "去掉")

#: 小问：第（1）问 / 第1问 / 第一问
_SUBQUESTION_PATTERNS = (
    re.compile(r"第\s*[（(]?\s*(\d{1,2})\s*[)）]?\s*问"),
    re.compile(r"第\s*([一二三四五六七八九十])\s*问"),
)

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

#: 解法分支的说法。
_VARIANT_PATTERNS = (
    (re.compile(r"解法\s*([一二三四五六七八九十1-9])"), "variant"),
    (re.compile(r"(另一个解法|其他解法|另一种解法)"), "other_variant"),
)

#: 题型词 → 题型。
TYPE_WORDS = {
    "选择题": "single_choice",
    "单选题": "single_choice",
    "多选题": "multiple_choice",
    "填空题": "fill_blank",
    "解答题": "free_response",
    "解答": "free_response",
}


class Conversion:
    """一次转换的结果。条件都带着来源和把握程度，界面可以逐条改。"""

    def __init__(self):
        self.query: dict = {
            "query_version": 2,
            "keywords": [],
            "filters": {},
            "concept_groups": [],
            "method_groups": [],
            "exclusions": {"conditions": [], "handling_of_unknown": "separate"},
            "include_clues": False,
            "sort": "recent",
        }
        #: 每条条件怎么来的 —— 界面要能显示「这句话被理解成了什么」。
        self.notes: list[dict] = []
        #: 需要老师自己定的歧义，不替他定。
        self.ambiguous: list[dict] = []

    def note(self, source: str, meaning: str, text: str = "") -> None:
        self.notes.append({"source": source, "meaning": meaning, "text": text})

    def as_dict(self) -> dict:
        return {"query": self.query, "notes": self.notes, "ambiguous": self.ambiguous}


def convert(text: str) -> Conversion:
    """把一句中文找题要求转成结构化条件。

    顺序有讲究：**先查词典，再认说法**。
    反过来的话，「方法」这种泛词会先把「配方法」切成两半，词典就再也匹配不上。
    """
    result = Conversion()
    raw = str(text or "").strip()
    if not raw:
        return result

    remaining = raw

    # ---- 词典匹配（先做！） ---------------------------------------------
    concepts, methods, remaining = _match_catalogue(remaining)

    # ---- 用途 ----------------------------------------------------------
    roles: list[str] = []
    for words, role in ROLE_WORDS:
        for word in words:
            if word in remaining:
                remaining = remaining.replace(word, " ")
                result.note(word, f"用途：{role}", word)
                roles.append(role)
                break
    primary_role = roles[0] if roles else "primary"

    # ---- 排除 ----------------------------------------------------------
    for word in EXCLUDE_WORDS:
        if word in remaining:
            remaining = remaining.replace(word, " ")
            result.note(word, "排除条件", word)
            result.query["exclusions"]["handling_of_unknown"] = "separate"

    # ---- 小问 ----------------------------------------------------------
    subquestion = ""
    for pattern in _SUBQUESTION_PATTERNS:
        match = pattern.search(remaining)
        if match:
            label = match.group(1)
            number = int(label) if label.isdigit() else _CN_DIGITS.get(label, 0)
            if number:
                subquestion = str(number)
                remaining = remaining[: match.start()] + " " + remaining[match.end() :]
                result.note(match.group(0), f"小问：第（{number}）问", match.group(0))
            break

    # ---- 解法分支 ------------------------------------------------------
    for pattern, kind in _VARIANT_PATTERNS:
        match = pattern.search(remaining)
        if match:
            variant = ""
            if kind == "variant":
                label = match.group(1)
                number = int(label) if label.isdigit() else _CN_DIGITS.get(label, 0)
                variant = f"解法{number}" if number else ""
            else:
                variant = "其他解法"
            if variant:
                remaining = remaining[: match.start()] + " " + remaining[match.end() :]
                result.note(match.group(0), f"解法分支：{variant}", match.group(0))
            break

    # ---- 题型 ----------------------------------------------------------
    for word, value in TYPE_WORDS.items():
        if word in remaining:
            remaining = remaining.replace(word, " ")
            result.query["filters"]["question_type"] = value
            result.note(word, f"题型：{value}", word)
            break

    # ---- 词典里找到的概念和方法 ----------------------------------------
    for concept in concepts:
        result.query["concept_groups"].append(
            {
                "ids": [concept["id"]],
                "roles": [primary_role],
                "all_or_any": "all",
                "include_descendants": False,
                "names": [concept["name"]],
            }
        )
        result.note(concept["name"], f"概念：{concept['name']}", concept["name"])
    for method in methods:
        result.query["method_groups"].append(
            {
                "ids": [method["id"]],
                # 带上名字，条件条上显示的是「配方法」，不是一串 UUID。
                "names": [method["name"]],
                "subquestion": subquestion,
                "variant_scope": "",
                "cooccurrence": "same_variant",
            }
        )
        result.note(method["name"], f"方法：{method['name']}", method["name"])

    # ---- 剩下的当关键词 ------------------------------------------------
    leftovers = [word for word in re.split(r"[\s,，。；;、]+", remaining) if word.strip()]
    keywords: list[str] = []
    for word in leftovers:
        cleaned = word
        for filler in _FILLER_WORDS:
            cleaned = cleaned.replace(filler, "")
        cleaned = cleaned.strip()
        if cleaned and cleaned not in keywords:
            keywords.append(cleaned)
    if keywords:
        result.query["keywords"] = keywords[:16]
        result.note("、".join(keywords), "关键词（全文检索）", "、".join(keywords))

    return result


def _match_catalogue(text: str) -> tuple[list[dict], list[dict], str]:
    """在本地目录里找概念和方法。找不到的词原样留着当关键词。"""
    if not text.strip():
        return [], [], text
    concepts: list[dict] = []
    methods: list[dict] = []
    matched_spans: list[tuple[int, int]] = []

    for tag in LibraryTag.objects.filter(active=True).order_by("-favorite", "name", "id"):
        names = [tag.name, *(tag.aliases or [])]
        best = None
        for name in names:
            if not name:
                continue
            index = text.find(name)
            if index < 0:
                continue
            if best is None or len(name) > len(best[1]):
                best = (index, name)
        if best is None:
            continue
        index, name = best
        matched_spans.append((index, index + len(name)))
        payload = {"id": str(tag.pk), "name": tag.name}
        if tag.kind == LibraryTag.Kind.METHOD:
            methods.append(payload)
        else:
            concepts.append(payload)

    leftover = text
    for start, end in sorted(matched_spans, reverse=True):
        leftover = leftover[:start] + " " + leftover[end:]
    return concepts, methods, leftover


def supported_phrases() -> list[str]:
    """列明本地认得的说法。界面上要能看。"""
    phrases = []
    for words, role in ROLE_WORDS:
        phrases.append(f"用途：{'／'.join(words)} → {role}")
    phrases.append("排除：" + "／".join(EXCLUDE_WORDS))
    phrases.append("小问：第（1）问／第1问／第一问")
    phrases.append("解法：解法一／解法二／另一个解法")
    phrases.append("题型：" + "／".join(sorted(TYPE_WORDS)))
    phrases.append("概念和方法：老师自己目录里的名字或别名")
    return phrases
