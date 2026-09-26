"""双击启动题卡版：准备本地环境，读取已加密保存的凭据，启动网页和后台工作者。"""

from __future__ import annotations

import ctypes
import contextlib
import getpass
import hashlib
import json
import os
import sqlite3
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import webbrowser
from pathlib import Path

from credential_store import CredentialStoreError, load_credentials, save_credentials


FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
PROGRAM_ROOT = Path(sys.executable).resolve().parent if FROZEN else RESOURCE_ROOT
ROOT = RESOURCE_ROOT
BACKEND = RESOURCE_ROOT / "backend"
FRONTEND = RESOURCE_ROOT / "frontend"
_local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
USER_ROOT = Path(os.environ.get("QB_USER_ROOT", _local_app_data / "QuestionBankCard" if FROZEN else BACKEND)).resolve()
DATABASE = USER_ROOT / "db.sqlite3"
DATA_ROOT = USER_ROOT / "data"
VENV = BACKEND / ".venv"
PYTHON = Path(sys.executable) if FROZEN else VENV / "Scripts" / "python.exe"
REQUIREMENTS = BACKEND / "requirements.txt"
LOCK_REQUIREMENTS = BACKEND / "requirements.lock.txt"
INSTALL_MARKER = VENV / ".card-requirements.sha256"
RUNTIME = USER_ROOT / "runtime"
INSTANCE_FILE = RUNTIME / "instance.json"
MIGRATION_MARKER = RUNTIME / ".migration-schema.sha256"
BACKUPS = USER_ROOT / "backups"
PREFERRED_PORT = 8768
SECRET_NAMES = {"MINERU_TOKEN", "MINIMAX_API_KEY", "SILICONFLOW_API_KEY"}


def _child_environment(
    source: dict[str, str] | None = None, *, keep_secrets: bool = False
) -> dict[str, str]:
    """Build a deterministic UTF-8 child environment without leaking API keys by default."""
    environment = dict(os.environ if source is None else source)
    if not keep_secrets:
        environment = {key: value for key, value in environment.items() if key.upper() not in SECRET_NAMES}
    environment.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "QB_DATABASE": str(DATABASE),
        "QB_DATA_ROOT": str(DATA_ROOT),
        "QB_FRONTEND_ROOT": str(FRONTEND),
    })
    return environment


