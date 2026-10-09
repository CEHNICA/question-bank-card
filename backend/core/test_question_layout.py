"""Independent, offline regressions for saved original-page layout gestures.

Every original document and database row is synthesized in a temporary root.
The browser and model services are never used by this module.
"""
from copy import deepcopy
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from unittest import mock
import uuid

from PIL import Image

from django.db.models.query import QuerySet
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from . import library, library_jobs, photos, pipeline, question_layout as layout, readers, region_reads, views
from .models import (
    LibraryJob, Paper, PublishedQuestion, Question, QuestionDeletionBatch, QuestionLayoutOperation,
    QuestionGroup, RegionRead,
)
from . import test_manual_intake_review as manual_review


class QuestionLayoutTests(TestCase):
    paper_fixture = manual_review.ManualIntakeReviewTests.paper

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="tiyouju-layout-test-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        settings = override_settings(DATA_ROOT=self.root)
        settings.enable()
        self.addCleanup(settings.disable)
        guards = ExitStack()
        self.addCleanup(guards.close)
        self.network = guards.enter_context(mock.patch(
            "requests.sessions.Session.request", side_effect=AssertionError("Unexpected external request")))
        self.model = guards.enter_context(mock.patch.object(
            readers, "chat", side_effect=AssertionError("Unexpected model invocation")))
        self.cloud = guards.enter_context(mock.patch.object(
            pipeline, "request_extract_file_from_pool", side_effect=AssertionError("Unexpected cloud extraction")))
        self.paper = self.paper_fixture()
        self.group = QuestionGroup.objects.create(
            paper=self.paper, title="合成题组", kind="exam", sequence=0,
            page_start=1, page_end=2, metadata={"pages": [0, 1]})
        self.first = self.question(1)
        self.second = self.question(2, regions=self.pieces(top=300))

    @staticmethod
    def pieces(*, page=0, top=80, count=1):
        return [{"page_idx": page, "bbox": [40, top + i * 25, 900, top + i * 25 + 20]}
                for i in range(count)]

    def question(self, number=1, **fields):
        regions = fields.pop("regions", self.pieces())
        values = dict(
            paper=self.paper, group=self.group, number=number, regions=deepcopy(regions),
            regions_auto=deepcopy(regions), stem=f"合成且人工改过的第 {number} 题",
            options={"A": "甲", "B": "乙"}, question_type="single_choice",
            body_mode="text", processing_mode="manual", start_source="manual",
            source_kind=Question.SourceKind.MANUAL, edited=True, text_source="human",
            state=Question.State.GREEN, type_locked=True,
            figures=[], figure_review={"status": "confirmed_no_figure", "source": "human"})
        return Question.objects.create(**{**values, **fields})

    def post(self, url, payload):
        return self.client.post(url, json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def state(self):
        """Persistent rows only, excluding derived crop caches in the temp root."""
        return {
            "paper": deepcopy(Paper.objects.values().get(pk=self.paper.pk)),
            "questions": list(Question.all_objects.filter(paper=self.paper).values().order_by("id")),
            "publications": list(PublishedQuestion.objects.filter(paper=self.paper).values().order_by("id")),
            "jobs": list(LibraryJob.objects.filter(publication__paper=self.paper).values().order_by("id")),
            "reads": list(RegionRead.objects.filter(question__paper=self.paper).values().order_by("id")),
            "trash": list(QuestionDeletionBatch.objects.filter(paper=self.paper).values().order_by("id")),
            "operations": list(QuestionLayoutOperation.objects.filter(paper=self.paper).values().order_by("id")),
        }

    def publication(self, question, *, status=PublishedQuestion.Status.PUBLISHED, version=1):
        content = library.final_content(question)
        return PublishedQuestion.objects.create(
            paper=self.paper, question=question, source_filename=self.paper.filename,
            number=question.number, question_type=question.question_type, version=version,
            status=status, content=deepcopy(content), content_hash=library.content_hash(content),
            extras={"synthetic_history": {"retained": True}})

    def approve(self, question):
        library.approve(question, now=timezone.now())
        question.save()
        return question

    def assert_offline(self):
        self.network.assert_not_called()
        self.model.assert_not_called()
        self.cloud.assert_not_called()

    def source(self, question):
        question.refresh_from_db()
        return {"id": question.pk, "revision": question.content_revision, "fingerprint": layout.fingerprint(question)}

    def payload(self, kind="regions", *, sources=None, targets=None, request_id=None):
        self.paper.refresh_from_db()
        return {
            "kind": kind, "layout_revision": self.paper.layout_revision,
            "client_request_id": str(request_id or uuid.uuid4()),
            "sources": [self.source(question) for question in (sources if sources is not None else [self.first])],
            "targets": targets if targets is not None else [{"regions": self.pieces(top=100)}],
        }

    def split_payload(self, question=None, *, second_number=3):
        question = question or self.first
        return self.payload("split", sources=[question], targets=[
            {"number": question.number, "group_id": question.group_id, "question_type": "single_choice", "regions": self.pieces(top=90)},
            {"number": second_number, "group_id": question.group_id, "question_type": "free_response", "regions": self.pieces(page=1, top=150)},
        ])

    def mutate(self, payload):
        operation, repeated = layout.mutate(self.paper.pk, payload)
        self.assertIsInstance(operation, QuestionLayoutOperation)
        self.assert_offline()
        return operation, repeated

    def undo(self, operation):
        self.paper.refresh_from_db()
        return layout.undo(self.paper.pk, operation.pk, {"layout_revision": self.paper.layout_revision})

    def rejected(self, payload, status=409):
        before = self.state()
        with self.assertRaises(layout.LayoutError) as failure:
            layout.mutate(self.paper.pk, payload)
        self.assertEqual(failure.exception.status, status)
        self.assertEqual(self.state(), before, "A rejected layout request must not make partial writes")
        self.assert_offline()

    def rejected_undo(self, operation, status=409):
        before = self.state()
        with self.assertRaises(layout.LayoutError) as failure:
            self.undo(operation)
        self.assertEqual(failure.exception.status, status)
        self.assertEqual(self.state(), before, "An unsafe undo must leave every current row unchanged")

    def test_colour_is_persistent_presentation_and_does_not_change_approval_hash(self):
        question = self.approve(self.first)
        digest, revision = library.approval_hash(question), question.content_revision
        question.color_index = (question.color_index + 1) % 6
        question.save(update_fields=["color_index"])
        question.refresh_from_db()
        self.assertEqual(library.approval_hash(question), digest)
        self.assertEqual(question.approved_content_hash, digest)
        self.assertTrue(library.approval_is_current(question))
        self.assertEqual(question.content_revision, revision)
        colours = dict(Question.objects.filter(paper=self.paper).values_list("id", "color_index"))
        for _ in range(2):
            response = self.client.get(f"/api/papers/{self.paper.pk}/question-layout")
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(dict(Question.objects.filter(paper=self.paper).values_list("id", "color_index")), colours)
        self.assertTrue(all(type(colour) is int and 0 <= colour < 6 for colour in colours.values()))
        self.assert_offline()

    def test_range_save_preserves_manual_text_type_figures_and_never_queues_recognition(self):
        for index, mode in enumerate(("manual", "auto", "assistant")):
            with self.subTest(mode=mode):
                question = self.question(10 + index, processing_mode=mode, start_source="mineru",
                    read_a={"stem": "原识读记录"}, figures=[{"slot": "stem", "page_idx": 0,
                        "bbox": [60, 100, 180, 180], "source": "manual"}],
                    answer="人工答案", analysis="人工解析", origin="虚构出处")
                before = deepcopy(Question.objects.values().get(pk=question.pk))
                operation, repeated = self.mutate(self.payload(sources=[question]))
                self.assertFalse(repeated)
                question.refresh_from_db()
                for name in ("stem", "options", "question_type", "type_locked", "figures", "figure_review",
                             "edited", "answer", "analysis", "origin", "read_a", "body_mode", "processing_mode", "color_index"):
                    self.assertEqual(getattr(question, name), before[name], name)
                self.assertEqual(question.regions, self.pieces(top=100))
                self.assertGreater(question.content_revision, before["content_revision"])
                self.assertFalse(question.reread_requested or question.ocr_pending or question.approved)
                self.assertEqual(operation.source_ids, [question.pk])
                self.assertEqual(operation.target_ids, [question.pk])

    def test_noop_records_retry_receipt_without_changing_question_or_replacing_last_undo(self):
        self.approve(self.first)
        before = Question.objects.values().get(pk=self.first.pk)
        payload = self.payload(targets=[{"regions": deepcopy(self.first.regions)}])
        operation, repeated = self.mutate(payload)
        self.assertFalse(repeated)
        self.assertTrue(operation.after_snapshot.get("no_change"))
        self.assertEqual(Question.objects.values().get(pk=self.first.pk), before)
        self.assertTrue(Question.objects.get(pk=self.first.pk).approved)
        again, repeated = self.mutate(payload)
        self.assertTrue(repeated)
        self.assertEqual(again.pk, operation.pk)
        self.rejected_undo(operation)

    def test_range_invalidates_old_ocr_but_retains_old_publication_and_its_generation_job(self):
        self.approve(self.first)
        publication = self.publication(self.first)
        job = LibraryJob.objects.create(publication=publication, kind="answer", status="running")
        Question.objects.filter(pk=self.first.pk).update(ocr_pending=True, reread_requested=True,
            ocr_suggestion={"revision": self.first.content_revision, "stem": "旧OCR建议"})
        content = deepcopy(publication.content)
        self.mutate(self.payload())
        self.first.refresh_from_db()
        publication.refresh_from_db()
        job.refresh_from_db()
        self.assertFalse(self.first.approved or self.first.ocr_pending or self.first.reread_requested)
        self.assertFalse(self.first.ocr_suggestion)
        self.assertEqual(publication.status, PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(publication.content, content)
        self.assertEqual(job.status, LibraryJob.Status.RUNNING)

    def test_add_creates_new_manual_source_identity_with_ordered_cross_page_crops(self):
        ordered = [*self.pieces(page=1, top=180), *self.pieces(page=0, top=120)]
        operation, repeated = self.mutate(self.payload("add", sources=[], targets=[{
            "number": 3, "group_id": self.group.pk, "question_type": "free_response", "regions": ordered}]))
        self.assertFalse(repeated)
        self.assertEqual(operation.source_ids, [])
        self.assertEqual(len(operation.target_ids), 1)
        added = Question.objects.get(pk=operation.target_ids[0])
        self.assertEqual((added.number, added.group_id, added.regions), (3, self.group.pk, ordered))
        self.assertEqual((added.start_source, added.source_kind, added.body_mode, added.processing_mode),
                         ("manual", "manual", "source_image", "manual"))
        self.assertEqual(added.state, Question.State.YELLOW)
        self.assertFalse(added.approved or added.reread_requested or added.ocr_pending)
        self.assertNotIn(added.source_key, {self.first.source_key, self.second.source_key})
        self.assertIn(added.color_index, range(6))

    def test_split_keeps_original_manual_content_in_history_and_uses_new_identities(self):
        before = deepcopy(Question.objects.values().get(pk=self.first.pk))
        operation, _ = self.mutate(self.split_payload())
        self.assertFalse(Question.objects.filter(pk=self.first.pk).exists())
        retired = Question.all_objects.get(pk=self.first.pk)
        self.assertIsNotNone(retired.deleted_at)
        self.assertEqual((retired.stem, retired.options), (before["stem"], before["options"]))
        self.assertEqual(operation.source_ids, [self.first.pk])
        self.assertEqual(len(operation.target_ids), 2)
        new = list(Question.objects.filter(pk__in=operation.target_ids).order_by("number"))
        self.assertEqual([question.number for question in new], [1, 3])
        self.assertEqual(len({question.source_key for question in new}), 2)
        self.assertTrue(all(question.source_key != self.first.source_key for question in new))
        self.assertTrue(all(question.pk != self.first.pk for question in new))
        self.assertTrue(all(question.body_mode == "source_image" and question.processing_mode == "manual" for question in new))
        self.assertTrue(all(not question.stem and not question.options for question in new))
        saved = operation.before_snapshot["questions"]
        self.assertTrue(any(row["id"] == self.first.pk and row["stem"] == before["stem"] and row["options"] == before["options"] for row in saved))
        self.assertEqual(Question.objects.get(pk=self.second.pk).number, 2, "Split must not renumber the rest of the paper")

    def test_merge_retires_sources_and_preserves_explicit_piece_order(self):
        ordered = [*self.pieces(page=1, top=200), *self.pieces(page=0, top=80), *self.pieces(top=300)]
        payload = self.payload("merge", sources=[self.first, self.second], targets=[{
            "number": 1, "group_id": self.group.pk, "question_type": "free_response", "regions": ordered}])
        operation, _ = self.mutate(payload)
        merged = Question.objects.get(pk=operation.target_ids[0])
        self.assertEqual(merged.regions, ordered)
        self.assertEqual(merged.number, 1)
        self.assertEqual(set(operation.source_ids), {self.first.pk, self.second.pk})
        self.assertFalse(Question.objects.filter(pk__in=operation.source_ids).exists())
        self.assertNotIn(merged.source_key, {self.first.source_key, self.second.source_key})

    def test_all_validation_errors_are_atomic(self):
        cases = []
        for regions in ([], self.pieces(count=13), [{"page_idx": 9, "bbox": [40, 80, 900, 100]}],
                        [{"page_idx": True, "bbox": [40, 80, 900, 100]}],
                        [{"page_idx": 0, "bbox": [40, 80, float("nan"), 100]}]):
            cases.append((self.payload("add", sources=[], targets=[{"number": 3, "group_id": self.group.pk, "regions": regions}]), 400))
        for number in (0, True, "3"):
            cases.append((self.payload("add", sources=[], targets=[{"number": number, "regions": self.pieces()}]), 400))
        cases.extend([
            (self.payload("add", sources=[], targets=[{"number": 2, "group_id": self.group.pk, "regions": self.pieces()}]), 409),
            (self.payload("split", targets=[{"number": 3, "regions": self.pieces()}]), 400),
            (self.payload("merge", sources=[self.first], targets=[{"number": 3, "regions": self.pieces()}]), 400),
        ])
        for payload, status in cases:
            with self.subTest(payload=payload):
                self.rejected(payload, status)

    def test_sources_cannot_be_duplicated_deleted_or_from_another_paper(self):
        other_paper = self.paper_fixture()
        other_group = QuestionGroup.objects.create(paper=other_paper, title="另卷", sequence=0, page_start=1, page_end=2, metadata={"pages": [0, 1]})
        foreign = self.question(1, paper=other_paper, group=other_group)
        deleted = self.question(3, deleted_at=timezone.now())
        duplicate = self.payload("merge", sources=[self.first, self.first], targets=[{"number": 1, "group_id": self.group.pk, "regions": self.pieces()}])
        self.rejected(duplicate, 400)
        for question in (foreign, deleted):
            with self.subTest(question=question.pk):
                self.rejected(self.payload(sources=[question]))

    def test_ambiguous_group_and_cross_group_merge_are_rejected_without_renumbering(self):
        other_group = QuestionGroup.objects.create(paper=self.paper, title="重叠题组", sequence=1,
            page_start=1, page_end=2, metadata={"pages": [0, 1]})
        self.rejected(self.payload("add", sources=[], targets=[{"number": 3, "regions": self.pieces()}]), 400)
        other = self.question(1, group=other_group)
        self.rejected(self.payload("merge", sources=[self.first, other], targets=[{
            "number": 3, "group_id": self.group.pk, "regions": self.pieces()}]))

    def test_twelve_ordered_pieces_are_kept_and_thirteen_are_never_truncated(self):
        ordered = [*self.pieces(page=1, top=60, count=6), *self.pieces(page=0, top=60, count=6)]
        operation, _ = self.mutate(self.payload("add", sources=[], targets=[{"number": 3, "group_id": self.group.pk, "regions": ordered}]))
        self.assertEqual(Question.objects.get(pk=operation.target_ids[0]).regions, ordered)
        self.rejected(self.payload("add", sources=[], targets=[{"number": 4, "group_id": self.group.pk, "regions": [*ordered, *self.pieces(top=500)]}]), 400)

    def test_client_request_requires_uuid_revision_and_complete_source_tokens(self):
        for field in ("client_request_id", "layout_revision"):
            payload = self.payload()
            payload.pop(field)
            self.rejected(payload, 400)
        for field in ("id", "revision", "fingerprint"):
            payload = self.payload()
            payload["sources"][0].pop(field)
            self.rejected(payload, 400)
        payload = self.payload()
        payload["client_request_id"] = "not-a-uuid"
        self.rejected(payload, 400)

    def test_stale_paper_revision_source_revision_and_fingerprint_reject_before_writes(self):
        payload = self.payload()
        payload["layout_revision"] += 1
        self.rejected(payload)
        for key, value in (("revision", self.first.content_revision + 1), ("fingerprint", "f" * 64)):
            payload = self.payload()
            payload["sources"][0][key] = value
            self.rejected(payload)

    def test_approval_without_content_revision_increment_invalidates_submitted_source(self):
        payload = self.payload()
        revision = self.first.content_revision
        self.approve(self.first)
        self.first.refresh_from_db()
        self.assertEqual(self.first.content_revision, revision)
        self.rejected(payload)

    def test_ocr_suggestion_and_region_read_are_part_of_complete_source_fingerprint(self):
        for update in ("ocr", "region_read"):
            with self.subTest(update=update):
                question = self.question(10 if update == "ocr" else 11)
                payload = self.payload(sources=[question])
                if update == "ocr":
                    Question.objects.filter(pk=question.pk).update(ocr_suggestion={"revision": question.content_revision, "stem": "新的识读建议"})
                else:
                    RegionRead.objects.create(question=question, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="done", text="新的框选识读", recommendation={"revision": question.content_revision})
                self.rejected(payload)

    def test_exact_request_retry_returns_same_receipt_even_after_revision_changes(self):
        payload = self.split_payload()
        operation, repeated = self.mutate(payload)
        self.assertFalse(repeated)
        after = self.state()
        again, repeated = self.mutate(payload)
        self.assertTrue(repeated)
        self.assertEqual(again.pk, operation.pk)
        self.assertEqual(self.state(), after)
        changed = deepcopy(payload)
        changed["targets"][1]["number"] = 4
        self.rejected(changed)

    def test_retry_of_undone_request_never_reexecutes_split(self):
        payload = self.split_payload()
        operation, _ = self.mutate(payload)
        self.undo(operation)
        after = self.state()
        again, repeated = self.mutate(payload)
        self.assertTrue(repeated)
        self.assertEqual(again.pk, operation.pk)
        self.assertEqual(self.state(), after)

    def test_receipt_failure_rolls_back_retire_create_withdraw_and_job_cancellation(self):
        publication = self.publication(self.first)
        LibraryJob.objects.create(publication=publication, kind="answer", status="running")
        RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="running")
        payload = self.split_payload()
        before = self.state()
        with mock.patch.object(QuestionLayoutOperation, "save", side_effect=RuntimeError("synthetic receipt failure")):
            with self.assertRaises(RuntimeError):
                layout.mutate(self.paper.pk, payload)
        self.assertEqual(self.state(), before)

    def test_failed_sqlite_compare_and_swap_does_not_partially_retire_any_source(self):
        payload = self.split_payload()
        before = self.state()
        original = QuerySet.update
        def fail_layout_claim(queryset, **fields):
            if queryset.model is Paper and "layout_revision" in fields:
                return 0
            return original(queryset, **fields)
        with mock.patch.object(QuerySet, "update", new=fail_layout_claim):
            with self.assertRaises(layout.LayoutError) as failure:
                layout.mutate(self.paper.pk, payload)
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.state(), before)

    def test_split_withdraws_publication_and_cancels_active_jobs_without_rewriting_history(self):
        self.approve(self.first)
        publication = self.publication(self.first)
        historical = self.publication(self.first, status="superseded", version=2)
        original_publication = deepcopy(PublishedQuestion.objects.values().get(pk=publication.pk))
        original_historical = deepcopy(PublishedQuestion.objects.values().get(pk=historical.pk))
        jobs = [LibraryJob.objects.create(publication=publication, kind="answer", status=status,
            result={"retained": status}) for status in ("queued", "running", "done", "failed")]
        reads = [RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100],
            target="stem", status=status, text="retained history", recommendation={"revision": self.first.content_revision})
            for status in ("queued", "running", "done", "failed")]
        self.mutate(self.split_payload())
        publication.refresh_from_db()
        historical.refresh_from_db()
        self.assertEqual(publication.status, "withdrawn")
        self.assertEqual(publication.content, original_publication["content"])
        self.assertEqual(publication.content_hash, original_publication["content_hash"])
        self.assertEqual(publication.extras, original_publication["extras"])
        self.assertEqual(PublishedQuestion.objects.values().get(pk=historical.pk), original_historical)
        for index, job in enumerate(jobs):
            job.refresh_from_db()
            self.assertEqual(job.status, "failed" if index < 2 else ("done", "failed")[index - 2])
            self.assertEqual(job.result, {"retained": ("queued", "running", "done", "failed")[index]})
        for index, read in enumerate(reads):
            read.refresh_from_db()
            self.assertEqual(read.status, "failed" if index < 2 else ("done", "failed")[index - 2])
            if index >= 2:
                self.assertEqual(read.text, "retained history")
        region_reads._finish(reads[1], RegionRead.Status.DONE, text="late result must not return")
        library_jobs._finish(jobs[1], LibraryJob.Status.DONE)
        reads[1].refresh_from_db()
        jobs[1].refresh_from_db()
        self.assertEqual((reads[1].status, jobs[1].status), ("failed", "failed"))

    def test_undo_restores_geometry_and_visibility_without_resurrecting_approval_publication_or_jobs(self):
        self.approve(self.first)
        publication = self.publication(self.first)
        job = LibraryJob.objects.create(publication=publication, kind="answer", status="running")
        read = RegionRead.objects.create(question=self.first, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="running")
        old_regions, old_key, old_revision = deepcopy(self.first.regions), self.first.source_key, self.first.content_revision
        operation, _ = self.mutate(self.split_payload())
        undone, repeated = self.undo(operation)
        self.assertFalse(repeated)
        self.assertEqual(undone.pk, operation.pk)
        restored = Question.objects.get(pk=self.first.pk)
        self.assertEqual((restored.regions, restored.source_key), (old_regions, old_key))
        self.assertGreater(restored.content_revision, old_revision)
        self.assertFalse(restored.approved)
        self.assertIsNone(restored.approved_at)
        self.assertEqual((restored.approved_content_hash, restored.approval_source, restored.approval_agent), ("", "", ""))
        self.assertFalse(restored.reread_requested or restored.ocr_pending)
        self.assertFalse(Question.objects.filter(pk__in=operation.target_ids).exists())
        publication.refresh_from_db(); job.refresh_from_db(); read.refresh_from_db()
        self.assertEqual((publication.status, job.status, read.status), ("withdrawn", "failed", "failed"))
        after = self.state()
        _, repeated = self.undo(operation)
        self.assertTrue(repeated)
        self.assertEqual(self.state(), after)

    def test_undo_refuses_later_edit_approval_publication_ocr_and_region_read(self):
        for index, change in enumerate(("text", "approval", "publication", "ocr", "region_read")):
            with self.subTest(change=change):
                question = self.question(20 + index)
                operation, _ = self.mutate(self.payload(sources=[question]))
                question.refresh_from_db()
                if change == "text":
                    Question.objects.filter(pk=question.pk).update(stem="后来人工修订，不能被撤销覆盖")
                elif change == "approval":
                    self.approve(question)
                elif change == "publication":
                    self.publication(question)
                elif change == "ocr":
                    Question.objects.filter(pk=question.pk).update(ocr_suggestion={"revision": question.content_revision, "stem": "后来完成的识读建议"})
                else:
                    RegionRead.objects.create(question=question, page_idx=0, bbox=[50, 80, 200, 100], target="stem", status="done", text="后来完成的框选识读")
                self.rejected_undo(operation)

    def test_undo_refuses_later_changes_to_retired_original_and_publication_job(self):
        publication = self.publication(self.first)
        job = LibraryJob.objects.create(publication=publication, kind="answer", status="running")
        operation, _ = self.mutate(self.split_payload())
        LibraryJob.objects.filter(pk=job.pk).update(result={"new_late_state": True})
        self.rejected_undo(operation)
        job.refresh_from_db()
        self.assertEqual(job.result, {"new_late_state": True})

    def test_undo_refuses_later_manual_edit_to_retired_original(self):
        operation, _ = self.mutate(self.split_payload())
        Question.all_objects.filter(pk=self.first.pk).update(stem="原题历史后来又被人工修订，不能被撤销覆盖")
        self.rejected_undo(operation)
        self.assertEqual(Question.all_objects.get(pk=self.first.pk).stem, "原题历史后来又被人工修订，不能被撤销覆盖")

    def test_add_undo_hides_only_its_new_identity_and_preserves_existing_cards(self):
        original_ids = set(Question.objects.filter(paper=self.paper).values_list("id", flat=True))
        operation, _ = self.mutate(self.payload("add", sources=[], targets=[{
            "number": 3, "group_id": self.group.pk, "question_type": "free_response", "regions": self.pieces(top=450)}]))
        created = Question.objects.get(pk=operation.target_ids[0])
        key, revision = created.source_key, created.content_revision
        self.undo(operation)
        self.assertEqual(set(Question.objects.filter(paper=self.paper).values_list("id", flat=True)), original_ids)
        retired = Question.all_objects.get(pk=created.pk)
        self.assertEqual(retired.source_key, key)
        self.assertGreater(retired.content_revision, revision)
        self.assertEqual(retired.deletion_batch.origin, QuestionDeletionBatch.Origin.LAYOUT)

    def test_ordinary_trash_reserves_its_number_and_cannot_be_silently_replaced_by_add(self):
        deletion = self.post(f"/api/papers/{self.paper.pk}/questions/delete", {"question_ids": [self.second.pk]})
        self.assertEqual(deletion.status_code, 200, deletion.content)
        self.rejected(self.payload("add", sources=[], targets=[{
            "number": self.second.number, "group_id": self.group.pk, "regions": self.pieces(top=500)}]))

    def test_newer_layout_operation_blocks_undo_of_an_earlier_saved_operation(self):
        previous, _ = self.mutate(self.payload())
        self.mutate(self.payload(sources=[self.second], targets=[{"regions": self.pieces(top=330)}]))
        self.rejected_undo(previous)

    def test_layout_origins_cannot_be_restored_independently_from_the_recycle_bin(self):
        operation, _ = self.mutate(self.split_payload())
        retired = Question.all_objects.get(pk=self.first.pk)
        self.assertIsNotNone(retired.deletion_batch_id)
        before = self.state()
        result = self.post(f"/api/papers/{self.paper.pk}/question-trash/{retired.deletion_batch_id}/restore", {})
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(self.state(), before)
        self.undo(operation)

    def test_archived_paper_cannot_mutate_or_undo_but_history_is_retained(self):
        operation, _ = self.mutate(self.payload())
        Paper.objects.filter(pk=self.paper.pk).update(archived=True)
        self.rejected(self.payload())
        self.rejected_undo(operation)
        self.assertTrue(QuestionLayoutOperation.objects.filter(pk=operation.pk).exists())

    def test_actual_archive_restore_retains_split_history_and_allows_guarded_undo(self):
        operation, _ = self.mutate(self.split_payload())
        saved_history = deepcopy(operation.before_snapshot)
        archived = self.post(f"/api/papers/{self.paper.pk}/archive", {})
        self.assertEqual(archived.status_code, 200, archived.content)
        self.paper.refresh_from_db()
        self.assertTrue(self.paper.archived)
        self.rejected_undo(operation)
        history = self.client.get(f"/api/papers/{self.paper.pk}/question-layout")
        self.assertEqual(history.status_code, 200, history.content)
        self.assertFalse(history.json()["latest_operation"]["can_undo"])
        self.assertEqual(QuestionLayoutOperation.objects.get(pk=operation.pk).before_snapshot, saved_history)
        restored = self.post(f"/api/papers/{self.paper.pk}/restore", {})
        self.assertEqual(restored.status_code, 200, restored.content)
        self.undo(operation)
        self.assertTrue(Question.objects.filter(pk=self.first.pk).exists())

    def test_actual_photo_page_order_moves_active_and_history_regions_and_invalidates_undo(self):
        folder = self.root / str(self.paper.pk)
        for page in range(2):
            path = photos.page_file(folder, page)
            path.parent.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (240, 360), (180 + 20 * page, 200, 210))
            image.save(path)
            image.close()
        self.paper.render_path = str(folder / "render.pdf")
        self.paper.photos = {"order": [0, 1], "files": [{"name": "fake-a.jpg"}, {"name": "fake-b.jpg"}]}
        photos.build_pdf(folder, self.paper.photos, Path(self.paper.render_path))
        self.paper.save()
        operation, _ = self.mutate(self.split_payload())
        self.paper.refresh_from_db()
        previous_revision, previous_epoch = self.paper.layout_revision, self.paper.layout_page_epoch
        response = self.post(f"/api/papers/{self.paper.pk}/page-order", {"order": [1, 0]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["changed"])
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.photos["order"], [1, 0])
        self.assertGreater(self.paper.layout_revision, previous_revision)
        self.assertGreater(self.paper.layout_page_epoch, previous_epoch)
        source = Question.all_objects.get(pk=self.first.pk)
        self.assertEqual(source.regions[0]["page_idx"], 1)
        children = list(Question.objects.filter(pk__in=operation.target_ids).order_by("number"))
        self.assertEqual([row.regions[0]["page_idx"] for row in children], [1, 0])
        operation.refresh_from_db()
        self.assertIn("页序", operation.blocked_reason)
        self.rejected_undo(operation)

    def test_late_whole_question_reader_cannot_overwrite_saved_range_or_manual_content(self):
        self.first.processing_mode = "auto"
        self.first.save(update_fields=["processing_mode"])
        before_text = self.first.stem
        operations = []
        def late_read(*unused):
            operations.append(self.mutate(self.payload())[0])
            return {"stem": "迟到结果不得覆盖人工正文", "options": {}, "question_type": "free_response",
                    "state": "green", "flags": [], "error": "", "text_source": "single", "figures": [],
                    "read_a": {"stem": "迟到结果"}, "read_b": {}, "read_c": {}}
        manual_review.ManualIntakeReviewTests.run_read(self, self.first, late_read)
        self.first.refresh_from_db()
        self.assertEqual(self.first.regions, self.pieces(top=100))
        self.assertEqual(self.first.stem, before_text)
        self.assertEqual(self.first.state, Question.State.YELLOW)
        self.assertFalse(self.first.ocr_pending or self.first.reread_requested)
        self.assertEqual(len(operations), 1)
        self.assertEqual(layout.undo_reason(operations[0]), "")
        self.assert_offline()

    def test_page_order_still_rejects_withdrawn_publication_history_after_a_split(self):
        publication = self.publication(self.first)
        self.paper.photos = {"order": [0, 1], "files": [{"name": "fake-a.jpg"}, {"name": "fake-b.jpg"}]}
        self.paper.save(update_fields=["photos"])
        operation, _ = self.mutate(self.split_payload())
        publication.refresh_from_db()
        self.assertEqual(publication.status, PublishedQuestion.Status.WITHDRAWN)
        before = self.state()
        response = self.post(f"/api/papers/{self.paper.pk}/page-order", {"order": [1, 0]})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("入库", response.json()["error"])
        self.assertEqual(self.state(), before)
        self.assertEqual(layout.undo_reason(operation), "")

    def test_page_epoch_change_invalidates_old_undo_even_when_geometry_stays_equal(self):
        operation, _ = self.mutate(self.payload())
        Paper.objects.filter(pk=self.paper.pk).update(layout_page_epoch=1)
        self.rejected_undo(operation)

    def test_history_number_reuse_gets_new_identity_without_rewriting_old_publication(self):
        publication = self.publication(self.first)
        old_content = deepcopy(publication.content)
        operation, _ = self.mutate(self.split_payload())
        replacement = Question.objects.get(pk=operation.target_ids[0])
        self.assertEqual(replacement.number, self.first.number)
        self.assertNotEqual(replacement.source_key, self.first.source_key)
        self.assertNotEqual(replacement.pk, self.first.pk)
        publication.refresh_from_db()
        self.assertEqual(publication.question_id, self.first.pk)
        self.assertEqual(publication.content, old_content)

    def test_http_mutation_and_undo_use_same_verified_service_and_return_active_set(self):
        payload = self.split_payload()
        url = f"/api/papers/{self.paper.pk}/question-layout"
        result = self.post(url, payload)
        self.assertEqual(result.status_code, 200, result.content)
        data = result.json()
        self.assertEqual({row["id"] for row in data["questions"]}, set(Question.objects.filter(paper=self.paper).values_list("id", flat=True)))
        self.assertFalse(data["already_applied"])
        repeated = self.post(url, payload)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertTrue(repeated.json()["already_applied"])
        operation = QuestionLayoutOperation.objects.get(client_request_id=payload["client_request_id"])
        self.paper.refresh_from_db()
        restored = self.post(f"{url}/{operation.pk}/undo", {"layout_revision": self.paper.layout_revision})
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertTrue(Question.objects.filter(pk=self.first.pk).exists())
        self.assert_offline()

    def test_legacy_range_always_saves_only_without_implicit_automatic_reading(self):
        for index, mode in enumerate(("manual", "auto", "assistant")):
            for explicit_flag in (False, True):
                with self.subTest(mode=mode, explicit_flag=explicit_flag):
                    question = self.question(30 + index * 2 + int(explicit_flag), processing_mode=mode,
                        start_source="mineru", answer="人工答案", analysis="人工解析",
                        figures=[{"slot": "stem", "page_idx": 0, "bbox": [60, 100, 180, 180], "source": "manual"}])
                    self.approve(question)
                    before = deepcopy(Question.objects.values().get(pk=question.pk))
                    before_count = QuestionLayoutOperation.objects.count()
                    payload = {"regions": self.pieces(top=200)}
                    if explicit_flag:
                        payload["save_only"] = True
                    result = self.post(f"/api/questions/{question.pk}/regions", payload)
                    self.assertEqual(result.status_code, 200, result.content)
                    question.refresh_from_db()
                    self.assertEqual(question.regions, self.pieces(top=200))
                    for name in ("stem", "options", "question_type", "type_locked", "figures", "edited",
                                 "answer", "analysis", "read_a", "body_mode", "processing_mode", "color_index"):
                        self.assertEqual(getattr(question, name), before[name], name)
                    self.assertFalse(question.approved or question.reread_requested or question.ocr_pending)
                    self.assertEqual(QuestionLayoutOperation.objects.count(), before_count + 1)
                    self.assert_offline()

    def test_legacy_noop_range_preserves_approval_and_content_revision(self):
        self.approve(self.first)
        before = Question.objects.values().get(pk=self.first.pk)
        result = self.post(f"/api/questions/{self.first.pk}/regions", {"regions": deepcopy(self.first.regions)})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(Question.objects.values().get(pk=self.first.pk), before)
        self.assertTrue(library.approval_is_current(Question.objects.get(pk=self.first.pk)))
        self.assert_offline()

    def test_legacy_add_is_manual_and_uses_durable_idempotent_layout_history(self):
        self.paper.refresh_from_db()
        payload = {"number": 3, "group_id": self.group.pk, "question_type": "free_response",
                   "regions": [*self.pieces(page=1, top=200), *self.pieces(top=100)],
                   "processing_mode": "auto", "body_mode": "text",
                   "layout_revision": self.paper.layout_revision, "client_request_id": str(uuid.uuid4())}
        url = f"/api/papers/{self.paper.pk}/questions"
        result = self.post(url, payload)
        self.assertEqual(result.status_code, 201, result.content)
        added = Question.objects.get(pk=result.json()["question"]["id"])
        self.assertEqual((added.processing_mode, added.body_mode, added.start_source), ("manual", "source_image", "manual"))
        self.assertFalse(added.reread_requested or added.ocr_pending)
        operation = QuestionLayoutOperation.objects.get(client_request_id=payload["client_request_id"])
        self.assertEqual(operation.target_ids, [added.pk])
        before = self.state()
        repeated = self.post(url, payload)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertTrue(repeated.json()["already_applied"])
        self.assertEqual(self.state(), before)
        self.assert_offline()

    def test_http_history_request_lookup_and_noop_keep_last_effective_undo(self):
        operation, _ = self.mutate(self.payload())
        self.first.refresh_from_db()
        noop, _ = self.mutate(self.payload(targets=[{"regions": deepcopy(self.first.regions)}]))
        url = f"/api/papers/{self.paper.pk}/question-layout"
        history = self.client.get(url)
        self.assertEqual(history.status_code, 200, history.content)
        data = history.json()
        self.assertEqual(data["latest_operation"]["id"], str(operation.pk))
        self.assertTrue(data["latest_operation"]["can_undo"])
        self.assertIn(str(noop.pk), {item["id"] for item in data["operations"]})
        before = self.state()
        found = self.client.get(url, {"client_request_id": str(operation.client_request_id)})
        self.assertEqual(found.status_code, 200, found.content)
        self.assertEqual(found.json()["operation"]["id"], str(operation.pk))
        self.assertEqual(len(found.json()["operations"]), 1)
        missing = self.client.get(url, {"client_request_id": str(uuid.uuid4())})
        self.assertEqual(missing.status_code, 200, missing.content)
        self.assertIsNone(missing.json()["operation"])
        self.assertEqual(missing.json()["operations"], [])
        self.assertEqual(self.state(), before, "Reading history must not update any persistent state")

    def test_legacy_add_retry_without_optional_version_tokens_is_idempotent(self):
        payload = {"number": 3, "group_id": self.group.pk, "question_type": "free_response",
                   "regions": self.pieces(page=1), "client_request_id": str(uuid.uuid4())}
        url = f"/api/papers/{self.paper.pk}/questions"
        saved = self.post(url, payload)
        self.assertEqual(saved.status_code, 201, saved.content)
        before = self.state()
        retried = self.post(url, payload)
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertTrue(retried.json()["already_applied"])
        self.assertEqual(retried.json()["question"]["id"], saved.json()["question"]["id"])
        self.assertEqual(self.state(), before)
        self.assert_offline()

    def test_legacy_range_retry_without_optional_version_tokens_is_idempotent(self):
        payload = {"regions": self.pieces(top=100), "client_request_id": str(uuid.uuid4())}
        url = f"/api/questions/{self.first.pk}/regions"
        saved = self.post(url, payload)
        self.assertEqual(saved.status_code, 200, saved.content)
        before = self.state()
        retried = self.post(url, payload)
        self.assertEqual(retried.status_code, 200, retried.content)
        self.assertTrue(retried.json()["already_applied"])
        self.assertEqual(self.state(), before)
        self.assert_offline()

    def test_http_stale_request_reports_conflict_token_without_mutation(self):
        payload = self.payload()
        payload["layout_revision"] += 1
        before = self.state()
        result = self.post(f"/api/papers/{self.paper.pk}/question-layout", payload)
        self.assertEqual(result.status_code, 409, result.content)
        self.paper.refresh_from_db()
        self.assertEqual(result.json()["layout_revision"], self.paper.layout_revision)
        self.assertEqual(self.state(), before)

    def test_legacy_deletion_changes_shared_revision_and_invalidates_old_layout_draft(self):
        payload = self.payload()
        before_revision = self.paper.layout_revision
        result = self.post(f"/api/papers/{self.paper.pk}/questions/delete", {"question_ids": [self.second.pk]})
        self.assertEqual(result.status_code, 200, result.content)
        self.paper.refresh_from_db()
        self.assertGreater(self.paper.layout_revision, before_revision)
        self.rejected(payload)


