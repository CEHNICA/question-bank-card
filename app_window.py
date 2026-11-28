"""窗口版启动器：像普通软件一样打开题库。

双击桌面上的“题有据”图标（或 app_launcher.pyw）后：
1. 不弹黑色命令行窗口，先显示一个小的启动画面；
2. 在后台启动网页服务和工作者（沿用 start_question_bank.py 的全部逻辑与安全约束）；
3. 由本程序自己创建一扇窗口（WebView2 只负责在窗口里画页面），不再另开 Edge；
4. 关掉这扇窗口，后台服务随之停止。程序已经在运行时，再点图标只把这一扇拉到前面。

第一次安装组件时会交给原来的命令行启动器；API 凭据则在软件
的“设置 → 服务与密钥”中录入。独立配置窗口仅作为凭据文件损坏时的故障恢复入口。
"""

from __future__ import annotations

import contextlib
import faulthandler
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import start_question_bank as launcher
from credential_dialog import show_credential_dialog
from credential_store import CredentialStoreError, credential_pool, load_credentials, provider_catalog

ROOT = launcher.ROOT
ASSETS = ROOT / "assets"
ICON_PNG = ASSETS / "app-icon.png"
CONSOLE_LAUNCHER = ROOT / "启动题有据.cmd"
LOG_FILE = launcher.RUNTIME / "launcher.log"
APP_TITLE = "题有据"
APP_SUBTITLE = "原卷可追溯的题库整理工具"
APP_USER_MODEL_ID = "QuestionBank.CardReview"
# 自己的窗口只认这些进程。Edge 里碰巧标题带「题有据」的页面不算。
_OWN_WINDOW_PROCESSES = frozenset({"questionbankcard.exe", "python.exe", "pythonw.exe"})


def _resolve_app_version() -> str:
    """读取当前软件版本号，供启动画面显示；读取失败时返回空串不阻断启动。"""
    try:
        from core.version import APP_VERSION

        return str(APP_VERSION).strip()
    except ImportError:
        backend_path = str(launcher.BACKEND)
        if backend_path not in sys.path:
            sys.path.insert(0, backend_path)
        try:
            from core.version import APP_VERSION

            return str(APP_VERSION).strip()
        except ImportError:
            return ""
    except Exception:  # noqa: BLE001 - 版本号只作启动展示，读取异常不能阻断启动
        return ""


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
_ICON_HANDLE_CACHE: dict[tuple[str, int, int], tuple[int, int]] = {}


class NeedsConsole(Exception):
    """需要在命令行里人工输入一次（首次安装、录入或更换凭据）。"""


# ---------------------------------------------------------------- 小工具


def _message(text: str, *, error: bool = False) -> None:
    """没有控制台时用系统对话框提示。"""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, 0x10 if error else 0x40)
            return
        except Exception:  # noqa: BLE001 - 提示失败也不能让启动器崩溃
            pass
    print(text)


def _log_stream():
    launcher.RUNTIME.mkdir(parents=True, exist_ok=True)
    return LOG_FILE.open("a", encoding="utf-8", buffering=1)


def _run_step_hidden(args: list[str], label: str) -> None:
    """与 launcher._run_step 相同，但子进程不弹出黑色窗口。"""
    print(label, flush=True)
    setup_env = launcher._child_environment()
    if launcher.FROZEN:
        setup_env["QB_INTERNAL_LOG"] = str(launcher.RUNTIME / "setup.log")
    result = subprocess.run(
        args,
        cwd=launcher.PROGRAM_ROOT if launcher.FROZEN else launcher.BACKEND,
        env=setup_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError(f"{label}失败：\n{result.stdout[-1800:]}")


def _needs_install() -> bool:
    """还没建好运行环境，或组件清单变了（要联网安装，放到命令行里能看到进度）。"""
    if launcher.FROZEN:
        return False
    if not launcher.PYTHON.is_file():
        return True
    install_file = launcher.LOCK_REQUIREMENTS if launcher.LOCK_REQUIREMENTS.is_file() else launcher.REQUIREMENTS
    current = hashlib.sha256(install_file.read_bytes()).hexdigest()
    previous = launcher.INSTALL_MARKER.read_text(encoding="ascii").strip() if launcher.INSTALL_MARKER.is_file() else ""
    return current != previous


def _saved_credentials() -> dict[str, object]:
    try:
        return load_credentials()
    except CredentialStoreError as exc:
        raise NeedsConsole(str(exc)) from exc


def credentials_ready(saved: dict[str, object] | None = None, preferences: dict[str, str] | None = None) -> bool:
    """不联网，只看 MinerU 与当前所选主读模型的凭据是否就绪。"""
    saved = _saved_credentials() if saved is None else saved
    preferences = launcher._model_preferences() if preferences is None else preferences
    has_token = bool(launcher._strip_bearer(os.environ.get("MINERU_TOKEN", "")) or credential_pool(saved, "mineru"))
    primary_name = preferences.get("primary_engine", "minimax_m3")
    # AI 助手读题不需要看图模型的密钥；其余情况下任何一家看图服务有密钥就能读题
    # （所选那家没有密钥时，后台按顺序换用有密钥的那家）。
    model_ready = primary_name == provider_catalog.ASSISTANT_ENGINE or any(
        credential_pool(saved, service) for service in provider_catalog.VISION
    )
    # 只有环境变量、没有保存时仍转交命令行，让人明确选择本次使用方式。
    return has_token and model_ready


def resolve_credentials_quietly() -> tuple[list[str], list[str]]:
    """与 launcher._resolve_credentials 同样的优先级，但绝不提问；需要提问时抛 NeedsConsole。"""
    saved = _saved_credentials()
    preferences = launcher._model_preferences()
    if not credentials_ready(saved, preferences):
        raise NeedsConsole("还没有保存题库凭据")
    environment_token = launcher._strip_bearer(os.environ.get("MINERU_TOKEN", ""))
    saved_tokens = credential_pool(saved, "mineru")
    tokens = [environment_token] if environment_token else saved_tokens
    environment_validity: bool | None = None
    if environment_token:
        environment_validity = launcher._mineru_token_validity(environment_token)
        if environment_validity is False and saved_tokens:
            tokens = saved_tokens
        elif environment_validity is False:
            raise NeedsConsole("环境变量中的 MinerU Token 未通过官网验证，需要重新输入")

    if environment_token and tokens == [environment_token]:
        checked_tokens = tokens
    else:
        # 账号池隔离单个过期账号：启动时并行做无消费预检，
        # 只过滤官网明确判定失效的项。网络不通（None）仍交给运行时池处理。
        with ThreadPoolExecutor(max_workers=min(8, len(tokens))) as executor:
            validity = list(executor.map(launcher._mineru_token_validity, tokens))
        checked_tokens = [token for token, result in zip(tokens, validity) if result is not False]
        if not checked_tokens:
            raise NeedsConsole("保存的 MinerU 账号均未通过官网验证，需要重新输入")
    return checked_tokens, credential_pool(saved, "minimax")


def credential_pools_quietly() -> dict[str, list[str]]:
    """Load saved pools without blocking application startup for first use.

    The settings page must remain reachable when nothing has been configured.
    The desktop application treats its encrypted store as authoritative;
    direct server users can still run the worker without the desktop hot-load
    flag and provide traditional environment variables there.
    """

    saved = _saved_credentials()
    return {service: credential_pool(saved, service) for service in launcher.POOL_ENVIRONMENT_NAMES}


# ---------------------------------------------------------------- 浏览器应用窗口


def browser_candidates() -> list[Path]:
    """Edge 优先（Windows 自带），其次 Chrome。"""
    found: list[Path] = []
    env = os.environ
    bases_edge = [env.get("PROGRAMFILES(X86)"), env.get("PROGRAMFILES"), env.get("LOCALAPPDATA")]
    bases_chrome = [env.get("PROGRAMFILES"), env.get("PROGRAMFILES(X86)"), env.get("LOCALAPPDATA")]
    paths = [Path(b) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for b in bases_edge if b]
    paths += [Path(b) / "Google" / "Chrome" / "Application" / "chrome.exe" for b in bases_chrome if b]
    paths += _registry_app_paths()
    for exe in ("msedge", "chrome"):
        located = shutil.which(exe)
        if located:
            paths.append(Path(located))
    for path in paths:
        if path.is_file() and path not in found:
            found.append(path)
    found.sort(key=lambda p: 0 if p.name.lower() == "msedge.exe" else 1)
    return found


def _registry_app_paths() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    paths = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for exe in ("msedge.exe", "chrome.exe"):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if value:
                        paths.append(Path(value.strip('"')))
            except OSError:
                continue
    return paths


def window_profile() -> Path:
    """应用窗口专用的浏览器配置目录：放在用户目录下，不进入题库数据、备份和打包。"""
    base = (
        launcher.USER_ROOT
        if launcher.FROZEN
        else Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "QuestionBankCard"
    )
    digest = hashlib.sha256(str(launcher.USER_ROOT).lower().encode("utf-8")).hexdigest()[:12]
    profile = base / f"browser-profile-{digest}"
    profile.mkdir(parents=True, exist_ok=True)
    local_state = profile / "Local State"
    if not local_state.exists():
        # 关窗即退出：不让浏览器在后台常驻，否则启动器会一直以为窗口还开着。
        local_state.write_text(json.dumps({"background_mode": {"enabled": False}}), encoding="utf-8")
    _disable_profile_password_saving(profile)
    _clear_stale_cache(profile)
    return profile


# 1.25.40：本地页面没有版本号，浏览器会按自己的启发式缓存旧脚本 —— 升级以后
# 窗口里还是上一版的界面（真的撞上过：修好的地方仍旧显示旧的）。启动时把这几个
# 缓存目录清掉，页面每次都从本机服务重新读；不碰 Cookies、密码和设置。
_CACHE_CLEARED = False


def _clear_stale_cache(profile: Path) -> None:
    global _CACHE_CLEARED
    if _CACHE_CLEARED:
        return
    _CACHE_CLEARED = True
    cache_dirs = (
        "Cache",
        "Code Cache",
        "GPUCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "GrShaderCache",
        "ShaderCache",
        "component_crx_cache",
        "extensions_crx_cache",
        "ProvenanceData",
        "Service Worker",
    )
    for base in (profile, profile / "Default", profile / "EBWebView" / "Default"):
        if not base.is_dir():
            continue
        for name in cache_dirs:
            target = base / name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)


