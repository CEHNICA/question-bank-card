"""原卷印在题后或卷末章节里的答案与解析拆解。

保留 MinerU 路径既有的 `marks()` / `choice_letter()` 契约不变，同时为 Word 直读
提供行内与卷末「参考答案与解析」章节的结构化剥离能力（含解析配图归位）。
"""

from __future__ import annotations

import re
from typing import Any

# 【答案】/【参考答案】/［答案］/(答案)：，中英文括号都收。
MARK = re.compile(r"[【\[（(]\s*(?:参考|标准)?答案\s*[】\]）)]\s*[:：]?\s*")
# 紧接着的下一个标记，用来把「【答案】A【分析】…」切干净。
NEXT = re.compile(r"[【\[（(]\s*(?:参考|标准)?(?:答案|解析|解答|详解|分析|点评|点睛|方法点拨|解题思路)\s*[】\]）)]")
LETTER = re.compile(r"^([A-Ha-h])$")
MAX_VALUE = 300

# Word 直读：独立卷末答案章节标题（如「参考答案」「参考答案与解析」「答案详解」）
ANSWER_CHAPTER_HEADING_RE = re.compile(
    r"^\s*(?:[-—―=＝*★☆·•]+\s*)?"
    r"(?:附录\s*[：:·]?\s*)?"
    r"(?:[【\[]\s*)?"
    r"(?:参考答案(?:与(?:试题)?解析|及(?:试题)?解析|与详解|及详解)?|"
    r"标准答案(?:与解析|及解析)?|答案(?:与|及)(?:试题)?解析|答案详解|试题答案(?:与解析)?|参考解析)"
    r"(?:\s*[】\]])?"
    r"(?:\s*[-—―=＝*★☆·•]+)?\s*[:：]?\s*$"
)

# 行内或段首的答案/解析标记
INLINE_TAG_RE = re.compile(
    r"(?P<tag>"
    r"[【\[（(]\s*(?P<bkind>(?:参考|标准)?答案|解析|详解|分析|解答|证明|点睛|点评|方法点拨|解题思路)\s*[】\]）)]\s*[:：]?|"
    r"(?:^|(?<=\n))[ \t　]*(?P<lkind>(?:参考|标准)?答案|解析|详解|解答|证明)[ \t　]*[:：]"
    r")",
    re.M,
)

STANDALONE_WORKED_START_RE = re.compile(r"^\s*(?:解|证明|解答|答)\s*[:：]\s*")
SECTION_HEADING_RE = re.compile(
    r"^\s*(?:[一二三四五六七八九十]{1,3}\s*[、.．]|第[一二三四五六七八九十]+[部分大题]|[ⅠⅡⅢⅣ]+\s*[、.．])\s*(.+)$"
)
ANSWER_ITEM_START_RE = re.compile(r"^\s*[*※▲]?\s*(\d{1,3})\s*[.．、:：]\s*(.*)$", re.S)
COMPACT_RANGE_RE = re.compile(r"(\d{1,3})\s*[-~～—–至]\s*(\d{1,3})\s*[:：]?\s*([A-Ha-h][A-Ha-h\s,，、;；]*)")
COMPACT_PAIR_RE = re.compile(
    r"(?<![\dA-Za-z])(\d{1,3})\s*[.．、:：)）]\s*([A-Ha-h]{1,6}|[√×✓✗对错TFtf])(?=\s|[;；,，、]|$|\d)"
)


def marks(blocks: list[dict]) -> list[str]:
    """这块范围里印着的答案原文，按块顺序返回。"""
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
            value = text[hit.end() : stop].strip(" 　:：;；.。,，、")
            if value:
                found.append(value[:MAX_VALUE])
    return found


def choice_letter(values: list[str], kind: str, choice_types) -> str | None:
    """这组印着的答案里，有没有一个能原样落进选择题「答案」栏的字母。"""
    if len(values) != 1 or kind not in choice_types:
        return None
    hit = LETTER.match(values[0].strip())
    return hit.group(1).upper() if hit else None


def is_answer_chapter_heading(text: str) -> bool:
    """判断独立段落是否为卷末「参考答案 / 答案与解析」总标题。"""
    cleaned = str(text or "").strip()
    if not cleaned or len(cleaned) > 40 or "?" in cleaned or "？" in cleaned:
        return False
    if ANSWER_ITEM_START_RE.match(cleaned):
        return False
    return bool(ANSWER_CHAPTER_HEADING_RE.match(cleaned))


