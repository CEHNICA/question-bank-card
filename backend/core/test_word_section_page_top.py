"""导出 Word：大题标题落在**页首**时的起始位置。

预览里的大题标题是 ``.print-section``（``margin: 12px 0 6px``）。它落在页首时，
那 12px 的上外边距是**算进去**的 —— ``.exam-page-body`` 是 flex 容器，
flex 项目的外边距不塌陷（Chrome 实测 _probe106.py：第 2 页第一个孩子就是
大题标题，它的上沿离版心顶正好 3.175 毫米 = 9 磅）。

Word 这边原来把分页写在标题自己身上（``w:pageBreakBefore``），可是
**Word 不吃页首的段前距** —— 硬分页、段前分页、兼容开关
``w:suppressSpBfAfterPgBrk=0`` 全试过，一个都不管用（见 library_export.py 里
PREVIEW_PAGE_TOP_SPACE_PT 上面的对照表）。于是那 12px 整段丢失：

    预览 p2「二、解答题」字顶 22.032mm
    Word p2「二、解答题」字顶 19.741mm   → 高了 2.291 毫米

现在改成：组开头先垫一个**带分页 + 固定行距**的空行，标题跟着垫行走，
标题自己的段前距归零（它在页首，写了也是白写；不归零就跟垫行叠加）。

垫行的高度不是预览那 9 磅，而是 7.8 磅（156 twips）：Word 把「固定行距」
量化到 0.6 磅一档，实测六档里 150/152 twips 落在最好的一档
（残差 +0.037 毫米），取那档的中间值，字体度量再动一点也不会掉出档。
见 library_export.PREVIEW_SECTION_PAGE_TOP_LINE_TWIPS 的注释。

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

CHOICE = "44444444-4444-4444-8444-444444444444"
FREE = "55555555-5555-4555-8555-555555555555"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 预览侧的字面值：
#   .print-section { margin: 12px 0 6px }  → 上外边距 12px = 9 磅 = 180 twips
#   .exam-page-body { display: flex }      → flex 容器，外边距不塌陷（相加）
PREVIEW_SECTION_MARGIN_TWIPS = 180
# 垫行高度：实测标定（真 Word 六档对照 _probe108/_probe109.py）
#     140/146 twips → 21.857mm（残差 −0.175mm）
#     150/152 twips → 22.069mm（残差 +0.037mm）  ← 预览 22.032mm
# Word 把固定行距量化到 0.6 磅一档，取最好那档的中间值：7.8 磅 = 156 twips
SECTION_PAGE_TOP_LINE_TWIPS = 156


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
    """两组题：选择题 + 解答题。大题标题按题型分组，第二组另起一页。"""
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
            "配方得 f(x) = (x-1)² + 2，最小值为 2。",
        ),
    ]


def _options(document="questions", breaks=()):
    return library_drafts._print_options(
        {"document": document, "question_breaks": list(breaks)},
        {"answer_layout": "appendix", "answer_space": "none", "student_info": True},
        ids=[CHOICE, FREE],
    )


def _document(options, mode=None):
    mode = mode or options["document"]
    payload = export._document(_captured(), "大题标题页首", options, mode)
    from docx import Document

    return Document(io.BytesIO(payload))


def _paragraphs(document):
    """正文里的每一段：文本 + 间距 + 有没有分页。"""
    out = []
    for element in document.element.body.findall(W_NS + "p"):
        text = "".join(node.text or "" for node in element.iter(W_NS + "t"))
        spacing = element.find(W_NS + "pPr/" + W_NS + "spacing")
        out.append(
            {
                "text": text,
                "before": None
                if spacing is None or spacing.get(W_NS + "before") is None
                else int(spacing.get(W_NS + "before")),
                "after": None
                if spacing is None or spacing.get(W_NS + "after") is None
                else int(spacing.get(W_NS + "after")),
                "line": None
                if spacing is None or spacing.get(W_NS + "line") is None
                else int(spacing.get(W_NS + "line")),
                "rule": None if spacing is None else spacing.get(W_NS + "lineRule"),
                "break": element.find(W_NS + "pPr/" + W_NS + "pageBreakBefore") is not None,
            }
        )
    return out


def _index_of(rows, needle):
    hits = [index for index, row in enumerate(rows) if needle in row["text"]]
    if len(hits) != 1:
        raise AssertionError(f"「{needle}」应该只出现一次，实际 {len(hits)} 次：{[r['text'] for r in rows]}")
    return hits[0]


class SectionPageTopTests(SimpleTestCase):
    """尺子自检：判据引用的预览事实本身要站得住。"""

    def test_the_preview_section_margin_is_twelve_pixels(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.print-section\s*\{[^}]*margin:\s*12px 0 6px")

    def test_the_preview_page_body_is_a_flex_container(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertRegex(css, r"\.exam-page-body\s*\{[^}]*display:\s*flex")

    def test_the_page_top_spacer_carries_the_page_break(self):
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        spacer = rows[_index_of(rows, "二、解答题") - 1]
        self.assertEqual(spacer["text"], "")
        self.assertTrue(spacer["break"], spacer)
        self.assertEqual(spacer["line"], SECTION_PAGE_TOP_LINE_TWIPS, spacer)
        self.assertEqual(spacer["rule"], "exact", spacer)

    def test_the_spacer_is_empty_and_has_no_extra_spacing(self):
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        spacer = rows[_index_of(rows, "二、解答题") - 1]
        self.assertEqual(spacer["text"], "")
        self.assertEqual(spacer["before"], 0, spacer)
        self.assertEqual(spacer["after"], 0, spacer)

    def test_the_section_title_does_not_add_its_own_space_before(self):
        """标题在页首：Word 不吃段前距，写了也是白写，不归零就跟垫行叠加。"""
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        title = rows[_index_of(rows, "二、解答题")]
        self.assertEqual(title["before"], 0, title)

    def test_the_first_section_title_keeps_the_preview_margin(self):
        """第一个大题标题不在页首：12px 的外边距照写。"""
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        title = rows[_index_of(rows, "一、选择题")]
        self.assertEqual(title["before"], PREVIEW_SECTION_MARGIN_TWIPS, title)
        self.assertFalse(title["break"], title)

    def test_the_break_is_not_written_on_the_question_itself(self):
        """分页只由标题那一组承担，题目身上不再写一遍（否则多一个空页）。"""
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        question = rows[_index_of(rows, "已知函数")]
        self.assertFalse(question["break"], question)

    def test_only_one_page_break_exists_in_the_document(self):
        rows = _paragraphs(_document(_options(breaks=[FREE])))
        breaks = [index for index, row in enumerate(rows) if row["break"]]
        self.assertEqual(len(breaks), 1, [(index, rows[index]["text"]) for index in breaks])

    def test_without_question_breaks_no_spacer_is_written(self):
        rows = _paragraphs(_document(_options()))
        self.assertFalse([row for row in rows if row["break"]], rows)
        title = rows[_index_of(rows, "二、解答题")]
        self.assertEqual(title["before"], PREVIEW_SECTION_MARGIN_TWIPS, title)

    def test_the_answer_paper_is_not_affected(self):
        rows = _paragraphs(_document(_options(document="answers", breaks=[FREE])))
        self.assertFalse([row for row in rows if row["break"]], rows)
        self.assertTrue([row for row in rows if "参考答案与解析" in row["text"]], rows)
