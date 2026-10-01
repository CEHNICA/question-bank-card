"""The free reading service (魔搭) and switching to another service when one is down."""

from __future__ import annotations

import json
from unittest import mock

from django.test import SimpleTestCase

from . import account_pool, provider_catalog, readers


class _Response:
    def __init__(self, status: int, *, text: str = "【题干】有效结果"):
        self.status_code = status
        self.headers = {}
        self.content = b"{}"
        self._text = text

    def json(self):
        return {"choices": [{"message": {"content": self._text}, "finish_reason": "stop"}]}


def keys(**services):
    """Environment with the given services configured (one key each)."""
    environment = {"QB_HEDGE_AFTER": "0", "QB_PRIMARY_ENGINE": "minimax_m3", "QB_CHECKER_ENGINE": "auto",
                   "QB_ARBITER_ENGINE": "primary"}
    for service in provider_catalog.VISION:
        pool, single = provider_catalog.environment_names()[service]
        environment[pool] = json.dumps([f"{service}-key"]) if service in services else "[]"
        environment[single] = ""
        environment[f"QB_{service.upper()}_CONFIGURED"] = "1" if service in services else "0"
    return mock.patch.dict("os.environ", environment)


class FreeProviderTests(SimpleTestCase):
    def setUp(self):
        account_pool.reset_account_pools()
        self.addCleanup(account_pool.reset_account_pools)
        self.addCleanup(readers._OUTPUT_LIMITS.clear)

    def test_a_teacher_with_only_the_free_key_reads_with_it(self):
        with keys(modelscope=1):
            primary, checker = readers.primary_engine(), readers.checker_engine()
            self.assertEqual((primary.provider, primary.model), ("modelscope", "Qwen/Qwen3.5-35B-A3B"))
            self.assertEqual(checker.provider, "modelscope")      # nobody else: read twice
        with keys(modelscope=1, siliconflow=1):
            self.assertEqual(readers.primary_engine().provider, "modelscope")
            self.assertEqual(readers.checker_engine().provider, "siliconflow")
        with keys(minimax=1, modelscope=1):
            # A paying user keeps MiniMax first; the free service checks.
            self.assertEqual(readers.primary_engine().provider, "minimax")
            self.assertEqual(readers.checker_engine().provider, "modelscope")
        self.assertNotIn("zhipu", provider_catalog.VISION)

    def test_requests_switch_reasoning_off(self):
        sent = []

        def post(url, key, payload, timeout=(10, 150)):
            sent.append((url, key, payload))
            return _Response(200)

        with keys(modelscope=1), mock.patch.object(readers, "_post", side_effect=post):
            readers.chat(readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B"), "读题", ["data:image/jpeg;base64,QUJD"])
        (url, key, payload), = sent
        self.assertEqual((url, key), (readers.MODELSCOPE_URL, "modelscope-key"))
        self.assertIs(payload["enable_thinking"], False)
        self.assertEqual(payload["messages"][0]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,QUJD")

    def test_a_rejected_request_is_retried_plainer(self):
        sent = []
        answers = [_Response(400), _Response(200, text="好")]

        def post(url, key, payload, timeout=(10, 150)):
            sent.append(payload)
            return answers.pop(0)

        with keys(modelscope=1), mock.patch.object(readers, "_post", side_effect=post):
            text = readers.chat(readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B"), "读题", ["data:image/png;base64,QUJD"])
        self.assertEqual(text, "好")
        self.assertIn("enable_thinking", sent[0])
        self.assertNotIn("enable_thinking", sent[1])

    def test_a_model_with_a_smaller_output_limit_is_asked_within_it(self):
        sent = []
        limited = _Response(400)
        limited.text = '{"error":{"code":"1210","message":"max_tokens参数非法：限制数值范围[1,1024]"}}'
        answers = [limited, _Response(200, text="好")]

        def post(url, key, payload, timeout=(10, 150)):
            sent.append(payload)
            return answers.pop(0)

        engine = readers.Engine("modelscope", "small-model")
        with keys(modelscope=1), mock.patch.object(readers, "_post", side_effect=post):
            text = readers.chat(engine, "读题", ["data:image/png;base64,QUJD"])
            self.assertEqual(text, "好")
            self.assertEqual([payload["max_tokens"] for payload in sent], [3000, 1024])
            self.assertIn("enable_thinking", sent[1])          # nothing else was dropped
            # The next question asks within the limit straight away.
            answers.append(_Response(200, text="又好"))
            readers.chat(engine, "读题", ["data:image/png;base64,QUJD"])
        self.assertEqual(sent[2]["max_tokens"], 1024)

    def test_an_unavailable_service_hands_the_question_to_another(self):
        def post(url, key, payload, timeout=(10, 150)):
            return _Response(503) if url == readers.MODELSCOPE_URL else _Response(200, text="【题干】硅基流动读的")

        with keys(modelscope=1, siliconflow=1), mock.patch.object(readers, "_post", side_effect=post):
            engine = readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B")
            text = readers.chat(engine, "读题", ["data:image/png;base64,QUJD"])
            self.assertEqual(text, "【题干】硅基流动读的")
            self.assertEqual(readers.answered_by(engine).provider, "siliconflow")
            account_pool.reset_account_pools()
            with mock.patch.dict("os.environ", {"QB_PROVIDER_FALLBACK": "0"}), \
                    self.assertRaises(readers.ReaderUnavailable):
                readers.chat(engine, "读题", ["data:image/png;base64,QUJD"])

    def test_a_failed_service_rests_so_later_questions_go_elsewhere(self):
        calls = []

        def post(url, key, payload, timeout=(10, 150)):
            calls.append(url)
            return _Response(503) if url == readers.MODELSCOPE_URL else _Response(200, text="好")

        image = ["data:image/png;base64,QUJD"]
        engine = readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B")
        with keys(modelscope=1, siliconflow=1), mock.patch.object(readers, "_post", side_effect=post):
            readers.chat(engine, "读题", image)
            self.assertEqual((calls[0], calls[-1]), (readers.MODELSCOPE_URL, readers.SILICONFLOW_URL))
            calls.clear()
            readers.chat(engine, "读题", image)
            self.assertEqual(calls, [readers.SILICONFLOW_URL])      # no waiting on 魔搭 again
            self.assertEqual(readers.answered_by(engine).provider, "siliconflow")
            # The next paper (or new keys) starts with 魔搭 again.
            account_pool.reset_account_pools()
            calls.clear()
            readers.chat(engine, "读题", image)
            self.assertEqual(calls[0], readers.MODELSCOPE_URL)
            with mock.patch.dict("os.environ", {"QB_PROVIDER_REST": "0"}):
                account_pool.rest_provider("modelscope")
                self.assertFalse(account_pool.provider_resting("modelscope"))

    def test_the_only_service_used_up_fails_fast_for_the_rest_of_the_paper(self):
        calls = []

        def post(url, key, payload, timeout=(10, 150)):
            calls.append(url)
            return _Response(503)

        image = ["data:image/png;base64,QUJD"]
        engine = readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B")
        with keys(modelscope=1), mock.patch.object(readers, "_post", side_effect=post):
            with self.assertRaises(readers.ReaderUnavailable):
                readers.chat(engine, "读题", image)
            asked = len(calls)
            with self.assertRaises(readers.ReaderUnavailable) as again:
                readers.chat(engine, "读题", image)
        self.assertEqual(len(calls), asked)                  # not asked while it rests
        self.assertIn("魔搭", str(again.exception))

    def test_a_busy_service_is_left_sooner_when_another_can_read(self):
        calls = []

        def post(url, key, payload, timeout=(10, 150)):
            calls.append(url)
            if url == readers.MODELSCOPE_URL:
                busy = _Response(429)
                busy.headers = {"Retry-After": "0"}
                return busy
            return _Response(200, text="好")

        with keys(modelscope=1, siliconflow=1), mock.patch.object(readers, "_post", side_effect=post):
            readers.chat(readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B"), "读题", ["data:image/png;base64,QUJD"])
        self.assertEqual(calls.count(readers.MODELSCOPE_URL), readers.RATE_LIMIT_ROUNDS)
        self.assertEqual(calls[-1], readers.SILICONFLOW_URL)

    def test_a_content_error_is_not_sent_elsewhere(self):
        calls = []

        def post(url, key, payload, timeout=(10, 150)):
            calls.append(url)
            return _Response(422)

        with keys(modelscope=1, siliconflow=1), mock.patch.object(readers, "_post", side_effect=post):
            with self.assertRaises(readers.ReaderError) as caught:
                readers.chat(readers.Engine("modelscope", "Qwen/Qwen3.5-35B-A3B"), "读题", ["data:,"])
        self.assertNotIsInstance(caught.exception, readers.ReaderUnavailable)
        self.assertEqual(set(calls), {readers.MODELSCOPE_URL})

    def test_the_free_tier_starts_with_few_parallel_requests(self):
        with mock.patch.dict("os.environ", {"QB_MODELSCOPE_ACCOUNT_CONCURRENCY": ""}):
            self.assertEqual(account_pool.concurrency_range("modelscope"), (1, 2))

    def test_assistant_mode_needs_no_vision_key(self):
        with keys(), mock.patch.dict("os.environ", {"QB_PRIMARY_ENGINE": "assistant"}):
            self.assertTrue(readers.assistant_mode())
            self.assertIsNone(readers.primary_engine())
            self.assertIsNone(readers.checker_engine())
            settings = readers.engine_settings()
            self.assertTrue(settings["assistant"])
            self.assertEqual(settings["selected"]["primary"], "assistant")


class FreeReaderFormattingTests(SimpleTestCase):
    def test_a_blank_written_as_underline_hspace_is_shown_as_underscores(self):
        from .textnorm import fix_symbols, witness_key
        self.assertEqual(fix_symbols(r"则 $BD$ 的长为 $\underline{\hspace{2em}}$."), "则 $BD$ 的长为 ____.")
        self.assertEqual(fix_symbols(r"是 $\underline{\quad}$ 尺"), "是 ____ 尺")
        # Inside a formula, or underlining real content, nothing changes.
        self.assertEqual(fix_symbols(r"$x=\underline{\qquad}$"), r"$x=\underline{\qquad}$")
        self.assertEqual(fix_symbols(r"$\underline{AB}$"), r"$\underline{AB}$")
        self.assertEqual(witness_key(r"长为$\underline{\hspace{2em}}$"), witness_key("长为____"))
