"""候选图没人认领、又整幅是手写时，那句「请点配图框出」就不该一直挂着。

真卷上量过的（2026-10-08，11 份卷子）：全库 9 张「没归类」的候选图，产品自己的手写
判定和人工逐张看有 8 张一致；不一致的那张（胜利初四月考第 19 题）是模型更准——那个框
里混着学生的演算和印刷的「4/8」，按「整幅没有一笔印刷内容」的标准确实不能算手写。

所以这个改动的安全方向是：框里只要有一笔印刷内容，提示就留着。悄悄丢掉一张印好的图
才是贵的错。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_handwritten_candidate
"""
from unittest import mock
from types import SimpleNamespace

from PIL import Image
from django.test import SimpleTestCase

from core import figure_policy, pipeline, readers
from core.models import Question


CROP = Image.new("RGB", (8, 8), "white")


def labels(*specs):
    return {str(index + 1): {"page_idx": 0, "bbox": box}
            for index, box in enumerate(specs)}


class HandwrittenCandidateTests(SimpleTestCase):
    def call(self, *, source=None, answers=None, crop=CROP, engine=object()):
        engine = engine if engine is not None else mock.Mock()
        verdict = answers if answers is not None else {1}
        with mock.patch.object(pipeline, "_figure_alone", return_value=crop) as crop_call, \
                mock.patch.object(readers, "confirm_all_handwritten",
                                  return_value=verdict) as ask:
            found = pipeline._exclude_handwritten_candidates(
                engine, mock.Mock(), labels([0.0, 0.0, 10.0, 10.0]),
                {"figures": dict(source or {})})
        return found, ask, crop_call

    def test_all_handwriting_is_marked_irrelevant(self):
        found, ask, _crop = self.call(answers={1})
        self.assertEqual(found, {"1": "none"})
        self.assertEqual(ask.call_count, 1)

    def test_a_box_with_printing_keeps_the_prompt(self):
        # Not all handwriting: the prompt stays, which is the direction that cannot
        # lose a printed figure.
        found, _ask, _crop = self.call(answers=set())
        self.assertEqual(found, {})

    def test_already_claimed_candidates_are_not_asked_about(self):
        found, ask, crop_call = self.call(source={"1": "stem"})
        self.assertEqual(found, {})
        self.assertEqual(ask.call_count, 0)
        self.assertEqual(crop_call.call_count, 0)

    def test_nothing_to_ask_costs_no_call(self):
        with mock.patch.object(readers, "confirm_all_handwritten") as ask:
            self.assertEqual(
                pipeline._exclude_handwritten_candidates(
                    mock.Mock(), mock.Mock(), {}, {"figures": {}}), {})
            self.assertEqual(ask.call_count, 0)

    def test_no_engine_leaves_the_card_alone(self):
        self.assertEqual(
            pipeline._exclude_handwritten_candidates(
                None, mock.Mock(), labels([0.0, 0.0, 10.0, 10.0]), {"figures": {}}), {})

    def test_a_box_that_cannot_be_cropped_is_skipped(self):
        found, _ask, _crop = self.call(crop=None)
        self.assertEqual(found, {})

    def test_a_failed_call_is_skipped_not_fatal(self):
        with mock.patch.object(pipeline, "_figure_alone", return_value=CROP), \
                mock.patch.object(readers, "confirm_all_handwritten",
                                  side_effect=readers.ReaderError("boom")):
            self.assertEqual(
                pipeline._exclude_handwritten_candidates(
                    mock.Mock(), mock.Mock(),
                    labels([0.0, 0.0, 10.0, 10.0]), {"figures": {}}), {})

    def test_only_the_unclaimed_boxes_are_asked_about(self):
        with mock.patch.object(pipeline, "_figure_alone", return_value=CROP) as crop_call, \
                mock.patch.object(readers, "confirm_all_handwritten", return_value={1}):
            found = pipeline._exclude_handwritten_candidates(
                mock.Mock(), mock.Mock(),
                labels([0.0, 0.0, 10.0, 10.0], [0.0, 20.0, 10.0, 30.0]),
                {"figures": {"1": "stem"}})
        self.assertEqual(found, {"2": "none"})
        self.assertEqual(crop_call.call_count, 1)


# 真实卷面：胜利初四月考第 21 题，全是文字的填空题，学生演算的一行落在切题范围内，
# 识读没提它，于是卡上挂着「原卷可能有图没有被找到，请点配图框出」。
READING = {"stem": "某种商品每销售一件书就捐赠 $a$ 元（$a>0$），结余灾区，当每天销售最大利润为 6000",
           "options": {}, "type": "fill_blank", "content_kind": "exercise",
           "figures": {}, "missing_figure": False,
           "number_seen": 21, "figure_descriptions": [], "unclear": False}
SCRATCH = {"label": "1", "page_idx": 2, "bbox": [216.0, 665.0, 416.0, 740.0]}


class HandwrittenCandidateFlagTests(SimpleTestCase):
    """The flag has to disappear on the card, not just inside the helper."""

    def read(self, verdict):
        snapshot = {
            "id": 1, "number": 21, "group_id": None,
            "regions": [{"page_idx": 2, "bbox": [60, 640, 940, 780]}],
            "candidates": [SCRATCH],
            "question_type": "fill_blank", "section": "三、解答题", "stem": "", "options": {},
            "edited": False, "source_kind": Question.SourceKind.UNKNOWN,
            "source_anchor_seq": 21, "segmentation_flags": [], "body_mode": "text",
            "start_source": "mineru", "witness_blocks": [],
        }
        with mock.patch.object(readers, "primary_engine", return_value=object()), \
                mock.patch.object(readers, "read_question", return_value=READING), \
                mock.patch.object(pipeline, "_figure_alone", return_value=CROP), \
                mock.patch.object(readers, "confirm_all_handwritten", return_value=verdict), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                                  return_value=(SimpleNamespace(height=900, width=1400), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"):
            return pipeline.read_card(snapshot, SimpleNamespace(load=lambda page: object()))

    def test_handwriting_in_the_range_stops_the_frame_a_picture_prompt(self):
        result = self.read({1})
        self.assertNotIn(figure_policy.FLAG_UNFOUND_FIGURE, result["flags"])
        self.assertEqual(result["figures"], [])

    def test_a_printed_box_keeps_asking(self):
        result = self.read(set())
        self.assertIn(figure_policy.FLAG_UNFOUND_FIGURE, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)