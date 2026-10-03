"""Offline formatting contract; saved mathematics must never be auto-invented."""
from django.test import SimpleTestCase

from . import library_jobs


class MathAnswerPromptTests(SimpleTestCase):
    def test_both_answer_fields_request_complete_inline_formula_syntax(self):
        for detailed in (False, True):
            with self.subTest(detailed=detailed):
                prompt = library_jobs.answer_prompt({"stem": "计算角度。"}, False, detailed=detailed)
                self.assertIn("【答案】与【解析】中的公式都使用配对的 $...$", prompt)
                self.assertIn("同一行内公式不换行", prompt)
                self.assertIn(r"$k\in\mathbb{Z}$", prompt)
                self.assertIn("不在中文括号前加反斜杠", prompt)
                self.assertIn("条件不够", prompt)

    def test_existing_answer_text_is_preserved_not_completed_or_normalized(self):
        for answer in (r"$40^\circ+k\cdot180^\circ\（k\in\mathbb Z）$",
                       r"$40^\circ+k\cdot180^\circ\ (k\in\mathbb Z$",
                       r"$\mathbb{Z$"):
            with self.subTest(answer=answer):
                analysis = r"结论为 $\alpha=40^\circ+k\cdot180^\circ$。"
                result = library_jobs.split_answer_tags(f"【答案】{answer}\n【解析】{analysis}")
                self.assertEqual(result, {"答案": answer, "解析": analysis})
