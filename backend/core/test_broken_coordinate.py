"""题干里那个只读到一半的坐标，会照原样印进组卷卷面。

202510 胜利初四月考第 23 题：原卷上清清楚楚印着「(-1,0)」，MinerU 也读对了，
识读却写成 ``$( \\ ,-1.0)$``——左边那格空了、右边多了个小数点。这张卡本来就有
「AI 与 MinerU 有差异」的提示，但那四处说的是「得/到」「抛物线 r/V」，坐标不在
里面。

判据只用纸面结构：坐标的两个数必须都在。两格都空（``（  ，  ）``）是待填的答案
括号，不算——所以要求逗号后面紧跟着一个数。全库 241 张卡只命中这一张。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_broken_coordinate
"""
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from core import figure_policy, pipeline, readers
from core.models import Question

REAL = ("如图1所示，平面直角坐标系中，抛物线 $y=ax^2-2ax+3$ 交 $x$ 轴于 $A$，$B$ 两点，"
        "已知点 $A$ 坐标为 $( \\ ,-1.0)$．")
GOOD = "如图1所示，平面直角坐标系中，抛物线 $y=ax^2-2ax+3$ 交 $x$ 轴于 $A$、$B$ 两点，" \
       "已知点 $A$ 坐标为 $(-1,0)$．"


class BrokenCoordinateTests(SimpleTestCase):
    def test_the_real_reading_is_caught(self):
        self.assertEqual(pipeline._broken_coordinate(REAL), r"( \ ,-1")

    def test_a_complete_coordinate_is_quiet(self):
        self.assertEqual(pipeline._broken_coordinate(GOOD), "")

    def test_an_empty_second_slot_is_caught(self):
        self.assertTrue(pipeline._broken_coordinate("点 $P$ 的坐标为 $(-1, )$．"))

    def test_an_empty_first_slot_with_a_number_after_it_is_caught(self):
        self.assertTrue(pipeline._broken_coordinate("点 $P$ 的坐标为 $( ,0)$．"))

    def test_a_blank_coordinate_to_fill_in_is_not_a_defect(self):
        # “点 P 的坐标为（  ，  ）” is the question, not a broken reading: both slots
        # are empty, so neither side of the comma has a number to compare against.
        for stem in ("点 $P$ 的坐标为（  ，  ），求 $P$ 的位置。",
                     "点 $P$ 的坐标为 $( , )$．", "坐标为（ ， ）"):
            self.assertEqual(pipeline._broken_coordinate(stem), "", stem)

    def test_an_ordinary_sentence_is_quiet(self):
        for stem in ("在区间 $(-1,2)$ 上求最值。", "如图，点 $A(1,2)$、$B(3,4)$。",
                     "由（1）可知结论成立。"):
            self.assertEqual(pipeline._broken_coordinate(stem), "", stem)

    def test_options_are_not_checked_but_the_stem_is(self):
        # 只查题干：选项里出现坐标不是缺陷，题干里才是。
        self.assertEqual(pipeline._broken_coordinate("下列说法正确的是（  ）。"), "")


READING = {"stem": REAL, "options": {}, "type": "free_response", "content_kind": "exercise",
           "figures": {}, "missing_figure": False, "number_seen": 23,
           "figure_descriptions": [], "unclear": False, "unreadable": False}


class BrokenCoordinateWiringTests(SimpleTestCase):
    def read(self, reading):
        snapshot = {
            "id": 1, "number": 23, "group_id": None,
            "regions": [{"page_idx": 2, "bbox": [517.0, 632.0, 952.0, 850.0]}],
            "candidates": [], "question_type": "free_response", "section": "三、解答题",
            "stem": "", "options": {}, "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN, "source_anchor_seq": 23,
            "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
            "witness_blocks": [{"page_idx": 2, "bbox": [532.0, 641.0, 897.0, 683.0],
                                "text": "23.如图1所示，平面直角坐标系中，抛物线…"}],
        }
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=reading), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=800, width=1200), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_the_card_names_the_broken_coordinate(self):
        result = self.read(READING)
        self.assertTrue(any("只读到一半" in f for f in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_good_coordinate_leaves_the_card_quiet(self):
        result = self.read({**READING, "stem": GOOD})
        self.assertFalse(any("只读到一半" in f for f in result["flags"]))
        self.assertNotIn(figure_policy.FLAG_UNFOUND_FIGURE, result["flags"])