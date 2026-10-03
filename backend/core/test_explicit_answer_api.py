"""Offline contract tests for an explicitly selected, API-only solution batch.

Use the real settings, queue, HTTP view and worker code. Only encryption and
the HTTP transport are synthetic; no real credential or cloud is consulted.
"""

from copy import deepcopy
import json
import os
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import features, library, library_ai_settings as service, library_job_control, library_jobs, library_solutions
from .models import LibraryJob, LibrarySolution, PublishedQuestion, Question
from .test_library_ai_settings import answer_response, protected_test_bytes
from .test_v110_types_origin import TempDataMixin


class ExplicitAnswerAPITests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "ai-settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "ai-key.dat"),
            "QB_FEATURES_FILE": str(self.temp / "features.json"),
            "QB_CREDENTIAL_FILE": str(self.temp / "ocr-key.dat"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        transform = mock.patch.object(service.credential_settings.store, "_transform", side_effect=protected_test_bytes)
        transform.start()
        self.addCleanup(transform.stop)
        transport = mock.patch.object(service.requests, "post", side_effect=AssertionError("unexpected cloud request"))
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        features.save({"ai_answer": False, "knowledge_tags": False})
        self.paper = self.make_paper()
        self.pub = self.publication(1)
        self.unselected = self.publication(2)
        self.configure()
        self.verify()
        service.save({"mode": "assistant"})
        self.assertTrue(service.public_status()["api_ready"])
        self.assertEqual(service.public_status()["mode"], "assistant")

    def publication(self, number):
        question = self.card(self.paper, number=number, question_type="free_response", stem=f"离线第 {number} 题，计算 $1+1$。")
        question.approved = True
        question.approved_at = timezone.now()
        question.approved_content_hash = library.approval_hash(question)
        question.save()
        return library.publish(question)[0]

    def configure(self, **extra):
        return service.save({"mode": "api", "endpoint_id": "ep-offline-explicit", "supports_images": True,
                             "thinking": True, "key": {"action": "replace", "value": "synthetic-explicit-api-key"}, **extra})

    def verify(self):
        service.save({"mode": "api"})
        self.transport.side_effect = None
        self.transport.return_value = answer_response()
        self.assertTrue(service.test_connection({"confirm": True})["api_ready"])
        self.transport.reset_mock()
        self.transport.side_effect = AssertionError("unexpected cloud request")

    def post(self, **changes):
        return self.client.post("/api/library/jobs", json.dumps({"kind": "answer", "ids": [str(self.pub.pk)],
                                "solution_scope": True, "executor": "api", **changes}),
                                content_type="application/json", HTTP_X_QB_REQUEST="1")

    def queue(self):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["executor"], "api")
        row = response.json()["jobs"][0]
        self.assertEqual(row["publication_id"], str(self.pub.pk))
        self.assertEqual(row["executor"], "api")
        self.assertTrue(row["solution_scope"])
        return LibraryJob.objects.get(pk=row["id"])

    def config_bytes(self):
        return {str(path.resolve().relative_to(self.temp.resolve())): path.read_bytes() if path.exists() else None
                for path in (service.path(), service.key_path(), features.path(), self.temp / "ocr-key.dat")}

    def process(self, result_mutation=None):
        def reply(*_args, **_kwargs):
            if result_mutation:
                result_mutation()
            return answer_response("【答案】2【解析】逐步推导的离线 API 初稿。")
        self.transport.side_effect = reply
        return library_jobs.process_pending()

    def assert_no_result(self, job):
        job.refresh_from_db()
        self.pub.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertFalse(job.result)
        self.assertNotIn("ai_answer", self.pub.extras or {})
        self.assertEqual(LibrarySolution.objects.count(), 0)

    def test_selected_api_runs_under_assistant_mode_without_enabling_any_global_feature(self):
        original = deepcopy(self.pub.content), deepcopy(self.pub.extras), self.pub.content_hash
        questions = list(Question.all_objects.order_by("pk").values())
        settings = self.config_bytes()
        job = self.queue()
        self.assertTrue(job.api_snapshot["explicit_api"])
        self.assertEqual(job.api_snapshot["mode"], "api")
        self.assertEqual(LibraryJob.objects.count(), 1)
        self.assertFalse(self.unselected.jobs.exists())
        self.assertEqual(self.process(), 1)
        self.transport.assert_called_once()
        request = self.transport.call_args.kwargs
        self.assertIn(self.pub.content["stem"], request["json"]["messages"][0]["content"][0]["text"])
        self.assertNotIn(self.unselected.content["stem"], json.dumps(request["json"], ensure_ascii=False))
        self.assertFalse(request["allow_redirects"])
        self.assertEqual(request["json"]["thinking"], {"type": "enabled"})
        job.refresh_from_db()
        self.pub.refresh_from_db()
        self.unselected.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["answer"], "2")
        self.assertIn("逐步推导", job.result["analysis"])
        self.assertEqual(job.result["publication_id"], str(self.pub.pk))
        self.assertEqual(job.result["fingerprint"], library.generation_fingerprint(self.pub.content, self.pub.pk))
        self.assertFalse(job.result["checked"])
        self.assertEqual((self.pub.content, self.pub.extras, self.pub.content_hash), original)
        self.assertFalse(self.unselected.extras)
        self.assertEqual(LibrarySolution.objects.count(), 0, "Generating a draft is not saving an exportable solution")
        self.assertEqual(list(Question.all_objects.order_by("pk").values()), questions)
        self.assertEqual(self.config_bytes(), settings)
        self.assertEqual(service.public_status()["mode"], "assistant")
        self.assertEqual(service.public_status()["features"], {"ai_answer": False, "knowledge_tags": False})

    def test_unconfigured_or_unverified_api_is_rejected_without_assistant_fallback(self):
        for change in ({"key": {"action": "clear"}}, {"endpoint_id": "ep-changed-unverified"}):
            with self.subTest(change=change):
                self.configure()
                self.verify()
                service.save({"mode": "assistant"})
                service.save(change)
                before = self.config_bytes()
                response = self.post()
                self.assertEqual(response.status_code, 409)
                self.assertFalse(LibraryJob.objects.exists())
                self.assertEqual(self.config_bytes(), before)
        self.transport.assert_not_called()

    def test_generation_preserves_saved_manual_solution_and_existing_ai_history(self):
        saved = library_solutions.save(self.pub, {"answer": "人工答案", "analysis": "已保存的完整解析", "figures": [],
                                                 "base_revision": None, "sync_library": True})
        self.pub.refresh_from_db()
        library.save_extras(self.pub, {**self.pub.extras, "ai_answer": {"answer": "旧 AI 答案", "analysis": "旧 AI 解析",
            "fingerprint": library.generation_fingerprint(self.pub.content, self.pub.pk), "checked": False}})
        self.pub.refresh_from_db()
        before = deepcopy(self.pub.extras)
        job = self.queue()
        self.process()
        job.refresh_from_db()
        self.pub.refresh_from_db()
        saved.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["answer"], "2")
        self.assertEqual(self.pub.extras, before)
        self.assertEqual(saved.answer, "人工答案")
        self.assertEqual(saved.analysis, "已保存的完整解析")
        self.assertEqual(LibrarySolution.objects.count(), 1)

    def test_executor_override_is_not_available_to_tags_or_unscoped_answers(self):
        for payload in ({"kind": "tags"}, {"kind": "tags", "solution_scope": False},
                        {"solution_scope": False}, {"executor": "assistant"}, {"executor": None}, {"missing": True}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(**payload).status_code, 400)
        with self.assertRaises(library_jobs.JobError):
            library_jobs.enqueue(self.pub, "tags", executor="api")
        self.assertFalse(LibraryJob.objects.exists())
        self.transport.assert_not_called()

    def test_identical_active_api_binding_is_reused(self):
        first = self.queue()
        second = self.queue()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(LibraryJob.objects.count(), 1)
        self.transport.assert_not_called()

    def test_prior_assistant_wait_does_not_consume_an_explicit_api_request(self):
        legacy = library_jobs.enqueue(self.pub, "answer", solution_scope=True)
        self.assertEqual(legacy.executor, "assistant")
        explicit = self.queue()
        self.assertNotEqual(explicit.pk, legacy.pk)
        self.assertEqual(self.process(), 1)
        legacy.refresh_from_db()
        explicit.refresh_from_db()
        self.assertEqual(legacy.status, "queued")
        self.assertFalse(legacy.result)
        self.assertEqual(explicit.status, "done")
        self.transport.assert_called_once()

    def test_stale_api_snapshot_does_not_swallow_a_new_request_after_reconfiguration(self):
        old = self.queue()
        service.save({"endpoint_id": "ep-offline-new-model"})
        self.verify()
        service.save({"mode": "assistant"})
        fresh = self.queue()
        duplicate = self.queue()
        self.assertNotEqual(old.pk, fresh.pk)
        self.assertEqual(fresh.pk, duplicate.pk)
        self.assertEqual(LibraryJob.objects.count(), 2)
        self.assertEqual(self.process(), 2)
        old.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(old.status, "failed")
        self.assertFalse(old.result)
        self.assertEqual(fresh.status, "done")
        self.transport.assert_called_once()
        self.assertEqual(self.transport.call_args.kwargs["json"]["model"], "ep-offline-new-model")

    def test_change_before_worker_launch_is_rejected_before_any_request(self):
        job = self.queue()
        service.save({"endpoint_id": "ep-stale-before-start"})
        self.assertEqual(library_jobs.process_pending(), 1)
        self.assert_no_result(job)
        self.transport.assert_not_called()

    def test_key_clear_before_worker_launch_does_not_call_api_or_fallback(self):
        job = self.queue()
        service.save({"key": {"action": "clear"}})
        self.assertEqual(library_jobs.process_pending(), 1)
        self.assert_no_result(job)
        self.transport.assert_not_called()

    def test_configuration_changed_during_response_discards_the_late_draft(self):
        job = self.queue()
        self.process(lambda: service.save({"endpoint_id": "ep-new-during-response"}))
        self.assert_no_result(job)
        self.transport.assert_called_once()

    def test_key_replaced_during_response_discards_the_late_draft(self):
        job = self.queue()
        self.process(lambda: service.save({"key": {"action": "replace", "value": "synthetic-replacement-key"}}))
        self.assert_no_result(job)
        self.transport.assert_called_once()

    def test_verification_withdrawn_during_response_discards_the_late_draft(self):
        job = self.queue()
        def revoke():
            service._write_settings({**service._load(), "verified": False, "verified_at": "", "resolved_model": ""})
        self.process(revoke)
        self.assert_no_result(job)
        self.transport.assert_called_once()

    def test_withdrawn_publication_cannot_receive_the_late_api_result(self):
        job = self.queue()
        self.process(lambda: PublishedQuestion.objects.filter(pk=self.pub.pk).update(status=PublishedQuestion.Status.WITHDRAWN))
        self.assert_no_result(job)
        self.assertEqual(self.pub.status, "withdrawn")
        self.transport.assert_called_once()

    def test_cancelled_explicit_job_keeps_its_terminal_state_after_late_response(self):
        job = self.queue()
        self.process(lambda: library_job_control.cancel_jobs({"ids": [str(job.pk)], "solution_scope": True}))
        self.assert_no_result(job)
        self.assertTrue(library_job_control.job_json(job)["cancelled"])
        self.transport.assert_called_once()

    def normal_post(self, kind="answer", **changes):
        return self.client.post("/api/library/jobs", json.dumps({"kind": kind, "ids": [str(self.pub.pk)], **changes}),
                                content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_normal_api_only_rejects_mode_change_between_ready_get_and_post(self):
        features.save({"ai_answer": True, "knowledge_tags": True})
        service.save({"mode": "api"})
        checked = self.client.get("/api/settings/library-ai").json()
        self.assertEqual(checked["mode"], "api")
        self.assertTrue(checked["api_ready"])
        # A second settings window/CLI changes execution after the UI checked.
        service.save({"mode": "assistant"})
        settings = self.config_bytes()
        questions = list(Question.all_objects.order_by("pk").values())
        publications = list(PublishedQuestion.objects.order_by("pk").values())
        for kind in ("tags", "answer"):
            with self.subTest(kind=kind):
                response = self.normal_post(kind, api_only=True)
                self.assertEqual(response.status_code, 409, response.content)
                self.assertIn("未创建助手任务", response.json()["error"])
                self.assertFalse(LibraryJob.objects.exists())
        self.assertEqual(self.config_bytes(), settings)
        self.assertEqual(list(Question.all_objects.order_by("pk").values()), questions)
        self.assertEqual(list(PublishedQuestion.objects.order_by("pk").values()), publications)
        # Losing verification also refuses; it never queues an assistant as a fallback.
        service.save({"mode": "api", "endpoint_id": "ep-unverified-after-ui-get"})
        self.assertFalse(service.public_status()["api_ready"])
        self.assertEqual(self.normal_post("tags", api_only=True).status_code, 409)
        self.assertFalse(LibraryJob.objects.exists())
        self.transport.assert_not_called()

    def test_legacy_normal_queue_without_api_only_keeps_assistant_compatibility(self):
        features.save({"ai_answer": True, "knowledge_tags": True})
        settings = self.config_bytes()
        for flag in ({}, {"api_only": False}):
            for kind in ("tags", "answer"):
                with self.subTest(flag=flag, kind=kind):
                    response = self.normal_post(kind, **flag)
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertEqual(response.json()["executor"], "assistant")
                    job = LibraryJob.objects.get(pk=response.json()["jobs"][0]["id"])
                    self.assertEqual(job.executor, "assistant")
                    self.assertFalse(job.solution_scope)
                    self.assertEqual(job.api_snapshot, {})
        self.assertEqual(LibraryJob.objects.count(), 2, "The false/omitted compatibility calls reuse existing identical waits")
        self.assertEqual(self.config_bytes(), settings)
        self.transport.assert_not_called()

    def test_normal_api_only_queues_api_tags_and_answers_without_bypassing_features(self):
        service.save({"mode": "api"})
        self.assertTrue(service.public_status()["api_ready"])
        questions = list(Question.all_objects.order_by("pk").values())
        publications = list(PublishedQuestion.objects.order_by("pk").values())
        timing = deepcopy(service.public_status()["on_intake"])
        for kind, feature in (("tags", "knowledge_tags"), ("answer", "ai_answer")):
            with self.subTest(kind=kind):
                before = LibraryJob.objects.count()
                self.assertEqual(self.normal_post(kind, api_only=True).status_code, 409, "API-only must not enable a feature")
                self.assertEqual(LibraryJob.objects.count(), before)
                features.save({feature: True})
                settings = self.config_bytes()
                response = self.normal_post(kind, api_only=True)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["executor"], "api")
                job = LibraryJob.objects.get(pk=response.json()["jobs"][0]["id"])
                self.assertEqual(job.executor, "api")
                self.assertFalse(job.solution_scope, "Ordinary UI work is not an explicit solution-scope bypass")
                self.assertNotIn("explicit_api", job.api_snapshot)
                self.assertEqual(job.api_snapshot["mode"], "api")
                self.assertEqual(self.config_bytes(), settings)
        self.assertEqual(LibraryJob.objects.count(), 2)
        self.assertFalse(LibraryJob.objects.filter(executor="assistant").exists())
        self.assertEqual(service.public_status()["on_intake"], timing)
        self.assertEqual(list(Question.all_objects.order_by("pk").values()), questions)
        self.assertEqual(list(PublishedQuestion.objects.order_by("pk").values()), publications)
        self.transport.assert_not_called()

    def test_normal_api_only_flag_requires_a_boolean_without_creating_any_job(self):
        before = self.config_bytes()
        for flag in (None, 0, 1, "true", "false", {}, [], 1.0):
            with self.subTest(flag=flag):
                response = self.normal_post("tags", api_only=flag)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("api_only", response.json()["error"])
        self.assertFalse(LibraryJob.objects.exists())
        self.assertEqual(self.config_bytes(), before)
        self.transport.assert_not_called()
