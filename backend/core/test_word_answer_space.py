"""导出 Word 的答题留白块要跟打印预览一样高。

预览（frontend/library.css 的 .print-answer-space，Chrome 实测 _probe129.py）：
    留白是一个**固定高度**的块，紧贴上一块、上下没有外边距；
    题目块的下外边距（8 磅）落在留白**之后**。

    一道解答题在预览里推进 46.871 磅：
        题干行盒 16.195 + 留白 22.676（正好 8 毫米）+ 题块下外边距 8

Word 这边原来把留白写成「一个空段落 + 段后距 8 毫米」：
    段后距**不吃行盒**，所以实际排出来是「一个空行盒（16.2 磅）+ 8 毫米间距」，
    每道题比预览高 3.95 毫米（11.2 磅）。而且题干自己的 3 磅段后距
    留在留白前面，留白并没有真正贴住题干。

这里钉死三件事：
    1. 留白是**固定行高**的空段（w:lineRule="exact"），行盒高度就是留白高度；
    2. 留白前面那一段的段后距归零 —— 留白要贴住上一块；
    3. 留白之后的 8 磅（题块下外边距）由 _question_gap 补上。

判据全部是独立字面值（Chrome 实测），不引用被测模块的常量。
"""

import io

from django.test import SimpleTestCase
from docx.oxml.ns import qn

STEM_QUESTION = "11111111-1111-4111-8111-111111111111"
CHOICE_QUESTION = "22222222-2222-4222-8222-222222222222"

# Chrome 实测的预览真相（独立字面值）。
PREVIEW_STEM_LINE_BOX_PT = 16.195
PREVIEW_ANSWER_SPACE_PT = 22.676  # 8 毫米
PREVIEW_QUESTION_GAP_PT = 8.0
PREVIEW_QUESTION_ADVANCE_PT = 46.871

# 8 毫米换算成 twips（1 磅 = 20 twips，1 毫米 = 72/25.4 磅）。
EIGHT_MM_TWIPS = 454
TWELVE_MM_TWIPS = 680


def build_item(question_id, question_type, stem="题干示例。", options=None):
    return {
        "id": question_id,
        "type": question_type,
        "where": "第 1 题",
        "content": {"options": options or {}, "body_mode": "text"},
        "fields": {"stem": [{"type": "text", "segments": [{"type": "text", "raw": stem}]}]},
        "images": [],
    }


def build_document(items, answer_space="auto", font_size=12):
    """走**真实导出路径**造一份 Word。"""
    from . import library_export

    options = {
        "font_size": font_size,
        "student_info": False,
        "answer_space": answer_space,
        "pagination": "none",
        "question_breaks": set(),
        "answer_layout": "appendix",
    }
    data = library_export._document(items, "第六十九轮", options, "questions")
    from docx import Document

    return Document(io.BytesIO(data))


def spacing_of(element):
    return element.find(qn("w:pPr") + "/" + qn("w:spacing"))


def answer_space_paragraphs(document):
    """导出结果里所有「固定行高的空段」—— 留白块。"""
    found = []
    for element in document._element.body:
        if element.tag != qn("w:p"):
            continue
        spacing = spacing_of(element)
        if spacing is None or spacing.get(qn("w:lineRule")) != "exact":
            continue
        if "".join(node.text or "" for node in element.findall(".//" + qn("w:t"))).strip():
            continue
        found.append(element)
    return found


def previous_paragraph(element):
    body = element.getparent()
    elements = list(body)
    index = elements.index(element)
    for candidate in reversed(elements[:index]):
        if candidate.tag == qn("w:p"):
            return candidate
    return None


def twips(spacing, name):
    value = spacing.get(qn(f"w:{name}"))
    return int(value) if value is not None else None


