"""Giving a question back the chart that was printed above it.

国内卷面常把配图印在题目文字正上方，整张落在上一题的纵向范围里，于是上一张卡把
它认领走了，本题只剩一句「如图」。产品自己的配图策略会把这种卡判成 blocked_missing
拦下来等人补图；`reclaim_stranded_figures` 在切题之后按纯几何把它改回本题。

这些用例钉住两件事：该抢回来的一定抢回来，以及**不该抢的绝不抢**——
抢错会把一道本来正确的题改坏，比漏掉一张图更糟。
"""
from __future__ import annotations

from django.test import SimpleTestCase

from . import pipeline

FOLD = [365.0, 176.0, 535.0, 308.0]     # 折正方形：第 15 题的图，压在第 14 题的范围里
GRAPH = [94.0, 181.0, 272.0, 305.0]     # 行程图：第 14 题自己的
CUE_TEXT = "15. 如图，正方形纸片 $ABCD$ 中，"


def image(seq: int, bbox: list[float], page_idx: int = 0) -> dict:
    return {"seq": seq, "type": "image", "page_idx": page_idx, "bbox": list(bbox), "text": ""}


def text(seq: int, value: str, bbox: list[float], page_idx: int = 0) -> dict:
    return {"seq": seq, "type": "text", "page_idx": page_idx, "bbox": list(bbox), "text": value}


def candidate(seq: int, bbox: list[float], page_idx: int = 0) -> dict:
    return {"seq": seq, "page_idx": page_idx, "bbox": list(bbox)}


def two_cards(holder_figures: list[dict]) -> list[dict]:
    """第 14 题的范围一路铺到 y=322，第 15 题从 y=315 才开始——折叠图整张在第 14 题里。"""
    return [
        {"number": 14, "regions": [{"page_idx": 0, "bbox": [70.0, 77.0, 923.0, 322.0]}],
         "figure_candidates": list(holder_figures)},
        {"number": 15, "regions": [{"page_idx": 0, "bbox": [70.0, 315.0, 923.0, 387.0]}],
         "figure_candidates": []},
    ]


def default_fixture(*, holder_figures: list[dict] | None = None, stem: str = CUE_TEXT):
    blocks = [
        image(1, GRAPH),
        image(2, FOLD),
        text(3, stem, [80.0, 320.0, 900.0, 380.0]),
    ]
    if holder_figures is None:
        holder_figures = [candidate(1, GRAPH), candidate(2, FOLD)]
    return two_cards(holder_figures), blocks


class ReclaimStrandedFiguresTests(SimpleTestCase):
    def test_chart_printed_above_the_question_returns_to_it(self):
        items, blocks = default_fixture()

        notes = pipeline.reclaim_stranded_figures(items, blocks)

        self.assertEqual(len(notes), 1)
        self.assertIn("第 15 题", notes[0])
        # 折正方形那张回到第 15 题：进候选池，也进范围（否则渲染出来是空的）
        self.assertEqual([c["seq"] for c in items[1]["figure_candidates"]], [2])
        self.assertEqual(items[1]["figure_candidates"][0]["bbox"], FOLD)
        self.assertEqual(len(items[1]["regions"]), 2)
        self.assertEqual(items[1]["regions"][1]["bbox"], FOLD)
        # 第 14 题只剩它自己的行程图
        self.assertEqual([c["seq"] for c in items[0]["figure_candidates"]], [1])
        self.assertEqual(len(items[0]["regions"]), 1)

    def test_a_chart_the_previous_question_still_needs_is_left_alone(self):
        # 第 14 题只有这一张图：抢走就等于把它改坏，宁可漏掉。
        items, blocks = default_fixture(holder_figures=[candidate(2, FOLD)])

        notes = pipeline.reclaim_stranded_figures(items, blocks)

        self.assertEqual(notes, [])
        self.assertEqual(items[1]["figure_candidates"], [])
        self.assertEqual(len(items[1]["regions"]), 1)
        self.assertEqual([c["seq"] for c in items[0]["figure_candidates"]], [2])

    def test_a_question_without_a_figure_cue_is_untouched(self):
        items, blocks = default_fixture(stem="15. 计算下列各式的值。")

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual(items[1]["figure_candidates"], [])

    def test_a_question_that_already_has_a_figure_is_untouched(self):
        # 本题范围里已经有图就不是这一类，别去动一张已经拿对的卡。
        items, blocks = default_fixture()
        items[1]["figure_candidates"] = [candidate(9, [700.0, 330.0, 800.0, 380.0])]
        blocks.append(image(9, [700.0, 330.0, 800.0, 380.0]))

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual([c["seq"] for c in items[1]["figure_candidates"]], [9])

    def test_a_chart_far_above_the_question_is_not_claimed(self):
        """距离阈值必须自己挡住，不能靠别的守卫顺带挡下来。

        上一题手上留着两张图（「只剩一张就不抢」那条守卫因此不触发），本页题目上方
        只有一张图、离题目上边缘 133 个单位。阈值一旦失效，它就会被认领走。
        第二张图放在页面底部，不在题目上方，不会被当成候选。
        """
        far = [365.0, 50.0, 535.0, 182.0]
        below = [94.0, 800.0, 272.0, 900.0]
        blocks = [
            image(2, far),
            image(5, below),
            text(3, CUE_TEXT, [80.0, 320.0, 900.0, 380.0]),
        ]
        items = two_cards([candidate(2, far), candidate(5, below)])

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual(items[1]["figure_candidates"], [])

    def test_the_closest_chart_wins_when_several_sit_above(self):
        items, blocks = default_fixture()
        nearer = [365.0, 240.0, 535.0, 312.0]     # 更靠近第 15 题上边缘
        blocks.append(image(4, nearer))
        items[0]["figure_candidates"].append(candidate(4, nearer))

        pipeline.reclaim_stranded_figures(items, blocks)

        self.assertEqual([c["seq"] for c in items[1]["figure_candidates"]], [4])

    def test_a_page_without_any_chart_changes_nothing(self):
        blocks = [text(3, CUE_TEXT, [80.0, 320.0, 900.0, 380.0])]
        items = [{"number": 15, "regions": [{"page_idx": 0, "bbox": [70.0, 315.0, 923.0, 387.0]}],
                  "figure_candidates": []}]

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual(items[0]["figure_candidates"], [])


