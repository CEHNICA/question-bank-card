"""在桌面和开始菜单放一个“题有据”图标，双击即以窗口版打开题库。

只写快捷方式文件（.lnk），不改注册表、不需要管理员权限；删掉图标即可撤销。
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

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
    """Desktop 可能被 OneDrive 重定向，用系统接口取真实位置。"""
    script = f"[Environment]::GetFolderPath('{name}')"
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30)
    path = (result.stdout or "").strip()
    if not path:
        raise RuntimeError(f"找不到 {name} 文件夹")
    return Path(path)


def _write_with_com(link: Path, spec: dict[str, str]) -> None:
    """用 Unicode Shell Link 接口写快捷方式。

    WScript.Shell 的 IDispatch 包装在部分 Windows 区域设置下会把中文目标路径
    转成问号，随后给 TargetPath 赋值时报 0x80070057。直接调用 IShellLinkW
    可以完整保留题库目录、参数和图标里的中文路径。
    """
    import pythoncom  # pywin32（已锁定在 Windows 运行环境依赖里）
    from win32com.shell import shell

    shortcut = pythoncom.CoCreateInstance(
        shell.CLSID_ShellLink,
        None,
        pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IShellLink,
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
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(link)) + ")",
        "$s.TargetPath = " + _ps_quote(spec["target"]),
        "$s.Arguments = " + _ps_quote(spec["arguments"]),
        "$s.WorkingDirectory = " + _ps_quote(spec["workdir"]),
        "$s.IconLocation = " + _ps_quote(spec["icon"]),
        "$s.Description = " + _ps_quote(spec["description"]),
        "$s.Save()",
    ])


def _write_with_powershell(link: Path, spec: dict[str, str]) -> None:
    # -EncodedCommand 用 UTF-16LE，中文路径不会因为控制台代码页而乱码。
    encoded = base64.b64encode(powershell_script(link, spec).encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], check=True, timeout=60)


def create(link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    spec = shortcut_spec()
    try:
        _write_with_com(link, spec)
    except Exception:  # noqa: BLE001 - 没有 pywin32 时退回 PowerShell
        _write_with_powershell(link, spec)
    if not link.is_file():
        raise RuntimeError(f"没能写入 {link}")


def remove_legacy_shortcut(link: Path) -> None:
    """改名后只清理同一目录下由旧版创建的精确快捷方式名。"""
    legacy_link = link.with_name(f"{LEGACY_NAME}.lnk")
    if legacy_link != link:
        legacy_link.unlink(missing_ok=True)


def main() -> int:
    if os.name != "nt":
        print("只支持 Windows。")
        return 1
    if not VENV_PYTHONW.is_file():
        print("还没有建立运行环境。请先双击“启动题有据.cmd”完成第一次安装，再运行本工具。")
        return 1
    places = [("桌面", _special_folder("Desktop") / f"{NAME}.lnk")]
    try:
        places.append(("开始菜单", _special_folder("Programs") / f"{NAME}.lnk"))
    except Exception:  # noqa: BLE001 - 开始菜单不是必需的
        pass
    for label, link in places:
        try:
            create(link)
            remove_legacy_shortcut(link)
            print(f"已创建{label}图标：{link}")
        except Exception as exc:  # noqa: BLE001
            print(f"创建{label}图标失败：{exc}")
            if label == "桌面":
                return 1
    print(f"\n以后双击“{NAME}”图标即可打开独立窗口；关掉窗口，后台服务会一起停止。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
