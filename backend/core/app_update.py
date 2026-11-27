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
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

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
_INSTALL_LOCK = threading.Lock()
_VERSION_PART = re.compile(r"^\d+$")


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


def _official_response(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": "tiyouju-update", "Accept": "*/*"})
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


def install_latest() -> dict:
    if not _INSTALL_LOCK.acquire(blocking=False):
        raise UpdateError("已经在下载更新。")
    try:
        info = describe_latest()
        if not info["update_available"]:
            raise UpdateError(f"已经是官网最新版本 {info['current']}。")
        checksums = _checksum_for_installer(_read_official(CHECKSUM_URL, 20, 100_000).decode("utf-8", "replace"))
        destination = (
            Path(os.environ.get("TEMP") or os.environ.get("TMP") or ".") / f"TiYouJu-Setup-{info['latest']}.exe"
        )
        digest = hashlib.sha256()
        size = 0
        with _official_response(INSTALLER_URL, 60) as response, destination.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_INSTALLER_BYTES:
                    raise UpdateError("安装包比预期大，已停止。")
                digest.update(chunk)
                handle.write(chunk)
        if digest.hexdigest() != checksums:
            destination.unlink(missing_ok=True)
            raise UpdateError("安装包校验没通过，没有安装。")
        _backup_database()
        if os.name != "nt":
            raise UpdateError("自动安装只支持 Windows。")
        subprocess.Popen(
            [
                str(destination),
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/CLOSEAPPLICATIONS",
                "/MERGETASKS=!desktopicon",
            ],
            close_fds=True,
        )
        return {"ok": True, "version": info["latest"]}
    finally:
        _INSTALL_LOCK.release()


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
