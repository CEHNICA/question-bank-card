"""导出 Word：相邻块之间的间距 —— **Word 取大 vs 预览相加**。

预览的 ``.exam-page-body`` 是 flex 容器：相邻两块的外边距**相加**
（块的下外边距 + 下一块的上外边距）。Word 是
``max(前段段后距, 后段段前距)``（真 Word 实测 _probe107.py，
本轮 _probe113.py 用整卷对照又验了一遍）。

只写「下一块的上外边距」的话，前面那块的下外边距会被 Word 吃掉：

    考生信息行 → 第一组大题标题   预览 16.5 + 9 = 25.5 磅   Word 只剩 16.5 磅
    上一组最后一道题 → 下一个标题  预览 8 + 9 = 17 磅       Word 只剩 8 磅

一处一处丢，整卷就往下逐段漂移（实测三个大题标题的字顶分别偏
−1.798 / −3.463 / −6.091 毫米，见 _probe113.py）。

修法：把**和值整个写进下一块的段前距**。Word 取大时取到的正是预览那个和值。

判据一律写独立字面值，不引用被测模块的常量 —— 否则反向变体把常量一改，
这条守护会跟着一起放绿。
"""

import io
import os
import re
from pathlib import Path

from django.test import SimpleTestCase

from . import library_drafts, library_export as export

REPO = Path(__file__).resolve().parents[2]
# 预览样式表的位置：环境变量优先（反向变体在副本里跑，前端目录不在副本里），
# 找不到就退回仓库自己的 frontend 目录。
FRONTEND = Path(os.environ.get("QB_FRONTEND_ROOT") or (REPO / "frontend"))
CSS = FRONTEND / "library.css"

CHOICE = "66666666-6666-4666-8666-666666666666"
FREE = "77777777-7777-4777-8777-777777777777"
FILL = "88888888-8888-4888-8888-888888888888"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 预览侧的字面值（单位：twips，1 磅 = 20 twips）：
#   .print-section  { margin: 12px 0 6px }  → 上外边距 12px = 9 磅 = 180 twips
#   .print-info     { margin: 12px 0 22px } → 下外边距 22px = 16.5 磅 = 330 twips
#   .print-question { margin: 0 0 8pt }     → 下外边距 8 磅 = 160 twips
#   .exam-page-body { display: flex }       → flex 容器，外边距不塌陷（相加）
SECTION_MARGIN_TOP_TWIPS = 180
INFO_MARGIN_BOTTOM_TWIPS = 330
QUESTION_MARGIN_BOTTOM_TWIPS = 160

# Word 侧应当写进去的段前距（= 预览那两个外边距的和）：
#   第一组（前面是考生信息行）：16.5 + 9 = 25.5 磅 = 510 twips
#   之后每一组（前面是上一组最后一道题）：8 + 9 = 17 磅 = 340 twips
FIRST_TITLE_SPACE_BEFORE_TWIPS = 510
LATER_TITLE_SPACE_BEFORE_TWIPS = 340


def _text(raw):
    return [{"type": "text", "segments": [{"type": "text", "raw": raw}]}]


def _item(uid, where, kind, stem, answer="", analysis="", options=None):
    fields = {"stem": _text(stem), "answer": _text(answer), "analysis": _text(analysis)}
    for letter, value in (options or {}).items():
        fields[f"options.{letter}"] = _text(value)
    return {
        "id": uid,
        "where": where,
        "type": kind,
        "content": {"stem": stem, "options": options or {}, "origin": "", "body_mode": "text"},
        "extras": {},
        "selected": {"answer": answer, "analysis": analysis},
        "ai": False,
        "hash": "",
        "fields": fields,
        "images": [],
        "solution_images": [],
        "solution_id": None,
        "selection_revision": None,
        "solution_explicit": False,
    }


def _captured():
    """三组题：选择题 + 解答题 + 填空题（按题型自动分组）。"""
    return [
        _item(
            CHOICE,
            "第 1 题",
            "single_choice",
            "下列各式中，正确的是（　　）",
            "B",
            "由定义可知。",
            {"A": "x > 1", "B": "x < 1", "C": "x = 1", "D": "x ≥ 1"},
        ),
        _item(
            FREE,
            "第 2 题",
            "free_response",
            "已知函数 f(x) = x² - 2x + 3，求它的最小值。",
            "",
            "配方得 f(x) = (x-1)² + 2。",
        ),
        _item(FILL, "第 3 题", "fill_blank", "地球的半径约为 6400 千米。", "6400", "地球的平均半径约为 6371 千米。"),
    ]


def _options(document="questions", breaks=(), student_info=True):
    return library_drafts._print_options(
        {"document": document, "question_breaks": list(breaks)},
        {"answer_layout": "appendix", "answer_space": "none", "student_info": student_info},
        ids=[CHOICE, FREE, FILL],
    )


def _document(options, mode=None):
    mode = mode or options["document"]
    payload = export._document(_captured(), "块间距对照卷", options, mode)
    from docx import Document

    return Document(io.BytesIO(payload))


