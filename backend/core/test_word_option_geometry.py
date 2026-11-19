"""导出 Word 的选项块，水平位置必须照抄打印预览。

Chrome 实测（2026-11-17，``_probe54.py``，178 毫米打印容器，模拟
``exam-layout.js`` 的内联 ``gridTemplateColumns`` 覆盖）：

  · 选项区左边留 2em = 8.4667 毫米（``.qb-options`` 的 ``padding-left``）
  · 栏与栏之间留 2em = 8.4667 毫米（``column-gap``）
  · 标签和正文之间留 0.3em = 1.27 毫米（``.qb-option`` 的 ``column-gap``）
  · 标签列是 ``auto``：「A.」4.117 毫米、「B.」3.882 毫米 —— 所以正文列
    左缘不一样（4 栏时 A 13.853 / B 58.117），正文列宽也不一样
    （A 30.646 / B 30.882）

Word 这边原来一张表平均分栏（4 栏时每栏 44.5 毫米）、整张表贴在版心左缘：
纸上选项整体比预览靠左 8.47 毫米，第二栏又整整偏左 8.47 毫米，正文列
也宽出一大截 —— 折行位置跟预览完全对不上。

现在照抄预览：``w:tblInd`` 给左缩进；栏间间隙算进左栏宽度、用段落右缩进
让出来（Word 的表格没有列间距这一说）；每格内边距清零（Word 默认 108
twips = 1.905 毫米，预览的选项栏两侧不留白）；标签用悬挂缩进 + 制表位把
正文推到预览里的那个位置。

这里的判据一律用**独立字面值**（Chrome 实测值），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "11111111-1111-4111-8111-111111111111"

TEXT_WIDTH_MM = 178.0

# Chrome 实测的预览几何（毫米）。
LIST_INDENT_MM = 8.4667  # 2em
COLUMN_GAP_MM = 8.4667  # 2em
LABEL_GAP_MM = 1.27  # 0.3em
# 正文列的左缘离栏左缘多远 = 标签宽 + 标签与正文之间的间隙。
LABEL_OFFSET_MM = {"A": 5.386, "B": 5.151, "C": 5.151, "D": 5.386, "E": 4.953}
# 正文列有多宽（4 栏时）。
BODY_WIDTH_MM = {"A": 30.646, "B": 30.882, "C": 30.882, "D": 30.646, "E": 30.882}


def twips(mm):
    return int(round(mm * 1440 / 25.4))


def png_bytes(width=1000, height=200):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def option_item(letters="ABCD", text="选项正文", images=()):
    fields = {
        f"options.{letter}": [{"type": "text", "segments": [{"type": "text", "raw": text}]}] for letter in letters
    }
    return {
        "id": QUESTION,
        "fields": fields,
        "images": [{"slot": slot, "bytes": png_bytes(), "size": (1000, 200)} for slot in images],
    }


def build_options(columns=4, item=None, measured=None):
    from docx import Document

    from . import library_export

    document = Document()
    figures = {"measured": measured} if measured is not None else {}
    library_export._write_compact_options(
        document, item or option_item(), 12, columns, text_width_mm=TEXT_WIDTH_MM, figures=figures
    )
    return document


def grid_widths_mm(table):
    return [column.width / 36000.0 for column in table.columns]


def table_indent_twips(table):
    element = table._tbl.tblPr.find(qn("w:tblInd"))
    assert element is not None, "选项表没写 w:tblInd，整块选项会贴在版心左缘"
    return int(element.get(qn("w:w")))


def cell_margins_twips(cell):
    """一格的四个内边距（twips）；没写 w:tcMar 就给 None。"""
    properties = cell._tc.find(qn("w:tcPr"))
    if properties is None:
        return None
    margins = properties.find(qn("w:tcMar"))
    if margins is None:
        return None
    return {side: int(margins.find(qn("w:" + side)).get(qn("w:w"))) for side in ("top", "left", "bottom", "right")}


def paragraph_indents_mm(paragraph):
    """段落的 (左缩进, 悬挂缩进, 右缩进, 制表位)，单位毫米。

    没有 ``w:ind`` 就当全是 0 —— 别在「这个元素存不存在」上和别的判据混在
    一条里：那样一条缺陷会同时打红两条守护，看门狗就分不清坏的是哪一处。
    """
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return 0.0, 0.0, 0.0, []
    indents = properties.find(qn("w:ind"))
    if indents is None:
        return 0.0, 0.0, 0.0, []
    left = int(indents.get(qn("w:left"), "0")) * 25.4 / 1440
    hanging = int(indents.get(qn("w:hanging"), "0")) * 25.4 / 1440
    right = int(indents.get(qn("w:right"), "0")) * 25.4 / 1440
    stops = []
    tabs = properties.find(qn("w:tabs"))
    if tabs is not None:
        stops = [int(stop.get(qn("w:pos"))) * 25.4 / 1440 for stop in tabs.findall(qn("w:tab"))]
    return left, hanging, right, stops


def picture_widths_mm(document):
    """文档里每张图的宽（毫米），按文档顺序。"""
    found = []
    for drawing in document.element.body.iter(qn("w:drawing")):
        extent = next(drawing.iter(qn("wp:extent")), None)
        if extent is not None:
            found.append(int(extent.get("cx")) / 36000.0)
    return found


class OptionGeometryTests(SimpleTestCase):
    def test_the_options_block_starts_two_em_from_the_text_edge(self):
        """选项区左边留 2em —— 预览的 .qb-options padding-left。"""
        for columns in (2, 4):
            table = build_options(columns).tables[0]
            self.assertAlmostEqual(
                table_indent_twips(table), twips(LIST_INDENT_MM), delta=1, msg=f"{columns} 栏的选项区没有按 2em 缩进"
            )

    def test_each_column_is_as_wide_as_the_preview_column(self):
        """栏宽 = (正文宽 − 2em − 栏间 2em) ÷ 栏数。

        左边几栏把栏间间隙算进自己的宽度（Word 的表格没有列间距），
        最后一栏不加 —— 纸上的栏左缘就落在预览的 8.467 / 52.966 / 97.466 上。
        """
        for columns in (2, 4):
            table = build_options(columns).tables[0]
            column_mm = (TEXT_WIDTH_MM - LIST_INDENT_MM - (columns - 1) * COLUMN_GAP_MM) / columns
            want = [column_mm + COLUMN_GAP_MM] * (columns - 1) + [column_mm]
            for got, expected in zip(grid_widths_mm(table), want):
                self.assertAlmostEqual(
                    got, expected, places=1, msg=f"{columns} 栏的列宽和预览对不上：{grid_widths_mm(table)}"
                )

    def test_the_options_table_exactly_fills_the_text_column(self):
        """所有列宽之和 + 左缩进 = 正文宽：不多不少，一毫米都不差。"""
        for columns in (2, 4):
            table = build_options(columns).tables[0]
            total = sum(grid_widths_mm(table)) + table_indent_twips(table) * 25.4 / 1440
            self.assertAlmostEqual(total, TEXT_WIDTH_MM, places=1)

    def test_the_cells_have_no_side_padding(self):
        """Word 默认每格左右留 1.905 毫米，预览的选项栏不留白。"""
        document = build_options(4)
        table = document.tables[0]
        for row in table.rows:
            for cell in row.cells:
                margins = cell_margins_twips(cell)
                self.assertIsNotNone(margins, "选项格没写 w:tcMar，Word 会留 1.905 毫米的默认内边距")
                for side, value in margins.items():
                    self.assertEqual(value, 0, f"选项格的 {side} 内边距是 {value} twips，不该留白")

    def test_the_body_text_starts_where_the_preview_starts_it(self):
        """标签 → 正文：悬挂缩进 + 制表位，把正文推到预览里的那个位置。

        预览里标签列是 ``auto``，各行自己算宽：「A.」比「B.」宽，
        所以 A 那一行的正文列就比 B 窄（Chrome 实测 4 栏时 30.646 / 30.882）。
        """
        document = build_options(4)
        table = document.tables[0]
        for index, letter in enumerate("ABCD"):
            cell = table.cell(0, index)
            paragraph = cell.paragraphs[0]
            left, hanging, _right, stops = paragraph_indents_mm(paragraph)
            self.assertAlmostEqual(left, LABEL_OFFSET_MM[letter], delta=0.05)
            self.assertAlmostEqual(hanging, LABEL_OFFSET_MM[letter], delta=0.05)
            self.assertTrue(stops, f"{letter} 那一格没有制表位，正文会顶到标签上")
            self.assertAlmostEqual(min(stops), LABEL_OFFSET_MM[letter], delta=0.05)
            # 标签和正文之间是一个 tab，不是硬敲的空格。
            self.assertTrue(paragraph._p.findall(".//" + qn("w:tab")), f"{letter} 那一格的正文前面没有 tab")

    def test_the_gap_to_the_next_column_is_left_out_of_the_text(self):
        """栏间的 2em 间隙要让出来：左边的栏用右缩进把空间还回去。"""
        document = build_options(4)
        table = document.tables[0]
        for index, letter in enumerate("ABCD"):
            _left, _hanging, right, _stops = paragraph_indents_mm(table.cell(0, index).paragraphs[0])
            if index < 3:
                self.assertAlmostEqual(right, COLUMN_GAP_MM, delta=0.05, msg=f"{letter} 那一栏没让出栏间间隙")
            else:
                self.assertAlmostEqual(right, 0.0, delta=0.05, msg="最后一栏右边没有下一栏，不该缩进")

    def test_a_picture_is_capped_by_its_own_body_column(self):
        """选项里的图最多占满正文列 —— 按这一格自己的标签算。

        一律按最宽的「A.」算，B、C、D 的图就会被多裁掉两毫米。
        """
        document = build_options(4, item=option_item(images=("A", "B", "C", "D")))
        widths = picture_widths_mm(document)
        self.assertEqual(len(widths), 4, "尺子自检：选项表里没找到四张图")
        for letter, width in zip("ABCD", widths):
            self.assertLessEqual(
                width,
                BODY_WIDTH_MM[letter] + 0.05,
                f"{letter} 那一格的图画成了 {width:.3f} 毫米，正文列只有 {BODY_WIDTH_MM[letter]} 毫米",
            )

    def test_a_measured_picture_width_is_capped_by_the_body_column_too(self):
        """预览量到的宽度也要受正文列的限制 —— 越界的实测值按列收口。"""
        document = build_options(4, item=option_item(images=("A", "B")), measured={"A": [60.0], "B": [60.0]})
        widths = picture_widths_mm(document)
        self.assertEqual(len(widths), 2, "尺子自检：选项表里没找到两张图")
        for letter, width in zip("AB", widths):
            self.assertAlmostEqual(width, BODY_WIDTH_MM[letter], delta=0.05)

    def test_each_option_is_capped_by_its_own_label(self):
        """「A.」比「B.」宽，A 那一行的正文列就比 B 窄 —— 收口要按行算。

        一律按最宽的「A.」算，B、C、D 的图就会被多裁掉两毫米；
        一律按最窄的算，A 的图又会溢出到隔壁列。
        """
        # 30.88 毫米：B 的正文列装得下（30.882），A 的装不下（30.646）。
        document = build_options(4, item=option_item(images=("A", "B")), measured={"A": [30.88], "B": [30.88]})
        widths = picture_widths_mm(document)
        self.assertEqual(len(widths), 2, "尺子自检：选项表里没找到两张图")
        self.assertAlmostEqual(widths[0], BODY_WIDTH_MM["A"], delta=0.05, msg="A 那一行的图没收口到自己的正文列")
        self.assertAlmostEqual(widths[1], 30.88, delta=0.05, msg="B 那一行的图被多裁了：它的正文列装得下 30.88")