def _service_command(role: str, *extra: str) -> list[str]:
    """Return a Django command for source mode or the self-contained desktop build."""
    if FROZEN:
        return [str(sys.executable), "--internal-role", role, *extra]
    return [str(PYTHON), "manage.py", role, *extra]


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class ChildJob:
    """Windows kills all assigned children if this launcher closes unexpectedly."""

    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel.CreateJobObjectW.restype = ctypes.c_void_p
        kernel.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel.SetInformationJobObject.restype = ctypes.c_int
        kernel.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel.AssignProcessToJobObject.restype = ctypes.c_int
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def add(self, process: subprocess.Popen) -> None:
        if not self.kernel.AssignProcessToJobObject(self.handle, process._handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class InstanceMutex:
    """Prevent two launchers for this exact data directory from starting concurrently."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        kernel.CreateMutexW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_int
        digest = hashlib.sha256(str(USER_ROOT).lower().encode("utf-8")).hexdigest()[:20]
        ctypes.set_last_error(0)
        handle = kernel.CreateMutexW(None, False, f"Local\\QuestionBankCard-{digest}")
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.kernel = kernel
        self.handle = handle
        self.acquired = ctypes.get_last_error() != self.ERROR_ALREADY_EXISTS
        if not self.acquired:
            self.close()

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _run_step(args: list[str], label: str) -> None:
    print(label, flush=True)
    setup_env = _child_environment()
    if FROZEN:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        setup_env["QB_INTERNAL_LOG"] = str(RUNTIME / "setup.log")
    result = subprocess.run(
        args,
        cwd=PROGRAM_ROOT if FROZEN else BACKEND,
        env=setup_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(f"{label}失败：\n{result.stdout[-1800:]}")


def _prepare() -> None:
    USER_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not FROZEN:
        if not PYTHON.is_file():
            _run_step([sys.executable, "-m", "venv", str(VENV)], "首次建立运行环境")
        install_file = LOCK_REQUIREMENTS if LOCK_REQUIREMENTS.is_file() else REQUIREMENTS
        current_hash = hashlib.sha256(install_file.read_bytes()).hexdigest()
        previous_hash = INSTALL_MARKER.read_text(encoding="ascii").strip() if INSTALL_MARKER.is_file() else ""
        if current_hash != previous_hash:
            _run_step([str(PYTHON), "-m", "pip", "install", "-r", str(install_file)], "安装已验证版本的组件")
            INSTALL_MARKER.write_text(current_hash, encoding="ascii")
    migration_hash = _migration_fingerprint()
    previous_migration_hash = MIGRATION_MARKER.read_text(encoding="ascii").strip() if MIGRATION_MARKER.is_file() else ""
    if migration_hash != previous_migration_hash:
        _backup_database_before_migrate(migration_hash)
    _run_step(_service_command("migrate", "--noinput"), "检查本地数据")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    MIGRATION_MARKER.write_text(migration_hash, encoding="ascii")
    _relocate_local_paths()


def _migration_fingerprint() -> str:
    digest = hashlib.sha256()
    migration_root = BACKEND / "core" / "migrations"
    for path in sorted(migration_root.glob("[0-9]*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _backup_database_before_migrate(migration_hash: str) -> Path | None:
    """Create a verified SQLite backup before a new migration set is applied."""
    database = DATABASE
    if not database.is_file():
        return None
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = BACKUPS / f"pre-migrate-{stamp}-{migration_hash[:8]}.sqlite3"
    with contextlib.closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as source, \
            contextlib.closing(sqlite3.connect(target)) as output:
        source.backup(output)
    with contextlib.closing(sqlite3.connect(target)) as check:
        if check.execute("PRAGMA quick_check").fetchone() != ("ok",):
            target.unlink(missing_ok=True)
            raise RuntimeError("升级前数据库备份校验失败，已停止迁移")
    print(f"升级前数据库备份：{target}", flush=True)
    return target


def _relocate_local_paths() -> None:
    """整个目录被复制或移动后，把数据库里记录的文件路径改到新位置。"""
    database = DATABASE
    data_root = DATA_ROOT
    if not database.is_file() or not data_root.is_dir():
        return
    with contextlib.closing(sqlite3.connect(database)) as connection:
        for paper_id, *old_paths in connection.execute(
            "SELECT id, source_path, render_path, zip_path FROM core_paper"
        ).fetchall():
            directory = data_root / str(uuid.UUID(paper_id))
            updated = []
            for old in old_paths:
                candidate = directory / Path(old.replace("\\", "/")).name if old else None
                updated.append(str(candidate) if candidate and candidate.is_file() else old)
            if updated != old_paths:
                connection.execute(
                    "UPDATE core_paper SET source_path=?, render_path=?, zip_path=? WHERE id=?", (*updated, paper_id)
                )
        connection.commit()


def _running_instance() -> str | None:
    """已经有一个题卡版在运行就直接打开它，避免两套服务抢同一个数据库。"""
    urls: list[str] = []
    if INSTANCE_FILE.is_file():
        try:
            record = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
            port = record.get("port") if isinstance(record, dict) else None
            if isinstance(port, int) and 1 <= port <= 65535:
                urls.append(f"http://127.0.0.1:{port}")
        except (OSError, ValueError):
            pass
    preferred = f"http://127.0.0.1:{PREFERRED_PORT}"
    if preferred not in urls:
        urls.append(preferred)
    for url in urls:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1.5) as response:
                data = json.loads(response.read(2048))
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        if isinstance(data, dict) and data.get("app") == "question-bank-card":
            return url
    if INSTANCE_FILE.is_file():
        INSTANCE_FILE.unlink(missing_ok=True)
    return None


def _write_instance(port: int) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    temporary = INSTANCE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "port": port}, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, INSTANCE_FILE)


def _clear_instance() -> None:
    try:
        record = json.loads(INSTANCE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(record, dict) and record.get("pid") == os.getpid():
        INSTANCE_FILE.unlink(missing_ok=True)


def _available_port() -> int:
    for candidate in (PREFERRED_PORT, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return int(listener.getsockname()[1])
    raise RuntimeError("没有可用的本机端口")


def _start(args: list[str], environment: dict[str, str], log_name: str) -> tuple[subprocess.Popen, object]:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    log_path = RUNTIME / log_name
    log_stream = log_path.open("ab", buffering=0)
    child_env = _child_environment(environment, keep_secrets=True)
    if FROZEN:
        child_env["QB_INTERNAL_LOG"] = str(log_path)
    try:
        process = subprocess.Popen(
            _service_command(args[0], *args[1:]),
            cwd=PROGRAM_ROOT if FROZEN else BACKEND,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log_stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return process, log_stream
    except Exception:
        log_stream.close()
        raise


def _health(url: str, process: subprocess.Popen) -> None:
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("网页服务提前退出，请查看 backend/runtime/web.log")
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError("网页服务未能在 30 秒内就绪，请查看 backend/runtime/web.log")


def _mineru_token_validity(token: str) -> bool | None:
    """Check authorization without uploading a document or printing the response."""
    request = urllib.request.Request(
        "https://mineru.net/api/v4/quota",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                return None
            result = json.loads(response.read(16384))
    except urllib.error.HTTPError as exc:
        return False if exc.code in (401, 403) else None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
    if not isinstance(result, dict):
        return None
    if result.get("code") in ("A0202", "A0211"):
        return False
    return True if result.get("code") == 0 else None


def _strip_bearer(value: str) -> str:
    value = value.strip()
    return value[7:].strip() if value.lower().startswith("bearer ") else value


def _siliconflow_key() -> str:
    """Optional third reader for 双读核对; never prompted for at startup."""
    try:
        saved = load_credentials().get("siliconflow_key", "")
    except CredentialStoreError:
        saved = ""
    return (saved or os.environ.get("SILICONFLOW_API_KEY", "")).strip()


def _resolve_credentials() -> tuple[str, str]:
    """Resolve each service separately; saved MiniMax choices override its environment."""
    try:
        saved = load_credentials()
    except CredentialStoreError as exc:
        print(exc)
        saved = {}
    entered: dict[str, str] = {}
    invalid_saved_token = False

    environment_token = _strip_bearer(os.environ.get("MINERU_TOKEN", ""))
    if environment_token:
        token, token_source = environment_token, "environment"
        print("已读取环境变量中的 MinerU Token。")
    elif "mineru_token" in saved:
        token, token_source = _strip_bearer(saved["mineru_token"]), "saved"
        print("已读取当前用户保存的 MinerU 配置。")
    else:
        token = _strip_bearer(getpass.getpass("MinerU API Token（留空仅查看已有资料）："))
        token_source = "manual"
        entered["mineru_token"] = token

    while token:
        validity = _mineru_token_validity(token)
        if validity is False:
            if token_source == "environment" and saved.get("mineru_token"):
                print("环境变量中的 MinerU Token 未通过官网验证，正在尝试当前用户保存的值。")
                token = _strip_bearer(saved["mineru_token"])
                token_source = "saved"
                continue
            print("MinerU Token 未通过官网验证，请隐藏式重新输入。")
            if token_source == "saved":
                saved.pop("mineru_token", None)
                invalid_saved_token = True
            token = _strip_bearer(getpass.getpass("重新输入 MinerU API Token（留空仅查看）："))
            token_source = "manual"
            entered["mineru_token"] = token
            continue
        if validity is None:
            print("暂时无法预检 MinerU Token；上传时会显示上游状态码和错误码。")
        else:
            print("MinerU Token 已通过官网验证。")
        break
    environment_key = os.environ.get("MINIMAX_API_KEY", "").strip().replace("\\_", "_")
    if "minimax_key" in saved:
        minimax_key = saved["minimax_key"].strip().replace("\\_", "_")
        print("已读取当前用户保存的 MiniMax 配置。")
        if environment_key:
            print("已忽略 MINIMAX_API_KEY 环境变量；当前用户保存的 MiniMax 配置优先。")
    elif environment_key:
        print("检测到 MINIMAX_API_KEY 环境变量。为避免使用过期 Key，请明确选择本次使用方式。")
        choice = input("直接回车=隐藏输入新 Key；输入 2=仅本次使用环境变量；输入 3=本次跳过 MiniMax：").strip()
        if choice == "2":
            minimax_key = environment_key
            print("本次使用环境变量中的 MiniMax API Key。")
        elif choice == "3":
            minimax_key = ""
            print("本次跳过 MiniMax。")
        else:
            minimax_key = getpass.getpass("新的 MiniMax API Key（留空则跳过）：").strip().replace("\\_", "_")
            entered["minimax_key"] = minimax_key
    else:
        minimax_key = getpass.getpass("MiniMax API Key（留空则跳过）：").strip().replace("\\_", "_")
        entered["minimax_key"] = minimax_key

    if invalid_saved_token:
        try:
            save_credentials(saved)
        except CredentialStoreError as exc:
            print(exc)
    if entered:
        answer = input("是否记住本次输入？留空项也会记住为“跳过”（之后可双击“管理题库凭据.cmd”补录）；仅加密保存在当前 Windows 用户下（直接回车=是；输入“否”不保存）：").strip().lower()
        if answer in {"", "是", "y", "yes"}:
            try:
                save_credentials({**saved, **entered})
            except CredentialStoreError as exc:
                print(exc)
                print("本次仍可继续使用已输入的凭据，下次启动需要重新输入。")
            else:
                print("已加密保存。下次启动无需重复输入；可双击“管理题库凭据.cmd”更换或清除。")
    return token, minimax_key


def main() -> int:
    if os.name != "nt":
        print("此启动器仅支持 Windows。")
        return 1
    if not FROZEN and sys.version_info[:2] != (3, 12):
        print(f"需要 Python 3.12；当前是 {sys.version_info.major}.{sys.version_info.minor}。请安装 Python 3.12 后重试。")
        return 1
    existing = _running_instance()
    if existing:
        print(f"题卡版已经在运行：{existing}（已为你打开）。")
        webbrowser.open_new_tab(existing)
        return 0
    instance_mutex = InstanceMutex()
    if not instance_mutex.acquired:
        print("题卡版正在另一个窗口启动或运行；为保护数据库，本次不会重复启动。")
        return 0
    job = None
    processes: list[subprocess.Popen] = []
    logs: list[object] = []
    try:
        _prepare()
        print("\n题库凭据：与 M3 共用当前 Windows 用户加密保存的配置。")
        token, minimax_key = _resolve_credentials()
        siliconflow_key = _siliconflow_key()
        base_env = _child_environment({
            key: value for key, value in os.environ.items()
            if key.upper() not in SECRET_NAMES and not key.upper().endswith("_CONFIGURED")
        })
        # 网页进程只需要知道"配置了没有"，真正的密钥只交给后台工作者。
        web_env = dict(base_env)
        web_env.update({"QB_MINERU_CONFIGURED": "1" if token else "0",
                        "QB_MINIMAX_CONFIGURED": "1" if minimax_key else "0",
                        "QB_SILICONFLOW_CONFIGURED": "1" if siliconflow_key else "0"})
        worker_env = dict(base_env)
        if token:
            worker_env["MINERU_TOKEN"] = token
        if minimax_key:
            worker_env["MINIMAX_API_KEY"] = minimax_key
        if siliconflow_key:
            worker_env["SILICONFLOW_API_KEY"] = siliconflow_key
        del token, minimax_key
        if not worker_env.get("MINIMAX_API_KEY"):
            print("未配置 MiniMax：只能查看已有题卡，不能读新题。")
        elif not worker_env.get("MINERU_TOKEN"):
            print("未配置 MinerU Token：不能上传新卷；已有试卷、从 M3 导入、单题重读照常可用。")
        if siliconflow_key:
            print("第二位读者：硅基流动 Qwen3-VL（另一家模型，核对更独立）。")
        else:
            print("第二位读者：MiniMax 再独立读一遍。想换成另一家，可双击“管理题库凭据.cmd”选 3 添加硅基流动 Key。")
        del siliconflow_key
        port = _available_port()
        url = f"http://127.0.0.1:{port}"
        job = ChildJob()
        worker, log = _start(["run_worker"], worker_env, "worker.log")
        processes.append(worker)
        logs.append(log)
        job.add(worker)
        del worker_env
        time.sleep(1.5)
        if worker.poll() is not None:
            print("注意：后台工作者没有启动成功（可能已有另一个题卡版在运行），新卷暂时不会被处理。"
                  "详情见 backend/runtime/worker.log")
        web, log = _start(["runserver", f"127.0.0.1:{port}", "--noreload"], web_env, "web.log")
        processes.append(web)
        logs.append(log)
        job.add(web)
        _health(url, web)
        _write_instance(port)
        print(f"\n题卡终审页已就绪：{url}")
        print(f"正式题库：{url}/library")
        webbrowser.open_new_tab(url)
        print("关闭此窗口或按 Ctrl+C 即停止本次服务。")
        try:
            input("也可以按回车停止服务：")
        except EOFError:
            while web.poll() is None:
                time.sleep(1)
        return 0
    except KeyboardInterrupt:
        print("\n正在停止服务……")
        return 0
    except Exception as exc:
        print(f"\n启动失败：{exc}")
        return 1
    finally:
        _clear_instance()
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


if __name__ == "__main__":
    raise SystemExit(main())
