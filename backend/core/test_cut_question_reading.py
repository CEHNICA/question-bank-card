"""Saved crops -> explicit recognition queue -> candidate -> explicit adoption."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from . import account_pool, library, library_jobs, mineru, pipeline, readers, views, test_manual_intake_review as manual_review
from .models import Question


class CutQuestionReadingTests(TestCase):
    paper = manual_review.ManualIntakeReviewTests.paper
    run_read = manual_review.ManualIntakeReviewTests.run_read

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        setting = override_settings(DATA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        self.ready = mock.patch.object(views, "_vision_ready", return_value=True)
        self.ready.start()
        self.addCleanup(self.ready.stop)
        self.original = self.paper()
        self.url = f"/api/papers/{self.original.pk}/read-cut-questions"

    def cut(self, number=1, **fields):
        defaults = dict(paper=self.original, number=number, body_mode="source_image",
            processing_mode="manual", question_type="free_response", state="yellow",
            regions=[{"page_idx": 1, "bbox": [50, 50, 900, 420]},
                     {"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        return Question.objects.create(**{**defaults, **fields})

    def post(self, payload=None, *, url=None):
        return self.client.post(url or self.url, json.dumps(payload or {}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_queue_uses_saved_ordered_crops_and_does_not_read_or_modify_body(self):
        question = self.cut(edited=True, stem="saved manual evidence",
            figures=[{"page_idx": 0, "bbox": [100, 100, 200, 200], "slot": "stem", "source": "manual"}],
            figure_review={"status": "ok", "source": "human"}, flags=["manual range warning"])
        before = deepcopy(Question.objects.values().get(pk=question.pk))
        with mock.patch("requests.sessions.Session.request", side_effect=AssertionError("unexpected cloud request")), \
                mock.patch.object(readers, "chat") as model, \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as mineru:
            result = self.post({"question_ids": [question.pk], "revisions": {str(question.pk): 0}, "revision": 0})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["queued_ids"], [question.pk])
        self.assertEqual(result.json()["question_revisions"], {str(question.pk): 1})
        model.assert_not_called()
        mineru.assert_not_called()
        question.refresh_from_db()
        self.assertTrue(question.ocr_pending and question.reread_requested)
        after = Question.objects.values().get(pk=question.pk)
        for key in before.keys() - {"content_revision", "reread_requested", "ocr_pending", "updated_at"}:
            self.assertEqual(after[key], before[key], key)
        repeated = self.post({"question_ids": [question.pk], "revisions": {str(question.pk): 0}})
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()["queued"], 0)
        question.refresh_from_db()
        self.assertEqual(question.content_revision, 1)

    def test_empty_selection_queues_only_eligible_crops_and_preserves_every_other_result(self):
        eligible = self.cut()
        approved = self.cut(2, approved=True, approved_content_hash="retained")
        text = self.cut(3, body_mode="text", stem="actual manual text", edited=True)
        pending = self.cut(4, ocr_pending=True, reread_requested=True)
        suggested = self.cut(5, ocr_suggestion={"revision": 0, "stem": "successful saved suggestion"})
        invalid = self.cut(6, regions=[])
        deleted = self.cut(7, deleted_at=timezone.now())
        protected = [approved, text, pending, suggested, invalid, deleted]
        snapshots = {q.pk: deepcopy(Question.all_objects.values().get(pk=q.pk)) for q in protected}
        result = self.post({"question_ids": []})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["queued_ids"], [eligible.pk])
        self.assertEqual({item["id"] for item in result.json()["skipped"]},
            {q.pk for q in protected if q is not deleted})
        for question in protected:
            self.assertEqual(Question.all_objects.values().get(pk=question.pk), snapshots[question.pk])

    def test_publication_history_blocks_batch_even_after_approval_is_cleared(self):
        question = self.cut()
        library.approve(question, now=timezone.now())
        question.save()
        with mock.patch.object(library_jobs, "queue_on_intake"):
            publication, _ = library.publish(question)
        published = deepcopy(publication.content)
        Question.objects.filter(pk=question.pk).update(approved=False, approved_content_hash="")
        before = Question.objects.values().get(pk=question.pk)
        result = self.post({"question_ids": [question.pk]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["queued"], 0)
        self.assertIn("入库", result.json()["skipped"][0]["reason"])
        self.assertEqual(Question.objects.values().get(pk=question.pk), before)
        publication.refresh_from_db()
        self.assertEqual(publication.content, published)

    def test_no_reader_fails_without_queue_and_no_repeated_background_work(self):
        question = self.cut()
        before = Question.objects.values().get(pk=question.pk)
        with mock.patch.object(views, "_vision_ready", return_value=False), \
                mock.patch.object(readers, "chat") as model:
            result = self.post()
            self.assertEqual(pipeline.process_rereads(), 0)
        self.assertEqual(result.status_code, 409)
        self.assertIn("设置 → 读题服务", result.json()["error"])
        self.assertEqual(Question.objects.values().get(pk=question.pk), before)
        model.assert_not_called()

    def test_stale_paper_or_question_version_rejects_entire_batch(self):
        first, changed = self.cut(), self.cut(2, content_revision=3)
        before = list(Question.objects.values().order_by("id"))
        for payload in ({"revision": 1},
                {"question_ids": [first.pk, changed.pk], "revisions": {str(first.pk): 0, str(changed.pk): 2}}):
            result = self.post(payload)
            self.assertEqual(result.status_code, 409, result.content)
            self.assertEqual(list(Question.objects.values().order_by("id")), before)

    def test_invalid_foreign_or_deleted_ids_do_not_partly_queue(self):
        eligible = self.cut()
        deleted = self.cut(2, deleted_at=timezone.now())
        other = self.cut(3, paper=self.paper())
        for invalid, status in (([True], 400), ([eligible.pk, eligible.pk], 400),
                ([eligible.pk, other.pk], 409), ([eligible.pk, deleted.pk], 409)):
            result = self.post({"question_ids": invalid})
            self.assertEqual(result.status_code, status, result.content)
        eligible.refresh_from_db()
        self.assertFalse(eligible.ocr_pending or eligible.reread_requested)

    def test_recognition_stays_candidate_until_adoption_and_uses_saved_regions(self):
        question = self.cut()
        key, regions = question.source_key, deepcopy(question.regions)
        self.assertEqual(self.post().json()["queued"], 1)
        question.refresh_from_db()
        def answer(snapshot, store):
            self.assertEqual(snapshot["regions"], regions)
            return {"stem": "Recognized draft", "options": {}, "question_type": "free_response",
                "figures": [], "flags": [], "state": "green"}
        self.run_read(question, answer)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.state), ("source_image", "", "yellow"))
        self.assertFalse(question.ocr_pending or question.reread_requested or question.approved)
        self.assertEqual(question.ocr_suggestion["stem"], "Recognized draft")
        self.assertEqual(self.post().json()["queued"], 0)
        result = self.post({"revision": question.content_revision}, url=f"/api/questions/{question.pk}/apply-reading")
        self.assertEqual(result.status_code, 200, result.content)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem), ("text", "Recognized draft"))
        self.assertEqual(question.source_key, key)
        self.assertFalse(question.approved or question.publications.exists())

    def test_late_candidate_cannot_replace_new_range_or_manual_text(self):
        question = self.cut()
        self.post()
        question.refresh_from_db()
        def answer(snapshot, store):
            self.post({"revision": question.content_revision, "regions": [
                {"page_idx": 0, "bbox": [100, 100, 850, 500]}]}, url=f"/api/questions/{question.pk}/regions")
            self.post({"stem": "latest actual manual correction", "question_type": "free_response"},
                url=f"/api/questions/{question.pk}/text")
            return {"stem": "late model text", "flags": [], "state": "green"}
        self.run_read(question, answer)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem), ("text", "latest actual manual correction"))
        self.assertFalse(question.ocr_suggestion or question.ocr_pending or question.reread_requested)

    def test_failed_candidate_requires_another_explicit_request_and_can_retry(self):
        question = self.cut()
        self.post()
        question.refresh_from_db()
        self.run_read(question, lambda *args: {"error": "service unavailable", "state": "red", "flags": []})
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending or question.reread_requested)
        self.assertEqual(question.body_mode, "source_image")
        with mock.patch.object(pipeline, "read_card") as reader:
            self.assertEqual(pipeline.process_rereads(), 0)
        reader.assert_not_called()
        self.assertEqual(self.post({"question_ids": [question.pk],
            "revisions": {str(question.pk): question.content_revision}}).json()["queued"], 1)
        question.refresh_from_db()
        self.assertEqual(question.content_revision, 2)

    def test_request_guard_and_in_progress_paper_cannot_start_batch(self):
        self.cut()
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.client.post(self.url, "{}", content_type="application/json").status_code, 403)
        self.original.status = "parsing"
        self.original.save(update_fields=["status"])
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(Question.objects.filter(ocr_pending=True).exists())

    def test_failed_stopped_paper_must_be_locally_resumed_before_batch_reading(self):
        question = self.cut()
        self.original.status, self.original.error = "failed", mineru.STOPPED_MESSAGE
        self.original.save(update_fields=["status", "error"])
        (pipeline.paper_dir(self.original) / mineru.CANCEL_FILE).write_text("old stop")
        with mock.patch.object(readers, "chat") as reader:
            self.assertEqual(self.post().status_code, 409)
            self.assertEqual(self.post(url=f"/api/questions/{question.pk}/reread").status_code, 409)
            self.assertEqual(pipeline.process_rereads(), 0)
        reader.assert_not_called()
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending or question.reread_requested)

    def stop(self, payload=None):
        return self.post(payload, url=f"/api/papers/{self.original.pk}/stop-cut-reading")

    def test_stop_queued_image_reads_is_immediate_idempotent_and_preserves_other_work(self):
        question = self.cut()
        self.post()
        question.refresh_from_db()
        protected = self.cut(2, ocr_pending=True, reread_requested=True,
            ocr_suggestion={"revision": 0, "stem": "retained candidate"})
        library.approve(protected, now=timezone.now())
        protected.save()
        with mock.patch.object(library_jobs, "queue_on_intake"):
            publication, _ = library.publish(protected)
        publication_content = deepcopy(publication.content)
        text = self.cut(3, body_mode="text", stem="unrelated text OCR", state="reading", reread_requested=True)
        text_before = Question.objects.values().get(pk=text.pk)
        paper_before = self.original.__class__.objects.values().get(pk=self.original.pk)
        protected_before = Question.objects.values().get(pk=protected.pk)
        result = self.stop()
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(set(result.json()["stopped_ids"]), {question.pk, protected.pk})
        self.assertEqual(result.json()["paper"]["status"], "ready")
        self.assertEqual(self.original.__class__.objects.values().get(pk=self.original.pk), paper_before)
        self.assertEqual(Question.objects.values().get(pk=text.pk), text_before)
        protected_after = Question.objects.values().get(pk=protected.pk)
        for key in protected_before.keys() - {"content_revision", "reread_requested", "ocr_pending", "updated_at"}:
            self.assertEqual(protected_after[key], protected_before[key], key)
        protected.refresh_from_db()
        self.assertTrue(library.approval_is_current(protected))
        publication.refresh_from_db()
        self.assertEqual(publication.content, publication_content)
        again = self.stop({"question_ids": [question.pk], "revisions": {str(question.pk): 1}})
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(again.json()["stopped"], 0)
        question.refresh_from_db()
        self.assertEqual(question.content_revision, 2)

    def test_stop_running_batch_rejects_late_result_and_does_not_start_remaining_crop(self):
        first, second = self.cut(), self.cut(2)
        self.post()
        first.refresh_from_db()
        second.refresh_from_db()
        called = []
        def answer(snapshot, store):
            called.append(snapshot["id"])
            self.assertEqual(self.stop().json()["stopped"], 2)
            return {"stem": "late model answer", "state": "green", "flags": []}
        with mock.patch.object(pipeline, "ThreadPoolExecutor", manual_review.ImmediateExecutor), \
                mock.patch.object(pipeline, "close_old_connections"), \
                mock.patch.object(pipeline, "_reader_parallelism", return_value=1), \
                mock.patch.object(readers, "assistant_mode", return_value=False), \
                mock.patch.object(pipeline, "read_card", side_effect=answer):
            pipeline.read_questions(self.original, [first, second])
        self.assertEqual(called, [first.pk])
        for question in (first, second):
            question.refresh_from_db()
            self.assertFalse(question.ocr_pending or question.reread_requested or question.ocr_suggestion)
            self.assertEqual((question.body_mode, question.stem), ("source_image", ""))
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, "ready")

    def test_stop_then_new_request_is_not_overwritten_by_old_worker(self):
        question = self.cut()
        self.post()
        question.refresh_from_db()
        def answer(snapshot, store):
            self.stop()
            self.assertEqual(self.post({"question_ids": [question.pk]}).json()["queued"], 1)
            return {"stem": "old late text", "state": "green", "flags": []}
        self.run_read(question, answer)
        question.refresh_from_db()
        self.assertEqual(question.content_revision, 3)
        self.assertTrue(question.ocr_pending and question.reread_requested)
        self.assertFalse(question.ocr_suggestion)
        self.run_read(question, lambda *args: {"stem": "new requested text", "state": "yellow", "flags": []})
        question.refresh_from_db()
        self.assertEqual(question.ocr_suggestion["stem"], "new requested text")
        self.assertFalse(question.ocr_pending or question.reread_requested)

    def test_stop_selected_version_guard_does_not_cancel_changed_or_unselected_reads(self):
        first, second = self.cut(), self.cut(2)
        self.post()
        rejected = self.stop({"question_ids": [first.pk, second.pk], "revisions": {str(second.pk): 0}})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(Question.objects.filter(ocr_pending=True).count(), 2)
        done = self.stop({"question_ids": [first.pk], "revisions": {str(first.pk): 1}, "revision": 0})
        self.assertEqual(done.json()["stopped_ids"], [first.pk])
        second.refresh_from_db()
        self.assertTrue(second.ocr_pending and second.reread_requested)

    def test_stop_account_wait_cancels_without_sending_http(self):
        question = self.cut()
        self.post()
        question.refresh_from_db()
        pool = account_pool.AccountPool("minimax", ("test-only",), per_account=1)
        engine = readers.Engine("minimax", "test-model")
        def wait(*, timeout):
            self.assertEqual(self.stop().json()["stopped"], 1)
        def answer(snapshot, store):
            return readers.chat(engine, "copy saved crop", ["test-image"])
        with pool.lease(), mock.patch.object(pool._condition, "wait", side_effect=wait), \
                mock.patch.object(readers, "account_pool", return_value=pool), \
                mock.patch.object(readers, "_hedge_after", return_value=0), \
                mock.patch.object(readers, "_post") as http:
            self.run_read(question, answer)
        http.assert_not_called()
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending or question.reread_requested or question.ocr_suggestion)


class WholeCropCancellationPolicyTests(SimpleTestCase):
    def test_cancel_only_keeps_whole_crop_http_timeout_and_chosen_service_policy(self):
        engine = readers.Engine("minimax", "test-model")
        response = mock.Mock(status_code=200)
        with readers.cancellable_request(lambda: False), readers.selected_services_only(), \
                mock.patch.object(readers.requests, "post", return_value=response) as post, \
                mock.patch.object(readers, "_chat_hedged", return_value="original text") as chosen:
            self.assertIs(readers._post("https://example.invalid", "test-only", {}), response)
            self.assertEqual(post.call_args.kwargs["timeout"], (10, 150))
            self.assertEqual(readers.chat(engine, "copy saved crop", ["test-image"]), "original text")
            chosen.assert_called_once()

    def test_cancel_only_does_not_accept_a_late_http_response(self):
        cancelled = False
        def post(*args, **kwargs):
            nonlocal cancelled
            cancelled = True
            return mock.Mock(status_code=200)
        with readers.cancellable_request(lambda: cancelled), \
                mock.patch.object(readers.requests, "post", side_effect=post):
            with self.assertRaises(readers.ReaderRequestStopped):
                readers._post("https://example.invalid", "test-only", {})
