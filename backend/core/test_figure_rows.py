"""A shared row of figures is handed to the consecutive questions it serves."""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from . import pipeline
from .figure_policy import automatic_review
from .models import Paper, Question


def _row(y0=800, y1=870):
    return [
        {"label": "1", "seq": 10, "page_idx": 0, "bbox": [527, y0, 623, y1]},
        {"label": "2", "seq": 11, "page_idx": 0, "bbox": [652, y0 - 10, 712, y1 + 3]},
        {"label": "3", "seq": 12, "page_idx": 0, "bbox": [736, y0 - 2, 807, y1 - 3]},
    ]


class FigureRowTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="r" * 64,
                                          status=Paper.Status.READY, pages=[{"page_idx": 0, "width": 842, "height": 595}])

    def question(self, number, *, stem, candidates=(), figures=()):
        review = automatic_review(stem=stem, options={}, candidate_labels=set(), assignments={},
                                  figures=list(figures))
        flags = ["题干说有图，但还没有配图，请点“配图”框出"] if not figures else []
        return Question.objects.create(
            paper=self.paper, number=number, question_type="fill_blank", stem=stem,
            regions=[{"page_idx": 0, "bbox": [498, 600 + number, 937, 700 + number]}],
            figure_candidates=list(candidates), figures=list(figures), figure_review=review, flags=flags,
            state=Question.State.YELLOW if flags else Question.State.GREEN,
        )

    def test_row_is_assigned_left_to_right_and_flagged(self):
        row = _row()
        q13 = self.question(13, stem="如图，在菱形 ABCD 中，G，H 分别为 AE，EF 的中点")
        q14 = self.question(14, stem="如图，在正五边形 ABCDE 的内部作正方形 CDFH")
        q15 = self.question(15, stem="如图，四边形 ABCD 是菱形，以 EF 为边作等边△EFG", candidates=row,
                            figures=[{"slot": "stem", "page_idx": 0, "bbox": row[0]["bbox"], "source": "auto"}])
        self.assertEqual(pipeline.distribute_figure_rows(self.paper), 3)
        for question, expected in ((q13, row[0]), (q14, row[1]), (q15, row[2])):
            question.refresh_from_db()
            self.assertEqual(question.figures[0]["bbox"], expected["bbox"])
            self.assertEqual(question.state, Question.State.YELLOW)
            self.assertIn(pipeline.FLAG_ROW_FIGURE, question.flags)
            self.assertFalse(any("还没有配图" in flag for flag in question.flags))

    def test_nothing_changes_unless_every_predecessor_needs_a_figure(self):
        row = _row()
        self.question(13, stem="已知 x=1，求 y")          # no figure cue
        self.question(14, stem="如图，在正五边形 ABCDE 的内部作正方形 CDFH")
        q15 = self.question(15, stem="如图，四边形 ABCD 是菱形", candidates=row,
                            figures=[{"slot": "stem", "page_idx": 0, "bbox": row[2]["bbox"], "source": "auto"}])
        self.assertEqual(pipeline.distribute_figure_rows(self.paper), 0)
        q15.refresh_from_db()
        self.assertEqual(q15.figures[0]["bbox"], row[2]["bbox"])

    def test_row_under_the_first_question_serves_the_next_column(self):
        # 汶源 9 月卷：网格、双曲线、直角三角形印在第 4 题下面，第 5、6 题在右栏。
        # The reader of question 4 kept two of them and guessed “第 3 题” for one.
        row = _row()
        q3 = self.question(3, stem="函数 y=ax²+c 与 y=ac/x 在同一直角坐标系中的图象大致是（ ）",
                           figures=[{"slot": "stem", "page_idx": 0, "bbox": row[1]["bbox"], "source": "other"}])
        q3.flags = [pipeline.FLAG_FOREIGN_FIGURE]
        q3.state = Question.State.YELLOW
        q3.save()
        q4 = self.question(4, stem="如图将△ABC放在每个小正方形的边长为1的网格中", candidates=row,
                           figures=[{"slot": "stem", "page_idx": 0, "bbox": row[0]["bbox"], "source": "auto"},
                                    {"slot": "stem", "page_idx": 0, "bbox": row[2]["bbox"], "source": "auto"}])
        q5 = self.question(5, stem="如图，正比例函数 y=x 与反比例函数 y=1/x 的图象相交于 A、B 两点")
        q6 = self.question(6, stem="已知，如图，在 Rt△ABC 中，∠ACB=90°，CD⊥AB 于点 D")
        self.assertEqual(pipeline.distribute_figure_rows(self.paper), 3)
        for question, expected in ((q4, row[0]), (q5, row[1]), (q6, row[2])):
            question.refresh_from_db()
            self.assertEqual([figure["bbox"] for figure in question.figures], [expected["bbox"]])
            self.assertIn(pipeline.FLAG_ROW_FIGURE, question.flags)
        q3.refresh_from_db()
        self.assertEqual(q3.figures, [])
        self.assertNotIn(pipeline.FLAG_FOREIGN_FIGURE, q3.flags)

    def test_a_borrowed_figure_on_a_card_with_its_own_figures_is_confirmed_by_a_person(self):
        row = _row()
        own = [{"slot": key, "page_idx": 0, "bbox": [100 + 60 * index, 100, 150 + 60 * index, 150],
                "source": "auto"} for index, key in enumerate("ABCD")]
        q3 = self.question(3, stem="下列图象中，大致是函数图象的是（ ）", figures=own)
        q9 = self.question(9, stem="如图，他们在 A 处仰望塔顶，测得仰角为 30°")
        pipeline.assign_foreign_figures(self.paper, [
            {"number": 3, "page_idx": 0, "bbox": row[1]["bbox"]},
            {"number": 9, "page_idx": 0, "bbox": row[2]["bbox"]},
        ])
        q3.refresh_from_db()
        q9.refresh_from_db()
        self.assertIn(pipeline.FLAG_FOREIGN_FIGURE, q3.flags)
        self.assertEqual(q3.state, Question.State.YELLOW)
        # “如图” without a figure: the other card's “第 9 题图” is what it needs.
        self.assertNotIn(pipeline.FLAG_FOREIGN_FIGURE, q9.flags)
        self.assertEqual(q9.state, Question.State.GREEN)

    def test_stacked_figures_are_not_a_row(self):
        stacked = [
            {"label": "1", "seq": 10, "page_idx": 0, "bbox": [527, 600, 623, 650]},
            {"label": "2", "seq": 11, "page_idx": 0, "bbox": [527, 700, 623, 760]},
        ]
        self.question(14, stem="如图，在正五边形 ABCDE 的内部作正方形 CDFH")
        self.question(15, stem="如图，四边形 ABCD 是菱形", candidates=stacked)
        self.assertEqual(pipeline.distribute_figure_rows(self.paper), 0)


