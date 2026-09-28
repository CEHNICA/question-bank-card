"""启动题有据：准备本地环境，读取已加密保存的凭据，启动网页和后台工作者。"""

from __future__ import annotations

import ctypes
import contextlib
from concurrent.futures import ThreadPoolExecutor
import getpass
import hashlib
import json
import os
import re
import shutil
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

from credential_store import (
    DEFAULT_MODEL_PREFERENCES,
    MODEL_ENVIRONMENT_KEYS,
    CredentialStoreError,
    credential_path,
    credential_pool,
    load_credentials,
    load_model_preferences,
    model_preference_environment,
    save_credentials,
)


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
POOL_ENVIRONMENT_NAMES = {
    "mineru": ("MINERU_TOKEN", "MINERU_TOKENS_JSON", "QB_MINERU_CONFIGURED", "QB_MINERU_POOL_SIZE"),
    "minimax": ("MINIMAX_API_KEY", "MINIMAX_API_KEYS_JSON", "QB_MINIMAX_CONFIGURED", "QB_MINIMAX_POOL_SIZE"),
    "siliconflow": (
        "SILICONFLOW_API_KEY", "SILICONFLOW_API_KEYS_JSON",
        "QB_SILICONFLOW_CONFIGURED", "QB_SILICONFLOW_POOL_SIZE",
    ),
}
SECRET_NAMES = {
    name
    for legacy_name, pool_name, _configured_name, _size_name in POOL_ENVIRONMENT_NAMES.values()
    for name in (legacy_name, pool_name)
}
CREDENTIAL_STATUS_NAMES = {
    name
    for _legacy_name, _pool_name, configured_name, size_name in POOL_ENVIRONMENT_NAMES.values()
    for name in (configured_name, size_name)
}
# Mirrors backend/core/account_pool.DEFAULT_ACCOUNT_CONCURRENCY: simultaneous
# requests one account may carry.  QB_<PROVIDER>_ACCOUNT_CONCURRENCY overrides.
ACCOUNT_CONCURRENCY_DEFAULTS = {"minimax": 4, "siliconflow": 2}
MAX_PARALLEL_CARDS = 16
MODEL_PROVIDER_BY_ENGINE = {
    "minimax_m3": "minimax",
    "siliconflow_qwen3": "siliconflow",
}
STAGED_DELETE_NAME = re.compile(
    r"^\.deleting-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-[0-9a-f]{32}$",
    re.IGNORECASE,
)
STAGED_UPLOAD_NAME = re.compile(
    r"^\.uploading-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-[0-9a-f]{32}$",
    re.IGNORECASE,
)
STAGED_SPLIT_NAME = re.compile(
    r"^\.splitting-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-[0-9a-f]{32}$",
    re.IGNORECASE,
)


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
        # Both child processes know only where the DPAPI file lives.  The web
        # process receives no secret environment variables; the worker reloads
        # the encrypted file only between tasks.
        "QB_CREDENTIAL_FILE": str(credential_path().resolve()),
        "QB_CREDENTIAL_HOT_RELOAD": "1",
    })
    return environment


def _worker_credential_environment(
    base: dict[str, str], pools: dict[str, list[str]],
) -> dict[str, str]:
    """Add compact account pools only to the worker environment.

    Singular variables intentionally remain as the first account for older
    backend code. Pool JSON never enters command arguments or the web process.
    """

    environment = dict(base)
    for service, (legacy_name, pool_name, _configured_name, _size_name) in POOL_ENVIRONMENT_NAMES.items():
        accounts = pools.get(service, [])
        if accounts:
            environment[legacy_name] = accounts[0]
            environment[pool_name] = json.dumps(accounts, ensure_ascii=True, separators=(",", ":"))
    return environment


def _credential_status_environment(pools: dict[str, list[str]]) -> dict[str, str]:
    """Return non-secret availability/count fields safe for the web process."""

    result: dict[str, str] = {}
    for service, (_legacy_name, _pool_name, configured_name, size_name) in POOL_ENVIRONMENT_NAMES.items():
        count = len(pools.get(service, []))
        result[configured_name] = "1" if count else "0"
        result[size_name] = str(count)
    return result


