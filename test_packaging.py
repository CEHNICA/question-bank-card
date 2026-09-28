"""Desktop-package guardrails.  These tests never read the user's real data."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import start_question_bank as launcher
import internal_runtime

sys.path.insert(0, str(Path(__file__).resolve().parent / "packaging"))
from audit_bundle import BundleAuditError, audit_bundle, audit_installer, find_forbidden_files  # noqa: E402


class BundleAuditTests(unittest.TestCase):
    def make_bundle(self, root: Path) -> Path:
        bundle = root / "QuestionBankCard"
        (bundle / "_internal" / "frontend").mkdir(parents=True)
        (bundle / "QuestionBankCard.exe").write_bytes(b"MZ")
        (bundle / "_internal" / "frontend" / "app.js").write_text("safe", encoding="utf-8")
        return bundle

    def test_clean_bundle_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            (bundle / "_internal" / "base_library.zip").write_bytes(b"standard-library")
            certifi = bundle / "_internal" / "certifi" / "cacert.pem"
            certifi.parent.mkdir(parents=True)
            certifi.write_text("-----BEGIN CERTIFICATE-----\npublic CA roots\n", encoding="utf-8")
            licenses = bundle / "THIRD_PARTY_LICENSES" / "example" / "LICENSE.txt"
            licenses.parent.mkdir(parents=True)
            licenses.write_text(
                "A license may document -----BEGIN PRIVATE KEY----- as plain text.",
                encoding="utf-8",
            )
            audit_bundle(bundle)

    def test_private_and_runtime_files_are_rejected_case_insensitively(self):
        forbidden = (
            "db.sqlite3",
            "Credentials.DAT",
            "backend/data/paper/source.PDF",
            "backend/runtime/worker.log",
            "backend/backups/pre-migrate.sqlite3",
            ".venv/pyvenv.cfg",
            "backend/tests/test_views.py",
            "backend/__pycache__/views.pyc",
            ".env.local",
            "backend/logs/launcher.log",
            "backend/cache.db",
            "backend/cache.db-wal",
            "backend/cache.db-shm",
            "private/server.key",
            "private/client.pfx",
            "private/client.crt",
            "notes/review.docx",
            ".git/config",
            "snapshot.TGZ",
            "mineru_result.ZIP",
        )
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            for relative in forbidden:
                path = bundle / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"private")
            self.assertEqual(
                set(find_forbidden_files(bundle)),
                {item.replace("\\", "/") for item in forbidden},
            )
            with self.assertRaises(BundleAuditError) as caught:
                audit_bundle(bundle)
            for relative in forbidden:
                self.assertIn(Path(relative).name, str(caught.exception))

    def test_sensitive_text_is_rejected_without_echoing_secret_content(self):
        sensitive = {
            "config.json": '{"Authorization": "Bearer supersecret0123456789"}',
            "worker.ini": 'api_key = "abcdefghijklmnop1234567890"',
            "private.txt": "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret-key-material",
            "windows-path.txt": r"copied from C:\Users\Alice\Documents\private.pdf",
            "linux-path.txt": "copied from /home/alice/private/data.json",
        }
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            for relative, content in sensitive.items():
                path = bundle / relative
                path.write_text(content, encoding="utf-8")
            self.assertEqual(set(find_forbidden_files(bundle)), set(sensitive))
            with self.assertRaises(BundleAuditError) as caught:
                audit_bundle(bundle)
            message = str(caught.exception)
            for relative in sensitive:
                self.assertIn(relative, message)
            for secret in ("supersecret0123456789", "abcdefghijklmnop1234567890", "Alice", "alice"):
                self.assertNotIn(secret, message)

    def test_source_code_credential_placeholders_are_not_treated_as_real_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.make_bundle(Path(tmp))
            (bundle / "_internal" / "frontend" / "app.js").write_text(
                'headers.Authorization = "Bearer " + token; const api_key = settings.api_key;',
                encoding="utf-8",
            )
            audit_bundle(bundle)

    def test_missing_main_executable_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "QuestionBankCard"
            bundle.mkdir()
            with self.assertRaisesRegex(BundleAuditError, "缺少主程序"):
                audit_bundle(bundle)

    def test_installer_must_be_a_realistic_windows_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            installer = Path(tmp) / "TiYouJu-Setup.exe"
            installer.write_bytes(b"MZ" + b"\0" * (1024 * 1024))
            audit_installer(installer)
            installer.write_bytes(b"NO" + b"\0" * (1024 * 1024))
            with self.assertRaisesRegex(BundleAuditError, "PE 文件头"):
                audit_installer(installer)


class DistributionLicenseTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent

    def test_complete_license_materials_are_versioned(self):
        self.assertGreater((self.ROOT / "LICENSE").stat().st_size, 30_000)
        license_root = self.ROOT / "THIRD_PARTY_LICENSES"
        expected = {
            "Python-3.12",
            "Django-5.2.17",
            "Pillow-12.3.0",
            "PyMuPDF-MuPDF-1.28.2",
            "pywin32-312",
            "requests-2.34.2",
            "KaTeX-0.18.9",
            "PyInstaller-6.22.3",
            "Inno-Setup-7.1.0",
            "packaging-26.3",
            "setuptools-84.0.0",
        }
        self.assertTrue((license_root / "README.md").is_file())
        self.assertTrue(expected.issubset({path.name for path in license_root.iterdir()}))

    def test_build_and_installer_ship_license_materials(self):
        build = (self.ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")
        installer = (self.ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
        source_template = (self.ROOT / "packaging" / "CORRESPONDING_SOURCE.template.txt").read_text(
            encoding="utf-8"
        )
        self.assertIn("$ThirdPartyLicenses", build)
        self.assertIn("Copy-Item -LiteralPath $ProjectLicense", build)
        self.assertIn("LicenseFile=..\\LICENSE", installer)
        self.assertIn('{app}\\THIRD_PARTY_LICENSES', installer)
        self.assertIn("OutputBaseFilename=TiYouJu-Setup-", installer)
        self.assertIn("'SHA256SUMS.txt'", build)
        self.assertIn("[System.Text.UTF8Encoding]::new($false)", build)
        self.assertNotIn("-Encoding ascii", build)
        self.assertIn("CORRESPONDING_SOURCE.txt", build)
        self.assertIn("CORRESPONDING_SOURCE.template.txt", build)
        self.assertIn("[System.Text.UTF8Encoding]::new($false, $true)", build)
        self.assertIn("题有据 {{VERSION}}——对应源码", source_template)
        self.assertIn("releases/tag/v{{VERSION}}", source_template)
        self.assertIn("question-bank-card-{{VERSION}}-source.zip", source_template)
        self.assertIn("5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249", source_template)
        self.assertIn("44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156", source_template)
        self.assertNotIn("é¢˜", source_template)

    def test_rebrand_keeps_upgrade_and_user_data_identity_stable(self):
        build = (self.ROOT / "packaging" / "build.ps1").read_text(encoding="utf-8")
        installer = (self.ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
        self.assertIn("[string]$Version = '1.3.0'", build)
        self.assertIn(r"StringStruct('ProductName', '\u9898\u6709\u636e')", build)
        self.assertIn('#define MyAppName "题有据"', installer)
        self.assertIn('#define MyAppExeName "QuestionBankCard.exe"', installer)
        self.assertIn("AppId={{8C1BC21C-A8B7-4E81-9E25-59032D0967D8}", installer)
        self.assertIn("DefaultDirName={localappdata}\\Programs\\QuestionBankCard", installer)
        self.assertIn('Name: "{localappdata}\\QuestionBankCard"', installer)
        self.assertIn("UsePreviousGroup=no", installer)
        self.assertIn('Name: "{userdesktop}\\{#MyLegacyAppName}.lnk"', installer)
        self.assertIn('Type: files; Name: "{userprograms}\\{#MyLegacyAppName}.lnk"', installer)
        self.assertIn('Type: files; Name: "{userprograms}\\{#MyLegacyAppName}\\{#MyLegacyAppName}.lnk"', installer)
        self.assertIn('Type: dirifempty; Name: "{userprograms}\\{#MyLegacyAppName}"', installer)
        self.assertNotIn('Type: filesandordirs; Name: "{userprograms}\\{#MyLegacyAppName}"', installer)


class Utf8ChildProcessTests(unittest.TestCase):
    def test_start_overrides_poisoned_encoding_and_writes_chinese_log(self):
        """Regression test for desktop uploads failing before process_paper ran."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            backend = root / "backend"
            runtime = root / "runtime"
            backend.mkdir()
            (backend / "manage.py").write_text(
                "print('处理试卷：中文文件名.pdf', flush=True)\n",
                encoding="utf-8",
            )
            poisoned = dict(os.environ)
            poisoned.update({"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"})
            with patch.object(launcher, "PYTHON", Path(sys.executable)), \
                    patch.object(launcher, "BACKEND", backend), \
                    patch.object(launcher, "RUNTIME", runtime):
                process, stream = launcher._start(["ignored"], poisoned, "worker.log")
                try:
                    self.assertEqual(process.wait(timeout=15), 0)
                finally:
                    stream.close()
            content = (runtime / "worker.log").read_text(encoding="utf-8")
            self.assertIn("处理试卷：中文文件名.pdf", content)

    @unittest.skipUnless(os.name == "nt", "pythonw.exe is a Windows executable")
    def test_real_pythonw_child_uses_utf8_environment(self):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("pythonw.exe is not installed next to this interpreter")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "probe.py"
            log = root / "probe.log"
            script.write_text("print('后台工作者：中文正常', flush=True)\n", encoding="utf-8")
            source = dict(os.environ)
            source.update({"PYTHONIOENCODING": "ascii", "PYTHONUTF8": "0"})
            environment = launcher._child_environment(source)
            with log.open("wb") as output:
                process = subprocess.Popen(
                    [str(pythonw), str(script)],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(process.wait(timeout=15), 0)
            self.assertEqual(log.read_text(encoding="utf-8").strip(), "后台工作者：中文正常")

    @unittest.skipUnless(os.name == "nt", "the packaged entry point targets Windows")
    def test_real_pythonw_internal_role_writes_utf8_log(self):
        """Exercise the .pyw dispatcher, not only the environment helper."""
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if not pythonw.is_file():
            self.skipTest("pythonw.exe is not installed next to this interpreter")
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "internal.log"
            environment = dict(os.environ)
            environment.update({
                "PYTHONIOENCODING": "ascii",
                "PYTHONUTF8": "0",
                "QB_INTERNAL_LOG": str(log),
            })
            process = subprocess.Popen(
                [str(pythonw), str(Path(__file__).resolve().parent / "app_launcher.pyw"),
                 "--internal-role", "_utf8_probe"],
                cwd=Path(__file__).resolve().parent,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(process.wait(timeout=15), 0)
            self.assertEqual(log.read_text(encoding="utf-8").strip(), "处理试卷 中文文件名.pdf")


class FrozenRuntimeTests(unittest.TestCase):
    def test_child_environment_is_utf8_data_scoped_and_secret_free_by_default(self):
        environment = launcher._child_environment({
            "Path": "kept",
            "PYTHONIOENCODING": "ascii",
            "PYTHONUTF8": "0",
            "MINERU_TOKEN": "private",
            "minimax_api_key": "also-private",
            "SILICONFLOW_API_KEY": "third-private",
        })
        self.assertEqual(environment["Path"], "kept")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(environment["PYTHONUTF8"], "1")
        self.assertEqual(environment["QB_DATABASE"], str(launcher.DATABASE))
        self.assertEqual(environment["QB_DATA_ROOT"], str(launcher.DATA_ROOT))
        self.assertEqual(environment["QB_FRONTEND_ROOT"], str(launcher.FRONTEND))
        for name in launcher.SECRET_NAMES:
            self.assertNotIn(name.casefold(), {key.casefold() for key in environment})

    def test_keep_secrets_is_explicit(self):
        environment = launcher._child_environment({"MINERU_TOKEN": "private"}, keep_secrets=True)
        self.assertEqual(environment["MINERU_TOKEN"], "private")
        self.assertEqual(environment["PYTHONIOENCODING"], "utf-8")

    def test_frozen_service_command_reenters_current_executable(self):
        executable = r"C:\Program Files\QuestionBankCard\QuestionBankCard.exe"
        with patch.object(launcher, "FROZEN", True), patch.object(launcher.sys, "executable", executable):
            self.assertEqual(
                launcher._service_command("runserver", "127.0.0.1:8768", "--noreload"),
                [executable, "--internal-role", "runserver", "127.0.0.1:8768", "--noreload"],
            )

    def test_frozen_import_separates_read_only_resources_from_user_data(self):
        """Simulate PyInstaller before importing the launcher in an isolated process."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / "installed" / "_internal"
            local = root / "LocalAppData"
            executable = root / "installed" / "QuestionBankCard.exe"
            resources.mkdir(parents=True)
            local.mkdir()
            code = "\n".join((
                "import json, os, sys",
                f"sys.frozen = True",
                f"sys._MEIPASS = {str(resources)!r}",
                f"sys.executable = {str(executable)!r}",
                f"os.environ['LOCALAPPDATA'] = {str(local)!r}",
                "os.environ.pop('QB_USER_ROOT', None)",
                "import start_question_bank as item",
                "print(json.dumps({name: str(getattr(item, name)) for name in "
                "['RESOURCE_ROOT', 'PROGRAM_ROOT', 'BACKEND', 'FRONTEND', 'USER_ROOT', "
                "'DATABASE', 'DATA_ROOT', 'RUNTIME', 'BACKUPS']}))",
            ))
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=Path(__file__).resolve().parent,
                env=launcher._child_environment(os.environ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=15,
                check=True,
            )
            paths = {name: Path(value) for name, value in json.loads(completed.stdout).items()}
            self.assertEqual(paths["RESOURCE_ROOT"], resources.resolve())
            self.assertEqual(paths["PROGRAM_ROOT"], executable.resolve().parent)
            self.assertEqual(paths["BACKEND"], resources.resolve() / "backend")
            self.assertEqual(paths["FRONTEND"], resources.resolve() / "frontend")
            expected_user = (local / "QuestionBankCard").resolve()
            self.assertEqual(paths["USER_ROOT"], expected_user)
            for name in ("DATABASE", "DATA_ROOT", "RUNTIME", "BACKUPS"):
                self.assertTrue(paths[name].is_relative_to(expected_user), f"{name} escaped the user data root")
                self.assertFalse(paths[name].is_relative_to(resources.resolve()), f"{name} leaked into install resources")

    def test_internal_runtime_rejects_unlisted_roles(self):
        with self.assertRaisesRegex(ValueError, "不支持的内部角色"):
            internal_runtime._run("arbitrary-command", [])


if __name__ == "__main__":
    unittest.main()
