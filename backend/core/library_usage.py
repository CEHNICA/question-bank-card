"""Per-exam deduplicated question usage history stored atomically beside library drafts."""

from __future__ import annotations

import errno
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .library_browse import BrowseError, normalize_ids

MAX_BATCHES = 500
MAX_USAGE_BYTES = 4 * 1024 * 1024
_LOCK = threading.RLock()
_CACHE: tuple[tuple[str, int, int], dict[str, dict[str, Any]], list[dict[str, Any]]] | None = None


class UsageError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def usage_path() -> Path:
    return Path(settings.DATA_ROOT) / "library-usage.json"


@contextmanager
def _write_lock():
    with _LOCK:
        target = usage_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(".lock")
        if lock_path.is_symlink():
            raise UsageError("用题记录锁文件不能是符号链接", 409)
        with lock_path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 5.0
            while True:
                handle.seek(0)
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if time.monotonic() >= deadline:
                        raise UsageError("用题记录正在被另一个窗口写入，请稍后重试", 409) from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_store() -> dict[str, Any]:
    target = usage_path()
    if target.is_symlink():
        raise UsageError("用题记录文件不能是符号链接", 409)
    if not target.exists():
        return {"schema": 1, "batches": []}
    try:
        raw = target.read_bytes()
        if len(raw) > MAX_USAGE_BYTES:
            return {"schema": 1, "batches": []}
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("schema") == 1 and isinstance(data.get("batches"), list):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"schema": 1, "batches": []}


def _write_store(data: dict[str, Any]) -> None:
    global _CACHE
    target = usage_path()
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_USAGE_BYTES:
        raise UsageError("用题历史记录超过存储上限", 409)
    fd, tmp_name = tempfile.mkstemp(prefix=".library-usage-", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)
    with _LOCK:
        _CACHE = None


