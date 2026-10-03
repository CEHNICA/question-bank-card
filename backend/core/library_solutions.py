"""Versioned solution overlays and private local assets, never OCR replacements."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import uuid
import warnings

from django.conf import settings
from django.db import OperationalError, transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from PIL import Image, ImageOps

from . import imaging, library
from .models import LibrarySolution, PublishedQuestion

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_FIGURES = 24


class SolutionError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identity(value, label="解析版本"):
    if not isinstance(value, str):
        raise SolutionError(f"{label}编号不正确")
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        raise SolutionError(f"{label}编号不正确") from None


def _folder(publication_id):
    root = Path(settings.DATA_ROOT)
    folder = root / "library-solutions" / str(publication_id)
    if any(part.is_symlink() for part in (folder.parent, folder)) or not folder.resolve().is_relative_to(root.resolve()):
        raise SolutionError("解析图片目录异常，未访问原文件", 409)
    return folder


def _asset_spec(publication_id, asset_id):
    key = identity(asset_id, "解析图片")
    folder = _folder(publication_id)
    target, metadata = folder / (key + ".png"), folder / (key + ".json")
    try:
        if target.is_symlink() or metadata.is_symlink() or target.stat().st_size > MAX_UPLOAD_BYTES or metadata.stat().st_size > 2000:
            raise ValueError
        spec = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(spec, dict) or spec.get("id") != key or spec.get("publication_id") != str(publication_id) \
                or not isinstance(spec.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", spec["sha256"]) \
                or type(spec.get("width")) is not int or type(spec.get("height")) is not int \
                or not 1 <= spec["width"] or not 1 <= spec["height"] or spec["width"] * spec["height"] > MAX_PIXELS:
            raise ValueError
        return spec, target
    except (OSError, ValueError, KeyError, TypeError):
        raise SolutionError("解析图片缺失或校验失败，请重新添加，原解析未被覆盖", 409) from None


def _asset(publication_id, asset_id):
    try:
        spec, target = _asset_spec(publication_id, asset_id)
        data = target.read_bytes()
        if len(data) > MAX_UPLOAD_BYTES or hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.width * image.height > MAX_PIXELS \
                        or [image.width, image.height] != [spec.get("width"), spec.get("height")]:
                    raise ValueError
                image.load()
        return spec, data
    except (OSError, ValueError, KeyError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise SolutionError("解析图片缺失或校验失败，请重新添加，原解析未被覆盖", 409) from None


def _figure_json(publication_id, value):
    # Listing hundreds of library entries or historical versions must not
    # decode every full image. Retrieval, save and export verify actual bytes.
    spec, _target = _asset_spec(publication_id, value["id"])
    return {**deepcopy(spec), **deepcopy(value),
            "url": f"/api/library/{publication_id}/solution-images/{spec['id']}?v={spec['sha256'][:16]}"}


def solution_json(solution, *, fingerprint=None):
    return {"id": str(solution.pk), "publication_id": str(solution.publication_id),
            "parent_id": str(solution.parent_id) if solution.parent_id else None,
            "fingerprint": solution.fingerprint, "answer": solution.answer, "analysis": solution.analysis,
            "figures": [_figure_json(solution.publication_id, item) for item in solution.figures],
            "created_at": solution.created_at.isoformat(),
            "needs_check": solution.fingerprint != (fingerprint or library.generation_fingerprint(solution.publication.content, solution.publication_id))}


def selected(publication, revision=None):
    if revision == "origin":
        return None
    key = revision if revision is not None else (publication.extras or {}).get("solution_id")
    if not key:
        return None
    solution = LibrarySolution.objects.filter(pk=identity(key), publication_id=publication.pk).first()
    if solution is None:
        raise SolutionError("解析版本不属于这道题或已经不存在", 409)
    if solution.fingerprint != library.generation_fingerprint(publication.content, publication.pk):
        raise SolutionError("解析对应的题面或配图已变化，请重新核对并保存解析", 409)
    return solution


def normalize_map(value, ids):
    if not isinstance(value, dict) or len(value) > 500:
        raise SolutionError("解析版本应为本次选题编号到版本编号的映射")
    wanted = set(ids)
    result = {}
    for key, revision in value.items():
        canonical = identity(key, "入库题目")
        if canonical not in wanted or canonical in result:
            raise SolutionError("解析只能绑定本次已选的题目")
        result[canonical] = "origin" if revision == "origin" else identity(revision)
    return result


def capture_images(solution):
    images = []
    for figure in solution.figures:
        spec, data = _asset(solution.publication_id, figure["id"])
        images.append({**deepcopy(figure), "slot": "solution", "bytes": data,
                       "size": (spec["width"], spec["height"]), "name": spec["id"], "sha256": spec["sha256"]})
    return images


def _text(value, name, maximum):
    if not isinstance(value, str) or any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value) \
            or len(value.encode("utf-16-le")) // 2 > maximum:
        raise SolutionError(f"{name}应为不超过 {maximum} 字的文字")
    return value.strip()


def save(publication, payload):
    allowed = {"answer", "analysis", "figures", "base_revision", "sync_library"}
    if not isinstance(payload, dict) or set(payload) - allowed or not {"answer", "analysis", "base_revision", "sync_library"} <= set(payload):
        raise SolutionError("请提供答案、解析、原解析版本和是否同步题库")
    if type(payload["sync_library"]) is not bool:
        raise SolutionError("是否同步题库只能是 true 或 false")
    answer, analysis = _text(payload["answer"], "答案", 8000), _text(payload["analysis"], "解析", 100000)
    figures = payload.get("figures", [])
    if not isinstance(figures, list) or len(figures) > MAX_FIGURES:
        raise SolutionError(f"解析配图最多 {MAX_FIGURES} 张")
    normalized, seen = [], set()
    for value in figures:
        value = {"id": value} if isinstance(value, str) else value
        if not isinstance(value, dict) or set(value) - {"id", "display_width", "position", "paragraph"} or "id" not in value:
            raise SolutionError("解析图片位置格式不正确")
        key = identity(value["id"], "解析图片")
        if key in seen:
            raise SolutionError("同一张解析图片不能重复添加")
        seen.add(key)
        spec, _data = _asset(publication.pk, key)
        width = value.get("display_width", min(178, max(20, spec["width"] * 25.4 / 150)))
        position, paragraph = value.get("position", "after"), value.get("paragraph", 0)
        if type(width) not in {float, int} or not math.isfinite(width) or not 5 <= width <= 178 \
                or position not in {"before", "after", "paragraph"} or type(paragraph) is not int or not 0 <= paragraph <= 1000:
            raise SolutionError("解析图宽度应为 5–178 mm，位置应为解析前、解析后或段落")
        normalized.append({"id": key, "display_width": width, "position": position, "paragraph": paragraph})
    if not answer and not analysis and not normalized:
        raise SolutionError("请填写答案、解析或添加解析图片")
    base = identity(payload["base_revision"]) if payload["base_revision"] is not None else None
    with transaction.atomic():
        publication = PublishedQuestion.objects.select_for_update().get(pk=publication.pk)
        if publication.status != PublishedQuestion.Status.PUBLISHED:
            raise SolutionError("题目已撤回或被新版替代，未覆盖原解析", 409)
        parent = LibrarySolution.objects.filter(pk=base, publication=publication).first() if base else None
        if base and parent is None:
            raise SolutionError("原解析版本不属于这道题", 409)
        current = (publication.extras or {}).get("solution_id")
        # A branch already synchronized by this editor may safely start from its
        # own parent; parallel edits still need the exact current head.
        if payload["sync_library"] and current != base:
            raise SolutionError("题库解析已在其他窗口修改，请重新载入；本次文字和原解析均保留", 409)
        row = LibrarySolution.objects.create(publication=publication, parent=parent,
            fingerprint=library.generation_fingerprint(publication.content, publication.pk),
            answer=answer, analysis=analysis, figures=normalized)
        if payload["sync_library"]:
            extras = {**deepcopy(publication.extras or {}), "solution_id": str(row.pk)}
            extras.pop("solution_needs_review_id", None)
            library.save_extras(publication, extras)
    return row


@csrf_exempt
def solution_view(request, publication_id):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    from .views import _body, _guard
    if request.method == "POST":
        rejected = _guard(request)
        if rejected is not None:
            return rejected
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        if request.method == "POST":
            row = save(publication, _body(request, limit=500_000))
            publication.refresh_from_db()
            return JsonResponse({"solution": solution_json(row), "base_revision": (publication.extras or {}).get("solution_id")}, status=201)
        row = selected(publication, request.GET.get("revision"))
        fingerprint = library.generation_fingerprint(publication.content, publication.pk)
        raw_ai = (publication.extras or {}).get("ai_answer")
        ai_answer = None
        ai_stale = False
        if isinstance(raw_ai, dict) and (raw_ai.get("answer") or raw_ai.get("analysis")):
            ai_stale = raw_ai.get("fingerprint") != fingerprint
            if not ai_stale:
                ai_answer = deepcopy(raw_ai)
        history = []
        for item in publication.solutions.select_related("publication")[:100]:
            try:
                history.append(solution_json(item, fingerprint=fingerprint))
            except SolutionError as error:
                # A broken old image must not prevent editing a healthy current
                # solution. Preserve the revision and prose for manual repair.
                history.append({"id": str(item.pk), "publication_id": str(item.publication_id),
                    "answer": item.answer, "analysis": item.analysis, "figures": [], "asset_error": str(error),
                    "created_at": item.created_at.isoformat(), "needs_check": True})
        return JsonResponse({"solution": solution_json(row) if row else None,
            "base_revision": (publication.extras or {}).get("solution_id"),
            "origin": {"answer": str((publication.content or {}).get("answer") or ""), "analysis": str((publication.content or {}).get("analysis") or "")},
            "ai_answer": ai_answer, "ai_answer_stale": ai_stale,
            "history": history})
    except SolutionError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except OSError:
        return JsonResponse({"error": "解析文件没能保存，请检查本机数据目录，原解析保留"}, status=500)
    except OperationalError:
        return JsonResponse({"error": "解析正在被另一个操作保存，请稍后重试；当前输入和原解析保留"}, status=409)


def _store_image(publication, image):
    image.load()
    if image.width < 1 or image.height < 1 or image.width * image.height > MAX_PIXELS:
        raise SolutionError("图片尺寸过大或为空")
    image = ImageOps.exif_transpose(image).convert("RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB")
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    data = output.getvalue()
    if len(data) > MAX_UPLOAD_BYTES:
        raise SolutionError("解析图片超过 16 MiB，请压缩后重试", 413)
    key = str(uuid.uuid4())
    spec = {"id": key, "publication_id": str(publication.pk), "width": image.width, "height": image.height,
            "sha256": hashlib.sha256(data).hexdigest()}
    folder = _folder(publication.pk)
    folder.mkdir(parents=True, exist_ok=True)
    # IDs are fresh for each addition; no saved immutable asset is overwritten.
    for suffix, raw in ((".png", data), (".json", json.dumps(spec).encode("utf-8"))):
        target = folder / (key + suffix)
        temporary = folder / ("." + key + suffix)
        try:
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return _figure_json(publication.pk, {"id": key, "display_width": min(178, max(20, image.width * 25.4 / 150)), "position": "after", "paragraph": 0})


@csrf_exempt
def image_upload(request, publication_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _guard
    rejected = _guard(request, json_body=False)
    if rejected is not None:
        return rejected
    publication = get_object_or_404(PublishedQuestion, pk=publication_id, status=PublishedQuestion.Status.PUBLISHED)
    try:
        if request.content_type == "application/json":
            payload = _body(request) or {}
            if set(payload) != {"page_idx", "bbox"} or publication.paper is None:
                raise SolutionError("请提供原卷页码和矩形范围")
            from .source_images import valid_regions
            if not valid_regions(publication.paper, [payload]):
                raise SolutionError("范围必须位于这道题所属原卷的真实页面内")
            from .pipeline import PageStore
            page = PageStore(publication.paper).load(payload["page_idx"])
            image = page.crop(imaging.to_pixels(payload["bbox"], page.size))
            figure = _store_image(publication, image)
        elif request.content_type == "multipart/form-data":
            upload = request.FILES.get("image")
            if not upload or set(request.FILES) != {"image"} or upload.size > MAX_UPLOAD_BYTES:
                raise SolutionError("请上传一张不超过 16 MiB 的图片")
            raw = upload.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise SolutionError("解析图片超过 16 MiB", 413)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in {"PNG", "JPEG", "WEBP"}:
                        raise SolutionError("请使用 PNG、JPEG 或 WebP 图片")
                    if image.width * image.height > MAX_PIXELS:
                        raise SolutionError("图片尺寸过大，请缩小后上传")
                    figure = _store_image(publication, image)
        else:
            raise SolutionError("上传格式不正确", 415)
        return JsonResponse({"figure": figure}, status=201)
    except SolutionError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return JsonResponse({"error": "图片无法读取或保存，请重新添加，原解析保留"}, status=409)


def image_view(request, publication_id, asset_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        _spec, data = _asset(publication_id, str(asset_id))
        response = HttpResponse(data, content_type="image/png")
        response["Cache-Control"] = "no-store"
        return response
    except SolutionError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
