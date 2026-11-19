"""tiyouju:// 自定义协议注册与回跳调起模块。

OneNote 页面中的「在题有据中打开此题」链接使用 `tiyouju://question/<publication_id>`，
点击时由本模块探测当前正在运行的题有据端口（或按需拉起服务），再打开对应题目页。

安装版（PyInstaller 冻结）没有松散的 .py 脚本可喂给解释器，必须注册成：
`QuestionBankCard.exe --tiyouju-uri "%1"`，由窗口启动器接手。
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
URI_FLAG = "--tiyouju-uri"
FALLBACK_PORTS: tuple[int, ...] = (8768, 8769, 8765, 8000)
DEEP_LINK_NAME = "deep-link.json"

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
FROZEN = bool(getattr(sys, "frozen", False))


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


def extract_uri_from_argv(argv: list[str] | None) -> str | None:
    """从启动参数里抽出协议 URI（`--tiyouju-uri <uri>` 或裸 `tiyouju://...`）。"""
    args = [str(item).strip() for item in (argv or []) if str(item).strip()]
    if not args:
        return None
    if args[0] == URI_FLAG:
        return args[1] if len(args) >= 2 else None
    if args[0].lower().startswith(f"{SCHEME}:"):
        return args[0]
    return None


def _instance_file_paths() -> list[Path]:
    paths = [BACKEND_DIR / "runtime" / "instance.json"]
    local_app_data = os.getenv("LOCALAPPDATA") or ""
    if local_app_data:
        paths.append(Path(local_app_data) / "QuestionBankCard" / "runtime" / "instance.json")
    return paths


def _runtime_dirs() -> list[Path]:
    dirs = [BACKEND_DIR / "runtime"]
    local_app_data = os.getenv("LOCALAPPDATA") or ""
    if local_app_data:
        dirs.append(Path(local_app_data) / "QuestionBankCard" / "runtime")
    return dirs


def deep_link_paths() -> list[Path]:
    """可能存放回跳深链的文件路径（安装版与源码版各一份）。"""
    return [folder / DEEP_LINK_NAME for folder in _runtime_dirs()]


def queue_deep_link(question_id: str | None) -> None:
    """把要定位的题目写进 runtime，供已打开的 Eagle 页消费。"""
    qid = str(question_id or "").strip()
    if not qid:
        return
    payload = {"q": qid, "ts": time.time()}
    text = json.dumps(payload, ensure_ascii=False)
    for folder in _runtime_dirs():
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / DEEP_LINK_NAME).write_text(text, encoding="utf-8")
        except OSError as exc:
            logger.debug("写入深链失败 (%s): %s", folder, exc)


def consume_deep_link() -> dict | None:
    """读取并删除深链文件；没有有效内容时返回 None。

    源码版与安装版可能各写一份，这里一次性清掉所有副本，只返回最先读到的有效定位。
    """
    found: dict | None = None
    for path in deep_link_paths():
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8")
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            if found is not None:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                continue
            qid = str(payload.get("q") or "").strip()
            if qid:
                found = {"q": qid, "ts": payload.get("ts")}
        except (OSError, ValueError, TypeError) as exc:
            logger.debug("读取深链失败 (%s): %s", path, exc)
    return found


def candidate_ports() -> list[int]:
    """按优先级收集可能正在监听的题有据本地端口列表（去重保序）。"""
    ordered: list[int] = []

    def _add(port_value: object) -> None:
        if port_value is None or isinstance(port_value, bool):
            return
        try:
            port = int(port_value) if isinstance(port_value, int) else int(str(port_value).strip())
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
    """构造写入 Windows 注册表 shell\\open\\command 的命令行。

    安装版与源码版都走窗口启动器的 `--tiyouju-uri`：写深链、置前已有题有据窗口。
    绝不能再注册成直接跑本脚本 —— 旧路径默认 webbrowser.open，会在外部浏览器里打开题库。
    """
    if FROZEN:
        exe = Path(sys.executable).resolve()
        return f'"{exe}" {URI_FLAG} "%1"'
    exe = _pythonw_executable()
    window_launcher = PROJECT_ROOT / "app_launcher.pyw"
    if not window_launcher.is_file():
        window_launcher = PROJECT_ROOT / "app_window.py"
    if window_launcher.is_file():
        return f'"{exe}" "{window_launcher}" {URI_FLAG} "%1"'
    # 极端兜底：没有窗口脚本时仍可调起本模块，但 main() 也不再外开浏览器。
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
            logger.info("已更新 tiyouju:// 协议命令: %s", desired_cmd)

    return True


def _launch_server_process() -> bool:
    """当题有据未运行时尝试在后台拉起本地服务。"""
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | getattr(
            subprocess, "DETACHED_PROCESS", 0x00000008
        )

    if FROZEN:
        cmd = [str(Path(sys.executable).resolve())]
        cwd = str(Path(sys.executable).resolve().parent)
    else:
        launcher = PROJECT_ROOT / "start_question_bank.py"
        if not launcher.is_file():
            return False
        cmd = [str(_pythonw_executable()), str(launcher)]
        cwd = str(PROJECT_ROOT)

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


def handle_uri(uri: str, *, open_browser: bool = False) -> bool:
    """解析 tiyouju:// URI：写入深链，确保服务在跑。

    默认不外开浏览器。正常回跳应由窗口启动器置前题有据；只有显式
    ``open_browser=True``（无窗口环境）才用 webbrowser 打开 /eagle?q=。
    """
    question_id = parse_question_id(uri)
    if question_id:
        queue_deep_link(question_id)

    port = ensure_server_running()
    if port is None:
        logger.error("未能连接或启动题有据本地服务 (uri=%s)", uri)
        return False

    if not open_browser:
        return True

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
    """命令行与协议调起入口。

    优先把 URI 交给窗口启动器（与安装版同一条路）；本进程只做注册 / 兜底写深链。
    """
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] == "--register":
        return 0 if ensure_registered() else 1

    uri = extract_uri_from_argv(args) or args[0]
    # 源码兜底：尽量转给 app_launcher，让它置前软件窗而不是外开浏览器。
    window_launcher = PROJECT_ROOT / "app_launcher.pyw"
    if not window_launcher.is_file():
        window_launcher = PROJECT_ROOT / "app_window.py"
    if window_launcher.is_file() and not FROZEN:
        try:
            subprocess.Popen(
                [str(_pythonw_executable()), str(window_launcher), URI_FLAG, uri],
                cwd=str(PROJECT_ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
            return 0
        except OSError as exc:
            logger.warning("转交窗口启动器失败，改走深链兜底: %s", exc)

    if handle_uri(uri, open_browser=False):
        return 0

    _notify_user_error("未能启动或连接到题有据服务，请手动启动题有据后再点击链接。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
