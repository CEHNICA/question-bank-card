"""Machine-local export destination, separate from papers, drafts and credentials.

Only a Windows web service explicitly started by the desktop launcher may
write exported documents outside its data directory. Requests never supply a
destination filename or path: the directory is an explicit saved preference,
and filenames come from the existing validated export builders.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from urllib.parse import quote, unquote

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

_lock = threading.RLock()
_receipts: dict[str, tuple[float, Path, tuple[int, int, int, int]]] = {}
_RECEIPT_SECONDS = 3600
_RESERVED = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class ExportPreferenceError(ValueError):
    pass


def preference_path() -> Path:
    root = Path(os.environ.get("QB_USER_ROOT") or Path(settings.DATA_ROOT).parent).resolve()
    return root / "export-preferences.json"


def desktop_capable() -> bool:
    return os.name == "nt" and os.environ.get("QB_DESKTOP_EXPORT") == "1"


def _directory(value: object) -> Path | None:
    if value == "":
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        raise ExportPreferenceError("请填写已存在的本地文件夹，或清空后使用浏览器下载。")
    # No UNC, device paths, URI, relative path, environment expansion or ADS.
    raw = value.strip()
    if raw.startswith(("\\\\", "//")) or any(ord(c) < 32 for c in raw) or "://" in raw:
        raise ExportPreferenceError("导出位置需要是本机已有的文件夹。")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ExportPreferenceError("导出位置需要是完整的本地文件夹路径。")
    try:
        resolved = candidate.resolve(strict=True)
        if not resolved.is_dir() or str(resolved).startswith("\\\\"):
            raise OSError("not local directory")
    except (OSError, ValueError):
        raise ExportPreferenceError("这个文件夹不存在或无法访问，请先创建文件夹，再保存设置。") from None
    return resolved


def _read() -> dict:
    target = preference_path()
    if target.is_symlink():
        raise ExportPreferenceError("导出目录设置被重定向，原文件已保留；请修复后再保存。")
    if not target.exists():
        return {"directory": ""}
    try:
        if target.is_symlink() or target.stat().st_size > 10_000:
            raise ValueError("invalid preference file")
        value = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or set(value) != {"directory"} or not isinstance(value["directory"], str):
            raise ValueError("invalid preferences")
        return value
    except (OSError, ValueError, UnicodeError):
        raise ExportPreferenceError("导出目录设置无法读取，原文件已保留；请修复后再保存。") from None


def describe() -> dict:
    try:
        value = _read()
        warning = ""
    except ExportPreferenceError as error:
        value, warning = {"directory": ""}, str(error)
    return {**value, "desktop_capable": desktop_capable(), "warning": warning}


def save(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != {"directory"}:
        raise ExportPreferenceError("导出目录设置格式不正确。")
    directory = _directory(payload["directory"])
    with _lock:
        _read()  # Never silently overwrite damaged or redirected preferences.
        target = preference_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                             prefix=".export-preferences-", suffix=".tmp", delete=False) as stream:
                pending = Path(stream.name)
                json.dump({"directory": str(directory) if directory else ""}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(pending, target)
            pending = None
        except OSError:
            raise ExportPreferenceError("导出目录设置未能保存，请检查本机文件夹权限。") from None
        finally:
            if pending:
                with contextlib.suppress(OSError):
                    pending.unlink()
    return describe()


def _guard(request, *, mutation=False):
    from .views import _guard as page_guard
    rejected = page_guard(request, json_body=mutation)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "请从本机题有据页面操作。"}, status=403)
    origin = request.headers.get("Origin")
    expected = request.scheme + "://" + request.get_host()
    if (origin and origin != expected) or (mutation and origin != expected):
        return JsonResponse({"error": "请从当前题有据页面操作。"}, status=403)
    return None


@csrf_exempt
def preferences_view(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request, mutation=request.method == "POST")
    if rejected:
        return rejected
    if request.method == "GET":
        response = JsonResponse(describe())
    else:
        if not desktop_capable():
            return JsonResponse({"error": "自定义导出目录仅在本机桌面版中可设置；此页面仍可正常下载。"}, status=409)
        from .views import _body
        try:
            response = JsonResponse(save(_body(request, limit=10_000)))
        except ExportPreferenceError as error:
            return JsonResponse({"error": str(error)}, status=400)
    response["Cache-Control"] = "no-store"
    return response


def _fingerprint(path: Path) -> tuple[int, int, int, int]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise OSError("not owned regular output")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _write(data: bytes, filename: str) -> tuple[Path, str]:
    value = _read()["directory"]
    directory = _directory(value)
    if directory is None:
        raise ExportPreferenceError("尚未设置导出目录，已改用浏览器下载。")
    # A formerly canonical directory must not be redirected after it was saved.
    if directory != Path(value):
        raise ExportPreferenceError("导出目录已变化，请重新确认设置；本次改用浏览器下载。")
    if (not isinstance(filename, str) or not filename or len(filename) > 200
            or filename != Path(filename).name or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', filename)
            or filename.endswith((".", " ")) or _RESERVED.match(filename)
            or Path(filename).suffix.lower() not in {".pdf", ".docx", ".zip"}):
        raise ExportPreferenceError("导出文件名不正确，未写入指定目录。")
    for number in range(1, 1001):
        name = filename if number == 1 else f"{Path(filename).stem} ({number}){Path(filename).suffix}"
        target = directory / name
        owned = None
        try:
            if directory.resolve(strict=True) != directory:
                raise OSError("redirected directory")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(target, flags, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                initial = os.fstat(stream.fileno())
                owned = initial.st_dev, initial.st_ino
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if directory.resolve(strict=True) != directory:
                raise OSError("directory moved during save")
            fingerprint = _fingerprint(target)
            token = secrets.token_urlsafe(24)
            now = time.monotonic()
            with _lock:
                for old, (created, _, _) in list(_receipts.items()):
                    if now - created > _RECEIPT_SECONDS or len(_receipts) >= 256:
                        _receipts.pop(old, None)
                _receipts[token] = now, target, fingerprint
            return target, token
        except FileExistsError:
            continue
        except OSError:
            # Remove only the incomplete file just created by this request.
            if owned:
                with contextlib.suppress(OSError):
                    current = target.stat()
                    if (current.st_dev, current.st_ino) == owned:
                        target.unlink()
            raise ExportPreferenceError("文件未能保存到指定目录，请检查文件夹权限或磁盘空间；本次改用浏览器下载。") from None
    raise ExportPreferenceError("指定目录中的同名文件过多；本次改用浏览器下载。")


def deliver(request, response, data: bytes, filename: str, *, count: int, pages: int | None = None):
    """Return a saved receipt only for an explicit desktop request, else the attachment.

    On a native save error return the already validated original attachment so
    the caller can download it without rendering again or losing the result.
    """
    if request.headers.get("X-QB-Export-Delivery") != "configured" or not desktop_capable():
        return response
    rejected = _guard(request, mutation=True)
    if rejected:
        return rejected
    try:
        target, token = _write(data, filename)
    except ExportPreferenceError as error:
        response["X-QB-Export-Warning"] = quote(str(error), safe="")
        return response
    receipt = {"saved": True, "filename": target.name, "path": str(target), "directory": str(target.parent),
               "question_count": count, "file_token": token}
    if response.get("X-QB-Layout-Warning"):
        receipt["warning"] = unquote(response["X-QB-Layout-Warning"])
    if pages is not None:
        receipt["page_count"] = pages
    result = JsonResponse(receipt)
    result["Cache-Control"] = "no-store"
    result["X-Question-Count"] = str(count)
    return result


@csrf_exempt
def open_export_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request, mutation=True)
    if rejected:
        return rejected
    if not desktop_capable():
        return JsonResponse({"error": "请在本机桌面版中打开导出文件。"}, status=409)
    from .views import _body
    value = _body(request, limit=10_000)
    if (not isinstance(value, dict) or set(value) - {"target", "file_token"}
            or value.get("target") not in {"directory", "file"}):
        return JsonResponse({"error": "打开导出位置的请求格式不正确。"}, status=400)
    try:
        if "file_token" in value:
            token = value["file_token"]
            if not isinstance(token, str):
                raise ExportPreferenceError("这个导出记录不可用，请重新导出。")
            with _lock:
                receipt = _receipts.pop(token, None)
            if not receipt or time.monotonic() - receipt[0] > _RECEIPT_SECONDS:
                raise ExportPreferenceError("这个导出记录已过期，请在导出目录中打开文件。")
            path = receipt[1]
            if path.resolve(strict=True) != path or _fingerprint(path) != receipt[2]:
                raise ExportPreferenceError("导出文件已移动或修改，请在文件夹中确认后再打开。")
            target = path if value["target"] == "file" else path.parent
        elif value["target"] == "directory":
            target = _directory(_read()["directory"])
            if target is None:
                raise ExportPreferenceError("请先设置导出目录。")
        else:
            raise ExportPreferenceError("打开文件需要本次导出的记录。")
        os.startfile(str(target))  # Native association; no shell command or supplied URL.
    except (ExportPreferenceError, OSError, ValueError) as error:
        return JsonResponse({"error": str(error) if isinstance(error, ExportPreferenceError) else "导出位置无法打开，请检查文件是否仍在原处。"}, status=409)
    return JsonResponse({"opened": True})
