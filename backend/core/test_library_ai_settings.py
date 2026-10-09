"""Offline tests for independent DPAPI settings, explicit probes and binding."""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import mock, skipUnless

import requests
from django.test import Client, SimpleTestCase, TestCase, TransactionTestCase
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


class IndependentAISettingsTests(TestCase):
    # 1.13.4：设置接口会连着「题库里还差几道」一起下发，好让开关旁边摆着数字。
    # 这是一次读库，所以这一组从 SimpleTestCase 升到 TestCase。
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
        payload = {"mode": "api", "endpoint_id": "ep-offline-pro", "supports_images": True, "thinking": True,
                   "key": {"action": "replace", "value": "offline-key-never-print"}}
        payload.update(extra)
        return service.save(payload)

    def configure_minimax(self, **extra):
        return service.save({"mode": "api", "provider": "minimax",
                             "key": {"action": "replace", "value": "offline-minimax-subscription-key"}, **extra})

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

    def test_saved_provider_metadata_counts_without_decryption_or_switching(self):
        service.save({"provider": "deepseek", "key": {"action": "replace", "value": "synthetic-deepseek-meta-key"}})
        self.configure_minimax()
        before = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        self.transform.reset_mock()
        result = self.client.get("/api/settings/library-ai").json()
        self.assertEqual(list(result["keys"]), ["deepseek", "minimax", "doubao", "custom", "modelscope", "siliconflow"])
        self.assertEqual(result["keys"], {"deepseek": {"configured": True, "count": 1, "shared_with_reading": False},
            "minimax": {"configured": True, "count": 1, "shared_with_reading": False},
            "doubao": {"configured": False, "count": 0, "shared_with_reading": False},
            "custom": {"configured": False, "count": 0, "shared_with_reading": False},
            "modelscope": {"configured": False, "count": 0, "shared_with_reading": False},
            "siliconflow": {"configured": False, "count": 0, "shared_with_reading": False}})
        self.assertEqual(result["provider"], "minimax")
        self.assertEqual(result["key_count"], 1)
        self.assertNotIn("synthetic-deepseek", json.dumps(result))
        self.assertNotIn("offline-minimax", json.dumps(result))
        self.transform.assert_not_called()
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()})
        self.network.assert_not_called()

    def test_metadata_shows_only_configured_provider_files_not_orphaned_keys(self):
        self.configure_minimax()
        service.key_path("deepseek").write_bytes(b"synthetic-orphan-opaque-file")
        service.key_path("minimax").unlink()
        self.transform.reset_mock()
        result = service.public_status()
        self.assertEqual(result["key_count"], 0)
        self.assertTrue(all(item == {"configured": False, "count": 0, "shared_with_reading": False}
                            for item in result["keys"].values()))
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_malformed_provider_key_metadata_is_safely_ignored(self):
        self.configure_minimax()
        config = json.loads(service.path().read_text(encoding="utf-8"))
        config["key_states"]["deepseek"] = ["invalid-synthetic-state"]
        config["key_states"]["custom"] = {"configured": "true", "revision": "invalid"}
        service._write_settings(config)
        self.transform.reset_mock()
        result = service.public_status()
        self.assertEqual(result["keys"]["deepseek"], {"configured": False, "count": 0, "shared_with_reading": False})
        self.assertEqual(result["keys"]["custom"], {"configured": False, "count": 0, "shared_with_reading": False})
        self.assertEqual(result["keys"]["minimax"], {"configured": True, "count": 1, "shared_with_reading": False})
        self.transform.assert_not_called()

    def test_closing_a_feature_also_closes_its_on_intake_switch(self):
        # 「入库时生成」开着、功能关着，是一种用户自己摆不出来、也说不清的状态：
        # 眼下不会多花钱（生成本来就两个开关都看），可哪天把功能打开，每道新题
        # 入库就悄悄恢复调用一次服务。保存时按实际生效的状态对齐。
        service.save({"features": {"knowledge_tags": True, "ai_answer": True},
                      "on_intake": {"tags": True, "answer": True}})
        self.assertEqual(service.public_status()["on_intake"], {"tags": True, "answer": True})
        saved = service.save({"features": {"knowledge_tags": False, "ai_answer": False}})
        self.assertEqual(saved["features"], {"knowledge_tags": False, "ai_answer": False})
        self.assertEqual(saved["on_intake"], {"tags": False, "answer": False},
                         "功能关掉时，入库时生成必须跟着关掉")
        self.assertEqual(service.public_status()["on_intake"], {"tags": False, "answer": False})

    def test_on_intake_feature_map_agrees_with_the_worker(self):
        # 两处各写了一份「任务种类 → 功能开关」的对应关系：这里改一处、那里忘了改，
        # 就会出现「标签按 ai_answer 开关决定要不要生成」这种错位。
        self.assertEqual({str(kind): feature for kind, feature in library_jobs.FEATURE_OF.items()},
                         service.ON_INTAKE_FEATURE)

    def test_legacy_v1_provider_metadata_remains_visible_without_migration(self):
        # v1 is the old single Doubao credential. Reading metadata neither
        # upgrades settings nor opens the encrypted file.
        service.path().write_text(json.dumps({"version": 1, "endpoint_id": "ep-legacy-test",
            "key_configured": True, "key_revision": "legacy-test-revision"}), encoding="utf-8")
        service.key_path("doubao").write_bytes(b"legacy-opaque-test-key")
        before = service.path().read_bytes()
        result = service.public_status()
        self.assertEqual(result["provider"], "doubao")
        self.assertEqual(result["key_count"], 1)
        self.assertEqual(result["keys"]["doubao"], {"configured": True, "count": 1, "shared_with_reading": False})
        self.assertEqual(service.path().read_bytes(), before)
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_minimax_selection_uses_official_preset_without_importing_ocr_or_enabling_features(self):
        ocr = self.root / "ocr-credentials.dat"
        ocr.write_bytes(b"preexisting opaque OCR key")
        with mock.patch.dict(os.environ, {"MINIMAX_API_KEY": "offline-existing-ocr-key"}):
            result = service.save({"provider": "minimax"})
        self.assertEqual(result["mode"], "assistant", "selecting a model never switches to a paid API")
        self.assertEqual(result["model"], "MiniMax-M3.1-Flash-Preview")
        self.assertEqual(result["base_url"], "https://api.minimax.cn/v1")
        self.assertTrue(result["supports_images"])
        self.assertFalse(result["configured"])
        self.assertFalse(result["api_ready"])
        self.assertEqual(result["features"], {"ai_answer": False, "knowledge_tags": False})
        self.assertEqual(result["on_intake"], {"tags": False, "answer": False})
        self.assertEqual(ocr.read_bytes(), b"preexisting opaque OCR key")
        self.assertFalse(service.key_path("minimax").exists())
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_new_vision_service_presets_do_not_import_keys_enable_features_or_contact_cloud(self):
        ocr = self.root / "ocr-credentials.dat"
        ocr.write_bytes(b"opaque synthetic reader credentials")
        for provider, base, model, thinking in (
            ("modelscope", readers.MODELSCOPE_URL.removesuffix("/chat/completions"), readers.preferences.DEFAULT_MODELS["modelscope"], True),
            ("siliconflow", readers.SILICONFLOW_URL.removesuffix("/chat/completions"), readers.preferences.DEFAULT_MODELS["siliconflow"], False),
        ):
            with self.subTest(provider=provider):
                result = service.save({"provider": provider})
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["base_url"], base)
                self.assertEqual(result["model"], model)
                self.assertIs(result["thinking"], thinking)
                self.assertTrue(result["supports_images"])
                self.assertEqual(result["mode"], "assistant")
                self.assertFalse(result["configured"])
                self.assertFalse(result["api_ready"])
                self.assertEqual(result["features"], {"ai_answer": False, "knowledge_tags": False})
                self.assertEqual(result["on_intake"], {"tags": False, "answer": False})
                self.assertFalse(service.key_path(provider).exists())
        self.assertEqual(ocr.read_bytes(), b"opaque synthetic reader credentials")
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_new_vision_providers_only_accept_their_official_base_urls(self):
        self.configure()
        original = service.path().read_bytes()
        for provider in ("modelscope", "siliconflow"):
            for address in ("https://other-service.invalid/v1", "https://api-inference.modelscope.cn/v1/extra", "http://localhost/v1"):
                with self.subTest(provider=provider, address=address), self.assertRaises(service.SettingsError):
                    service.save({"provider": provider, "base_url": address,
                                  "key": {"action": "replace", "value": "offline-key"}})
                self.assertEqual(service.path().read_bytes(), original)
                self.assertFalse(service.key_path(provider).exists())
            base = service.DEFAULTS[provider]["base_url"]
            result = service.save({"provider": provider, "base_url": base + "/chat/completions"})
            self.assertEqual(result["base_url"], base)
            service.save({"provider": "doubao"})
            original = service.path().read_bytes()
        self.network.assert_not_called()

    def test_vision_gateway_probes_and_generation_send_boolean_thinking_controls(self):
        self.network.side_effect = None
        for provider in ("modelscope", "siliconflow"):
            for thinking in (True, False):
                with self.subTest(provider=provider, thinking=thinking):
                    self.network.reset_mock()
                    self.network.return_value = answer_response(model="offline-vision-model", thinking=thinking)
                    service.save({"mode": "api", "provider": provider, "thinking": thinking,
                                  "features": {"ai_answer": True},
                                  "key": {"action": "replace", "value": "synthetic-independent-key"}})
                    self.network.assert_not_called()
                    self.assertTrue(service.test_connection({"confirm": True})["verified"])
                    args, kwargs = self.network.call_args
                    self.assertEqual(args, (service.DEFAULTS[provider]["base_url"] + "/chat/completions",))
                    sent = kwargs["json"]
                    self.assertIs(sent["enable_thinking"], thinking)
                    for unsupported in ("thinking", "reasoning_effort", "max_completion_tokens"):
                        self.assertNotIn(unsupported, sent)
                    self.assertEqual(sent["max_tokens"], 12000)
                    self.assertFalse(kwargs["allow_redirects"])
                    image = next(part["image_url"]["url"] for part in sent["messages"][0]["content"] if part["type"] == "image_url")
                    with Image.open(io.BytesIO(base64.b64decode(image.split(",", 1)[1]))) as rendered:
                        self.assertEqual(rendered.size, (120, 70))
                    service.chat("synthetic answer question", [], kind="answer")
                    self.assertIs(self.network.call_args.kwargs["json"]["enable_thinking"], thinking)

    def test_per_provider_profiles_preserve_existing_independent_models_and_keys(self):
        service.save({"mode": "api", "provider": "custom", "base_url": "https://synthetic-custom.invalid/v1",
                      "model": "existing-custom-math", "supports_images": True, "thinking": False,
                      "key": {"action": "replace", "value": "offline-custom-profile-key"}})
        original_custom = service.key_path("custom").read_bytes()
        service.save({"provider": "modelscope", "model": "Qwen/explicit-custom-vision", "thinking": False,
                      "key": {"action": "replace", "value": "offline-modelscope-profile-key"}})
        result = service.save({"provider": "custom"})
        self.assertEqual(result["model"], "existing-custom-math")
        self.assertEqual(result["base_url"], "https://synthetic-custom.invalid/v1")
        self.assertFalse(result["thinking"])
        self.assertTrue(result["supports_images"])
        self.assertEqual(service.key_path("custom").read_bytes(), original_custom)
        result = service.save({"provider": "modelscope"})
        self.assertEqual(result["model"], "Qwen/explicit-custom-vision")
        self.assertFalse(result["thinking"])
        self.assertEqual(set(result["provider_profiles"]["custom"]), set(service.API_FIELDS) - {"provider"})
        self.network.assert_not_called()

    def test_provider_profile_status_exposes_only_supported_nonsecret_fields(self):
        self.configure()
        config = service._load()
        config["provider_profiles"]["modelscope"] = {**service.DEFAULTS["modelscope"],
            "key": "synthetic-should-never-be-exposed", "accounts": ["synthetic-account"]}
        config["provider_profiles"]["unknown"] = {"key": "synthetic-unknown-secret"}
        service._write_settings(config)
        self.transform.reset_mock()
        result = service.public_status()
        self.assertEqual(set(result["provider_profiles"]["modelscope"]), set(service.DEFAULTS["modelscope"]))
        self.assertNotIn("unknown", result["provider_profiles"])
        self.assertNotIn("synthetic-should-never-be-exposed", json.dumps(result))
        self.assertNotIn("synthetic-account", json.dumps(result))
        self.transform.assert_not_called()
        self.network.assert_not_called()

    def test_inactive_profile_drafts_are_saved_without_changing_the_verified_active_service(self):
        self.configure_minimax()
        self.verify()
        before = service.execution_snapshot()
        key = service.key_path("minimax").read_bytes()
        result = service.save({"provider_profiles": {
            "modelscope": {"model": "Qwen/synthetic-inactive-profile", "thinking": False},
            "custom": {"base_url": "https://synthetic-inactive.invalid/v1", "model": "inactive-math-model", "supports_images": True}}})
        self.assertTrue(result["verified"])
        self.assertEqual(service.execution_snapshot(), before)
        self.assertEqual(service.key_path("minimax").read_bytes(), key)
        self.assertFalse(service.key_path("modelscope").exists())
        self.assertEqual(result["provider_profiles"]["modelscope"]["model"], "Qwen/synthetic-inactive-profile")
        self.assertFalse(result["provider_profiles"]["modelscope"]["thinking"])
        switched = service.save({"provider": "modelscope"})
        self.assertEqual(switched["model"], "Qwen/synthetic-inactive-profile")
        self.assertFalse(switched["verified"])
        self.assertFalse(switched["thinking"])
        self.network.assert_not_called()

    def test_invalid_inactive_profiles_are_rejected_before_any_file_is_modified(self):
        self.configure_minimax()
        before = {target.name: target.read_bytes() for target in self.root.iterdir() if target.is_file()}
        for profiles in (
            {"minimax": {"model": "cannot-edit-active-here"}},
            {"unknown": {}}, {"modelscope": {"key": "never-an-accepted-field"}},
            {"modelscope": {"base_url": "https://other-provider.invalid/v1"}},
            {"modelscope": {"model": "contains whitespace"}},
            {"siliconflow": {"thinking": "true"}}, {"custom": {"reasoning_effort": "low"}},
            {"doubao": {"model": "bad-endpoint"}}, ["not-a-mapping"], {"custom": "bad-profile"},
        ):
            with self.subTest(profiles=profiles), self.assertRaises(service.SettingsError):
                service.save({"provider_profiles": profiles})
            self.assertEqual({target.name: target.read_bytes() for target in self.root.iterdir() if target.is_file()}, before)
        self.network.assert_not_called()

    def assert_failed_storage_preserved(self, config, key_bytes, snapshot):
        after = service._load()
        for field in ("provider", "mode", "base_url", "model", "supports_images", "thinking", "key_revision", "on_intake", "provider_profiles"):
            self.assertEqual(after[field], config[field], field)
        self.assertFalse(after["verified"])
        self.assertNotEqual(after["revision"], config["revision"])
        for provider, original in key_bytes.items():
            self.assertEqual(service.key_path(provider).read_bytes(), original)
        with self.assertRaises(service.ServiceError):
            service.require_snapshot(snapshot)
        self.assertFalse(service._recovery_path().exists())

    def test_key_write_followed_by_settings_failure_restores_old_key_and_rejects_late_results(self):
        self.configure_minimax()
        self.verify()
        config = service._load()
        snapshot = service.execution_snapshot()
        old_key = service.key_path("minimax").read_bytes()
        write = service._write_settings
        count = 0
        def fail_final(value):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("synthetic storage failure must be redacted")
            return write(value)
        with mock.patch.object(service, "_write_settings", side_effect=fail_final):
            with self.assertRaisesRegex(service.SettingsError, "原密钥和设置已保留.*重新测试连接"):
                service.save({"key": {"action": "replace", "value": "synthetic-replacement-key"},
                              "provider_profiles": {"modelscope": {"model": "Qwen/unsaved-draft"}}})
        self.assert_failed_storage_preserved(config, {"minimax": old_key}, snapshot)
        self.assertFalse(any(self.root.glob(".library-ai-rollback-*")))
        self.network.assert_not_called()

    def test_partial_multi_provider_key_write_failure_restores_every_touched_key(self):
        self.configure_minimax()
        service.save({"keys": {"deepseek": {"action": "replace", "value": "synthetic-old-deepseek"}}})
        self.verify()
        config = service._load()
        snapshot = service.execution_snapshot()
        old_keys = {provider: service.key_path(provider).read_bytes() for provider in ("minimax", "deepseek")}
        write = service._write_provider_key
        def fail_second(provider, action, key):
            if provider == "modelscope":
                raise OSError("synthetic second provider failure")
            return write(provider, action, key)
        with mock.patch.object(service, "_write_provider_key", side_effect=fail_second):
            with self.assertRaises(service.SettingsError):
                service.save({"key": {"action": "replace", "value": "synthetic-new-minimax"}, "keys": {
                    "deepseek": {"action": "replace", "value": "synthetic-new-deepseek"},
                    "modelscope": {"action": "replace", "value": "synthetic-new-modelscope"}}})
        self.assert_failed_storage_preserved(config, old_keys, snapshot)
        self.assertFalse(service.key_path("modelscope").exists())
        self.network.assert_not_called()

    def test_feature_write_failure_restores_keys_switches_and_intake_timing(self):
        self.configure_minimax(features={"ai_answer": True, "knowledge_tags": True}, on_intake={"answer": True, "tags": True})
        self.verify()
        config = service._load()
        snapshot = service.execution_snapshot()
        old_key = service.key_path("minimax").read_bytes()
        old_switches = features.path().read_bytes()
        save_features = features.save
        def fail_after_feature_write(changes):
            save_features(changes)
            raise OSError("synthetic failure after feature write")
        with mock.patch.object(features, "save", side_effect=fail_after_feature_write):
            with self.assertRaises(service.SettingsError):
                service.save({"key": {"action": "replace", "value": "synthetic-new-key"},
                              "features": {"ai_answer": False, "knowledge_tags": False},
                              "on_intake": {"answer": False, "tags": False}})
        self.assert_failed_storage_preserved(config, {"minimax": old_key}, snapshot)
        self.assertEqual(features.path().read_bytes(), old_switches)
        self.assertTrue(features.enabled("ai_answer"))
        self.assertTrue(features.enabled("knowledge_tags"))
        self.network.assert_not_called()

    def test_failed_recovery_is_redacted_and_marker_prevents_api_readiness_until_saved_again(self):
        self.configure_minimax()
        self.verify()
        snapshot = service.execution_snapshot()
        original_replace = service.os.replace
        def fail_settings_restore(source, target):
            if Path(source).name.startswith(".library-ai-rollback-") and Path(target) == service.path():
                raise OSError("synthetic-sensitive-value-in-storage-error")
            return original_replace(source, target)
        with mock.patch.object(service, "_write_settings", side_effect=OSError("synthetic-secret-storage-message")), \
             mock.patch.object(service.os, "replace", side_effect=fail_settings_restore):
            with self.assertRaises(service.SettingsError) as caught:
                service.save({"key": {"action": "replace", "value": "synthetic-new-key"}})
        self.assertIn("未能全部恢复", str(caught.exception))
        self.assertNotIn("synthetic", str(caught.exception))
        self.assertNotIn("原密钥和设置已保留", str(caught.exception))
        self.assertTrue(service._recovery_path().exists())
        self.assertFalse(service.public_status()["verified"])
        self.assertFalse(service.public_status()["api_ready"])
        with self.assertRaises(service.ServiceError):
            service.require_snapshot(snapshot)
        with self.assertRaises(service.ServiceError):
            service.ensure_api_ready()
        result = service.save({"key": {"action": "keep"}})
        self.assertFalse(service._recovery_path().exists())
        self.assertFalse(result["api_ready"])
        self.network.assert_not_called()

    def test_minimax_key_is_independent_and_provider_switch_or_clear_never_reuses_other_keys(self):
        self.configure(provider="deepseek", endpoint_id="deepseek-v4-pro", supports_images=False)
        other_key = service.key_path("deepseek").read_bytes()
        self.configure_minimax()
        own_path = service.key_path("minimax")
        self.assertEqual(service._key(service._load()), "offline-minimax-subscription-key")
        self.assertNotEqual(own_path, service.key_path("deepseek"))
        self.assertEqual(service.key_path("deepseek").read_bytes(), other_key)
        public = service.public_status()
        self.assertNotIn("offline-minimax", json.dumps(public) + service.path().read_text())
        service.save({"key": {"action": "clear"}})
        self.assertFalse(own_path.exists())
        self.assertEqual(service.key_path("deepseek").read_bytes(), other_key)
        self.assertTrue(service.save({"provider": "deepseek"})["key_configured"])
        self.assertFalse(service.save({"provider": "minimax"})["key_configured"])
        self.network.assert_not_called()

    def test_minimax_m31_probe_and_generation_use_actual_multimodal_adaptive_contract(self):
        self.configure_minimax(features={"knowledge_tags": True, "ai_answer": True})
        self.network.side_effect = None
        # The gateway/SDK may omit reasoning_content for a trivial answer.
        # A correct explicit image probe must still succeed without storing it.
        self.network.return_value = answer_response(model=service.MINIMAX_MODEL, thinking=False)
        self.assertTrue(service.test_connection({"confirm": True})["verified"])
        args, kwargs = self.network.call_args
        self.assertEqual(args, ("https://api.minimax.cn/v1/chat/completions",))
        sent = kwargs["json"]
        self.assertEqual(sent["model"], service.MINIMAX_MODEL)
        self.assertEqual(sent["thinking"], {"type": "adaptive"})
        self.assertEqual(sent["reasoning_effort"], "high")
        self.assertIs(sent["reasoning_split"], True)
        self.assertEqual(sent["max_completion_tokens"], 12000)
        self.assertNotIn("max_tokens", sent)
        self.assertNotIn("service_tier", sent)
        self.assertFalse(kwargs["allow_redirects"])
        urls = [part["image_url"]["url"] for part in sent["messages"][0]["content"] if part["type"] == "image_url"]
        self.assertEqual(len(urls), 1)
        with Image.open(io.BytesIO(base64.b64decode(urls[0].split(",", 1)[1]))) as image:
            self.assertEqual(image.size, (120, 70))
        for kind in ("answer", "tags", None):
            with self.subTest(kind=kind):
                text, engine = service.chat("offline synthetic question", [], kind=kind)
                self.assertIn("【答案】2", text)
                self.assertIn(service.MINIMAX_MODEL, engine)
                self.assertTrue(service.public_status()["verified"])

    def test_minimax_reasoning_formats_only_return_final_answer_and_do_not_false_fail(self):
        self.configure_minimax(features={"ai_answer": True}, supports_images=False)
        self.network.side_effect = None
        for message in [
            {"content": "【答案】2", "reasoning_content": "private reasoning"},
            {"content": "【答案】2", "reasoning_details": [{"type": "reasoning.text", "text": "private reasoning"}]},
            {"content": "<think>private reasoning</think>【答案】2"},
            {"content": "【答案】2"},
        ]:
            with self.subTest(fields=list(message)):
                response = answer_response(model=service.MINIMAX_MODEL)
                response.json.return_value["choices"][0]["message"] = message
                self.network.return_value = response
                self.assertTrue(service.test_connection({"confirm": True})["ready"])
                self.assertEqual(service.chat("offline", [], kind="answer")[0], "【答案】2")
                self.assertNotIn("private reasoning", json.dumps(service.public_status()) + service.path().read_text())

    def test_minimax_m3_and_m2_never_send_unsupported_effort_or_assume_vision(self):
        self.network.side_effect = None
        self.network.return_value = answer_response(text="【答案】2", thinking=False, model="MiniMax-M3")
        for model, thinking in [("MiniMax-M3", True), ("MiniMax-M3", False), ("MiniMax-M2.7", True), ("future-explicit-model", True)]:
            with self.subTest(model=model, thinking=thinking):
                self.configure_minimax(model=model, thinking=thinking, supports_images=False)
                self.assertTrue(service.test_connection({"confirm": True})["ready"])
                sent = self.network.call_args.kwargs["json"]
                self.assertNotIn("reasoning_effort", sent)
                self.assertEqual([part["type"] for part in sent["messages"][0]["content"]], ["text"])
                if model == "MiniMax-M3":
                    self.assertEqual(sent["thinking"], {"type": "adaptive" if thinking else "disabled"})
                else:
                    self.assertNotIn("thinking", sent)
                self.network.reset_mock()
                with self.assertRaisesRegex(service.ServiceError, "未声明支持图片"):
                    service._request(service._load(), "offline", ["data:image/png;base64,offline"], 500)
                self.network.assert_not_called()

    def test_minimax_invalid_model_controls_reject_before_saving_or_network(self):
        self.configure_minimax()
        original_config = service.path().read_bytes()
        original_key = service.key_path().read_bytes()
        for changes in [
            {"thinking": False},
            {"model": "MiniMax-M2.7", "supports_images": True},
            {"model": "MiniMax-M2.7", "supports_images": False, "thinking": False},
            {"base_url": "https://not-minimax.invalid/v1"},
            {"base_url": "https://api.minimax.io/v1"},
        ]:
            with self.subTest(changes=changes):
                with self.assertRaises(service.SettingsError):
                    service.save(changes)
                self.assertEqual(service.path().read_bytes(), original_config)
                self.assertEqual(service.key_path().read_bytes(), original_key)
        for confirmation in [{}, {"confirm": False}]:
            with self.assertRaises(service.SettingsError):
                service.test_connection(confirmation)
        self.network.assert_not_called()

    def test_minimax_still_rejects_wrong_image_or_incomplete_final_answer_without_fallback(self):
        self.configure_minimax()
        self.network.side_effect = None
        wrong_image = answer_response(text="【答案】2【图示】3", thinking=False, model=service.MINIMAX_MODEL)
        unfinished = answer_response(text="<think>unfinished private reasoning", model=service.MINIMAX_MODEL)
        truncated = answer_response(model=service.MINIMAX_MODEL)
        truncated.json.return_value["choices"][0]["finish_reason"] = "length"
        missing_answer = answer_response(text="", model=service.MINIMAX_MODEL)
        for response in (wrong_image, unfinished, truncated, missing_answer):
            with self.subTest(response_type=response.json.return_value["choices"][0]["finish_reason"]):
                self.network.return_value = response
                with mock.patch.object(readers, "chat", side_effect=AssertionError("OCR fallback")):
                    with self.assertRaises(service.ServiceError):
                        service.test_connection({"confirm": True})
                self.assertFalse(service.public_status()["api_ready"])
        self.assertEqual(self.network.call_count, 4, "a failed probe never retries another model, key or endpoint")

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
        for payload in [{"thinking": "yes"}, {"features": {"ai_answer": "yes"}},
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
            with self.assertRaisesRegex(service.ServiceError, "通过显式测试"):
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

    def test_probe_checks_requested_reasoning_and_images_without_claiming_answer_quality(self):
        self.configure()
        self.network.side_effect = None
        for response in [answer_response(thinking=False), answer_response(text="【答案】2【图示】3")]:
            self.network.return_value = response
            with self.assertRaises(service.ServiceError):
                service.test_connection({"confirm": True})
            self.assertFalse(service.public_status()["ready"])
        self.network.return_value = answer_response(model="another-compatible-model")
        self.assertTrue(service.test_connection({"confirm": True})["api_ready"])

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
            "mode": "api",
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
        self.assertIn("通过显式测试", response.json()["error"])
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


class GenerationBindingTests(TempDataMixin, TransactionTestCase):
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
        isolated = mock.patch.dict(os.environ, {"QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "settings.json"),
                                                "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "library-ai.dat")})
        isolated.start()
        self.addCleanup(isolated.stop)
        ready = mock.patch.object(service, "ensure_ready", return_value={"ready": True, "mode": "api"})
        ready.start()
        self.addCleanup(ready.stop)
        snapshot = mock.patch.object(service, "execution_snapshot", return_value={"mode": "api", "revision": "offline-test"})
        snapshot.start()
        self.addCleanup(snapshot.stop)
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

    def test_queue_fingerprint_stops_changed_picture_before_any_api_request(self):
        target = self.figure()
        job = library_jobs.enqueue(self.publication, "answer")
        Image.new("RGB", (12, 12), "black").save(target)
        with mock.patch.object(service, "chat") as chat:
            library_jobs.process_pending()
            chat.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_api_configuration_snapshot_is_checked_before_request_and_after_response(self):
        job = library_jobs.enqueue(self.publication, "answer")
        with mock.patch.object(service, "execution_snapshot", return_value={"mode": "api", "revision": "changed"}), \
                mock.patch.object(service, "chat") as chat:
            library_jobs.process_pending()
            chat.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")
        def changed(*_args, **_kwargs):
            self.changed_snapshot = mock.patch.object(service, "execution_snapshot", return_value={"mode": "api", "revision": "changed"})
            self.changed_snapshot.start()
            self.addCleanup(self.changed_snapshot.stop)
            return "【答案】2", "离线 API"
        job, _chat = self.run_job(side_effect=changed)
        self.assertEqual(job.status, "failed")
        self.assertNotIn("ai_answer", self.publication.extras)

    def test_direct_enqueue_and_pending_write_cannot_overwrite_existing_ai_result(self):
        job = library_jobs.enqueue(self.publication, "answer")
        library.save_extras(self.publication, {"ai_answer": {"answer": "已有结果", "checked": False}})
        with self.assertRaisesRegex(library_jobs.JobError, "已有"):
            library_jobs.enqueue(self.publication, "answer")
        with mock.patch.object(service, "chat") as chat:
            library_jobs.process_pending()
            chat.assert_not_called()
        job.refresh_from_db()
        self.publication.refresh_from_db()
        self.assertEqual(job.status, "failed")
        self.assertEqual(self.publication.extras["ai_answer"]["answer"], "已有结果")


class SharedMiniMaxKeyTests(IndependentAISettingsTests):
    """同一家 MiniMax 的密钥只填一次——但绝不自动填。

    读题和答案各存一份加密文件，所以同一个 Key 过去要粘贴两次。这里是那条
    显式的路：老师点一下，才把读题那份复制到答案这边；已经有一份独立密钥时
    按钮会明说“改用”，不会悄悄换掉。
    """

    def save_reading_key(self, *accounts):
        return service.credential_settings.save_actions(
            {"minimax": {"action": "replace", "accounts": list(accounts)}})

    def test_the_reading_key_can_be_copied_over_once_instead_of_typed_twice(self):
        self.save_reading_key("reading-minimax-key-one", "reading-minimax-key-two")
        self.configure_minimax()
        self.assertFalse(service.public_status()["keys"]["minimax"]["shared_with_reading"])
        result = service.share_reading_key("minimax")
        self.assertTrue(result["key_configured"])
        self.assertTrue(result["keys"]["minimax"]["shared_with_reading"])
        # 一个读题账号池里有多条时，答案侧只取第 1 条，并且这件事要说出来。
        self.assertEqual(service._key(service._load()), "reading-minimax-key-one")
        # 共用后原来的“已测通”不再成立：换的是凭据，不是配置。
        self.assertFalse(result["verified"])

    def test_an_existing_independent_key_is_never_replaced_without_the_press(self):
        self.save_reading_key("reading-minimax-key-one")
        self.configure_minimax()
        self.assertEqual(service._key(service._load()), "offline-minimax-subscription-key")
        # 保存本身不动它；只有 share_reading_key 才是那个显式动作。
        self.assertFalse(service.public_status()["keys"]["minimax"]["shared_with_reading"])
        self.assertEqual(service._key(service._load()), "offline-minimax-subscription-key")
        service.share_reading_key("minimax")
        self.assertEqual(service._key(service._load()), "reading-minimax-key-one")

    def test_going_back_to_an_independent_key_clears_the_shared_mark(self):
        self.save_reading_key("reading-minimax-key-one")
        self.configure_minimax()
        service.share_reading_key("minimax")
        result = service.save({"key": {"action": "clear"}})
        self.assertFalse(result["key_configured"])
        self.assertFalse(result["keys"]["minimax"]["shared_with_reading"])
        # 读题那一份始终没被动过。
        self.assertEqual(service.credential_settings.reveal_saved_key("minimax", 0), "reading-minimax-key-one")

    def test_nothing_is_offered_when_the_reading_side_has_no_minimax_key(self):
        self.configure_minimax()
        self.assertNotIn("reading_key_available", service.public_status(),
                         "the answers status must never open the reading credential store")
        with self.assertRaises(service.SettingsError):
            service.share_reading_key("minimax")

    def test_only_supported_reading_providers_can_be_shared(self):
        self.configure()  # doubao: answers only
        self.assertEqual(service.public_status()["shareable_from_reading"], ["minimax", "modelscope", "siliconflow"])
        with self.assertRaises(service.SettingsError):
            service.share_reading_key("deepseek")

    def test_explicit_reuse_selects_each_reading_service_but_keeps_independent_keys_and_switches(self):
        for provider in ("minimax", "modelscope", "siliconflow"):
            with self.subTest(provider=provider):
                service.save({"mode": "api", "provider": "deepseek", "model": "deepseek-existing-math",
                              "key": {"action": "replace", "value": "synthetic-existing-deepseek-key"}})
                independent_key = service.key_path("deepseek").read_bytes()
                service.credential_settings.save_actions({provider: {"action": "replace", "accounts": [
                    f"synthetic-{provider}-first", f"synthetic-{provider}-second"]}})
                result = service.share_reading_key(provider)
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["mode"], "api")
                self.assertEqual(result["model"], service.DEFAULTS[provider]["model"])
                self.assertEqual(result["base_url"], service.DEFAULTS[provider]["base_url"])
                self.assertTrue(result["supports_images"])
                self.assertEqual(result["thinking"], service.DEFAULTS[provider]["thinking"])
                self.assertTrue(result["keys"][provider]["shared_with_reading"])
                self.assertEqual(service._key(service._load()), f"synthetic-{provider}-first")
                self.assertFalse(result["verified"])
                self.assertFalse(result["api_ready"])
                self.assertEqual(result["features"], {"ai_answer": False, "knowledge_tags": False})
                self.assertEqual(result["on_intake"], {"tags": False, "answer": False})
                self.assertEqual(service.key_path("deepseek").read_bytes(), independent_key)
                self.assertEqual(result["provider_profiles"]["deepseek"]["model"], "deepseek-existing-math")
                rendered = json.dumps(result) + service.path().read_text(encoding="utf-8")
                self.assertNotIn("synthetic-existing-deepseek-key", rendered)
                self.assertNotIn(f"synthetic-{provider}-first", rendered)
        self.network.assert_not_called()

    def test_reuse_retains_the_saved_target_profile_and_invalidates_its_probe(self):
        for provider in ("modelscope", "siliconflow"):
            with self.subTest(provider=provider):
                service.save({"mode": "api", "provider": provider, "model": "Qwen/synthetic-saved-model",
                              "thinking": False, "supports_images": False,
                              "key": {"action": "replace", "value": "synthetic-target-independent-key"},
                              "features": {"knowledge_tags": True, "ai_answer": True},
                              "on_intake": {"tags": False, "answer": True}})
                self.network.side_effect = None
                self.network.return_value = answer_response(text="【答案】2", thinking=False)
                self.assertTrue(service.test_connection({"confirm": True})["verified"])
                self.network.reset_mock()
                self.network.side_effect = AssertionError("unexpected paid request")
                service.save({"provider": "deepseek"})
                service.credential_settings.save_actions({provider: {"action": "replace", "accounts": ["synthetic-shared-key"]}})
                result = service.share_reading_key(provider)
                self.assertEqual(result["model"], "Qwen/synthetic-saved-model")
                self.assertFalse(result["thinking"])
                self.assertFalse(result["supports_images"])
                self.assertFalse(result["verified"])
                self.assertEqual(result["features"], {"ai_answer": True, "knowledge_tags": True})
                self.assertEqual(result["on_intake"], {"tags": False, "answer": True})
                self.network.assert_not_called()

    def test_shared_copy_never_follows_changes_and_keep_does_not_erase_its_source_mark(self):
        for provider in ("minimax", "modelscope", "siliconflow"):
            with self.subTest(provider=provider):
                service.credential_settings.save_actions({provider: {"action": "replace", "accounts": ["synthetic-source-old"]}})
                service.share_reading_key(provider)
                initial_revision = service._load()["key_revision"]
                service.credential_settings.save_actions({provider: {"action": "replace", "accounts": ["synthetic-source-new"]}})
                result = service.save({"key": {"action": "keep"}})
                self.assertEqual(service._key(service._load()), "synthetic-source-old")
                self.assertEqual(service._load()["key_revision"], initial_revision)
                self.assertTrue(result["keys"][provider]["shared_with_reading"])
                service.credential_settings.save_actions({provider: {"action": "clear"}})
                self.assertEqual(service._key(service._load()), "synthetic-source-old")
                self.assertTrue(service.public_status()["key_configured"])
        self.network.assert_not_called()

    def test_failed_explicit_reuse_does_not_switch_or_overwrite_any_configuration(self):
        self.configure()
        before_settings = service.path().read_bytes()
        before_key = service.key_path("doubao").read_bytes()
        for provider in ("minimax", "modelscope", "siliconflow", "deepseek", "custom", "unknown"):
            with self.subTest(provider=provider), self.assertRaises(service.SettingsError):
                service.share_reading_key(provider)
            self.assertEqual(service.path().read_bytes(), before_settings)
            self.assertEqual(service.key_path("doubao").read_bytes(), before_key)
        self.network.assert_not_called()

    def test_http_reuse_selects_modelscope_from_an_independent_answer_service_without_secret_response(self):
        self.configure()
        service.credential_settings.save_actions({"modelscope": {"action": "replace", "accounts": ["synthetic-http-reused-key"]}})
        response = self.client.post("/api/settings/library-ai/share-reading-key", data=json.dumps({"provider": "modelscope"}),
            content_type="application/json", HTTP_X_QB_REQUEST="1", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result["provider"], "modelscope")
        self.assertFalse(result["verified"])
        self.assertTrue(result["keys"]["doubao"]["configured"])
        self.assertNotIn("synthetic-http-reused-key", json.dumps(result))
        self.network.assert_not_called()

    def test_failed_share_restores_target_key_and_active_service_without_reviving_old_snapshot(self):
        self.configure_minimax()
        service.save({"provider": "modelscope", "model": "Qwen/synthetic-original-profile",
                      "key": {"action": "replace", "value": "synthetic-independent-modelscope"}})
        service.save({"provider": "minimax"})
        self.verify()
        config = service._load()
        snapshot = service.execution_snapshot()
        old_keys = {provider: service.key_path(provider).read_bytes() for provider in ("modelscope", "minimax")}
        service.credential_settings.save_actions({"modelscope": {"action": "replace", "accounts": ["synthetic-shared-copy"]}})
        with mock.patch.object(service, "_write_settings", side_effect=OSError("synthetic-private-storage-error")):
            with self.assertRaisesRegex(service.SettingsError, "原密钥和设置已保留.*重新测试连接"):
                service.share_reading_key("modelscope")
        self.assert_failed_storage_preserved(config, old_keys, snapshot)
        self.assertEqual(service.credential_settings.reveal_saved_key("modelscope", 0), "synthetic-shared-copy")
        self.network.assert_not_called()

    def test_the_endpoint_is_local_only_and_never_returns_a_secret(self):
        self.save_reading_key("reading-minimax-key-one")
        self.configure_minimax()
        result = service.share_reading_key("minimax")
        rendered = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("reading-minimax-key-one", rendered)
        self.assertNotIn("offline-minimax-subscription-key", rendered)

    def test_the_http_route_shares_the_key_and_stays_on_this_machine(self):
        self.save_reading_key("reading-minimax-key-one")
        self.configure_minimax()
        client = Client()
        remote = client.post("/api/settings/library-ai/share-reading-key", data="{}",
                             content_type="application/json", HTTP_X_QB_REQUEST="1",
                             REMOTE_ADDR="203.0.113.9")
        self.assertEqual(remote.status_code, 403)
        local = client.post("/api/settings/library-ai/share-reading-key",
                            data=json.dumps({"provider": "minimax"}),
                            content_type="application/json", HTTP_X_QB_REQUEST="1",
                            REMOTE_ADDR="127.0.0.1")
        self.assertEqual(local.status_code, 200, local.content)
        self.assertTrue(local.json()["keys"]["minimax"]["shared_with_reading"])


class PerServiceKeyTests(IndependentAISettingsTests):
    """每家服务各存一个 Key：多填一家不必先切服务商，也不必存两次。

    界面上是一家一块，各自带输入框和删除按钮；切服务商只决定“正在用哪一家”，
    顺手填的另外几家要跟着这一次保存一起落盘。这里守的是两件事：另外几家的密钥
    真的写进了它们自己的文件，以及正在用的那一家完全没被这些保存动过。
    """

    def stored(self, provider):
        return service.credential_settings.store._transform(
            service.key_path(provider).read_bytes(), protect=False).decode("utf-8")

    def test_a_second_service_is_stored_without_switching_the_one_in_use(self):
        self.configure_minimax()
        verified = self.verify()
        self.assertTrue(verified["verified"])
        result = service.save({"keys": {"deepseek": {"action": "replace", "value": "offline-second-service-key"}}})
        self.assertEqual(result["provider"], "minimax", "saving another service never changes which one is in use")
        self.assertTrue(result["keys"]["deepseek"]["configured"])
        self.assertIn("offline-second-service-key", self.stored("deepseek"))
        # 换的是另一家正在用的凭据，本来就与当前这一家无关：已测通不该被推翻。
        self.assertTrue(result["verified"])
        self.assertEqual(service._key(service._load()), "offline-minimax-subscription-key")

    def test_several_services_can_be_filled_in_one_save(self):
        self.configure()
        result = service.save({"keys": {"deepseek": {"action": "replace", "value": "offline-deepseek-key"},
                                        "minimax": {"action": "replace", "value": "offline-minimax-key"},
                                        "custom": {"action": "replace", "value": "offline-custom-key"}}})
        self.assertEqual([result["keys"][name]["configured"] for name in ("deepseek", "minimax", "custom")], [True, True, True])
        for provider, expected in (("deepseek", "offline-deepseek-key"), ("minimax", "offline-minimax-key"), ("custom", "offline-custom-key")):
            self.assertIn(expected, self.stored(provider))

    def test_replacing_and_clearing_another_service_touches_only_its_own_file(self):
        self.configure()
        service.save({"keys": {"deepseek": {"action": "replace", "value": "offline-deepseek-one"},
                               "custom": {"action": "replace", "value": "offline-custom-one"}}})
        service.save({"keys": {"deepseek": {"action": "replace", "value": "offline-deepseek-two"}}})
        self.assertIn("offline-deepseek-two", self.stored("deepseek"))
        self.assertIn("offline-custom-one", self.stored("custom"), "an untouched service keeps its key")
        self.assertEqual(service._key(service._load()), "offline-key-never-print")
        result = service.save({"keys": {"custom": {"action": "clear"}}})
        self.assertFalse(result["keys"]["custom"]["configured"])
        self.assertFalse(service.key_path("custom").exists())
        self.assertTrue(result["keys"]["deepseek"]["configured"], "clearing one service never clears another")
        self.assertEqual(service._key(service._load()), "offline-key-never-print")

    def test_keeping_a_service_that_was_not_typed_into_changes_nothing(self):
        self.configure()
        before = service.path().read_bytes()
        result = service.save({"keys": {"deepseek": {"action": "keep"}}})
        self.assertEqual(service.path().read_bytes(), before)
        self.assertFalse(result["keys"]["deepseek"]["configured"])

    def test_the_service_in_use_cannot_be_saved_through_the_other_list(self):
        self.configure_minimax()
        with self.assertRaisesMessage(service.SettingsError, "它自己那一栏"):
            service.save({"keys": {"minimax": {"action": "clear"}}})
        self.assertEqual(service._key(service._load()), "offline-minimax-subscription-key")

    def test_unknown_services_and_broken_instructions_are_refused_before_anything_is_written(self):
        self.configure()
        before = service.path().read_bytes()
        for payload in ({"keys": {"openai": {"action": "clear"}}},
                        {"keys": {"deepseek": {"action": "delete"}}},
                        {"keys": {"deepseek": {"action": "keep", "value": "offline-x"}}},
                        {"keys": {"deepseek": {"action": "replace", "value": "offline with spaces"}}},
                        {"keys": {"deepseek": {"action": "replace", "value": ""}}},
                        {"keys": "deepseek"},
                        {"keys": {"deepseek": "offline-key"}}):
            with self.subTest(payload=payload), self.assertRaises(service.SettingsError):
                service.save(payload)
        self.assertEqual(service.path().read_bytes(), before)
        self.assertFalse(service.key_path("deepseek").exists())

    def test_the_http_route_accepts_the_same_multi_service_save(self):
        self.configure_minimax()
        client = Client()
        remote = client.post("/api/settings/library-ai", data=json.dumps(
            {"keys": {"deepseek": {"action": "replace", "value": "offline-remote-key"}}}),
            content_type="application/json", HTTP_X_QB_REQUEST="1", REMOTE_ADDR="203.0.113.9")
        self.assertEqual(remote.status_code, 403)
        self.assertFalse(service.key_path("deepseek").exists(), "a remote caller stores nothing")
        local = client.post("/api/settings/library-ai", data=json.dumps(
            {"keys": {"deepseek": {"action": "replace", "value": "offline-local-key"}}}),
            content_type="application/json", HTTP_X_QB_REQUEST="1", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(local.status_code, 200, local.content)
        body = local.json()
        self.assertTrue(body["keys"]["deepseek"]["configured"])
        self.assertEqual(body["provider"], "minimax")
        self.assertNotIn("offline-local-key", json.dumps(body, ensure_ascii=False))