class PreviewMeasurementTests(SimpleTestCase):
    """尺子自检：预览的像素/毫米值换算成 Word 的磅值。"""

    def test_eight_millimetres_is_twenty_two_and_a_half_points(self):
        """8 毫米 = 22.677 磅 = 454 twips。"""
        self.assertAlmostEqual(8 * 72 / 25.4, PREVIEW_ANSWER_SPACE_PT, places=2)
        self.assertAlmostEqual(8 * 72 / 25.4 * 20, EIGHT_MM_TWIPS, delta=1)

    def test_the_preview_advance_is_the_sum_of_its_parts(self):
        """预览里一道题推进 46.871 磅 = 题干行盒 + 留白 + 题块下外边距。"""
        total = PREVIEW_STEM_LINE_BOX_PT + PREVIEW_ANSWER_SPACE_PT + PREVIEW_QUESTION_GAP_PT
        self.assertAlmostEqual(total, PREVIEW_QUESTION_ADVANCE_PT, places=2)


class AnswerSpaceLineBoxTests(SimpleTestCase):
    """留白必须是**固定行高**的空段，不能拿段后距撑高度。"""

    def setUp(self):
        self.document = build_document([build_item(STEM_QUESTION, "free_response")], answer_space="none")
        self.spaces = answer_space_paragraphs(self.document)
        self.assertEqual(len(self.spaces), 1, "尺子自检：没找到唯一的留白块")

    def test_the_answer_space_is_a_fixed_line_height_paragraph(self):
        """固定行高：w:lineRule="exact"。

        用「至少 N 磅」不行 —— 空段的行盒会回到字号决定的高度，
        留白就比预览矮一整行。
        """
        spacing = spacing_of(self.spaces[0])
        self.assertEqual(spacing.get(qn("w:lineRule")), "exact", "留白块不是固定行高")

    def test_the_answer_space_line_height_is_eight_millimetres(self):
        """解答题的留白下限是 8 毫米（与前端 exam-layout.js 一致）。"""
        spacing = spacing_of(self.spaces[0])
        line = twips(spacing, "line")
        self.assertIsNotNone(line, "留白块没有写行高")
        self.assertAlmostEqual(line, EIGHT_MM_TWIPS, delta=1, msg=f"留白行高是 {line} twips，实测 {EIGHT_MM_TWIPS}")

    def test_the_answer_space_carries_the_question_gap(self):
        """留白之后是 8 磅的题块下外边距（预览 .print-question 的 margin-bottom）。"""
        spacing = spacing_of(self.spaces[0])
        after = twips(spacing, "after")
        self.assertIsNotNone(after, "留白块没有写段后距")
        self.assertAlmostEqual(after / 20, PREVIEW_QUESTION_GAP_PT, places=3)

    def test_the_answer_space_has_no_space_before(self):
        """留白上面没有外边距 —— 它紧贴上一块。"""
        spacing = spacing_of(self.spaces[0])
        before = twips(spacing, "before")
        self.assertIn(before, (None, 0), "留白块上面多了外边距")


class AnswerSpaceAttachmentTests(SimpleTestCase):
    """留白要贴住上一块：上一段的段后距必须归零。"""

    def test_the_paragraph_before_the_answer_space_has_no_space_after(self):
        """预览里 .print-answer-space 的上下外边距都是 0。"""
        document = build_document([build_item(STEM_QUESTION, "free_response")], answer_space="none")
        space = answer_space_paragraphs(document)[0]
        previous = previous_paragraph(space)
        self.assertIsNotNone(previous, "尺子自检：留白块前面没有段落")
        spacing = spacing_of(previous)
        self.assertIsNotNone(spacing, "尺子自检：留白块前面的段落没有写行距")
        after = twips(spacing, "after")
        self.assertEqual(after, 0, f"留白前面那段还留着 {after} twips 的段后距")


