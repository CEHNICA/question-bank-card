"""Bounded, local PDF text-layer extraction. Coordinates come from the PDF.

No model, guessed formula, or guessed scan-page text enters this adapter.
The extracted text is a draft; original-page bodies remain the safe default.
"""
from __future__ import annotations

import math
import unicodedata
from pathlib import Path

import pymupdf as fitz


def normalized_bbox(page, bbox) -> list[float] | None:
    rect = fitz.Rect(bbox)
    if any(not math.isfinite(value) for value in rect):
        return None
    rect = (rect * page.rotation_matrix) & page.rect
    bounds = page.rect
    if rect.is_empty or bounds.width <= 0 or bounds.height <= 0:
        return None
    return [round((rect.x0 - bounds.x0) / bounds.width * 1000, 2),
            round((rect.y0 - bounds.y0) / bounds.height * 1000, 2),
            round((rect.x1 - bounds.x0) / bounds.width * 1000, 2),
            round((rect.y1 - bounds.y0) / bounds.height * 1000, 2)]


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
                rectangles = (page.cluster_drawings(drawings=drawings) if hasattr(page, "cluster_drawings")
                              else [drawing["rect"] for drawing in drawings]) if drawings else []
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
                            blocks.append({"seq": len(blocks), "type": "text", "page_idx": index,
                                           "bbox": box, "text": text[:20000], "html": ""})
                for box in visual_boxes:
                    blocks.append({"seq": len(blocks), "type": "image", "page_idx": index,
                                   "bbox": box, "text": "", "html": ""})
                joined = "\n".join(text_lines)
                corrupt = sum(ch == "\ufffd" or unicodedata.category(ch) == "Co" for ch in joined)
                if not joined.strip():
                    warnings.append("没有可复制文字，保留原页并手工框题。")
                elif corrupt:
                    warnings.append("文字层存在乱码，正文应保留原图并对照原卷。")
                if full_page_image:
                    warnings.append("本页以整页扫描图为主，文字层只作辅助，请手工框出完整题目。")
                warnings.append("文字层不能保证分式、上下标和图表准确；没有自动补写公式。")
                pages.append({"page_idx": index, "mode": "native" if joined.strip() and not corrupt and not full_page_image else "manual",
                              "text_characters": len(joined), "warnings": warnings})
            except Exception as exc:
                # One malformed page must not erase successful pages.
                pages.append({"page_idx": index, "mode": "manual", "warnings": [f"本页文字提取失败（{type(exc).__name__}），请手工框题。"]})
    return {"blocks": blocks, "pages": pages}