def _disable_profile_password_saving(profile: Path) -> bool:
    """Disable password saving only in the application's dedicated profile.

    API keys remain encrypted by credential_store, never by the browser's
    login database. Existing unrelated preferences are round-tripped without
    inspection or output; Login Data, Cookies and saved API keys are not read.
    """
    preferences = profile / "Default" / "Preferences"
    pending: Path | None = None
    try:
        # Do not follow a redirected Default directory into another profile.
        if not preferences.resolve().is_relative_to(profile.resolve()):
            raise ValueError("redirected profile")
        data = json.loads(preferences.read_text(encoding="utf-8")) if preferences.exists() else {}
        if not isinstance(data, dict):
            raise ValueError("invalid preferences")
        settings = {"credentials_enable_service": False, "credentials_enable_autosignin": False}
        session = data.get("session")
        # 5 = 打开指定页面，不恢复上次的窗口。恢复上次会话时，--app 会再开一扇，
        # 桌面上就并排两扇题有据；关掉先被盯上的那扇，服务跟着停，另一扇就空了。
        restore_off = isinstance(session, dict) and session.get("restore_on_startup") == 5
        if restore_off and all(data.get(key) is value for key, value in settings.items()):
            return True
        data.update(settings)
        if not isinstance(session, dict):
            session = {}
            data["session"] = session
        session["restore_on_startup"] = 5
        preferences.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=preferences.parent,
            prefix=".password-preferences-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            pending = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, preferences)
        pending = None
        return True
    except (OSError, ValueError, UnicodeError):
        # Preserve a malformed/locked file rather than resetting the profile.
        # Do not print its contents or the exception, which may include paths.
        print("应用浏览器的密码保存偏好未能更新；原有配置已保留。", flush=True)
        return False
    finally:
        if pending is not None:
            with contextlib.suppress(OSError):
                pending.unlink()


def window_command(browser: Path, url: str, profile: Path) -> list[str]:
    # 1.25.90：老师要的是「跟普通浏览器窗口一样」—— 标题栏上有最小化 / 最大化 / 关闭
    # 三个按钮，能拖边框改大小，也能最小化到任务栏去看别的窗口。
    # 以前这里用 --kiosk 整屏无边框（为了拿掉浏览器自带的那条标题栏）；那样窗口既不能
    # 最大化也不能拖大小，页面只好自绘两个按钮。现在反过来：保留浏览器的标题栏，
    # 三个按钮就是系统的，页面不再自绘窗口控件。
    # 不传 --start-maximized / --window-size：这款 Edge 的 app 窗口会忽略它们，
    # 而写死一个尺寸在小屏笔记本上会超出屏幕。交给浏览器给默认大小，想铺满点□就是。
    return [
        str(browser),
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
    ]


# ---------------------------------------------------------------- 窗口控制

# 窗口控制：宿主窗口是**普通的浏览器窗口**，标题栏自带最小化 / 最大化 / 关闭。
# 1.26.3：页面不再自绘那三个按钮（两套并排，老师会不知道该点哪一个），
# 于是"页面 → 启动器"的控制通道（控制文件 + /api/window/…）也一并撤掉了。
#
# 只用 ctypes：本文件已经在用它弹系统对话框，不为同一件事再引入一个打包依赖。
_SW_SHOW = 5
_SW_RESTORE = 9
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
# 窗口迟迟不出现就放弃守候，不拖住退出。
_WINDOW_GRACE = 60.0
# 窗口还没出现时，别每轮都去重新定位：定位要起一个 PowerShell 查进程号，冷启动
# 三四百毫秒，比 _CONTROL_POLL 还贵，密集重查只会把守护线程自己拖垮。
_WINDOW_LOOKUP_BACKOFF = 1.0
# 句柄失效后，重查也不是一下一下地查。
_WINDOW_LOOKUP_INTERVAL = 0.5
# 兜底：拿不到窗口句柄、只能按配置目录判断时，最多再等这么久就强行收尾。
# 以前这里没有上限，浏览器残留一个崩溃收集器就能让退出永远停在这一行。
_CLOSE_BACKSTOP = 20.0
# 一扇窗刚关掉时，另一扇可能还在画标题。这段时间里再找一次，找到就接着守。
_REPLACEMENT_GRACE = 1.0
# 服务已经起来、窗口还没画出来时，再点图标不要立刻另开一扇。
_FOCUS_RETRY_SECONDS = 8.0
_FOCUS_RETRY_INTERVAL = 0.4
# Chromium 用来恢复上次窗口的文件。只在这个配置目录还没有浏览器进程时删。
_SESSION_LEFTOVERS = ("Current Session", "Current Tabs", "Last Session", "Last Tabs")


def _window_alive(user32, hwnd: int) -> bool:
    """应用窗口的句柄还有效吗。

    这是纯 Win32 调用，不查进程、不起子进程，微秒级，所以可以放在等退出的主
    循环里反复问。

    之所以不拿「这个配置目录下还剩几个浏览器进程」当判据：一个应用窗口背后挂
    着崩溃收集器、显卡进程、三个工具进程和两个页面进程，它们比窗口晚退好几秒
    —— 崩溃收集器是 Edge 故意留着的，要把崩溃记录写完才走。按进程数等，等到的是
    它们而不是用户等的那扇窗；碰上残留就是永远等下去，服务不杀、运行记录不清，
    下次双击会撞上一个半死不活的服务，页面就白了。
    """
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def _user32():
    if os.name != "nt":
        return None
    try:
        import ctypes

        return ctypes.windll.user32
    except Exception:  # noqa: BLE001 - 拿不到 Win32 就保持原样，不让启动器崩溃
        return None


