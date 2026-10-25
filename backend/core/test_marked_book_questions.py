r"""教材把难题、拓展题印成 `※3.` `*5.`：这些题以前从来没有被切成卡。

真实案例（数学八年级下册第 41 页，逐块对着原页核过）：
    习题 7.1
      知识技能
        1. a 是怎样的实数时，下列各式在实数范围内有意义？……
        2.计算：（1）$(\sqrt{16})^2$ （2）$(-3\sqrt{1/6})^2$ 。
      数学理解
        ※3. 计算：
        （1）$(\sqrt{3a^3})^2$（$a\geq 0$）；（2）$(2\sqrt{5b+1})^2$（$b\geq -\frac15$）。
`BOOK_NUMBERED_RE` 只认裸的 `N.`，`※3.` 压根不是题号，于是：

  * ※3 永远不会被切成卡 —— 这道题在库里不存在；
  * 第 2 题的范围一路罩到本页末尾、还跨到下一页的「议一议」，识读只把 ※3 的
    正文写进了第 2 题的卡；
  * 印在 ※3 旁边的配图挂在第 2 题的候选里。

`※N.` 里的 N 就是这道习题里真正的序号（两本教材 27 个带标记题号，27/27 都刚好
是前一个印刷题号 +1，没有一个重新编号），所以认出它不会和同组的裸号撞号。
"""
from django.test import SimpleTestCase

from . import segment

PAGES = [{"page_idx": index, "width": 1000, "height": 1400} for index in range(3)]


def block(seq, page, y, text, kind="text", x0=80, x1=920, height=45):
    return {"seq": seq, "type": kind, "page_idx": page,
            "bbox": [x0, y, x1, y + height], "text": text}


def contains(regions, page, y):
    return any(item["page_idx"] == page and item["bbox"][1] <= y <= item["bbox"][3]
               for item in regions)


# 数学八年级下册第 41 页，坐标与 MinerU 给的一致（0-1000 归一化）
PAGE41 = [
    block(731, 0, 49, "第七章二次根式", kind="header", height=35),
    block(734, 0, 328, "习题 7.1"),
    block(736, 0, 375, "知识技能"),
    block(737, 0, 411, "1. a 是怎样的实数时，下列各式在实数范围内有意义？"
                       "（1） $\\sqrt { 2 - a } $ ；", height=63),
    block(738, 0, 476, "2.计算：（1） $( { \\sqrt { 1 6 } } ) ^ { 2 } $ "
                       "（2） $( - 3 \\sqrt { \\frac { 1 } { 6 } } ) ^ { 2 }$ 。"),
    block(740, 0, 559, "数学理解"),
    block(741, 0, 600, "※3. 计算："),
    block(742, 0, 625, "（1） $( \\sqrt { 3 a ^ { 3 } } ) ^ { 2 } \\left( a \\geqslant 0 \\right)$ ；",
           height=22),
    block(743, 0, 620, "（2） $( 2 \\sqrt { 5 b + 1 } ) ^ { 2 } ( b \\geqslant - \\frac { 1 } { 5 } )$",
           x0=535, height=31),
    block(745, 0, 722, "<sup>2</sup> 二次根式的性质", x1=505, height=48),
    block(747, 0, 828, "议一论".replace("论", "议")),
    block(748, 0, 864, "（1）计算： $\\sqrt { 2 ^ { 2 } } , \\sqrt { 3 ^ { 2 } } $ 。你发现了什么？"),
    block(749, 0, 905, "（2）猜一猜：当 $a \\geqslant 0$ 时，二次根式 $\\sqrt { a ^ { 2 } }$ 的值是什么？"),
]

# 下一页的开头正文：真实书上它跟着第 2 节，不是题。
PAGE42 = [
    block(751, 1, 110, "当 $a \\geqslant 0$ 时， ${ \\sqrt { a ^ { 2 } } } = a$"),
    block(752, 1, 163, "利用二次根式的这一性质，我们可以计算、化简一些二次根式。"),
]


class MarkedBookQuestionTests(SimpleTestCase):
    def cut(self):
        return segment.segment_book(PAGES[:2], PAGE41 + PAGE42)["questions"]

    def test_the_marked_question_becomes_a_card_of_its_own(self):
        questions = self.cut()

        self.assertIn(("exercise", 3), [(item["source_kind"], item["number"]) for item in questions])
        third = next(item for item in questions if item["number"] == 3)
        self.assertEqual(third["source_anchor_seq"], 741, "锚点必须是 ※3. 那一块")

    def test_the_question_above_no_longer_swallows_it(self):
        questions = self.cut()

        second = next(item for item in questions if item["number"] == 2)
        self.assertFalse(contains(second["regions"], 0, 600), "第 2 题的范围罩住了 ※3")
        self.assertFalse(contains(second["regions"], 0, 625))
        self.assertEqual([region["page_idx"] for region in second["regions"]], [0],
                         "第 2 题不该再一路罩到下一页的正文")

    def test_the_marked_question_gets_its_own_body(self):
        questions = self.cut()

        third = next(item for item in questions if item["number"] == 3)
        self.assertTrue(contains(third["regions"], 0, 600))
        self.assertTrue(contains(third["regions"], 0, 625))

    def test_plain_numbers_still_cut_the_same_way(self):
        questions = self.cut()

        self.assertEqual([item["number"] for item in questions], [1, 2, 3])


class MarkedBookFigureTests(SimpleTestCase):
    """八下第 28 页「联系拓广」的 ※4：配图就印在它旁边，图注写着「第 4 题」。"""

    def test_the_figure_belongs_to_the_marked_question(self):
        blocks = [
            block(0, 0, 100, "习题 16.1"),
            block(1, 0, 150, "知识技能"),
            block(2, 0, 190, "2. 已知：如图，在 $\\triangle ABC$ 中，$AB=AC$。"),
            block(3, 0, 300, "", kind="image", x0=700, x1=920, height=150),
            block(4, 0, 470, "联系拓广"),
            block(5, 0, 520, "※4. 如图，在矩形 $ABCD$ 中，$AB = 3$, $AD = 4$，"
                             "过点 $P$ 分别作 $AC$ 和 $BD$ 的垂线，求 $PE + PF$ 的值。",
                   height=70),
            block(6, 0, 540, "", kind="image", x0=120, x1=500, height=200),
        ]

        questions = segment.segment_book(PAGES[:1], blocks)["questions"]
        fourth = next(item for item in questions if item["number"] == 4)
        second = next(item for item in questions if item["number"] == 2)

        self.assertEqual([int(candidate["seq"]) for candidate in fourth["figure_candidates"]],
                         [6], "※4 的配图应当挂在 ※4 上")
        self.assertNotIn(6, [int(candidate["seq"]) for candidate in second["figure_candidates"]])


class SingleStarMarkerTests(SimpleTestCase):
    def test_a_starred_harder_question_is_cut_too(self):
        blocks = [
            block(0, 0, 100, "习题 18.2"),
            block(1, 0, 150, "4. 一元二次方程的根与系数有怎样的关系？"),
            block(2, 0, 250, "*5. 判断下列各式的值。"),
        ]

        questions = segment.segment_book(PAGES[:1], blocks)["questions"]

        self.assertEqual([item["number"] for item in questions], [4, 5])