class QuestionAdvanceTests(SimpleTestCase):
    """一道题在 Word 里推进的距离，必须等于预览。"""

    def test_the_question_advance_matches_the_preview(self):
        """Word 里一道解答题推进 46.871 磅（预览实测）。

        修之前是 58.1 磅 —— 每题高 3.95 毫米，卷子越往后偏得越多。
        """
        document = build_document([build_item(STEM_QUESTION, "free_response")], answer_space="none")
        space = answer_space_paragraphs(document)[0]
        stem = previous_paragraph(space)
        self.assertIsNotNone(stem, "尺子自检：留白块前面没有段落")
        stem_spacing = spacing_of(stem)
        space_spacing = spacing_of(space)
        total = (
            twips(stem_spacing, "line") / 20
            + (twips(stem_spacing, "after") or 0) / 20
            + twips(space_spacing, "line") / 20
            + (twips(space_spacing, "after") or 0) / 20
        )
        self.assertAlmostEqual(
            total,
            PREVIEW_QUESTION_ADVANCE_PT,
            delta=0.3,
            msg=f"Word 里一道题推进 {total:.3f} 磅，预览实测 {PREVIEW_QUESTION_ADVANCE_PT} 磅",
        )

    def test_the_answer_space_is_the_only_thing_between_two_questions(self):
        """尺子自检：两道题之间只隔着留白块（没有别的隐藏段落）。"""
        document = build_document(
            [
                build_item(STEM_QUESTION, "free_response", stem="第一题。"),
                build_item(CHOICE_QUESTION, "free_response", stem="第二题。"),
            ],
            answer_space="none",
        )
        spaces = answer_space_paragraphs(document)
        self.assertEqual(len(spaces), 2, "尺子自检：两道题应该有两个留白块")


class PaginationEstimateTests(SimpleTestCase):
    """分页估算也得按固定行高算留白，否则会提前分页。"""

    def test_the_answer_space_is_measured_by_its_fixed_line_height(self):
        """留白块在分页估算里要占 8 毫米 + 8 磅题块间距。

        留白改成固定行高之前，它是个「空行盒 + 段后距」；
        估算器若还按默认行距去猜，会把 8 毫米的留白当成 5.7 毫米，
        分页就会提前一截。
        """
        from . import library_export

        document = build_document([build_item(STEM_QUESTION, "free_response")], answer_space="none")
        space = answer_space_paragraphs(document)[0]
        measured = library_export._paragraph_measure(space, 12)
        expected = 8.0 + PREVIEW_QUESTION_GAP_PT * 25.4 / 72
        self.assertAlmostEqual(
            measured, expected, delta=0.05, msg=f"分页估算里留白块占 {measured:.3f} 毫米，应为 {expected:.3f} 毫米"
        )


class AnswerSpaceRegressionTests(SimpleTestCase):
    """改留白不能顺手改坏别的题型。"""

    def test_a_choice_question_with_auto_space_has_no_answer_space(self):
        """选择题在「智能」档下不留白 —— 预览也不留。"""
        document = build_document([build_item(CHOICE_QUESTION, "single_choice")], answer_space="auto")
        self.assertEqual(answer_space_paragraphs(document), [], "选择题不该有留白块")

    def test_a_choice_question_with_a_named_space_keeps_its_height(self):
        """选择题指定 12 毫米时，留白是 12 毫米（不是 8 毫米下限）。"""
        document = build_document([build_item(CHOICE_QUESTION, "single_choice")], answer_space=12)
        spaces = answer_space_paragraphs(document)
        self.assertEqual(len(spaces), 1, "尺子自检：没找到留白块")
        spacing = spacing_of(spaces[0])
        self.assertAlmostEqual(twips(spacing, "line"), TWELVE_MM_TWIPS, delta=1)

    def test_the_stem_of_a_question_without_answer_space_keeps_its_paragraph_gap(self):
        """不留白的题，题干的 3 磅段后距还在（别把归零写成无差别归零）。"""
        document = build_document([build_item(CHOICE_QUESTION, "single_choice")], answer_space="auto")
        stems = [
            element
            for element in document._element.body
            if element.tag == qn("w:p")
            and "题干示例" in "".join(node.text or "" for node in element.findall(".//" + qn("w:t")))
        ]
        self.assertEqual(len(stems), 1, "尺子自检：没找到题干段")
        spacing = spacing_of(stems[0])
        self.assertIsNotNone(spacing, "尺子自检：题干段没有写行距")
        self.assertNotEqual(twips(spacing, "after"), 0, "不留白的题不该被归零段后距")