def _profile_markers(profile: Path) -> tuple[str, ...]:
    """生成浏览器配置目录在命令行中的各种可能路径写法（含长短路径与正反斜杠）。"""
    raw_variants = [str(profile), os.path.normpath(str(profile))]
    if os.name == "nt":
        with contextlib.suppress(Exception):
            import ctypes

            kernel32 = ctypes.windll.kernel32
            for api_name in ("GetLongPathNameW", "GetShortPathNameW"):
                fn = getattr(kernel32, api_name, None)
                if fn is None:
                    continue
                buf = ctypes.create_unicode_buffer(1024)
                length = fn(str(profile), buf, len(buf))
                if length and length < len(buf) and buf.value:
                    raw_variants.append(buf.value)
    markers: list[str] = []
    for variant in raw_variants:
        for slash in (variant, variant.replace("\\", "/")):
            lowered = slash.strip().lower()
            if lowered and lowered not in markers:
                markers.append(lowered)
    return tuple(markers)


def _command_matches_profile(command_line: str, markers: tuple[str, ...]) -> bool:
    """判断浏览器进程命令行是否绑定到当前配置目录。"""
    if not command_line or not markers:
        return False
    lowered = command_line.lower()
    normalized = lowered.replace("/", "\\")
    return any(marker in lowered or marker in normalized for marker in markers)


def _browser_pids_win32(markers: tuple[str, ...]) -> tuple[bool, set[int]]:
    """通过 Win32 进程快照与 NtQueryInformationProcess 快速查询目标浏览器 PID。"""
    if os.name != "nt":
        return False, set()
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        class UNICODE_STRING(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE,
            wintypes.ULONG,
            ctypes.c_void_p,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long

        TH32CS_SNAPPROCESS = 0x00000002
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        PROCESS_COMMAND_LINE_INFORMATION = 60
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return False, set()
        pids: set[int] = set()
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = bool(kernel32.Process32FirstW(snap, ctypes.byref(entry)))
            while ok:
                if entry.szExeFile.lower() in ("msedge.exe", "chrome.exe"):
                    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, entry.th32ProcessID)
                    if handle:
                        try:
                            needed = wintypes.ULONG()
                            buf_size = 8192
                            buf = ctypes.create_string_buffer(buf_size)
                            status = ntdll.NtQueryInformationProcess(
                                handle, PROCESS_COMMAND_LINE_INFORMATION, buf, buf_size, ctypes.byref(needed)
                            )
                            if status != 0 and needed.value > buf_size:
                                buf = ctypes.create_string_buffer(needed.value)
                                status = ntdll.NtQueryInformationProcess(
                                    handle,
                                    PROCESS_COMMAND_LINE_INFORMATION,
                                    buf,
                                    ctypes.sizeof(buf),
                                    ctypes.byref(needed),
                                )
                            if status == 0:
                                us = UNICODE_STRING.from_buffer(buf)
                                if us.Buffer and us.Length:
                                    cmd = ctypes.wstring_at(us.Buffer, us.Length // 2)
                                    if _command_matches_profile(cmd, markers):
                                        pids.add(int(entry.th32ProcessID))
                        finally:
                            kernel32.CloseHandle(handle)
                ok = bool(kernel32.Process32NextW(snap, ctypes.byref(entry)))
        finally:
            kernel32.CloseHandle(snap)
        return True, pids
    except Exception:  # noqa: BLE001 - Win32 进程枚举不可用时回退 PowerShell
        return False, set()


def _browser_pids_powershell(markers: tuple[str, ...]) -> set[int]:
    """回退方案：使用 PowerShell 字面量匹配命令行中的配置目录。"""
    conditions = " -or ".join(
        "$_.CommandLine.IndexOf('" + m.replace("'", "''") + "', [StringComparison]::OrdinalIgnoreCase) -ge 0"
        for m in markers
    )
    if not conditions:
        return set()
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -and ({conditions}) }} | ForEach-Object {{ $_.ProcessId }}"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {int(line) for line in (result.stdout or "").split() if line.strip().isdigit()}


def _browser_pids(profile: Path) -> set[int]:
    """命令行里带着这个配置目录的浏览器进程号。窗口可能被交给一个已经在运行的
    浏览器进程，不能只看自己 spawn 出来的那个 pid。"""
    markers = _profile_markers(profile)
    ok, pids = _browser_pids_win32(markers)
    if ok:
        return pids
    return _browser_pids_powershell(markers)


def _window_title(user32, handle) -> str:
    import ctypes

    length = user32.GetWindowTextLengthW(handle)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def _app_window(user32, profile: Path, *, require_title: bool = False) -> int:
    """应用窗口的句柄；还没出现就返回 0。

    先按标题认窗口，认不出再退回面积最大的那个。

    只看面积踩过两次坑。同一组浏览器进程里除了题有据窗口，还挂着「无法更新
    Microsoft Edge」的气泡、加载期的空白窗口、宽度为零的辅助窗口。前一次是按
    Z 序取，挑中了气泡窗；改成面积最大也不牢靠 —— 上一次运行没退干净时，进程组
    里会多出一个比题有据窗口还大的临时窗口，挑中它之后它自己消失了，于是程序
    认为「用户已经把窗关了」，当场停服务退出去，把真正开着、又连着一个死服务的
    题有据窗口留在屏幕上。老师看到的就是：点了退出，窗口还在，页面白了。

    标题最靠得住：题有据窗口的标题永远带「题有据」三个字，气泡窗和临时窗没有。

    require_title=True 时只认标题对得上的窗口。等着应用窗口出现的那段路要用它：
    上一次运行没退干净时，进程组里可能先冒出别的窗口，抢在题有据窗口之前被找到。
    """
    import ctypes
    import ctypes.wintypes

    pids = _browser_pids(profile)
    if not pids:
        return 0
    named: list[tuple[int, int, bool]] = []
    fallback: list[tuple[int, int, bool]] = []

    def _iconic(handle) -> bool:
        check = getattr(user32, "IsIconic", None)
        try:
            return bool(check(handle)) if check else False
        except Exception:  # noqa: BLE001 - 拿不到状态就当作没最小化
            return False

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_size_t)
    def collect(handle, _):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(handle):
            rect = ctypes.wintypes.RECT()
            if user32.GetWindowRect(handle, ctypes.byref(rect)):
                area = (rect.right - rect.left) * (rect.bottom - rect.top)
                if area > 0:
                    entry = (area, int(handle), _iconic(handle))
                    if APP_TITLE in _window_title(user32, handle):
                        named.append(entry)
                    else:
                        fallback.append(entry)
        return True

    user32.EnumWindows(collect, 0)

    def pick(entries: list[tuple[int, int, bool]]) -> int:
        """先挑没最小化的；都最小化了再按面积取最大的那个。"""
        if not entries:
            return 0
        visible = [entry for entry in entries if not entry[2]]
        return max(visible or entries)[1]

    # 1.25.51：多开过窗口时，用户点的那个窗口才是前台窗口；原来是挑面积最大的，
    # 结果可能挑中一个缩成一条的旧窗口 —— 点「最小化/关闭」没反应，或者页面淡出
    # 成白屏而窗口还在。
    try:
        foreground = int(user32.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        foreground = 0
    if foreground:
        for entry in named + fallback:
            if entry[1] == foreground:
                return foreground
    if named:
        return pick(named)
    if require_title:
        return 0
    return pick(fallback)


def watch_window(profile: Path, interval: float = 0.5) -> None:
    """守着应用窗口。

    1.26.3：这里原来还会执行页面发来的"最小化/最大化/关闭"。宿主窗口是普通的
    浏览器窗口，标题栏自带那三个按钮，页面不必再画一套 —— 这条通道一并撤掉，
    守护线程现在只做一件事：盯着窗口，窗口没了就收工。
    """
    user32 = _user32()
    if user32 is None:
        return
    deadline = time.monotonic() + _WINDOW_GRACE
    strict_until = time.monotonic() + _WINDOW_GRACE / 2
    hwnd = 0
    next_lookup = 0.0
    # 1.25.52：窗口刚出现的头一两秒里，浏览器可能还在恢复上一次的最小化状态。
    # 找到窗口后过 2 秒再看一眼：如果它还是最小化的，就把它恢复出来。
    startup_check = 0.0
    next_icon_check = 0.0
    while True:
        # 定位窗口：句柄缓存住，只在它失效时才重找，并留出退避间隔，避免抢占交互响应。
        if not _window_alive(user32, hwnd) and time.monotonic() >= next_lookup:
            hwnd = _app_window(user32, profile, require_title=time.monotonic() < strict_until)
            # 1.25.52：上一次运行留下的窗口可能还是最小化状态（浏览器复用了
            # 旧窗口）。启动后要把它恢复出来，否则老师点桌面图标只看到任务栏。
            if hwnd:
                apply_window_icon(user32, hwnd)
                try:
                    if getattr(user32, "IsIconic", None) and user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, _SW_RESTORE)
                        user32.SetForegroundWindow(hwnd)
                except Exception:  # noqa: BLE001 - 恢复失败不挡后续控制
                    pass
                if not startup_check:
                    startup_check = time.monotonic() + 2.0
                next_icon_check = time.monotonic() + 1.0
            next_lookup = time.monotonic() + (_WINDOW_LOOKUP_BACKOFF if not hwnd else _WINDOW_LOOKUP_INTERVAL)
        if not hwnd:
            if time.monotonic() > deadline:
                return
            time.sleep(interval)
            continue
        now = time.monotonic()
        if startup_check and now >= startup_check:
            startup_check = 0.0
            # 页面加载后浏览器可能又把图标改回默认，再刷一次 TUG。
            apply_window_icon(user32, hwnd)
            next_icon_check = now + 1.0
            try:
                if getattr(user32, "IsIconic", None) and user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, _SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
        elif now >= next_icon_check:
            next_icon_check = now + 1.0
            if _window_needs_icon_refresh(hwnd):
                apply_window_icon(user32, hwnd)
        time.sleep(interval)


