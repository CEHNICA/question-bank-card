"""Fewer false yellow cards without letting real reading errors through.

- The arbiter may take each reader's word at different spots (two votes each).
- A choice question whose options skip a letter is never green.
- A bound figure the text never mentions is confirmed by one narrow question.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from . import figure_policy, pipeline, readers, textnorm
from .models import Paper, Question
from .tests import PAGES, ScriptedChat, fake_page_pdf, spot_answer, tagged


def reading(stem, **options):
    return {"stem": stem, "options": options}


class SpotwiseMajorityTests(SimpleTestCase):
    def test_each_spot_backed_by_one_reader_is_a_majority(self):
        # 胜利十中第 25 题：A 漏了“当”，B 把“如果是”读成“若是”；裁决两处各随一方。
        a = reading("交x轴于点E，y≥0时，是否为定值？如果是，请求出")
        b = reading("交x轴于点E，当y≥0时，是否为定值？若是，请求出")
        c = reading("交x轴于点E，当y≥0时，是否为定值？如果是，请求出")
        self.assertTrue(textnorm.spotwise_majority(a, b, c))

    def test_something_neither_reader_read_is_not(self):
        # 裁决在两位读者都读作 2√3 的地方写了 2√5。
        a = reading("在△ABC中，AF=2\\sqrt{3}，求BF")
        b = reading("在VABC中，AF=2\\sqrt{3}，求BF")
        c = reading("在VABC中，AF=2\\sqrt{5}，求BF")
        self.assertFalse(textnorm.spotwise_majority(a, b, c))

    def test_three_different_readings_of_one_spot_are_not(self):
        a = reading("从正面看、从左面看得到的形状")
        b = reading("从正面看和从左面看得到的形状")
        c = reading("从正面看从左面看得到的形状")
        self.assertFalse(textnorm.spotwise_majority(a, b, c))

    def test_neighbouring_spots_are_one_spot(self):
        # shengli8 #19：“已知点” / “知识点” 被裁成 “知点”，两位读者都没这样读。
        a = reading("已知点A、B、C在数轴上表示的数a、b、c的位置如图所示")
        b = reading("知识点A、B、C在数轴上表示的数a、b、c的位置如图所示")
        c = reading("知点A、B、C在数轴上表示的数a、b、c的位置如图所示")
        self.assertFalse(textnorm.spotwise_majority(a, b, c))

    def test_options_are_part_of_the_comparison(self):
        a = reading("下列计算正确的是", A="1", B="2")
        b = reading("下列计算正确的是", A="1", B="3")
        self.assertFalse(textnorm.spotwise_majority(a, b, reading("下列计算正确的是", A="1", B="4")))
        self.assertTrue(textnorm.spotwise_majority(a, b, reading("下列计算正确的是", A="1", B="3")))


class EchoedNumberTests(SimpleTestCase):
    def test_arbiter_number_is_dropped_when_no_reader_has_it(self):
        c = pipeline._without_echoed_number(reading("9如图，在△ABC中"), 9, (reading("如图，在△ABC中"),))
        self.assertEqual(c["stem"], "如图，在△ABC中")
        c = pipeline._without_echoed_number(reading("9．如图"), 9, (reading("如图"),))
        self.assertEqual(c["stem"], "如图")

    def test_a_stem_that_really_starts_with_the_number_is_kept(self):
        kept = pipeline._without_echoed_number(reading("9个同学排队"), 9, (reading("9个同学排队"),))
        self.assertEqual(kept["stem"], "9个同学排队")
        decimal = pipeline._without_echoed_number(reading("9.5米长的绳子"), 9, (reading("长的绳子"),))
        self.assertEqual(decimal["stem"], "9.5米长的绳子")


class DisputedSpotTests(SimpleTestCase):
    def test_each_spot_names_the_side_the_judge_took(self):
        a = reading("交x轴于点E，y≥0时，是否为定值？如果是，请求出")
        b = reading("交x轴于点E，当y≥0时，是否为定值？若是，请求出")
        c = reading("交x轴于点E，当y≥0时，是否为定值？如果是，请求出")
        spots = textnorm.disputed_spots(a, b, c)
        self.assertEqual([(s["reading"], s["mineru"], s["side"]) for s in spots], [("当", "", "b"), ("如果", "若", "a")])

    def test_a_judge_matching_neither_side_gives_no_spots(self):
        a, b = reading("AF=2\\sqrt{3}"), reading("AF=3\\sqrt{3}")
        self.assertIsNone(textnorm.disputed_spots(a, b, reading("AF=5\\sqrt{3}")))
        self.assertEqual(textnorm.disputed_spots(a, a, a), [])

    def test_punctuation_differences_are_not_worth_a_question(self):
        spot = lambda a, b, before="xx", after="yy": {"reading": a, "mineru": b, "before": before, "after": after}
        for a, b in (("()", ""), ("'", "′"), (".", ";"), ("_", "()"), ("", ".")):
            self.assertTrue(textnorm.punctuation_only_spot(spot(a, b)), (a, b))
        self.assertFalse(textnorm.punctuation_only_spot(spot("", ".", before="2", after="5")))  # 2.5 / 25
        for a, b in (("6", "5"), ("∴", "∵"), ("", "sqrt"), ("-", ""), ("定", "单")):
            self.assertFalse(textnorm.punctuation_only_spot(spot(a, b)), (a, b))

    def test_empty_side_is_shown_as_empty_in_the_question(self):
        prompt, _order = readers.spot_check_prompt([{"reading": "±", "mineru": "", "before": "方根是", "after": ""}])
        self.assertIn("（空）", prompt)


class OptionGapTests(SimpleTestCase):
    def test_a_skipped_letter_is_reported(self):
        self.assertEqual(pipeline._option_gaps({"A": "1", "C": "3", "D": "4"}, []), ["B"])
        self.assertEqual(pipeline._option_gaps({"B": "1", "C": "3", "D": "4"}, []), ["A"])
        self.assertEqual(pipeline._option_gaps({"A": "1", "B": "2", "C": "3"}, []), [])
        self.assertEqual(pipeline._option_gaps({}, []), [])

    def test_two_identical_options_are_reported(self):
        options = {"A": "$\\dfrac{1}{2024}$", "B": "$-\\dfrac{1}{2024}$", "C": "$-\\frac{1}{2024}$", "D": "2024"}
        self.assertEqual(pipeline._identical_options(options), ["B", "C"])
        self.assertEqual(pipeline._identical_options({"A": "1", "B": "2", "C": "", "D": ""}), [])

    def test_option_images_fill_their_letter(self):
        figures = [{"slot": "B", "page_idx": 0, "bbox": [0, 0, 1, 1]}]
        self.assertEqual(pipeline._option_gaps({"A": "1", "C": "3", "D": "4"}, figures), [])

    def test_restore_takes_the_option_another_reader_read(self):
        final = reading("下列计算正确的是", A="-6-(-4)=-1", C="1+1=2", D="12-27=-15")
        other = reading("下列计算正确的是", A="-6-(-4)=-1", B="$-7.25+3\\dfrac{1}{4}=4$", C="1+1=2", D="12-27=-15")
        witness = "9.下列计算正确的是 A.-6-(-4)=-1 C.1+1=2 B. $-7.25+3\\frac{1}{4}=4$ D.12-27=-15"
        restored, supported = pipeline._restore_skipped_options(final, [final, other], witness)
        self.assertEqual(list(restored["options"]), ["A", "B", "C", "D"])
        self.assertEqual(supported, {"B": True})
        _, unsupported = pipeline._restore_skipped_options(final, [final, other], "")
        self.assertEqual(unsupported, {"B": False})

    def test_a_shifted_label_is_not_a_skipped_option(self):
        # 学生的叉盖住了“A.”：一位读者从 B 读起，另一位把 B 的内容当成 A。
        final = reading("则", B="甲命题", C="乙命题", D="丙命题")
        shifted = reading("则", A="甲命题", B="乙命题", C="丙命题")
        restored, supported = pipeline._restore_skipped_options(final, [final, shifted], "")
        self.assertEqual(supported, {})
        self.assertEqual(pipeline._option_gaps(restored["options"], []), ["A"])


class PrintedFigureParsingTests(SimpleTestCase):
    def test_answers_are_read_per_box(self):
        chat = mock.Mock(return_value="1=本题\n2＝手写\n3=别题\n4=看不清")
        with mock.patch.object(readers, "chat", chat):
            result = readers.verify_printed_figures(mock.Mock(), "data:,", 5, ["1", "2", "3", "4"])
        self.assertEqual(result, {"1": "printed", "2": "handwritten", "3": "other"})
        self.assertIn("编号=本题", chat.call_args.args[1])


class ReadingPipelineTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test", "SILICONFLOW_API_KEY": "test2"})
        env.start()
        self.addCleanup(env.stop)
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="r" * 64,
                                          status=Paper.Status.READING)
        folder = self.temp / str(self.paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        self.paper.source_path = str(folder / "source.pdf")
        self.paper.pages = PAGES[:1]
        self.paper.save()

    def card(self, candidates=()):
        return Question.objects.create(
            paper=self.paper, number=1, question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [50, 100, 480, 200]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 100, 480, 200]}],
            figure_candidates=[
                {"label": str(index), "seq": 6 + index, "page_idx": 0, "bbox": [300.0, 120.0, 380.0, 180.0]}
                for index in range(1, len(candidates) + 1)
            ],
        )

    def run_card(self, question, answers):
        chat = ScriptedChat(answers)
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        return chat

    def test_arbiter_siding_with_each_reader_somewhere_is_green(self):
        question = self.card()
        self.run_card(question, {
            ("a", 1): tagged("交x轴于点E，y≥0时，是否为定值？如果是，请求出"),
            ("b", 1): tagged("交x轴于点E，当y≥0时，是否为定值？若是，请求出"),
            ("arbiter", 1): tagged("交x轴于点E，当y≥0时，是否为定值？如果是，请求出"),
            ("spotcheck", 1): spot_answer("当", "如果"),
        })
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "majority"), question.flags)
        self.assertTrue(question.read_c["spotwise"])
        self.assertEqual(question.read_c["second_look"]["answers"], ["reading", "reading"])

    def test_an_arbiter_choice_the_second_look_doubts_stays_yellow(self):
        # 胜利十中第 21 题：裁决跟了读法甲的“单价”，原卷印的是“定价”。
        question = self.card()
        self.run_card(question, {
            ("a", 1): tagged("通过前几天的销售发现，当销售单价为15元时，每天可售出700本"),
            ("b", 1): tagged("通过前几天的销售发现，当销售定价为15元时，每天可售出700本"),
            ("arbiter", 1): tagged("通过前几天的销售发现，当销售单价为15元时，每天可售出700本"),
            ("spotcheck", 1): spot_answer("定"),
        })
        self.assertEqual((question.state, question.text_source), (Question.State.YELLOW, "majority"))
        self.assertIn("单价", question.stem)            # never rewritten by the check
        flag = next(f for f in question.flags if f.startswith(pipeline.SECOND_LOOK_FLAG_PREFIX))
        self.assertIn("【单】", flag)
        self.assertIn("另一次识读：定", flag)

    def test_arbiter_inventing_a_spot_stays_yellow(self):
        question = self.card()
        self.run_card(question, {
            ("a", 1): tagged("在△ABC中，AF=2\\sqrt{3}，求BF"),
            ("b", 1): tagged("在VABC中，AF=2\\sqrt{3}，求BF"),
            ("arbiter", 1): tagged("1如图，在VABC中，AF=2\\sqrt{5}，求BF"),
        })
        self.assertEqual(question.text_source, "arbiter")
        self.assertIn("两次识读不一致，已由第三次识读裁决", question.flags)
        self.assertTrue(question.stem.startswith("如图"))

    def test_mineru_still_gets_its_say_after_the_arbiter(self):
        # shengli7 #16：两位读者和裁决都写“器补”，MinerU 读的是印刷的“添补”。
        question = self.card()
        self.paper.blocks.create(seq=1, type="text", page_idx=0, bbox=[60, 110, 470, 170],
                                 text="1. 小毅设计了包装盒，共有____种添补的方法")
        self.run_card(question, {
            ("a", 1): tagged("三、解答题 小毅设计了包装盒，共有____种器补的方法"),
            ("b", 1): tagged("小毅设计了包装盒，共有____种器补的方法和步骤"),
            ("arbiter", 1): tagged("小毅设计了包装盒，共有____种器补的方法"),
            ("spotcheck", 1): lambda prompt: "1=不确定\n2=不确定\n3=不确定",
        })
        self.assertEqual(question.state, Question.State.YELLOW)
        flag = next(f for f in question.flags if f.startswith(pipeline.ARBITER_OBJECTION_FLAG_PREFIX))
        self.assertIn("【器】", flag)
        self.assertEqual(question.read_c["stem"], "小毅设计了包装盒，共有____种器补的方法")   # arbiter kept
        self.assertIn("objection_check", question.read_c)

    def test_a_choice_question_missing_a_letter_is_not_green(self):
        question = self.card()
        options = {"B": "甲命题成立", "C": "乙命题成立", "D": "丙命题成立"}
        self.run_card(question, {("a", 1): tagged("已知二元函数，则", options),
                                 ("b", 1): tagged("已知二元函数，则", options)})
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertIn("选项 A 没有读出来，请对照原卷补上", question.flags)

    def uncued_figure_card(self, verdict):
        question = self.card(candidates=[1])
        stem = "在平行四边形ABCD中，作线段AC的垂直平分线，求四边形ANCM的面积。"
        chat = self.run_card(question, {
            ("a", 1): tagged(stem, figures="1=题干"),
            ("b", 1): tagged(stem),
            ("verify", 1): verdict,
        })
        return question, chat

    def test_a_confirmed_printed_figure_without_a_text_cue_is_green(self):
        question, chat = self.uncued_figure_card("1=本题")
        self.assertEqual([call[0] for call in chat.calls].count("verify"), 1)
        self.assertEqual((question.state, len(question.figures)), (Question.State.GREEN, 1), question.flags)
        self.assertEqual(question.read_a["figures_verified"], {"1": "printed"})
        self.assertIn("printed_figure_confirmed", question.figure_review["signals"])
        # An unrelated later edit must not bring the warning back.
        question.stem = question.stem + "（改一个字）"
        question.figure_review = {}
        review = figure_policy.stored_or_derived_review(question)
        self.assertEqual(review["status"], figure_policy.OK)

    def test_a_sketch_or_another_questions_figure_keeps_the_card_for_a_person(self):
        for verdict in ("1=手写", "1=别题", "看不清"):
            Question.objects.all().delete()
            question, _chat = self.uncued_figure_card(verdict)
            self.assertEqual(question.state, Question.State.YELLOW, verdict)
            self.assertIn(figure_policy.FLAG_UNCUED_FIGURE, question.flags)

    def test_followup_judgements_survive_a_later_edit(self):
        question = self.card(candidates=[1, 2])
        stem = "如图，在菱形 $ABCD$ 中，$AC=8$，求 $BD$ 的长。"
        self.run_card(question, {("a", 1): tagged(stem, figures="1=题干"), ("b", 1): tagged(stem),
                                 ("classify", 1): "2=无关"})
        self.assertEqual(question.state, Question.State.GREEN, question.flags)
        question.stem = stem + "（改一个字）"
        question.figure_review = {}
        review = figure_policy.stored_or_derived_review(question)
        self.assertNotIn("candidate_unclassified", review["signals"])
