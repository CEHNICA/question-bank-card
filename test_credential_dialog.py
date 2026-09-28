"""Offline tests for the native API configuration window's data handling."""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from credential_dialog import (
    credentials_complete,
    normalize_credential_values,
    validate_credential_values,
    validate_model_preferences,
    verify_credential_accounts,
)
from credential_store import (
    DEFAULT_MODEL_PREFERENCES,
    MAX_ACCOUNT_POOL_SIZE,
    _transform,
    credential_pool,
    load_credentials,
    load_model_configuration,
    load_model_preferences,
    model_preference_environment,
    save_credentials,
    save_model_preferences,
)


class CredentialFormTests(unittest.TestCase):
    def test_normalizes_supported_paste_forms(self):
        values = normalize_credential_values({
            "mineru_token": "  Bearer mineru-token  ",
            "minimax_key": r"mini\_max",
            "siliconflow_key": "  silicon-key  ",
        })
        self.assertEqual(values, {
            "mineru_token": "mineru-token",
            "mineru_tokens": ["mineru-token"],
            "minimax_key": "mini_max",
            "minimax_keys": ["mini_max"],
            "siliconflow_key": "silicon-key",
            "siliconflow_keys": ["silicon-key"],
        })

    def test_semicolon_pools_are_ordered_deduplicated_and_keep_legacy_first(self):
        values = normalize_credential_values({
            "mineru_token": "Bearer m-one; m-two; m-one",
            "minimax_key": r"mm\_one;mm_two",
            "siliconflow_key": "sf-one; sf-two",
        })
        self.assertEqual(values["mineru_tokens"], ["m-one", "m-two"])
        self.assertEqual(values["minimax_keys"], ["mm_one", "mm_two"])
        self.assertEqual(values["siliconflow_keys"], ["sf-one", "sf-two"])
        self.assertEqual(
            (values["mineru_token"], values["minimax_key"], values["siliconflow_key"]),
            ("m-one", "mm_one", "sf-one"),
        )

    def test_each_pool_is_limited_to_eight_accounts(self):
        values = normalize_credential_values({
            "mineru_token": ";".join(f"m-{index}" for index in range(MAX_ACCOUNT_POOL_SIZE + 1)),
            "minimax_key": "mm",
            "siliconflow_key": "",
        })
        self.assertIn("最多", validate_credential_values(values))

    def test_optional_key_is_removed_when_blank(self):
        values = normalize_credential_values({
            "mineru_token": "m",
            "minimax_key": "k",
            "siliconflow_key": "   ",
        })
        self.assertNotIn("siliconflow_key", values)
        self.assertNotIn("siliconflow_keys", values)
        self.assertIsNone(validate_credential_values(values))

    def test_mineru_and_selected_primary_model_must_be_nonempty(self):
        self.assertFalse(credentials_complete({"mineru_token": "", "minimax_key": ""}))
        self.assertFalse(credentials_complete({"mineru_token": "m"}))
        self.assertFalse(credentials_complete({"minimax_key": "k"}))
        self.assertTrue(credentials_complete({"mineru_token": "m", "minimax_key": "k"}))
        self.assertIn("MinerU", validate_credential_values({"mineru_token": "", "minimax_key": "k"}))
        self.assertIsNone(validate_credential_values({"mineru_token": "m", "minimax_key": ""}))
        siliconflow = {**DEFAULT_MODEL_PREFERENCES, "primary_engine": "siliconflow_qwen3"}
        self.assertTrue(credentials_complete(
            {"mineru_token": "m", "siliconflow_key": "sf"}, siliconflow,
        ))

    def test_rejects_embedded_whitespace_and_control_characters(self):
        self.assertIn("空格或换行", validate_credential_values({
            "mineru_token": "m token", "minimax_key": "k",
        }))
        self.assertIn("空格或换行", validate_credential_values({
            "mineru_token": "m", "minimax_key": "key\nnext",
        }))

    def test_siliconflow_model_requires_its_optional_key(self):
        credentials = {"mineru_token": "m", "minimax_key": "k"}
        self.assertIsNone(validate_model_preferences(DEFAULT_MODEL_PREFERENCES, credentials))
        selected = {**DEFAULT_MODEL_PREFERENCES, "primary_engine": "siliconflow_qwen3"}
        self.assertIn("硅基流动", validate_model_preferences(selected, credentials))
        credentials["siliconflow_key"] = "sf"
        self.assertIsNone(validate_model_preferences(selected, credentials))

    def test_verifies_each_account_without_putting_secrets_in_result_labels(self):
        values = normalize_credential_values({
            "mineru_token": "m-secret-one;m-secret-two",
            "minimax_key": "mm-secret-one;mm-secret-two",
            "siliconflow_key": "sf-secret-one;sf-secret-two",
        })
        seen = []
        results = verify_credential_accounts(
            values,
            verify_mineru=lambda value: seen.append(("mineru", value)) or True,
            verify_minimax=lambda value: seen.append(("minimax", value)) or None,
            verify_siliconflow=lambda value: seen.append(("siliconflow", value)) or True,
        )
        self.assertEqual(seen, [
            ("mineru", "m-secret-one"), ("mineru", "m-secret-two"),
            ("minimax", "mm-secret-one"), ("minimax", "mm-secret-two"),
            ("siliconflow", "sf-secret-one"), ("siliconflow", "sf-secret-two"),
        ])
        self.assertEqual(list(results), [
            "MinerU 第 1 个账号", "MinerU 第 2 个账号",
            "MiniMax 第 1 个账号", "MiniMax 第 2 个账号",
            "硅基流动 第 1 个账号", "硅基流动 第 2 个账号",
        ])
        for label in results:
            self.assertNotIn("secret", label)

    def test_siliconflow_only_configuration_is_valid_when_no_role_selects_minimax(self):
        selected = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "auto",
            "arbiter_engine": "primary",
        }
        credentials = {"mineru_token": "m", "minimax_key": "", "siliconflow_key": "sf"}
        self.assertIsNone(validate_credential_values(credentials))
        self.assertIsNone(validate_model_preferences(selected, credentials))


