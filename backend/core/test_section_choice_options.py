"""大题标题写着选择题，卡上却一个选项都没有——不能让它悄悄变绿。

胜利初四月考第 8 题：原卷印着「一、单选题（本小题共10个小题，每小题4分，共40分。）」，
裁图里 A.2m B.2.6m C.3.2m D.3.6m 四个印刷选项都在，学生把四个选项字母都划掉了，
识读就把它读成填空题：零选项、零旗标、卡是绿的，学生拿到的是一道没有选项也做不出来的题。

已有的「选项缺位」「选项读多」都接不住这一类：它们问的是「识读以为有几个选项」，
而这一张的识读以为一个都没有。唯一不依赖识读、纸上写着的事实就是大题标题。

两条预防路线都在真机（MiniMax-M3）上量过、都没成，写在这里免得再走一遍：
- 把大题标题写进提示词：3 次读仍有 3 次零选项，题型在填空题/单选题之间抖；
- 加一条「选项被划掉也要照抄」的规则：3 次里 1 次读全四个、那次还多编了一个 E，
  对照组四张卡（正常四选×2、图选项、填空题）都没有变化——安全，但没用。
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, readers
from .models import Question

CHOICE_SECTION = "一、单选题（本小题共10个小题，每小题4分，共40分。）"
BLANK_SECTION = "二、填空题（本大题共5个小题，每小题4分，共20分。请直接填写答案。）"
LOST = {"stem": "图为某拦河坝改造前后河床的横断面示意图，$AD\\parallel BC$，坝高 $DC=8\\text{m}$，"
                 "此时河床面的宽减少的长度 $AE$ 等于（　　）（结果精确到 $0.1\\text{m}$）。",
        "options": {}, "type": "fill_blank", "content_kind": "exercise",
        "figures": {"1": "stem"}, "missing_figure": False, "number_seen": 8,
        "figure_descriptions": [], "unclear": False}
KEPT = {**LOST, "type": "single_choice",
        "options": {"A": "$2\\text{m}$", "B": "$2.6\\text{m}$",
                    "C": "$3.2\\text{m}$", "D": "$3.6\\text{m}$"}}


class SectionChoiceOptionTests(SimpleTestCase):
    def check(self, section, options, figures=()):
        return pipeline._section_choice_without_options(section, options, list(figures))

    def test_a_choice_section_with_no_options_at_all_is_reported(self):
        self.assertTrue(self.check(CHOICE_SECTION, {}))

    def test_four_read_options_are_clean(self):
        self.assertFalse(self.check(CHOICE_SECTION, KEPT["options"]))

    def test_option_images_count_as_options(self):
        # 真实数据里 14 张纯图选项卡的 options 字典本来就是空的。
        self.assertFalse(self.check(CHOICE_SECTION, {}, [{"slot": "A"}, {"slot": "B"},
                                                         {"slot": "C"}, {"slot": "D"}]))

    def test_one_stem_figure_is_not_an_option(self):
        self.assertTrue(self.check(CHOICE_SECTION, {}, [{"slot": "stem"}]))

    def test_a_blank_section_says_nothing(self):
        self.assertFalse(self.check(BLANK_SECTION, {}))

    def test_a_section_without_a_heading_says_nothing(self):
        self.assertFalse(self.check("", {}))

    def test_a_multi_choice_heading_counts_too(self):
        self.assertTrue(self.check("二、多选题：本题共3小题，每小题6分，共18分。", {}))

    def test_an_empty_option_value_is_not_an_option(self):
        self.assertTrue(self.check(CHOICE_SECTION, {"A": "  ", "B": ""}))

    def test_the_existing_checks_stay_silent_on_this_card(self):
        # 这就是它漏过去的原因：识读以为一个选项都没有，缺位和超量都无从谈起。
        self.assertEqual(pipeline._option_gaps(LOST["options"], []), [])
        self.assertEqual(pipeline._surplus_options(LOST["options"], []), [])


class SectionChoiceFlagTests(SimpleTestCase):
    def read(self, reading, section=CHOICE_SECTION):
        snapshot = {
            "id": 1, "number": 8, "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [512, 364, 977, 577]}],
            "candidates": [{"label": "1", "page_idx": 0, "bbox": [526, 420, 631, 551]}],
            "question_type": "single_choice", "section": section, "stem": "", "options": {},
            "edited": False, "source_kind": Question.SourceKind.UNKNOWN,
            "source_anchor_seq": 8, "segmentation_flags": [], "body_mode": "text",
            "start_source": "mineru",
        }
        with patch.object(readers, "primary_engine", return_value=object()), \
                patch.object(readers, "read_question", return_value=reading), \
                patch.object(pipeline.imaging, "stack_regions",
                             return_value=(SimpleNamespace(height=900, width=1400), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_the_lost_options_card_is_yellow(self):
        result = self.read(LOST)

        self.assertIn("一个选项都没有读出来", "　".join(result["flags"]))
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_card_that_read_its_options_stays_green(self):
        result = self.read(KEPT)

        self.assertFalse(any("一个选项都没有读出来" in flag for flag in result["flags"]),
                         result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_a_fill_in_blank_section_card_is_untouched(self):
        result = self.read(LOST, section=BLANK_SECTION)

        self.assertFalse(any("一个选项都没有读出来" in flag for flag in result["flags"]),
                         result["flags"])
