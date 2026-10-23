"""窗口版启动器：像普通软件一样打开题库。

双击桌面上的“题有据”图标（或 app_launcher.pyw）后：
1. 不弹黑色命令行窗口，先显示一个小的启动画面；
2. 在后台启动网页服务和工作者（沿用 start_question_bank.py 的全部逻辑与安全约束）；
3. 用 Edge（没有 Edge 时用 Chrome）的“应用窗口”模式打开题库：没有地址栏和标签页，
   有自己的任务栏图标；
4. 关掉这个窗口，后台服务随之停止。

第一次安装组件时会交给原来的命令行启动器；API 凭据则在软件
的“设置 → 服务与密钥”中录入。独立配置窗口仅作为凭据文件损坏时的故障恢复入口。
"""

from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
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
import webbrowser
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
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_NEW_CONSOLE = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)


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
        args, cwd=launcher.PROGRAM_ROOT if launcher.FROZEN else launcher.BACKEND,
        env=setup_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", creationflags=CREATE_NO_WINDOW,
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


def credentials_ready(
    saved: dict[str, object] | None = None,
    preferences: dict[str, str] | None = None,
) -> bool:
    """不联网，只看 MinerU 与当前所选主读模型的凭据是否就绪。"""
    saved = _saved_credentials() if saved is None else saved
    preferences = launcher._model_preferences() if preferences is None else preferences
    has_token = bool(
        launcher._strip_bearer(os.environ.get("MINERU_TOKEN", ""))
        or credential_pool(saved, "mineru")
    )
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
    return {
        service: credential_pool(saved, service)
        for service in launcher.POOL_ENVIRONMENT_NAMES
    }


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
    base = launcher.USER_ROOT if launcher.FROZEN else Path(
        os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    ) / "QuestionBankCard"
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
    for name in ("Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache"):
        target = profile / name
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
        if all(data.get(key) is value for key, value in settings.items()):
            return True
        data.update(settings)
        preferences.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=preferences.parent,
                                         prefix=".password-preferences-", suffix=".tmp", delete=False) as stream:
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
        str(browser), f"--app={url}", f"--user-data-dir={profile}",
        "--profile-directory=Default", "--no-first-run", "--no-default-browser-check",
    ]


# ---------------------------------------------------------------- 窗口控制

# 窗口不带系统标题栏：页面顶部留了拖拽区（-webkit-app-region: drag），
# 最小化与关闭由页面右上角自绘。
#
# 去掉标题栏之后那两个按钮就成了唯一的窗口控制入口，所以窗口动作必须同时做出来 ——
# 浏览器没有「最小化/关闭本窗口」的脚本接口（window.close() 对命令行 --app 打开的
# 窗口无效），因此网页只写一个控制文件，由这里去操作真正的窗口句柄。
#
# 只用 ctypes：本文件已经在用它弹系统对话框，不为同一件事再引入一个打包依赖。
_SW_MINIMIZE = 6
_SW_RESTORE = 9
_SW_MAXIMIZE = 3
_WM_CLOSE = 0x0010
_CONTROL_NAME = "window-control.json"
_STATE_NAME = "window-state.json"
# 页面点一下「最小化」就得马上有反应。真机上量到的是：请求 20~100ms 就到了，
# 窗口却要 1.2 秒才缩下去 —— 那是这里一秒一轮询的代价。100ms 一轮，
# 每轮只多读一个几十字节的 json，可以忽略不计。
_CONTROL_POLL = 0.1
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


def control_file() -> Path:
    """页面与启动器之间唯一约定的控制文件。两端各自算出路径但指向同一个：
    启动器把自己的 DATA_ROOT 通过 QB_DATA_ROOT 传给了服务进程。"""
    return launcher.DATA_ROOT / _CONTROL_NAME


def window_state_file() -> Path:
    """窗口当前"标题栏还在不在"的状态文件。页面据此决定要不要自己画按钮。"""
    return launcher.DATA_ROOT / _STATE_NAME


def _caption_reachable(rect: tuple[int, int, int, int], screen: tuple[int, int]) -> bool:
    """纯算的版本，方便测：窗口矩形 + 屏幕尺寸 → 标题栏够不够得着。"""
    left, top, right, bottom = rect
    width, height = screen
    fullscreen = abs(right - left) >= width - 2 and abs(bottom - top) >= height - 2
    if fullscreen:
        return False
    # 窗口顶边被拖到屏幕上方之外 → 标题栏那条不在屏幕上。
    return top >= -2


