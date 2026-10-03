"""Shortcut routing and ownership checks; real Windows checks use temporary folders."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import assistant_setup as setup
import create_shortcut as shortcut


class FileShortcuts:
    def read(self, path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise setup.SetupError("unreadable shortcut") from error


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "题有据 测试"
        self.root.mkdir()
        self.desktop = self.root / "OneDrive" / "桌面"
        self.desktop.mkdir(parents=True)
        self.menu = self.root / "开始菜单"
        self.menu.mkdir()
        self.app = self.root / "QuestionBankCard.exe"
        self.cli = self.root / "tiyouju.exe"
        self.pythonw = self.root / "pythonw.exe"
        self.launcher = self.root / "app_launcher.pyw"
        for file in (self.app, self.cli, self.pythonw, self.launcher):
            file.write_bytes(b"fixture, never executed")
        self.spec = {"target": str(self.pythonw), "arguments": f'"{self.launcher}"',
                     "workdir": str(self.root), "icon": str(self.root / "app.ico") + ",0", "description": "题有据"}
        self.reader = FileShortcuts()
        self.link = self.desktop / "题有据.lnk"

    @staticmethod
    def write_link(path, spec):
        path.write_text(json.dumps({"target": spec["target"], "arguments": spec["arguments"],
                                    "working_dir": spec["workdir"]}), encoding="utf-8")

    def installed_reply(self, *, returncode=0, result=None):
        def run(cli, app, arguments):
            self.assertEqual((cli, app), (self.cli, self.app))
            if arguments == ["--help"]:
                return subprocess.CompletedProcess([], 0, stdout="assistant-setup --desktop show|hide", stderr="")
            self.assertEqual(arguments, ["--desktop", "show", "--json"])
            data = result
            if data is None:
                self.write_link(self.link, {"target": str(self.app), "arguments": "", "workdir": str(self.root)})
                data = {"desktop": {"status": "shown", "verified": True, "path": str(self.link), "target": str(self.app)}}
            return subprocess.CompletedProcess([], returncode, stdout=json.dumps(data), stderr="")
        return run

    def run_main(self, installed, run_cli=None):
        output = io.StringIO()
        with mock.patch.object(shortcut, "_is_windows", return_value=True), \
                mock.patch.object(shortcut, "_installed_paths", return_value=installed), \
                mock.patch.object(shortcut, "_run_cli", side_effect=run_cli), \
                mock.patch.object(setup, "windows_desktop", return_value=self.desktop), \
                mock.patch.object(setup, "WindowsShortcuts", return_value=self.reader), \
                contextlib.redirect_stdout(output):
            code = shortcut.main()
        return code, output.getvalue()

    def test_installed_cli_is_used_without_a_source_environment_and_read_back(self):
        with mock.patch.object(shortcut, "VENV_PYTHONW", self.root / "missing.exe"), \
                mock.patch.object(shortcut, "create", side_effect=AssertionError("source fallback forbidden")):
            code, output = self.run_main((self.app, self.cli), self.installed_reply())
        self.assertEqual(code, 0)
        self.assertIn("安装版", output)
        self.assertEqual(self.reader.read(self.link)["target"], str(self.app))

    def test_installed_failure_keeps_existing_foreign_link_and_never_uses_source(self):
        self.link.write_bytes(b"another application's link")
        original = self.link.read_bytes()
        with mock.patch.object(shortcut, "create", side_effect=AssertionError("source fallback forbidden")):
            code, output = self.run_main((self.app, self.cli), self.installed_reply(
                returncode=3, result={"error": "同名图标属于其他软件，未覆盖"}))
        self.assertEqual(code, 1)
        self.assertIn("未覆盖", output)
        self.assertEqual(self.link.read_bytes(), original)

    def test_old_installed_cli_and_incomplete_install_do_not_fall_back(self):
        with mock.patch.object(shortcut, "create", side_effect=AssertionError("source fallback forbidden")):
            code, output = self.run_main((self.app, self.cli), lambda *_: subprocess.CompletedProcess([], 2, stdout="", stderr=""))
            self.assertEqual(code, 1)
            self.assertIn("请升级", output)
            self.cli.unlink()
            run = mock.Mock(side_effect=AssertionError("incomplete installation must not run"))
            code, output = self.run_main((self.app, self.cli), run)
            self.assertEqual(code, 1)
            self.assertIn("不完整", output)
            run.assert_not_called()
        self.assertFalse(self.link.exists())

    def test_success_requires_verified_matching_json_and_actual_shortcut_target(self):
        for result in (
            [], {"desktop": {"status": "shown", "verified": False}},
            {"desktop": {"status": "shown", "verified": True, "target": ["wrong"]}},
            {"desktop": {"status": "shown", "verified": True, "target": str(self.app), "path": str(self.link)}},
        ):
            with self.subTest(result=result):
                code, output = self.run_main((self.app, self.cli), self.installed_reply(result=result))
                self.assertEqual(code, 1)
                self.assertNotIn("已显示并核验", output)
        self.assertFalse(self.link.exists())

    def test_report_cannot_hide_a_different_actual_target(self):
        self.write_link(self.link, self.spec)
        result = {"desktop": {"status": "shown", "verified": True, "target": str(self.app), "path": str(self.link)}}
        code, output = self.run_main((self.app, self.cli), self.installed_reply(result=result))
        self.assertEqual(code, 1)
        self.assertIn("回读校验失败", output)
        self.assertEqual(self.reader.read(self.link)["target"], str(self.pythonw))

    def test_no_install_and_missing_source_stops_before_any_folder_lookup(self):
        with mock.patch.object(shortcut, "VENV_PYTHONW", self.root / "missing.exe"), \
                mock.patch.object(shortcut, "_special_folder", side_effect=AssertionError("must not inspect/write Desktop")):
            code, output = self.run_main(None)
        self.assertEqual(code, 1)
        self.assertIn("源码运行环境也未准备好", output)
        self.assertFalse(self.link.exists())

    def test_desktop_is_the_known_folder_including_onedrive_without_powershell(self):
        with mock.patch.object(setup, "windows_desktop", return_value=self.desktop) as native, \
                mock.patch.object(shortcut.subprocess, "run", side_effect=AssertionError("no string-decoded Desktop")):
            self.assertEqual(shortcut._special_folder("Desktop"), self.desktop)
        native.assert_called_once_with()

    def test_source_preserves_foreign_and_unreadable_links_and_same_target_is_idempotent(self):
        with mock.patch.object(shortcut, "_write_with_com", side_effect=AssertionError("must not rewrite")):
            for value in (b"unknown shortcut", json.dumps({"target": str(self.app), "arguments": "", "working_dir": str(self.root)}).encode()):
                self.link.write_bytes(value)
                with self.assertRaises(setup.SetupError):
                    shortcut.create(self.link, spec=self.spec, shortcuts=self.reader)
                self.assertEqual(self.link.read_bytes(), value)
            self.write_link(self.link, self.spec)
            original = self.link.read_bytes()
            self.assertFalse(shortcut.create(self.link, spec=self.spec, shortcuts=self.reader))
            self.assertEqual(self.link.read_bytes(), original)

    def test_source_only_publishes_verified_link_and_removes_staging_on_failure(self):
        wrong = {**self.spec, "target": str(self.app)}
        with mock.patch.object(shortcut, "_write_with_com", side_effect=lambda path, _spec: self.write_link(path, wrong)):
            with self.assertRaisesRegex(setup.SetupError, "校验失败"):
                shortcut.create(self.link, spec=self.spec, shortcuts=self.reader)
        self.assertFalse(self.link.exists())
        self.assertFalse(list(self.desktop.glob(".tiyouju-shortcut-*")))
        with mock.patch.object(shortcut, "_write_with_com", side_effect=self.write_link):
            self.assertTrue(shortcut.create(self.link, spec=self.spec, shortcuts=self.reader))
        self.assertEqual(self.reader.read(self.link)["arguments"], self.spec["arguments"])

    def test_source_does_not_replace_a_link_created_while_the_new_link_is_staged(self):
        original_rename = setup._rename
        def race(stage, target):
            target.write_bytes(b"concurrent foreign link")
            original_rename(stage, target)
        with mock.patch.object(shortcut, "_write_with_com", side_effect=self.write_link), \
                mock.patch.object(setup, "_rename", side_effect=race):
            with self.assertRaises(setup.SetupError):
                shortcut.create(self.link, spec=self.spec, shortcuts=self.reader)
        self.assertEqual(self.link.read_bytes(), b"concurrent foreign link")
        self.assertFalse(list(self.desktop.glob(".tiyouju-shortcut-*")))

    def test_only_verified_legacy_source_link_is_removed(self):
        legacy = self.link.with_name("题库题卡版.lnk")
        for data in (b"unreadable", json.dumps({"target": str(self.app), "arguments": "", "working_dir": str(self.root)}).encode()):
            legacy.write_bytes(data)
            shortcut.remove_legacy_shortcut(self.link, spec=self.spec, shortcuts=self.reader)
            self.assertEqual(legacy.read_bytes(), data)
        self.write_link(legacy, self.spec)
        shortcut.remove_legacy_shortcut(self.link, spec=self.spec, shortcuts=self.reader)
        self.assertFalse(legacy.exists())

    def test_source_main_creates_only_in_injected_desktop_and_start_menu(self):
        folders = {"Desktop": self.desktop, "Programs": self.menu}
        with mock.patch.object(shortcut, "VENV_PYTHONW", self.pythonw), \
                mock.patch.object(shortcut, "LAUNCHER", self.launcher), \
                mock.patch.object(shortcut, "shortcut_spec", return_value=self.spec), \
                mock.patch.object(shortcut, "_special_folder", side_effect=lambda name: folders[name]), \
                mock.patch.object(shortcut, "_write_with_com", side_effect=self.write_link):
            code, output = self.run_main(None)
        self.assertEqual(code, 0)
        self.assertIn("源码版", output)
        self.assertEqual(self.reader.read(self.link)["target"], str(self.pythonw))
        self.assertTrue((self.menu / "题有据.lnk").is_file())

    def test_failed_powershell_write_reports_a_clear_error_without_encoded_command(self):
        error = subprocess.CalledProcessError(1, ["powershell.exe", "-EncodedCommand", "large-encoded-command"])
        with mock.patch.object(shortcut.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(setup.SetupError, "Windows 无法写入") as caught:
                shortcut._write_with_powershell(self.link, self.spec)
        self.assertNotIn("large-encoded-command", str(caught.exception))
        self.assertFalse(self.link.exists())

    @unittest.skipUnless(os.name == "nt", "real Windows shortcut")
    def test_real_powershell_fallback_keeps_unicode_paths_and_source_arguments(self):
        # Real Shell validation should receive an actual PE and icon, not the
        # placeholder bytes used by the unit tests. Neither is executed.
        shutil.copyfile(sys.executable, self.pythonw)
        shutil.copyfile(shortcut.ICON, self.root / "app.ico")
        with mock.patch.object(shortcut, "_write_with_com", side_effect=ImportError("pywin32 unavailable")):
            try:
                self.assertTrue(shortcut.create(self.link, spec=self.spec))
            except setup.SetupError as error:
                cause = error.__cause__
                if isinstance(cause, subprocess.CalledProcessError):
                    # This fixture contains no user data. Keep the native Windows
                    # diagnostic visible rather than losing it behind exit code 1.
                    diagnostic = cause.stderr or b""
                    if isinstance(diagnostic, bytes):
                        diagnostic = diagnostic.decode("utf-8", errors="replace")
                    self.fail(f"{error}\nWindows shortcut diagnostic:\n{diagnostic[:8000]}")
                raise
        info = setup.WindowsShortcuts().read(self.link)
        self.assertTrue(shortcut._same_path(info["target"], self.pythonw))
        self.assertEqual(info["arguments"], self.spec["arguments"])


@unittest.skipUnless(os.name == "nt", "Windows cmd and temporary native CLI fixture")
class ShortcutBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.fake_exe = cls.root / "fixture.exe"
        code = r"""
