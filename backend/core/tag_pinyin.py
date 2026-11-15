"""标签名 → 拼音，供前端的「按拼音搜标签」用。

1.26.53（2026-11-15）：题库工作台的快速联想打标框一直写着「输入标签名或拼音」，
但全代码库从来没有任何一处写过 ``tag.pinyin`` —— 前端那句拼音匹配读到的永远是
空串。这块补上。

两条口径，跟 pypinyin 的默认行为对齐：

- 全拼：``勾股定理`` → ``gou gu ding li``，去掉声调、去掉分隔符后交给前端做
  子串匹配（``gougudingli`` 能搜到 ``勾股定理的逆定理``）。
- 首字母：``勾股定理`` → ``gdl``，老师习惯敲 ``gdl``。

pypinyin 是可选依赖：装不上时退化成「只按中文名和别名搜」，题库照常能用，
只是拼音搜不了。不能因为一个锦上添花的搜索方式就让整个标签系统打不开。
"""

from __future__ import annotations

import functools
import re

try:  # pragma: no cover - 取决于运行环境是否装了可选依赖
    from pypinyin import Style, lazy_pinyin

    _HAS_PINYIN = True
except Exception:  # pragma: no cover - 缺依赖时静默降级
    Style = None
    lazy_pinyin = None
    _HAS_PINYIN = False


_NON_PINYIN = re.compile(r"[^a-z0-9]+")


def _normalize(value: str) -> str:
    """把一段拼音压成 ``a-z0-9`` 的连续串，便于前端做子串匹配。"""
    return _NON_PINYIN.sub("", (value or "").lower())


@functools.lru_cache(maxsize=4096)
def _convert(name: str, style) -> str:
    if not _HAS_PINYIN or not name:
        return ""
    try:
        syllables = lazy_pinyin(name, style=style, errors="default")
    except Exception:  # pragma: no cover - 词库异常时不能连带打标功能一起崩
        return ""
    return "".join(part for part in syllables if part and part.isascii())


def full_pinyin(name: str) -> str:
    """``勾股定理`` → ``gougudingli``（无声调、无分隔符）。"""
    if not name:
        return ""
    if not _HAS_PINYIN:
        return ""
    return _normalize(_convert(name, Style.NORMAL))


def initials(name: str) -> str:
    """``勾股定理`` → ``gdl``（每个字的拼音首字母）。"""
    if not name:
        return ""
    if not _HAS_PINYIN:
        return ""
    return _normalize(_convert(name, Style.FIRST_LETTER))


def search_keys(name: str, aliases=()) -> list[str]:
    """给一个标签算出前端该拿来做匹配的全部字符串。

    包含标签名本身、别名的中文写法、全拼和首字母。前端只要判断
    「查询串是否命中其中任意一个」就能同时支持中文名 / 别名 / 全拼 / 首字母。
    """
    keys: list[str] = []
    seen: set[str] = set()

    def push(value: str) -> None:
        text = (value or "").strip()
        if text and text not in seen:
            seen.add(text)
            keys.append(text)

    push(name)
    push(full_pinyin(name))
    push(initials(name))
    for alias in aliases or ():
        push(alias)
        push(full_pinyin(alias))
        push(initials(alias))
    return keys
