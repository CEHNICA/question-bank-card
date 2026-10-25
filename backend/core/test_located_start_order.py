"""题号定位不得跑到前一题起点之前——口镇第 5 题就是这么没的。

口镇那份卷子第 5 题的题号被红笔批干扰，MinerU 没读出来，AI 把它定位到左栏 y660，
而第 4 题的起点在 y687。起点一倒退，第 4 题的范围被迫跨栏，把右栏顶部的真第 5 题
整块吃掉，第 5 题的题干还会混进第 4 题的原图截图，同时留下一张题干只有 [?] 的空壳。

``_inside_previous_opening`` 明写着 ``y < previous.y`` 就放弃——它只管前一题的第
一行，起点倒退的情况它根本不看。所以这里单独钉住边界。
"""

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from . import pipeline, segment


class LocatedStartOrderTests(SimpleTestCase):
    # 口镇真实的数：第 4 题起点在左栏 y687，AI 把第 5 题定在左栏 y660。
    previous = segment.Start(number=4, page=0, x=53, y=687, seq=12, col=0)

    def test_a_start_in_front_of_the_previous_question_is_a_misread(self):
        self.assertTrue(pipeline._located_before_previous(self.previous, 0, 0, 660))

    def test_the_same_position_as_the_previous_start_is_also_rejected(self):
        self.assertTrue(pipeline._located_before_previous(self.previous, 0, 0, 687))

    def test_a_start_below_the_previous_question_is_accepted(self):
        self.assertFalse(pipeline._located_before_previous(self.previous, 0, 0, 700))

    def test_a_start_in_the_next_column_is_accepted(self):
        # Same page, same y — but question 4 has ended and the column continues.
        self.assertFalse(pipeline._located_before_previous(self.previous, 0, 1, 40))

    def test_a_start_on_a_later_page_is_accepted(self):
        self.assertFalse(pipeline._located_before_previous(self.previous, 1, 0, 10))

    def test_the_guard_covers_what_the_opening_guard_gives_up_on(self):
        # The two guards must not both return False for the same bad position,
        # or the case falls through both of them.
        blocks = [{"seq": 12, "bbox": [53, 687, 511, 706], "text": "4. 如图"}]

        self.assertFalse(pipeline._inside_previous_opening(blocks, self.previous, 0, 0, 660))
        self.assertTrue(pipeline._located_before_previous(self.previous, 0, 0, 660))


class LocateMissingRejectsBackwardsStartTests(SimpleTestCase):
    """上面那组只钉住纯函数；这里钉住它真的接在 locate_missing 上。

    纯函数返回对了、可是没人调用它，测试照样全绿——所以这里走一遍 locate_missing
    的真实路径，把 AI 定位的结果摆成口镇那个错位坐标，看它是被拒绝还是被采纳。
    """

    def locate(self, located_y):
        previous = segment.Start(number=4, page=0, x=53, y=687, seq=12, col=0)
        starts = [previous]
        unresolved: list[tuple[int, int]] = []
        placed = [{"page_idx": 0, "bbox": [53, 640, 511, 940]}]
        layout = segment.Layout(page_count=1, splits={0: [511]}, slots=[])
        with (
            mock.patch.object(pipeline.readers, "primary_engine", return_value=object()),
            mock.patch.object(segment, "missing_numbers", return_value=[(5, previous)]),
            mock.patch.object(
                segment, "region_regions_between", return_value=[{"page_idx": 0, "bbox": [53, 640, 511, 940]}]
            ),
            mock.patch.object(
                pipeline.imaging, "stack_regions", return_value=(SimpleNamespace(height=1200, width=800), placed)
            ),
            mock.patch.object(pipeline.imaging, "add_ruler", return_value=(object(), 12)),
            mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:url"),
            mock.patch.object(pipeline.imaging, "band_to_page", return_value=(0, located_y)),
            mock.patch.object(pipeline.readers, "locate_band", return_value=6),
            mock.patch.object(pipeline, "_snap_located_start", return_value=located_y),
            mock.patch.object(pipeline, "_snap_to_gap", side_effect=lambda _i, r, _b: r),
            mock.patch.object(pipeline, "_inside_previous_opening", return_value=False),
        ):
            notes = pipeline.locate_missing(mock.Mock(), layout, starts, mock.Mock(), blocks=[], unresolved=unresolved)
        return starts, unresolved, notes

    def test_the_backwards_start_is_refused_and_reported_as_swallowed(self):
        starts, unresolved, notes = self.locate(660)

        self.assertEqual(len(starts), 1, "错位的第 5 题起点不该被采纳")
        self.assertEqual(unresolved, [(5, 4)], "应交给上一题自己说可能吞了它")
        self.assertTrue(any("比第 4 题的起点还靠前" in note for note in notes), notes)

    def test_a_forward_start_is_still_accepted(self):
        starts, unresolved, notes = self.locate(700)

        self.assertEqual(len(starts), 2, "正常的第 5 题起点应当照常采纳")
        self.assertEqual(starts[1].number, 5)
        self.assertEqual(starts[1].source, "located")
        self.assertEqual(unresolved, [])
        self.assertFalse(any("比第 4 题的起点还靠前" in note for note in notes), notes)

    def test_landing_exactly_on_the_previous_start_is_refused(self):
        # 正好落在第 4 题的起点上：既不算“在它后面”，也不是一道新题的位置。
        starts, unresolved, notes = self.locate(687)

        self.assertEqual(len(starts), 1, "与前一题同一起点的定位不能被采纳")
        self.assertEqual(unresolved, [(5, 4)])
        self.assertTrue(any("比第 4 题的起点还靠前" in note for note in notes), notes)
