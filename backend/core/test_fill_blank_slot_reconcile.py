"""「题型标成填空题、题干里却没有可填位置」这条兜底旗标的守门测试。

真库实测（2026-10-11，959 张）：符合判据的 37 张，30 张一条旗都没有、全是绿卡。
成品卷只给解答题留书写空间，这 30 张印出来学生没地方写解题过程。
"""

from django.test import TestCase

from . import pipeline, qtypes
from .models import Paper, Question


class FillBlankSlotReconcileTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="数学八年级下册.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="f" * 64,
            source_path="missing-source.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1400}],
            status=Paper.Status.READY,
        )
        # 真实题干：八下第 2 题 #798
        self.suspect = Question.objects.create(
            paper=self.paper,
            number=2,
            question_type="fill_blank",
            state=Question.State.GREEN,
            stem="一个矩形的两条对角线的一个夹角为 $60^\\circ$，对角线长为 $15$，求矩形较短边的长。",
            regions=[{"page_idx": 0, "bbox": [40, 90, 490, 320]}],
            flags=[],
        )
        # 真填空题：题干里有答题括号，成品卷不需要留过程
        self.real_blank = Question.objects.create(
            paper=self.paper,
            number=3,
            question_type="fill_blank",
            state=Question.State.GREEN,
            stem="已知 $x+1=2$，则 $x=$ ______。",
            regions=[{"page_idx": 0, "bbox": [40, 330, 490, 420]}],
            flags=[],
        )

    def test_a_green_card_asking_for_a_solution_is_asked_about(self):
        self.assertEqual(pipeline.reconcile_fill_blank_slot_flags(self.paper), 1)
        values = Question.objects.values("state", "flags").get(pk=self.suspect.pk)
        self.assertEqual(values["state"], Question.State.YELLOW)
        self.assertEqual(values["flags"], [qtypes.FLAG_FILL_NO_SLOT])

    def test_a_real_fill_in_blank_is_left_alone(self):
        pipeline.reconcile_fill_blank_slot_flags(self.paper)
        values = Question.objects.values("state", "flags").get(pk=self.real_blank.pk)
        self.assertEqual(values["state"], Question.State.GREEN)
        self.assertEqual(values["flags"], [])

    def test_it_never_changes_the_type_or_the_body(self):
        pipeline.reconcile_fill_blank_slot_flags(self.paper)
        card = Question.objects.get(pk=self.suspect.pk)
        self.assertEqual(card.question_type, "fill_blank")
        self.assertIn("求矩形较短边的长", card.stem)

    def test_a_person_who_already_looked_at_it_is_not_asked_again(self):
        for maker in (
            lambda: setattr(self.suspect, "edited", True),
            lambda: setattr(self.suspect, "approved", True),
            lambda: setattr(self.suspect, "flags", ["别的题识读时有歧义，请核对"]),
        ):
            maker()
            self.suspect.save()
            self.assertEqual(pipeline.reconcile_fill_blank_slot_flags(self.paper), 0)
            self.assertEqual(Question.objects.get(pk=self.suspect.pk).flags, list(self.suspect.flags))

    def test_repeated_runs_add_the_flag_once(self):
        self.assertEqual(pipeline.reconcile_fill_blank_slot_flags(self.paper), 1)
        self.assertEqual(pipeline.reconcile_fill_blank_slot_flags(self.paper), 0)
        self.assertEqual(Question.objects.get(pk=self.suspect.pk).flags, [qtypes.FLAG_FILL_NO_SLOT])

    def test_read_card_and_this_pass_say_the_same_thing(self):
        # 识读那一刻挂的和事后补的必须是同一句，否则界面上会出现两种说法。
        self.assertEqual(qtypes.FLAG_FILL_NO_SLOT.split("；")[0], "题型标成填空题，但题干里没有可填的空格")
        self.assertLessEqual(len(qtypes.FLAG_FILL_NO_SLOT), 80)

    def test_it_runs_with_the_other_reconciles_at_the_end_of_segmentation(self):
        import inspect

        source = inspect.getsource(pipeline.segment_paper)
        self.assertIn("reconcile_fill_blank_slot_flags(paper)", source)
