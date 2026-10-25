"""看图识读交了白卷时，卡不许是绿的。

真实案例：胜利初一月考第 11 题，原页上「车轮旋转时看起来像个圆面，这说明
_______.」完整存在，切题范围也确实罩住了那一行——不是切错，是识读一个字都没
读出来，而系统照收成了一张 green、零旗标的卡。谁都看不出来少了这道题。

原有的两处检查都覆盖不到这里：FLAG_MINERU_DRAFT_EMPTY 只管“不用看图模型、
题面全用 MinerU 初稿”那条路；FLAG_LOCATED_WITHOUT_NUMBER 只管“题号由 AI 定位”
的卡。这两条用例把边界钉住——正文保留原图的卡题干本来就该是空的，不许被误伤。
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline
from .models import Question

EMPTY_READING = {
    "stem": "",
    "options": {},
    "content_kind": "question",
    "figures": {},
    "missing_figure": False,
    "number_seen": 11,
    "figure_descriptions": [],
    "unclear": False,
}
FILLED_READING = {
    "stem": "车轮旋转时看起来像个圆面，这说明________。",
    "options": {},
    "content_kind": "question",
    "figures": {},
    "missing_figure": False,
    "number_seen": 11,
    "figure_descriptions": [],
    "unclear": False,
}


def snapshot(**changes):
    base = {
        "id": 1,
        "number": 11,
        "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [11, 322, 895, 345]}],
        "candidates": [],
        "question_type": "fill_blank",
        "stem": "",
        "options": {},
        "edited": False,
        "source_kind": Question.SourceKind.UNKNOWN,
        "source_anchor_seq": 11,
        "segmentation_flags": [],
        "body_mode": "text",
        "start_source": "mineru",
    }
    base.update(changes)
    return base


class EmptyStemFlagTests(SimpleTestCase):
    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, reading, **changes):
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_reader_that_returns_nothing_is_flagged_not_shipped_green(self):
        result = self.run_card(EMPTY_READING)

        self.assertEqual(result["stem"], "")
        self.assertIn(pipeline.FLAG_EMPTY_STEM, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_whitespace_only_stem_is_treated_as_empty(self):
        result = self.run_card({**EMPTY_READING, "stem": "\n  \n"})

        self.assertIn(pipeline.FLAG_EMPTY_STEM, result["flags"])

    def test_a_card_that_keeps_the_source_image_may_have_no_stem(self):
        result = self.run_card(EMPTY_READING, body_mode="source_image")

        self.assertNotIn(pipeline.FLAG_EMPTY_STEM, result["flags"])

    def test_a_real_reading_is_untouched(self):
        result = self.run_card(FILLED_READING)

        self.assertNotIn(pipeline.FLAG_EMPTY_STEM, result["flags"])
        self.assertEqual(result["stem"], FILLED_READING["stem"])

    def test_the_flag_survives_an_unrelated_reading_repair(self):
        # 题号由 AI 定位那条路本来就有自己的提示；两条不该互相顶掉。
        # number_seen 必须是「没人看到题号」的样子，那条提示才会出现。
        result = self.run_card({**EMPTY_READING, "number_seen": None}, start_source="located")

        self.assertIn(pipeline.FLAG_LOCATED_WITHOUT_NUMBER, result["flags"])
        self.assertIn(pipeline.FLAG_EMPTY_STEM, result["flags"])

    def test_a_reader_that_did_see_the_number_does_not_raise_the_range_flag(self):
        result = self.run_card(EMPTY_READING, start_source="located")

        self.assertNotIn(pipeline.FLAG_LOCATED_WITHOUT_NUMBER, result["flags"])
        self.assertIn(pipeline.FLAG_EMPTY_STEM, result["flags"])
