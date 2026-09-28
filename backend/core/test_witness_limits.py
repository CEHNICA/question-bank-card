"""Where the cross-engine witness does *not* fire, measured on real papers.

Two independent runs on 2026-09-28, both with a single MiniMax key:

* Scanned exam papers — the shortcut is doing real work: 38 of 113 cards
  (34%) were settled by MinerU's text instead of a second vision call, up to
  72% on one 月考 paper.
* A phone photo of a heavily marked 集合/不等式 worksheet — 0 of 6 cards.
  MinerU's OCR renders ``\\{x\\mid ...\\}`` as ``|x| ... |`` there, and the
  student's working bleeds into the prose blocks, so the two texts never
  align and every card falls back to a second reader.

These tests pin that *fail-safe* direction — the photo case defers instead of
guessing — rather than a best case.  They also record why the obvious
one-line fix is wrong.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from .textnorm import witness_agrees, witness_choice, witness_key


def reading(stem: str, **extra) -> dict:
    return {"stem": stem, **extra}


class WitnessBraceArtifactTests(SimpleTestCase):
    """MinerU renders ``\\{x\\mid ...\\}`` as ``|x| ... |`` on photographed papers.

    Scanned exam papers do not hit this as hard, so the failure mode is scoped
    to the photographed-and-marked material above.  Normalising ``|x|`` away is
    *not* a safe fix: the same glyphs carry 绝对值 and conditional probability,
    which these tests guard.
    """

    # Captured verbatim from MinerU on 2026-09-28 against a phone photo of a
    # marked 每周两练 worksheet (page 1, question 1).
    MINERU = r"|x| - 3\leqslant x\leqslant 10|"
    VISION = r"\{x\mid -3\leqslant x\leqslant 10\}"

    def test_correct_reading_is_not_green_when_mineru_mangles_the_delimiters(self):
        # The vision text is right and MinerU holds the right numbers too, but
        # the delimiters keep the keys apart, so the shortcut must not fire.
        self.assertFalse(witness_agrees(reading(self.VISION), self.MINERU))

    def test_witness_choice_defers_instead_of_guessing(self):
        first = reading(r"\{x\mid \dfrac{1}{2}\leq x\leq 10\}")
        second = reading(r"\{x\mid -\frac{1}{3}\leq x\leq 3\}")
        self.assertIsNone(witness_choice(first, second, self.MINERU))

    def test_delimiter_normalisation_would_destroy_absolute_value(self):
        # Documents why the obvious one-line fix is wrong.  A regex that
        # rewrites |x| to x| would collapse the three expressions below into
        # one key, so every vertical bar has to survive exactly as printed.
        absolute_value = witness_key("A|x|=3")
        function_value = witness_key("f(x)=|x|")
        conditional = witness_key("P(A|B)")
        self.assertNotEqual(absolute_value, function_value)
        self.assertNotEqual(absolute_value, conditional)
        self.assertNotEqual(function_value, conditional)
        self.assertEqual(absolute_value.count("|"), 2)
        self.assertEqual(function_value.count("|"), 2)

    def test_vertical_bars_survive_untouched_in_set_builder_notation(self):
        # \mid is a real delimiter in this project's canon, never collapsed.
        self.assertIn("|", witness_key(r"\{x\mid a<b\}"))


class WitnessSafetyTests(SimpleTestCase):
    """The shortcut may only speak when the witness supports exactly one side."""

    BASE = "已知集合A={x|2<x<4}，求实数m的取值范围。"

    def test_agreeing_readings_are_left_to_the_normal_path(self):
        self.assertIsNone(witness_choice(reading(self.BASE), reading(self.BASE), self.BASE))

    def test_whitespace_only_difference_never_invokes_the_witness(self):
        self.assertIsNone(witness_choice(reading(self.BASE + "。"), reading(self.BASE), self.BASE))

    def test_witness_settles_only_when_it_backs_one_side_completely(self):
        first = reading(self.BASE.replace("m", "n"))
        second = reading(self.BASE)
        self.assertEqual(witness_choice(first, second, self.BASE), "b")

    def test_difference_touching_the_very_start_goes_to_the_arbiter(self):
        # A reading that dropped the opening sentence would otherwise "match"
        # trivially because it contains nothing to contradict.
        first = reading("求实数m的取值范围。")
        second = reading(self.BASE)
        self.assertIsNone(witness_choice(first, second, self.BASE))

    def test_difference_touching_the_very_end_goes_to_the_arbiter(self):
        first = reading(self.BASE + "并说明理由。")
        second = reading(self.BASE)
        self.assertIsNone(witness_choice(first, second, self.BASE))

    def test_one_settled_difference_does_not_rescue_an_undecidable_one(self):
        # The first difference is settled by the witness in favour of the
        # first reading; the second one has no right-hand context, so the
        # whole card must still go to the arbiter.
        first = reading("起点20末尾甲乙丙丁戊己庚")
        second = reading("起点26末尾甲乙丙丁戊己庚附带结论")
        witness = "起点20末尾甲乙丙丁戊己庚 30 附带结论"
        self.assertIsNone(witness_choice(first, second, witness))
        # Remove the undecidable tail and the same witness does settle it.
        self.assertEqual(
            witness_choice(first, reading("起点26末尾甲乙丙丁戊己庚"), witness), "a",
        )

    def test_missing_stem_never_counts_as_support(self):
        self.assertIsNone(witness_choice(reading(""), reading(self.BASE), self.BASE))

    def test_short_witness_is_not_enough_evidence(self):
        self.assertFalse(witness_agrees(reading("已知集合"), "x"))


class WitnessPipelineCostTests(SimpleTestCase):
    """The shortcut saves a vision call only when it actually fires."""

    def test_marked_photo_material_keeps_both_readings(self):
        # Captured run: 6/6 cards on a marked photo worksheet still carried a
        # second vision reading because MinerU's text interleaved the student's
        # working into the prose blocks.
        reading_with_handwriting = r"\{x\mid x^2 - 3x + 2 = 0\}，非空集合 $\{x\mid 2ax^2-3(a^2+1)x+4=0\}$"
        mineru_with_working = r"|x|x^2 $\Rightarrow -3a^2 + 2a + 1$ $-3x + 2 = 0$ , 非空集合"
        self.assertFalse(witness_agrees(reading(reading_with_handwriting), mineru_with_working))

    def test_scanned_paper_without_handwriting_does_settle(self):
        # The other half of the comparison, copied verbatim from the same
        # benchmark run: on scanned exam papers the shortcut fired on 38 of
        # 113 cards.  Guard both directions so a change to witness_key cannot
        # silently turn the photographed case into a false green *or* stop the
        # scanned case from being settled.
        vision = r"在 Rt△ABC 中，∠C=90°，AB=5，BC=3，则 tanA 的值是（　　）"
        options = {"A": r"$\frac{3}{4}$", "B": r"$\frac{4}{3}$", "C": r"$\frac{3}{5}$", "D": r"$\frac{4}{5}$"}
        mineru = (
            r"在 Rt△ABC 中，∠C=90°，AB=5，BC=3，则 tanA 的值是（）"
            r"A. $\frac{3}{4}$ B. $\frac{4}{3}$ C. $\frac{3}{5}$ D. $\frac{4}{5}$"
        )
        self.assertTrue(witness_agrees(reading(vision, options=options), mineru))
        # One wrong digit keeps the card out of the shortcut.
        wrong = dict(options, C=r"$\frac{3}{6}$")
        self.assertFalse(witness_agrees(reading(vision, options=wrong), mineru))
