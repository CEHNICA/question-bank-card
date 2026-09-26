"""手机拍的试卷照片：几张合成一份卷、拍歪了拉正、扫描件效果、按卷面题号排页序。

只用 Pillow 和 PyMuPDF（运行环境里本来就有），不需要额外安装图像库。
处理顺序：转正（EXIF）→ 找纸张四角并拉正 → 缩到 3000 像素 → 扫描件效果 → 合成 PDF 交给 MinerU。
原图原样保留在试卷目录里；处理后的每张照片也单独保存，换页序时直接重新拼 PDF，不用重算。
"""

from __future__ import annotations

import hashlib
import re
from collections import deque
from pathlib import Path

from PIL import Image, ImageFilter, ImageMath, ImageOps

from . import segment

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_PHOTOS = 30
LONG_SIDE = 3000          # 与 imaging.PAGE_LONG_SIDE 一致
PDF_DPI = 200             # 只决定 PDF 页面的"物理尺寸"，不影响清晰度
DETECT_SIDE = 400         # 找纸张四角时用的小图边长


# ---------------------------------------------------------------- 初始顺序（只是粗排，最终按题号排）

def capture_time(path: Path) -> str:
    """照片 EXIF 里的拍摄时间（"2025:10:28 07:00:13"）；没有就返回空串。"""
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            value = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132) or ""
    except Exception:
        return ""
    value = str(value).strip().strip("\x00")
    return value if re.fullmatch(r"\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}", value) else ""


def _natural_key(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def initial_order(files: list[dict]) -> tuple[list[int], str]:
    """按拍摄时间 → 文件名里的数字 → 选择顺序，给出一个初步顺序和依据。"""
    indexes = list(range(len(files)))
    times = [f.get("taken") or "" for f in files]
    if len(files) > 1 and all(times) and len(set(times)) == len(times):
        return sorted(indexes, key=lambda i: times[i]), "拍摄时间"
    if len(files) > 1 and all(re.search(r"\d", f["name"]) for f in files):
        return sorted(indexes, key=lambda i: _natural_key(files[i]["name"])), "文件名"
    return indexes, "选择顺序"


# ---------------------------------------------------------------- 拍歪了：找纸张四角并拉正

def _otsu(histogram: list[int]) -> int:
    total = sum(histogram)
    weighted = sum(i * h for i, h in enumerate(histogram))
    best, threshold, background, background_sum = -1.0, 128, 0, 0.0
    for value in range(256):
        background += histogram[value]
        if background == 0:
            continue
        foreground = total - background
        if foreground == 0:
            break
        background_sum += value * histogram[value]
        mean_b = background_sum / background
        mean_f = (weighted - background_sum) / foreground
        between = background * foreground * (mean_b - mean_f) ** 2
        if between > best:
            best, threshold = between, value
    return threshold


def _largest_bright_region(mask: Image.Image) -> list[tuple[int, int]]:
    width, height = mask.size
    pixels = mask.load()
    seen = bytearray(width * height)
    best: list[tuple[int, int]] = []
    for start_y in range(height):
        for start_x in range(width):
            index = start_y * width + start_x
            if seen[index] or not pixels[start_x, start_y]:
                continue
            seen[index] = 1
            region, queue = [], deque([(start_x, start_y)])
            while queue:
                x, y = queue.popleft()
                region.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < width and 0 <= ny < height:
                        n = ny * width + nx
                        if not seen[n] and pixels[nx, ny]:
                            seen[n] = 1
                            queue.append((nx, ny))
            if len(region) > len(best):
                best = region
    return best


def _filled_size(region: list[tuple[int, int]], width: int, height: int) -> int:
    """亮区连同被它围住的"洞"（大块配图、粗黑字、手写）一共多大：从画面边缘能走到的非亮区才算背景。"""
    inside = bytearray(width * height)
    for x, y in region:
        inside[y * width + x] = 1
    outside = bytearray(width * height)
    queue = deque()
    for x in range(width):
        for y in (0, height - 1):
            if not inside[y * width + x] and not outside[y * width + x]:
                outside[y * width + x] = 1
                queue.append((x, y))
    for y in range(height):
        for x in (0, width - 1):
            if not inside[y * width + x] and not outside[y * width + x]:
                outside[y * width + x] = 1
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height:
                n = ny * width + nx
                if not inside[n] and not outside[n]:
                    outside[n] = 1
                    queue.append((nx, ny))
    return width * height - sum(outside)


def _polygon_area(points: list[tuple[float, float]]) -> float:
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))) / 2