class QuestionLayoutMigrationTests(TransactionTestCase):
    """Upgrade the last release's schema with active and recycled sample cards."""

    migrate_from = [("core", "0020_alter_question_state")]
    migrate_to = [("core", "0021_question_layout")]

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_upgrade_assigns_all_colours_without_changing_manual_content_or_approval(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old = executor.loader.project_state(self.migrate_from).apps
        OldPaper, OldQuestion = old.get_model("core", "Paper"), old.get_model("core", "Question")
        paper = OldPaper.objects.create(filename="synthetic-upgrade.pdf", kind="pdf", sha256="a" * 64,
            status="ready", pages=[{"page_idx": 0, "width": 595, "height": 842}])
        records = []
        for number, deleted in ((1, False), (2, True)):
            question = OldQuestion.all_objects.create(paper=paper, number=number, stem=f"保留的人工内容 {number}",
                options={"A": "甲"}, state="green", edited=True, approved=True,
                approved_at=timezone.now(), approved_content_hash=str(number) * 64,
                approval_source="human", content_revision=number,
                deleted_at=timezone.now() if deleted else None,
                regions=[{"page_idx": 0, "bbox": [40, 80, 900, 100]}])
            records.append((question.pk, {key: getattr(question, key) for key in (
                "stem", "options", "approved", "approved_at", "approved_content_hash", "approval_source",
                "content_revision", "deleted_at", "regions", "source_key")}))
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        new = executor.loader.project_state(self.migrate_to).apps
        NewPaper, NewQuestion = new.get_model("core", "Paper"), new.get_model("core", "Question")
        upgraded = NewPaper.objects.get(pk=paper.pk)
        self.assertEqual((upgraded.layout_revision, upgraded.layout_page_epoch), (0, 0))
        self.assertEqual(NewQuestion.all_objects.filter(paper_id=paper.pk).count(), 2)
        for identity, snapshot in records:
            question = NewQuestion.all_objects.get(pk=identity)
            self.assertIn(question.color_index, range(6))
            for name, value in snapshot.items():
                self.assertEqual(getattr(question, name), value, name)
