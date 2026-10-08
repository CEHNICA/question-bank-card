"""答案版卷子：发给识读的图不该把整版【答案】【解析】带进去。

202510 答案版第 18 题的范围是「第 8 页底部 + 第 9 页顶部一整页」，第 9 页第 4 个
版面单位就是【答案】，于是送进模型的那张图 1862x1614 里八成是解答文字，识读对着
它回了「（无法识别图片内容）」，卡是绿的（2026-10-08 实测）。

真卷实测：答案版 6 道题裁掉解析之后，识读结果与卡上现有题干逐字一致，图的高度少
2.9–14.8 倍；10 份不含这些标记的正常试卷一张都不受影响。

裁的只是给模型看的那张图——卡上存的范围、配图候选、旗标判据全都按原范围走。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_solution_trim
"""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core import figure_policy, pipeline, readers
from core.models import Question

# 第 18 题的真实范围与解析标记（page_idx 9 那段从第 4 个单位起就是【答案】）。
REAL_REGIONS = [{"page_idx": 8, "bbox": [62.0, 480.0, 937.0, 583.0]},
               {"page_idx": 9, "bbox": [60.0, 45.0, 937.0, 474.0]}]
REAL_BLOCKS = [
    {"page_idx": 8, "bbox": [87.0, 500.0, 800.0, 520.0], "text": "18.已知二次函数 $y=-x^2+bx+c$"},
    {"page_idx": 9, "bbox": [87.0, 49.0, 578.0, 63.0], "text": "【答案】（1) $y=-x^2+2x+3$"},
    {"page_idx": 9, "bbox": [89.0, 161.0, 489.0, 173.0], "text": "【详解】解：（1）∵二次函数"},
]


class SolutionTrimTests(SimpleTestCase):
    def trim(self, regions, blocks):
        return pipeline._reading_regions({"regions": regions, "witness_blocks": blocks})

    def test_the_real_case_drops_the_page_that_is_all_solution(self):
        trimmed = self.trim(REAL_REGIONS, REAL_BLOCKS)
        self.assertEqual(trimmed, [{"page_idx": 8, "bbox": [62.0, 480.0, 937.0, 583.0]}])

    def test_a_segment_keeps_the_text_printed_above_the_marker(self):
        regions = [{"page_idx": 1, "bbox": [60.0, 40.0, 930.0, 900.0]}]
        blocks = [{"page_idx": 1, "bbox": [87.0, 344.0, 912.0, 899.0],
                   "text": "【答案】C\n【分析】本题考查..."}]
        self.assertEqual(self.trim(regions, blocks),
                         [{"page_idx": 1, "bbox": [60.0, 40.0, 930.0, 342.0]}])

    def test_a_normal_paper_is_returned_untouched(self):
        regions = [{"page_idx": 0, "bbox": [52.0, 580.0, 512.0, 789.0]}]
        blocks = [{"page_idx": 0, "bbox": [80.0, 600.0, 400.0, 620.0], "text": "5. 如图所示，"}]
        self.assertEqual(self.trim(regions, blocks), regions)

    def test_a_marker_outside_the_region_does_not_cut(self):
        regions = [{"page_idx": 1, "bbox": [60.0, 500.0, 930.0, 900.0]}]
        blocks = [{"page_idx": 1, "bbox": [87.0, 100.0, 912.0, 130.0], "text": "【答案】C"}]
        self.assertEqual(self.trim(regions, blocks), regions)

    def test_a_marker_on_another_page_does_not_cut(self):
        regions = [{"page_idx": 2, "bbox": [60.0, 40.0, 930.0, 400.0]}]
        blocks = [{"page_idx": 1, "bbox": [87.0, 60.0, 912.0, 90.0], "text": "【答案】C"}]
        self.assertEqual(self.trim(regions, blocks), regions)

    def test_text_without_the_marker_word_does_not_cut(self):
        regions = [{"page_idx": 0, "bbox": [60.0, 40.0, 930.0, 900.0]}]
        blocks = [{"page_idx": 0, "bbox": [87.0, 60.0, 912.0, 90.0], "text": "16. （6分）小毅设计了包装盒"}]
        self.assertEqual(self.trim(regions, blocks), regions)

    def test_a_marker_inside_a_merged_block_never_cuts(self):
        # MinerU merged the question line and the answer line into one block on
        # 202510 答案版 page 15: “(3)将 ΔABD… 判断点 E_i 是否在该双曲线上【答案】(1)…”.
        # Cutting at that block's top deleted sub-question (3) from the reading,
        # and the card stayed green.  A marker in the middle is not a section start.
        regions = [{"page_idx": 14, "bbox": [60.0, 658.0, 932.0, 920.0]}]
        blocks = [{"page_idx": 14, "bbox": [80.0, 898.0, 700.0, 916.0],
                   "text": "(3)将 $\\Delta ABD$ 顺时针旋转 $90^\\circ$，判断点 $E_i$ 是否在该双曲线上"
                           "【答案】(1)直线l的函数解析式为 $y=x+1$;"}]
        self.assertEqual(self.trim(regions, blocks), regions)

    def test_every_segment_dropped_falls_back_to_the_stored_range(self):
        regions = [{"page_idx": 0, "bbox": [60.0, 40.0, 930.0, 60.0]}]
        blocks = [{"page_idx": 0, "bbox": [87.0, 45.0, 912.0, 55.0], "text": "【答案】C"}]
        self.assertEqual(self.trim(regions, blocks), regions)

    def test_no_blocks_means_no_trimming(self):
        regions = REAL_REGIONS
        self.assertEqual(self.trim(regions, []), regions)

    def test_mixed_segments_keep_the_ones_without_a_marker(self):
        regions = [{"page_idx": 0, "bbox": [60.0, 40.0, 500.0, 200.0]},
                   {"page_idx": 1, "bbox": [60.0, 40.0, 930.0, 900.0]}]
        blocks = [{"page_idx": 1, "bbox": [87.0, 300.0, 912.0, 340.0], "text": "【详解】解："}]
        self.assertEqual(
            self.trim(regions, blocks),
            [{"page_idx": 0, "bbox": [60.0, 40.0, 500.0, 200.0]},
             {"page_idx": 1, "bbox": [60.0, 40.0, 930.0, 298.0]}])


