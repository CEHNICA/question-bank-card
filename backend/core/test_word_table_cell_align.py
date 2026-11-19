"""导出 Word 的表格单元格：文字的**水平对齐**必须和打印预览一样。

预览里 ``.qb-table td, .qb-table th { text-align: center }``
（frontend/styles.css:1690）—— 表头和数据行的文字**全是居中**的。
Chrome 实测（_probe80.py，178 毫米打印容器）：

  · 普通表 | x | y | 的表头格：内容盒 [7.751, 12.829]，文字 [9.054, 11.435]，
    左空 1.303 / 右空 1.394 毫米 → 居中；
  · 数据格 '1'：左空 1.481 / 右空 1.481 毫米 → 居中；
  · dense 表（13 列）每一格：左空 0.129 / 右空 0.129 毫米 → 居中。

Word 这边**从不设单元格段落的对齐**，落到 Word 的默认（左）。窄表那一列的内容盒
只有 4.97 毫米，'x' 宽 2.38 毫米：居中时左边空 1.30 毫米，左对齐时只空 0.13
毫米 —— 纸上差 1.17 毫米，肉眼能看出来「表格里的字没对齐」。

注意这跟**表格本身**的位置是两回事：表格整体的 w:jc 是 left（题干表贴在题号
右边、答案表在正文列里居中），管的是**表格框**在页面上的位置；单元格里的
w:jc 管的是**文字**在格子里的位置。两者别混。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "22222222-2222-4222-8222-222222222222"

# 预览 .qb-table td, .qb-table th { text-align: center } —— 独立字面值。
PREVIEW_CELL_ALIGNMENT = "center"
# 表格整体在页面上的对齐（题干表靠左贴题号列）。
PREVIEW_TABLE_ALIGNMENT = "left"

NARROW_TABLE = "| x | y |\n| --- | --- |\n| 1 | 2 |"
TWELVE_NUMBERS = " | ".join(str(index) for index in range(1, 13))
DENSE_TABLE = (
    f"| 零售收入/万元 | {TWELVE_NUMBERS} |\n| --- | " + " | ".join("---" for _ in range(12)) + " |\n"
    f"| 合计 | {TWELVE_NUMBERS} |"
)


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
    data = library_export._document([item], "第五十八轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def paragraph_alignment(paragraph):
    """段落的对齐方式；没写 w:jc 就是 Word 的默认（左）。"""
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return None
    alignment = properties.find(qn("w:jc"))
    if alignment is None:
        return None
    return alignment.get(qn("w:val"))


class CellAlignmentTests(SimpleTestCase):
    """表格里每一格的文字都得居中。"""

    def test_the_header_cells_are_centred(self):
        """表头格（第一行）里的字得居中。

        预览里 th 和 td 是同一条规则（``.qb-table td, .qb-table th
        { text-align: center }``），表头不居中的话，表头和下面的数就错开一列。
        """
        table = build_document(NARROW_TABLE).tables[0]
        for cell_index, cell in enumerate(table.rows[0].cells):
            for paragraph_index, paragraph in enumerate(cell.paragraphs):
                self.assertEqual(
                    paragraph_alignment(paragraph),
                    PREVIEW_CELL_ALIGNMENT,
                    f"表头第 {cell_index + 1} 格第 {paragraph_index + 1} 段的对齐是 "
                    f"{paragraph_alignment(paragraph)!r}，预览实测 {PREVIEW_CELL_ALIGNMENT}",
                )

    def test_the_body_cells_are_centred(self):
        """数据格里的字也得居中。"""
        table = build_document(NARROW_TABLE).tables[0]
        self.assertGreater(len(table.rows), 1, "尺子自检：这张表没有数据行")
        for cell_index, cell in enumerate(table.rows[1].cells):
            for paragraph_index, paragraph in enumerate(cell.paragraphs):
                self.assertEqual(
                    paragraph_alignment(paragraph),
                    PREVIEW_CELL_ALIGNMENT,
                    f"数据行第 {cell_index + 1} 格第 {paragraph_index + 1} 段的对齐是 "
                    f"{paragraph_alignment(paragraph)!r}，预览实测 {PREVIEW_CELL_ALIGNMENT}",
                )

    def test_a_dense_table_is_centred_too(self):
        """dense 表（>= 13 列）同样居中 —— 规则跟表宽无关。"""
        table = build_document(DENSE_TABLE).tables[0]
        self.assertEqual(len(table.columns), 13, "尺子自检：这张表不是 13 列")
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                for paragraph in cell.paragraphs:
                    self.assertEqual(
                        paragraph_alignment(paragraph),
                        PREVIEW_CELL_ALIGNMENT,
                        f"dense 表第 {row_index + 1} 行第 {cell_index + 1} 格没居中",
                    )

    def test_the_alignment_survives_a_bigger_font(self):
        """14 磅也一样 —— 对齐跟字号无关。"""
        table = build_document(NARROW_TABLE, font_size=14).tables[0]
        for cell in table.rows[0].cells:
            for paragraph in cell.paragraphs:
                self.assertEqual(paragraph_alignment(paragraph), PREVIEW_CELL_ALIGNMENT)


class TableAlignmentTests(SimpleTestCase):
    """单元格里的字居中，不等于表格整体也居中 —— 这两个别混。"""

    def test_the_table_itself_stays_left(self):
        """表格整体仍然靠左（题干表贴在题号右边那一列）。"""
        table = build_document(NARROW_TABLE).tables[0]
        properties = table._tbl.tblPr
        alignment = properties.find(qn("w:jc"))
        self.assertIsNotNone(alignment, "尺子自检：表里没有 w:jc")
        self.assertEqual(alignment.get(qn("w:val")), PREVIEW_TABLE_ALIGNMENT)