def _angle(a, b, c) -> float:
    """b 点处的内角（度）。"""
    import math

    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    norm = math.hypot(*v1) * math.hypot(*v2)
    if norm == 0:
        return 0.0
    cosine = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / norm))
    return math.degrees(math.acos(cosine))


def find_page_quad(image: Image.Image) -> list[tuple[float, float]] | None:
    """在照片里找整张试卷纸的四个角（左上、右上、右下、左下，原图像素坐标）。

    纸比桌面亮：缩小 → Otsu 分出亮区 → 取最大的一块 → 用 x+y、x−y 的极值找四角。
    只有整张纸都在画面里、四周露出背景、形状确实是四边形时才返回；否则返回 None（不拉正）。
    """
    scale = DETECT_SIDE / max(image.size)
    if scale >= 1:
        scale = 1.0
    small = image.convert("L").resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                                      Image.Resampling.BOX).filter(ImageFilter.GaussianBlur(1.5))
    threshold = _otsu(small.histogram())
    # 闭运算：把纸上的黑字"填平"，纸成为一整块。
    mask = small.point(lambda v: 255 if v > threshold else 0).filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    region = _largest_bright_region(mask)
    width, height = small.size
    if len(region) < 0.2 * width * height:
        return None
    corners = [
        min(region, key=lambda p: p[0] + p[1]),     # 左上
        max(region, key=lambda p: p[0] - p[1]),     # 右上
        max(region, key=lambda p: p[0] + p[1]),     # 右下
        min(region, key=lambda p: p[0] - p[1]),     # 左下
    ]
    margin = 0.012 * max(width, height)
    touching = sum(1 for x, y in corners if x <= margin or y <= margin or x >= width - 1 - margin or y >= height - 1 - margin)
    if touching:
        return None                               # 纸没有完整拍进来（或本来就是扫描件/截图）
    area = _polygon_area(corners)
    if area < 0.2 * width * height:
        return None
    filled = _filled_size(region, width, height)
    if not 0.9 * area <= filled <= 1.1 * area:
        return None                               # 亮区不像一张四边形的纸
    angles = [_angle(corners[i - 1], corners[i], corners[(i + 1) % 4]) for i in range(4)]
    if not all(55 <= a <= 125 for a in angles):
        return None
    # 小图上找的角点有约 1% 的误差：往里收一点，宁可切掉一丝空白纸边，也不把桌面带进来。
    cx = sum(x for x, _ in corners) / 4
    cy = sum(y for _, y in corners) / 4
    grow = 0.985
    return [((cx + (x - cx) * grow) / scale, (cy + (y - cy) * grow) / scale) for x, y in corners]


def _solve(matrix: list[list[float]], values: list[float]) -> list[float]:
    """高斯消元解 8×8 线性方程组。"""
    size = len(values)
    rows = [row[:] + [value] for row, value in zip(matrix, values)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(rows[r][col]))
        rows[col], rows[pivot] = rows[pivot], rows[col]
        if abs(rows[col][col]) < 1e-12:
            raise ValueError("四角共线")
        for r in range(size):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][size] / rows[i][i] for i in range(size)]


