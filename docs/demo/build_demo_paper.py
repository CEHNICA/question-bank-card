"""Create the original public demo paper used in screenshots and release demos."""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont as FontToolsFont
from fontTools.varLib.instancer import instantiateVariableFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "tiyouju-demo-paper.pdf"
FONT_CANDIDATES = (
    Path(os.environ.get("QB_DEMO_NOTO_FONT", "")),
    Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
)

INK = colors.HexColor("#163F3A")
JADE = colors.HexColor("#2F9668")
GOLD = colors.HexColor("#D6A65D")
PAPER = colors.HexColor("#FFF9EC")
MUTED = colors.HexColor("#6A7D78")
GRID = colors.HexColor("#D9E4DF")


def find_font() -> Path:
    for candidate in FONT_CANDIDATES:
        if str(candidate) and candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "缺少 Noto Sans SC。请安装 Noto Sans SC，或用 QB_DEMO_NOTO_FONT 指向其 OFL 字体文件。"
    )


def static_font(source: Path, target: Path, weight: int, style: str) -> None:
    font = FontToolsFont(source, fontNumber=0)
    if "fvar" in font:
        font = instantiateVariableFont(font, {"wght": weight}, inplace=False)
    family = "Tiyouju Demo Sans"
    full_name = f"{family} {style}"
    postscript = f"TiyoujuDemoSans-{style}"
    names = font["name"]
    names.names = [record for record in names.names if record.nameID not in {1, 2, 4, 6}]
    for platform_id, encoding_id, language_id in ((3, 1, 0x409), (1, 0, 0)):
        names.setName(family, 1, platform_id, encoding_id, language_id)
        names.setName(style, 2, platform_id, encoding_id, language_id)
        names.setName(full_name, 4, platform_id, encoding_id, language_id)
        names.setName(postscript, 6, platform_id, encoding_id, language_id)
    font.save(target)


def register_fonts(work: Path) -> None:
    source = find_font()
    regular = work / "NotoSansSC-Regular.ttf"
    bold = work / "NotoSansSC-Bold.ttf"
    static_font(source, regular, 400, "Regular")
    static_font(source, bold, 700, "Bold")
    pdfmetrics.registerFont(TTFont("DemoSans", str(regular)))
    pdfmetrics.registerFont(TTFont("DemoSansBold", str(bold)))


