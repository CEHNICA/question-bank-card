r"""卡上挂着一张落在本题范围外面的图，而这张卡看上去很正常、旗标也是空的。

卡的范围就是这道题在纸上的位置，切题的候选图正是从范围里圈的——全库 64 张
``auto`` 图里只有 1 张在范围外。所以落在外面只有两种可能：范围切少了，或者图挂错了。

真实案例（八下 p136 第 4 题 id=1083，逐层核过）：
  卡的范围 ``[83, 505, 899, 535]``，只有一行字那么高；那张图在 ``y=368–474``，
  完全在外面。图的 ``source`` 是 ``other``——别的题的识读说「这张图属于第 4 题」，
  ``assign_foreign_figures`` 信了就自动挂上、不提示（同行排图时它通常是对的）。
  可一旦范围没罩住，重新识读时这个指认未必还在。

⚠️ 接线点不在 ``read_card``：它挂的图只能来自候选池（``source=auto``），而候选
又是从范围里圈的，那条路径几乎不可能有越界图（真实数据 64 张 auto 里只有 1 张）。
缺口在识读之后的 ``assign_foreign_figures``。

全库 959 张卡里 50 张挂图的卡有越界图，其中 **19 张是绿卡、零图旗标**，
它们的图全部来自 ``other`` 这条通道。
"""
from django.test import SimpleTestCase, TestCase

from . import figure_policy, pipeline
from .models import Paper, Question, QuestionGroup

# 纸上这一行的形状就是「如图」，图提示词必须认得出。
REGIONS = [{"page_idx": 0, "bbox": [83, 505, 899, 535]}]
FIGURE = {"slot": "stem", "page_idx": 0, "bbox": [543, 368, 727, 474],
          "source": "other"}


class FiguresOutsideOwnRangeTests(SimpleTestCase):
    def test_a_figure_printed_outside_the_range_is_reported(self):
        self.assertEqual(len(figure_policy.figures_outside_own_range(
            REGIONS, [FIGURE])), 1)

    def test_a_figure_inside_the_range_is_not_reported(self):
        inside = {"slot": "stem", "page_idx": 0, "bbox": [500, 506, 700, 534],
                  "source": "auto"}
        self.assertEqual(figure_policy.figures_outside_own_range(REGIONS, [inside]), [])

    def test_the_same_box_on_another_page_is_outside(self):
        # 范围只罩住第 0 页。同一坐标印在第 1 页上的图不属于本题。
        self.assertEqual(len(figure_policy.figures_outside_own_range(
            REGIONS, [{**FIGURE, "page_idx": 1}])), 1)

    def test_a_page_that_is_not_covered_at_all_counts_as_outside(self):
        # 这张图的坐标正好落在范围的框里，只有页码不同。不看页码的话它会被
        # 判成「在范围内」放过去 —— 这条专门盯住页码这一项。
        self.assertEqual(len(figure_policy.figures_outside_own_range(
            REGIONS, [{"slot": "stem", "page_idx": 3, "bbox": [500, 506, 700, 534],
                       "source": "auto"}])), 1)

    def test_a_malformed_figure_is_left_to_the_other_guards(self):
        # 没有 bbox、或 bbox 不成形，不是「越界」，别在这里下结论。
        for figure in ({"slot": "stem"}, {"slot": "stem", "bbox": [1, 2]},
                       {"slot": "stem", "bbox": None}, "不是字典"):
            self.assertEqual(figure_policy.figures_outside_own_range(
                REGIONS, [figure]), [], figure)

    def test_no_regions_means_every_figure_is_outside(self):
        # 范围缺失时无法判断归属，仍然报出来让人看，而不是静默放过。
        self.assertEqual(len(figure_policy.figures_outside_own_range([], [FIGURE])), 1)


class ForeignFigureOutsideRangeTests(TestCase):
    """接线：别的题的识读把图指过来时，越界的那张要挂提示。"""

    def setUp(self):
        self.paper = Paper.objects.create(
            filename="教材.pdf", kind="pdf", material_type=Paper.MaterialType.BOOK,
            sha256="c" * 64, source_path="missing-source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400}],
            status=Paper.Status.READY,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第一章", sequence=1,
            page_start=1, page_end=1, metadata={"pages": [0]},
        )

    def make_card(self, *, stem, regions):
        return Question.objects.create(
            paper=self.paper, group=self.group, number=4, stem=stem,
            regions=regions, regions_auto=regions,
            source_kind=Question.SourceKind.EXERCISE, source_anchor_seq=128,
            processing_mode="auto", body_mode="text",
            state=Question.State.GREEN, flags=[],
        )

    def hand_over(self, figure_bbox):
        pipeline.assign_foreign_figures(self.paper, [
            {"number": 4, "page_idx": 0, "bbox": list(figure_bbox)},
        ])

    def test_a_handed_over_figure_outside_the_range_is_not_green(self):
        card = self.make_card(
            stem="如图，△ABC 的高 BD，CE 相交于点 G，找出其中的三对相似三角形。",
            regions=list(REGIONS))

        self.hand_over([543, 368, 727, 474])
        card.refresh_from_db()

        self.assertIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, card.flags)
        self.assertEqual(card.state, Question.State.YELLOW)

    def test_a_handed_over_figure_inside_the_range_stays_green(self):
        card = self.make_card(
            stem="如图，△ABC 的高 BD，CE 相交于点 G，找出其中的三对相似三角形。",
            regions=[{"page_idx": 0, "bbox": [83, 340, 899, 500]}])

        self.hand_over([543, 368, 727, 474])
        card.refresh_from_db()

        self.assertNotIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, card.flags)
        self.assertEqual(card.state, Question.State.GREEN)

    def test_the_foreign_figure_warning_still_takes_precedence(self):
        # 题干压根没说图时，本来就该挂「别的题的识读认为这张图属于本题」；
        # 越界只是其中一种原因，不能把它顶掉、也不能两条都挂。
        card = self.make_card(stem="下列说法正确的是（　）", regions=list(REGIONS))

        self.hand_over([543, 368, 727, 474])
        card.refresh_from_db()

        self.assertIn(figure_policy.FLAG_FOREIGN_FIGURE, card.flags)
        self.assertNotIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, card.flags)


