"""Queued-without-worker, exact cancellation and bounded model waits, offline."""
from __future__ import annotations

from datetime import timedelta
import json
import threading
import time
from unittest import mock

from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from . import account_pool, readers, region_reads
from .models import RegionRead
from .test_v110_types_origin import TempDataMixin


class RegionDeadlineTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, stem="original saved question", approved=True)
        self.url = f"/api/questions/{self.question.pk}/region-read"
        patch = mock.patch.object(readers, "primary_engine", return_value=readers.Engine("minimax", "test-model"))
        patch.start()
        self.addCleanup(patch.stop)

    def request(self, method="post", **payload):
        body = ({"page_idx": 0, "bbox": [100, 80, 500, 120], "target": "A"}
                if method == "post" else {})
        return getattr(self.client, method)(self.url, json.dumps({**body, **payload}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")

    def expire(self, job):
        RegionRead.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(seconds=91))
        job.refresh_from_db()

    def test_queued_without_worker_expires_on_page_refresh_with_precise_reason(self):
        response = self.request()
        shown = response.json()["question"]["region_read"]
        self.assertEqual(shown["status_label"], "等待读题后台")
        self.assertEqual(shown["timeout_seconds"], 90)
        self.assertIn("deadline_at", shown)
        job = RegionRead.objects.get()
        self.expire(job)
        result = region_reads.latest_json(self.question)
        self.assertEqual(result["status"], "failed")
        self.assertIn("后台可能未运行", result["error"])
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "original saved question")
        self.assertTrue(self.question.approved)

    def test_old_queued_and_running_jobs_are_never_sent_after_worker_restart(self):
        self.request()
        first = RegionRead.objects.get()
        self.expire(first)
        second = RegionRead.objects.create(question=self.question, page_idx=0, bbox=[100, 80, 500, 120],
            target="A", status="running")
        self.expire(second)
        with mock.patch.object(readers, "chat") as model:
            self.assertEqual(region_reads.recover_interrupted(), 0)
            self.assertEqual(region_reads.process_pending(), 0)
        model.assert_not_called()
        self.assertEqual(set(RegionRead.objects.values_list("status", flat=True)), {"failed"})

    def test_delete_old_read_id_cannot_cancel_new_region(self):
        old = self.request().json()["question"]["region_read"]["id"]
        new = self.request().json()["question"]["region_read"]["id"]
        response = self.request("delete", read_id=old)
        self.assertEqual(response.json()["question"]["region_read"]["id"], new)
        self.assertTrue(RegionRead.objects.filter(pk=new, status="queued").exists())

    def test_cancel_token_before_late_post_prevents_queue_and_model_call(self):
        token = "late-post-token"
        response = self.request("delete", client_request_id=token)
        self.assertIsNone(response.json()["question"]["region_read"])
        response = self.request(client_request_id=token, revision=self.question.content_revision)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["question"]["region_read"])
        with mock.patch.object(readers, "chat") as model:
            self.assertEqual(region_reads.process_pending(), 0)
        model.assert_not_called()

    def test_token_is_idempotent_and_old_token_cancel_does_not_affect_new(self):
        first = self.request(client_request_id="first").json()["question"]["region_read"]
        repeated = self.request(client_request_id="first").json()["question"]["region_read"]
        self.assertEqual(first["id"], repeated["id"])
        self.assertEqual(repeated["client_request_id"], "first")
        second = self.request(client_request_id="second").json()["question"]["region_read"]
        response = self.request("delete", read_id=first["id"], client_request_id="first")
        self.assertEqual(response.json()["question"]["region_read"]["id"], second["id"])
        self.assertTrue(RegionRead.objects.filter(pk=second["id"], status="queued").exists())

    def test_wrong_id_plus_current_token_cannot_cancel_current_read(self):
        read = self.request(client_request_id="current").json()["question"]["region_read"]
        response = self.request("delete", read_id=read["id"] + 10, client_request_id="current")
        self.assertEqual(response.json()["question"]["region_read"]["id"], read["id"])
        self.assertTrue(RegionRead.objects.filter(pk=read["id"], status="queued").exists())

    def test_stale_revision_rejected_before_queue(self):
        response = self.request(revision=self.question.content_revision + 1)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(RegionRead.objects.exists())

    def test_cancel_during_model_call_discards_late_result(self):
        read = self.request(client_request_id="cancel-now").json()["question"]["region_read"]
        def model(*args, **kwargs):
            self.request("delete", read_id=read["id"], client_request_id="cancel-now")
            return "$x=5$"
        with mock.patch.object(readers, "chat", side_effect=model):
            self.assertEqual(region_reads.process_pending(), 1)
        self.assertIsNone(region_reads.latest_json(self.question))
        self.assertFalse(RegionRead.objects.filter(text="$x=5$").exists())
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "original saved question")
        self.assertTrue(self.question.approved)

    def test_failed_read_preserves_request_metadata_can_close_and_generate_again(self):
        token = "failed-read-token"
        revision = self.question.content_revision
        read = self.request(client_request_id=token, revision=revision).json()["question"]["region_read"]
        with mock.patch.object(readers, "chat", side_effect=readers.ReaderError("service unavailable")) as model:
            self.assertEqual(region_reads.process_pending(), 1)
        model.assert_called_once()
        failed = region_reads.latest_json(self.question)
        self.assertEqual((failed["status"], failed["revision"], failed["client_request_id"]),
            ("failed", revision, token))
        response = self.request("delete", read_id=read["id"], client_request_id=token)
        self.assertIsNone(response.json()["question"]["region_read"])
        self.assertIsNone(region_reads.latest_json(self.question))
        self.assertEqual(RegionRead.objects.count(), 1)  # cancelled original, no second tombstone
        self.assertIsNone(self.request(client_request_id=token).json()["question"]["region_read"])
        second = self.request(client_request_id="new-read-token", revision=revision).json()["question"]["region_read"]
        self.assertNotEqual(second["id"], read["id"])
        with mock.patch.object(readers, "chat", return_value="$x=5$"):
            self.assertEqual(region_reads.process_pending(), 1)
        done = region_reads.latest_json(self.question)
        self.assertEqual((done["id"], done["status"], done["client_request_id"], done["revision"]),
            (second["id"], "done", "new-read-token", revision))
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "original saved question")
        self.assertTrue(self.question.approved)

    def test_finish_failure_uses_saved_request_metadata_even_if_in_memory_proposal_lost_it(self):
        self.request(client_request_id="stored-baseline", revision=self.question.content_revision)
        job = RegionRead.objects.get()
        job.status = "running"
        job.save(update_fields=["status"])
        job.recommendation = {"base": {}, "status": "manual"}  # partially processed model response
        region_reads._finish(job, "failed", error="invalid model response")
        failed = region_reads.latest_json(self.question)
        self.assertEqual(failed["client_request_id"], "stored-baseline")
        self.assertEqual(failed["revision"], self.question.content_revision)

    def test_expired_running_success_cannot_turn_into_done(self):
        self.request()
        def model(*args, **kwargs):
            job = RegionRead.objects.get()
            self.expire(job)
            return "$x=5$"
        with mock.patch.object(readers, "chat", side_effect=model):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.text), ("failed", ""))
        self.assertIn("等待超时", job.error)

    def test_edited_revision_discards_late_text_but_keeps_actual_user_edit(self):
        self.request()
        def model(*args, **kwargs):
            self.question.__class__.objects.filter(pk=self.question.pk).update(
                stem="actual user edit", content_revision=self.question.content_revision + 1)
            return "$x=5$"
        with mock.patch.object(readers, "chat", side_effect=model):
            region_reads.process_pending()
        job = RegionRead.objects.get()
        self.assertEqual((job.status, job.text), ("failed", ""))
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "actual user edit")


