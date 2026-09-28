"""A shared row of figures is handed to the consecutive questions it serves."""

from __future__ import annotations

from django.test import TestCase

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
