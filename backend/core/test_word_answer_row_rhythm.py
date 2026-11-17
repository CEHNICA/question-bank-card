"""第六十三轮守护：导出 Word 的**答案区纵向节奏**要跟打印预览一致。

预览真值（Chrome 实测 DOM + 真实预览 PDF，见 _probe100.py）：

    版心      .exam-page { padding: 18mm 16mm }
    标题      .print-section { margin: 12px 0 6px; font-size: 16px; line-height: 24px }
    答案行    .print-answer-row { margin: 6px 0; font-size: 12pt; line-height: 1.35 }
              → 上下外边距 6px = 4.5 磅；行盒 21.6px = 16.2 磅
    解析正文  .solution-paragraph { margin: 3pt 0 }

    纸上位置  预览 PDF 第 2 页：标题 22.180mm、答案 30.948mm、解析 36.769mm、正文 43.984mm

**为什么照抄了预览的外边距还不够**（第六十三轮实测）：

    Chrome 把多出来的行距一半放上、一半放下 —— 字顶在行盒里居中
      标题行盒 18 磅 → 字顶偏移 3.0 磅
      答案行盒 16.2 磅 → 字顶偏移 1.5 磅
    Word 把多出来的空间全放在基线上方 —— 字顶几乎贴着行盒顶

    于是「标题字顶 → 答案字顶」这段距离：
      预览 24.84 磅   Word 20.46 磅   差 4.38 磅
    其中 0.885 磅是标题自身的字顶残差（SimHei 在 Chrome 是 Type3、在 Word 是
    CID，ascent 度量不同），补不了；净差 3.5 磅要靠段间距补。

    实测（真 Word 导 PDF 再量，_probe103.py）：补 3.0 → 偏 −0.190 毫米，
    补 3.5 → 偏 +0.018 毫米，补 4.0 → 偏 +0.233 毫米 —— 3.5 最准。

另外解析正文那一段要单独补 1.5 磅：预览里那一行的行盒被行内公式撑高了
（行盒顶离上一行底 3 磅，字顶却比行盒顶还高 1.52 磅），Word 的公式是行内
对象，行盒不会撑成那个形状。

这个文件锁四件事：
  1) 预览那边的真值没变（外边距、行高）—— 变了下面的补偿就失效，尺子先红；
  2) 第一个答案块的段前距 = 预览的 4.5 磅 + 3.5 磅补偿；
  3) 补偿**只**给第一个答案块（后面的答案块之间是预览折叠后的 12px = 9 磅）；
  4) 解析正文的第一段拿 1.5 磅补偿，后面的解析段不拿。
"""

from __future__ import annotations

import io
import re

from django.conf import settings
from django.test import SimpleTestCase

from . import library_export

# 预览真值（独立写死，不引用被测模块的常量）
PREVIEW_ANSWER_ROW_MARGIN_PX = 6
PREVIEW_ANSWER_ROW_MARGIN_PT = 4.5  # 6px × 0.75
PREVIEW_ANSWER_ROW_LINE_HEIGHT_EM = 1.35
PREVIEW_ANSWER_ROW_LINE_HEIGHT_PT = 16.2  # 12pt × 1.35
PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT = 3
# Word 行盒模型与 Chrome 的差（实测，见模块 docstring）
WORD_LINE_BOX_LEADING_COMPENSATION_PT = 3.5
PREVIEW_SOLUTION_LINE_BOX_COMPENSATION_PT = 1.5

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def item(index=1, analysis_text=None):
    analysis = analysis_text or f"第{index}题的解析"
    return {
        "id": f"{index:08d}-1111-4111-8111-111111111111",
        "type": "single_choice",
        "where": f"第 {index} 题",
        "content": {"stem": f"第{index}题的题干", "options": {}, "origin": "", "body_mode": "text"},
        "extras": {},
        "selected": {"answer": "B", "analysis": analysis},
        "ai": False,
        "hash": "",
        "fields": {
            "stem": [{"type": "text", "segments": [{"type": "text", "raw": f"第{index}题的题干"}]}],
            "answer": [{"type": "text", "segments": [{"type": "text", "raw": "B"}]}],
            "analysis": [{"type": "text", "segments": [{"type": "text", "raw": analysis}]}],
        },
        "images": [],
        "solution_images": [],
        "solution_id": None,
        "selection_revision": None,
        "solution_explicit": False,
    }


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


