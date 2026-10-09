"""两道题并成了一张卡：题干里印着另一道题的题号，而卡是绿的。

真实案例（两本教材各查到几张，逐张翻原页确认过）：
  数学九年级上册第 1 题，原页上印着
    1. 你能举出现实生活中有关反比例函数的几个实例吗？
    2. 说说反比例函数 y=2/x 与 y=-2/x 的图象的联系和区别。
  两行挨得近，MinerU 并成了一个版面块，第 2 题的题号不在块开头，切题没认出来，
  它的正文被并进第 1 题。卡上只有一个题号、零旗标，谁都看不出来。

判据只看纸上印着的形状，不依赖切题结果 —— 这也是为什么它能在「锚点对账」
这种循环检查（锚点漏一道、卡也跟着漏）之外提供证据。
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


def snapshot(**changes):
    base = {
        "id": 1, "number": 1, "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [80, 160, 900, 240]}],
        "candidates": [], "question_type": "free_response", "stem": "", "options": {},
        "edited": False, "source_kind": Question.SourceKind.UNKNOWN,
        "source_anchor_seq": 1, "segmentation_flags": [], "body_mode": "text",
        "start_source": "mineru",
    }
    base.update(changes)
    return base


class MergedQuestionNumberTests(SimpleTestCase):
    def test_the_second_printed_question_is_found(self):
        stem = "你能举出现实生活中有关反比例函数的几个实例吗？\n\n2. 说说反比例函数的联系和区别。"
        self.assertEqual(textnorm.merged_question_number(stem, 1), 2)

    def test_a_starred_harder_question_is_found(self):
        # 教材用 * 标难题，*5. 也是一道独立的题
        stem = "如何判断方程根的情况？请举例说明。\n\n*5. 一元二次方程的根与系数有怎样的关系？"
        self.assertEqual(textnorm.merged_question_number(stem, 4), 5)

    def test_the_optional_marker_counts_too(self):
        stem = "计算：\n\n※3. 化简：$\\sqrt{8}$。"
        self.assertEqual(textnorm.merged_question_number(stem, 2), 3)

    def test_sub_questions_are_not_other_questions(self):
        stem = "计算下列各题：\n（1）$2x^2-4x-1=0$；\n（2）$5x+2=3x^2$。\n（3）$x^2=25$。"
        self.assertIsNone(textnorm.merged_question_number(stem, 1))

    def test_a_decimal_is_not_a_question_number(self):
        self.assertIsNone(textnorm.merged_question_number("已知 x 的值为 3.14，再求 y。", 1))

    def test_the_cards_own_number_again_is_not_another_question(self):
        # 切线把题号重复印了一遍，不是两道题
        self.assertIsNone(textnorm.merged_question_number("1. 甲乙两地相距 300 千米。\n求平均速度。", 1))

    def test_a_clipped_binding_edge_is_still_this_card(self):
        # 切线削掉了前一位数字：「9.」可能是第 19 题，不是第 9 题
        self.assertIsNone(textnorm.merged_question_number("9. 求这个三角形的面积。", 19))

    def test_no_stem_is_not_a_hit(self):
        self.assertIsNone(textnorm.merged_question_number("", 1))


class MergedQuestionFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem, **changes):
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question",
                             return_value={**READING, "stem": stem}), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url",
                             return_value="data:image/jpeg;base64,x"):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_card_holding_two_questions_is_not_green(self):
        stem = "你能举出现实生活中有关反比例函数的几个实例吗？\n\n2. 说说图象的联系和区别。"
        result = self.run_card(stem)

        self.assertTrue(any("第 2 题的题号" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_an_ordinary_card_stays_green(self):
        stem = "你能举出现实生活中有关反比例函数的几个实例吗？\n\n（1）说一说。\n（2）举例。"
        result = self.run_card(stem)

        self.assertFalse(any("的题号" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)