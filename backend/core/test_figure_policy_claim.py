"""Why a bound figure without a text cue stays a yellow card.

This looked like a false alarm at first.  Over 113 cards from five real exam
papers (2026-09-28) three of them were reported as a conflict even though the
reader had affirmatively answered ``1=题干``:

* 汶源初四 #21 — 「已知一次函数 $y_1=kx+b$ 的**图象**与反比例函数的图象交于
  A、B 两点 … （2）**根据图象**写出 …」
* 胜利初二 #8 — 「在△ABC中，AB=13cm，AC=20 cm，BC边上的高为12cm …」
* 初三周清菱形 #19 — 「在平行四边形 $ABCD$ 中，作线段 $AC$ 的垂直平分线 …」

The first one is tempting: the text does talk about 图象, it just does not use
one of the cue shapes in ``_CHINESE_CUE``.  Widening the cue list was measured
and rejected — a bare 函数图象 rule also matches 「求函数图象的对称轴」 on a
paper that prints no figure, which would silence a correct warning.

The structural point is that the reader's claim cannot decide this.  The two
shapes are identical to this function:

* the reader said ``1=题干`` and a crop is bound, the text has no cue, and
* the reader said ``1=题干`` and a crop is bound, the text has no cue.

Only a human can tell 「求阴影部分的面积。」 (a plausible wrong binding) from
「根据图象写出…」 (a real one).  So the conflict is deliberate: see
``PipelineTests.test_printed_figure_without_text_reference_is_a_review_conflict``.

These tests pin the guard on both sides of the reader's answer, so a future
attempt to trust the reader here has to change them on purpose rather than by
accident.

2026-09-30, changed on purpose: the reader's claim still never clears the
conflict.  What can clear it is a *separate* question asked only about the
attached boxes — “this question's printed figure, another question's figure,
or handwriting?” (``readers.verify_printed_figures``).  Over the 11 such cards
of the 2026-09-30 benchmark (sample labels), 5 were real printed figures
(lingxing #19, pxsbx #11, shengli10 #15, wenyuan9 #21, zuobiao #28) and 6 were
wrong bindings — five students' sketches (fengcheng10 #5 #11, shengli7 #9 #25,
shengli8 #8) and one corner of a neighbouring figure (shengli7 #11).  So the guard was right
about half the time; only the separate answer “本题” turns a card green, and
“别题”, “手写” or no answer keep it yellow as before.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from . import figure_policy


FIGURE = {"slot": "stem", "page_idx": 0, "bbox": [10.0, 20.0, 90.0, 40.0], "source": "auto"}

# The two real stems above.  Neither contains a cue the matcher recognises,
# which is why both reach this rule.
REAL_STEMS = (
    "已知一次函数 $y_1=kx+b$ 的图象与反比例函数 $y_2=-\\dfrac{8}{x}$ 的图象"
    "交于 A、B 两点，且点 A 的横坐标和点 B 的纵坐标都是 $-2$，求："
    "（1）一次函数的解析式；（2）根据图象写出使一次函数的值大于反比例函数的值",
    "在△ABC中，AB=13cm，AC=20 cm，BC边上的高为12cm，则△ABC的面积为( )cm²。",
    "在学习\"特殊平行四边形\"时，在平行四边形 $ABCD$ 中，作线段 $AC$ 的垂直平分线，"
    "分别交 $AD$，$AC$，$BC$ 于点 $M$，$O$，$N$，连接 $AN$，$CM$，得到四边形 $ANCM$．",
)


class BoundFigureWithoutTextCueIsAReviewConflictTests(SimpleTestCase):
    def review(self, stem, *, assignments, figures, candidates):
        return figure_policy.automatic_review(
            stem=stem, options={}, candidate_labels=set(candidates),
            assignments=assignments, figures=figures, reader_missing=False,
        )

    def test_the_real_stems_really_have_no_cue(self):
        # Guards the premise of the finding: the three yellow cards exist
        # because the matcher does not see a cue in any of them.
        for stem in REAL_STEMS:
            with self.subTest(stem=stem[:20]):
                self.assertEqual(figure_policy.cue_matches(stem, {}), [])

    def test_readers_claim_alone_does_not_clear_the_conflict(self):
        for stem in REAL_STEMS:
            with self.subTest(stem=stem[:20]):
                review = self.review(
                    stem, assignments={"1": "stem"}, figures=[FIGURE], candidates=["1"],
                )
                self.assertEqual(review["status"], figure_policy.CONFLICT)
                self.assertIn("bound_figure_without_text_cue", review["signals"])

    def test_a_cue_still_clears_it(self):
        review = self.review(
            "如图，阴影部分的面积为（  ）。", assignments={"1": "stem"},
            figures=[FIGURE], candidates=["1"],
        )
        self.assertEqual(review["status"], figure_policy.OK)

    def test_asking_the_student_to_draw_is_not_a_cue(self):
        # 「请画出下列函数的图象」 must not create one, and must not be
        # silenced by the reader's claim either.
        stem = "请画出下列函数的图象。"
        self.assertTrue(figure_policy.asks_student_to_draw(stem, {}))
        self.assertEqual(figure_policy.cue_matches(stem, {}), [])
        review = self.review(
            stem, assignments={"1": "stem"}, figures=[FIGURE], candidates=["1"],
        )
        self.assertEqual(review["status"], figure_policy.CONFLICT)

    def test_only_a_separate_check_naming_this_questions_figure_clears_it(self):
        for stem in REAL_STEMS:
            with self.subTest(stem=stem[:20]):
                cleared = figure_policy.automatic_review(
                    stem=stem, options={}, candidate_labels={"1"}, assignments={"1": "stem"},
                    figures=[FIGURE], reader_missing=False, printed_labels={"1"},
                )
                self.assertEqual(cleared["status"], figure_policy.OK)
                self.assertIn("printed_figure_confirmed", cleared["signals"])
                kept = figure_policy.automatic_review(
                    stem=stem, options={}, candidate_labels={"1"}, assignments={"1": "stem"},
                    figures=[FIGURE], reader_missing=False, printed_labels=set(),
                )
                self.assertEqual(kept["status"], figure_policy.CONFLICT)
