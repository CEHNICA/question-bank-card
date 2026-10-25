r"""识读把下一节的正文当成了题干，而卡看上去很正常、旗标也是空的。

真实案例（两本教材，逐张对着原页核过，这几张都是绿卡、零旗标）：
  八下第 4 题，纸上印着
    4. 如果一个三角形两边的长分别等于一元二次方程 $x^2 - 17x + 66 = 0$ 的两个实数根…
  卡上写着
    在一块长 16 m、宽 12 m 的矩形土地上，要建造一个花园…
  九上第 3 题，纸上印着
    3. 已知矩形的面积为 9，试用图象表示出这个矩形两邻边之间的关系。
  卡上写着
    某校科技小组进行野外考察，利用铺垫木板的方式通过了一片烂泥湿地…

最后一道题的范围常常一路罩到下一节正文，识读就把那段正文读成了题干。

判据两边都只用文字：这张卡题号那一行印的话，和识读写出来的题干/选项/解析。
不依赖切题结果，也不依赖模型的自述。

⚠️ 比对必须容得下**改写**：模型会把「化去根号内的分母」读成「化简下列各式根号
内的分母」。第一版只判「取样不在读出来的文字里」，全库咬到 58 张，绝大多数是
这种改写；收紧成「连 4 个字都重合不上」之后是 25 张，试卷上 0 张，逐张看过
没有误报。
"""

from unittest.mock import patch

from django.test import SimpleTestCase

from . import pipeline, textnorm
from .models import Question

READING = {
    "stem": "",
    "options": {},
    "content_kind": "question",
    "figures": {},
    "missing_figure": False,
    "number_seen": 4,
    "figure_descriptions": [],
    "unclear": False,
}

# 数学八年级下册第 132 页，习题里那道印在卡上的第 4 题
PRINTED = (
    "4. 如果一个三角形两边的长分别等于一元二次方程 $x^{2}-17x+66=0$ 的两个实数根，"
    "那么这个三角形的第三边的长可能是20、21、22中的哪个"
)


def snapshot(**changes):
    base = {
        "id": 1,
        "number": 4,
        "group_id": None,
        "regions": [{"page_idx": 0, "bbox": [80, 160, 900, 240]}],
        "candidates": [],
        "question_type": "free_response",
        "stem": "",
        "options": {},
        "edited": False,
        "source_kind": Question.SourceKind.EXERCISE,
        "source_anchor_seq": 1884,
        "segmentation_flags": [],
        "body_mode": "text",
        "start_source": "mineru",
        "witness": "",
        "witness_blocks": [{"seq": 1884, "page_idx": 0, "bbox": [80, 160, 900, 200], "text": PRINTED, "type": "text"}],
    }
    base.update(changes)
    return base


class AnchorReadingMismatchTests(SimpleTestCase):
    def test_a_completely_different_question_is_found(self):
        reading = "在一块长 $16\\text{ m}$、宽 $12\\text{ m}$ 的矩形土地上，要建造一个花园。"
        self.assertTrue(textnorm.anchor_reading_mismatch(PRINTED, reading))

    def test_a_rewritten_but_the_same_question_is_not_a_hit(self):
        # 模型把「化去根号内的分母」读成「化简下列各式根号内的分母」
        printed = "5. 化去根号内的分母：（1）$\\sqrt{\\dfrac{1}{3}}$；（2）$\\sqrt{\\dfrac{2}{11}}$。"
        reading = "化简下列各式根号内的分母：\n（1）$\\sqrt{\\dfrac{1}{3}}$；（2）$\\sqrt{\\dfrac{2}{11}}$。"
        self.assertIsNone(textnorm.anchor_reading_mismatch(printed, reading))

    def test_only_the_first_question_in_a_merged_block_counts(self):
        # 八下第 2 题那块版面里还塞着「3. 分别说说判定一个四边形…」
        printed = (
            "2. 在菱形、矩形、正方形中，哪些图形是轴对称图形？哪些图形是中心对称图形？"
            "3. 分别说说判定一个四边形是菱形、矩形、正方形的条件。"
        )
        reading = "在菱形、矩形、正方形中，哪些图形是轴对称图形？哪些图形是中心对称图形？"
        self.assertIsNone(textnorm.anchor_reading_mismatch(printed, reading))

    def test_a_heading_like_printed_line_has_nothing_to_look_for(self):
        self.assertIsNone(textnorm.anchor_reading_mismatch("3. 计算：", "任意内容"))

    def test_no_printed_line_is_not_a_hit(self):
        self.assertIsNone(textnorm.anchor_reading_mismatch("", "任意内容"))

    def test_the_words_can_live_in_an_option(self):
        printed = "4. “两个三角形全等”是“两个三角形相似”的（　　）\nA. 充分不必要 B. 必要不充分"
        reading = "“两个三角形全等”是“两个三角形相似”的（　　）\nA. 充分不必要 B. 必要不充分"
        self.assertIsNone(textnorm.anchor_reading_mismatch(printed, reading))


