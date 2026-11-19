"""第六十二轮守护：导出 Word 的**页首起始位置**要照抄打印预览。

预览真值（Chrome 实测 + 真实预览 PDF，见 _probe97.py / _probe98.py）：

    版心      .exam-page { padding: 18mm 16mm } → 页首元素离页顶 18mm
    页首元素  .print-section { margin: 12px 0 6px; font-size: 16px; line-height: 24px }
              .exam-page-body 是 flex 容器，外边距不塌陷 → **页首那 12px 照算**
              实测标题元素上沿离版心顶 3.175mm（= 12px）
    纸上位置  预览 PDF 第 2 页「参考答案与解析」y = 22.180mm

Word 侧原来写的是 `space_before = Pt(9)`（就是那 12px），但 **Word 不吃页首的
段前距**。真 Word 五变体实测（_probe97.py）：

    现状（硬分页 + 段前距 9 磅）      19.317mm   ← 差 2.863mm
    段前分页（w:pageBreakBefore）      19.741mm   ← 照样不吃
    兼容开关 w:suppressSpBfAfterPgBrk=0（顺序放对后）19.317 / 19.741mm ← 无效

唯一靠得住的是：**页首垫一个固定行距 9 磅（180 twips）的空行**，把 12px 补回来，
实测落到 22.492mm（与预览差 0.312mm，那是 SimHei 在 Chrome 与 Word 里 ascent
度量不同带来的行内残差，跟页脚那 0.227mm 一个量级）。

这个文件锁三件事：
  1) 合卷（答案进附录）时，答案区标题前面必须有这个垫行，固定行距 180 twips；
  2) 垫行在位了，标题自己的段前距就得让位（否则补两次，实测多出 3.175 毫米）；
  3) 答案卷（标题跟在卷名后面、**不在**页首）不垫行，段前距照写 9 磅。
"""

from __future__ import annotations

import io
import re

from django.conf import settings
from django.test import SimpleTestCase

from . import library_export