class PictureOptionRowTests(TestCase):
    row = [
        {"label": str(index + 1), "page_idx": 0, "bbox": [540 + 70 * index, 226, 594 + 70 * index, 297]}
        for index in range(4)
    ]

    def test_four_pictures_under_a_choice_stem_become_options(self):
        assignments = {"1": "stem", "2": "stem", "3": "stem", "4": "stem"}
        result = pipeline._row_as_choice_options(
            stem="下面四幅图中，不能证明勾股定理的是（ ）", options={}, kind="unknown",
            candidates=list(reversed(self.row)), assignments=assignments,
        )
        self.assertEqual(result, {"1": "A", "2": "B", "3": "C", "4": "D"})

    def test_text_options_or_existing_option_pictures_are_left_alone(self):
        assignments = {"1": "stem", "2": "stem", "3": "stem", "4": "stem"}
        self.assertEqual(pipeline._row_as_choice_options(
            stem="下列说法正确的是（ ）", options={"A": "甲"}, kind="single_choice",
            candidates=self.row, assignments=assignments), assignments)
        mixed = {"1": "A", "2": "B", "3": "stem", "4": "stem"}
        self.assertEqual(pipeline._row_as_choice_options(
            stem="下列图形中（ ）", options={}, kind="single_choice",
            candidates=self.row, assignments=mixed), mixed)
        self.assertEqual(pipeline._row_as_choice_options(
            stem="观察下列图形，求面积。", options={}, kind="free_response",
            candidates=self.row, assignments=assignments), assignments)



