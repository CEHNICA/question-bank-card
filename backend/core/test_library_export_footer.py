"""导出 Word 的页脚：题数、页码域、对齐、字号。

页脚是用域（PAGE）写的 —— python-docx 只写域代码，不写域的结果，
域的结果是 Word 打开时才算出来的。所以这里锁的是「写对了没有」，
「算出来对不对」由真实 Word 渲染的尺子验（_audit41.py：实测页码逐页连续、
总题数一致、页脚落在页面底部）。

2026-11-16 实测：真实产物里页脚是「共 9 题 · 第 1 页」「共 9 题 · 第 2 页」，
Word 报 2 页，PDF 也是 2 页，页脚底 285.55 毫米 / 页高 296.97 毫米。
"""

import io

from django.test import SimpleTestCase

from . import library_export


def item(index, kind="single_choice"):
    return {
        "id": f"{index:08d}-1111-4111-8111-111111111111",
        "type": kind,
        "where": f"第 {index} 题",
        "content": {},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": f"第{index}题的题干"}]}]},
        "images": [],
    }


def captured(count, kinds=None):
    kinds = kinds or ["single_choice"] * count
    return [item(index + 1, kind) for index, kind in enumerate(kinds[:count])]


def options(**overrides):
    base = {
        "font_size": 12,
        "student_info": False,
        "answer_space": 0,
        "answer_space_overrides": {},
        "question_breaks": [],
        "pagination": "compact",
        "answer_layout": "appendix",
        "figure_overrides": {},
        "measured_figure_widths": {},
    }
    base.update(overrides)
    return base


def build(count=3, kinds=None, **option_overrides):
    """导出后的 docx 字节（_document 直接返回字节流）。"""
    return library_export._document(captured(count, kinds), "导出体检卷", options(**option_overrides), "questions")


def open_document(blob):
    from docx import Document

    return Document(io.BytesIO(blob))


def footer_paragraph(blob):
    return open_document(blob).sections[0].footer.paragraphs[0]


class FooterTests(SimpleTestCase):
    def test_the_footer_counts_the_questions(self):
        """页脚里的总题数就是这份卷子的题数。"""
        document = build(count=3)
        self.assertIn("共 3 题", footer_paragraph(document).text)

    def test_the_count_follows_the_number_of_questions(self):
        """不能写死：换题数就得换。"""
        for count in (1, 2, 5):
            document = build(count=count)
            self.assertIn(f"共 {count} 题", footer_paragraph(document).text)

    def test_the_count_sums_every_group(self):
        """不同题型分在不同大题下，总数是全部题，不是某一组的。"""
        document = build(count=4, kinds=["single_choice", "single_choice", "fill_blank", "true_false"])
        self.assertIn("共 4 题", footer_paragraph(document).text)

    def test_the_page_number_is_a_real_field(self):
        """页码必须是 PAGE 域，不能是写死的数字 —— 域才会算出第几页。"""
        from docx.oxml.ns import qn

        paragraph = footer_paragraph(build(count=2))
        fields = paragraph._p.findall(qn("w:fldSimple"))
        self.assertEqual(len(fields), 1, "页脚里应当正好有一个域")
        self.assertEqual(fields[0].get(qn("w:instr")), "PAGE")

    def test_the_footer_is_right_aligned(self):
        """页脚贴着右页边，和预览一致。"""
        paragraph = footer_paragraph(build(count=2))
        self.assertEqual(int(paragraph.alignment), 2, "页脚应当右对齐")

    def test_the_footer_is_small_print(self):
        """页脚是 9 号字，比正文小一号，别跟正文抢眼。"""
        paragraph = footer_paragraph(build(count=2))
        sizes = [run.font.size.pt for run in paragraph.runs if run.font.size is not None]
        self.assertTrue(sizes, "页脚里的文字没有字号")
        self.assertEqual(set(sizes), {9.0})

    def test_the_footer_sits_below_the_text_area(self):
        """页脚离页面底边 8 毫米，不能跑到正文区里。"""
        from docx.shared import Mm

        document = open_document(build(count=2))
        section = document.sections[0]
        self.assertLessEqual(section.footer_distance, Mm(10))
        # 正文下边距 18 毫米，页脚在它下面：底边距 - 页脚距离 > 0 才有位置
        self.assertGreater(section.bottom_margin - section.footer_distance, Mm(5))
