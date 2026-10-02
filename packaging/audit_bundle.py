"""Fail a desktop build when private or runtime-only files leaked into it.

This audit intentionally works on the finished PyInstaller ``onedir`` tree,
rather than trusting the inputs in the spec file.  That makes it a final
guardrail against an accidentally broad ``Tree(...)`` or ``--add-data``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


FORBIDDEN_FILENAMES = {
    ".env",
    "credentials.dat",
    "db.sqlite3",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
FORBIDDEN_SUFFIXES = {
    ".cer",
    ".crt",
    ".db",
    ".db-shm",
    ".db-wal",
    ".doc",
    ".docm",
    ".docx",
    ".jks",
    ".key",
    ".keystore",
    ".log",
    ".odt",
    ".ods",
    ".odp",
    ".p12",
    ".pem",
    ".pdf",
    ".pfx",
    ".potm",
    ".potx",
    ".ppt",
    ".pptm",
    ".pptx",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
    ".sqlite3-shm",
    ".sqlite3-wal",
    ".tgz",
    ".xls",
    ".xlsb",
    ".xlsm",
    ".xlsx",
    ".zip",
}
FORBIDDEN_SEGMENTS = {
    ".git",
    ".svn",
    ".venv",
    "__pycache__",
    "backup",
    "backups",
    "data",
    "logs",
    "runtime",
    "tests",
}
ALLOWED_EXACT_PATHS = {
    # Standard PyInstaller bootstrap archive; it contains Python's stdlib, not user files.
    "_internal/base_library.zip",
    # Requests needs certifi's public CA roots for HTTPS certificate validation.  It is
    # neither a user certificate nor a private key.
    "_internal/certifi/cacert.pem",
    # The practice paper for 新手教学: an original public demo (docs/demo), no user data.
    "_internal/backend/core/demo_data/demo-paper.pdf",
    # python-docx's blank public template is required to create Word files.
    # Only this dependency resource is allowed; user documents remain forbidden.
    "_internal/docx/templates/default.docx",
}

# Only inspect formats which should contain readable configuration or source text.
# Binary executables and libraries can coincidentally contain byte sequences that
# look like a path or token, so scanning those would produce noisy false positives.
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_TEXT_SCAN_BYTES = 2 * 1024 * 1024
CONTENT_SCAN_EXEMPT_FILENAMES = {
    "copying",
    "copying.txt",
    "corresponding_source.txt",
    "installation-notice.txt",
    "license",
    "license.txt",
    "third_party_notices.txt",
}
CONTENT_SCAN_EXEMPT_SEGMENTS = {"third_party_licenses"}

# These expressions intentionally look for credential *values*, not words such as
# ``api_key`` that legitimately occur in source code and UI labels.
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN CERTIFICATE-----", re.IGNORECASE),
    re.compile(
        r"\bAuthorization[\"']?\s*[:=]\s*[\"']?Bearer\s+[A-Za-z0-9._~+/=-]{12,}",
        re.IGNORECASE,
    ),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_-]?key|access[_-]?token|secret[_-]?key|mineru[_-]?token|"
        r"minimax[_-]?api[_-]?key|siliconflow[_-]?api[_-]?key)\b\s*[:=]\s*[\"']"
        r"[A-Za-z0-9._~+/=-]{16,}[\"']",
        re.IGNORECASE,
    ),
)
USER_PATH_PATTERNS = (
    re.compile(r"\b[A-Z]:[\\/]Users[\\/][^\\/\s\"'<>]+[\\/]", re.IGNORECASE),
    re.compile(r"(?:^|[\s\"'])/(?:home|root)/[^/\s\"'<>]+(?:/|$)", re.IGNORECASE | re.MULTILINE),
)
APPLICATION_INTERNAL_ROOTS = {"assets", "backend", "frontend", "skills"}


class BundleAuditError(RuntimeError):
    """The packaged application contains unsafe or unexpected content."""


def _is_forbidden_name(path: Path) -> bool:
    """Return whether a path name indicates private or runtime-only content."""
    lowered_name = path.name.casefold()
    if lowered_name in FORBIDDEN_FILENAMES or lowered_name.startswith(".env."):
        return True
    # ``Path.suffix`` correctly covers compound runtime suffixes such as
    # ``database.db-wal`` and ``database.sqlite3-shm``.
    return path.suffix.casefold() in FORBIDDEN_SUFFIXES


def _should_scan_text(relative: Path, path: Path) -> bool:
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    if any(part in CONTENT_SCAN_EXEMPT_SEGMENTS for part in lowered_parts[:-1]):
        return False
    if path.name.casefold() in CONTENT_SCAN_EXEMPT_FILENAMES:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    return 0 < size <= MAX_TEXT_SCAN_BYTES and path.suffix.casefold() in TEXT_SUFFIXES


def _contains_sensitive_text(relative: Path, path: Path) -> bool:
    """Inspect a small text file without ever returning its sensitive contents."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    # JSON and source files commonly escape backslashes.  Normalize them so an
    # embedded ``C:\\Users\\name`` is detected like a literal Windows path.
    normalized = text.replace("\\\\", "\\")
    if any(pattern.search(normalized) for pattern in SENSITIVE_TEXT_PATTERNS):
        return True
    lowered_parts = tuple(part.casefold() for part in relative.parts)
    # Vendored libraries sometimes include harmless documentation examples such
    # as ``/home/foo/project``.  Limit path checks to our own resources while
    # still scanning every small text file for actual key/token material.
    scan_user_paths = (
        not lowered_parts
        or lowered_parts[0] != "_internal"
        or (len(lowered_parts) > 1 and lowered_parts[1] in APPLICATION_INTERNAL_ROOTS)
    )
    return scan_user_paths and any(pattern.search(normalized) for pattern in USER_PATH_PATTERNS)


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
        if (
            _is_forbidden_name(path)
            or any(part in FORBIDDEN_SEGMENTS for part in lowered_parts[:-1])
            or (_should_scan_text(relative, path) and _contains_sensitive_text(relative, path))
        ):
            findings.append(relative_text)
    return sorted(findings, key=str.casefold)


def audit_bundle(bundle: Path) -> None:
    """Validate a complete PyInstaller directory or raise a useful error."""
    for name in ("QuestionBankCard.exe", "tiyouju.exe"):
        executable = bundle / name
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
    parser = argparse.ArgumentParser(description="审计题有据桌面打包结果")
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
