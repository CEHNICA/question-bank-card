"""Named paper drafts: ordered publication IDs and print choices, never question text or keys."""

from __future__ import annotations

from copy import deepcopy
import errno
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings
from django.http import HttpResponseNotAllowed, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .library_browse import BrowseError, normalize_ids, resolve_ids

MAX_DRAFTS = 100
MAX_STORE_BYTES = 2 * 1024 * 1024
MAX_TITLE_LENGTH = 120
LEGACY_PRINT_KEYS = {"answers", "origin", "ai_answers"}
PRINT_DEFAULTS = {"answers": True, "origin": False, "ai_answers": False,
                  "document": "combined", "font_size": 12, "answer_space": "none", "student_info": True,
                  "pagination": "compact", "option_layout": "auto", "option_overrides": {}, "question_breaks": [],
                  "answer_layout": "inline"}
INPUT_FIELDS = {"title", "ids", "print_options", "revision", "solutions"}
STORED_FIELDS = {"id", "title", "ids", "print_options", "created_at", "updated_at", "revision"}
_LOCK = threading.RLock()


class DraftError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def draft_path() -> Path:
    return Path(settings.DATA_ROOT) / "library-drafts.json"


def _title(value) -> str:
    if not isinstance(value, str):
        raise DraftError("草稿名称应为文字")
    value = value.strip()
    if not value or len(value) > MAX_TITLE_LENGTH or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DraftError(f"请填写 1–{MAX_TITLE_LENGTH} 字的草稿名称，不含换行或控制字符")
    return value


def _print_options(value, defaults=None, *, ids=None) -> dict:
    if not isinstance(value, dict) or set(value) - set(PRINT_DEFAULTS):
        raise DraftError("打印设置包含不支持的选项")
    if any(type(value[key]) is not bool for key in (LEGACY_PRINT_KEYS | {"student_info"}) & set(value)):
        raise DraftError("打印开关只能是 true 或 false")
    if "font_size" in value and (type(value["font_size"]) is not int or value["font_size"] not in (12, 14, 16)):
        raise DraftError("字号只能选 12、14、16 磅")
    if "document" in value and value["document"] not in ("questions", "answers", "combined"):
        raise DraftError("请选择题目卷、答案解析卷或题目与答案合卷")
    if "answer_space" in value and value["answer_space"] not in ("none", "medium", "large"):
        raise DraftError("答题留白只能选 none、medium、large")
    if "pagination" in value and value["pagination"] not in ("compact", "keep"):
        raise DraftError("分页只能选紧凑排版或尽量整题同页")
    if "option_layout" in value and value["option_layout"] not in ("auto", "four", "two"):
        raise DraftError("选项排版只能选 auto、four、two")
    if "answer_layout" in value and value["answer_layout"] not in ("inline", "appendix"):
        raise DraftError("答案位置只能选 inline 或 appendix")
    result = {**deepcopy(PRINT_DEFAULTS), **deepcopy(defaults or {}), **deepcopy(value)}
    if "document" not in value and "answers" in value:
        result["document"] = "combined" if value["answers"] else "questions"
    result["answers"] = result["document"] != "questions"
    overrides = result["option_overrides"]
    if not isinstance(overrides, dict) or len(overrides) > 500 \
            or any(mode not in ("auto", "four", "two") for mode in overrides.values()):
        raise DraftError("单题选项排版应为题目编号到 auto、four、two 的映射，最多 500 题")
    try:
        keys = normalize_ids(list(overrides))
        breaks = normalize_ids(result["question_breaks"])
        selected = set(normalize_ids(ids)) if ids is not None else None
    except BrowseError as error:
        raise DraftError("单题排版或另起页的题目编号不正确：" + str(error)) from None
    if len(keys) != len(overrides) or len(breaks) != len(result["question_breaks"]):
        raise DraftError("单题排版或另起页的题目编号不能重复")
    if selected is not None and (set(keys) - selected or set(breaks) - selected):
        raise DraftError("单题排版和另起页只能指定当前选中的题目")
    result["option_overrides"] = dict(zip(keys, overrides.values()))
    result["question_breaks"] = breaks
    return result


