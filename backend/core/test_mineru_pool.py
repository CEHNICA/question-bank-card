"""Safe MinerU account-pool failover tests; no network is used."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import account_pool, mineru


class MineruPoolTests(SimpleTestCase):
    def tearDown(self):
        account_pool.reset_account_pools()

    def test_invalid_account_is_disabled_and_next_account_is_used(self):
        seen = []

        def extract(token, _source, target, _page_count, *, heartbeat=None):
            seen.append(token)
            if token == "bad-token":
                raise mineru.MineruError("safe", category="A0202")
            return target

        environment = {
            "MINERU_TOKENS_JSON": json.dumps(["bad-token", "good-token"]),
            "MINERU_TOKEN": "bad-token",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(mineru, "request_extract_file", side_effect=extract):
            result = mineru.request_extract_file_from_pool(
                Path("source.pdf"), Path("result.zip"), 1,
            )
        self.assertEqual(result, Path("result.zip"))
        self.assertEqual(seen, ["bad-token", "good-token"])

    def test_rate_limited_account_is_cooled_and_next_account_is_used(self):
        seen = []

        def extract(token, _source, target, _page_count, *, heartbeat=None):
            seen.append(token)
            if token == "busy-token":
                raise mineru.MineruError("safe", http_status=429)
            return target

        with mock.patch.dict("os.environ", {
            "MINERU_TOKENS_JSON": json.dumps(["busy-token", "good-token"]),
        }, clear=False), mock.patch.object(mineru, "request_extract_file", side_effect=extract):
            result = mineru.request_extract_file_from_pool(
                Path("source.pdf"), Path("result.zip"), 1,
            )
        self.assertEqual(result, Path("result.zip"))
        self.assertEqual(seen, ["busy-token", "good-token"])

    def test_all_invalid_accounts_fail_without_echoing_any_token(self):
        private_tokens = ["private-token-one", "private-token-two"]

        def extract(_token, _source, _target, _page_count, *, heartbeat=None):
            raise mineru.MineruError("Token 无效", http_status=401, category="A0202")

        with mock.patch.dict("os.environ", {
            "MINERU_TOKENS_JSON": json.dumps(private_tokens),
        }, clear=False), mock.patch.object(mineru, "request_extract_file", side_effect=extract):
            with self.assertRaises(mineru.MineruError) as raised:
                mineru.request_extract_file_from_pool(
                    Path("source.pdf"), Path("result.zip"), 1,
                )
        self.assertTrue(raised.exception.account_unusable)
        self.assertEqual(raised.exception.category, "A0202")
        for token in private_tokens:
            self.assertNotIn(token, str(raised.exception))

    def test_unknown_code_cannot_hide_recognised_invalid_token_category(self):
        error = mineru._mineru_error("解析", {
            "code": "A9999",
            "err_msg": "Invalid token",
            "trace_id": "a" * 32,
        })
        self.assertEqual(error.code, "A9999")
        self.assertEqual(error.category, "A0202")
        self.assertTrue(error.account_unusable)
        self.assertEqual(error.trace_id, "a" * 32)
        self.assertNotIn("Invalid token", str(error))

    def test_invalid_pool_configuration_is_safe_and_not_called_unconfigured(self):
        raw = "not-json-with-private-value"
        with mock.patch.dict("os.environ", {"MINERU_TOKENS_JSON": raw}, clear=False):
            with self.assertRaises(mineru.MineruError) as raised:
                mineru.request_extract_file_from_pool(
                    Path("source.pdf"), Path("result.zip"), 1,
                )
        self.assertIn("配置", str(raised.exception))
        self.assertNotIn(raw, str(raised.exception))
