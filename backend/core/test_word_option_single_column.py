"""导出 Word 的**单栏**选项块，水平位置必须照抄打印预览。

多栏路径第五十三轮已经对齐了（见 ``test_word_option_geometry.py``），
但单栏路径是另一条代码：选项直接写成普通段落，不进表格。
预览里单栏的选项照样是「选项区缩进 2em + 标签列 + 正文列」两列网格
（``.qb-options { padding-left: 2em }``、``.qb-option { grid-template-columns: auto minmax(0,1fr) }``），
所以单栏也必须缩进 —— 预览里选项的正文列在离版心左缘 13.85 毫米处。

Chrome 实测（2026-11-17，``_probe58.py``，178 毫米打印容器，1 栏）：

  · 栏左缘 8.467（选项区那 2em），栏宽 169.532；
  · A 行：正文列左缘 **13.853**、正文列宽 164.145；
    选项里的图左缘 **13.853**（``.qb-option .qb-figure { grid-column: 2 }``）；
  · B 行：正文列左缘 **13.618**、正文列宽 164.381
    （标签列是 ``auto`` 宽：「A.」比「B.」宽 0.235 毫米，正文列就窄 0.235）；
  · 选项里的表左缘 **90.173**（预览里 ``.qb-table { margin: 0 auto }``
    是在**正文列**里居中：13.618 + (164.381 − 11.270) / 2）。

Word 这边原来整块选项顶格（真 Word 实测 ``_probe59.py``：选项行落在离版心左缘
0.106 毫米处），图还在版心右边（144.145 毫米），表在整栏居中（85.072 毫米）。
屏幕上看着还行的卷子，导出来选项是齐着左页边的。

这里的判据一律用**独立字面值**（Chrome 实测值），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "11111111-1111-4111-8111-111111111111"

TEXT_WIDTH_MM = 178.0

# Chrome 实测的预览几何（毫米）。
LIST_INDENT_MM = 8.4667  # 2em，选项区整体离版心左缘
LABEL_OFFSET_MM = {"A": 5.386, "B": 5.151, "C": 5.151, "D": 5.386, "E": 4.953}
# 单栏时正文列的左缘（离版心左缘）与宽度。
BODY_LEFT_MM = {"A": 13.853, "B": 13.618, "C": 13.618, "D": 13.853}
BODY_WIDTH_MM = {"A": 164.145, "B": 164.381, "C": 164.381, "D": 164.145}
# 选项里那张表的左缘（预览：在正文列里居中）。
TABLE_LEFT_MM = 90.173

# Word 默认给每个单元格留 108 twips（1.905 毫米）的左边距 —— 主文档里的表，
# ``w:tblInd`` 量到的是第一格**文字**的左缘，边框还要再往左退这么宽。
# 这是 Word 自己的默认值，不是被测模块的常量。
WORD_DEFAULT_CELL_MARGIN_TWIPS = 108


def png_bytes(width=1000, height=200):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def text_block(raw):
    return {"type": "text", "segments": [{"type": "text", "raw": raw}]}


def table_block():
    from . import library_export

    rows = library_export._table_source("| x |\n| --- |\n| 1 |", "第 1 题")
    return {
        "type": "table",
        "rows": [[{**cell, "segments": [{"type": "text", "raw": cell["source"]}]} for cell in row] for row in rows],
    }


def option_item():
    """一道有四个选项的题：A 带图，B 里带一张表，C、D 是普通文字。"""
    return {
        "id": QUESTION,
        "type": "single_choice",
        "where": "第 1 题",
        "content": {"options": {"A": "带图", "B": "表", "C": "普通项", "D": "第四项"}, "body_mode": "text"},
        "fields": {
            "stem": [text_block("第一题：")],
            "options.A": [text_block("带图")],
            "options.B": [table_block()],
            "options.C": [text_block("普通项")],
            "options.D": [text_block("第四项")],
        },
        "images": [{"slot": "A", "bytes": png_bytes(), "size": (1000, 200)}],
    }


def build_document(item=None, columns=1):
    """走**真实导出路径**造一份 Word：和老师点「导出 Word」跑的是同一段代码。"""
    from . import library_export

    item = item or option_item()
    options = {
        "font_size": 12,
        "student_info": False,
        "answer_space": "none",
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
        "option_columns": {item["id"]: columns},
        # 选项里的图在预览里是 CSS 自然宽（96dpi）：200 像素 = 52.917 毫米。
        "measured_figure_widths": {item["id"]: {"A": [52.917]}},
    }
    data = library_export._document([item], "第五十五轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def paragraph_left_indent_mm(paragraph):
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return 0.0
    indents = properties.find(qn("w:ind"))
    if indents is None:
        return 0.0
    return int(indents.get(qn("w:left"), "0")) * 25.4 / 1440


def paragraph_first_line_indent_mm(paragraph):
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return 0.0
    indents = properties.find(qn("w:ind"))
    if indents is None:
        return 0.0
    return int(indents.get(qn("w:hanging"), "0")) * 25.4 / 1440


def paragraph_tab_stops_mm(paragraph):
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return []
    tabs = properties.find(qn("w:tabs"))
    if tabs is None:
        return []
    return [int(tab.get(qn("w:pos"), "0")) * 25.4 / 1440 for tab in tabs.findall(qn("w:tab"))]


def paragraph_alignment(paragraph):
    """段落的对齐方式；没写 w:jc 就是 Word 的默认（左）。"""
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return "left"
    alignment = properties.find(qn("w:jc"))
    if alignment is None:
        return "left"
    return alignment.get(qn("w:val")) or "left"


def option_paragraph(document, letter):
    """那个选项的段落（正文是「字母. + 制表符 + 正文」）。"""
    for paragraph in document.paragraphs:
        if paragraph.text.startswith(f"{letter}."):
            return paragraph
    raise AssertionError(f"尺子自检：文档里找不到选项 {letter} 的段落")


def figure_paragraphs(document):
    return [paragraph for paragraph in document.paragraphs if paragraph._p.findall(".//" + qn("w:drawing"))]


def table_border_left_mm(table):
    """这张表**边框**的左缘离版心左缘多远（毫米）。

    主文档里的表：``w:tblInd`` 量到的是第一格文字的左缘，边框还要再往左退
    一个单元格左边距（Word 默认 108 twips）。表格自己写了 ``w:tblCellMar``
    就按它写的算。
    """
    properties = table._tbl.tblPr
    indent = properties.find(qn("w:tblInd"))
    assert indent is not None, "表格没写 w:tblInd，位置就只能靠 Word 自己猜"
    margin = properties.find(qn("w:tblCellMar"))
    left_margin = WORD_DEFAULT_CELL_MARGIN_TWIPS
    if margin is not None:
        cell = margin.find(qn("w:left"))
        if cell is not None:
            left_margin = int(cell.get(qn("w:w"), "0"))
    return (int(indent.get(qn("w:w"))) - left_margin) * 25.4 / 1440


class OptionSingleColumnPositionTests(SimpleTestCase):
    def test_the_option_body_starts_at_the_body_column_of_the_preview(self):
        """选项正文从正文列的左缘开始（预览 13.853）。

        预览里 ``.qb-options`` 左边留 2em，``.qb-option`` 再分成标签列和正文列。
        Word 这边原来整块顶格（实测 0.106 毫米），比预览靠左 13.75 毫米。
        """
        document = build_document()
        indent = paragraph_left_indent_mm(option_paragraph(document, "A"))
        self.assertAlmostEqual(
            indent, BODY_LEFT_MM["A"], delta=0.05, msg=f"选项 A 的正文落在离版心左缘 {indent:.3f} 毫米的地方"
        )

    def test_each_option_label_has_its_own_body_column(self):
        """B 行的正文列比 A 行窄 0.235 毫米 —— 标签列是 auto 宽。

        「A.」比「B.」宽（4.117 / 3.882 毫米），所以预览里 B 那一行的正文列
        左缘是 13.618，不是 13.853。Word 这边按最宽的标签算就会差 0.235 毫米。
        """
        document = build_document()
        indent = paragraph_left_indent_mm(option_paragraph(document, "B"))
        self.assertAlmostEqual(
            indent, BODY_LEFT_MM["B"], delta=0.05, msg=f"选项 B 的正文落在离版心左缘 {indent:.3f} 毫米的地方"
        )

    def test_the_option_label_hangs_at_the_list_indent(self):
        """首行是悬挂的：标签落在选项区那一列（离版心左缘 8.467）。

        Word 这边原来没有悬挂缩进，标签和正文都顶格，折行也不回正文列。
        """
        document = build_document()
        hanging = paragraph_first_line_indent_mm(option_paragraph(document, "A"))
        self.assertAlmostEqual(
            hanging,
            LABEL_OFFSET_MM["A"],
            delta=0.05,
            msg=f"选项 A 的悬挂缩进是 {hanging:.3f} 毫米（首行落在离版心左缘 {BODY_LEFT_MM['A'] - hanging:.3f} 毫米处）",
        )

    def test_the_option_body_is_pushed_to_the_body_column_by_a_tab_stop(self):
        """正文靠制表位推到正文列 —— Word 默认制表位是 12.7 毫米，我们要 13.853。"""
        document = build_document()
        stops = paragraph_tab_stops_mm(option_paragraph(document, "A"))
        self.assertTrue(stops, "尺子自检：选项段落里没有制表位")
        self.assertAlmostEqual(
            stops[0], BODY_LEFT_MM["A"], delta=0.05, msg=f"选项 A 的制表位在离版心左缘 {stops[0]:.3f} 毫米处"
        )

    def test_the_picture_in_a_single_column_option_starts_at_the_body_column(self):
        """选项里的图左缘 = 正文列左缘（预览 13.853）。

        预览里 ``.qb-option .qb-figure { grid-column: 2 }`` 把图钉在正文那一列。
        """
        document = build_document()
        paragraphs = figure_paragraphs(document)
        self.assertEqual(len(paragraphs), 1, f"尺子自检：文档里有 {len(paragraphs)} 个图段落")
        indent = paragraph_left_indent_mm(paragraphs[0])
        self.assertAlmostEqual(
            indent, BODY_LEFT_MM["A"], delta=0.05, msg=f"选项里的图落在离版心左缘 {indent:.3f} 毫米的地方"
        )

    def test_the_picture_in_a_single_column_option_is_left_aligned(self):
        """选项里的图是**左**对齐 —— 预览里它就贴着正文列的左缘。

        Word 这边原来沿用题干配图的「靠右」，图被推到版心右边（实测 144.145 毫米）。
        """
        document = build_document()
        paragraphs = figure_paragraphs(document)
        self.assertEqual(len(paragraphs), 1, "尺子自检：文档里的图段落数不对")
        self.assertEqual(
            paragraph_alignment(paragraphs[0]), "left", msg="选项里的图被右对齐了，预览里它贴着正文列的左缘"
        )

    def test_the_table_in_a_single_column_option_is_centred_in_the_body_column(self):
        """选项里的表在**正文列**里居中（预览 90.173）。

        预览里 ``.qb-table { margin: 0 auto }`` 是在正文列里居中，不是在整栏
        （178 毫米）里居中。Word 这边原来在整栏居中，边框落在 85.072 毫米。
        """
        document = build_document()
        self.assertEqual(len(document.tables), 1, f"尺子自检：文档里有 {len(document.tables)} 张表")
        left = table_border_left_mm(document.tables[0])
        self.assertAlmostEqual(left, TABLE_LEFT_MM, delta=0.1, msg=f"选项里的表落在离版心左缘 {left:.3f} 毫米的地方")
