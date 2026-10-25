r"""切题把一道小问吞掉了，成品卷上印成 (1)(3)。

真实案例（绿卡、零旗标）：九上第 1 题 id=1152，页 18 自己印着三条：

    1.（1）已知点（-6，y₁），（-4，y₂）… 试比较 y₁ 与 y₂ 的大小。你是怎么做的？
       （2）已知点（4，y₃），（6，y₄）… 试比较 y₃ 与 y₄ 的大小。
       （3）已知点（-4，y₅），（6，y₆）… 试比较 y₅ 与 y₆ 的大小。

卡上只剩 (1) 和 (3)——(2) 整条没进来。学生拿到卷子一眼就看出来少了题。

两条判据缺一不可，少一条都会教用户忽略这条提示：

* 只看相邻编号会误伤教材并组的卡（(1)(2)…(1)(3)，四张样本全是这种），所以要再要求
  那个号在整段正文里一次都没出现过；
* 北师大版的例题与习题**题面只印单数小题**——例 1 的题面就是「(1)…(3)」，(2)(4) 只出现
  在下面的「解」里。14 张跳号样本里有 13 张属于这一类，卡是忠实的，不该报。
"""
from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, textnorm
from .models import Question


def block(seq, text, y, page=0):
    return {"seq": seq, "text": text, "page_idx": page, "bbox": [100, y, 900, y + 30],
            "type": "text"}


STEM_1152 = ("（1）已知点（$-6$，$y_1$），（$-4$，$y_2$）在反比例函数 $y=-\\dfrac{6}{x}$ 的图象上，"
             "试比较 $y_1$ 与 $y_2$ 的大小。你是怎么做的？\n\n"
             "（3）已知点（$-4$，$y_5$），（$6$，$y_6$）在反比例函数 $y=-\\dfrac{6}{x}$ 的图象上，"
             "试比较 $y_5$ 与 $y_6$ 的大小。")


class MissingSubQuestionTests(SimpleTestCase):
    def missing(self, stem, blocks):
        return textnorm.missing_sub_questions(stem, blocks)

    def test_the_real_case_names_the_swallowed_sub_question(self):
        blocks = [
            block(1, "1.（1）已知点（-6，y1），（-4，y2）在反比例函数 y=-6/x 的图象上，"
                     "试比较 y1 与 y2 的大小。你是怎么做的？", 100),
            block(2, "（2）已知点（4，y3），（6，y4）在反比例函数 y=-6/x 的图象上，"
                     "试比较 y3 与 y4 的大小。", 160),
            block(3, "（3）已知点（-4，y5），（6，y6）在反比例函数 y=-6/x 的图象上，"
                     "试比较 y5 与 y6 的大小。", 220),
        ]
        self.assertEqual(self.missing(STEM_1152, blocks), [2])

    def test_textbook_prints_only_odd_sub_questions_is_left_alone(self):
        # 例 1：题面只有 (1)(3)，(2)(4) 只印在下面的「解」里——书上就这么印的。
        blocks = [
            block(1, "例 1  计算：（1）√5 · √10；（3）√7/√3；", 100),
            block(2, "解：（1）√5 · √10 = √50 = 5√2；（2）3√2 · 2√6 = 12√3；"
                     "（3）√7/√3 = √21/3；（4）2√6 ÷ 4√3 = √2/2；", 180),
        ]
        self.assertEqual(self.missing("计算：\n\n（1）$\\sqrt{5} \\cdot \\sqrt{10}$；\n\n"
                                     "（3）$\\dfrac{\\sqrt{7}}{\\sqrt{3}}$；", blocks), [])

    def test_textbook_prints_only_odd_sub_questions_without_a_solution_block(self):
        blocks = [block(1, "1. 计算：（1）√96 · √(1/8)；", 100),
                  block(2, "（3）√6 · √18；", 150)]
        self.assertEqual(self.missing("计算：\n\n（1）$\\sqrt{96} \\cdot \\sqrt{\\dfrac{1}{8}}$；\n\n"
                                     "（3）$\\sqrt{6} \\cdot \\sqrt{18}$；", blocks), [])

    def test_merged_textbook_card_with_two_groups_is_not_a_gap(self):
        # 教材把「做一做」和例题并成一张：(1)(2)…(1)(3)。(2) 在正文里出现过，不是缺口。
        stem = ("点 $A(2, 4)$ 在二次函数 $y=x^2$ 的图象上吗？\n\n"
                "（1）写出点 $A$ 关于 $x$ 轴的对称点 $B$ 的坐标；\n\n"
                "（2）写出点 $B$ 关于 $y$ 轴的对称点 $C$ 的坐标；\n\n"
                "（1）判断下列各式是否成立：$\\sqrt{2}=2$；（ ）\n\n"
                "（3）请说明你所发现的规律。")
        blocks = [block(1, stem, 100)]
        self.assertEqual(self.missing(stem, blocks), [])

    def test_consecutive_sub_questions_are_not_reported(self):
        stem = "解下列方程：\n（1）$x^2-10x+25=7$；\n（2）$x^2-3x=-1$；"
        blocks = [block(1, stem, 100)]
        self.assertEqual(self.missing(stem, blocks), [])

    def test_a_number_absent_from_both_stem_and_printed_range_is_not_reported(self):
        # 范围里根本没印这个号：不是切题吞的，是书上就没有。
        stem = "计算：\n（1）$\\sqrt{5} \\cdot \\sqrt{10}$；\n（3）$\\dfrac{\\sqrt{7}}{\\sqrt{3}}$；"
        blocks = [block(1, "计算：（1）√5 · √10；", 100), block(2, "（3）√7/√3；", 150)]
        self.assertEqual(self.missing(stem, blocks), [])

    def test_no_blocks_means_no_verdict(self):
        # 没有块就没有纸上印着的证据，整张放弃——宁可漏报也不凭空报错。
        self.assertEqual(self.missing(STEM_1152, []), [])
        self.assertEqual(self.missing(STEM_1152, ["不是字典", None]), [])

    def test_several_swallowed_sub_questions_are_all_named(self):
        stem = "解下列方程：\n（1）$x(x-14)=0$；\n（3）$x^2=x+56$；\n（5）$4x^2-45=31x$；\n（7）$(x+8)(x+1)=-12$；"
        blocks = [block(1, "解下列方程：（1）x(x-14)=0；", 100),
                  block(2, "（2）2x^2+4x=x+2；", 150),
                  block(3, "（3）x^2=x+56；", 200),
                  block(4, "（4）x^2-4x+3=0；", 250),
                  block(5, "（5）4x^2-45=31x；", 300),
                  block(6, "（6）x^2+2x-3=0；", 350),
                  block(7, "（7）(x+8)(x+1)=-12；", 400)]
        self.assertEqual(self.missing(stem, blocks), [2, 4, 6])

    def test_a_lone_parenthesised_number_that_is_not_a_sub_question_is_ignored(self):
        stem = "如图，正方形 $ABCD$ 中，点 $P(2,-1)$ 在对角线上，求 $AP$ 的长；\n\n" \
               "（2）求 $BP$。"
        blocks = [block(1, stem, 100)]
        self.assertEqual(self.missing(stem, blocks), [])


