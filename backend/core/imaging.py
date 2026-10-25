"""页面渲染、题图拼接与裁图。坐标均为页面归一化 0–1000。"""

from __future__ import annotations

import base64
import io
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

PAGE_LONG_SIDE = 3000  # 本地保存的原页图（读题、裁图用）
PREVIEW_LONG_SIDE = 2000  # 网页显示用
SEGMENT_GAP = 18


class NotAPdfFile(ValueError):
    """内容不是 PDF，只是文件名叫 .pdf。

    PyMuPDF 不只认 PDF：改名为 .pdf 的网页、PNG、SVG 都能打开，还会被赋予一个
    **假的页面尺寸**（HTML 恒为 400×600，图片按像素数当磅）。所以「能不能打开」
    不能证明它是 PDF。以前只判「能不能打开」，这样的文件会被当成一份正常卷子
    收下：库里记着 1 页，预览是一片空白（渲染出来只有一个灰阶），失败原因还说
    「本地文字层没有可靠题卡」——老师看到的是「切题坏了」，真实原因是文件根本
    不是 PDF。

    现实里最常见的一种是下载链接失效存下了 404 页面：内容是
    ``<!DOCTYPE html><html><body>404</body></html>``。
    """


# PyMuPDF 自报的实际格式 -> 老师看得懂的一句话。
_WRONG_PDF_FORMATS = {"HTML5": "这其实是一个网页", "Image": "这其实是一张图片", "SVG": "这其实是一张矢量图"}


def _not_pdf_message(pdf) -> str:
    actual = (pdf.metadata or {}).get("format") or "别的格式"
    what = _WRONG_PDF_FORMATS.get(actual, "这不是 PDF")
    return f"{what}，只是文件名叫 .pdf。请把原文件另存为真正的 PDF 再传；如果是试卷照片，直接上传照片即可。"


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
        if not pdf.is_pdf:
            raise NotAPdfFile(_not_pdf_message(pdf))
        return [
            {"page_idx": i, "width": round(p.rect.width, 2), "height": round(p.rect.height, 2)}
            for i, p in enumerate(pdf)
        ]


def _finite_box(bbox) -> tuple[float, float, float, float] | None:
    """四个有限的数字就算一个框；超出页面由 ``to_pixels`` 的 clamp 负责。

    以前这里直接 ``x0, y0, x1, y1 = bbox``：少一维、坐标是字符串、坐标是 None、
    NaN 或无穷，分别会抛「解包不够」「拿字符串比大小」「NaN 转整数」「无穷转整数」
    四种异常。调用方写的是 ``except ValueError``，其中三种根本接不住——一道题的
    配图框坏掉，整份卷子的识读就停在半路，而不是这一张图不出来。

    注意这里**不**拒绝超出 0–1000 的坐标：``figure_strip`` 加边距、
    ``stack_regions`` 收整段范围，都会故意算出负数或超过页宽的框，交给 clamp。
    """
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in bbox):
        return None
    return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))


def _croppable(bbox) -> tuple[float, float, float, float] | None:
    """再加一条：框确实落在这一页里面，否则裁不出东西。

    判定和 ``figure_claims.frame`` 一致，所以「这张图裁不出来」在清单、进编号、
    发请求三处是同一个答案，不会出现某一处说裁得出、另一处崩掉。
    """
    box = _finite_box(bbox)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    return box if 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000 else None


def to_pixels(bbox: list[float], size: tuple[int, int]) -> tuple[int, int, int, int]:
    box_in_page = _finite_box(bbox)
    if box_in_page is None:
        raise ValueError("配图范围格式不正确")
    width, height = size
    x0, y0, x1, y1 = box_in_page
    box = (
        max(0, min(width - 1, math.floor(x0 * width / 1000))),
        max(0, min(height - 1, math.floor(y0 * height / 1000))),
        max(1, min(width, math.ceil(x1 * width / 1000))),
        max(1, min(height, math.ceil(y1 * height / 1000))),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError("范围小于一个像素")
    return box


# 严格按识读给的框裁，一点余量都不留：框边一旦压在某行字上，裁出来那行字就只剩
# 半截。教材里最常见的是压在图注上——图注「第 N 题」被切成上半行（八下 #1048、
# #1034，九上第 205 题印出来那张反比例函数图，底部一整行坐标数字被切掉）。
#
# 所以裁之前把边往外让一点，让到边上没有墨为止。原页长边 3000 像素，一行正文
# 约 40 像素，让 56 像素足够把一整行图注收进来，又不会碰到下一段正文。
FIGURE_CROP_SLACK_PX = 56
INK_MAX = 200  # 亮度高于这个算「白」。浅蓝方格线约 147，也算有墨——
# 方格线让进来只是几像素白边，无所谓；真正的目标是别把字切掉。


def _edge_has_ink(page: Image.Image, box: tuple[int, int, int, int]) -> bool:
    """这条边上有没有墨。只看最外面一列/一行，不看里面。"""
    return page.crop(box).convert("L").getextrema()[0] < INK_MAX


def crop_figure(page: Image.Image, box: tuple[int, int, int, int], slack: int = FIGURE_CROP_SLACK_PX) -> Image.Image:
    """按框裁图，但把切到字的那几条边往外让开，让到边上干净为止。

    四条边各自独立判断：有墨的那条才让，别的边一动不动，所以整张图不会平白
    胀一圈。让满 ``slack`` 像素仍有墨的（整版照片、坐标方格纸）就到头为止，
    不无限往外长。
    """
    left, top, right, bottom = box
    width, height = page.size
    for _ in range(max(0, slack)):
        grew = False
        if left > 0 and _edge_has_ink(page, (left, top, left + 1, bottom)):
            left -= 1
            grew = True
        if right < width and _edge_has_ink(page, (right - 1, top, right, bottom)):
            right += 1
            grew = True
        if top > 0 and _edge_has_ink(page, (left, top, right, top + 1)):
            top -= 1
            grew = True
        if bottom < height and _edge_has_ink(page, (left, bottom - 1, right, bottom)):
            bottom += 1
            grew = True
        if not grew:
            break
    return page.crop((left, top, right, bottom))


def stack_figure_pieces(pieces: list[tuple[Image.Image, tuple[int, int, int, int]]]) -> Image.Image:
    """Join the pieces of one figure (a table cut by a page break) into one image.

    pieces: (page image, pixel box) in reading order.  Each crop keeps its
    horizontal position on the page, so the columns of a table split across
    two pages line up again; pages of a different pixel width are scaled to
    the first one.  No gap: the halves meet like the uncut table.
    """
    if not pieces:
        raise ValueError("没有可拼接的配图")
    base_width = pieces[0][0].width
    placed = []
    for page, box in pieces:
        crop = page.crop(box).convert("RGB")
        scale = base_width / max(1, page.width)
        if abs(scale - 1) > 0.02:
            crop = crop.resize(
                (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))), Image.Resampling.LANCZOS
            )
        placed.append((crop, round(box[0] * scale)))
    left_most = min(left for _, left in placed)
    width = max(left - left_most + crop.width for crop, left in placed)
    height = sum(crop.height for crop, _ in placed)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for crop, left in placed:
        canvas.paste(crop, (left - left_most, y))
        y += crop.height
    return canvas


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


