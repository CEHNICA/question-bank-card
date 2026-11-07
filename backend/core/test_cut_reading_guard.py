"""1.26.47：AI 识读的保护状态，前后端必须说同一件事。

后端对原图题有三条硬保护（已审核、已入库、已有人工文字），以前审题页面不知道，
用户点了「AI 识读这题」才看到一条红条说不行 —— 点了才知道失败，等于没保护。
现在后端把判定和原因一起透出，页面照着做：事前就说清楚为什么灰、下一步去哪。
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TransactionTestCase, override_settings

from . import cut_guard, test_manual_intake_review as manual_review, views
from .models import PublishedQuestion, Question


class CutReadingGuardTests(TransactionTestCase):
    paper = manual_review.ManualIntakeReviewTests.paper

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        settings = override_settings(DATA_ROOT=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        self.original = self.paper()
        for name in ("_vision_ready", "_reading_ready"):
            patch = mock.patch.object(views, name, return_value=True)
            patch.start()
            self.addCleanup(patch.stop)

    def cut(self, number=1, **fields):
        defaults = {
            "paper": self.original,
            "number": number,
            "body_mode": "source_image",
            "processing_mode": "manual",
            "question_type": "free_response",
            "state": "yellow",
            "regions": [{"page_idx": 0, "bbox": [40, 40, 900, 480]}],
        }
        return Question.objects.create(**{**defaults, **fields})

    def publish(self, question, status=PublishedQuestion.Status.WITHDRAWN, version=1):
        return PublishedQuestion.objects.create(
            question=question,
            paper=self.original,
            source_filename="review-original.pdf",
            number=question.number,
            question_type="free_response",
            version=version,
            status=status,
            content={"stem": "入库时保存的题面"},
            content_hash="a" * 64,
        )

    def guard(self, question):
        return views.question_json(question)["cut_reading_guard"]

    def post(self, action, payload=None):
        return self.client.post(
            action, json.dumps(payload or {}), content_type="application/json", HTTP_X_QB_REQUEST="1"
        )

    def reread(self, question):
        return self.post(f"/api/questions/{question.pk}/reread", {"revision": question.content_revision})

    # ---- 页面拿到的判定 ---------------------------------------------------

    def test_plain_original_card_can_still_be_read(self):
        self.assertEqual(self.guard(self.cut()), {"blocked": False, "reason": ""})

    def test_approved_card_reports_approved(self):
        question = self.cut(approved=True)
        self.assertEqual(self.guard(question), {"blocked": True, "reason": "approved"})

    def test_withdrawn_publication_does_not_protect_the_card(self):
        # 1.26.51：撤回的入库记录不在题库里了，挡不住任何东西。
        # 老师撤回一道题，意思正是「我要回原卷重做」—— 不该被它挡住。
        question = self.cut()
        self.publish(question, PublishedQuestion.Status.WITHDRAWN)
        self.assertEqual(self.guard(question), {"blocked": False, "reason": ""})

    def test_superseded_publication_does_not_protect_the_card(self):
        # 被新版替代的记录也不在题库里了，同样不挡。
        question = self.cut()
        self.publish(question, PublishedQuestion.Status.SUPERSEDED)
        self.assertEqual(self.guard(question), {"blocked": False, "reason": ""})

    def test_live_publication_reports_published(self):
        question = self.cut()
        self.publish(question, PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(self.guard(question), {"blocked": True, "reason": "published"})

    def test_withdrawn_publication_does_not_stop_the_endpoint(self):
        # 判据和接口必须一致：页面放行的，接口也得放行。
        question = self.cut()
        self.publish(question, PublishedQuestion.Status.WITHDRAWN)
        self.assertNotEqual(self.reread(question).status_code, 409)

    def test_manual_words_report_manual_text(self):
        question = self.cut(edited=True, stem="人工改过的字")
        self.assertEqual(self.guard(question), {"blocked": True, "reason": "manual_text"})

    def test_edited_without_words_is_not_guarded(self):
        # 只动过配图、题干还是空的：后端照样放行识读，页面也不能说它被挡住。
        question = self.cut(edited=True)
        self.assertEqual(self.guard(question), {"blocked": False, "reason": ""})

    def test_text_cards_are_not_guarded(self):
        # 文字题走的是另一套保护（人工通过），不能套用原图题的理由。
        question = self.cut(body_mode="text", edited=True, stem="文字题")
        self.assertEqual(self.guard(question), {"blocked": False, "reason": ""})

    # ---- 判定必须和接口的拒绝口径一致 ------------------------------------

    def test_every_guarded_card_is_refused_by_the_endpoint(self):
        approved = self.cut(1, approved=True)
        published = self.cut(2)
        self.publish(published, PublishedQuestion.Status.PUBLISHED)
        manual = self.cut(3, edited=True, stem="人工改过的字")
        for question, reason in ((approved, "approved"), (published, "published"), (manual, "manual_text")):
            self.assertEqual(self.guard(question)["reason"], reason)
            response = self.reread(question)
            self.assertEqual(response.status_code, 409, question.pk)
            self.assertIn("已保留", json.loads(response.content)["error"])

    def test_guard_agrees_with_the_endpoint_for_an_unguarded_card(self):
        question = self.cut()
        self.assertFalse(self.guard(question)["blocked"])
        self.assertNotEqual(self.reread(question).status_code, 409)

    # ---- 批量判定不能一道题查一次 ----------------------------------------

    def test_bulk_reasons_use_one_query(self):
        rows = [self.cut(number) for number in range(1, 6)]
        self.publish(rows[2], PublishedQuestion.Status.PUBLISHED)
        with self.assertNumQueries(1):
            reasons = cut_guard.reasons_for(rows)
        self.assertEqual(reasons[rows[2].pk], "published")
        self.assertEqual(reasons[rows[0].pk], "")

    def test_bulk_reasons_match_single_questions(self):
        rows = [self.cut(1, approved=True), self.cut(2), self.cut(3, edited=True, stem="人工改过的字")]
        self.publish(rows[1], PublishedQuestion.Status.PUBLISHED)
        reasons = cut_guard.reasons_for(rows)
        for row in rows:
            self.assertEqual(reasons[row.pk], cut_guard.protection_reason(row))

    # ---- 真库上真实遇到的那一种 ------------------------------------------

    def test_the_real_library_case_is_readable_again(self):
        """真库实测：原图题的入库记录被撤回，题库里一条活的都没有。

        老师撤回了这道题，就是要回原卷重做 —— 不该被一条不在题库里的记录
        挡住，更不该点了才弹一条红条说不行。
        """
        question = self.cut()
        self.publish(question, PublishedQuestion.Status.WITHDRAWN)
        # 题库里没有活的入库记录了
        self.assertFalse(question.publications.filter(status=PublishedQuestion.Status.PUBLISHED).exists())
        self.assertEqual(self.guard(question), {"blocked": False, "reason": ""})
        self.assertNotEqual(self.reread(question).status_code, 409)

    def test_a_withdrawn_publication_next_to_a_live_one_still_protects(self):
        # 同一道题既有撤回的旧版本、又有在用的新版本：在用的那个照样挡。
        question = self.cut()
        self.publish(question, PublishedQuestion.Status.WITHDRAWN, version=1)
        self.publish(question, PublishedQuestion.Status.PUBLISHED, version=2)
        self.assertEqual(self.guard(question), {"blocked": True, "reason": "published"})