def _classify_tag(raw_kind: str) -> str:
    label = raw_kind.strip()
    return "answer" if label.endswith("答案") else "analysis"


def split_tagged_text(text: str) -> tuple[str, str, str, bool]:
    """从一段或多段文本中剥离【答案】【解析/分析/详解/点睛】。

    返回 ``(保留题面, answer, analysis, 是否命中答案或解析标记)``。
    """
    source = str(text or "")
    matches = list(INLINE_TAG_RE.finditer(source))
    if not matches:
        return source, "", "", False

    kept_prefix = source[: matches[0].start()].rstrip()
    answer_parts: list[str] = []
    analysis_parts: list[tuple[str, str]] = []
    for idx, hit in enumerate(matches):
        raw_kind = hit.group("bkind") or hit.group("lkind") or ""
        role = _classify_tag(raw_kind)
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(source)
        content = source[hit.end() : seg_end].strip()
        if not content:
            continue
        if role == "answer":
            answer_parts.append(content.strip(" 　。．"))
        else:
            analysis_parts.append((raw_kind.strip(), content))

    answer = "；".join(part for part in answer_parts if part)
    if len(analysis_parts) == 1 and analysis_parts[0][0] in {"解析", "详解", "解答"}:
        analysis = analysis_parts[0][1]
    else:
        rendered: list[str] = []
        for label, body in analysis_parts:
            if len(analysis_parts) > 1:
                rendered.append(f"【{label}】{body}")
            else:
                rendered.append(body)
        analysis = "\n".join(rendered)
    return kept_prefix, answer.strip(), analysis.strip(), True


def split_question_blocks(blocks: list[dict[str, Any]], *, allow_worked_start: bool = True) -> dict[str, Any]:
    """将单道题内部的段落/表格块拆分为学生题面块与随题答案解析（含解析图片分流）。"""
    question_blocks: list[dict[str, Any]] = []
    solution_tail_lines: list[str] = []
    solution_images: list[dict[str, Any]] = []
    in_solution = False
    inline_answer = ""
    inline_analysis = ""

    for idx, block in enumerate(blocks):
        text = str(block.get("text") or "")
        imgs = list(block.get("images") or [])
        if in_solution:
            if text.strip():
                solution_tail_lines.append(text.strip())
            solution_images.extend(imgs)
            continue

        hit = INLINE_TAG_RE.search(text)
        if hit is not None:
            before = text[: hit.start()].rstrip()
            after = text[hit.start() :]
            if before:
                question_blocks.append({**block, "text": before, "images": imgs if not after.strip() else []})
                if after.strip():
                    solution_images.extend(imgs)
            else:
                solution_images.extend(imgs)
            in_solution = True
            solution_tail_lines.append(after)
            continue

        if idx > 0 and allow_worked_start and not block.get("is_table") and STANDALONE_WORKED_START_RE.match(text):
            in_solution = True
            solution_tail_lines.append(text.strip())
            solution_images.extend(imgs)
            continue

        question_blocks.append(block)

    if solution_tail_lines:
        joined = "\n".join(solution_tail_lines)
        prefix, ans, ana, matched = split_tagged_text(joined)
        if matched:
            inline_answer = ans
            inline_analysis = ("\n".join(p for p in (prefix.strip(), ana) if p)).strip()
        else:
            inline_analysis = joined.strip()

    return {
        "question_blocks": question_blocks,
        "answer": inline_answer,
        "analysis": inline_analysis,
        "solution_images": solution_images,
    }


def _parse_single_answer_entry(body: str) -> tuple[str, str]:
    text = body.strip()
    if not text:
        return "", ""
    prefix, ans, ana, matched = split_tagged_text(text)
    if matched:
        if not ans and prefix.strip() and len(prefix.strip()) <= 80 and not STANDALONE_WORKED_START_RE.match(prefix):
            ans = prefix.strip(" 　。．；;")
        elif prefix.strip():
            ana = f"{prefix.strip()}\n{ana}".strip()
        return ans, ana
    if STANDALONE_WORKED_START_RE.match(text):
        return "", text
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) == 1 and len(lines[0]) <= 120:
        return lines[0].rstrip("。．"), ""
    if lines and re.fullmatch(r"[A-Ha-h]{1,6}|[√×✓✗对错TFtf]", lines[0]):
        return lines[0].upper() if lines[0].isascii() else lines[0], "\n".join(lines[1:]).strip()
    return "", text


