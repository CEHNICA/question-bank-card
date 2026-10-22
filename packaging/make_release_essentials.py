"""Assemble the public release assets for a version that has no formal acceptance run.

`make_release.py` is the full path: it wants an acceptance record document and
accepted acceptance-report JSONs.  When those are missing, this script assembles
the same asset set minus the acceptance evidence, using the project's own
archiving helpers so the layout matches the published releases.

Used for 1.25.97: the one-line installer script had not been attached to any
release since v1.24.9, so the documented install command was broken.  The
portable folder is included as the fallback for machines where the installer is
blocked by local security software.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))

import make_local_acceptance as common  # noqa: E402

VERSION = sys.argv[1] if len(sys.argv) > 1 else "1.25.97"


def main() -> None:
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip():
        raise SystemExit("Commit the reviewed source before assembling the public release")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    output = ROOT / "packaging/dist/releases" / f"{VERSION}-public"
    if output.exists():
        raise SystemExit(f"Use a fresh release directory; {output} already exists")
    output.mkdir(parents=True)
    bundle = ROOT / "packaging/dist/QuestionBankCard"
    installer = ROOT / "packaging/dist/installer" / f"TiYouJu-Setup-{VERSION}.exe"
    if not installer.is_file() or not (bundle / "QuestionBankCard.exe").is_file():
        raise SystemExit("Build the installer and portable folder first")
    actual = subprocess.check_output([str(bundle / "tiyouju.exe"), "--version"], cwd=ROOT, text=True).strip()
    if actual != "tiyouju " + VERSION:
        raise SystemExit(f"Frozen CLI and requested version differ: {actual}")
    if f'APP_VERSION = "{VERSION}"' not in (ROOT / "backend/core/version.py").read_text(encoding="utf-8"):
        raise SystemExit("Source and requested version differ")

    shutil.copy2(installer, output / installer.name)
    portable = [(path.relative_to(bundle).as_posix(), path) for path in sorted(bundle.rglob("*")) if path.is_file()]
    common.archive(output / f"QuestionBankCard-{VERSION}-portable.zip", portable, "QuestionBankCard/")
    sources = common.source_files()
    common.archive(output / f"question-bank-card-{VERSION}-source.zip", sources, f"question-bank-card-{VERSION}/")
    licenses = ROOT / "THIRD_PARTY_LICENSES"
    common.archive(output / f"third-party-licenses-{VERSION}.zip",
                   [(path.relative_to(licenses).as_posix(), path) for path in sorted(licenses.rglob("*")) if path.is_file()],
                   "THIRD_PARTY_LICENSES/")
    for source, name in [
        (ROOT / "docs" / f"release-notes-v{VERSION}.md", f"release-notes-v{VERSION}.md"),
        (bundle / "CORRESPONDING_SOURCE.txt", "CORRESPONDING_SOURCE.txt"),
        (ROOT / "install.ps1", "install.ps1"),
        (ROOT / "LICENSE", "LICENSE"),
    ]:
        if not source.is_file():
            raise SystemExit("Missing release document: " + str(source.relative_to(ROOT)))
        shutil.copy2(source, output / name)

    assets = [{"name": path.name, "size": path.stat().st_size, "sha256": common.sha(path)}
              for path in sorted(output.iterdir()) if path.is_file()]
    manifest = {
        "version": VERSION, "git_commit": commit, "artifact_type": "GitHub release",
        "local_installation_performed": True,
        "source_file_count": len(sources), "portable_file_count": len(portable),
        "acceptance": [], "assets": assets,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    hashes = [f"{common.sha(path)} *{path.name}" for path in sorted(output.iterdir())
              if path.is_file() and path.name != "SHA256SUMS.txt"]
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "commit": commit, "assets": len(hashes), "version": VERSION},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
