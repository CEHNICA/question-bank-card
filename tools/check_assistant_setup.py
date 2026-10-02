"""Verify real Windows shortcuts and the frozen CLI in temporary directories.

This never starts the app, contacts a service, installs into a real AI client's
skills directory, or changes the actual Desktop. Requires an audited bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import assistant_setup as setup  # noqa: E402


def digest(file: Path) -> str | None:
    return hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    assert os.name == "nt", "requires real Windows shortcut support"
    cli = bundle / "tiyouju.exe"
    app = bundle / "QuestionBankCard.exe"
    assert cli.is_file() and app.is_file(), "audited bundle is incomplete"
    resources = bundle / "_internal" / "skills" / "tiyouju"
    assert setup._manifest(resources) == setup._manifest(ROOT / "skills" / "tiyouju")
    actual_link = setup.windows_desktop() / "题有据.lnk"
    before_link = digest(actual_link)
    checks = []

    with tempfile.TemporaryDirectory(prefix="tiyouju-native-setup-") as temporary:
        test_root = Path(temporary).resolve()
        isolated_app = test_root / "app" / "QuestionBankCard.exe"
        isolated_app.parent.mkdir()
        shutil.copyfile(app, isolated_app)
        shutil.copyfile(cli, isolated_app.parent / "tiyouju.exe")
        desktop = test_root / "OneDrive" / "Desktop"
        desktop.mkdir(parents=True)
        env = os.environ.copy()
        env["TIYOUJU_APP"] = str(isolated_app)
        env["PYTHONUTF8"] = "1"

        def run(*arguments, expected=0):
            result = subprocess.run(
                [str(cli), "assistant-setup", *arguments, "--json"],
                env=env, capture_output=True, encoding="utf-8", timeout=40,
            )
            assert result.returncode == expected, result.stderr or result.stdout
            return json.loads(result.stdout)

        default = run()
        assert default["software"]["version"] == setup.executable_version(app)
        assert default["software"]["version_verified"]
        assert default["skill"]["available"] and not default["skill"]["changed"]
        assert not default["desktop"]["changed"]
        assert {q["id"] for q in default["questions"]} == {"install_skill", "desktop_icon"}
        checks.append("frozen CLI reports real product version and asks both choices without changes")

        parent = test_root / "client" / "skills"
        first = run("--skill-dir", str(parent))
        assert first["skill"]["verified"] and first["skill"]["changed"]
        assert setup._manifest(parent / "tiyouju") == setup._manifest(resources)
        again = run("--skill-dir", str(parent))
        assert again["skill"]["status"] == "already_installed"
        assert not again["skill"]["changed"]
        checks.append("complete bundled skill installs and repeated requests are idempotent")

        customized = parent / "tiyouju" / "my-custom-note.md"
        customized.write_text("Keep this user customization.", encoding="utf-8")
        custom_manifest = setup._manifest(parent / "tiyouju")
        conflict = run("--skill-dir", str(parent), expected=3)
        assert "未覆盖" in conflict["error"] and customized.is_file()
        replaced = run("--skill-dir", str(parent), "--replace-skill")
        assert setup._manifest(Path(replaced["skill"]["backup"])) == custom_manifest
        assert setup._manifest(parent / "tiyouju") == setup._manifest(resources)
        checks.append("custom skill survives refusal and explicit replacement retains a verified backup")

        com = setup.WindowsShortcuts()
        link = desktop / "题有据.lnk"
        shown = setup.assistant_setup(isolated_app, desktop="show", resources=resources, desktop_path=desktop)
        assert shown["desktop"]["verified"] and link.is_file()
        assert Path(com.read(link)["target"]).resolve() == isolated_app.resolve()
        original = digest(link)
        repeated = setup.assistant_setup(isolated_app, desktop="show", resources=resources, desktop_path=desktop)
        assert not repeated["desktop"]["changed"] and digest(link) == original
        hidden = setup.assistant_setup(isolated_app, desktop="hide", resources=resources, desktop_path=desktop)
        backup = Path(hidden["desktop"]["backup"])
        assert not link.exists() and digest(backup) == original
        restored = setup.assistant_setup(isolated_app, desktop="show", resources=resources, desktop_path=desktop)
        assert restored["desktop"]["verified"] and digest(link) == original
        checks.append("real Windows COM creates, verifies, hides and restores the exact shortcut")

        setup.assistant_setup(isolated_app, desktop="hide", resources=resources, desktop_path=desktop)
        com.create(link, cli)
        conflicting = digest(link)
        for choice in ("show", "hide"):
            try:
                setup.assistant_setup(isolated_app, desktop=choice, resources=resources, desktop_path=desktop)
            except setup.SetupError:
                pass
            else:
                raise AssertionError("another application's shortcut was accepted")
            assert digest(link) == conflicting
        checks.append("same-name shortcut to another executable remains untouched")

    assert digest(actual_link) == before_link, "actual Desktop shortcut changed"
    report = {"version": setup.executable_version(app), "passed": True,
              "actual_desktop_unchanged": True, "paid_services_called": False, "checks": checks}
    if args.report:
        args.report.resolve().write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