class BoundedInteractiveReaderTests(SimpleTestCase):
    def test_auto_scope_never_falls_back_to_another_configured_service(self):
        engine = readers.Engine("minimax", "test-model")
        with mock.patch.dict("os.environ", {"QB_PROVIDER_FALLBACK": "1"}), \
                mock.patch.object(readers, "_chat_hedged", side_effect=readers.ReaderUnavailable("busy")) as chosen, \
                mock.patch.object(readers, "fallback_engines") as fallback:
            self.assertTrue(readers._fallback_enabled())
            with readers.selected_services_only():
                with self.assertRaises(readers.ReaderUnavailable):
                    readers.chat(engine, "copy original", ["test-image"])
            self.assertTrue(readers._fallback_enabled())
        chosen.assert_called_once_with(engine, "copy original", ["test-image"], 3000)
        fallback.assert_not_called()

    def test_auto_scope_missing_chosen_primary_does_not_select_another_service(self):
        with readers.selected_services_only(), \
                mock.patch.object(readers, "_primary_selection", return_value="minimax_m3"), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "_first_configured") as alternative:
            self.assertIsNone(readers.primary_engine())
        alternative.assert_not_called()

    def test_auto_checker_without_independent_provider_does_not_duplicate_primary(self):
        engine = readers.Engine("minimax", "test-model")
        with readers.selected_services_only(), \
                mock.patch.object(readers, "assistant_mode", return_value=False), \
                mock.patch.object(readers, "_configured_selection", return_value="auto"), \
                mock.patch.object(readers, "primary_engine", return_value=engine), \
                mock.patch.object(readers, "_first_configured", return_value=None):
            self.assertIsNone(readers.checker_engine())

    def test_interactive_crop_does_not_fallback_or_send_duplicate_hedge(self):
        engine = readers.Engine("minimax", "test-model")
        with readers.bounded_request(5), \
                mock.patch.object(readers, "_chat_once", side_effect=readers.ReaderUnavailable("busy")) as once, \
                mock.patch.object(readers, "_chat_hedged") as hedge, \
                mock.patch.object(readers, "fallback_engines") as fallback:
            with self.assertRaises(readers.ReaderUnavailable):
                readers.chat(engine, "copy original", ["test-image"])
        once.assert_called_once()
        hedge.assert_not_called()
        fallback.assert_not_called()

    def test_account_wait_is_inside_total_budget_and_sends_no_http(self):
        engine = readers.Engine("minimax", "test-model")
        pool = account_pool.AccountPool("minimax", ("test-only",), per_account=1)
        with pool.lease(), readers.bounded_request(0.05), \
                mock.patch.object(readers, "account_pool", return_value=pool), \
                mock.patch.object(readers.requests, "post") as post:
            started = time.monotonic()
            with self.assertRaises(readers.ReaderRequestStopped):
                readers.chat(engine, "copy original", ["test-image"])
            self.assertLess(time.monotonic() - started, 1.5)
            post.assert_not_called()
            self.assertEqual(pool.spare, 0)

    def test_global_http_slot_wait_is_bounded_and_cancelled_without_http(self):
        semaphore = threading.BoundedSemaphore(1)
        semaphore.acquire()
        with readers.bounded_request(0.05), mock.patch.object(readers, "_IN_FLIGHT", semaphore), \
                mock.patch.object(readers.requests, "post") as post:
            with self.assertRaises(readers.ReaderRequestStopped):
                readers._post("https://example.invalid", "test-only", {})
        post.assert_not_called()
        semaphore.release()

    def test_http_timeout_is_capped_by_remaining_budget_and_cancelled_reply_is_not_accepted(self):
        stopped = False
        def post(*args, **kwargs):
            nonlocal stopped
            self.assertLessEqual(kwargs["timeout"][0], 2)
            self.assertLessEqual(kwargs["timeout"][1], 2)
            stopped = True
            return mock.Mock(status_code=200)
        with readers.bounded_request(2, cancel=lambda: stopped), \
                mock.patch.object(readers.requests, "post", side_effect=post):
            with self.assertRaises(readers.ReaderRequestStopped):
                readers._post("https://example.invalid", "test-only", {})
