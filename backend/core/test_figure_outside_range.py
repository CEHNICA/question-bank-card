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
from unittest import mock

from django.test import SimpleTestCase, TestCase

from . import figure_policy, pipeline, segment
from .models import Block, Paper, Question, QuestionGroup

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
        base = {
            "paper": self.paper, "group": self.group, "number": self.counter,
            "stem": "如图，△ABC 的三个顶点分别为 A（1，2），求函数 $y=\\dfrac{k}{x}$ 的图象。",
            "regions": [{"page_idx": 1, "bbox": [83, 505, 899, 535]}],
            "processing_mode": "auto", "body_mode": "text",
            "state": Question.State.GREEN, "flags": [], "figures": [], "figure_review": {},
        }
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


class ResegmentAlsoAsksAboutGeometricFiguresTests(TestCase):
    """接线：老师点「重新切题」之后，这句提示要跟着出现。

    这条兜底原先只挂在整份重读末尾（`read_questions`）。可老师手上最短的那条
    路是「重新切题」——几分钟、零模型调用；挂在那条路的另一头，就得先跑一次
    287 张的全量重读（约 48 分钟）才补得上一句提示。

    真实数据：真库 959 张里 22 张的几何挂图落在本题范围外（九上 12、八下 7、
    汶源初四 3），18 张显示为绿；九上第 8 题 id=1443 更极端——它自己一张图都
    没有，卡上五张全是邻题借来的，而纸上第 8 题的图明明白白印着「第 8 题」。

    ⚠️ 范围一变，切题自己就会把非人工图摘掉（``pipeline.py:3003``），那一刻卡上
    是零张图、而「题干说有图但还没有配图」要等识读回来才补。这里没有为此加守卫：
    隔离库跑完整重切，「题干提图 + 卡上零图 + 无人提示」实测 **0 张**（仅有的两张
    都已挂着提示）。真实数据里量不到的口子不写代码。
    """

    def setUp(self):
        self.paper = Paper.objects.create(
            filename="教材.pdf", kind="pdf", material_type=Paper.MaterialType.BOOK,
            sha256="e" * 64, source_path="missing-source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400}],
            status=Paper.Status.SEGMENTING,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第一章", kind=QuestionGroup.Kind.CHAPTER,
            sequence=0, page_start=1, page_end=1, metadata={"pages": [0]},
        )
        self.rows = [
            {"seq": 10, "type": "text", "page_idx": 0, "bbox": [80, 500, 920, 545],
             "text": "例1 如图，求函数的最大值。"},
            {"seq": 20, "type": "text", "page_idx": 0, "bbox": [80, 800, 920, 845],
             "text": "例2 求函数的最小值。"},
        ]
        Block.objects.bulk_create([Block(paper=self.paper, **row) for row in self.rows])
        self.items = [{**item, "group": self.group}
                      for item in segment.segment_book(self.paper.pages, self.rows)["questions"]]
        # 版式照真实教材：图印在题目文字的**上方**（y 380–470），范围只罩住
        # y 496 往下那一行。八下第 4 题 id=1083、汶源初四第 9 题 id=664 都是这样。
        self.foreign = {"slot": "stem", "page_idx": 0, "bbox": [500, 380, 700, 470],
                        "source": "other"}

    def make_card(self, regions):
        return Question.objects.create(
            paper=self.paper, group=self.group, number=1,
            stem="例1 如图，求函数的最大值。",
            regions=regions, regions_auto=regions,
            source_kind=Question.SourceKind.EXAMPLE, source_anchor_seq=10,
            processing_mode="auto", body_mode="text", state=Question.State.GREEN,
            flags=[], figures=[dict(self.foreign)], figure_review={},
        )

    def resegment(self):
        with mock.patch.object(
                pipeline, "_prospective_book_groups",
                return_value=([self.group], dict(self.paper.structure or {}))), \
                mock.patch.object(pipeline, "_ensure_question_groups",
                                  return_value=[self.group]), \
                mock.patch.object(
                    pipeline, "_collect_segmentation_items",
                    return_value=(self.rows, self.items, [], [])):
            pipeline.segment_paper(self.paper)

    def test_an_unchanged_card_keeps_its_picture_and_gains_the_warning(self):
        card = self.make_card(list(self.items[0]["regions"]))

        self.resegment()

        card.refresh_from_db()
        self.assertEqual(card.figures, [self.foreign])
        self.assertIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, card.flags)
        self.assertEqual(card.state, Question.State.YELLOW)

    def test_resegment_does_not_ask_twice(self):
        card = self.make_card(list(self.items[0]["regions"]))

        self.resegment()
        self.resegment()

        card.refresh_from_db()
        self.assertEqual(card.flags.count(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE), 1)
