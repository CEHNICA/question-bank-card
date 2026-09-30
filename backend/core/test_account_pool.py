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


def _token_plan_response(message: str = "Token Plan exhausted (2056)") -> _Response:
    response = _Response(429)
    response.content = b'{"error":true}'
    response.json = mock.Mock(return_value={
        "type": "error",
        "request_id": "opaque-request-id",
        "error": {
            "http_code": "429",
            "type": "rate_limit_error",
            "message": message,
        },
    })
    return response


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


class AccountConcurrencyTests(SimpleTestCase):
    def tearDown(self):
        account_pool.reset_account_pools()

    def test_one_account_can_carry_its_configured_number_of_leases(self):
        pool = account_pool.AccountPool("test", ("only",), per_account=3)
        lock = threading.Lock()
        active = 0
        peak = 0

        def task():
            nonlocal active, peak
            with pool.lease():
                with lock:
                    active += 1
                    peak = max(peak, active)
                time.sleep(0.03)
                with lock:
                    active -= 1

        with ThreadPoolExecutor(max_workers=6) as executor:
            list(executor.map(lambda _index: task(), range(6)))
        self.assertEqual(peak, 3)
        self.assertEqual(pool.capacity, 3)

    def test_leases_spread_to_the_least_loaded_account_first(self):
        pool = account_pool.AccountPool("test", ("a", "b"), per_account=2)
        with pool.lease() as first, pool.lease() as second:
            self.assertNotEqual(first.secret, second.secret)
            with pool.lease() as third:
                self.assertIn(third.secret, {"a", "b"})

    def test_rate_limit_removes_one_parallel_slot_until_requests_succeed_again(self):
        pool = account_pool.AccountPool("test", ("only",), per_account=3)
        with pool.lease() as lease:
            lease.cooldown(0)
        self.assertEqual(pool.capacity, 2)
        for _ in range(3):
            with pool.lease() as lease:
                lease.cooldown(0)
        self.assertEqual(pool.capacity, 1)
        # A burst limit passes: clean requests give the slots back, one at a time.
        for _ in range(account_pool.recover_after(1) - 1):
            with pool.lease():
                pass
        self.assertEqual(pool.capacity, 1)
        with pool.lease():
            pass
        self.assertEqual(pool.capacity, 2)
        for _ in range(3 * account_pool.recover_after(3)):
            with pool.lease():
                pass
        self.assertEqual(pool.capacity, 3)          # never above the configured limit
        with pool.lease() as lease:
            lease.cooldown(0)
        self.assertEqual(pool.capacity, 2)
        self.assertEqual(pool.spare, 2)

    def test_one_burst_of_429s_removes_one_slot_not_all_of_them(self):
        # Eight requests in flight hit the limit together (measured on MiniMax).
        pool = account_pool.AccountPool("test", ("only",), per_account=8)
        leases = [pool._acquire() for _ in range(8)]
        for lease in leases:
            lease.cooldown(0)
            pool._release(lease)
        self.assertEqual(pool.capacity, 7)
        # A request sent after that cut can cut again.
        with pool.lease() as lease:
            lease.cooldown(0)
        self.assertEqual(pool.capacity, 6)

    def test_recovery_takes_about_the_same_time_at_any_level(self):
        self.assertLess(account_pool.recover_after(1), account_pool.recover_after(6))
        self.assertEqual(account_pool.recover_after(1) * 6, account_pool.recover_after(6))

    def test_configured_concurrency_is_validated_and_clamped(self):
        cases = (("", 6), ("2", 2), ("0", 1), ("99", 8), ("x", 6))
        for raw, expected in cases:
            with self.subTest(raw=raw), mock.patch.dict(
                    "os.environ", {"QB_MINIMAX_ACCOUNT_CONCURRENCY": raw}):
                self.assertEqual(account_pool.account_concurrency("minimax"), expected)
        with mock.patch.dict("os.environ", {"QB_MINERU_ACCOUNT_CONCURRENCY": ""}):
            self.assertEqual(account_pool.account_concurrency("mineru"), 1)

    def test_shared_pool_uses_service_concurrency(self):
        with mock.patch.dict("os.environ", {
            "MINIMAX_API_KEYS_JSON": json.dumps(["k1", "k2"]),
            "QB_MINIMAX_ACCOUNT_CONCURRENCY": "3",
        }):
            self.assertEqual(account_pool.account_pool("minimax").capacity, 6)


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

    def test_exact_selected_model_id_reaches_provider_payload(self):
        payloads = []

        def post(_url, _key, payload, timeout=(10, 150)):
            payloads.append(payload)
            return _Response(200, text="OK")

        environment = {"MINIMAX_API_KEY": "test-key"}
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post):
            result = readers.chat(
                readers.Engine("minimax", "MiniMax-Custom-Vision"), "p", [], max_tokens=32,
            )

        self.assertEqual(result, "OK")
        self.assertEqual(payloads[0]["model"], "MiniMax-Custom-Vision")

    def test_exact_siliconflow_model_id_reaches_provider_payload(self):
        payloads = []

        def post(_url, _key, payload, timeout=(10, 150)):
            payloads.append(payload)
            return _Response(200, text="OK")

        environment = {"SILICONFLOW_API_KEY": "test-key"}
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post):
            result = readers.chat(
                readers.Engine("siliconflow", "Qwen/Custom-VL"), "p", [], max_tokens=32,
            )

        self.assertEqual(result, "OK")
        self.assertEqual(payloads[0]["model"], "Qwen/Custom-VL")

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

    def test_single_rate_limited_key_is_released_then_retried(self):
        secret = "single-private-key"
        responses = [
            _Response(429, headers={"Retry-After": "0"}),
            _Response(200, text="稍后成功"),
        ]
        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps([secret]),
            "MINIMAX_API_KEY": secret,
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=responses) as post:
            result = readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(result, "稍后成功")
        self.assertEqual(post.call_count, 2)

    def test_minimax_token_plan_exhaustion_raises_safe_dedicated_error(self):
        secret = "never-print-this-token-plan-key"
        private_message = "private provider detail: Token Plan exhausted (2056)"
        response = _token_plan_response(private_message)
        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps([secret]),
            "MINIMAX_API_KEY": secret,
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", return_value=response) as post, \
                self.assertRaises(readers.ReaderQuotaExhausted) as raised:
            readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(post.call_count, 1)
        self.assertIn("Token Plan", str(raised.exception))
        self.assertNotIn(private_message, str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_token_plan_exhausted_account_fails_over_to_next_account(self):
        seen = []

        def post(_url, key, _payload, timeout=(10, 150)):
            seen.append(key)
            return _token_plan_response() if key == "exhausted-key" else _Response(200, text="备用账号成功")

        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps(["exhausted-key", "healthy-key"]),
            "MINIMAX_API_KEY": "exhausted-key",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post):
            result = readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(result, "备用账号成功")
        self.assertEqual(seen, ["exhausted-key", "healthy-key"])

    def test_all_token_plan_accounts_exhausted_pause_provider(self):
        seen = []

        def post(_url, key, _payload, timeout=(10, 150)):
            seen.append(key)
            return _token_plan_response()

        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps(["exhausted-one", "exhausted-two"]),
            "MINIMAX_API_KEY": "exhausted-one",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post), \
                self.assertRaises(readers.ReaderQuotaExhausted):
            readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(seen, ["exhausted-one", "exhausted-two"])

    def test_token_plan_plus_invalid_account_still_pauses_for_quota(self):
        seen = []

        def post(_url, key, _payload, timeout=(10, 150)):
            seen.append(key)
            return _token_plan_response() if key == "exhausted-key" else _Response(401)

        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps(["exhausted-key", "invalid-key"]),
            "MINIMAX_API_KEY": "exhausted-key",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post), \
                self.assertRaises(readers.ReaderQuotaExhausted):
            readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(seen, ["exhausted-key", "invalid-key"])

    def test_parallel_waiters_share_confirmed_plan_exhaustion(self):
        secret = "single-exhausted-key"
        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps([secret]),
            "MINIMAX_API_KEY": secret,
        }

        def call(_index):
            try:
                readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])
            except readers.ReaderQuotaExhausted:
                return "quota"
            return "unexpected"

        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", return_value=_token_plan_response()) as post:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(call, range(2)))

        self.assertEqual(results, ["quota", "quota"])
        self.assertEqual(post.call_count, 1)

    def test_2056_text_without_exact_error_structure_is_an_ordinary_429(self):
        secret = "single-private-key"
        malformed = _Response(429, headers={"Retry-After": "0"})
        malformed.content = b'{"error":true}'
        malformed.json = mock.Mock(return_value={
            "type": "error",
            "error": {
                "http_code": "429",
                "type": "other_error",
                "message": "not the documented structured error (2056)",
            },
        })
        responses = [malformed, _Response(200, text="普通限流后成功")]
        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps([secret]),
            "MINIMAX_API_KEY": secret,
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=responses) as post:
            result = readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(result, "普通限流后成功")
        self.assertEqual(post.call_count, 2)

    def test_persistent_rate_limit_has_bounded_safe_retries(self):
        secret = "never-show-this-rate-limit-key"
        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps([secret]),
            "MINIMAX_API_KEY": secret,
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(
                    readers, "_post", return_value=_Response(429, headers={"Retry-After": "0"}),
                ) as post, self.assertRaises(readers.ReaderError) as raised:
            readers.chat(readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [])

        self.assertEqual(post.call_count, readers.RATE_LIMIT_ROUNDS)
        self.assertIn("持续限流", str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_single_account_cooldown_does_not_wake_parallel_http_requests(self):
        secret = "single-key"
        lock = threading.Lock()
        active = 0
        maximum_active = 0
        call_count = 0

        def post(_url, _key, _payload, timeout=(10, 150)):
            nonlocal active, maximum_active, call_count
            with lock:
                active += 1
                maximum_active = max(maximum_active, active)
                call_count += 1
                current = call_count
            time.sleep(0.01)
            with lock:
                active -= 1
            if current == 1:
                return _Response(429, headers={"Retry-After": "0.01"})
            return _Response(200, text=f"成功{current}")

        environment = {
            "MINIMAX_API_KEYS_JSON": json.dumps([secret]),
            "MINIMAX_API_KEY": secret,
            # This contract is about a single-slot account: a cooldown must not
            # release a herd.  Multi-slot accounts are covered separately.
            "QB_MINIMAX_ACCOUNT_CONCURRENCY": "1",
        }
        with mock.patch.dict("os.environ", environment, clear=False), \
                mock.patch.object(readers, "_post", side_effect=post):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _index: readers.chat(
                        readers.Engine("minimax", readers.MINIMAX_MODEL), "p", [],
                    ),
                    range(2),
                ))

        self.assertEqual(len(results), 2)
        self.assertEqual(call_count, 3)
        self.assertEqual(maximum_active, 1)

    def test_http_429_is_not_retried_while_holding_one_key(self):
        response = _Response(429)
        with mock.patch("requests.post", return_value=response) as post, \
                mock.patch("time.sleep") as sleep:
            actual = readers._post("https://example.invalid", "hidden", {})
        self.assertIs(actual, response)
        post.assert_called_once()
        sleep.assert_not_called()

    def test_parallel_limit_is_always_safe(self):
        for value, expected in (("0", 1), ("99", readers.MAX_PARALLEL_CARDS), ("bad", 4), ("3", 3)):
            with self.subTest(value=value), mock.patch.dict("os.environ", {"QB_PARALLEL": value}):
                self.assertEqual(readers._parallel_limit(), expected)

    def test_retry_after_parser_rejects_unicode_digits_and_caps_seconds(self):
        self.assertEqual(readers._retry_after_seconds("²"), readers.BACKOFF[-1])
        self.assertEqual(readers._retry_after_seconds("garbage"), readers.BACKOFF[-1])
        self.assertEqual(readers._retry_after_seconds("garbage", fallback=2.0), 2.0)
        self.assertEqual(readers._retry_after_seconds("120"), 60.0)
        self.assertEqual(readers._retry_after_seconds("1.5"), 1.5)


