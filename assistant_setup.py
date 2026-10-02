"""Local, explicitly requested finishing steps for an AI-assisted installation.

The default operation only inspects files. It neither contacts a service nor
reads the question bank or credentials. Skill destinations are supplied by the
assistant's confirmed configuration, never inferred from an assistant name.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path


class SetupError(Exception):
    """A requested finishing step could not be verified safely."""


def bundled_skill() -> Path:
    root = Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    return root / "skills" / "tiyouju"


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & 0x400:
        tag = getattr(info, "st_reparse_tag", 0)
        # OneDrive Files On-Demand uses cloud reparse tags, which do not
        # redirect the path. Name-surrogate tags (symlinks/junctions) do.
        # Unknown tags remain a reason to refuse a write.
        return not tag or bool(tag & 0x20000000)
    return False


def _safe_path(path: Path) -> Path:
    """Reject relative paths and links/junctions, including existing ancestors."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise SetupError("请使用已确认的绝对目录，不能包含 ..。")
    for part in reversed((path, *path.parents)):
        if _is_link(part):
            raise SetupError(f"目录含符号链接或重解析点，无法安全写入：{part}")
        if part != path and part.exists() and not part.is_dir():
            raise SetupError(f"上级路径不是文件夹：{part}")
    return path


def _manifest(root: Path) -> dict[str, str]:
    _safe_path(root)
    if not root.is_dir():
        raise SetupError(f"技能文件夹不存在：{root}")
    entries: dict[str, str] = {}
    for current, directories, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        for name in directories:
            directory = _safe_path(base / name)
            entries[directory.relative_to(root).as_posix() + "/"] = "directory"
        for name in filenames:
            file = _safe_path(base / name)
            if not file.is_file():
                raise SetupError(f"技能资源不是普通文件：{file}")
            entries[file.relative_to(root).as_posix()] = hashlib.sha256(file.read_bytes()).hexdigest()
    return entries


def _resource_manifest(source: Path) -> dict[str, str]:
    manifest = _manifest(source)
    if "SKILL.md" not in manifest or not (source / "SKILL.md").stat().st_size:
        raise SetupError("随软件附带的技能缺少有效 SKILL.md，未执行安装。")
    return manifest


def _unique_path(parent: Path, prefix: str) -> Path:
    return parent / f"{prefix}{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:12]}"


def _rename(source: Path, destination: Path) -> None:
    _safe_path(source)
    _safe_path(destination)
    if destination.exists():
        raise SetupError(f"目标已经存在，未覆盖：{destination}")
    # On Windows os.rename refuses an existing destination, also during a race.
    os.rename(source, destination)


def _remove_staging(stage: Path, parent: Path) -> None:
    """Only remove a newly allocated staging directory inside its checked parent."""
    if not stage.exists():
        return
    _safe_path(stage)
    _safe_path(parent)
    if stage.parent.resolve() != parent.resolve() or not stage.name.startswith(".tiyouju-install-"):
        raise SetupError("临时技能目录校验失败，已保留现场。")
    shutil.rmtree(stage)


