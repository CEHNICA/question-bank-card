"""Hand-cut regions join normal reading/review, without an adoption step."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from . import library, library_jobs, pipeline, source_images, views
from . import test_manual_intake_review as manual_review
from .models import Question


class DirectCutReadingTests(TestCase):
    paper = manual_review.ManualIntakeReviewTests.paper
    run_read = manual_review.ManualIntakeReviewTests.run_read

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
        patch = mock.patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected network"))
        patch.start()
        self.addCleanup(patch.stop)

    def cut(self, number=1, **fields):
        defaults = dict(paper=self.original, number=number, body_mode="source_image", processing_mode="manual",
            question_type="free_response", state="yellow", regions=[{"page_idx": 0, "bbox": [40, 40, 900, 480]}])
        return Question.objects.create(**{**defaults, **fields})

    def post(self, action, payload=None):
        return self.client.post(action, json.dumps(payload or {}), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def queue(self, question):
        result = self.post(f"/api/papers/{self.original.pk}/read-cut-questions", {"question_ids": [question.pk]})
        self.assertEqual(result.status_code, 200, result.content)
        question.refresh_from_db()
        return result

    def reading(self, **fields):
        return {"stem": "Recognized question", "options": {}, "question_type": "free_response",
            "state": "green", "flags": [], "error": "", "text_source": "single", "figures": [],
            "read_a": {"stem": "Recognized question", "engine": "mock"}, "read_b": {"skipped": "disabled"},
            "read_c": {}, **fields}

    def test_success_keeps_original_assets_and_normal_fields_without_approval(self):
        question = self.cut(answer="printed answer", analysis="printed explanation")
        images = deepcopy(source_images.assets(question))
        key, regions = question.source_key, deepcopy(question.regions)
        self.queue(question)
        self.run_read(question, lambda *args: self.reading(origin="printed source"))
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.text_source, question.state),
            ("text", "Recognized question", "single", "green"))
        self.assertEqual((question.answer, question.analysis, question.origin),
            ("printed answer", "printed explanation", "printed source"))
        self.assertEqual((question.source_key, question.regions), (key, regions))
        self.assertEqual(views.question_json(question)["question_images"], images)
        response = self.client.get(images[0]["url"])
        try:
            self.assertEqual(response.status_code, 200)
        finally:
            response.close()
        self.assertFalse(question.approved or question.ocr_suggestion or question.ocr_pending or question.reread_requested)
        with self.assertRaisesRegex(ValueError, "还没有通过终审"):
            library.publish(question)

    def test_waiting_manual_edit_cannot_be_overwritten_even_if_external_writer_forgot_revision(self):
        question = self.cut()
        self.queue(question)
        def read(*args):
            Question.objects.filter(pk=question.pk).update(edited=True, stem="new manual correction")
            return self.reading()
        self.run_read(question, read)
        question.refresh_from_db()
        self.assertEqual(question.stem, "new manual correction")
        self.assertEqual(question.body_mode, "source_image")
        self.assertFalse(question.ocr_pending or question.reread_requested)

    def test_review_during_read_protects_original_and_existing_hash(self):
        question = self.cut()
        self.queue(question)
        digest = []
        def read(*args):
            current = Question.objects.get(pk=question.pk)
            library.approve(current, now=timezone.now())
            current.save()
            digest.append(current.approved_content_hash)
            return self.reading()
        self.run_read(question, read)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem), ("source_image", ""))
        self.assertEqual(question.approved_content_hash, digest[0])
        self.assertTrue(library.approval_is_current(question))
        self.assertFalse(question.ocr_pending)

    def test_publication_during_read_remains_immutable_after_approval_is_cleared(self):
        question = self.cut()
        self.queue(question)
        saved = []
        def read(*args):
            current = Question.objects.get(pk=question.pk)
            library.approve(current, now=timezone.now())
            current.save()
            with mock.patch.object(library_jobs, "queue_on_intake"):
                publication, _ = library.publish(current)
            saved.append((publication, deepcopy(publication.content)))
            Question.objects.filter(pk=question.pk).update(approved=False, approved_content_hash="")
            return self.reading()
        self.run_read(question, read)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem), ("source_image", ""))
        publication, content = saved[0]
        publication.refresh_from_db()
        self.assertEqual(publication.content, content)

    def test_existing_manual_text_and_approved_original_are_not_queued(self):
        manual = self.cut(edited=True, stem="existing manual words")
        approved = self.cut(2, approved=True)
        for question in (manual, approved):
            self.assertEqual(self.queue(question).json()["queued"], 0)
            result = self.post(f"/api/questions/{question.pk}/reread")
            self.assertEqual(result.status_code, 409)
            self.assertFalse(Question.objects.get(pk=question.pk).ocr_pending)

    def test_actual_source_file_change_rejects_late_text(self):
        question = self.cut()
        self.queue(question)
        def read(*args):
            Path(self.original.source_path).write_bytes(manual_review.original_pdf(pages=3))
            return self.reading()
        self.run_read(question, read)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem), ("source_image", ""))
        self.assertIn("原卷文件或裁片已变化", question.error)
        self.assertFalse(question.ocr_pending)

    def test_legacy_current_result_converts_without_service_or_new_call_and_keeps_evidence(self):
        candidate = self.reading()
        candidate.pop("read_a")
        candidate.pop("read_b")
        candidate.pop("read_c")
        candidate["revision"] = 0
        question = self.cut(ocr_suggestion=candidate, read_a={"engine": "old saved model", "stem": "Recognized question"},
            read_b={"skipped": "disabled"}, answer="printed answer")
        with mock.patch.object(views, "_vision_ready", return_value=False), mock.patch.object(pipeline.readers, "chat") as chat:
            result = self.queue(question)
        chat.assert_not_called()
        self.assertEqual(result.json()["converted_ids"], [question.pk])
        self.assertEqual(result.json()["queued"], 0)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.read_a["engine"], question.content_revision),
            ("text", "Recognized question", "old saved model", 1))
        self.assertEqual(question.answer, "printed answer")
        self.assertFalse(question.approved or question.ocr_suggestion)
        self.assertEqual(pipeline.promote_saved_readings(), [])

    def test_legacy_stale_failed_reviewed_published_edited_or_pending_results_are_untouched(self):
        base = {**self.reading(), "revision": 0}
        protected = [self.cut(ocr_suggestion={**base, "revision": 1}),
            self.cut(2, ocr_suggestion={**base, "error": "failed"}),
            self.cut(3, ocr_suggestion=base, approved=True),
            self.cut(4, ocr_suggestion=base, edited=True, stem="human text"),
            self.cut(5, ocr_suggestion=base, ocr_pending=True),
            self.cut(6, ocr_suggestion={**base, "paper_revision": 44})]
        before = {q.pk: deepcopy(Question.objects.values().get(pk=q.pk)) for q in protected}
        self.assertEqual(pipeline.promote_saved_readings(), [])
        for q in protected:
            self.assertEqual(Question.objects.values().get(pk=q.pk), before[q.pk])

    def test_legacy_locked_type_and_manual_figures_follow_normal_preservation_rules(self):
        figure = {"page_idx": 0, "bbox": [100, 100, 250, 250], "slot": "stem", "source": "manual"}
        candidate = {**self.reading(question_type="single_choice"), "revision": 0,
            "flags": ["配图提醒"], "figure_review": {"status": "blocked_missing"}}
        question = self.cut(type_locked=True, figures=[figure], ocr_suggestion=candidate)
        self.assertEqual(pipeline.promote_saved_readings(), [question.pk])
        question.refresh_from_db()
        self.assertEqual(question.question_type, "free_response")
        self.assertEqual(question.figures, [figure])
        self.assertFalse(question.approved)

    def test_success_then_manual_text_reread_can_stop_and_discard_late_text(self):
        question = self.cut()
        self.queue(question)
        self.run_read(question, lambda *args: self.reading())
        question.refresh_from_db()
        result = self.post(f"/api/questions/{question.pk}/reread", {"revision": question.content_revision})
        self.assertEqual(result.status_code, 200, result.content)
        question.refresh_from_db()
        self.assertTrue(question.ocr_pending)
        def read(*args):
            stopped = self.post(f"/api/papers/{self.original.pk}/stop-cut-reading", {"question_ids": [question.pk]})
            self.assertEqual(stopped.json()["stopped_ids"], [question.pk])
            return self.reading(stem="late replacement")
        self.run_read(question, read)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.state), ("text", "Recognized question", "green"))
        self.assertFalse(question.ocr_pending or question.reread_requested or question.approved)

    def test_explicit_reread_after_historical_manual_edit_is_allowed_for_unpublished_text(self):
        question = self.cut(body_mode="text", edited=True, stem="past manual text", text_source="human", state="green")
        result = self.post(f"/api/questions/{question.pk}/reread")
        self.assertEqual(result.status_code, 200, result.content)
        question.refresh_from_db()
        self.assertFalse(question.edited)
        self.run_read(question, lambda *args: self.reading())
        question.refresh_from_db()
        self.assertEqual(question.stem, "Recognized question")
        self.assertFalse(question.ocr_pending or question.approved)

    def test_empty_edited_original_still_gets_text_but_keeps_type_and_manual_figures(self):
        figure = {"page_idx": 0, "bbox": [100, 100, 250, 250], "slot": "stem", "source": "manual"}
        question = self.cut(edited=True, type_locked=True, stem="", figures=[figure])
        self.assertEqual(self.queue(question).json()["queued"], 1)
        self.run_read(question, lambda *args: self.reading(question_type="single_choice"))
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.question_type),
            ("text", "Recognized question", "free_response"))
        self.assertEqual(question.figures, [figure])
        self.assertFalse(question.edited or question.approved)
