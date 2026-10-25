"""从上一题的草稿区探进来的配图，不该再端到这道题面前。

真卷实测（2026-10-08，11 份卷子 282 张候选图）：这类图有 7 张，其中 0 张绑在题上，
逐张看过——3 张是学生自己的草稿，4 张是上一题印的图（选项数轴、方框图、水池芦苇、
手画坐标框）。反过来也量过一遍：胜利初一第 3 题的正方体展开图整张印在题行**下方**、
中心落在范围之外，那是这道题自己的图，不能因为「中心不在范围内」就丢掉。

所以判据分两个方向：图挂在题行下方是正常的，从上边界之上探进来不是。

    .\\backend\\.venv\\Scripts\\python.exe backend\\manage.py test core.test_figure_spill_in
"""

from django.test import SimpleTestCase

from core import segment


class FigureSpillDirectionTests(SimpleTestCase):
    """A picture crossing a question boundary counts from the direction it crosses."""

    # 202510口镇高中数学第 10 题 starts at y 68.6; the crossed-out matrix the student
    # left above it spans y 29–82, so a quarter of it dips under the line.
    question_below = [{"page_idx": 0, "bbox": [0.0, 68.6, 512.0, 167.0]}]
    scratch_above = [324.0, 29.0, 371.0, 82.0]

    # 胜利初一第 3 题: the printed cube net hangs entirely below its own text line.
    question_above = [{"page_idx": 0, "bbox": [146.0, 378.3, 981.0, 445.0]}]
    picture_below = [801.0, 416.0, 934.0, 489.0]

    def test_scratch_reaching_in_from_above_is_not_this_question_picture(self):
        self.assertTrue(segment.overlaps_regions(0, self.scratch_above, self.question_below))
        self.assertFalse(segment.figure_in_regions(0, self.scratch_above, self.question_below))

    def test_picture_hanging_below_the_text_line_is_kept(self):
        # The 20% overlap test alone would keep it, and that is the point: centre-in
        # would throw a printed figure away here.
        self.assertFalse(segment.center_in_regions(0, self.picture_below, self.question_above))
        self.assertTrue(segment.figure_in_regions(0, self.picture_below, self.question_above))

    def test_a_picture_inside_the_range_is_kept(self):
        inside = [200.0, 90.0, 300.0, 150.0]
        self.assertTrue(segment.figure_in_regions(0, inside, self.question_below))

    def test_another_page_is_never_matched(self):
        self.assertFalse(segment.figure_in_regions(1, self.picture_below, self.question_above))

    def test_only_the_regions_it_overlaps_count(self):
        # The scratch dips into one range and touches nothing else.  A second question
        # further down the same page must not turn the verdict around.
        later = {"page_idx": 0, "bbox": [0.0, 300.0, 512.0, 400.0]}
        self.assertFalse(segment.figure_in_regions(0, self.scratch_above, [self.question_below[0], later]))

    def test_empty_regions_keep_the_old_answer(self):
        self.assertFalse(segment.figure_in_regions(0, self.picture_below, []))


PAGES = [{"page_idx": 0, "width": 595, "height": 415}]


def block(seq, y, text, kind="text", x0=60, x1=520, height=30):
    return {"seq": seq, "type": kind, "page_idx": 0, "bbox": [x0, y, x1, y + height], "text": text}


class FigureSpillWiringTests(SimpleTestCase):
    """The rule has to hold where exam-paper candidates are actually produced."""

    def build(self, picture_y, picture_height=120):
        blocks = [
            block(0, 100, "8. 下列说法正确的是（ ）"),
            block(1, picture_y, "", kind="image", x0=324, x1=371, height=picture_height),
            block(2, 460, "9. 直线 $3x+4y-2=0$ 的一个方向向量是（ ）"),
        ]
        result = segment.segment(PAGES, blocks)
        return result, blocks

    def candidates(self, result, number):
        item = next(entry for entry in result["questions"] if entry["number"] == number)
        return [entry["seq"] for entry in item["figure_candidates"]]

    def test_scratch_above_the_next_start_never_becomes_its_candidate(self):
        # Question 9 is cut at y 451; the picture spans 380–500, so it passes the
        # old 20%-overlap test and is then dropped for reaching in from above.
        result, blocks = self.build(380)
        self.assertEqual(self.candidates(result, 9), [])
        self.assertEqual(self.candidates(result, 8), [1])
        self.assertTrue(segment.overlaps_regions(0, blocks[1]["bbox"], result["questions"][1]["regions"]))

    def test_picture_hanging_below_the_boundary_is_kept(self):
        result, _blocks = self.build(430)
        self.assertEqual(self.candidates(result, 9), [1])

    def test_printed_picture_below_its_line_keeps_a_centre_outside_the_range(self):
        # 胜利初一第 3 题's shape: the picture is printed under its own text line and
        # reaches past where the *next* question starts, so its centre falls outside
        # this question's range.  Requiring the centre inside would throw a printed
        # figure away — the reason this is a direction test rather than the
        # 20%-overlap test on its own.
        blocks = [
            block(0, 100, "8. 下列说法正确的是（ ）"),
            block(1, 460, "9. 直线 $3x+4y-2=0$ 的一个方向向量是（ ）"),
            block(2, 490, "", kind="image", x0=324, x1=371, height=110),
            block(3, 520, "10. 下列各式中正确的是（ ）"),
        ]
        result = segment.segment(PAGES, blocks)
        region = next(item for item in result["questions"] if item["number"] == 9)["regions"][0]
        self.assertFalse(segment.center_in_regions(0, blocks[2]["bbox"], [region]))
        self.assertTrue(segment.overlaps_regions(0, blocks[2]["bbox"], [region]))
        self.assertIn(2, self.candidates(result, 9))
