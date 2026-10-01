"""本机模型角色选择：只验证路由，不接触任何真实 API。"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase

from . import readers


class ModelRoutingTests(SimpleTestCase):
    def env(self, **values):
        base = {
            "QB_MINIMAX_CONFIGURED": "1",
            "QB_SILICONFLOW_CONFIGURED": "0",
            "QB_PRIMARY_ENGINE": "minimax_m3",
            "QB_CHECKER_ENGINE": "auto",
            "QB_ARBITER_ENGINE": "primary",
        }
        base.update(values)
        return mock.patch.dict("os.environ", base, clear=True)

    def test_defaults_use_minimax_twice_when_other_provider_is_missing(self):
        with self.env():
            primary = readers.primary_engine()
            checker = readers.checker_engine()
            self.assertEqual(primary.key, "minimax_m3")
            self.assertEqual(checker.key, "minimax_m3")
            self.assertEqual(readers.arbiter_engine(primary, checker).key, "minimax_m3")

    def test_auto_checker_prefers_configured_other_provider(self):
        with self.env(QB_SILICONFLOW_CONFIGURED="1"):
            self.assertEqual(readers.checker_engine().key, "siliconflow_qwen3")

    def test_auto_checker_uses_minimax_when_siliconflow_is_primary(self):
        with self.env(QB_SILICONFLOW_CONFIGURED="1", QB_PRIMARY_ENGINE="siliconflow_qwen3"):
            self.assertEqual(readers.primary_engine().key, "siliconflow_qwen3")
            self.assertEqual(readers.checker_engine().key, "minimax_m3")

    def test_roles_can_be_selected_independently(self):
        with self.env(
            QB_SILICONFLOW_CONFIGURED="1",
            QB_PRIMARY_ENGINE="siliconflow_qwen3",
            QB_CHECKER_ENGINE="minimax_m3",
            QB_ARBITER_ENGINE="checker",
        ):
            primary = readers.primary_engine()
            checker = readers.checker_engine()
            self.assertEqual(primary.key, "siliconflow_qwen3")
            self.assertEqual(checker.key, "minimax_m3")
            self.assertEqual(readers.arbiter_engine(primary, checker).key, "minimax_m3")

    def test_provider_model_ids_are_dynamic_without_changing_legacy_engine_keys(self):
        with self.env(
            QB_SILICONFLOW_CONFIGURED="1",
            QB_MINIMAX_MODEL="MiniMax-M3.1+vision",
            QB_SILICONFLOW_MODEL="Qwen/new-vl:model",
        ):
            primary = readers.primary_engine()
            checker = readers.checker_engine()
            self.assertEqual((primary.key, primary.model), ("minimax_m3", "MiniMax-M3.1+vision"))
            self.assertEqual((checker.key, checker.model), ("siliconflow_qwen3", "Qwen/new-vl:model"))
            settings = readers.engine_settings()
            self.assertEqual(settings["models"]["minimax"], "MiniMax-M3.1+vision")
            self.assertEqual(settings["choices"][0]["provider_key"], "minimax")

    def test_unconfigured_explicit_engine_falls_back_and_says_so(self):
        # The chosen service has no key: the first one that has a key reads,
        # and the status names the engine that really reads.
        with self.env(QB_PRIMARY_ENGINE="siliconflow_qwen3"):
            self.assertEqual(readers.primary_engine().key, "minimax_m3")
            status = readers.engine_settings()
            self.assertEqual((status["selected"]["primary"], status["primary"]), ("siliconflow_qwen3", "minimax_m3"))
            qwen = next(item for item in status["choices"] if item["key"] == "siliconflow_qwen3")
            self.assertFalse(qwen["available"])

    def test_unknown_values_fall_back_to_safe_defaults(self):
        with self.env(QB_PRIMARY_ENGINE="unknown", QB_CHECKER_ENGINE="unknown", QB_ARBITER_ENGINE="unknown"):
            primary = readers.primary_engine()
            checker = readers.checker_engine()
            self.assertEqual(primary.key, "minimax_m3")
            self.assertEqual(checker.key, "minimax_m3")
            self.assertEqual(readers.arbiter_engine(primary, checker).key, "minimax_m3")