class ModelPreferenceStoreTests(unittest.TestCase):
    def test_missing_preferences_use_legacy_defaults(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "never-created.json"
            self.assertEqual(load_model_preferences(path), DEFAULT_MODEL_PREFERENCES)

    def test_preferences_save_load_and_reject_unknown_values(self):
        selected = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "minimax_m3",
            "arbiter_engine": "checker",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model-preferences.json"
            save_model_preferences(selected, path)
            self.assertEqual(load_model_preferences(path), selected)

            path.write_text(
                '{"version":1,"roles":{"primary_engine":"arbitrary-model",'
                '"checker_engine":"minimax_m3","arbiter_engine":"not-allowed"}}',
                encoding="utf-8",
            )
            self.assertEqual(load_model_preferences(path), {
                "primary_engine": "minimax_m3",
                "checker_engine": "minimax_m3",
                "arbiter_engine": "primary",
            })

    def test_native_role_save_preserves_models_selected_in_the_app(self):
        selected = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "minimax_m3",
            "arbiter_engine": "checker",
        }
        models = {
            "minimax": "MiniMax-M3",
            "siliconflow": "Qwen/Qwen3-VL-30B-A3B-Instruct",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "model-preferences.json"
            save_model_preferences(selected, path, models=models)

            changed_roles = {**selected, "arbiter_engine": "primary"}
            save_model_preferences(changed_roles, path)

            stored = load_model_configuration(path)
            self.assertEqual(stored["roles"], changed_roles)
            self.assertEqual(stored["models"], models)

    def test_environment_contract_contains_only_allowlisted_nonsecrets(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"LOCALAPPDATA": temporary}
        ):
            environment = model_preference_environment({
                "primary_engine": "siliconflow_qwen3",
                "checker_engine": "minimax_m3",
                "arbiter_engine": "checker",
            })
            self.assertEqual(environment["QB_PRIMARY_ENGINE"], "siliconflow_qwen3")
            self.assertEqual(environment["QB_CHECKER_ENGINE"], "minimax_m3")
            self.assertEqual(environment["QB_ARBITER_ENGINE"], "checker")
            self.assertEqual(environment["QB_MINIMAX_MODEL"], "MiniMax-M3")
            self.assertEqual(environment["QB_SILICONFLOW_MODEL"], "Qwen/Qwen3-VL-32B-Instruct")
            preference_path = Path(environment["QB_MODEL_PREFERENCES_FILE"])
            self.assertTrue(preference_path.is_absolute())
            self.assertEqual(preference_path, Path(temporary).resolve() / "QuestionBankM2" / "model-preferences.json")
            self.assertFalse(any("API_KEY" in name or name == "MINERU_TOKEN" for name in environment))

    def test_environment_contract_uses_saved_concrete_model_ids(self):
        roles = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "minimax_m3",
            "arbiter_engine": "checker",
        }
        models = {
            "minimax": "MiniMax-Custom-Vision",
            "siliconflow": "Qwen/Custom-VL",
        }
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"LOCALAPPDATA": temporary}
        ):
            path = Path(temporary) / "QuestionBankM2" / "model-preferences.json"
            save_model_preferences(roles, path, models=models)
            environment = model_preference_environment(roles)

        self.assertEqual(environment["QB_MINIMAX_MODEL"], models["minimax"])
        self.assertEqual(environment["QB_SILICONFLOW_MODEL"], models["siliconflow"])


@unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
class DpapiRoundTripTests(unittest.TestCase):
    def test_dialog_values_are_encrypted_and_round_trip(self):
        values = {
            "mineru_token": "test-mineru-secret",
            "minimax_key": "test-minimax-secret",
            "siliconflow_key": "test-siliconflow-secret",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.dat"
            preference_path = Path(temporary) / "model-preferences.json"
            save_credentials(values, path)
            # Even a mistakenly merged mapping must not copy credentials into
            # the adjacent plaintext preference file.
            save_model_preferences({**DEFAULT_MODEL_PREFERENCES, **values}, preference_path)
            payload = path.read_bytes()
            preference_payload = preference_path.read_bytes()
            for secret in values.values():
                self.assertNotIn(secret.encode(), payload)
                self.assertNotIn(secret.encode(), preference_payload)
            loaded = load_credentials(path)
            self.assertEqual(loaded, {
                **values,
                "mineru_tokens": [values["mineru_token"]],
                "minimax_keys": [values["minimax_key"]],
                "siliconflow_keys": [values["siliconflow_key"]],
            })
            decrypted = json.loads(_transform(payload, protect=False).decode("utf-8"))
            self.assertEqual(decrypted["version"], 1)
            self.assertEqual(decrypted["mineru_token"], values["mineru_token"])
            self.assertEqual(decrypted["mineru_tokens"], [values["mineru_token"]])
            # A version-1 credential payload with no adjacent preference file is
            # the pre-feature layout and must still receive safe defaults.
            self.assertEqual(load_model_preferences(Path(temporary) / "missing.json"), DEFAULT_MODEL_PREFERENCES)

    def test_legacy_version_one_single_fields_load_as_one_account_pools(self):
        legacy = {
            "version": 1,
            "mineru_token": "legacy-mineru",
            "minimax_key": "legacy-minimax",
            "siliconflow_key": "legacy-siliconflow",
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "credentials.dat"
            path.write_bytes(_transform(json.dumps(legacy).encode("utf-8"), protect=True))
            loaded = load_credentials(path)
        self.assertEqual(credential_pool(loaded, "mineru"), ["legacy-mineru"])
        self.assertEqual(credential_pool(loaded, "minimax"), ["legacy-minimax"])
        self.assertEqual(credential_pool(loaded, "siliconflow"), ["legacy-siliconflow"])


if __name__ == "__main__":
    unittest.main()
