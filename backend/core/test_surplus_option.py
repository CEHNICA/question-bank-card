"""读到了第 5 个及以后的选项时要说一声，不能让四选的选择题悄悄变成五选。

胜利初四月考第 8 题：原卷只有 A/B/C/D 四个印刷选项，识读把题目右边学生手写的
8/√3 当成了第五个选项 E。已有的「选项缺位」检查在 A–E 齐全时返回空，所以这张卡
连一条提示都没有——学生答对了选 E 还会被判错。

守卫只提示、不删除：确实存在五选项的卷子，那是提示误伤的代价，而漏报要付的
是学生做不出来。全库 87 张四选项卡与 14 张图选项卡不受影响。
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, readers
from .models import Question

SURPLUS = {
    "stem": "图1为某拦河坝改造前后河床的横断面示意图，$AD\\parallel BC$，坝高 $DC=8\\text{m}$。",
    "options": {
        "A": "$2\\text{m}$",
        "B": "$2.6\\text{m}$",
        "C": "$3.2\\text{m}$",
        "D": "$3.6\\text{m}$",
        "E": "$\\dfrac{8}{\\sqrt{3}}\\text{m}$",
    },
    "content_kind": "question",
    "figures": {},
    "missing_figure": False,
    "number_seen": 8,
    "figure_descriptions": [],
    "unclear": False,
}
FOUR = {**SURPLUS, "options": {k: v for k, v in SURPLUS["options"].items() if k != "E"}}


class SurplusOptionTests(SimpleTestCase):
    def letters(self, options, figures=()):
        return pipeline._surplus_options(options, list(figures))

    def test_a_fifth_option_is_reported(self):
        self.assertEqual(self.letters(SURPLUS["options"]), ["E"])

    def test_four_printed_options_are_clean(self):
        self.assertEqual(self.letters(FOUR["options"]), [])

    def test_option_images_count_as_present(self):
        self.assertEqual(self.letters({}, [{"slot": "D"}]), [])

    def test_five_option_images_are_reported_too(self):
        slots = [{"slot": letter} for letter in "ABCDE"]
        self.assertEqual(self.letters({}, slots), ["E"])

    def test_an_empty_option_is_not_counted(self):
        options = {**FOUR["options"], "E": "   "}
        self.assertEqual(self.letters(options), [])

    def test_the_existing_gap_check_says_nothing_about_this_case(self):
        # This is why it slipped through: A–E complete means no missing letter.
        self.assertEqual(pipeline._option_gaps(SURPLUS["options"], []), [])


class SurplusOptionFlagTests(SimpleTestCase):
    def read(self, reading):
        # The stem says “图1”, so give the card its dam diagram; without it the
        # figure review would flag a missing picture and this test would be
        # measuring the wrong thing.
        reading = {**reading, "figures": {"1": "stem"}}
        snapshot = {
            "id": 1,
            "number": 8,
            "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [45, 400, 977, 620]}],
            "candidates": [{"label": "1", "page_idx": 0, "bbox": [80, 430, 300, 560]}],
            "question_type": "single_choice",
            "stem": "",
            "options": {},
            "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN,
            "source_anchor_seq": 8,
            "segmentation_flags": [],
            "body_mode": "text",
            "start_source": "mineru",
        }
        with (
            patch.object(readers, "primary_engine", return_value=object()),
            patch.object(readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(SimpleNamespace(height=900, width=1400), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"),
        ):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_the_card_is_flagged_instead_of_shipping_a_made_up_option(self):
        result = self.read(SURPLUS)

        self.assertIn("第 5 个及以后的选项", "　".join(result["flags"]))
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_normal_four_option_card_stays_green(self):
        result = self.read(FOUR)

        self.assertFalse(any("第 5 个及以后的选项" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)
