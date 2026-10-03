"""Explicitly continue missing automatic cuts without replacing saved cards."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

from django.conf import settings
from django.db import models, transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404

from . import demo, imaging, intake, mineru, readers, source_images
from .models import Paper, Question, RegionRead


ACTIVE = {Paper.Status.QUEUED, Paper.Status.PARSING, Paper.Status.SEGMENTING, Paper.Status.READING}


class ContinueCutConflict(ValueError):
    def __init__(self, message, reason):
        super().__init__(message)
        self.reason = reason


def _check_original(paper):
    """The queue must still have its real original/render, not just old crops."""
    if paper.kind not in {"pdf", "image", "docx"}:
        raise ContinueCutConflict("这份资料的原文件格式不能继续自动切题。", "unsupported_source")
    source = Path(paper.render_path or paper.source_path)
    try:
        if not (paper.render_path or paper.source_path) or not source.is_file() or source.stat().st_size < 1:
            raise OSError("Missing original")
        if paper.source_path and not Path(paper.source_path).is_file():
            raise OSError("Missing uploaded original")
        if paper.kind == "docx" and not paper.render_path:
            with ZipFile(source) as document:
                if "word/document.xml" not in document.namelist():
                    raise ValueError("Missing document")
        else:
            actual_pages = imaging.page_sizes(source, "pdf" if source.suffix.lower() == ".pdf" else paper.kind)
            if not actual_pages or (paper.pages and actual_pages != paper.pages):
                raise ValueError("Changed original pages")
        _source, _kind, digest = source_images.source_identity(paper)
        stored_digest = (paper.processing_plan or {}).get("render_sha256")
        # A legitimate photo reorder updates card coordinates but older builds
        # did not update render_sha256. Compare that intake evidence for ordinary
        # originals; a fresh continuation digest fences photos from queue onward.
        if stored_digest and not paper.photos and stored_digest != digest:
            raise ValueError("Changed original bytes")
        return digest
    except Exception as exc:
        raise ContinueCutConflict("原文件或已保存的原页缺失、损坏或发生变化，请先恢复原件再继续；已有题目已保留。", "missing_source") from exc


def _cancellation_file(paper):
    root = Path(settings.DATA_ROOT).resolve()
    folder = root / str(paper.pk)
    # Never follow a changed/junction task directory to another user's files.
    if folder.resolve() != folder:
        raise ContinueCutConflict("原卷目录发生变化，请检查原件后再继续。", "unsafe_source")
    target = folder / mineru.CANCEL_FILE
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ContinueCutConflict("停止标记无法安全清除，已有成果已保留。", "cancel_unavailable")
    return target


def continue_ai_cut(request, paper_id):
    from .views import _body, _guard, paper_json

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None or set(payload) - {"revision", "allow_cloud", "expected_mode"}:
        return JsonResponse({"error": "请求内容不正确"}, status=400)
    revision = payload.get("revision")
    if type(revision) is not int or revision < 0:
        return JsonResponse({"error": "请提供当前原卷的整数版本"}, status=400)
    if "allow_cloud" in payload and type(payload["allow_cloud"]) is not bool:
        return JsonResponse({"error": "自动解析授权格式不正确"}, status=400)
    expected_mode = payload.get("expected_mode")
    if "expected_mode" in payload and (type(expected_mode) is not str or expected_mode not in intake.MODES):
        return JsonResponse({"error": "原卷处理方式格式不正确"}, status=400)

    cancel = None
    previous_cancel = None
    cancellation_removed = False
    try:
        with transaction.atomic():
            paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
            plan = deepcopy(paper.processing_plan or {})
            current_revision = plan.get("revision", 0)
            mode = plan.get("mode", "mineru")
            if type(current_revision) is not int or current_revision < 0 or type(mode) is not str or mode not in intake.MODES:
                raise ContinueCutConflict("原卷处理记录不完整，请刷新或恢复原件后再继续。", "invalid_plan")
            if revision != current_revision or (expected_mode is not None and expected_mode != mode):
                raise ContinueCutConflict("原卷处理方式已发生变化，请刷新后再继续。", "stale_revision")
            if paper.archived:
                raise ContinueCutConflict("请先恢复已归档的资料，再继续自动切题。", "archived")
            if demo.is_demo(paper):
                raise ContinueCutConflict("示例资料用于练习审核；请使用自己的原卷继续自动切题。", "demo")
            if mode == "mineru" and paper.status in ACTIVE:
                return JsonResponse({"paper": paper_json(paper), "changed": False,
                    "action": "already_running", "reuses_saved_parse": paper.blocks.exists(),
                    "requires_cloud": False, "message": "自动切题任务已在处理中，可查看当前进度。"})
            if paper.status not in {Paper.Status.READY, Paper.Status.FAILED}:
                raise ContinueCutConflict("请先停止当前处理并确认资料结构，再继续自动切题。", "active_or_ambiguous")
            structure = paper.structure or {}
            if ((structure.get("signals") or len(structure.get("suggested_groups") or []) > 1
                    or plan.get("native_numbering_fallback"))
                    and not structure.get("confirmed") and paper.material_type == Paper.MaterialType.EXAM):
                raise ContinueCutConflict("原卷可能含多套题号，请先确认或拆分资料结构，再继续自动切题。", "needs_grouping")
            if structure.get("groups_need_rebuild"):
                raise ContinueCutConflict("题组结构仍需重新确认；为保留已有题目的来源，本次未继续自动切题。", "needs_grouping")
            if paper.material_type == Paper.MaterialType.BOOK:
                groups = list(paper.question_groups.all())
                if len(groups) > 1 and any(type((group.metadata or {}).get("seq_start")) is not int
                        or type((group.metadata or {}).get("seq_end")) is not int for group in groups):
                    raise ContinueCutConflict("这份教材的既有题组来源范围不明确，不能安全补切；请继续手工切题。", "ambiguous_book_groups")
            if (paper.photos or {}).get("check") and not (paper.photos or {}).get("manual"):
                raise ContinueCutConflict("请先确认照片页序，再继续自动切题；已有裁剪范围已保留。", "photo_order")
            if paper.questions.filter(models.Q(ocr_pending=True) | models.Q(reread_requested=True)
                    | models.Q(state=Question.State.READING, processing_mode="manual")).exists() \
                    or RegionRead.objects.filter(question__paper=paper,
                        status__in=[RegionRead.Status.QUEUED, RegionRead.Status.RUNNING]).exists():
                raise ContinueCutConflict("仍有题目或框选区域正在识读，请完成或停止这些识读后再继续自动切题。", "active_read")
            original_digest = _check_original(paper)
            has_blocks = paper.blocks.exists()
            if not has_blocks:
                # A ZIP's mere presence cannot prove the worker will avoid an
                # upload. Keep the explicit authorization even when it may reuse
                # a valid archive; do not promise reuse of an old remote batch.
                if payload.get("allow_cloud") is not True:
                    raise ContinueCutConflict("没有可直接切题的解析结果；继续可能重新提交原卷，请明确允许本次自动解析。", "cloud_authorization")
                if not readers.configured("mineru"):
                    raise ContinueCutConflict("请先在“设置 → 读题服务”配置 MinerU Token，再继续自动切题。", "missing_mineru")
            existing_ids = list(Question.all_objects.filter(paper=paper).values_list("pk", flat=True))
            cancel = _cancellation_file(paper)
            previous_cancel = cancel.read_bytes() if cancel.exists() else None
            plan.update(schema=1, mode="mineru", requested_mode="mineru", revision=current_revision + 1,
                auto_fallback=False, cloud_authorized=not has_blocks,
                continue_preserve_existing=True, continue_revision=current_revision + 1,
                continue_existing_question_ids=existing_ids, continue_render_sha256=original_digest)
            recorded = {item["page_idx"]: item for item in plan.get("pages", [])
                if isinstance(item, dict) and type(item.get("page_idx")) is int}
            plan["pages"] = [{**recorded.get(item["page_idx"], {}), "page_idx": item["page_idx"], "mode": "mineru"}
                for item in paper.pages]
            paper.processing_plan = plan
            paper.status = Paper.Status.SEGMENTING if has_blocks else Paper.Status.QUEUED
            paper.error = ""
            paper.save(update_fields=["processing_plan", "status", "error", "updated_at"])
            # Clear only after storing the new generation under the paper lock.
            # Late old writes still fail their revision checks after commit.
            cancel.unlink(missing_ok=True)
            cancellation_removed = previous_cancel is not None
    except ContinueCutConflict as exc:
        paper = get_object_or_404(Paper, pk=paper_id)
        return JsonResponse({"error": str(exc), "reason": exc.reason, "paper": paper_json(paper)}, status=409)
    except OSError:
        paper = get_object_or_404(Paper, pk=paper_id)
        return JsonResponse({"error": "停止标记无法安全清除，处理未继续；已有成果已保留。",
            "reason": "cancel_unavailable", "paper": paper_json(paper)}, status=409)
    except Exception:
        if cancellation_removed and cancel is not None and previous_cancel is not None:
            cancel.write_bytes(previous_cancel)
        raise
    return JsonResponse({"paper": paper_json(paper), "changed": True,
        "action": "local_segmentation" if has_blocks else "queued_mineru",
        "reuses_saved_parse": has_blocks, "requires_cloud": not has_blocks,
        "message": "已用保存的解析结果继续切题；已有题目会保留，只补充尚未切出的题目。" if has_blocks else
            "已继续自动切题；本机会优先使用有效解析缓存，否则重新提交原卷，可能使用 MinerU 额度。已有题目会保留。"})
