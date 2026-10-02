"""Offline tests for independent DPAPI settings, explicit probes and binding."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import mock, skipUnless

import requests
from django.test import Client, SimpleTestCase, TestCase
from django.utils import timezone
from PIL import Image

from . import features, library, library_ai_settings as service, library_jobs, readers
from .models import LibraryJob, PublishedQuestion, Question
from .test_v110_types_origin import TempDataMixin

WINDOWS_DPAPI_TRANSFORM = service.credential_settings.store._transform


def protected_test_bytes(data, *, protect):
    # A fake encryption boundary for cross-platform offline tests. The real
    # implementation is credential_store._transform (Windows DPAPI), not this.
    return b"test-dpapi:" + base64.b64encode(data) if protect else base64.b64decode(data.removeprefix(b"test-dpapi:"))


def answer_response(text="【答案】2【图示】2", *, model="doubao-offline-pro", thinking=True, status=200):
    response = mock.Mock(status_code=status)
    response.json.return_value = {"model": model, "choices": [{"finish_reason": "stop", "message": {
        "content": text, "reasoning_content": "offline reasoning" if thinking else ""}}]}
    return response


class IndependentAISettingsTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.root / "settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.root / "library-ai.dat"),
            "QB_FEATURES_FILE": str(self.root / "features.json"),
            "QB_CREDENTIAL_FILE": str(self.root / "ocr-credentials.dat"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        transform = mock.patch.object(service.credential_settings.store, "_transform", side_effect=protected_test_bytes)
        self.transform = transform.start()
        self.addCleanup(transform.stop)
        network = mock.patch.object(service.requests, "post", side_effect=AssertionError("unexpected paid request"))
        self.network = network.start()
        self.addCleanup(network.stop)

    def configure(self, **extra):
        payload = {"endpoint_id": "ep-offline-pro", "thinking": True,
                   "key": {"action": "replace", "value": "offline-key-never-print"}}
        payload.update(extra)
        return service.save(payload)

    def verify(self):
        self.network.side_effect = None
        self.network.return_value = answer_response()
        result = service.test_connection({"confirm": True})
        self.network.reset_mock()
        return result

    def test_default_features_off_and_save_never_calls_cloud_or_ocr_credentials(self):
        ocr = self.root / "ocr-credentials.dat"
        ocr.write_bytes(b"preexisting opaque OCR credentials")
        self.assertEqual(service.public_status()["features"], {"ai_answer": False, "knowledge_tags": False})
        result = self.configure(features={"knowledge_tags": True})
        self.assertTrue(result["configured"])
        self.assertFalse(result["ready"])
        self.assertFalse(result["features"]["ai_answer"])
        self.assertEqual(ocr.read_bytes(), b"preexisting opaque OCR credentials")
        self.assertNotIn("offline-key", service.key_path().read_text())
        self.assertNotIn("offline-key", json.dumps(result) + service.path().read_text())
        self.network.assert_not_called()

    def test_status_get_does_not_decrypt_or_expose_key(self):
        self.configure()
        self.transform.reset_mock()
        result = service.public_status()
        self.transform.assert_not_called()
        self.assertNotIn("key", result)
        self.assertFalse(result["verified"])

    @skipUnless(os.name == "nt", "Windows DPAPI integration")
    def test_real_windows_dpapi_roundtrip_uses_only_a_disposable_key_file(self):
        with mock.patch.object(service.credential_settings.store, "_transform", WINDOWS_DPAPI_TRANSFORM):
            self.configure()
            encrypted = service.key_path().read_bytes()
            self.assertNotIn(b"offline-key-never-print", encrypted)
            self.assertFalse(encrypted.startswith(b"{"))
            self.assertEqual(service._key(service._load()), "offline-key-never-print")
            result = service.save({"key": {"action": "clear"}})
            self.assertFalse(result["configured"])
            self.assertFalse(service.key_path().exists())
        self.network.assert_not_called()

    def test_invalid_settings_do_not_overwrite_existing_credentials(self):
        self.configure()
        original = service.key_path().read_bytes()
        for payload in [{"thinking": False}, {"features": {"ai_answer": "yes"}},
                        {"endpoint_id": "https://untrusted.invalid/secret"},
                        {"key": {"action": "replace", "value": "private key\n"}},
                        {"key": {"action": "clear", "value": "private-key"}}]:
            with self.subTest(payload_keys=list(payload)):
                with self.assertRaises(service.SettingsError) as caught:
                    service.save(payload)
                self.assertNotIn("private", str(caught.exception))
                self.assertEqual(service.key_path().read_bytes(), original)
        self.network.assert_not_called()

    def test_unverified_or_desktop_assistant_never_enables_generation(self):
        self.configure(features={"ai_answer": True})
        with mock.patch.dict(os.environ, {"QB_PRIMARY_ENGINE": "assistant", "MINIMAX_API_KEY": "offline-ocr-key"}), \
                mock.patch.object(readers, "chat", side_effect=AssertionError("OCR fallback")):
            with self.assertRaisesRegex(service.ServiceError, "DeepSeek Pro"):
                service.chat("fake maths", [], kind="answer")
        self.network.assert_not_called()

    def test_probe_requires_explicit_confirmation_and_only_sends_synthetic_image(self):
        self.configure()
        for payload in [{}, {"confirm": False}, {"confirm": True, "question": "user question"}]:
            with self.assertRaises(service.SettingsError):
                service.test_connection(payload)
        self.network.assert_not_called()
        result = self.verify()
        self.assertTrue(result["ready"])
        # Rerun once in a mocked transport to inspect the documented API payload.
        service.test_connection({"confirm": True})
        args, kwargs = self.network.call_args
        self.assertEqual(args, (service.ARK_URL,))
        sent = kwargs["json"]
        self.assertEqual(sent["model"], "ep-offline-pro")
        self.assertEqual(sent["thinking"], {"type": "enabled"})
        self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(sent["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("user question", json.dumps(sent))

    def test_probe_does_not_accept_non_pro_model_or_missing_reasoning_or_wrong_image(self):
        self.configure()
        self.network.side_effect = None
        for response in [answer_response(model="other-vision-pro"), answer_response(model="doubao-lite"),
                         answer_response(thinking=False), answer_response(text="【答案】2【图示】3")]:
            self.network.return_value = response
            with self.assertRaises(service.ServiceError):
                service.test_connection({"confirm": True})
            self.assertFalse(service.public_status()["ready"])

    def test_feature_off_stops_new_requests_and_keep_does_not_force_answers_on(self):
        self.configure(features={"knowledge_tags": True, "ai_answer": False})
        self.verify()
        result = service.save({"features": {"knowledge_tags": False}, "key": {"action": "keep"}})
        self.assertTrue(result["ready"])
        self.assertFalse(result["features"]["ai_answer"])
        with self.assertRaises(service.ServiceError):
            service.chat("maths", [], kind="tags")
        self.network.assert_not_called()

    def test_replace_clear_or_endpoint_change_invalidates_probe_without_network(self):
        for change in [{"endpoint_id": "ep-offline-new"}, {"key": {"action": "replace", "value": "replacement-offline"}},
                       {"key": {"action": "clear"}}]:
            self.configure()
            self.verify()
            result = service.save(change)
            self.assertFalse(result["ready"])
            self.network.assert_not_called()

    def test_failure_is_redacted_invalidates_probe_and_never_falls_back(self):
        self.configure(features={"ai_answer": True})
        self.verify()
        self.network.side_effect = requests.RequestException("Bearer offline-key-never-print")
        with mock.patch.object(readers, "chat", side_effect=AssertionError("OCR fallback")):
            with self.assertRaises(service.ServiceError) as caught:
                service.chat("maths", [], kind="answer")
        self.assertNotIn("offline-key", str(caught.exception))
        self.assertFalse(service.public_status()["ready"])
        self.assertEqual(self.network.call_count, 1)
        with self.assertRaises(service.ServiceError):
            service.chat("maths", [], kind="answer")
        self.assertEqual(self.network.call_count, 1)

    def test_configuration_changed_during_probe_does_not_mark_new_config_verified(self):
        self.configure()
        def changed(*_args, **_kwargs):
            service.save({"endpoint_id": "ep-offline-new"})
            return answer_response()
        self.network.side_effect = changed
        with self.assertRaisesRegex(service.ServiceError, "设置已变化"):
            service.test_connection({"confirm": True})
        self.assertFalse(service.public_status()["ready"])

    def test_settings_api_is_local_guarded_and_never_returns_a_secret(self):
        client = Client()
        self.assertEqual(client.get("/api/settings/library-ai", REMOTE_ADDR="198.51.100.1").status_code, 403)
        self.assertEqual(client.post("/api/settings/library-ai", data="{}", content_type="application/json").status_code, 403)
        response = client.post("/api/settings/library-ai", data=json.dumps({
            "features": {"knowledge_tags": True, "ai_answer": False},
            "endpoint_id": "ep-offline-pro", "key": {"action": "replace", "value": "offline-api-secret"},
        }), content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unverified")
        self.assertFalse(response.json()["ready"])
        self.assertNotIn("offline-api-secret", response.content.decode())
        self.transform.reset_mock()
        response = client.get("/api/settings/library-ai")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("offline-api-secret", response.content.decode())
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_probe_api_rejects_implicit_calls_and_unavailable_service_without_network(self):
        client = Client()
        self.assertEqual(client.get("/api/settings/library-ai/test").status_code, 405)
        self.assertEqual(client.post("/api/settings/library-ai/test", data="{}", content_type="application/json").status_code, 403)
        response = client.post("/api/settings/library-ai/test", data="{}", content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400)
        response = client.post("/api/settings/library-ai/test", data='{"confirm": true}', content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 409)
        self.assertIn("DeepSeek Pro", response.json()["error"])
        self.network.assert_not_called()

    def test_explicit_probe_api_returns_only_public_capability_state(self):
        self.configure()
        self.network.side_effect = None
        self.network.return_value = answer_response()
        response = Client().post("/api/settings/library-ai/test", data='{"confirm": true}',
                                 content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ready"])
        self.assertNotIn("offline-key-never-print", response.content.decode())
        self.assertNotIn("reasoning_content", response.json())
        self.assertEqual(self.network.call_count, 1)


class GenerationBindingTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, question_type="free_response", stem="计算 $1+1$。")
        self.question.approved = True
        self.question.approved_at = timezone.now()
        self.question.approved_content_hash = library.approval_hash(self.question)
        self.question.save()
        self.publication = library.publish(self.question)[0]
        features.save({"ai_answer": True, "knowledge_tags": True})
        ready = mock.patch.object(service, "ensure_ready", return_value={"ready": True})
        ready.start()
        self.addCleanup(ready.stop)
        self.transport = mock.patch.object(service.requests, "post", side_effect=AssertionError("unexpected paid request"))
        self.transport.start()
        self.addCleanup(self.transport.stop)

    def run_job(self, *, kind="answer", side_effect=None):
        job = library_jobs.enqueue(self.publication, kind)
        with mock.patch.object(service, "chat", side_effect=side_effect,
                               return_value=("【答案】2【解析】合成测试", "模拟豆包 Pro")) as chat:
            library_jobs.process_pending()
        job.refresh_from_db()
        self.publication.refresh_from_db()
        return job, chat

    def test_answer_binding_preserves_original_and_is_explicitly_unchecked(self):
        self.publication.content["answer"] = "原卷答案"
        self.publication.save()
        before = deepcopy(self.publication.content)
        job, _chat = self.run_job()
        self.assertEqual(job.status, "done")
        self.assertEqual(self.publication.content, before)
        answer = self.publication.extras["ai_answer"]
        self.assertEqual(answer["publication_id"], str(self.publication.pk))
        self.assertEqual(answer["fingerprint"], library.generation_fingerprint(before, self.publication.pk))
        self.assertFalse(answer["checked"])

    def test_closed_feature_prevents_worker_request_for_an_already_queued_job(self):
        job = library_jobs.enqueue(self.publication, "answer")
        features.save({"ai_answer": False})
        with mock.patch.object(service, "chat") as chat:
            library_jobs.process_pending()
            chat.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_changed_task_and_replaced_version_never_receive_old_async_result(self):
        newer = []
        def changed(*_args, **_kwargs):
            question = Question.objects.select_related("paper").get(pk=self.question.pk)
            question.stem = "计算 $1+2$。"
            question.approved_content_hash = library.approval_hash(question)
            question.save()
            newer.append(library.publish(question)[0])
            return "【答案】2【解析】旧题结果", "模拟豆包 Pro"
        job, _chat = self.run_job(side_effect=changed)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.publication_id, self.publication.pk)
        newer[0].refresh_from_db()
        self.assertNotIn("ai_answer", newer[0].extras)
        self.assertNotIn("ai_answer", self.publication.extras)

    def test_same_publication_content_mutation_discards_the_result(self):
        def changed(*_args, **_kwargs):
            PublishedQuestion.objects.filter(pk=self.publication.pk).update(content={**self.publication.content, "stem": "新题面"})
            return "【答案】2", "模拟豆包 Pro"
        job, _chat = self.run_job(side_effect=changed)
        self.assertEqual(job.status, "failed")
        self.assertNotIn("ai_answer", self.publication.extras)

    def figure(self):
        target = self.temp / "library" / str(self.publication.pk) / "figure-1.png"
        Image.new("RGB", (12, 12), "white").save(target)
        self.publication.content["figures"] = [{"slot": "stem", "page_idx": 0, "bbox": [0, 0, 10, 10],
                                                "file": target.name, "url": f"/api/library/{self.publication.pk}/figures/{target.name}"}]
        self.publication.save()
        return target

    def test_image_bytes_changed_during_call_are_detected_even_with_identical_content_hash(self):
        target = self.figure()
        def changed(*_args, **_kwargs):
            Image.new("RGB", (12, 12), "black").save(target)
            return "【答案】2", "模拟豆包 Pro"
        job, _chat = self.run_job(side_effect=changed)
        self.assertEqual(job.status, "failed")
        self.assertNotIn("ai_answer", self.publication.extras)

    def test_missing_figure_stops_generation_instead_of_silently_omitting_it(self):
        self.figure().unlink()
        job, chat = self.run_job()
        self.assertEqual(job.status, "failed")
        chat.assert_not_called()

    def test_result_is_discarded_if_feature_closes_during_the_call(self):
        def closed(*_args, **_kwargs):
            features.save({"ai_answer": False})
            return "【答案】2", "模拟豆包 Pro"
        # ensure_ready is mocked for service configuration; the worker must
        # independently enforce its own feature gate again before writing.
        job, _chat = self.run_job(side_effect=closed)
        self.assertEqual(job.status, "failed")
        self.assertNotIn("ai_answer", self.publication.extras)

    def test_legacy_answer_is_not_inherited_and_task_or_crop_changes_clear_bound_answer(self):
        content = deepcopy(self.publication.content)
        self.publication.extras = {"ai_answer": {"answer": "旧的无指纹答案"}, "tags": ["集合"]}
        self.assertNotIn("ai_answer", library.carried_extras(self.publication, content))
        fingerprint = library.generation_fingerprint(content, self.publication.pk)
        self.publication.extras["ai_answer"] = {"answer": "2", "fingerprint": fingerprint, "checked": False}
        self.assertIn("ai_answer", library.carried_extras(self.publication, content))
        changed = {**content, "stem": "计算 $1+2$。"}
        self.assertNotIn("ai_answer", library.carried_extras(self.publication, changed))
        self.figure()
        content = deepcopy(self.publication.content)
        self.publication.extras["ai_answer"]["fingerprint"] = library.generation_fingerprint(content, self.publication.pk)
        content["figures"][0]["bbox"] = [0, 0, 11, 10]
        self.assertNotIn("ai_answer", library.carried_extras(self.publication, content))