def _window_icon_path() -> Path | None:
    """任务栏图标：优先打包进 _internal/assets 的 app.ico，源码版用仓库 assets。"""
    for candidate in (ASSETS / "app.ico", ROOT / "assets" / "app.ico"):
        if candidate.is_file():
            return candidate
    return None


def _enable_dpi_awareness() -> None:
    """开启 Windows 系统 DPI 感知，防止高分屏将启动画面与图标按低分位图拉伸发糊。"""
    if os.name != "nt":
        return
    with contextlib.suppress(Exception):
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001 - 旧版 Windows 回退到 user32 接口
            ctypes.windll.user32.SetProcessDPIAware()


def _load_window_icon_handles(ico: Path) -> tuple[int, int]:
    """加载并缓存用于 WM_SETICON 的大小图标句柄，避免守护轮询重复创建 GDI 对象。"""
    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("user32", use_last_error=True)
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    LR_DEFAULTSIZE = 0x0040
    SM_CXICON = 11
    SM_CXSMICON = 49
    big_size = int(dll.GetSystemMetrics(SM_CXICON) or 0)
    small_size = int(dll.GetSystemMetrics(SM_CXSMICON) or 0)
    key = (str(ico), big_size, small_size)
    cached = _ICON_HANDLE_CACHE.get(key)
    if cached is not None:
        return cached

    dll.LoadImageW.argtypes = [
        wintypes.HINSTANCE,
        wintypes.LPCWSTR,
        wintypes.UINT,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    dll.LoadImageW.restype = wintypes.HANDLE
    big_handle = (
        dll.LoadImageW(None, str(ico), IMAGE_ICON, big_size, big_size, LR_LOADFROMFILE)
        if big_size > 0
        else dll.LoadImageW(None, str(ico), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
    )
    small_handle = (
        dll.LoadImageW(None, str(ico), IMAGE_ICON, small_size, small_size, LR_LOADFROMFILE)
        if small_size > 0
        else big_handle
    )
    pair = (int(big_handle or 0), int(small_handle or big_handle or 0))
    if pair[0] or pair[1]:
        _ICON_HANDLE_CACHE[key] = pair
    return pair


def _send_window_message_timeout(hwnd: int, msg: int, wparam: int, lparam: int) -> tuple[bool, int]:
    """使用 SendMessageTimeoutW 收发窗口消息，防止浏览器冷启动或繁忙时阻塞守护线程。"""
    import ctypes
    from ctypes import wintypes

    dll = ctypes.WinDLL("user32", use_last_error=True)
    SMTO_ABORTIFHUNG = 0x0002
    dll.SendMessageTimeoutW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    dll.SendMessageTimeoutW.restype = ctypes.c_ssize_t
    result = ctypes.c_size_t(0)
    ok = bool(dll.SendMessageTimeoutW(hwnd, msg, wparam, lparam, SMTO_ABORTIFHUNG, 200, ctypes.byref(result)))
    return ok, int(result.value)


def _apply_window_app_id(hwnd: int, ico: Path) -> None:
    """为浏览器应用窗口写入独立 AppUserModelID 与图标资源，防止 Windows 11 任务栏归回 Edge 组。"""
    if os.name != "nt" or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        class PROPERTYKEY(ctypes.Structure):
            _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

        class PROPVARIANT(ctypes.Structure):
            _fields_ = [
                ("vt", wintypes.WORD),
                ("wReserved1", wintypes.WORD),
                ("wReserved2", wintypes.WORD),
                ("wReserved3", wintypes.WORD),
                ("pwszVal", ctypes.c_void_p),
                ("extra", ctypes.c_uint64),
            ]

        def _guid(raw: str) -> GUID:
            parsed = uuid.UUID(raw)
            return GUID(
                parsed.time_low, parsed.time_mid, parsed.time_hi_version, (ctypes.c_ubyte * 8)(*parsed.bytes[8:])
            )

        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
        ole32.CoTaskMemAlloc.restype = ctypes.c_void_p
        ole32.PropVariantClear.argtypes = [ctypes.c_void_p]
        ole32.PropVariantClear.restype = ctypes.c_long
        shell32.SHGetPropertyStoreForWindow.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long

        hr_co = int(ole32.CoInitializeEx(None, 2))
        try:
            iid_store = _guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
            fmtid_app = _guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}")
            store_ptr = ctypes.c_void_p()
            if shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid_store), ctypes.byref(store_ptr)) != 0:
                return
            if not store_ptr.value:
                return
            vtable = ctypes.cast(
                ctypes.cast(store_ptr, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p)
            )
            release_fn = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtable[2])
            set_value_fn = ctypes.WINFUNCTYPE(
                ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)
            )(vtable[6])
            commit_fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[7])
            try:
                props: list[tuple[int, str]] = [(5, APP_USER_MODEL_ID), (4, APP_TITLE), (3, f"{ico},0")]
                if getattr(launcher, "FROZEN", False) and Path(sys.executable).is_file():
                    props.append((2, f'"{sys.executable}"'))
                for pid, text in props:
                    encoded = (text + "\0").encode("utf-16-le")
                    mem = ole32.CoTaskMemAlloc(len(encoded))
                    if not mem:
                        continue
                    ctypes.memmove(mem, encoded, len(encoded))
                    pv = PROPVARIANT()
                    pv.vt = 31  # VT_LPWSTR
                    pv.pwszVal = mem
                    pk = PROPERTYKEY(fmtid_app, pid)
                    try:
                        set_value_fn(store_ptr, ctypes.byref(pk), ctypes.byref(pv))
                    finally:
                        ole32.PropVariantClear(ctypes.byref(pv))
                commit_fn(store_ptr)
            finally:
                release_fn(store_ptr)
        finally:
            if hr_co in (0, 1):
                ole32.CoUninitialize()
    except Exception:  # noqa: BLE001 - Shell 属性设置失败不能阻断主流程
        pass


def _window_needs_icon_refresh(hwnd: int) -> bool:
    """检查窗口当前大小图标是否已被 Edge/Chrome 覆盖，需要在后台补刷。"""
    if os.name != "nt" or not hwnd:
        return False
    ico = _window_icon_path()
    if ico is None:
        return False
    expected = None
    for (cached_path, _b, _s), pair in _ICON_HANDLE_CACHE.items():
        if cached_path == str(ico):
            expected = pair
            break
    if not expected:
        return False
    try:
        WM_GETICON = 0x007F
        ICON_SMALL = 0
        ICON_BIG = 1
        ok_big, cur_big = _send_window_message_timeout(hwnd, WM_GETICON, ICON_BIG, 0)
        ok_small, cur_small = _send_window_message_timeout(hwnd, WM_GETICON, ICON_SMALL, 0)
        if expected[0] and ok_big and cur_big != expected[0]:
            return True
        if expected[1] and ok_small and cur_small != expected[1]:
            return True
    except Exception:  # noqa: BLE001
        return False
    return False


