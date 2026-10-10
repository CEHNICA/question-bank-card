"""原卷印着的答案要能落到卡上。

2026-10-11：真库 14 份资料 959 张卡里只有「202510胜利初四数学答案版本.pdf」
印了答案，25 道题每道都印着【答案】…，而 25 张卡的 answer 全是空字符串。
界面照实写着「原卷未提供答案与解析。」——一句假话。根因是
readers.TRANSCRIBE_RULES/OUTPUT_FORMAT 只让模型读题面，没有答案这一栏。
"""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from core import pipeline, printed_answers
from core.models import Question
from core.qtypes import CHOICE_TYPES


class PrintedAnswerTests(TestCase):
    def blocks(self, *texts):
        return [{"page_idx": 0, "bbox": [0, 0, 10, 10], "text": text, "seq": i}
                for i, text in enumerate(texts)]

    def test_the_printed_answer_comes_off_the_page(self):
        # 真卷第 1 题那一行：MinerU 读出来就是「【答案】A」。
        found = printed_answers.marks(self.blocks("1.下列各式中…", "【答案】A"))
        self.assertEqual(found, ["A"])

    def test_only_one_card_can_claim_a_printed_answer(self):
        # 调用方只给本卡范围内的块（segment.center_in_regions 过滤过），
        # 所以这里拿到的必须只有本卡那一条。
        found = printed_answers.marks(self.blocks("【答案】C"))
        self.assertEqual(found, ["C"])

    def test_a_question_that_asks_about_answers_is_not_a_printed_answer(self):
        # 题干自己写着「答案是 B」是这道题在问，不能当成原卷给的答案。
        found = printed_answers.marks(self.blocks("下列说法正确的是（ ），答案是 B"))
        self.assertEqual(found, [])

    def test_the_next_marker_cuts_the_value_short(self):
        found = printed_answers.marks(self.blocks("【答案】A【分析】本题考查…"))
        self.assertEqual(found, ["A"])

    def test_a_single_letter_answer_lands_on_a_choice_card(self):
        self.assertEqual(
            printed_answers.choice_letter(["C"], "single_choice", CHOICE_TYPES), "C")
        # 这道题的选项印在图里，卡上一个选项文字都没有，答案照样是纸上那个。
        self.assertEqual(
            printed_answers.choice_letter(["C"], "single_choice", CHOICE_TYPES), "C")

    def test_prose_answers_are_never_copied_onto_the_card(self):
        # 真卷第 11 题的【答案】被 MinerU 读成了这样：照抄会把乱码印上答案卷。
        garbled = r"$\frac { 5 \sqrt { 3 4 } } { 3 4 } = 1 \times \fra"
        self.assertIsNone(printed_answers.choice_letter([garbled], "fill_blank", CHOICE_TYPES))
        # 就算题型是选择，答案不是单个字母也不填。
        self.assertIsNone(printed_answers.choice_letter([garbled], "single_choice", CHOICE_TYPES))
        self.assertIsNone(printed_answers.choice_letter(["AB"], "single_choice", CHOICE_TYPES))

    def test_two_printed_answers_means_the_range_spans_two_questions(self):
        self.assertIsNone(
            printed_answers.choice_letter(["A", "C"], "single_choice", CHOICE_TYPES))

    def test_no_marker_means_nothing_to_do(self):
        self.assertEqual(printed_answers.marks(self.blocks("1. 已知…", "解：设 x=1")), [])
        self.assertEqual(printed_answers.marks([]), [])
        self.assertIsNone(printed_answers.choice_letter([], "single_choice", CHOICE_TYPES))


CHOICE_READING = {
    "stem": "下列各式中，$y$ 一定是 $x$ 的反比例函数的是（    ）",
    "options": {"A": "$y=2x^{-1}$", "B": "$y=\\dfrac{2x}{3}$",
                "C": "$y=\\dfrac{k}{x}$", "D": "$y=-x^{2}$"},
    "type": "single_choice", "content_kind": "exercise", "figures": {},
    "missing_figure": False, "number_seen": 1, "figure_descriptions": [], "unclear": False,
}
BLANK_READING = {
    "stem": "化简：$\\sqrt{9}$ = ______。", "options": {}, "type": "fill_blank",
    "content_kind": "exercise", "figures": {}, "missing_figure": False,
    "number_seen": 13, "figure_descriptions": [], "unclear": False,
}


class PrintedAnswerReReadTests(SimpleTestCase):
    """read_card 里那几行：印着的字母落进答案栏，印着的一段话只提示。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def snapshot(self, texts, reading, **changes):
        base = {
            "id": 1, "number": reading.get("number_seen"), "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [58, 117, 932, 357]}],
            "candidates": [], "question_type": reading["type"], "stem": reading["stem"],
            "options": reading["options"], "edited": False, "answer": "",
            "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": None,
            "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
            "witness": "\n".join(texts),
            "witness_blocks": [
                {"page_idx": 0, "bbox": [58, 117 + 10 * i, 932, 127 + 10 * i],
                 "text": text, "seq": i, "type": "text"}
                for i, text in enumerate(texts)],
        }
        base.update(changes)
        return base

    def run_card(self, texts, reading, **changes):
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question", return_value=reading), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url",
                             return_value="data:image/jpeg;base64,x"):
            return pipeline.read_card(self.snapshot(texts, reading, **changes), self.store)

    def test_the_printed_letter_lands_on_the_card(self):
        # 真卷第 1 题：原页上「【答案】A」就在这道题的范围内，识读回来时却是空的。
        result = self.run_card(["1.下列各式中…", "【答案】A", "【分析】本题考查…"],
                               CHOICE_READING)

        self.assertEqual(result["answer"], "A")
        self.assertNotIn(pipeline.FLAG_PRINTED_ANSWER, result["flags"])

    def test_a_prose_answer_is_asked_for_not_guessed(self):
        # 真卷第 13 题印的是【答案】10，题型是填空题；这种答案不自动填。
        result = self.run_card(["13.化简…", "【答案】10"], BLANK_READING)

        self.assertNotIn("answer", result)
        self.assertIn(pipeline.FLAG_PRINTED_ANSWER, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_an_answer_the_teacher_typed_is_never_overwritten(self):
        result = self.run_card(["1.下列各式中…", "【答案】A"], CHOICE_READING, answer="D")

        self.assertNotIn("answer", result)

    def test_a_paper_without_printed_answers_changes_nothing(self):
        result = self.run_card(["1.下列各式中…", "【分析】本题考查…"], CHOICE_READING)

        self.assertNotIn("answer", result)
        self.assertNotIn(pipeline.FLAG_PRINTED_ANSWER, result["flags"])
