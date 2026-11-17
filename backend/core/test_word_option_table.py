"""选项里的表，两条排版路必须给出同一份内容。

预览里选项里也能排表（``.qb-option-body`` 里可以出现 ``<table>``），
Word 这边多栏排版走的是 ``_write_compact_options``，单栏走 ``_write_field``。

原来多栏那条路只写 ``blocks[0]``：

* 选项以表开头 → ``KeyError('segments')``，整份 Word 导出直接崩；
* 表排在第二块 → 整张表被**静默丢掉**，纸上的选项少一截，
  而 PDF 路线那边好好地排着表。

这条路不是理论上的：一张窄表（``| x |`` / ``| 1 |``）的前端实测栏数就是 2，
``auto`` 布局下老师什么都不用选，Word 就崩（实测 2026-11-17，
真 PDF 流水线量出来的 ``data-exam-columns`` = 2）。

现在多栏和单栏走同一套块处理（``_write_field``），内容不会少。
本文件守着这条不变量：**同一个选项，Word 单栏和多栏排出来的内容一样**。
"""

from django.test import SimpleTestCase

from . import library_drafts, library_export

QUESTION = "66666666-1111-4111-8111-111111111111"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

TABLE_ONLY = "| x |\n| --- |\n| 1 |"


def text_block(raw):
    return {"type": "text", "segments": [{"type": "text", "raw": raw}]}


def table_block(raw=TABLE_ONLY, where="第 1 题"):
    """The block shape ``library_export._field`` produces for a table."""
    rows = library_export._table_source(raw.strip("\n"), where)
    return {
        "type": "table",
        "rows": [[{**cell, "segments": [{"type": "text", "raw": cell["source"]}]} for cell in row] for row in rows],
    }


def option_item(option_blocks):
    return {
        "id": QUESTION,
        "where": "第 1 题",
        "images": [],
        "fields": {
            "options.A": option_blocks,
            "options.B": [text_block("普通项")],
            "options.C": [text_block("第三项")],
            "options.D": [text_block("第四项")],
        },
    }


def build_options(columns=2, option_blocks=None):
    from docx import Document

    document = Document()
    blocks = option_blocks if option_blocks is not None else [table_block(TABLE_ONLY)]
    library_export._write_compact_options(document, option_item(blocks), 12, columns)
    return document


