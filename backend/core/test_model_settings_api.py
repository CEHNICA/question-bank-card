"""Model settings API contract tests; preferences contain no credentials."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase


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
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["saved"], {
            "primary": "siliconflow_qwen3",
            "checker": "minimax_m3",
            "arbiter": "checker",
        })
        self.assertTrue(response.json()["restart_required"])
        self.assertIn("重新打开", response.json()["message"])

        self.assertTrue(self.preference_file.is_file())
        saved = json.loads(self.preference_file.read_text(encoding="utf-8"))
        self.assertEqual(saved, {
            "version": 1,
            "roles": {
                "primary_engine": "siliconflow_qwen3",
                "checker_engine": "minimax_m3",
                "arbiter_engine": "checker",
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
        })
        self.assertTrue(status["restart_required"])
