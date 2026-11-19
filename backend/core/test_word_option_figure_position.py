"""导出 Word 的选项块里，**图和表**要落在预览里那个正文列上。

Chrome 实测（2026-11-17，``_probe56.py``，178 毫米打印容器，4 栏，
用 ``exam-layout.js`` 那样的内联 ``gridTemplateColumns`` 覆盖容器查询）：

  · A 栏：栏左缘 8.467，正文列左缘 **13.853**，正文列宽 30.646；
    选项里的图左缘 **13.853** —— 预览里 ``.qb-option .qb-figure { grid-column: 2 }``
    把图钉在正文那一列的左缘（不是标签列，也不是靠右）。
  · B 栏：栏左缘 52.966，正文列左缘 **58.117**，正文列宽 30.882；
    选项里的表左缘 **67.924**、表宽 11.270 —— 预览里 ``.qb-table { margin: 0 auto }``
    是在**正文列**里居中，不是在整格里居中。

Word 这边原来两处都错：

  · 图：``_write_images`` 在选项格里新起段落，那一段没有缩进，
    而对齐方式是「右」（题干配图默认靠右）—— 于是图贴在整格的右边，
    左缘落在 22.313 毫米（差 8.46 毫米，正好一个 2em）。
  · 表：选项路径没有把「自己所在那一列」告诉 ``_write_table``，
    它就在**整格**（宽 44.5 毫米 = 栏宽 + 栏间间隙）里居中，
    左缘落在 71.296 毫米（差 3.37 毫米）。

还有第三处，是 Word 自己的两条规矩不一样（真 Word 实测，_probe57.py：
同一个格里摆 5 张 tblInd = 0/200/400/848/1200 twips 的嵌套表，导 PDF 量边框）：
**嵌套表**的 ``w:tblInd`` 直接就是**边框**的位置（缩进 0 时边框落在格子左缘
53.075 毫米），而**主文档**里的表量到的是第一格**文字**的左缘、边框还要再往左
退 1.905 毫米。补偿加错方向，选项里的表又偏右 2 毫米（70.026 vs 67.924）。

判据一律用**独立字面值**（Chrome 实测值），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "11111111-1111-4111-8111-111111111111"

TEXT_WIDTH_MM = 178.0

# Chrome 实测的预览几何（毫米）。
LIST_INDENT_MM = 8.4667  # 2em，选项区整体离版心左缘
LABEL_OFFSET_MM = {"A": 5.386, "B": 5.151, "C": 5.151, "D": 5.386, "E": 4.953}
# 正文列的左缘（离版心左缘）与宽度。
BODY_LEFT_MM = {"A": 13.853, "B": 58.117, "C": 102.617, "D": 147.352}
BODY_WIDTH_MM = {"A": 30.646, "B": 30.882, "C": 30.882, "D": 30.646}
# 选项里那张表的左缘（预览：在正文列里居中）。
TABLE_LEFT_MM = 67.924


def twips(mm):
    return int(round(mm * 1440 / 25.4))


def png_bytes(width=1000, height=200):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def small_png_bytes(width=40, height=20):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def table_block():
    from . import library_export

    rows = library_export._table_source("| x |\n| --- |\n| 1 |", "第 1 题")
    return {
        "type": "table",
        "rows": [[{**cell, "segments": [{"type": "text", "raw": cell["source"]}]} for cell in row] for row in rows],
    }


def option_item(images=(), table_letters=()):
    fields = {
        f"options.{letter}": [{"type": "text", "segments": [{"type": "text", "raw": "选项正文"}]}]
        for letter in "ABCD"
        if letter not in table_letters
    }
    for letter in table_letters:
        fields[f"options.{letter}"] = [table_block()]
    return {
        "id": QUESTION,
        "where": "第 1 题",
        "fields": fields,
        "images": [{"slot": slot, "bytes": png_bytes(), "size": (1000, 200)} for slot in images],
    }


def build_options(columns=4, item=None):
    from docx import Document

    from . import library_export

    document = Document()
    library_export._write_compact_options(document, item or option_item(), 12, columns, text_width_mm=TEXT_WIDTH_MM)
    return document


def table_indent_twips(table):
    element = table._tbl.tblPr.find(qn("w:tblInd"))
    assert element is not None, "表格没写 w:tblInd，位置就只能靠 Word 自己猜"
    return int(element.get(qn("w:w")))


def grid_widths_twips(table):
    grid = table._tbl.find(qn("w:tblGrid"))
    return [int(column.get(qn("w:w"))) for column in grid.findall(qn("w:gridCol"))]


def cell_left_mm(table, column_index):
    """第 ``column_index`` 栏的格子左缘离版心左缘多远（毫米）—— 从 docx 自己算。"""
    indent = table_indent_twips(table) * 25.4 / 1440
    widths = grid_widths_twips(table)
    return indent + sum(widths[:column_index]) * 25.4 / 1440


def paragraph_left_indent_mm(paragraph):
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return 0.0
    indents = properties.find(qn("w:ind"))
    if indents is None:
        return 0.0
    return int(indents.get(qn("w:left"), "0")) * 25.4 / 1440


def paragraph_alignment(paragraph):
    """段落的对齐方式；没写 w:jc 就是 Word 的默认（左）。"""
    properties = paragraph._p.find(qn("w:pPr"))
    if properties is None:
        return "left"
    alignment = properties.find(qn("w:jc"))
    if alignment is None:
        return "left"
    return alignment.get(qn("w:val")) or "left"


def figure_paragraphs(cell):
    """这一格里带图的段落（按顺序）。"""
    return [paragraph for paragraph in cell.paragraphs if paragraph._p.findall(".//" + qn("w:drawing"))]


class OptionFigurePositionTests(SimpleTestCase):
    def test_the_picture_in_an_option_starts_at_the_body_column(self):
        """选项里的图左缘 = 正文列左缘（预览 13.853）。

        预览里 ``.qb-option .qb-figure { grid-column: 2 }`` 把图钉在正文那一列，
        Word 这边就得在图的段落上留出「标签宽 + 标签与正文之间的间隙」。
        """
        document = build_options(4, item=option_item(images=("A",)))
        table = document.tables[0]
        cell = table.cell(0, 0)
        paragraphs = figure_paragraphs(cell)
        self.assertEqual(len(paragraphs), 1, "尺子自检：A 那一格里的图没找到")
        indent = paragraph_left_indent_mm(paragraphs[0])
        self.assertAlmostEqual(indent, LABEL_OFFSET_MM["A"], delta=0.05, msg=f"图的段落左缩进是 {indent:.3f} 毫米")
        left = cell_left_mm(table, 0) + indent
        self.assertAlmostEqual(left, BODY_LEFT_MM["A"], delta=0.05, msg=f"图落在离版心左缘 {left:.3f} 毫米的地方")

    def test_the_picture_in_an_option_is_not_pushed_to_the_right(self):
        """选项里的图是**左**对齐 —— 预览里它就在正文列的左缘。

        Word 这边原来沿用题干配图的「靠右」，图被推到整格的右边，
        左缘偏右 8.46 毫米（正好一个 2em）。
        """
        document = build_options(4, item=option_item(images=("A",)))
        table = document.tables[0]
        paragraphs = figure_paragraphs(table.cell(0, 0))
        self.assertEqual(len(paragraphs), 1, "尺子自检：A 那一格里的图没找到")
        self.assertEqual(
            paragraph_alignment(paragraphs[0]), "left", msg="选项里的图被右对齐了，预览里它贴着正文列的左缘"
        )

    def test_the_table_in_an_option_is_centred_in_the_body_column(self):
        """选项里的表在**正文列**里居中（预览 67.924）。

        Word 这边原来在整格（宽 44.5 毫米）里居中，偏右 3.37 毫米。
        """
        document = build_options(4, item=option_item(table_letters=("B",)))
        table = document.tables[0]
        cell = table.cell(0, 1)
        nested = cell.tables
        self.assertEqual(len(nested), 1, "尺子自检：B 那一格里的表没找到")
        nested_table = nested[0]
        written = table_indent_twips(nested_table) * 25.4 / 1440
        # 嵌套表的 w:tblInd 直接就是边框的位置（真 Word 实测，_probe57.py：
        # 缩进 0 时边框落在 B 栏格子左缘 53.075 毫米）。
        border_left = cell_left_mm(table, 1) + written
        self.assertAlmostEqual(
            border_left, TABLE_LEFT_MM, delta=0.05, msg=f"选项里的表落在离版心左缘 {border_left:.3f} 毫米的地方"
        )

    def test_each_picture_in_an_option_gets_a_line_of_its_own(self):
        """选项里的图一张一行 —— 预览里它们是网格里各自一行的格子。

        Word 这边原来会把几张图并排塞进一张表（题干配图那套「一行放几张」的规矩），
        在 30 毫米宽的正文列里根本放不下。
        """
        item = option_item()
        item["images"] = [
            {"slot": "A", "bytes": small_png_bytes(), "size": (40, 20)},
            {"slot": "A", "bytes": small_png_bytes(), "size": (40, 20)},
            {"slot": "A", "bytes": small_png_bytes(), "size": (40, 20)},
        ]
        document = build_options(4, item=item)
        cell = document.tables[0].cell(0, 0)
        paragraphs = figure_paragraphs(cell)
        self.assertEqual(len(paragraphs), 3, f"尺子自检：三张图被排成了 {len(paragraphs)} 段")
        self.assertEqual(len(cell.tables), 0, "尺子自检：选项里的图被塞进了一张表")