class GeometricFigureFlagReconcileTests(TestCase):
    """兜底：几何挂图挂在范围外面，卡上却一句「请核对」都没有。

    真实案例（汶源初四月考第 10 题 id=665，逐层核过）：卡 ``green``、旗标为空，
    题干写着「如图」、``cue_matches=['如图']``，挂的那张图 ``source=other`` 而且
    **印在上一页**（图 page 0，卡范围 page 1）。老师看不出这张图不是识读认下的那道。

    上一组测试守住的是 ``assign_foreign_figures`` 那一刻；真库这 19 张是别的次序
    留下的——旗标挂上之后又被 ``_flags_after_figure_review`` 按 review 状态重选掉，
    再没有一处补回来。这里补的是这个口子。
    """

    def setUp(self):
        self.paper = Paper.objects.create(
            filename="月考.pdf", kind="pdf", material_type=Paper.MaterialType.EXAM,
            sha256="d" * 64, source_path="missing-source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400},
                   {"page_idx": 1, "width": 1000, "height": 1400}],
            status=Paper.Status.READY,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第一章", sequence=1,
            page_start=1, page_end=2, metadata={"pages": [0, 1]},
        )
        self.counter = 0

    def card(self, **values):
        self.counter += 1
        base = dict(
            paper=self.paper, group=self.group, number=self.counter,
            stem="如图，△ABC 的三个顶点分别为 A（1，2），求函数 $y=\\dfrac{k}{x}$ 的图象。",
            regions=[{"page_idx": 1, "bbox": [83, 505, 899, 535]}],
            processing_mode="auto", body_mode="text",
            state=Question.State.GREEN, flags=[], figures=[], figure_review={},
        )
        base.update(values)
        return Question.objects.create(**base)

    OUTSIDE = {"slot": "stem", "page_idx": 0, "bbox": [747, 628, 853, 756], "source": "other"}
    INSIDE = {"slot": "stem", "page_idx": 1, "bbox": [500, 506, 700, 534], "source": "other"}

    def test_a_foreign_figure_from_another_page_is_asked_about(self):
        card = self.card(figures=[self.OUTSIDE])

        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 1)

        card.refresh_from_db()
        self.assertIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, card.flags)
        self.assertEqual(card.state, Question.State.YELLOW)

    def test_a_figure_the_reader_picked_up_itself_is_left_alone(self):
        # 识读自己认下的图本来就落在范围里，不归这条兜底管。
        card = self.card(figures=[{**self.OUTSIDE, "source": "auto"}])

        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 0)

        card.refresh_from_db()
        self.assertEqual(card.flags, [])
        self.assertEqual(card.state, Question.State.GREEN)

    def test_a_geometric_figure_inside_the_range_is_left_alone(self):
        # 同行的图印在本页范围内是常态（教材里第 10、11 题的图并排），不提示。
        card = self.card(figures=[self.INSIDE])

        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 0)

        card.refresh_from_db()
        self.assertEqual(card.flags, [])

    def test_a_card_already_asked_about_its_figures_is_not_asked_twice(self):
        card = self.card(figures=[self.OUTSIDE],
                         flags=[figure_policy.FLAG_FOREIGN_FIGURE])

        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 0)

        card.refresh_from_db()
        self.assertEqual(card.flags, [figure_policy.FLAG_FOREIGN_FIGURE])

    def test_a_person_boxed_figure_is_never_second_guessed(self):
        # 人工框过的配图是人的决定，机器不该重新翻出来质疑。
        card = self.card(figures=[{**self.OUTSIDE, "source": "manual"}])

        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 0)

        card.refresh_from_db()
        self.assertEqual(card.flags, [])

    def test_only_the_flag_is_added_and_the_picture_is_left_where_it_is(self):
        card = self.card(figures=[self.OUTSIDE])

        pipeline.reconcile_geometric_figure_flags(self.paper)

        card.refresh_from_db()
        self.assertEqual(card.figures, [self.OUTSIDE])
