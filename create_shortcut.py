"""Create TiYouJu shortcuts, preferring the installed application's verified CLI.

The source fallback keeps unrelated links intact and reads a new link back before
publishing it. No application is started and no question-bank settings are read.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import assistant_setup as setup

ROOT = Path(__file__).resolve().parent
VENV_PYTHONW = ROOT / "backend" / ".venv" / "Scripts" / "pythonw.exe"
LAUNCHER = ROOT / "app_launcher.pyw"
ICON = ROOT / "assets" / "app.ico"
NAME = "题有据"
LEGACY_NAME = "题库题卡版"
DESCRIPTION = "题有据 · 原卷可追溯的题库整理工具"


def shortcut_spec() -> dict[str, str]:
    return {
        "target": str(VENV_PYTHONW),
        "arguments": f'"{LAUNCHER}"',
        "workdir": str(ROOT),
        "icon": f"{ICON},0",
        "description": DESCRIPTION,
    }


def _special_folder(name: str) -> Path:
    if name == "Desktop":
        return setup.windows_desktop()
    if name != "Programs":
        raise setup.SetupError("不支持的快捷方式目录。")
    script = ("$ErrorActionPreference = 'Stop'; "
              "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false); "
              "[Environment]::GetFolderPath('Programs')")
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, encoding="utf-8", check=True, timeout=20,
    )
    path = (result.stdout or "").strip().lstrip("\ufeff")
    if not path:
        raise setup.SetupError("找不到开始菜单文件夹。")
    return Path(path)


def _write_with_com(link: Path, spec: dict[str, str]) -> None:
    """Use the Unicode Shell Link interface provided by the source runtime."""
    import pythoncom
    from win32com.shell import shell

    shortcut = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink,
    )
    shortcut.SetPath(spec["target"])
    shortcut.SetArguments(spec["arguments"])
    shortcut.SetWorkingDirectory(spec["workdir"])
    icon_path, _, icon_index = spec["icon"].rpartition(",")
    shortcut.SetIconLocation(icon_path, int(icon_index or "0"))
    shortcut.SetDescription(spec["description"])
    shortcut.QueryInterface(pythoncom.IID_IPersistFile).Save(str(link), 0)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def powershell_script(link: Path, spec: dict[str, str]) -> str:
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(link)) + ")",
        "$s.TargetPath = " + _ps_quote(spec["target"]),
        "$s.Arguments = " + _ps_quote(spec["arguments"]),
        "$s.WorkingDirectory = " + _ps_quote(spec["workdir"]),
        "$s.IconLocation = " + _ps_quote(spec["icon"]),
        "$s.Description = " + _ps_quote(spec["description"]),
        "$s.Save()",
    ])


def _write_with_powershell(link: Path, spec: dict[str, str]) -> None:
    encoded = base64.b64encode(powershell_script(link, spec).encode("utf-16-le")).decode("ascii")
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, check=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise setup.SetupError("Windows 无法写入源码快捷方式，未确认创建成功。") from error


def _same_path(first: str | Path, second: str | Path) -> bool:
    if not isinstance(first, (str, Path)) or not isinstance(second, (str, Path)):
        return False
    try:
        a, b = Path(first), Path(second)
        return a.is_absolute() and b.is_absolute() and os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except (OSError, ValueError):
        return False


def _matches(link: Path, spec: dict[str, str], *, shortcuts=None) -> bool:
    setup._safe_path(link)
    if not link.is_file():
        return False
    info = (shortcuts or setup.WindowsShortcuts()).read(link)
    return (_same_path(info.get("target") or "", spec["target"])
            and info.get("arguments", "") == spec["arguments"]
            and _same_path(info.get("working_dir") or "", spec["workdir"]))


def create(link: Path, *, spec=None, shortcuts=None) -> bool:
    """Publish a verified source shortcut, refusing to overwrite existing links."""
    link = setup._safe_path(Path(link))
    spec = spec or shortcut_spec()
    if link.exists():
        if _matches(link, spec, shortcuts=shortcuts):
            return False
        raise setup.SetupError(f"已有同名快捷方式与当前源码启动方式不同，已保留：{link}")
    if not link.parent.is_dir():
        raise setup.SetupError(f"快捷方式文件夹不存在，未创建：{link.parent}")
    stage = link.parent / f".tiyouju-shortcut-{uuid.uuid4().hex}.lnk"
    try:
        try:
            _write_with_com(stage, spec)
        except Exception:
            _write_with_powershell(stage, spec)
        if not _matches(stage, spec, shortcuts=shortcuts):
            raise setup.SetupError("新快捷方式目标、启动参数或工作目录校验失败，未显示图标。")
        setup._rename(stage, link)
        if not _matches(link, spec, shortcuts=shortcuts):
            raise setup.SetupError("快捷方式安装后的目标校验失败，未确认成功。")
        return True
    finally:
        if stage.exists():
            setup._safe_path(stage)
            stage.unlink()


def remove_legacy_shortcut(link: Path, *, spec=None, shortcuts=None) -> None:
    """Only remove a legacy name whose complete source launch target matches."""
    legacy = link.with_name(f"{LEGACY_NAME}.lnk")
    if legacy == link or not legacy.exists():
        return
    try:
        owned = _matches(legacy, spec or shortcut_spec(), shortcuts=shortcuts)
    except (setup.SetupError, OSError):
        return  # An unreadable or unrelated legacy file is not ours to delete.
    if owned:
        legacy.unlink()


def _installed_paths() -> tuple[Path, Path] | None:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if not local:
        return None
    directory = Path(local) / "Programs" / "QuestionBankCard"
    app, cli = directory / "QuestionBankCard.exe", directory / "tiyouju.exe"
    if app.exists() or cli.exists():
        return app, cli
    return None


def _run_cli(cli: Path, app: Path, arguments: list[str]):
    environment = dict(os.environ)
    environment["TIYOUJU_APP"] = str(app)
    return subprocess.run(
        [str(cli), "assistant-setup", *arguments], cwd=str(cli.parent),
        env=environment, capture_output=True, encoding="utf-8", errors="replace", timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _create_installed(app: Path, cli: Path) -> Path:
    if not app.is_file() or not cli.is_file():
        raise setup.SetupError("已找到安装目录，但程序或配套命令不完整。请重新安装或升级题有据；未改用源码创建图标。")
    help_result = _run_cli(cli, app, ["--help"])
    if help_result.returncode or "--desktop" not in help_result.stdout:
        raise setup.SetupError("已安装的题有据不支持新版图标命令。请升级题有据；未改用源码创建图标。")
    process = _run_cli(cli, app, ["--desktop", "show", "--json"])
    try:
        result = json.loads(process.stdout.strip().lstrip("\ufeff"))
    except (TypeError, ValueError) as error:
        raise setup.SetupError("安装版图标命令没有返回可核实结果，未确认创建成功。") from error
    if not isinstance(result, dict):
        raise setup.SetupError("安装版图标命令返回格式无效，未确认创建成功。")
    if process.returncode:
        raise setup.SetupError(str(result.get("error") or "安装版图标命令失败，未确认创建成功。"))
    icon = result.get("desktop")
    if (not isinstance(icon, dict) or icon.get("status") != "shown" or icon.get("verified") is not True
            or not _same_path(icon.get("target") or "", app)):
        raise setup.SetupError("安装版未确认正确的桌面图标，不能宣称创建成功。")
    link = setup.windows_desktop() / f"{NAME}.lnk"
    if not _same_path(icon.get("path") or "", link) or not setup._owned_shortcut(link, app, setup.WindowsShortcuts()):
        raise setup.SetupError("桌面快捷方式回读校验失败，未确认创建成功。")
    return link


def _is_windows() -> bool:
    return os.name == "nt"


def main() -> int:
    if not _is_windows():
        print("只支持 Windows。")
        return 1
    try:
        installed = _installed_paths()
        if installed is not None:
            link = _create_installed(*installed)
            print(f"已显示并核验安装版题有据桌面图标：{link}")
            return 0
        if not VENV_PYTHONW.is_file() or not LAUNCHER.is_file():
            raise setup.SetupError("未找到安装版，源码运行环境也未准备好。请先安装题有据，或双击“启动题有据.cmd”准备源码环境，再运行本工具。")
        desktop_link = _special_folder("Desktop") / f"{NAME}.lnk"
        create(desktop_link)
        remove_legacy_shortcut(desktop_link)
        print(f"已显示并核验源码版桌面图标：{desktop_link}")
        try:
            menu_link = _special_folder("Programs") / f"{NAME}.lnk"
            create(menu_link)
            remove_legacy_shortcut(menu_link)
            print(f"已核验开始菜单图标：{menu_link}")
        except (setup.SetupError, OSError, subprocess.SubprocessError) as error:
            print(f"桌面图标已完成；开始菜单图标未完成：{error}")
        return 0
    except (setup.SetupError, OSError, subprocess.SubprocessError) as error:
        print(f"创建图标没有完成：{error}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
