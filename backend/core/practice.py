"""Offline practice selection and export. Never create formal publications or jobs."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
from urllib.parse import quote

from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from PIL import Image

from . import demo, library, library_pdf, source_images
from .library_browse import BrowseError, normalize_ids
from .library_drafts import DraftError, _print_options
from .library_export import ExportError, MAX_IMAGE_BYTES, MAX_TOTAL_IMAGES
from .models import Paper


def _paper(paper_id):
    paper = get_object_or_404(Paper, pk=paper_id, archived=False)
    if not demo.is_demo(paper):
        raise ExportError("这里只能打开练习题。正式题目请到正式题库查看。", 404)
    return paper


def page(request, paper_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        _paper(paper_id)
    except ExportError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    from .views import _frontend
    return _frontend("practice.html", "text/html; charset=utf-8")(request)


def library_view(request, paper_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        paper = _paper(paper_id)
        from .views import question_json
        items = []
        for question in paper.questions.select_related("paper").order_by("number", "id"):
            if library.approval_is_current(question):
                content = question_json(question)
                items.append({"id": str(question.source_key), "number": question.number,
                    "fingerprint": question.approved_content_hash, "content": content})
        response = JsonResponse({"paper": str(paper.pk), "items": items, "practice": True})
        response["Cache-Control"] = "no-store"
        return response
    except ExportError as error:
        return JsonResponse({"error": str(error)}, status=error.status)


def _capture(paper, payload):
    if not isinstance(payload, dict) or set(payload) - {"ids", "fingerprints"}:
        raise ExportError("练习选题格式不正确")
    try:
        ids = normalize_ids(payload.get("ids"))
    except BrowseError as error:
        raise ExportError(str(error)) from None
    if not ids or len(ids) > 20 or len(ids) != len(payload["ids"]):
        raise ExportError("请勾选不重复的练习题，最多 20 题。")
    fingerprints = payload.get("fingerprints")
    if not isinstance(fingerprints, dict) or set(fingerprints) != set(ids):
        raise ExportError("练习题版本不完整，请刷新后重选。", 409)
    found = {str(q.source_key): q for q in paper.questions.select_related("paper").filter(source_key__in=ids)}
    captured, total_bytes = [], 0
    for key in ids:
        question = found.get(key)
        if question is None or not library.approval_is_current(question) or fingerprints[key] != question.approved_content_hash:
            raise ExportError("练习题已修改或尚未通过，请返回核对后重选。", 409)
        image_body = source_images.is_image(question)
        files = [("body", source_images.asset_file(question, index))
                 for index in range(len(source_images.assets(question)))] if image_body else [
                 (figure["slot"].removeprefix("option_"), library.figure_file(question, index))
                 for index, figure in enumerate(question.figures)]
        images = []
        for slot, path in files:
            if path.stat().st_size > MAX_IMAGE_BYTES:
                raise ExportError("练习图片过大，请缩小范围后重试。", 413)
            data = path.read_bytes()
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_IMAGES:
                raise ExportError("练习图片过多，请减少选题。", 413)
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                if image.format != "PNG":
                    raise ExportError("练习图片未完整载入。", 409)
                size = image.size
            images.append({"slot": slot, "bytes": data, "size": size,
                "sha256": hashlib.sha256(data).hexdigest(), "name": path.name})
        content = {"stem": question.stem, "options": deepcopy(question.options), "origin": "",
                   "body_mode": question.body_mode}
        captured.append({"id": key, "type": question.question_type, "content": content,
            "selected": {}, "ai": False, "images": images, "solution_images": []})
    options = _print_options({"document": "questions", "student_info": False, "answer_space": "small"}, ids=ids)
    return captured, options


def _recheck(paper_id, payload):
    paper = _paper(paper_id)
    ids = payload["ids"]
    found = {str(q.source_key): q for q in paper.questions.select_related("paper").filter(source_key__in=ids)}
    for key in ids:
        question = found.get(key)
        if not question or not library.approval_is_current(question) or question.approved_content_hash != payload["fingerprints"][key]:
            raise ExportError("练习内容已改变，请重新预览。", 409)


@csrf_exempt
def output_view(request, paper_id, *, preview=False):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _guard
    rejected = _guard(request)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "练习导出需在本机打开。"}, status=403)
    try:
        payload = _body(request)
        captured, options = _capture(_paper(paper_id), payload)
        document = library_pdf._html_document(captured, "新手练习卷", options)
        if preview:
            _recheck(paper_id, payload)
            response = HttpResponse(document, content_type="text/html; charset=utf-8")
        else:
            data, pages = library_pdf._render(document)
            _recheck(paper_id, payload)
            response = HttpResponse(data, content_type="application/pdf")
            response["Content-Disposition"] = "attachment; filename=practice.pdf; filename*=UTF-8''" + quote("新手练习卷.pdf")
            response["X-Page-Count"] = str(pages)
        response["X-Question-Count"] = str(len(captured))
        response["Cache-Control"] = "no-store"
        return response
    except (ExportError, DraftError) as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except (OSError, ValueError, IndexError, RuntimeError):
        return JsonResponse({"error": "练习原图暂时无法读取，请返回核对范围或重新开始练习。"}, status=409)