def straighten(image: Image.Image, quad: list[tuple[float, float]]) -> Image.Image:
    """把照片里的梯形纸面拉回长方形（透视矫正）。"""
    import math

    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = quad
    width = max(math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x3, y2 - y3))
    height = max(math.hypot(x3 - x0, y3 - y0), math.hypot(x2 - x1, y2 - y1))
    # 斜着拍时远处的边被"压扁"，量出来的长宽比不准。试卷纸（A4、A3、8 开、16 开）长宽比都接近 √2，
    # 量出来接近时就按 √2 还原。
    long_side, short_side = max(width, height), min(width, height)
    if short_side and abs(long_side / short_side / math.sqrt(2) - 1) <= 0.15:
        if width >= height:
            height = width / math.sqrt(2)
        else:
            width = height / math.sqrt(2)
    ratio = min(1.0, LONG_SIDE * 1.2 / max(width, height))
    out_w, out_h = max(1, round(width * ratio)), max(1, round(height * ratio))
    targets = [(0, 0), (out_w, 0), (out_w, out_h), (0, out_h)]
    matrix, values = [], []
    for (u, v), (x, y) in zip(targets, quad):     # 输出坐标 (u, v) → 原图坐标 (x, y)
        matrix.append([u, v, 1, 0, 0, 0, -u * x, -v * x])
        values.append(x)
        matrix.append([0, 0, 0, u, v, 1, -u * y, -v * y])
        values.append(y)
    coefficients = _solve(matrix, values)
    return image.transform((out_w, out_h), Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)


# ---------------------------------------------------------------- 扫描件效果