def apply_window_icon(user32, hwnd: int) -> None:
    """把 Edge/Chrome --app 窗口的任务栏图标换成题有据 TUG，而不是浏览器默认图标。"""
    if not hwnd or user32 is None:
        return
    ico = _window_icon_path()
    if ico is None:
        return
    try:
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        big_handle, small_handle = _load_window_icon_handles(ico)
        if big_handle:
            _send_window_message_timeout(hwnd, WM_SETICON, ICON_BIG, big_handle)
        if small_handle or big_handle:
            _send_window_message_timeout(hwnd, WM_SETICON, ICON_SMALL, small_handle or big_handle)
        _apply_window_app_id(hwnd, ico)
    except Exception:  # noqa: BLE001 - 图标失败不能挡窗口
        pass


def _forget_restored_session(profile: Path) -> None:
    """删掉上次没关干净的会话文件，避免这次启动把旧窗口和新窗口一起摆出来。

    只在这个配置目录还没有浏览器进程时调用。进程还在的时候删，会把正在用的窗口弄坏。
    """
    default = profile / "Default"
    if not default.is_dir():
        return
    for name in _SESSION_LEFTOVERS:
        path = default / name
        if path.is_file():
            with contextlib.suppress(OSError):
                path.unlink()
    sessions = default / "Sessions"
    if sessions.is_dir():
        shutil.rmtree(sessions, ignore_errors=True)


class _AlreadyOpen:
    """屏幕上已经有一扇题有据窗口，这次没有再启动浏览器。"""

    def poll(self) -> None:
        return None


def open_window(url: str) -> subprocess.Popen | None:
    browsers = browser_candidates()
    if not browsers:
        webbrowser.open_new_tab(url)
        return None
    profile = window_profile()
    user32 = _user32()
    if user32 is not None:
        existing = _app_window(user32, profile, require_title=True)
        if existing:
            apply_window_icon(user32, existing)
            with contextlib.suppress(Exception):
                user32.ShowWindow(existing, _SW_RESTORE)
                user32.SetForegroundWindow(existing)
            threading.Thread(target=watch_window, args=(profile,), daemon=True).start()
            return _AlreadyOpen()  # type: ignore[return-value]
    if not _browser_pids(profile):
        _forget_restored_session(profile)
    web = subprocess.Popen(window_command(browsers[0], url, profile), creationflags=CREATE_NO_WINDOW)
    threading.Thread(target=watch_window, args=(profile,), daemon=True).start()
    return web


def _process_image_name(pid: int) -> str:
    """进程的可执行文件名，小写。查不到就返回空字符串。"""
    if not pid or os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).name.lower()
    finally:
        kernel.CloseHandle(handle)


def _own_window_hwnd(user32) -> int:
    """题有据自己创建的那扇窗口。Edge 里打开的同名页面不算。"""
    import ctypes

    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_size_t)
    def collect(handle, _):
        if not user32.IsWindowVisible(handle):
            return True
        if APP_TITLE not in _window_title(user32, handle):
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        if _process_image_name(int(pid.value)) in _OWN_WINDOW_PROCESSES:
            found.append(int(handle))
        return True

    user32.EnumWindows(collect, 0)
    if not found:
        return 0
    try:
        foreground = int(user32.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        foreground = 0
    if foreground in found:
        return foreground
    return found[0]


def _own_window_storage() -> Path:
    folder = launcher.USER_ROOT / "own-window"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class _OwnWindowApi:
    """暴露给本进程 WebView2 页面的最小窗口控制接口（不含任何文件或系统命令权限）。"""

    def __init__(self) -> None:
        self._window = None
        self._fullscreen = False

    def bind(self, window) -> None:
        self._window = window

    def is_fullscreen(self) -> bool:
        if self._window is not None:
            return bool(getattr(self._window, "fullscreen", self._fullscreen))
        return self._fullscreen

    def toggle_fullscreen(self) -> bool:
        if self._window is None:
            return self._fullscreen
        self._window.toggle_fullscreen()
        self._fullscreen = bool(getattr(self._window, "fullscreen", not self._fullscreen))
        return self._fullscreen


def _work_area_logical_size() -> tuple[int, int]:
    """返回主显示器去除任务栏后的可用逻辑宽高，拿不到时退回常见桌面尺寸。"""
    if os.name != "nt":
        return (1440, 900)
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return (1440, 900)
        dpi = 96
        get_dpi = getattr(user32, "GetDpiForSystem", None)
        if callable(get_dpi):
            dpi = int(get_dpi() or 96)
        dpi = max(96, dpi)
        width = max(640, int((rect.right - rect.left) * 96 / dpi))
        height = max(480, int((rect.bottom - rect.top) * 96 / dpi))
        return (width, height)
    except Exception:  # noqa: BLE001 - 桌面工作区读取异常时退回默认窗口尺寸
        return (1440, 900)


def _clamp_own_window_to_work_area(user32, hwnd: int) -> None:
    """若非最大化窗口底边越过任务栏，收进系统可用工作区内，避免菜单或底栏被遮挡。"""
    if os.name != "nt" or not hwnd:
        return
    try:
        import ctypes
        from ctypes import wintypes

        if getattr(user32, "IsZoomed", None) and user32.IsZoomed(hwnd):
            return
        work = wintypes.RECT()
        win = wintypes.RECT()
        if not user32.SystemParametersInfoW(48, 0, ctypes.byref(work), 0):
            return
        if not user32.GetWindowRect(hwnd, ctypes.byref(win)):
            return
        work_w = max(480, int(work.right - work.left))
        work_h = max(360, int(work.bottom - work.top))
        cur_w = int(win.right - win.left)
        cur_h = int(win.bottom - win.top)
        new_w = min(cur_w, work_w)
        new_h = min(cur_h, work_h)
        new_x = max(int(work.left), min(int(win.left), int(work.right) - new_w))
        new_y = max(int(work.top), min(int(win.top), int(work.bottom) - new_h))
        if (new_x, new_y, new_w, new_h) != (int(win.left), int(win.top), cur_w, cur_h):
            user32.SetWindowPos(hwnd, 0, new_x, new_y, new_w, new_h, 0x0004 | 0x0010)  # NOZORDER | NOACTIVATE
    except Exception:  # noqa: BLE001 - 坐标调整失败不阻断窗口展示
        pass


def run_own_window(url: str) -> None:
    """打开题有据自己的窗口，直到用户关掉它才返回。

    窗口属于本进程。WebView2 只在这扇窗口里面画页面，不再另开一个 Edge。
    """
    import webview

    icon = ASSETS / "app.ico"
    work_w, work_h = _work_area_logical_size()
    init_w = min(1440, max(960, work_w - 32))
    init_h = min(900, max(640, work_h - 32))
    min_w = min(1100, init_w)
    min_h = min(720, init_h)
    window_api = _OwnWindowApi()
    window = webview.create_window(
        APP_TITLE,
        url,
        width=init_w,
        height=init_h,
        min_size=(min_w, min_h),
        text_select=True,
        background_color="#F4F7FB",
        js_api=window_api,
    )
    window_api.bind(window)

    def _ensure_front_after_start() -> None:
        # Splash 销毁后 Windows 可能把焦点还给桌面/上一扇窗口，等 WebView2 首帧就绪后再复核一次。
        time.sleep(0.25)
        user32 = _user32()
        if user32 is None:
            return
        hwnd = _own_window_hwnd(user32)
        if not hwnd:
            return
        _clamp_own_window_to_work_area(user32, hwnd)
        fg = 0
        with contextlib.suppress(Exception):
            fg = int(user32.GetForegroundWindow() or 0)
        if fg != hwnd or user32.IsIconic(hwnd):
            _raise_own_window(user32, hwnd, pulse_topmost=True)

    def _activate_own_window() -> None:
        user32 = _user32()
        if user32 is None:
            return
        hwnd = _own_window_hwnd(user32)
        if not hwnd:
            for _ in range(10):
                time.sleep(0.05)
                hwnd = _own_window_hwnd(user32)
                if hwnd:
                    break
        if hwnd:
            apply_window_icon(user32, hwnd)
            with contextlib.suppress(Exception):
                window.restore()
            _clamp_own_window_to_work_area(user32, hwnd)
            _raise_own_window(user32, hwnd, pulse_topmost=True)
        threading.Thread(target=_ensure_front_after_start, daemon=True).start()

    window.events.shown += _activate_own_window
    webview.start(
        gui="edgechromium",
        private_mode=False,
        storage_path=str(_own_window_storage()),
        icon=str(icon) if icon.is_file() else None,
    )


def _raise_own_window(user32, hwnd: int, *, pulse_topmost: bool = False) -> None:
    """把题有据自己的窗口切到最前（兼容首次启动与从 OneNote 回跳）。

    启动画面刚关闭或从 OneNote 点「回到题有据」时，新窗口可能没有前台权，
    直接 SetForegroundWindow 会被系统丢在任务栏后面。先把当前前台线程和这扇
    窗口线程接到一起再置前，首次启动还可短脉冲一次置顶确保窗口弹出显示。
    """
    import ctypes

    if getattr(user32, "IsIconic", None) and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)
    else:
        user32.ShowWindow(hwnd, _SW_SHOW)
    foreground = 0
    try:
        foreground = int(user32.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        foreground = 0
    attached = False
    fg_thread = 0
    target_thread = 0
    if foreground and foreground != hwnd:
        fg_pid = ctypes.c_ulong()
        target_pid = ctypes.c_ulong()
        fg_thread = int(user32.GetWindowThreadProcessId(foreground, ctypes.byref(fg_pid)) or 0)
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid)) or 0)
        if fg_thread and target_thread and fg_thread != target_thread:
            attached = bool(user32.AttachThreadInput(fg_thread, target_thread, True))
    if getattr(user32, "BringWindowToTop", None):
        user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(fg_thread, target_thread, False)
    if pulse_topmost:
        after_fg = 0
        with contextlib.suppress(Exception):
            after_fg = int(user32.GetForegroundWindow() or 0)
        if after_fg != hwnd and getattr(user32, "SetWindowPos", None):
            with contextlib.suppress(Exception):
                user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE)
                user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE)
                user32.SetForegroundWindow(hwnd)