def install_skill(source: Path, parent: Path, *, replace: bool = False) -> dict:
    source, parent = _safe_path(source), _safe_path(parent)
    expected = _resource_manifest(source)
    if parent.exists() and not parent.is_dir():
        raise SetupError(f"技能父目录不是文件夹：{parent}")
    target = _safe_path(parent / "tiyouju")
    source_location, target_location = source.resolve(), target.resolve()
    if source_location != target_location and (
            source_location.is_relative_to(target_location) or target_location.is_relative_to(source_location)):
        raise SetupError("技能目标不能与附带资源目录相互包含，未复制或移动任何技能。")
    previous = None
    if target.exists():
        previous = _manifest(target)
        if previous == expected:
            return {"status": "already_installed", "target": str(target), "backup": None,
                    "verified": True, "changed": False, "file_count": sum(not k.endswith("/") for k in expected)}
        if not replace:
            raise SetupError("已有题有据技能与附带资源不同，可能含用户修改。未覆盖；确认保留备份后可加 --replace-skill。")
    parent.mkdir(parents=True, exist_ok=True)
    _safe_path(parent)
    stage = _unique_path(parent, ".tiyouju-install-")
    backup = None
    try:
        shutil.copytree(source, stage)
        if _manifest(stage) != expected or _resource_manifest(source) != expected:
            raise SetupError("技能复制后的文件与附带资源不一致，未安装。")
        if target.exists():
            if previous is None or _manifest(target) != previous:
                raise SetupError("已有技能在安装期间发生变化，未覆盖。")
            backup = _unique_path(parent, "tiyouju.backup-")
            _rename(target, backup)
        elif previous is not None:
            raise SetupError("已有技能在安装期间被移走，未继续安装。")
        try:
            _rename(stage, target)
        except Exception:
            if backup is not None and not target.exists():
                _rename(backup, target)
            raise
        if _manifest(target) != expected:
            failed = _unique_path(parent, "tiyouju.failed-")
            _rename(target, failed)
            if backup is not None:
                _rename(backup, target)
            raise SetupError(f"安装后技能校验失败；原技能已恢复，失败文件保留在：{failed}" if backup else
                             f"安装后技能校验失败；失败文件保留在：{failed}")
    except OSError as error:
        raise SetupError(f"技能安装未完成：{error}") from error
    finally:
        _remove_staging(stage, parent)
    return {"status": "installed", "target": str(target), "backup": str(backup) if backup else None,
            "verified": True, "changed": True, "file_count": sum(not k.endswith("/") for k in expected)}


def executable_version(exe: Path) -> str:
    """Read the installed PE product version without launching the application."""
    if os.name != "nt":
        raise SetupError("只能在 Windows 上核实已安装程序版本。")
    from ctypes import wintypes

    library = ctypes.WinDLL("version", use_last_error=True)
    library.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    library.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    library.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    library.GetFileVersionInfoW.restype = wintypes.BOOL
    library.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.POINTER(wintypes.UINT)]
    library.VerQueryValueW.restype = wintypes.BOOL
    handle = wintypes.DWORD()
    size = library.GetFileVersionInfoSizeW(str(exe), ctypes.byref(handle))
    if not size:
        raise SetupError("程序文件没有可验证的产品版本。")
    data = ctypes.create_string_buffer(size)
    if not library.GetFileVersionInfoW(str(exe), 0, size, data):
        raise SetupError("无法读取程序产品版本。")
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not library.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise SetupError("程序产品版本不完整。")
    values = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
    if values[0] != 0xFEEF04BD:
        raise SetupError("程序产品版本格式无效。")
    version = [values[4] >> 16, values[4] & 0xFFFF, values[5] >> 16, values[5] & 0xFFFF]
    if version[-1] == 0:
        version.pop()
    return ".".join(str(part) for part in version)


def windows_desktop() -> Path:
    """Use the real per-user Desktop known folder, including OneDrive redirection."""
    if os.name != "nt":
        raise SetupError("桌面图标设置仅支持 Windows。")
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    guid = GUID.from_buffer_copy(uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641").bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                         ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole = ctypes.WinDLL("ole32")
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    result = ctypes.c_void_p()
    code = shell.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(result))
    if code != 0 or not result.value:
        raise SetupError("无法确定当前用户的真实桌面目录，未修改图标。")
    try:
        return Path(ctypes.wstring_at(result.value))
    finally:
        ole.CoTaskMemFree(result)


