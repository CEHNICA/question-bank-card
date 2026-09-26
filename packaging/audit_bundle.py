"""Fail a desktop build when private or runtime-only files leaked into it.

This audit intentionally works on the finished PyInstaller ``onedir`` tree,
rather than trusting the inputs in the spec file.  That makes it a final
guardrail against an accidentally broad ``Tree(...)`` or ``--add-data``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


FORBIDDEN_FILENAMES = {
    "credentials.dat",
    "db.sqlite3",
}
FORBIDDEN_SUFFIXES = {
    ".pdf",
    ".sqlite3",
    ".tgz",
    ".zip",
}
FORBIDDEN_SEGMENTS = {
    ".venv",
    "__pycache__",
    "backups",
    "data",
    "runtime",
    "tests",
}
ALLOWED_EXACT_PATHS = {
    # Standard PyInstaller bootstrap archive; it contains Python's stdlib, not user files.
    "_internal/base_library.zip",
}


class BundleAuditError(RuntimeError):
    """The packaged application contains unsafe or unexpected content."""


def find_forbidden_files(bundle: Path) -> list[str]:
    """Return bundle-relative paths which must never ship to another user."""
    bundle = bundle.resolve()
    if not bundle.is_dir():
        raise BundleAuditError(f"打包目录不存在：{bundle}")

    findings: list[str] = []
    for path in bundle.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle)
        relative_text = relative.as_posix()
        if relative_text.casefold() in ALLOWED_EXACT_PATHS:
            continue
        lowered_parts = tuple(part.casefold() for part in relative.parts)
        lowered_name = path.name.casefold()
        lowered_suffix = path.suffix.casefold()
        if (
            lowered_name in FORBIDDEN_FILENAMES
            or lowered_suffix in FORBIDDEN_SUFFIXES
            or any(part in FORBIDDEN_SEGMENTS for part in lowered_parts[:-1])
        ):
            findings.append(relative_text)
    return sorted(findings, key=str.casefold)


def audit_bundle(bundle: Path) -> None:
    """Validate a complete PyInstaller directory or raise a useful error."""
    executable = bundle / "QuestionBankCard.exe"
    if not executable.is_file():
        raise BundleAuditError(f"缺少主程序：{executable}")
    findings = find_forbidden_files(bundle)
    if findings:
        details = "\n".join(f"  - {path}" for path in findings)
        raise BundleAuditError(f"打包结果含有私人数据或运行中文件：\n{details}")


def audit_installer(installer: Path) -> None:
    """Apply cheap integrity checks to an optional finished installer."""
    installer = installer.resolve()
    if not installer.is_file():
        raise BundleAuditError(f"安装包不存在：{installer}")
    if installer.suffix.casefold() != ".exe":
        raise BundleAuditError(f"安装包不是 EXE：{installer}")
    if installer.stat().st_size < 1024 * 1024:
        raise BundleAuditError(f"安装包异常过小：{installer.stat().st_size} 字节")
    with installer.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise BundleAuditError(f"安装包缺少 Windows PE 文件头：{installer}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="审计题库题卡版桌面打包结果")
    parser.add_argument("--bundle", required=True, type=Path, help="PyInstaller onedir 输出目录")
    parser.add_argument("--installer", type=Path, help="可选：Inno Setup 生成的 EXE")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        audit_bundle(options.bundle)
        if options.installer is not None:
            audit_installer(options.installer)
    except BundleAuditError as exc:
        print(f"打包审计失败：{exc}", file=sys.stderr)
        return 1
    print("打包审计通过：未发现数据库、试卷、凭据或运行日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
