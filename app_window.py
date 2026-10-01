"""窗口版启动器：像普通软件一样打开题库。

双击桌面上的“题有据”图标（或 app_launcher.pyw）后：
1. 不弹黑色命令行窗口，先显示一个小的启动画面；
2. 在后台启动网页服务和工作者（沿用 start_question_bank.py 的全部逻辑与安全约束）；
3. 用 Edge（没有 Edge 时用 Chrome）的“应用窗口”模式打开题库：没有地址栏和标签页，
   有自己的任务栏图标；
4. 关掉这个窗口，后台服务随之停止。

第一次安装组件时会交给原来的命令行启动器；API 凭据则在软件
的“设置 → 常用 → 填写或更换密钥”中录入。独立配置窗口仅作为凭据文件损坏时的故障恢复入口。
"""

from __future__ import annotations

import contextlib
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import start_question_bank as launcher
from credential_dialog import show_credential_dialog
from credential_store import CredentialStoreError, credential_pool, load_credentials

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
    model_accounts = credential_pool(
        saved, "siliconflow" if primary_name == "siliconflow_qwen3" else "minimax",
    )
    # 只有环境变量、没有保存时仍转交命令行，让人明确选择本次使用方式。
    return has_token and bool(model_accounts)


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
    return profile


def window_command(browser: Path, url: str, profile: Path) -> list[str]:
    return [
        str(browser), f"--app={url}", f"--user-data-dir={profile}",
        "--no-first-run", "--no-default-browser-check", "--window-size=1440,920",
    ]


def open_window(url: str) -> subprocess.Popen | None:
    browsers = browser_candidates()
    if not browsers:
        webbrowser.open_new_tab(url)
        return None
    return subprocess.Popen(window_command(browsers[0], url, window_profile()), creationflags=CREATE_NO_WINDOW)


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


def wait_for_window(window: subprocess.Popen | None, web: subprocess.Popen) -> None:
    """等到用户关掉题库窗口（或网页服务意外退出）。"""
    started = time.monotonic()
    if window is not None:
        while window.poll() is None:
            if web.poll() is not None:
                return
            time.sleep(0.5)
        if time.monotonic() - started > 5:
            return
        # 浏览器把窗口交给了已有进程就立刻返回：改为按配置目录轮询。
        profile = window_profile()
        time.sleep(2)
        while web.poll() is None and _profile_still_open(profile):
            time.sleep(4)
        return
    _control_window(web)


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
        print(f"已在运行：{existing}，只打开窗口", flush=True)
        open_window(existing)
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
            web_env.update(launcher._credential_status_environment(credential_pools))
            web_env.update(model_env)
            worker_env = launcher._worker_credential_environment(base_env, credential_pools)
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
        window = open_window(url)
        wait_for_window(window, processes[-1])
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
