"""教材栏目标题：上一道题不能把栏目正文吃进去，栏目里的题要能单独成卡。

「做一做」「随堂练习」里的题既没有印刷题号，编号也是「（1）①②③」这种形式，
``NUMBER_RE`` 和 ``BOOK_NUMBERED_RE`` 都不认，两本书 314 个这样的栏目标题块
原本一个都不会成为起点：页面上没有卡，或者整段栏目正文被上一道习题的范围
罩过去（真库 959 张卡里 25 张的题干混进了栏目内容）。

这些断言守的是 ``_column_markers`` 产出的两类 marker：
``column_header`` 只当边界，``column_question`` 才开新题。
"""
from django.test import SimpleTestCase

from . import segment


PAGES = [
    {"page_idx": index, "width": 1000, "height": 1400}
    for index in range(4)
]

# 只当边界、不开新题的栏目
BOUNDARY_ONLY = ["议一议", "想一想", "读一读", "问题解决", "回顾与思考", "综合与实践"]
# 既当边界、又开新题的栏目
COLUMN_QUESTIONS = ["做一做", "随堂练习"]


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
    def test_exercise_stops_before_the_column(self):
        """上一道习题的范围不能罩住栏目标题和它的正文。"""
        result = segment.segment_book(PAGES[:1], practice_page("做一做"))

        self.assertEqual(len(result["questions"]), 2)
        first = result["questions"][0]
        self.assertFalse(covers(first["regions"], 0, 300))   # 「做一做」
        self.assertFalse(covers(first["regions"], 0, 380))   # 栏目正文

    def test_column_question_becomes_its_own_card(self):
        """「做一做」后面的第一个内容块是一道独立的题。"""
        result = segment.segment_book(PAGES[:1], practice_page("做一做"))

        second = result["questions"][1]
        self.assertEqual(second["source_anchor_seq"], 3)
        self.assertEqual(second["number"], 0)          # 纸上没有印刷题号
        self.assertEqual(second["start"]["source_kind"], "exercise")
        self.assertTrue(covers(second["regions"], 0, 380))
        self.assertFalse(covers(second["regions"], 0, 120))  # 不倒吃上一题

    def test_practice_choice_works_too(self):
        result = segment.segment_book(PAGES[:1], practice_page("随堂练习"))
        self.assertEqual(len(result["questions"]), 2)
        self.assertEqual(result["questions"][1]["source_anchor_seq"], 3)

    def test_column_with_inline_choices_is_one_card(self):
        """选项和题干挤在一个块里（九上第 128 页那类四选一）也要成卡。"""
        blocks = [
            block(0, 0, 60, "习题 4.1"),
            block(1, 0, 120, "1. 下列说法正确的是（ ）"),
            block(2, 0, 300, "随堂练习"),
            block(3, 0, 380,
                  "如图是某一天上午不同时刻同一根旗杆的影子，则按时间先后排列应为（ ）。"
                  "（A）①②③④ （B）④③②① （C）④①③② （D）②③④①"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(len(result["questions"]), 2)
        self.assertEqual(result["questions"][1]["source_anchor_seq"], 3)

    def test_bold_marker_block_is_still_a_header(self):
        """MinerU 常把栏目标题读成粗体，去掉 ``**`` 后仍要认。"""
        result = segment.segment_book(PAGES[:1], practice_page("**做一做**"))
        self.assertEqual(len(result["questions"]), 2)
        self.assertFalse(covers(result["questions"][0]["regions"], 0, 380))

    def test_other_columns_only_stop_the_previous_exercise(self):
        """「读一读」「议一议」这些是文章或讨论：只截断，不开新题。"""
        for header in BOUNDARY_ONLY:
            with self.subTest(header=header):
                result = segment.segment_book(PAGES[:1], practice_page(header))

                self.assertEqual(len(result["questions"]), 1)
                self.assertFalse(covers(result["questions"][0]["regions"], 0, 380))

    def test_header_does_not_close_the_practice_area(self):
        """栏目标题夹在习题区中间时，后面的编号题仍然是题。"""
        blocks = practice_page("做一做") + [block(4, 0, 600, "2. 化简下列根式。")]

        result = segment.segment_book(PAGES[:1], blocks)

        numbers = [item["number"] for item in result["questions"]]
        self.assertIn(2, numbers)

    def test_header_ending_a_page_opens_no_question(self):
        """栏目标题排在页末时，下一块在下一页，不再认作它的题。"""
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

        若句中的「做一做」被当成栏目标题，紧随其后的块就会被开成第二道题。
        """
        blocks = [
            block(0, 0, 60, "习题 1.1"),
            block(1, 0, 120, "1. 做一做里的第 1 小问，请对照上面那张表格，"
                              "把三个空都填上，并说明每一步的理由。"),
            block(2, 0, 300, "这是同一道题的续行。"),
        ]

        result = segment.segment_book(PAGES[:1], blocks)

        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(result["questions"][0]["source_anchor_seq"], 1)