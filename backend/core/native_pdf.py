"""Bounded, local PDF text-layer extraction. Coordinates come from the PDF.

No model, guessed formula, or guessed scan-page text enters this adapter.
The extracted text is a draft; original-page bodies remain the safe default.
"""

from __future__ import annotations

import math
import unicodedata
from pathlib import Path

import pymupdf as fitz

# 一页里读不出来的替换字符超过这个比例，才当成「这页正文不可信」。
# 零星几个不降级：文字层其余部分照常能用，把整页的题丢掉代价比留下几个空位大得多。
UNREADABLE_PAGE_RATIO = 0.05


def count_private(text: str) -> int:
    """字体私有区（Co）字符的个数。

    私有区是 PDF 字体厂商给数学符号的正常编码：分段函数的大括号、⌈ ⌉ ⌊ ⌋ 这些
    在原图上印出来是对的，只是 Unicode 里没有对应码位，复制不出来。把它当成乱码，
    整页会因为三个取整号降级去手工框题，那一页的题一道都不剩 —— 《凤城高一数学
    试题_打印版_v8》第 9-15 题就这样没了，而那一页 945 个字符里 942 个都正常。
    """
    return sum(unicodedata.category(ch) == "Co" for ch in text)


def mark_private(text: str) -> str:
    """把私有区字符换成一个看得见的占位符。

    卡上正文本来是原图，这个占位只出现在给人核对的草稿文本里；留一个方框让老师
    看得出「这儿少了个符号」，比一片空白好。
    """
    return "".join("\u25a1" if unicodedata.category(ch) == "Co" else ch for ch in text)


def unreadable_ratio(text: str) -> float:
    """真正读不出来的字符（替换字符 U+FFFD）占全页的比例。"""
    return text.count("\ufffd") / max(1, len(text))


def page_mode(text: str, *, full_page_image: bool = False) -> str:
    """这一页的文字层够不够格让切题器自己切题。

    只有整页读不出东西、或者读不出来的比例大到正文不可信时才降级去手工。零星几个
    替换字符不该让整页的题从题库里消失。
    """
    if not text.strip() or full_page_image:
        return "manual"
    return "native" if unreadable_ratio(text) <= UNREADABLE_PAGE_RATIO else "manual"


def classify_page_text(text: str, *, full_page_image: bool = False) -> list[str]:
    """给这一页的文字层该说的话。判定本身在 page_mode。"""
    messages = []
    private, corrupt = count_private(text), text.count("\ufffd")
    if not text.strip():
        messages.append("没有可复制文字，保留原页并手工框题。")
    elif corrupt:
        messages.append(f"文字层有 {corrupt} 个字符读不出来，正文应保留原图并对照原卷。")
    if private:
        messages.append(f"文字层有 {private} 个符号是字体私有编码（复制不出来，打印和阅读不受影响）。")
    if full_page_image:
        messages.append("本页以整页扫描图为主，文字层只作辅助，请手工框出完整题目。")
    messages.append("文字层不能保证分式、上下标和图表准确；没有自动补写公式。")
    return messages


def normalized_bbox(page, bbox) -> list[float] | None:
    rect = fitz.Rect(bbox)
    if any(not math.isfinite(value) for value in rect):
        return None
    rect = (rect * page.rotation_matrix) & page.rect
    bounds = page.rect
    if rect.is_empty or bounds.width <= 0 or bounds.height <= 0:
        return None
    return [
        round((rect.x0 - bounds.x0) / bounds.width * 1000, 2),
        round((rect.y0 - bounds.y0) / bounds.height * 1000, 2),
        round((rect.x1 - bounds.x0) / bounds.width * 1000, 2),
        round((rect.y1 - bounds.y0) / bounds.height * 1000, 2),
    ]


def extract(source: Path, selected_pages: list[int] | None = None) -> dict:
    blocks, pages = [], []
    with fitz.open(source) as pdf:
        selected = selected_pages if selected_pages is not None else list(range(len(pdf)))
        if any(type(index) is not int or not 0 <= index < len(pdf) for index in selected):
            raise ValueError("页面不存在")
        for index in selected:
            page = pdf[index]
            warnings, text_lines = [], []
            try:
                full_page_image = False
                # Include actual image occurrences and vector geometry so the
                # last question's crop cannot stop at its last text line.
                # Resource-table images may be unused and are never consulted.
                visual_boxes = []
                for image in page.get_image_info():
                    box = normalized_bbox(page, image["bbox"])
                    if box:
                        area = (box[2] - box[0]) * (box[3] - box[1])
                        if area >= 800000:
                            full_page_image = True
                        else:
                            visual_boxes.append(box)
                drawings = []
                for drawing in page.get_drawings():
                    rect = fitz.Rect(drawing["rect"])
                    if not all(math.isfinite(value) for value in rect) or rect.get_area() >= page.rect.get_area() * 0.8:
                        continue
                    # Stroked horizontal/vertical lines have zero area in the
                    # PDF path bounds, but occupy their actual stroke width.
                    padding = max(0.5, float(drawing.get("width") or 1) / 2)
                    rect = (rect + (-padding, -padding, padding, padding)) & page.rect
                    if not rect.is_empty:
                        drawings.append({**drawing, "rect": rect})
                rectangles = (
                    (
                        page.cluster_drawings(drawings=drawings)
                        if hasattr(page, "cluster_drawings")
                        else [drawing["rect"] for drawing in drawings]
                    )
                    if drawings
                    else []
                )
                for rectangle in rectangles:
                    box = normalized_bbox(page, rectangle)
                    if box:
                        visual_boxes.append(box)
                data = page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES, sort=True)
                for block in data["blocks"]:
                    for line in block.get("lines", []):
                        text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
                        box = normalized_bbox(page, line["bbox"])
                        if box is not None and text.strip():
                            text_lines.append(text)
                            marked = mark_private(text)
                            blocks.append(
                                {
                                    "seq": len(blocks),
                                    "type": "text",
                                    "page_idx": index,
                                    "bbox": box,
                                    "text": marked[:20000],
                                    "html": "",
                                }
                            )
                for box in visual_boxes:
                    blocks.append(
                        {"seq": len(blocks), "type": "image", "page_idx": index, "bbox": box, "text": "", "html": ""}
                    )
                joined = "\n".join(text_lines)
                warnings.extend(classify_page_text(joined, full_page_image=full_page_image))
                pages.append(
                    {
                        "page_idx": index,
                        "mode": page_mode(joined, full_page_image=full_page_image),
                        "text_characters": len(joined),
                        "warnings": warnings,
                    }
                )
            except Exception as exc:
                # One malformed page must not erase successful pages.
                pages.append(
                    {
                        "page_idx": index,
                        "mode": "manual",
                        "warnings": [f"本页文字提取失败（{type(exc).__name__}），请手工框题。"],
                    }
                )
    return {"blocks": blocks, "pages": pages}
