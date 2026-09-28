"""页面渲染、题图拼接与裁图。坐标均为页面归一化 0–1000。"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

PAGE_LONG_SIDE = 3000       # 本地保存的原页图（读题、裁图用）
PREVIEW_LONG_SIDE = 2000    # 网页显示用
SEGMENT_GAP = 18


def render_source_page(source: Path, kind: str, page_idx: int, long_side: int = PAGE_LONG_SIDE) -> Image.Image:
    if kind == "image":
        if page_idx != 0:
            raise IndexError("图片只有 1 页")
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw)
            image.load()
            image = image.convert("RGB")
        if max(image.size) > long_side:
            image.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
        return image
    import pymupdf as fitz

    with fitz.open(source) as pdf:
        page = pdf[page_idx]
        scale = long_side / max(page.rect.width, page.rect.height)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples)


def page_sizes(source: Path, kind: str) -> list[dict]:
    if kind == "image":
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw)
            return [{"page_idx": 0, "width": image.width, "height": image.height}]
    import pymupdf as fitz

    with fitz.open(source) as pdf:
        return [{"page_idx": i, "width": round(p.rect.width, 2), "height": round(p.rect.height, 2)}
                for i, p in enumerate(pdf)]


def to_pixels(bbox: list[float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = size
    x0, y0, x1, y1 = bbox
    box = (
        max(0, min(width - 1, math.floor(x0 * width / 1000))),
        max(0, min(height - 1, math.floor(y0 * height / 1000))),
        max(1, min(width, math.ceil(x1 * width / 1000))),
        max(1, min(height, math.ceil(y1 * height / 1000))),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("范围小于一个像素")
    return box


def stack_regions(regions: list[dict], page_loader, marks: list[dict] | None = None) -> tuple[Image.Image, list[dict]]:
    """把一道题的若干原卷矩形自上而下拼成一张图。

    marks: [{"label": "图1", "page_idx": p, "bbox": [...]}] 画成细框加标签（只用于发给模型的图）。
    返回 (图像, 段落位置表)；段落位置表用于把模型给出的图内位置换回页面坐标。
    """
    pieces, layout = [], []
    for region in regions:
        page = page_loader(region["page_idx"])
        box = to_pixels(region["bbox"], page.size)
        piece = page.crop(box).copy()
        if marks:
            draw = ImageDraw.Draw(piece)
            font = _font(max(18, piece.width // 40))
            for mark in marks:
                if mark["page_idx"] != region["page_idx"]:
                    continue
                mx0, my0, mx1, my1 = to_pixels(mark["bbox"], page.size)
                rect = (mx0 - box[0], my0 - box[1], mx1 - box[0], my1 - box[1])
                if rect[2] < 0 or rect[3] < 0 or rect[0] > piece.width or rect[1] > piece.height:
                    continue
                draw.rectangle(rect, outline=(0, 90, 255), width=max(2, piece.width // 500))
                label_xy = (max(0, rect[0]), max(0, rect[1] - font.size - 6))
                text_box = draw.textbbox(label_xy, mark["label"], font=font)
                draw.rectangle((text_box[0] - 3, text_box[1] - 2, text_box[2] + 3, text_box[3] + 2), fill=(0, 90, 255))
                draw.text(label_xy, mark["label"], fill=(255, 255, 255), font=font)
        pieces.append(piece)
        layout.append({"page_idx": region["page_idx"], "bbox": region["bbox"], "pixels": box})
    if not pieces:
        raise ValueError("没有可拼接的范围")
    width = max(piece.width for piece in pieces)
    height = sum(piece.height for piece in pieces) + SEGMENT_GAP * (len(pieces) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    draw = ImageDraw.Draw(canvas)
    for index, piece in enumerate(pieces):
        canvas.paste(piece, (0, y))
        layout[index]["offset"] = y
        layout[index]["height"] = piece.height
        y += piece.height
        if index < len(pieces) - 1:
            line_y = y + SEGMENT_GAP // 2
            draw.line((0, line_y, width, line_y), fill=(200, 200, 200), width=2)
            y += SEGMENT_GAP
    return canvas, layout


TRIM_MIN_BLANK = 30.0   # 页面坐标（0–1000）：底部空白至少这么高才裁掉


def trim_regions(regions: list[dict], page_loader) -> list[dict]:
    """去掉每段范围底部的大片空白（解答题的作答空间）。有字迹（含手写）处保留。

    先二值化再缩小：直接缩小灰度图会把分式分母、下标这类细笔画平均成浅灰，
    被误判为空白而裁掉（实测：高考卷 V甲/V乙 的“乙”被切掉半截）。小段空白
    不值得冒险，只有底部空白足够大时才裁。
    """
    trimmed = []
    for region in regions:
        page = page_loader(region["page_idx"])
        box = to_pixels(region["bbox"], page.size)
        ink = page.crop(box).convert("L").point(lambda value: 255 if value < 170 else 0)
        width, height = ink.size
        small = ink.resize((max(1, width // 4), max(1, height // 4)), Image.Resampling.BOX)
        sw, sh = small.size
        pixels = small.load()
        last_ink = -1
        for y in range(sh):
            dark = sum(1 for x in range(sw) if pixels[x, y] > 40)
            if dark >= max(2, sw // 300):
                last_ink = y
        x0, y0, x1, y1 = region["bbox"]
        if last_ink < 0:
            continue  # 整段空白
        keep = min(sh, last_ink + 1 + max(6, sh // 25)) / sh
        blank = (y1 - y0) * (1 - keep)
        if keep < 0.85 and blank >= TRIM_MIN_BLANK:
            y1 = y0 + (y1 - y0) * keep
        trimmed.append({"page_idx": region["page_idx"], "bbox": [x0, y0, x1, round(y1, 1)]})
    return trimmed or regions


def add_ruler(image: Image.Image, bands: int = 30) -> tuple[Image.Image, int]:
    """在左侧加一列带编号的横向刻度（01…），用于让模型报告"第几格"而不是坐标。"""
    margin = max(70, image.width // 16)
    ruled = Image.new("RGB", (image.width + margin, image.height), "white")
    ruled.paste(image, (margin, 0))
    draw = ImageDraw.Draw(ruled)
    band = image.height / bands
    font = _font(max(16, min(int(band * 0.7), margin // 2)))
    for index in range(bands):
        top = round(index * band)
        color = (220, 0, 0) if index % 2 == 0 else (0, 110, 0)
        draw.line((0, top, ruled.width, top), fill=color + (0,), width=1)
        draw.text((4, top + 2), f"{index + 1:02d}", fill=color, font=font)
    return ruled, bands


def band_to_page(layout: list[dict], band: int, bands: int, total_height: int) -> tuple[int, float] | None:
    """把刻度编号（1 起）换回 (页码, 页面 y 坐标)。"""
    y_pixel = (band - 1) * total_height / bands
    for segment in layout:
        if segment["offset"] - SEGMENT_GAP <= y_pixel < segment["offset"] + segment["height"]:
            local = max(0.0, y_pixel - segment["offset"])
            x0, y0, x1, y1 = segment["bbox"]
            fraction = local / max(1, segment["height"])
            return segment["page_idx"], y0 + fraction * (y1 - y0)
    return None


def jpeg_data_url(image: Image.Image, long_side: int = 2000, quality: int = 88) -> str:
    image = image.convert("RGB")
    if max(image.size) > long_side:
        image = image.copy()
        image.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


_FONT_CACHE: dict[int, ImageFont.ImageFont] = {}


def _font(size: int):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for name in ("arial.ttf", "DejaVuSans.ttf", "msyh.ttc", "simhei.ttf"):
        try:
            font = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    else:
        font = ImageFont.load_default(size=size)
    _FONT_CACHE[size] = font
    return font
