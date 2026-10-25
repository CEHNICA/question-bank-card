r"""印刷的解答并进了题干，学生做卷子时答案就摆在题后面。

真实案例（两本教材，逐张对着原页核过）：
  八下第 4 题，纸上印着
    4. 如图，在菱形 ABCD 中，对角线 AC 与 BD 交于点 O，图中有多少个等腰三角形和直角三角形？
  卡上却是
    …求证：▱ABCD 是菱形。 证明：∵ 四边形 ABCD 是平行四边形，∴ …

  九上例 1，例题本来有一条机制会停在印刷解答上面，可 MinerU 把「解：在 Rt△ABC 中，」
  写进了公式块（`$$\begin{array}…{\text{解：在}…}…$$`），解答边界没认出来，解答就留在了
  题干里。同一页的例 2 因为解答是文字块，反而正常。

⚠️ 「证明：」有两种完全不同的来历，八下有 5 张卡的题干本身就是一道证明题：
  ``2.证明：一条对角线平分一内角的平行四边形是菱形。`` —— 「证明：」是它的动词。
  锚点那一行里没有解答标签时，题干里出现的才算数。
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

# 八下第 4 题：题号那一行是数图形个数，后面却跟着一道证明题和它的印刷解答
PRINTED = "4. 如图，在菱形 ABCD 中，对角线 AC 与 BD 交于点 O，图中有多少个等腰三角形和直角三角形？"
STEM_WITH_SOLUTION = (
    "如图，在菱形 ABCD 中，对角线 AC 与 BD 交于点 O，图中有多少个等腰三角形和直角三角形？\n"
    "已知：如图 6-3，在 ▱ABCD 中，AC ⊥ BD。\n求证：▱ABCD 是菱形。\n"
    "证明：∵ 四边形 ABCD 是平行四边形，∴ OA = OC。"
)

# 八下第 2 题：题干本身就是一道证明题
PRINTED_PROOF_QUESTION = "2.证明：一条对角线平分一内角的平行四边形是菱形。"
STEM_PROOF_QUESTION = "证明：一条对角线平分一内角的平行四边形是菱形。"


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
        "source_anchor_seq": 128,
        "segmentation_flags": [],
        "body_mode": "text",
        "start_source": "mineru",
        "witness": "",
        "witness_blocks": [{"seq": 128, "page_idx": 0, "bbox": [80, 160, 900, 200], "text": PRINTED, "type": "text"}],
    }
    base.update(changes)
    return base


class PrintedSolutionInStemTests(SimpleTestCase):
    def test_a_foreign_solution_is_found(self):
        found = textnorm.printed_solution_in_stem(PRINTED, STEM_WITH_SOLUTION)

        self.assertIsNotNone(found)
        self.assertIn("证明", found)

    def test_a_question_that_is_itself_a_proof_is_not_a_hit(self):
        self.assertIsNone(textnorm.printed_solution_in_stem(PRINTED_PROOF_QUESTION, STEM_PROOF_QUESTION))

    def test_the_starred_marker_before_the_number_is_stripped(self):
        # 教材用 ※ 标拓展题，锚点行是 `※3.证明：…`，同样不算答案
        self.assertIsNone(
            textnorm.printed_solution_in_stem(
                "※3.证明：对角线互相垂直的矩形是正方形。", "证明：对角线互相垂直的矩形是正方形。"
            )
        )

    def test_a_question_asking_students_to_complete_a_solution_is_left_alone(self):
        # 试卷上就有这种题：「请你把它补充完整」，后面跟着学生的部分解答
        printed = "25.（本题满分10分）"
        stem = "……(1) 以下是他的部分求解过程，请你把它补充完整．\n解：∵一个三角形边长依次为5,6,7，"
        found = textnorm.printed_solution_in_stem(printed, stem)
        self.assertIsNotNone(found, "题干自己带解答段落时仍会提示一次，由人判断")

    def test_no_printed_line_is_not_a_hit(self):
        self.assertIsNone(textnorm.printed_solution_in_stem("", "任意内容"))


class PrintedSolutionFlagTests(SimpleTestCase):
    """接线：read_card 真要把这条提示挂到卡上。"""

    store = staticmethod(type("Store", (), {"load": lambda self, page: object()})())

    def run_card(self, stem, **changes):
        # 识读看到的题号就是这张卡的题号：夹具里给死会串到「AI 看到的题号是 N」那条旗上，
        # 反而盖住这里要验的东西。
        reading = {**READING, "stem": stem, "number_seen": changes.get("number", READING["number_seen"])}
        with (
            patch.object(pipeline.readers, "primary_engine", return_value=object()),
            patch.object(pipeline.readers, "read_question", return_value=reading),
            patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])),
            patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,x"),
        ):
            return pipeline.read_card(snapshot(**changes), self.store)

    def test_a_card_carrying_a_printed_solution_is_not_green(self):
        result = self.run_card(STEM_WITH_SOLUTION)

        self.assertTrue(any("混进了印刷的解答" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.YELLOW)

    def test_a_proof_question_stays_green(self):
        result = self.run_card(
            STEM_PROOF_QUESTION,
            number=2,
            witness_blocks=[
                {"seq": 128, "page_idx": 0, "bbox": [80, 160, 900, 200], "text": PRINTED_PROOF_QUESTION, "type": "text"}
            ],
        )

        self.assertFalse(any("混进了印刷的解答" in flag for flag in result["flags"]), result["flags"])
        self.assertEqual(result["state"], Question.State.GREEN)

    def test_the_same_sentence_reworded_only_reports_the_solution(self):
        """题号那行的话识读认得出来时，不能顺带报「卡上可能是另一道题」。

        两条判据挂在同一条链上。真实场景：本题原文照抄、后面粘着下一题的印刷解答
        （八下第 4 题 id=781）。锚点那行认得出，只该报解答这一条 —— 报两条等于
        让老师去查一个不存在的问题。
        """
        stem = (
            "如图，在菱形 ABCD 中，对角线 AC 与 BD 交于点 O，"
            "图中有多少个等腰三角形和直角三角形？\n"
            "证明：∵ 四边形 ABCD 是平行四边形，∴ OA = OC。"
        )
        result = self.run_card(stem)

        self.assertTrue(any("混进了印刷的解答" in flag for flag in result["flags"]), result["flags"])
        self.assertFalse(any("卡上可能是另一道题" in flag for flag in result["flags"]), result["flags"])
        # 这一条只报一次：题干第一行是本题原话，本来就不该再被当成解答。
        self.assertEqual(sum("混进了印刷的解答" in flag for flag in result["flags"]), 1, result["flags"])

    def test_a_wholly_different_question_reports_both_flags(self):
        """反过来：两条都该报时，一条都不能被另一条吞掉。"""
        result = self.run_card(
            "在一块长 16 m、宽 12 m 的矩形土地上，要建造一个花园。\n证明：∵ 四边形 ABCD 是平行四边形，∴ OA = OC。"
        )

        self.assertTrue(any("卡上可能是另一道题" in flag for flag in result["flags"]), result["flags"])
        self.assertTrue(any("混进了印刷的解答" in flag for flag in result["flags"]), result["flags"])

    def test_an_anchor_that_starts_with_the_solution_label_is_left_alone(self):
        """守门的第二条分支：答案版把卡锚在解答上时，题干里的解答是该有的内容。

        库里目前 0 样本（胜利初四答案版本那 4 处「答：」都不是任何卡的锚点），
        留着是为了它真出现时不误报。
        """
        self.assertIsNone(textnorm.printed_solution_in_stem("答：该书的销售单价为25元.", "答：该书的销售单价为25元."))

    def test_a_section_heading_in_brackets_is_not_a_solution(self):
        """教材用【深入探究】这类小节标题，它和【答案】同一副壳，但不是解答。

        全库 9 处小节标题、其中 4 处真的进了题干。认了它就换来一批误报、0 收益。
        """
        stem = (
            "【问题背景】\n某商场促销…\n【深入探究】\n(2) 如图 2，若将图 1 中的 "
            "$\\triangle DBE$ 绕点 $B$ 旋转一定的角度…"
        )
        self.assertIsNone(textnorm.printed_solution_in_stem(PRINTED, stem))
