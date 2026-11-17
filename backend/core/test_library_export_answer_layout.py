"""第二十九轮守护：合卷（题目＋答案）的答案位置，预览和导出的 Word 必须一致。

前端 library.js 的规矩（renderPrint）：
    answer_layout=inline   → 答案紧跟在题目后面（print-answer-row print-answer-inline），
                             并且 answer_space 归零，不留做题的空白
    answer_layout=appendix → 答案集中进卷末「参考答案与解析」附录，题目留白照常

后端 library_export._document 里：
    inline_answers = mode == "combined" and options.get("answer_layout", "appendix") == "inline"

既有测试 test_library_export.py 盯的是 combined 的附录布局（默认 answer_layout），
inline 这一支没人盯：答案到底跟不跟题后、有没有多余的卷末附录、留白还在不在，
全靠现读现改，改错了也不会有人拦。

本文件把这两条契约钉死：
  1) combined + inline   → 答案跟在该题后面，没有「参考答案与解析」标题，没有分页符，
                           没答案的题不写空答案段
  2) combined + appendix → 答案集中在卷末附录，题后不留答案
  3) answers（只有答案卷）→ 只有答案，没有题干
"""

from __future__ import annotations

from django.test import TestCase

from . import library_drafts
from .test_library_export import NS, ExportFixture, document_xml


def paragraph_texts(xml):
    """正文里每个顶层段落的文字，按出现顺序。"""
    out = []
    for para in xml.find("w:body", NS).findall("w:p", NS):
        out.append("".join(para.itertext()).strip())
    return out


def answer_positions(paragraphs):
    """每个答案段前面最近的是哪道题号（附录里的「N. 答案」也算答案，不算题号）。"""
    out = []
    last_number = ""
    in_appendix = False
    for text in paragraphs:
        if not text:
            continue
        if text.startswith("参考答案与解析"):
            in_appendix = True
            continue
        head = text.split(".", 1)[0]
        if head.isdigit():
            if in_appendix:
                out.append({"after": last_number, "number": head, "text": text})
            else:
                last_number = head
            continue
        if "答案：" in text:
            out.append({"after": last_number, "number": None, "text": text})
    return out


class CombinedAnswerLayoutTests(ExportFixture, TestCase):
    """合卷的答案该在题后还是在卷末，必须和预览说的一样。"""

    def papers(self):
        first = self.publication(stem="甲卷第一题题干", answer="甲卷第一题答案")
        second = self.publication(stem="甲卷第二题题干", answer="甲卷第二题答案")
        third = self.publication(stem="甲卷第三题题干")
        return first, second, third

    def export(self, pubs, print_options):
        payload = self.payload(pubs, print_options=print_options)
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return document_xml(response.content)

    # ── inline：答案跟题后 ────────────────────────────────────────
    def test_inline_answers_follow_their_own_question(self):
        first, second, third = self.papers()
        xml = self.export(
            [first, second, third],
            {"document": "combined", "answers": True, "answer_layout": "inline"},
        )
        paragraphs = paragraph_texts(xml)
        text = "".join(paragraphs)
        self.assertNotIn("参考答案与解析", text,
                         "合卷 inline 布局不该出现卷末附录：答案要跟题后，"
                         "卷末再堆一遍等于同一份答案印两遍")
        self.assertFalse(xml.findall(".//w:br[@w:type='page']", NS),
                         "合卷 inline 布局不该插分页符：答案跟题后，插页会把题和答案拆开")
        positions = answer_positions(paragraphs)
        self.assertEqual(len(positions), 2,
                         "两题有答案就该写两段答案，实际 %d 段" % len(positions))
        self.assertEqual([row["after"] for row in positions], ["1", "2"],
                         "答案段没有紧跟在各自题号后面：%s" % [row["after"] for row in positions])
        self.assertIn("甲卷第一题答案", positions[0]["text"])
        self.assertIn("甲卷第二题答案", positions[1]["text"])
        self.assertNotIn("原卷未提供答案", text,
                         "没答案的题不该写一段空答案：学生看到的是「这题没答案」，"
                         "而不是一段空白")

    # ── appendix：答案在卷末 ──────────────────────────────────────
    def test_appendix_answers_are_collected_at_the_end(self):
        first, second, third = self.papers()
        xml = self.export(
            [first, second, third],
            {"document": "combined", "answers": True, "answer_layout": "appendix"},
        )
        paragraphs = paragraph_texts(xml)
        text = "".join(paragraphs)
        self.assertIn("参考答案与解析", text,
                      "合卷 appendix 布局要把答案收进卷末附录：现在没有附录，"
                      "老师拿到的卷子上找不到答案")
        positions = answer_positions(paragraphs)
        self.assertEqual(len(positions), 3, "三题都该在附录里有一段答案")
        for row in positions:
            self.assertEqual(row["after"], "3",
                             "附录里的答案不该紧跟某一题（那是 inline 布局）：%s" % row["after"])

    # ── 两种布局互相独立：inline 不能带出 appendix ──────────────
    def test_inline_layout_is_not_silently_treated_as_appendix(self):
        first, second, third = self.papers()
        inline = self.export(
            [first, second, third],
            {"document": "combined", "answers": True, "answer_layout": "inline"},
        )
        appendix = self.export(
            [first, second, third],
            {"document": "combined", "answers": True, "answer_layout": "appendix"},
        )
        inline_text = "".join(paragraph_texts(inline))
        appendix_text = "".join(paragraph_texts(appendix))
        self.assertNotIn("参考答案与解析", inline_text)
        self.assertIn("参考答案与解析", appendix_text)

    # ── 答案卷：只有答案，没有题干 ───────────────────────────────
    def test_answers_document_prints_answers_without_the_stems(self):
        first, second, third = self.papers()
        xml = self.export(
            [first, second, third],
            {"document": "answers", "answer_layout": "appendix"},
        )
        text = "".join(paragraph_texts(xml))
        self.assertIn("甲卷第一题答案", text)
        self.assertNotIn("甲卷第一题题干", text,
                         "答案卷里印了题干：老师要的是答案页，不是把题再印一遍")


class PrintOptionAnswerLayoutTests(TestCase):
    """answer_layout 这个键必须一直在打印设置的白名单里。"""

    def test_answer_layout_is_accepted_and_normalized(self):
        options = library_drafts._print_options({"answer_layout": "inline"})
        self.assertEqual(options["answer_layout"], "inline")

    def test_an_unknown_answer_layout_is_refused(self):
        with self.assertRaises(library_drafts.DraftError):
            library_drafts._print_options({"answer_layout": "sideways"})
