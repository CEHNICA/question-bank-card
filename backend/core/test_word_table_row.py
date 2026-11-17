"""导出 Word：表格的行高和单元格的上下留白要跟预览一样。

预览那一侧的规矩（frontend/styles.css）：

    .qb-table td, .qb-table th { min-width: 2.6em; height: 2.1em; padding: .2em .7em; ... }

Chrome 实测（2026-11-17，12pt 字号）：
    单元格 height 33.59px = 2.1em，上下内边距各 3.2px = .2em，
    行与行之间 33.59px（= 25.2 磅），文字在这块白里垂直居中。

Word 原来只写 1.6em 的行距、上下一点白都不留：真 Word 实测每行 19.8 磅，
比预览矮 5.4 磅，一张四行的表就差出 7.5 毫米 —— 屏幕上明明是松开的，
导出来挤成一团。

Word 这边要写的：
    w:trHeight hRule="atLeast" val=408   （内容区 1.7em = 2.1em − 2×.2em）
    w:tcMar top/bottom = 48 twips          （.2em = 2.4 磅）
    w:vAlign center                       （文字垂直居中）

注意 Word 的 w:trHeight 量的是「内容区」，单元格边距算在它外面
（实测：trHeight 25.2 磅 + 上下 2.4 磅 = 行高 30.6 磅），所以那个数要减掉边距。

判据全部用独立字面值（不引用被测模块的常量），免得反向变体把尺子一起放绿。
"""

import unittest
from pathlib import Path

from django.test import TestCase

from . import library_export as export
from .test_library_export import ExportFixture, document_xml, field, units

STYLES = Path(__file__).resolve().parents[2] / "frontend" / "styles.css"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

TABLE = "| 年份 | 产量 |\n| --- | --- |\n| 2023 | 120 |\n| 2024 | 150 |"


class WordTableRowTests(ExportFixture, TestCase):
    """导出的 docx 里，每一行都得按预览的规矩来。"""

    def table_xml(self, *, print_options=None):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload([publication], print_options=print_options)
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        return document_xml(result.content)

    def test_every_row_asks_for_the_height_the_preview_shows(self):
        xml = self.table_xml()
        heights = xml.findall(".//w:tr/w:trPr/w:trHeight", {"w": W[1:-1]})
        self.assertEqual(len(heights), 3)
        for node in heights:
            self.assertEqual(node.get(f"{W}hRule"), "atLeast")
            # 12pt 字号：内容区 1.7em = 20.4 磅 = 408 twips
            self.assertEqual(node.get(f"{W}val"), "408")

    def test_cells_keep_the_preview_padding_above_and_below(self):
        xml = self.table_xml()
        margins = xml.findall(".//w:tbl//w:tc/w:tcPr/w:tcMar", {"w": W[1:-1]})
        self.assertEqual(len(margins), 6)
        for margin in margins:
            top = margin.find(f"{W}top")
            bottom = margin.find(f"{W}bottom")
            self.assertIsNotNone(top, "单元格上面要留 .2em 的白")
            self.assertIsNotNone(bottom, "单元格下面要留 .2em 的白")
            # 12pt 字号：.2em = 2.4 磅 = 48 twips
            self.assertEqual(top.get(f"{W}w"), "48")
            self.assertEqual(bottom.get(f"{W}w"), "48")

    def test_cell_text_is_centred_vertically(self):
        xml = self.table_xml()
        alignments = xml.findall(".//w:tbl//w:tc/w:tcPr/w:vAlign", {"w": W[1:-1]})
        self.assertEqual(len(alignments), 6)
        for node in alignments:
            self.assertEqual(node.get(f"{W}val"), "center")


class WordTableRowFollowsFontSizeTests(ExportFixture, TestCase):
    """字号选项一改，行高和留白都得跟着改。"""

    def export_table(self, font_size):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload([publication], print_options={"font_size": font_size})
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        return document_xml(result.content)

    def test_a_bigger_font_size_makes_the_row_taller(self):
        # 14pt：内容区 1.7em = 23.8 磅 = 476 twips
        xml = self.export_table(14)
        height = xml.find(".//w:tr/w:trPr/w:trHeight", {"w": W[1:-1]})
        self.assertIsNotNone(height)
        self.assertEqual(height.get(f"{W}val"), "476")
        # 16pt：内容区 1.7em = 27.2 磅 = 544 twips
        xml = self.export_table(16)
        height = xml.find(".//w:tr/w:trPr/w:trHeight", {"w": W[1:-1]})
        self.assertIsNotNone(height)
        self.assertEqual(height.get(f"{W}val"), "544")

    def test_a_bigger_font_size_makes_the_padding_taller(self):
        # 14pt：.2em = 2.8 磅 = 56 twips；16pt：.2em = 3.2 磅 = 64 twips
        for font_size, expected in ((14, "56"), (16, "64")):
            xml = self.export_table(font_size)
            margin = xml.find(".//w:tc/w:tcPr/w:tcMar", {"w": W[1:-1]})
            self.assertIsNotNone(margin, "单元格上下要留白")
            self.assertEqual(margin.find(f"{W}top").get(f"{W}w"), expected)


class PreviewTableRowBaselineTests(unittest.TestCase):
    """预览那侧的规矩不能悄悄改掉。"""

    def test_the_preview_table_cells_are_two_point_one_em_tall_with_padding(self):
        css = STYLES.read_text(encoding="utf-8")
        self.assertIn("height: 2.1em", css)
        self.assertIn("padding: .2em .7em", css)
        self.assertIn(".qb-table td, .qb-table th", css)


class WordTablePaginationTests(ExportFixture, TestCase):
    """Word 自己算分页的时候，表格得按真实行高算。"""

    def test_the_table_is_measured_at_its_real_row_height(self):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload([publication])
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        table = xml.find(".//w:tbl", {"w": W[1:-1]})
        self.assertIsNotNone(table)
        rows = table.findall("w:tr", {"w": W[1:-1]})
        # 预览里每一行 2.1em（12pt 字号 = 2.1 × 12 × 25.4/72 毫米）。
        # 旧算法只按文字高度算（每行 5.7 毫米），比真实版式矮一大截。
        measured = export._unit_height([table], 12)
        self.assertGreaterEqual(measured, len(rows) * 2.1 * 12 * 25.4 / 72 - 0.01)
