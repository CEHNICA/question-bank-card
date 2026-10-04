"""Local review safeguards that remain after the optional second read is removed."""

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


class LetterCaseObjectionTests(SimpleTestCase):
    def test_a_case_only_difference_in_mineru_is_no_objection(self):
        reading = {"stem": "如图，边长为 $c$ 的大正方形由四个直角三角形拼成"}
        self.assertEqual(textnorm.witness_objections(reading, "如图,边长为C的大正方形由四个直角三角形拼成"), [])
        # A different letter still is.
        self.assertTrue(textnorm.witness_objections(reading, "如图,边长为b的大正方形由四个直角三角形拼成"))


class OptionGapTests(SimpleTestCase):
    def test_a_skipped_letter_is_reported(self):
        self.assertEqual(pipeline._option_gaps({"A": "1", "C": "3", "D": "4"}, []), ["B"])
        self.assertEqual(pipeline._option_gaps({"B": "1", "C": "3", "D": "4"}, []), ["A"])
        self.assertEqual(pipeline._option_gaps({"A": "1", "B": "2", "C": "3"}, []), [])
        self.assertEqual(pipeline._option_gaps({}, []), [])
        # 胜利初四第 9 题：只读出了 B，其余三个选项都没了。
        self.assertEqual(pipeline._option_gaps({"B": "$\\dfrac{\\sqrt{3}}{3}$"}, []), ["A"])
        self.assertEqual(pipeline._option_gaps({"A": "1"}, []), [])

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

    def test_single_read_does_not_start_a_second_read_or_arbiter(self):
        question = self.card()
        chat = self.run_card(question, {
            ("a", 1): tagged("交x轴于点E，y≥0时，是否为定值？如果是，请求出"),
            ("b", 1): tagged("交x轴于点E，当y≥0时，是否为定值？若是，请求出"),
            ("arbiter", 1): tagged("交x轴于点E，当y≥0时，是否为定值？如果是，请求出"),
        })
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "single"), question.flags)
        self.assertEqual([call[0] for call in chat.calls], ["a"])

    def test_mineru_difference_is_visible_for_manual_review_without_an_ai_spot_check(self):
        # MinerU and the single vision reading differ at a clean one-character spot.
        question = self.card()
        self.paper.blocks.create(seq=1, type="text", page_idx=0, bbox=[60, 110, 470, 170],
                                 text="1. 小毅设计了包装盒，共有____种添补的方法")
        chat = self.run_card(question, {
            ("a", 1): tagged("小毅设计了包装盒，共有____种器补的方法"),
            ("spotcheck", 1): lambda prompt: "1=不确定",
        })
        self.assertEqual(question.state, Question.State.YELLOW)
        flag = next(f for f in question.flags if f.startswith(pipeline.WITNESS_FLAG_PREFIX))
        self.assertIn("【器】", flag)
        self.assertTrue(question.read_c["unverified"])
        self.assertEqual([call[0] for call in chat.calls], ["a"])

    def test_a_choice_question_missing_a_letter_is_not_green(self):
        question = self.card()
        options = {"B": "甲命题成立", "C": "乙命题成立", "D": "丙命题成立"}
        self.run_card(question, {("a", 1): tagged("已知二元函数，则", options),
                                 ("b", 1): tagged("已知二元函数，则", options)})
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertIn("选项 A 没有读出来，请对照原卷补上", question.flags)

    def test_unjudged_candidate_stays_flagged_after_a_later_edit(self):
        question = self.card(candidates=[1, 2])
        stem = "如图，在菱形 $ABCD$ 中，$AC=8$，求 $BD$ 的长。"
        chat = self.run_card(question, {("a", 1): tagged(stem, figures="1=题干"),
                                        ("classify", 1): "2=无关"})
        self.assertEqual(question.state, Question.State.YELLOW, question.flags)
        self.assertEqual([call[0] for call in chat.calls], ["a"])
        question.stem = stem + "（改一个字）"
        question.figure_review = {}
        review = figure_policy.stored_or_derived_review(question)
        self.assertIn("candidate_unclassified", review["signals"])
