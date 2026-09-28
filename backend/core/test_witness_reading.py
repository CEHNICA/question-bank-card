"""Cross-engine witness: MinerU's own text can stand in for a second vision read."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from . import pipeline, readers
from .models import Paper, Question
from .tests import PAGES, ScriptedChat, fake_page_pdf, tagged
from .textnorm import canon, clean_stem, witness_agrees, witness_choice, witness_key


class WitnessKeyTests(SimpleTestCase):
    def test_formatting_differences_between_engines_are_ignored(self):
        reading = {
            "stem": r"已知 $b$ 是 $a,c$ 的等差中项，则 $|AB|$ 的最小值为（ ）",
            "options": {"A": "2", "B": "3", "C": "4", "D": r"$2\sqrt{5}$"},
        }
        mineru = r"12. 已知b是 a c, 的等差中项，则 $\left| A B \right|$ 的最小值为（ ） A. 2 B. 3 C. 4 D. $2 \sqrt { 5 }$"
        self.assertTrue(witness_agrees(reading, mineru))

    def test_degrees_parallel_and_score_markers_normalise(self):
        self.assertEqual(witness_key(r"（本题满分6分）$BC / / AD$，$60 ^ { \circ }$"),
                         witness_key(r"$BC\parallel AD$，60°"))
        self.assertEqual(canon(r"$60 ^ { \circ }$"), canon("60°"))

    def test_any_content_difference_is_disagreement(self):
        base = {"stem": "E 为 OB 上一动点，连接 CE，若 OE=1"}
        self.assertFalse(witness_agrees(base, "E为OB上一点，连接CE，若OE=1"))
        self.assertFalse(witness_agrees({"stem": "0.12122122221"}, "0.1212212221"))
        self.assertFalse(witness_agrees({"stem": "x=0.5 时"}, "x=05 时"))

    def test_unclear_or_short_readings_never_count(self):
        self.assertFalse(witness_agrees({"stem": "CD=[?]", "unclear": True}, "CD=[?]"))
        self.assertFalse(witness_agrees({"stem": "求 x"}, "求 x"))
        self.assertFalse(witness_agrees({"stem": ""}, ""))

    def test_handwritten_answer_letter_is_ignored_but_probability_notation_is_not(self):
        reading = {"stem": "无理数的个数是（ ）个", "options": {"A": "1", "B": "2"}}
        self.assertTrue(witness_agrees(reading, "3. 无理数的个数是（ C ）个 A. 1 B. 2"))
        self.assertFalse(witness_agrees({"stem": "则 P(A) 与 P(B) 的和为"}, "则 P(B) 与 P(B) 的和为"))

    def test_student_handwriting_in_ocr_blocks_the_shortcut(self):
        reading = {"stem": "下列各组数中，是勾股数的是（ ）", "options": {"A": "12,8,5", "B": "9,12,15"}}
        self.assertFalse(witness_agrees(reading, "1. 下列各组数中，是勾股数的是（C）$\\frac{n5}{225}$ A. 12, 8, 5 B. 9,12,15"))


class StemCleanupTests(SimpleTestCase):
    def test_number_and_score_markers_are_removed_only_when_they_are_this_question(self):
        self.assertEqual(clean_stem("14 如图，在正五边形内部", 14), "如图，在正五边形内部")
        self.assertEqual(clean_stem("3 个数中，最大的是", 14), "3 个数中，最大的是")
        self.assertEqual(clean_stem("（本题满分14分）\n（1）比较大小", 17), "（1）比较大小")
        self.assertEqual(clean_stem("17.（本题满分6分）已知", 17), "已知")


class WitnessChoiceTests(SimpleTestCase):
    """Real disagreements from marked papers, settled by MinerU's text."""

    def test_inserted_word_loses_even_with_handwriting_in_the_witness(self):
        a = {"stem": "(2) 求直线 $l$ 与两坐标轴围成的图形面积."}
        b = {"stem": "(2) 求直线 $l$ 与两坐标轴围成的图形的面积."}
        witness = "15. (13分) 已知直线 l 经过点(1,6)… (2) 求直线 l 与两坐标轴围成的图形面积. y-6=-2(x-1) K=-2"
        self.assertEqual(witness_choice(a, b, witness), "a")
        self.assertEqual(witness_choice(b, a, witness), "b")

    def test_swapped_letters_and_unclear_marks(self):
        a = {"stem": "若 AD⊥BD，AB=5，BC=3，EF=8，求点 D 到 AF 的距离"}
        b = {"stem": "若 AD⊥BD，AB=5，BC=3，FE=8，求点 D 到 AF 的距离"}
        self.assertEqual(witness_choice(a, b, "(2) 若 AD⊥BD, AB=5, BC=3, FE=8, 求点 D 到 AF 的距离. ∴AE=CF"), "b")
        c = {"stem": "CD=[?]，以 AC，AD 为邻边", "unclear": True}
        d = {"stem": "CD=6，以 AC，AD 为邻边"}
        self.assertEqual(witness_choice(c, d, "8. 如图，AB=10，CD=6，以AC，AD为邻边作"), "b")

    def test_a_reading_missing_its_opening_sentence_never_wins(self):
        truncated = {"stem": "(1) 求梯子靠墙的顶端 A 距地面有多少米？"}
        complete = {"stem": "如图，一架 25 米长的梯子 AB 斜靠在墙 AO 上。(1) 求梯子靠墙的顶端 A 距地面有多少米？"}
        witness = "0.（本题满分6分） 图，一架25米长的梯子AB斜靠在墙A0上。(1) 求梯子靠墙的顶端 A 距地面有多少米？"
        self.assertIsNone(witness_choice(truncated, complete, witness))
        self.assertEqual(clean_stem("（（1）如图1，长方体", 24), "（1）如图1，长方体")

    def test_undecided_or_split_differences_defer_to_the_arbiter(self):
        a = {"stem": "已知 x=1，y=2，求 z"}
        b = {"stem": "已知 x=7，y=3，求 z"}
        self.assertIsNone(witness_choice(a, b, "已知 x=1，y=3，求 z"))     # split vote
        self.assertIsNone(witness_choice(a, b, "完全无关的文字"))            # no support
        self.assertIsNone(witness_choice(a, dict(a), "已知 x=1，y=2，求 z"))  # no difference


class WitnessPipelineTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test", "SILICONFLOW_API_KEY": "test2"})
        env.start()
        self.addCleanup(env.stop)
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="w" * 64,
                                          status=Paper.Status.READING)
        folder = self.temp / str(self.paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        self.paper.source_path = str(folder / "source.pdf")
        self.paper.pages = PAGES[:1]
        self.paper.save()

    def card(self, text: str) -> Question:
        self.paper.blocks.create(seq=1, type="text", page_idx=0, bbox=[60, 110, 470, 170], text=text)
        return Question.objects.create(
            paper=self.paper, number=1, question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [50, 100, 480, 200]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 100, 480, 200]}],
        )

    def test_agreeing_witness_skips_the_checker(self):
        question = self.card("1. 已知函数 $f ( x ) = x ^ { 2 }$ ，求 $f ( 2 )$ 的值。")
        chat = ScriptedChat({("a", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。")})
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "witness"))
        self.assertEqual([call[0] for call in chat.calls], ["a"])
        self.assertEqual(question.read_b["skipped"], "witness")
        self.assertNotIn("stem", question.read_b)

    def test_disagreeing_witness_falls_back_to_an_independent_reader(self):
        question = self.card("1. 已知函数 $f ( x ) = x ^ { 3 }$ ，求 $f ( 2 )$ 的值。")
        chat = ScriptedChat({
            ("a", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
            ("b", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "agree"))
        self.assertEqual(sorted(call[0] for call in chat.calls), ["a", "b"])

    def test_failed_primary_still_uses_the_checker(self):
        question = self.card("1. 已知函数 $f ( x ) = x ^ { 2 }$ ，求 $f ( 2 )$ 的值。")
        chat = ScriptedChat({
            ("a", 1): readers.ReaderError("MiniMax 接口返回 HTTP 500"),
            ("b", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual(question.text_source, "single")
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_witness_breaks_a_disagreement_without_an_arbiter(self):
        question = self.card("3. 在 $0 . 1 2 1 2 2 1 2 2 2 1 \\ldots$ 这些数中，无理数的个数是（ ）个")
        chat = ScriptedChat({
            ("a", 1): tagged("在 $0.12122122221\\ldots$ 这些数中，无理数的个数是（ ）个"),
            ("b", 1): tagged("在 $0.1212212221\\ldots$ 这些数中，无理数的个数是（ ）个"),
            ("arbiter", 1): tagged("在 $0.12122122221\\ldots$ 这些数中，无理数的个数是（ ）个"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual(question.text_source, "majority")
        self.assertIn("0.1212212221", question.stem)
        self.assertNotIn("arbiter", [call[0] for call in chat.calls])
        self.assertEqual(question.read_c["skipped"], "witness")
