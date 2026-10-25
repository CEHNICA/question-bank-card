"""重读把一道好题读坏时，系统该留住旧的，而不是照单全收。

真实重跑实测（2026-10-11，24 张抽检）里出现过：
  #1150  库上 538 字、三个小问 -> 重读 61 字、小问全丢
  #802   库上 546 字、两个小问 -> 重读 91 字，还换成了另一道题
  #1354  库上 329 字、两个小问 -> 重读 86 字，同样换成另一道题
守卫一句提示都没挂，成品卷上就印成了残题。

同时也必须放过真正的修复：
  #1152  库上 174 字、缺小问(2) -> 重读 257 字、把 (2) 补上了
"""
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from core import pipeline
from core.models import Paper, Question

LONG = ("在同一直角坐标系内画出函数 $y=\\dfrac{2}{x}$ 与函数 $y=x-1$ 的图象，"
        "并利用图象求它们的交点坐标。\n\n"
        "（1）先画出反比例函数 $y=\\dfrac{2}{x}$ 的图象，再画出直线 $y=x-1$；\n\n"
        "（2）观察图象，说出这两个图象的交点坐标；\n\n"
        "（3）用你求出的交点坐标代入两个函数，检验是否正确。")


class ReadingRegressionTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(DATA_ROOT=Path(folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.paper = Paper.objects.create(filename="甲卷.pdf", kind="pdf", sha256="c" * 64)

    def card(self, stem, **extra):
        question = Question.objects.create(
            paper=self.paper, number=extra.pop("number", 1), question_type="free_response",
            stem=stem, state=Question.State.YELLOW, **extra)
        return question

    def fields(self, stem, **extra):
        base = {"stem": stem, "options": {}, "flags": [],
                "state": Question.State.GREEN, "question_type": "free_response"}
        base.update(extra)
        return base

    def reread(self, question, fields):
        pipeline._apply_reading_fields(question, fields)
        return Question.objects.get(pk=question.pk)

    def test_a_reread_that_drops_the_subquestions_keeps_the_previous_text(self):
        question = self.card(LONG)
        before = question.stem
        after = self.reread(question, self.fields("在同一直角坐标系内画出函数 $y=2/x$ 的图象。"))
        self.assertEqual(after.stem, before, "上一次识读的题干必须留下")
        self.assertTrue(any("读短了" in f for f in after.flags), after.flags)
        self.assertEqual(after.state, Question.State.YELLOW)

    def test_a_reread_that_reads_a_completely_different_question_is_also_refused(self):
        question = self.card(LONG)
        other = "已知：如图，在四边形 $ABCD$ 中，$E$，$F$ 分别是 $AC$，$BD$ 的中点。\n求证：$EF\\perp BD$。"
        after = self.reread(question, self.fields(other))
        self.assertEqual(after.stem, LONG)
        self.assertTrue(any("读短了" in f for f in after.flags), after.flags)

    def test_a_reread_that_restores_a_missing_subquestion_is_accepted(self):
        short = "点 $A(2,4)$ 在二次函数的图象上吗？\n\n（1）写出点 $A$ 关于 $x$ 轴的对称点。"
        long = short + "\n\n（2）写出点 $A$ 关于原点的对称点。\n\n（3）三个点都在图象上吗？"
        question = self.card(short)
        after = self.reread(question, self.fields(long))
        self.assertEqual(after.stem, long, "补上小问是真修复，必须收下")
        self.assertFalse(any("读短了" in f for f in after.flags), after.flags)

    def test_a_first_reading_is_never_refused(self):
        question = self.card("")
        after = self.reread(question, self.fields("新识读出来的题干。"))
        self.assertEqual(after.stem, "新识读出来的题干。")
        self.assertFalse(any("读短了" in f for f in after.flags), after.flags)

    def test_a_shorter_text_that_keeps_every_subquestion_is_left_alone(self):
        # 小问数不变（1)(2) 都在），只是第 (1) 问的文字短了很多——这不是读坏。
        long_first = "（1）" + "先用计算器反复试算，观察函数值的变化规律，" * 30 + "再记录下来。"
        question = self.card(long_first + "\n\n（2）另一个根可以类似的求出。")
        shorter = "（1）先求根。\n\n（2）另一个根可以类似的求出。"
        self.assertGreater(len(question.stem), len(shorter) * 2, "本用例要求新结果短一半以上")
        after = self.reread(question, self.fields(shorter))
        self.assertEqual(after.stem, shorter, "小问一个没少，只是变短，不该拦")
        self.assertFalse(any("读短了" in f for f in after.flags), after.flags)

    def test_a_moderately_shorter_text_is_not_treated_as_a_regression(self):
        # 门槛是「短一半以上」。短三成不算读坏——精简单句很常见。
        long_first = "（1）" + "先用计算器反复试算，观察函数值的变化规律，" * 12 + "再记录下来。"
        question = self.card(long_first + "\n\n（2）另一个根可以类似的求出。")
        shorter = "（1）" + "先用计算器试算，观察变化规律，" * 9 + "再记录。\n\n（2）另一个根可以类似的求出。"
        self.assertLess(len(shorter), len(question.stem), "本用例要求新结果更短")
        self.assertGreaterEqual(len(shorter) * 2, len(question.stem), "但没短到一半")
        after = self.reread(question, self.fields(shorter))
        self.assertEqual(after.stem, shorter)
        self.assertFalse(any("读短了" in f for f in after.flags), after.flags)

    def test_a_manual_edit_is_still_never_overwritten(self):
        question = self.card(LONG, edited=True)
        after = self.reread(question, self.fields("短得多的另一段文字。"))
        self.assertEqual(after.stem, LONG)
