"""Views for the AI assistant workbench and teacher presence (1.26.34)."""

from __future__ import annotations

import json

from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import assistant_workbench, library_assistant
from .library_browse import BrowseError, normalize_ids


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _guard(request, *, json_body: bool = True):
    if request.headers.get("X-QB-Request") != "1":
        return _error("请求需从题库页面发起", 403)
    if json_body and request.content_type != "application/json":
        return _error("请求格式不正确", 415)
    return None


def _body(request, limit: int = 200_000) -> dict | None:
    if len(request.body) > limit:
        return None
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


@csrf_exempt
def library_assistant_tasks(request):
    """Read the local assistant inbox without starting any cloud generation."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("助手待处理任务只能在本机读取", 403)
    try:
        limit = int(request.GET.get("limit", "50"))
        if not 1 <= limit <= 50:
            return _error("limit 应为 1–50")
        raw_ids = request.GET.get("ids")
        ids = normalize_ids(raw_ids.split(",")) if raw_ids is not None else None
        return JsonResponse(library_assistant.list_tasks(ids=ids, limit=limit))
    except (BrowseError, library_assistant.AssistantError) as error:
        return _error(str(error), getattr(error, "status", 400))
    except (ValueError, TypeError):
        return _error("待处理任务查询格式不正确")


@csrf_exempt
def library_assistant_prepare(request):
    return _library_assistant_action(request, "prepare")


@csrf_exempt
def library_assistant_complete(request):
    return _library_assistant_action(request, "complete")


@csrf_exempt
def library_assistant_workbench(request):
    """题库页里那块「助手工作台」读的东西：一屏看完助手交上来了什么。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("助手工作台只能在本机查看", 403)
    try:
        limit = int(request.GET.get("limit", "50"))
        return JsonResponse(assistant_workbench.workbench(limit=limit))
    except assistant_workbench.WorkbenchError as error:
        return _error(str(error), getattr(error, "status", 400))
    except (ValueError, TypeError):
        return _error("助手工作台读取格式不正确")


@csrf_exempt
def library_assistant_propose(request):
    """助手交一份题面改动。题面不动，等人点采纳。"""
    return _assistant_workbench_action(request, "propose")


@csrf_exempt
def library_assistant_claim(request):
    """助手领一批活；每道活带到期时间，助手没了活会自己回来。"""
    return _assistant_workbench_action(request, "claim")


@csrf_exempt
def library_assistant_release(request):
    """助手把活交回池子。"""
    return _assistant_workbench_action(request, "release")


@csrf_exempt
def library_assistant_whereami(request):
    """助手问一句：老师此刻在哪、我刚提的有没有被否决。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("助手只能在本机查看老师的位置", 403)
    return JsonResponse(assistant_workbench.whereami())


@csrf_exempt
def library_assistant_proposal_decide(request, proposal_id):
    """人点采纳或不采纳。采纳前题面一个字不动。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("处理提案的请求必须是 JSON 对象")
    payload = {**payload, "proposal_id": str(proposal_id)}
    try:
        return JsonResponse(assistant_workbench.decide(payload))
    except assistant_workbench.WorkbenchError as error:
        return _error(str(error), getattr(error, "status", 409))


@csrf_exempt
def teacher_presence(request):
    """题库页/审核页报一声：老师此刻在这道题上做什么。助手据此绕开。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("在场信息只能在本机记录", 403)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("在场信息请求必须是 JSON 对象")
    try:
        return JsonResponse(assistant_workbench.set_presence(payload))
    except assistant_workbench.WorkbenchError as error:
        return _error(str(error), getattr(error, "status", 400))


def _assistant_workbench_action(request, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("助手操作只能在本机使用", 403)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("助手请求必须是 JSON 对象")
    try:
        return JsonResponse(getattr(assistant_workbench, action)(payload))
    except assistant_workbench.WorkbenchError as error:
        return _error(str(error), getattr(error, "status", 409))


def _library_assistant_action(request, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("助手生成和写回只能在本机使用", 403)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("助手任务请求必须是 JSON 对象")
    try:
        return JsonResponse(getattr(library_assistant, action)(payload))
    except library_assistant.AssistantError as error:
        return _error(str(error), getattr(error, "status", 409))
