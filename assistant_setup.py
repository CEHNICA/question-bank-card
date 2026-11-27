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


def canonical_skill_dir(data_root: Path | None = None) -> Path:
    """返回随用户数据目录持久化的规范技能目录，升级主程序时不会断链。"""
    if data_root is None:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        root = Path(local) / "QuestionBankCard" if local else Path.home() / ".local" / "share" / "tiyouju"
    else:
        root = Path(data_root)
    return root / "skills" / "tiyouju"


def discover_agent_skill_parents(home: Path | None = None) -> list[tuple[str, Path]]:
    """按主流国际与国内 AI Agent 约定探测当前用户的技能父目录。"""
    user_home = Path.home() if home is None else Path(home)
    candidates: list[tuple[str, Path, bool]] = [
        ("universal_agents", user_home / ".agents" / "skills", True),
        ("antigravity", user_home / ".gemini" / "antigravity" / "skills", (user_home / ".gemini" / "antigravity").is_dir()),
        ("gemini_config", user_home / ".gemini" / "config" / "skills", (user_home / ".gemini").is_dir()),
        ("claude_code", user_home / ".claude" / "skills", (user_home / ".claude").is_dir()),
        ("codex", user_home / ".codex" / "skills", (user_home / ".codex").is_dir()),
        ("cursor", user_home / ".cursor" / "skills", (user_home / ".cursor").is_dir()),
        ("trae", user_home / ".trae" / "skills", (user_home / ".trae").is_dir()),
        ("trae_cn", user_home / ".trae-cn" / "skills", (user_home / ".trae-cn").is_dir()),
        ("windsurf", user_home / ".codeium" / "windsurf" / "skills", (user_home / ".codeium" / "windsurf").is_dir()),
        ("opencode", user_home / ".config" / "opencode" / "skills", (user_home / ".config" / "opencode").is_dir()),
        ("qwen_code", user_home / ".qwen" / "skills", (user_home / ".qwen").is_dir()),
        ("qoder", user_home / ".qoder" / "skills", (user_home / ".qoder").is_dir()),
        ("lingma", user_home / ".lingma" / "skills", (user_home / ".lingma").is_dir()),
        ("codebuddy", user_home / ".codebuddy" / "skills", (user_home / ".codebuddy").is_dir()),
        ("kimi", user_home / ".kimi" / "skills", (user_home / ".kimi").is_dir()),
        ("iflow", user_home / ".iflow" / "skills", (user_home / ".iflow").is_dir()),
        ("openclaw", user_home / ".openclaw" / "skills", (user_home / ".openclaw").is_dir()),
        ("cline", user_home / ".cline" / "skills", (user_home / ".cline").is_dir()),
        ("roo_code", user_home / ".roo" / "skills", (user_home / ".roo").is_dir()),
        ("kilocode", user_home / ".kilocode" / "skills", (user_home / ".kilocode").is_dir()),
    ]
    if home is None:
        raw_codex = os.environ.get("CODEX_HOME", "").strip()
        if raw_codex:
            codex_home = Path(raw_codex)
            if codex_home.is_absolute() and codex_home.is_dir():
                candidates.append(("codex_home", codex_home / "skills", True))
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for agent_id, parent, active in candidates:
        if not active:
            continue
        key = os.path.normcase(str(parent))
        if key in seen:
            continue
        seen.add(key)
        found.append((agent_id, parent))
    return found


