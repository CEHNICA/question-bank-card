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
from .tests import PAGES, ScriptedChat, fake_page_pdf, tagged


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


class OptionGapTests(SimpleTestCase):
    def test_a_skipped_letter_is_reported(self):
        self.assertEqual(pipeline._option_gaps({"A": "1", "C": "3", "D": "4"}, []), ["B"])
        self.assertEqual(pipeline._option_gaps({"B": "1", "C": "3", "D": "4"}, []), ["A"])
        self.assertEqual(pipeline._option_gaps({"A": "1", "B": "2", "C": "3"}, []), [])
        self.assertEqual(pipeline._option_gaps({}, []), [])

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
        chat = mock.Mock(return_value="1=印刷\n2＝手写\n3=看不清")
        with mock.patch.object(readers, "chat", chat):
            result = readers.verify_printed_figures(mock.Mock(), "data:,", 5, ["1", "2", "3"])
        self.assertEqual(result, {"1": "printed", "2": "handwritten"})
        self.assertIn("编号=印刷", chat.call_args.args[1])


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
        })
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "majority"), question.flags)
        self.assertTrue(question.read_c["spotwise"])

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
        question, chat = self.uncued_figure_card("1=印刷")
        self.assertEqual([call[0] for call in chat.calls].count("verify"), 1)
        self.assertEqual((question.state, len(question.figures)), (Question.State.GREEN, 1), question.flags)
        self.assertEqual(question.read_a["figures_verified"], {"1": "printed"})
        self.assertIn("printed_figure_confirmed", question.figure_review["signals"])
        # An unrelated later edit must not bring the warning back.
        question.stem = question.stem + "（改一个字）"
        question.figure_review = {}
        review = figure_policy.stored_or_derived_review(question)
        self.assertEqual(review["status"], figure_policy.OK)

    def test_a_sketch_keeps_the_card_for_a_person(self):
        question, _chat = self.uncued_figure_card("1=手写")
        self.assertEqual(question.state, Question.State.YELLOW)
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