# 真库原样：《5年高考3年模拟 A版 数学》能力拔高练第 3 题（题有据 #1096）。
# 教辅在题前印出处，这一句比题干还长，早先被当题目文字取样；而它早就被单独拆进
# 「题源」字段了，压根不在题干里 —— 于是这张卡被报成「认不出这句话，卡上可能是
# 另一道题」。真库 10 张里 9 张是这么误报的（#1085~#1099，同一份卷子）。
SOURCE_NOTE_PRINTED = (
    "3.(2025届江苏盐城射阳中学开学考,6)函数 $f(x)=1+\\ln x$ 与 $g(x)=\\mathrm{e}^{x}-1$ 图象的公切线的\n"
    "纵截距为\nA.1 或 0 B.-1 或 0 C.1 或 e D.-1 或 e"
)
SOURCE_NOTE_READING = (
    "函数 $f(x)=1+\\ln x$ 与 $g(x)=e^x-1$ 图象的公切线的纵截距为$1$ 或 $0$$-1$ 或 $0$$1$ 或 $e$$-1$ 或 $e$"
)
SOURCE_NOTE_ORIGIN = "2025届江苏盐城射阳中学开学考，6"


class SourceNoteIsNotTheQuestionTests(SimpleTestCase):
    """出处那一句不能拿来当题目内容比对。"""

    def test_the_printed_source_note_alone_does_not_make_a_hit(self):
        # 不给「题源」字段：出处就在印刷行开头那对括号里，也得认出来。
        self.assertIsNone(textnorm.anchor_reading_mismatch(SOURCE_NOTE_PRINTED, SOURCE_NOTE_READING))

    def test_the_source_note_in_the_origin_field_is_skipped_too(self):
        self.assertIsNone(
            textnorm.anchor_reading_mismatch(SOURCE_NOTE_PRINTED, SOURCE_NOTE_READING, SOURCE_NOTE_ORIGIN)
        )

    def test_a_line_holding_nothing_but_a_source_note_has_nothing_to_compare(self):
        printed = "3.(2025届江苏盐城射阳中学开学考,6)"
        self.assertIsNone(textnorm.anchor_reading_mismatch(printed, "", SOURCE_NOTE_ORIGIN))

    def test_the_probe_lands_on_the_question_itself_not_on_the_source_note(self):
        # 锚点那行印的是第 7 题，卡上装的是第 1 题（真库 #1100）：出处和题干都要认得出，
        # 判据照样报 —— 剔除出处不是为了放过真问题。
        printed = (
            "7.(2025届江苏淮安十校联考,14)若函数 $f(x)=\\frac{k}{x},k>0$ 的图象与 $y=\\ln x$ 和 "
            "$y=e^x$ 的图象分别交于 $A(x_1,y_1),B(x_2,y_2)$ 两点"
        )
        reading = "（创新考法）（2024江苏南通一中月考，6）牛顿迭代法是求方程近似解的一种方法．如图，方程 $f(x)=0$ 的根"
        probe = textnorm.anchor_reading_mismatch(printed, reading, "")
        self.assertTrue(probe, "卡上装的是另一道题，必须报出来")
        self.assertNotIn("淮安", probe)

    def test_a_rewritten_question_is_still_not_a_hit(self):
        printed = "5. 化去根号内的分母：（1）$\\sqrt{\\dfrac{1}{3}}$"
        reading = "化简下列各式根号内的分母：（1）$\\sqrt{\\dfrac{1}{3}}$"
        self.assertIsNone(textnorm.anchor_reading_mismatch(printed, reading))


class AnchorReadingFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem, **changes):
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value={**READING, "stem": stem}),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_card_holding_another_question_is_not_green(self):
        result = self.run_card("在一块长 $16\\text{ m}$、宽 $12\\text{ m}$ 的矩形土地上，要建造一个花园。")

        self.assertTrue(any("认不出这句话" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_the_same_question_stays_green(self):
        result = self.run_card(
            "如果一个三角形两边的长分别等于一元二次方程 $x^{2}-17x+66=0$ 的两个实数根，那么第三边可能是多少？"
        )

        self.assertFalse(any("认不出这句话" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_a_card_without_a_source_anchor_is_left_alone(self):
        result = self.run_card(
            "在一块长 $16\\text{ m}$ 的矩形土地上，要建造一个花园。", source_anchor_seq=None, witness_blocks=[]
        )

        self.assertFalse(any("认不出这句话" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_a_card_whose_source_note_was_split_off_is_not_asked_about(self):
        """接线：出处被拆进「题源」之后，那张卡不该再被说成「认不出这句话」。

        真库 #1096：《5年高考3年模拟 A版 数学》第 3 题。识读回来的题干开头带着
        出处，它会被拆进「题源」；印刷那行的最长一段恰好也是这句出处。
        """
        result = self.run_card(
            "（2025届江苏盐城射阳中学开学考，6）函数 $f(x)=1+\\ln x$ 与 $g(x)=e^x-1$ 图象的公切线的纵截距为",
            witness_blocks=[
                {"seq": 1884, "page_idx": 0, "bbox": [80, 160, 900, 200], "text": SOURCE_NOTE_PRINTED, "type": "text"}
            ],
        )

        self.assertFalse(any("认不出这句话" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)
