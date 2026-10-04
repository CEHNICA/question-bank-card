"""Isolated assistant protocol and optional-API compatibility, with no cloud."""
import json
import os
from copy import deepcopy
from unittest import mock

from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.utils import timezone
from PIL import Image

from . import features, library, library_assistant as assistant, library_ai_settings as service, library_jobs
from .models import LibraryJob, PublishedQuestion, Question
from . import test_library_ai_settings as settings_tests
from .test_v110_types_origin import TempDataMixin


class AssistantSettingsTests(SimpleTestCase):
    """Reuse disposable DPAPI/transport fixtures, not the API test cases."""
    setUp = settings_tests.IndependentAISettingsTests.setUp
    configure = settings_tests.IndependentAISettingsTests.configure
    verify = settings_tests.IndependentAISettingsTests.verify
    def test_assistant_is_default_ready_but_both_features_and_intake_are_off(self):
        state = service.public_status()
        self.assertEqual(state["mode"], "assistant")
        self.assertTrue(state["ready"])
        self.assertFalse(state["api_ready"])
        self.assertEqual(state["on_intake"], {"tags": False, "answer": False})
        self.assertFalse(any(state["features"].values()))
        with self.assertRaises(service.ServiceError):
            service.chat("not user maths", [], kind="answer")
        self.network.assert_not_called()

    def test_assistant_enable_does_not_require_or_decrypt_a_key_or_call_cloud(self):
        state = service.save({"mode": "assistant", "features": {"knowledge_tags": True}})
        self.assertTrue(service.ensure_ready("tags")["ready"])
        self.assertFalse(state["features"]["ai_answer"])
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_old_v1_doubao_keeps_key_and_probe_but_migrates_to_assistant(self):
        self.configure()
        self.verify()
        before = service.key_path("doubao").read_bytes()
        config = service._load()
        legacy = {key: config[key] for key in ("revision", "key_revision", "key_configured", "verified", "verified_at", "resolved_model")}
        legacy.update(version=1, endpoint_id="ep-offline-pro", thinking=True)
        service._write_settings(legacy)
        self.transform.reset_mock()
        state = service.public_status()
        self.assertEqual((state["mode"], state["provider"], state["model"]), ("assistant", "doubao", "ep-offline-pro"))
        self.assertTrue(state["api_ready"])
        service.save({"mode": "assistant", "features": {"ai_answer": True}})
        self.assertEqual(service.key_path("doubao").read_bytes(), before)
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_hidden_api_configuration_survives_assistant_feature_and_timing_saves(self):
        self.configure()
        self.verify()
        before = service.key_path("doubao").read_bytes()
        state = service.save({"mode": "assistant", "features": {"ai_answer": True}, "on_intake": {"answer": True}})
        self.assertTrue(state["api_ready"])
        self.assertEqual(state["model"], "ep-offline-pro")
        self.assertEqual(state["on_intake"], {"tags": False, "answer": True})
        service.save({"features": {"ai_answer": False}})
        self.assertTrue(service.public_status()["on_intake"]["answer"])
        self.assertEqual(service.key_path("doubao").read_bytes(), before)
        self.network.assert_not_called()

    def test_switching_provider_never_reuses_another_providers_key(self):
        self.configure()
        old = service.key_path("doubao").read_bytes()
        state = service.save({"provider": "deepseek", "key": {"action": "keep"}})
        self.assertFalse(state["key_configured"])
        self.assertFalse(state["configured"])
        self.assertEqual(state["model"], "deepseek-v4-pro")
        self.assertFalse(state["supports_images"])
        self.assertEqual(service.key_path("doubao").read_bytes(), old)
        state = service.save({"provider": "deepseek", "key": {"action": "replace", "value": "fake-deepseek-key"}})
        self.assertTrue(state["key_configured"])
        self.assertEqual(service._key(service._load()), "fake-deepseek-key")
        service.save({"provider": "doubao", "model": "ep-offline-pro"})
        self.assertEqual(service._key(service._load()), "offline-key-never-print")
        self.network.assert_not_called()

    def test_custom_models_can_be_text_only_and_choose_thinking_explicitly(self):
        service.save({"mode": "api", "provider": "custom", "base_url": "http://127.0.0.1:9999/v1",
                      "model": "offline-model", "thinking": False, "supports_images": False,
                      "key": {"action": "replace", "value": "synthetic-key"}})
        self.network.side_effect = None
        self.network.return_value = settings_tests.answer_response("【答案】2", model="offline-model", thinking=False)
        state = service.test_connection({"confirm": True})
        self.assertTrue(state["api_ready"])
        args, kwargs = self.network.call_args
        self.assertEqual(args[0], "http://127.0.0.1:9999/v1/chat/completions")
        self.assertNotIn("thinking", kwargs["json"])
        self.assertEqual(len(kwargs["json"]["messages"][0]["content"]), 1)

    def test_deepseek_recommendation_does_not_claim_image_capability(self):
        service.save({"mode": "api", "provider": "deepseek", "features": {"ai_answer": True},
                      "key": {"action": "replace", "value": "synthetic-key"}})
        self.network.side_effect = None
        self.network.return_value = settings_tests.answer_response("【答案】2", model="deepseek-v4-pro")
        service.test_connection({"confirm": True})
        self.assertEqual(self.network.call_args.kwargs["json"]["reasoning_effort"], "high")
        self.network.reset_mock()
        with self.assertRaisesRegex(service.ServiceError, "未声明支持图片"):
            service.chat("synthetic maths", ["data:image/png;base64,fake"], kind="answer")
        self.network.assert_not_called()

    def test_invalid_mode_provider_timing_and_url_leave_previous_settings_unchanged(self):
        service.save({"features": {"ai_answer": True}})
        before = service.path().read_bytes()
        for payload in ({"mode": "desktop"}, {"provider": []}, {"on_intake": {"answer": 1}},
                        {"on_intake": {"other": True}}, {"provider": "custom", "base_url": "https://[invalid"},
                        {"provider": "custom", "base_url": "https://user:secret@example.invalid"},
                        {"provider": "custom", "base_url": "http://example.invalid"}, {"key": {"action": []}}):
            with self.subTest(payload=payload):
                with self.assertRaises(service.SettingsError):
                    service.save(payload)
                self.assertEqual(service.path().read_bytes(), before)
        self.network.assert_not_called()