class HedgedRequestTests(SimpleTestCase):
    engine = readers.Engine("minimax", readers.MINIMAX_MODEL)

    def test_straggler_is_answered_by_a_duplicate_request(self):
        release = threading.Event()
        calls = []

        def once(_engine, _prompt, _images, _max_tokens=3000, started=None):
            calls.append(len(calls))
            if started is not None:
                started.set()
            if len(calls) == 1:
                release.wait(5)
                return "slow"
            return "fast"

        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "0.05"}), \
                mock.patch.object(readers, "_has_spare_slot", return_value=True), \
                mock.patch.object(readers, "_chat_once", side_effect=once):
            self.assertEqual(readers.chat(self.engine, "p", []), "fast")
        release.set()
        self.assertEqual(len(calls), 2)

    def test_a_request_still_waiting_for_an_account_slot_is_not_duplicated(self):
        calls = []

        def once(_engine, _prompt, _images, _max_tokens=3000, started=None):
            calls.append(1)
            time.sleep(0.3)          # queued behind other cards' requests
            if started is not None:
                started.set()
            return "answer"

        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "0.05"}), \
                mock.patch.object(readers, "_has_spare_slot", return_value=True), \
                mock.patch.object(readers, "_chat_once", side_effect=once):
            self.assertEqual(readers.chat(self.engine, "p", []), "answer")
        self.assertEqual(len(calls), 1)

    def test_no_duplicate_when_every_account_slot_is_busy(self):
        release = threading.Event()
        calls = []

        def once(_engine, _prompt, _images, _max_tokens=3000, started=None):
            calls.append(1)
            if started is not None:
                started.set()
            release.wait(0.3)
            return "only"

        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "0.05"}), \
                mock.patch.object(readers, "_has_spare_slot", return_value=False), \
                mock.patch.object(readers, "_chat_once", side_effect=once):
            self.assertEqual(readers.chat(self.engine, "p", []), "only")
        self.assertEqual(len(calls), 1)

    def test_fast_answer_sends_no_duplicate(self):
        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "5"}), \
                mock.patch.object(readers, "_chat_once", return_value="ok") as once:
            self.assertEqual(readers.chat(self.engine, "p", []), "ok")
        once.assert_called_once()

    def test_a_failed_duplicate_does_not_hide_a_late_success(self):
        release = threading.Event()

        def once(_engine, _prompt, _images, _max_tokens=3000, started=None):
            if started is not None:
                started.set()
            if not release.is_set():
                release.set()
                time.sleep(0.2)
                return "late but fine"
            raise readers.ReaderError("MiniMax 接口返回 HTTP 500")

        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "0.05"}), \
                mock.patch.object(readers, "_has_spare_slot", return_value=True), \
                mock.patch.object(readers, "_chat_once", side_effect=once):
            self.assertEqual(readers.chat(self.engine, "p", []), "late but fine")

    def test_both_failing_raises_and_zero_disables_hedging(self):
        def failing(*args, **_kwargs):
            if len(args) > 4 and args[4] is not None:
                args[4].set()
            time.sleep(0.05)
            raise readers.ReaderError("MiniMax 接口返回 HTTP 500")

        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "0.01"}), \
                mock.patch.object(readers, "_has_spare_slot", return_value=True), \
                mock.patch.object(readers, "_chat_once", side_effect=failing):
            with self.assertRaises(readers.ReaderError):
                readers.chat(self.engine, "p", [])
        with mock.patch.dict("os.environ", {"QB_HEDGE_AFTER": "0"}), \
                mock.patch.object(readers, "_chat_once", return_value="direct") as once:
            self.assertEqual(readers.chat(self.engine, "p", []), "direct")
        once.assert_called_once()
