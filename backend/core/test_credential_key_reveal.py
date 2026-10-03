"""Local pool viewing tests: synthetic encrypted files only, no network."""
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.test import SimpleTestCase, override_settings

from . import credential_settings
from .test_library_ai_settings import protected_test_bytes


class BrowserInteractionRouteTests(SimpleTestCase):
    def test_interaction_script_is_served_as_javascript_and_never_accepts_post(self):
        response = self.client.get("/browser-interactions.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/javascript", response["Content-Type"])
        body = b"".join(response.streaming_content)
        self.assertIn(b"contextmenu", body)
        self.assertIn(b"keepTextMenu", body)
        self.assertEqual(self.client.post("/browser-interactions.js").status_code, 405)


class CredentialKeyRevealTests(SimpleTestCase):
    url = "/api/settings/credentials/key/reveal"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "credentials.dat"
        environment = {
            "QB_CREDENTIAL_FILE": str(self.path), "QB_MODEL_PREFERENCES_FILE": str(self.root / "models.json"),
            "MINIMAX_API_KEY": "synthetic-environment-key-must-not-be-viewed",
        }
        for service in credential_settings.SERVICES:
            environment[f"QB_{service.upper()}_CONFIGURED"] = "0"
            environment[f"QB_{service.upper()}_POOL_SIZE"] = "0"
        env = mock.patch.dict(os.environ, environment)
        env.start(); self.addCleanup(env.stop)
        cipher = mock.patch.object(credential_settings.store, "_transform", side_effect=protected_test_bytes)
        self.cipher = cipher.start(); self.addCleanup(cipher.stop)
        self.secrets = {
            service: [f"synthetic-{service}-key-{index}" for index in range(credential_settings.MAX_ACCOUNT_POOL_SIZE)]
            for service in credential_settings.SERVICES
        }
        values = {credential_settings.store.ACCOUNT_POOL_FIELDS[service][1]: keys for service, keys in self.secrets.items()}
        credential_settings.store.save_credentials(values)
        credential_settings.apply_public_environment(credential_settings.public_status(values))
        (self.root / "models.json").write_text('{"roles":{"primary_engine":"modelscope_qwen"}}', encoding="utf-8")
        self.cipher.reset_mock()
        reset = mock.patch.object(credential_settings, "reset_account_pools")
        self.reset = reset.start(); self.addCleanup(reset.stop)
        network = mock.patch("requests.sessions.Session.request", side_effect=AssertionError("credential viewing must stay offline"))
        self.network = network.start(); self.addCleanup(network.stop)
        urllib_network = mock.patch("urllib.request.urlopen", side_effect=AssertionError("credential viewing must stay offline"))
        self.urllib_network = urllib_network.start(); self.addCleanup(urllib_network.stop)
        self.files = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        self.environment = dict(os.environ)

    def post(self, payload=None, **headers):
        options = {"HTTP_HOST": "127.0.0.1:8768", "HTTP_ORIGIN": "http://127.0.0.1:8768",
                   "HTTP_X_QB_REQUEST": "1", "HTTP_SEC_FETCH_SITE": "same-origin"}
        options.update(headers)
        return self.client.post(self.url, json.dumps(payload if payload is not None else {"service": "minimax", "index": 0}),
                                content_type="application/json", **options)

    def unchanged(self):
        self.assertEqual(self.files, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()})
        self.assertEqual(self.environment, dict(os.environ))
        self.reset.assert_not_called()
        self.network.assert_not_called()
        self.urllib_network.assert_not_called()

    def test_each_service_first_middle_and_last_saved_key_without_pool_changes(self):
        for service, keys in self.secrets.items():
            for index in (0, 3, 7):
                with self.subTest(service=service, index=index):
                    response = self.post({"service": service, "index": index})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"service": service, "index": index, "key": keys[index]})
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertIn("private", response["Cache-Control"])
                    for other in set(sum(self.secrets.values(), [])) - {keys[index]}:
                        self.assertNotIn('"' + other + '"', response.content.decode())
        self.assertEqual(self.cipher.call_count, 12)
        self.assertTrue(all(call.kwargs == {"protect": False} for call in self.cipher.call_args_list))
        self.unchanged()

    def test_get_head_put_delete_and_metadata_never_decrypt_or_expose_secrets(self):
        for method in ("get", "head", "put", "delete"):
            response = getattr(self.client, method)(self.url)
            self.assertEqual(response.status_code, 405)
            self.assertIn("no-store", response["Cache-Control"])
        status = self.client.get("/api/settings/credentials").json()
        self.assertEqual(status, {"services": {service: {"configured": True, "count": 8} for service in self.secrets}, "max_accounts": 8})
        for keys in self.secrets.values():
            for key in keys:
                self.assertNotIn(key, json.dumps(status))
        self.cipher.assert_not_called()
        self.unchanged()

    def test_nonlocal_missing_cross_origin_fetch_site_and_request_header_refuse_before_read(self):
        for headers in ({"REMOTE_ADDR": "192.0.2.10"}, {"REMOTE_ADDR": ""}, {"HTTP_ORIGIN": ""},
                        {"HTTP_ORIGIN": "https://other.example"}, {"HTTP_ORIGIN": "http://127.0.0.1:8778"},
                        {"HTTP_SEC_FETCH_SITE": "cross-site"}, {"HTTP_SEC_FETCH_SITE": "same-site"},
                        {"HTTP_X_QB_REQUEST": ""}, {"HTTP_HOST": "localhost:8768"}):
            with self.subTest(headers=headers):
                response = self.post(**headers)
                self.assertEqual(response.status_code, 403)
                self.assertIn("no-store", response["Cache-Control"])
        self.cipher.assert_not_called()
        self.unchanged()

    @override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "[::1]", "testserver"])
    def test_matching_localhost_and_ipv6_hosts_are_allowed(self):
        for host, remote in (("localhost:8768", "127.0.0.1"), ("[::1]:8768", "::1")):
            with self.subTest(host=host):
                response = self.post(HTTP_HOST=host, HTTP_ORIGIN="http://" + host, REMOTE_ADDR=remote)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["key"], self.secrets["minimax"][0])
        self.unchanged()

    def test_untrusted_or_invalid_host_never_reveals_even_when_origin_matches(self):
        with override_settings(ALLOWED_HOSTS=["*"]):
            for host in ("testserver", "127.0.0.1.example.com", "example.com"):
                with self.subTest(host=host):
                    response = self.post(HTTP_HOST=host, HTTP_ORIGIN="http://" + host)
                    self.assertEqual(response.status_code, 403)
        # Invalid HTTP Host syntax is rejected by Django's request middleware
        # before the view and its never-cache decorator are reached.
        for host in ("user@localhost", "localhost:bad"):
            response = self.post(HTTP_HOST=host, HTTP_ORIGIN="http://" + host)
            self.assertEqual(response.status_code, 400)
        self.cipher.assert_not_called()
        self.unchanged()

    def test_bad_service_index_shape_or_extra_fields_never_read_or_echo_values(self):
        bad = ({}, {"service": "minimax"}, {"service": "minimax", "index": 0, "key": "synthetic-never-echo"},
               {"service": "../credentials.dat", "index": 0}, {"service": [], "index": 0},
               {"service": "minimax", "index": True}, {"service": "minimax", "index": "0"},
               {"service": "minimax", "index": 0.0}, {"service": "minimax", "index": -1},
               {"service": "minimax", "index": 8}, {"service": "minimax", "index": None},
               {"service": "minimax", "index": []})
        for payload in bad:
            with self.subTest(payload=payload):
                response = self.post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("synthetic-never-echo", response.content.decode())
                self.assertIn("private", response["Cache-Control"])
        self.cipher.assert_not_called()
        self.unchanged()

    def test_invalid_json_large_body_and_nonjson_requests_do_not_decrypt(self):
        headers = {"HTTP_HOST": "127.0.0.1:8768", "HTTP_ORIGIN": "http://127.0.0.1:8768", "HTTP_X_QB_REQUEST": "1"}
        for value in ("[1,2]", "{broken", json.dumps({"service": "x" * 1100, "index": 0})):
            response = self.client.post(self.url, value, content_type="application/json", **headers)
            self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url, "service=minimax&index=0", content_type="application/x-www-form-urlencoded", **headers)
        self.assertEqual(response.status_code, 415)
        self.cipher.assert_not_called()
        self.unchanged()

    def test_missing_saved_key_does_not_fall_back_to_worker_environment(self):
        self.path.unlink()
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("key", response.json())
        self.assertNotIn(os.environ["MINIMAX_API_KEY"], response.content.decode())
        self.cipher.assert_not_called()

    def test_removed_second_row_and_corrupt_store_return_only_safe_uncached_error(self):
        credential_settings.store.save_credentials({"minimax_keys": [self.secrets["minimax"][0]]})
        response = self.post({"service": "minimax", "index": 1})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("key", response.json())
        self.path.write_bytes(b"corrupt-synthetic-must-not-echo")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("corrupt", response.content.decode())
        self.assertNotIn("key", response.json())
        self.assertIn("no-store", response["Cache-Control"])
        self.network.assert_not_called()

    def test_legacy_singular_key_is_revealed_without_migrating_or_rewriting_file(self):
        legacy = json.dumps({"version": 1, "minimax_key": "synthetic-legacy-key"}).encode()
        self.path.write_bytes(protected_test_bytes(legacy, protect=True))
        before = self.path.read_bytes()
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["key"], "synthetic-legacy-key")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.post({"service": "minimax", "index": 1}).status_code, 409)

    def test_guard_errors_cannot_trigger_store_read(self):
        with mock.patch.object(credential_settings.store, "load_credentials", side_effect=AssertionError("must not read")):
            self.assertEqual(self.post(REMOTE_ADDR="198.51.100.1").status_code, 403)
            self.assertEqual(self.post({"service": "minimax", "index": False}).status_code, 400)
            self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_decryption_error_never_returns_underlying_exception_or_key(self):
        self.cipher.side_effect = credential_settings.CredentialStoreError("synthetic-secret-in-low-level-error")
        response = self.post()
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("synthetic-secret", response.content.decode())
        self.assertNotIn("key", response.json())
        self.unchanged()