def _parse_answer_table(rows: list[list[str]]) -> list[tuple[int, str]]:
    """识别形如「题号 1 2 3 / 答案 A B C」的双行或多组双行选择题答案表。"""
    pairs: list[tuple[int, str]] = []
    for r in range(len(rows) - 1):
        top, bot = rows[r], rows[r + 1]
        if len(top) < 2 or len(top) != len(bot):
            continue
        nums: list[tuple[int, str]] = []
        for c_top, c_bot in zip(top, bot):
            t_clean = c_top.strip()
            b_clean = c_bot.strip()
            if t_clean in {"题号", "序号", "题目", ""} and b_clean in {"答案", "选项", "参考答案", ""}:
                continue
            if re.fullmatch(r"\d{1,3}", t_clean) and re.fullmatch(r"[A-Ha-h]{1,6}|[√×✓✗对错]", b_clean):
                nums.append((int(t_clean), b_clean.upper() if b_clean.isascii() else b_clean))
        if len(nums) >= 2:
            pairs.extend(nums)
    return pairs


def parse_answer_chapter(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """解析卷末参考答案章节，按出现顺序返回 ``[{section_idx, section, number, answer, analysis, images}]``。"""
    entries: list[dict[str, Any]] = []
    section_idx = 0
    section_name = ""
    current: dict[str, Any] | None = None

    def flush_current() -> None:
        nonlocal current
        if current is None:
            return
        ans, ana = _parse_single_answer_entry("\n".join(current["lines"]))
        entries.append(
            {
                "section_idx": current["section_idx"],
                "section": current["section"],
                "number": current["number"],
                "answer": ans,
                "analysis": ana,
                "images": list(current["images"]),
            }
        )
        current = None

    for block in blocks:
        text = str(block.get("text") or "").strip()
        imgs = list(block.get("images") or [])
        table_rows = block.get("table_rows")
        if isinstance(table_rows, list):
            table_pairs = _parse_answer_table(table_rows)
            if table_pairs:
                flush_current()
                for num, val in table_pairs:
                    entries.append(
                        {
                            "section_idx": section_idx,
                            "section": section_name,
                            "number": num,
                            "answer": val,
                            "analysis": "",
                            "images": [],
                        }
                    )
                continue

        if not text and not imgs:
            continue
        if SECTION_HEADING_RE.match(text) and not ANSWER_ITEM_START_RE.match(text):
            flush_current()
            if entries or section_name:
                section_idx += 1
            section_name = text
            continue

        # 紧凑答案行，例如 "1-5 ACBDA" 或 "1. A  2. C  3. B  4. D"
        range_hits = list(COMPACT_RANGE_RE.finditer(text))
        if range_hits and not INLINE_TAG_RE.search(text):
            expanded: list[tuple[int, str]] = []
            for hit in range_hits:
                start_n, end_n = int(hit.group(1)), int(hit.group(2))
                letters = re.findall(r"[A-Ha-h]", hit.group(3))
                if end_n >= start_n and len(letters) == (end_n - start_n + 1):
                    for offset, ch in enumerate(letters):
                        expanded.append((start_n + offset, ch.upper()))
            if expanded:
                flush_current()
                for num, val in expanded:
                    entries.append(
                        {
                            "section_idx": section_idx,
                            "section": section_name,
                            "number": num,
                            "answer": val,
                            "analysis": "",
                            "images": [],
                        }
                    )
                continue

        pair_hits = list(COMPACT_PAIR_RE.finditer(text))
        if len(pair_hits) >= 2 and not INLINE_TAG_RE.search(text):
            consumed = sum(len(m.group(0)) for m in pair_hits)
            stripped_len = len(re.sub(r"[\s;；,，、]", "", text))
            if consumed >= stripped_len * 0.7:
                flush_current()
                for m in pair_hits:
                    val = m.group(2).upper() if m.group(2).isascii() else m.group(2)
                    entries.append(
                        {
                            "section_idx": section_idx,
                            "section": section_name,
                            "number": int(m.group(1)),
                            "answer": val,
                            "analysis": "",
                            "images": [],
                        }
                    )
                continue

        item_match = ANSWER_ITEM_START_RE.match(text)
        if item_match:
            flush_current()
            current = {
                "section_idx": section_idx,
                "section": section_name,
                "number": int(item_match.group(1)),
                "lines": [item_match.group(2).strip()] if item_match.group(2).strip() else [],
                "images": list(imgs),
            }
            continue

        if current is not None:
            if text:
                current["lines"].append(text)
            current["images"].extend(imgs)

    flush_current()
    return entries