def _parallel_environment(
    source: dict[str, str],
    pools: dict[str, list[str]],
    preferences: dict[str, str],
) -> dict[str, str]:
    """Choose reader concurrency from the accounts that can actually serve.

    A user-supplied value is honoured only when it is an integer from 1 to 16
    (and is then marked explicit so the worker never raises it).  Otherwise
    concurrency is the sum, over the distinct providers serving the selected
    primary/checker/arbiter roles, of accounts x per-account concurrency.  The
    worker and web process receive only the resulting non-secret number.
    """

    raw = str(source.get("QB_PARALLEL", "")).strip()
    try:
        explicit = int(raw)
    except (TypeError, ValueError):
        explicit = 0
    if 1 <= explicit <= MAX_PARALLEL_CARDS:
        return {"QB_PARALLEL": str(explicit), "QB_PARALLEL_EXPLICIT": "1"}

    primary = MODEL_PROVIDER_BY_ENGINE.get(
        preferences.get("primary_engine", DEFAULT_MODEL_PREFERENCES["primary_engine"]),
        "minimax",
    )
    providers = {primary} if pools.get(primary) else set()

    checker_choice = preferences.get(
        "checker_engine", DEFAULT_MODEL_PREFERENCES["checker_engine"],
    )
    if checker_choice == "auto":
        checker = next(
            (
                provider for provider in ("minimax", "siliconflow")
                if provider != primary and pools.get(provider)
            ),
            primary,
        )
    else:
        checker = MODEL_PROVIDER_BY_ENGINE.get(checker_choice, primary)
    if pools.get(checker):
        providers.add(checker)

    arbiter_choice = preferences.get(
        "arbiter_engine", DEFAULT_MODEL_PREFERENCES["arbiter_engine"],
    )
    if arbiter_choice == "checker":
        arbiter = checker
    elif arbiter_choice == "primary":
        arbiter = primary
    else:
        arbiter = MODEL_PROVIDER_BY_ENGINE.get(arbiter_choice, primary)
    if pools.get(arbiter):
        providers.add(arbiter)

    def per_account(provider: str) -> int:
        default = ACCOUNT_CONCURRENCY_DEFAULTS.get(provider, 1)
        try:
            value = int(str(source.get(f"QB_{provider.upper()}_ACCOUNT_CONCURRENCY", "")).strip() or default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(8, value))

    automatic = sum(len(pools.get(provider, [])) * per_account(provider) for provider in providers)
    return {"QB_PARALLEL": str(max(1, min(MAX_PARALLEL_CARDS, automatic))), "QB_PARALLEL_EXPLICIT": "0"}


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
    _reconcile_staged_storage()
    _relocate_local_paths()


def _reconcile_staged_storage() -> tuple[int, int]:
    """按数据库状态恢复或清理上传、拆分、删除流程留下的暂存目录。"""
    if not DATA_ROOT.is_dir() or not DATABASE.is_file():
        return 0, 0
    try:
        with contextlib.closing(sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True)) as connection:
            paper_ids = {str(uuid.UUID(str(row[0]))) for row in connection.execute("SELECT id FROM core_paper")}
    except (sqlite3.Error, ValueError):
        # 数据库状态不明确时不碰任何暂存文件，避免把仍有记录的原卷误删。
        return 0, 0

    restored = removed = 0
    staged_paths = [
        *DATA_ROOT.glob(".deleting-*"),
        *DATA_ROOT.glob(".uploading-*"),
        *DATA_ROOT.glob(".splitting-*"),
    ]
    for staged in sorted(staged_paths):
        match = (
            STAGED_DELETE_NAME.fullmatch(staged.name)
            or STAGED_UPLOAD_NAME.fullmatch(staged.name)
            or STAGED_SPLIT_NAME.fullmatch(staged.name)
        )
        if match is None or staged.is_symlink() or not staged.is_dir():
            continue
        paper_id = str(uuid.UUID(match.group(1)))
        canonical = DATA_ROOT / paper_id
        if paper_id in paper_ids:
            if canonical.exists():
                continue
            try:
                staged.replace(canonical)
            except OSError:
                continue
            restored += 1
        else:
            try:
                shutil.rmtree(staged)
            except OSError:
                continue
            removed += 1
    if restored or removed:
        print(f"恢复未完成的任务文件操作：还原 {restored} 项，清理 {removed} 项", flush=True)
    return restored, removed


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
    """已经有一个题有据在运行就直接打开它，避免两套服务抢同一个数据库。"""
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


def _siliconflow_keys() -> list[str]:
    """Optional reader account pool; never prompted for by the console launcher."""
    try:
        saved = credential_pool(load_credentials(), "siliconflow")
    except CredentialStoreError:
        saved = []
    if saved:
        return saved
    environment_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    return [environment_key] if environment_key else []


def _siliconflow_key() -> str:
    """Legacy singular accessor retained for callers outside this package."""
    keys = _siliconflow_keys()
    return keys[0] if keys else ""