def _stat_signature(path: Path) -> tuple[str, int, int]:
    try:
        st = path.stat()
        return (str(path), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        return (str(path), 0, 0)


def _load_cached() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    global _CACHE
    target = usage_path()
    sig = _stat_signature(target)
    with _LOCK:
        if _CACHE is not None and _CACHE[0] == sig:
            return _CACHE[1], _CACHE[2]
        store = _read_store()
        batches = [dict(b) for b in store.get("batches") or [] if isinstance(b, dict)]
        batches.sort(key=lambda b: str(b.get("used_at") or ""), reverse=True)
        qmap: dict[str, dict[str, Any]] = {}
        for batch in batches:
            bid = str(batch.get("id") or "")
            title = str(batch.get("title") or "未命名练习")
            used_at = str(batch.get("used_at") or "")
            export_count = int(batch.get("export_count") or 1)
            for pid in batch.get("ids") or []:
                key = str(pid)
                entry = qmap.get(key)
                if entry is None:
                    entry = {"count": 0, "last_used_at": used_at, "last_title": title, "recent_batches": []}
                    qmap[key] = entry
                entry["count"] += 1
                if len(entry["recent_batches"]) < 5:
                    entry["recent_batches"].append(
                        {"id": bid, "title": title, "used_at": used_at, "export_count": export_count}
                    )
        _CACHE = (sig, qmap, batches)
        return qmap, batches


def question_usage_map() -> dict[str, dict[str, Any]]:
    qmap, _batches = _load_cached()
    return qmap


def list_batches() -> list[dict[str, Any]]:
    _qmap, batches = _load_cached()
    return [
        {
            "id": str(b.get("id") or ""),
            "title": str(b.get("title") or "未命名练习"),
            "date_key": str(b.get("date_key") or ""),
            "question_count": len(b.get("ids") or []),
            "ids": list(b.get("ids") or []),
            "paper_spec": str(b.get("paper_spec") or "a4"),
            "total_score": b.get("total_score"),
            "last_format": str(b.get("last_format") or "pdf"),
            "export_count": int(b.get("export_count") or 1),
            "used_at": str(b.get("used_at") or ""),
            "created_at": str(b.get("created_at") or ""),
        }
        for b in batches
    ]


def excluded_ids_within_days(days: int) -> set[str]:
    _qmap, batches = _load_cached()
    cutoff = None
    if days > 0:
        cutoff = timezone.now() - timedelta(days=days)
    excluded: set[str] = set()
    for b in batches:
        if cutoff is not None:
            raw_dt = str(b.get("used_at") or "")
            try:
                parsed = datetime.fromisoformat(raw_dt)
                if timezone.is_naive(parsed):
                    parsed = timezone.make_aware(parsed)
                if parsed < cutoff:
                    continue
            except ValueError:
                continue
        excluded.update(str(pid) for pid in (b.get("ids") or []))
    return excluded


def excluded_ids_for_batch(batch_id: str) -> set[str]:
    target = str(batch_id or "").strip()
    if not target:
        return set()
    _qmap, batches = _load_cached()
    for b in batches:
        if str(b.get("id") or "") == target:
            return {str(pid) for pid in (b.get("ids") or [])}
    return set()


def record_exam_export(
    *,
    ids: list[str],
    title: str,
    batch_key: str = "",
    paper_spec: str = "a4",
    total_score: float | None = None,
    export_format: str = "pdf",
) -> dict[str, Any]:
    """Merge repeated exports of the same exam/paper into a single deduplicated usage batch."""
    try:
        clean_ids = normalize_ids(list(ids))
    except BrowseError as error:
        raise UsageError(str(error)) from None
    if not clean_ids:
        raise UsageError("用题记录不能为空")
    clean_title = str(title or "未命名练习").strip()[:120] or "未命名练习"
    clean_key = str(batch_key or "").strip()[:80]
    now = timezone.now()
    now_iso = now.isoformat()
    date_key = timezone.localtime(now).date().isoformat()
    clean_spec = "a3_2col" if paper_spec == "a3_2col" else "a4"
    clean_fmt = export_format if export_format in {"pdf", "docx", "split"} else "pdf"

    with _write_lock():
        store = _read_store()
        batches = [dict(b) for b in store.get("batches") or [] if isinstance(b, dict)]
        matched = None
        for b in batches:
            if clean_key and str(b.get("batch_key") or "") == clean_key:
                matched = b
                break
            if str(b.get("title") or "") == clean_title and str(b.get("date_key") or "") == date_key:
                matched = b
                break
        if matched is not None:
            matched["title"] = clean_title
            if clean_key:
                matched["batch_key"] = clean_key
            matched["ids"] = clean_ids
            matched["paper_spec"] = clean_spec
            matched["total_score"] = total_score
            matched["last_format"] = clean_fmt
            matched["export_count"] = int(matched.get("export_count") or 1) + 1
            matched["used_at"] = now_iso
            record = matched
        else:
            record = {
                "id": str(uuid.uuid4()),
                "batch_key": clean_key,
                "title": clean_title,
                "date_key": date_key,
                "ids": clean_ids,
                "paper_spec": clean_spec,
                "total_score": total_score,
                "last_format": clean_fmt,
                "export_count": 1,
                "created_at": now_iso,
                "used_at": now_iso,
            }
            batches.insert(0, record)
        batches.sort(key=lambda b: str(b.get("used_at") or ""), reverse=True)
        store["batches"] = batches[:MAX_BATCHES]
        _write_store(store)
    return record


def delete_usage_batch(batch_id: str) -> bool:
    target = str(batch_id or "").strip()
    if not target:
        return False
    with _write_lock():
        store = _read_store()
        batches = [dict(b) for b in store.get("batches") or [] if isinstance(b, dict)]
        kept = [b for b in batches if str(b.get("id") or "") != target]
        if len(kept) == len(batches):
            return False
        store["batches"] = kept
        _write_store(store)
    return True


@csrf_exempt
def usage_view(request):
    if request.method == "GET":
        batches = list_batches()
        return JsonResponse({"batches": batches, "total": len(batches)})
    if request.method == "POST":
        from .views import _body, _error, _guard

        rejected = _guard(request)
        if rejected is not None:
            return rejected
        payload = _body(request)
        if payload is None or not isinstance(payload.get("ids"), list):
            return _error("请提供合法的题目编号列表")
        try:
            rec = record_exam_export(
                ids=[str(x) for x in payload["ids"]],
                title=str(payload.get("title") or "练习"),
                batch_key=str(payload.get("batch_key") or ""),
                paper_spec=str(payload.get("paper_spec") or "a4"),
                total_score=payload.get("total_score"),
                export_format=str(payload.get("format") or "pdf"),
            )
            return JsonResponse({"ok": True, "batch": rec})
        except UsageError as error:
            return JsonResponse({"error": str(error)}, status=error.status)
    return HttpResponseNotAllowed(["GET", "POST"])


@csrf_exempt
def usage_detail_view(request, batch_id: str):
    if request.method != "DELETE":
        return HttpResponseNotAllowed(["DELETE"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected is not None:
        return rejected
    if not delete_usage_batch(batch_id):
        return JsonResponse({"error": "找不到该次出卷记录"}, status=404)
    return JsonResponse({"ok": True, "deleted": batch_id})