class AssistantTaskTests(TempDataMixin, TransactionTestCase):
    def setUp(self):
        self.use_temp_data()
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "key.dat"),
            "QB_FEATURES_FILE": str(self.temp / "features.json"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.network = mock.patch.object(service.requests, "post", side_effect=AssertionError("paid API forbidden")).start()
        self.addCleanup(mock.patch.stopall)
        self.paper = self.make_paper()
        self.question = self.card(self.paper, question_type="free_response", stem="计算 $1+1$。", answer="原卷答案")
        self.approve(self.question)
        self.publication = library.publish(self.question)[0]
        service.save({"features": {"ai_answer": True, "knowledge_tags": True}})

    def approve(self, question):
        question.approved = True
        question.approved_at = timezone.now()
        question.approved_content_hash = library.approval_hash(question)
        question.save()

    def prepare(self, kinds=None, agent="离线测试助手"):
        return assistant.prepare({"publication_id": str(self.publication.pk), "kinds": kinds or ["answer"], "agent": agent})

    def result(self, job, **data):
        return {"job_id": job["id"], "fingerprint": job["fingerprint"], "agent": job["agent"], **data}

    def test_prepare_both_kinds_is_offline_bound_and_worker_never_consumes_them(self):
        original = deepcopy(self.publication.content)
        prepared = self.prepare(["tags", "answer"])
        self.assertEqual(len(prepared["jobs"]), 2)
        self.assertEqual(prepared["publication"]["content"], original)
        self.assertEqual(prepared["images"]["crop"], f"/api/library/{self.publication.pk}/crop")
        self.assertEqual(prepared["images"]["originals"], [{"page_idx": 0, "url": f"/api/library/{self.publication.pk}/pages/0"}])
        self.assertTrue(prepared["knowledge"]["points"])
        for job in prepared["jobs"]:
            self.assertEqual(job["executor"], "assistant")
            self.assertEqual(job["fingerprint"], library.generation_fingerprint(original, self.publication.pk))
            self.assertIn("原卷截图", job["prompt"])
        self.assertFalse(library_jobs.pending())
        self.assertEqual(library_jobs.process_pending(), 0)
        self.assertEqual(library_jobs.recover_interrupted(), 0)
        self.assertEqual(LibraryJob.objects.filter(status="queued").count(), 2)
        self.network.assert_not_called()

    def test_complete_adds_provenance_without_touching_original_or_review(self):
        before_q = Question.all_objects.filter(pk=self.question.pk).values().get()
        before = deepcopy(self.publication.content)
        jobs = self.prepare(["answer", "tags"])["jobs"]
        answer = assistant.complete(self.result(jobs[0], answer="2", analysis="相加得 2。"))
        self.assertEqual(answer["job"]["status"], "done")
        assistant.complete(self.result(jobs[1], tags=["基本不等式"]))
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.content, before)
        self.assertEqual(self.publication.content["answer"], "原卷答案")
        self.assertEqual(Question.all_objects.filter(pk=self.question.pk).values().get(), before_q)
        generated = self.publication.extras["ai_answer"]
        self.assertEqual((generated["answer"], generated["agent"], generated["executor"], generated["checked"]), ("2", "离线测试助手", "assistant", False))
        self.assertEqual(self.publication.extras["tags_agent"], "离线测试助手")
        self.assertFalse(self.publication.extras["tags_checked"])
        self.assertEqual(self.publication.extras["tags_publication_id"], str(self.publication.pk))
        self.network.assert_not_called()

    def test_same_assistant_reuses_queue_other_agent_and_duplicate_result_are_refused(self):
        job = self.prepare()["jobs"][0]
        self.assertEqual(self.prepare()["jobs"][0]["id"], job["id"])
        with self.assertRaisesRegex(assistant.AssistantError, "其他助手"):
            self.prepare(agent="另一个助手")
        wrong = self.result(job, answer="3") | {"agent": "另一个助手"}
        with self.assertRaises(assistant.AssistantError):
            assistant.complete(wrong)
        assistant.complete(self.result(job, answer="2"))
        with self.assertRaisesRegex(assistant.AssistantError, "重复"):
            assistant.complete(self.result(job, answer="3"))
        with self.assertRaisesRegex(assistant.AssistantError, "已有"):
            self.prepare()
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.extras["ai_answer"]["answer"], "2")

    def test_strict_result_fields_limits_and_catalogue(self):
        answer, tags = self.prepare(["answer", "tags"])["jobs"]
        for data in ({"answer": ""}, {"answer": "x" * 2001}, {"answer": "2", "analysis": "x" * 6001},
                     {"answer": "2", "extra": "data"}, {"answer": "2", "tags": ["基本不等式"]}, {"analysis": "2"}):
            with self.subTest(data_keys=list(data)), self.assertRaises(assistant.AssistantError) as error:
                assistant.complete(self.result(answer, **data))
            self.assertEqual(error.exception.status, 400)
        for data in ([], ["凭空编造"], ["基本不等式", "基本不等式"], ["基本不等式"] * 4, "基本不等式", [1]):
            with self.subTest(tags=data), self.assertRaises(assistant.AssistantError):
                assistant.complete(self.result(tags, tags=data))
        self.assertFalse(LibraryJob.objects.filter(status="done").exists())
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.extras, {})

    def test_closed_feature_blocks_prepare_submit_and_marks_inbox_paused(self):
        job = self.prepare()["jobs"][0]
        features.save({"ai_answer": False})
        with self.assertRaisesRegex(assistant.AssistantError, "启用"):
            self.prepare()
        with self.assertRaises(assistant.AssistantError):
            assistant.complete(self.result(job, answer="2"))
        tasks = assistant.list_tasks([str(self.publication.pk)])
        self.assertEqual(tasks["total"], 1)
        self.assertFalse(tasks["tasks"][0]["enabled"])
        self.assertEqual(library_jobs.process_pending(), 0)

    def test_withdrawn_and_replaced_versions_reject_old_submission(self):
        job = self.prepare()["jobs"][0]
        self.question.stem = "计算 $1+2$。"
        self.approve(self.question)
        newer = library.publish(self.question)[0]
        with self.assertRaisesRegex(assistant.AssistantError, "替代"):
            assistant.complete(self.result(job, answer="2"))
        newer.refresh_from_db()
        self.assertNotIn("ai_answer", newer.extras)
        self.assertTrue(assistant.list_tasks()["tasks"][0]["stale"])
        self.publication = newer
        job = self.prepare()["jobs"][0]
        library.withdraw(newer)
        with self.assertRaises(assistant.AssistantError):
            assistant.complete(self.result(job, answer="3"))

    def test_changed_unpublished_original_and_same_publication_text_are_rejected(self):
        job = self.prepare()["jobs"][0]
        self.question.stem = "未入库的新题面"
        self.question.save(update_fields=["stem"])
        with self.assertRaisesRegex(assistant.AssistantError, "原始题卡"):
            assistant.complete(self.result(job, answer="2"))
        self.question.stem = self.publication.content["stem"]
        self.question.save(update_fields=["stem"])
        changed = self.publication.content | {"stem": "直接变了的快照"}
        PublishedQuestion.objects.filter(pk=self.publication.pk).update(content=changed)
        with self.assertRaisesRegex(assistant.AssistantError, "已变化"):
            assistant.complete(self.result(job, answer="2"))

    def add_figure(self):
        target = self.temp / "library" / str(self.publication.pk) / "figure-1.png"
        Image.new("RGB", (20, 20), "white").save(target)
        self.publication.content["figures"] = [{"file": target.name, "slot": "stem", "page_idx": 0, "bbox": [0, 0, 10, 10]}]
        self.publication.save(update_fields=["content"])
        return target

    def test_image_pixels_changed_without_content_json_change_are_rejected(self):
        target = self.add_figure()
        job = self.prepare()["jobs"][0]
        Image.new("RGB", (20, 20), "black").save(target)
        with self.assertRaisesRegex(assistant.AssistantError, "配图"):
            assistant.complete(self.result(job, answer="2"))
        self.assertTrue(assistant.list_tasks()["tasks"][0]["stale"])

    def test_missing_figure_cannot_be_silently_omitted(self):
        self.add_figure().unlink()
        with self.assertRaisesRegex(assistant.AssistantError, "配图缺失"):
            self.prepare()
        self.assertFalse(LibraryJob.objects.exists())

    def test_closed_during_image_validation_blocks_writeback(self):
        job = self.prepare()["jobs"][0]
        real_images = assistant._images
        def closed(publication):
            result = real_images(publication)
            features.save({"ai_answer": False})
            return result
        with mock.patch.object(assistant, "_images", side_effect=closed), self.assertRaises(assistant.AssistantError):
            assistant.complete(self.result(job, answer="2"))
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.extras, {})

    def test_mode_change_does_not_turn_assistant_job_into_an_api_request(self):
        job = self.prepare()["jobs"][0]
        service.save({"mode": "api"})
        with self.assertRaisesRegex(assistant.AssistantError, "API"):
            assistant.complete(self.result(job, answer="2"))
        self.assertEqual(library_jobs.process_pending(), 0)
        self.network.assert_not_called()

    def test_inbox_limits_ids_and_uuid_format(self):
        self.prepare()
        for kwargs in ({"limit": 0}, {"limit": 51}, {"limit": True}, {"ids": "invalid"}, {"ids": ["invalid"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(assistant.AssistantError) as error:
                assistant.list_tasks(**kwargs)
            self.assertEqual(error.exception.status, 400)
        self.assertEqual(assistant.list_tasks(ids=[])["total"], 0)

    def test_original_crop_uses_saved_publication_regions_after_draft_move(self):
        before = assistant.crop_png(self.publication.pk)
        self.question.regions = [{"page_idx": 0, "bbox": [0, 0, 10, 10]}]
        self.question.save(update_fields=["regions"])
        self.assertEqual(assistant.crop_png(self.publication.pk), before)
        self.assertTrue(before.startswith(b"\x89PNG"))

    def test_real_urls_allow_offline_prepare_submit_then_show_unchecked_answer(self):
        prepared = self.client.post("/api/library/assistant/prepare", data=json.dumps({
            "publication_id": str(self.publication.pk), "kinds": ["answer"], "agent": "离线工具"}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(prepared.status_code, 200, prepared.content)
        job = prepared.json()["jobs"][0]
        response = self.client.post("/api/library/assistant/complete", data=json.dumps(self.result(job, answer="2")),
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200, response.content)
        shown = self.client.get(f"/api/library/{self.publication.pk}").json()["publication"]
        self.assertEqual(shown["ai_answer"]["answer"], "2")
        self.assertFalse(shown["ai_answer"]["checked"])
        self.network.assert_not_called()

    def fresh(self, number=3, answer=""):
        question = self.card(self.paper, number=number, question_type="free_response", stem="合成新题", answer=answer)
        self.approve(question)
        return question, library.publish(question)[0]

    def test_intake_default_off_then_feature_and_timing_queue_only_selected_kind(self):
        self.fresh()
        self.assertEqual(LibraryJob.objects.count(), 0)
        service.save({"on_intake": {"tags": True, "answer": True}, "features": {"knowledge_tags": False}})
        question, publication = self.fresh(4)
        jobs = publication.jobs.all()
        self.assertEqual([(job.kind, job.executor, job.status) for job in jobs], [("answer", "assistant", "queued")])
        self.assertEqual(publication.extras, {})
        self.assertTrue(question.approved)
        library.publish(question)
        self.assertEqual(publication.jobs.count(), 1)
        self.network.assert_not_called()

    def test_intake_original_answer_skips_ai_answer_and_api_absence_does_not_undo_publish(self):
        service.save({"on_intake": {"tags": True, "answer": True}})
        _question, publication = self.fresh(3, answer="原卷已有解答")
        self.assertEqual(list(publication.jobs.values_list("kind", flat=True)), ["tags"])
        service.save({"mode": "api"})
        _question, publication = self.fresh(4)
        self.assertEqual(publication.status, "published")
        self.assertEqual(list(publication.jobs.values_list("status", flat=True)), ["failed", "failed"])
        self.assertEqual(publication.extras, {})
        self.network.assert_not_called()

    def test_legacy_api_jobs_never_become_assistant_tasks_or_call_cloud(self):
        LibraryJob.objects.create(publication=self.publication, kind="answer")
        self.assertEqual(assistant.list_tasks()["total"], 0)
        self.assertEqual(library_jobs.process_pending(), 1)
        job = LibraryJob.objects.get()
        self.assertEqual((job.executor, job.status), ("api", "failed"))
        self.assertIn("重新排队", job.error)
        self.network.assert_not_called()
