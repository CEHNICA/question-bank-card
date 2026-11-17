"""导出 Word：表格的横向位置要跟预览一样。

预览那一侧的规矩（frontend/styles.css + library.css）：

    .qb-table { margin: 0 auto; }          —— 表在自己的那一列里居中
    .qb-stem  { display: flex; gap: .35em; } —— 题号列 + 正文列
    .print-answer-row { grid-template-columns: 2.4em minmax(0, 1fr); }

真 Chrome 实测（2026-11-17，12pt 字号，178 毫米版心）：

    题干表离版心左缘 17.59 像素 = 4.66 毫米
    答案表离版心左缘 301.69 像素 = 79.80 毫米

Word 原来无条件 ``jc=center``（整栏居中）：题干表落在离版心左缘 56.9 毫米处，
答案表差 5.1 毫米 —— 屏幕上贴着题号右边，导出来飘到版心中间。

Word 这边要写的：
    w:jc left                          —— 左边对齐，不要整栏居中
    w:tblInd = 目标毫米 × 1440/25.4 + 108   —— 见下面那条 108 twips

``w:tblInd`` 量的是**第一格文字的左缘**，不是表格边框的左缘。Word 的
Table Grid 样式给每格留 108 twips（1.905 毫米）的左右内边距，边框还要
再往左退这一格 —— 真 Word 实测：写 tblInd=264（4.66 毫米）时边框只落在
离版心左缘 2.65 毫米处，差的正是这 1.905 毫米。

判据全部用独立字面值（不引用被测模块的常量），免得反向变体把尺子一起放绿。
"""

import unittest

from django.test import TestCase

from . import library_export as export
from .test_library_export import ExportFixture, document_xml, field, units

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
NS = {"w": W[1:-1]}

TABLE = "| 年份 | 产量 |\n| --- | --- |\n| 2023 | 120 |\n| 2024 | 150 |"


class WordTableIndentTests(ExportFixture, TestCase):
    """导出的 docx 里，表格得摆在预览量出来的那个位置上。"""

    def table_xml(self, *, measured=None):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        print_options = {"measured_table_offsets": measured} if measured is not None else None
        payload = self.payload([publication], print_options=print_options)
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        return document_xml(result.content)

    def indent_of(self, xml):
        node = xml.find(".//w:tbl/w:tblPr/w:tblInd", NS)
        self.assertIsNotNone(node, "表格必须写 w:tblInd")
        return int(node.get(f"{W}w"))

    def test_a_measured_table_is_left_aligned_instead_of_centred(self):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload(
            [publication], print_options={"measured_table_offsets": {str(publication.id): {"stem": [4.66]}}}
        )
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        jc = xml.find(".//w:tbl/w:tblPr/w:jc", NS)
        self.assertIsNotNone(jc)
        self.assertEqual(jc.get(f"{W}val"), "left")

    def test_the_indent_carries_the_measured_position_plus_the_cell_margin(self):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload(
            [publication], print_options={"measured_table_offsets": {str(publication.id): {"stem": [4.66]}}}
        )
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        # 4.66 毫米 = 264 twips，加 108 twips 的单元格左边距 = 372 twips。
        self.assertEqual(self.indent_of(xml), 372)

    def test_a_different_measured_position_moves_the_table(self):
        publication = self.publication(stem=TABLE)
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        payload = self.payload(
            [publication], print_options={"measured_table_offsets": {str(publication.id): {"stem": [79.8]}}}
        )
        payload["rendered_fields"][str(publication.id)]["stem"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": rows}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200)
        xml = document_xml(result.content)
        # 79.80 毫米 = 4524 twips，加 108 = 4632 twips。
        self.assertEqual(self.indent_of(xml), 4632)

    def test_without_a_measurement_the_table_falls_back_to_its_column(self):
        xml = self.table_xml()
        # 没有实测值时退回到「在题号右边那一列里居中」，不是整栏居中。
        indent = self.indent_of(xml)
        self.assertGreater(indent, 0)
        jc = xml.find(".//w:tbl/w:tblPr/w:jc", NS)
        self.assertEqual(jc.get(f"{W}val"), "left")


class MeasuredTableOffsetsValidationTests(TestCase):
    """实测偏移写错了不能进导出。"""

    # 题目编号必须是 UUID（前端送来的就是发布题目的 pk）。
    UID = "11111111-2222-3333-4444-555555555555"

    def options(self, measured):
        from . import library_drafts

        return library_drafts._print_options({"measured_table_offsets": measured}, {}, ids=None)

    def test_a_position_outside_the_page_is_refused(self):
        from . import library_drafts

        with self.assertRaises(library_drafts.DraftError):
            self.options({self.UID: {"stem": [200]}})

    def test_an_unknown_slot_is_refused(self):
        from . import library_drafts

        with self.assertRaises(library_drafts.DraftError):
            self.options({self.UID: {"options": [4.66]}})

    def test_too_many_measured_tables_are_refused(self):
        from . import library_drafts

        with self.assertRaises(library_drafts.DraftError):
            self.options({self.UID: {"stem": [4.66] * 51}})

    def test_a_valid_measurement_survives(self):
        result = self.options({self.UID: {"stem": [4.66], "answer": [79.8]}})
        self.assertEqual(result["measured_table_offsets"], {self.UID: {"stem": [4.66], "answer": [79.8]}})


class StemColumnLeftTests(TestCase):
    """题干正文列的左缘要按预览那条两栏网格算。"""

    def test_one_digit_number_leaves_the_column_at_1_point_1_em(self):
        # 12pt 字号：1 位题号 = 0.5em 数字 + 0.25em 句点 + 0.35em 列间隙 = 1.1em
        # 1.1em × 12pt = 13.2 磅 = 4.655 毫米（实测 4.66）。
        self.assertAlmostEqual(export._stem_column_left_mm(12, 1), 4.655, places=2)

    def test_two_digit_numbers_shift_the_column_right(self):
        # 2 位题号 = 2×0.5em + 0.25em + 0.35em = 1.6em × 12pt = 19.2 磅 = 6.773 毫米。
        self.assertAlmostEqual(export._stem_column_left_mm(12, 2), 6.773, places=2)
