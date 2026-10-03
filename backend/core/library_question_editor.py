"""Human edits of a current library question, published as a new snapshot.

The library entry is never used as a mutable OCR card. A save locks its source
card, verifies that no unpublished work exists, and publishes the edited card
in the same transaction. Existing solution overlays remain on their versions.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re

from django.conf import settings
from django.db import OperationalError, transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import library, prose, qtypes, source_images
from .figure_policy import CONFIRMED_NO_FIGURE, OK, candidate_key, cue_matches, figure_flag
from .models import Paper, PublishedQuestion, Question
from .textnorm import fix_symbols


class EditorError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _text(value, label, maximum):
    if not isinstance(value, str) or any(
        (ord(c) < 32 and c not in "\r\n\t") or 0xD800 <= ord(c) <= 0xDFFF for c in value
    ) or len(value.encode("utf-16-le")) // 2 > maximum:
        raise EditorError(f"{label}应为不超过 {maximum} 字的文字")
    return value.strip()


def _current_reason(publication, question):
    if publication.status != PublishedQuestion.Status.PUBLISHED:
        return "这道入库题已撤回或被新版替代，请打开当前版本再修改"
    if question is None:
        return "来源题卡已删除或放入回收站，请先恢复来源题卡"
    latest = question.publications.order_by("-version").first()
    if latest is None or latest.pk != publication.pk:
        return "题库已有更新的版本，请重新打开后修改"
    if question.reread_requested or question.ocr_pending or question.state not in library.REVIEWABLE_STATES:
        return "来源题正在识读或尚未完成审核，请先处理来源题卡"
    if not library.approval_is_current(question) or library.content_hash(library.final_content(question)) != publication.content_hash:
        return "来源题卡有尚未入库的修改，请先处理来源题卡，未覆盖任何内容"
    return ""


def editor_data(publication):
    question = Question.objects.select_related("paper", "group").filter(pk=publication.question_id).first()
    reason = _current_reason(publication, question)
    content = publication.content or {}
    return {
        "ok": True, "publication": library.publication_json(publication),
        "revision": question.content_revision if question else None,
        "content_hash": publication.content_hash,
        "body_mode": content.get("body_mode", "text"),
        "stem": content.get("stem", ""), "options": deepcopy(content.get("options") or {}),
        "question_type": publication.question_type,
        "figures": deepcopy(content.get("figures") or []),
        "editable": not bool(reason), "reason": reason,
    }


def _figures(publication, question, payload):
    originals = publication.content.get("figures") or []
    if "figures" not in payload:
        if (publication.content or {}).get("body_mode") == "source_image":
            # Old image-body cards may still hold unused OCR figure metadata.
            # The published complete crop, not those hidden boxes, is edited.
            return [], []
        return deepcopy(question.figures), list(range(len(originals)))
    requested = payload["figures"]
    if not isinstance(requested, list) or len(requested) > len(originals):
        raise EditorError("配图只能选择这道入库题已有的图片")
    files = {figure.get("file"): index for index, figure in enumerate(originals)}
    seen, result, indices = set(), [], []
    for value in requested:
        if not isinstance(value, dict) or set(value) != {"file", "slot"} or not isinstance(value["file"], str) \
                or not isinstance(value["slot"], str) or value["slot"] not in {"stem", *library.OPTION_KEYS}:
            raise EditorError("配图应提供已有图片 file 和题干或选项 slot")
        name = value["file"]
        if name not in files or name in seen:
            raise EditorError("配图不属于这道题，或同一张图片被重复添加")
        seen.add(name)
        index = files[name]
        if index >= len(question.figures):
            raise EditorError("来源配图已变化，请重新打开后修改", 409)
        figure = deepcopy(question.figures[index])
        # Geometry, provenance, cross-page pieces and label offsets stay intact.
        figure["slot"] = value["slot"]
        result.append(figure)
        indices.append(index)
    return result, indices


def _verify_retained_assets(publication, question, indices):
    root = Path(settings.DATA_ROOT)
    folder = root / "library" / str(publication.pk)

    def check_path(path):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) \
                or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
            raise EditorError("配图目录异常，未覆盖原题", 409)

    for index in indices:
        name = (publication.content.get("figures") or [])[index].get("file", "")
        if not isinstance(name, str) or not re.fullmatch(r"figure-\d{1,4}\.png", name):
            raise EditorError("入库配图文件不正确，请从来源题卡重新设置配图", 409)
        stored = folder / name
        check_path(stored)
        # A missing cache is recreated by figure_file; validate its directory
        # before that helper can write a newly cropped image.
        check_path(root / str(question.paper_id) / "figures")
        original = library.figure_file(question, index)
        check_path(original)
        if hashlib.sha256(stored.read_bytes()).digest() != hashlib.sha256(original.read_bytes()).digest():
            raise EditorError("来源配图已变化，请先核对来源题卡，未覆盖原题", 409)


def _review_figures(question, previous_figures, now):
    if question.figures == previous_figures and question.figure_review.get("source") == "human":
        # A no-op does not change the confirmed-no-figure identity timestamp.
        return
    available = {key for figure in question.figure_candidates or [] if (key := candidate_key(figure))}
    selected = set()
    for figure in question.figures:
        for part in [figure, *(figure.get("parts") or [])]:
            key = part.get("candidate_key") or candidate_key(part)
            if key in available:
                selected.add(key)
    ignored = sorted(available - selected)
    question.figure_review = {
        "status": OK if question.figures else CONFIRMED_NO_FIGURE, "source": "human",
        "reason": "题库编辑时人工确认配图" if question.figures else "题库编辑时人工确认无图",
        "signals": ["manual_figure"] if question.figures else ["human_confirmed_no_figure"],
        "cue_matches": cue_matches(question.stem, question.options),
        "ignored_candidates": ignored, "excluded_count": len(ignored), "confirmed_at": now.isoformat(),
    }


def save(publication, payload):
    allowed = {"revision", "content_hash", "stem", "options", "question_type", "body_mode", "figures"}
    required = {"revision", "content_hash", "stem", "options", "question_type"}
    if not isinstance(payload, dict) or set(payload) - allowed or not required <= set(payload):
        raise EditorError("请提供原题版本、题干、选项和题型；答案解析请在解析编辑器修改")
    if type(payload["revision"]) is not int or payload["revision"] < 0 \
            or not isinstance(payload["content_hash"], str) or not re.fullmatch(r"[a-f0-9]{64}", payload["content_hash"]):
        raise EditorError("原题版本不正确，请重新打开题目")
    stem = _text(payload["stem"], "题干", 20000)
    options = payload["options"]
    if not isinstance(options, dict) or set(options) - set(library.OPTION_KEYS):
        raise EditorError("选项只能使用 A、B、C、D、E")
    options = {key: _text(value, "选项", 4000) for key, value in options.items()}
    kind = payload["question_type"]
    if not isinstance(kind, str) or kind not in qtypes.DECIDED_TYPES:
        raise EditorError("请选择单选、多选、填空、判断或解答题")
    mode = payload.get("body_mode", (publication.content or {}).get("body_mode", "text"))
    if not isinstance(mode, str) or mode not in {"text", "source_image"} or (mode == "text" and not stem):
        raise EditorError("文字题的题干不能为空，原图题请保留原图正文形式")

    # Match publish's lock order. An OCR completion or a second editor cannot
    # slip between validating the source and publishing the human edit.
    with transaction.atomic():
        try:
            paper = Paper.objects.select_for_update().get(pk=publication.paper_id)
            question = Question.objects.select_for_update().select_related("paper", "group").get(
                pk=publication.question_id, paper=paper,
            )
            publication = PublishedQuestion.objects.select_for_update().get(pk=publication.pk)
        except (Paper.DoesNotExist, Question.DoesNotExist):
            raise EditorError("来源题卡已删除或放入回收站，请先恢复来源题卡", 409) from None
        reason = _current_reason(publication, question)
        if reason:
            raise EditorError(reason, 409)
        if payload["revision"] != question.content_revision or payload["content_hash"] != publication.content_hash:
            raise EditorError("题目已在其他窗口修改，请重新载入；本次文字未覆盖原题", 409)
        figures, indices = _figures(publication, question, payload)
        if mode == "source_image" and figures:
            raise EditorError("完整原图正文已经包含配图，不应另加重复的配图")
        _verify_retained_assets(publication, question, indices)
        previous_figures = deepcopy(question.figures)
        tidied = prose.tidy_fields({
            "stem": fix_symbols(stem), "options": {key: fix_symbols(value) for key, value in options.items() if value},
            "question_type": kind,
        }, origin=question.origin)
        question.stem, question.options = tidied["stem"].strip(), tidied["options"]
        # The explicitly selected type wins over a printed '(多选)' prefix.
        question.question_type = kind
        question.origin = tidied["origin"]
        question.body_mode, question.figures = mode, figures
        if mode == "text" and not question.stem:
            raise EditorError("整理格式后的题干不能为空")
        if mode == "source_image" and not source_images.body_valid(question):
            raise EditorError("原卷裁片已缺失，请先修复来源题卡", 409)
        now = timezone.now()
        _review_figures(question, previous_figures, now)
        question.flags = [flag for flag in question.flags or [] if "截图" in flag and not figure_flag(flag)]
        question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
        question.error, question.text_source = "", "human"
        question.edited, question.type_locked = True, True
        question.reread_requested, question.ocr_pending, question.ocr_suggestion = False, False, {}
        question.content_revision += 1
        library.approve(question, now=now, source="human")
        question.save()
        updated, created = library.publish(question)
        return updated, created, question.content_revision


@csrf_exempt
def question_editor(request, publication_id):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    from .views import _body, _guard
    if request.method == "POST":
        rejected = _guard(request)
        if rejected is not None:
            return rejected
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        if request.method == "GET":
            return JsonResponse(editor_data(publication))
        publication, created, revision = save(publication, _body(request))
        return JsonResponse({"ok": True, "created": created, "publication": library.publication_json(publication),
                             "revision": revision, "content_hash": publication.content_hash}, status=201 if created else 200)
    except EditorError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except (OSError, ValueError, OperationalError):
        # Do not expose paths, and do not partially save a card if publishing
        # its assets fails. The exception leaves save's atomic block first.
        return JsonResponse({"error": "保存未完成，原题和历史版本均保留；请重新载入或修复来源图片后再试"}, status=409)
