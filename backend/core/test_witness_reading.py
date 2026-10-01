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
        mineru = r"12. 已知b是 a,c 的等差中项，则 $\left| A B \right|$ 的最小值为（ ） A. 2 B. 3 C. 4 D. $2 \sqrt { 5 }$"
        self.assertTrue(witness_agrees(reading, mineru))
        # A moved comma can alter a mathematical expression, so it now falls
        # back to a second vision reading instead of counting as agreement.
        self.assertFalse(witness_agrees(reading, mineru.replace("a,c", "a c,")))

    def test_degrees_parallel_and_score_markers_normalise(self):
        self.assertEqual(witness_key(r"（本题满分6分）$BC / / AD$，$60 ^ { \circ }$"),
                         witness_key(r"$BC\parallel AD$，60°"))
        self.assertEqual(canon(r"$60 ^ { \circ }$"), canon("60°"))

    def test_any_content_difference_is_disagreement(self):
        base = {"stem": "E 为 OB 上一动点，连接 CE，若 OE=1"}
        self.assertFalse(witness_agrees(base, "E为OB上一点，连接CE，若OE=1"))
        self.assertFalse(witness_agrees({"stem": "0.12122122221"}, "0.1212212221"))
        self.assertFalse(witness_agrees({"stem": "x=0.5 时"}, "x=05 时"))

    def test_mathematical_punctuation_cannot_disappear_in_a_green_card(self):
        for reading, mineru in (
            ("x=2，求x的值", "|x|=2，求x的值"),
            ("点A与点B连接，求线段AB", "点A'与点B连接，求线段A'B"),
            ("求5的阶乘是多少", "求5!的阶乘是多少"),
            ("求数列a1的首项", r"求数列$a_{1}$的首项"),
            ("已知ab=6，求a", r"已知$a\cdot b=6$，求a"),
            ("已知2x+1=6，求x", "已知2(x+1)=6，求x"),
            ("点P(12,3)与点Q", "点P(1,23)与点Q"),
            ("若xy=2，求x", "若x:y=2，求x"),
            ("已知x=2，求x", "已知[x]=2，求x"),
            ("数列1,2,3求通项", "数列1,2,3,…求通项"),
            ("已知ab=6，求a", "已知a.b=6，求a"),
            ("已知x=2，求x", "已知x=2；求x"),
            ("求x=2时的值", "求x?=2时的值"),
            ('已知x=2，求x', '已知"x"=2，求x'),
        ):
            with self.subTest(mineru=mineru):
                self.assertFalse(witness_agrees({"stem": reading}, mineru))

    def test_decorations_are_not_discarded_as_layout(self):
        for decorated, plain in (
            (r"$\overline{AB}=CD$，求AB", "$AB=CD$，求AB"),
            (r"$\hat{x}=1$，求x", "$x=1$，求x"),
            (r"$\bar{x}=1$，求x", "$x=1$，求x"),
            (r"$\widehat{ABC}=60°$，求角ABC", "$ABC=60°$，求角ABC"),
            (r"$\underline{x}=2$，求x", "$x=2$，求x"),
            (r"$a\stackrel{*}{=}b$，求a", "$a=b$，求a"),
            ("已知a~b，求a", "已知ab，求a"),
        ):
            with self.subTest(decorated=decorated):
                self.assertFalse(witness_agrees({"stem": plain}, decorated))

    def test_html_subscripts_and_superscripts_keep_their_meaning(self):
        self.assertEqual(witness_key("x<sub>2</sub>"), witness_key(r"$x_{2}$"))
        self.assertEqual(witness_key("x<sup>2</sup>"), witness_key(r"$x^{2}$"))
        self.assertNotEqual(witness_key("x<sub>2</sub>"), witness_key("x2"))
        self.assertNotEqual(witness_key("x<sup>2</sup>"), witness_key("x2"))
        self.assertNotEqual(witness_key("1.23米是多少"), witness_key("23米是多少"))

    def test_parallelogram_stays_distinct_from_square_in_witness(self):
        self.assertEqual(canon("▱ABCD"), canon("□ABCD"))  # existing two-reader comparison
        self.assertNotEqual(witness_key("如图在▱ABCD中求面积"), witness_key("如图在□ABCD中求面积"))
        raw_square = "【题干】如图在□ABCD中求面积"
        reading = readers.parse_reading(raw_square, 1)
        self.assertEqual(reading["stem"], "如图在▱ABCD中求面积")
        reading["raw"] = raw_square
        self.assertFalse(witness_agrees(reading, "如图在▱ABCD中求面积"))
        raw_correct = "【题干】如图在▱ABCD中求面积"
        correct = readers.parse_reading(raw_correct, 1)
        correct["raw"] = raw_correct
        self.assertTrue(witness_agrees(correct, "如图在▱ABCD中求面积"))

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


