"""管道表格混在普通文字里送回来，成品卷上会印出一排竖线。

识读提示词要求模型把表格写成 Markdown 管道表（`readers.py` 的表格那一条），
浏览器导出前会把它切成 `type: "table"` 块再送回来。但只要客户端漏了这一步，
管道表就会以普通文字的身份一路通过：HTML 表格有「表格尚未转换」的拒绝，
Markdown 表格没有，成品卷上就是一串 `| 星期 | 一 |` 而老师毫无察觉。
"""

from django.test import SimpleTestCase

from . import library_export


def u16(text):
    return len(text.encode("utf-16-le")) // 2


def as_text(official, start, end):
    """客户端把整段当普通文字送回来的样子。"""
    return {
        "source": official,
        "blocks": [
            {"type": "text", "start": start, "end": end, "segments": [{"type": "text", "start": start, "end": end}]}
        ],
    }


TABLE = "| 星期 | 一 | 二 |\n|---|---|---|\n| 增减 | +5 | -2 |"
LEAD = "如表是某周的生产情况：\n"


class PipeTableAsPlainTextTests(SimpleTestCase):
    def test_pipe_table_inside_a_text_segment_is_refused(self):
        official = LEAD + TABLE + "\n求总数。"
        with self.assertRaises(library_export.ExportError) as caught:
            library_export._field(as_text(official, 0, u16(official)), official, "第 1 题 · 题干")
        self.assertIn("表格尚未转换", str(caught.exception))

    def test_pipe_table_at_the_very_start_is_refused(self):
        with self.assertRaises(library_export.ExportError) as caught:
            library_export._field(as_text(TABLE, 0, u16(TABLE)), TABLE, "第 1 题 · 题干")
        self.assertIn("表格尚未转换", str(caught.exception))

    def test_html_table_is_still_refused(self):
        official = "如下表：<table><tr><th>a</th></tr></table>"
        with self.assertRaises(library_export.ExportError) as caught:
            library_export._field(as_text(official, 0, u16(official)), official, "第 1 题 · 题干")
        self.assertIn("表格尚未转换", str(caught.exception))

    def test_absolute_value_is_not_a_table(self):
        # |x|=2 是绝对值，不是表格 —— 误报会把正常题目的导出也堵死
        official = "|x| = 2 且 |y| = 3"
        library_export._field(as_text(official, 0, u16(official)), official, "第 1 题 · 题干")

    def test_latex_absolute_value_hits_the_formula_guard_not_the_table_one(self):
        # 两道闸门各管各的：$|x|=2$ 该说的是「公式尚未转换」，不能报成表格
        official = "若 $|x|=2$，求 $x$。"
        with self.assertRaises(library_export.ExportError) as caught:
            library_export._field(as_text(official, 0, u16(official)), official, "第 1 题 · 题干")
        self.assertIn("公式尚未转换", str(caught.exception))

    def test_ordinary_math_text_passes(self):
        official = "在 Rt△ABC 中，∠C=90°，求 tan A。"
        library_export._field(as_text(official, 0, u16(official)), official, "第 1 题 · 题干")


class ProperTableBlockTests(SimpleTestCase):
    """正确切好的表格块必须照旧通过 —— 这条守卫不能把客户端堵死。"""

    def test_a_correctly_segmented_pipe_table_is_accepted(self):
        official = LEAD + TABLE + "\n求总数。"
        head = LEAD
        tail = "\n求总数。"
        expected = library_export._table_source(TABLE, "第 1 题")
        blocks = [
            {
                "type": "text",
                "start": 0,
                "end": u16(head),
                "segments": [{"type": "text", "start": 0, "end": u16(head)}],
            },
            {
                "type": "table",
                "start": u16(head),
                "end": u16(head) + u16(TABLE),
                "rows": [
                    [{**cell, "segments": [{"type": "text", "start": 0, "end": u16(cell["source"])}]} for cell in row]
                    for row in expected
                ],
            },
            {
                "type": "text",
                "start": u16(head) + u16(TABLE),
                "end": u16(official),
                "segments": [{"type": "text", "start": u16(head) + u16(TABLE), "end": u16(official)}],
            },
        ]
        result = library_export._field({"source": official, "blocks": blocks}, official, "第 1 题 · 题干")
        self.assertEqual([block["type"] for block in result], ["text", "table", "text"])
        self.assertEqual(
            [[cell["source"] for cell in row] for row in result[1]["rows"]],
            [["星期", "一", "二"], ["增减", "+5", "-2"]],
        )
