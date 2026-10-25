"""标签名的拼音首字母 —— 标签管理窗口按 A/B/C… 分组用。

用 pypinyin 算，不自己造表：汉字拼音首字母有几千条，自己维护一张表迟早漏字，
漏掉的字会被塞进「#」组里，看着像归类错了。

算不出来的时候（符号、纯数字、没见过的怪字）给 ``#``，
界面上把这一组排在最后 —— 不假装它属于哪个字母。
"""

from __future__ import annotations

from pypinyin import Style, lazy_pinyin

FALLBACK = "#"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _first_meaningful(text: str) -> str:
    """第一个能当名字起头的字符。

    标签名常带前缀符号（括号、星号、书名号），那些不算名字的一部分。
    """
    for character in text:
        if character.isspace():
            continue
        if character.isalnum() or "一" <= character <= "鿿":
            return character
    return ""


def initial_for(name: str) -> str:
    """名字的拼音首字母（大写）。

    * 「爱情」→ A，「白噪音」→ B，「抖音」→ D，「douyin」→ D
    * 认不出来的给 ``#``，不猜。
    """
    text = str(name or "").strip()
    if not text:
        return FALLBACK
    first = _first_meaningful(text)
    if not first:
        return FALLBACK
    if first.isascii():
        # 英文和数字：字母取自己的首字母，数字没有首字母。
        return first.upper() if first.isalpha() else FALLBACK
    try:
        letters = lazy_pinyin(first, style=Style.FIRST_LETTER, errors="default")
    except Exception:  # pragma: no cover - 词库坏了也不该让界面挂掉
        return FALLBACK
    for letter in letters:
        letter = str(letter).strip().upper()
        if letter and letter[0] in LETTERS:
            return letter[0]
    return FALLBACK
