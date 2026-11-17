"""题干里的表格题，列宽必须真的落到 Word 认的地方。

实测（第四十二轮，Word 16 真渲染）：python-docx 的 ``column.width`` 只改
``w:gridCol``，每个单元格原有的 ``w:tcW`` 原地不动 —— 两个值对不上的时候，
Word 不按你写的排，而是自己重排成等宽。

我们写的 30/20/15 毫米的表，Word 认成 50.8×3（等宽，总宽 6 英寸
152.4 毫米），预览里按内容分配的列宽全丢了；同一批表在 PDF 里也是等宽的，
和预览对不上。

三个变体实测（_probe42.py）：
  只改 gridCol            -> Word 认 50.8×3（152.4 毫米）
  加 w:tblW               -> 总宽对了（65 毫米），但仍均分 21.68/21.66/21.66
  加 w:tblW + w:tcW       -> Word 认 30.0 / 20.0 / 14.99，一字不差

所以这三样都得写，而且顺序有硬要求（w:tblW 排在 w:jc 前，w:tcW 排在
w:gridSpan 前），否则 Word 打开时会自己重排。
"""

from django.test import SimpleTestCase
from docx import Document
from docx.oxml.ns import qn

from . import library_export

MM_PER_TWIP = 25.4 / 1440


def cell(text, header=False, colspan=1, rowspan=1):
    return {"rowspan": rowspan, "colspan": colspan, "header": header, "segments": [{"type": "text", "raw": text}]}


def build(rows):
    document = Document()
    library_export._write_table(document, rows, 12, "表格宽度体检")
    return document.tables[-1]


def column_widths_mm(table):
    """我们写进 w:gridCol 的列宽。"""
    return [column.width / 36000 for column in table.columns]


def table_width_mm(table):
    element = table._tbl.tblPr.find(qn("w:tblW"))
    assert element is not None, "表格没有写 w:tblW，Word 会自己重排"
    return int(element.get(qn("w:w"))) * MM_PER_TWIP


def row_cells_mm(table):
    """每行每个单元格的 (tcW 毫米, 跨了几列)。

    没写 w:tcW 就给 None —— 由「每格都要写宽度」那条去喊。
    这里直接 assert 的话，「没写 tcW」会同时把好几条测试打红，
    看门狗就分不清到底是哪一处坏了。
    """
    result = []
    for row in table._tbl.tr_lst:
        cells = []
        for tc in row.tc_lst:
            properties = tc.find(qn("w:tcPr"))
            span = 1
            width = None
            if properties is not None:
                span_element = properties.find(qn("w:gridSpan"))
                if span_element is not None:
                    span = int(span_element.get(qn("w:val")))
                width_element = properties.find(qn("w:tcW"))
                if width_element is not None:
                    width = int(width_element.get(qn("w:w"))) * MM_PER_TWIP
            cells.append((width, span))
        result.append(cells)
    return result


def uneven_rows():
    """列宽一定不等的三列表：靠内容长度拉开差距。"""
    return [
        [cell("表头一", True), cell("表头二", True), cell("表头三", True)],
        [cell("短"), cell("中等长度的内容"), cell("很长很长很长很长很长很长的一段文字")],
    ]


def option_item():
    """一道四个选项的题，够选项紧凑表用。"""
    values = {"A": "3", "B": "4", "C": "5", "D": "6"}
    fields = {
        f"options.{letter}": [{"type": "text", "segments": [{"type": "text", "raw": values[letter]}]}]
        for letter in values
    }
    return {"id": 1, "content": {"options": values}, "fields": fields, "images": []}


def build_options(columns):
    """造一张选项紧凑表（两栏 / 四栏排选项的那张）。"""
    document = Document()
    library_export._page_setup(document)
    library_export._write_compact_options(document, option_item(), 12, columns)
    return document.tables[-1]