def _twips(direct, inherited):
    """段间距：先看段落自己写的，没写就看样式上的（Word 就是这么继承的）。"""
    for value in (direct, inherited):
        if value is not None:
            return int(value.twips)
    return None


def _paragraphs(document):
    """正文里的每一段（表格里的段落不算）：文本 + 段间距 + 有没有分页。"""
    out = []
    for paragraph in document.paragraphs:
        fmt = paragraph.paragraph_format
        style = paragraph.style
        style_fmt = style.paragraph_format if style is not None else None
        out.append(
            {
                "text": paragraph.text,
                "style": style.name if style is not None else "",
                "before": _twips(fmt.space_before, style_fmt.space_before if style_fmt else None),
                "after": _twips(fmt.space_after, style_fmt.space_after if style_fmt else None),
                "break": bool(fmt.page_break_before),
            }
        )
    return out


def _index_of(rows, needle):
    hits = [index for index, row in enumerate(rows) if needle in row["text"]]
    if len(hits) != 1:
        raise AssertionError(f"「{needle}」应该只出现一次，实际 {len(hits)} 次：{[r['text'] for r in rows]}")
    return hits[0]


def _effective_gap(rows, index):
    """Word 这一处的真实间距：max(前段段后距, 后段段前距)。"""
    before = rows[index - 1]["after"] or 0
    after = rows[index]["before"] or 0
    return max(before, after)


class PreviewSpacingFactsTests(SimpleTestCase):
    """尺子自检：判据引用的预览事实本身要站得住。"""

    def test_the_preview_section_margin_top_is_twelve_pixels(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.print-section\s*\{[^}]*margin:\s*12px 0 6px")

    def test_the_preview_info_margin_bottom_is_twenty_two_pixels(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.print-info\s*\{[^}]*margin:\s*12px 0 22px")

    def test_the_preview_question_margin_bottom_is_eight_points(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.print-question\s*\{[^}]*margin:\s*0 0 8pt")

    def test_the_preview_page_body_is_a_flex_container(self):
        """flex 容器的外边距不塌陷：相邻块的外边距是相加，不是取大。"""
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.exam-page-body\s*\{[^}]*display:\s*flex")


class SectionTitleSpacingTests(SimpleTestCase):
    """大题标题的段前距要吃掉前一块的下外边距。"""

    def test_the_first_title_takes_the_student_info_margin(self):
        rows = _paragraphs(_document(_options()))
        title = rows[_index_of(rows, "一、选择题")]
        self.assertEqual(title["before"], FIRST_TITLE_SPACE_BEFORE_TWIPS, title)

    def test_the_later_title_takes_the_question_margin(self):
        rows = _paragraphs(_document(_options()))
        title = rows[_index_of(rows, "二、填空题")]
        self.assertEqual(title["before"], LATER_TITLE_SPACE_BEFORE_TWIPS, title)

    def test_without_student_info_the_first_title_keeps_only_its_own_margin(self):
        rows = _paragraphs(_document(_options(student_info=False)))
        title = rows[_index_of(rows, "一、选择题")]
        self.assertEqual(title["before"], SECTION_MARGIN_TOP_TWIPS, title)

    def test_the_effective_gap_after_the_student_info_row_matches_the_preview(self):
        """Word 取大之后，这一处必须正好等于预览的相加值 16.5 + 9 = 25.5 磅。"""
        rows = _paragraphs(_document(_options()))
        index = _index_of(rows, "一、选择题")
        self.assertEqual(_effective_gap(rows, index), 510, rows[index - 1 : index + 1])

    def test_the_effective_gap_between_groups_matches_the_preview(self):
        """题块下外边距 8 磅 + 标题上外边距 9 磅 = 17 磅。"""
        rows = _paragraphs(_document(_options()))
        index = _index_of(rows, "二、填空题")
        self.assertEqual(_effective_gap(rows, index), 340, rows[index - 1 : index + 1])

    def test_the_gap_between_two_groups_is_untouched(self):
        """组与组之间那 8 磅还是 8 磅（这一处本来就是对的，别顺手改坏）。"""
        rows = _paragraphs(_document(_options()))
        index = _index_of(rows, "二、填空题")
        self.assertEqual(rows[index - 1]["after"], QUESTION_MARGIN_BOTTOM_TWIPS, rows[index - 1])

    def test_a_title_on_a_new_page_has_no_space_before(self):
        """标题落在页首：那里没有前一块，12px 由垫行补回来。"""
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        title = rows[_index_of(rows, "三、解答题")]
        self.assertEqual(title["before"], 0, title)

    def test_the_answer_paper_title_is_untouched(self):
        """答案卷里没有大题标题，答案区标题的段前距照旧（9 磅）。"""
        rows = _paragraphs(_document(_options(document="answers")))
        heading = rows[_index_of(rows, "参考答案与解析")]
        self.assertEqual(heading["before"], SECTION_MARGIN_TOP_TWIPS, heading)
