"""教材栏目标题：上一道题不能把栏目正文吃进去。

教材正文里「做一做」「随堂练习」「读一读」这些栏目的正文不属于任何一道习题。
不设边界，整段栏目就会被上一道习题的范围罩过去（真库 959 张卡里 25 张的题干
混进了栏目内容，学生在成品卷上会看到一道习题后面莫名跟着一整段「读一读」）。

栏目标题里的题**不**单独成卡：它们在纸面上没有印刷题号，而
``Question.number`` 是 NOT NULL（真库 959 张卡全是 ≥1 的正整数，没有 0、没有空）。
要让它们成卡，只能编一个会和本章习题号撞车的号，或者把列改成可空、牵连下游
几十处判断。这些断言守的就是这条边界——栏目只截断，不开新卡。
"""
from django.test import SimpleTestCase

from . import segment

PAGES = [
    {"page_idx": index, "width": 1000, "height": 1400}
    for index in range(4)
]

# 八个栏目标题全部只做边界
COLUMNS = ["做一做", "随堂练习", "议一议", "想一想",
           "读一读", "问题解决", "回顾与思考", "综合与实践"]


def block(seq, page, y, text, kind="text", x0=80, x1=920, height=45):
    return {
        "seq": seq,
        "type": kind,
        "page_idx": page,
        "bbox": [x0, y, x1, y + height],
        "text": text,
    }


def covers(regions, page, y):
    return any(
        item["page_idx"] == page and item["bbox"][1] <= y <= item["bbox"][3]
        for item in regions
    )


def practice_page(header, column_text="在图中画出函数的图象。", top=60):
    """一页习题区：习题标题 + 第 1 题 + 栏目标题 + 栏目正文。"""
    return [
        block(0, 0, top, "习题 1.1"),
        block(1, 0, top + 60, "1. 计算下列各式的值。"),
        block(2, 0, top + 240, header),
        block(3, 0, top + 320, column_text),
    ]


class ColumnHeaderBoundaryTests(SimpleTestCase):
    def test_every_column_stops_the_previous_exercise(self):
        """八个栏目标题都是边界，且都不额外开新卡。"""
        for header in COLUMNS:
            with self.subTest(header=header):
                result = segment.segment_book(PAGES[:1], practice_page(header))

                self.assertEqual(len(result["questions"]), 1,
                                 f"「{header}」不该自己开一道新卡")
                first = result["questions"][0]
                self.assertFalse(covers(first["regions"], 0, 300),   # 栏目标题
                                 f"「{header}」应结束上一题的范围")
                self.assertFalse(covers(first["regions"], 0, 380))   # 栏目正文

    def test_column_question_with_its_own_printed_number_is_still_kept(self):
        """栏目正文自己印着题号时，那道题照常成卡，边界也不能把它吃掉。"""
        blocks = practice_page("随堂练习", "1. 这道题自己就印着题号 1。")

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(len(result["questions"]), 2)
        anchors = [q["source_anchor_seq"] for q in result["questions"]]
        self.assertEqual(len(anchors), len(set(anchors)), f"锚点重复：{anchors}")
        self.assertEqual(result["questions"][1]["number"], 1)
        self.assertTrue(covers(result["questions"][1]["regions"], 0, 380))

    def test_every_card_keeps_a_positive_printed_number(self):
        """Question.number 是 NOT NULL，真库 959 张全是 ≥1 的正整数。

        栏目标题成卡就必须有个号，而纸面上没有，所以它们不成卡；剩下的每一张
        都必须带着真实题号落库。
        """
        blocks = practice_page("做一做") + [block(4, 0, 600, "2. 化简下列根式。")]

        result = segment.segment_book(PAGES[:1], blocks)

        numbers = [q["number"] for q in result["questions"]]
        self.assertEqual(numbers, [1, 2])
        for value in numbers:
            self.assertIsInstance(value, int)
            self.assertGreaterEqual(value, 1)

    def test_bold_marker_block_is_still_a_header(self):
        """MinerU 常把栏目标题读成粗体，去掉 ``**`` 后仍要认。"""
        result = segment.segment_book(PAGES[:1], practice_page("**做一做**"))

        self.assertEqual(len(result["questions"]), 1)
        self.assertFalse(covers(result["questions"][0]["regions"], 0, 380))

    def test_header_does_not_close_the_practice_area(self):
        """栏目标题夹在习题区中间时，后面的编号题仍然是题。"""
        blocks = practice_page("做一做") + [block(4, 0, 600, "2. 化简下列根式。")]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual([q["number"] for q in result["questions"]], [1, 2])

    def test_header_on_the_last_page_line_opens_no_question(self):
        """栏目标题排在页末时，下一块在下一页，不会被拉进这道卡。"""
        blocks = [
            block(0, 0, 60, "习题 1.1"),
            block(1, 0, 120, "1. 计算下列各式的值。"),
            block(2, 0, 1300, "做一做"),
            block(3, 1, 100, "（1）在图中画出函数的图象。"),
        ]

        result = segment.segment_book(PAGES[:2], blocks)

        self.assertEqual(len(result["questions"]), 1)
        self.assertFalse(covers(result["questions"][0]["regions"], 1, 100))

    def test_plain_text_mentioning_a_column_is_not_a_header(self):
        """只有独占一块的栏目标题才算边界，正文里提到它不算。

        若把「做一做」两个字当成栏目标题，这道题就会在提到它的地方被切断。
        """
        blocks = [
            block(0, 0, 60, "习题 1.1"),
            block(1, 0, 120, "1. 请回答下面这个问题，它需要写满二十四个字以上"
                              "才不会被当成小节标题，进而影响这道题的完整范围。"),
            block(2, 0, 300, "这是第一题的续行，里面提到了做一做这两个字。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(result["questions"][0]["source_anchor_seq"], 1)
        self.assertTrue(covers(result["questions"][0]["regions"], 0, 300))

    def test_anchors_stay_unique_with_many_columns_on_one_page(self):
        """一页里连着好几个栏目，锚点仍然唯一且不为空。"""
        blocks = [
            block(0, 0, 40, "习题 1.1"),
            block(1, 0, 100, "1. 计算下列各式的值。"),
            block(2, 0, 200, "做一做"),
            block(3, 0, 260, "在图中画出函数的图象。"),
            block(4, 0, 400, "随堂练习"),
            block(5, 0, 460, "1. 下面哪一组是反比例函数？请说明理由。"),
            block(6, 0, 600, "2. 化简下列根式。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        anchors = [q["source_anchor_seq"] for q in result["questions"]]
        self.assertNotIn(None, anchors)
        self.assertEqual(len(anchors), len(set(anchors)), f"锚点重复：{anchors}")
        self.assertEqual([q["number"] for q in result["questions"]], [1, 1, 2])
