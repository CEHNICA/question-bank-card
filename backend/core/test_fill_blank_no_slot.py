r"""题型标成填空题，而题干里没有任何可填的空格 —— 卡是绿的、旗标也是空的。

真实数据：两本教材共 37 张，逐张看过没有一张是真填空题。八下第1题
「化简：(1)√36；(2)√(9/4)。」、第1题「一个菱形的两条对角线的长分别为 4cm 和 2cm，
求它的边长。」、九上第1题「用计算器求下列各式的值：…」—— 都是计算题或解答题，
被识读判成了填空题，其中 30 张原本是绿卡零旗标。成品卷按填空题排版时，学生只写
得出一个短答案，而这几道题要的是解题过程。

卷面大题标题那一层的对照由 `_section_kind_conflict` 负责（`core/test_section_kind.py`）。
教材卡没有大题标题，抓不到这一类。

⚠️ 「有填空位置」的正则第一版漏了三类，逐张看过 42 张候选之后补上，每一类都在
下面的测试里锁住 —— 少一类就会误报一张真填空题：
  * LaTeX 转义的下划线 `\_\_\_\_\_\_`（九上第3题 id=1168）；
  * 空括号 `(  )` / `（　）`，半角空格也算（九上第2题 id=1312）；
  * 填空位画在图里或表里，靠「填表」「填在…上」「填入」这些动词认（id=1293、id=846）。
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, qtypes
from .models import Question

READING = {
    "stem": "",
    "options": {},
    "type": "fill_blank",
    "content_kind": "exercise",
    "figures": {},
    "missing_figure": False,
    "number_seen": 1,
    "figure_descriptions": [],
    "unclear": False,
}


def snapshot(**changes):
    base = {
        "id": 1,
        "number": 1,
        "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [80, 160, 900, 240]}],
        "candidates": [],
        "question_type": "fill_blank",
        "stem": "",
        "options": {},
        "edited": False,
        "source_kind": Question.SourceKind.EXERCISE,
        "source_anchor_seq": 128,
        "segmentation_flags": [],
        "body_mode": "text",
        "start_source": "mineru",
        "witness": "",
        "witness_blocks": [
            {"seq": 128, "page_idx": 0, "bbox": [80, 160, 900, 200], "text": "1. 化简：", "type": "text"}
        ],
    }
    base.update(changes)
    return base


class FillBlankWithoutSlotTests(SimpleTestCase):
    def test_a_calculation_question_read_as_fill_blank_is_a_hit(self):
        self.assertTrue(
            qtypes.fill_blank_without_slot("fill_blank", "化简：\n（1）$\\sqrt{36}$；\n（2）$\\sqrt{\\dfrac{9}{4}}$。")
        )

    def test_a_free_response_with_several_sub_questions_is_a_hit(self):
        # 这类最容易被判成填空题：小问编号和 (1)(2) 的填空括号长得一样，
        # 但它是解答题。（）里必须有东西才算填空位。
        self.assertTrue(
            qtypes.fill_blank_without_slot(
                "fill_blank", "$x$ 取什么值时，下列各式在实数范围内有意义？\n(1) $\\sqrt{x-4}$；\n(2) $\\sqrt{1+x^2}$。"
            )
        )

    def test_another_type_is_left_alone(self):
        for kind in ("free_response", "single_choice", "multiple_choice", "true_false", "unknown"):
            self.assertFalse(qtypes.fill_blank_without_slot(kind, "化简：\n（1）$\\sqrt{36}$。"), kind)

    def test_an_escaped_underscore_is_a_slot(self):
        # 九上第3题 id=1168：LaTeX 把下划线写成 \_\_\_\_\_\_。
        self.assertFalse(
            qtypes.fill_blank_without_slot(
                "fill_blank", "已知反比例函数 $y=\\frac{m+1}{x}$ …那么 $m$ 的取值范围是 \\_\\_\\_\\_\\_\\_。"
            )
        )

    def test_an_empty_parenthesis_is_a_slot(self):
        # 九上第2题 id=1312：函数表达式后面挂一个空括号。
        self.assertFalse(
            qtypes.fill_blank_without_slot(
                "fill_blank",
                "下列二次函数在同一直角坐标系中的图象如图所示，"
                "将函数图象的序号填在对应函数表达式后面的括号里：\n"
                "( 1 ) $y=2x^2$； ( )",
            )
        )

    def test_a_slot_written_in_a_figure_or_table_is_a_slot(self):
        # 填空位画在图里（八下 id=846「填在相应的箭头上」）或表里
        # （九上 id=1293「填表表示物体在前 5s 下落的高度」）。
        for stem in (
            "将相应的条件填在相应的箭头上，使得下图能清楚地表达几种四边形之间的关系。",
            "物体从某一高度落下…填表表示物体在前 5 s 下落的高度：",
            "下列图形中各有多少个小圆圈？请填空。",
        ):
            self.assertFalse(qtypes.fill_blank_without_slot("fill_blank", stem), stem)

    def test_an_ordinary_pipe_table_is_not_a_slot(self):
        # 管道表里的空单元格不是填空位，这一格要留给「表里有空格」的那道判据。
        self.assertTrue(
            qtypes.fill_blank_without_slot(
                "fill_blank", "某商品单价如下：\n| 数量 | 1 | 2 |\n|---|---|---|\n| 单价 | 3 | 5 |"
            )
        )


class FillBlankFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem, *, kind="fill_blank", **changes):
        reading = {**READING, "stem": stem, "type": kind}
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_calculation_card_read_as_fill_blank_is_not_green(self):
        result = self.run_card("化简：\n（1）$\\sqrt{36}$；\n（2）$\\sqrt{\\dfrac{9}{4}}$。")

        self.assertIn(qtypes.FLAG_FILL_NO_SLOT, result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_real_fill_blank_stays_green(self):
        result = self.run_card("在 $\\triangle ABC$ 中，$\\angle A=30°$，则 $\\angle B=$ ______。")

        self.assertNotIn(qtypes.FLAG_FILL_NO_SLOT, result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_a_free_response_card_is_not_prompted(self):
        result = self.run_card("化简：\n（1）$\\sqrt{36}$。", kind="free_response")

        self.assertNotIn(qtypes.FLAG_FILL_NO_SLOT, result["flags"])