class HangingFigureTests(SimpleTestCase):
    """The second shape: the chart starts inside the question and hangs below it.

    教材例 1 的图 1-29：题目范围 y 94–151，图 y 135–275。图的上端在题目里、下端垂在
    外面，于是图心落在两处范围之外，谁都没认领它。
    """

    CHART = [627.0, 135.0, 843.0, 275.0]
    EXAMPLE = "例 1 如图 1-29，在 $\\triangle ABC$ 中，$AB=AC$，$AD$ 是中线。"

    def hanging_fixture(self, extra_cards=(), extra_blocks=()):
        blocks = [
            image(2, self.CHART),
            text(3, self.EXAMPLE, [90.0, 100.0, 880.0, 145.0]),
            *extra_blocks,
        ]
        items = [
            {"number": 1, "regions": [{"page_idx": 0, "bbox": [81.0, 94.0, 899.0, 151.0]}],
             "figure_candidates": []},
            *extra_cards,
        ]
        return items, blocks

    def test_a_chart_hanging_out_of_the_question_is_taken(self):
        items, blocks = self.hanging_fixture()

        notes = pipeline.reclaim_stranded_figures(items, blocks)

        self.assertEqual(len(notes), 1)
        self.assertIn("第 1 题", notes[0])
        self.assertEqual([c["seq"] for c in items[0]["figure_candidates"]], [2])
        self.assertEqual(len(items[0]["regions"]), 2)
        self.assertEqual(items[0]["regions"][1]["bbox"], self.CHART)

    def test_a_chart_whose_centre_sits_in_another_question_is_left_alone(self):
        # 图心落在另一张卡的范围里，说明它是那道题的图，只是起点碰巧压在这里。
        items, blocks = self.hanging_fixture(
            extra_cards=[{"number": 2,
                          "regions": [{"page_idx": 0, "bbox": [70.0, 180.0, 920.0, 240.0]}],
                          "figure_candidates": []}],
        )

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual(items[0]["figure_candidates"], [])

    def test_a_hanging_chart_already_owned_is_not_stolen(self):
        items, blocks = self.hanging_fixture(
            extra_cards=[{"number": 2,
                          "regions": [{"page_idx": 0, "bbox": [70.0, 300.0, 920.0, 380.0]}],
                          "figure_candidates": [candidate(2, self.CHART)]}],
        )

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual(items[0]["figure_candidates"], [])
        self.assertEqual([c["seq"] for c in items[1]["figure_candidates"]], [2])

    def test_a_chart_hanging_far_below_the_question_is_not_taken(self):
        # 垂出去 260 个单位：那是下一题的图，不是本题的。阈值之外不动。
        items, blocks = self.hanging_fixture()
        far = [627.0, 100.0, 843.0, 411.0]
        blocks[0] = image(2, far)

        self.assertEqual(pipeline.reclaim_stranded_figures(items, blocks), [])
        self.assertEqual(items[0]["figure_candidates"], [])