def focus_existing_window() -> bool:
    """应用已经在运行时，把题有据自己的那扇窗口切到前面，而不是再开一个。

    OneNote 里点「在题有据中打开」走的也是这里：深链写好之后，把这扇窗口拉回前台，
    页面再按题目定位。不会另开浏览器。
    """
    user32 = _user32()
    if user32 is None:
        return False
    hwnd = _own_window_hwnd(user32)
    if not hwnd:
        return False
    apply_window_icon(user32, hwnd)
    _raise_own_window(user32, hwnd)
    return True


def _focus_running_window() -> bool:
    """服务已经在跑时，先等现有窗口出现并切到前面。

    窗口还在画的那几秒里再点一次图标，原来会立刻再开一扇。两扇连着同一个后端，
    关掉被盯上的那扇，服务就停了，剩下的一扇页面变空。
    """
    deadline = time.monotonic() + _FOCUS_RETRY_SECONDS
    while True:
        if focus_existing_window():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_FOCUS_RETRY_INTERVAL)


def _replacement_window(user32, profile: Path, closed_hwnd: int) -> int:
    """刚关掉的那扇之外，还有没有别的题有据窗口。没有就返回 0。"""
    deadline = time.monotonic() + _REPLACEMENT_GRACE
    while True:
        candidate = _app_window(user32, profile, require_title=True)
        if candidate and candidate != closed_hwnd and _window_alive(user32, candidate):
            return candidate
        if time.monotonic() >= deadline:
            return 0
        time.sleep(0.2)