def rounded_label(c: canvas.Canvas, x: float, y: float, text: str) -> None:
    c.setFillColor(colors.HexColor("#E4F2EC"))
    c.roundRect(x, y - 15, 90, 25, 12, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("DemoSans", 9)
    c.drawCentredString(x + 45, y - 7, text)


def header(c: canvas.Canvas, page: int, section: str) -> None:
    width, height = A4
    c.setFillColor(PAPER)
    c.rect(0, 0, width, height, fill=1, stroke=0)
    c.setFillColor(INK)
    c.roundRect(34, height - 91, width - 68, 54, 14, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("DemoSansBold", 19)
    c.drawString(54, height - 68, "题有据功能演示卷")
    c.setFont("DemoSans", 9.5)
    c.drawRightString(width - 54, height - 65, "原创演示数据 · 可公开使用")
    rounded_label(c, 42, height - 114, section)
    c.setFillColor(MUTED)
    c.setFont("DemoSans", 8.5)
    c.drawRightString(width - 42, 25, f"题有据 · 第 {page} 页 / 共 3 页")


def text(c: canvas.Canvas, x: float, y: float, value: str, size: float = 11, bold: bool = False) -> None:
    c.setFillColor(colors.HexColor("#263C37"))
    c.setFont("DemoSansBold" if bold else "DemoSans", size)
    c.drawString(x, y, value)


def option_row(c: canvas.Canvas, x: float, y: float, values: list[str]) -> None:
    c.setFont("DemoSans", 10.2)
    c.setFillColor(colors.HexColor("#263C37"))
    step = 122
    for index, value in enumerate(values):
        c.drawString(x + index * step, y, value)


def divider(c: canvas.Canvas, y: float) -> None:
    c.setStrokeColor(GRID)
    c.setLineWidth(0.7)
    c.line(42, y, A4[0] - 42, y)


def triangle(c: canvas.Canvas, x: float, y: float, scale: float = 1.0) -> None:
    points = [(x, y), (x + 126 * scale, y), (x + 42 * scale, y + 92 * scale)]
    c.setStrokeColor(INK)
    c.setLineWidth(1.8)
    path = c.beginPath()
    path.moveTo(*points[0])
    path.lineTo(*points[1])
    path.lineTo(*points[2])
    path.close()
    c.drawPath(path, stroke=1, fill=0)
    c.setFillColor(JADE)
    for px, py in points:
        c.circle(px, py, 3, fill=1, stroke=0)
    c.setFillColor(INK)
    c.setFont("DemoSans", 9)
    c.drawString(points[0][0] - 12, points[0][1] - 13, "B")
    c.drawString(points[1][0] + 5, points[1][1] - 13, "C")
    c.drawString(points[2][0] - 2, points[2][1] + 8, "A")
    c.setStrokeColor(GOLD)
    c.setDash(4, 3)
    c.line(points[2][0], points[2][1], points[2][0], y)
    c.setDash()


def draw_table(c: canvas.Canvas, x: float, y: float) -> None:
    widths = [74, 66, 66, 66, 66]
    rows = [
        ["阅读时长/分钟", "20", "30", "40", "50"],
        ["人数", "3", "7", "8", "2"],
    ]
    height = 28
    current_y = y
    for row_index, row in enumerate(rows):
        current_x = x
        for col_index, cell in enumerate(row):
            c.setFillColor(colors.HexColor("#E4F2EC") if col_index == 0 else colors.white)
            c.setStrokeColor(GRID)
            c.rect(current_x, current_y - height, widths[col_index], height, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("DemoSansBold" if col_index == 0 else "DemoSans", 8.5)
            c.drawCentredString(current_x + widths[col_index] / 2, current_y - 18, cell)
            current_x += widths[col_index]
        current_y -= height


def axes(c: canvas.Canvas, x: float, y: float, width: float, height: float) -> None:
    c.setStrokeColor(GRID)
    c.setLineWidth(0.6)
    for i in range(1, 6):
        c.line(x + i * width / 6, y, x + i * width / 6, y + height)
        c.line(x, y + i * height / 6, x + width, y + i * height / 6)
    c.setStrokeColor(INK)
    c.setLineWidth(1.3)
    c.line(x, y + height / 2, x + width, y + height / 2)
    c.line(x + width / 2, y, x + width / 2, y + height)
    c.line(x + width - 7, y + height / 2 + 4, x + width, y + height / 2)
    c.line(x + width - 7, y + height / 2 - 4, x + width, y + height / 2)
    c.line(x + width / 2 - 4, y + height - 7, x + width / 2, y + height)
    c.line(x + width / 2 + 4, y + height - 7, x + width / 2, y + height)


def triangular_prism(c: canvas.Canvas, x: float, y: float) -> None:
    front = [(x, y), (x + 78, y), (x + 24, y + 62)]
    shift = (62, 34)
    back = [(px + shift[0], py + shift[1]) for px, py in front]
    c.setStrokeColor(INK)
    c.setLineWidth(1.6)
    for tri in (front, back):
        p = c.beginPath()
        p.moveTo(*tri[0])
        p.lineTo(*tri[1])
        p.lineTo(*tri[2])
        p.close()
        c.drawPath(p, stroke=1, fill=0)
    for a, b in zip(front, back):
        c.line(a[0], a[1], b[0], b[1])
    c.setDash(3, 3)
    c.setStrokeColor(GOLD)
    c.line(front[2][0], front[2][1], back[1][0], back[1][1])
    c.setDash()


def page_one(c: canvas.Canvas) -> None:
    header(c, 1, "代数 · 几何 · 统计")
    text(c, 44, 688, "1. 已知集合 A={x | x²-5x+6=0}，则 A 的元素个数是（　）。", 11.2, True)
    option_row(c, 64, 653, ["A. 0", "B. 1", "C. 2", "D. 3"])
    divider(c, 626)

    text(c, 44, 592, "2. 如图，在 △ABC 中，AD⊥BC，AB=AC。下列结论一定成立的是（　）。", 10.6, True)
    option_row(c, 64, 556, ["A. BD=CD", "B. ∠B=90°"])
    option_row(c, 64, 526, ["C. AB=BC", "D. AD=BC"])
    triangle(c, 380, 490, 0.72)
    c.setFillColor(INK)
    c.setFont("DemoSans", 8)
    c.drawString(415, 493, "D")
    divider(c, 468)

    text(c, 44, 435, "3. 某小组记录了课外阅读时长，数据如下表。平均阅读时长为（　）。", 10.6, True)
    draw_table(c, 108, 398)
    option_row(c, 64, 315, ["A. 30", "B. 34", "C. 36", "D. 40"])
    divider(c, 286)

    text(c, 44, 250, "4. 一辆自行车以 12 km/h 的速度行驶 45 分钟，共行驶多少千米？", 10.6, True)
    text(c, 64, 216, "请写出计算过程，并说明单位换算。", 10)


def page_two(c: canvas.Canvas) -> None:
    header(c, 2, "函数 · English · 空间图形")
    text(c, 44, 688, "5. As shown in the figure, line ℓ passes through A(-2, 1) and B(2, 3).", 10.4, True)
    text(c, 64, 659, "Which equation represents line ℓ?", 10.2)
    option_row(c, 64, 622, ["A. y=x+1", "B. y=½x+2"])
    option_row(c, 64, 592, ["C. y=2x+1", "D. y=-x+1"])
    axes(c, 365, 576, 160, 110)
    c.setStrokeColor(JADE)
    c.setLineWidth(2.2)
    c.line(386, 600, 512, 663)
    divider(c, 548)

    text(c, 44, 514, "6. The graph below shows the temperature during a morning experiment.", 10.4, True)
    text(c, 64, 486, "At what time did the temperature first reach 18°C?", 10.2)
    axes(c, 344, 397, 180, 92)
    c.setStrokeColor(JADE)
    c.setLineWidth(2.4)
    p = c.beginPath()
    p.moveTo(356, 418)
    p.lineTo(398, 431)
    p.lineTo(443, 452)
    p.lineTo(486, 466)
    p.lineTo(518, 470)
    c.drawPath(p, stroke=1, fill=0)
    divider(c, 366)

    text(c, 44, 332, "7. Solve 3(x-2)=2x+5. Show each transformation and verify the result.", 10.4, True)
    divider(c, 292)

    text(c, 44, 258, "8. 参照右侧三棱柱示意图，若底面三角形面积为 12 cm²，棱柱高为 8 cm，", 10.2, True)
    text(c, 64, 231, "求该三棱柱的体积。", 10.2)
    triangular_prism(c, 390, 174)


def page_three(c: canvas.Canvas) -> None:
    header(c, 3, "数轴 · 规律 · 综合")
    text(c, 44, 688, "9. 如图，数轴上点 P 表示的数为 -2。将点 P 向右移动 5 个单位后表示（　）。", 10.5, True)
    c.setStrokeColor(INK)
    c.setLineWidth(1.6)
    c.line(88, 620, 500, 620)
    c.line(493, 625, 500, 620)
    c.line(493, 615, 500, 620)
    for i, label in enumerate(range(-4, 5)):
        px = 118 + i * 42
        c.line(px, 614, px, 626)
        c.setFillColor(INK)
        c.setFont("DemoSans", 8)
        c.drawCentredString(px, 598, str(label))
    c.setFillColor(JADE)
    c.circle(202, 620, 5, fill=1, stroke=0)
    text(c, 196, 636, "P", 9, True)
    option_row(c, 64, 558, ["A. -7", "B. -3", "C. 3", "D. 7"])
    divider(c, 528)

    text(c, 44, 495, "10. 观察下表中的规律，填写第 5 列空格。", 10.5, True)
    c.setStrokeColor(GRID)
    cells = [["n", "1", "2", "3", "4", "5"], ["2n+1", "3", "5", "7", "9", "____"]]
    x0, y0, w, h = 78, 446, 72, 30
    for r, row in enumerate(cells):
        for col, value in enumerate(row):
            c.setFillColor(colors.HexColor("#E4F2EC") if col == 0 else colors.white)
            c.rect(x0 + col * w, y0 - r * h, w, h, fill=1, stroke=1)
            c.setFillColor(INK)
            c.setFont("DemoSansBold" if col == 0 else "DemoSans", 9)
            c.drawCentredString(x0 + col * w + w / 2, y0 - r * h + 10, value)
    divider(c, 366)

    text(c, 44, 332, "11. 如图，函数 y=x²-4 的图像与 x 轴交于 A、B 两点。", 10.4, True)
    text(c, 64, 308, "求线段 AB 的长度。", 10.2)
    axes(c, 336, 190, 190, 108)
    c.setStrokeColor(JADE)
    c.setLineWidth(2.2)
    p = c.beginPath()
    for i in range(41):
        xv = -3 + i * 0.15
        yv = xv * xv - 4
        px = 431 + xv * 28
        py = 244 + yv * 9
        if i == 0:
            p.moveTo(px, py)
        else:
            p.lineTo(px, py)
    c.drawPath(p, stroke=1, fill=0)
    divider(c, 168)

    text(c, 44, 136, "12. 已知 a+b=10，ab=21。求 a²+b²，并说明所使用的恒等式。", 10.5, True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="tiyouju-demo-font-") as temp:
        register_fonts(Path(temp))
        c = canvas.Canvas(str(OUTPUT), pagesize=A4, pageCompression=1)
        c.setTitle("题有据功能演示卷")
        c.setAuthor("题有据开源项目")
        c.setSubject("用于题有据公开截图的原创演示数据")
        for draw in (page_one, page_two, page_three):
            draw(c)
            c.showPage()
        c.save()


if __name__ == "__main__":
    main()