def _model_preferences() -> dict[str, str]:
    """Load safe non-secret role choices, falling back for legacy installs."""
    try:
        return load_model_preferences()
    except CredentialStoreError as exc:
        print(exc)
        return dict(DEFAULT_MODEL_PREFERENCES)


def _resolve_credentials() -> tuple[str, str]:
    """Resolve each service separately; saved MiniMax choices override its environment."""
    try:
        saved = load_credentials()
    except CredentialStoreError as exc:
        print(exc)
        saved = {}
    entered: dict[str, object] = {}
    invalid_saved_token = False
    saved_mineru = credential_pool(saved, "mineru")

    environment_token = _strip_bearer(os.environ.get("MINERU_TOKEN", ""))
    if environment_token:
        token, token_source = environment_token, "environment"
        print("已读取环境变量中的 MinerU Token。")
    elif saved_mineru:
        token, token_source = saved_mineru[0], "saved"
        print("已读取当前用户保存的 MinerU 配置。")
    else:
        token = _strip_bearer(getpass.getpass("MinerU API Token（留空仅查看已有资料）："))
        token_source = "manual"
        entered["mineru_token"] = token
        entered["mineru_tokens"] = [token] if token else []

    while token:
        validity = _mineru_token_validity(token)
        if validity is False:
            if token_source == "environment" and saved_mineru:
                print("环境变量中的 MinerU Token 未通过官网验证，正在尝试当前用户保存的值。")
                token = saved_mineru[0]
                token_source = "saved"
                continue
            if token_source == "saved":
                # 只移除明确失效的这一项，继续尝试池中其他账号。
                # 绝不因首项过期而清空整个已保存账号池。
                saved_mineru = [candidate for candidate in saved_mineru if candidate != token]
                if saved_mineru:
                    saved["mineru_token"] = saved_mineru[0]
                    saved["mineru_tokens"] = saved_mineru
                else:
                    saved.pop("mineru_token", None)
                    saved["mineru_tokens"] = []
                invalid_saved_token = True
                if saved_mineru:
                    print("已跳过一个未通过官网验证的 MinerU 账号，正在尝试池中下一个。")
                    token = saved_mineru[0]
                    continue
            print("MinerU Token 未通过官网验证，请隐藏式重新输入。")
            token = _strip_bearer(getpass.getpass("重新输入 MinerU API Token（留空仅查看）："))
            token_source = "manual"
            entered["mineru_token"] = token
            entered["mineru_tokens"] = [token] if token else []
            continue
        if validity is None:
            print("暂时无法预检 MinerU Token；上传时会显示上游状态码和错误码。")
        else:
            print("MinerU Token 已通过官网验证。")
        break
    environment_key = os.environ.get("MINIMAX_API_KEY", "").strip().replace("\\_", "_")
    if "minimax_key" in saved:
        minimax_key = str(saved["minimax_key"]).strip().replace("\\_", "_")
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
            entered["minimax_keys"] = [minimax_key] if minimax_key else []
    else:
        minimax_key = getpass.getpass("MiniMax API Key（留空则跳过）：").strip().replace("\\_", "_")
        entered["minimax_key"] = minimax_key
        entered["minimax_keys"] = [minimax_key] if minimax_key else []

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


def _resolve_credential_pools() -> tuple[list[str], list[str]]:
    """Run the legacy interactive flow, then expand its selected first accounts.

    The existing prompts remain compatible for users with one account. Pools
    are normally edited in the native configuration dialog.
    """

    token, minimax_key = _resolve_credentials()
    try:
        saved = load_credentials()
    except CredentialStoreError:
        saved = {}

    environment_token = _strip_bearer(os.environ.get("MINERU_TOKEN", ""))
    saved_mineru = credential_pool(saved, "mineru")
    if token and environment_token and token == environment_token:
        mineru_tokens = [token]
    elif token and token in saved_mineru:
        mineru_tokens = [token, *(item for item in saved_mineru if item != token)]
    else:
        mineru_tokens = [token] if token else []

    # The first account was already checked by _resolve_credentials. Validate
    # every additional account without ever printing or returning its value.
    checked_mineru = mineru_tokens[:1]
    additional = mineru_tokens[1:]
    if additional:
        with ThreadPoolExecutor(max_workers=min(8, len(additional))) as executor:
            additional_validity = list(executor.map(_mineru_token_validity, additional))
    else:
        additional_validity = []
    for index, (candidate, validity) in enumerate(zip(additional, additional_validity), start=2):
        if validity is False:
            print(f"已跳过未通过官网验证的第 {index} 个 MinerU 账号。")
            continue
        if validity is None:
            print(f"暂时无法预检第 {index} 个 MinerU 账号；实际上传时仍会由服务端校验。")
        checked_mineru.append(candidate)

    saved_minimax = credential_pool(saved, "minimax")
    if minimax_key and minimax_key in saved_minimax:
        minimax_keys = [minimax_key, *(item for item in saved_minimax if item != minimax_key)]
    else:
        minimax_keys = [minimax_key] if minimax_key else []
    return checked_mineru, minimax_keys