class WitnessOcrNoiseTests(SimpleTestCase):
    """Spellings from real MinerU output that print exactly like the reading."""

    def test_layout_html_tags_on_digital_pdfs_are_not_subscripts(self):
        # 2024 全国甲卷: MinerU wraps whole runs of an off-baseline line.
        mineru = "<sub>13.</sub> <sub>已知函数</sub> $f(x)$ <sub>的最小值为</sub>（ ）"
        self.assertEqual(witness_key(mineru), witness_key("已知函数 $f(x)$ 的最小值为（ ）"))
        self.assertEqual(witness_key("设<sup>p</sup>为优级品率"), witness_key("设 $p$ 为优级品率"))
        # A script glued to its symbol keeps its meaning (see the test above).
        self.assertNotEqual(witness_key("x<sub>2</sub>+1"), witness_key("x2+1"))
        self.assertNotEqual(witness_key("x <sub>2</sub>+1"), witness_key("x2+1"))

    def test_same_printed_mark_in_another_spelling(self):
        pairs = (
            (r"$\bar{z}+z$ 的值为多少", r"$\overline { { z } } + z$ 的值为多少"),
            (r"约束条件 $\begin{cases} x\geq 0 \\ y\leq 1 \end{cases}$ 则",
             r"约束条件 $\left\{ { \begin{array} { l } { x \geq 0 } \\ { y \leq 1 } \end{array} } \right.$ 则"),
            (r"$0.1212212221\ldots$（相邻两个", "0.1212212221...（相邻两个"),
            (r"$\frac{3}{4}$ 与 $x$ 的和为", r"$\scriptscriptstyle \frac{3}{4}$ 与 $~x$ 的和为"),
        )
        for reading, mineru in pairs:
            with self.subTest(mineru=mineru):
                self.assertTrue(witness_agrees({"stem": reading}, mineru))

    def test_symbols_mineru_spells_differently(self):
        self.assertTrue(witness_agrees({"stem": "且与 AE 重合，求 $\\triangle BDE$ 的面积."},
                                       "且与 AE 重合，求 $\\Delta BDE$ 的面积."))
        self.assertTrue(witness_agrees({"stem": "求证：△ABE≌△CDF"}, "求证：$\\triangle ABE \\cong \\triangle CDF$"))
        self.assertTrue(witness_agrees({"stem": "已知 BD=3√2，则 CD 的长为"}, "已知 $BD=3\\sqrt{2}$ ，则 CD 的长为"))
        # The Greek letter itself is not rewritten.
        self.assertFalse(witness_agrees({"stem": "若 △x=2，求 y 的值"}, "若 Δx=2，求 y 的值"))

    def test_mineru_square_supports_a_parallelogram_the_reader_wrote_itself(self):
        read = readers.parse_reading("【题干】在▱ABCD中，连接BD，求证：BD⊥CD", 20)
        read["raw"] = "【题干】在▱ABCD中，连接BD，求证：BD⊥CD"
        self.assertTrue(witness_agrees(read, "20. 在□ABCD中，连接BD，求证：BD⊥CD"))
        guessed = readers.parse_reading("【题干】在□ABCD中，连接BD，求证：BD⊥CD", 20)
        guessed["raw"] = "【题干】在□ABCD中，连接BD，求证：BD⊥CD"
        self.assertFalse(witness_agrees(guessed, "20. 在□ABCD中，连接BD，求证：BD⊥CD"))

    def test_stray_option_letters_in_mineru_text(self):
        reading = {"stem": "则 AH 等于（ ）", "options": {"A": "24/5", "B": "48/5", "C": "4", "D": "5"}}
        self.assertTrue(witness_agrees(reading, "7. 则 AH 等于（） A. 24/5 B. 48/5 B.C.4 D.5"))
        pictures = {"stem": "如图所示的几何体，其从上面看的图是（ ）", "options": {}}
        self.assertTrue(witness_agrees(pictures, "8.如图所示的几何体，其从上面看的图是( C )\nA.\nB.\nC.\nD."))
        # A label that carries text the reader does not have still disagrees.
        self.assertFalse(witness_agrees(reading, "7. 则 AH 等于（） A. 24/5 B. 48/5 C.4 D.5 E.6"))
        self.assertFalse(witness_agrees({"stem": "则 AH 等于（ ）", "options": {"A": "24/5", "B": "48/5", "D": "5"}},
                                        "7. 则 AH 等于（） A. 24/5 B. 48/5 C.4 D.5"))

    def test_punctuation_mineru_dropped_is_tolerated_only_where_it_cannot_change_maths(self):
        tolerated = (
            ("且4S_n=3a_n+4.(1)求通项；(2)求前n项和T_n.", "且4S_n=3a_n+4(1)求通项；(2)求前n项和T_n"),
            ("若AB=√5，则阴影部分的面积为", "若AB=√5则阴影部分的面积为"),
            ("各项系数的最大值是____.", "各项系数的最大值是"),
        )
        for reading, mineru in tolerated:
            with self.subTest(mineru=mineru):
                self.assertTrue(witness_agrees({"stem": reading}, mineru))
        options = {"stem": "则 i(z̄+z)=（ ）", "options": {"A": "10i", "B": "2i"}}
        self.assertTrue(witness_agrees(options, "1 则 i(z̄+z)=（ ） A 10i B. 2i"))
        not_tolerated = (
            # The reader left out a comma MinerU saw: read the card again.
            ("连接AN，CM得到四边形ANCM", "连接AN，CM，得到四边形ANCM"),
            # A comma between two symbols, or a mark swapped for another.
            ("若实数x,y满足约束条件", "若实数xy满足约束条件"),
            ("EF∥AD，AD=4，AB=2", "EF∥ADAD=4，AB=2"),
            ("噪声影响越大，若已知卡车", "噪声影响越大.若已知卡车"),
            ("长依次为5.6.7，求面积", "长依次为567，求面积"),
        )
        for reading, mineru in not_tolerated:
            with self.subTest(mineru=mineru):
                self.assertFalse(witness_agrees({"stem": reading}, mineru))


