"""Explicit local credential viewing uses only synthetic offline credentials."""
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.test import TestCase

from . import library_ai_settings as service
from .test_library_ai_settings import protected_test_bytes


class APIKeyRevealTests(TestCase):
    # 1.13.4：密钥读取前会先拉一次设置，设置里带着「题库里还差几道」，那是一次读库。
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.root / "settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.root / "keys.dat"),
            "QB_FEATURES_FILE": str(self.root / "features.json"),
            "QB_CREDENTIAL_FILE": str(self.root / "ocr-credentials.dat"),
        })
        environment.start(); self.addCleanup(environment.stop)
        transform = mock.patch.object(service.credential_settings.store, "_transform", side_effect=protected_test_bytes)
        self.transform = transform.start(); self.addCleanup(transform.stop)
        network = mock.patch.object(service.requests, "post", side_effect=AssertionError("no cloud calls while viewing"))
        self.network = network.start(); self.addCleanup(network.stop)
        service.save({"provider": "minimax", "key": {"action": "replace", "value": "synthetic-eye-key-only"}})
        self.transform.reset_mock()
        self.url = "/api/settings/library-ai/key/reveal"
        self.snapshot = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}

    def post(self, payload=None, **headers):
        options = {"HTTP_HOST": "127.0.0.1:8768", "HTTP_ORIGIN": "http://127.0.0.1:8768", "HTTP_X_QB_REQUEST": "1", "HTTP_SEC_FETCH_SITE": "same-origin"}
        options.update(headers)
        return self.client.post(self.url, json.dumps(payload if payload is not None else {"provider": "minimax"}), content_type="application/json", **options)

    def test_only_explicit_same_origin_post_reveals_without_modifying_settings(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"provider": "minimax", "key": "synthetic-eye-key-only"})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.assertEqual(self.snapshot, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        self.transform.assert_called_once()
        self.network.assert_not_called()

    def test_status_and_get_never_decrypt_or_return_key(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        status = self.client.get("/api/settings/library-ai").json()
        self.assertNotIn("key", status)
        self.assertNotIn("synthetic-eye-key-only", json.dumps(status))
        self.transform.assert_not_called()

    def test_missing_or_cross_site_origin_and_request_header_never_decrypt(self):
        for headers in ({"HTTP_ORIGIN": ""}, {"HTTP_ORIGIN": "https://other.example"},
                        {"HTTP_SEC_FETCH_SITE": "cross-site"}, {"HTTP_X_QB_REQUEST": ""},
                        {"HTTP_HOST": "localhost:8768"}, {"HTTP_ORIGIN": "http://127.0.0.1:8770"},
                        {"REMOTE_ADDR": "192.0.2.10"}, {"REMOTE_ADDR": ""}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(**headers).status_code, 403)
        self.transform.assert_not_called()

    def test_explicit_index_zero_returns_the_selected_single_key(self):
        response = self.post({"provider": "minimax", "index": 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"provider": "minimax", "index": 0, "key": "synthetic-eye-key-only"})
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.snapshot, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        self.network.assert_not_called()

    def test_each_saved_provider_can_be_viewed_without_switching_or_retesting(self):
        saved = {"minimax": "synthetic-eye-key-only", "deepseek": "synthetic-deepseek-eye-only",
                 "doubao": "synthetic-doubao-eye-only", "custom": "synthetic-custom-eye-only"}
        service.save({"provider": "deepseek", "key": {"action": "replace", "value": saved["deepseek"]}})
        service.save({"provider": "doubao", "model": "ep-offline-eye", "key": {"action": "replace", "value": saved["doubao"]}})
        service.save({"provider": "custom", "model": "offline-model", "base_url": "http://localhost:8778/v1",
                      "key": {"action": "replace", "value": saved["custom"]}})
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        self.transform.reset_mock()
        for provider, key in saved.items():
            with self.subTest(provider=provider):
                response = self.post({"provider": provider, "index": 0})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"provider": provider, "index": 0, "key": key})
        self.assertEqual(service.public_status()["provider"], "custom")
        self.assertFalse(service.public_status()["api_ready"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})
        self.assertEqual(self.transform.call_count, 4)
        self.network.assert_not_called()

    def test_invalid_provider_or_index_is_rejected_before_read(self):
        for payload in ({"provider": []}, {"provider": "../keys.dat"},
                        {"provider": "minimax", "index": True}, {"provider": "minimax", "index": "0"},
                        {"provider": "minimax", "index": -1}, {"provider": "minimax", "index": 1},
                        {"provider": "minimax", "index": 0.0}, {"provider": "minimax", "index": None}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code, 400)
        self.transform.assert_not_called()

    def test_remote_peer_refused_even_with_local_matching_origin(self):
        response = self.post(REMOTE_ADDR="203.0.113.8")
        self.assertEqual(response.status_code, 403)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])
        self.transform.assert_not_called()

    def test_missing_file_metadata_and_view_do_not_import_ocr_key(self):
        service.key_path("minimax").unlink()
        (self.root / "ocr-credentials.dat").write_bytes(b"synthetic-opaque-ocr-file-must-not-be-read")
        self.assertEqual(service.public_status()["keys"]["minimax"],
            {"configured": False, "count": 0, "shared_with_reading": False})
        self.assertEqual(self.post({"provider": "minimax", "index": 0}).status_code, 409)
        self.assertEqual((self.root / "ocr-credentials.dat").read_bytes(), b"synthetic-opaque-ocr-file-must-not-be-read")
        self.network.assert_not_called()

    def test_mismatched_provider_unknown_fields_and_large_payload_refuse(self):
        self.assertEqual(self.post({"provider": "deepseek"}).status_code, 409)
        self.assertEqual(self.post({"provider": "minimax", "key": "ignore"}).status_code, 400)
        self.assertEqual(self.post({"provider": "a" * 1100}).status_code, 400)
        self.assertEqual(self.post({}).status_code, 400)
        self.transform.assert_not_called()

    def test_cleared_and_corrupt_keys_are_not_exposed(self):
        service.key_path().write_bytes(b"damaged synthetic credential")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("key", response.json())
        self.assertNotIn("damaged", response.content.decode())
        service.save({"key": {"action": "clear"}})
        self.transform.reset_mock()
        self.assertEqual(self.post().status_code, 409)
        self.transform.assert_not_called()
