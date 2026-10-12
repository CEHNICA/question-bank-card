"""Assemble a local acceptance delivery, without installing or publishing.

Includes the current worktree (tracked and untracked maintained source), not
an older Git commit. Every input is explicitly confined to this checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = {"backend", "frontend", "tools", "packaging", "assets", "docs", "skills", ".github", "THIRD_PARTY_LICENSES"}
BLOCKED_PARTS = {".git", ".venv", ".build", "dist", "data", "runtime", "backups", "tmp", "__pycache__", "logs", ".planning"}
BLOCKED_NAMES = {"credentials.dat", "credentials.bin", "secrets.json", "credentials.json", "library-ai-credentials.json"}
EXTENSIONS = {".py", ".pyw", ".js", ".css", ".html", ".json", ".md", ".txt", ".ps1", ".cmd", ".iss", ".spec",
              ".svg", ".ico", ".png", ".webp", ".woff", ".woff2", ".ttf", ".map", ".yaml", ".yml", ".toml"}
PUBLIC_PDFS = {"backend/core/demo_data/demo-paper.pdf", "docs/demo/tiyouju-demo-paper.pdf"}
THIRD_PARTY = {
    "pymupdf-1.28.2.tar.gz": "5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249",
    "mupdf-1.28.2-source.tar.gz": "44075a84e329db55b9bef5f342a70fd26d69e48ad1d33cb89d9664581c641156",
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def confined(path, parent):
    resolved = path.resolve()
    if not resolved.is_relative_to(parent.resolve()) or resolved == parent.resolve():
        raise ValueError("Artifact must be a child of its expected directory")
    return resolved


def source_files():
    listing = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
    selected = []
    for name in sorted(set(listing.decode("utf-8").split("\0")) - {""}):
        relative = Path(name)
        if any(part in BLOCKED_PARTS for part in relative.parts) or relative.name.lower() in BLOCKED_NAMES:
            continue
        if relative.name.startswith(".env") or relative.suffix.lower() in {".db", ".sqlite3", ".log", ".dat", ".bin", ".key", ".pem"}:
            continue
        if len(relative.parts) > 1 and relative.parts[0] not in PREFIXES:
            continue
        if relative.suffix.lower() not in EXTENSIONS and relative.name not in {"LICENSE", ".gitignore", ".gitattributes"} and name not in PUBLIC_PDFS:
            continue
        path = confined(ROOT / relative, ROOT)
        if path.is_file() and not path.is_symlink():
            selected.append((name, path))
    required = {"backend/core/version.py", "backend/core/library_tagging.py", "backend/core/tag_catalogue_seed.py",
                "frontend/library.js", "frontend/library-tag-manager.js", "frontend/library-tag-map.js",
                "frontend/library-tag-map.css", "backend/core/migrations/0027_librarytagrelation.py"}
    if not required.issubset({name for name, _ in selected}):
        raise ValueError("Source snapshot would omit required hierarchy files")
    return selected


def archive(target, files, prefix=""):
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, path in files:
            output.write(path, prefix + name)
    with zipfile.ZipFile(target) as result:
        invalid = result.testzip()
        if invalid:
            raise ValueError("Archive integrity check failed: " + invalid)


def assemble(version, reports):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a numeric application version")
    output = confined(ROOT / "packaging/dist" / f"local-acceptance-{version}", ROOT / "packaging/dist")
    output.mkdir(parents=True, exist_ok=True)
    bundle = ROOT / "packaging/dist/QuestionBankCard"
    installer = ROOT / "packaging/dist/installer" / f"TiYouJu-Setup-{version}.exe"
    if not installer.is_file() or not (bundle / "QuestionBankCard.exe").is_file():
        raise ValueError("Build the Windows installer and portable folder first")
    shutil.copy2(installer, output / installer.name)
    portable_files = [(path.relative_to(bundle).as_posix(), path) for path in sorted(bundle.rglob("*")) if path.is_file()]
    archive(output / f"TiYouJu-{version}-portable.zip", portable_files, "QuestionBankCard/")
    sources = source_files()
    archive(output / f"question-bank-card-{version}-source.zip", sources, f"question-bank-card-{version}/")
    license_root = ROOT / "THIRD_PARTY_LICENSES"
    archive(output / f"third-party-licenses-{version}.zip", [
        (path.relative_to(license_root).as_posix(), path) for path in sorted(license_root.rglob("*")) if path.is_file()],
        "THIRD_PARTY_LICENSES/")
    public_dependency_dir = ROOT / "packaging/dist/releases/1.18.0-local"
    for name, expected in THIRD_PARTY.items():
        source = public_dependency_dir / name
        if not source.is_file() or sha(source) != expected:
            raise ValueError("Missing or mismatched corresponding dependency source: " + name)
        shutil.copy2(source, output / name)
    for source, name in [
        (ROOT / "docs" / f"release-notes-v{version}.md", "使用说明.md"),
        (ROOT / "docs" / f"验收记录-v{version}.md", "验收记录.md"),
        (bundle / "CORRESPONDING_SOURCE.txt", "CORRESPONDING_SOURCE.txt"),
        (ROOT / "LICENSE", "LICENSE"),
    ]:
        if not source.is_file():
            raise ValueError("Required delivery document missing: " + str(source.relative_to(ROOT)))
        shutil.copy2(source, output / name)
    evidence = output / "验收证据"
    evidence.mkdir(exist_ok=True)
    summaries = []
    for index, report_path in enumerate(reports, 1):
        report_path = confined(report_path, ROOT / "tmp")
        data = json.loads(report_path.read_text(encoding="utf-8"))
        if not data.get("success"):
            raise ValueError("Cannot deliver a failed acceptance report")
        destination = evidence / f"{index}-{report_path.parent.name}"
        destination.mkdir(exist_ok=True)
        shutil.copy2(report_path, destination / "report.json")
        for path in report_path.parent.iterdir():
            if path.is_file() and (path.suffix.lower() in {".png", ".pdf"} or path.name in {"test.log", "all-js-final.log"}):
                shutil.copy2(path, destination / path.name)
        summaries.append({"report": str(report_path.relative_to(ROOT)), "success": True})
    manifest = {"version": version, "scope": "local acceptance only", "installed": False, "published": False,
                "source": "current worktree including uncommitted changes", "source_file_count": len(sources),
                "portable_file_count": len(portable_files), "acceptance": summaries}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    hashes = [f"{sha(path)} *{path.relative_to(output).as_posix()}" for path in sorted(output.rglob("*"))
              if path.is_file() and path.name != "SHA256SUMS.txt"]
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--report", type=Path, action="append", default=[])
    args = parser.parse_args()
    assemble(args.version, args.report)