def sync_canonical_skill(source: Path, canonical: Path) -> dict:
    """将软件内附带技能原子同步到持久化规范目录，供各 AI Agent 共享链接。"""
    source = _safe_path(source)
    expected = _resource_manifest(source)
    canonical = Path(canonical)
    parent = _safe_path(canonical.parent)
    parent.mkdir(parents=True, exist_ok=True)
    if _is_link(canonical):
        _remove_link_entry(canonical)
    if canonical.exists():
        _safe_path(canonical)
        if _manifest(canonical) == expected:
            return {
                "status": "up_to_date",
                "path": str(canonical),
                "changed": False,
                "file_count": sum(not k.endswith("/") for k in expected),
            }
    stage = _unique_path(parent, ".tiyouju-install-")
    old_copy: Path | None = None
    try:
        shutil.copytree(source, stage)
        if _manifest(stage) != expected:
            raise SetupError("规范技能同步校验失败。")
        if canonical.exists():
            old_copy = _unique_path(parent, ".tiyouju-old-")
            _rename(canonical, old_copy)
        try:
            _rename(stage, canonical)
        except Exception:
            if old_copy is not None and not canonical.exists():
                _rename(old_copy, canonical)
            raise
        if old_copy is not None and old_copy.exists():
            shutil.rmtree(old_copy, ignore_errors=True)
    except OSError as error:
        raise SetupError(f"同步规范技能失败：{error}") from error
    finally:
        _remove_staging(stage, parent)
    return {
        "status": "synced",
        "path": str(canonical),
        "changed": True,
        "file_count": sum(not k.endswith("/") for k in expected),
    }


