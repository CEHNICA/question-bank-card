"""In-app credential API and worker boundary reload tests.

The store cipher is replaced by an identity transform in most tests so the
same API contract can run on Linux CI.  Windows DPAPI encryption itself is
covered by the root-level credential store round-trip tests.
"""

from __future__ import annotations

import json
import http.client
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import Client, SimpleTestCase

from . import credential_settings
from .management.commands import run_worker


def identity_transform(data: bytes, *, protect: bool) -> bytes:
    return data


class MineruCredentialProbeTests(SimpleTestCase):
    def test_transport_and_header_encoding_failures_are_unavailable_not_exceptions(self):
        response = mock.MagicMock(status=200)
        response.read.side_effect = http.client.IncompleteRead(b"")
        context = mock.MagicMock()
        context.__enter__.return_value = response
        with mock.patch.object(credential_settings.urllib.request, "urlopen", return_value=context):
            self.assertIsNone(credential_settings._mineru_token_validity("test-token"))

        encoding_error = UnicodeEncodeError("latin-1", "密", 0, 1, "not representable")
        with mock.patch.object(
            credential_settings.urllib.request, "urlopen", side_effect=encoding_error,
        ):
            self.assertIsNone(credential_settings._mineru_token_validity("含中文的测试令牌"))


class CredentialSettingsApiTests(SimpleTestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.path = self.temp / "nested" / "credentials.dat"
        self.client = Client()
        self.environment = mock.patch.dict(os.environ, {
            "QB_CREDENTIAL_FILE": str(self.path),
            "QB_CREDENTIAL_HOT_RELOAD": "1",
            "QB_MODEL_PREFERENCES_FILE": str(self.temp / "model-preferences.json"),
            "QB_MINERU_CONFIGURED": "0",
            "QB_MINERU_POOL_SIZE": "0",
            "QB_MINIMAX_CONFIGURED": "0",
            "QB_MINIMAX_POOL_SIZE": "0",
            "QB_SILICONFLOW_CONFIGURED": "0",
            "QB_SILICONFLOW_POOL_SIZE": "0",
        })
        self.cipher = mock.patch.object(
            credential_settings.store, "_transform", side_effect=identity_transform,
        )
        self.mineru_validity = mock.patch.object(
            credential_settings, "_mineru_token_validity", return_value=True,
        )
        self.environment.start()
        self.cipher.start()
        self.mineru_validity.start()
        self.addCleanup(self.mineru_validity.stop)
        self.addCleanup(self.cipher.stop)
        self.addCleanup(self.environment.stop)

    def post(self, services: dict):
        return self.client.post(
            "/api/settings/credentials",
            data=json.dumps({"services": services}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

    def test_replace_response_and_get_expose_only_counts(self):
        secrets = ["mineru-secret-one", "mineru-secret-two", "minimax-secret"]
        response = self.post({
            "mineru": {"action": "replace", "accounts": secrets[:2]},
            "minimax": {"action": "replace", "accounts": secrets[2:]},
            "siliconflow": {"action": "keep"},
        })
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["services"], {
            "mineru": {"configured": True, "count": 2},
            "minimax": {"configured": True, "count": 1},
            "siliconflow": {"configured": False, "count": 0},
        })
        self.assertEqual(response.json()["max_accounts"], 8)
        self.assertFalse(response.json()["restart_required"])
        for secret in secrets:
            self.assertNotIn(secret, response.content.decode("utf-8"))

        fetched = self.client.get("/api/settings/credentials")
        self.assertEqual(fetched.status_code, 200, fetched.content)
        self.assertEqual(fetched.json()["services"], response.json()["services"])
        for secret in secrets:
            self.assertNotIn(secret, fetched.content.decode("utf-8"))
        self.assertEqual(os.environ["QB_MINERU_POOL_SIZE"], "2")
        self.assertEqual(os.environ["QB_MINIMAX_CONFIGURED"], "1")
        status = self.client.get("/api/status").json()
        self.assertTrue(status["mineru"])
        self.assertTrue(status["engines"]["configured"]["minimax"])
        self.assertEqual(list(self.path.parent.glob("credentials-*.tmp")), [])

        with mock.patch.object(
            credential_settings.store, "load_credentials",
            side_effect=AssertionError("GET must not decrypt the credential store"),
        ):
            self.assertEqual(self.client.get("/api/settings/credentials").status_code, 200)
            self.assertEqual(self.client.get("/api/status").status_code, 200)

    def test_mineru_replace_rejects_known_invalid_and_marks_offline_save_unverified(self):
        secret = "mineru-secret-must-not-echo"
        with mock.patch.object(credential_settings, "_mineru_token_validity", return_value=False):
            invalid = self.post({
                "mineru": {"action": "replace", "accounts": [secret]},
                "minimax": {"action": "keep"},
                "siliconflow": {"action": "keep"},
            })
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertFalse(self.path.exists())
        self.assertNotIn(secret, invalid.content.decode("utf-8"))

        with mock.patch.object(credential_settings, "_mineru_token_validity", return_value=None):
            unavailable = self.post({
                "mineru": {"action": "replace", "accounts": [secret]},
                "minimax": {"action": "keep"},
                "siliconflow": {"action": "keep"},
            })
        self.assertEqual(unavailable.status_code, 200, unavailable.content)
        self.assertEqual(unavailable.json()["mineru_verification"], "unavailable")
        self.assertIn("尚未验证", unavailable.json()["message"])
        self.assertNotIn(secret, unavailable.content.decode("utf-8"))

    def test_keep_clear_and_replace_are_explicit(self):
        credential_settings.store.save_credentials({
            "mineru_tokens": ["old-mineru"],
            "minimax_keys": ["old-minimax"],
            "siliconflow_keys": ["old-siliconflow"],
        })
        response = self.post({
            "mineru": {"action": "keep"},
            "minimax": {"action": "clear"},
            "siliconflow": {"action": "replace", "accounts": ["new-siliconflow"]},
        })
        self.assertEqual(response.status_code, 200, response.content)
        loaded = credential_settings.store.load_credentials()
        self.assertEqual(credential_settings.store.credential_pool(loaded, "mineru"), ["old-mineru"])
        self.assertEqual(credential_settings.store.credential_pool(loaded, "minimax"), [])
        self.assertEqual(credential_settings.store.credential_pool(loaded, "siliconflow"), ["new-siliconflow"])

    def test_rejects_bad_shape_too_many_accounts_and_nonlocal_requests_without_echo(self):
        secret = "never-echo-this-secret"
        without_guard = self.client.post(
            "/api/settings/credentials",
            data=json.dumps({"services": {"mineru": {"action": "replace", "accounts": [secret]}}}),
            content_type="application/json",
        )
        self.assertEqual(without_guard.status_code, 403)
        self.assertNotIn(secret, without_guard.content.decode("utf-8"))

        invalid = self.post({
            "mineru": {"action": "replace", "accounts": [f"{secret}-{index}" for index in range(9)]},
            "minimax": {"action": "keep"},
            "siliconflow": {"action": "keep"},
        })
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertNotIn(secret, invalid.content.decode("utf-8"))
        self.assertFalse(self.path.exists())

        remote = self.client.get("/api/settings/credentials", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(remote.status_code, 403)

    def test_failed_save_preserves_old_file_and_public_environment(self):
        credential_settings.store.save_credentials({
            "mineru_tokens": ["old-mineru"], "minimax_keys": ["old-minimax"],
        })
        before = self.path.read_bytes()
        credential_settings.refresh_public_environment()
        self.assertEqual(os.environ["QB_MINIMAX_POOL_SIZE"], "1")
        secret = "new-secret-must-not-leak"
        with mock.patch.object(
            credential_settings.store,
            "save_credentials",
            side_effect=credential_settings.CredentialStoreError("无法保存当前用户的凭据。"),
        ):
            response = self.post({
                "mineru": {"action": "keep"},
                "minimax": {"action": "replace", "accounts": [secret]},
                "siliconflow": {"action": "keep"},
            })
        self.assertEqual(response.status_code, 500, response.content)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(os.environ["QB_MINIMAX_POOL_SIZE"], "1")
        self.assertNotIn(secret, response.content.decode("utf-8"))


class WorkerCredentialReloadTests(SimpleTestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.path = self.temp / "credentials.dat"

    def test_saved_change_waits_for_next_paper_boundary(self):
        environment = {
            "QB_CREDENTIAL_FILE": str(self.path),
            "QB_CREDENTIAL_HOT_RELOAD": "1",
            "MINERU_TOKEN": "startup-mineru",
            "MINIMAX_API_KEY": "startup-minimax",
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            credential_settings.store, "_transform", side_effect=identity_transform,
        ):
            credential_settings.store.save_credentials({
                "mineru_tokens": ["old-mineru"], "minimax_keys": ["old-minimax"],
            })
            first = SimpleNamespace(display_name="甲", get_status_display=lambda: "等待")
            second = SimpleNamespace(display_name="乙", get_status_display=lambda: "等待")
            first_query = mock.Mock()
            first_query.order_by.return_value = [first, second]
            empty_query = mock.Mock()
            empty_query.order_by.return_value = []
            observations: list[tuple[str, str, str]] = []

            def process(paper):
                observations.append((
                    paper.display_name,
                    os.environ["MINERU_TOKEN"],
                    os.environ["MINIMAX_API_KEY"],
                ))
                if paper is first:
                    credential_settings.store.save_credentials({
                        "mineru_tokens": ["new-mineru"], "minimax_keys": ["new-minimax"],
                    })
                    # Saving cannot mutate an active task's worker snapshot.
                    self.assertEqual(os.environ["MINIMAX_API_KEY"], "old-minimax")

            with mock.patch.object(run_worker, "SingleInstance"), \
                    mock.patch.object(run_worker.Paper.objects, "filter", side_effect=[first_query, empty_query]), \
                    mock.patch.object(run_worker, "process_paper", side_effect=process), \
                    mock.patch.object(run_worker, "rereads_pending", return_value=False), \
                    mock.patch.object(run_worker, "process_rereads", return_value=0):
                command = run_worker.Command()
                command.stdout = mock.Mock()
                command.handle(once=True)

        self.assertEqual(observations, [
            ("甲", "old-mineru", "old-minimax"),
            ("乙", "new-mineru", "new-minimax"),
        ])

    def test_failed_reload_keeps_last_known_good_environment_and_logs_no_value(self):
        environment = {
            "QB_CREDENTIAL_HOT_RELOAD": "1",
            "MINERU_TOKEN": "old-secret-value",
            "MINERU_TOKENS_JSON": '["old-secret-value"]',
        }
        with mock.patch.dict(os.environ, environment, clear=True), mock.patch.object(
            credential_settings.store,
            "load_credentials",
            side_effect=credential_settings.CredentialStoreError("已保存的凭据无法读取。"),
        ), self.assertLogs("core", level="ERROR") as captured:
            self.assertIsNone(run_worker.apply_saved_credentials())
            self.assertEqual(os.environ.get("MINERU_TOKEN"), "old-secret-value")
            self.assertEqual(os.environ.get("MINERU_TOKENS_JSON"), '["old-secret-value"]')
            self.assertNotIn("old-secret-value", "\n".join(captured.output))

    def test_task_boundary_resets_pool_without_desktop_hot_reload(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(
                    credential_settings, "apply_worker_environment", return_value=None,
                ) as reload_credentials, \
                mock.patch.object(run_worker, "reset_account_pools") as reset:
            self.assertIsNone(run_worker.apply_saved_credentials())

        reload_credentials.assert_called_once_with()
        reset.assert_called_once_with()


@unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
class CredentialApiDpapiTests(SimpleTestCase):
    def test_real_api_write_is_dpapi_protected_and_never_echoed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.dat"
            secret = "api-route-private-value-7Qz"
            with mock.patch.dict(os.environ, {
                "QB_CREDENTIAL_FILE": str(path),
                "QB_CREDENTIAL_HOT_RELOAD": "1",
            }), mock.patch.object(
                credential_settings, "_mineru_token_validity", return_value=True,
            ):
                response = Client().post(
                    "/api/settings/credentials",
                    data=json.dumps({"services": {
                        "mineru": {"action": "replace", "accounts": [secret]},
                        "minimax": {"action": "keep"},
                        "siliconflow": {"action": "keep"},
                    }}),
                    content_type="application/json",
                    HTTP_X_QB_REQUEST="1",
                )
                loaded = credential_settings.store.load_credentials()

            self.assertEqual(response.status_code, 200, response.content)
            self.assertNotIn(secret, response.content.decode("utf-8"))
            self.assertNotIn(secret.encode("utf-8"), path.read_bytes())
            self.assertEqual(
                credential_settings.store.credential_pool(loaded, "mineru"), [secret],
            )