# 预览真值（独立写死，不引用被测模块的常量）
PREVIEW_SECTION_MARGIN_TOP_PX = 12
PREVIEW_SECTION_MARGIN_TOP_PT = 9.0  # 12px × 0.75
PREVIEW_PAGE_TOP_LINE_TWIPS = 180  # 9 磅
PREVIEW_EXAM_PAGE_PADDING_TOP = "18mm"
PREVIEW_EXAM_PAGE_PADDING_LEFT = "16mm"

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def item(index=1):
    return {
        "id": f"{index:08d}-1111-4111-8111-111111111111",
        "type": "single_choice",
        "where": f"第 {index} 题",
        "content": {"stem": f"第{index}题的题干", "options": {}, "origin": "", "body_mode": "text"},
        "extras": {},
        "selected": {"answer": "B", "analysis": f"第{index}题的解析"},
        "ai": False,
        "hash": "",
        "fields": {
            "stem": [{"type": "text", "segments": [{"type": "text", "raw": f"第{index}题的题干"}]}],
            "answer": [{"type": "text", "segments": [{"type": "text", "raw": "B"}]}],
            "analysis": [{"type": "text", "segments": [{"type": "text", "raw": f"第{index}题的解析"}]}],
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


def build(mode="combined", **option_overrides):
    data = library_export._document([item()], "页首体检卷", options(**option_overrides), mode)
    from docx import Document

    return Document(io.BytesIO(data))


def body_paragraphs(document):
    return list(document.element.body.findall("w:p", NS))


def text_of(paragraph):
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", NS))


def heading_index(document):
    for index, paragraph in enumerate(body_paragraphs(document)):
        if text_of(paragraph).strip() == "参考答案与解析":
            return index
    raise AssertionError("文档里没有「参考答案与解析」这一行")


def page_break_index(document):
    for index, paragraph in enumerate(body_paragraphs(document)):
        if paragraph.findall(".//w:br[@w:type='page']", NS):
            return index
    return None


def spacer_index(document):
    """页首垫行：**紧跟在分页符后面**的那一段。

    按位置找（不按属性找），这样「垫行没了」和「垫行位置错了」是两种不同的
    缺陷，能分别红在各自的守护上。
    """
    break_index = page_break_index(document)
    if break_index is None:
        return None
    paragraphs = body_paragraphs(document)
    if break_index + 1 >= len(paragraphs):
        return None
    return break_index + 1


def exact_line_paragraphs(document):
    """所有「固定行距 180 twips」的段落 —— 垫行必须是其中之一。"""
    found = []
    for index, paragraph in enumerate(body_paragraphs(document)):
        spacing = paragraph.find("w:pPr/w:spacing", NS)
        if spacing is None:
            continue
        if spacing.get(f"{{{NS['w']}}}line") != str(PREVIEW_PAGE_TOP_LINE_TWIPS):
            continue
        if spacing.get(f"{{{NS['w']}}}lineRule") != "exact":
            continue
        found.append((index, paragraph))
    return found


def spacing_of(paragraph):
    return paragraph.find("w:pPr/w:spacing", NS)


def space_before_twips(paragraph):
    spacing = spacing_of(paragraph)
    if spacing is None:
        return None
    value = spacing.get(f"{{{NS['w']}}}before")
    return None if value is None else int(value)


class PreviewTruthTests(SimpleTestCase):
    """尺子自检：预览那边到底是什么，别让尺子自己过期。"""

    def test_print_section_margin_top_is_twelve_pixels(self):
        css = open(f"{settings.FRONTEND_ROOT}/library.css", encoding="utf-8").read()
        match = re.search(r"\.print-section\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "尺子自检：library.css 里找不到 .print-section")
        self.assertIn("margin: 12px 0 6px", match.group(1), f"尺子自检：.print-section 的外边距变了：{match.group(1)}")
        self.assertEqual(PREVIEW_SECTION_MARGIN_TOP_PX * 0.75, PREVIEW_SECTION_MARGIN_TOP_PT)

    def test_exam_page_padding_is_the_body_top(self):
        css = open(f"{settings.FRONTEND_ROOT}/library.css", encoding="utf-8").read()
        match = re.search(r"\.exam-page\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "尺子自检：library.css 里找不到 .exam-page")
        self.assertIn(f"padding: {PREVIEW_EXAM_PAGE_PADDING_TOP} {PREVIEW_EXAM_PAGE_PADDING_LEFT}", match.group(1))


class PageTopSpaceTests(SimpleTestCase):
    """合卷的答案区从新页页首开始，那 12px 得补回来。"""

    def test_combined_has_a_page_top_spacer(self):
        document = build("combined")
        index = spacer_index(document)
        self.assertIsNotNone(index, "合卷的答案区前面没有分页符")
        paragraphs = body_paragraphs(document)
        self.assertNotEqual(index, heading_index(document), "分页符后面直接就是答案标题：页首那 12px 谁补的？")
        self.assertEqual(text_of(paragraphs[index]).strip(), "", "垫行里不该有文字")

    def test_the_spacer_uses_a_nine_point_exact_line(self):
        document = build("combined")
        index = spacer_index(document)
        self.assertIsNotNone(index, "尺子自检：找不到垫行")
        spacing = spacing_of(body_paragraphs(document)[index])
        self.assertEqual(spacing.get(f"{{{NS['w']}}}line"), str(PREVIEW_PAGE_TOP_LINE_TWIPS))
        self.assertEqual(spacing.get(f"{{{NS['w']}}}lineRule"), "exact", "垫行必须是固定行距，行距跟着字号走就补不准了")

    def test_the_spacer_sits_right_after_the_page_break_and_before_the_heading(self):
        document = build("combined")
        index = spacer_index(document)
        self.assertIsNotNone(index)
        self.assertEqual(index, page_break_index(document) + 1, "垫行必须紧跟在分页符后面")
        self.assertLess(index, heading_index(document), "垫行必须排在答案标题前面")

    def test_the_spacer_has_no_space_around_it(self):
        document = build("combined")
        index = spacer_index(document)
        self.assertIsNotNone(index)
        spacing = spacing_of(body_paragraphs(document)[index])
        self.assertEqual(spacing.get(f"{{{NS['w']}}}before"), "0", "垫行自己不能带段前距")
        self.assertEqual(spacing.get(f"{{{NS['w']}}}after"), "0", "垫行自己不能带段后距")

    def test_the_answer_heading_does_not_add_the_same_space_again(self):
        document = build("combined")
        heading = body_paragraphs(document)[heading_index(document)]
        self.assertEqual(
            space_before_twips(heading),
            0,
            "页首那 12px 已经由垫行补过了，标题再写一遍段前距就补了两次（实测多 3.175 毫米）",
        )

    def test_answers_document_needs_no_spacer_because_the_heading_is_not_at_the_page_top(self):
        document = build("answers")
        self.assertEqual(exact_line_paragraphs(document), [], "答案卷里标题跟在卷名后面，不在页首，不该垫行")

    def test_answers_document_keeps_the_section_margin_before(self):
        document = build("answers")
        heading = body_paragraphs(document)[heading_index(document)]
        self.assertEqual(
            space_before_twips(heading),
            int(PREVIEW_SECTION_MARGIN_TOP_PT * 20),
            "答案卷里标题的段前距就是 .print-section 的 margin-top 12px = 9 磅",
        )

    def test_inline_layout_has_neither_page_break_nor_spacer(self):
        document = build("combined", answer_layout="inline")
        self.assertIsNone(page_break_index(document), "inline 布局不该有分页符")
        self.assertEqual(exact_line_paragraphs(document), [], "inline 布局不该有页首垫行")
