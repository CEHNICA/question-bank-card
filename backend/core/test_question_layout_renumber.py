"""Renumber a selected original-paper question without replacing its identity."""
from copy import deepcopy
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import question_layout as layout, test_question_layout as fixtures
from .models import (LibraryJob, PublishedQuestion, Question, QuestionDeletionBatch,
                     QuestionLayoutOperation, QuestionGroup, RegionRead)


class QuestionLayoutRenumberTests(TestCase):
    # Reuse the offline synthetic original paper, not the fixture's test cases.
    paper_fixture = fixtures.QuestionLayoutTests.paper_fixture
    setUp = fixtures.QuestionLayoutTests.setUp
    pieces = staticmethod(fixtures.QuestionLayoutTests.pieces)
    question = fixtures.QuestionLayoutTests.question
    post = fixtures.QuestionLayoutTests.post
    state = fixtures.QuestionLayoutTests.state
    publication = fixtures.QuestionLayoutTests.publication
    approve = fixtures.QuestionLayoutTests.approve
    assert_offline = fixtures.QuestionLayoutTests.assert_offline
    source = fixtures.QuestionLayoutTests.source
    payload = fixtures.QuestionLayoutTests.payload
    mutate = fixtures.QuestionLayoutTests.mutate
    undo = fixtures.QuestionLayoutTests.undo
    rejected = fixtures.QuestionLayoutTests.rejected
    rejected_undo = fixtures.QuestionLayoutTests.rejected_undo

    def renumber(self, number=4, question=None):
        return self.payload("renumber", sources=[question or self.first], targets=[{"number": number}])

    def test_renumber_preserves_identity_all_ranges_manual_content_and_original_evidence(self):
        self.first.regions = [*self.pieces(page=1, top=180), *self.pieces(top=100)]
        self.first.figures = [{"slot": "stem", "page_idx": 0, "bbox": [60, 100, 180, 180], "source": "manual"}]
        self.first.figure_candidates = [{"page_idx": 0, "bbox": [60, 100, 180, 180], "label": "原配图"}]
        self.first.answer, self.first.analysis, self.first.origin = "人工答案", "人工解析", "合成出处"
        self.first.read_a, self.first.read_b, self.first.read_c = {"stem": "甲旧稿"}, {"stem": "乙旧稿"}, {"stem": "裁决旧稿"}
        self.first.source_kind, self.first.source_anchor_seq = "mineru", 14
        self.first.save()
        self.approve(self.first)
        before = Question.objects.values().get(pk=self.first.pk)
        before_ids = set(Question.objects.filter(paper=self.paper).values_list("id", flat=True))
        self.paper.refresh_from_db()
        revision = self.paper.layout_revision
        operation, repeated = self.mutate(self.renumber())
        self.assertFalse(repeated)
        after = Question.objects.values().get(pk=self.first.pk)
        changed = {"number", "content_revision", "ocr_pending", "reread_requested", "ocr_suggestion",
                   "state", "error", "flags", "approved", "approved_at", "approved_content_hash",
                   "approval_source", "approval_agent", "updated_at"}
        for name, value in before.items():
            if name not in changed:
                self.assertEqual(after[name], value, name)
        self.assertEqual(after["number"], 4)
        self.assertEqual(after["content_revision"], before["content_revision"] + 1)
        self.assertFalse(after["approved"] or after["ocr_pending"] or after["reread_requested"])
        self.assertEqual(after["state"], "yellow")
        self.assertEqual(set(Question.objects.filter(paper=self.paper).values_list("id", flat=True)), before_ids)
        self.assertEqual(operation.source_ids, [self.first.pk])
        self.assertEqual(operation.target_ids, [self.first.pk])
        self.assertIsNone(operation.deletion_batch_id)
        self.assertEqual(QuestionDeletionBatch.objects.count(), 0)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.layout_revision, revision + 1)

    def test_same_group_active_and_ordinary_trash_numbers_are_reserved(self):
        self.rejected(self.renumber(2))
        batch = QuestionDeletionBatch.objects.create(paper=self.paper, question_ids=[], origin="user")
        self.question(3, deleted_at=timezone.now(), deletion_batch=batch)
        self.rejected(self.renumber(3))
        self.question(4, deleted_at=timezone.now())
        self.rejected(self.renumber(4))

    def test_other_group_and_layout_history_do_not_reserve_target_number(self):
        other_group = QuestionGroup.objects.create(paper=self.paper, title="另一组", sequence=1,
            page_start=1, page_end=2, metadata={"pages": [0, 1]})
        self.question(4, group=other_group)
        batch = QuestionDeletionBatch.objects.create(paper=self.paper, question_ids=[], origin="layout")
        historical = self.question(4, deleted_at=timezone.now(), deletion_batch=batch)
        self.mutate(self.renumber(4))
        self.first.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(self.first.number, 4)
        self.assertIsNotNone(historical.deleted_at)

    def test_renumber_requires_one_source_one_target_and_integer_number_1_to_999(self):
        for number in (None, True, False, 0, -1, 1000, "4", 4.0):
            with self.subTest(number=number):
                self.rejected(self.renumber(number), 400)
        for sources in ([], [self.first, self.second]):
            self.rejected(self.payload("renumber", sources=sources, targets=[{"number": 4}]), 400)
        for targets in ([], [{"number": 4}, {"number": 5}]):
            self.rejected(self.payload("renumber", targets=targets), 400)
        self.mutate(self.renumber(999))
        self.first.refresh_from_db()
        self.assertEqual(self.first.number, 999)

    def test_receipt_retry_after_commit_returns_same_operation_and_changed_payload_is_rejected(self):
        payload = self.renumber()
        operation, _ = self.mutate(payload)
        before = self.state()
        same, repeated = self.mutate(payload)
        self.assertTrue(repeated)
        self.assertEqual(same.pk, operation.pk)
        self.assertEqual(self.state(), before)
        different = deepcopy(payload)
        different["targets"][0]["number"] = 5
        self.rejected(different)

    def test_retry_of_an_undone_request_cannot_renumber_again(self):
        payload = self.renumber()
        operation, _ = self.mutate(payload)
        self.undo(operation)
        before = self.state()
        same, repeated = self.mutate(payload)
        self.assertTrue(repeated)
        self.assertEqual(same.pk, operation.pk)
        self.assertEqual(self.state(), before)
        self.assertEqual(Question.objects.get(pk=self.first.pk).number, 1)

    def test_noop_preserves_approval_pending_reads_and_last_effective_undo(self):
        previous, _ = self.mutate(self.payload(sources=[self.second]))
        self.approve(self.first)
        Question.objects.filter(pk=self.first.pk).update(ocr_pending=True, reread_requested=True,
            ocr_suggestion={"revision": self.first.content_revision, "stem": "保留的识读建议"})
        pending = RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="queued")
        self.paper.refresh_from_db()
        revision = self.paper.layout_revision
        before = Question.objects.values().get(pk=self.first.pk)
        payload = self.renumber(1)
        operation, _ = self.mutate(payload)
        self.assertEqual(Question.objects.values().get(pk=self.first.pk), before)
        self.paper.refresh_from_db(); pending.refresh_from_db()
        self.assertEqual(self.paper.layout_revision, revision)
        self.assertEqual(pending.status, "queued")
        self.assertTrue(operation.after_snapshot["no_change"])
        self.assertIn("题号未变化", operation.blocked_reason)
        self.assertEqual(layout._latest(self.paper).pk, previous.pk)
        self.assertEqual(layout.undo_reason(previous), "")
        self.rejected_undo(operation)
        same, repeated = self.mutate(payload)
        self.assertTrue(repeated)
        self.assertEqual(same.pk, operation.pk)

    def test_undo_restores_number_without_deleting_question_or_resurrecting_approval(self):
        self.approve(self.first)
        publication = self.publication(self.first)
        job = LibraryJob.objects.create(publication=publication, kind="answer", status="running")
        old_publication = PublishedQuestion.objects.values().get(pk=publication.pk)
        old_job = LibraryJob.objects.values().get(pk=job.pk)
        before = Question.objects.values().get(pk=self.first.pk)
        operation, _ = self.mutate(self.renumber())
        self.assertEqual(PublishedQuestion.objects.values().get(pk=publication.pk), old_publication)
        self.assertEqual(LibraryJob.objects.values().get(pk=job.pk), old_job)
        revision_after = Question.objects.get(pk=self.first.pk).content_revision
        self.undo(operation)
        restored = Question.objects.get(pk=self.first.pk)
        self.assertEqual(restored.number, before["number"])
        self.assertEqual(restored.regions, before["regions"])
        self.assertEqual(restored.stem, before["stem"])
        self.assertEqual(restored.source_key, before["source_key"])
        self.assertEqual(restored.color_index, before["color_index"])
        self.assertGreater(restored.content_revision, revision_after)
        self.assertFalse(restored.approved)
        self.assertIsNone(restored.deleted_at)
        self.assertEqual(QuestionDeletionBatch.objects.count(), 0)
        self.assertEqual(PublishedQuestion.objects.values().get(pk=publication.pk), old_publication)
        self.assertEqual(LibraryJob.objects.values().get(pk=job.pk), old_job)
        before_retry = self.state()
        _, repeated = self.undo(operation)
        self.assertTrue(repeated)
        self.assertEqual(self.state(), before_retry)

    def test_old_source_revision_fingerprint_and_paper_revision_all_reject_atomically(self):
        stale = self.renumber()
        self.mutate(self.payload(sources=[self.second]))
        self.rejected(stale)
        stale = self.renumber()
        stale["sources"][0]["revision"] += 1
        self.rejected(stale)
        stale = self.renumber()
        self.approve(self.first)
        self.rejected(stale)

    def test_undo_refuses_later_manual_edit_approval_recognition_and_publication_change(self):
        for index, change in enumerate(("text", "approval", "ocr", "region_read", "publication")):
            with self.subTest(change=change):
                question = self.question(10 + index)
                operation, _ = self.mutate(self.renumber(20 + index, question))
                question.refresh_from_db()
                if change == "text":
                    Question.objects.filter(pk=question.pk).update(stem="保留后来人工修改")
                elif change == "approval":
                    self.approve(question)
                elif change == "ocr":
                    Question.objects.filter(pk=question.pk).update(ocr_suggestion={"revision": question.content_revision, "stem": "后来建议"})
                elif change == "region_read":
                    RegionRead.objects.create(question=question, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="done", text="后来结果")
                else:
                    self.publication(question)
                self.rejected_undo(operation)

    def test_number_change_cancels_old_region_read_and_invalidates_whole_question_suggestion(self):
        Question.objects.filter(pk=self.first.pk).update(ocr_pending=True, reread_requested=True,
            ocr_suggestion={"revision": self.first.content_revision, "stem": "旧建议"})
        pending = RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="running")
        completed = RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="done", text="原识读历史")
        self.mutate(self.renumber())
        self.first.refresh_from_db(); pending.refresh_from_db(); completed.refresh_from_db()
        self.assertFalse(self.first.ocr_pending or self.first.reread_requested or self.first.ocr_suggestion)
        self.assertEqual(pending.status, "failed")
        self.assertIn("题号", pending.error)
        self.assertEqual(completed.status, "done")
        self.assertEqual(completed.text, "原识读历史")

    def test_late_whole_question_read_cannot_overwrite_renumber_or_manual_content(self):
        self.first.processing_mode = "auto"
        self.first.save(update_fields=["processing_mode"])
        before_text, before_regions = self.first.stem, deepcopy(self.first.regions)
        operations = []
        def late_read(*unused):
            operations.append(self.mutate(self.renumber())[0])
            return {"stem": "迟到结果不得覆盖人工正文", "options": {}, "question_type": "free_response",
                    "state": "green", "flags": [], "error": "", "text_source": "single", "figures": [],
                    "read_a": {"stem": "迟到结果"}, "read_b": {}, "read_c": {}}
        fixtures.manual_review.ManualIntakeReviewTests.run_read(self, self.first, late_read)
        self.first.refresh_from_db()
        self.assertEqual((self.first.number, self.first.stem, self.first.regions), (4, before_text, before_regions))
        self.assertEqual(self.first.state, "yellow")
        self.assertFalse(self.first.ocr_pending or self.first.reread_requested)
        self.assertEqual(len(operations), 1)
        self.assertEqual(layout.undo_reason(operations[0]), "")
        self.assert_offline()

    def test_failed_receipt_rolls_back_number_approval_and_cancelled_read(self):
        self.approve(self.first)
        RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="running")
        payload = self.renumber()
        before = self.state()
        with mock.patch.object(QuestionLayoutOperation, "save", side_effect=RuntimeError("synthetic receipt failure")):
            with self.assertRaises(RuntimeError):
                layout.mutate(self.paper.pk, payload)
        self.assertEqual(self.state(), before)

    def test_http_renumber_history_lookup_and_undo_survive_reopen(self):
        url = f"/api/papers/{self.paper.pk}/question-layout"
        shown = self.client.get(f"/api/papers/{self.paper.pk}").json()
        source = next(row for row in shown["questions"] if row["id"] == self.first.pk)
        payload = self.renumber()
        payload["sources"] = [{"id": source["id"], "revision": source["content_revision"], "fingerprint": source["layout_fingerprint"]}]
        saved = self.post(url, payload)
        self.assertEqual(saved.status_code, 200, saved.content)
        data = saved.json()
        row = next(row for row in data["questions"] if row["id"] == self.first.pk)
        self.assertEqual(row["number"], 4)
        self.assertEqual(data["operation"]["kind"], "renumber")
        self.assertTrue(data["operation"]["can_undo"])
        reopened = self.client.get(url).json()
        self.assertEqual(reopened["latest_operation"]["id"], data["operation"]["id"])
        looked_up = self.client.get(url, {"client_request_id": payload["client_request_id"]}).json()
        self.assertEqual(looked_up["operation"]["id"], data["operation"]["id"])
        repeated = self.post(url, payload)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertTrue(repeated.json()["already_applied"])
        undone = self.post(f"{url}/{data['operation']['id']}/undo", {"layout_revision": reopened["layout_revision"]})
        self.assertEqual(undone.status_code, 200, undone.content)
        restored = next(row for row in undone.json()["questions"] if row["id"] == self.first.pk)
        self.assertEqual(restored["number"], 1)
        self.assertEqual(QuestionDeletionBatch.objects.count(), 0)
        self.assert_offline()