def build(mode="combined", count=1, analysis_text=None, **option_overrides):
    data = library_export._document(
        [item(index, analysis_text=analysis_text) for index in range(1, count + 1)],
        "答案区节奏体检卷",
        options(**option_overrides),
        mode,
    )
    from docx import Document

    return Document(io.BytesIO(data))


def body_paragraphs(document):
    return list(document.element.body.findall("w:p", NS))


def text_of(paragraph):
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))


def spacing_of(paragraph):
    return paragraph.find("w:pPr/w:spacing", NS)


def twips_of(paragraph, name):
    spacing = spacing_of(paragraph)
    if spacing is None:
        return None
    value = spacing.get(f"{{{NS['w']}}}{name}")
    return None if value is None else int(value)


def answer_paragraphs(document):
    """答案段：正文以「答案：」结尾的那几段（每题一个）。"""
    return [paragraph for paragraph in body_paragraphs(document) if text_of(paragraph).strip().endswith("答案：B")]


def heading_paragraph(document):
    for paragraph in body_paragraphs(document):
        if text_of(paragraph).strip() == "参考答案与解析":
            return paragraph
    raise AssertionError("文档里没有「参考答案与解析」这一行")


def analysis_body_paragraphs(document):
    """解析正文段：以解析文字开头、且不是「解析：」标签的那几段。"""
    return [
        paragraph
        for paragraph in body_paragraphs(document)
        if text_of(paragraph).strip().startswith("第") and "题的解析" in text_of(paragraph)
    ]