def enhance(image: Image.Image) -> Image.Image:
    """估计纸张底色并除掉（去阴影、去偏黄、提亮），再拉开对比度。

    输出灰度，不做纯黑白：纯黑白会丢掉配图里的细线、虚线和浅色印刷。
    """
    gray = image.convert("L").filter(ImageFilter.MedianFilter(3))     # 先去拍照噪点，免得提亮后成了麻点
    width, height = gray.size
    small = gray.resize((max(1, width // 4), max(1, height // 4)), Image.Resampling.BILINEAR)
    # 最大值滤波把字"抹掉"，只剩纸的底色；再模糊得到平滑的光照图。
    background = small.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MaxFilter(9)) \
        .filter(ImageFilter.GaussianBlur(12)).resize((width, height), Image.Resampling.BILINEAR)
    ratio = ImageMath.lambda_eval(
        lambda args: args["float"](args["g"]) * 255.0 / (args["float"](args["b"]) + 1.0), g=gray, b=background)
    normalized = ratio.convert("L")
    histogram = normalized.histogram()
    total = sum(histogram)
    running, black = 0, 0
    for value, count in enumerate(histogram):
        running += count
        if running >= total * 0.005:
            black = value
            break
    black = min(black, 150)
    white = 228
    table = []
    for value in range(256):
        level = max(0.0, min(1.0, (value - black) / max(1, white - black)))
        table.append(round(255 * level ** 1.25))
    return normalized.point(table)


# ---------------------------------------------------------------- 一张照片的完整处理

def load_photo(path: Path) -> Image.Image:
    with Image.open(path) as raw:
        image = ImageOps.exif_transpose(raw)
        image.load()
        return image.convert("RGB")


def process_photo(path: Path, *, clean: bool) -> tuple[Image.Image, bool]:
    """返回 (处理后的页面图, 是否做了透视矫正)。"""
    image = load_photo(path)
    straightened = False
    quad = find_page_quad(image)
    if quad is not None:
        image = straighten(image, quad)
        straightened = True
    if max(image.size) > LONG_SIDE:
        image.thumbnail((LONG_SIDE, LONG_SIDE), Image.Resampling.LANCZOS)
    if clean:
        image = enhance(image)
    return image, straightened


def page_file(folder: Path, index: int) -> Path:
    return folder / f"photo_{index + 1:02d}.page.jpg"


def prepare_pages(folder: Path, photos: dict) -> list[str]:
    """把每张照片处理成页面图（已处理过的跳过）。返回给人看的说明。"""
    notes = []
    for index, item in enumerate(photos["files"]):
        target = page_file(folder, index)
        if target.is_file():
            continue
        image, straightened = process_photo(folder / item["file"], clean=photos.get("enhance", True))
        item["straightened"] = straightened
        image.save(target, format="JPEG", quality=90, optimize=True)
    tilted = [item["name"] for item in photos["files"] if item.get("straightened")]
    if tilted:
        notes.append(f"{'、'.join(tilted)} 拍得有点斜，已自动拉正。")
    if photos.get("enhance", True):
        notes.append("照片已做扫描件效果（去阴影、提亮、去偏色）。")
    return notes


def build_pdf(folder: Path, photos: dict, target: Path) -> None:
    """按 photos["order"] 把处理好的页面图拼成 PDF（给 MinerU 解析，也用来渲染页面）。"""
    import pymupdf as fitz

    document = fitz.open()
    try:
        for index in photos["order"]:
            source = page_file(folder, index)
            with Image.open(source) as image:
                width, height = image.size
            page = document.new_page(width=width * 72 / PDF_DPI, height=height * 72 / PDF_DPI)
            page.insert_image(page.rect, stream=source.read_bytes())
        temp = target.with_suffix(".tmp.pdf")
        document.save(temp, garbage=3, deflate=True)
    finally:
        document.close()
    temp.replace(target)


def order_version(photos: dict) -> str:
    """页序变了，网页上的原卷预览图地址也跟着变（避免浏览器用旧缓存）。"""
    if not photos:
        return ""
    return hashlib.sha1(repr(photos.get("order")).encode()).hexdigest()[:8]


# ---------------------------------------------------------------- 按卷面题号排页序

def page_ranges(pages: list[dict], blocks: list[dict]) -> dict[int, tuple[int, int] | None]:
    """每一页自己找出的印刷题号范围（和页序无关，每页单独判断）。"""
    result: dict[int, tuple[int, int] | None] = {}
    for page in pages:
        index = page["page_idx"]
        own = [{**block, "page_idx": 0} for block in blocks if block["page_idx"] == index]
        numbers = []
        if own:
            _, chain = segment.analyse([{**page, "page_idx": 0}], own)
            numbers = [start.number for start in chain]
        result[index] = (min(numbers), max(numbers)) if numbers else None
    return result


def arrange(ranges: dict[int, tuple[int, int] | None], current: list[int]) -> tuple[list[int], str]:
    """按题号排页序。返回 (新页序：当前页码的排列, 需要人确认的原因；空串表示有把握)。

    current：当前页码按初步顺序（拍摄时间/文件名）的排列，用来安放没有题号的页。
    """
    numbered = [page for page in current if ranges.get(page)]
    blank = [page for page in current if not ranges.get(page)]
    if len(numbered) < 2 and not blank:
        return current, ""
    by_number = sorted(numbered, key=lambda page: ranges[page])
    for previous, following in zip(by_number, by_number[1:]):
        if ranges[following][0] <= ranges[previous][1]:
            low = ranges[following][0]
            high = min(ranges[previous][1], ranges[following][1])
            same = f"第 {low} 题" if low == high else f"第 {low}–{high} 题"
            return current, (f"有两页的题号重复（都有{same}），可能同一页拍了两次，或者混进了别的卷子。"
                             "先按拍摄/文件顺序排着，请点“调整页序”确认。")
    order = list(by_number)
    for page in blank:
        position = current.index(page)
        before = next((p for p in reversed(current[:position]) if p in order), None)
        if before is None:
            order.insert(0, page)
        else:
            spot = order.index(before) + 1
            while spot < len(order) and order[spot] in blank:
                spot += 1
            order.insert(spot, page)
    if blank:
        return order, "有照片上没找到题号，按拍摄/文件顺序放着，请点“调整页序”确认它的位置。"
    return order, ""


def remap_page(mapping: dict[int, int], item: dict) -> dict:
    return {**item, "page_idx": mapping[item["page_idx"]]} if "page_idx" in item else item