class TableWidthTests(SimpleTestCase):
    def test_the_table_declares_its_total_width(self):
        """w:tblW 必须写，且等于各列宽之和。"""
        table = build(uneven_rows())
        widths = column_widths_mm(table)
        self.assertAlmostEqual(table_width_mm(table), sum(widths), places=1)

    def test_every_cell_declares_its_width(self):
        """每个单元格都要写 w:tcW，值就是它那一列的宽度。"""
        table = build(uneven_rows())
        widths = column_widths_mm(table)
        for row in row_cells_mm(table):
            self.assertEqual(len(row), len(widths))
            for (value, span), want in zip(row, widths):
                self.assertIsNotNone(value, "有单元格没写 w:tcW，Word 会自己均分这一列")
                self.assertEqual(span, 1)
                self.assertAlmostEqual(value, want, places=1)

    def test_the_columns_keep_their_own_widths(self):
        """列宽必须各不一样 —— 一样就说明又被均分了。"""
        table = build(uneven_rows())
        widths = [round(value, 2) for value in column_widths_mm(table)]
        self.assertGreater(len(set(widths)), 1, f"三列宽成了同一个数：{widths}")

    def test_a_merged_cell_spans_the_width_of_its_columns(self):
        """跨两列的单元格，宽度是那两列之和。"""
        table = build([[cell("跨两列", colspan=2), cell("右边")]])
        widths = column_widths_mm(table)
        cells = row_cells_mm(table)[0]
        self.assertEqual(cells[0][1], 2, "跨列信息没写进 w:gridSpan")
        if cells[0][0] is None:
            # 压根没写 w:tcW 是「每格都要写宽度」那条的活儿，这里不重复喊。
            return
        self.assertAlmostEqual(cells[0][0], widths[0] + widths[1], places=1)

    def test_the_elements_sit_in_the_order_word_expects(self):
        """w:tblW 必须在 w:jc 前，w:tcW 必须在 w:gridSpan 前。

        这里只管**顺序**。「压根没写 w:tblW / w:tcW」是上面两条的活儿
        （总宽那条、每格那条）—— 一条缺陷只该打红一条，看门狗才分得清
        到底是哪一处坏了。所以元素不在的时候这条不发作。
        """
        table = build([[cell("跨两列", colspan=2), cell("右边")]])
        tbl_pr = table._tbl.tblPr
        order = [child.tag for child in tbl_pr]
        if qn("w:tblW") in order and qn("w:jc") in order:
            self.assertLess(order.index(qn("w:tblW")), order.index(qn("w:jc")))
        tc_pr = table._tbl.tr_lst[0].tc_lst[0].find(qn("w:tcPr"))
        cell_order = [child.tag for child in tc_pr]
        if qn("w:tcW") in cell_order and qn("w:gridSpan") in cell_order:
            self.assertLess(cell_order.index(qn("w:tcW")), cell_order.index(qn("w:gridSpan")))


class CompactOptionsWidthTests(SimpleTestCase):
    """选项紧凑表：w:gridCol 和 w:tcW 必须一致。

    python-docx 建表时，每格的 w:tcW 是按正文宽平均分的默认值；``column.width``
    只改 w:gridCol。选项紧凑表写进去的列宽恰好等于那个默认值（正文宽 ÷ 栏数），
    所以两边没打架、Word 才排得对 —— 差 0.01 毫米 Word 就自己重排了。

    这条守的就是这个「恰好一致」：正文净宽改了、栏数改了、或者谁把
    ``column.width`` 写偏了，这里立刻叫。
    """

    def test_the_options_table_keeps_the_grid_and_the_cells_in_step(self):
        for columns in (2, 4):
            table = build_options(columns)
            grid = [column.width / 36000 for column in table.columns]
            for row in row_cells_mm(table):
                for (value, _span), want in zip(row, grid):
                    self.assertIsNotNone(value, "有单元格没写 w:tcW")
                    self.assertAlmostEqual(value, want, places=1)

    def test_the_options_table_fills_the_text_column(self):
        """每列 = 正文净宽 ÷ 栏数，表正好铺满正文。"""
        for columns in (2, 4):
            table = build_options(columns)
            document = table._parent.part.document
            section = document.sections[0]
            text_width = (section.page_width - section.left_margin - section.right_margin) / 36000
            grid = [column.width / 36000 for column in table.columns]
            for want in grid:
                self.assertAlmostEqual(want, text_width / columns, places=1)