def omml(text):
    """A minimal native oMath element, the way ``_segments`` hands it over.

    ``omml`` 为 None 在真实链路里意味着「这个公式本地排不了，要退化成图片」，
    那是另一条分支；display 公式走的是原生 oMath。
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    return parse_xml(f"<m:oMath {nsdecls('m')}><m:r><m:t>{text}</m:t></m:r></m:oMath>")


def cell_text(cell):
    """Text of a cell, math included.

    ``| x |`` 里的单个字母会被前端当成公式段，内容落在 OMML 的 ``m:t`` 里，
    只读 ``w:t`` 会把它看成空格子 —— 那是尺子的盲点，不是产物的缺陷。
    """
    from lxml import etree

    return "".join(
        node.text or ""
        for node in cell._tc.iter()
        if etree.QName(node).localname == "t" and etree.QName(node).namespace in (W, M)
    ).strip()


def option_cells(document):
    """The four option cells, in reading order."""
    table = document.tables[0]
    return [cell for row in table.rows for cell in row.cells]


def nested_tables(document):
    """Every table nested inside an option cell."""
    found = []
    for cell in option_cells(document):
        found.extend(cell.tables)
    return found


def cell_outline(cell):
    """What sits in one option cell: paragraphs and nested tables, in order."""
    from lxml import etree

    outline = []
    for child in cell._tc:
        tag = etree.QName(child).localname
        if tag == "p":
            outline.append(("p", cell_text_of(child)))
        elif tag == "tbl":
            outline.append(("tbl", ""))
    return outline


def cell_text_of(paragraph):
    from lxml import etree

    return "".join(
        node.text or ""
        for node in paragraph.iter()
        if etree.QName(node).localname == "t" and etree.QName(node).namespace in (W, M)
    ).strip()


class OptionTableTests(SimpleTestCase):
    def test_an_option_that_is_only_a_table_is_not_dropped(self):
        document = build_options(columns=2, option_blocks=[table_block()])
        tables = nested_tables(document)
        self.assertEqual(len(tables), 1, "选项里那张表没排进格子里")
        rows = tables[0].rows
        self.assertEqual(len(rows), 2, "表头行和���据行都得在")
        self.assertEqual(cell_text(rows[0].cells[0]), "x")
        self.assertEqual(cell_text(rows[1].cells[0]), "1")

    def test_a_table_after_the_option_text_is_not_dropped(self):
        document = build_options(columns=2, option_blocks=[text_block("先看这张表："), table_block()])
        tables = nested_tables(document)
        self.assertEqual(len(tables), 1, "选项里的表被静默丢掉了")
        first = option_cells(document)[0]
        outline = [kind for kind, _ in cell_outline(first)]
        # 表后面那个空段是 OOXML 的硬要求（w:tc 必须以 w:p 结尾），不是多余的段。
        self.assertEqual(outline[:2], ["p", "tbl"], f"选项格里的顺序不对：{outline}")
        self.assertEqual(outline[-1], "p", "表后面必须收一个空段，否则 Word 会说文件坏了")

    def test_the_text_of_the_option_is_kept_next_to_the_table(self):
        document = build_options(columns=2, option_blocks=[text_block("先看这张表："), table_block()])
        first = option_cells(document)[0]
        self.assertIn("先看这张表：", cell_text(first))

    def test_one_column_and_two_columns_agree(self):
        """The same option, laid out one way and the other way, must be equal."""
        for blocks in ([table_block()], [text_block("先看这张表："), table_block()]):
            one = build_options(columns=1, option_blocks=blocks)
            two = build_options(columns=2, option_blocks=blocks)
            self.assertEqual(
                cell_outline(option_cells(one)[0]),
                cell_outline(option_cells(two)[0]),
                "单栏和多栏排出来的选项内容不一样",
            )

    def test_the_table_in_an_option_stays_inside_its_cell(self):
        document = build_options(columns=2, option_blocks=[table_block()])
        table = document.tables[0]
        column_mm = table.columns[0].width / 36000.0
        usable = column_mm - 2 * library_export.WORD_CELL_MARGIN_MM
        inner = nested_tables(document)[0]
        width_mm = sum(column.width for column in inner.columns) / 36000.0
        self.assertLessEqual(width_mm, usable + 0.5, f"选项里的表有 {width_mm:.2f} 毫米宽，格子只有 {usable:.2f} 毫米")

    def test_the_whole_export_survives_an_option_that_is_only_a_table(self):
        """The export must not fall over; that is the bug this file watches."""
        content = {
            "stem": "第一题：",
            "options": {"A": TABLE_ONLY, "B": "普通项", "C": "第三项", "D": "第四项"},
            "answer": "",
            "analysis": "",
            "origin": "",
        }
        captured = [
            {
                "id": QUESTION,
                "where": "第 1 题",
                "type": "single_choice",
                "content": content,
                "extras": {},
                "selected": {"answer": "", "analysis": ""},
                "ai": False,
                "hash": "",
                "fields": {
                    "stem": [text_block("第一题：")],
                    "options.A": [table_block(TABLE_ONLY)],
                    "options.B": [text_block("普通项")],
                    "options.C": [text_block("第三项")],
                    "options.D": [text_block("第四项")],
                },
                "images": [],
                "solution_images": [],
                "solution_id": None,
                "selection_revision": None,
                "solution_explicit": False,
            }
        ]
        options = library_drafts._print_options(
            {"document": "questions", "student_info": False}, {"answer_layout": "appendix"}, ids=[QUESTION]
        )
        options["option_columns"] = {QUESTION: 2}
        data = library_export._document(captured, "第五十二轮", options, "questions")
        self.assertTrue(data, "导出结果为空")
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
        self.assertIn("word/document.xml", names, "导出的不是一份 Word 文档")

    def test_an_option_with_a_display_formula_keeps_its_own_paragraph(self):
        """A display formula starts its own line, like the one-column path does."""
        formula = {"type": "text", "segments": [{"type": "math", "raw": "x", "display": True, "omml": omml("x")}]}
        document = build_options(columns=2, option_blocks=[formula])
        first = option_cells(document)[0]
        outline = [kind for kind, _ in cell_outline(first)]
        self.assertEqual(outline, ["p", "p"], f"行间公式没有自己起一段：{outline}")
