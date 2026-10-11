# -*- coding: utf-8 -*-
r"""`manage.py reconcile_flags` 这条独立入口的守门测试。

「重新切题」跑完末尾会顺带补三条兜底提示（几何挂图落在范围外、卡号那一行认不出、
题型标成填空题却没有空格）。这三条判据只跟卡上已有的范围、题型、旗标有关，跟重切
没有半点关系，却只能等一次全量重切才补得上——一次几分钟到十几分钟，还要重新读题。

真库实测（2026-11-04，959 张）：三条一起跑 0.7 秒、零模型调用，84 张挂上新提示、
绿卡 720 → 667，题干一个字没改、已通过的一张没掉。

这里守三件事：

1. ``--list`` 只是预览，**一个字都不许写进库**；
2. 清完再跑一次必须是 0，否则「命中 22 张」是在报一件没发生的事；
3. ``--only`` 真的只跑指定那几条，未知名字要报错。
"""
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase

from . import figure_policy, pipeline, qtypes
from .models import Block, Paper, Question, QuestionGroup


class ReconcileFlagsCommandTests(TestCase):
    """四条卡各踩一条判据（第四张是正常题），加命令外壳。"""

    def setUp(self):
        self.paper = Paper.objects.create(
            filename="月考.pdf", kind="pdf", material_type=Paper.MaterialType.EXAM,
            sha256="a" * 64, source_path="missing-source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400},
                   {"page_idx": 1, "width": 1000, "height": 1400}],
            status=Paper.Status.READY)
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="第一章", sequence=1, page_start=1, page_end=2,
            metadata={"pages": [0, 1]})
        # 纸上印的是这一行；认出它的卡会在「卡号那一行认不出」那条上中招。
        Block.objects.create(paper=self.paper, seq=10, type="text", page_idx=1,
                             bbox=[80, 500, 920, 545],
                             text="1. 已知矩形面积为 9，求它的对角线长。")
        self.counter = 0

        self.geometric = self.card(
            stem="如图，△ABC 的三个顶点分别为 A（1，2），求 $y=\\dfrac{k}{x}$ 的图象。",
            regions=[{"page_idx": 1, "bbox": [83, 505, 899, 535]}],
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [747, 628, 853, 756],
                      "source": "other"}])
        self.anchor = self.card(
            stem="某果园有苹果树 $x$ 棵，梨树 $y$ 棵，两种树共 120 棵。",
            regions=[{"page_idx": 1, "bbox": [40, 330, 490, 420]}],
            source_anchor_seq=10)
        # 题型标成填空题，成品卷却只给解答题留书写空间——学生在卷上没地方写过程。
        self.no_slot = self.card(
            stem="一个矩形的两条对角线的夹角为 $60^\\circ$，对角线长为 $15$，求矩形较短边的长。",
            regions=[{"page_idx": 1, "bbox": [40, 90, 490, 320]}], question_type="fill_blank")
        self.real_blank = self.card(
            stem="已知 $x+1=2$，则 $x=$ ______。",
            regions=[{"page_idx": 1, "bbox": [40, 430, 490, 480]}], question_type="fill_blank")

    def card(self, **values):
        self.counter += 1
        base = dict(
            paper=self.paper, group=self.group, number=self.counter,
            processing_mode="auto", body_mode="text",
            state=Question.State.GREEN, flags=[], figures=[], figure_review={})
        base.update(values)
        return Question.objects.create(**base)

    def run_command(self, *args):
        out = StringIO()
        call_command("reconcile_flags", *args, stdout=out)
        return out.getvalue()

    def stored(self):
        """从库里重新读。手里那个 Question 是建卡时的旧对象，不会自己变。"""
        return {q.pk: (q.state, list(q.flags or []))
                for q in Question.objects.order_by("pk")}

    # -- 三条判据的接线 ---------------------------------------------------

    def test_each_of_the_three_reaches_its_own_card(self):
        self.assertEqual(pipeline.reconcile_geometric_figure_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_anchor_reading_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_fill_blank_slot_flags(self.paper), 1)

        rows = self.stored()
        self.assertIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE, rows[self.geometric.pk][1])
        self.assertTrue(any(str(f).startswith(pipeline.ANCHOR_MISMATCH_FLAG_PREFIX)
                            for f in rows[self.anchor.pk][1]),
                        rows[self.anchor.pk][1])
        self.assertIn(qtypes.FLAG_FILL_NO_SLOT, rows[self.no_slot.pk][1])

    def test_a_card_that_breaks_none_of_them_is_left_green(self):
        self.run_command()

        self.assertEqual(self.stored()[self.real_blank.pk],
                         (Question.State.GREEN, []))

    # -- 1) --list 不许写库 ----------------------------------------------

    def test_list_only_writes_nothing_at_all(self):
        before = self.stored()

        self.run_command("--list")

        self.assertEqual(self.stored(), before)

    def test_list_only_counts_the_same_cards_a_real_run_would(self):
        preview = self.run_command("--list")

        self.assertIn("几何挂图落在本题范围外：命中 1 张", preview)
        self.assertIn("卡号那一行认不出：命中 1 张", preview)
        self.assertIn("题型标成填空题却没有空格：命中 1 张", preview)

    def test_list_only_says_the_library_was_left_alone(self):
        self.assertIn("库没有改动", self.run_command("--list"))

    # -- 2) 跑第二遍必须是 0 ----------------------------------------------

    def test_a_second_run_reports_nothing_left_to_do(self):
        self.run_command()

        second = self.run_command("--list")

        self.assertIn("几何挂图落在本题范围外：命中 0 张", second)
        self.assertIn("卡号那一行认不出：命中 0 张", second)
        self.assertIn("题型标成填空题却没有空格：命中 0 张", second)

    def test_a_flag_is_never_written_twice(self):
        self.run_command()
        self.run_command()

        card = Question.objects.get(pk=self.geometric.pk)
        self.assertEqual(card.flags.count(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE), 1)

    # -- 3) --only / --paper / 报错 ---------------------------------------

    def test_only_skips_the_check_it_was_not_asked_for(self):
        self.run_command("--only", "fill_blank")

        rows = self.stored()
        self.assertIn(qtypes.FLAG_FILL_NO_SLOT, rows[self.no_slot.pk][1])
        self.assertNotIn(figure_policy.FLAG_FIGURE_OUTSIDE_RANGE,
                         rows[self.geometric.pk][1])
        self.assertEqual(rows[self.geometric.pk][0], Question.State.GREEN)

    def test_a_name_that_does_not_exist_is_refused(self):
        with self.assertRaisesMessage(CommandError, "不知道这几条检查：nope"):
            self.run_command("--only", "nope")

    def test_a_file_name_that_does_not_exist_is_refused(self):
        with self.assertRaisesMessage(CommandError, "找不到这些资料"):
            self.run_command("--paper", "没有这份卷子.pdf")

    def test_a_real_run_reports_how_many_cards_were_recoloured(self):
        self.assertIn("绿卡 4 → 1", self.run_command())