class WindowsShortcuts:
    """Inspect/write .lnk through Windows' shortcut COM interface."""

    SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$item = [Console]::In.ReadToEnd() | ConvertFrom-Json
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut([string]$item.path)
if ($item.mode -eq 'create') {
    $link.TargetPath = [string]$item.target
    $link.WorkingDirectory = [string]$item.working_dir
    $link.IconLocation = ([string]$item.target) + ',0'
    $link.Description = '题有据'
    $link.Save()
}
[ordered]@{ target = $link.TargetPath; arguments = $link.Arguments; working_dir = $link.WorkingDirectory } | ConvertTo-Json -Compress
"""

    def _run(self, payload: dict) -> dict:
        if os.name != "nt":
            raise SetupError("桌面快捷方式操作仅支持 Windows。")
        try:
            process = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", self.SCRIPT],
                input=json.dumps(payload, ensure_ascii=False), capture_output=True, encoding="utf-8", check=True,
                timeout=20, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            result = json.loads(process.stdout.strip().lstrip("\ufeff"))
            if not isinstance(result, dict) or not isinstance(result.get("target"), str):
                raise ValueError("invalid shortcut response")
            return result
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            raise SetupError("Windows 无法读取或保存快捷方式，未确认操作成功。") from error

    def read(self, path: Path) -> dict:
        return self._run({"mode": "read", "path": str(path)})

    def create(self, path: Path, app: Path) -> None:
        self._run({"mode": "create", "path": str(path), "target": str(app), "working_dir": str(app.parent)})


def _owned_shortcut(path: Path, app: Path, shortcuts) -> bool:
    _safe_path(path)
    if not path.is_file():
        return False
    info = shortcuts.read(path)
    target = Path(info.get("target") or "")
    if not target.is_absolute() or (info.get("arguments") or "").strip():
        return False
    return os.path.normcase(str(target.resolve())) == os.path.normcase(str(app.resolve()))


def desktop_icon(app: Path, desktop: Path, mode: str | None, *, shortcuts=None) -> dict:
    app, desktop = Path(app), _safe_path(desktop)
    if not app.is_file():
        raise SetupError("没找到已安装的题有据程序，未修改桌面图标。")
    if mode not in {None, "show", "hide"}:
        raise SetupError("桌面图标选项必须是 show 或 hide。")
    shortcuts = shortcuts or WindowsShortcuts()
    path = _safe_path(desktop / "题有据.lnk")
    present = path.exists()
    owned = _owned_shortcut(path, app, shortcuts) if present else False
    base = {"path": str(path), "target": str(app), "backup": None, "changed": False,
            "requested": mode is not None, "verified": not present or owned,
            "status": "shown" if owned else "conflict" if present else "hidden"}
    if mode is None:
        return base
    if present and not owned:
        raise SetupError("桌面已有同名快捷方式，但目标不是题有据或含其他启动参数，未覆盖或移走。")
    if (mode == "show" and owned) or (mode == "hide" and not present):
        return base
    if not desktop.is_dir():
        raise SetupError("真实桌面文件夹不存在，未创建或修改图标。")
    backups = _safe_path(app.parent / "assistant-shortcut-backups")
    if mode == "hide":
        backups.mkdir(parents=True, exist_ok=True)
        _safe_path(backups)
        backup = _unique_path(backups, "题有据-").with_suffix(".lnk")
        if not _owned_shortcut(path, app, shortcuts):
            raise SetupError("桌面快捷方式在操作期间发生变化，未移走。")
        _rename(path, backup)
        if path.exists() or not _owned_shortcut(backup, app, shortcuts):
            raise SetupError(f"图标隐藏后的校验失败，备份保留在：{backup}")
        return {**base, "status": "hidden", "backup": str(backup), "changed": True, "verified": True}
    stage = desktop / f".tiyouju-shortcut-{uuid.uuid4().hex}.lnk"
    try:
        recover = None
        if backups.is_dir():
            for candidate in sorted(backups.glob("题有据-*.lnk"), reverse=True):
                if _owned_shortcut(candidate, app, shortcuts):
                    recover = candidate
                    break
        if recover is not None:
            shutil.copy2(recover, stage)
        else:
            shortcuts.create(stage, app)
        if not _owned_shortcut(stage, app, shortcuts):
            raise SetupError("新桌面快捷方式目标校验失败，未显示图标。")
        _rename(stage, path)
        if not _owned_shortcut(path, app, shortcuts):
            raise SetupError("新桌面快捷方式安装后的校验失败，未确认成功。")
        return {**base, "status": "shown", "backup": str(recover) if recover else None,
                "changed": True, "verified": True}
    except OSError as error:
        raise SetupError(f"桌面图标操作未完成：{error}") from error
    finally:
        if stage.exists():
            _safe_path(stage)
            if stage.parent.resolve() != desktop.resolve() or not stage.name.startswith(".tiyouju-shortcut-"):
                raise SetupError("临时快捷方式校验失败，已保留现场。")
            stage.unlink()


def assistant_setup(app: Path | None, *, skill_dir: Path | None = None, replace_skill: bool = False,
                    desktop: str | None = None, resources: Path | None = None,
                    desktop_path: Path | None = None, shortcuts=None, version_reader=None) -> dict:
    """Return verified facts and the remaining choices; mutations are opt-in."""
    if replace_skill and skill_dir is None:
        raise SetupError("--replace-skill 必须与已确认的 --skill-dir 一起使用。")
    if desktop not in {None, "show", "hide"}:
        raise SetupError("桌面图标选项必须是 show 或 hide。")
    if skill_dir is not None:
        _safe_path(Path(skill_dir))
    source = resources or bundled_skill()
    installed = app is not None and Path(app).is_file()
    software = {"installed": installed, "path": str(app) if installed else None,
                "version": None, "version_verified": False, "cli_path": None}
    if installed:
        app = Path(app)
        cli = app.parent / "tiyouju.exe"
        software["cli_path"] = str(cli) if cli.is_file() else None
        try:
            software["version"] = (version_reader or executable_version)(app)
            software["version_verified"] = bool(software["version"])
        except (SetupError, OSError) as error:
            software["verification_error"] = str(error)
    skill = {"status": "not_requested", "bundled_path": str(source), "target": None, "backup": None,
             "verified": False, "available": False, "changed": False}
    try:
        _resource_manifest(source)
        skill["available"] = True
    except (SetupError, OSError) as error:
        skill["availability_error"] = str(error)
    icon = {"status": "unavailable", "path": None, "target": str(app) if installed else None,
            "backup": None, "verified": False, "changed": False, "requested": desktop is not None}
    if installed:
        try:
            actual_desktop = desktop_path if desktop_path is not None else windows_desktop()
            icon = desktop_icon(app, actual_desktop, None, shortcuts=shortcuts)
        except (SetupError, OSError) as error:
            icon["inspection_error"] = str(error)
            if desktop is not None:
                raise SetupError(str(error)) from error
    elif desktop is not None:
        raise SetupError("没找到已安装的题有据，未修改桌面图标。")
    # Inspect conflicting desktop links before performing another requested step.
    if desktop is not None and icon["status"] == "conflict":
        raise SetupError("桌面已有同名但属于其他用途的快捷方式，未覆盖或移走。")
    if skill_dir is not None:
        skill.update(install_skill(source, Path(skill_dir), replace=replace_skill))
    if desktop is not None:
        try:
            icon = desktop_icon(app, actual_desktop, desktop, shortcuts=shortcuts)
        except (SetupError, OSError) as error:
            if skill["verified"]:
                completed = f"题有据技能已完成核验：{skill['target']}。"
                if skill["backup"]:
                    completed += f"原技能备份已保留：{skill['backup']}。"
                raise SetupError(f"{completed}桌面图标未确认设置成功：{error}") from error
            raise
    questions = []
    if skill_dir is None:
        questions.append({"id": "install_skill", "text": "要把题有据技能安装到当前 AI 助手中，方便以后直接帮你处理题目吗？"})
    if desktop is None:
        questions.append({"id": "desktop_icon", "text": "桌面上的题有据图标要显示，还是隐藏？隐藏后仍可由 AI 调用，也能恢复。"})
    return {"software": software, "skill": skill, "desktop": icon, "questions": questions,
            "invitation": "以后有试卷、PDF 或题目照片，可以直接发给我，我会通过题有据帮你整理、核对并保留出处；需要识读服务时会先检查是否可用。"}


def format_report(result: dict) -> str:
    software, skill, icon = result["software"], result["skill"], result["desktop"]
    if software["installed"] and software["version_verified"]:
        lines = [f"已确认电脑上安装了题有据 {software['version']}。"]
    elif software["installed"]:
        lines = ["已找到题有据程序，但还不能核实它的版本。"]
    else:
        lines = ["尚未找到已安装的题有据，不能宣称安装成功。"]
    if skill["verified"]:
        lines.append(f"题有据技能已安装并核验：{skill['target']}。")
        if skill["backup"]:
            lines.append(f"原技能已备份：{skill['backup']}。")
    elif not skill["available"]:
        lines.append("附带技能资源不可用；未安装技能。")
    if (icon["changed"] or icon.get("requested")) and icon["verified"]:
        lines.append("桌面图标已显示。" if icon["status"] == "shown" else "桌面图标已隐藏，可恢复。")
        if icon["backup"]:
            lines.append(f"快捷方式备份：{icon['backup']}。")
    lines.extend(item["text"] for item in result["questions"])
    lines.append(result["invitation"])
    return "\n".join(lines)
