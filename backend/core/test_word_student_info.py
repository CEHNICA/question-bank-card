"""导出 Word 的考生信息行（``.print-info``）要跟打印预览一模一样。

预览 `frontend/library.css:929`：
    .print-info { text-align: center; margin: 12px 0 22px; font-size: 14px; color: #333 }
内容（frontend/library.js 与 backend/core/library_pdf.py 的 _DRIVER 一致）：
    姓名 ____________　班级 ____________　得分 ________

Chrome 实测（_probe93.py）：
  · 文字：'姓名 ____________\\u3000班级 ____________\\u3000得分 ________'
    —— 三个词后面各一个**半角空格**，词之间是**全角空格 U+3000**；
    下划线段长度 12 / 12 / **8**（得分后面只有 8 个）；
  · 字号 14px = 10.5 磅；颜色 rgb(51, 51, 51) = #333333；对齐 center；
  · 行高 21px = 15.75 磅；字体 "Microsoft YaHei UI", "Microsoft YaHei", …（界面字体，
    这一行不在 .qb-question 里，拿不到卷面字体）；
  · margin 12px 上 / 22px 下。

Word 这边原来写的是「姓名：____________    班级：____________    得分：____________」：
多了三个冒号、分隔符是四个半角空格、得分后是 12 个下划线，字号用了正文 12 磅，
颜色纯黑，上下外边距没写 —— 结果整页从这一行往下**整体上移 7.3 毫米**
（预览第 1 页的「一、选择题」实测 y 差 -7.31mm）。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

QUESTION = "55555555-5555-4555-8555-555555555555"

# Chrome 实测的预览值（独立字面值，不引用被测模块的常量）。
PREVIEW_TEXT = "姓名 ____________　班级 ____________　得分 ________"
PREVIEW_FONT_SIZE_PT = 10.5  # 14px
PREVIEW_COLOR = "333333"
PREVIEW_SPACE_BEFORE_PT = 9.0  # 12px
PREVIEW_SPACE_AFTER_PT = 16.5  # 22px
PREVIEW_LINE_HEIGHT_PT = 15.75  # 21px
PREVIEW_LATIN_FONT = "Microsoft YaHei UI"
PREVIEW_EAST_ASIA_FONT = "Microsoft YaHei"


def build_document(student_info=True, font_size=12):
    """走**真实导出路径**造一份带考生信息行的 Word。"""
    from . import library_export

    item = {
        "id": QUESTION,
        "type": "single_choice",
        "where": "第 1 题",
        "content": {"options": {}, "body_mode": "text"},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": "题干示例。"}]}]},
        "images": [],
    }
    options = {
        "font_size": font_size,
        "student_info": student_info,
        "answer_space": "none",
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
    }
    data = library_export._document([item], "第六十轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def info_paragraph(document):
    """考生信息行：正文里第一个不是标题、且含「姓名」的段落。"""
    for paragraph in document.paragraphs:
        if "姓名" in paragraph.text:
            return paragraph
    raise AssertionError("尺子自检：导出结果里没有考生信息行")


def run_color(run):
    properties = run._element.find(qn("w:rPr"))
    if properties is None:
        return None
    node = properties.find(qn("w:color"))
    if node is None:
        return None
    return node.get(qn("w:val"))


def east_asia_font(run):
    properties = run._element.find(qn("w:rPr"))
    if properties is None:
        return None
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        return None
    return fonts.get(qn("w:eastAsia"))


def latin_font(run):
    properties = run._element.find(qn("w:rPr"))
    if properties is None:
        return None
    fonts = properties.find(qn("w:rFonts"))
    if fonts is None:
        return None
    return fonts.get(qn("w:ascii"))


class PreviewMeasurementTests(SimpleTestCase):
    """尺子自检：预览的像素值换算成 Word 的磅值。"""

    def test_fourteen_pixels_is_ten_and_a_half_points(self):
        """14px × 0.75 = 10.5 磅。"""
        self.assertEqual(14 * 0.75, PREVIEW_FONT_SIZE_PT)

    def test_the_margins_convert_to_points(self):
        """12px → 9 磅，22px → 16.5 磅。"""
        self.assertEqual(12 * 0.75, PREVIEW_SPACE_BEFORE_PT)
        self.assertEqual(22 * 0.75, PREVIEW_SPACE_AFTER_PT)


class StudentInfoTextTests(SimpleTestCase):
    """这一行的文字要和预览逐字相同。"""

    def test_the_text_matches_the_preview(self):
        """不多冒号、不换分隔符、下划线个数也不多不少。"""
        paragraph = info_paragraph(build_document())
        self.assertEqual(paragraph.text, PREVIEW_TEXT, f"考生信息行是 {paragraph.text!r}，预览实测 {PREVIEW_TEXT!r}")

    def test_the_underscore_runs_match_the_preview(self):
        """下划线段长度 12 / 12 / 8 —— 得分后面只有 8 个。"""
        import re

        paragraph = info_paragraph(build_document())
        runs = [len(match.group()) for match in re.finditer(r"_+", paragraph.text)]
        self.assertEqual(runs, [12, 12, 8], f"下划线段长度是 {runs}，预览实测 [12, 12, 8]")

    def test_the_words_are_separated_by_a_full_width_space(self):
        """词之间是全角空格 U+3000，不是半角空格。"""
        paragraph = info_paragraph(build_document())
        self.assertIn("　", paragraph.text, "预览里词之间是全角空格 U+3000")
        self.assertNotIn("  ", paragraph.text, "预览里没有两个连着的半角空格")


class StudentInfoStyleTests(SimpleTestCase):
    """字号、颜色、字体。"""

    def test_the_font_size_is_ten_and_a_half_points(self):
        """预览 14px = 10.5 磅，不是正文的 12 磅。"""
        paragraph = info_paragraph(build_document())
        sizes = [run.font.size.pt for run in paragraph.runs if run.font.size is not None]
        self.assertTrue(sizes, "尺子自检：考生信息行没有字号")
        for size in sizes:
            self.assertEqual(size, PREVIEW_FONT_SIZE_PT, f"字号是 {size} 磅，预览实测 10.5 磅")

    def test_the_color_is_dark_grey(self):
        """预览 rgb(51,51,51)，不是纯黑。"""
        paragraph = info_paragraph(build_document())
        for run in paragraph.runs:
            self.assertEqual(run_color(run), PREVIEW_COLOR, "预览实测颜色 #333333")

    def test_the_font_is_the_interface_font(self):
        """这一行在预览里是微软雅黑，不是卷面的宋体 / Times New Roman。"""
        paragraph = info_paragraph(build_document())
        for run in paragraph.runs:
            self.assertEqual(east_asia_font(run), PREVIEW_EAST_ASIA_FONT, "中文字体不是微软雅黑")
            self.assertEqual(latin_font(run), PREVIEW_LATIN_FONT, "西文字体不是微软雅黑")


class StudentInfoSpacingTests(SimpleTestCase):
    """上下外边距与行高 —— 少了它们，整页会整体上移 7.3 毫米。"""

    def test_the_space_before_is_twelve_pixels(self):
        """12px 上外边距。"""
        paragraph = info_paragraph(build_document())
        before = paragraph.paragraph_format.space_before
        self.assertIsNotNone(before, "没有写上外边距")
        self.assertAlmostEqual(before.pt, PREVIEW_SPACE_BEFORE_PT, places=3)

    def test_the_space_after_is_twenty_two_pixels(self):
        """22px 下外边距。"""
        paragraph = info_paragraph(build_document())
        after = paragraph.paragraph_format.space_after
        self.assertIsNotNone(after, "没有写下外边距")
        self.assertAlmostEqual(after.pt, PREVIEW_SPACE_AFTER_PT, places=3)

    def test_the_line_height_is_twenty_one_pixels(self):
        """21px 行盒（14px × 1.5）。"""
        paragraph = info_paragraph(build_document())
        spacing = paragraph._p.find(qn("w:pPr") + "/" + qn("w:spacing"))
        self.assertIsNotNone(spacing, "没有写行高")
        line = spacing.get(qn("w:line"))
        self.assertIsNotNone(line, "w:spacing 里没有 w:line")
        self.assertAlmostEqual(int(line) / 20, PREVIEW_LINE_HEIGHT_PT, places=3)


class StudentInfoAbsenceTests(SimpleTestCase):
    """答案卷里没有考生信息行（预览也是没有）。"""

    def test_the_answers_paper_has_no_student_info(self):
        """mode=answers 时不写这一行。"""
        from . import library_export

        item = {
            "id": QUESTION,
            "type": "single_choice",
            "where": "第 1 题",
            "content": {"options": {}, "body_mode": "text"},
            "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": "题干示例。"}]}]},
            "selected": {"answer": "", "analysis": ""},
            "images": [],
        }
        options = {
            "font_size": 12,
            "student_info": True,
            "answer_space": "none",
            "pagination": "none",
            "question_breaks": set(),
            "answer_layout": "appendix",
        }
        data = library_export._document([item], "第六十轮", options, "answers")
        from docx import Document

        document = Document(io.BytesIO(data))
        self.assertFalse(any("姓名" in paragraph.text for paragraph in document.paragraphs), "答案卷里不该有考生信息行")
