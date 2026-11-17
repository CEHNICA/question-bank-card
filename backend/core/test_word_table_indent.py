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

量不到预览位置时（没渲染过预览、或题被拆到跨页续片里），落位退回到
按预览自己的排版规则算 —— 题干正文列会缩到内容宽（窄表贴左），答案正文列
不会（表永远居中）。两条规则差 77 毫米，见下面 ``FallbackTablePlacementTests``。

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


class FallbackTablePlacementTests(ExportFixture, TestCase):
    """没有实测偏移时，兜底落位也得照抄预览的排版规则。

    预览那边的两条规则不一样（真 Chrome 实测，2026-11-18，12pt 字号、178 毫米版心）：

      · 题干正文列 ``.qb-stem-body`` 是 flex item，宽度 =
        min(列可用宽, max(正文宽, 表宽))：
        正文比表窄 → 整列缩到表宽 → 表**贴左**（离版心左缘 4.655 毫米）；
        正文撑满 → 列宽 173.34 毫米 → 表在列里居中（离版心左缘 76.543 毫米）。
      · 答案正文列是 grid 的 ``minmax(0, 1fr)``，宽度恒定 166.78 毫米 →
        表永远在列里居中（离版心左缘 79.822 毫米），答案长短都不影响。

    两条规则差 77 毫米，搞混了窄表就飘到版心正中。

    判据用独立字面值（Chrome 实测换算出来的 twips），不引用被测模块的常量。
    """

    LONG = "补充说明" * 40

    def table_rows(self):
        rows = export._table_source(TABLE, "合成表格")
        for row in rows:
            for cell in row:
                cell["segments"] = field(cell["source"])["blocks"][0]["segments"] if cell["source"] else []
        return rows

    def stem_xml(self, text=""):
        source = (text + "\n\n" + TABLE) if text else TABLE
        publication = self.publication(stem=source)
        blocks = []
        if text:
            # 正文块把正文和它后面的空行一起圈进去，表格块再从表格那行开始 ——
            # 排版块的位置必须首尾相连，中间不能断（后端会校验）。
            blocks.append(
                {
                    "type": "text",
                    "start": 0,
                    "end": units(text) + 2,
                    "segments": [{"type": "text", "start": 0, "end": units(text) + 2}],
                }
            )
            blocks.append(
                {
                    "type": "table",
                    "start": units(text) + 2,
                    "end": units(text) + 2 + units(TABLE),
                    "rows": self.table_rows(),
                }
            )
        else:
            blocks.append({"type": "table", "start": 0, "end": units(TABLE), "rows": self.table_rows()})
        payload = self.payload([publication])
        payload["rendered_fields"][str(publication.id)]["stem"] = {"source": source, "blocks": blocks}
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        return document_xml(result.content)

    def answer_xml(self):
        publication = self.publication(stem="题干", answer=TABLE)
        payload = self.payload([publication], print_options={"document": "answers"})
        payload["rendered_fields"][str(publication.id)]["answer"] = {
            "source": TABLE,
            "blocks": [{"type": "table", "start": 0, "end": units(TABLE), "rows": self.table_rows()}],
        }
        result = self.post(payload)
        self.assertEqual(result.status_code, 200, result.content[:300])
        return document_xml(result.content)

    def indent_of(self, xml):
        node = xml.find(".//w:tbl/w:tblPr/w:tblInd", NS)
        self.assertIsNotNone(node, "表格必须写 w:tblInd")
        return int(node.get(f"{W}w"))

    def test_a_stem_table_without_text_sits_at_the_left_edge_of_the_stem_column(self):
        # 题干正文比表窄 → 正文列缩到表宽 → 表贴左：4.655 毫米 = 264 twips + 108 = 372。
        self.assertEqual(self.indent_of(self.stem_xml()), 372)

    def test_a_stem_table_after_a_long_text_is_centred_in_the_stem_column(self):
        # 正文撑满 → 正文列 173.34 毫米 → 表在列里居中：76.543 毫米 = 4339 + 108 = 4447。
        self.assertEqual(self.indent_of(self.stem_xml(self.LONG)), 4447)

    def test_an_answer_table_is_centred_in_the_answer_column(self):
        # 答案正文列宽度恒定（1fr），表永远居中：79.822 毫米 = 4525 + 108 = 4633。
        self.assertEqual(self.indent_of(self.answer_xml()), 4633)

    def test_the_fallback_never_centres_the_table_in_the_whole_page(self):
        # 整栏居中是 Word 这边一直排错的老毛病：题干表会飘到版心正中（差 56.9 毫米）。
        xml = self.stem_xml()
        jc = xml.find(".//w:tbl/w:tblPr/w:jc", NS)
        self.assertIsNotNone(jc)
        self.assertEqual(jc.get(f"{W}val"), "left")


class PreviewTableWidthTests(TestCase):
    """预览里那张表有多宽，兜底落位要用它算居中位置。"""

    def rows_of(self, raw):
        rows = export._table_source(raw, "合成表格")
        placements = []
        for index, row in enumerate(rows):
            column = 0
            for cell in row:
                cell["segments"] = [{"type": "text", "raw": cell["source"]}]
                placements.append((index, column, cell))
                column += cell["colspan"]
        return placements

    def test_a_two_column_table_matches_the_browser(self):
        # 「年份 | 产量」：每列 max(2.6em, 文字宽 + 1.4em + 1 像素) = 55.4 像素，
        # 两列 110.8 + 外边框 1 = 111.8 像素 = 29.567 毫米（Chrome 实测 29.5672）。
        # 差 0.02 毫米是字体的实际字宽不是整 em（实测「年份」31.96 像素），
        # 对落位的影响不到 0.01 毫米。
        placements = self.rows_of("| 年份 | 产量 |\n| --- | --- |\n| 2023 | 120 |")
        self.assertAlmostEqual(export._preview_table_width_mm(placements, 2, 12), 29.5672, places=1)

    def test_a_three_column_table_matches_the_browser(self):
        # 「年份 | 产量（辆） | 备注」：Chrome 实测 56.9185 毫米。
        placements = self.rows_of("| 年份 | 产量（辆） | 备注 |\n| --- | --- | --- |\n| 2023 | 120 | 平稳 |")
        self.assertAlmostEqual(export._preview_table_width_mm(placements, 3, 12), 56.9185, places=1)

    def test_the_stem_text_width_matches_the_browser(self):
        # 十三个汉字（字号 16 像素，一个字 4.2333 毫米）= 55.033 毫米。
        blocks = [{"type": "text", "segments": [{"type": "text", "raw": "某工厂近三年的产量如下表："}]}]
        self.assertAlmostEqual(export._field_max_text_width_mm(blocks, 12), 55.0333, places=3)

    def test_the_stem_text_width_is_the_widest_line_not_the_sum(self):
        # 浏览器算 max-content 时只有源码里的真换行才算断点：取最宽的那一行，
        # 不是把几行加起来。
        blocks = [{"type": "text", "segments": [{"type": "text", "raw": "甲乙丙\n丁戊"}]}]
        self.assertAlmostEqual(export._field_max_text_width_mm(blocks, 12), 12.7, places=2)


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
