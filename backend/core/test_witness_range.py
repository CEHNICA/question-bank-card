"""Where the cross-engine witness can and cannot fire.

Measured 2026-09-28 on 113 cards from five real exam papers.  The shortcut
settled 38 of them, but the split is not random and it is not about scan
quality: a digital PDF scored 9% while a photographed scan scored 72%.

Comparing *within* each paper, so the paper itself is held constant:

    paper                     settled   LaTeX cmds   stem chars
    2024 高考甲卷（理）         2/23      2.50 / 5.43   110 / 136
    初三周清菱形               12/20      1.08 / 2.12    93 / 161
    202510 胜利初二             5/26      1.60 / 1.86   114 / 139
    202510 口镇高中             1/19      3.00 / 3.00    76 / 104
    202510 汶源初四            18/25      1.44 / 2.57   112 / 121

Settled cards are the short ones in 5 of 5 papers and the formula-light ones
in 4 of 5 (口镇 had a single settled card, so its tie is not evidence).  The
two measures cannot be separated at this sample size — more formulas means a
longer stem — and the honest summary is that the shortcut holds on simple
questions and gives up on elaborate ones.

That is the expected consequence of the design rather than a defect:
``witness_agrees`` demands equality after normalisation, so every additional
LaTeX token is another chance for the two engines to spell the same thing
differently.  Widening the symbol table was measured on this corpus and
settled 0 additional cards, because a card fails on several tokens at once.

These tests record the two ends of that range with real inputs, so a future
change to the normaliser has to show which end it moved.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from . import textnorm
from .textnorm import canon, witness_agrees, witness_key


# Settled: a short stem, two fractions, no subscripted variables.  Both sides
# are what the reader and MinerU actually produced for 汶源初四 #2.
SETTLED_READING = {
    "stem": r"在 Rt△ABC 中，∠C=90°，AB=5，BC=3，则 tanA 的值是（　　）",
    "options": {"A": r"$\dfrac{3}{4}$", "B": r"$\frac{4}{3}$", "C": r"$\frac{3}{5}$", "D": r"$\frac{4}{5}$"},
}
SETTLED_WITNESS = (
    r"在 Rt△ABC 中，∠C=90°，AB=5，BC=3，则 tanA 的值是（）"
    r"A. $\frac{3}{4}$ B. $\frac{4}{3}$ C. $\frac{3}{5}$ D. $\frac{4}{5}$"
)

# Not settled: 高考甲卷 #4.  The same mathematics, but every clause carries a
# subscripted variable and a second engine has to repeat all of them exactly.
UNSETTLED_READING = {
    "stem": r"等差数列$\{a_n\}$的前$n$项和为$S_n$，若$S_5=S_{10}$，$a_5=1$，则$a_1=$（   ）",
    "options": {},
}


class WitnessRangeTests(SimpleTestCase):
    def test_the_short_card_was_settled_in_the_real_run(self):
        self.assertTrue(witness_agrees(SETTLED_READING, SETTLED_WITNESS))

    def test_fraction_and_dfrac_are_the_same_token(self):
        # Why the short card settles: the two engines disagree on spelling
        # only, and the normaliser already folds dfrac into frac.
        self.assertEqual(canon(r"$\dfrac{3}{4}$"), canon(r"$\frac{3}{4}$"))

    def test_one_changed_digit_keeps_a_card_out_of_the_shortcut(self):
        broken = dict(SETTLED_READING, options=dict(SETTLED_READING["options"], C=r"$\frac{3}{6}$"))
        self.assertFalse(witness_agrees(broken, SETTLED_WITNESS))

    def test_the_dense_card_did_not_settle(self):
        # No witness copy is stored, so reconstruct the shape: a second engine
        # reproducing this stem would have to match five subscripts.  A single
        # differing subscript is enough to hold the card back.
        for wrong in (r"$S_{10}$", r"$a_{5}$", r"$\{a_{n}\}$"):
            near = UNSETTLED_READING["stem"].replace(r"$S_{10}$", r"$S_{9}$")
            near = near.replace(r"$a_5$", r"$a_3$").replace(r"$\{a_n\}$", r"$\{a_m\}$")
            self.assertNotEqual(canon(near), canon(UNSETTLED_READING["stem"]), wrong)

    def test_accents_are_the_same_token_only_within_one_family(self):
        # The one addition to the symbol table that was considered, and the
        # reason it is written as distinct sentinels rather than "".
        self.assertNotEqual(canon(r"$\bar{x}$"), canon(r"$\hat{x}$"))
        self.assertNotEqual(canon(r"$\tilde{A}$"), canon(r"$\hat{A}$"))
        self.assertNotEqual(canon(r"$\vec{a}$"), canon(r"$\hat{a}$"))

    def test_a_short_witness_is_refused_before_any_comparison(self):
        self.assertFalse(witness_agrees({"stem": "已知集合"}, "x"))

    def test_witness_key_of_a_settled_card_is_stable(self):
        self.assertEqual(witness_key(SETTLED_WITNESS), witness_key(SETTLED_WITNESS))
        self.assertGreaterEqual(len(witness_key(SETTLED_WITNESS)), textnorm.WITNESS_MIN_LENGTH)
