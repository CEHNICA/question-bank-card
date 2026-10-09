r"""题目范围一路罩到这道题后面去了，成品卷上带着一整块不相干的内容。

真实案例（绿卡、零旗标，学生在成品卷上会看到完全不相干的东西）：

* 八下第 1 题 id=811 —— 罩住了第 1、2、3、※4 四道题，外加「数学理解」整栏。
* 九上第 23 题 id=1386 —— 罩住两道题，外加「用公式法解一元二次方程」「二次根式的
  乘除」两节新课。
* 八下第 2 题 id=851 —— 题目只有「2. 计算：（1）（2）」两行，下面压着「读一读 方根
  符号」整个侧栏（4 段正文、提到 carani、latus rectum、radix 的来历）。
* 八下第 2 题 id=790 —— 题目一行，下面是第 2 题整题外加「读一读 筝形」整栏。

已有的 `printed_question_numbers`（范围里印着别的题号）抓不到这一类：范围越界到下一
节而没罩住别的题号时它一声不吭；而且它只在教材上用（试卷各大题题号各自从 1 重编）。

判据只读纸上印着的形状：块文字恰好是一个栏目标题或「第X章」「一、」这种大题标题，
而题干里没有这句话。名单是从两本教材的块里量出来的（短、不含数字标点、全书反复出现）
再逐个裁图看过——同形状的「即」「所以」「山东教育出版社」「I」不是标题，没收进来。
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, textnorm
from .models import Question


def block(seq, text, y, page=0):
    return {"seq": seq, "text": text, "page_idx": page, "bbox": [100, y, 900, y + 30],
            "type": "text"}


class SectionHeadingsAfterAnchorTests(SimpleTestCase):
    def headings(self, blocks, anchor=1, text="2. 计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。"):
        return textnorm.section_headings_after_anchor(blocks, anchor, text)

    def test_a_column_heading_printed_after_this_question_is_reported(self):
        blocks = [block(1, "2.计算：（1）$\\sqrt{8}$", 100), block(2, "读一读", 400),
                  block(3, "方根符号", 440), block(4, "开方是最早产生的运算之一", 480)]
        self.assertEqual(self.headings(blocks), ["读一读"])

    def test_the_next_questions_and_a_whole_column_are_all_named(self):
        blocks = [block(1, "1.如图，在Rt△ABC中", 100), block(2, "2.已知：如图", 300),
                  block(3, "数学理解", 700), block(4, "※4.计算：", 900)]
        found = self.headings(blocks, text="1. 如图，在 Rt△ABC 中，$\\angle ACB=90°$。")
        self.assertEqual(found, ["数学理解"])

    def test_a_heading_printed_before_this_question_is_not_reported(self):
        # 八下「知识技能」正好压在第 1 题范围的上边界外：它在题的上方，是正常版面。
        # 第一版没做这个区分，六张样本里四张是这么虚报的。
        blocks = [block(1, "知识技能", 40), block(2, "1.解下列方程：", 100)]
        self.assertEqual(self.headings(blocks, text="1. 解下列方程：$(x)^2+12x+25=0$。"), [])

    def test_the_same_heading_in_the_stem_is_left_alone(self):
        # 题干自己提到「数学理解」时，范围里那个同名块不算越界。
        blocks = [block(1, "2.计算：", 100), block(2, "数学理解", 400)]
        self.assertEqual(self.headings(blocks, text="2. 计算：（1）（2）数学理解"), [])

    def test_a_chapter_heading_is_reported(self):
        blocks = [block(1, "3.如图，一个矩形广场", 100), block(2, "第八章 一元二次方程", 800)]
        self.assertEqual(self.headings(blocks, text="3. 如图，一个矩形广场的长为 60 m。"), ["第八章一元二次方程"])

    def test_a_big_section_heading_on_an_exam_is_reported(self):
        # 试卷上也一样：口镇第 6 题的范围罩住了「第一部分（选择题共58分）」那一段。
        blocks = [block(1, "6.某几何体的三视图", 100), block(2, "第一部分（选择题共58分）", 600)]
        self.assertEqual(self.headings(blocks, text="6. 某几何体的三视图如图。"), ["第一部分（选择题共58分）"])
        blocks = [block(1, "6.某几何体的三视图", 100), block(2, "一、选择题：本题共8小题", 600)]
        self.assertEqual(self.headings(blocks, text="6. 某几何体的三视图如图。"), ["一、选择题：本题共8小题"])

    def test_ordinary_question_text_is_not_a_heading(self):
        blocks = [block(1, "2.计算：", 100), block(2, "（1）$\\sqrt{8}$；", 200),
                  block(3, "3.如图，$\\triangle ABC$ 中", 400)]
        self.assertEqual(self.headings(blocks), [])

    def test_a_heading_on_a_later_page_still_counts(self):
        blocks = [block(1, "2.计算：", 100, page=3), block(2, "数学理解", 200, page=4)]
        self.assertEqual(self.headings(blocks), ["数学理解"])

    def test_no_anchor_means_no_verdict(self):
        # 锚点块不在范围内（人工补的卡）就整张放弃，宁可漏报也不把整页标题都算上。
        blocks = [block(1, "读一读", 100), block(2, "数学理解", 400)]
        self.assertEqual(self.headings(blocks, anchor=None), [])

    def test_malformed_blocks_are_skipped(self):
        blocks = [{"text": "数学理解"}, "不是字典", {"seq": 1, "text": "读一读"},
                  {"seq": 1, "text": "做一做", "page_idx": "x", "bbox": None}]
        self.assertEqual(self.headings(blocks), [])


READING = {
    "stem": "", "options": {}, "type": "free_response", "content_kind": "exercise",
    "figures": {}, "missing_figure": False, "number_seen": 2,
    "figure_descriptions": [], "unclear": False,
}


def snapshot(**changes):
    stem = changes.get("stem") or "2. 计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。"
    base = {
        "id": 1, "number": 2, "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [85, 100, 895, 700]}],
        "candidates": [], "question_type": "free_response",
        "stem": "", "options": {}, "edited": False,
        "source_kind": Question.SourceKind.EXERCISE, "source_anchor_seq": 128,
        "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
        "is_book": True,
        "witness": "2.计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。\n读一读\n方根符号\n开方是最早产生的运算之一",
        "witness_blocks": [
            {"seq": 128, "page_idx": 0, "bbox": [100, 100, 900, 130],
             "text": "2. 计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。", "type": "text"},
            {"seq": 130, "page_idx": 0, "bbox": [100, 400, 300, 430],
             "text": "读一读", "type": "title"},
            {"seq": 131, "page_idx": 0, "bbox": [100, 440, 500, 470],
             "text": "方根符号", "type": "title"},
            {"seq": 132, "page_idx": 0, "bbox": [100, 480, 900, 560],
             "text": "开方是最早产生的运算之一。古埃及人…", "type": "text"},
        ],
    }
    base.update(changes)
    return base


class RangeReachesNextSectionFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem="2. 计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。", **changes):
        reading = {**READING, "stem": stem}
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question", return_value=reading), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url",
                             return_value="data:image/jpeg;base64,x"):
            return pipeline.read_card(snapshot(stem=stem, **changes), self.store)

    def test_a_card_whose_range_reaches_the_next_column_is_not_green(self):
        result = self.run_card()

        self.assertTrue(any("范围往后罩住了" in str(flag) for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_card_that_stops_before_the_column_stays_green(self):
        result = self.run_card(
            witness_blocks=[{"seq": 128, "page_idx": 0, "bbox": [100, 100, 900, 130],
                             "text": "2. 计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。", "type": "text"}],
            witness="2.计算：（1）$\\sqrt{8}$；（2）$\\sqrt{9}$。")

        self.assertFalse(any("范围往后罩住了" in str(flag) for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)