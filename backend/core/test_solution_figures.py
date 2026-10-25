"""答案版卷子上，解析部分的图被当成题图挂上去——不能让它悄悄变绿。

202510胜利初四数学答案版本第 25 题：原卷题目只印图1、图2（我对着原页看过），卡上挂了
4 张，多出来的两张出自下面的【详解】，而且是绿的、零旗标。学生复习时看到的是一张
根本不属于这道题的图。

已有的配图检查都答不了这件事：它们看的是「题干有没有提示图」「图归属对不对」，
没有一条问过「这张图是不是印在答案之后」。这条判据只看纸面：卷面自己印了【答案】
【解析】【详解】【点睛】，落在这些标记下方的图就不是题图。

只提示、不删图：答案版卷子上解析里的图也许用户想留着当讲解用，删掉要人工确认，
而误删一张真题图的代价比多提示一次大。
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, readers
from .models import Question

# 真实版面：这张卡跨两页。page 0 是【答案】和【详解】连同详解里印的坐标格图；
# page 1 才是题目自己的图1、图2。题图和解析图不在同一页，所以判据必须同时看页和上下。
ANSWER_BLOCK = {"page_idx": 0, "bbox": [89, 49, 245, 61], "text": "【答案】(1) y = -x + 2x + 3"}
DETAIL_BLOCK = {"page_idx": 0, "bbox": [89, 178, 324, 191], "text": "【详解】(1) 解：∵", "type": "text"}
QUESTION_FIGURES = [
    {"slot": "stem", "page_idx": 1, "bbox": [87, 60, 289, 120]},
    {"slot": "stem", "page_idx": 1, "bbox": [297, 60, 497, 120]},
]
SOLUTION_FIGURES = [
    {"slot": "stem", "page_idx": 0, "bbox": [136, 275, 371, 424]},
    {"slot": "stem", "page_idx": 0, "bbox": [137, 581, 373, 753]},
]


class SolutionFigureTests(SimpleTestCase):
    def count(self, blocks, figures):
        return pipeline._figures_below_solution(blocks, figures)

    def test_a_figure_printed_below_the_answer_is_counted(self):
        self.assertEqual(self.count([ANSWER_BLOCK], SOLUTION_FIGURES[:1]), 1)

    def test_the_question_own_figures_are_not_counted(self):
        self.assertEqual(self.count([ANSWER_BLOCK, DETAIL_BLOCK], QUESTION_FIGURES), 0)

    def test_two_solution_figures_are_counted(self):
        self.assertEqual(self.count([ANSWER_BLOCK, DETAIL_BLOCK], SOLUTION_FIGURES), 2)

    def test_the_question_own_figures_survive_beside_the_solution_ones(self):
        figures = [*QUESTION_FIGURES, *SOLUTION_FIGURES]
        self.assertEqual(self.count([ANSWER_BLOCK, DETAIL_BLOCK], figures), 2)

    def test_a_figure_above_the_marker_on_the_same_page_is_not_counted(self):
        above = [{"slot": "stem", "page_idx": 0, "bbox": [136, 10, 371, 40]}]
        self.assertEqual(self.count([ANSWER_BLOCK], above), 0)

    def test_an_ordinary_paper_has_no_marker_at_all(self):
        self.assertEqual(
            self.count([{"page_idx": 0, "bbox": [78, 60, 400, 90], "text": "25.如图1，抛物线"}], SOLUTION_FIGURES), 0
        )

    def test_nothing_is_counted_without_blocks_or_figures(self):
        self.assertEqual(self.count([], SOLUTION_FIGURES), 0)
        self.assertEqual(self.count([ANSWER_BLOCK], []), 0)
        self.assertEqual(self.count(None, None), 0)


class SolutionFigureFlagTests(SimpleTestCase):
    READING = {
        "stem": "如图1，抛物线 $y=ax^2+2x+c$，求抛物线的表达式。",
        "options": {},
        "type": "free_response",
        "content_kind": "exercise",
        "figures": {"1": "stem", "2": "stem", "3": "stem"},
        "missing_figure": False,
        "number_seen": 25,
        "figure_descriptions": [],
        "unclear": False,
    }

    def read(self, witness_blocks):
        snapshot = {
            "id": 1,
            "number": 25,
            "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [67, 0, 870, 970]}, {"page_idx": 1, "bbox": [60, 40, 920, 900]}],
            "candidates": [
                {"label": "1", "page_idx": 1, "bbox": [87, 60, 289, 120]},
                {"label": "2", "page_idx": 1, "bbox": [297, 60, 497, 120]},
                {"label": "3", "page_idx": 0, "bbox": [136, 275, 371, 424]},
            ],
            "question_type": "free_response",
            "section": "三、解答题",
            "stem": "",
            "options": {},
            "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN,
            "source_anchor_seq": 25,
            "segmentation_flags": [],
            "body_mode": "text",
            "start_source": "mineru",
            "witness_blocks": witness_blocks,
        }
        with (
            patch.object(readers, "primary_engine", return_value=object()),
            patch.object(readers, "read_question", return_value=self.READING),
            patch.object(pipeline.imaging, "stack_regions", return_value=(SimpleNamespace(height=900, width=1400), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"),
        ):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_the_card_carrying_a_solution_figure_is_yellow(self):
        result = self.read([ANSWER_BLOCK, DETAIL_BLOCK])

        self.assertIn("印在【解析】之后", "　".join(result["flags"]))
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_an_ordinary_paper_stays_green(self):
        result = self.read([{"page_idx": 0, "bbox": [78, 60, 400, 90], "text": "25.如图1，抛物线"}])

        self.assertFalse(any("印在【解析】之后" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)