READING = {"stem": "如图，在 $\\triangle ABC$ 中，$\\angle B=30°$，$AC=10$。", "options": {},
           "type": "free_response", "content_kind": "exercise", "figures": {},
           "missing_figure": False, "number_seen": 17, "figure_descriptions": [],
           "unclear": False, "unreadable": False}


class SolutionFigureFlagTests(SimpleTestCase):
    """解析里的图不再并进题目后，提示语不能还说「原卷可能有图没有被找到」。

    真卷形状：202510 答案版第 5 题，候选 1 印在【答案】上方（属于题目），
    候选 2 印在下方（属于解析）。裁掉解析之后模型看不见候选 2，它就一直没归类。
    """

    # 【答案】在 y556，候选 1 在它上面、候选 2 在它下面。
    BLOCKS = [{"page_idx": 1, "bbox": [90.0, 556.0, 144.0, 568.0], "text": "【答案】C"}]
    CANDIDATES = [{"label": "1", "page_idx": 1, "bbox": [85.0, 375.0, 226.0, 489.0]},
                  {"label": "2", "page_idx": 1, "bbox": [132.0, 639.0, 233.0, 719.0]}]

    def count(self, blocks, assignments):
        return pipeline._unclassified_below_solution(blocks, self.CANDIDATES, assignments)

    def test_all_pending_boxes_sit_below_the_solution(self):
        self.assertEqual(self.count(self.BLOCKS, {"1": "stem"}), (1, 1))

    def test_one_pending_box_sits_above_the_solution(self):
        candidates = [{"label": "1", "page_idx": 1, "bbox": [132.0, 639.0, 233.0, 719.0]},
                      {"label": "2", "page_idx": 1, "bbox": [85.0, 375.0, 226.0, 489.0]}]
        self.assertEqual(
            pipeline._unclassified_below_solution(self.BLOCKS, candidates, {}), (1, 2))

    def test_a_paper_without_the_marker_reports_nothing_below(self):
        self.assertEqual(self.count([], {"1": "stem"}), (0, 1))

    def test_nothing_pending_means_nothing_to_place(self):
        self.assertEqual(self.count(self.BLOCKS, {"1": "stem", "2": "none"}), (0, 0))

    def snapshot(self):
        return {
            "id": 1, "number": 5, "group_id": None,
            "regions": [{"page_idx": 1, "bbox": [60.0, 344.0, 912.0, 899.0]}],
            "candidates": [dict(c) for c in self.CANDIDATES],
            "question_type": "single_choice", "section": "一、单选题",
            "stem": "", "options": {}, "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": 5,
            "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
            "witness_blocks": [{"page_idx": 1, "bbox": [80.0, 360.0, 700.0, 380.0],
                                "text": "5. 如图所示，$\\triangle ABC$的顶点在正方形网格的格点上"}] + self.BLOCKS,
        }

    def read(self):
        reading = {**READING, "type": "single_choice", "number_seen": 5,
                   "stem": "如图所示，$\\triangle ABC$的顶点在正方形网格的格点上，则 $\\tan C$ 的值为（  ）",
                   "options": {"A": "$\\frac{\\sqrt{2}}{2}$", "B": "$\\frac{1}{2}$",
                               "C": "$\\frac{1}{3}$", "D": "$3$"},
                   "figures": {"1": "stem"}}
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=reading), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=900, width=1400), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            return pipeline.read_card(self.snapshot(),
                                      SimpleNamespace(load=lambda page: object()))

    def test_the_card_says_it_came_from_the_solution(self):
        result = self.read()
        joined = " ".join(result["flags"])
        self.assertIn("【答案】/【解析】之后", joined)
        self.assertNotIn(figure_policy.FLAG_UNFOUND_FIGURE, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_only_the_question_s_own_box_stays_on_the_card(self):
        result = self.read()
        self.assertEqual([f["slot"] for f in result["figures"]], ["stem"])


class SolutionTrimWiringTests(SimpleTestCase):
    """裁出来的范围必须真的用来拼那张送识读的图。"""

    def snapshot(self):
        return {
            "id": 1, "number": 18, "group_id": None, "regions": REAL_REGIONS,
            "candidates": [], "question_type": "free_response", "section": "三、解答题",
            "stem": "", "options": {}, "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": 18,
            "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
            "witness_blocks": REAL_BLOCKS,
        }

    def test_the_stacked_image_is_built_from_the_trimmed_range(self):
        seen = []

        def stack(regions, loader, marks=None):
            seen.append([dict(r) for r in regions])
            return SimpleNamespace(height=900, width=1400), []

        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=READING), \
                mock.patch.object(pipeline.imaging, "stack_regions", side_effect=stack), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            pipeline.read_card(self.snapshot(), SimpleNamespace(load=lambda page: object()))
        self.assertTrue(seen)
        for regions in seen:
            self.assertEqual(regions, [{"page_idx": 8, "bbox": [62.0, 480.0, 937.0, 583.0]}])

    def test_the_card_keeps_its_stored_range(self):
        snapshot = self.snapshot()
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=READING), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=900, width=1400), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))
        self.assertEqual(snapshot["regions"], REAL_REGIONS)