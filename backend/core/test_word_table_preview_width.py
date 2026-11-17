"""导出 Word 的**表格宽度**必须和打印预览一模一样。

这一轮查出来的是两件事：

1. **列宽模型不对**。Word 这边原来每列是 ``max(8.0mm, 内容 + 5.0mm)``，
   预览里同一张表每列是 ``max(2.6em, 文字 + 1.4em + 1 像素)``。
   两列表：预览每列 11.005 毫米，Word 只有 8.052（真 Word 16 纸上实测），
   每列差 3 毫米，整张表窄了 6 毫米 —— 屏幕上看着还行，导出来明显小一号。

2. **宽表走的是另一套规则**。预览里列数 >= 13 的表会挂 ``qb-table-dense``
   （qb-render.js 的 ``DENSE_TABLE_COLUMNS = 13``）：最小列宽放开、
   内边距收成 ``.15em .45em``。Word 这边完全不知道有 dense，
   十三列的表每列还占 11 毫米 —— 预览实测整表 111.32 毫米，
   Word 算出来 165.8 毫米，**宽 54 毫米**，一眼就看出来不是同一张表。

Chrome 实测（2026-11-17，``_probe66.py`` / ``_probe69.py``，178 毫米打印容器）：

  · 两列表 ``| x | y |``：每列 **11.005** 毫米，整表 **22.275** 毫米
  · 十三列表（表头「零售收入/万元」+ 12 个数字列）：
    表头列 **30.646**、数字列 **6.185**、两位数那几列 **8.30**、
    整表 **111.319** 毫米
  · 十二列表：每列还是 11.007 毫米（没到 dense 门槛）

这里的判据一律用**独立字面值**（Chrome 实测值），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "22222222-2222-4222-8222-222222222222"

# Chrome 实测的预览几何（毫米）。
NARROW_COLUMN_MM = 11.005
NARROW_TABLE_MM = 22.275
DENSE_HEADER_COLUMN_MM = 30.646
DENSE_NUMBER_COLUMN_MM = 6.185
DENSE_TWO_DIGIT_COLUMN_MM = 8.30
DENSE_TABLE_MM = 111.319
NORMAL_COLUMN_MM = 11.007
# 表头是长文字的窄表：有一列是**按内容**算宽的（不是被 2.6em 下限撑着）。
CONTENT_COLUMN_MM = 32.763
CONTENT_TABLE_MM = 44.033

NARROW_TABLE = "| x | y |\n| --- | --- |\n| 1 | 2 |"
CONTENT_TABLE = "| 零售收入/万元 | 甲 |\n| --- | --- |\n| 1 | 2 |"
TWELVE_NUMBERS = " | ".join(str(index) for index in range(1, 13))
DENSE_TABLE = f"| 零售收入/万元 | {TWELVE_NUMBERS} |\n| --- | " + " | ".join("---" for _ in range(12)) + " |"
TWELVE_TABLE = (
    "| " + " | ".join(str(index) for index in range(1, 13)) + " |\n| " + " | ".join("---" for _ in range(12)) + " |"
)


def table_rows(markdown, where="第 1 题"):
    """把一段 Markdown 表格拆成导出用的行（和真导出走同一个解析器）。"""
    from . import library_export

    rows = library_export._table_source(markdown, where)
    return [[{**cell, "segments": [{"type": "text", "raw": cell["source"]}]} for cell in row] for row in rows]


def placements_for(markdown, where="第 1 题"):
    """表格的摆放：每一行的每一格在第几列。"""
    placements = []
    for index, row in enumerate(table_rows(markdown, where)):
        column = 0
        for cell in row:
            placements.append((index, column, cell))
            column += cell["colspan"]
    width = max(column for _, column, _ in placements) + 1
    return placements, width


def build_document(markdown):
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
        "font_size": 12,
        "student_info": False,
        "answer_space": "none",
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
    }
    data = library_export._document([item], "第五十六轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def grid_columns_mm(document):
    """文档里第一张表每一列的宽度（毫米），照 ``w:gridCol`` 读。"""
    assert document.tables, "尺子自检：文档里没有表"
    grid = document.tables[0]._tbl.find(qn("w:tblGrid"))
    assert grid is not None, "尺子自检：表里没有 w:tblGrid"
    return [int(node.get(qn("w:w"))) * 25.4 / 1440 for node in grid.findall(qn("w:gridCol"))]


class PreviewColumnWidthTests(SimpleTestCase):
    def test_a_narrow_table_gets_the_column_width_of_the_preview(self):
        """窄表每列 = 11.005 毫米（预览实测）。

        这是预览的 ``max(2.6em, 文字 + 1.4em + 1 像素)``：12 磅正文里
        2.6em = 11.007 毫米，Chrome 实测 11.005。
        """
        from . import library_export

        placements, width = placements_for(NARROW_TABLE)
        columns = library_export._preview_column_widths(placements, width, 12)
        self.assertEqual(len(columns), 2, "尺子自检：列数不对")
        for index, value in enumerate(columns):
            self.assertAlmostEqual(
                value,
                NARROW_COLUMN_MM,
                delta=0.02,
                msg=f"预览模型里第 {index + 1} 列是 {value:.4f} 毫米，预览实测 {NARROW_COLUMN_MM}",
            )

    def test_a_narrow_table_gets_the_table_width_of_the_preview(self):
        """窄表整表 = 22.275 毫米（预览实测）。

        表宽 = 各列边框盒之和 + 左右外边框（合起来 1 像素 = 0.2646 毫米）。
        """
        from . import library_export

        placements, width = placements_for(NARROW_TABLE)
        value = library_export._preview_table_width_mm(placements, width, 12)
        self.assertAlmostEqual(
            value, NARROW_TABLE_MM, delta=0.02, msg=f"预览模型算出整表 {value:.4f} 毫米，预览实测 {NARROW_TABLE_MM}"
        )

    def test_a_content_sized_column_keeps_the_width_of_the_preview(self):
        """按内容算宽的那一列 = 32.763 毫米（预览实测）。

        这张表有一列的表头是「零售收入/万元」，宽度是内容 + 1.4em 内边距 + 1 像素
        边框算出来的（26.577 + 5.927 + 0.265），不是 2.6em 下限撑着的那种 ——
        内边距那一项要是漏了，这一列就窄 5.93 毫米。
        """
        from . import library_export

        placements, width = placements_for(CONTENT_TABLE)
        columns = library_export._preview_column_widths(placements, width, 12)
        self.assertAlmostEqual(
            columns[0],
            CONTENT_COLUMN_MM,
            delta=0.05,
            msg=f"按内容算宽的那一列是 {columns[0]:.4f} 毫米，预览实测 {CONTENT_COLUMN_MM}",
        )
        self.assertAlmostEqual(
            columns[1],
            NARROW_COLUMN_MM,
            delta=0.02,
            msg=f"另一列是 {columns[1]:.4f} 毫米（该被 2.6em 下限撑着），预览实测 {NARROW_COLUMN_MM}",
        )

    def test_a_content_sized_table_keeps_the_table_width_of_the_preview(self):
        """这张表整表 = 44.033 毫米（预览实测）。"""
        from . import library_export

        placements, width = placements_for(CONTENT_TABLE)
        value = library_export._preview_table_width_mm(placements, width, 12)
        self.assertAlmostEqual(
            value, CONTENT_TABLE_MM, delta=0.05, msg=f"预览模型算出整表 {value:.4f} 毫米，预览实测 {CONTENT_TABLE_MM}"
        )


class WordColumnWidthTests(SimpleTestCase):
    def test_the_word_columns_use_the_preview_model(self):
        """Word 这边算列宽用的就是预览那套模型（每列 11.005 毫米）。

        原来这里是 ``max(8.0mm, 内容 + 5.0mm)``：窄表被 8 毫米的下限卡住，
        每列比预览窄 3 毫米。
        """
        from . import library_export

        placements, width = placements_for(NARROW_TABLE)
        columns = library_export._table_column_widths(placements, width, 12)
        self.assertEqual(len(columns), 2, "尺子自检：列数不对")
        for index, value in enumerate(columns):
            self.assertAlmostEqual(
                value,
                NARROW_COLUMN_MM,
                delta=0.02,
                msg=f"Word 模型里第 {index + 1} 列是 {value:.4f} 毫米，预览实测 {NARROW_COLUMN_MM}",
            )

    def test_the_exported_document_writes_the_preview_column_widths(self):
        """导出的 docx 里 ``w:gridCol`` 就是预览的列宽。

        真 Word 16 实测：写进去 8.008 毫米，纸上是 8.052；写 11.005，
        纸上是 11.016 —— Word 画的列距就等于 ``w:gridCol``，不需要补偿。
        """
        columns = grid_columns_mm(build_document(NARROW_TABLE))
        self.assertEqual(len(columns), 2, f"尺子自检：导出的表有 {len(columns)} 列")
        for index, value in enumerate(columns):
            self.assertAlmostEqual(
                value,
                NARROW_COLUMN_MM,
                delta=0.05,
                msg=f"导出的第 {index + 1} 列宽 {value:.4f} 毫米，预览实测 {NARROW_COLUMN_MM}",
            )

    def test_the_word_columns_use_the_preview_model_for_a_content_sized_table(self):
        """按内容算宽的那一列，Word 侧也是 32.763 毫米（预览实测）。"""
        from . import library_export

        placements, width = placements_for(CONTENT_TABLE)
        columns = library_export._table_column_widths(placements, width, 12)
        self.assertAlmostEqual(
            columns[0],
            CONTENT_COLUMN_MM,
            delta=0.05,
            msg=f"Word 模型里那一列是 {columns[0]:.4f} 毫米，预览实测 {CONTENT_COLUMN_MM}",
        )

    def test_the_exported_document_writes_the_preview_width_of_a_content_sized_column(self):
        """导出的 docx 里，按内容算宽的那一列也是 32.763 毫米。"""
        columns = grid_columns_mm(build_document(CONTENT_TABLE))
        self.assertEqual(len(columns), 2, f"尺子自检：导出的表有 {len(columns)} 列")
        self.assertAlmostEqual(
            columns[0],
            CONTENT_COLUMN_MM,
            delta=0.05,
            msg=f"导出的那一列宽 {columns[0]:.4f} 毫米，预览实测 {CONTENT_COLUMN_MM}",
        )


class DenseTableWidthTests(SimpleTestCase):
    """列数 >= 13 的表在预览里走 ``qb-table-dense``，Word 这边必须跟着换模型。"""

    def test_a_dense_table_drops_the_minimum_column_width(self):
        """dense 表放开 2.6em 下限、内边距收成 0.45em 两侧。

        预览实测：表头列 30.646、数字列 6.185 毫米。
        按普通表算出来是 33.7 / 11.007，每列差 4.8 毫米。
        """
        from . import library_export

        placements, width = placements_for(DENSE_TABLE)
        self.assertEqual(width, 13, "尺子自检：这张表不是 13 列")
        columns = library_export._preview_column_widths(placements, width, 12)
        self.assertAlmostEqual(
            columns[0],
            DENSE_HEADER_COLUMN_MM,
            delta=0.2,
            msg=f"表头列算出来 {columns[0]:.4f} 毫米，预览实测 {DENSE_HEADER_COLUMN_MM}",
        )
        self.assertAlmostEqual(
            columns[1],
            DENSE_NUMBER_COLUMN_MM,
            delta=0.05,
            msg=f"数字列算出来 {columns[1]:.4f} 毫米，预览实测 {DENSE_NUMBER_COLUMN_MM}",
        )
        self.assertAlmostEqual(
            columns[12],
            DENSE_TWO_DIGIT_COLUMN_MM,
            delta=0.2,
            msg=f"两位数那列算出来 {columns[12]:.4f} 毫米，预览实测 {DENSE_TWO_DIGIT_COLUMN_MM}",
        )

    def test_a_dense_table_keeps_the_table_width_of_the_preview(self):
        """dense 整表 = 111.319 毫米（预览实测）。

        按普通表算出来是 165.8 毫米，**宽 54 毫米** —— 屏幕上是一张表，
        导出来成了另一张。
        """
        from . import library_export

        placements, width = placements_for(DENSE_TABLE)
        value = library_export._preview_table_width_mm(placements, width, 12)
        self.assertAlmostEqual(
            value, DENSE_TABLE_MM, delta=0.5, msg=f"预览模型算出整表 {value:.4f} 毫米，预览实测 {DENSE_TABLE_MM}"
        )

    def test_the_exported_dense_document_writes_the_preview_column_widths(self):
        """导出的十三列表，每列就是预览里的宽度（表头 30.646、数字 6.185）。"""
        columns = grid_columns_mm(build_document(DENSE_TABLE))
        self.assertEqual(len(columns), 13, f"尺子自检：导出的表有 {len(columns)} 列")
        self.assertAlmostEqual(
            columns[0],
            DENSE_HEADER_COLUMN_MM,
            delta=0.2,
            msg=f"导出的表头列宽 {columns[0]:.4f} 毫米，预览实测 {DENSE_HEADER_COLUMN_MM}",
        )
        self.assertAlmostEqual(
            columns[1],
            DENSE_NUMBER_COLUMN_MM,
            delta=0.05,
            msg=f"导出的数字列宽 {columns[1]:.4f} 毫米，预览实测 {DENSE_NUMBER_COLUMN_MM}",
        )

    def test_a_twelve_column_table_is_not_dense(self):
        """十二列还没到 dense 门槛，每列还是 11.007 毫米。

        门槛是 13（qb-render.js 的 ``DENSE_TABLE_COLUMNS = 13``），
        门槛写错的话十二列的表会突然瘦一圈。
        """
        from . import library_export

        placements, width = placements_for(TWELVE_TABLE)
        self.assertEqual(width, 12, "尺子自检：这张表不是 12 列")
        columns = library_export._preview_column_widths(placements, width, 12)
        for index, value in enumerate(columns):
            self.assertAlmostEqual(
                value, NORMAL_COLUMN_MM, delta=0.02, msg=f"第 {index + 1} 列是 {value:.4f} 毫米，十二列的表不该走 dense"
            )
