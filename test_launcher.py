"""启动器的离线测试：密钥只交给后台工作者；已在运行时不重复启动。不访问网络。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import start_question_bank as launcher


class LauncherTests(unittest.TestCase):
    def test_mineru_precheck_does_not_contact_undocumented_api(self):
        with patch.object(launcher.urllib.request, "urlopen") as network:
            self.assertIsNone(launcher._mineru_token_validity("test-token"))
        network.assert_not_called()

    def run_main(
        self,
        saved: dict[str, str],
        preferences: dict[str, str] | None = None,
        parallel: str = "",
    ):
        environments = {}
        process = Mock()
        process.poll.return_value = 0
        preferences = preferences or dict(launcher.DEFAULT_MODEL_PREFERENCES)

        def capture(args, environment, log_name):
            environments[log_name] = dict(environment)
            return process, io.BytesIO()

        output = io.StringIO()
        with patch.object(launcher.os, "name", "nt"), \
                patch.dict(os.environ, {
                    "MINERU_TOKEN": "", "MINIMAX_API_KEY": "", "SILICONFLOW_API_KEY": "",
                    "MINERU_TOKENS_JSON": '["untrusted"]',
                    "MINIMAX_API_KEYS_JSON": '["untrusted"]',
                    "SILICONFLOW_API_KEYS_JSON": '["untrusted"]',
                    "QB_PRIMARY_ENGINE": "untrusted", "QB_CHECKER_ENGINE": "untrusted",
                    "QB_ARBITER_ENGINE": "untrusted", "QB_MINIMAX_MODEL": "untrusted",
                    "QB_SILICONFLOW_MODEL": "untrusted", "QB_MODEL_PREFERENCES_FILE": "C:/untrusted.json",
                    "QB_PARALLEL": parallel,
                }), \
                patch.object(launcher, "_running_instance", return_value=None), \
                patch.object(launcher, "InstanceMutex") as mutex_type, \
                patch.object(launcher, "_prepare"), \
                patch.object(launcher, "load_credentials", return_value=saved), \
                patch.object(launcher, "load_model_preferences", return_value=preferences), \
                patch.object(launcher, "save_credentials"), \
                patch.object(launcher, "_mineru_token_validity", return_value=True), \
                patch.object(launcher, "_available_port", return_value=8768), \
                patch.object(launcher, "ChildJob"), \
                patch.object(launcher, "_start", side_effect=capture), \
                patch.object(launcher, "_health"), \
                patch.object(launcher.webbrowser, "open_new_tab"), \
                patch("builtins.input", return_value=""), contextlib.redirect_stdout(output):
            mutex_type.return_value.acquired = True
            self.assertEqual(launcher.main(), 0)
        environments["_stdout"] = output.getvalue()
        return environments

    def test_only_worker_receives_secrets(self):
        environments = self.run_main({"mineru_token": "m-token", "minimax_key": "mm-key", "siliconflow_key": "sf-key"})
        worker, web = environments["worker.log"], environments["web.log"]
        self.assertEqual((worker["MINERU_TOKEN"], worker["MINIMAX_API_KEY"], worker["SILICONFLOW_API_KEY"]),
                         ("m-token", "mm-key", "sf-key"))
        self.assertEqual(json.loads(worker["MINERU_TOKENS_JSON"]), ["m-token"])
        self.assertEqual(json.loads(worker["MINIMAX_API_KEYS_JSON"]), ["mm-key"])
        self.assertEqual(json.loads(worker["SILICONFLOW_API_KEYS_JSON"]), ["sf-key"])
        for secret in ("m-token", "mm-key", "sf-key"):
            self.assertNotIn(secret, environments["_stdout"])
        for name in launcher.SECRET_NAMES:
            self.assertNotIn(name, web)
        self.assertEqual((web["QB_MINERU_CONFIGURED"], web["QB_MINIMAX_CONFIGURED"], web["QB_SILICONFLOW_CONFIGURED"]),
                         ("1", "1", "1"))
        self.assertEqual((web["QB_MINERU_POOL_SIZE"], web["QB_MINIMAX_POOL_SIZE"], web["QB_SILICONFLOW_POOL_SIZE"]),
                         ("1", "1", "1"))
        for environment in (worker, web):
            self.assertEqual(environment["QB_CREDENTIAL_HOT_RELOAD"], "1")
            self.assertTrue(Path(environment["QB_CREDENTIAL_FILE"]).is_absolute())
            self.assertEqual(environment["QB_PRIMARY_ENGINE"], "minimax_m3")
            self.assertEqual(environment["QB_CHECKER_ENGINE"], "auto")
            self.assertEqual(environment["QB_ARBITER_ENGINE"], "primary")
            self.assertEqual(environment["QB_MINIMAX_MODEL"], "MiniMax-M3")
            self.assertEqual(environment["QB_SILICONFLOW_MODEL"], "Qwen/Qwen3-VL-32B-Instruct")
            self.assertTrue(Path(environment["QB_MODEL_PREFERENCES_FILE"]).is_absolute())

    def test_all_saved_accounts_reach_only_the_worker_as_compact_json(self):
        saved = {
            "mineru_token": "m1", "mineru_tokens": ["m1", "m2"],
            "minimax_key": "mm1", "minimax_keys": ["mm1", "mm2", "mm3"],
            "siliconflow_key": "sf1", "siliconflow_keys": ["sf1", "sf2"],
        }
        environments = self.run_main(saved)
        worker, web = environments["worker.log"], environments["web.log"]
        self.assertEqual(worker["MINERU_TOKENS_JSON"], '["m1","m2"]')
        self.assertEqual(worker["MINIMAX_API_KEYS_JSON"], '["mm1","mm2","mm3"]')
        self.assertEqual(worker["SILICONFLOW_API_KEYS_JSON"], '["sf1","sf2"]')
        self.assertEqual((worker["MINERU_TOKEN"], worker["MINIMAX_API_KEY"], worker["SILICONFLOW_API_KEY"]),
                         ("m1", "mm1", "sf1"))
        for name in launcher.SECRET_NAMES:
            self.assertNotIn(name, web)
        self.assertEqual((web["QB_MINERU_POOL_SIZE"], web["QB_MINIMAX_POOL_SIZE"], web["QB_SILICONFLOW_POOL_SIZE"]),
                         ("2", "3", "2"))
        # 3 MiniMax accounts x 6 + 2 SiliconFlow accounts x 2, capped at 16.
        self.assertEqual(worker["QB_PARALLEL"], "16")
        self.assertEqual(web["QB_PARALLEL"], "16")
        self.assertEqual(worker["QB_PARALLEL_EXPLICIT"], "0")
        for secret in ("m1", "m2", "mm1", "mm2", "mm3", "sf1", "sf2"):
            self.assertNotIn(secret, environments["_stdout"])

    def test_explicit_valid_parallelism_overrides_pool_default(self):
        saved = {
            "mineru_token": "m1", "mineru_tokens": ["m1", "m2"],
            "minimax_key": "mm1", "minimax_keys": ["mm1", "mm2", "mm3"],
            "siliconflow_key": "sf1", "siliconflow_keys": ["sf1", "sf2"],
        }
        environments = self.run_main(saved, parallel="7")
        self.assertEqual(environments["worker.log"]["QB_PARALLEL"], "7")
        self.assertEqual(environments["web.log"]["QB_PARALLEL"], "7")
        self.assertEqual(environments["worker.log"]["QB_PARALLEL_EXPLICIT"], "1")

    def test_zero_and_invalid_parallelism_fall_back_to_provider_pool_size(self):
        pools = {
            "mineru": ["m1", "m2"],
            "minimax": ["mm1", "mm2", "mm3"],
            "siliconflow": ["sf1", "sf2"],
        }
        preferences = dict(launcher.DEFAULT_MODEL_PREFERENCES)
        for invalid in ("0", "-1", "abc", "17"):
            with self.subTest(invalid=invalid):
                self.assertEqual(
                    launcher._parallel_environment(
                        {"QB_PARALLEL": invalid, "QB_MINIMAX_ACCOUNT_CONCURRENCY": "1",
                         "QB_SILICONFLOW_ACCOUNT_CONCURRENCY": "1"},
                        pools, preferences,
                    ),
                    {"QB_PARALLEL": "5", "QB_PARALLEL_EXPLICIT": "0"},
                )
        self.assertEqual(
            launcher._parallel_environment({"QB_PARALLEL": "9"}, pools, preferences),
            {"QB_PARALLEL": "9", "QB_PARALLEL_EXPLICIT": "1"},
        )
        # One MiniMax key alone now reads six cards at once by default.
        self.assertEqual(
            launcher._parallel_environment({}, {"minimax": ["mm1"]}, preferences),
            {"QB_PARALLEL": "6", "QB_PARALLEL_EXPLICIT": "0"},
        )
        pools["minimax"] = [f"mm{index}" for index in range(8)]
        pools["siliconflow"] = [f"sf{index}" for index in range(8)]
        self.assertEqual(
            launcher._parallel_environment({}, pools, preferences),
            {"QB_PARALLEL": "16", "QB_PARALLEL_EXPLICIT": "0"},
        )

    def test_free_keys_alone_set_who_reads_and_how_many_at_once(self):
        preferences = dict(launcher.DEFAULT_MODEL_PREFERENCES)      # MiniMax chosen, but no MiniMax key
        pools = {"mineru": ["m1"], "modelscope": ["s1"]}
        self.assertEqual(launcher._effective_primary(preferences, pools), "modelscope")
        # 魔搭 reads and checks: up to 2 at once per key.
        self.assertEqual(launcher._parallel_environment({}, pools, preferences),
                         {"QB_PARALLEL": "2", "QB_PARALLEL_EXPLICIT": "0"})
        self.assertEqual(launcher._effective_primary(preferences, {**pools, "minimax": ["mm"]}), "minimax")
        assistant = {**preferences, "primary_engine": "assistant"}
        self.assertIsNone(launcher._effective_primary(assistant, pools))
        self.assertIsNone(launcher._effective_primary(preferences, {"mineru": ["m1"]}))

    def test_custom_model_roles_reach_web_and_worker_from_trusted_preferences(self):
        selected = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "minimax_m3",
            "arbiter_engine": "checker",
        }
        environments = self.run_main(
            {"mineru_token": "m-token", "minimax_key": "mm-key", "siliconflow_key": "sf-key"},
            selected,
        )
        for environment in (environments["worker.log"], environments["web.log"]):
            self.assertEqual(environment["QB_PRIMARY_ENGINE"], "siliconflow_qwen3")
            self.assertEqual(environment["QB_CHECKER_ENGINE"], "minimax_m3")
            self.assertEqual(environment["QB_ARBITER_ENGINE"], "checker")
            self.assertNotEqual(environment["QB_MODEL_PREFERENCES_FILE"], "C:/untrusted.json")

    def test_missing_siliconflow_is_reported_to_web(self):
        environments = self.run_main({"mineru_token": "m-token", "minimax_key": "mm-key"})
        self.assertEqual(environments["web.log"]["QB_SILICONFLOW_CONFIGURED"], "0")
        self.assertNotIn("SILICONFLOW_API_KEY", environments["worker.log"])

    def test_free_keys_only_start_reading_with_them(self):
        environments = self.run_main({"mineru_token": "m-token", "modelscope_key": "s-key"})
        worker, web = environments["worker.log"], environments["web.log"]
        self.assertEqual(json.loads(worker["MODELSCOPE_API_KEYS_JSON"]), ["s-key"])
        self.assertNotIn("MODELSCOPE_API_KEYS_JSON", web)
        self.assertEqual(web["QB_MODELSCOPE_CONFIGURED"], "1")
        self.assertIn("主读 魔搭 Qwen3.5-35B-A3B；复核 魔搭 Qwen3.5-35B-A3B", environments["_stdout"])
        self.assertEqual(worker["QB_PARALLEL"], "2")

    def test_assistant_reading_needs_no_reading_key(self):
        preferences = {**launcher.DEFAULT_MODEL_PREFERENCES, "primary_engine": "assistant"}
        environments = self.run_main({"mineru_token": "m-token"}, preferences)
        self.assertEqual(environments["worker.log"]["QB_PRIMARY_ENGINE"], "assistant")
        self.assertIn("模型分工：AI 助手", environments["_stdout"])
        self.assertNotIn("还没有填看图读题的密钥", environments["_stdout"])

    def test_no_credentials_still_opens_in_app_settings_without_prompting(self):
        environments = self.run_main({})
        worker, web = environments["worker.log"], environments["web.log"]
        for name in launcher.SECRET_NAMES:
            self.assertNotIn(name, worker)
            self.assertNotIn(name, web)
        self.assertEqual(web["QB_MINERU_CONFIGURED"], "0")
        self.assertEqual(web["QB_MINIMAX_CONFIGURED"], "0")
        self.assertIn("设置 → 服务与密钥", environments["_stdout"])

    def test_console_fallback_keeps_good_saved_account_when_first_is_invalid(self):
        saved = {
            "mineru_token": "bad-first",
            "mineru_tokens": ["bad-first", "good-second"],
            "minimax_key": "model-key",
            "minimax_keys": ["model-key"],
        }
        saved_updates = []
        validity = {"bad-first": False, "good-second": True}
        with patch.dict(os.environ, {"MINERU_TOKEN": "", "MINIMAX_API_KEY": ""}), \
                patch.object(launcher, "load_credentials", return_value=saved), \
                patch.object(launcher, "save_credentials", side_effect=lambda value: saved_updates.append(value)), \
                patch.object(launcher, "_mineru_token_validity", side_effect=lambda token: validity[token]), \
                patch.object(launcher.getpass, "getpass") as prompt, \
                contextlib.redirect_stdout(io.StringIO()):
            token, model_key = launcher._resolve_credentials()

        self.assertEqual((token, model_key), ("good-second", "model-key"))
        prompt.assert_not_called()
        self.assertEqual(len(saved_updates), 1)
        self.assertEqual(saved_updates[0]["mineru_token"], "good-second")
        self.assertEqual(saved_updates[0]["mineru_tokens"], ["good-second"])

    def test_existing_instance_is_reused(self):
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher, "_running_instance", return_value="http://127.0.0.1:8768"), \
                patch.object(launcher, "_prepare") as prepare, \
                patch.object(launcher.webbrowser, "open_new_tab") as browser, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(), 0)
        prepare.assert_not_called()
        browser.assert_called_once_with("http://127.0.0.1:8768")

    def test_busy_mutex_never_starts_a_second_copy(self):
        mutex = Mock(acquired=False)
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher, "_running_instance", return_value=None), \
                patch.object(launcher, "InstanceMutex", return_value=mutex), \
                patch.object(launcher, "_prepare") as prepare, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(launcher.main(), 0)
        prepare.assert_not_called()

    def test_recorded_random_port_is_checked(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, _limit):
                return b'{"app":"question-bank-card"}'

        instance_file = Mock()
        instance_file.is_file.return_value = True
        instance_file.read_text.return_value = '{"port":49152}'
        with patch.object(launcher, "INSTANCE_FILE", instance_file), \
                patch.object(launcher.urllib.request, "urlopen", return_value=Response()) as urlopen:
            self.assertEqual(launcher._running_instance(), "http://127.0.0.1:49152")
        self.assertEqual(urlopen.call_args.args[0], "http://127.0.0.1:49152/api/health")

    def test_staged_storage_is_restored_or_cleaned_from_database_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            data_root.mkdir()
            database = root / "db.sqlite3"
            kept_id, deleted_id, split_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            with contextlib.closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE core_paper (id char(32) PRIMARY KEY)")
                connection.execute("INSERT INTO core_paper (id) VALUES (?)", (kept_id.hex,))
                connection.execute("INSERT INTO core_paper (id) VALUES (?)", (split_id.hex,))
                connection.commit()

            staged_kept = data_root / f".deleting-{kept_id}-{uuid.uuid4().hex}"
            staged_deleted = data_root / f".deleting-{deleted_id}-{uuid.uuid4().hex}"
            staged_upload = data_root / f".uploading-{uuid.uuid4()}-{uuid.uuid4().hex}"
            staged_split = data_root / f".splitting-{split_id}-{uuid.uuid4().hex}"
            unrelated = data_root / ".deleting-not-a-paper"
            for path in (staged_kept, staged_deleted, staged_upload, staged_split, unrelated):
                path.mkdir()
                (path / "source.pdf").write_bytes(b"test")

            with patch.object(launcher, "DATA_ROOT", data_root), \
                    patch.object(launcher, "DATABASE", database), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launcher._reconcile_staged_storage(), (2, 2))

            self.assertTrue((data_root / str(kept_id) / "source.pdf").is_file())
            self.assertFalse(staged_deleted.exists())
            self.assertFalse(staged_upload.exists())
            self.assertTrue((data_root / str(split_id) / "source.pdf").is_file())
            self.assertTrue(unrelated.is_dir())


if __name__ == "__main__":
    unittest.main()
