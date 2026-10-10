"""原卷印在题后印着的答案。

题有据的誊录提示词只让模型读题面（题号、题型、题干、选项、配图），
【内容类型】到【配图】里没有答案这一栏。于是凡是原卷自己印了
【答案】的卷子——比如「202510胜利初四数学答案版本.pdf」25 道题每道
都印着——MinerU 早就把它读成了文字块，卡上却是空的，界面还照实写着
「原卷未提供答案与解析。」

这里从已经读出来的文字块里把那条答案取出来。只在卡片自己的范围内
找（调用方给的是按 segment.center_in_regions 过滤过的块），所以一块
答案文字只可能属于一道题，不会被两道卡同时认领。
"""
import re

# 【答案】/【参考答案】/［答案］/(答案)：，中英文括号都收。
MARK = re.compile(r"[【\[（(]\s*(?:参考|标准)?答案\s*[】\]）)]\s*[:：]?\s*")
# 紧接着的下一个标记，用来把「【答案】A【分析】…」切干净。
NEXT = re.compile(r"[【\[（(]\s*(?:参考|标准)?(?:答案|解析|解答|详解|分析|点评)\s*[】\]）)]")
LETTER = re.compile(r"^([A-Ea-e])$")
MAX_VALUE = 300


def marks(blocks: list[dict]) -> list[str]:
    """这块范围里印着的答案原文，按块顺序返回。

    只认带书名号/括号的标记形式：题干里出现「答案是 B」是这道题自己在
    问，不是原卷给的答案。返回空列表表示这张卡范围内没有印答案。
    """
    found: list[str] = []
    for block in blocks:
        text = str((block or {}).get("text") or "")
        if not text:
            continue
        cursor = 0
        while True:
            hit = MARK.search(text, cursor)
            if hit is None:
                break
            following = NEXT.search(text, hit.end())
            stop = following.start() if following else len(text)
            cursor = hit.end()
            value = text[hit.end():stop].strip(" 　:：;；.。,，、")
            if value:
                found.append(value[:MAX_VALUE])
    return found


def choice_letter(values: list[str], kind: str, choice_types) -> str | None:
    """这组印着的答案里，有没有一个能原样落进选择题「答案」栏的字母。

    只认整条就是一个字母的情况。填空题和解答题的答案是一段话，MinerU
    自己就读错过（那份卷子第 11 题的答案被读成
    「$5\\sqrt{34}/34 = 1\\times\\fra…」），照抄等于把乱码印到答案卷上，
    所以那里一律不填，只提示人自己去抄。
    """
    if len(values) != 1 or kind not in choice_types:
        return None
    hit = LETTER.match(values[0].strip())
    return hit.group(1).upper() if hit else None