TRIM_MIN_BLANK = 30.0  # 页面坐标（0–1000）：底部空白至少这么高才裁掉


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


STRIP_TILE_HEIGHT = 560  # 每幅候选图归一到这个高度（页面短边单位）
STRIP_GAP = 24
STRIP_MAX_ZOOM = 3.0


def figure_strip(page_loader, figures: list[dict]) -> Image.Image | None:
    """Crop every candidate figure on its own, scale them to one height, number them.

    按题卡范围整段裁（``stack_regions``）在范围接近整页时会失效：跨栏大题、题组
    的范围能到页面的七成以上，那张候选在整段图里只剩几十像素，模型看不清就答
    「不是印刷插图」——实测 shengliesans 第 7 题就是这样被误否的。这里每张单独
    裁、按自己的尺寸放大再横排，每一幅都看得清。

    任何一幅的 bbox 裁不出图就返回 None，调用方必须放弃这次提问：发一张缺图的
    裁图等于让模型对着不存在的东西回答，它的「不是印刷插图」会读起来像结论。
    框写得不对（少一维、坐标是字符串或 None、NaN、无穷、超出页面）同样算裁不出图，
    一律返回 None 而不是往上抛——这条承诺以前只对「坐标反了」成立。
    """
    tiles = []
    for index, figure in enumerate(figures, 1):
        box_in_page = _croppable(figure.get("bbox"))
        if box_in_page is None:
            return None
        try:
            page = page_loader(figure.get("page_idx"))
            width, height = page.size
        except (LookupError, AttributeError, TypeError, ValueError):
            # 页取不出来（页码越界、缓存损坏、page_loader 拿到不是图像的东西），
            # 同样是不给图，而不是让整条流水线带着这一道题崩掉。
            return None
        x0, y0, x1, y1 = box_in_page
        pad = max(2.0, (y1 - y0) * 0.04)
        box = (
            max(0, min(width - 1, math.floor((x0 - pad) * width / 1000))),
            max(0, min(height - 1, math.floor((y0 - pad) * height / 1000))),
            max(1, min(width, math.ceil((x1 + pad) * width / 1000))),
            max(1, min(height, math.ceil((y1 + pad) * height / 1000))),
        )
        if box[2] <= box[0] or box[3] <= box[1]:
            return None
        tiles.append((index, page.crop(box).convert("RGB")))
    if not tiles:
        return None

    scaled = []
    for index, tile in tiles:
        factor = min(STRIP_MAX_ZOOM, STRIP_TILE_HEIGHT / max(1, tile.height))
        if abs(factor - 1) > 0.01:
            tile = tile.resize((max(1, round(tile.width * factor)), STRIP_TILE_HEIGHT), Image.Resampling.LANCZOS)
        scaled.append((index, tile))
    canvas = Image.new(
        "RGB", (sum(tile.width for _, tile in scaled) + STRIP_GAP * (len(scaled) + 1), STRIP_TILE_HEIGHT), "white"
    )
    draw = ImageDraw.Draw(canvas)
    font = _font(max(24, STRIP_TILE_HEIGHT // 12))
    x = STRIP_GAP
    for index, tile in scaled:
        canvas.paste(tile, (x, 0))
        draw.rectangle(
            [x, 0, x + tile.width - 1, STRIP_TILE_HEIGHT - 1], outline=(0, 90, 255), width=max(3, tile.width // 300)
        )
        draw.rectangle([x, 0, x + font.size + 26, font.size + 12], fill=(0, 90, 255))
        draw.text((x + 12, 6), str(index), fill=(255, 255, 255), font=font)
        x += tile.width + STRIP_GAP
    return canvas


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