def _revision(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 1_000_000_000:
        raise DraftError("草稿版本编号不正确")
    return value


def _read() -> dict:
    target = draft_path()
    if target.is_symlink():
        raise DraftError("草稿文件不能是符号链接", 409)
    if not target.exists():
        return {"schema": 1, "drafts": []}
    try:
        if target.stat().st_size > MAX_STORE_BYTES:
            raise DraftError("草稿文件超过大小上限，未覆盖原文件", 409)
        raw = target.read_bytes()
        if len(raw) > MAX_STORE_BYTES:
            raise DraftError("草稿文件超过大小上限，未覆盖原文件", 409)
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"schema", "drafts"} or data["schema"] != 1 \
                or isinstance(data["schema"], bool) or not isinstance(data["drafts"], list) \
                or len(data["drafts"]) > MAX_DRAFTS:
            raise ValueError()
        seen = set()
        for draft in data["drafts"]:
            if not isinstance(draft, dict) or set(draft) - STORED_FIELDS - {"solutions"} or not STORED_FIELDS <= set(draft):
                raise ValueError()
            key = str(uuid.UUID(draft["id"]))
            if key != draft["id"] or key in seen:
                raise ValueError()
            seen.add(key)
            if _title(draft["title"]) != draft["title"] or normalize_ids(draft["ids"]) != draft["ids"]:
                raise ValueError()
            if not isinstance(draft["print_options"], dict) or not LEGACY_PRINT_KEYS <= set(draft["print_options"]):
                raise ValueError()
            # Old saved papers intentionally retain their appendix layout.
            draft["print_options"] = _print_options(draft["print_options"], {"answer_layout": "appendix"}, ids=draft["ids"])
            from .library_solutions import normalize_map, SolutionError
            try:
                fixed = normalize_map(draft.get("solutions", {}), draft["ids"])
                draft["solutions"] = {key: fixed.get(key, "origin") for key in draft["ids"]}
            except SolutionError as error:
                raise DraftError(str(error)) from None
            _revision(draft["revision"])
            for field in ("created_at", "updated_at"):
                if not isinstance(draft[field], str) or len(draft[field]) > 40:
                    raise ValueError()
                from datetime import datetime
                datetime.fromisoformat(draft[field])
    except DraftError as error:
        raise DraftError("草稿文件内容异常，未覆盖原文件", 409) from error
    except (OSError, ValueError, TypeError, KeyError, AttributeError, BrowseError):
        raise DraftError("组卷草稿文件无法读取，未覆盖原文件", 409) from None
    return data


@contextmanager
def _write_lock():
    """Serialize both threads and desktop processes before read-modify-replace."""
    with _LOCK:
        target = draft_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        lock_path = target.with_suffix(".lock")
        if lock_path.is_symlink():
            raise DraftError("草稿锁文件不能是符号链接", 409)
        with lock_path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 5
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
                        raise DraftError("草稿正在被另一个窗口保存，请稍后重试", 409) from None
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


