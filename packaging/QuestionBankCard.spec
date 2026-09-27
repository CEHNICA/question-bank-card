# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir recipe for the Windows desktop application.

Only program code and static UI assets are selected here. In particular, this
file must never collect the project-level database, uploaded papers, logs,
backups, credentials, virtual environments, or tests.
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


PROJECT_ROOT = Path(SPEC).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
ASSETS_ROOT = PROJECT_ROOT / "assets"
BUILD_ROOT = PROJECT_ROOT / "packaging" / ".build"


def destination_for(root: Path, path: Path, prefix: str) -> str:
    """Return a stable PyInstaller destination for one explicitly selected file."""
    relative_parent = path.relative_to(root).parent
    return str(Path(prefix) / relative_parent).replace("\\", "/")


datas = []

# Browser UI: use an allow-list of file extensions and explicitly omit tests.
for source in sorted(FRONTEND_ROOT.rglob("*")):
    if not source.is_file():
        continue
    if source.name.startswith("test_"):
        continue
    if source.name != "LICENSE" and source.suffix.lower() not in {
        ".css", ".html", ".js", ".map", ".png", ".svg", ".ttf", ".woff", ".woff2"
    }:
        continue
    datas.append((str(source), destination_for(FRONTEND_ROOT, source, "frontend")))

# Application icon/splash assets only. User documents are never under assets.
for source in sorted(ASSETS_ROOT.iterdir()):
    if source.is_file() and source.suffix.lower() in {".ico", ".png", ".svg"}:
        datas.append((str(source), "assets"))

# The frozen launcher uses these source files to fingerprint pending migrations.
# The modules themselves are also included below as hidden imports.
MIGRATIONS_ROOT = BACKEND_ROOT / "core" / "migrations"
for source in sorted(MIGRATIONS_ROOT.glob("*.py")):
    datas.append((str(source), "backend/core/migrations"))


def project_modules(package_root: Path, package_name: str) -> list[str]:
    """Enumerate app modules without ever collecting tests or caches."""
    modules = [package_name]
    for source in sorted(package_root.rglob("*.py")):
        relative = source.relative_to(package_root)
        if "__pycache__" in relative.parts:
            continue
        if source.name.startswith("test_") or source.stem in {"tests"}:
            continue
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        dotted = ".".join([package_name, *parts]) if parts else package_name
        if dotted not in modules:
            modules.append(dotted)
    return modules


hiddenimports = [
    *project_modules(BACKEND_ROOT / "core", "core"),
    *project_modules(BACKEND_ROOT / "qb_server", "qb_server"),
    "django.core.management.commands.migrate",
    "django.core.management.commands.runserver",
    "django.contrib.contenttypes",
    "tkinter",
    "tkinter.messagebox",
    "tkinter.ttk",
    "pymupdf",
    "pythoncom",
    "pywintypes",
    "win32api",
    "win32con",
    "win32com.client",
    "win32crypt",
    "win32event",
    "win32process",
]

# Django discovers contenttypes and its migrations at runtime.
hiddenimports += collect_submodules("django.contrib.contenttypes")

version_file = BUILD_ROOT / "version_info.txt"

a = Analysis(
    [str(PROJECT_ROOT / "app_launcher.pyw")],
    pathex=[str(PROJECT_ROOT), str(BACKEND_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "core.tests",
        "core.test_photos",
        "core.test_real_papers",
        "core.test_resilience",
        "test_app_window",
        "test_backup",
        "test_launcher",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuestionBankCard",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS_ROOT / "app.ico"),
    version=str(version_file) if version_file.is_file() else None,
    uac_admin=False,
    uac_uiaccess=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="QuestionBankCard",
)
