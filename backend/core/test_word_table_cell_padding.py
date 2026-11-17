"""导出 Word 的**单元格左右内边距**必须和打印预览一模一样。

这一轮查出来的是：**Word 用的是它自己的默认单元格边距**。

预览里普通表左右各 .7em（12 磅 = 2.963 毫米 = 168 twips），Word 的 Table Grid
默认每格留 108 twips（1.905 毫米）—— 每侧差 60 twips，格子里的内容盒比预览
**宽 2.1 毫米**：

  · 预览（Chrome 实测，_probe72.py）：11.005 毫米那一列，内容盒 4.820 毫米；
  · Word（真 Word 16 实测，_probe76.py）：内容盒 = 列宽 − 2×边距 − 0.3pt
    = 11.005 − 2×1.905 − 0.106 = 7.09 毫米。

格子里的字因此折行位置跟预览对不上 —— 屏幕上是一行，导出来折成两行。

Word 那三条实测规律（_probe76.py：八张 40 毫米的表，w:tblCellMar 的左右从 0
一路排到 216 twips，每格放左对齐/居中/右对齐三段探针，导 PDF 量原始 pt）：

  · 第一格**文字左缘** = 版心左缘 + tblInd + 0.3pt，**与单元格边距无关**
    （tblInd=0 时八张表的左对齐那一段 x0 恒为 45.650pt，一动不动）；
  · 表格**边框中心** = 版心左缘 + tblInd − 单元格左边距；
  · **内容盒宽度** = 列宽 − 2×单元格左边距 − 0.3pt。

所以写 tblInd 时补的那个数，必须是**这张表实际写进去的边距**（普通表 168、
dense 表 108），不是 Word 默认的 108 —— 补错了表就整体偏出去。

这里的判据一律用**独立字面值**（Chrome 实测换算出来的 twips），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "22222222-2222-4222-8222-222222222222"

# Chrome 实测的预览几何（毫米，178 毫米打印容器，12 磅正文）。
PREVIEW_PADDING_MM = 2.963  # 普通表 .7em
PREVIEW_DENSE_PADDING_MM = 1.905  # dense 表（>= 13 列）.45em
PREVIEW_COLUMN_MM = 11.005  # 窄表每列的边框盒宽度
PREVIEW_CONTENT_WIDTH_MM = 4.820  # 那一列里的内容盒宽度
# 真 Word 16 实测：内容盒宽度 = 列宽 − 2×单元格边距 − 0.3pt（0.3pt = 0.106 毫米）。
WORD_BORDER_HALF_MM = 0.106
# 预览里题干正文列的左缘（实测 4.655 毫米 = 264 twips）。
STEM_LEFT_TWIPS = 264

NARROW_TABLE = "| x | y |\n| --- | --- |\n| 1 | 2 |"
TWELVE_NUMBERS = " | ".join(str(index) for index in range(1, 13))
DENSE_TABLE = f"| 零售收入/万元 | {TWELVE_NUMBERS} |\n| --- | " + " | ".join("---" for _ in range(12)) + " |"


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
    data = library_export._document([item], "第五十七轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def cell_margins(table):
    """表级 w:tblCellMar 的四边（twips 字符串）。"""
    margins = table._tbl.tblPr.find(qn("w:tblCellMar"))
    assert margins is not None, "尺子自检：表里没有 w:tblCellMar"
    values = {}
    for side in ("top", "left", "bottom", "right"):
        node = margins.find(qn("w:" + side))
        values[side] = node.get(qn("w:w")) if node is not None else None
    return values


def cell_margins_of_first_cell(table):
    """第一格的 w:tcMar（twips 字符串）。"""
    tc_pr = table.rows[0].cells[0]._tc.find(qn("w:tcPr"))
    assert tc_pr is not None, "尺子自检：格子里没有 w:tcPr"
    margins = tc_pr.find(qn("w:tcMar"))
    assert margins is not None, "尺子自检：格子里没有 w:tcMar"
    values = {}
    for side in ("top", "left", "bottom", "right"):
        node = margins.find(qn("w:" + side))
        values[side] = node.get(qn("w:w")) if node is not None else None
    return values


def indent_twips(table):
    element = table._tbl.tblPr.find(qn("w:tblInd"))
    assert element is not None, "尺子自检：表里没有 w:tblInd"
    return int(element.get(qn("w:w")))


def grid_columns_mm(table):
    grid = table._tbl.find(qn("w:tblGrid"))
    assert grid is not None, "尺子自检：表里没有 w:tblGrid"
    return [int(node.get(qn("w:w"))) * 25.4 / 1440 for node in grid.findall(qn("w:gridCol"))]


def effective_padding_twips(table):
    """这一格**实际生效**的左右边距：格级 w:tcMar 优先，缺项回退表级 w:tblCellMar。

    这跟 Word 的继承规则一致：格级没写的边继承表级声明。
    """
    own = cell_margins_of_first_cell(table)
    if own["left"] is not None:
        return int(own["left"])
    fallback = cell_margins(table)
    assert fallback["left"] is not None, "尺子自检：格级和表级都没写左边距"
    return int(fallback["left"])


class PreviewPaddingMeasurementTests(SimpleTestCase):
    """尺子自检：Chrome 实测的毫米值换算成 twips 就是下面那几个数。"""

    def test_the_preview_padding_is_168_twips(self):
        """普通表 .7em = 2.963 毫米 = 168 twips（12 磅）。"""
        self.assertEqual(round(PREVIEW_PADDING_MM * 1440 / 25.4), 168)

    def test_the_dense_padding_is_108_twips(self):
        """dense 表 .45em = 1.905 毫米 = 108 twips（12 磅）。"""
        self.assertEqual(round(PREVIEW_DENSE_PADDING_MM * 1440 / 25.4), 108)


class ExportedCellPaddingTests(SimpleTestCase):
    """导出的 docx 里，单元格的左右内边距必须是预览那两个数。"""

    def test_a_normal_table_writes_the_preview_padding(self):
        """普通表左右各 168 twips —— 不是 Word 默认的 108。

        每侧差 60 twips（1.058 毫米），格子里的内容盒就宽了 2.1 毫米，
        折行位置跟预览对不上。
        """
        table = build_document(NARROW_TABLE).tables[0]
        margins = cell_margins(table)
        self.assertEqual(margins["left"], "168", f"表级左边距 {margins['left']}，预览实测 168")
        self.assertEqual(margins["right"], "168", f"表级右边距 {margins['right']}，预览实测 168")

    def test_a_normal_table_leaves_no_vertical_padding_at_the_table_level(self):
        """上下留白是每格的 w:tcMar（.2em），表级这一层是 0。"""
        table = build_document(NARROW_TABLE).tables[0]
        margins = cell_margins(table)
        self.assertEqual(margins["top"], "0")
        self.assertEqual(margins["bottom"], "0")

    def test_every_cell_writes_the_padding_too(self):
        """每一格自己的 w:tcMar 里也要有左右内边距。

        Word 对「只写了上下的 tcMar」不一定去继承表级的 w:tblCellMar ——
        写死最保险。
        """
        table = build_document(NARROW_TABLE).tables[0]
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                margins = cell_margins_of_first_cell(table) if (row_index, cell_index) == (0, 0) else None
                if margins is None:
                    tc_pr = cell._tc.find(qn("w:tcPr"))
                    node = tc_pr.find(qn("w:tcMar")) if tc_pr is not None else None
                    assert node is not None, f"尺子自检：第 {row_index + 1} 行第 {cell_index + 1} 格没有 w:tcMar"
                    margins = {
                        side: (
                            node.find(qn("w:" + side)).get(qn("w:w"))
                            if node.find(qn("w:" + side)) is not None
                            else None
                        )
                        for side in ("top", "left", "bottom", "right")
                    }
                self.assertEqual(
                    margins["left"],
                    "168",
                    f"第 {row_index + 1} 行第 {cell_index + 1} 格的左边距 {margins['left']}，预览实测 168",
                )
                self.assertEqual(
                    margins["right"],
                    "168",
                    f"第 {row_index + 1} 行第 {cell_index + 1} 格的右边距 {margins['right']}，预览实测 168",
                )

    def test_a_dense_table_writes_the_narrower_padding(self):
        """dense 表（>= 13 列）左右各 108 twips = .45em。

        预览里 dense 表的内边距收窄（frontend/library.css 的
        ``.print-flow .qb-table-dense td { padding: .15em .45em }``），
        Word 这边不跟着收，列里的字就挤不下。
        """
        table = build_document(DENSE_TABLE).tables[0]
        self.assertEqual(len(table.columns), 13, "尺子自检：这张表不是 13 列")
        margins = cell_margins(table)
        self.assertEqual(margins["left"], "108", f"表级左边距 {margins['left']}，预览实测 108")
        self.assertEqual(margins["right"], "108", f"表级右边距 {margins['right']}，预览实测 108")

    def test_the_padding_follows_the_font_size(self):
        """字号变了，em 跟着变：14 磅时普通表 196 twips、dense 表 126 twips。"""
        normal = build_document(NARROW_TABLE, font_size=14).tables[0]
        self.assertEqual(cell_margins(normal)["left"], "196")
        dense = build_document(DENSE_TABLE, font_size=14).tables[0]
        self.assertEqual(cell_margins(dense)["left"], "126")


class IndentCompensationTests(SimpleTestCase):
    """写 tblInd 时补的那个数，必须是这张表实际写进去的边距。"""

    def test_the_indent_carries_the_padding_that_was_written(self):
        """普通表：264（题干正文列左缘）+ 168（它的单元格边距）= 432 twips。

        补的是**这张表的**边距。补成 Word 默认的 108，表就整体偏出去
        （第一格文字会离开它该在的位置 60 twips）。
        """
        table = build_document(NARROW_TABLE).tables[0]
        self.assertEqual(indent_twips(table), STEM_LEFT_TWIPS + 168)

    def test_a_dense_table_compensates_its_own_padding(self):
        """dense 表：264 + 108 = 372 twips（它的边距是 .45em，不是 .7em）。"""
        table = build_document(DENSE_TABLE).tables[0]
        self.assertEqual(indent_twips(table), STEM_LEFT_TWIPS + 108)


class ContentBoxTests(SimpleTestCase):
    """格子里的**内容盒**（字能站的地方）得跟预览一样宽。"""

    def test_the_content_box_is_as_wide_as_the_preview(self):
        """Word 的内容盒 = 列宽 − 2×单元格边距 − 0.3pt。

        11.005 毫米那一列：预览内容盒 4.820 毫米，Word 用 168 twips 之后是
        11.005 − 2×2.963 − 0.106 = 4.972 毫米。

        差的 0.15 毫米来自**边框骑线**：浏览器的折叠边框把整条边框算进内容盒，
        Word 只算半条。这 0.15 毫米落不进任何一种实际字宽（一个汉字 4.233 毫米、
        一个大写字母 3.048 毫米），不会改变折行 —— 但 Word 原来用 108 twips 时
        内容盒有 7.09 毫米（宽 2.27 毫米），那才会让格子里的字少折一行。

        边距必须**从导出的 docx 里读**：写死 168 的话，这个测试就只是在验自己
        的算术，实现改坏了它照样绿（这正是尺子最该防的事）。

        生效的边距是「格级 w:tcMar 的左右，缺项回退表级 w:tblCellMar」——
        这跟 Word 的继承规则一致（格级没写的边继承表级）。
        """
        table = build_document(NARROW_TABLE).tables[0]
        columns = grid_columns_mm(table)
        self.assertAlmostEqual(columns[0], PREVIEW_COLUMN_MM, delta=0.05, msg="尺子自检：列宽不是预览那个")
        effective = effective_padding_twips(table)
        padding_mm = effective * 25.4 / 1440
        content_mm = columns[0] - 2 * padding_mm - WORD_BORDER_HALF_MM
        self.assertAlmostEqual(
            content_mm,
            PREVIEW_CONTENT_WIDTH_MM,
            delta=0.2,
            msg=f"Word 的内容盒 {content_mm:.3f} 毫米，预览实测 {PREVIEW_CONTENT_WIDTH_MM}",
        )
