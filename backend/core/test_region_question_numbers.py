r"""卡的范围罩住了后面的题：书上印着第 N 题，库里却没有它的卡。

真实案例（两本教材，逐张翻原页确认过）：
  数学八年级下册第 41 页「习题 7.1」，书上印着
    2. 计算：（1）$(\sqrt{16})^2$；（2）$(-3\sqrt{1/6})^2$。
    ※3. 计算：（1）$(\sqrt{3a^3})^2$（$a\geq 0$）；（2）$(2\sqrt{5b+1})^2$。
  切题把第 2 题的范围一路罩到本页末尾（还跨到下一页的「议一议」），识读只把
  ※3 的正文写进了卡。全库搜不到第 2 题那句「2. 计算」—— 这道题就这么没了，
  而这张卡看上去只有一道题、只有一条「AI 看到的题号是 3」的提示。

  九上第 63～64 页更明显：第 15 题的范围罩住了 ※16、※17、※18，三道题的原
  文全库里都找不到。

判据只看这张卡自己的范围里印着什么，不依赖切题结果。
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, textnorm
from .models import Question

READING = {
    "stem": "", "options": {}, "content_kind": "question", "figures": {},
    "missing_figure": False, "number_seen": 1, "figure_descriptions": [],
    "unclear": False,
}

# 数学八年级下册第 41 页「习题 7.1」第 2 题的范围文字：自己的那一行 + 罩到的
# ※3，再往下是本页末尾和下一页开头的「2 二次根式的性质」「议一议」。
WIDOW_SPAN = """习题 7.1
知识技能
2.计算：（1） $( { \\sqrt { 1 6 } } ) ^ { 2 }$ （2） $( - 3 \\sqrt { \\frac { 1 } { 6 } } ) ^ { 2 }$ 。
数学理解
※3. 计算：
（1） $( \\sqrt { 3 a ^ { 3 } } ) ^ { 2 } \\left( a \\geqslant 0 \\right)$ ；
2 二次根式的性质
议一议
（1）计算： $\\sqrt { 2 ^ { 2 } } , \\sqrt { 3 ^ { 2 } } $ 。你发现了什么？"""


def snapshot(**changes):
    base = {
        "id": 1, "number": 2, "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [80, 160, 900, 940]}],
        "candidates": [], "question_type": "free_response", "stem": "", "options": {},
        "edited": False, "source_kind": Question.SourceKind.UNKNOWN,
        "source_anchor_seq": 1, "segmentation_flags": [], "body_mode": "text",
        "start_source": "mineru", "is_book": True, "witness": "",
    }
    base.update(changes)
    return base


class PrintedQuestionNumberTests(SimpleTestCase):
    def test_the_question_swallowed_by_the_range_is_found(self):
        self.assertEqual(textnorm.printed_question_numbers(WIDOW_SPAN, 2), [3])

    def test_several_swallowed_questions_keep_their_order(self):
        witness = "21. 如图，隧道的截面……\n※22.一座抛物线形拱桥……\n※28.求海星图中的小圆圈总数。"
        self.assertEqual(textnorm.printed_question_numbers(witness, 21), [22, 28])

    def test_the_cards_own_number_is_not_reported(self):
        self.assertEqual(textnorm.printed_question_numbers("4. 不解方程，判断根的情况。", 4), [])

    def test_a_question_above_this_card_is_reported_too(self):
        witness = "1. a 是怎样的实数时，下列各式有意义？\n2. 计算：（1）$x$；（2）$y$。\n※3. 化简。"
        self.assertEqual(textnorm.printed_question_numbers(witness, 2), [1, 3])

    def test_sub_questions_are_not_other_questions(self):
        witness = "2. 计算：\n（1）$2x^2-4x-1=0$；\n（2）$5x+2=3x^2$。"
        self.assertEqual(textnorm.printed_question_numbers(witness, 2), [])

    def test_a_decimal_is_not_a_question_number(self):
        self.assertEqual(textnorm.printed_question_numbers("已知 3.14，再求 y。", 1), [])

    def test_a_clipped_binding_edge_is_still_this_card(self):
        # 切线削掉了前一位数字：「9.」可能是第 19 题，不是第 9 题
        self.assertEqual(textnorm.printed_question_numbers("9. 求这个三角形的面积。", 19), [])

    def test_no_witness_is_not_a_hit(self):
        self.assertEqual(textnorm.printed_question_numbers("", 1), [])


class SwallowedQuestionFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem, number=2, number_seen=2, **changes):
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question",
                             return_value={**READING, "stem": stem, "number_seen": number_seen}), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url",
                             return_value="data:image/jpeg;base64,x"):
            return pipeline.read_card(snapshot(number=number, **changes), self.store)

    def test_a_range_that_swallowed_another_question_is_not_green(self):
        result = self.run_card("计算：\n（1）$(\\sqrt{3a^3})^2$（$a\\geq 0$）；",
                               witness=WIDOW_SPAN)

        self.assertTrue(any("范围里还印着第 3 题" in flag for flag in result["flags"]),
                        result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_an_ordinary_range_stays_green(self):
        witness = "2. 计算：\n（1）$(\\sqrt{16})^2$；\n（2）$(-3\\sqrt{1/6})^2$。\n数学理解"
        result = self.run_card("计算：\n（1）$(\\sqrt{16})^2$；", witness=witness)

        self.assertFalse(any("范围里还印着" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN, result["flags"])

    def test_an_exam_paper_is_not_judged_by_printed_numbers(self):
        # 一张卷分几道大题、各自从 1 重新编号：卷内第 23 题面上印的是「5.」
        witness = "5.（10分）如图，BN，CM分别是△ABC的两条高。\n(1) 求证：DE⊥MN；"
        result = self.run_card("点 $D$、$E$ 分别是 $BC$、$MN$ 的中点，求证：$DE\\perp MN$。",
                               number=23, number_seen=23, is_book=False, witness=witness)

        self.assertFalse(any("范围里还印着" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN, result["flags"])