def _profile_still_open(profile: Path) -> bool:
    """应用窗口被交给已在运行的浏览器进程时，按命令行里的配置目录找它是否还开着。"""
    marker = str(profile).replace("'", "''")
    script = (
        "$n = @(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{marker}*' }}).Count; Write-Output $n"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        return int((result.stdout or "0").strip() or 0) > 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _wait_until_window_closed(web: subprocess.Popen, profile: Path, user32) -> str:
    """等窗口出现，再守到它关闭。

    「关好了」的判据是窗口本身：句柄一失效就是用户已经把窗关了，立刻收工去停服
    务。浏览器那些后台进程（崩溃收集器等）随后自己退，退得慢也不会拖住软件。
    """
    deadline = time.monotonic() + _WINDOW_GRACE
    # 前一半的宽限期只认标题对得上的窗口。老实例没退干净时，别的窗口可能先冒
    # 出来，抢在题有据窗口之前被找到；它随后自己消失，就会被误判成「用户关了窗」。
    strict_until = time.monotonic() + _WINDOW_GRACE / 2
    hwnd = 0
    next_lookup = 0.0
    while not hwnd:
        if web.poll() is not None:
            return _service_died(web)
        if time.monotonic() >= next_lookup:
            hwnd = _app_window(user32, profile, require_title=time.monotonic() < strict_until)
            next_lookup = time.monotonic() + _WINDOW_LOOKUP_BACKOFF
        if hwnd:
            apply_window_icon(user32, hwnd)
            break
        if time.monotonic() > deadline:
            return "no-window"
        time.sleep(0.3)
    # 最小化不会让句柄失效，所以这一段一直等得到 —— 用户开着软件一整天也不该被
    # 某个超时打断。服务真死了的话上面那句 poll 会先兜住。
    while _window_alive(user32, hwnd):
        if web.poll() is not None:
            return _service_died(web)
        time.sleep(0.3)
    return "closed"


def _wait_by_profile(web: subprocess.Popen | None) -> str:
    """拿不到 Win32 时的退路：仍按配置目录判断，但不再无限等。

    这条路只有在非常规环境下才会走到（没装 user32 之类），平时用不到；留着它是
    因为它是当时唯一能判断「窗口关了」的依据，不能直接删掉。web 为 None 表示
    这个进程没启动服务，只等窗口。"""
    profile = window_profile()
    deadline = time.monotonic() + _CLOSE_BACKSTOP
    while _profile_still_open(profile):
        if web is not None and web.poll() is not None:
            return _service_died(web)
        if time.monotonic() > deadline:
            print("浏览器进程迟迟没有退出，不再等它，直接停止服务。", flush=True)
            return "closed-slow"
        time.sleep(1.0)
    return "closed" if web is None or web.poll() is None else _service_died(web)


def wait_for_window(window: subprocess.Popen | None, web: subprocess.Popen) -> str:
    """等到用户关掉题库窗口（或网页服务意外退出）。

    返回结束原因。以前这两种情况都直接 return，日志里写的是同一句「窗口已关闭」，
    于是「服务自己没了」和「你把窗口关了」分不开 —— 出了事回头查，只能靠猜。
    """
    if window is None:
        # 没有 Edge/Chrome，页面开在默认浏览器里，另有一个小窗口管收尾。
        _control_window(web)
        return "closed" if web.poll() is not None else "closed"
    user32 = _user32()
    if user32 is None:
        return _wait_by_profile(web)
    return _wait_until_window_closed(web, window_profile(), user32)


def _service_died(web: subprocess.Popen) -> str:
    """窗口还开着，网页服务却自己退了。

    这种情况以前不留任何记录，事后只看得出「程序没了」，看不出服务是怎么没的。
    退出码和当时的时间都写下来，下次再遇到就有据可查，不用再猜。
    """
    code = web.poll()
    print(
        f"网页服务在窗口仍开着时退出（退出码 {code}）；"
        f"如果是 0 多半是被人结束的，不是自己崩的。"
        f"时间 {time.strftime('%Y-%m-%d %H:%M:%S')}",
        flush=True,
    )
    return "service-died"


def wait_for_reattached_window(window: subprocess.Popen | None) -> None:
    """Wait out a window opened onto an instance this process did not start.

    Same shape as ``wait_for_window`` without the server handle: this process
    started no service, so the only thing it has to outlive is the window.

    判据也换成窗口本身：用户关了窗，这个进程就该跟着结束，而不是陪着浏览器
    那几个晚退的后台进程一起等 —— 那样每多开一个窗口，就多一次「退不掉」。
    """
    if window is None:
        # No Edge/Chrome, so the page went to the default browser, which draws a
        # real title bar and needs none of this.
        return
    user32 = _user32()
    if user32 is None:
        _wait_by_profile(None)
        return
    deadline = time.monotonic() + _WINDOW_GRACE
    strict_until = time.monotonic() + _WINDOW_GRACE / 2
    hwnd = 0
    next_lookup = 0.0
    while not hwnd:
        if time.monotonic() >= next_lookup:
            hwnd = _app_window(user32, window_profile(), require_title=time.monotonic() < strict_until)
            next_lookup = time.monotonic() + _WINDOW_LOOKUP_BACKOFF
        if hwnd:
            break
        if time.monotonic() > deadline:
            return
        time.sleep(0.3)
    while _window_alive(user32, hwnd):
        time.sleep(0.3)


def _control_window(web: subprocess.Popen) -> None:
    """找不到 Edge/Chrome 时，已在默认浏览器里打开；留一个小窗口，关掉它即停止服务。"""
    try:
        import tkinter as tk
    except ImportError:
        while web.poll() is None:
            time.sleep(1)
        return
    root = tk.Tk()
    root.title(APP_TITLE)
    root.geometry("360x140")
    tk.Label(root, text="题有据正在运行，已在浏览器中打开。\n关闭这个小窗口即停止服务。", pady=24).pack()
    tk.Button(root, text="停止并退出", command=root.destroy).pack()
    _set_icon(root)
    root.mainloop()


# ---------------------------------------------------------------- 启动画面


def _set_icon(root) -> None:
    ico = ASSETS / "app.ico"
    with contextlib.suppress(Exception):
        if ico.is_file():
            root.iconbitmap(default=str(ico))


def _load_splash_icon(icon_size: int, master):
    """使用高质量 LANCZOS 从 256px 源图缩放启动图标，不可用时回退 Tk 内置缩放。"""
    if not ICON_PNG.is_file():
        return None
    target = max(16, int(icon_size))
    with contextlib.suppress(Exception):
        from PIL import Image, ImageTk

        with Image.open(ICON_PNG) as raw:
            resized = raw.convert("RGBA").resize((target, target), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(resized, master=master)
    with contextlib.suppress(Exception):
        import tkinter as tk

        raw_tk = tk.PhotoImage(file=str(ICON_PNG), master=master)
        step = max(1, round(raw_tk.width() / target))
        return raw_tk.subsample(step, step) if step > 1 else raw_tk
    return None


class Splash:
    """启动时的小窗口：图标、名称、当前步骤和进度条。tkinter 不可用时什么也不显示。"""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.root = None
        self.version_text = _resolve_app_version()
        _enable_dpi_awareness()
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError:
            return
        try:
            root = tk.Tk()
        except Exception:  # noqa: BLE001 - 没有图形环境
            return
        self.root = root
        root.overrideredirect(True)
        root.configure(bg="#ffffff")
        scale = 1.0
        with contextlib.suppress(Exception):
            scale = max(1.0, float(root.winfo_fpixels("1i")) / 96.0)
        width, height = round(420 * scale), round(230 * scale)
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 3
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.attributes("-topmost", True)
        _set_icon(root)
        frame = tk.Frame(root, bg="#ffffff", highlightthickness=1, highlightbackground="#dfe4dd")
        frame.pack(fill="both", expand=True)
        self.image = _load_splash_icon(round(64 * scale), root)
        if self.image is not None:
            tk.Label(frame, image=self.image, bg="#ffffff").pack(pady=(round(26 * scale), round(8 * scale)))
        else:
            tk.Label(frame, text="题", font=("SimSun", 30, "bold"), fg="#fff4dc", bg="#1f6b5f", width=2).pack(
                pady=(round(26 * scale), round(8 * scale))
            )
        title_row = tk.Frame(frame, bg="#ffffff")
        title_row.pack()
        tk.Label(title_row, text=APP_TITLE, font=("Microsoft YaHei UI", 14, "bold"), fg="#1c2a28", bg="#ffffff").pack(
            side="left"
        )
        if self.version_text:
            tk.Label(
                title_row,
                text=f"v{self.version_text}",
                font=("Microsoft YaHei UI", 8, "bold"),
                fg="#047857",
                bg="#ecfdf5",
                padx=round(6 * scale),
                pady=max(1, round(1 * scale)),
            ).pack(side="left", padx=(round(6 * scale), 0), pady=(round(2 * scale), 0))
        tk.Label(frame, text=APP_SUBTITLE, font=("Microsoft YaHei UI", 9), fg="#72807b", bg="#ffffff").pack(
            pady=(round(2 * scale), 0)
        )
        self.status = tk.StringVar(value="正在启动…")
        tk.Label(frame, textvariable=self.status, font=("Microsoft YaHei UI", 9), fg="#72807b", bg="#ffffff").pack(
            pady=(round(6 * scale), round(10 * scale))
        )
        style = ttk.Style(root)
        with contextlib.suppress(Exception):
            style.theme_use("clam")
        style.configure(
            "QB.Horizontal.TProgressbar",
            troughcolor="#eaede7",
            background="#1f6b5f",
            bordercolor="#eaede7",
            lightcolor="#1f6b5f",
            darkcolor="#1f6b5f",
            thickness=max(4, round(4 * scale)),
        )
        bar = ttk.Progressbar(
            frame, mode="indeterminate", length=round(260 * scale), style="QB.Horizontal.TProgressbar"
        )
        bar.pack()
        bar.start(12)

    def say(self, text: str) -> None:
        print(text, flush=True)
        self.events.put(("status", text))

    def run(self, work) -> object:
        """在后台线程执行 work(self)，主线程跑启动画面；返回 work 的结果或抛出它的异常。"""
        outcome: dict[str, object] = {}

        def target() -> None:
            try:
                outcome["value"] = work(self)
            except BaseException as exc:  # noqa: BLE001 - 交回主线程再抛
                outcome["error"] = exc
            finally:
                self.events.put(("done", None))

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        if self.root is None:
            thread.join()
        else:

            def poll() -> None:
                try:
                    while True:
                        kind, value = self.events.get_nowait()
                        if kind == "status":
                            self.status.set(value)
                        elif kind == "done":
                            self.root.destroy()
                            return
                except queue.Empty:
                    pass
                self.root.after(80, poll)

            self.root.after(80, poll)
            self.root.mainloop()
            thread.join()
        if "error" in outcome:
            raise outcome["error"]  # type: ignore[misc]
        return outcome.get("value")


# ---------------------------------------------------------------- 主流程


def _hand_off_to_console(reason: str) -> int:
    print(f"转交命令行启动器：{reason}", flush=True)
    if not CONSOLE_LAUNCHER.is_file():
        _message(f"{reason}。\n请双击“启动题有据.cmd”。", error=True)
        return 1
    _message(f"{reason}。\n\n接下来会打开命令行窗口，请按提示完成一次；以后双击图标就能直接打开窗口版。")
    subprocess.Popen(["cmd.exe", "/c", str(CONSOLE_LAUNCHER)], cwd=ROOT, creationflags=CREATE_NEW_CONSOLE)
    return 0


def _set_app_id() -> None:
    with contextlib.suppress(Exception):
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def _configure_credentials(*, first_run: bool, reason: str = "") -> bool:
    if reason:
        print(f"需要配置 API：{reason}", flush=True)
    try:
        saved = show_credential_dialog(first_run=first_run, verify_mineru=launcher._mineru_token_validity)
    except Exception as exc:  # noqa: BLE001 - GUI failure must be visible in a windowed build
        print(f"API 配置窗口打开失败：{exc!r}", flush=True)
        _message(f"无法打开 API 配置窗口：{exc}", error=True)
        return False
    if not saved:
        print("用户取消了 API 配置。", flush=True)
    return saved


def main(arguments: list[str] | None = None) -> int:
    if os.name != "nt":
        print("窗口版启动器仅支持 Windows。")
        return 1
    log = _log_stream()
    # 原生层的崩溃（段错误、访问冲突）拿不到 Python 异常，日志里会一个字都没有，
    # 事后只知道「程序没了」。打开它，这类崩溃会把当时的调用栈一起写进日志。
    # 取不到真实句柄时（日志被换成假对象）就跳过：记不了账，也不能因此起不来。
    try:
        faulthandler.enable(log)
    except Exception:  # noqa: BLE001 - 记不了崩溃绝不能连启动一起挡住
        pass
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        print(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} 窗口版启动 ====", flush=True)
        try:
            return _main([] if arguments is None else arguments)
        except Exception as exc:  # noqa: BLE001 - 任何失败都要让用户看得到
            print(f"启动失败：{exc!r}", flush=True)
            _message(f"启动失败：{exc}\n\n详情见：{launcher.RUNTIME}", error=True)
            return 1
        finally:
            log.flush()


def _handle_protocol_uri(uri: str) -> int:
    """处理 OneNote 回跳的 tiyouju:// 协议：写深链、置前已有窗口；冷启动则交回正常开机流程。"""
    try:
        from core import tiyouju_protocol
    except ImportError:
        sys.path.insert(0, str(launcher.BACKEND))
        from core import tiyouju_protocol  # type: ignore

    try:
        tiyouju_protocol.ensure_registered()
    except Exception as exc:  # noqa: BLE001 - 注册失败不能挡住回跳
        print(f"协议注册失败：{exc!r}", flush=True)

    action = tiyouju_protocol.dispatch_protocol_action(uri)
    question_id = str(action.get("q") or "").strip() if action and action.get("action") == "question" else None
    action_name = str(action.get("action") or "") if action else ""

    existing = launcher._running_instance()
    if existing:
        focused = _focus_running_window()
        print(f"协议回跳：已通知运行中的窗口 action={action_name} q={question_id} focused={focused}", flush=True)
        return 0

    # 尚未运行：本进程就是冷启动，不要再拉一份 exe。深链留给 Eagle 首屏消费。
    print(f"协议回跳：将冷启动 action={action_name} q={question_id}", flush=True)
    return -1


def _main(arguments: list[str] | None = None) -> int:
    _enable_dpi_awareness()
    _set_app_id()
    arguments = [] if arguments is None else list(arguments)
    if arguments == ["--configure"]:
        saved = _configure_credentials(first_run=False)
        if saved:
            running = launcher._running_instance()
            suffix = "\n\n题库当前正在运行：新配置从下一份任务或下一次重读开始时生效。" if running else ""
            _message(f"API 密钥与模型选择已保存；密钥已加密。{suffix}")
        return 0

    try:
        from core import tiyouju_protocol as _protocol
    except ImportError:
        sys.path.insert(0, str(launcher.BACKEND))
        try:
            from core import tiyouju_protocol as _protocol  # type: ignore
        except ImportError:
            _protocol = None
    if _protocol is not None:
        try:
            _protocol.ensure_registered()
        except Exception as exc:  # noqa: BLE001
            print(f"启动时协议注册失败：{exc!r}", flush=True)
        protocol_uri = _protocol.extract_uri_from_argv(arguments)
        if protocol_uri:
            handled = _handle_protocol_uri(protocol_uri)
            if handled >= 0:
                return handled
            arguments = []

    if arguments:
        _message("无法识别的启动参数。", error=True)
        return 2
    if not launcher.FROZEN and sys.version_info[:2] != (3, 12):
        venv_window_python = launcher.PYTHON.with_name("pythonw.exe")
        if venv_window_python.is_file():
            # 用本目录自己的 Python 3.12 环境重新打开窗口版。
            subprocess.Popen(
                [str(venv_window_python), str(ROOT / "app_launcher.pyw")], cwd=ROOT, creationflags=CREATE_NO_WINDOW
            )
            return 0
        return _hand_off_to_console(f"需要 Python 3.12（当前是 {sys.version_info.major}.{sys.version_info.minor}）")
    existing = launcher._running_instance()
    if existing:
        # 已经在运行：只把自己的那扇窗口拉到前面。不再另开 Edge，也不再开第二扇。
        if _focus_running_window():
            print(f"已在运行：{existing}，已切到现有窗口", flush=True)
            return 0
        _message("题有据已经在运行。请从任务栏点开现有窗口，这次不会再开一扇。")
        print(f"已在运行：{existing}，没有找到自己的窗口，不再另开", flush=True)
        return 0
    if _needs_install():
        return _hand_off_to_console("第一次使用（或组件有更新），需要联网安装已验证版本的组件")
    instance_mutex = launcher.InstanceMutex()
    if not instance_mutex.acquired:
        _message("题库正在启动或已在运行，请稍候几秒再试。")
        return 0
    job = None
    processes: list[subprocess.Popen] = []
    logs: list[object] = []
    configure_reason: str | None = None
    try:
        splash = Splash()

        def start(ui: Splash) -> tuple[str, str]:
            nonlocal job
            ui.say("正在检查运行环境和本地数据…")
            original_step = launcher._run_step
            launcher._run_step = _run_step_hidden
            try:
                launcher._prepare()
            finally:
                launcher._run_step = original_step
            ui.say("正在读取本机设置…")
            try:
                credential_pools = credential_pools_quietly()
            except NeedsConsole as exc:
                return ("configure", str(exc))
            preferences = launcher._model_preferences()
            model_env = launcher.model_preference_environment(preferences)
            base_env = launcher._child_environment(
                {
                    key: value
                    for key, value in os.environ.items()
                    if key.upper() not in launcher.SECRET_NAMES
                    and key.upper() not in launcher.MODEL_ENVIRONMENT_KEYS
                    and key.upper() not in launcher.CREDENTIAL_STATUS_NAMES
                }
            )
            base_env.update(launcher._parallel_environment(base_env, credential_pools, preferences))
            # 模型角色和型号可供网页展示；三类密钥仍只交给后台工作者。
            web_env = dict(base_env)
            # Native export delivery is available only to the desktop-owned
            # loopback service; console/browser deployments keep attachments.
            # This is a capability flag, never a credential or browser token.
            web_env["QB_DESKTOP_EXPORT"] = "1"
            web_env.update(launcher._credential_status_environment(credential_pools))
            web_env.update(model_env)
            worker_env = launcher._worker_credential_environment(base_env, credential_pools)
            worker_env.pop("QB_DESKTOP_EXPORT", None)
            # Worker 执行题库标签与答案任务时同样需要统一凭据的可用状态标记。
            worker_env.update(launcher._credential_status_environment(credential_pools))
            worker_env.update(model_env)
            del credential_pools, preferences, model_env
            ui.say("正在启动后台服务…")
            port = launcher._available_port()
            url = f"http://127.0.0.1:{port}"
            job = launcher.ChildJob()
            worker, log = launcher._start(["run_worker"], worker_env, "worker.log")
            processes.append(worker)
            logs.append(log)
            job.add(worker)
            del worker_env
            web, log = launcher._start(["runserver", f"127.0.0.1:{port}", "--noreload"], web_env, "web.log")
            processes.append(web)
            logs.append(log)
            job.add(web)
            launcher._health(url, web)
            launcher._write_instance(port)
            if worker.poll() is not None:
                print("注意：后台工作者没有启动成功，新卷暂时不会被处理；详见 worker.log", flush=True)
            ui.say("正在打开窗口…")
            # 默认进题库工作台；切题核对仍在根路径 /
            home = f"{url.rstrip('/')}/eagle"
            try:
                from core import tiyouju_protocol as protocol

                pending = protocol.consume_deep_link()
                if pending:
                    if pending.get("q"):
                        # 冷启动时把 q 直接带进首屏；同时再写回一份，供轮询兜底。
                        protocol.queue_deep_link(pending["q"])
                        home = protocol.build_target_url(port, pending["q"])
                    elif pending.get("action"):
                        protocol.queue_deep_link(pending)
            except Exception as exc:  # noqa: BLE001
                print(f"读取回跳深链失败：{exc!r}", flush=True)
            return ("ready", home)

        kind, value = splash.run(start)
        if kind == "configure":
            configure_reason = value
            return 0
        url = value
        print(f"服务就绪：{url}", flush=True)
        run_own_window(url)
        print("窗口已关闭，停止服务", flush=True)
        return 0
    finally:
        launcher._clear_instance()
        if job is not None:
            job.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        for log in logs:
            log.close()
        instance_mutex.close()
        if configure_reason and _configure_credentials(first_run=False, reason=configure_reason):
            return _main([])


if __name__ == "__main__":
    raise SystemExit(main())