def _saved_credential_pools() -> dict[str, list[str]]:
    """Load desktop pools without prompting so the in-app settings can open."""

    try:
        saved = load_credentials()
    except CredentialStoreError as exc:
        # Safe, value-free message from credential_store.  A corrupt file is
        # repaired through the explicitly retained recovery tool.
        print(exc)
        saved = {}
    return {service: credential_pool(saved, service) for service in POOL_ENVIRONMENT_NAMES}


def main() -> int:
    if os.name != "nt":
        print("此启动器仅支持 Windows。")
        return 1
    if not FROZEN and sys.version_info[:2] != (3, 12):
        print(f"需要 Python 3.12；当前是 {sys.version_info.major}.{sys.version_info.minor}。请安装 Python 3.12 后重试。")
        return 1
    existing = _running_instance()
    if existing:
        print(f"题有据已经在运行：{existing}（已为你打开）。")
        webbrowser.open_new_tab(existing)
        return 0
    instance_mutex = InstanceMutex()
    if not instance_mutex.acquired:
        print("题有据正在另一个窗口启动或运行；为保护数据库，本次不会重复启动。")
        return 0
    job = None
    processes: list[subprocess.Popen] = []
    logs: list[object] = []
    try:
        _prepare()
        print("\nAPI 凭据可在题库的“设置 → API 与模型”中配置。")
        credential_pools = _saved_credential_pools()
        preferences = _model_preferences()
        model_env = model_preference_environment(preferences)
        base_env = _child_environment({
            key: value for key, value in os.environ.items()
            if key.upper() not in SECRET_NAMES
            and key.upper() not in MODEL_ENVIRONMENT_KEYS
            and key.upper() not in CREDENTIAL_STATUS_NAMES
        })
        base_env.update(_parallel_environment(base_env, credential_pools, preferences))
        # 模型角色和型号不是秘密，网页可用于状态展示；真正的密钥只交给后台工作者。
        web_env = dict(base_env)
        web_env.update(_credential_status_environment(credential_pools))
        web_env.update(model_env)
        worker_env = _worker_credential_environment(base_env, credential_pools)
        worker_env.update(model_env)
        del model_env
        primary_available = (
            bool(credential_pools["siliconflow"])
            if preferences["primary_engine"] == "siliconflow_qwen3"
            else bool(credential_pools["minimax"])
        )
        if not primary_available:
            print("所选主读模型没有可用的 API Key：只能查看已有题卡，不能读新题。")
        elif not credential_pools["mineru"]:
            print("未配置 MinerU Token：不能上传新卷；已有试卷、从 M3 导入、单题重读照常可用。")
        effective_checker = preferences["checker_engine"]
        if effective_checker == "auto":
            if preferences["primary_engine"] == "minimax_m3" and credential_pools["siliconflow"]:
                effective_checker = "siliconflow_qwen3"
            elif preferences["primary_engine"] == "siliconflow_qwen3" and credential_pools["minimax"]:
                effective_checker = "minimax_m3"
            else:
                effective_checker = preferences["primary_engine"]
        engine_names = {
            "minimax_m3": "MiniMax-M3",
            "siliconflow_qwen3": "硅基流动 Qwen3-VL",
        }
        arbiter_names = {
            "primary": "跟随主读",
            "checker": "跟随复核",
            **engine_names,
        }
        print(
            f"模型分工：主读 {engine_names[preferences['primary_engine']]}；"
            f"复核 {engine_names[effective_checker]}；"
            f"裁决 {arbiter_names[preferences['arbiter_engine']]}。"
        )
        del preferences
        del credential_pools
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
            print("注意：后台工作者没有启动成功（可能已有另一个题有据在运行），新卷暂时不会被处理。"
                  "详情见 backend/runtime/worker.log")
        web, log = _start(["runserver", f"127.0.0.1:{port}", "--noreload"], web_env, "web.log")
        processes.append(web)
        logs.append(log)
        job.add(web)
        _health(url, web)
        _write_instance(port)
        print(f"\n题有据逐题核对页已就绪：{url}")
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
