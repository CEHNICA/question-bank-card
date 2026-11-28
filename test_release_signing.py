"""发布签名门禁：只使用虚构凭据、文件和证书，不运行安装包。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PWSH = shutil.which("pwsh")


def ps_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(PWSH, "需要 PowerShell 7")
class ReleaseSigningTests(unittest.TestCase):
    def run_ps(self, command: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=20,
        )

    def preflight(self, values: dict[str, str]) -> tuple[subprocess.CompletedProcess, str]:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output.txt"
            env = os.environ.copy()
            for name in (
                "SIGNPATH_API_TOKEN",
                "SIGNPATH_ORG_ID",
                "SIGNPATH_PROJECT_SEC",
                "SIGNPATH_PROJECT_VAR",
                "SIGNPATH_POLICY_SEC",
                "SIGNPATH_POLICY_VAR",
            ):
                env.pop(name, None)
            env.update(values)
            env["GITHUB_OUTPUT"] = str(output)
            result = self.run_ps("& " + ps_literal(ROOT / "packaging/check_release_signing.ps1"), env)
            return result, output.read_text(encoding="utf-8-sig") if output.exists() else ""

    def test_missing_or_blank_credentials_stop_release(self):
        for values in (
            {},
            {"SIGNPATH_API_TOKEN": "fake-token"},
            {"SIGNPATH_ORG_ID": "fake-org"},
            {"SIGNPATH_API_TOKEN": " ", "SIGNPATH_ORG_ID": "fake-org"},
        ):
            with self.subTest(values=values):
                result, output = self.preflight(values)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("SIGNPATH_", result.stderr)
                self.assertEqual(output, "")

    def test_complete_config_passes_without_printing_credentials(self):
        result, output = self.preflight({"SIGNPATH_API_TOKEN": "fake-private-token", "SIGNPATH_ORG_ID": "fake-org"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project_slug=question-bank-card", output)
        self.assertIn("policy_slug=release-signing", output)
        self.assertNotIn("fake-private-token", result.stdout + result.stderr + output)

    def test_custom_config_and_invalid_output_injection(self):
        values = {
            "SIGNPATH_API_TOKEN": "fake-token",
            "SIGNPATH_ORG_ID": "fake-org",
            "SIGNPATH_PROJECT_VAR": "my-project",
            "SIGNPATH_POLICY_SEC": "approved-policy",
        }
        result, output = self.preflight(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("project_slug=my-project", output)
        self.assertIn("policy_slug=approved-policy", output)
        values["SIGNPATH_PROJECT_VAR"] = "bad\npolicy_slug=wrong"
        result, output = self.preflight(values)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, "")

    def verify(self, signature_body: str, *, missing_installer: bool = False) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("QuestionBankCard.exe", "tiyouju.exe", "setup.exe"):
                if name != "setup.exe" or not missing_installer:
                    (root / name).write_bytes(b"MZ-test-only")
            command = (
                "$ErrorActionPreference = 'Stop'; function Get-AuthenticodeSignature { param($LiteralPath) "
                + signature_body
                + " }; & "
                + ps_literal(ROOT / "packaging/verify_release_signatures.ps1")
                + " -BundleRoot "
                + ps_literal(root)
                + " -InstallerPath "
                + ps_literal(root / "setup.exe")
            )
            return self.run_ps(command)

    def test_all_three_trusted_timestamped_files_pass(self):
        result = self.verify(
            "[pscustomobject]@{Status='Valid'; SignerCertificate=@{Thumbprint='same'}; TimeStamperCertificate=@{Subject='TSA'}}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count(".exe"), 3)

    def test_unsigned_or_invalid_signature_stops_release(self):
        for status in ("NotSigned", "HashMismatch", "NotTrusted", "UnknownError"):
            with self.subTest(status=status):
                result = self.verify(
                    f"[pscustomobject]@{{Status='{status}'; SignerCertificate=$null; TimeStamperCertificate=$null}}"
                )
                self.assertNotEqual(result.returncode, 0)

    def test_missing_timestamp_stops_release(self):
        result = self.verify(
            "[pscustomobject]@{Status='Valid'; SignerCertificate=@{Thumbprint='same'}; TimeStamperCertificate=$null}"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_different_installer_publisher_stops_release(self):
        result = self.verify(
            "$key = if ($LiteralPath.EndsWith('setup.exe')) {'other'} else {'same'}; [pscustomobject]@{Status='Valid'; SignerCertificate=@{Thumbprint=$key}; TimeStamperCertificate=@{Subject='TSA'}}"
        )
        self.assertNotEqual(result.returncode, 0)

    def test_missing_installer_stops_release(self):
        result = self.verify(
            "[pscustomobject]@{Status='Valid'; SignerCertificate=@{Thumbprint='same'}; TimeStamperCertificate=@{Subject='TSA'}}",
            missing_installer=True,
        )
        self.assertNotEqual(result.returncode, 0)


class ReleaseWorkflowTests(unittest.TestCase):
    def test_signing_is_required_before_build_and_publication(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("./packaging/check_release_signing.ps1"), workflow.index("name: Build the installer")
        )
        self.assertLess(
            workflow.index("./packaging/verify_release_signatures.ps1"),
            workflow.index("name: Assemble the public assets"),
        )
        self.assertNotIn("signpath.outputs.enabled", workflow)
        self.assertIn("test_release_signing", workflow)
        self.assertIn("test_release_signing", (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
