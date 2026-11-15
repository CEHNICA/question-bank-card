"""tiyouju:// 自定义协议注册与回跳调起模块。

OneNote 页面中的「在题有据中打开此题」链接使用 `tiyouju://question/<publication_id>`，
点击时由本模块探测当前正在运行的题有据端口（或按需拉起服务），再通过浏览器打开对应题目页。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEME = "tiyouju"
FALLBACK_PORTS: tuple[int, ...] = (8768, 8769, 8765, 8000)

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent


def parse_question_id(uri: str) -> str | None:
    """从 tiyouju:// URI 中解析题目 ID。

    支持格式：
    - tiyouju://question/<uuid>
    - tiyouju://question/<uuid>/
    """
    raw = (uri or "").strip()
    if not raw:
        return None
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme.lower() != SCHEME:
        return None
    host = (parsed.netloc or "").strip().lower()
    path_parts = [part for part in (parsed.path or "").split("/") if part]
    if host == "question" and path_parts:
        return urllib.parse.unquote(path_parts[0]).strip() or None
    if not host and len(path_parts) >= 2 and path_parts[0].lower() == "question":
        return urllib.parse.unquote(path_parts[1]).strip() or None
    return None


def _instance_file_paths() -> list[Path]:
    paths = [BACKEND_DIR / "runtime" / "instance.json"]
    local_app_data = os.getenv("LOCALAPPDATA") or ""
    if local_app_data:
        paths.append(Path(local_app_data) / "QuestionBankCard" / "runtime" / "instance.json")
    return paths


def candidate_ports() -> list[int]:
    """按优先级收集可能正在监听的题有据本地端口列表（去重保序）。"""
    ordered: list[int] = []

    def _add(port_value: object) -> None:
        try:
            port = int(port_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return
        if 1 <= port <= 65535 and port not in ordered:
            ordered.append(port)

    _add(os.getenv("TIYOUJU_PORT"))
    _add(os.getenv("QB_PORT"))

    for instance_path in _instance_file_paths():
        try:
            if instance_path.is_file():
                payload = json.loads(instance_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    _add(payload.get("port"))
        except (OSError, ValueError) as exc:
            logger.debug("读取实例端口文件失败 (%s): %s", instance_path, exc)

    for port in FALLBACK_PORTS:
        _add(port)

    return ordered


def probe_health(port: int, timeout: float = 0.45) -> bool:
    """探测指定本地端口是否为运行中的题有据服务。"""
    url = f"http://127.0.0.1:{port}/api/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def find_running_port() -> int | None:
    """返回首个通过健康检查的题有据服务端口，未运行则返回 None。"""
    for port in candidate_ports():
        if probe_health(port):
            return port
    return None


def _pythonw_executable() -> Path:
    venv_pythonw = BACKEND_DIR / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.is_file():
        return venv_pythonw
    sibling = Path(sys.executable).with_name("pythonw.exe")
    if sibling.is_file():
        return sibling
    return Path(sys.executable)


def build_command_line() -> str:
    """构造写入 Windows 注册表 shell\\open\\command 的命令行。"""
    exe = _pythonw_executable()
    return f'"{exe}" "{SCRIPT_PATH}" "%1"'


def ensure_registered() -> bool:
    """在 HKCU\\Software\\Classes\\tiyouju 下幂等注册自定义协议（无需管理员权限）。"""
    if sys.platform != "win32":
        return False

    import winreg

    desired_cmd = build_command_line()
    base_key_path = rf"Software\Classes\{SCHEME}"
    cmd_key_path = rf"{base_key_path}\shell\open\command"

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, base_key_path) as base_key:
        try:
            current_label, _ = winreg.QueryValueEx(base_key, "")
        except FileNotFoundError:
            current_label = None
        if current_label != "URL:TiYouJu Protocol":
            winreg.SetValueEx(base_key, "", 0, winreg.REG_SZ, "URL:TiYouJu Protocol")

        try:
            current_proto, _ = winreg.QueryValueEx(base_key, "URL Protocol")
        except FileNotFoundError:
            current_proto = None
        if current_proto != "":
            winreg.SetValueEx(base_key, "URL Protocol", 0, winreg.REG_SZ, "")

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, cmd_key_path) as cmd_key:
        try:
            current_cmd, _ = winreg.QueryValueEx(cmd_key, "")
        except FileNotFoundError:
            current_cmd = None
        if current_cmd != desired_cmd:
            winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, desired_cmd)

    return True


def _launch_server_process() -> bool:
    """当题有据未运行时尝试在后台拉起本地服务。"""
    launcher = PROJECT_ROOT / "start_question_bank.py"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )

    if launcher.is_file():
        cmd = [str(_pythonw_executable()), str(launcher)]
        cwd = str(PROJECT_ROOT)
    else:
        return False

    try:
        subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return True
    except OSError as exc:
        logger.error("后台拉起题有据失败: %s", exc)
        return False


def ensure_server_running(timeout_seconds: float = 15.0) -> int | None:
    """确保题有据服务正在运行并返回其监听端口。"""
    running = find_running_port()
    if running is not None:
        return running

    if not _launch_server_process():
        return None

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        time.sleep(0.4)
        running = find_running_port()
        if running is not None:
            return running
    return None


def build_target_url(port: int, question_id: str | None) -> str:
    """构造题有据前端定位 URL。"""
    base = f"http://127.0.0.1:{port}/eagle"
    if question_id:
        return f"{base}?q={urllib.parse.quote(question_id)}"
    return base


def handle_uri(uri: str) -> bool:
    """解析 tiyouju:// URI 并打开对应的题有据题目页。"""
    question_id = parse_question_id(uri)
    port = ensure_server_running()
    if port is None:
        logger.error("未能连接或启动题有据本地服务 (uri=%s)", uri)
        return False

    target_url = build_target_url(port, question_id)
    logger.info("打开题有据页面: %s", target_url)
    return bool(webbrowser.open(target_url))


def _notify_user_error(message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "题有据", 0x10)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    """命令行与协议调起入口。"""
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] == "--register":
        return 0 if ensure_registered() else 1

    uri = args[0]
    if handle_uri(uri):
        return 0

    _notify_user_error("未能启动或连接到题有据服务，请手动启动题有据后再点击链接。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