def _remove_link_entry(path: Path) -> None:
    """仅摘除目录联接点或符号链接本身，绝不递归删除其指向的目标内容。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        path.unlink()
        return
    if os.name == "nt":
        try:
            os.rmdir(path)
            return
        except OSError:
            pass
    path.unlink()


def _create_directory_link(target: Path, canonical: Path) -> str:
    """在 Windows 优先使用免管理员权限的 Directory Junction，失败时安全回退。"""
    if os.name == "nt":
        proc = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(target), str(canonical)],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and _is_link(target):
            return "junction"
    try:
        os.symlink(canonical, target, target_is_directory=True)
        return "symlink"
    except OSError:
        shutil.copytree(canonical, target)
        return "copy"


def link_agent_skill(agent_id: str, canonical: Path, parent: Path, *, replace: bool = False) -> dict:
    """把单个 AI Agent 的 skills/tiyouju 指向规范技能目录。"""
    canonical = _safe_path(canonical)
    parent = _safe_path(parent)
    target = parent / "tiyouju"
    if parent.resolve() == canonical.parent.resolve() or canonical.resolve().is_relative_to(parent.resolve()):
        return {"agent": agent_id, "status": "skipped_overlap", "target": str(target), "changed": False}
    parent.mkdir(parents=True, exist_ok=True)
    expected = _resource_manifest(canonical)
    backup: Path | None = None
    identical_static_copy = False
    if _is_link(target):
        try:
            if target.resolve() == canonical.resolve():
                return {"agent": agent_id, "status": "already_linked", "target": str(target), "changed": False}
        except OSError:
            pass
        _remove_link_entry(target)
    elif target.exists():
        if not target.is_dir():
            return {"agent": agent_id, "status": "skipped_conflict", "target": str(target), "changed": False}
        current = _manifest(target)
        identical_static_copy = current == expected
        if not identical_static_copy and not replace:
            return {
                "agent": agent_id,
                "status": "skipped_custom",
                "target": str(target),
                "changed": False,
            }
        backup = _unique_path(parent, "tiyouju.backup-")
        _rename(target, backup)
    mode = _create_directory_link(target, canonical)
    if mode in {"junction", "symlink"}:
        verified = _is_link(target) and target.resolve() == canonical.resolve() and (target / "SKILL.md").is_file()
    else:
        verified = _manifest(target) == expected
    if backup is not None and identical_static_copy and verified:
        shutil.rmtree(backup, ignore_errors=True)
        backup = None
    return {
        "agent": agent_id,
        "status": "linked" if mode in {"junction", "symlink"} else "copied",
        "mode": mode,
        "target": str(target),
        "backup": str(backup) if backup else None,
        "changed": True,
        "verified": verified,
    }


def _default_read_user_path() -> tuple[str, int]:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
        try:
            value, reg_type = winreg.QueryValueEx(key, "Path")
            return str(value), int(reg_type)
        except FileNotFoundError:
            return "", winreg.REG_EXPAND_SZ


def _default_write_user_path(value: str, reg_type: int) -> None:
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "Path", 0, reg_type, value)


def _default_broadcast_env() -> None:
    if os.name != "nt":
        return
    try:
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,
            0x001A,
            0,
            "Environment",
            0x0002,
            1000,
            ctypes.byref(result),
        )
    except Exception:  # noqa: BLE001 - 广播超时不阻塞安装收尾
        pass


def ensure_user_path(
    cli_dir: Path,
    *,
    read_env=None,
    write_env=None,
    broadcast=None,
) -> dict:
    """将 tiyouju.exe 所在目录写入当前用户 PATH，方便所有 AI Agent 直接调用。"""
    target = os.path.normpath(str(Path(cli_dir)))
    if os.name != "nt" and read_env is None:
        return {"status": "unsupported_os", "dir": target, "changed": False}
    reader = read_env or _default_read_user_path
    writer = write_env or _default_write_user_path
    notifier = broadcast or _default_broadcast_env
    raw_value, reg_type = reader()
    entries = [part.strip() for part in str(raw_value or "").split(";") if part.strip()]
    normalized = {os.path.normcase(os.path.normpath(part.rstrip("\\/"))) for part in entries}
    target_norm = os.path.normcase(os.path.normpath(target.rstrip("\\/")))
    if target_norm in normalized:
        return {"status": "already_present", "dir": target, "changed": False}
    entries.append(target)
    updated = ";".join(entries)
    writer(updated, reg_type)
    notifier()
    return {"status": "added", "dir": target, "changed": True}


def mcp_server_entry(app: Path | None, *, prefer_command_name: bool = False) -> dict[str, object]:
    """生成标准的 stdio MCP 服务配置项。"""
    command = "tiyouju"
    if not prefer_command_name and app is not None:
        cli = Path(app).parent / "tiyouju.exe"
        if cli.is_file():
            command = str(cli)
    return {"command": command, "args": ["mcp"]}


def _resolve_appdata(home: Path | None = None, appdata: Path | None = None) -> Path:
    if appdata is not None:
        return Path(appdata)
    if home is not None:
        return Path(home) / "AppData" / "Roaming"
    env_appdata = os.environ.get("APPDATA", "").strip()
    return Path(env_appdata) if env_appdata else Path.home() / "AppData" / "Roaming"


def discover_mcp_config_targets(home: Path | None = None, appdata: Path | None = None) -> list[tuple[str, Path]]:
    """探测本机已安装的国际与国内 AI 客户端 MCP JSON 配置文件路径。"""
    user_home = Path.home() if home is None else Path(home)
    roaming = _resolve_appdata(home, appdata)
    candidates: list[tuple[str, Path, bool]] = [
        ("cursor", user_home / ".cursor" / "mcp.json", (user_home / ".cursor").is_dir()),
        ("trae_cn", roaming / "Trae CN" / "User" / "mcp.json", (roaming / "Trae CN" / "User").is_dir()),
        ("trae", roaming / "Trae" / "User" / "mcp.json", (roaming / "Trae" / "User").is_dir()),
        (
            "windsurf",
            user_home / ".codeium" / "windsurf" / "mcp_config.json",
            (user_home / ".codeium" / "windsurf").is_dir(),
        ),
        (
            "claude_desktop",
            roaming / "Claude" / "claude_desktop_config.json",
            (roaming / "Claude").is_dir(),
        ),
        ("claude_code", user_home / ".claude.json", (user_home / ".claude.json").is_file()),
        (
            "antigravity",
            user_home / ".gemini" / "antigravity" / "mcp_config.json",
            (user_home / ".gemini" / "antigravity").is_dir(),
        ),
        ("gemini_cli", user_home / ".gemini" / "settings.json", (user_home / ".gemini").is_dir()),
        ("qwen_code", user_home / ".qwen" / "settings.json", (user_home / ".qwen").is_dir()),
        ("qoder", user_home / ".qoder" / "mcp.json", (user_home / ".qoder").is_dir()),
        ("lingma", user_home / ".lingma" / "mcp.json", (user_home / ".lingma").is_dir()),
        ("codebuddy", user_home / ".codebuddy" / "mcp.json", (user_home / ".codebuddy").is_dir()),
        ("kimi", user_home / ".kimi" / "mcp.json", (user_home / ".kimi").is_dir()),
        ("openclaw", user_home / ".openclaw" / "mcp.json", (user_home / ".openclaw").is_dir()),
        ("iflow", user_home / ".iflow" / "settings.json", (user_home / ".iflow").is_dir()),
    ]
    found: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for client_id, config_path, active in candidates:
        if not active:
            continue
        key = os.path.normcase(str(config_path))
        if key in seen:
            continue
        seen.add(key)
        found.append((client_id, config_path))
    return found


def _is_tiyouju_mcp_entry(entry: object) -> bool:
    if not isinstance(entry, dict) or not set(entry.keys()) <= {"command", "args"}:
        return False
    if entry.get("args") != ["mcp"]:
        return False
    command = str(entry.get("command") or "").strip()
    return Path(command).name.lower() in {"tiyouju", "tiyouju.exe"}


def _read_mcp_json_state(config_path: Path) -> tuple[str, dict | None]:
    if not config_path.exists():
        return "missing", {}
    raw = config_path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return "empty", {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "invalid_json", None
    if not isinstance(data, dict):
        return "invalid_json", None
    if "mcpServers" in data and not isinstance(data["mcpServers"], dict):
        return "invalid_json", None
    return "ok", data


def register_mcp_config(
    client_id: str,
    config_path: Path,
    entry: dict[str, object],
    *,
    replace: bool = False,
) -> dict:
    """将题有据 stdio MCP 服务安全写入客户端配置文件，不破坏已有配置。"""
    config_path = _safe_path(config_path)
    state, data = _read_mcp_json_state(config_path)
    if state == "invalid_json" or data is None:
        return {
            "client": client_id,
            "status": "skipped_invalid_json",
            "path": str(config_path),
            "backup": None,
            "changed": False,
            "verified": False,
        }
    servers = data.setdefault("mcpServers", {})
    existing = servers.get("tiyouju")
    if existing == entry:
        return {
            "client": client_id,
            "status": "already_configured",
            "path": str(config_path),
            "backup": None,
            "changed": False,
            "verified": True,
        }
    if existing is not None and not _is_tiyouju_mcp_entry(existing) and not replace:
        return {
            "client": client_id,
            "status": "skipped_custom",
            "path": str(config_path),
            "backup": None,
            "changed": False,
            "verified": False,
        }
    backup: Path | None = None
    if existing is not None and not _is_tiyouju_mcp_entry(existing) and replace and config_path.is_file():
        backup = Path(f"{_unique_path(config_path.parent, f'{config_path.stem}.tiyouju-backup-')}.json")
        shutil.copy2(config_path, backup)
    servers["tiyouju"] = dict(entry)
    rendered = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.parent / f".tiyouju-mcp-tmp-{uuid.uuid4().hex}.json"
    try:
        tmp_path.write_text(rendered, encoding="utf-8")
        os.replace(tmp_path, config_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
    verified = False
    try:
        check_data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        verified = isinstance(check_data, dict) and check_data.get("mcpServers", {}).get("tiyouju") == entry
    except (OSError, json.JSONDecodeError, AttributeError):
        verified = False
    return {
        "client": client_id,
        "status": "updated" if existing is not None else "configured",
        "path": str(config_path),
        "backup": str(backup) if backup else None,
        "changed": True,
        "verified": verified,
    }


def inspect_mcp_config(
    app: Path | None,
    *,
    home: Path | None = None,
    appdata: Path | None = None,
    prefer_command_name: bool = False,
) -> dict:
    """只读返回标准 MCP 配置片段与本机已探测客户端的接入状态。"""
    entry = mcp_server_entry(app, prefer_command_name=prefer_command_name)
    command = str(entry["command"])
    clients: list[dict[str, str]] = []
    for client_id, config_path in discover_mcp_config_targets(home=home, appdata=appdata):
        status = "not_configured"
        try:
            _safe_path(config_path)
            state, data = _read_mcp_json_state(config_path)
            if state == "invalid_json" or data is None:
                status = "invalid_json"
            else:
                servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
                existing = servers.get("tiyouju")
                if existing == entry:
                    status = "configured"
                elif existing is not None and _is_tiyouju_mcp_entry(existing):
                    status = "needs_update"
                elif existing is not None:
                    status = "custom"
        except (SetupError, OSError):
            status = "error"
        clients.append({"client": client_id, "path": str(config_path), "status": status})
    return {
        "command": command,
        "args": ["mcp"],
        "entry": entry,
        "mcp_servers": {"mcpServers": {"tiyouju": entry}},
        "codex_command": f'codex mcp add tiyouju -- "{command}" mcp',
        "clients": clients,
    }


def format_mcp_config_report(result: dict) -> str:
    snippet = json.dumps(result.get("mcp_servers") or {}, ensure_ascii=False, indent=2)
    lines = [
        "题有据 MCP 标准配置（适用于 Cherry Studio / Chatbox / Trae / Cursor / Claude / Qwen / CodeBuddy / Kimi 等）：",
        snippet,
        "",
        "Codex CLI 一行接入：",
        str(result.get("codex_command") or ""),
    ]
    clients = result.get("clients") or []
    lines.append("")
    if clients:
        lines.append("本机已探测到的 MCP 客户端配置文件：")
        for item in clients:
            lines.append(f"- {item.get('client')}: {item.get('status')} -> {item.get('path')}")
    else:
        lines.append("未探测到已有客户端目录；可将上方 JSON 直接粘贴到客户端的 MCP 设置中。")
    return "\n".join(lines)


def setup_agents(
    app: Path | None,
    *,
    data_root: Path | None = None,
    home: Path | None = None,
    appdata: Path | None = None,
    resources: Path | None = None,
    update_path: bool = True,
    register_mcp: bool = True,
    replace: bool = False,
) -> dict:
    """仿照 egolite 的安装接入方式：同步规范技能、链接至全平台 AI Agent、注册 MCP 并配置用户 PATH。"""
    source = resources or bundled_skill()
    canonical = canonical_skill_dir(data_root)
    canonical_result = sync_canonical_skill(source, canonical)
    agent_results: list[dict] = []
    for agent_id, parent in discover_agent_skill_parents(home):
        try:
            agent_results.append(link_agent_skill(agent_id, canonical, parent, replace=replace))
        except (SetupError, OSError) as error:
            agent_results.append(
                {
                    "agent": agent_id,
                    "status": "error",
                    "target": str(parent / "tiyouju"),
                    "changed": False,
                    "error": str(error),
                }
            )
    mcp_results: list[dict] = []
    if register_mcp:
        entry = mcp_server_entry(app)
        for client_id, config_path in discover_mcp_config_targets(home=home, appdata=appdata):
            try:
                mcp_results.append(register_mcp_config(client_id, config_path, entry, replace=replace))
            except (SetupError, OSError) as error:
                mcp_results.append(
                    {
                        "client": client_id,
                        "status": "error",
                        "path": str(config_path),
                        "changed": False,
                        "verified": False,
                        "error": str(error),
                    }
                )
    path_result: dict = {"status": "skipped", "changed": False}
    if update_path and app is not None and Path(app).is_file():
        path_result = ensure_user_path(Path(app).parent)
    return {
        "canonical": canonical_result,
        "agents": agent_results,
        "mcp": mcp_results,
        "path": path_result,
    }


def format_setup_agents_report(result: dict) -> str:
    canonical = result.get("canonical") or {}
    lines = [f"规范技能目录：{canonical.get('path', '')}（{canonical.get('status', '')}）"]
    for item in result.get("agents") or []:
        lines.append(f"- {item.get('agent')}: {item.get('status')} -> {item.get('target')}")
    mcp_items = result.get("mcp") or []
    if mcp_items:
        lines.append("MCP 配置：")
        for item in mcp_items:
            lines.append(f"- {item.get('client')}: {item.get('status')} -> {item.get('path')}")
    else:
        lines.append("MCP 配置：未检测到已安装的受支持客户端（可用 tiyouju mcp-config 复制通用配置）")
    path_info = result.get("path") or {}
    lines.append(f"用户 PATH：{path_info.get('status')} ({path_info.get('dir', '-')})")
    return "\n".join(lines)