def caption_visible(user32, hwnd) -> bool:
    """标题栏这三个按钮现在点得到吗。

    窗口是普通窗口时，标题栏自带最小化/最大化/关闭 —— 页面就不该再画一份
    （两套控件并排，老师会不知道该点哪一个）。但有两种情况标题栏够不着：

    - 窗口被整屏显示（F11）：标题栏整个没了；
    - 窗口被拖到屏幕边缘外面：标题栏那条被顶出去了。

    这两种时候页面右上角那份才该露出来。
    """
    if user32 is None or not hwnd:
        return True
    try:
        import ctypes
        import ctypes.wintypes

        rect = ctypes.wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        screen = (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))
        return _caption_reachable((rect.left, rect.top, rect.right, rect.bottom), screen)
    except Exception:  # noqa: BLE001 - 拿不准就当标题栏还在（页面不画按钮）
        return True


def write_window_state(user32, hwnd) -> None:
    """把"标题栏还在不在"写给页面。只在变了的时候写，免得每秒刷磁盘。"""
    visible = caption_visible(user32, hwnd)
    if getattr(write_window_state, "_last", None) == visible:
        return
    try:
        target = window_state_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"caption": visible}), encoding="utf-8")
    except OSError:
        pass
    write_window_state._last = visible


def _user32():
    if os.name != "nt":
        return None
    try:
        import ctypes

        return ctypes.windll.user32
    except Exception:  # noqa: BLE001 - 拿不到 Win32 就保持原样，不让启动器崩溃
        return None


def _browser_pids(profile: Path) -> set[int]:
    """命令行里带着这个配置目录的浏览器进程号。窗口可能被交给一个已经在运行的
    浏览器进程，不能只看自己 spawn 出来的那个 pid。"""
    marker = str(profile).replace("'", "''")
    script = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | ForEach-Object {{ $_.ProcessId }}")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {int(line) for line in (result.stdout or "").split() if line.strip().isdigit()}


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


def _read_control() -> tuple[str, str] | None:
    """控制文件里的 (动作, nonce)。文件不存在、被写坏或缺字段都当没有。"""
    try:
        data = json.loads(control_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("action"), str) and isinstance(data.get("nonce"), str):
        return data["action"], data["nonce"]
    return None


def watch_window(profile: Path, interval: float = _CONTROL_POLL) -> None:
    """守着应用窗口，执行页面发来的最小化/关闭。

    窗口消失且已经过了宽限期就收工，不会拖住进程退出。
    """
    user32 = _user32()
    if user32 is None:
        return
    # 上一次运行可能留下了控制文件（上次没关窗口、或关到一半）。那一条是上一次
    # 的请求，这次不能再执行一遍 —— 否则新窗口一开就被上一轮的「关闭」关掉。
    stale = _read_control()
    seen: str | None = stale[1] if stale else None
    deadline = time.monotonic() + _WINDOW_GRACE
    strict_until = time.monotonic() + _WINDOW_GRACE / 2
    hwnd = 0
    next_lookup = 0.0
    # 1.25.52：窗口刚出现的头一两秒里，浏览器可能还在恢复上一次的最小化状态。
    # 找到窗口后过 2 秒再看一眼：如果它还是最小化的，就把它恢复出来。
    startup_check = 0.0
    while True:
        # 定位窗口很贵：_app_window 要起一个 PowerShell 进程去查进程号。句柄缓存住，
        # 只在它失效时才重找，而且重找之间留出间隔 —— 否则每轮 100ms 都去起一次
        # PowerShell，点「最小化」得先等它排完队。
        if not _window_alive(user32, hwnd) and time.monotonic() >= next_lookup:
            hwnd = _app_window(user32, profile,
                               require_title=time.monotonic() < strict_until)
            # 1.25.52：上一次运行留下的窗口可能还是最小化状态（浏览器复用了
            # 旧窗口）。启动后要把它恢复出来，否则老师点桌面图标只看到任务栏。
            if hwnd:
                try:
                    if getattr(user32, "IsIconic", None) and user32.IsIconic(hwnd):
                        user32.ShowWindow(hwnd, _SW_RESTORE)
                        user32.SetForegroundWindow(hwnd)
                except Exception:  # noqa: BLE001 - 恢复失败不挡后续控制
                    pass
                if not startup_check:
                    startup_check = time.monotonic() + 2.0
            next_lookup = time.monotonic() + (
                _WINDOW_LOOKUP_BACKOFF if not hwnd else _WINDOW_LOOKUP_INTERVAL
            )
        if not hwnd:
            if time.monotonic() > deadline:
                return
            time.sleep(interval)
            continue
        if startup_check and time.monotonic() >= startup_check:
            startup_check = 0.0
            try:
                if getattr(user32, "IsIconic", None) and user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, _SW_RESTORE)
                    user32.SetForegroundWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
        request = _read_control()
        # 1.26.0：标题栏够不着的时候，页面右上角那份按钮才该露出来。
        write_window_state(user32, hwnd)
        if request is not None and request[1] != seen:
            action, seen = request
            # 1.25.51：动作落到「用户正在点的那个窗口」上。句柄是启动时找的；
            # 多开过窗口时它可能是另一个缩成一条的旧窗口 —— 那就点了没反应。
            target = hwnd
            try:
                import ctypes

                foreground = int(user32.GetForegroundWindow())
                if foreground and foreground != hwnd:
                    fg_pid = ctypes.c_ulong()
                    win_pid = ctypes.c_ulong()
                    user32.GetWindowThreadProcessId(ctypes.c_void_p(foreground), ctypes.byref(fg_pid))
                    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(win_pid))
                    if fg_pid.value and fg_pid.value == win_pid.value:
                        target = foreground
            except Exception:  # noqa: BLE001 - 拿不到前台窗口就按原句柄
                target = hwnd
            if action == "minimize":
                user32.ShowWindow(target, _SW_MINIMIZE)
            elif action == "maximize":
                # 1.26.0：页面右上角的三个按钮要能真的用。窗口已经最大化就还原，
                # 否则最大化 —— 与标题栏上的 □ 同一个意思。
                try:
                    zoomed = bool(getattr(user32, "IsZoomed", lambda _h: False)(target))
                except Exception:  # noqa: BLE001
                    zoomed = False
                user32.ShowWindow(target, _SW_RESTORE if zoomed else _SW_MAXIMIZE)
            elif action == "close":
                user32.PostMessageW(target, _WM_CLOSE, 0, 0)
            else:
                seen = None          # 不认识的动作不执行，也不当成已处理
        time.sleep(interval)


