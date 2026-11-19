"""导出 Word 的表格：**视觉样式**要跟打印预览一模一样。

预览（frontend/styles.css:1689-1691）：
  .qb-table { border-collapse: collapse; margin: 0 auto; white-space: normal; line-height: 1.6 }
  .qb-table td, .qb-table th { min-width: 2.6em; height: 2.1em; padding: .2em .7em;
      border: 1px solid var(--paper-ink); text-align: center; vertical-align: middle;
      font-weight: 400; overflow-wrap: normal; word-break: keep-all; white-space: nowrap }

而 ``--paper-ink: #161816``（styles.css:17）。

Chrome 实测（_probe82.py，178 毫米打印容器）：

  · 每一格四边：``1px solid rgb(22, 24, 22)`` —— 1 像素 = 0.75 磅，颜色 #161816；
  · 表头格底色：``rgba(0, 0, 0, 0)`` —— **没有底色**；
  · 表头格字重：``400`` —— **不加粗**（th 和 td 同一条规则）。

Word 这边三处都不一样：

  · 用的是 Table Grid 样式的边框：0.5 磅（sz=4）纯黑 auto；
  · 表头涂了 F1F5F3 浅绿底；
  · 表头加粗。

导出来的卷子里，表格是细黑框、表头一块浅绿底、粗体字 —— 跟老师核对的那张
预览纸完全不是一回事。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "22222222-2222-4222-8222-222222222222"

# Chrome 实测的预览值（独立字面值，不引用被测模块的常量）。
PREVIEW_BORDER_COLOR = "161816"
PREVIEW_BORDER_WIDTH_PX = 1
# 1 CSS 像素 = 1/96 英寸 = 0.75 磅；w:sz 的单位是 1/8 磅 → 0.75 × 8 = 6。
PREVIEW_BORDER_SZ = 6
PREVIEW_HEADER_SHADING = None  # 预览里表头没有底色

NARROW_TABLE = "| x | y |\n| --- | --- |\n| 1 | 2 |"
SIDES = ("top", "left", "bottom", "right", "insideH", "insideV")


def table_rows(markdown, where="第 1 题"):
    """把一段 Markdown 表格拆成导出用的行（和真导出走同一个解析器）。"""
    from . import library_export

    rows = library_export._table_source(markdown, where)
    return [[{**cell, "segments": [{"type": "text", "raw": cell["source"]}]} for cell in row] for row in rows]


def build_document(markdown, font_size=12):
    """走**真实导出路径**造一份 Word：题干里放一张表。"""
    from . import library_export

    item = {
        "id": QUESTION,
        "type": "single_choice",
        "where": "第 1 题",
        "content": {"options": {}, "body_mode": "text"},
        "fields": {"stem": [{"type": "table", "rows": table_rows(markdown)}]},
        "images": [],
    }
    options = {
        "font_size": font_size,
        "student_info": False,
        "answer_space": "none",
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
    }
    data = library_export._document([item], "第五十九轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def borders(table):
    """表级 w:tblBorders 的每一边：{边: (val, sz, color)}。"""
    properties = table._tbl.tblPr
    node = properties.find(qn("w:tblBorders"))
    assert node is not None, "尺子自检：表里没有 w:tblBorders"
    result = {}
    for side in SIDES:
        edge = node.find(qn("w:" + side))
        if edge is None:
            result[side] = None
            continue
        result[side] = (edge.get(qn("w:val")), edge.get(qn("w:sz")), edge.get(qn("w:color")))
    return result


def cell_shading(cell):
    """格子自己的底色 w:shd 的 fill；没有就是 None。"""
    properties = cell._tc.find(qn("w:tcPr"))
    if properties is None:
        return None
    node = properties.find(qn("w:shd"))
    if node is None:
        return None
    return node.get(qn("w:fill"))


def run_bold(run):
    """run 是不是粗体；没写 w:b 就是继承（预览那边继承下来是 400）。"""
    properties = run._element.find(qn("w:rPr"))
    if properties is None:
        return False
    node = properties.find(qn("w:b"))
    if node is None:
        return False
    return node.get(qn("w:val")) not in ("0", "false")


class PreviewMeasurementTests(SimpleTestCase):
    """尺子自检：预览的 1 像素边框换算成 Word 的 w:sz 就是 6。"""

    def test_one_pixel_is_six_eighths_of_a_point(self):
        """1 CSS 像素 = 0.75 磅，w:sz 单位是 1/8 磅 → 6。"""
        self.assertEqual(PREVIEW_BORDER_WIDTH_PX * 0.75 * 8, PREVIEW_BORDER_SZ)


class TableBorderTests(SimpleTestCase):
    """表格的边框：0.75 磅、#161816、六边齐全。"""

    def test_every_side_uses_the_preview_border(self):
        """六边（外框四条 + 内部两条）都得是 single / sz 6 / #161816。"""
        table = build_document(NARROW_TABLE).tables[0]
        for side, value in borders(table).items():
            self.assertIsNotNone(value, f"{side} 边没写")
            val, size, color = value
            self.assertEqual(val, "single", f"{side} 边的线型是 {val!r}，预览是实线")
            self.assertEqual(size, str(PREVIEW_BORDER_SZ), f"{side} 边的线宽是 {size}，预览实测 0.75 磅")
            self.assertEqual(color, PREVIEW_BORDER_COLOR, f"{side} 边的颜色是 {color}，预览实测 {PREVIEW_BORDER_COLOR}")


class HeaderStyleTests(SimpleTestCase):
    """表头：不加底色、不加粗。"""

    def test_the_header_row_has_no_shading(self):
        """预览里表头格底色是透明的（rgba(0, 0, 0, 0)），Word 这边别涂底。"""
        table = build_document(NARROW_TABLE).tables[0]
        for cell_index, cell in enumerate(table.rows[0].cells):
            self.assertIsNone(
                cell_shading(cell), f"表头第 {cell_index + 1} 格涂了底色 {cell_shading(cell)}，预览实测没有底色"
            )

    def test_the_header_text_is_not_bold(self):
        """预览里 th 和 td 同一条规则，font-weight: 400 —— 表头不加粗。"""
        table = build_document(NARROW_TABLE).tables[0]
        for cell_index, cell in enumerate(table.rows[0].cells):
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    self.assertFalse(run_bold(run), f"表头第 {cell_index + 1} 格的字是粗体，预览实测字重 400")

    def test_the_body_text_is_not_bold_either(self):
        """数据行也不加粗（以前只有表头被加粗过）。"""
        table = build_document(NARROW_TABLE).tables[0]
        for cell in table.rows[1].cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    self.assertFalse(run_bold(run), "数据行的字是粗体，预览实测字重 400")
