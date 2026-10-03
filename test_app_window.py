"""窗口版启动器与桌面图标工具的离线测试：不联网、不启动真实浏览器或服务。"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app_window
import create_shortcut


class DirectSplash:
    """测试用启动画面：直接在当前线程执行。"""

    def __init__(self):
        self.messages = []

    def say(self, text):
        self.messages.append(text)

    def run(self, work):
        return work(self)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {"MINERU_TOKEN": "", "MINIMAX_API_KEY": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        preferences = patch.object(app_window.launcher, "_model_preferences", return_value={
            "primary_engine": "minimax_m3", "checker_engine": "auto", "arbiter_engine": "primary",
        })
        preferences.start()
        self.addCleanup(preferences.stop)

    def test_ready_needs_both_services_even_if_skipped(self):
        self.assertFalse(app_window.credentials_ready({"mineru_token": "", "minimax_key": ""}))
        self.assertTrue(app_window.credentials_ready({"mineru_token": "m", "minimax_key": "k"}))
        self.assertFalse(app_window.credentials_ready({"mineru_token": "m"}))
        self.assertFalse(app_window.credentials_ready({"minimax_key": "k"}))

    def test_invalid_saved_token_goes_to_console(self):
        with patch.object(app_window, "load_credentials", return_value={"mineru_token": "bad", "minimax_key": "k"}), \
                patch.object(app_window.launcher, "_mineru_token_validity", return_value=False):
            with self.assertRaises(app_window.NeedsConsole):
                app_window.resolve_credentials_quietly()

    def test_invalid_environment_token_falls_back_to_saved(self):
        validity = {"env-bad": False, "saved-good": True}
        with patch.dict(os.environ, {"MINERU_TOKEN": "Bearer env-bad"}), \
                patch.object(app_window, "load_credentials", return_value={"mineru_token": "saved-good", "minimax_key": "k\\_1"}), \
                patch.object(app_window.launcher, "_mineru_token_validity", side_effect=lambda t: validity[t]):
            self.assertEqual(app_window.resolve_credentials_quietly(), (["saved-good"], ["k_1"]))

    def test_offline_check_does_not_block_start(self):
        with patch.object(app_window, "load_credentials", return_value={"mineru_token": "tok", "minimax_key": "k"}), \
                patch.object(app_window.launcher, "_mineru_token_validity", return_value=None):
            self.assertEqual(app_window.resolve_credentials_quietly(), (["tok"], ["k"]))

    def test_invalid_saved_mineru_account_is_isolated_without_echoing_the_token(self):
        saved = {
            "mineru_token": "good-one",
            "mineru_tokens": ["good-one", "private-bad-token"],
            "minimax_key": "k",
            "minimax_keys": ["k"],
        }
        with patch.object(app_window, "load_credentials", return_value=saved), \
                patch.object(app_window.launcher, "_mineru_token_validity", side_effect=[True, False]):
            self.assertEqual(app_window.resolve_credentials_quietly(), (["good-one"], ["k"]))

    def test_all_invalid_saved_mineru_accounts_require_reconfiguration_without_echo(self):
        saved = {
            "mineru_token": "private-bad-one",
            "mineru_tokens": ["private-bad-one", "private-bad-two"],
            "minimax_key": "k",
        }
        with patch.object(app_window, "load_credentials", return_value=saved), \
                patch.object(app_window.launcher, "_mineru_token_validity", return_value=False):
            with self.assertRaises(app_window.NeedsConsole) as caught:
                app_window.resolve_credentials_quietly()
        self.assertIn("均未通过", str(caught.exception))
        self.assertNotIn("private-bad", str(caught.exception))

    def test_minimax_only_in_environment_is_not_used_silently(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "env-key"}), \
                patch.object(app_window, "load_credentials", return_value={"mineru_token": "tok"}):
            self.assertFalse(app_window.credentials_ready())

    def test_siliconflow_only_saved_credentials_work_when_it_is_primary(self):
        preferences = {
            "primary_engine": "siliconflow_qwen3", "checker_engine": "auto", "arbiter_engine": "primary",
        }
        saved = {"mineru_token": "tok", "minimax_key": "", "siliconflow_key": "sf"}
        self.assertTrue(app_window.credentials_ready(saved, preferences))


class WindowTests(unittest.TestCase):
    def test_edge_is_preferred_and_profile_stays_outside_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            edge = base / "x86" / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            chrome = base / "pf" / "Google" / "Chrome" / "Application" / "chrome.exe"
            for exe in (edge, chrome):
                exe.parent.mkdir(parents=True)
                exe.write_bytes(b"")
            env = {"PROGRAMFILES(X86)": str(base / "x86"), "PROGRAMFILES": str(base / "pf"), "LOCALAPPDATA": str(base / "local")}
            with patch.dict(os.environ, env), patch.object(app_window, "_registry_app_paths", return_value=[]):
                self.assertEqual(app_window.browser_candidates(), [edge, chrome])
                profile = app_window.window_profile()
            self.assertTrue(str(profile).startswith(str(base / "local")))
            self.assertNotIn(str(app_window.ROOT), str(profile))
            state = json.loads((profile / "Local State").read_text(encoding="utf-8"))
            self.assertIs(state["background_mode"]["enabled"], False)
            command = app_window.window_command(edge, "http://127.0.0.1:8768", profile)
            self.assertIn("--app=http://127.0.0.1:8768", command)
            self.assertIn(f"--user-data-dir={profile}", command)
            self.assertIn("--profile-directory=Default", command)
            self.assertIn("--start-maximized", command)
            preferences = json.loads((profile / "Default" / "Preferences").read_text(encoding="utf-8"))
            self.assertIs(preferences["credentials_enable_service"], False)
            self.assertIs(preferences["credentials_enable_autosignin"], False)

    def test_password_preferences_change_only_the_dedicated_profile_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "app-profile"
            preferences = profile / "Default" / "Preferences"
            preferences.parent.mkdir(parents=True)
            original = {"credentials_enable_service": True, "credentials_enable_autosignin": True,
                        "appearance": {"theme": "custom"}, "profile": {"name": "题有据", "exit_type": "Normal"}}
            preferences.write_text(json.dumps(original), encoding="utf-8")
            ordinary = root / "ordinary-browser" / "Default" / "Preferences"
            ordinary.parent.mkdir(parents=True)
            ordinary.write_text(json.dumps(original), encoding="utf-8")
            ordinary_bytes = ordinary.read_bytes()
            unrelated = [profile / "Default" / "Login Data", profile / "Default" / "Cookies", root / "credentials.dat"]
            for target in unrelated:
                target.write_bytes(b"never-read-or-change-this-test-fixture")
            actual_read = Path.read_text
            reads = []

            def read_only_preferences(target, *args, **kwargs):
                reads.append(target)
                self.assertEqual(target, preferences)
                return actual_read(target, *args, **kwargs)

            with patch.object(Path, "read_text", read_only_preferences):
                self.assertTrue(app_window._disable_profile_password_saving(profile))
            self.assertEqual(reads, [preferences])
            expected = dict(original, credentials_enable_service=False, credentials_enable_autosignin=False)
            self.assertEqual(json.loads(preferences.read_text(encoding="utf-8")), expected)
            self.assertEqual(ordinary.read_bytes(), ordinary_bytes)
            for target in unrelated:
                self.assertEqual(target.read_bytes(), b"never-read-or-change-this-test-fixture")
            self.assertEqual(list(preferences.parent.glob(".password-preferences-*")), [])
            with patch.object(app_window.os, "replace") as replace:
                self.assertTrue(app_window._disable_profile_password_saving(profile))
                replace.assert_not_called()

    def test_invalid_password_preferences_are_preserved_without_content_in_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            preferences = profile / "Default" / "Preferences"
            preferences.parent.mkdir()
            original = b'{"unrelated-private-test-value": invalid JSON'
            preferences.write_bytes(original)
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.assertFalse(app_window._disable_profile_password_saving(profile))
            self.assertEqual(preferences.read_bytes(), original)
            self.assertNotIn("private-test-value", output.getvalue())
            self.assertEqual(list(preferences.parent.glob(".password-preferences-*")), [])

    def test_failed_password_preferences_replace_preserves_original_and_removes_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp)
            preferences = profile / "Default" / "Preferences"
            preferences.parent.mkdir()
            original = b'{"credentials_enable_service":true,"appearance":{"theme":"custom"}}'
            preferences.write_bytes(original)
            output = io.StringIO()
            with patch.object(app_window.os, "replace", side_effect=OSError("private failure detail")), patch("sys.stdout", output):
                self.assertFalse(app_window._disable_profile_password_saving(profile))
            self.assertEqual(preferences.read_bytes(), original)
            self.assertNotIn("private failure detail", output.getvalue())
            self.assertEqual(list(preferences.parent.glob(".password-preferences-*")), [])

    def test_no_browser_falls_back_to_default_browser(self):
        with patch.object(app_window, "browser_candidates", return_value=[]), \
                patch.object(app_window.webbrowser, "open_new_tab") as opened:
            self.assertIsNone(app_window.open_window("http://127.0.0.1:8768"))
        opened.assert_called_once_with("http://127.0.0.1:8768")


class MainFlowTests(unittest.TestCase):
    def patches(self, **extra):
        base = {
            "os": SimpleNamespace(name="nt", environ=os.environ),
            "sys": SimpleNamespace(version_info=(3, 12, 0)),
            "_log_stream": Mock(return_value=io.StringIO()),
            "_set_app_id": Mock(),
            "_message": Mock(),
        }
        base.update(extra)
        return [patch.object(app_window, name, value) for name, value in base.items()]

    def run_patched(self, patchers, launcher_patches, arguments=None):
        started = []
        for item in patchers + [patch.object(app_window.launcher, n, v) for n, v in launcher_patches.items()]:
            started.append(item)
            item.start()
        try:
            return app_window.main(arguments)
        finally:
            for item in reversed(started):
                item.stop()

    def test_running_instance_only_opens_window(self):
        prepare = Mock()
        opened = Mock()
        result = self.run_patched(self.patches(open_window=opened),
                                  {"_running_instance": Mock(return_value="http://127.0.0.1:8768"), "_prepare": prepare})
        self.assertEqual(result, 0)
        opened.assert_called_once_with("http://127.0.0.1:8768")
        prepare.assert_not_called()

    def test_first_install_is_handed_to_console(self):
        handoff = Mock(return_value=0)
        mutex = Mock()
        result = self.run_patched(self.patches(_needs_install=Mock(return_value=True), _hand_off_to_console=handoff),
                                  {"_running_instance": Mock(return_value=None), "InstanceMutex": mutex})
        self.assertEqual(result, 0)
        handoff.assert_called_once()
        mutex.assert_not_called()

    def test_configure_argument_only_opens_credential_dialog(self):
        configure = Mock(return_value=True)
        prepare = Mock()
        message = Mock()
        result = self.run_patched(
            self.patches(_configure_credentials=configure, _message=message),
            {"_running_instance": Mock(return_value=None), "_prepare": prepare},
            ["--configure"],
        )
        self.assertEqual(result, 0)
        configure.assert_called_once_with(first_run=False)
        prepare.assert_not_called()
        message.assert_called_once()

    def test_full_start_keeps_secrets_in_worker_and_cleans_up(self):
        environments = {}
        processes = {}

        def fake_start(args, environment, log_name):
            process = Mock()
            process.poll.return_value = None
            processes[log_name] = process
            environments[log_name] = dict(environment)
            return process, io.BytesIO()

        mutex = Mock(acquired=True)
        waited = Mock()
        saved = {
            "mineru_token": "m-token", "mineru_tokens": ["m-token", "m-token-2"],
            "minimax_key": "mm-key", "minimax_keys": ["mm-key", "mm-key-2"],
            "siliconflow_key": "sf-key", "siliconflow_keys": ["sf-key", "sf-key-2"],
        }
        preferences = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "minimax_m3",
            "arbiter_engine": "checker",
        }
        with patch.dict(os.environ, {
            "MINERU_TOKEN": "", "MINIMAX_API_KEY": "", "SILICONFLOW_API_KEY": "",
            "MINERU_TOKENS_JSON": '["untrusted"]',
            "MINIMAX_API_KEYS_JSON": '["untrusted"]',
            "SILICONFLOW_API_KEYS_JSON": '["untrusted"]',
            "QB_PRIMARY_ENGINE": "untrusted", "QB_CHECKER_ENGINE": "untrusted",
            "QB_ARBITER_ENGINE": "untrusted", "QB_MINIMAX_MODEL": "untrusted",
            "QB_SILICONFLOW_MODEL": "untrusted", "QB_MODEL_PREFERENCES_FILE": "C:/untrusted.json",
            "QB_PARALLEL": "",
        }):
            result = self.run_patched(
                self.patches(_needs_install=Mock(return_value=False), Splash=DirectSplash, load_credentials=Mock(return_value=saved),
                             open_window=Mock(return_value=Mock()), wait_for_window=waited),
                {"_running_instance": Mock(return_value=None), "InstanceMutex": Mock(return_value=mutex),
                 "_prepare": Mock(), "_mineru_token_validity": Mock(return_value=True), "load_credentials": Mock(return_value=saved),
                  "_model_preferences": Mock(return_value=preferences),
                  "_available_port": Mock(return_value=8768), "ChildJob": Mock(), "_start": fake_start, "_health": Mock(),
                 "_write_instance": Mock(), "_clear_instance": Mock()})
        self.assertEqual(result, 0)
        worker, web = environments["worker.log"], environments["web.log"]
        self.assertEqual(web["QB_DESKTOP_EXPORT"], "1")
        self.assertNotIn("QB_DESKTOP_EXPORT", worker)
        self.assertEqual((worker["MINERU_TOKEN"], worker["MINIMAX_API_KEY"], worker["SILICONFLOW_API_KEY"]), ("m-token", "mm-key", "sf-key"))
        self.assertEqual(json.loads(worker["MINERU_TOKENS_JSON"]), ["m-token", "m-token-2"])
        self.assertEqual(json.loads(worker["MINIMAX_API_KEYS_JSON"]), ["mm-key", "mm-key-2"])
        self.assertEqual(json.loads(worker["SILICONFLOW_API_KEYS_JSON"]), ["sf-key", "sf-key-2"])
        for name in app_window.launcher.SECRET_NAMES:
            self.assertNotIn(name, web)
        self.assertEqual(
            (web["QB_MINERU_POOL_SIZE"], web["QB_MINIMAX_POOL_SIZE"], web["QB_SILICONFLOW_POOL_SIZE"]),
            ("2", "2", "2"),
        )
        # 2 MiniMax accounts x 6 + 2 SiliconFlow accounts x 2 simultaneous requests.
        self.assertEqual(worker["QB_PARALLEL"], "16")
        self.assertEqual(web["QB_PARALLEL"], "16")
        self.assertEqual(web["QB_SILICONFLOW_CONFIGURED"], "1")
        for environment in (worker, web):
            self.assertEqual(environment["QB_CREDENTIAL_HOT_RELOAD"], "1")
            self.assertTrue(Path(environment["QB_CREDENTIAL_FILE"]).is_absolute())
            self.assertEqual(environment["QB_PRIMARY_ENGINE"], "siliconflow_qwen3")
            self.assertEqual(environment["QB_CHECKER_ENGINE"], "minimax_m3")
            self.assertEqual(environment["QB_ARBITER_ENGINE"], "checker")
            self.assertEqual(environment["QB_MINIMAX_MODEL"], "MiniMax-M3")
            self.assertEqual(environment["QB_SILICONFLOW_MODEL"], "Qwen/Qwen3-VL-32B-Instruct")
            self.assertTrue(Path(environment["QB_MODEL_PREFERENCES_FILE"]).is_absolute())
            self.assertNotEqual(environment["QB_MODEL_PREFERENCES_FILE"], "C:/untrusted.json")
        waited.assert_called_once()
        for process in processes.values():
            process.terminate.assert_called_once()
        mutex.close.assert_called_once()

    def test_siliconflow_only_primary_completes_desktop_start(self):
        environments = {}
        processes = {}

        def fake_start(args, environment, log_name):
            process = Mock()
            process.poll.return_value = None
            processes[log_name] = process
            environments[log_name] = dict(environment)
            return process, io.BytesIO()

        mutex = Mock(acquired=True)
        waited = Mock()
        saved = {"mineru_token": "m-token", "siliconflow_key": "sf-key"}
        preferences = {
            "primary_engine": "siliconflow_qwen3",
            "checker_engine": "auto",
            "arbiter_engine": "primary",
        }
        with patch.dict(os.environ, {
            "MINERU_TOKEN": "", "MINIMAX_API_KEY": "", "SILICONFLOW_API_KEY": "",
        }):
            result = self.run_patched(
                self.patches(
                    _needs_install=Mock(return_value=False), Splash=DirectSplash,
                    load_credentials=Mock(return_value=saved),
                    open_window=Mock(return_value=Mock()), wait_for_window=waited,
                ),
                {
                    "_running_instance": Mock(return_value=None),
                    "InstanceMutex": Mock(return_value=mutex),
                    "_prepare": Mock(), "_mineru_token_validity": Mock(return_value=True),
                    "load_credentials": Mock(return_value=saved),
                    "_model_preferences": Mock(return_value=preferences),
                    "_available_port": Mock(return_value=8768), "ChildJob": Mock(),
                    "_start": fake_start, "_health": Mock(),
                    "_write_instance": Mock(), "_clear_instance": Mock(),
                },
            )

        self.assertEqual(result, 0)
        worker, web = environments["worker.log"], environments["web.log"]
        self.assertEqual(worker["MINERU_TOKEN"], "m-token")
        self.assertEqual(worker["SILICONFLOW_API_KEY"], "sf-key")
        self.assertNotIn("MINIMAX_API_KEY", worker)
        self.assertEqual(json.loads(worker["MINERU_TOKENS_JSON"]), ["m-token"])
        self.assertEqual(json.loads(worker["SILICONFLOW_API_KEYS_JSON"]), ["sf-key"])
        for name in app_window.launcher.SECRET_NAMES:
            self.assertNotIn(name, web)
        self.assertEqual(web["QB_MINIMAX_CONFIGURED"], "0")
        self.assertEqual(web["QB_SILICONFLOW_CONFIGURED"], "1")
        self.assertEqual(web["QB_MINIMAX_POOL_SIZE"], "0")
        self.assertEqual(web["QB_SILICONFLOW_POOL_SIZE"], "1")
        for environment in (worker, web):
            self.assertEqual(environment["QB_PRIMARY_ENGINE"], "siliconflow_qwen3")
            self.assertEqual(environment["QB_CHECKER_ENGINE"], "auto")
            self.assertEqual(environment["QB_ARBITER_ENGINE"], "primary")
        waited.assert_called_once()
        for process in processes.values():
            process.terminate.assert_called_once()
        mutex.close.assert_called_once()

    def test_missing_credentials_still_open_the_app_without_native_dialog(self):
        environments = {}
        processes = {}

        def fake_start(args, environment, log_name):
            process = Mock()
            process.poll.return_value = None
            processes[log_name] = process
            environments[log_name] = dict(environment)
            return process, io.BytesIO()

        mutex = Mock(acquired=True)
        configure = Mock(return_value=False)
        waited = Mock()
        with patch.dict(os.environ, {
            "MINERU_TOKEN": "", "MINIMAX_API_KEY": "", "SILICONFLOW_API_KEY": "",
        }):
            result = self.run_patched(
                self.patches(
                    _needs_install=Mock(return_value=False), Splash=DirectSplash,
                    load_credentials=Mock(return_value={}), _configure_credentials=configure,
                    open_window=Mock(return_value=Mock()), wait_for_window=waited,
                ),
                {"_running_instance": Mock(return_value=None), "InstanceMutex": Mock(return_value=mutex),
                 "_prepare": Mock(), "_model_preferences": Mock(return_value={
                     "primary_engine": "minimax_m3", "checker_engine": "auto", "arbiter_engine": "primary",
                 }), "_available_port": Mock(return_value=8768), "ChildJob": Mock(),
                 "_start": fake_start, "_health": Mock(), "_write_instance": Mock(),
                 "_clear_instance": Mock()})
        self.assertEqual(result, 0)
        configure.assert_not_called()
        waited.assert_called_once()
        self.assertEqual(environments["web.log"]["QB_MINERU_CONFIGURED"], "0")
        self.assertEqual(environments["web.log"]["QB_MINIMAX_CONFIGURED"], "0")
        for name in app_window.launcher.SECRET_NAMES:
            self.assertNotIn(name, environments["web.log"])
            self.assertNotIn(name, environments["worker.log"])
        mutex.close.assert_called_once()


class ShortcutTests(unittest.TestCase):
    def test_public_brand_is_used_without_changing_internal_launcher(self):
        self.assertEqual(app_window.APP_TITLE, "题有据")
        self.assertEqual(app_window.APP_SUBTITLE, "原卷可追溯的题库整理工具")
        self.assertEqual(create_shortcut.NAME, "题有据")
        self.assertEqual(create_shortcut.LEGACY_NAME, "题库题卡版")
        self.assertEqual(app_window.CONSOLE_LAUNCHER.name, "启动题有据.cmd")
        self.assertTrue(app_window.CONSOLE_LAUNCHER.is_file())

    def test_shortcut_runs_window_launcher_with_project_python(self):
        spec = create_shortcut.shortcut_spec()
        self.assertTrue(spec["target"].endswith("pythonw.exe"))
        self.assertIn("app_launcher.pyw", spec["arguments"])
        self.assertTrue(spec["icon"].endswith("app.ico,0"))
        self.assertEqual(spec["description"], "题有据 · 原卷可追溯的题库整理工具")

    def test_powershell_quotes_paths(self):
        script = create_shortcut.powershell_script(Path("C:/Users/O'Neil/Desktop/题有据.lnk"),
                                                   {"target": "a", "arguments": '"b"', "workdir": "c", "icon": "d,0", "description": "e"})
        self.assertIn("O''Neil", script)
        self.assertIn("题有据.lnk", script)

    def test_rebrand_preserves_legacy_file_without_verified_ownership(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            new_link = folder / "题有据.lnk"
            old_link = folder / "题库题卡版.lnk"
            unrelated = folder / "题库题卡版-我的备份.lnk"
            old_link.write_bytes(b"old")
            unrelated.write_bytes(b"keep")
            create_shortcut.remove_legacy_shortcut(new_link)
            self.assertEqual(old_link.read_bytes(), b"old")
            self.assertTrue(unrelated.exists())

    @unittest.skipUnless(os.name == "nt", "Windows Shell Link only")
    def test_unicode_target_path_survives_real_shell_link(self):
        import pythoncom
        from win32com.shell import shell

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "题有据"
            folder.mkdir()
            target = folder / "pythonw.exe"
            shutil.copy2(sys.executable, target)
            launcher = folder / "app_launcher.pyw"
            launcher.write_text("", encoding="utf-8")
            icon = folder / "app.ico"
            icon.write_bytes(b"")
            link = Path(tmp) / "题有据.lnk"
            spec = {
                "target": str(target),
                "arguments": f'"{launcher}"',
                "workdir": str(folder),
                "icon": f"{icon},0",
                "description": "题有据",
            }

            create_shortcut._write_with_com(link, spec)
            shortcut = pythoncom.CoCreateInstance(
                shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink,
            )
            shortcut.QueryInterface(pythoncom.IID_IPersistFile).Load(str(link))

            # Windows may expand an 8.3 temporary-directory component when a
            # Shell Link is loaded.  Compare file identity rather than the two
            # equivalent path spellings.
            self.assertTrue(os.path.samefile(shortcut.GetPath(shell.SLGP_RAWPATH)[0], target))
            self.assertEqual(shortcut.GetArguments(), f'"{launcher}"')


if __name__ == "__main__":
    unittest.main()