def _write(data: dict) -> None:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_STORE_BYTES:
        raise DraftError("组卷草稿总大小已达上限，请删去不再需要的草稿", 409)
    target = draft_path()
    if target.is_symlink():
        raise DraftError("草稿文件不能是符号链接", 409)
    descriptor, temporary = tempfile.mkstemp(prefix=".library-drafts-", suffix=".json", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _public(draft: dict) -> dict:
    checked = resolve_ids(draft["ids"], include_items=False)
    return {**draft, "validity": {"valid": not checked["missing"],
                                 "valid_count": checked["available_count"], "missing": checked["missing"]}}


def _payload(request) -> dict:
    from .views import _body
    payload = _body(request)
    if payload is None or set(payload) - INPUT_FIELDS:
        raise DraftError("只可保存草稿名称、题目编号、打印设置和草稿版本")
    return payload


def _save(payload: dict, draft_id=None) -> dict:
    with _write_lock():
        data = _read()
        draft = None
        if draft_id is not None:
            draft = next((item for item in data["drafts"] if item["id"] == str(draft_id)), None)
            if draft is None:
                raise DraftError("找不到这份组卷草稿", 404)
            if "revision" in payload and _revision(payload["revision"]) != draft["revision"]:
                raise DraftError("这份草稿已在其他窗口修改，请重新载入后再保存", 409)
        elif len(data["drafts"]) >= MAX_DRAFTS:
            raise DraftError(f"最多保存 {MAX_DRAFTS} 份组卷草稿，请先删去不再需要的草稿", 409)
        elif "revision" in payload:
            raise DraftError("新增草稿不需要提供版本编号")
        if draft is None and "ids" not in payload:
            raise DraftError("请提供按顺序排列的题目编号 ids")
        title = _title(payload.get("title", draft["title"] if draft else "练习"))
        try:
            ids = normalize_ids(payload.get("ids", draft["ids"] if draft else []))
        except BrowseError as error:
            raise DraftError(str(error)) from None
        defaults = deepcopy(draft["print_options"] if draft else PRINT_DEFAULTS)
        # Removing a question also removes its saved layout hint. Explicit new
        # hints for an unselected ID still fail validation rather than disappearing.
        defaults["option_overrides"] = {key: mode for key, mode in defaults["option_overrides"].items() if key in ids}
        defaults["question_breaks"] = [key for key in defaults["question_breaks"] if key in ids]
        options = _print_options(payload.get("print_options", {}), defaults, ids=ids)
        from .library_solutions import normalize_map, SolutionError
        try:
            previous_solutions = {key: value for key, value in (draft.get("solutions", {}) if draft else {}).items() if key in ids}
            solutions = normalize_map(payload.get("solutions", previous_solutions), ids)
            # Newly added entries capture their current library overlay once;
            # an original selection is explicit so later edits cannot alter it.
            from .models import PublishedQuestion
            current = {str(item.pk): (item.extras or {}).get("solution_id") for item in PublishedQuestion.objects.filter(pk__in=ids)}
            for key in ids:
                solutions.setdefault(key, current.get(key) or "origin")
        except SolutionError as error:
            raise DraftError(str(error), error.status) from None
        now = timezone.now().isoformat()
        result = {"id": str(draft_id) if draft else str(uuid.uuid4()), "title": title, "ids": ids,
                  "print_options": options, "solutions": solutions, "created_at": draft["created_at"] if draft else now,
                  "updated_at": now, "revision": draft["revision"] + 1 if draft else 1}
        if draft is None:
            data["drafts"].append(result)
        else:
            data["drafts"][data["drafts"].index(draft)] = result
        _write(data)
    return result


@csrf_exempt
def drafts_view(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.method == "POST":
        from .views import _guard
        rejected = _guard(request)
        if rejected is not None:
            return rejected
    try:
        if request.method == "GET":
            data = _read()
            drafts = sorted(data["drafts"], key=lambda item: (item["updated_at"], item["id"]), reverse=True)
            return JsonResponse({"drafts": [_public(item) for item in drafts], "total": len(drafts)})
        return JsonResponse({"draft": _public(_save(_payload(request)))}, status=201)
    except DraftError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except OSError:
        return JsonResponse({"error": "组卷草稿没能保存，请检查数据目录是否可写"}, status=500)


@csrf_exempt
def draft_detail(request, draft_id):
    if request.method not in {"GET", "PUT", "DELETE"}:
        return HttpResponseNotAllowed(["GET", "PUT", "DELETE"])
    if request.method != "GET":
        from .views import _guard
        rejected = _guard(request)
        if rejected is not None:
            return rejected
    try:
        try:
            draft_id = str(uuid.UUID(str(draft_id)))
        except ValueError:
            raise DraftError("草稿编号格式不正确") from None
        if request.method == "PUT":
            return JsonResponse({"draft": _public(_save(_payload(request), draft_id))})
        if request.method == "GET":
            draft = next((item for item in _read()["drafts"] if item["id"] == draft_id), None)
            if draft is None:
                raise DraftError("找不到这份组卷草稿", 404)
            return JsonResponse({"draft": _public(draft)})
        with _write_lock():
            data = _read()
            before = len(data["drafts"])
            data["drafts"] = [item for item in data["drafts"] if item["id"] != draft_id]
            if len(data["drafts"]) == before:
                raise DraftError("找不到这份组卷草稿", 404)
            _write(data)
        return JsonResponse({"deleted": draft_id})
    except DraftError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except OSError:
        return JsonResponse({"error": "组卷草稿没能保存，请检查数据目录是否可写"}, status=500)