READING = {
    "stem": "", "options": {}, "type": "free_response", "content_kind": "exercise",
    "figures": {}, "missing_figure": False, "number_seen": 1,
    "figure_descriptions": [], "unclear": False,
}


def snapshot(**changes):
    base = {
        "id": 1, "number": 1, "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [85, 100, 895, 700]}],
        "candidates": [], "question_type": "free_response",
        "stem": "", "options": {}, "edited": False,
        "source_kind": Question.SourceKind.EXERCISE, "source_anchor_seq": 257,
        "segmentation_flags": [], "body_mode": "text", "start_source": "mineru",
        "is_book": True,
        "witness": "1.（1）已知点（-6，y1）…\n（2）已知点（4，y3）…\n（3）已知点（-4，y5）…",
        "witness_blocks": [
            {"seq": 257, "page_idx": 0, "bbox": [100, 100, 900, 130],
             "text": "1.（1）已知点（-6，y1），（-4，y2）在反比例函数 y=-6/x 的图象上，"
                     "试比较 y1 与 y2 的大小。你是怎么做的？", "type": "text"},
            {"seq": 258, "page_idx": 0, "bbox": [100, 140, 900, 170],
             "text": "（2）已知点（4，y3），（6，y4）在反比例函数 y=-6/x 的图象上，"
                     "试比较 y3 与 y4 的大小。", "type": "text"},
            {"seq": 259, "page_idx": 0, "bbox": [100, 180, 900, 210],
             "text": "（3）已知点（-4，y5），（6，y6）在反比例函数 y=-6/x 的图象上，"
                     "试比较 y5 与 y6 的大小。", "type": "text"},
        ],
    }
    base.update(changes)
    return base


class SwallowedSubQuestionFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem=STEM_1152, **changes):
        reading = {**READING, "stem": stem}
        with patch.object(pipeline.readers, "primary_engine", return_value=object()), \
                patch.object(pipeline.readers, "read_question", return_value=reading), \
                patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                patch.object(pipeline.imaging, "jpeg_data_url",
                             return_value="data:image/jpeg;base64,x"):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_card_that_lost_a_sub_question_is_not_green(self):
        result = self.run_card()
        self.assertTrue(any("小问编号跳过" in str(flag) for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_card_whose_sub_questions_are_all_there_stays_green(self):
        stem = ("（1）已知点（$-6$，$y_1$），（$-4$，$y_2$）在反比例函数 $y=-\\dfrac{6}{x}$ 的图象上，"
                "试比较 $y_1$ 与 $y_2$ 的大小。\n\n"
                "（2）已知点（$4$，$y_3$），（$6$，$y_4$）在反比例函数 $y=-\\dfrac{6}{x}$ 的图象上，"
                "试比较 $y_3$ 与 $y_4$ 的大小。")
        result = self.run_card(stem=stem)
        self.assertFalse(any("小问编号跳过" in str(flag) for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_the_range_overrun_warning_still_fires_alongside_this_one(self):
        blocks = list(snapshot()["witness_blocks"]) + [
            {"seq": 262, "page_idx": 0, "bbox": [100, 500, 300, 530],
             "text": "数学理解", "type": "title"}]
        result = self.run_card(witness_blocks=blocks)
        self.assertTrue(any("小问编号跳过" in str(flag) for flag in result["flags"]), result["flags"])
        self.assertTrue(any("范围往后罩住了" in str(flag) for flag in result["flags"]), result["flags"])
