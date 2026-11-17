"""第三十二轮守护：答案卷（answers / combined）的 Word 文本必须和预览逐字一致。

尺子实测（_audit32f.py）在预览和导出的 Word 之间量到六处对不上：

    D1 答案卷标题   预览「练习」            Word「练习 · 答案解析」
    D2 空答案占位   预览「（原卷未提供答案解析）」 Word「（原卷未提供答案）」
    D3 答案标签     预览有「答案：」        Word 没有，正文只有「N. 」前缀
    D4 inline 前缀  预览无                 Word「【答案解析】」
    D5 AI 标记      预览「（AI 参考，未核对）」 Word「（AI 参考 · 未核对）」
    D6 选择题答案   预览正体（题型库的老规矩） Word 正体

D1~D5 的方向是「Word 迁就预览」：预览和 PDF 共用同一套文案，本来就对齐，
Word 是唯一偏离的一方。卷末的「参考答案与解析」标题和分卷的文件名仍然区分
题目卷与答案卷，去掉标题后缀不会让两份卷子分不清。

D6 反过来 —— 项目里早有「选择题答案保持正体」的规矩（exam-export.js 的
add("answer", answer, /^\\s*[A-E]{1,5}\\s*$/)），Word 一直照做，是预览漏了。
预览那一侧在 frontend/library-solutions.js 改，Word 不动；见
frontend/test_library_answer_text_parity.js。

这份文件把 D1~D5 的 Word 侧钉死：预览改了文案，这里必须跟着变。
"""

from __future__ import annotations

from django.test import TestCase, override_settings

from . import features, library_export as export
from .test_library_export import NS, ExportFixture, document_xml


def paragraphs(data):
    """正文里每个顶层段落的文字，按出现顺序。"""
    xml = document_xml(data)
    return ["".join(para.itertext()).strip() for para in xml.find("w:body", NS).findall("w:p", NS)]


@override_settings(ROOT_URLCONF="core.test_library_export")
class AnswerTextParityTests(ExportFixture, TestCase):
    """答案卷的每一个字都要跟预览说得一样。"""

    def export(self, pubs, print_options=None, output_format="docx", title="单元练习"):
        payload = self.payload(pubs, print_options=print_options, output_format=output_format, title=title)
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response

    def test_answer_document_title_drops_the_extra_suffix(self):
        """D1：答案卷的标题就是卷名，卷末「参考答案与解析」已经承担了区分。"""
        pub = self.publication(stem="离线合成题干", answer="原卷答案")
        result = self.export([pub], {"document": "answers"})
        texts = paragraphs(result.content)
        self.assertEqual(texts[0], "单元练习", "答案卷的标题应与预览一样，只有卷名：%s" % texts[:2])
        self.assertNotIn(" · 答案解析", "".join(texts))

    def test_empty_answer_uses_the_preview_placeholder(self):
        """D2：没答案的题，写「（原卷未提供答案解析）」，不是「（原卷未提供答案）」。

        只有答案卷时后端会直接拒收没答案的题，所以这条只能在合卷里量：
        有一道有答案、一道没有，卷末附录里那道没答案的题要写占位句。
        """
        answered = self.publication(stem="离线合成题干甲", answer="原卷答案")
        blank = self.publication(stem="离线合成题干乙")
        result = self.export([answered, blank], {"document": "combined"})
        texts = paragraphs(result.content)
        # 题号和占位句之间现在是制表符（预览里那一栏），所以只锁语义不锁分隔符。
        self.assertTrue(any(text.startswith("2.") and "（原卷未提供答案解析）" in text for text in texts), texts)
        self.assertNotIn("（原卷未提供答案）", "".join(texts))

    def test_answer_and_analysis_carry_the_same_labels_as_the_preview(self):
        """D3：答案前面有「答案：」，解析前面有「解析：」，跟预览里的 strong 标签一样。"""
        pub = self.publication(stem="离线合成题干", answer="原卷答案", analysis="原卷解析")
        result = self.export([pub], {"document": "answers"})
        texts = paragraphs(result.content)
        # 题号和「答案：」之间是制表符（答案块那一栏），断言只锁顺序和标签。
        self.assertTrue(
            any(text.startswith("1.") and "答案：原卷答案" in text for text in texts),
            "答案正文前面必须有「答案：」标签：%s" % texts,
        )
        self.assertIn("解析：原卷解析", texts, "解析正文前面必须有「解析：」标签：%s" % texts)

    def test_analysis_without_an_answer_still_gets_one_label_only(self):
        """只有解析没有答案时，题号和「解析：」只能出现一次。"""
        pub = self.publication(stem="离线合成题干", analysis="原卷仅有解析")
        result = self.export([pub], {"document": "answers"})
        texts = paragraphs(result.content)
        self.assertEqual(sum(text.startswith("1.") for text in texts), 1, "题号只该出现一次：%s" % texts)
        self.assertTrue(any(text.startswith("1.") and "解析：原卷仅有解析" in text for text in texts), texts)

    def test_inline_answer_has_no_prefix_and_no_question_number(self):
        """D4：inline 的答案紧跟题目，不带「【答案解析】」，也不重复题号。"""
        pub = self.publication(stem="离线合成题干", answer="原卷答案")
        result = self.export([pub], {"document": "combined", "answers": True, "answer_layout": "inline"})
        texts = paragraphs(result.content)
        self.assertNotIn("【答案解析】", "".join(texts))
        answer_rows = [text for text in texts if "答案：" in text]
        self.assertEqual(len(answer_rows), 1, texts)
        self.assertTrue(answer_rows[0].startswith("答案："), answer_rows[0])
        self.assertFalse(answer_rows[0][:1].isdigit(), "inline 的答案段不该重复题号：%s" % answer_rows[0])

    def test_ai_note_matches_the_preview_wording_and_appears_once(self):
        """D5：AI 标记是「（AI 参考，未核对）」，且整段答案里只出现一次。"""
        features.save({"ai_answer": True})
        pub = self.publication(stem="离线合成题干", extras={"ai_answer": {"answer": "AI 参考答案"}})
        result = self.export([pub], {"document": "answers", "ai_answers": True})
        texts = paragraphs(result.content)
        joined = "".join(texts)
        self.assertIn("（AI 参考，未核对）", joined)
        self.assertNotIn("AI 参考 · 未核对", joined)
        self.assertEqual(joined.count("AI 参考，未核对"), 1, "AI 标记只该出现一次：%s" % texts)

    def test_the_wording_constants_are_the_ones_the_preview_uses(self):
        """Word 这边的两句话必须就是预览那两句，不是各写各的。"""
        self.assertEqual(export.EMPTY_SOLUTION_TEXT, "（原卷未提供答案解析）")
        self.assertEqual(export.AI_REFERENCE_NOTE, "（AI 参考，未核对）")
