"""Account-pool concurrency and vision-provider failover tests."""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from django.test import SimpleTestCase

from . import account_pool, readers


class _Response:
    def __init__(self, status: int, *, text: str = "ok", headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self.content = b"{}"
        self._text = text

    def json(self):
        return {"choices": [{"message": {"content": self._text}, "finish_reason": "stop"}]}


class AccountPoolTests(SimpleTestCase):
    def tearDown(self):
        account_pool.reset_account_pools()

    def test_json_pool_is_ordered_deduplicated_and_legacy_compatible(self):
        with mock.patch.dict("os.environ", {
            "MINERU_TOKENS_JSON": json.dumps(["Bearer first", "second", "first"]),
            "MINERU_TOKEN": "legacy",
        }, clear=False):
            self.assertEqual(account_pool.secrets_from_environment("mineru"), ("first", "second"))
        with mock.patch.dict("os.environ", {
            "MINERU_TOKENS_JSON": "", "MINERU_TOKEN": "Bearer legacy",
        }, clear=False):
            self.assertEqual(account_pool.secrets_from_environment("mineru"), ("legacy",))

    def test_invalid_or_oversized_pool_fails_without_echoing_values(self):
        secret = "never-print-this"
        with mock.patch.dict("os.environ", {"MINIMAX_API_KEYS_JSON": "not-json"}, clear=False):
            with self.assertRaises(account_pool.AccountPoolError) as raised:
                account_pool.secrets_from_environment("minimax")
        self.assertNotIn("not-json", str(raised.exception))
        with mock.patch.dict("os.environ", {
            "MINIMAX_API_KEYS_JSON": json.dumps([f"{secret}-{index}" for index in range(9)]),
        }, clear=False):
            with self.assertRaises(account_pool.AccountPoolError) as raised:
                account_pool.secrets_from_environment("minimax")
        self.assertNotIn(secret, str(raised.exception))

    def test_each_account_has_one_lease_and_three_accounts_run_in_parallel(self):
        keys = ("k1", "k2", "k3")
        pool = account_pool.AccountPool("test", keys)
        lock = threading.Lock()
        active = {key: 0 for key in keys}
        maxima = {key: 0 for key in keys}
        global_active = 0
        global_max = 0

        def task():
            nonlocal global_active, global_max
            with pool.lease() as lease:
                with lock:
                    active[lease.secret] += 1
                    maxima[lease.secret] = max(maxima[lease.secret], active[lease.secret])
                    global_active += 1
                    global_max = max(global_max, global_active)
                time.sleep(0.03)
                with lock:
                    active[lease.secret] -= 1
                    global_active -= 1

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda _index: task(), range(6)))
        self.assertEqual(maxima, {"k1": 1, "k2": 1, "k3": 1})
        self.assertEqual(global_max, 3)

    def test_disabled_account_is_not_reissued(self):
        pool = account_pool.AccountPool("test", ("bad", "good"))
        with pool.lease() as lease:
            self.assertEqual(lease.secret, "bad")
            lease.disable()
        with pool.lease() as lease:
            self.assertEqual(lease.secret, "good")
        self.assertEqual(pool.enabled_size, 1)

    def test_one_request_can_exclude_an_account_already_tried(self):
        pool = account_pool.AccountPool("test", ("first", "second"))
        with pool.lease() as first:
            first_slot = first.slot
            self.assertEqual(first.secret, "first")
        with pool.lease(exclude={first_slot}) as second:
            self.assertEqual(second.secret, "second")
        with self.assertRaises(account_pool.AccountPoolError):
            with pool.lease(exclude={0, 1}):
                pass


class VisionPoolTests(SimpleTestCase):
    def tearDown(self):
        account_pool.reset_account_pools()

    def test_auth_failure_disables_one_key_and_retries_with_the_next(self):
        seen = []

        def post(_url, key, _payload, timeout=(10, 150)):
            seen.append(key)
            return _Response(401 if key == "bad-key" else 200, text="【题干】有效结果")

        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps(["bad-key", "good-key"]),
            "MINIMAX_API_KEY": "bad-key",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post):
            result = readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])
        self.assertEqual(result, "【题干】有效结果")
        self.assertEqual(seen, ["bad-key", "good-key"])

    def test_all_invalid_keys_return_safe_error(self):
        environment = {
            "SILICONFLOW_API_KEYS_JSON": json.dumps(["private-one", "private-two"]),
            "SILICONFLOW_API_KEY": "private-one",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", return_value=_Response(403)):
            with self.assertRaises(readers.ReaderError) as raised:
                readers.chat(readers.Engine("siliconflow", readers.SILICONFLOW_MODEL), "p", [])
        message = str(raised.exception)
        self.assertIn("账号池", message)
        self.assertNotIn("private-one", message)
        self.assertNotIn("private-two", message)

    def test_rate_limited_key_is_immediately_replaced(self):
        seen = []

        def post(_url, key, _payload, timeout=(10, 150)):
            seen.append(key)
            return _Response(429, headers={"Retry-After": "60"}) if key == "busy-key" else \
                _Response(200, text="【题干】有效结果")

        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps(["busy-key", "good-key"]),
            "MINIMAX_API_KEY": "busy-key",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post):
            result = readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])
        self.assertEqual(result, "【题干】有效结果")
        self.assertEqual(seen, ["busy-key", "good-key"])

    def test_http_429_is_not_retried_while_holding_one_key(self):
        response = _Response(429)
        with mock.patch("requests.post", return_value=response) as post, \
                mock.patch("time.sleep") as sleep:
            actual = readers._post("https://example.invalid", "hidden", {})
        self.assertIs(actual, response)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_parallel_limit_is_always_safe(self):
        for value, expected in (("0", 1), ("99", 8), ("bad", 4), ("3", 3)):
            with self.subTest(value=value), mock.patch.dict("os.environ", {"QB_PARALLEL": value}):
                self.assertEqual(readers._parallel_limit(), expected)

    def test_retry_after_parser_rejects_unicode_digits_and_caps_seconds(self):
        self.assertEqual(readers._retry_after_seconds("²"), readers.BACKOFF[-1])
        self.assertEqual(readers._retry_after_seconds("garbage"), readers.BACKOFF[-1])
        self.assertEqual(readers._retry_after_seconds("120"), 60.0)
        self.assertEqual(readers._retry_after_seconds("1.5"), 1.5)