class StemCleanupTests(SimpleTestCase):
    def test_number_and_score_markers_are_removed_only_when_they_are_this_question(self):
        self.assertEqual(clean_stem("14 如图，在正五边形内部", 14), "如图，在正五边形内部")
        self.assertEqual(clean_stem("3 个数中，最大的是", 14), "3 个数中，最大的是")
        self.assertEqual(clean_stem("（本题满分14分）\n（1）比较大小", 17), "（1）比较大小")
        self.assertEqual(clean_stem("17.（本题满分6分）已知", 17), "已知")
        self.assertEqual(clean_stem("．如图，所有三角形", 2), "如图，所有三角形")
        self.assertEqual(clean_stem("2..如图，所有三角形", 2), "如图，所有三角形")
        self.assertEqual(clean_stem("（ ）如图，在▱ABCD中", 23), "如图，在▱ABCD中")

    def test_clipped_numbers_and_small_question_score_markers(self):
        # 装订边裁掉了“1”：读者看到的是“9.”。
        self.assertEqual(clean_stem("9.（本题满分 6 分）\n\n已知点 A", 19), "已知点 A")
        self.assertEqual(clean_stem("（本小题满分 6 分）\n\n如图，一架梯子", 20), "如图，一架梯子")
        self.assertEqual(clean_stem("9.8 米每秒的速度", 19), "9.8 米每秒的速度")

    def test_text_cut_in_from_above_or_below_the_question(self):
        self.assertEqual(clean_stem("合题目要求的.\n1. 经过点 (3,1)，斜率为", 1), "经过点 (3,1)，斜率为")
        self.assertEqual(clean_stem("（1）求证\n2. 若 x=1", 2), "（1）求证\n2. 若 x=1")
        self.assertEqual(clean_stem("求 a 的值．\n\n[选修 4-5：不等式选讲]", 22), "求 a 的值．")
        self.assertEqual(
            clean_stem("恒成立，求 a 的取值范围．\n\n（二）选考题：共 10 分，请考生在第 22、23 题中任选一题作答．"
                       "\n\n[选修 4-4：坐标系与参数方程]", 21),
            "恒成立，求 a 的取值范围．",
        )


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
        question = self.card("1. 已知函数 $f ( x ) = x ^ { 2 }$ 与 $g ( x )$ ，求 $f ( 2 )$ 的值。")
        chat = ScriptedChat({
            ("a", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
            ("b", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "agree"))
        self.assertEqual(sorted(call[0] for call in chat.calls), ["a", "b"])

    @staticmethod
    def pick(side: str):
        """A scripted spot check that always names ``side`` ("reading" or "mineru")."""
        import re as _re

        def answer(prompt: str) -> str:
            lines = []
            for index, first, second in _re.findall(r"第(\d+)处：.*?甲：(\S+)　乙：(\S+)", prompt):
                # The reading's spelling is the one scripted below as ``x^2`` / ``美``.
                reading_is_first = first in {"2", "美"}
                wants_first = (side == "reading") == reading_is_first
                lines.append(f"{index}={'甲' if wants_first else '乙'}")
            return "\n".join(lines)
        return answer

    def test_two_agreeing_reads_against_mineru_get_a_neutral_spot_check(self):
        # 凤城高一第 19 题：两次都把 1/x³ 读成 1/x²，MinerU 读对了。
        question = self.card("1. 已知函数 $f ( x ) = x ^ { 3 }$ ，求 $f ( 2 )$ 的值。")
        chat = ScriptedChat({
            ("a", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
            ("b", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
            ("spotcheck", 1): self.pick("mineru"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual([call[0] for call in chat.calls].count("spotcheck"), 1)
        self.assertIn("x^2", question.stem)          # the text is not rewritten by the check
        self.assertEqual(question.state, Question.State.YELLOW)
        flag = next(flag for flag in question.flags if flag.startswith(pipeline.OBJECTION_FLAG_PREFIX))
        self.assertIn("MinerU：3", flag)
        self.assertEqual(question.read_c["answers"], ["mineru"])

    def test_a_confirmed_reading_stays_green(self):
        # MinerU misread the printed 垂美四边形; the spot check sides with the reading.
        question = self.card("1. 对角线互相垂直的四边形叫做垂夹四边形，求证其面积。")
        reading = tagged("对角线互相垂直的四边形叫做垂美四边形，求证其面积。")
        chat = ScriptedChat({("a", 1): reading, ("b", 1): reading, ("spotcheck", 1): self.pick("reading")})
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual((question.state, question.text_source), (Question.State.GREEN, "agree"))
        self.assertIn("垂美", question.stem)
        self.assertEqual(question.read_c["objections"][0]["reading"], "美")

    def test_an_unclear_spot_check_leaves_the_card_for_a_person(self):
        question = self.card("1. 已知函数 $f ( x ) = x ^ { 3 }$ ，求 $f ( 2 )$ 的值。")
        chat = ScriptedChat({
            ("a", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
            ("b", 1): tagged("已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"),
            ("spotcheck", 1): "1=不确定",
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual(question.state, Question.State.YELLOW)
        self.assertEqual(question.read_c["answers"], [None])


    def figure_card(self):
        question = self.card("1. 如图，在菱形 $ABCD$ 中，$AC=8$，求 $BD$ 的长。")
        question.figure_candidates = [
            {"label": "1", "seq": 7, "page_idx": 0, "bbox": [300.0, 120.0, 380.0, 180.0]},
            {"label": "2", "seq": 8, "page_idx": 0, "bbox": [400.0, 120.0, 470.0, 180.0]},
        ]
        question.save()
        return question

    def test_boxes_a_reader_did_not_judge_are_asked_about_once(self):
        question = self.figure_card()
        chat = ScriptedChat({
            ("a", 1): tagged("如图，在菱形 $ABCD$ 中，$AC=8$，求 $BD$ 的长。", figures="1=题干"),
            ("classify", 1): "2=无关",
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual([call[0] for call in chat.calls].count("classify"), 1)
        self.assertEqual((question.state, len(question.figures)), (Question.State.GREEN, 1), question.flags)
        self.assertEqual(question.read_a["figures_followup"], {"2": "none"})

    def test_a_box_still_unjudged_keeps_the_card_for_a_person(self):
        question = self.figure_card()
        chat = ScriptedChat({
            ("a", 1): tagged("如图，在菱形 $ABCD$ 中，$AC=8$，求 $BD$ 的长。", figures="1=题干"),
            ("classify", 1): "看不清",
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])
        question.refresh_from_db()
        self.assertEqual(question.state, Question.State.YELLOW)

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


class WitnessObjectionTests(SimpleTestCase):
    def test_only_clean_substitutions_count(self):
        from .textnorm import witness_objections
        reading = {"stem": "当 $x>0$ 时，求 $y=2x+\\dfrac{1}{x^3}$ 最小值"}
        spots = witness_objections(reading, "当 $x > 0$ 时，求 $y = 2x + \\frac{1}{x^{2}}$ 最小值")
        self.assertEqual([(s["reading"], s["mineru"]) for s in spots], [("3", "2")])
        # MinerU's look-alike confusions and handwriting insertions are not objections.
        self.assertEqual(witness_objections({"stem": "若n与α和β所成的角相等"}, "若n与a和β所成的角相等"), [])
        self.assertEqual(witness_objections({"stem": "求证：△ABN≌△MAD，并说明"}, "求证：VABN≌VMAD，并说明"), [])
        self.assertEqual(witness_objections({"stem": "则 OE 长为（ ）的值"}, "则 OE 长为（ B ）13 的值"), [])


class SpotCheckParsingTests(SimpleTestCase):
    def test_answers_map_back_to_their_engines(self):
        spots = [{"reading": "3", "mineru": "2", "before": "1/x", "after": "最小值"},
                 {"reading": "要", "mineru": "用", "before": "至少需", "after": "_个小"}]
        prompt, order = readers.spot_check_prompt(spots)
        self.assertIn("第1处", prompt)
        self.assertNotIn("原誊录", prompt)
        raw = "<think>看一下</think>1=甲\n第2处：乙"
        answers = readers.parse_spot_answers(raw, order)
        self.assertEqual(answers[0], order[0]["甲"])
        self.assertEqual(answers[1], order[1]["乙"])
        self.assertEqual(readers.parse_spot_answers("看不清", order), [None, None])


class ReadingFormTests(SimpleTestCase):
    """Two readings that print the same must not need an arbiter."""

    def test_options_written_inside_the_stem_are_split_out(self):
        raw = ("【题型】单选题\n【题干】\n已知点 A(2,1,-1)，B(2,t,0)，则 |AB|=（C）"
               "A. √23 B. √5 C. √26 D. √11\n【A】\n【B】\n【C】\n【D】\n【配图】无")
        reading = readers.parse_reading(raw, 7)
        self.assertEqual(reading["stem"], "已知点 A(2,1,-1)，B(2,t,0)，则 |AB|=（　）")
        self.assertEqual(reading["options"], {"A": "√23", "B": "√5", "C": "√26", "D": "√11"})
        # Not a choice question, or not all four labels: untouched.
        self.assertEqual(readers.split_inline_options("点A. B两点间的距离")[1], {})
        free = readers.parse_reading("【题型】解答题\n【题干】\n说明 A. 的含义与 B. 的区别 C. 与 D. 呢", 3)
        self.assertEqual(free["options"], {})

    def test_square_root_sign_and_command_are_the_same_reading(self):
        from .textnorm import same_reading
        self.assertTrue(same_reading({"stem": "求 BD=3√2 时 CD 的长"}, {"stem": "求 $BD=3\\sqrt{2}$ 时 $CD$ 的长"}))
        self.assertFalse(same_reading({"stem": "求 BD=3√2 时"}, {"stem": "求 $BD=3\\sqrt{3}$ 时"}))

