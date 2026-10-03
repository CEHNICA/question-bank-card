"""Isolated lifecycle audit: finite API work and explicit assistant handoffs."""
from copy import deepcopy
from datetime import timedelta
import json
import os
from unittest import mock
import uuid

from django.test import TestCase
from django.utils import timezone

from . import features, library, library_ai_settings as service, library_assistant as assistant, library_job_control as control, library_jobs
from .models import LibraryJob, LibrarySolution, PublishedQuestion
from .test_v110_types_origin import TempDataMixin


class LibraryJobControlTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "key.dat"),
            "QB_FEATURES_FILE": str(self.temp / "features.json"),
        })
        environment.start(); self.addCleanup(environment.stop)
        transport = mock.patch.object(service.requests, "post", side_effect=AssertionError("cloud calls forbidden"))
        self.network = transport.start(); self.addCleanup(transport.stop)
        self.pub = self.publication(1)

    def publication(self, number):
        content = {"question_type": "free_response", "stem": "(1) 求 $1+2$。\n(2) 求 $3+4$。", "options": {},
                   "figures": [], "sources": [], "answer": "原卷结果", "analysis": "原卷过程", "source_filename": "isolated.pdf"}
        return PublishedQuestion.objects.create(source_filename="isolated.pdf", number=number, question_type="free_response",
                                               version=1, content=content, content_hash=library.content_hash(content))

    def job(self, *, publication=None, executor="assistant", status="queued", scoped=True, **extra):
        target = publication or self.pub
        return LibraryJob.objects.create(publication=target, executor=executor, kind="answer", status=status,
            solution_scope=scoped, fingerprint=extra.pop("fingerprint", library.generation_fingerprint(target.content, target.pk)), **extra)

    def post(self, endpoint, payload, **extra):
        return self.client.post(endpoint, json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1", **extra)

    def claim(self, job, agent="离线审计助手"):
        return assistant.prepare({"publication_id": str(job.publication_id), "kinds": [job.kind], "job_id": str(job.pk), "agent": agent})

    def submit(self, job, **extra):
        return assistant.complete({"job_id": str(job.pk), "fingerprint": job.fingerprint, "agent": job.agent,
                                   "answer": "(1) 3；(2) 7", "analysis": "(1) $1+2=3$。\n\n(2) $3+4=7$。", **extra})

    def run_api(self, callback):
        with mock.patch.object(service, "require_snapshot"), mock.patch.object(service, "ensure_ready", return_value={"ready": True}), \
             mock.patch.object(library_jobs, "run_answer", side_effect=callback) as run:
            self.assertEqual(library_jobs.process_pending(), 1)
        self.network.assert_not_called()
        return run

    def test_expiry_boundary_only_affects_active_api_jobs(self):
        now = timezone.now()
        queued = self.job(executor="api")
        running = self.job(executor="api", status="running")
        fresh = self.job(executor="api")
        waiting_assistant = self.job()
        claimed_assistant = self.job(status="running")
        done = self.job(executor="api", status="done", result={"answer": "saved"})
        LibraryJob.objects.filter(pk=queued.pk).update(created_at=now-timedelta(seconds=control.API_QUEUE_SECONDS))
        LibraryJob.objects.filter(pk=running.pk).update(updated_at=now-timedelta(seconds=control.API_RUNNING_SECONDS))
        LibraryJob.objects.filter(pk=fresh.pk).update(created_at=now-timedelta(seconds=control.API_QUEUE_SECONDS-1))
        LibraryJob.objects.filter(pk__in=[waiting_assistant.pk, claimed_assistant.pk, done.pk]).update(
            created_at=now-timedelta(days=2), updated_at=now-timedelta(days=2))
        with mock.patch.object(control.timezone, "now", return_value=now):
            self.assertEqual(control.expire_api_jobs(), 2)
            self.assertEqual(control.expire_api_jobs(), 0)
        for row in (queued, running):
            row.refresh_from_db(); shown = control.job_json(row)
            self.assertEqual(row.status, "failed"); self.assertEqual(row.error, control.TIMEOUT_MESSAGE)
            self.assertTrue(shown["timed_out"]); self.assertIsNone(shown["timeout_at"])
        for row, status in [(fresh, "queued"), (waiting_assistant, "queued"), (claimed_assistant, "running"), (done, "done")]:
            row.refresh_from_db(); self.assertEqual(row.status, status)
        self.assertEqual(done.result, {"answer": "saved"})

    def test_queued_assistant_does_not_claim_a_start_or_timeout(self):
        waiting = self.job()
        shown = control.job_json(waiting)
        self.assertIsNone(shown["started_at"]); self.assertIsNone(shown["timeout_at"])
        self.assertFalse(shown["cancelled"]); self.assertFalse(shown["timed_out"])
        api = self.job(executor="api")
        self.assertEqual(control.deadline(api), api.created_at+timedelta(seconds=control.API_QUEUE_SECONDS))
        self.assertIsNone(control.job_json(api)["started_at"])

    def test_cancel_is_atomic_for_unknown_id_or_wrong_scope(self):
        selected = self.job(); ordinary = self.job(scoped=False)
        for payload in ({"ids": [str(selected.pk), str(uuid.uuid4())], "solution_scope": True},
                        {"ids": [str(selected.pk), str(ordinary.pk)], "solution_scope": True}):
            with self.assertRaises(control.ControlError): control.cancel_jobs(payload)
            selected.refresh_from_db(); self.assertEqual(selected.status, "queued")

    def test_cancel_requires_exact_ids_scope_and_preserves_done_results(self):
        active = self.job(); finished = self.job(status="done", result={"answer": "prior"})
        for payload in ({}, {"ids": [], "solution_scope": True}, {"ids": [str(active.pk)], "solution_scope": 1},
                        {"ids": ["invalid"], "solution_scope": True}, {"ids": [str(active.pk)], "solution_scope": True, "all": True}):
            with self.assertRaises(control.ControlError): control.cancel_jobs(payload)
        result = control.cancel_jobs({"ids": [str(active.pk), str(active.pk), str(finished.pk)], "solution_scope": True})
        self.assertEqual(result["cancelled"], 1); self.assertEqual(result["assistant_handoff"]["job_ids"], [])
        active.refresh_from_db(); self.assertTrue(control.job_json(active)["cancelled"])
        finished.refresh_from_db(); self.assertEqual(finished.result, {"answer": "prior"})
        self.assertEqual(control.cancel_jobs({"ids": [str(active.pk)], "solution_scope": True})["cancelled"], 0)

    def test_cancelled_api_late_success_cannot_write_or_replace_terminal_state(self):
        job = self.job(executor="api")
        def late(_publication, **_kwargs):
            control.cancel_jobs({"ids": [str(job.pk)], "solution_scope": True})
            return {"answer": "late", "analysis": "should not save"}
        self.run_api(late)
        job.refresh_from_db(); self.pub.refresh_from_db()
        self.assertEqual(job.error, control.CANCEL_MESSAGE); self.assertEqual(job.status, "failed")
        self.assertEqual(job.result, {}); self.assertEqual(self.pub.extras, {})

    def test_timed_out_api_late_success_cannot_write_or_replace_timeout(self):
        job = self.job(executor="api")
        def late(_publication, **_kwargs):
            LibraryJob.objects.filter(pk=job.pk).update(updated_at=timezone.now()-timedelta(seconds=control.API_RUNNING_SECONDS+1))
            return {"answer": "late", "analysis": "should not save"}
        self.run_api(late); job.refresh_from_db()
        self.assertEqual(job.error, control.TIMEOUT_MESSAGE); self.assertEqual(job.result, {})

    def test_changed_fingerprint_before_run_never_calls_generator(self):
        job = self.job(executor="api", fingerprint="0"*64)
        run = self.run_api(lambda *_args, **_kwargs: self.fail("stale task cannot call generator"))
        run.assert_not_called(); job.refresh_from_db()
        self.assertEqual(job.status, "failed"); self.assertEqual(job.result, {})

    def test_changed_publication_during_api_call_drops_late_result(self):
        job = self.job(executor="api")
        def late(_publication, **_kwargs):
            PublishedQuestion.objects.filter(pk=self.pub.pk).update(content={**self.pub.content, "stem": "changed"})
            return {"answer": "old task"}
        self.run_api(late); job.refresh_from_db()
        self.assertEqual(job.status, "failed"); self.assertEqual(job.result, {})

    def test_finish_never_changes_cancelled_or_expired_jobs(self):
        for error in (control.CANCEL_MESSAGE, control.TIMEOUT_MESSAGE):
            job = self.job(status="running")
            LibraryJob.objects.filter(pk=job.pk).update(status="failed", error=error)
            library_jobs._finish(job, "done")
            library_jobs._finish(job, "failed", "late failure")
            job.refresh_from_db(); self.assertEqual(job.status, "failed"); self.assertEqual(job.error, error)

    def test_recovery_requeues_only_unexpired_api_running_work(self):
        old = self.job(executor="api", status="running"); fresh = self.job(executor="api", status="running")
        assistant_job = self.job(status="running")
        LibraryJob.objects.filter(pk=old.pk).update(updated_at=timezone.now()-timedelta(seconds=control.API_RUNNING_SECONDS+1))
        self.assertEqual(library_jobs.recover_interrupted(), 1)
        for row, status in [(old, "failed"), (fresh, "queued"), (assistant_job, "running")]:
            row.refresh_from_db(); self.assertEqual(row.status, status)

    def test_scoped_single_question_handoff_does_not_enable_globals_or_other_jobs(self):
        other = self.publication(2)
        prior_content = deepcopy(self.pub.content); prior_extras = deepcopy(self.pub.extras)
        switches = features.load(); settings = service.public_status()
        response = self.post("/api/library/jobs", {"kind": "answer", "ids": [str(self.pub.pk)], "solution_scope": True})
        self.assertEqual(response.status_code, 200, response.content)
        task = response.json()["jobs"][0]
        self.assertEqual(task["status"], "queued"); self.assertIsNone(task["started_at"])
        self.assertEqual(response.json()["assistant_handoff"]["publication_ids"], [str(self.pub.pk)])
        self.assertEqual(LibraryJob.objects.count(), 1); self.assertFalse(other.jobs.exists())
        job = LibraryJob.objects.get(); claimed = self.claim(job)
        job.refresh_from_db(); self.assertEqual(job.status, "running")
        prompt = claimed["jobs"][0]["prompt"]
        self.assertIn("(1)", prompt); self.assertIn("(2)", prompt); self.assertIn("完整推导", prompt)
        self.submit(job); job.refresh_from_db(); self.pub.refresh_from_db()
        self.assertEqual(job.status, "done"); self.assertIn("(2)", job.result["analysis"])
        self.assertEqual(self.pub.content, prior_content); self.assertEqual(self.pub.extras, prior_extras)
        self.assertFalse(LibrarySolution.objects.exists()); self.assertEqual(features.load(), switches)
        self.assertEqual(service.public_status(), settings); self.network.assert_not_called()

    def test_explicit_cancelled_or_completed_job_cannot_create_replacement(self):
        for status in ("failed", "done"):
            job = self.job(status=status)
            before = LibraryJob.objects.count()
            with self.assertRaisesRegex(assistant.AssistantError, "未新建"): self.claim(job)
            self.assertEqual(LibraryJob.objects.count(), before)

    def test_exact_job_id_must_match_kind_publication_executor_and_fingerprint(self):
        other = self.publication(2)
        cases = [self.job(publication=other), self.job(executor="api"), self.job(fingerprint="0"*64)]
        wrong_kind = self.job(); LibraryJob.objects.filter(pk=wrong_kind.pk).update(kind="tags"); cases.append(wrong_kind)
        for job in cases:
            before = LibraryJob.objects.count()
            with self.assertRaises(assistant.AssistantError):
                assistant.prepare({"publication_id": str(self.pub.pk), "kinds": ["answer"], "job_id": str(job.pk), "agent": "审计助手"})
            self.assertEqual(LibraryJob.objects.count(), before)

    def test_agent_and_fingerprint_bind_claim_and_submission(self):
        job = self.job(); self.claim(job); job.refresh_from_db()
        with self.assertRaisesRegex(assistant.AssistantError, "其他助手"): self.claim(job, agent="其他助手")
        with self.assertRaises(assistant.AssistantError): self.submit(job, agent="其他助手")
        with self.assertRaises(assistant.AssistantError): self.submit(job, fingerprint="0"*64)
        job.refresh_from_db(); self.assertEqual(job.status, "running"); self.assertEqual(job.result, {})

    def test_cancelled_assistant_submission_is_rejected_without_new_jobs(self):
        job = self.job(); self.claim(job); job.refresh_from_db()
        control.cancel_jobs({"ids": [str(job.pk)], "solution_scope": True})
        with self.assertRaises(assistant.AssistantError): self.submit(job)
        job.refresh_from_db(); self.assertEqual(job.error, control.CANCEL_MESSAGE)
        self.assertEqual(job.result, {}); self.assertEqual(LibraryJob.objects.count(), 1)

    def test_cancel_during_image_validation_is_rechecked_before_submission_write(self):
        job = self.job(); self.claim(job); job.refresh_from_db()
        original = assistant._images
        def cancelled(publication):
            images = original(publication)
            control.cancel_jobs({"ids": [str(job.pk)], "solution_scope": True})
            return images
        with mock.patch.object(assistant, "_images", side_effect=cancelled), self.assertRaises(assistant.AssistantError):
            self.submit(job)
        # This same-connection injection is nested in complete's transaction;
        # rejecting complete rolls the injected cancellation back as well.
        # The separately committed cancellation case above checks its terminal
        # message; here the invariant is that a stale local job never writes.
        job.refresh_from_db(); self.assertNotEqual(job.status, "done"); self.assertEqual(job.result, {})

    def test_handoff_uses_only_active_assistant_job_ids_and_all_subquestions(self):
        active = self.job(); done = self.job(status="done"); api = self.job(executor="api")
        with mock.patch.object(control, "_cli_command", return_value="tiyouju"):
            handoff = control.assistant_handoff([active, done, api])
        self.assertEqual(handoff["job_ids"], [str(active.pk)])
        self.assertIn(f"--job-id '{active.pk}'", handoff["text"])
        self.assertIn("每个小问", handoff["text"]); self.assertIn("只处理这些任务", handoff["text"])
        self.assertIn("人工保存前不进入导出", handoff["text"])

    def test_read_only_job_polling_does_not_claim_or_create_assistant_tasks(self):
        scoped = self.job(); ordinary = self.job(scoped=False)
        response = self.client.get("/api/library/jobs", {"ids": str(self.pub.pk), "solution_scope": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.json()["jobs"]], [str(scoped.pk)])
        self.assertIsNone(response.json()["jobs"][0]["started_at"])
        scoped.refresh_from_db(); ordinary.refresh_from_db()
        self.assertEqual(scoped.status, "queued"); self.assertEqual(ordinary.status, "queued")
        self.assertEqual(LibraryJob.objects.count(), 2)
