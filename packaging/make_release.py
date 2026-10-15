"""Assemble versioned GitHub assets from a clean, reviewed source commit.

Does not install or publish. Only maintained source, audited binaries, public
dependency source and explicitly successful synthetic evidence are included.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import make_local_acceptance as common

ROOT = common.ROOT


def public_report(value):
    if isinstance(value, dict):
        return {key: public_report(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_report(item) for item in value]
    if isinstance(value, str):
        value = value.replace(str(ROOT), "<checkout>").replace(ROOT.as_posix(), "<checkout>")
        return re.sub(r"(?i)[a-z]:[\\/]Users[\\/][^\\/\s\"']+", "<user>", value)
    return value


def assemble(version, reports):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a numeric application version")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise ValueError("Commit the reviewed source before assembling the public release")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    output = common.confined(ROOT / "packaging/dist/releases" / f"{version}-public", ROOT / "packaging/dist")
    if output.exists():
        raise ValueError("Use a fresh release directory; published assets are not overwritten")
    output.mkdir(parents=True)
    bundle = ROOT / "packaging/dist/QuestionBankCard"
    installer = ROOT / "packaging/dist/installer" / f"TiYouJu-Setup-{version}.exe"
    if not installer.is_file() or not (bundle / "QuestionBankCard.exe").is_file():
        raise ValueError("Build the installer and portable folder first")
    actual = subprocess.check_output([str(bundle / "tiyouju.exe"), "--version"], cwd=ROOT, text=True).strip()
    if actual != "tiyouju " + version:
        raise ValueError("Frozen CLI and requested version differ")
    if f'APP_VERSION = "{version}"' not in (ROOT / "backend/core/version.py").read_text(encoding="utf-8"):
        raise ValueError("Source and requested version differ")
    subprocess.run([sys.executable, str(ROOT / "packaging/audit_bundle.py"), "--bundle", str(bundle), "--installer", str(installer)], check=True, cwd=ROOT)
    shutil.copy2(installer, output / installer.name)
    portable = [(path.relative_to(bundle).as_posix(), path) for path in sorted(bundle.rglob("*")) if path.is_file()]
    common.archive(output / f"QuestionBankCard-{version}-portable.zip", portable, "QuestionBankCard/")
    sources = common.source_files()
    common.archive(output / f"question-bank-card-{version}-source.zip", sources, f"question-bank-card-{version}/")
    licenses = ROOT / "THIRD_PARTY_LICENSES"
    common.archive(output / f"third-party-licenses-{version}.zip", [(path.relative_to(licenses).as_posix(), path) for path in sorted(licenses.rglob("*")) if path.is_file()], "THIRD_PARTY_LICENSES/")
    for name, digest in common.THIRD_PARTY.items():
        matches = [path for path in (ROOT / "packaging/dist/releases").glob("*/" + name) if path.parent != output and common.sha(path) == digest]
        if not matches:
            raise ValueError("Missing or mismatched dependency source: " + name)
        shutil.copy2(matches[0], output / name)
    for source, name in [
        (ROOT / "docs" / f"release-notes-v{version}.md", f"release-notes-v{version}.md"),
        (ROOT / "docs" / f"验收记录-v{version}.md", f"Acceptance-Record-v{version}.md"),
        (bundle / "CORRESPONDING_SOURCE.txt", "CORRESPONDING_SOURCE.txt"),
        (ROOT / "install.ps1", "install.ps1"),
        (ROOT / "LICENSE", "LICENSE"),
    ]:
        if not source.is_file():
            raise ValueError("Missing release document: " + str(source.relative_to(ROOT)))
        shutil.copy2(source, output / name)
    evidence = output / "evidence"
    evidence.mkdir()
    summaries = []
    for index, requested in enumerate(reports, 1):
        path = common.confined(requested, ROOT / "tmp")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("success"):
            raise ValueError("A required acceptance report did not pass")
        target = evidence / f"{index}-{path.parent.name}"
        target.mkdir()
        (target / "report.json").write_text(json.dumps(public_report(data), ensure_ascii=False, indent=2), encoding="utf-8")
        for image in path.parent.glob("*.png"):
            shutil.copy2(image, target / image.name)
        for pdf in path.parent.glob("*.pdf"):
            shutil.copy2(pdf, target / pdf.name)
            if pdf.name == "practice-actual.pdf":
                shutil.copy2(pdf, output / "PDF-Export-Acceptance-Sample.pdf")
        summaries.append({"report": str(path.relative_to(ROOT)).replace("\\", "/"), "success": True})
    common.archive(output / f"Acceptance-Screenshots-{version}.zip", [(path.relative_to(evidence).as_posix(), path) for path in sorted(evidence.rglob("*")) if path.is_file()])
    assets = [{"name": path.name, "size": path.stat().st_size, "sha256": common.sha(path)} for path in sorted(output.iterdir()) if path.is_file()]
    manifest = {"version": version, "git_commit": commit, "artifact_type": "GitHub release", "local_installation_performed": False,
                "source_file_count": len(sources), "portable_file_count": len(portable), "acceptance": summaries, "assets": assets}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    hashes = [f"{common.sha(path)} *{path.name}" for path in sorted(output.iterdir()) if path.is_file() and path.name != "SHA256SUMS.txt"]
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "commit": commit, "assets": len(hashes), "version": version}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--report", type=Path, action="append", default=[])
    args = parser.parse_args()
    assemble(args.version, args.report)
