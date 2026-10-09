"""离线测试：虚构原卷 + 模拟模型输出，不产生任何付费调用。"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from . import figure_policy, library, pipeline, readers, segment
from .models import Block, ImportChunk, Paper, PublishedQuestion, Question
from .textnorm import canon, clean_stem, same_reading


def block(seq, page, bbox, text, kind="text"):
    return {"seq": seq, "type": kind, "page_idx": page, "bbox": bbox, "text": text}


PAGES = [{"page_idx": 0, "width": 842, "height": 595}, {"page_idx": 1, "width": 842, "height": 595}]


def two_column_paper():
    """两栏试卷：第 3 题从左栏底部跨到右栏顶部，第 4 题开头被 MinerU 误读成"4"以外的样子。"""
    return [
        block(0, 0, [60, 30, 460, 60], "高一数学月考", "text"),
        block(1, 0, [60, 70, 460, 90], "注意事项：1. 答卷前，考生务必填写姓名。"),
        block(2, 0, [60, 100, 470, 120], "一、选择题：本题共 3 小题"),
        block(3, 0, [60, 130, 470, 200], "1. 已知集合 A={1,2}，则（ ）A. 1 B. 2"),
        block(4, 0, [60, 330, 470, 400], "2. 如图，在三角形 ABC 中，求角 A"),
        block(5, 0, [300, 410, 460, 500], "", "image"),
        block(6, 0, [60, 800, 470, 900], "3. （10分）已知函数 f(x)=x^2，"),
        block(7, 0, [540, 40, 940, 80], "(1) 求 f(2)；(2) 求 f(x) 的最小值。"),
        block(8, 0, [540, 90, 700, 180], "", "image"),
        block(9, 0, [540, 200, 950, 230], "二、解答题：本题共 2 小题"),
        block(10, 0, [540, 250, 950, 300], "5. (12分) 已知数列 {a_n}"),
        block(11, 0, [540, 600, 950, 640], "6. (12分) 如图，四棱锥"),
        block(12, 0, [480, 960, 520, 980], "第1页", "page_number"),
    ]


class SegmentTests(TestCase):
    def test_cross_column_question_keeps_its_continuation(self):
        result = segment.segment(PAGES[:1], two_column_paper())
        numbers = [q["number"] for q in result["questions"]]
        self.assertEqual(numbers, [1, 2, 3, 5, 6])  # 说明里的"1. 答卷前"不算题
        q3 = next(q for q in result["questions"] if q["number"] == 3)
        self.assertEqual(len(q3["regions"]), 2)
        left, right = q3["regions"]
        self.assertLess(left["bbox"][0], 500)
        self.assertGreaterEqual(right["bbox"][0], 500)
        # 右栏续题止于"二、解答题"标题，不含标题。
        self.assertLessEqual(right["bbox"][3], 200)
        self.assertEqual([f["seq"] for f in q3["figure_candidates"]], [8])
        self.assertEqual(q3["question_type"], "single_choice")
        q5 = next(q for q in result["questions"] if q["number"] == 5)
        self.assertEqual(q5["question_type"], "free_response")
        self.assertEqual([(n, a.number) for n, a in result["missing"]], [(4, 3)])

    def test_national_paper_elective_headings_end_the_previous_question(self):
        # 2024 全国甲卷第 21–23 题：选考说明和“[选修 4-4]”曾被截进第 21 题。
        blocks = [
            block(0, 0, [60, 40, 900, 70], "三、解答题：共 70 分．"),
            block(1, 0, [60, 80, 900, 110], "（一）必考题：共 60 分．"),
            block(2, 0, [60, 120, 900, 220], "21．已知函数 f(x)=(1-ax)ln(1+x)-x．"),
            block(3, 0, [60, 230, 900, 270], "（二）选考题：共 10 分．请考生在第 22、23 题中任选一题作答．"),
            block(4, 0, [60, 280, 900, 300], "[选修 4-4：坐标系与参数方程]"),
            block(5, 0, [60, 310, 900, 400], "22．在平面直角坐标系 xOy 中，曲线 C 的极坐标方程为 ρ=ρcosθ+1．"),
            block(6, 0, [60, 410, 900, 430], "[选修 4-5：不等式选讲]"),
            block(7, 0, [60, 440, 900, 520], "23．已知实数 a，b 满足 a+b≥3．"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        questions = {q["number"]: q for q in result["questions"]}
        self.assertEqual(sorted(questions), [21, 22, 23])
        self.assertLessEqual(questions[21]["regions"][-1]["bbox"][3], 230)
        self.assertLessEqual(questions[22]["regions"][-1]["bbox"][3], 410)
        # The major section still names the cards.
        self.assertTrue(all(q["section"].startswith("三、解答题") for q in questions.values()))
        self.assertTrue(all(q["question_type"] == "free_response" for q in questions.values()))

    def test_previous_option_sharing_a_block_with_the_next_number_stays_with_its_question(self):
        # 陈毅初三照片卷：MinerU 把第 9 题的“D. 3”和第 10 题合成一个框。
        blocks = [
            block(39, 0, [535, 549, 904, 601], "9. 如图, 在菱形 $ABCD$ 中, $AB=13$"),
            block(40, 0, [537, 607, 562, 623], "A. 6"),
            block(43, 0, [537, 654, 561, 669], "C. 4"),
            block(46, 0, [537, 680, 911, 739], "D. 3\n10. 如图, $\\triangle ABC$ 中, $\\angle ACB=90^{\\circ}$"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        questions = {q["number"]: q for q in result["questions"]}
        self.assertGreaterEqual(questions[9]["regions"][-1]["bbox"][3], 696)   # D. 3 is inside
        self.assertLessEqual(questions[10]["regions"][0]["bbox"][1], 704)
        # The photo's first line of question 10 rises to about 682 at its right
        # end (“且 AE=4，BD=6，分别连”); the estimated start leaves room for it.
        self.assertLessEqual(questions[10]["regions"][0]["bbox"][1], 684)

    def test_crop_stays_below_a_section_heading_printed_right_above(self):
        # shengli7 #16: the padding above “16.” reached into the heading line and
        # one reader copied “三、解答题（共 10 小题 共 90 分）” into the question.
        blocks = [
            block(50, 0, [36, 420, 431, 440], "14. 计算 $1+1$ 的值."),
            block(51, 0, [36, 445, 431, 461], "15. 若 $|a|=3$，则 a-b="),
            block(52, 0, [36, 466, 332, 482], "三、解答题（共10小题共90分）"),
            block(53, 0, [33, 483, 579, 542], "16.（6分）小毅设计了某个产品的包装盒(如图所示)."),
            block(57, 0, [36, 600, 835, 679], "17.（6分）把下列各数填入它所属的集合内"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        questions = {q["number"]: q for q in result["questions"]}
        self.assertGreaterEqual(questions[16]["regions"][0]["bbox"][1], 482)
        self.assertLessEqual(questions[16]["regions"][0]["bbox"][1], 484)

    def test_a_line_running_past_the_column_split_keeps_its_last_character(self):
        regions = [{"page_idx": 0, "bbox": [53.0, 93.0, 482.0, 275.0]}]
        blocks = [
            block(36, 0, [75, 97, 490, 147], "15. 如图 Rt△ABC，图中阴影部分在数学史上称为“希波克拉底"),
            block(50, 0, [515, 123, 766, 149], "右栏的文字"),
            block(51, 0, [470, 200, 530, 230], "", "image"),
        ]
        widened = segment._cover_own_lines(regions, blocks)
        self.assertEqual(widened[0]["bbox"], [53.0, 93.0, 490.0, 275.0])
        # Never more than a few units: a block reaching far into the other column is not followed.
        far = segment._cover_own_lines(regions, [block(1, 0, [75, 97, 560, 147], "很宽的一行")])
        self.assertEqual(far[0]["bbox"][2], 482.0)

    def test_a_number_glued_to_the_stem_fills_its_gap(self):
        # 胜利初四月考第 9 题：MinerU 读成“9如图，在△ABC中…”，没有点也没有空格。
        blocks = [
            block(0, 0, [529, 100, 922, 130], "7. 抛物线 y=x² 向左平移1个单位长度"),
            block(1, 0, [529, 373, 922, 392], "8.图为某拦河坝改造前后河床的横断面示意图"),
            block(2, 0, [527, 470, 900, 490], "3个数中最大的是多少"),
            block(3, 0, [527, 579, 921, 607], "9如图，在 $\\triangle ABC$ 中 $\\angle B = 45^{\\circ}$"),
            block(4, 0, [527, 779, 894, 852], "10.二次函数 y=ax²+bx+c 的图象如图所示"),
        ]
        layout, starts = segment.analyse(PAGES[:1], blocks)
        self.assertEqual([(s.number, s.y) for s in starts], [(7, 100), (8, 373), (9, 579), (10, 779)])

    def test_suffix_repair(self):
        blocks = [block(i, 0, [60, 50 + 60 * i, 470, 80 + 60 * i], f"{n}. 题目{n}")
                  for i, n in enumerate([21, 22, 3, 24])]
        layout, starts = segment.analyse(PAGES[:1], blocks)
        self.assertEqual([s.number for s in starts], [21, 22, 23, 24])
        self.assertEqual(starts[2].source, "repaired")

    def test_missing_leading_one_is_repaired_from_existing_blocks_without_a_model(self):
        blocks = [
            block(6, 0, [250, 261, 597, 290], "每周两练.数学不难"),
            block(7, 0, [250, 291, 597, 315], "9. 1 8", "equation"),
            block(8, 0, [190, 319, 608, 374],
                  "[2026吉林、黑龙江两省十校期中联考]已知全集 U=R，集合 A={x|x>1}"),
            block(9, 0, [216, 374, 515, 392], "(1) 若 m=2，求 A∩B；"),
            block(10, 0, [216, 392, 507, 409], "(2) 若 A∪B=A，求 m 的取值范围；"),
            block(11, 0, [190, 430, 620, 500], "解(1) 当 m=2 时，计算可得。"),
            block(21, 0, [193, 654, 646, 712], "2.[2026湖北期中]下列说法正确的是（ ） A.甲 B.乙"),
            block(22, 1, [190, 100, 646, 160], "3. 已知函数 f(x)=x，求值。"),
        ]

        result = segment.segment(PAGES, blocks)

        self.assertEqual([item.number for item in result["starts"]], [1, 2, 3])
        inferred = result["starts"][0]
        self.assertEqual((inferred.seq, inferred.page, inferred.y, inferred.source), (8, 0, 319, "inferred"))
        self.assertEqual(result["leading"].status, "repaired")
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["questions"][0]["regions"][0]["bbox"][1], 310.0)

    def test_split_source_citation_and_stem_use_the_citation_as_inferred_start(self):
        blocks = [
            block(0, 0, [60, 80, 470, 105], "[2026吉林联考]"),
            block(1, 0, [60, 108, 470, 155], "已知集合 A={1,2}，求 A 的子集个数。"),
            block(2, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(3, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [1, 2, 3])
        self.assertEqual((result["starts"][0].seq, result["starts"][0].y), (0, 80))
        self.assertEqual(result["leading"].status, "repaired")

    def test_leading_scan_does_not_cross_the_nearest_section_heading(self):
        blocks = [
            block(0, 0, [60, 50, 470, 105], "[2026联考]已知旧章节条件，求结果。"),
            block(1, 0, [60, 150, 470, 185], "一、选择题"),
            block(2, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(3, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [2, 3])
        self.assertEqual(result["leading"].status, "none")

    def test_second_section_without_a_first_section_anchor_does_not_pull_old_body_forward(self):
        blocks = [
            block(0, 0, [60, 50, 470, 105], "[2026联考]已知上一章条件，求结果。"),
            block(1, 0, [60, 150, 470, 185], "二、解答题"),
            block(2, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(3, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [2, 3])
        self.assertEqual(result["leading"].status, "none")

    def test_first_question_before_second_section_can_still_be_recovered(self):
        blocks = [
            block(0, 0, [60, 30, 470, 60], "一、选择题"),
            block(1, 0, [60, 80, 470, 135], "已知集合 A={1,2}，其子集个数是（ ） A.2 B.4"),
            block(2, 0, [60, 180, 470, 215], "二、解答题"),
            block(3, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(4, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [1, 2, 3])
        self.assertEqual((result["starts"][0].seq, result["starts"][0].y), (1, 80))

    def test_section_directions_before_first_stem_do_not_consume_the_boundary_evidence(self):
        blocks = [
            block(0, 0, [60, 30, 470, 60], "一、选择题"),
            block(1, 0, [60, 65, 470, 90], "本题共10小题，每小题5分"),
            block(2, 0, [60, 100, 470, 155], "已知集合 A={1,2}，其子集个数是（ ） A.2 B.4"),
            block(3, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(4, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [1, 2, 3])
        self.assertEqual((result["starts"][0].seq, result["starts"][0].y), (2, 100))

    def test_question_shaped_prefix_without_independent_boundary_is_only_suspected(self):
        blocks = [
            block(0, 0, [60, 80, 470, 135], "已知集合 A={1,2}，其子集个数是（ ） A.2 B.4"),
            block(1, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(2, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [2, 3])
        self.assertEqual(result["leading"].status, "suspected")

    def test_material_that_really_starts_at_two_does_not_invent_question_one(self):
        blocks = [
            block(0, 0, [60, 40, 470, 70], "数学练习节选"),
            block(1, 0, [60, 100, 470, 160], "2. 已知 x=2，求 x+1。"),
            block(2, 0, [60, 300, 470, 360], "3. 已知 y=3，求 y+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [2, 3])
        self.assertEqual(result["leading"].status, "none")

    def test_ambiguous_leading_body_warns_but_is_not_synthesized(self):
        blocks = [
            block(0, 0, [60, 60, 470, 100], "已知下面两个条件"),
            block(1, 0, [80, 105, 470, 140], "(1) 条件甲成立"),
            block(2, 0, [60, 300, 470, 360], "2. 已知 y=3，求 y+1。"),
            block(3, 0, [60, 500, 470, 560], "3. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [2, 3])
        self.assertEqual(result["leading"].status, "suspected")
        self.assertIn("可能漏了组首题", result["leading"].message)

    def test_strong_prefix_before_three_is_only_flagged_not_guessed(self):
        blocks = [
            block(0, 0, [60, 60, 470, 120], "[2026联考]已知集合 A，求 A 的子集个数。"),
            block(1, 0, [60, 300, 470, 360], "3. 已知 y=3，求 y+1。"),
            block(2, 0, [60, 500, 470, 560], "4. 已知 z=4，求 z+1。"),
        ]

        result = segment.segment(PAGES[:1], blocks)

        self.assertEqual([item.number for item in result["starts"]], [3, 4])
        self.assertEqual(result["leading"].status, "suspected")

    def test_number_in_middle_of_handwriting_box(self):
        blocks = [
            block(0, 0, [60, 50, 470, 80], "1. 已知 x=1"),
            block(1, 0, [20, 200, 470, 300], "-7x+1=9 2. 直线 3x+4y-2=0 的一个方向向量为"),
            block(2, 0, [60, 400, 470, 430], "3. 已知 y=2"),
        ]
        layout, starts = segment.analyse(PAGES[:1], blocks)
        self.assertEqual([s.number for s in starts], [1, 2, 3])
        self.assertGreater(starts[1].y, 200)


    def test_single_column_pages_with_alternating_margins(self):
        """A3 对折扫描成单页：单页题号靠右（装订边），双页题号靠左——都是单栏，不能当成两栏。"""
        pages = [{"page_idx": 0, "width": 595, "height": 842}, {"page_idx": 1, "width": 595, "height": 842}]
        blocks, seq = [], 0
        for page, x0, numbers in ((0, 166, range(1, 6)), (1, 33, range(6, 11))):
            for i, n in enumerate(numbers):
                blocks.append(block(seq, page, [x0, 100 + i * 150, 980 if page == 0 else 900, 140 + i * 150],
                                    f"{n}. 下列说法正确的是（ ）A. 1 B. 2 C. 3 D. 4"))
                seq += 1
        result = segment.segment(pages, blocks)
        self.assertEqual(result["layout"].splits, {0: [], 1: []})
        self.assertEqual([q["number"] for q in result["questions"]], list(range(1, 11)))
        for q in result["questions"]:
            self.assertTrue(q["regions"], q["number"])
            left, _, right, _ = q["regions"][0]["bbox"]
            self.assertLess(left, 170)
            self.assertGreater(right, 890)

    def test_right_column_with_single_question_is_still_a_column(self):
        blocks = [
            block(0, 0, [50, 80, 480, 240], "23. （10分）小明对笔记本电脑的研究"),
            block(1, 0, [50, 270, 150, 410], "", "image"),
            block(2, 0, [50, 495, 480, 540], "24. （12分）如图，直线 y=kx+2"),
            block(3, 0, [55, 550, 450, 610], "（1）求双曲线的解析式；"),
            block(4, 0, [505, 78, 947, 228], "25. （12分）如图，在平面直角坐标系中"),
            block(5, 0, [523, 253, 627, 413], "", "image"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        self.assertEqual(len(result["layout"].splits[0]), 1)
        q25 = next(q for q in result["questions"] if q["number"] == 25)
        self.assertGreater(q25["regions"][0]["bbox"][0], 480)
        q24 = next(q for q in result["questions"] if q["number"] == 24)
        self.assertLess(q24["regions"][0]["bbox"][2], 520)

    def test_figure_straddling_next_question_is_candidate_for_both(self):
        blocks = [
            block(0, 0, [60, 100, 470, 130], "1. 如图是正方体的展开图，相对面上的字是（ ）"),
            block(1, 0, [380, 150, 470, 260], "", "image"),
            block(2, 0, [60, 180, 360, 210], "2. 四位同学画数轴如图所示（ ）"),
            block(3, 0, [60, 400, 470, 430], "3. 已知"),
        ]
        result = segment.segment(PAGES[:1], blocks)
        q1, q2 = result["questions"][:2]
        self.assertEqual([f["seq"] for f in q1["figure_candidates"]], [1])
        self.assertEqual([f["seq"] for f in q2["figure_candidates"]], [1])


class TextTests(TestCase):
    def test_canon_ignores_notation(self):
        self.assertEqual(canon(r"$\dfrac{1}{2}$ ，且 $AB//CD$"), canon(r"\frac12, 且 AB \parallel CD"))
        self.assertEqual(canon(r"$\overrightarrow{a}\cdot\vec{b}$"), canon(r"\vec a \cdot \vec b"))
        self.assertEqual(canon("则 x=（　）"), canon("则 $x=$ ( )"))
        self.assertNotEqual(canon(r"$\frac{1}{2}$"), canon(r"$\frac{1}{3}$"))
        self.assertEqual(canon("（15分）已知"), canon("已知"))

    def test_same_reading_checks_options(self):
        a = {"stem": "已知", "options": {"A": "1", "B": "2"}}
        self.assertTrue(same_reading(a, {"stem": "已知", "options": {"A": "$1$", "B": "2"}}))
        self.assertFalse(same_reading(a, {"stem": "已知", "options": {"A": "1", "B": "3"}}))

    def test_clean_stem(self):
        self.assertEqual(clean_stem("17. （15分）已知向量", 17), "已知向量")

    def test_figure_reference_detector_covers_chinese_and_english_without_substring_matches(self):
        positive = [
            "如图所示，求阴影部分的面积。",
            "由图可知，点 A 在第二象限。",
            "从图可知，甲车先到达终点。",
            "观察下图并回答问题。",
            "根据右图可知，点 A 的坐标是（ ）。",
            "根据表中数据完成计算。",
            "参照右侧三棱柱示意图，求该三棱柱的体积。",
            "函数的图象大致是（　　）",
            "图为河床横断面示意图。",
            "As shown in the figure below, find the value of x.",
            "The diagram above shows a triangular prism.",
            "Refer to the graph on the right.",
            "Use the table below to answer the question.",
            "The map below shows the route.",
            "The drawing is not to scale.",
        ]
        for value in positive:
            with self.subTest(value=value):
                self.assertTrue(figure_policy.cue_matches(value))

        negative = [
            "在图书馆阅读数学书。",
            "比如图书馆距学校2千米。",
            "该方法不如表格法直观。",
            "根据图书资料回答。",
            "Configure the application before use.",
            "A stable solution exists.",
            "Write one paragraph about the result.",
            "Sketch the graph of y=x.",
            "Draw the diagram yourself.",
            "As shown in the equation below, solve for x.",
            "Use the table method.",
            "According to graph theory, a tree has no cycles.",
            "参考图书资料回答问题。",
        ]
        for value in negative:
            with self.subTest(value=value):
                self.assertEqual(figure_policy.cue_matches(value), [])


class StaticAssetTests(TestCase):
    def test_favicon_is_served_as_png(self):
        response = self.client.get("/favicon.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG\r\n\x1a\n"))


class ParallelogramSymbolTests(TestCase):
    def test_square_before_vertex_letters_becomes_parallelogram(self):
        from .textnorm import fix_symbols

        self.assertEqual(fix_symbols(r"如图，在 $\square ABCD$ 中，$E$ 为中点"), "如图，在 ▱ABCD 中，$E$ 为中点")
        self.assertEqual(fix_symbols("在□ABCD中"), "在▱ABCD中")
        self.assertEqual(fix_symbols(r"$\square ABCD \cong \Box EFGH$"), r"$\text{▱}ABCD \cong \text{▱}EFGH$")
        self.assertEqual(fix_symbols(r"在 \square ABCD 中"), "在 ▱ABCD 中")
        self.assertEqual(fix_symbols(r"在 $\text{□}ABCD$ 中"), "在 ▱ABCD 中")
        self.assertEqual(fix_symbols(r"在 $\parallelogram ABCD$ 中"), "在 ▱ABCD 中")
        self.assertEqual(fix_symbols("□$ABCD$"), "▱$ABCD$")
        # 填空用的方框不动
        self.assertEqual(fix_symbols(r"在□内填数：$3\square 5=15$"), r"在□内填数：$3\square 5=15$")

    def test_reading_is_normalized_and_old_cards_are_fixed(self):
        reading = readers.parse_reading("【题干】如图，在$\\square ABCD$中，求证：$AC=BD$\n【题型】解答题", 23)
        self.assertEqual(reading["stem"], "如图，在▱ABCD中，求证：$AC=BD$")
        from importlib import import_module

        from django.apps import apps

        paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="p" * 64)
        question = Question.objects.create(paper=paper, number=23, stem="在 $\\square ABCD$ 中", options={"A": "□ABCD 是菱形"})
        import_module("core.migrations.0003_fix_parallelogram_symbol").forwards(apps, None)
        question.refresh_from_db()
        self.assertEqual((question.stem, question.options), ("在 ▱ABCD 中", {"A": "▱ABCD 是菱形"}))

    def test_followup_migration_fixes_structured_reads_but_preserves_raw(self):
        from importlib import import_module

        from django.apps import apps

        paper = Paper.objects.create(filename="旧识读.pdf", kind="pdf", sha256="r" * 64)
        question = Question.objects.create(
            paper=paper,
            number=5,
            stem="在 ▱ABCD 中",
            read_a={"stem": "在□ABCD中", "options": {"A": r"$\square EFGH$"}, "raw": "模型原文：□ABCD"},
            approved=True,
            approved_content_hash="a" * 64,
        )
        bad_final = Question.objects.create(
            paper=paper,
            number=6,
            stem=r"在 $\square WXYZ$ 中",
            approved=True,
            approved_content_hash="b" * 64,
        )
        migrate = import_module("core.migrations.0005_normalize_parallelogram_readings").forwards
        migrate(apps, None)
        migrate(apps, None)  # 可重复执行不应产生第二次变化
        question.refresh_from_db()
        bad_final.refresh_from_db()
        self.assertEqual(question.read_a["stem"], "在▱ABCD中")
        self.assertEqual(question.read_a["options"]["A"], "▱EFGH")
        self.assertEqual(question.read_a["raw"], "模型原文：□ABCD")
        self.assertTrue(question.approved)  # 只清理审计记录，不改变最终题面
        self.assertEqual(bad_final.stem, "在 ▱WXYZ 中")
        self.assertFalse(bad_final.approved)
        self.assertEqual(bad_final.approved_content_hash, "")


class ParseTests(TestCase):
    def test_parse_reading(self):
        raw = """<think>略</think>```
【题号】9
【题型】单选题
【题干】
下列图象中，是函数图象的是（ ）
【A】A. 图
【B】
【C】$y=\\frac{1}{x}$
【D】无
【配图】1=A, 2=B, 3=无关，4＝C 缺图
```"""
        reading = readers.parse_reading(raw, 9)
        self.assertEqual(reading["stem"], "下列图象中，是函数图象的是（ ）")
        self.assertEqual(reading["options"], {"A": "图", "C": "$y=\\frac{1}{x}$"})
        self.assertEqual(reading["type"], "single_choice")
        self.assertEqual(reading["figures"], {"1": "A", "2": "B", "3": "none", "4": "C"})
        self.assertTrue(reading["missing_figure"])
        self.assertEqual(reading["number_seen"], 9)

    def test_handwritten_choice_in_brackets_is_dropped(self):
        self.assertEqual(readers.drop_filled_choice("直线方程为(A)"), "直线方程为(　)")
        self.assertEqual(readers.drop_filled_choice("则 x=（ B ）"), "则 x=（　）")
        self.assertEqual(readers.drop_filled_choice("正确的是（ACD）。"), "正确的是（　）。")
        self.assertEqual(readers.drop_filled_choice("求 $P(A)$"), "求 $P(A)$")
        self.assertEqual(readers.drop_filled_choice("事件 P(A)"), "事件 P(A)")

    def test_options_repeated_in_stem_are_removed(self):
        reading = readers.parse_reading("**【题干】**：已知函数\nA. 1\nB. 2\n【A】1\n【B】2", 3)
        self.assertEqual(reading["stem"], "已知函数")
        self.assertEqual(reading["options"], {"A": "1", "B": "2"})

    def test_parse_requires_stem(self):
        with self.assertRaises(ValueError):
            readers.parse_reading("没有格式", 1)

    def test_band(self):
        self.assertEqual(readers.parse_band("【刻度】07"), 7)
        self.assertIsNone(readers.parse_band("【刻度】无"))

    def test_generated_figure_descriptions_are_not_options(self):
        reading = readers.parse_reading(
            "【题型】单选题\n【题干】选出正确的数轴（ ）[图：数轴上有 [?]、1、2]\n"
            "【A】[图：数轴上有 1、2、3]\n"
            "【B】【图片: 函数图象经过 [?]】\n"
            "【C】（图示：一个三角形）\n"
            "【D】图\n【配图】1=A, 2=B, 3=C",
            4,
        )
        self.assertEqual(reading["options"], {"D": "图"})
        self.assertEqual(reading["stem"], "选出正确的数轴（ ）")
        self.assertEqual(reading["figure_descriptions"], ["A", "B", "C", "stem"])
        self.assertFalse(reading["unclear"])

    def test_figure_description_filter_is_conservative(self):
        for value in (
            "[图：数轴]", "【图片: 函数图象】", "（示意图：三角形）", "[表格：星期与产量]",
            "（图形：呈阶梯状排列的六个正方形）",
            "数轴上标有点，标号依次为 1, 2, 3, 4, 5",
            "数轴上标有点，标号依次为 $-2$、$-1$、$0$、$1$、$2$",
        ):
            self.assertTrue(readers.is_figure_description(value), value)
        for value in (
            "如图，点 A 在数轴上", "图1中的阴影部分", "[图1]", "$[a,b]$", "图",
            "数轴上点 A 表示 1", "向右", "甲",
        ):
            self.assertFalse(readers.is_figure_description(value), value)

    def test_all_reader_prompts_forbid_turning_figures_into_text(self):
        prompts = [
            readers.transcribe_prompt(4, with_figures=True),
            readers.transcribe_prompt(4, with_figures=False),
            readers.arbiter_prompt(4, {"stem": "题干"}, {"stem": "题干"}),
        ]
        for prompt in prompts:
            self.assertIn("不得改写成", prompt)
            self.assertIn("某个选项只有图时", prompt)
            self.assertIn("表格", prompt)
            self.assertIn("Markdown", prompt)

    def test_markdown_table_can_be_removed_without_touching_surrounding_text(self):
        source = "已知数据如下：\n\n| 星期 | 一 | 二 |\n|---|---|---|\n| 增减 | +5 | -2 |\n\n求总数。"
        cleaned, removed = readers.strip_markdown_tables(source)
        self.assertTrue(removed)
        self.assertEqual(cleaned, "已知数据如下：\n\n求总数。")
        untouched, removed = readers.strip_markdown_tables("若 $|x|=2$，求 $x$。")
        self.assertFalse(removed)
        self.assertEqual(untouched, "若 $|x|=2$，求 $x$。")

    def test_migration_cleans_drafts_but_preserves_human_edits_and_published_snapshot(self):
        from importlib import import_module

        from django.apps import apps

        paper = Paper.objects.create(filename="图片选项.pdf", kind="pdf", sha256="f" * 64)
        table_text = "根据下表回答：\n\n| 星期 | 一 |\n|---|---|\n| 增减 | +5 |\n\n求总数。"
        question = Question.objects.create(
            paper=paper,
            number=4,
            question_type="single_choice",
            stem=table_text,
            options={
                "A": "数轴上标有点，标号依次为 1, 2, 3, 4, 5",
                "B": "向右",
                "C": "数轴上标有点，标号依次为 -2, -1, 0, 1, 2",
                "D": "数轴上标有点，标号依次为 $-2$、$-1$、$0$、$1$、$2$",
            },
            figures=[
                {"slot": "A", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"},
                {"slot": "B", "page_idx": 0, "bbox": [2, 3, 4, 5], "source": "auto"},
                {"slot": "stem", "page_idx": 0, "bbox": [5, 6, 7, 8], "source": "auto"},
            ],
            read_a={"options": {}, "figures": {"1": "A", "2": "B", "3": "stem"},
                    "raw": "【A】\\n【B】\\n【配图】1=A,2=B,3=题干"},
            state="green",
            approved=True,
            approved_content_hash="a" * 64,
        )
        human = Question.objects.create(
            paper=paper,
            number=5,
            options={"A": "[图：这是人工保留的说明]"},
            figures=[{"slot": "A", "page_idx": 0, "bbox": [1, 2, 3, 4], "source": "auto"}],
            read_a={"options": {}, "figures": {"1": "A"}, "raw": "原始记录"},
            edited=True,
            text_source="human",
            state="green",
            approved=True,
            approved_content_hash="b" * 64,
        )
        table_only = Question.objects.create(
            paper=paper,
            number=6,
            stem=table_text,
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [5, 6, 7, 8], "source": "auto"}],
            read_a={"options": {}, "figures": {"1": "stem"}, "raw": "原始表格"},
            state="green",
            approved=True,
            approved_content_hash="c" * 64,
        )
        publication = PublishedQuestion.objects.create(
            question=table_only,
            paper=paper,
            source_filename=paper.filename,
            number=6,
            question_type="free_response",
            version=1,
            content={"stem": table_text},
            content_hash="d" * 64,
            search_text=table_text,
        )
        migrate = import_module("core.migrations.0006_remove_ai_figure_descriptions").forwards
        migrate(apps, None)
        migrate(apps, None)
        question.refresh_from_db()
        human.refresh_from_db()
        table_only.refresh_from_db()
        publication.refresh_from_db()
        self.assertEqual(question.options, {"B": "向右"})
        self.assertEqual(question.stem, "根据下表回答：\n\n求总数。")
        self.assertEqual(question.read_a["raw"], "【A】\\n【B】\\n【配图】1=A,2=B,3=题干")
        self.assertFalse(question.approved)
        self.assertEqual(question.approved_content_hash, "")
        self.assertEqual(question.state, "yellow")
        self.assertTrue(any("重复转写" in flag for flag in question.flags))
        self.assertTrue(any("表格内容" in flag for flag in question.flags))
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, question.flags)
        self.assertEqual(human.options, {"A": "[图：这是人工保留的说明]"})
        self.assertTrue(human.approved)
        self.assertEqual(table_only.stem, "根据下表回答：\n\n求总数。")
        self.assertFalse(table_only.approved)
        self.assertTrue(any("表格内容" in flag for flag in table_only.flags))
        self.assertFalse(any("AI 生成的图片说明" in flag for flag in table_only.flags))
        self.assertEqual(publication.content["stem"], table_text)


def fake_page_pdf(path: Path, pages: int = 1) -> None:
    images = []
    for index in range(pages):
        image = Image.new("RGB", (1684, 1190), "white")
        draw = ImageDraw.Draw(image)
        for row in range(20):
            draw.rectangle((120, 80 + row * 52, 900, 96 + row * 52), fill="black")
            draw.rectangle((1080, 80 + row * 52, 1600, 96 + row * 52), fill="black")
        images.append(image)
    images[0].save(path, "PDF", save_all=True, append_images=images[1:])


class ScriptedChat:
    """按题号返回预设的模型输出；记录调用次数。"""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, engine, prompt, images, max_tokens=3000):
        import re
        # Prefer the explicit candidate label.  The figure instructions also
        # contain examples such as “第14题图”, which are not this card's id.
        match = re.search(r"(?:候选显示编号为|显示编号)\s*(\d+)", prompt)
        if match is None:
            match = re.search(r"第\s*(\d+)\s*题", prompt)
        # The spot check names no card; scripts key it by the first card number.
        number = int(match.group(1)) if match else 1
        kind = "locate" if "横带" in prompt else "arbiter" if "读法甲" in prompt else \
            "spotcheck" if "每一处空位上印的是甲还是乙" in prompt else \
            "classify" if "上次没有判断编号" in prompt else "a" if "蓝色框" in prompt else "b"
        self.calls.append((kind, number, engine.provider))
        value = self.answers.get((kind, number), self.answers.get(("*", number), ""))
        if isinstance(value, Exception):
            raise value
        return value(prompt) if callable(value) else value


def tagged(stem, options=None, figures="无", number=None):
    lines = ([f"【题号】{number}"] if number else []) + ["【题型】单选题" if options else "【题型】解答题", "【题干】", stem]
    for key, value in (options or {}).items():
        lines.append(f"【{key}】{value}")
    lines += [f"【配图】{figures}"]
    return "\n".join(lines)


class PipelineTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.settings_override = override_settings(DATA_ROOT=self.temp)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test", "SILICONFLOW_API_KEY": "test2",
                                             "MINERU_TOKEN": "tok"})
        env.start()
        self.addCleanup(env.stop)
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="x" * 64)
        folder = self.temp / str(self.paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        self.paper.source_path = str(folder / "source.pdf")
        self.paper.pages = PAGES[:1]
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save()
        Block.objects.bulk_create([Block(paper=self.paper, **b) for b in two_column_paper()])

    def run_paper(self, answers):
        chat = ScriptedChat(answers)
        with mock.patch.object(readers, "chat", chat):
            pipeline.process_paper(self.paper)
        self.paper.refresh_from_db()
        return chat

    def read_policy_card(self, stem, *, figure_role="无", candidates=True):
        primary = readers.parse_reading(tagged(stem, figures=figure_role), 9)
        checker = readers.parse_reading(tagged(stem), 9)
        snapshot = {
            "id": 999,
            "number": 9,
            "regions": [{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            "candidates": ([{
                "label": "1", "seq": 5, "page_idx": 0, "bbox": [300, 410, 460, 500],
            }] if candidates else []),
            "question_type": "free_response",
        }
        store = pipeline.PageStore(self.paper)
        with mock.patch.object(
                readers, "read_question",
                side_effect=lambda _engine, _url, _number, with_figures: primary if with_figures else checker,
        ) as read_mock, \
                mock.patch.object(readers, "arbitrate") as arbitrate_mock:
            result = pipeline.read_card(snapshot, store)
        return result, read_mock, arbitrate_mock

    def test_primary_reads_once_without_starting_a_checker(self):
        primary = readers.parse_reading(tagged("计算 $1+1$ 的值。"), 9)
        checker = readers.parse_reading(tagged("计算 $1+1$ 的值。"), 9)
        threads: list[int] = []

        def read(_engine, _url, _number, with_figures):
            threads.append(threading.get_ident())
            return primary if with_figures else checker

        snapshot = {
            "id": 999, "number": 9, "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            "candidates": [], "question_type": "free_response",
        }
        with mock.patch.object(readers, "read_question", side_effect=read), \
                mock.patch.object(readers, "arbitrate") as arbitrate_mock:
            result = pipeline.read_card(snapshot, pipeline.PageStore(self.paper))

        self.assertEqual(result["state"], Question.State.GREEN)
        self.assertEqual(len(threads), 1)
        self.assertEqual(len(set(threads)), 1)
        self.assertEqual(result["read_b"], {"skipped": "disabled"})
        arbitrate_mock.assert_not_called()

    def test_figure_policy_uses_existing_reads_without_extra_api_call_and_excludes_irrelevant_candidate(self):
        result, read_mock, arbitrate_mock = self.read_policy_card(
            "计算 $1+1$ 的值。", figure_role="1=无关",
        )

        # One selected vision read supplies the automatic figure review.
        self.assertEqual(read_mock.call_count, 1)
        arbitrate_mock.assert_not_called()
        self.assertEqual(result["figures"], [])
        self.assertEqual(result["figure_review"]["status"], "auto_excluded")
        self.assertEqual(result["figure_review"]["source"], "automatic")
        self.assertEqual(result["figure_review"]["excluded_count"], 1)

    def test_unclassified_candidate_is_not_silently_deleted(self):
        result, read_mock, _ = self.read_policy_card("计算 $1+1$ 的值。", figure_role="无")
        self.assertEqual(read_mock.call_count, 1)
        self.assertEqual(result["figure_review"]["status"], "conflict")
        self.assertEqual(result["figure_review"]["unclassified_count"], 1)
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_bound_candidate_does_not_hide_a_second_unclassified_candidate(self):
        review = figure_policy.automatic_review(
            stem="如图所示，求角 A。",
            options={},
            candidate_labels={"1", "2"},
            assignments={"1": "stem"},
            figures=[{
                "slot": "stem", "page_idx": 0, "bbox": [10, 20, 30, 40], "source": "auto",
            }],
        )

        self.assertEqual(review["status"], "conflict")
        self.assertEqual(review["unclassified_count"], 1)
        self.assertIn("candidate_unclassified", review["signals"])

    def test_missing_figure_reference_is_blocked_for_chinese_and_english(self):
        for stem in ("如图所示，求角 A。", "As shown in the diagram below, find angle A."):
            with self.subTest(stem=stem):
                result, _, _ = self.read_policy_card(stem, candidates=False)
                self.assertEqual(result["state"], Question.State.YELLOW)
                self.assertIn(figure_policy.FLAG_NO_FIGURE, result["flags"])
                self.assertEqual(result["figure_review"]["status"], "blocked_missing")
                self.assertEqual(result["figure_review"]["source"], "automatic")

    def test_graph_with_stem_figure_is_not_treated_as_four_missing_image_options(self):
        stem = (
            "The graph below shows the temperature during a morning experiment.\n\n"
            "At what time did the temperature first reach 18℃?"
        )
        primary = readers.parse_reading(
            f"【题号】6\n【题型】单选题\n【题干】\n{stem}\n【配图】1=题干",
            6,
        )
        checker = readers.parse_reading(
            f"【题号】6\n【题型】解答题\n【题干】\n{stem}",
            6,
        )
        snapshot = {
            "id": 999,
            "number": 6,
            "regions": [{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            "candidates": [{
                "label": "1", "seq": 5, "page_idx": 0, "bbox": [300, 410, 460, 500],
            }],
            "question_type": "unknown",
        }
        with mock.patch.object(
                readers, "read_question",
                side_effect=lambda _engine, _url, _number, with_figures: primary if with_figures else checker,
        ) as read_mock, mock.patch.object(readers, "arbitrate") as arbitrate_mock:
            result = pipeline.read_card(snapshot, pipeline.PageStore(self.paper))

        self.assertEqual(read_mock.call_count, 1)
        arbitrate_mock.assert_not_called()
        self.assertEqual([figure["slot"] for figure in result["figures"]], ["stem"])
        self.assertEqual(result["figure_review"]["status"], "blocked_missing")
        self.assertEqual(result["figure_review"]["cue_matches"], ["graph below"])
        self.assertIn(figure_policy.FLAG_UNFOUND_FIGURE, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

        self.assertEqual(
            figure_policy.missing_choice_figure_slots(
                kind="single_choice", options={}, figures=[], readings=[primary, checker],
            ),
            {"A", "B", "C", "D"},
        )

    def test_numeric_statement_list_is_not_invented_as_four_missing_image_options(self):
        stem = (
            "下列哪一组中的函数 $f(x)$ 与 $g(x)$ 是同一个函数？\n"
            "(1) $f(x)=x-1$，$g(x)=x^2/x-1$；\n"
            "(2) $f(x)=x^2$，$g(x)=(\\sqrt{x})^4$；\n"
            "(3) $f(x)=x^2$，$g(x)=\\sqrt[3]{x^6}$。"
        )
        common = {
            "stem": stem, "options": {}, "content_kind": "exercise",
            "figures": {}, "missing_figure": False,
            "number_seen": 2, "figure_descriptions": [], "unclear": False,
        }
        primary = {**common, "type": "single_choice", "raw": "primary raw audit"}
        checker = {**common, "type": "free_response", "raw": "checker raw audit"}
        snapshot = {
            "id": 998, "number": 2, "group_id": None,
            "regions": [{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            "candidates": [], "question_type": "single_choice",
            "source_kind": Question.SourceKind.EXERCISE,
        }
        with mock.patch.object(
                readers, "read_question",
                side_effect=lambda _engine, _url, _number, with_figures, **_kwargs:
                primary if with_figures else checker,
        ), mock.patch.object(readers, "arbitrate") as arbitrate_mock:
            result = pipeline.read_card(snapshot, pipeline.PageStore(self.paper))

        arbitrate_mock.assert_not_called()
        self.assertEqual(result["question_type"], "single_choice")
        self.assertEqual(result["state"], Question.State.YELLOW)
        self.assertTrue(any("选择题没有读出完整选项" in flag for flag in result["flags"]))
        # The one saved read retains the model's original result for a human to check.
        self.assertEqual(result["read_a"]["type"], "single_choice")
        self.assertEqual(result["read_a"]["raw"], "primary raw audit")
        self.assertEqual(result["read_b"], {"skipped": "disabled"})

    def test_numeric_type_normalisation_does_not_touch_image_choice_questions(self):
        final = {"stem": "(1) 甲图；(2) 乙图。", "options": {}}
        readings = [{"type": "single_choice"}, {"type": "free_response"}]

        self.assertEqual(
            pipeline._normalise_unlabelled_numeric_choice_type(
                "single_choice", final=final, readings=readings,
                candidates=[{"label": "1"}], figures=[],
            ),
            "single_choice",
        )

    def test_printed_figure_without_text_reference_is_a_review_conflict(self):
        result, _, _ = self.read_policy_card("求阴影部分的面积。", figure_role="1=题干")
        self.assertEqual([figure["slot"] for figure in result["figures"]], ["stem"])
        self.assertEqual(result["state"], Question.State.YELLOW)
        self.assertEqual(result["figure_review"]["status"], "conflict")
        self.assertEqual(result["figure_review"]["source"], "automatic")

    def test_missing_checker_key_does_not_affect_primary_reading(self):
        primary = readers.Engine("minimax", readers.MINIMAX_MODEL)
        primary_reading = readers.parse_reading(tagged("计算 $1+1$ 的值。"), 9)
        snapshot = {
            "id": 999,
            "number": 9,
            "regions": [{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            "candidates": [],
            "question_type": "free_response",
        }
        store = pipeline.PageStore(self.paper)

        with mock.patch.object(readers, "primary_engine", return_value=primary), \
                mock.patch.object(readers, "checker_engine", return_value=None) as checker_mock, \
                mock.patch.object(readers, "read_question", return_value=primary_reading) as read_mock, \
                mock.patch.object(readers, "arbitrate") as arbitrate_mock:
            result = pipeline.read_card(snapshot, store)

        read_mock.assert_called_once_with(primary, mock.ANY, 9, with_figures=True)
        arbitrate_mock.assert_not_called()
        checker_mock.assert_not_called()
        self.assertEqual(result["stem"], "计算 $1+1$ 的值。")
        self.assertEqual(result["text_source"], "single")
        self.assertEqual(result["state"], Question.State.GREEN)
        self.assertEqual(result["read_b"], {"skipped": "disabled"})
        self.assertEqual(result["flags"], [])

    def test_reread_never_removes_a_manually_selected_figure(self):
        first_key = "0:300,410,460,500"
        ignored_key = "0:500,410,640,500"
        manual = {
            "slot": "stem", "page_idx": 0, "bbox": [300, 410, 460, 500],
            "source": "manual", "candidate_key": first_key,
        }
        question = Question.objects.create(
            paper=self.paper,
            number=9,
            question_type="free_response",
            stem="计算 $1+1$ 的值。",
            regions=[{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            figure_candidates=[
                {"label": "1", "seq": 5, "page_idx": 0, "bbox": [300, 410, 460, 500]},
                {"label": "2", "seq": 6, "page_idx": 0, "bbox": [500, 410, 640, 500]},
            ],
            figures=[manual],
            figure_review={
                "status": "ok", "source": "human", "reason": "配图已经由人工设置",
                "signals": ["manual_figure"], "cue_matches": [], "excluded_count": 1,
                "ignored_candidates": [ignored_key], "confirmed_at": "2026-09-27T00:00:00+08:00",
            },
            state=Question.State.WAITING,
        )
        chat = ScriptedChat({
            ("a", 9): tagged("计算 $1+1$ 的值。", figures="1=无关"),
            ("b", 9): tagged("计算 $1+1$ 的值。"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])

        question.refresh_from_db()
        self.assertEqual(question.figures, [manual])
        self.assertEqual(question.figures[0]["source"], "manual")
        self.assertEqual(question.figure_review["status"], "ok")
        self.assertEqual(question.figure_review["source"], "human")
        self.assertEqual(question.figure_review["ignored_candidates"], [ignored_key])
        self.assertEqual(question.figure_review["confirmed_at"], "2026-09-27T00:00:00+08:00")

    def test_reread_refreshes_text_without_overwriting_human_no_figure_decision(self):
        ignored_key = "0:300,410,460,500"
        confirmed = {
            "status": "confirmed_no_figure",
            "source": "human",
            "reason": "已人工确认本题确实无图",
            "signals": ["human_confirmed_no_figure"],
            "cue_matches": [],
            "excluded_count": 1,
            "ignored_candidates": [ignored_key],
            "confirmed_at": "2026-09-27T00:00:00+08:00",
        }
        question = Question.objects.create(
            paper=self.paper,
            number=9,
            question_type="free_response",
            stem="重读前的题干。",
            regions=[{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 300, 480, 520]}],
            figure_candidates=[{
                "label": "1", "seq": 5, "page_idx": 0, "bbox": [300, 410, 460, 500],
            }],
            figures=[],
            figure_review=confirmed,
            state=Question.State.WAITING,
            reread_requested=True,
        )
        chat = ScriptedChat({
            ("a", 9): tagged("重读后的题干。", figures="1=题干"),
            ("b", 9): tagged("重读后的题干。"),
        })
        with mock.patch.object(readers, "chat", chat):
            pipeline.read_questions(self.paper, [question])

        question.refresh_from_db()
        self.assertEqual(question.stem, "重读后的题干。")
        self.assertEqual(question.figures, [])
        self.assertEqual(question.figure_review, confirmed)
        self.assertFalse(question.reread_requested)

    def test_full_pipeline_states(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("a", 1): tagged("已知集合 $A=\\{1,2\\}$，则（ ）", {"A": "1", "B": "2"}),
            ("b", 1): tagged("已知集合 $A = \\{1, 2\\}$ ，则（　）", {"A": "$1$", "B": "2"}),
            ("a", 2): tagged("如图，在三角形 $ABC$ 中，求角 $A$", {"A": "30°", "B": "60°"}, figures="1=题干", number=2),
            ("b", 2): tagged("如图，在三角形 ABC 中，求角 B", {"A": "30°", "B": "60°"}),
            ("arbiter", 2): tagged("如图，在三角形 $ABC$ 中，求角 $A$", {"A": "$30^\\circ$", "B": "60°"}),
            ("a", 3): tagged("已知函数 $f(x)=x^2$，\n(1) 求 $f(2)$；\n(2) 求最小值。", figures="1=题干", number=4),
            ("b", 3): tagged("已知函数 $f(x)=x^3$，\n(1) 求 $f(2)$；\n(2) 求最小值。"),
            ("arbiter", 3): tagged("已知函数 $f(x)=x^4$，\n(1) 求 $f(2)$；\n(2) 求最小值。"),
            ("a", 5): tagged("已知数列 $\\{a_n\\}$", number=5),
            ("b", 5): readers.ReaderError("硅基流动接口返回 HTTP 500"),
            ("a", 6): readers.ReaderError("MiniMax 接口返回 HTTP 500"),
            ("b", 6): readers.ReaderError("硅基流动接口返回 HTTP 500"),
        }
        chat = self.run_paper(answers)
        self.assertEqual(self.paper.status, Paper.Status.READY, self.paper.error)
        cards = {q.number: q for q in self.paper.questions.all()}
        self.assertEqual(sorted(cards), [1, 2, 3, 5, 6])
        # MinerU remains a local source of text evidence; it no longer triggers
        # a second vision reader or changes the primary read's source label.
        self.assertEqual((cards[1].state, cards[1].text_source), ("green", "single"))
        self.assertEqual(cards[1].read_b, {"skipped": "disabled"})
        self.assertEqual((cards[2].state, cards[2].text_source), ("green", "single"), cards[2].flags)
        self.assertEqual([f["slot"] for f in cards[2].figures], ["stem"])
        # A single read remains the saved text and keeps local number warnings.
        self.assertEqual((cards[3].state, cards[3].text_source), ("yellow", "single"))
        self.assertIn("x^2", cards[3].stem)
        self.assertNotIn(("arbiter", 3, "minimax"), chat.calls)
        self.assertTrue(any("AI 看到的题号是 4" in f for f in cards[3].flags))
        # Question 4 was never found: the card that holds it says so, and the
        # locator got a second look before giving up.
        self.assertIn(pipeline.merged_question_flag(4), cards[3].flags)
        self.assertEqual([call for call in chat.calls if call[0] == "locate"], [("locate", 4, "minimax")] * 2)
        # 1.12.5：截图里露出的邻题号不再提示。题号对得上、没别的信号的卡是绿的，
        # 也不会再冒出"还露出了第 N 题"这种把切对了的题说成切错的提醒。
        self.assertEqual((cards[5].state, cards[5].text_source), ("green", "single"))
        self.assertEqual(cards[5].flags, [])
        self.assertEqual(cards[6].state, "red")
        self.assertTrue(any("没有找到第 4 题" in note or "AI 没有找到第 4 题" in note for note in self.paper.notes))
        # No checker or arbiter call is made for any newly read card.
        self.assertFalse(any(kind in {"b", "arbiter"} for kind, _number, _provider in chat.calls))
        self.assertIn(("a", 1, "minimax"), chat.calls)

    def test_figure_printed_for_another_question_is_handed_over(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("a", 1): tagged("已知集合", {"A": "1", "B": "2"}),
            ("b", 1): tagged("已知集合", {"A": "1", "B": "2"}),
            # 第 2 题范围里的图其实印着"第 3 题图"
            ("a", 2): tagged("下列图形中是柱体的是（ ）", figures="1=第3题"),
            ("b", 2): tagged("下列图形中是柱体的是（ ）"),
            ("*", 3): tagged("如图，已知函数 $f(x)=x^2$"),
            ("a", 5): tagged("已知数列"), ("b", 5): tagged("已知数列"),
            ("a", 6): tagged("如图，四棱锥", figures="无"), ("b", 6): tagged("如图，四棱锥"),
        }
        self.run_paper(answers)
        cards = {q.number: q for q in self.paper.questions.all()}
        # 第 2 题：选择题，但选项全是图？这里没有选项文字也没有选项图 → 提示。
        # 印着"第 3 题图"的候选图按配图规则处理（"还露出了第 N 题"已于 1.12.5 删除）。
        self.assertEqual(cards[2].figures, [])
        # 第 3 题明确写有“如图”，且自己范围内恰好只有一张普通候选图：
        # 本地规则可确定性认领该图，随后再接收第 2 题交来的另一张图。
        # 多候选、无文字提示或图片选项题仍由其他测试保持人工审核。
        self.assertEqual(
            sorted(f["source"] for f in cards[3].figures), ["auto", "other"],
            cards[3].figures,
        )
        self.assertEqual(cards[3].figure_review["status"], "ok")
        self.assertNotIn(figure_policy.FLAG_UNFOUND_FIGURE, cards[3].flags)
        # 第 6 题说"如图"却没有图 → 提示
        self.assertIn(pipeline.FLAG_NO_FIGURE, cards[6].flags)
        self.assertEqual(cards[6].state, "yellow")

    def test_partial_option_figures_keep_the_question_blocked(self):
        answers = {("locate", 4): "【刻度】无", ("*", 1): tagged("x"), ("*", 3): tagged("x"), ("*", 5): tagged("x"),
                   ("*", 6): tagged("x"),
                   ("a", 2): "【题型】单选题\n【题干】\n下列图形，不是柱体的是（ ）\n【A】\n【B】\n【配图】1=A",
                   ("b", 2): "【题型】单选题\n【题干】\n下列图形，不是柱体的是（ ）"}
        self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual([f["slot"] for f in q2.figures], ["A"])
        self.assertEqual(q2.state, "yellow", q2.flags)
        self.assertEqual(q2.figure_review["status"], "blocked_missing")
        self.assertEqual(q2.figure_review["missing_slots"], ["B", "C", "D"])
        self.assertIn(figure_policy.FLAG_UNFOUND_FIGURE, q2.flags)

    def test_borrowed_figure_does_not_clear_conflict_or_unfilled_option_slots(self):
        no_cue = Question.objects.create(
            paper=self.paper,
            number=8,
            stem="计算 $1+1$ 的值。",
            regions=[{"page_idx": 0, "bbox": [50, 300, 480, 380]}],
            state=Question.State.YELLOW,
        )
        missing_options = Question.objects.create(
            paper=self.paper,
            number=9,
            question_type="single_choice",
            stem="下列四幅图中，正确的是（ ）。",
            regions=[{"page_idx": 0, "bbox": [50, 380, 480, 520]}],
            state=Question.State.YELLOW,
            flags=[figure_policy.FLAG_UNFOUND_FIGURE],
            figure_review={
                "status": "blocked_missing",
                "source": "automatic",
                "reason": "纯图片选择题的选项图尚未补齐",
                "signals": ["unbound_figure_description"],
                "cue_matches": ["下列四幅图"],
                "missing_slots": ["A", "B", "C", "D"],
                "excluded_count": 0,
            },
        )

        pipeline.assign_foreign_figures(self.paper, [
            {"number": 8, "page_idx": 0, "bbox": [300, 310, 440, 370]},
            {"number": 9, "page_idx": 0, "bbox": [300, 390, 440, 450]},
        ])

        no_cue.refresh_from_db()
        missing_options.refresh_from_db()
        self.assertEqual(no_cue.figure_review["status"], "conflict")
        self.assertIn("bound_figure_without_text_cue", no_cue.figure_review["signals"])
        self.assertEqual(missing_options.figure_review["status"], "blocked_missing")
        self.assertEqual(missing_options.figure_review["missing_slots"], ["A", "B", "C", "D"])
        self.assertIn(figure_policy.FLAG_UNFOUND_FIGURE, missing_options.flags)

    def test_plain_image_description_from_second_reader_is_not_saved(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): (
                "【题型】单选题\n【题干】四位同学画数轴如图所示（ ）\n"
                "【A】\n【B】\n【C】\n【D】\n【配图】1=A"
            ),
            ("b", 2): tagged(
                "四位同学画数轴如图所示（ ）",
                {
                    "A": "数轴上标有点，标号依次为 1, 2, 3, 4, 5",
                    "B": "数轴上标有点，标号依次为 -1, -2, 0, 1, 2",
                    "C": "数轴上标有点，标号依次为 -2, -1, 0, 1, 2",
                    "D": "数轴上标有点，标号依次为 -2, -1, [?], 1, 2",
                },
            ),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {})
        self.assertEqual([figure["slot"] for figure in q2.figures], ["A"])
        self.assertEqual(q2.state, "yellow", q2.flags)
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, q2.flags)
        self.assertEqual(q2.figure_review["status"], "blocked_missing")
        self.assertEqual(q2.figure_review["missing_slots"], ["B", "C", "D"])
        self.assertFalse(any("看不清的字" in flag for flag in q2.flags))
        self.assertEqual(q2.read_b, {"skipped": "disabled"})
        self.assertFalse(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_missing_option_text_from_primary_read_stays_flagged_for_human_review(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): (
                "【题型】单选题\n【题干】选择箭头方向（ ）\n"
                "【A】\n【配图】1=A"
            ),
            ("b", 2): tagged("选择箭头方向（ ）", {"A": "向右"}),
            ("arbiter", 2): tagged("选择箭头方向（ ）", {"A": "向右"}),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {})
        self.assertEqual([figure["slot"] for figure in q2.figures], ["A"])
        self.assertEqual(q2.text_source, "single")
        self.assertEqual(q2.state, "yellow", q2.flags)
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, q2.flags)
        self.assertEqual(q2.figure_review["status"], "blocked_missing")
        self.assertFalse(any(kind in {"b", "arbiter"} and number == 2 for kind, number, _ in chat.calls))

    def test_plain_image_description_from_primary_reader_is_not_saved(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): tagged(
                "选择正确的数轴（ ）",
                {"A": "数轴上标有点，标号依次为 1, 2, 3, 4, 5"},
                figures="1=A",
            ),
            ("b", 2): tagged("选择正确的数轴（ ）"),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {})
        self.assertEqual([figure["slot"] for figure in q2.figures], ["A"])
        self.assertEqual(q2.state, "yellow", q2.flags)
        self.assertIn(figure_policy.FLAG_UNFOUND_FIGURE, q2.flags)
        self.assertEqual(q2.figure_review["status"], "blocked_missing")
        self.assertEqual(q2.figure_review["missing_slots"], ["B", "C", "D"])
        self.assertFalse(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_primary_text_is_kept_without_a_second_reader(self):
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): tagged("选择正确答案（ ）", {"A": "甲"}),
            ("b", 2): tagged("选择正确答案（ ）", {"A": "乙"}),
            ("arbiter", 2): tagged("选择正确答案（ ）", {"A": "[图：一条没有框出的数轴]"}),
        }
        self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.options, {"A": "甲"})
        self.assertEqual(q2.text_source, "single")
        self.assertIn(pipeline.FLAG_UNFOUND_FIGURE, q2.flags)
        self.assertEqual(q2.state, "yellow")

    def test_markdown_table_is_not_saved_when_the_table_is_a_stem_figure(self):
        table_stem = "根据下表回答：\n\n| 星期 | 一 | 二 |\n|---|---|---|\n| 增减 | +5 | -2 |\n\n求总数。"
        answers = {
            ("locate", 4): "【刻度】无",
            ("*", 1): tagged("x"), ("*", 3): tagged("x"),
            ("*", 5): tagged("x"), ("*", 6): tagged("x"),
            ("a", 2): tagged(table_stem, {"A": "1", "B": "2"}, figures="1=题干"),
            ("b", 2): tagged(table_stem, {"A": "1", "B": "2"}),
        }
        chat = self.run_paper(answers)
        q2 = self.paper.questions.get(number=2)
        self.assertEqual(q2.stem, "根据下表回答：\n\n求总数。")
        self.assertEqual([figure["slot"] for figure in q2.figures], ["stem"])
        self.assertEqual(q2.state, "green", q2.flags)
        self.assertIn("| 星期 |", q2.read_a["stem"])
        self.assertFalse(any(kind == "arbiter" and number == 2 for kind, number, _ in chat.calls))

    def test_resegment_keeps_unchanged_cards_and_rereads_changed(self):
        answers = {("locate", 4): "【刻度】无", **{("*", n): tagged(f"第{n}题", {"A": "1", "B": "2"}) for n in (1, 2, 3)},
                   **{("*", n): tagged(f"第{n}题") for n in (5, 6)}}
        answers[("a", 2)] = tagged("第2题", {"A": "1", "B": "2"}, figures="1=无关")
        self.run_paper(answers)
        q1 = self.paper.questions.get(number=1)
        q1.approved = True
        q1.approved_content_hash = library.approval_hash(q1)
        q1.save(update_fields=["approved", "approved_content_hash"])
        # 模拟旧版切错：第 2 题范围是空的（红卡），第 5 题被人工调整过
        q2 = self.paper.questions.get(number=2)
        Question.objects.filter(pk=q2.pk).update(regions=[], regions_auto=[], state="red", error="x")
        q5 = self.paper.questions.get(number=5)
        manual = [{"page_idx": 0, "bbox": [520, 240, 960, 420]}]
        Question.objects.filter(pk=q5.pk).update(regions=manual)
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save()
        chat = self.run_paper(answers)
        cards = {q.number: q for q in self.paper.questions.all()}
        self.assertTrue(library.approval_is_current(cards[1]))  # 内容和来源没变：保留通过
        self.assertTrue(cards[2].regions)                   # 重新切出范围
        self.assertEqual(cards[2].state, "green")           # 并重新识读
        self.assertEqual(cards[5].regions, manual)          # 人工范围不动
        read_numbers = {n for kind, n, _ in chat.calls if kind in ("a", "b")}
        self.assertEqual(read_numbers, {2})
        self.assertTrue(any("重新切题" in note for note in self.paper.notes))

    def test_resegment_preserves_missing_human_cards_and_only_deletes_unreviewed_auto_cards(self):
        edited = Question.objects.create(
            paper=self.paper, number=90, stem="人工改过的题干", edited=True, text_source="human",
            start_source="mineru", state=Question.State.GREEN,
            regions=[{"page_idx": 0, "bbox": [10, 10, 100, 100]}],
        )
        approved = Question.objects.create(
            paper=self.paper, number=91, stem="已人工通过的草稿", approved=True,
            approved_at=timezone.now(), approved_content_hash="a" * 64,
            start_source="mineru", state=Question.State.GREEN,
            regions=[{"page_idx": 0, "bbox": [10, 110, 100, 200]}],
        )
        disposable = Question.objects.create(
            paper=self.paper, number=92, stem="纯自动未审核", start_source="mineru",
            state=Question.State.WAITING,
            regions=[{"page_idx": 0, "bbox": [10, 210, 100, 300]}],
        )

        with mock.patch.object(pipeline, "locate_missing", return_value=[]):
            pipeline.segment_paper(self.paper)

        edited.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(edited.stem, "人工改过的题干")
        self.assertTrue(edited.edited)
        self.assertEqual(edited.state, Question.State.YELLOW)
        self.assertIn(pipeline.FLAG_RESEGMENT_PRESERVED, edited.flags)
        self.assertEqual(approved.stem, "已人工通过的草稿")
        self.assertFalse(approved.approved)
        self.assertEqual(approved.state, Question.State.YELLOW)
        self.assertIn(pipeline.FLAG_RESEGMENT_PRESERVED, approved.flags)
        self.assertFalse(Question.objects.filter(pk=disposable.pk).exists())
        self.paper.refresh_from_db()
        self.assertTrue(any("已保留并标黄" in note for note in self.paper.notes))

    def test_repeated_number_in_one_group_never_overwrites_another_card(self):
        first_regions = [{"page_idx": 0, "bbox": [40, 120, 470, 220]}]
        second_regions = [{"page_idx": 0, "bbox": [40, 320, 470, 420]}]

        def items(section_a="第一处", section_b="第二处"):
            return [
                {"number": 1, "section": section_a, "question_type": "free_response",
                 "regions": first_regions, "figure_candidates": [],
                 "start": {"source": "mineru"}},
                {"number": 1, "section": section_b, "question_type": "free_response",
                 "regions": second_regions, "figure_candidates": [],
                 "start": {"source": "mineru"}},
            ]

        with mock.patch.object(pipeline, "locate_missing", return_value=[]), \
                mock.patch.object(segment, "build_questions", return_value=items()), \
                mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value):
            pipeline.segment_paper(self.paper)

        cards = list(self.paper.questions.filter(number=1).order_by("id"))
        self.assertEqual(len(cards), 2)
        source_keys = {card.source_key for card in cards}
        self.assertEqual({card.section for card in cards}, {"第一处", "第二处"})

        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save(update_fields=["status"])
        with mock.patch.object(pipeline, "locate_missing", return_value=[]), \
                mock.patch.object(segment, "build_questions", return_value=items("第一处更新", "第二处更新")), \
                mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda value, _load: value):
            pipeline.segment_paper(self.paper)

        cards = list(self.paper.questions.filter(number=1).order_by("id"))
        self.assertEqual(len(cards), 2)
        self.assertEqual({card.source_key for card in cards}, source_keys)
        self.assertEqual({card.section for card in cards}, {"第一处更新", "第二处更新"})

    def test_missing_number_is_located_and_split(self):
        answers = {("locate", 4): "【刻度】05", ("*", 1): tagged("x"), ("*", 2): tagged("x"), ("*", 3): tagged("x"),
                   ("*", 4): tagged("x"), ("*", 5): tagged("x"), ("*", 6): tagged("x")}
        self.run_paper(answers)
        numbers = list(self.paper.questions.values_list("number", flat=True))
        self.assertEqual(numbers, [1, 2, 3, 4, 5, 6])
        q4 = self.paper.questions.get(number=4)
        self.assertEqual(q4.start_source, "located")
        # The scripted readers report no printed number: the opening may be cut off.
        self.assertIn(pipeline.FLAG_LOCATED_WITHOUT_NUMBER, q4.flags)
        q3 = self.paper.questions.get(number=3)
        # A located start's crop reaches one line higher (the band is coarse);
        # the previous question's range still ends at the located number.
        self.assertLessEqual(q3.regions[-1]["bbox"][3],
                             q4.regions[0]["bbox"][1] + segment.START_PAD + segment.LOCATED_EXTRA_PAD + 1)

    def test_segment_pipeline_recovers_group_first_question_and_records_note(self):
        self.paper.blocks.all().delete()
        self.paper.pages = PAGES
        self.paper.save(update_fields=["pages"])
        blocks = [
            block(6, 0, [250, 261, 597, 290], "每周两练.数学不难"),
            block(7, 0, [250, 291, 597, 315], "9. 1 8", "equation"),
            block(8, 0, [190, 319, 608, 374],
                  "[2026吉林、黑龙江两省十校期中联考]已知全集 U=R，集合 A={x|x>1}"),
            block(9, 0, [216, 374, 515, 392], "(1) 若 m=2，求 A∩B；"),
            block(10, 0, [216, 392, 507, 409], "(2) 若 A∪B=A，求 m 的取值范围；"),
            block(11, 0, [190, 430, 620, 500], "解(1) 当 m=2 时，计算可得。"),
            block(21, 0, [193, 654, 646, 712], "2.[2026湖北期中]下列说法正确的是（ ） A.甲 B.乙"),
            block(22, 1, [190, 100, 646, 160], "3. 已知函数 f(x)=x，求值。"),
        ]
        Block.objects.bulk_create([Block(paper=self.paper, **item) for item in blocks])
        with mock.patch.object(pipeline.readers, "locate_band") as locate_band, \
                mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda regions, _load: regions):
            pipeline.segment_paper(self.paper)

        self.paper.refresh_from_db()
        locate_band.assert_not_called()
        self.assertEqual(
            list(self.paper.questions.order_by("number").values_list("number", "start_source")),
            [(1, "inferred"), (2, "mineru"), (3, "mineru")],
        )
        self.assertTrue(any("未增加额外模型调用" in note for note in self.paper.notes), self.paper.notes)
        self.assertTrue(any("仍按正常流程识读" in note for note in self.paper.notes), self.paper.notes)
        self.assertTrue(any("请对照原卷核对" in note for note in self.paper.notes), self.paper.notes)

        # A second local segmentation is idempotent.  A manually recovered q1
        # remains authoritative, and already-approved unchanged q2/q3 cards do
        # not lose approval merely because the local boundary check runs again.
        q1, q2, q3 = [self.paper.questions.get(number=number) for number in (1, 2, 3)]
        manual_regions = [{"page_idx": 0, "bbox": [175, 305, 615, 645]}]
        q1.regions = manual_regions
        q1.start_source = "manual"
        q1.save(update_fields=["regions", "start_source"])
        for question in (q2, q3):
            question.question_type = "free_response"
            question.approved = True
            question.approved_at = timezone.now()
            question.approved_content_hash = f"approved-{question.number}"
            question.save(update_fields=["question_type", "approved", "approved_at", "approved_content_hash"])
        unchanged = {question.number: list(question.regions) for question in (q2, q3)}
        self.paper.status = Paper.Status.SEGMENTING
        self.paper.save(update_fields=["status"])

        with mock.patch.object(pipeline.readers, "locate_band") as locate_band, \
                mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda regions, _load: regions):
            pipeline.segment_paper(self.paper)

        locate_band.assert_not_called()
        self.assertEqual(self.paper.questions.count(), 3)
        q1.refresh_from_db()
        self.assertEqual((q1.start_source, q1.regions), ("manual", manual_regions))
        for question in (q2, q3):
            question.refresh_from_db()
            self.assertTrue(question.approved)
            self.assertEqual(question.question_type, "free_response")
            self.assertEqual(question.regions, unchanged[question.number])


class ApiTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.client = Client()
        self.paper = Paper.objects.create(filename="卷.pdf", kind="pdf", sha256="y" * 64, status=Paper.Status.READY,
                                          pages=PAGES[:1])
        folder = self.temp / str(self.paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        self.paper.source_path = str(folder / "source.pdf")
        self.paper.save()
        self.q = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem="如图，已知 $x=1$", options={"A": "1", "B": "2"},
            regions=[{"page_idx": 0, "bbox": [50, 100, 480, 300]}], state=Question.State.GREEN,
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [300, 150, 450, 250], "source": "auto"}],
        )
        self.q2 = Question.objects.create(paper=self.paper, number=2, stem="求证", regions=[{"page_idx": 0, "bbox": [50, 300, 480, 400]}],
                                          question_type="free_response", state=Question.State.YELLOW, flags=["两次识读不一致，已由第三次识读裁决，请看标黄的地方"])

    def post(self, path, body=None, header=True):
        headers = {"HTTP_X_QB_REQUEST": "1"} if header else {}
        return self.client.post(path, data=json.dumps(body or {}), content_type="application/json", **headers)

    def patch(self, path, body=None, header=True):
        headers = {"HTTP_X_QB_REQUEST": "1"} if header else {}
        return self.client.patch(path, data=json.dumps(body or {}), content_type="application/json", **headers)

    def test_empty_review_reuses_saved_reads_without_rereading_old_cards(self):
        candidate = {"label": "1", "seq": 1, "page_idx": 0, "bbox": [300, 310, 440, 370]}
        base_read = {
            "stem": "求证", "options": {}, "type": "free_response", "figures": {"1": "none"},
            "missing_figure": False, "figure_descriptions": [],
        }
        Question.objects.filter(pk=self.q2.pk).update(
            figure_review={}, figure_candidates=[candidate], read_a=base_read, read_b={}, read_c={},
            figures=[], question_type="free_response",
        )
        self.q2.refresh_from_db()
        self.assertEqual(figure_policy.stored_or_derived_review(self.q2)["status"], "auto_excluded")

        self.q2.read_a = {**base_read, "figures": {}}
        self.q2.save(update_fields=["read_a"])
        self.assertEqual(figure_policy.stored_or_derived_review(self.q2)["status"], "conflict")

        self.q2.question_type = "single_choice"
        self.q2.read_a = {**base_read, "type": "single_choice", "figures": {"1": "A"}}
        self.q2.figures = [{
            "slot": "A", "page_idx": 0, "bbox": [300, 310, 440, 370], "source": "auto",
        }]
        self.q2.save(update_fields=["question_type", "read_a", "figures"])
        review = figure_policy.stored_or_derived_review(self.q2)
        self.assertEqual(review["status"], "blocked_missing")
        self.assertEqual(review["missing_slots"], ["B", "C", "D"])

    def delete(self, path, header=True):
        headers = {"HTTP_X_QB_REQUEST": "1"} if header else {}
        return self.client.delete(path, **headers)

    def test_requires_page_header(self):
        response = self.post(f"/api/questions/{self.q.id}/approve", {"approved": True}, header=False)
        self.assertEqual(response.status_code, 403)

    def test_approve_green_then_publish_and_version(self):
        data = self.post(f"/api/papers/{self.paper.id}/approve-green").json()
        self.assertEqual(data["approved"], 1)
        self.q.refresh_from_db()
        self.assertTrue(self.q.approved_content_hash)
        publication = PublishedQuestion.objects.get(question=self.q, version=1)
        self.assertEqual(publication.status, PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        again = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual((again["created"], again["unchanged"]), (0, 1))
        self.assertEqual(PublishedQuestion.objects.get().pk, publication.pk)
        self.assertEqual(publication.content["figures"][0]["url"], f"/api/library/{publication.id}/figures/figure-1.png")
        self.assertEqual(publication.content["figures"][0]["source"], "auto")
        # 测试题没有 regions_auto，对系统来说是人工框定的来源范围。
        self.assertEqual(publication.content["sources"][0]["source"], "manual")
        self.assertEqual(publication.content["review"]["state"], "green")
        self.assertTrue(publication.content["review"]["approved_content_hash"])
        self.assertTrue((self.temp / "library" / str(publication.id) / "figure-1.png").is_file())
        # 改字后再入库 → 第 2 版，旧版标记为已替代
        self.post(f"/api/questions/{self.q.id}/text", {"stem": "如图，已知 $x=2$", "options": {"A": "1", "B": "2"}})
        self.q.refresh_from_db()
        self.assertFalse(self.q.approved)
        self.assertEqual(self.q.approved_content_hash, "")
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        self.assertEqual(PublishedQuestion.objects.count(), 1)
        self.assertEqual(self.post(f"/api/questions/{self.q.id}/approve", {"approved": True}).status_code, 200)
        second = PublishedQuestion.objects.get(question=self.q, version=2)
        self.assertEqual(second.content["stem"], "如图，已知 $x=2$")
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        self.assertEqual(PublishedQuestion.objects.count(), 2)
        self.assertEqual(list(PublishedQuestion.objects.order_by("version").values_list("status", flat=True)),
                         ["superseded", "published"])
        library = self.client.get("/api/library").json()
        self.assertEqual(library["total"], 1)
        self.assertEqual(library["items"][0]["content"]["stem"], "如图，已知 $x=2$")
        self.assertEqual(self.client.get(f"/api/library?q=x=2").json()["total"], 1)
        malformed = self.client.get("/api/library?document=--------------------------------")
        self.assertEqual(malformed.status_code, 200)
        self.assertEqual(malformed.json()["total"], 0)

    def test_rename_updates_every_publication_source_without_revalidating_stale_content(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.post(f"/api/papers/{self.paper.id}/publish")

        for stem in ("如图，已知 $x=2$", "如图，已知 $x=3$"):
            current = PublishedQuestion.objects.filter(
                question=self.q, status=PublishedQuestion.Status.PUBLISHED,
            ).first()
            if stem.endswith("3$"):
                library.withdraw(current)
            self.post(f"/api/questions/{self.q.id}/text", {
                "stem": stem, "options": {"A": "1", "B": "2"},
            })
            self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
            self.post(f"/api/papers/{self.paper.id}/publish")

        before = {
            item.pk: (item.version, item.status, item.published_at, item.withdrawn_at, item.content_hash)
            for item in PublishedQuestion.objects.filter(question=self.q)
        }
        self.assertEqual(
            [item.status for item in PublishedQuestion.objects.filter(question=self.q).order_by("version")],
            [PublishedQuestion.Status.SUPERSEDED, PublishedQuestion.Status.WITHDRAWN,
             PublishedQuestion.Status.PUBLISHED],
        )
        self.q.refresh_from_db()
        self.assertTrue(library.approval_is_current(self.q))

        response = self.patch(f"/api/papers/{self.paper.id}", {"name": "秋季月考任务"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["changed"])
        self.assertEqual(response.json()["paper"]["name"], "秋季月考任务")
        self.assertEqual(response.json()["paper"]["filename"], "卷.pdf")
        self.paper.refresh_from_db()
        self.q.refresh_from_db()
        self.assertEqual(self.paper.filename, "卷.pdf")
        self.assertEqual(self.paper.task_name, "秋季月考任务")
        self.assertTrue(library.approval_is_current(self.q))
        self.assertTrue(library.publication_state(self.q)["up_to_date"])

        publications = list(PublishedQuestion.objects.filter(question=self.q).order_by("version"))
        self.assertEqual(len(publications), 3)
        for publication in publications:
            old = before[publication.pk]
            self.assertEqual(
                (publication.version, publication.status, publication.published_at, publication.withdrawn_at),
                old[:4],
            )
            self.assertNotEqual(publication.content_hash, old[4])
            self.assertEqual(publication.source_filename, "秋季月考任务")
            self.assertEqual(publication.content["source_filename"], "秋季月考任务")
            self.assertEqual(publication.content_hash, library.content_hash(publication.content))
            self.assertEqual(publication.content["review"]["approved_content_hash"], publication.content_hash)
            self.assertIn(library.search_key("秋季月考任务"), publication.search_text)
            self.assertNotIn(library.search_key("卷.pdf"), publication.search_text)

        listing = self.client.get("/api/library?q=秋季月考任务").json()
        self.assertEqual(listing["total"], 1)
        self.assertEqual(listing["items"][0]["source_filename"], "秋季月考任务")
        source = listing["facets"]["sources"][0]
        # 1.12.6：来源带上入库时间，题库里同名来源（同一份卷录过两次）才分得开。
        self.assertEqual((source["document_id"], source["filename"], source["count"]),
                         (str(self.paper.id), "秋季月考任务", 1))
        published = datetime.fromisoformat(listing["items"][0]["published_at"].replace("Z", "+00:00"))
        # JsonResponse 会把 UTC 写成 Z，publication_json 里是 +00:00；数据库只存到
        # 微秒而发布记录的序列化保留全精度，所以比到秒。
        self.assertEqual(source["first_published_at"][:19], published.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertEqual(source["last_published_at"][:19], published.strftime("%Y-%m-%dT%H:%M:%S"))
        self.assertEqual(self.client.get("/api/library?q=卷.pdf").json()["total"], 0)

    def test_rename_keeps_an_already_stale_approval_stale(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.q.refresh_from_db()
        approved_hash = self.q.approved_content_hash
        Question.objects.filter(pk=self.q.pk).update(stem="如图，审批后被后台改过")
        self.q.refresh_from_db()
        self.assertFalse(library.approval_is_current(self.q))

        response = self.patch(f"/api/papers/{self.paper.id}", {"name": "改名后仍需复核"})
        self.assertEqual(response.status_code, 200, response.content)
        self.q.refresh_from_db()
        self.assertTrue(self.q.approved)
        self.assertEqual(self.q.approved_content_hash, approved_hash)
        self.assertFalse(library.approval_is_current(self.q))

    def test_rename_validates_name_and_keeps_original_filename(self):
        endpoint = f"/api/papers/{self.paper.id}"
        for invalid in (None, "", "   ", "甲\n乙", "甲\x00乙", "甲" * 256):
            with self.subTest(invalid=repr(invalid)):
                response = self.patch(endpoint, {"name": invalid})
                self.assertEqual(response.status_code, 400)
                self.paper.refresh_from_db()
                self.assertEqual(self.paper.task_name, "")
                self.assertEqual(self.paper.filename, "卷.pdf")

        same = self.patch(endpoint, {"name": "卷.pdf"})
        self.assertEqual(same.status_code, 200)
        self.assertFalse(same.json()["changed"])
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.task_name, "")
        self.assertEqual(self.paper.filename, "卷.pdf")

    def test_paper_rename_and_delete_require_local_page_header(self):
        failed = Paper.objects.create(
            filename="失败.pdf", kind="pdf", sha256="h" * 64, status=Paper.Status.FAILED,
        )
        rename = self.patch(f"/api/papers/{self.paper.id}", {"name": "不应生效"}, header=False)
        delete = self.delete(f"/api/papers/{failed.id}", header=False)
        self.assertEqual(rename.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.task_name, "")
        self.assertTrue(Paper.objects.filter(pk=failed.pk).exists())

    def test_delete_failed_task_removes_related_drafts_and_controlled_folder(self):
        failed = Paper.objects.create(
            filename="失败.pdf", kind="pdf", sha256="d" * 64, status=Paper.Status.FAILED,
        )
        folder = self.temp / str(failed.id)
        folder.mkdir()
        source = folder / "source.pdf"
        source.write_bytes(b"private draft")
        failed.source_path = str(source)
        failed.save(update_fields=["source_path"])
        draft = Question.objects.create(paper=failed, number=1, stem="未完成草稿")
        block = Block.objects.create(
            paper=failed, seq=0, type="text", page_idx=0, bbox=[0, 0, 10, 10], text="草稿",
        )

        response = self.delete(f"/api/papers/{failed.id}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["deleted"], str(failed.id))
        self.assertFalse(Paper.objects.filter(pk=failed.pk).exists())
        self.assertFalse(Question.objects.filter(pk=draft.pk).exists())
        self.assertFalse(Block.objects.filter(pk=block.pk).exists())
        self.assertFalse(folder.exists())

    def test_delete_keeps_task_when_its_files_cannot_be_staged(self):
        failed = Paper.objects.create(
            filename="占用中.pdf", kind="pdf", sha256="b" * 64, status=Paper.Status.FAILED,
        )
        folder = self.temp / str(failed.id)
        folder.mkdir()
        (folder / "source.pdf").write_bytes(b"busy")
        with mock.patch("pathlib.Path.replace", side_effect=OSError("busy")):
            response = self.delete(f"/api/papers/{failed.id}")
        self.assertEqual(response.status_code, 400)
        self.assertIn("被占用", response.json()["error"])
        self.assertTrue(Paper.objects.filter(pk=failed.pk).exists())
        self.assertTrue(folder.exists())

    def test_delete_rejects_active_and_any_task_with_publication_history(self):
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.SEGMENTING)
        active = self.delete(f"/api/papers/{self.paper.id}")
        self.assertEqual(active.status_code, 400)
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())

        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.READY)
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        self.post(f"/api/papers/{self.paper.id}/publish")
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.FAILED)
        blocked = self.delete(f"/api/papers/{self.paper.id}")
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("正式题库", blocked.json()["error"])
        self.assertTrue(Paper.objects.filter(pk=self.paper.pk).exists())
        self.assertTrue(PublishedQuestion.objects.filter(paper_id=self.paper.pk).exists())

    def test_delete_rejects_both_split_source_and_split_child(self):
        source = Paper.objects.create(
            filename="原书.pdf", kind="pdf", sha256="1" * 64, status=Paper.Status.FAILED,
        )
        child = Paper.objects.create(
            filename="第一册.pdf", kind="pdf", sha256="2" * 64, status=Paper.Status.FAILED,
            structure={"split_from": str(source.id), "split_index": 1},
        )
        source.structure = {"split_children": [str(child.id)]}
        source.save(update_fields=["structure"])

        for paper in (source, child):
            with self.subTest(paper=paper.filename):
                response = self.delete(f"/api/papers/{paper.id}")
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("追溯", response.json()["error"])
                self.assertTrue(Paper.objects.filter(pk=paper.pk).exists())

    def test_text_edit_clears_text_flags_but_requires_separate_approval(self):
        data = self.post(f"/api/questions/{self.q2.id}/text", {"stem": "求证：AB=CD", "question_type": "free_response"}).json()
        self.assertFalse(data["question"]["approved"])
        self.assertFalse(data["question"]["approval_valid"])
        self.assertEqual(data["question"]["state"], "green")
        self.assertEqual(data["question"]["text_source"], "human")
        approved = self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).json()["question"]
        self.assertTrue(approved["approved"])
        self.assertTrue(approved["approval_valid"])

    def test_manual_text_edit_normalizes_parallelogram_notation(self):
        data = self.post(f"/api/questions/{self.q.id}/text", {
            "stem": r"如图，在 $\square ABCD$ 中",
            "options": {"A": "□EFGH", "B": r"$3\square 5=15$"},
            "answer": r"\Box WXYZ",
            "analysis": r"由 $\text{□}ABCD$ 可知",
            "question_type": "single_choice",
        }).json()["question"]
        self.assertEqual(data["stem"], "如图，在 ▱ABCD 中")
        self.assertEqual(data["options"], {"A": "▱EFGH", "B": r"$3\square 5=15$"})
        self.assertEqual(data["answer"], "▱WXYZ")
        self.assertEqual(data["analysis"], "由 ▱ABCD 可知")
        self.assertFalse(data["approved"])

    def test_regions_change_saves_only_and_preserves_figures(self):
        old_figures = deepcopy(self.q.figures)
        data = self.post(f"/api/questions/{self.q.id}/regions",
                         {"regions": [{"page_idx": 0, "bbox": [40, 90, 490, 320]}]}).json()
        q = data["question"]
        self.assertEqual(q["state"], "yellow")
        self.assertTrue(q["regions_changed"])
        saved = Question.objects.get(pk=self.q.id)
        self.assertEqual(saved.figures, old_figures)
        self.assertFalse(saved.reread_requested or saved.ocr_pending)
        bad = self.post(f"/api/questions/{self.q.id}/regions", {"regions": [{"page_idx": 3, "bbox": [0, 0, 10, 10]}]})
        self.assertEqual(bad.status_code, 400)

    def test_figures_manual(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        data = self.post(f"/api/questions/{self.q.id}/figures",
                         {"figures": [{"slot": "A", "page_idx": 0, "bbox": [60, 200, 120, 260]}]}).json()
        self.assertEqual(data["question"]["figures"][0]["source"], "manual")
        self.assertEqual(data["question"]["figure_review"]["status"], "ok")
        self.assertEqual(data["question"]["figure_review"]["source"], "human")
        self.assertFalse(data["question"]["approved"])
        self.assertFalse(Question.objects.get(pk=self.q.id).approved_content_hash)
        image = self.client.get(data["question"]["figures"][0]["url"])
        self.assertEqual(image.status_code, 200)

    def test_blocking_figure_reviews_reject_direct_and_bulk_approval(self):
        for status in ("blocked_missing", "conflict"):
            with self.subTest(status=status):
                Question.objects.filter(pk=self.q2.pk).update(
                    state=Question.State.YELLOW,
                    approved=False,
                    approved_content_hash="",
                    figure_review={
                        "status": status,
                        "reason": "测试中的配图冲突",
                        "signals": ["text_reference"] if status == "blocked_missing" else ["printed_figure"],
                        "source": "automatic",
                    },
                )
                response = self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True})
                self.assertEqual(response.status_code, 400, response.content)
                self.q2.refresh_from_db()
                self.assertFalse(self.q2.approved)

        # 即使异常旧数据把阻塞题留成绿卡，批量通过也必须做同样的防线检查。
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.GREEN,
            figure_review={
                "status": "blocked_missing",
                "reason": "题干提示有图但未绑定配图",
                "signals": ["text_reference"],
                "source": "automatic",
            },
        )
        response = self.post(f"/api/papers/{self.paper.id}/approve-green")
        self.assertEqual(response.status_code, 200, response.content)
        self.q2.refresh_from_db()
        self.assertFalse(self.q2.approved)
        # 1.12.5：过不去的题逐条说明原因，不再静默跳过。这张卡带着它自己的
        # 提醒（识读不一致 + 配图没框出来），原因就照这些提醒报。
        skipped = {item["number"]: item["reason"] for item in response.json()["skipped"]}
        self.assertIn(self.q2.number, skipped, response.content)
        self.assertIn(figure_policy.FLAG_UNFOUND_FIGURE, skipped[self.q2.number], response.content)

    def test_approve_green_reports_a_card_the_figure_review_demoted(self):
        """绿卡只要还带着待核查提醒，就必须说出来。

        配图检查在内存里把"绿"改回"黄"时，老代码直接 continue，界面上看起来
        就是"一键通过什么都没做"。这里钉住它必须出现在 skipped 里，并带上原因。
        """
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.GREEN,
            figure_review={
                "status": "blocked_missing", "reason": "题干提示有图但未绑定配图",
                "signals": ["text_reference"], "source": "automatic",
            },
        )
        data = self.post(f"/api/papers/{self.paper.id}/approve-green").json()
        reasons = {item["number"]: item["reason"] for item in data["skipped"]}
        self.assertIn(self.q2.number, reasons, data["skipped"])
        self.assertIn("还有待核查的提醒", reasons[self.q2.number])
        self.q2.refresh_from_db()
        self.assertFalse(self.q2.approved)

    def test_approve_green_reports_why_each_card_cannot_pass(self):
        """一键通过的结果面板要能说清每道过不去的题卡卡在哪一步。"""
        # 空题干：没读出文字
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.GREEN, stem="   ", question_type="free_response",
            figure_review={"status": figure_policy.CONFIRMED_NO_FIGURE, "source": "human"},
        )
        # 题型未定
        q3 = Question.objects.create(
            paper=self.paper, number=3, state=Question.State.GREEN, stem="求证：如图。",
            question_type="",
            figure_review={"status": figure_policy.CONFIRMED_NO_FIGURE, "source": "human"},
        )
        data = self.post(f"/api/papers/{self.paper.id}/approve-green").json()
        reasons = {item["number"]: item["reason"] for item in data["skipped"]}
        self.assertIn("还没读出题干", reasons[self.q2.number], data["skipped"])
        self.assertIn("题型还没定", reasons[q3.number], data["skipped"])
        # 能过的照过，已入库的那道不算"过不去"。
        self.assertEqual(data["approved"], 1, data["skipped"])
        self.q.refresh_from_db()
        self.assertTrue(self.q.approved_content_hash)
        # 已经入库的题不该被列为待处理项。
        self.assertNotIn(self.q.number, reasons)

    def test_human_no_figure_confirmation_is_traceable_and_allows_approval(self):
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.YELLOW,
            flags=[figure_policy.FLAG_NO_FIGURE],
            figure_review={
                "status": "blocked_missing",
                "reason": "题干提示有图但未绑定配图",
                "signals": ["text_reference"],
                "source": "automatic",
            },
        )
        blocked = self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True})
        self.assertEqual(blocked.status_code, 400, blocked.content)

        confirmed = self.post(
            f"/api/questions/{self.q2.id}/figure-review",
            {"decision": "confirm_no_figure"},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        payload = confirmed.json()["question"]
        self.assertEqual(payload["figure_review"]["status"], "confirmed_no_figure")
        self.assertEqual(payload["figure_review"]["source"], "human")
        self.assertTrue(payload["figure_review"]["reason"])
        self.assertFalse(any(figure_policy.figure_flag(flag) for flag in payload["flags"]))

        self.q2.refresh_from_db()
        self.assertEqual(self.q2.figure_review["status"], "confirmed_no_figure")
        self.assertEqual(self.q2.figure_review["source"], "human")
        approved = self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True})
        self.assertEqual(approved.status_code, 200, approved.content)

        publication = PublishedQuestion.objects.get(question=self.q2, version=1)
        published = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual((published["created"], published["unchanged"]), (0, 1), published)
        self.assertEqual(PublishedQuestion.objects.get(question=self.q2).pk, publication.pk)
        snapshot = publication.content
        self.assertEqual(snapshot["review"]["figure_review"]["status"], "confirmed_no_figure")
        self.assertEqual(snapshot["review"]["figure_review"]["source"], "human")

    def test_no_figure_confirmation_can_restore_previous_figures(self):
        original = json.loads(json.dumps(self.q.figures))
        confirmed = self.post(
            f"/api/questions/{self.q.id}/figure-review",
            {"decision": "confirm_no_figure"},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["question"]["figures"], [])

        reset = self.post(f"/api/questions/{self.q.id}/figure-review", {"decision": "reset"})
        self.assertEqual(reset.status_code, 200, reset.content)
        self.q.refresh_from_db()
        self.assertEqual(self.q.figures, original)
        self.assertEqual(self.q.figure_review["status"], "ok")
        self.assertFalse(self.q.approved)

    def test_text_changes_invalidate_no_figure_but_range_save_preserves_manual_review(self):
        confirmed = {
            "status": "confirmed_no_figure",
            "reason": "人工确认本题确实无图",
            "signals": ["human_confirmation"],
            "source": "human",
        }
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.GREEN, flags=[], figure_review=confirmed,
        )

        edited = self.post(f"/api/questions/{self.q2.id}/text", {
            "stem": "如图所示，求证：AB=CD",
            "question_type": "free_response",
        })
        self.assertEqual(edited.status_code, 200, edited.content)
        edited_question = edited.json()["question"]
        self.assertEqual(edited_question["figure_review"]["status"], "blocked_missing")
        self.assertEqual(edited_question["figure_review"]["source"], "automatic")
        self.assertEqual(
            self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code,
            400,
        )

        confirmed_again = self.post(
            f"/api/questions/{self.q2.id}/figure-review",
            {"decision": "confirm_no_figure"},
        )
        self.assertEqual(confirmed_again.status_code, 200, confirmed_again.content)
        moved = self.post(f"/api/questions/{self.q2.id}/regions", {
            "regions": [{"page_idx": 0, "bbox": [40, 280, 490, 430]}],
        })
        self.assertEqual(moved.status_code, 200, moved.content)
        self.q2.refresh_from_db()
        self.assertEqual(self.q2.figure_review.get("status"), "confirmed_no_figure")
        self.assertFalse(self.q2.approved)

    def test_published_figure_review_is_immutable_and_new_version_records_new_review(self):
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.GREEN,
            flags=[],
            figure_review={
                "status": "confirmed_no_figure",
                "reason": "人工确认本题确实无图",
                "signals": ["human_confirmation"],
                "source": "human",
            },
        )
        self.assertEqual(
            self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code,
            200,
        )
        first = PublishedQuestion.objects.get(question=self.q2, version=1)
        original_snapshot = json.loads(json.dumps(first.content))
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        self.assertEqual(self.q2.publications.count(), 1)

        changed = self.post(f"/api/questions/{self.q2.id}/text", {
            "stem": "如图所示，求证：AB=CD",
            "question_type": "free_response",
        }).json()["question"]
        self.assertEqual(changed["figure_review"]["status"], "blocked_missing")
        self.post(f"/api/questions/{self.q2.id}/figure-review", {"decision": "confirm_no_figure"})
        self.assertEqual(
            self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code,
            200,
        )
        second = PublishedQuestion.objects.get(question=self.q2, version=2)
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        self.assertEqual(self.q2.publications.count(), 2)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.content, original_snapshot)
        self.assertEqual(first.status, PublishedQuestion.Status.SUPERSEDED)
        self.assertEqual(second.content["review"]["figure_review"]["status"], "confirmed_no_figure")
        self.assertEqual(second.content["review"]["figure_review"]["source"], "human")

    def test_review_only_human_no_figure_confirmation_creates_a_new_version(self):
        Question.objects.filter(pk=self.q2.pk).update(
            state=Question.State.GREEN,
            flags=[],
            figure_review={
                "status": "ok",
                "source": "automatic",
                "reason": "配图检查未发现矛盾",
                "signals": [],
                "cue_matches": [],
                "excluded_count": 0,
            },
        )
        self.assertEqual(
            self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code,
            200,
        )
        first = PublishedQuestion.objects.get(question=self.q2, version=1)
        self.assertEqual(first.content["review"]["figure_review"]["source"], "automatic")
        original_snapshot = json.loads(json.dumps(first.content))
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/publish").json()["created"], 0)
        self.assertEqual(self.q2.publications.count(), 1)

        confirmed = self.post(
            f"/api/questions/{self.q2.id}/figure-review",
            {"decision": "confirm_no_figure"},
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        self.assertEqual(confirmed.json()["question"]["figure_review"]["source"], "human")
        self.assertEqual(
            self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code,
            200,
        )
        second = PublishedQuestion.objects.get(question=self.q2, version=2)
        published = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual((published["created"], published["unchanged"]), (0, 1), published)
        self.assertEqual(self.q2.publications.count(), 2)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.content, original_snapshot)
        self.assertEqual(first.status, PublishedQuestion.Status.SUPERSEDED)
        self.assertEqual(second.content["review"]["figure_review"]["status"], "confirmed_no_figure")
        self.assertEqual(second.content["review"]["figure_review"]["source"], "human")

    def test_cannot_approve_empty(self):
        Question.objects.filter(pk=self.q2.id).update(stem="")
        self.assertEqual(self.post(f"/api/questions/{self.q2.id}/approve", {"approved": True}).status_code, 400)

    def test_cannot_approve_unreviewable_states(self):
        for state in (Question.State.RED, Question.State.WAITING, Question.State.READING):
            Question.objects.filter(pk=self.q.id).update(state=state, approved=False, approved_content_hash="")
            response = self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
            self.assertEqual(response.status_code, 400, state)
        Question.objects.filter(pk=self.q.id).update(state=Question.State.YELLOW, flags=["请人工核对"])
        self.assertEqual(self.post(f"/api/questions/{self.q.id}/approve", {"approved": True}).status_code, 200)

    def test_publish_rejects_stale_approval_hash(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        original = PublishedQuestion.objects.get(question=self.q, version=1)
        snapshot = json.loads(json.dumps(original.content))
        Question.objects.filter(pk=self.q.id).update(stem="如图，审批后被其他代码改过")
        result = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual(result["created"], 0)
        self.assertTrue(any("重新终审" in problem for problem in result["problems"]))
        original.refresh_from_db()
        self.assertEqual(original.content, snapshot)
        self.assertEqual(original.status, PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(PublishedQuestion.objects.count(), 1)

    def test_publish_rejects_red_even_with_matching_hash(self):
        self.q.state = Question.State.RED
        self.q.approved = True
        self.q.approved_content_hash = library.approval_hash(self.q)
        self.q.save(update_fields=["state", "approved", "approved_content_hash"])
        result = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual(result["created"], 0)
        self.assertTrue(any("当前状态不能入库" in problem for problem in result["problems"]))
        self.assertFalse(PublishedQuestion.objects.exists())

    def test_source_range_change_creates_new_version_after_reapproval(self):
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        first = PublishedQuestion.objects.get(question=self.q, version=1)
        original_snapshot = json.loads(json.dumps(first.content))
        self.post(f"/api/papers/{self.paper.id}/publish")
        self.post(f"/api/questions/{self.q.id}/regions",
                  {"regions": [{"page_idx": 0, "bbox": [40, 90, 490, 320]}]})
        # 调整范围会清掉旧配图；重新人工配图并确认文字后，才能再通过。
        self.post(f"/api/questions/{self.q.id}/figures", {
            "figures": [{"slot": "stem", "page_idx": 0, "bbox": [300, 150, 450, 250]}],
        })
        self.post(f"/api/questions/{self.q.id}/text",
                  {"stem": self.q.stem, "options": self.q.options, "question_type": self.q.question_type})
        self.post(f"/api/questions/{self.q.id}/approve", {"approved": True})
        second = PublishedQuestion.objects.get(question=self.q, version=2)
        result = self.post(f"/api/papers/{self.paper.id}/publish").json()
        self.assertEqual((result["created"], result["unchanged"]), (0, 1))
        versions = list(PublishedQuestion.objects.order_by("version"))
        self.assertEqual([item.version for item in versions], [1, 2])
        self.assertEqual(versions[0].content, original_snapshot)
        self.assertEqual(versions[1].pk, second.pk)
        self.assertNotEqual(versions[0].content_hash, versions[1].content_hash)
        self.assertEqual(versions[1].content["sources"][0]["source"], "manual")

    def test_resegment_endpoint(self):
        Block.objects.create(paper=self.paper, seq=0, type="text", page_idx=0, bbox=[50, 100, 480, 130], text="1. 已知")
        data = self.post(f"/api/papers/{self.paper.id}/resegment").json()
        self.assertEqual(data["paper"]["status"], "segmenting")
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/resegment").status_code, 409)

    def test_card_without_regions_turns_red_with_hint(self):
        store = pipeline.PageStore(self.paper)
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "k"}):
            result = pipeline.read_card({"id": 1, "number": 9, "regions": [], "candidates": [],
                                         "question_type": "single_choice"}, store)
        self.assertEqual(result["state"], "red")
        self.assertIn("调整范围", result["error"])

    def test_page_store_bounds_decoded_page_memory(self):
        store = pipeline.PageStore(self.paper)
        store.MAX_MEMORY_PAGES = 2
        with mock.patch("core.pipeline.imaging.render_source_page",
                        side_effect=lambda *_args: Image.new("RGB", (12, 12), "white")):
            first = store.load(0)
            for page_idx in range(3):
                store.load(page_idx)
        self.assertEqual(list(store.memory), [1, 2])
        # 被 LRU 淘汰只能释放缓存引用；另一识读线程手里的页面仍必须可用。
        self.assertEqual(first.crop((0, 0, 2, 2)).size, (2, 2))

    def test_add_and_delete_question(self):
        data = self.post(f"/api/papers/{self.paper.id}/questions",
                         {"number": 3, "regions": [{"page_idx": 0, "bbox": [520, 100, 950, 300]}]})
        self.assertEqual(data.status_code, 201)
        new_id = data.json()["question"]["id"]
        self.assertFalse(Question.objects.get(pk=new_id).reread_requested)
        self.assertEqual(self.post(f"/api/papers/{self.paper.id}/questions",
                                   {"number": 3, "regions": [{"page_idx": 0, "bbox": [520, 100, 950, 300]}]}).status_code, 409)
        Question.objects.filter(pk=new_id).update(reread_requested=True, state=Question.State.WAITING)
        response = self.client.delete(f"/api/questions/{new_id}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 409)  # 尚在重读队列，不能和工作者竞态
        Question.objects.filter(pk=new_id).update(
            reread_requested=False, state=Question.State.GREEN, stem="人工补录完成",
        )
        response = self.client.delete(f"/api/questions/{new_id}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Question.objects.filter(pk=new_id).exists())
        self.assertTrue(Question.all_objects.filter(pk=new_id).exists())

    def test_page_preview_and_detail(self):
        self.assertEqual(self.client.get(f"/api/papers/{self.paper.id}/pages/0/preview").status_code, 200)
        self.assertEqual(self.client.get(f"/api/documents/{self.paper.id}/pages/0/preview").status_code, 200)
        detail = self.client.get(f"/api/papers/{self.paper.id}").json()
        self.assertEqual(detail["paper"]["counts"]["yellow"], 1)
        self.assertEqual(len(detail["questions"]), 2)

    def test_explicit_cloud_upload_needs_credentials(self):
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "", "MINIMAX_API_KEY": ""}):
            response = self.client.post("/api/papers", {"file": io.BytesIO(b"%PDF-1.4"),
                "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400)

    def test_upload_creates_queued_paper(self):
        source = self.temp / "valid-upload.pdf"
        fake_page_pdf(source)
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}):
            upload = io.BytesIO(source.read_bytes())
            upload.name = "新卷.pdf"
            response = self.client.post("/api/papers", {"file": upload, "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="新卷.pdf")
        self.assertEqual(paper.status, Paper.Status.QUEUED)
        self.assertEqual(len(paper.pages), 1)
        self.assertTrue(Path(paper.source_path).is_file())

    def test_upload_pdf_preflight_accepts_exactly_200_pages(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(200)]
        upload = io.BytesIO(b"mock PDF at the supported page limit")
        upload.name = "200页.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post("/api/papers", {"file": upload, "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="200页.pdf")
        self.assertEqual(len(paper.pages), 200)
        self.assertFalse(paper.import_chunks.exists())
        self.assertTrue((self.temp / str(paper.id)).is_dir())

    def test_upload_book_at_100_pages_creates_one_stable_chunk(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(100)]
        upload = io.BytesIO(b"mock 100 page book")
        upload.name = "100页教材.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post(
                "/api/papers", {"file": upload, "material_type": "book", "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1",
            )
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="100页教材.pdf")
        self.assertEqual(paper.material_type, Paper.MaterialType.BOOK)
        self.assertEqual(
            list(paper.import_chunks.values_list("source_page_start", "source_page_end")),
            [(1, 100)],
        )

    def test_upload_book_over_100_pages_creates_stable_chunks(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(270)]
        upload = io.BytesIO(b"mock 270 page book")
        upload.name = "270页教材.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post(
                "/api/papers", {"file": upload, "material_type": "book", "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1",
            )
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="270页教材.pdf")
        chunks = list(paper.import_chunks.order_by("sequence"))
        self.assertEqual(
            [(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
            [(1, 100), (101, 200), (201, 270)],
        )
        self.assertEqual([page for chunk in chunks for page in chunk.page_map], list(range(1, 271)))

    def test_retry_legacy_failed_pdf_can_be_explicitly_changed_to_book(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(270)]
        failed = Paper.objects.create(
            filename="旧版失败教材.pdf",
            kind="pdf",
            sha256="l" * 64,
            source_path=str(self.temp / "legacy-book.pdf"),
            pages=pages,
            status=Paper.Status.FAILED,
            error="MinerU 解析失败（接口返回错误）",
        )
        self.assertEqual(failed.material_type, Paper.MaterialType.EXAM)

        response = self.post(
            f"/api/papers/{failed.id}/retry", {"material_type": Paper.MaterialType.BOOK},
        )

        self.assertEqual(response.status_code, 200, response.content)
        failed.refresh_from_db()
        self.assertEqual(failed.status, Paper.Status.QUEUED)
        self.assertEqual(failed.material_type, Paper.MaterialType.BOOK)
        self.assertEqual(failed.error, "")
        self.assertEqual(
            list(failed.import_chunks.values_list("source_page_start", "source_page_end")),
            [(1, 100), (101, 200), (201, 270)],
        )

    def test_failed_task_with_results_cannot_change_material_type(self):
        failed = Paper.objects.create(
            filename="已有解析结果.pdf", kind="pdf", sha256="r" * 64,
            source_path=str(self.temp / "parsed.pdf"), pages=PAGES[:1], status=Paper.Status.FAILED,
        )
        Block.objects.create(
            paper=failed, seq=0, type="text", page_idx=0, bbox=[10, 10, 20, 20], text="1. 已知",
        )
        response = self.post(
            f"/api/papers/{failed.id}/retry", {"material_type": Paper.MaterialType.BOOK},
        )
        self.assertEqual(response.status_code, 400, response.content)
        failed.refresh_from_db()
        self.assertEqual(failed.material_type, Paper.MaterialType.EXAM)
        self.assertEqual(failed.status, Paper.Status.FAILED)

    def test_upload_pdf_over_mineru_limit_creates_lossless_chunk_plan(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(201)]
        upload = io.BytesIO(b"mock PDF over the supported page limit")
        upload.name = "201页.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post("/api/papers", {"file": upload, "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="201页.pdf")
        chunks = list(ImportChunk.objects.filter(paper=paper).order_by("sequence"))
        self.assertEqual([(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
                         [(1, 200), (201, 201)])
        self.assertEqual(chunks[0].page_map, list(range(1, 201)))
        self.assertEqual(chunks[1].page_map, [201])
        self.assertTrue((self.temp / str(paper.id) / "source.pdf").is_file())

    def test_upload_pdf_exactly_1200_pages_creates_six_complete_chunks(self):
        pages = [{"page_idx": index, "width": 842, "height": 595} for index in range(1200)]
        upload = io.BytesIO(b"mock long PDF")
        upload.name = "1200页.pdf"
        with mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"}), \
                mock.patch("core.views.imaging.page_sizes", return_value=pages):
            response = self.client.post("/api/papers", {"file": upload, "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get(filename="1200页.pdf")
        chunks = list(paper.import_chunks.order_by("sequence"))
        self.assertEqual([(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
                         [(1, 200), (201, 400), (401, 600), (601, 800), (801, 1000), (1001, 1200)])
        self.assertEqual([page for chunk in chunks for page in chunk.page_map], list(range(1, 1201)))