def app_window_url(url: str) -> str:
    """题有据自己窗口要打开的地址：带一个"我在应用窗口里"的标记。

    页面据此决定要不要在右上角自绘窗口按钮：应用自己的窗口有系统标题栏
    （自带最小化/最大化/关闭），再画一套就是两套并排；别的标签页没有标题栏，
    那三个按钮是唯一的出口，就该画出来。
    """
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}qbhost=app"


def open_window(url: str) -> subprocess.Popen | None:
    browsers = browser_candidates()
    if not browsers:
        webbrowser.open_new_tab(url)
        return None
    profile = window_profile()
    web = subprocess.Popen(window_command(browsers[0], url, profile), creationflags=CREATE_NO_WINDOW)
    threading.Thread(target=watch_window, args=(profile,), daemon=True).start()
    return web


def focus_existing_window() -> bool:
    """应用已经在运行时，把现有窗口切到前面，而不是再开一个。

    1.25.42：再点一次桌面图标，原来会「只打开窗口」—— 于是桌面上叠出好几个
    题有据窗口，每个都连同一个服务。现在先找现有窗口，找到就还原并置前。
    """
    user32 = _user32()
    if user32 is None:
        return False
    hwnd = _app_window(user32, window_profile())
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, _SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def _profile_still_open(profile: Path) -> bool:
    """应用窗口被交给已在运行的浏览器进程时，按命令行里的配置目录找它是否还开着。"""
    marker = str(profile).replace("'", "''")
    script = ("$n = @(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
              f"Where-Object {{ $_.CommandLine -like '*{marker}*' }}).Count; Write-Output $n")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        return int((result.stdout or "0").strip() or 0) > 0
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def _clear_control() -> None:
    """退出时把控制文件删掉。

    留着也无害（每次请求都带一次性编号，启动时会跳过上一次留下的那条），但一个
    没人再听的请求摊在那里，下一次启动还得先把它读一遍才知道该跳过。
    """
    with contextlib.suppress(OSError):
        control_file().unlink(missing_ok=True)


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
            hwnd = _app_window(user32, profile,
                               require_title=time.monotonic() < strict_until)
            next_lookup = time.monotonic() + _WINDOW_LOOKUP_BACKOFF
        if hwnd:
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
    print(f"网页服务在窗口仍开着时退出（退出码 {code}）；"
          f"如果是 0 多半是被人结束的，不是自己崩的。"
          f"时间 {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)
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
            hwnd = _app_window(user32, window_profile(),
                               require_title=time.monotonic() < strict_until)
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


class Splash:
    """启动时的小窗口：图标、名称、当前步骤和进度条。tkinter 不可用时什么也不显示。"""

    def __init__(self) -> None:
        self.events: queue.Queue = queue.Queue()
        self.root = None
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
        width, height = 420, 230
        x = (root.winfo_screenwidth() - width) // 2
        y = (root.winfo_screenheight() - height) // 3
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.attributes("-topmost", True)
        _set_icon(root)
        frame = tk.Frame(root, bg="#ffffff", highlightthickness=1, highlightbackground="#dfe4dd")
        frame.pack(fill="both", expand=True)
        self.image = None
        with contextlib.suppress(Exception):
            if ICON_PNG.is_file():
                self.image = tk.PhotoImage(file=str(ICON_PNG)).subsample(4, 4)
        if self.image is not None:
            tk.Label(frame, image=self.image, bg="#ffffff").pack(pady=(26, 8))
        else:
            tk.Label(frame, text="题", font=("SimSun", 30, "bold"), fg="#fff4dc", bg="#1f6b5f", width=2).pack(pady=(26, 8))
        tk.Label(frame, text=APP_TITLE, font=("Microsoft YaHei UI", 14, "bold"), fg="#1c2a28", bg="#ffffff").pack()
        tk.Label(frame, text=APP_SUBTITLE, font=("Microsoft YaHei UI", 9), fg="#72807b", bg="#ffffff").pack(pady=(2, 0))
        self.status = tk.StringVar(value="正在启动…")
        tk.Label(frame, textvariable=self.status, font=("Microsoft YaHei UI", 9), fg="#72807b", bg="#ffffff").pack(pady=(6, 10))
        style = ttk.Style(root)
        with contextlib.suppress(Exception):
            style.theme_use("clam")
        style.configure("QB.Horizontal.TProgressbar", troughcolor="#eaede7", background="#1f6b5f", bordercolor="#eaede7",
                        lightcolor="#1f6b5f", darkcolor="#1f6b5f", thickness=4)
        bar = ttk.Progressbar(frame, mode="indeterminate", length=260, style="QB.Horizontal.TProgressbar")
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

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QuestionBank.CardReview")


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


def _main(arguments: list[str] | None = None) -> int:
    _set_app_id()
    arguments = [] if arguments is None else list(arguments)
    if arguments == ["--configure"]:
        saved = _configure_credentials(first_run=False)
        if saved:
            running = launcher._running_instance()
            suffix = "\n\n题库当前正在运行：新配置从下一份任务或下一次重读开始时生效。" if running else ""
            _message(f"API 密钥与模型选择已保存；密钥已加密。{suffix}")
        return 0
    if arguments:
        _message("无法识别的启动参数。", error=True)
        return 2
    if not launcher.FROZEN and sys.version_info[:2] != (3, 12):
        venv_window_python = launcher.PYTHON.with_name("pythonw.exe")
        if venv_window_python.is_file():
            # 用本目录自己的 Python 3.12 环境重新打开窗口版。
            subprocess.Popen([str(venv_window_python), str(ROOT / "app_launcher.pyw")], cwd=ROOT, creationflags=CREATE_NO_WINDOW)
            return 0
        return _hand_off_to_console(f"需要 Python 3.12（当前是 {sys.version_info.major}.{sys.version_info.minor}）")
    existing = launcher._running_instance()
    if existing:
        # 1.25.42：已经在运行时先切到现有窗口。原来这里直接「只打开窗口」，
        # 点几次图标就叠出几个题有据窗口 —— 老师说的「打开好几个题目界面」。
        if focus_existing_window():
            print(f"已在运行：{existing}，已切到现有窗口", flush=True)
            return 0
        print(f"已在运行：{existing}，只打开窗口", flush=True)
        window = open_window(existing)
        # 窗口上的「最小化 / 关闭」是网页自己画的按钮，由 watch_window 这条守护
        # 线程照着控制文件去做。就在这里 return 的话，进程一退出，线程跟着被杀，
        # 两个按钮就再也点不动了 —— 窗口还开着，按钮却是死的。
        # 这个进程没有自己启动服务，不需要收尾，陪着窗口开下去就行。
        wait_for_reattached_window(window)
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
            base_env = launcher._child_environment({
                key: value for key, value in os.environ.items()
                if key.upper() not in launcher.SECRET_NAMES
                and key.upper() not in launcher.MODEL_ENVIRONMENT_KEYS
                and key.upper() not in launcher.CREDENTIAL_STATUS_NAMES
            })
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
            return ("ready", url)

        kind, value = splash.run(start)
        if kind == "configure":
            configure_reason = value
            return 0
        url = value
        print(f"服务就绪：{url}", flush=True)
        window = open_window(app_window_url(url))
        how = wait_for_window(window, processes[-1])
        if how == "service-died":
            print("窗口还开着但服务没了，下面这次退出会把整个软件一起关掉。", flush=True)
        print("窗口已关闭，停止服务", flush=True)
        return 0
    finally:
        launcher._clear_instance()
        _clear_control()
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
