"""Versioned solution overlays and private local assets, never OCR replacements."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import uuid
import warnings
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.db import OperationalError, transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from PIL import Image, ImageOps

from . import imaging, library, library_jobs, qtypes
from .models import LibrarySolution, PublishedQuestion

MAX_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_FIGURES = 24
MAX_BATCH_ITEMS = 500
MAX_BATCH_HISTORY = 50
OBJECTIVE_ANSWER_TYPES = frozenset({"single_choice", "multiple_choice", "fill_blank", "true_false"})
_BATCH_LOCK = threading.RLock()


class SolutionError(ValueError):
    def __init__(self, message, status=400, *, errors=None):
        super().__init__(message)
        self.status = status
        self.errors = list(errors) if errors is not None else None


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
    if any(part.is_symlink() for part in (folder.parent, folder)) or not folder.resolve().is_relative_to(
        root.resolve()
    ):
        raise SolutionError("解析图片目录异常，未访问原文件", 409)
    return folder


def _asset_spec(publication_id, asset_id):
    key = identity(asset_id, "解析图片")
    folder = _folder(publication_id)
    target, metadata = folder / (key + ".png"), folder / (key + ".json")
    try:
        if (
            target.is_symlink()
            or metadata.is_symlink()
            or target.stat().st_size > MAX_UPLOAD_BYTES
            or metadata.stat().st_size > 2000
        ):
            raise ValueError
        spec = json.loads(metadata.read_text(encoding="utf-8"))
        if (
            not isinstance(spec, dict)
            or spec.get("id") != key
            or spec.get("publication_id") != str(publication_id)
            or not isinstance(spec.get("sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", spec["sha256"])
            or type(spec.get("width")) is not int
            or type(spec.get("height")) is not int
            or not spec["width"] >= 1
            or not spec["height"] >= 1
            or spec["width"] * spec["height"] > MAX_PIXELS
        ):
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
                if (
                    image.format != "PNG"
                    or image.width * image.height > MAX_PIXELS
                    or [image.width, image.height] != [spec.get("width"), spec.get("height")]
                ):
                    raise ValueError
                image.load()
        return spec, data
    except (OSError, ValueError, KeyError, SyntaxError, Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise SolutionError("解析图片缺失或校验失败，请重新添加，原解析未被覆盖", 409) from None


def _adopt_review_asset(publication, asset_id):
    """题面一改，题目就换了新一版，解析图跟着留在旧版的目录里。界面上那个
    「填回编辑区」把它带回来时，存图只认当前版本目录，不搬一份就是 409「图片
    缺失或校验失败」，老师看到的是自己刚点了一下就红了。这里按原字节复制一份到
    当前版本，再交回 _asset 走一遍同样的校验。只认 extras 里自己记下的那一条旧
    解析，不认任何别的来源。"""
    review_id = (publication.extras or {}).get("solution_needs_review_id")
    if not review_id:
        return None
    try:
        older = LibrarySolution.objects.filter(pk=identity(review_id)).first()
    except SolutionError:
        return None
    if older is None:
        return None
    figure = next((item for item in older.figures if isinstance(item, dict) and item.get("id") == str(asset_id)), None)
    if figure is None:
        return None
    spec, data = _asset(older.publication_id, figure["id"])
    folder = _folder(publication.pk)
    folder.mkdir(parents=True, exist_ok=True)
    spec = {**spec, "publication_id": str(publication.pk)}
    (folder / (str(asset_id) + ".png")).write_bytes(data)
    (folder / (str(asset_id) + ".json")).write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return _asset(publication.pk, asset_id)


def _adopt_previous_publication_asset(publication, asset_id):
    """当用户在统一编辑工作台同时上传解析图并修改题干/选项时，题面会发布为新版
    PublishedQuestion；刚上传到上一版目录、尚未进入新版目录的新解析图需要安全迁移。"""
    if not publication.question_id:
        return None
    siblings = PublishedQuestion.objects.filter(
        question_id=publication.question_id, version__lt=publication.version
    ).order_by("-version")[:3]
    for older in siblings:
        try:
            spec, data = _asset(older.pk, asset_id)
        except SolutionError:
            continue
        folder = _folder(publication.pk)
        folder.mkdir(parents=True, exist_ok=True)
        spec = {**spec, "publication_id": str(publication.pk)}
        (folder / (str(asset_id) + ".png")).write_bytes(data)
        (folder / (str(asset_id) + ".json")).write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return _asset(publication.pk, asset_id)
    return None


def _figure_json(publication_id, value):
    # Listing hundreds of library entries or historical versions must not
    # decode every full image. Retrieval, save and export verify actual bytes.
    spec, _target = _asset_spec(publication_id, value["id"])
    return {
        **deepcopy(spec),
        **deepcopy(value),
        "url": f"/api/library/{publication_id}/solution-images/{spec['id']}?v={spec['sha256'][:16]}",
    }


def solution_json(solution, *, fingerprint=None):
    return {
        "id": str(solution.pk),
        "publication_id": str(solution.publication_id),
        "parent_id": str(solution.parent_id) if solution.parent_id else None,
        "fingerprint": solution.fingerprint,
        "answer": solution.answer,
        "analysis": solution.analysis,
        "figures": [_figure_json(solution.publication_id, item) for item in solution.figures],
        "created_at": solution.created_at.isoformat(),
        "needs_check": solution.fingerprint
        != (fingerprint or library.generation_fingerprint(solution.publication.content, solution.publication_id)),
    }


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
        images.append(
            {
                **deepcopy(figure),
                "slot": "solution",
                "bytes": data,
                "size": (spec["width"], spec["height"]),
                "name": spec["id"],
                "sha256": spec["sha256"],
            }
        )
    return images


def _text(value, name, maximum):
    if (
        not isinstance(value, str)
        or any((ord(c) < 32 and c not in "\n\r\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value)
        or len(value.encode("utf-16-le")) // 2 > maximum
    ):
        raise SolutionError(f"{name}应为不超过 {maximum} 字的文字")
    return value.strip()


def save(publication, payload):
    allowed = {"answer", "analysis", "figures", "base_revision", "sync_library"}
    if (
        not isinstance(payload, dict)
        or set(payload) - allowed
        or not {"answer", "analysis", "base_revision", "sync_library"} <= set(payload)
    ):
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
        if (
            not isinstance(value, dict)
            or set(value) - {"id", "display_width", "position", "paragraph"}
            or "id" not in value
        ):
            raise SolutionError("解析图片位置格式不正确")
        key = identity(value["id"], "解析图片")
        if key in seen:
            raise SolutionError("同一张解析图片不能重复添加")
        seen.add(key)
        try:
            spec, _data = _asset(publication.pk, key)
        except SolutionError:
            # 上一版解析的图（或刚同次改题面新发的上一版暂存图）在旧目录里，按同题安全搬过来。
            adopted = _adopt_review_asset(publication, key) or _adopt_previous_publication_asset(publication, key)
            if adopted is None:
                raise
            spec, _data = adopted
        width = value.get("display_width", min(178, max(20, spec["width"] * 25.4 / 150)))
        position, paragraph = value.get("position", "after"), value.get("paragraph", 0)
        if (
            type(width) not in {float, int}
            or not math.isfinite(width)
            or not 5 <= width <= 178
            or position not in {"before", "after", "paragraph"}
            or type(paragraph) is not int
            or not 0 <= paragraph <= 1000
        ):
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
        row = LibrarySolution.objects.create(
            publication=publication,
            parent=parent,
            fingerprint=library.generation_fingerprint(publication.content, publication.pk),
            answer=answer,
            analysis=analysis,
            figures=normalized,
        )
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
            return JsonResponse(
                {"solution": solution_json(row), "base_revision": (publication.extras or {}).get("solution_id")},
                status=201,
            )
        row = selected(publication, request.GET.get("revision"))
        fingerprint = library.generation_fingerprint(publication.content, publication.pk)
        # 1.13.5：题面一改，题目就换成新一版，原来那份解析留在**旧版**上 ——
        # 那一行属于上一道题，不属于这道，旧版本身又不在题库里。不把它取出来给
        # 界面一条回来的路，老师填过的那份就永远看不见了。这里只读出来显示、允许
        # 填回编辑区；save() 那边的归属校验一个字没松，它仍然不能直接覆盖本题。
        previous = None
        review_id = (publication.extras or {}).get("solution_needs_review_id")
        if review_id and row is None:
            # 这个编号坏掉只该让「旧解析」这一块消失，不能连编辑器一起打不开。
            try:
                older = LibrarySolution.objects.filter(pk=identity(review_id)).select_related("publication").first()
            except SolutionError:
                older = None
            if older is not None:
                try:
                    previous = {
                        **solution_json(older, fingerprint=older.fingerprint),
                        "needs_check": True,
                        "from_version": older.publication.version,
                    }
                except SolutionError as error:
                    previous = {
                        "id": str(older.pk),
                        "answer": older.answer,
                        "analysis": older.analysis,
                        "figures": [],
                        "created_at": older.created_at.isoformat(),
                        "needs_check": True,
                        "from_version": older.publication.version,
                        "asset_error": str(error),
                    }
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
                history.append(
                    {
                        "id": str(item.pk),
                        "publication_id": str(item.publication_id),
                        "answer": item.answer,
                        "analysis": item.analysis,
                        "figures": [],
                        "asset_error": str(error),
                        "created_at": item.created_at.isoformat(),
                        "needs_check": True,
                    }
                )
        return JsonResponse(
            {
                "solution": solution_json(row) if row else None,
                "base_revision": (publication.extras or {}).get("solution_id"),
                "previous": previous,
                "origin": {
                    "answer": str((publication.content or {}).get("answer") or ""),
                    "analysis": str((publication.content or {}).get("analysis") or ""),
                },
                "ai_answer": ai_answer,
                "ai_answer_stale": ai_stale,
                "history": history,
            }
        )
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
    image = ImageOps.exif_transpose(image).convert(
        "RGBA" if "A" in image.getbands() or "transparency" in image.info else "RGB"
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    data = output.getvalue()
    if len(data) > MAX_UPLOAD_BYTES:
        raise SolutionError("解析图片超过 16 MiB，请压缩后重试", 413)
    key = str(uuid.uuid4())
    spec = {
        "id": key,
        "publication_id": str(publication.pk),
        "width": image.width,
        "height": image.height,
        "sha256": hashlib.sha256(data).hexdigest(),
    }
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
    return _figure_json(
        publication.pk,
        {"id": key, "display_width": min(178, max(20, image.width * 25.4 / 150)), "position": "after", "paragraph": 0},
    )


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


# ---------------------------------------------------------------- 批量解析导入与整批撤销


def _batch_store_path() -> Path:
    # 独立放在 DATA_ROOT 根目录，避免混入 library-solutions/ 资产目录触发传输或校验异常
    return (Path(settings.DATA_ROOT) / "library-solution-batches.json").resolve()


def _load_batches_unlocked() -> list[dict]:
    path = _batch_store_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("batch_id")]


def _save_batches_unlocked(records: list[dict]) -> None:
    path = _batch_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(records[-MAX_BATCH_HISTORY:], ensure_ascii=False, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix="library-solution-batches-",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            stream.write(payload)
        temp_path.replace(path)
    except OSError as exc:
        raise SolutionError("解析批次记录暂时无法写入磁盘", 500) from exc
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def record_batch(agent: str, note: str, entries: list[dict]) -> str | None:
    """持久化一次批量解析写入记录，供后续整批撤销。"""
    if not entries:
        return None
    batch_id = str(uuid.uuid4())
    clean_agent = " ".join(str(agent or "老师").split()).strip()[:120] or "老师"
    clean_note = " ".join(str(note or "").split()).strip()[:200]
    record = {
        "batch_id": batch_id,
        "agent": clean_agent,
        "note": clean_note,
        "count": len(entries),
        "created_at": timezone.now().isoformat(),
        "undone_at": None,
        "entries": entries,
    }
    with _BATCH_LOCK:
        records = _load_batches_unlocked()
        records.append(record)
        _save_batches_unlocked(records)
    return batch_id


def _batch_agent(raw) -> str:
    if raw is None:
        return "老师"
    if not isinstance(raw, str):
        raise SolutionError("助手或操作者名称必须是文字")
    agent = " ".join(raw.split()).strip()
    if not agent or len(agent) > 120:
        raise SolutionError("助手或操作者名称需为 1–120 个字符")
    return agent


def _batch_note(raw) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise SolutionError("批次说明必须是文字")
    note = " ".join(raw.split()).strip()
    if len(note) > 200:
        raise SolutionError("批次说明最多 200 个字符")
    return note


def _type_label(question_type: str) -> str:
    return qtypes.TYPE_LABELS.get(question_type, question_type or "未定")


def _resolve_batch_targets(
    items: list,
) -> tuple[dict[str, PublishedQuestion], dict[int, tuple[str | None, str | None]]]:
    """一次批量解析 UUID，其余短码/定位串回落到 resolve_publication_ref。"""
    exact_uuids: list[uuid.UUID] = []
    parsed_refs: dict[int, str] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        raw_ref = item.get("publication_id")
        if raw_ref is None:
            raw_ref = item.get("id")
        if not isinstance(raw_ref, str) or not raw_ref.strip():
            continue
        ref = raw_ref.strip()
        parsed_refs[index] = ref
        try:
            exact_uuids.append(uuid.UUID(ref))
        except ValueError:
            continue

    by_id: dict[str, PublishedQuestion] = {}
    if exact_uuids:
        for pub in PublishedQuestion.objects.filter(pk__in=set(exact_uuids)):
            by_id[str(pub.pk)] = pub

    resolved: dict[int, tuple[str | None, str | None]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            resolved[index] = (None, "条目必须是 JSON 对象")
            continue
        target_ref = parsed_refs.get(index)
        if not target_ref:
            resolved[index] = (None, "缺少 publication_id（支持 UUID 或 Q-xxxxxxxx 短码）")
            continue
        try:
            canonical = str(uuid.UUID(target_ref))
        except ValueError:
            canonical = None
        if canonical is not None:
            pub = by_id.get(canonical)
            if pub is None:
                resolved[index] = (None, f"找不到编号为 {canonical} 的入库题目")
            else:
                resolved[index] = (canonical, None)
            continue
        try:
            pub = library.resolve_publication_ref(target_ref)
        except ValueError as error:
            resolved[index] = (None, str(error))
            continue
        pid = str(pub.pk)
        by_id[pid] = pub
        resolved[index] = (pid, None)
    return by_id, resolved


def batch_save(payload: dict) -> dict:
    """批量将标准答案与解析写入正式题库解析层（LibrarySolution），支持干跑与整批撤销。"""
    if not isinstance(payload, dict):
        raise SolutionError("批量导入请求必须是 JSON 对象")
    items = payload.get("items")
    if not isinstance(items, list) or not items or len(items) > MAX_BATCH_ITEMS:
        raise SolutionError(f"items 须为 1–{MAX_BATCH_ITEMS} 条的非空列表")
    overwrite = payload.get("overwrite", False)
    dry_run = payload.get("dry_run", False)
    if type(overwrite) is not bool:
        raise SolutionError("overwrite 只能是 true 或 false")
    if type(dry_run) is not bool:
        raise SolutionError("dry_run 只能是 true 或 false")
    agent = _batch_agent(payload.get("agent"))
    note = _batch_note(payload.get("note"))

    by_id, resolved = _resolve_batch_targets(items)
    seen_ids: set[str] = set()
    validated: list[dict] = []
    errors: list[dict] = []
    warnings: list[str] = []
    results: list[dict] = []
    analysis_only_count = 0
    would_create = would_overwrite = would_skip_existing = 0

    for index, item in enumerate(items):
        pid, resolve_err = resolved[index]
        raw_ref = item.get("publication_id") or item.get("id") if isinstance(item, dict) else ""
        if resolve_err is not None or pid is None:
            msg = resolve_err or "无法识别的题目编号"
            errors.append({"index": index, "publication_id": raw_ref, "error": msg})
            results.append({"index": index, "publication_id": raw_ref, "status": "invalid", "error": msg})
            continue
        pub = by_id[pid]
        if pid in seen_ids:
            msg = f"同一批里重复出现了第 {pub.number} 题（{pid}）"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue
        seen_ids.add(pid)
        if pub.status != PublishedQuestion.Status.PUBLISHED:
            msg = f"第 {pub.number} 题已撤回或被新版替代"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue

        raw_answer = item.get("answer")
        raw_analysis = item.get("analysis")
        if raw_answer is not None and not isinstance(raw_answer, str):
            msg = f"第 {pub.number} 题的 answer 必须是文字"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue
        if raw_analysis is not None and not isinstance(raw_analysis, str):
            msg = f"第 {pub.number} 题的 analysis 必须是文字"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue
        try:
            answer = _text(raw_answer or "", "答案", 8000)
            analysis = _text(raw_analysis or "", "解析", 100000)
        except SolutionError as error:
            msg = f"第 {pub.number} 题：{error}"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue
        if not answer and not analysis:
            msg = f"第 {pub.number} 题的答案和解析不能同时为空"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue
        if library_jobs.unpaired_dollar(answer) or library_jobs.unpaired_dollar(analysis):
            msg = f"第 {pub.number} 题的公式存在未配对的 $"
            errors.append({"index": index, "publication_id": pid, "error": msg})
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": "invalid",
                    "error": msg,
                }
            )
            continue

        if not answer and analysis:
            analysis_only_count += 1
            if pub.question_type in OBJECTIVE_ANSWER_TYPES:
                warnings.append(f"第 {pub.number} 题（{_type_label(pub.question_type)}）仅填了解析未填答案")

        has_existing = library.has_saved_solution(pub.extras)
        if has_existing and not overwrite:
            status = "would_skip_existing"
            would_skip_existing += 1
        elif has_existing:
            status = "would_overwrite"
            would_overwrite += 1
        else:
            status = "would_create"
            would_create += 1
        validated.append({"index": index, "publication_id": pid, "answer": answer, "analysis": analysis})
        if dry_run:
            results.append(
                {
                    "index": index,
                    "publication_id": pid,
                    "number": pub.number,
                    "source_filename": pub.source_filename,
                    "status": status,
                }
            )

    if dry_run:
        return {
            "ok": not errors,
            "dry_run": True,
            "total": len(items),
            "would_create": would_create,
            "would_overwrite": would_overwrite,
            "would_skip_existing": would_skip_existing,
            "invalid": len(errors),
            "analysis_only_count": analysis_only_count,
            "warnings": warnings,
            "errors": errors,
            "results": results,
        }

    if errors:
        raise SolutionError(f"有 {len(errors)} 条校验未通过，未写入任何题目：{errors[0]['error']}", 400, errors=errors)

    created_count = overwritten_count = skipped_count = 0
    batch_entries: list[dict] = []
    commit_results: list[dict] = []
    target_ids = [item["publication_id"] for item in validated]
    with transaction.atomic():
        locked = {str(pub.pk): pub for pub in PublishedQuestion.objects.select_for_update().filter(pk__in=target_ids)}
        for item in validated:
            locked_pub = locked.get(item["publication_id"])
            if locked_pub is None or locked_pub.status != PublishedQuestion.Status.PUBLISHED:
                raise SolutionError("写入期间有题目已撤回或被新版替代，整批未写入", 409)
            extras = deepcopy(locked_pub.extras or {})
            prev_sol_id = str(extras.get("solution_id") or "").strip() or None
            prev_review_id = str(extras.get("solution_needs_review_id") or "").strip() or None
            if prev_sol_id and not overwrite:
                skipped_count += 1
                commit_results.append(
                    {
                        "index": item["index"],
                        "publication_id": item["publication_id"],
                        "number": locked_pub.number,
                        "source_filename": locked_pub.source_filename,
                        "status": "skipped_existing",
                    }
                )
                continue
            parent_row = None
            if prev_sol_id:
                try:
                    parent_row = LibrarySolution.objects.filter(
                        pk=uuid.UUID(prev_sol_id), publication=locked_pub
                    ).first()
                except ValueError:
                    parent_row = None
            row = LibrarySolution.objects.create(
                publication=locked_pub,
                parent=parent_row,
                fingerprint=library.generation_fingerprint(locked_pub.content, locked_pub.pk),
                answer=item["answer"],
                analysis=item["analysis"],
                figures=[],
            )
            extras["solution_id"] = str(row.pk)
            extras.pop("solution_needs_review_id", None)
            library.save_extras(locked_pub, extras)
            status = "overwritten" if prev_sol_id else "created"
            if prev_sol_id:
                overwritten_count += 1
            else:
                created_count += 1
            batch_entries.append(
                {
                    "publication_id": str(locked_pub.pk),
                    "new_solution_id": str(row.pk),
                    "previous_solution_id": prev_sol_id,
                    "previous_needs_review_id": prev_review_id,
                }
            )
            commit_results.append(
                {
                    "index": item["index"],
                    "publication_id": str(locked_pub.pk),
                    "number": locked_pub.number,
                    "source_filename": locked_pub.source_filename,
                    "status": status,
                    "solution_id": str(row.pk),
                }
            )

    batch_id = record_batch(agent, note, batch_entries)
    return {
        "ok": True,
        "dry_run": False,
        "total": len(items),
        "created": created_count,
        "overwritten": overwritten_count,
        "skipped_existing": skipped_count,
        "analysis_only_count": analysis_only_count,
        "warnings": warnings,
        "errors": [],
        "batch_id": batch_id,
        "results": commit_results,
    }


def list_batches(limit: int = 20) -> dict:
    """列出最近的批量解析导入批次摘要。"""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_BATCH_HISTORY:
        raise SolutionError(f"limit 应为 1–{MAX_BATCH_HISTORY}")
    with _BATCH_LOCK:
        records = _load_batches_unlocked()
    ordered = list(reversed(records))
    return {
        "batches": [
            {
                "batch_id": item["batch_id"],
                "agent": item.get("agent", ""),
                "note": item.get("note", ""),
                "count": int(item.get("count") or len(item.get("entries") or [])),
                "created_at": item.get("created_at"),
                "undone_at": item.get("undone_at"),
            }
            for item in ordered[:limit]
        ],
        "total": len(ordered),
    }


def undo_batch(payload: dict | None = None) -> dict:
    """整批撤销指定（或最近一次未撤销的）批量解析导入；若题目后续已被人工另行修改则保护不覆盖。"""
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise SolutionError("撤销请求格式不正确")
    raw_batch_id = payload.get("batch_id")
    target_batch_id = identity(raw_batch_id, "解析批次") if raw_batch_id is not None else None

    with _BATCH_LOCK:
        records = _load_batches_unlocked()
        target_index = None
        if target_batch_id:
            for idx, item in enumerate(records):
                if item.get("batch_id") == target_batch_id:
                    target_index = idx
                    break
            if target_index is None:
                raise SolutionError("找不到指定的批量解析导入批次", 404)
            if records[target_index].get("undone_at"):
                raise SolutionError("这一批已经撤销过了", 409)
        else:
            for idx in range(len(records) - 1, -1, -1):
                if not records[idx].get("undone_at"):
                    target_index = idx
                    break
            if target_index is None:
                raise SolutionError("没有可撤销的批量解析导入记录", 404)

        record = records[target_index]
        entries = list(record.get("entries") or [])
        pub_ids = [item["publication_id"] for item in entries if isinstance(item, dict) and item.get("publication_id")]
        restored = skipped_modified = 0
        with transaction.atomic():
            locked = {str(pub.pk): pub for pub in PublishedQuestion.objects.select_for_update().filter(pk__in=pub_ids)}
            for entry in entries:
                pid = entry.get("publication_id")
                pub = locked.get(pid)
                if pub is None or pub.status != PublishedQuestion.Status.PUBLISHED:
                    skipped_modified += 1
                    continue
                extras = deepcopy(pub.extras or {})
                current_sol_id = str(extras.get("solution_id") or "").strip() or None
                if current_sol_id != entry.get("new_solution_id"):
                    skipped_modified += 1
                    continue
                prev_sol_id = entry.get("previous_solution_id")
                if prev_sol_id:
                    extras["solution_id"] = prev_sol_id
                else:
                    extras.pop("solution_id", None)
                prev_review_id = entry.get("previous_needs_review_id")
                if prev_review_id:
                    extras["solution_needs_review_id"] = prev_review_id
                else:
                    extras.pop("solution_needs_review_id", None)
                library.save_extras(pub, extras)
                restored += 1

        record["undone_at"] = timezone.now().isoformat()
        records[target_index] = record
        _save_batches_unlocked(records)

    return {
        "ok": True,
        "batch_id": record["batch_id"],
        "restored": restored,
        "skipped_modified": skipped_modified,
        "message": (
            f"已撤销批量解析写入：恢复 {restored} 题"
            + (f"，另有 {skipped_modified} 题因后续已被修改或撤回而跳过" if skipped_modified else "")
            + "。"
        ),
    }


@csrf_exempt
def batch_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "批量解析操作只能在本机使用"}, status=403)
    from .views import _body, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request, limit=8_000_000)
    if payload is None:
        return JsonResponse({"error": "批量解析请求必须是 JSON 对象，且不能超过 8 MB"}, status=400)
    try:
        return JsonResponse(batch_save(payload))
    except SolutionError as error:
        body = {"error": str(error)}
        if error.errors is not None:
            body["errors"] = error.errors
        return JsonResponse(body, status=error.status)
    except OSError:
        return JsonResponse({"error": "解析批次记录没能保存，请检查本机数据目录"}, status=500)
    except OperationalError:
        return JsonResponse({"error": "解析正在被另一个操作保存，请稍后重试"}, status=409)


@csrf_exempt
def batches_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "解析批次记录只能在本机查看"}, status=403)
    try:
        raw_limit = request.GET.get("limit", "20")
        limit = int(raw_limit)
    except (ValueError, TypeError):
        return JsonResponse({"error": "limit 必须是整数"}, status=400)
    try:
        return JsonResponse(list_batches(limit=limit))
    except SolutionError as error:
        return JsonResponse({"error": str(error)}, status=error.status)


@csrf_exempt
def undo_batch_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "批量解析撤销只能在本机使用"}, status=403)
    from .views import _body, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return JsonResponse({"error": "撤销请求必须是 JSON 对象"}, status=400)
    try:
        return JsonResponse(undo_batch(payload))
    except SolutionError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except OSError:
        return JsonResponse({"error": "解析批次记录没能更新，请检查本机数据目录"}, status=500)
    except OperationalError:
        return JsonResponse({"error": "解析正在被另一个操作保存，请稍后重试"}, status=409)