class LocatedStartSnapTests(TestCase):
    def test_a_start_located_inside_the_previous_options_moves_to_the_stem_line(self):
        from types import SimpleNamespace
        layout = SimpleNamespace(splits={0: [515.0]})
        blocks = [
            {"seq": 13, "type": "text", "page_idx": 0, "bbox": [86, 639, 116, 655], "text": "A. $S$"},
            {"seq": 16, "type": "text", "page_idx": 0, "bbox": [200, 645, 229, 700], "text": "B. $\\frac{s}{2}$ D. $\\frac{s}{4}$"},
            {"seq": 18, "type": "text", "page_idx": 0, "bbox": [79, 660, 504, 735], "text": "如图，在▱ABCD中，∠ABC、∠BCD的角平分线交于边"},
            {"seq": 22, "type": "text", "page_idx": 0, "bbox": [529, 640, 906, 660], "text": "6. 如图所示，DE为中位线"},
        ]
        self.assertEqual(pipeline._snap_located_start(blocks, layout, 0, 0, 636.0), 660)
        # Already on a stem line, or nothing near: unchanged.
        self.assertEqual(pipeline._snap_located_start(blocks, layout, 0, 0, 658.0), 658.0)
        self.assertEqual(pipeline._snap_located_start(blocks, layout, 0, 0, 300.0), 300.0)

    def test_a_start_located_on_the_previous_questions_first_line_is_rejected(self):
        from . import segment
        blocks = [{"seq": 24, "type": "text", "page_idx": 0, "bbox": [529, 373, 922, 392],
                   "text": "8.图为某拦河坝改造前后河床的横断面示意图"}]
        previous = segment.Start(number=8, page=0, x=529, y=373, seq=24, col=1)
        self.assertTrue(pipeline._inside_previous_opening(blocks, previous, 0, 1, 380.0))
        self.assertFalse(pipeline._inside_previous_opening(blocks, previous, 0, 1, 575.0))
        self.assertFalse(pipeline._inside_previous_opening(blocks, previous, 0, 0, 380.0))  # other column
        self.assertFalse(pipeline._inside_previous_opening(blocks, previous, 1, 1, 380.0))  # next page


class SpillStripCandidateTests(TestCase):
    def test_thin_strip_does_not_claim_the_next_questions_picture(self):
        from . import segment
        picture = {"seq": 18, "page_idx": 0, "bbox": [905.0, 45.0, 947.0, 123.0]}
        own = {"seq": 15, "page_idx": 0, "bbox": [411.0, 680.0, 478.0, 790.0]}
        questions = [
            {"number": 7, "regions": [
                {"page_idx": 0, "bbox": [52.0, 779.0, 490.0, 858.0]},
                {"page_idx": 0, "bbox": [490.0, 41.0, 967.0, 71.0]},   # 30-unit strip
            ], "figure_candidates": [dict(picture), dict(own)]},
            {"number": 8, "regions": [{"page_idx": 0, "bbox": [490.0, 64.0, 967.0, 133.0]}],
             "figure_candidates": [dict(picture)]},
        ]
        segment._drop_spill_candidates(questions)
        self.assertEqual([c["seq"] for c in questions[0]["figure_candidates"]], [15])
        self.assertEqual([c["seq"] for c in questions[1]["figure_candidates"]], [18])

    def test_a_tall_continuation_keeps_its_pictures(self):
        from . import segment
        picture = {"seq": 18, "page_idx": 0, "bbox": [905.0, 45.0, 947.0, 123.0]}
        questions = [
            {"number": 7, "regions": [
                {"page_idx": 0, "bbox": [52.0, 700.0, 490.0, 858.0]},
                {"page_idx": 0, "bbox": [490.0, 41.0, 967.0, 101.0]},  # a real continuation
            ], "figure_candidates": [dict(picture)]},
            {"number": 8, "regions": [{"page_idx": 0, "bbox": [490.0, 94.0, 967.0, 200.0]}],
             "figure_candidates": [dict(picture)]},
        ]
        segment._drop_spill_candidates(questions)
        self.assertEqual([c["seq"] for c in questions[0]["figure_candidates"]], [18])


class FigureCueWordingTests(SimpleTestCase):
    def test_cues_followed_directly_by_the_sentence(self):
        from .figure_policy import has_figure_cue
        for stem in ("如图将△ABC放在每个小正方形的边长为1的网格中", "如图在菱形ABCD中，AC=6",
                     "如表是某周的生产情况（超产为正，减产为负）"):
            with self.subTest(stem=stem):
                self.assertTrue(has_figure_cue(stem))
        for stem in ("例如图书馆里有 120 本书", "比如表示成分数的形式", "如表示为 x 的函数"):
            with self.subTest(stem=stem):
                self.assertFalse(has_figure_cue(stem))
