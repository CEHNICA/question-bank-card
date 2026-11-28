"""向官网 tiyouju.com 检查并安装新版本。

只访问 tiyouju.com。安装包要和官网给出的 SHA-256 对得上才运行。
题库文件只做一份拷贝备份，不打开、不读取里面的题目。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from django.conf import settings
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .version import APP_VERSION

OFFICIAL_HOSTS = frozenset({"tiyouju.com", "www.tiyouju.com"})
OFFICIAL_RELEASE_HOSTS = frozenset({"tiyouju.com", "www.tiyouju.com", "github.com"})
LATEST_URL = "https://tiyouju.com/api/latest-release"
INSTALLER_URL = "https://tiyouju.com/download/installer"
CHECKSUM_URL = "https://tiyouju.com/download/SHA256SUMS.txt"
MAX_INSTALLER_BYTES = 800 * 1024 * 1024
_DOWNLOAD_WORKERS = 6
_DOWNLOAD_CHUNK_BYTES = 2 * 1024 * 1024
_READ_BLOCK_BYTES = 64 * 1024
_CHUNK_MAX_RETRIES = 3
_INSTALL_LOCK = threading.Lock()
_PROGRESS_LOCK = threading.Lock()
_VERSION_PART = re.compile(r"^\d+$")
_CONTENT_RANGE_RE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$", re.IGNORECASE)
_ACTIVE_STATUSES = frozenset({"preparing", "downloading", "verifying", "backing_up", "launching"})


class _ProgressState(TypedDict):
    status: str
    stage_label: str
    version: str
    bytes_downloaded: int
    bytes_total: int
    speed_bps: float
    eta_seconds: int | None
    error: str
    started_at: float


_PROGRESS_STATE: _ProgressState = {
    "status": "idle",
    "stage_label": "",
    "version": "",
    "bytes_downloaded": 0,
    "bytes_total": 0,
    "speed_bps": 0.0,
    "eta_seconds": None,
    "error": "",
    "started_at": 0.0,
}
_SPEED_SAMPLES: deque[tuple[float, int]] = deque(maxlen=64)


class UpdateError(Exception):
    """更新没做成。消息可以给用户看，不含下载内容。"""


def _preference_path() -> Path:
    return Path(settings.DATA_ROOT).parent / "runtime" / "update-check.json"


def auto_check_enabled() -> bool:
    path = _preference_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return True
    if not isinstance(payload, dict) or "auto_check" not in payload:
        return True
    return bool(payload["auto_check"])


def set_auto_check(enabled: bool) -> None:
    path = _preference_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"auto_check": bool(enabled)}, ensure_ascii=False), encoding="utf-8")


def version_key(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in str(text or "").strip().lstrip("vV").split("."):
        if not _VERSION_PART.match(piece):
            break
        parts.append(int(piece))
    if not parts:
        raise UpdateError("版本号不认识。")
    return tuple(parts)


def _official_response(url: str, timeout: float, headers: dict[str, str] | None = None):
    req_headers = {"User-Agent": "tiyouju-update", "Accept": "*/*"}
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, headers=req_headers)
    response = urllib.request.urlopen(request, timeout=timeout)
    final = urllib.parse.urlparse(response.geturl())
    if final.scheme != "https" or (final.hostname or "").lower() not in OFFICIAL_HOSTS:
        response.close()
        raise UpdateError("更新地址不是 tiyouju.com。")
    return response


def _read_official(url: str, timeout: float, limit: int) -> bytes:
    with _official_response(url, timeout) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise UpdateError("官网返回的内容太长。")
    return body


def _safe_release_url(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return "https://tiyouju.com/"
    parsed = urllib.parse.urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in OFFICIAL_RELEASE_HOSTS:
        return "https://tiyouju.com/"
    if host == "github.com" and not parsed.path.startswith("/CEHNICA/question-bank-card/"):
        return "https://tiyouju.com/"
    return text


def describe_latest() -> dict:
    try:
        payload = json.loads(_read_official(LATEST_URL, 20, 100_000).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError) as exc:
        raise UpdateError("连不上 tiyouju.com，稍后再试。") from exc
    if not isinstance(payload, dict):
        raise UpdateError("官网没有给出版本信息。")
    latest = str(payload.get("version") or "").strip()
    latest_key = version_key(latest)
    current_key = version_key(APP_VERSION)
    notes = str(payload.get("notes") or "").strip()[:4000]
    raw_highlights = payload.get("highlights")
    highlights: list[str] = (
        [str(item).strip()[:160] for item in raw_highlights if isinstance(item, str) and str(item).strip()][:5]
        if isinstance(raw_highlights, list)
        else []
    )
    return {
        "current": APP_VERSION,
        "latest": latest,
        "update_available": latest_key > current_key,
        "published_at": str(payload.get("published_at") or ""),
        "auto_check": auto_check_enabled(),
        "notes": notes,
        "highlights": highlights,
        "release_url": _safe_release_url(payload.get("release_url")),
    }


def _checksum_for_installer(text: str) -> str:
    for line in text.splitlines():
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$", line.strip())
        if match and match.group(2).lower().startswith("tiyouju-setup-") and match.group(2).lower().endswith(".exe"):
            return match.group(1).lower()
    raise UpdateError("官网没有给出安装包的校验值。")


def _backup_database() -> None:
    database = Path(settings.DATABASES["default"]["NAME"])
    if not database.is_file():
        return
    folder = database.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"before-update-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    shutil.copy2(database, destination)
    old_snapshots = sorted(folder.glob("before-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in old_snapshots[3:]:
        stale.unlink(missing_ok=True)


_START_LOCK = threading.Lock()


def _reset_progress(*, status: str = "preparing", stage_label: str = "", version: str = "") -> None:
    now = time.monotonic()
    with _PROGRESS_LOCK:
        _PROGRESS_STATE.update(
            {
                "status": status,
                "stage_label": stage_label,
                "version": version,
                "bytes_downloaded": 0,
                "bytes_total": 0,
                "speed_bps": 0.0,
                "eta_seconds": None,
                "error": "",
                "started_at": now,
            }
        )
        _SPEED_SAMPLES.clear()
        _SPEED_SAMPLES.append((now, 0))


def _update_speed_and_eta_locked(now: float, record_sample: bool = True) -> None:
    done = _PROGRESS_STATE["bytes_downloaded"]
    total = _PROGRESS_STATE["bytes_total"]
    if record_sample:
        _SPEED_SAMPLES.append((now, done))
        while len(_SPEED_SAMPLES) > 2 and (now - _SPEED_SAMPLES[0][0]) > 2.5:
            _SPEED_SAMPLES.popleft()
        if len(_SPEED_SAMPLES) >= 2:
            dt = _SPEED_SAMPLES[-1][0] - _SPEED_SAMPLES[0][0]
            dbytes = _SPEED_SAMPLES[-1][1] - _SPEED_SAMPLES[0][1]
            if dt > 0.05 and dbytes >= 0:
                _PROGRESS_STATE["speed_bps"] = dbytes / dt
    speed = _PROGRESS_STATE["speed_bps"]
    if total > 0 and done < total and speed > 1024:
        _PROGRESS_STATE["eta_seconds"] = max(1, round((total - done) / speed))
    else:
        _PROGRESS_STATE["eta_seconds"] = None


def _set_progress(
    *,
    status: str | None = None,
    stage_label: str | None = None,
    version: str | None = None,
    bytes_downloaded: int | None = None,
    bytes_total: int | None = None,
    error: str | None = None,
) -> None:
    now = time.monotonic()
    with _PROGRESS_LOCK:
        if status is not None:
            _PROGRESS_STATE["status"] = status
        if stage_label is not None:
            _PROGRESS_STATE["stage_label"] = stage_label
        if version is not None:
            _PROGRESS_STATE["version"] = version
        if bytes_total is not None:
            _PROGRESS_STATE["bytes_total"] = max(0, int(bytes_total))
        if bytes_downloaded is not None:
            _PROGRESS_STATE["bytes_downloaded"] = max(0, int(bytes_downloaded))
        if error is not None:
            _PROGRESS_STATE["error"] = error
        _update_speed_and_eta_locked(now, record_sample=bytes_downloaded is not None)


def _add_progress_bytes(delta: int, bytes_total: int | None = None) -> None:
    now = time.monotonic()
    with _PROGRESS_LOCK:
        if bytes_total is not None and bytes_total > 0:
            _PROGRESS_STATE["bytes_total"] = int(bytes_total)
        val = max(0, _PROGRESS_STATE["bytes_downloaded"] + int(delta))
        total = _PROGRESS_STATE["bytes_total"]
        if total > 0:
            val = min(val, total)
        _PROGRESS_STATE["bytes_downloaded"] = val
        _update_speed_and_eta_locked(now, record_sample=True)


def get_install_progress() -> dict:
    with _PROGRESS_LOCK:
        total = _PROGRESS_STATE["bytes_total"]
        done = _PROGRESS_STATE["bytes_downloaded"]
        status = _PROGRESS_STATE["status"] or "idle"
        if status in {"verifying", "backing_up", "launching", "succeeded"}:
            percent = 100.0
        elif total > 0:
            percent = round(min(100.0, (done / total) * 100.0), 1)
        else:
            percent = 0.0
        return {
            "status": status,
            "active": status in _ACTIVE_STATUSES,
            "stage_label": _PROGRESS_STATE["stage_label"],
            "version": _PROGRESS_STATE["version"],
            "bytes_downloaded": done,
            "bytes_total": total,
            "percent": percent,
            "speed_bps": round(_PROGRESS_STATE["speed_bps"]),
            "eta_seconds": _PROGRESS_STATE["eta_seconds"],
            "error": _PROGRESS_STATE["error"],
        }


def _header_value(headers, name: str) -> str:
    if not headers:
        return ""
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return ""
    value = getter(name)
    return str(value or "").strip()


def _download_installer(part_path: Path, expected_checksum: str) -> None:
    part_path.parent.mkdir(parents=True, exist_ok=True)
    with _official_response(INSTALLER_URL, 30, headers={"Range": f"bytes=0-{_DOWNLOAD_CHUNK_BYTES - 1}"}) as first_resp:
        first_headers = getattr(first_resp, "headers", None)
        status = getattr(first_resp, "status", 200)
        content_range = _header_value(first_headers, "Content-Range")
        range_match = _CONTENT_RANGE_RE.match(content_range) if status == 206 else None

        if range_match and int(range_match.group(1)) == 0:
            first_end = int(range_match.group(2))
            total = int(range_match.group(3))
            if total <= 0 or total > MAX_INSTALLER_BYTES:
                raise UpdateError("安装包比预期大，已停止。")
            _set_progress(
                status="downloading",
                stage_label="正在从官网多线程加速下载安装包…",
                bytes_downloaded=0,
                bytes_total=total,
            )
            with part_path.open("wb") as init_handle:
                init_handle.truncate(total)
            write_lock = threading.Lock()

            def _consume_range(resp, start_offset: int, expected_len: int, handle) -> None:
                written = 0
                try:
                    while written < expected_len:
                        block = resp.read(min(_READ_BLOCK_BYTES, expected_len - written))
                        if not block:
                            break
                        with write_lock:
                            handle.seek(start_offset + written)
                            handle.write(block)
                        written += len(block)
                        _add_progress_bytes(len(block), total)
                except Exception:
                    if written:
                        _add_progress_bytes(-written, total)
                    raise
                if written != expected_len:
                    _add_progress_bytes(-written, total)
                    raise OSError(f"分段下载不完整（{written}/{expected_len}）")

            def _fetch_chunk(start: int, end: int, handle) -> None:
                expected_len = end - start + 1
                last_exc: Exception | None = None
                for attempt in range(_CHUNK_MAX_RETRIES):
                    try:
                        with _official_response(
                            INSTALLER_URL, 30, headers={"Range": f"bytes={start}-{end}"}
                        ) as chunk_resp:
                            if getattr(chunk_resp, "status", 200) != 206:
                                raise OSError("服务器未按分段返回数据")
                            _consume_range(chunk_resp, start, expected_len, handle)
                            return
                    except UpdateError:
                        raise
                    except Exception as exc:
                        last_exc = exc
                        if attempt + 1 < _CHUNK_MAX_RETRIES:
                            time.sleep(0.4 * (attempt + 1))
                raise UpdateError("从官网下载安装包超时或中断，请检查网络后重试。") from last_exc

            remaining_ranges = [
                (start, min(start + _DOWNLOAD_CHUNK_BYTES - 1, total - 1))
                for start in range(first_end + 1, total, _DOWNLOAD_CHUNK_BYTES)
            ]
            with part_path.open("r+b") as target_handle:
                with ThreadPoolExecutor(max_workers=max(1, _DOWNLOAD_WORKERS - 1)) as pool:
                    futures = [pool.submit(_fetch_chunk, start, end, target_handle) for start, end in remaining_ranges]
                    try:
                        _consume_range(first_resp, 0, first_end + 1, target_handle)
                    except UpdateError:
                        raise
                    except Exception as exc:
                        raise UpdateError("从官网下载安装包失败，请重试。") from exc
                    for future in futures:
                        future.result()
        else:
            raw_len = _header_value(first_headers, "Content-Length")
            try:
                total = int(raw_len) if raw_len else 0
            except ValueError:
                total = 0
            if total > MAX_INSTALLER_BYTES:
                raise UpdateError("安装包比预期大，已停止。")
            _set_progress(
                status="downloading", stage_label="正在从官网下载安装包…", bytes_downloaded=0, bytes_total=total
            )
            size = 0
            with part_path.open("wb") as handle:
                while True:
                    chunk = first_resp.read(_READ_BLOCK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_INSTALLER_BYTES:
                        raise UpdateError("安装包比预期大，已停止。")
                    handle.write(chunk)
                    _add_progress_bytes(len(chunk), total or size)

    _set_progress(status="verifying", stage_label="下载完成，正在核对官网 SHA-256 校验值…")
    if _file_sha256(part_path) != expected_checksum:
        part_path.unlink(missing_ok=True)
        raise UpdateError("安装包校验没通过，没有安装。")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as verify_handle:
        while True:
            block = verify_handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _launch_detached_installer(args: list[str]) -> subprocess.Popen:
    if os.name != "nt":
        return subprocess.Popen(args, close_fds=True)
    base_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
    )
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
    try:
        return subprocess.Popen(args, close_fds=True, creationflags=base_flags | breakaway)
    except OSError as exc:
        # 去掉 BREAKAWAY 重试会让安装器继续属于旧程序的 Job：它结束旧程序
        # 时也会被杀掉。保留旧程序和已校验的安装包，交给用户手动运行。
        raise UpdateError(f"无法独立启动安装程序，软件将保持打开。请退出软件后手动运行安装包：{args[0]}") from exc


def install_latest() -> dict:
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise UpdateError("已经在下载更新。")
    part_path: Path | None = None
    try:
        _reset_progress(status="preparing", stage_label="正在向官网确认版本与校验信息…")
        info = describe_latest()
        if not info["update_available"]:
            raise UpdateError(f"已经是官网最新版本 {info['current']}。")
        _set_progress(version=info["latest"], stage_label="正在获取官网安装包校验码…")
        checksums = _checksum_for_installer(_read_official(CHECKSUM_URL, 20, 100_000).decode("utf-8", "replace"))
        temp_dir = Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".")
        destination = temp_dir / f"TiYouJu-Setup-{info['latest']}.exe"
        part_path = temp_dir / f"TiYouJu-Setup-{info['latest']}.exe.part"
        reused_cached = False
        if destination.is_file():
            _set_progress(status="verifying", stage_label="本机已有同版本安装包，正在核对官网 SHA-256 校验值…")
            if _file_sha256(destination) == checksums:
                cached_size = int(destination.stat().st_size)
                _set_progress(
                    status="verifying",
                    stage_label="本机已有校验通过的安装包，跳过重复下载。",
                    bytes_downloaded=cached_size,
                    bytes_total=cached_size,
                )
                reused_cached = True
            else:
                destination.unlink(missing_ok=True)
        if not reused_cached:
            _download_installer(part_path, checksums)
            os.replace(part_path, destination)
            part_path = None
        _set_progress(status="backing_up", stage_label="校验通过，正在备份本地题库…")
        _backup_database()
        if os.name != "nt":
            raise UpdateError("自动安装只支持 Windows。")
        _set_progress(status="launching", stage_label="题库已备份，正在启动安装程序…")
        _launch_detached_installer(
            [
                str(destination),
                "/SILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
                "/MERGETASKS=!desktopicon",
                "/AUTOSTART=1",
            ]
        )
        _set_progress(status="succeeded", stage_label=f"正在安装 {info['latest']}，软件即将关掉并在装完后自动重开。")
        return {"ok": True, "version": info["latest"]}
    except UpdateError as exc:
        _set_progress(status="failed", stage_label="更新未完成", error=str(exc))
        raise
    except Exception as exc:
        _set_progress(status="failed", stage_label="更新未完成", error="下载安装过程中出现错误，请重试。")
        raise UpdateError("下载安装过程中出现错误，请重试。") from exc
    finally:
        if part_path is not None:
            part_path.unlink(missing_ok=True)
        _INSTALL_LOCK.release()


def start_install_background() -> dict:
    with _START_LOCK:
        if _INSTALL_LOCK.locked():
            return {"ok": True, "already_running": True, **get_install_progress()}
        _reset_progress(status="preparing", stage_label="正在向官网确认版本与校验信息…")
        ready = threading.Event()

        def _worker() -> None:
            ready.set()
            try:
                install_latest()
            except UpdateError:
                pass

        thread = threading.Thread(target=_worker, name="tiyouju-app-update", daemon=True)
        thread.start()
        ready.wait(timeout=0.5)
        return {"ok": True, "started": True, **get_install_progress()}


def _guard(request: HttpRequest):
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "只能在本机检查更新。"}, status=403)
    if request.headers.get("X-QB-Request") != "1":
        return JsonResponse({"error": "请求需从题库页面发起"}, status=403)
    return None


def _snapshot() -> dict:
    return {
        "current": APP_VERSION,
        "auto_check": auto_check_enabled(),
        "latest": "",
        "update_available": False,
        "notes": "",
        "highlights": [],
        "release_url": "https://tiyouju.com/",
    }


@csrf_exempt
def update_status_view(request: HttpRequest):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    refused = _guard(request)
    if refused:
        return refused
    return JsonResponse(_snapshot())


@csrf_exempt
def update_settings_view(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refused = _guard(request)
    if refused:
        return refused
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "请求格式不正确"}, status=400)
    if not isinstance(payload, dict) or "auto_check" not in payload:
        return JsonResponse({"error": "请说明要不要自动检查更新。"}, status=400)
    set_auto_check(bool(payload["auto_check"]))
    return JsonResponse(_snapshot())


@csrf_exempt
def update_check_view(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refused = _guard(request)
    if refused:
        return refused
    if request.GET.get("auto") == "1" and not auto_check_enabled():
        body = _snapshot()
        body["skipped"] = True
        return JsonResponse(body)
    try:
        return JsonResponse(describe_latest())
    except UpdateError as exc:
        return JsonResponse({"error": str(exc), **_snapshot()}, status=502)


@csrf_exempt
def update_install_view(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refused = _guard(request)
    if refused:
        return refused
    try:
        return JsonResponse(install_latest())
    except UpdateError as exc:
        return JsonResponse({"error": str(exc)}, status=400)


@csrf_exempt
def update_install_start_view(request: HttpRequest):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    refused = _guard(request)
    if refused:
        return refused
    return JsonResponse(start_install_background())


@csrf_exempt
def update_progress_view(request: HttpRequest):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    refused = _guard(request)
    if refused:
        return refused
    return JsonResponse(get_install_progress())
