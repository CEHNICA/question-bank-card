r"""卡号那一行印的话，识读出来的内容里认不出——卡却看上去完全正常。

真实数据（真库 959 张，2026-10-11）：25 张现在符合 ``anchor_reading_mismatch``
的判据，**25 张全都没挂上那句话**，其中 5 张还是 ``green``、旗标为空：

  八下第 2 题 #965  纸上印的是农业合作社储藏农产品那道，卡上却是章末回顾
  九上第 2 题 #1153 纸上印的是「下列函数的图象位于第一、三象限的有」，卡上是整段
                    「读一读 反比例函数与三等分角」
  九上第 3 题 #1156 纸上印的是「矩形面积为 9 试用图象表示两邻边关系」，卡上是
                    「科技小组野外考察…压强 p」
  八下第 4 题 #956  纸上印的是三角形第三边那道，卡上是矩形土地建花园
  八下第 4 题 #1069 纸上印的是 12 个直角三角形拼成的图形，卡上是四边形坐标

这 5 张印在学生卷上和正常题一模一样，界面上也看不出异常。

⚠️ 接线点不在 ``read_card``：那条判据只在那一次识读里算，存量卡是在它存在之前
读出来的（或者读完之后又被别处的改动动过），就永远没有这句话。这里补的是这个口子，
范式照 ``reconcile_geometric_figure_flags``：只加旗标，一个字也不改。
"""
from unittest import mock

from django.test import TestCase

from . import pipeline
from .models import Block, Paper, Question, QuestionGroup


class AnchorReadingFlagReconcileTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="数学九年级上册.pdf", kind="pdf",
            material_type=Paper.MaterialType.BOOK, sha256="e" * 64,
            source_path="missing-source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400}],
            status=Paper.Status.READY,
        )
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第三章", sequence=1,
            page_start=1, page_end=1, metadata={"pages": [0]},
        )
        self.counter = 0
        Block.objects.create(
            paper=self.paper, seq=671, type="text", page_idx=0,
            bbox=[100, 200, 900, 260],
            text="20.已知两条对角线，利用尺规作一个菱形。※21.如图，画一个矩形 EFGH。")

    def card(self, **values):
        self.counter += 1
        base = dict(
            paper=self.paper, group=self.group, number=self.counter,
            # 九上第 2 题 #1153 的真实情形：卡号对的是「下列函数…」，卡上却是整段读一读。
            stem="读一读  反比例函数与三等分角  同学们都知道，关于尺规作图有著名的“三大不能”。",
            regions=[{"page_idx": 0, "bbox": [100, 200, 900, 900]}],
            processing_mode="auto", body_mode="text", source_anchor_seq=671,
            state=Question.State.GREEN, flags=[], figures=[], figure_review={},
        )
        base.update(values)
        return Question.objects.create(**base)

    def test_a_green_card_holding_another_question_is_asked_about(self):
        card = self.card()

        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 1)

        card.refresh_from_db()
        self.assertEqual(len(card.flags), 1, card.flags)
        self.assertIn("卡上可能是另一道题", card.flags[0])
        self.assertEqual(card.state, Question.State.YELLOW)

    def test_the_flag_can_never_invent_a_claim_the_card_does_not_earn(self):
        # 题干就是纸上那一行原话：不动它。
        card = self.card(stem="已知两条对角线，利用尺规作一个菱形。")

        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 0)

        card.refresh_from_db()
        self.assertEqual(card.flags, [])
        self.assertEqual(card.state, Question.State.GREEN)

    def test_a_rewritten_but_the_same_question_is_left_alone(self):
        # 判据自己容得下改写：只要有 4 个字原样出现过就不报。
        card = self.card(stem="利用尺规作一个菱形，怎么画？请写出作法。")

        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 0)

        card.refresh_from_db()
        self.assertEqual(card.flags, [])

    def test_a_card_someone_already_looked_at_is_not_asked_again(self):
        already = pipeline.ANCHOR_MISMATCH_FLAG_PREFIX + "x" + "…"
        for values in ({"edited": True}, {"approved": True},
                       {"processing_mode": "manual"}, {"body_mode": "source_image"},
                       {"flags": [already]}):
            card = self.card(**values)

            self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 0, values)

            card.refresh_from_db()
            self.assertEqual(card.flags, values.get("flags", []), values)

    def test_a_card_without_an_anchor_block_is_left_alone(self):
        card = self.card(source_anchor_seq=None)
        orphan = self.card(source_anchor_seq=999)

        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 0)

        card.refresh_from_db()
        orphan.refresh_from_db()
        self.assertEqual((card.flags, orphan.flags), ([], []))

    def test_it_never_rewrites_the_card_body(self):
        card = self.card()
        before = Question.objects.values("stem", "options", "regions").get(pk=card.pk)

        pipeline.reconcile_anchor_reading_flags(self.paper)

        after = Question.objects.values("stem", "options", "regions").get(pk=card.pk)
        self.assertEqual(before, after)

    def test_repeated_runs_add_the_flag_once(self):
        self.card()
        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 0)

    def test_read_card_and_this_pass_say_the_same_thing(self):
        """两处必须给出同一句话：识读那一刻挂的，和事后补的。"""
        from . import textnorm
        card = self.card()
        fresh = pipeline.read_card({
            "id": card.pk, "number": card.number, "regions": card.regions,
            "source_anchor_seq": card.source_anchor_seq,
            "witness_blocks": [{"seq": 671, "type": "text", "text": (
                "20.已知两条对角线，利用尺规作一个菱形。※21.如图，画一个矩形 EFGH。")}],
            "witness": "", "candidates": [], "options": {}, "body_mode": "text",
            "processing_mode": "manual", "text_source": "", "stem": card.stem,
            "edited": False, "source_kind": "", "start_source": "", "is_book": True,
        }, store=type("Store", (), {"load": lambda self, page: object()})())
        if fresh.get("flags"):
            first = [f for f in fresh["flags"]
                     if f.startswith(pipeline.ANCHOR_MISMATCH_FLAG_PREFIX)]
            if first:
                self.assertEqual(textnorm.anchor_reading_mismatch(
                    "20.已知两条对角线，利用尺规作一个菱形。※21.如图，画一个矩形 EFGH。",
                    card.stem), "利用尺规作一个菱形")
                self.assertIn("利用尺规作一个菱形…", first[0])