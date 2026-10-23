"""备份与合并的入口：设置 → 数据备份与迁移。

给老师看到的每一句都是人话：做了什么、动了几样、接下来点哪里。
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import library_transfer
from .library_transfer import TransferError

MAX_UPLOAD_BYTES = library_transfer.MAX_BUNDLE_BYTES


def _guard(request, *, json_body=True):
    from .views import _guard as guard
    return guard(request, json_body=json_body)


def _error(message: str, status: int = 400) -> JsonResponse:
    response = JsonResponse({"error": message}, status=status)
    response["Cache-Control"] = "no-store"
    return response


def _body(request, limit: int = 100_000) -> dict | None:
    from .views import _body as body
    return body(request, limit=limit)


@csrf_exempt
def export_view(request):
    """把整库打包成一个 zip，放到老师选的文件夹里。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request, limit=10_000)
    if payload is None:
        return _error("请求格式不正确。")
    directory = str(payload.get("directory") or "").strip()
    if not directory:
        return _error("先选一个文件夹，备份会放在那里。")
    try:
        result = library_transfer.export_bundle(Path(directory))
    except TransferError as error:
        return _error(str(error))
    except OSError:
        return _error("备份没写成，检查一下那个文件夹能不能写入。", status=500)
    counts = result.get("counts") or {}
    response = JsonResponse({
        "file": result["file"], "name": result["name"], "size": result["size"],
        "papers": counts.get("papers", 0), "questions": counts.get("questions", 0),
        "tags": counts.get("tags", 0), "publications": counts.get("publications", 0),
    })
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def choose_directory_view(request):
    """只在这台电脑上打开系统文件夹选择窗口；取消什么也不改。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    from .export_preferences import desktop_capable
    if not desktop_capable():
        return _error("只有 Windows 桌面版能选文件夹；网页版请用浏览器的下载。", status=409)
    from .native_folder_picker import choose_directory, FolderPickerError
    try:
        chosen = choose_directory("", "选择备份要存放的文件夹")
    except FolderPickerError as error:
        return _error(str(error), status=409)
    if chosen is None:
        return JsonResponse({"selected": False, "cancelled": True})
    return JsonResponse({"selected": True, "directory": str(chosen)})


def _uploaded_bundle(request) -> Path:
    uploads = request.FILES.getlist("file")
    if not uploads:
        raise TransferError("先选一个备份包。")
    if len(uploads) > 1:
        raise TransferError("一次只并一个备份包。")
    upload = uploads[0]
    if upload.size > MAX_UPLOAD_BYTES:
        raise TransferError("这个备份包太大了，请确认选的是题有据导出的备份包。")
    work = Path(tempfile.mkdtemp(prefix="qb-bundle-"))
    target = work / (Path(upload.name).name or "bundle.zip")
    with open(target, "wb") as writer:
        for chunk in upload.chunks():
            writer.write(chunk)
    return target


@csrf_exempt
def inspect_view(request):
    """合并前先看一眼：这份备份会带来什么。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    try:
        path = _uploaded_bundle(request)
    except TransferError as error:
        return _error(str(error))
    try:
        result = library_transfer.plan_merge(path)
    except TransferError as error:
        return _error(str(error))
    except (OSError, ValueError):
        return _error("这个备份包读不出来，没法看。请确认它是题有据导出的备份包。", status=400)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    manifest = result["manifest"]
    counts = manifest.get("counts") or {}
    response = JsonResponse({
        "created_at": manifest.get("created_at"), "machine": manifest.get("machine"),
        "app_version": manifest.get("app_version"),
        "papers": counts.get("papers", 0), "questions": counts.get("questions", 0),
        "tags": counts.get("tags", 0), "publications": counts.get("publications", 0),
        "report": result["report"],
    })
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def import_view(request):
    """把一份备份并进本机：只增不减，本机已有的题一道不删。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    try:
        path = _uploaded_bundle(request)
    except TransferError as error:
        return _error(str(error))
    try:
        result = library_transfer.merge_bundle(path)
    except TransferError as error:
        return _error(str(error))
    except (OSError, ValueError):
        return _error("合并没能完成，本机题库还是原样。可以再试一次。", status=500)
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)
    response = JsonResponse({"report": result["report"], "record": result["record"]})
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
def records_view(request):
    """合并过哪些次、能不能撤销。"""
    if request.method == "GET":
        response = JsonResponse({"records": library_transfer.list_records()})
        response["Cache-Control"] = "no-store"
        return response
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    try:
        removed = library_transfer.undo_last_merge()
    except TransferError as error:
        return _error(str(error))
    response = JsonResponse({"removed": removed})
    response["Cache-Control"] = "no-store"
    return response