class PreviewTruthTests(SimpleTestCase):
    """尺子自检：预览那边到底是什么，别让尺子自己过期。"""

    def test_answer_row_margin_is_six_pixels(self):
        css = open(f"{settings.FRONTEND_ROOT}/library.css", encoding="utf-8").read()
        match = re.search(r"\.print-answer-row\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "尺子自检：library.css 里找不到 .print-answer-row")
        self.assertIn(
            f"margin: {PREVIEW_ANSWER_ROW_MARGIN_PX}px 0",
            match.group(1),
            f"尺子自检：.print-answer-row 的外边距变了：{match.group(1)}",
        )
        self.assertEqual(PREVIEW_ANSWER_ROW_MARGIN_PX * 0.75, PREVIEW_ANSWER_ROW_MARGIN_PT)

    def test_print_media_keeps_the_answer_row_line_height(self):
        css = open(f"{settings.FRONTEND_ROOT}/library.css", encoding="utf-8").read()
        # 这条规则是逗号分隔的多选择器：
        #     .print-flow .print-answer-row, .exam-page .print-answer-row,
        #     .print-flow .qb-analysis, .exam-page .qb-analysis { line-height: 1.35; }
        # 所以从选择器里跨过逗号去找大括号。
        match = re.search(r"\.exam-page \.print-answer-row[^{]*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "尺子自检：library.css 里找不到 .exam-page .print-answer-row")
        self.assertIn(
            f"line-height: {PREVIEW_ANSWER_ROW_LINE_HEIGHT_EM}",
            match.group(1),
            f"尺子自检：打印态答案行的行高变了：{match.group(1)}",
        )
        self.assertAlmostEqual(12 * PREVIEW_ANSWER_ROW_LINE_HEIGHT_EM, PREVIEW_ANSWER_ROW_LINE_HEIGHT_PT, places=6)

    def test_solution_paragraph_margin_is_three_points(self):
        css = open(f"{settings.FRONTEND_ROOT}/library.css", encoding="utf-8").read()
        match = re.search(r"\.solution-paragraph\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "尺子自检：library.css 里找不到 .solution-paragraph")
        self.assertIn(
            f"margin: {PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT}pt 0",
            match.group(1),
            f"尺子自检：.solution-paragraph 的外边距变了：{match.group(1)}",
        )


class AnswerRowRhythmTests(SimpleTestCase):
    """答案区在纸上跟预览差的那段距离，得补回来。"""

    def test_the_first_answer_block_leads_by_the_preview_margin_plus_the_compensation(self):
        for mode in ("combined", "answers"):
            with self.subTest(mode=mode):
                document = build(mode)
                answer = answer_paragraphs(document)[0]
                self.assertEqual(
                    twips_of(answer, "before"),
                    int((PREVIEW_ANSWER_ROW_MARGIN_PT + WORD_LINE_BOX_LEADING_COMPENSATION_PT) * 20),
                    "第一个答案块的段前距 = 预览的 6px（4.5 磅）+ Word 行盒模型补偿 3.5 磅",
                )

    def test_only_the_first_answer_block_gets_the_compensation(self):
        document = build("combined", count=2)
        answers = answer_paragraphs(document)
        self.assertEqual(len(answers), 2, "尺子自检：应该有两段答案")
        self.assertEqual(
            twips_of(answers[1], "before"),
            0,
            "补偿只给第一个答案块：后面的答案块之间是预览里折叠后的 12px = 9 磅（写在上一段的段后距上）",
        )

    def test_the_section_heading_keeps_the_preview_margin_below_it(self):
        document = build("combined")
        self.assertEqual(
            twips_of(heading_paragraph(document), "after"),
            int(PREVIEW_ANSWER_ROW_MARGIN_PT * 20),
            "标题的下外边距照抄预览的 6px = 4.5 磅",
        )

    def test_the_first_analysis_paragraph_gets_the_formula_line_box_compensation(self):
        document = build("combined")
        bodies = analysis_body_paragraphs(document)
        self.assertTrue(bodies, "尺子自检：找不到解析正文段")
        self.assertEqual(
            twips_of(bodies[0], "before"),
            int((PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT + PREVIEW_SOLUTION_LINE_BOX_COMPENSATION_PT) * 20),
            "解析正文第一段 = 预览的 3pt + Chrome 行盒被公式撑高的 1.5 磅",
        )

    def test_the_analysis_label_row_keeps_no_space_around_it(self):
        document = build("combined")
        labels = [paragraph for paragraph in body_paragraphs(document) if text_of(paragraph).strip() == "解析："]
        self.assertEqual(len(labels), 1, "尺子自检：应该有一段「解析：」")
        self.assertEqual(twips_of(labels[0], "before"), 0, "「解析：」标签段前不该有距离")
        self.assertEqual(twips_of(labels[0], "after"), 0, "「解析：」标签段后不该有距离")

    def test_later_analysis_paragraphs_do_not_get_the_compensation(self):
        document = build("combined", analysis_text="第一段\n第二段")
        bodies = [
            paragraph for paragraph in body_paragraphs(document) if text_of(paragraph).strip() in {"第一段", "第二段"}
        ]
        self.assertEqual(len(bodies), 2, "尺子自检：解析应该被拆成两段")
        self.assertEqual(
            twips_of(bodies[1], "before"),
            None,
            "补偿只给紧跟「解析：」标签的那一段：预览里后面的解析段之间是折叠后的 3 磅，补了就多一截",
        )

    def test_later_analysis_parts_do_not_get_the_compensation(self):
        # 空行会把解析切成两个块（_analysis_parts）。预览里块与块之间是
        # 折叠后的 3 磅 —— 补偿只该给紧跟标签的那个块。
        document = build("combined", analysis_text="第一块\n\n第二块")
        bodies = [
            paragraph for paragraph in body_paragraphs(document) if text_of(paragraph).strip() in {"第一块", "第二块"}
        ]
        self.assertEqual(len(bodies), 2, "尺子自检：解析应该被拆成两块")
        self.assertEqual(
            twips_of(bodies[1], "before"), 0, "第二个解析块的第一段不拿补偿：上一段已经留了 3 磅段后距，再补就多一截"
        )