using System;
using System.IO;
class ShortcutFixture {
    static int Main(string[] args) {
        string log = Environment.GetEnvironmentVariable("QB_SHORTCUT_TEST_LOG");
        File.AppendAllText(log, Path.GetFileName(Environment.GetCommandLineArgs()[0]) + "|" + String.Join(" ", args) + "\n");
        if (args.Length > 0 && args[0] == "assistant-setup") {
            if (Array.IndexOf(args, "--help") >= 0) {
                Console.WriteLine("assistant-setup --desktop show|hide");
                return Environment.GetEnvironmentVariable("QB_SHORTCUT_TEST_CAPABILITY") == "missing" ? 2 : 0;
            }
            return Int32.Parse(Environment.GetEnvironmentVariable("QB_SHORTCUT_TEST_EXIT") ?? "0");
        }
        return 19;
    }
}
"""
        script = "Add-Type -TypeDefinition '" + code.replace("'", "''") + "' -OutputAssembly '" + str(cls.fake_exe).replace("'", "''") + "' -OutputType ConsoleApplication"
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, check=True, timeout=45)

    def run_batch(self, *, installed, exit_code=0, capability="ok"):
        with tempfile.TemporaryDirectory(dir=self.root) as temporary:
            folder = Path(temporary)
            cmd = folder / "创建桌面图标.cmd"
            shutil.copyfile(shortcut.ROOT / "创建桌面图标.cmd", cmd)
            local = folder / "local"
            if installed:
                directory = local / "Programs" / "QuestionBankCard"
                directory.mkdir(parents=True)
                (directory / "QuestionBankCard.exe").write_bytes(b"not launched")
                shutil.copyfile(self.fake_exe, directory / "tiyouju.exe")
            source_python = folder / "backend" / ".venv" / "Scripts" / "python.exe"
            source_python.parent.mkdir(parents=True)
            shutil.copyfile(self.fake_exe, source_python)
            log = folder / "calls.txt"
            env = dict(os.environ, LOCALAPPDATA=str(local), QB_SHORTCUT_TEST_LOG=str(log),
                       QB_SHORTCUT_TEST_EXIT=str(exit_code), QB_SHORTCUT_TEST_CAPABILITY=capability)
            result = subprocess.run(["cmd.exe", "/d", "/c", str(cmd)], input=b"\n", capture_output=True, env=env, timeout=20)
            return result, log.read_text(encoding="utf-8").splitlines()

    def test_batch_installed_route_does_not_run_source_python(self):
        result, calls = self.run_batch(installed=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, ["tiyouju.exe|assistant-setup --help", "tiyouju.exe|assistant-setup --desktop show"])

    def test_batch_preserves_failure_exit_after_pause_and_never_uses_source(self):
        result, calls = self.run_batch(installed=True, exit_code=3)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(len(calls), 2)
        result, calls = self.run_batch(installed=True, capability="missing")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(calls, ["tiyouju.exe|assistant-setup --help"])

    def test_batch_without_install_uses_source_and_preserves_its_exit(self):
        result, calls = self.run_batch(installed=False)
        self.assertEqual(result.returncode, 19)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("python.exe|"))
        self.assertIn("create_shortcut.py", calls[0])


if __name__ == "__main__":
    unittest.main()
