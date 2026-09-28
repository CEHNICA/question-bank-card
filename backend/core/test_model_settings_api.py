"""Model settings API contract tests; preferences contain no credentials."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from .management.commands import run_worker


class ModelSettingsApiTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.preference_file = self.temp / "nested" / "model-preferences.json"
        self.client = Client()

    def _post(self, body: dict, **env):
        values = {
            "QB_MODEL_PREFERENCES_FILE": str(self.preference_file),
            "QB_MINIMAX_CONFIGURED": "1",
            "QB_SILICONFLOW_CONFIGURED": "1",
        }
        values.update(env)
        with mock.patch.dict(os.environ, values, clear=True):
            return self.client.post(
                "/api/settings/models",
                data=json.dumps(body),
                content_type="application/json",
                HTTP_X_QB_REQUEST="1",
            )

    def test_each_role_is_restricted_to_its_explicit_allowlist(self):
        valid = {
            "primary": "minimax_m3",
            "checker": "auto",
            "arbiter": "primary",
        }
        for field in ("primary", "checker", "arbiter"):
            with self.subTest(field=field):
                payload = {**valid, field: "../../untrusted-model"}
                response = self._post(payload)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("不受支持", response.json()["error"])
                self.assertFalse(self.preference_file.exists())

    def test_selected_provider_must_have_its_api_configured(self):
        minimax = self._post(
            {"primary": "minimax_m3", "checker": "auto", "arbiter": "primary"},
            QB_MINIMAX_CONFIGURED="0",
            QB_SILICONFLOW_CONFIGURED="0",
        )
        self.assertEqual(minimax.status_code, 400, minimax.content)
        self.assertIn("MiniMax API Key", minimax.json()["error"])
        self.assertFalse(self.preference_file.exists())

        siliconflow = self._post(
            {"primary": "minimax_m3", "checker": "siliconflow_qwen3", "arbiter": "primary"},
            QB_MINIMAX_CONFIGURED="1",
            QB_SILICONFLOW_CONFIGURED="0",
        )
        self.assertEqual(siliconflow.status_code, 400, siliconflow.content)
        self.assertIn("硅基流动 API Key", siliconflow.json()["error"])
        self.assertFalse(self.preference_file.exists())

    def test_valid_selection_is_saved_only_to_explicit_nonsecret_path_and_response_is_stable(self):
        response = self._post({
            "primary": "siliconflow_qwen3",
            "checker": "minimax_m3",
            "arbiter": "checker",
            "models": {
                "minimax": "MiniMax-M3",
                "siliconflow": "Qwen/Qwen3-VL-30B-A3B-Instruct",
            },
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["saved"], {
            "primary": "siliconflow_qwen3",
            "checker": "minimax_m3",
            "arbiter": "checker",
            "models": {
                "minimax": "MiniMax-M3",
                "siliconflow": "Qwen/Qwen3-VL-30B-A3B-Instruct",
            },
        })
        self.assertFalse(response.json()["restart_required"])
        self.assertIn("下一份任务", response.json()["message"])

        self.assertTrue(self.preference_file.is_file())
        saved = json.loads(self.preference_file.read_text(encoding="utf-8"))
        self.assertEqual(saved, {
            "version": 2,
            "roles": {
                "primary_engine": "siliconflow_qwen3",
                "checker_engine": "minimax_m3",
                "arbiter_engine": "checker",
            },
            "models": {
                "minimax": "MiniMax-M3",
                "siliconflow": "Qwen/Qwen3-VL-30B-A3B-Instruct",
            },
        })
        serialized = self.preference_file.read_text(encoding="utf-8")
        self.assertNotIn("API_KEY", serialized)
        self.assertNotIn("test-token", serialized)
        self.assertEqual(list(self.preference_file.parent.glob("model-preferences-*.tmp")), [])

        with mock.patch.dict(os.environ, {
            "QB_MODEL_PREFERENCES_FILE": str(self.preference_file),
            "QB_MINIMAX_CONFIGURED": "1",
            "QB_SILICONFLOW_CONFIGURED": "1",
            "QB_PRIMARY_ENGINE": "minimax_m3",
            "QB_CHECKER_ENGINE": "auto",
            "QB_ARBITER_ENGINE": "primary",
        }, clear=True):
            status = self.client.get("/api/status").json()["engines"]
        self.assertEqual(status["saved"], {
            "primary": "siliconflow_qwen3", "checker": "minimax_m3", "arbiter": "checker",
            "models": {
                "minimax": "MiniMax-M3",
                "siliconflow": "Qwen/Qwen3-VL-30B-A3B-Instruct",
            },
        })
        self.assertTrue(status["pending_change"])

    def test_model_ids_allow_new_provider_models_but_reject_whitespace_urls_and_oversize_values(self):
        valid = {
            "primary": "minimax_m3", "checker": "auto", "arbiter": "primary",
            "models": {"minimax": "MiniMax-M3.1+vision", "siliconflow": "vendor/model:v2"},
        }
        response = self._post(valid)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["saved"]["models"], valid["models"])

        for invalid in ("model with space", "https://example.com/model", "x" * 161, "模型"):
            with self.subTest(invalid=invalid[:30]):
                response = self._post({
                    **valid,
                    "models": {**valid["models"], "minimax": invalid},
                })
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("模型 ID", response.json()["error"])

    def test_status_exposes_current_models_provider_keys_and_suggestions(self):
        with mock.patch.dict(os.environ, {
            "QB_MINIMAX_CONFIGURED": "1",
            "QB_SILICONFLOW_CONFIGURED": "1",
            "QB_MINIMAX_MODEL": "MiniMax-M3.1",
            "QB_SILICONFLOW_MODEL": "Qwen/custom-vl",
        }, clear=True):
            engines = self.client.get("/api/status").json()["engines"]
        self.assertEqual(engines["models"], {
            "minimax": "MiniMax-M3.1", "siliconflow": "Qwen/custom-vl",
        })
        self.assertEqual([item["provider_key"] for item in engines["choices"]], ["minimax", "siliconflow"])
        self.assertIn("Qwen/Qwen3-VL-30B-A3B-Instruct", engines["suggested_models"]["siliconflow"])

    def test_status_converges_only_after_worker_applies_saved_snapshot(self):
        body = {
            "primary": "siliconflow_qwen3",
            "checker": "minimax_m3",
            "arbiter": "checker",
            "models": {"minimax": "MiniMax-New", "siliconflow": "Qwen/NewApplied"},
        }
        response = self._post(body)
        self.assertEqual(response.status_code, 200, response.content)

        old_environment = {
            "QB_MODEL_PREFERENCES_FILE": str(self.preference_file),
            "QB_MINIMAX_CONFIGURED": "1",
            "QB_SILICONFLOW_CONFIGURED": "1",
            "QB_PRIMARY_ENGINE": "minimax_m3",
            "QB_CHECKER_ENGINE": "auto",
            "QB_ARBITER_ENGINE": "primary",
            "QB_MINIMAX_MODEL": "MiniMax-M3",
            "QB_SILICONFLOW_MODEL": "Qwen/Qwen3-VL-32B-Instruct",
        }
        with mock.patch.dict(os.environ, old_environment, clear=True):
            before = self.client.get("/api/status").json()
        self.assertTrue(before["engines"]["pending_change"])
        self.assertEqual(before["engines"]["selected"]["primary"], "minimax_m3")

        with mock.patch.dict(os.environ, old_environment, clear=True):
            applied = run_worker.apply_saved_model_preferences()
        self.assertEqual(applied["models"], body["models"])
        self.assertTrue(self.preference_file.with_name("model-preferences.applied.json").is_file())

        # Even a web process whose startup environment still contains the old
        # values must report what the worker actually applied.
        with mock.patch.dict(os.environ, old_environment, clear=True):
            after = self.client.get("/api/status").json()
        self.assertFalse(after["engines"]["pending_change"])
        self.assertEqual(after["engines"]["selected"], {
            "primary": "siliconflow_qwen3", "checker": "minimax_m3", "arbiter": "checker",
        })
        self.assertEqual(after["engines"]["models"], body["models"])
        self.assertEqual(after["reader"], "NewApplied")
        self.assertEqual(after["checker"], "MiniMax-New")
        self.assertEqual(after["arbiter"], "MiniMax-New")
