from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import unicodedata
import uuid
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.db import models, transaction
from django.http import FileResponse, Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from PIL import Image

from . import credential_settings, imaging, import_planning, library, m3import, mineru, photos, preferences, readers
from .figure_policy import (
    BLOCKED_MISSING, CONFIRMED_NO_FIGURE, CONFLICT, FLAG_NO_FIGURE, FLAG_UNCUED_FIGURE,
    FLAG_UNFOUND_FIGURE, OK, blocking_message, blocks_approval, candidate_key as figure_candidate_key,
    cue_matches, figure_flag,
    stored_or_derived_review,
)
from .models import (
    Block, ImportChunk, Paper, PublishedQuestion, Question, QuestionDeletionBatch, QuestionGroup,
)
from .pipeline import PageStore, candidates_in, preview_resegment, reorder_photo_pages
from .textnorm import fix_reading_symbols, fix_symbols

FRONTEND = settings.FRONTEND_ROOT
UPLOAD_KINDS = {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".docx": "docx"}
TYPES = {"single_choice", "multiple_choice", "fill_blank", "free_response", "unknown"}
SLOTS = {"stem", "A", "B", "C", "D"}


# ---------------------------------------------------------------- 工具

def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _guard(request, *, json_body: bool = True):
    """改动必须来自本机题库页面：自定义请求头会让跨站请求被浏览器拦下。"""
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


def _valid_bbox(value) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    x0, y0, x1, y1 = (round(float(v), 1) for v in value)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1000, x1), min(1000, y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    return [x0, y0, x1, y1]


def _valid_label_offset(value) -> dict[str, float] | None:
    """Validate optional, presentation-only figure-label coordinates."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        return None
    coordinates: dict[str, float] = {}
    for key in ("x", "y"):
        number = value.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            return None
        if not -4000 <= float(number) <= 4000:
            return None
        coordinates[key] = round(float(number), 1)
    return coordinates


def _candidate_key(item: dict) -> str | None:
    """Use the same stable page/bbox key as the figure editor."""
    return figure_candidate_key(item)


def _candidate_keys(question: Question) -> set[str]:
    return {
        key for item in (question.figure_candidates or [])
        if (key := _candidate_key(item)) is not None
    }


def _saved_ignored_candidates(question: Question, review: dict | None = None) -> list[str]:
    """Return only still-valid ignored candidate keys from a saved review."""
    available = _candidate_keys(question)
    review = review if isinstance(review, dict) else (
        question.figure_review if isinstance(question.figure_review, dict) else {}
    )
    raw = review.get("ignored_candidates")
    if not isinstance(raw, list):
        return []
    return sorted({value for value in raw if isinstance(value, str) and value in available})


def _selected_candidate_keys(question: Question, figures: list[dict] | None = None) -> set[str]:
    """Return candidate identities represented by selected figure boxes.

    Older automatic figures do not carry ``candidate_key``.  An exact saved
    page/bbox match is still the same candidate; adjusted manual crops retain
    their explicit provenance key.
    """
    available = _candidate_keys(question)
    selected: set[str] = set()
    for figure in figures if isinstance(figures, list) else (question.figures or []):
        if not isinstance(figure, dict):
            continue
        explicit = figure.get("candidate_key")
        if isinstance(explicit, str) and explicit in available:
            selected.add(explicit)
            continue
        exact = _candidate_key(figure)
        if exact in available:
            selected.add(exact)
    return selected


def _unclassified_candidate_details(
    question: Question, *, figures: list[dict] | None = None, ignored_candidates: list[str] | None = None,
) -> list[dict]:
    """Identify the concrete candidates behind ``candidate_unclassified``.

    A candidate already assigned to this question, another numbered question,
    or ``none`` by the primary reader is not an unclassified candidate.  Human
    selection/ignore decisions then remove candidates from the outstanding
    list.  The result is safe to expose to the editor and lets the write path
    prove that a conflict was actually resolved instead of merely hidden.
    """
    readings = [
        value for value in (question.read_a, question.read_b, question.read_c)
        if isinstance(value, dict)
    ]
    primary = next((value for value in readings if "stem" in value or "figures" in value), {})
    assignments = {
        str(label): str(role) for label, role in (primary.get("figures") or {}).items()
    } if isinstance(primary.get("figures"), dict) else {}
    resolved_elsewhere_labels = {
        label for label, role in assignments.items()
        if role == "none" or (role.startswith("q") and role[1:].isdigit())
    }
    selected = _selected_candidate_keys(question, figures)
    ignored = set(ignored_candidates if isinstance(ignored_candidates, list)
                  else _saved_ignored_candidates(question))
    result = []
    for candidate in question.figure_candidates or []:
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label"))
        key = _candidate_key(candidate)
        if key is None or label in resolved_elsewhere_labels or key in selected or key in ignored:
            continue
        result.append({
            "key": key, "label": label, "page_idx": candidate.get("page_idx"),
            "bbox": candidate.get("bbox"),
        })
    return result


def _valid_regions(paper: Paper, value) -> list[dict] | None:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        return None
    pages = {page["page_idx"] for page in paper.pages}
    regions = []
    for item in value:
        if not isinstance(item, dict) or item.get("page_idx") not in pages:
            return None
        bbox = _valid_bbox(item.get("bbox"))
        if bbox is None:
            return None
        regions.append({"page_idx": item["page_idx"], "bbox": bbox})
    return regions


def _file(path: Path, content_type: str):
    if not path.is_file():
        raise Http404()
    return FileResponse(path.open("rb"), content_type=content_type)


def _valid_paper_name(value) -> str | None:
    if not isinstance(value, str):
        return None
    name = value.strip()
    if not name or len(name) > 255:
        return None
    if any(unicodedata.category(char) in {"Cc", "Cs", "Zl", "Zp"} for char in name):
        return None
    return name


# ---------------------------------------------------------------- JSON

_ACTIVE_PAPER_STATUSES = (
    Paper.Status.QUEUED,
    Paper.Status.PARSING,
    Paper.Status.SEGMENTING,
    Paper.Status.READING,
)


def _processing_json(paper: Paper) -> dict | None:
    """Expose only progress that the worker has actually persisted.

    MinerU does not provide a percentage for one submitted file, and local
    segmentation has no stable denominator.  Those phases therefore stay
    explicitly indeterminate instead of manufacturing a percentage or ETA.
    """

    if paper.status not in _ACTIVE_PAPER_STATUSES:
        return None
    now = timezone.now()
    elapsed_seconds = max(0, int((now - paper.created_at).total_seconds()))
    idle_seconds = max(0, int((now - paper.updated_at).total_seconds()))
    progress = {
        "stage": paper.status,
        "stage_label": Paper.Status(paper.status).label,
        # Paper.created_at survives retries and manual re-segmentation.  It is
        # the task creation time, not the start of the current processing run.
        "task_created_at": paper.created_at.isoformat(),
        "last_update_at": paper.updated_at.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "idle_seconds": idle_seconds,
        "determinate": False,
        "completed": None,
        "total": None,
        "unit": "",
    }
    ahead = Paper.objects.filter(
        status__in=_ACTIVE_PAPER_STATUSES,
        created_at__lt=paper.created_at,
    ).exclude(pk=paper.pk).count()
    if paper.status == Paper.Status.QUEUED:
        progress["queue_ahead"] = ahead
    elif ahead and paper.status in (Paper.Status.PARSING, Paper.Status.SEGMENTING):
        # The worker's parse lane sends later papers to MinerU while an
        # earlier one is read; such a paper then waits for its turn.
        progress["queue_ahead"] = ahead
        progress["parsed_ahead"] = True
    if paper.status == Paper.Status.QUEUED:
        pass
    elif paper.status == Paper.Status.PARSING:
        chunks = list(paper.import_chunks.order_by("sequence"))
        if chunks:
            parsed = sum(chunk.status == ImportChunk.Status.PARSED for chunk in chunks)
            active = [
                {
                    "sequence": chunk.sequence,
                    "page_start": chunk.source_page_start,
                    "page_end": chunk.source_page_end,
                }
                for chunk in chunks if chunk.status == ImportChunk.Status.PARSING
            ]
            progress.update({
                "determinate": True,
                "completed": parsed,
                "total": len(chunks),
                "unit": "chunk",
                "chunks": {
                    "parsed": parsed,
                    "parsing": sum(chunk.status == ImportChunk.Status.PARSING for chunk in chunks),
                    "queued": sum(chunk.status == ImportChunk.Status.QUEUED for chunk in chunks),
                    "failed": sum(chunk.status == ImportChunk.Status.FAILED for chunk in chunks),
                    "active_ranges": active,
                },
            })
    elif paper.status == Paper.Status.READING and paper.total:
        progress.update({
            "determinate": True,
            "completed": paper.progress,
            "total": paper.total,
            "unit": "question",
        })
        # A measured estimate, not a promise: the pace between the first and
        # the latest finished card of this run.  Needs a few finished cards.
        finished = paper.questions.exclude(
            state__in=[Question.State.WAITING, Question.State.READING],
        ).aggregate(first=models.Min("updated_at"), last=models.Max("updated_at"), count=models.Count("id"))
        remaining = max(0, paper.total - paper.progress)
        if remaining and finished["count"] >= 4 and finished["first"] and finished["last"]:
            span = (finished["last"] - finished["first"]).total_seconds()
            if span > 0:
                pace = span / (finished["count"] - 1)
                progress["eta_seconds"] = int(min(24 * 3600, remaining * pace))
    return progress


def paper_json(paper: Paper, *, with_counts: bool = True) -> dict:
    info = paper.photos or {}
    quota_paused = (
        paper.status == Paper.Status.FAILED
        and paper.error == readers.TOKEN_PLAN_EXHAUSTED_MESSAGE
    )
    data = {
        "id": str(paper.id), "name": paper.display_name, "filename": paper.filename,
        "original_filename": paper.filename, "kind": paper.kind, "status": paper.status,
        "material_type": paper.material_type, "archived": paper.archived,
        "status_label": "额度不足，已暂停" if quota_paused else Paper.Status(paper.status).label,
        "recoverable_pause": quota_paused,
        "progress": paper.progress, "total": paper.total,
        "trash_count": Question.all_objects.filter(paper=paper, deleted_at__isnull=False).count(),
        "error": paper.error, "notes": [*(info.get("notes") or []), *paper.notes], "pages": paper.pages,
        "structure": paper.structure or {},
        "structure_conflict": paper.status == Paper.Status.NEEDS_GROUPING,
        "suggested_groups": (paper.structure or {}).get("suggested_groups") or [],
        "question_groups": [
            {
                "id": group.pk,
                "title": group.title,
                "sequence": group.sequence,
                "pages": list((group.metadata or {}).get("pages") or []),
            }
            for group in paper.question_groups.order_by("sequence", "id")
        ],
        "pages_version": photos.order_version(info),
        "imported_from_m3": bool(paper.imported_from), "created_at": paper.created_at.isoformat(),
        "processing": _processing_json(paper),
        "photos": {
            "count": len(info.get("files", [])),
            "names": [info["files"][index]["name"] for index in info.get("order", [])],
            # 每一页上印的题号范围 [起, 止]；没找到题号为 null；还没解析完时整个为 null。
            "ranges": [info["ranges"].get(str(index)) for index in info["order"]] if info.get("ranges") else None,
            "check": info.get("check", ""),
            "manual": bool(info.get("manual")),
        } if info else None,
    }
    if with_counts:
        rows = list(paper.questions.select_related("paper", "group"))
        approved_ids = {row.pk for row in rows if library.approval_is_current(row)}
        figure_blocked_ids = {
            row.pk for row in rows if blocks_approval(stored_or_derived_review(row))
        }
        published = PublishedQuestion.objects.filter(paper=paper, status=PublishedQuestion.Status.PUBLISHED)\
            .values("question_id").distinct().count()
        data["counts"] = {
            "total": len(rows),
            "green": sum(1 for r in rows if r.state == Question.State.GREEN and r.pk not in approved_ids
                         and r.pk not in figure_blocked_ids),
            "yellow": sum(1 for r in rows if (r.state == Question.State.YELLOW or r.pk in figure_blocked_ids)
                          and r.pk not in approved_ids),
            "red": sum(1 for r in rows if r.state == Question.State.RED and r.pk not in approved_ids),
            "waiting": sum(1 for r in rows if r.state in (Question.State.WAITING, Question.State.READING)),
            "approved": len(approved_ids),
            "published": published,
        }
    return data


def _reading(value: dict) -> dict:
    value = fix_reading_symbols(value)
    return {k: value.get(k) for k in ("engine", "stem", "options", "error", "witness", "chosen", "objections", "answers",
                                      "spotwise")
            if k in value}


def question_json(question: Question) -> dict:
    figure_review = stored_or_derived_review(question)
    valid_candidate_keys = _candidate_keys(question)
    if isinstance(figure_review, dict) and "ignored_candidates" in figure_review:
        ignored = sorted({
            value for value in figure_review.get("ignored_candidates", [])
            if isinstance(value, str) and value in valid_candidate_keys
        }) if isinstance(figure_review.get("ignored_candidates"), list) else []
        figure_review = {
            **figure_review,
            "ignored_candidates": ignored,
            "excluded_count": len(ignored),
        }
    if (isinstance(figure_review, dict)
            and "candidate_unclassified" in (figure_review.get("signals") or [])):
        details = _unclassified_candidate_details(
            question, ignored_candidates=_saved_ignored_candidates(question, figure_review),
        )
        figure_review = {
            **figure_review,
            "unclassified_candidates": details,
            "unclassified_count": len(details),
        }
    approval_valid = library.approval_is_current(question)
    figures = []
    for index, figure in enumerate(question.figures):
        digest = hashlib.sha1(json.dumps([figure["page_idx"], figure["bbox"]]).encode()).hexdigest()[:10]
        shown_figure = {**figure}
        if shown_figure.get("candidate_key") not in valid_candidate_keys:
            shown_figure.pop("candidate_key", None)
        figures.append({**shown_figure, "url": f"/api/questions/{question.id}/figures/{index}?v={digest}"})
    return {
        "id": question.id, "source_key": str(question.source_key), "number": question.number,
        "group": ({"id": question.group_id, "title": question.group.title,
                   "sequence": question.group.sequence} if question.group_id else None),
        "section": question.section,
        "question_type": question.question_type, "regions": question.regions,
        "regions_changed": question.regions != question.regions_auto, "start_source": question.start_source,
        "source_kind": question.source_kind, "source_anchor_seq": question.source_anchor_seq,
        "figure_candidates": question.figure_candidates, "figures": figures,
        "figure_review": figure_review, "figure_blocked": blocks_approval(figure_review),
        "stem": question.stem, "options": question.options, "text_source": question.text_source,
        "state": question.state, "flags": question.flags, "error": question.error,
        "edited": question.edited, "approved": approval_valid,
        "approval_valid": approval_valid,
        "approval_stale": bool(question.approved and not approval_valid),
        "approved_at": question.approved_at.isoformat() if question.approved_at else None,
        "answer": question.answer, "analysis": question.analysis,
        "reads": {"a": _reading(question.read_a), "b": _reading(question.read_b), "c": _reading(question.read_c)},
        "publication": library.publication_state(question),
    }


_QUESTION_MUTATION_STATUSES = {Paper.Status.READY}


def _question_trash_json(batch: QuestionDeletionBatch) -> dict:
    rows = list(
        Question.all_objects.filter(
            paper_id=batch.paper_id,
            deletion_batch=batch,
            deleted_at__isnull=False,
        ).select_related("group").order_by("group__sequence", "number", "id")
    )
    return {
        "id": str(batch.pk),
        "origin": batch.origin,
        "reason": batch.reason,
        "created_at": batch.created_at.isoformat(),
        "restored_at": batch.restored_at.isoformat() if batch.restored_at else None,
        "count": len(rows),
        "questions": [
            {
                "id": question.pk,
                "number": question.number,
                "group": ({
                    "id": question.group_id,
                    "title": question.group.title,
                    "sequence": question.group.sequence,
                } if question.group_id else None),
                "section": question.section,
                "stem": question.stem[:160],
                "deleted_at": question.deleted_at.isoformat(),
            }
            for question in rows
        ],
    }


def _sync_paper_question_counts(paper: Paper) -> None:
    """Keep the persisted progress fields aligned with the visible cards."""
    active = paper.questions.all()
    paper.total = active.count()
    paper.progress = active.exclude(
        state__in=[Question.State.WAITING, Question.State.READING],
    ).count()
    paper.updated_at = timezone.now()
    paper.save(update_fields=["total", "progress", "updated_at"])


def _soft_delete_questions(paper: Paper, question_ids: list[int]) -> tuple[QuestionDeletionBatch, int]:
    """Delete one user-selected set, returning its undo batch.

    An identical retry returns the original batch rather than creating another
    recycle-bin entry.  Mixing visible and already-deleted cards is rejected so
    a stale browser can never delete more than the user actually selected.
    """
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper.pk)
        if paper.status not in _QUESTION_MUTATION_STATUSES:
            raise ValueError("只有待终审状态可以删除题卡；处理中或失败的任务不能修改")
        rows = list(
            Question.all_objects.select_for_update().select_related("paper", "group")
            .filter(pk__in=question_ids)
        )
        if len(rows) != len(question_ids) or any(row.paper_id != paper.pk for row in rows):
            raise LookupError("所选题卡不存在，或不属于当前任务")
        deleted_rows = [row for row in rows if row.deleted_at is not None]
        if deleted_rows:
            batch_ids = {row.deletion_batch_id for row in rows}
            if len(deleted_rows) == len(rows) and len(batch_ids) == 1 and None not in batch_ids:
                batch = QuestionDeletionBatch.objects.select_for_update().get(pk=batch_ids.pop())
                if sorted(question_ids) == sorted(batch.question_ids):
                    return batch, 0
            raise ValueError("所选题卡中包含已经删除的项目，请刷新页面后重试")
        if any(row.state in {Question.State.WAITING, Question.State.READING}
               or row.reread_requested for row in rows):
            raise ValueError("所选题卡仍在识读或等待重读，请完成处理后再删除")
        if PublishedQuestion.objects.filter(
            question_id__in=question_ids,
            status=PublishedQuestion.Status.PUBLISHED,
        ).exists():
            raise ValueError("所选题卡中有已经入库的题目，请先在正式题库中撤回")

        batch = QuestionDeletionBatch.objects.create(
            paper=paper,
            question_ids=sorted(question_ids),
        )
        deleted_at = timezone.now()
        Question.all_objects.filter(pk__in=question_ids).update(
            deleted_at=deleted_at,
            deletion_batch=batch,
        )
        _sync_paper_question_counts(paper)
    return batch, len(rows)


# ---------------------------------------------------------------- 页面与静态文件

def _frontend(name: str, content_type: str):
    def view(request):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return _file(FRONTEND / name, content_type)
    return view


index_page = _frontend("index.html", "text/html; charset=utf-8")
library_page = _frontend("library.html", "text/html; charset=utf-8")
app_script = _frontend("app.js", "application/javascript; charset=utf-8")
render_script = _frontend("qb-render.js", "application/javascript; charset=utf-8")
library_script = _frontend("library.js", "application/javascript; charset=utf-8")
styles = _frontend("styles.css", "text/css; charset=utf-8")
library_styles = _frontend("library.css", "text/css; charset=utf-8")
favicon = _frontend("favicon.png", "image/png")


def katex_asset(request, asset: str):
    if asset in {"katex.min.js", "contrib/auto-render.min.js"}:
        content_type = "application/javascript; charset=utf-8"
    elif asset == "katex.min.css":
        content_type = "text/css; charset=utf-8"
    elif re.fullmatch(r"fonts/KaTeX_[A-Za-z0-9_-]+\.(?:woff2?|ttf)", asset):
        content_type = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf"}[asset.rsplit(".", 1)[1]]
    else:
        raise Http404()
    return _file(FRONTEND / "vendor" / "katex" / asset, content_type)


# ---------------------------------------------------------------- 状态

def health(request):
    return JsonResponse({"ok": True, "app": "question-bank-card"})


def status(request):
    try:
        applied_preferences = preferences.load_applied_configuration()
    except preferences.PreferenceError:
        applied_preferences = None
    checker = readers.checker_engine(applied_preferences)
    primary = readers.primary_engine(applied_preferences)
    arbiter = readers.arbiter_engine(primary, checker, applied_preferences)
    engines = readers.engine_settings(applied_preferences)
    try:
        saved_preferences = preferences.load_configuration() if preferences.preference_path().is_file() else None
    except preferences.PreferenceError:
        saved_preferences = None
    if saved_preferences is not None:
        saved_roles = saved_preferences["roles"]
        engines["saved"] = {
            "primary": saved_roles["primary_engine"],
            "checker": saved_roles["checker_engine"],
            "arbiter": saved_roles["arbiter_engine"],
            "models": saved_preferences["models"],
        }
        current = {**engines["selected"], "models": engines["models"]}
        engines["pending_change"] = engines["saved"] != current
    return JsonResponse({
        "upload_enabled": readers.configured("mineru") and primary is not None,
        "mineru": readers.configured("mineru"),
        "reader": primary.label if primary else None,
        "checker": checker.label if checker else None,
        "arbiter": arbiter.label if arbiter else None,
        "independent_checker": bool(checker and primary and checker.provider != primary.provider),
        "engines": engines,
        "m3_available": m3import.m3_backend() is not None,
    })


@csrf_exempt
def credential_settings_view(request):
    """Read non-secret pool counts or save DPAPI-protected API credentials.

    Responses never contain a key fragment, fingerprint, or submitted value.
    The POST contract makes every provider explicit: keep, clear, or replace.
    """

    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    remote = request.META.get("REMOTE_ADDR", "")
    if remote not in {"127.0.0.1", "::1"}:
        return _error("凭据设置只能在本机题库中使用", 403)
    try:
        if request.method == "GET":
            services = credential_settings.public_environment_status()
            return JsonResponse({
                "services": services,
                "max_accounts": credential_settings.MAX_ACCOUNT_POOL_SIZE,
            })

        rejected = _guard(request)
        if rejected:
            return rejected
        payload = _body(request)
        if payload is None or set(payload) != {"services"} or not isinstance(payload["services"], dict):
            return _error("凭据设置格式不正确")
        mineru_verification = credential_settings.verify_mineru_replacement(payload["services"])
        services = credential_settings.save_actions(payload["services"])
        credential_settings.apply_public_environment(services)
        if mineru_verification == "verified":
            message = "API 配置已加密保存，MinerU Token 已通过官网验证；新任务或下一次重读开始时生效。"
        elif mineru_verification == "unavailable":
            message = "API 配置已加密保存；MinerU 官网暂时无法连接，本次 Token 尚未验证。新任务或下一次重读开始时生效。"
        else:
            message = "API 配置已加密保存；新任务或下一次重读开始时生效，当前任务不会中途换账号。"
        return JsonResponse({
            "services": services,
            "max_accounts": credential_settings.MAX_ACCOUNT_POOL_SIZE,
            "restart_required": False,
            "mineru_verification": mineru_verification,
            "message": message,
        })
    except credential_settings.CredentialValidationError as exc:
        return _error(str(exc), 400)
    except credential_settings.CredentialStoreError as exc:
        # CredentialStoreError messages are deliberately value-free.  Do not
        # log the request body or chain the DPAPI exception into a response.
        return _error(str(exc), 500)


@csrf_exempt
def model_settings(request):
    """保存非秘密模型偏好；worker 会在下一任务边界加载。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("模型设置格式不正确")
    roles = {
        "primary_engine": payload.get("primary"),
        "checker_engine": payload.get("checker"),
        "arbiter_engine": payload.get("arbiter"),
    }
    normalized = preferences.normalize(roles)
    if normalized is None:
        return _error("模型选择不受支持")
    try:
        current = preferences.load_configuration()
    except preferences.PreferenceError:
        current = {"roles": dict(preferences.DEFAULTS), "models": dict(preferences.DEFAULT_MODELS)}
    raw_models = payload.get("models", current["models"])
    normalized_models = preferences.normalize_models(raw_models, defaults=current["models"])
    if normalized_models is None:
        return _error("模型 ID 格式不正确：只能使用 1–160 位字母、数字及 . _ : / + -，且不能填写网址")
    selected = set(normalized.values())
    if "minimax_m3" in selected and not readers.configured("minimax"):
        return _error("所选模型需要先配置 MiniMax API Key")
    if "siliconflow_qwen3" in selected and not readers.configured("siliconflow"):
        return _error("所选模型需要先配置硅基流动 API Key")
    try:
        saved = preferences.save_configuration(normalized, normalized_models)
    except preferences.PreferenceError as exc:
        return _error(str(exc), 500)
    saved_roles = saved["roles"]
    return JsonResponse({
        "saved": {
            "primary": saved_roles["primary_engine"],
            "checker": saved_roles["checker_engine"],
            "arbiter": saved_roles["arbiter_engine"],
            "models": saved["models"],
        },
        "restart_required": False,
        "message": "模型选择已保存；下一份任务或下一次重读开始时生效，正在处理的任务不会中途换模型。",
    })


# ---------------------------------------------------------------- 试卷

@csrf_exempt
def papers(request):
    if request.method == "GET":
        include_archived = request.GET.get("archived") == "1"
        queryset = Paper.objects.all() if include_archived else Paper.objects.filter(archived=False)
        return JsonResponse({"papers": [paper_json(p) for p in queryset[:200]]})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    if not readers.configured("mineru") or readers.primary_engine() is None:
        return _error("上传新资料需要配置 MinerU Token 和所选主读模型的 API Key（请在“设置 → API 与模型”中配置）")
    uploads = request.FILES.getlist("file")
    if not uploads:
        return _error("请选择文件")
    material_type = request.POST.get("material_type", Paper.MaterialType.EXAM)
    if material_type not in Paper.MaterialType.values:
        return _error("请选择“一份试卷”或“一本书”")
    kinds = []
    for item in uploads:
        kind = UPLOAD_KINDS.get(Path(item.name).suffix.lower())
        if kind is None:
            return _error(f"{Path(item.name).name}：只支持 PDF、JPG、PNG、WEBP 或 DOCX")
        limit = settings.MAX_PDF_UPLOAD_BYTES if kind == "pdf" else settings.MAX_UPLOAD_BYTES
        if item.size > limit:
            shown_limit = 200 if kind == "pdf" else limit // (1024 * 1024)
            return _error(f"{Path(item.name).name} 超过 {shown_limit} MB")
        kinds.append(kind)
    if kinds[0] == "image" or len(uploads) > 1:
        if any(kind != "image" for kind in kinds):
            return _error("几个文件一起上传时只能都是照片（合成一份试卷）；PDF 和 Word 请一份一份上传")
        return _upload_photos(request, uploads, material_type=material_type)
    upload = uploads[0]
    suffix = Path(upload.name).suffix.lower()
    kind = kinds[0]
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    existing = Paper.objects.filter(sha256=digest.hexdigest(), material_type=material_type, archived=False)\
        .exclude(status=Paper.Status.FAILED).first()
    if existing:
        return JsonResponse({"paper": paper_json(existing), "duplicate": True})
    paper = Paper(
        filename=Path(upload.name).name[:255], kind=kind, sha256=digest.hexdigest(),
        material_type=material_type,
    )
    folder = settings.DATA_ROOT / str(paper.id)
    staged = settings.DATA_ROOT / f".uploading-{paper.id}-{uuid.uuid4().hex}"
    staged.mkdir(parents=True, exist_ok=False)
    target = staged / f"source{suffix}"
    with target.open("wb") as output:
        for chunk in upload.chunks():
            output.write(chunk)
    if kind == "pdf":
        try:
            pages = imaging.page_sizes(target, "pdf")
            if not pages:
                raise ValueError("empty pdf")
        except Exception:
            try:
                shutil.rmtree(staged)
            except OSError:
                pass  # 下次启动会按 .uploading-* 规则继续清理。
            return _error("这份 PDF 无法打开或没有有效页面，请检查文件后重试")
        paper.pages = pages
    try:
        staged.replace(folder)
        paper.source_path = str(folder / target.name)
        with transaction.atomic():
            paper.save()
            if kind == "pdf" and import_planning.pdf_requires_chunks(
                len(paper.pages), paper.material_type, mineru.MAX_PDF_PAGES,
            ):
                chunk_limit = import_planning.pdf_chunk_page_limit(
                    paper.material_type, mineru.MAX_PDF_PAGES,
                )
                ImportChunk.objects.bulk_create([
                    ImportChunk(paper=paper, **chunk.as_record())
                    for chunk in import_planning.plan_pdf_chunks(len(paper.pages), chunk_limit)
                ])
    except Exception:
        if folder.exists() and not staged.exists():
            try:
                folder.replace(staged)
            except OSError:
                pass
        cleanup = staged if staged.exists() else folder
        try:
            shutil.rmtree(cleanup)
        except OSError:
            pass
        raise
    return JsonResponse({"paper": paper_json(paper)}, status=201)


def _upload_photos(request, uploads, *, material_type: str = Paper.MaterialType.EXAM) -> JsonResponse:
    """一张或几张照片合成一份试卷。页序先按拍摄时间/文件名粗排，MinerU 读完后按卷面题号排定。"""
    if len(uploads) > photos.MAX_PHOTOS:
        return _error(f"一份试卷最多 {photos.MAX_PHOTOS} 张照片")
    enhance = request.POST.get("enhance", "1") != "0"
    digests = []
    for item in uploads:
        digest = hashlib.sha256()
        for chunk in item.chunks():
            digest.update(chunk)
        digests.append(digest.hexdigest())
    if len(set(digests)) != len(digests):
        return _error("选中的照片里有重复的文件，请去掉重复的再上传")
    # 同一组照片（不论选择顺序）、同样的处理方式算同一份卷；不做扫描件效果再传一次会得到另一份卷。
    combined = hashlib.sha256(
        f"photos:{material_type}:{int(enhance)}:{','.join(sorted(digests))}".encode()
    ).hexdigest()
    existing = Paper.objects.filter(sha256=combined, material_type=material_type, archived=False)\
        .exclude(status=Paper.Status.FAILED).first()
    if existing:
        return JsonResponse({"paper": paper_json(existing), "duplicate": True})
    first = Path(uploads[0].name).name
    name = first if len(uploads) == 1 else f"{Path(first).stem} 等 {len(uploads)} 张照片"
    paper = Paper(filename=name[:255], kind="image", sha256=combined, material_type=material_type)
    folder = settings.DATA_ROOT / str(paper.id)
    folder.mkdir(parents=True, exist_ok=False)
    files = []
    for index, item in enumerate(uploads):
        target = folder / f"photo_{index + 1:02d}{Path(item.name).suffix.lower()}"
        with target.open("wb") as output:
            for chunk in item.chunks():
                output.write(chunk)
        try:
            with Image.open(target) as image:
                image.verify()
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            return _error(f"{Path(item.name).name} 打不开，不是有效的图片")
        files.append({"name": Path(item.name).name[:120], "file": target.name, "taken": photos.capture_time(target)})
    order, basis = photos.initial_order(files)
    paper.photos = {"files": files, "enhance": enhance, "order": order, "basis": basis, "check": "", "notes": []}
    paper.source_path = str(folder / files[0]["file"])
    paper.save()
    return JsonResponse({"paper": paper_json(paper)}, status=201)


@csrf_exempt
def paper_page_order(request, paper_id):
    """照片卷调整页序：order 是当前页码的新排列（例如 [1, 0, 2] 把第 2 页调到最前）。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    count = len(paper.pages)
    if not paper.photos or count < 2:
        return _error("只有几张照片合成的试卷可以调整页序")
    if Question.all_objects.filter(paper=paper, deleted_at__isnull=False).exists():
        return _error("回收站里还有题卡；请先恢复这些题卡，再调整页序", 409)
    if paper.status not in (Paper.Status.READY, Paper.Status.FAILED, Paper.Status.NEEDS_GROUPING) \
            or not paper.blocks.exists():
        return _error("这份试卷还在处理中，稍后再调整页序")
    payload = _body(request) or {}
    order = payload.get("order")
    if not isinstance(order, list) or sorted(order) != list(range(count)) \
            or any(isinstance(v, bool) or not isinstance(v, int) for v in order):
        return _error("页序不正确")
    if order == list(range(count)):
        info = dict(paper.photos)
        info["check"], info["manual"] = "", True
        paper.photos = info
        paper.save(update_fields=["photos", "updated_at"])
        return JsonResponse({"paper": paper_json(paper), "changed": False})
    if PublishedQuestion.objects.filter(paper=paper).exists():
        return _error("这份试卷已经有题目入库，不能再调整页序")
    reorder_photo_pages(paper, order)
    paper.refresh_from_db()
    return JsonResponse({"paper": paper_json(paper), "changed": True})


def _validated_split_groups(value, page_count: int) -> list[list[int]] | None:
    """A split must be a lossless partition: every current page exactly once."""
    if not isinstance(value, list) or len(value) < 2:
        return None
    groups: list[list[int]] = []
    flattened: list[int] = []
    for raw_group in value:
        if not isinstance(raw_group, list) or not raw_group:
            return None
        if any(type(page) is not int or not 0 <= page < page_count for page in raw_group):
            return None
        if len(set(raw_group)) != len(raw_group):
            return None
        group = list(raw_group)
        groups.append(group)
        flattened.extend(group)
    if sorted(flattened) != list(range(page_count)) or len(flattened) != page_count:
        return None
    return groups


def _remap_page_items(items, page_mapping: dict[int, int], seq_mapping: dict[int, int] | None = None) -> list[dict]:
    result = []
    for raw in items or []:
        if not isinstance(raw, dict) or raw.get("page_idx") not in page_mapping:
            continue
        item = deepcopy(raw)
        item["page_idx"] = page_mapping[item["page_idx"]]
        if seq_mapping is not None and item.get("seq") in seq_mapping:
            item["seq"] = seq_mapping[item["seq"]]
        result.append(item)
    return result


def _copy_split_question(
    question: Question,
    *,
    paper: Paper,
    group: QuestionGroup,
    page_mapping: dict[int, int],
    seq_mapping: dict[int, int],
) -> Question | None:
    """Preserve an already-read card only when all of its source regions stay together."""
    source_items = [
        *(question.regions or []),
        *(question.regions_auto or []),
        *(question.figures or []),
        *(question.figure_candidates or []),
    ]
    source_pages = {item.get("page_idx") for item in source_items if isinstance(item, dict)}
    if not source_pages or not source_pages.issubset(page_mapping):
        return None
    return Question.objects.create(
        paper=paper,
        group=group,
        number=question.number,
        section=question.section,
        question_type=question.question_type,
        regions=_remap_page_items(question.regions, page_mapping),
        regions_auto=_remap_page_items(question.regions_auto, page_mapping),
        start_source=question.start_source,
        source_kind=question.source_kind,
        source_anchor_seq=(seq_mapping.get(question.source_anchor_seq)
                           if question.source_anchor_seq is not None else None),
        figure_candidates=_remap_page_items(question.figure_candidates, page_mapping, seq_mapping),
        figures=_remap_page_items(question.figures, page_mapping),
        figure_review=deepcopy(question.figure_review),
        read_a=deepcopy(question.read_a),
        read_b=deepcopy(question.read_b),
        read_c=deepcopy(question.read_c),
        stem=question.stem,
        options=deepcopy(question.options),
        text_source=question.text_source,
        state=question.state,
        flags=deepcopy(question.flags),
        error=question.error,
        edited=question.edited,
        approved=False,
        approved_at=None,
        approved_content_hash="",
        answer=question.answer,
        analysis=question.analysis,
        reread_requested=False,
    )


@csrf_exempt
def paper_split(request, paper_id):
    """Split mixed photos or a PDF locally without losing, duplicating, or re-sending pages."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("拆分内容格式不正确")

    paper = get_object_or_404(Paper, pk=paper_id)
    groups = _validated_split_groups(payload.get("groups"), len(paper.pages))
    if groups is None:
        return _error("拆分必须让每一页恰好出现一次，不能漏页、重复页或使用无效页码")
    is_photo_source = bool(paper.photos) and paper.kind == "image"
    render_source = Path(paper.render_path or paper.source_path)
    is_pdf_source = paper.kind in {"pdf", "docx"} and render_source.suffix.lower() == ".pdf"
    if not is_photo_source and not is_pdf_source:
        return _error("这项任务没有可安全拆分的照片或 PDF 原稿")
    if is_pdf_source and any(
        group != list(range(min(group), max(group) + 1)) for group in groups
    ):
        return _error("PDF 只能按连续页段拆分，请重新核对分组")
    if paper.archived:
        return _error("这项任务已经归档，不能重复拆分")
    if paper.status not in {Paper.Status.NEEDS_GROUPING, Paper.Status.READY, Paper.Status.FAILED}:
        return _error("这项任务还在处理中，完成后再拆分")
    if paper.publications.exists():
        return _error("这项任务已有正式题库记录，为保留来源追溯不能拆分")
    if Question.all_objects.filter(paper=paper, deleted_at__isnull=False).exists():
        return _error("这项任务的回收站里还有题卡；请先恢复这些题卡，再拆分资料")

    data_root = settings.DATA_ROOT.resolve()
    source_folder = (data_root / str(paper.id)).resolve()
    if source_folder.parent != data_root or not source_folder.is_dir():
        return _error("任务原文件不完整，未执行拆分", 500)

    created_folders: list[Path] = []
    staged_folders: list[Path] = []
    pending_moves: list[tuple[Path, Path]] = []
    children: list[Paper] = []
    committed = False
    try:
        with transaction.atomic():
            paper = Paper.objects.select_for_update().get(pk=paper_id)
            if paper.archived or paper.publications.exists():
                return _error("任务状态已经变化，请刷新后重试")
            source_info = deepcopy(paper.photos) if is_photo_source else {}
            source_order = source_info.get("order") or list(range(len(source_info.get("files") or [])))
            if is_photo_source and len(source_order) != len(paper.pages):
                return _error("照片页序记录不完整，未执行拆分", 500)
            source_blocks = list(paper.blocks.order_by("seq"))
            source_questions = list(paper.questions.select_related("group").order_by("id"))

            for group_index, source_pages in enumerate(groups, start=1):
                page_mapping = {old_page: new_page for new_page, old_page in enumerate(source_pages)}
                selected_blocks = [block for block in source_blocks if block.page_idx in page_mapping]
                child = Paper(
                    filename=paper.filename,
                    task_name=f"{paper.display_name}（第 {group_index} 部分）"[:255],
                    kind="image" if is_photo_source else "pdf",
                    material_type=paper.material_type,
                    sha256=hashlib.sha256(
                        f"{paper.sha256}:split:{','.join(map(str, source_pages))}".encode()
                    ).hexdigest(),
                    status=Paper.Status.SEGMENTING if selected_blocks else Paper.Status.QUEUED,
                    structure={
                        "confirmed": True,
                        "confirmed_groups": [list(range(len(source_pages)))],
                        "suggested_groups": [list(range(len(source_pages)))],
                        "split_from": str(paper.id),
                        "source_pages": [page + 1 for page in source_pages],
                        "signals": [],
                    },
                    notes=[f"由任务“{paper.display_name}”无损拆分；对应原任务第 "
                           f"{'、'.join(str(page + 1) for page in source_pages)} 页。"],
                )
                final_folder = data_root / str(child.id)
                staged = data_root / f".splitting-{child.id}-{uuid.uuid4().hex}"
                staged.mkdir(parents=True, exist_ok=False)
                staged_folders.append(staged)

                if is_photo_source:
                    child_files = []
                    child_ranges = {}
                    for new_page, old_page in enumerate(source_pages):
                        source_file_index = source_order[old_page]
                        source_record = source_info["files"][source_file_index]
                        source_raw = source_folder / source_record["file"]
                        suffix = source_raw.suffix.lower() or ".jpg"
                        target_raw = staged / f"photo_{new_page + 1:02d}{suffix}"
                        shutil.copy2(source_raw, target_raw)
                        record = deepcopy(source_record)
                        record["file"] = target_raw.name
                        child_files.append(record)

                        source_page_file = photos.page_file(source_folder, source_file_index)
                        target_page_file = photos.page_file(staged, new_page)
                        if source_page_file.is_file():
                            shutil.copy2(source_page_file, target_page_file)
                        else:
                            image, straightened = photos.process_photo(
                                target_raw, clean=source_info.get("enhance", True),
                            )
                            record["straightened"] = straightened
                            image.save(target_page_file, format="JPEG", quality=90, optimize=True)
                        ranges = source_info.get("ranges") or {}
                        child_ranges[str(new_page)] = deepcopy(ranges.get(str(source_file_index)))

                    child.photos = {
                        "files": child_files,
                        "enhance": source_info.get("enhance", True),
                        "order": list(range(len(child_files))),
                        "mineru_order": list(range(len(child_files))),
                        "basis": "从原任务拆分",
                        "check": "",
                        "manual": True,
                        "ranges": child_ranges,
                        "notes": ["已从混合上传中拆出；所有原图均保留，未重新压缩。"],
                    }
                    render = staged / "pages.pdf"
                    photos.build_pdf(staged, child.photos, render)
                    child.source_path = str(final_folder / child_files[0]["file"])
                else:
                    render = staged / "source.pdf"
                    mineru.write_pdf_slice(
                        render_source,
                        render,
                        min(source_pages),
                        max(source_pages) + 1,
                    )
                    child.source_path = str(final_folder / render.name)
                child.pages = imaging.page_sizes(render, "pdf")
                child.render_path = str(final_folder / render.name)
                child.save()

                question_group = QuestionGroup.objects.create(
                    paper=child,
                    title=child.display_name,
                    kind=(QuestionGroup.Kind.CHAPTER if child.material_type == Paper.MaterialType.BOOK
                          else QuestionGroup.Kind.EXAM),
                    sequence=0,
                    page_start=1,
                    page_end=len(source_pages),
                    metadata={"pages": list(range(len(source_pages))),
                              "source_pages": [page + 1 for page in source_pages]},
                )
                seq_mapping = {block.seq: sequence for sequence, block in enumerate(selected_blocks)}
                Block.objects.bulk_create([
                    Block(
                        paper=child,
                        seq=seq_mapping[block.seq],
                        type=block.type,
                        page_idx=page_mapping[block.page_idx],
                        bbox=deepcopy(block.bbox),
                        text=block.text,
                    )
                    for block in selected_blocks
                ], batch_size=300)
                for question in source_questions:
                    _copy_split_question(
                        question,
                        paper=child,
                        group=question_group,
                        page_mapping=page_mapping,
                        seq_mapping=seq_mapping,
                    )
                pending_moves.append((staged, final_folder))
                children.append(child)

            structure = deepcopy(paper.structure or {})
            structure.update({
                "confirmed": True,
                "split_children": [str(child.id) for child in children],
                "split_groups": groups,
                "split_at": timezone.now().isoformat(),
            })
            paper.archived = True
            paper.structure = structure
            paper.notes = [*(paper.notes or []), f"已无损拆成 {len(children)} 项任务；原任务保留为归档来源。"]
            paper.save(update_fields=["archived", "structure", "notes", "updated_at"])
        committed = True
        # Move files only after the database commit. If Windows or the process
        # interrupts here, startup reconciliation can restore each .splitting
        # directory because the child record already exists.
        for staged, final_folder in pending_moves:
            staged.replace(final_folder)
            staged_folders.remove(staged)
            created_folders.append(final_folder)
    except OSError:
        if not committed:
            for folder in [*staged_folders, *created_folders]:
                shutil.rmtree(folder, ignore_errors=True)
            return _error("拆分时有文件正在被占用，原任务未改变；关闭原卷窗口后再试")
        return _error("拆分记录已安全保存，但部分文件尚未就位；请重新启动程序，它会自动恢复", 500)
    except Exception:
        for folder in [*staged_folders, *created_folders]:
            shutil.rmtree(folder, ignore_errors=True)
        raise

    return JsonResponse({
        "papers": [paper_json(child) for child in children],
        "source": paper_json(paper),
        "message": f"已拆成 {len(children)} 项任务；每一页都已核对且原任务已归档保留。",
    }, status=201)


@csrf_exempt
def paper_confirm_structure(request, paper_id):
    """Confirm that numbering restarts belong to one material and continue safely.

    Repeated numbers still keep separate internal question groups, so confirming
    a book/exam never overwrites an earlier card with the same printed number.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        if paper.status != Paper.Status.NEEDS_GROUPING:
            return _error("这项任务当前不需要确认资料结构")
        if paper.publications.exists():
            return _error("这项任务已有正式题库记录，不能再改变资料结构")
        structure = deepcopy(paper.structure or {})
        groups = structure.get("suggested_groups") or [list(range(len(paper.pages)))]
        scopes = structure.get("suggested_scopes") or []
        structure.update({
            "confirmed": True,
            "confirmed_groups": groups,
            "confirmed_scopes": scopes,
            "confirmed_at": timezone.now().isoformat(),
            # Existing groups may describe the page order before a human
            # rearrangement. The worker will safely reconcile them with these
            # confirmed scopes before cutting any card.
            "groups_need_rebuild": True,
        })
        paper.structure = structure
        paper.status = Paper.Status.SEGMENTING
        paper.error = ""
        paper.save(update_fields=["structure", "status", "error", "updated_at"])
    return JsonResponse({
        "paper": paper_json(paper),
        "message": "已确认属于同一份资料；重复题号会分属不同题组，现已继续切题。",
    })


@csrf_exempt
def paper_archive(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if paper.status in {
        Paper.Status.QUEUED, Paper.Status.PARSING, Paper.Status.SEGMENTING, Paper.Status.READING,
    }:
        return _error("任务正在处理中，完成或失败后再归档")
    if Question.all_objects.filter(paper=paper, deleted_at__isnull=False).exists():
        return _error("回收站里还有题卡；请先恢复这些题卡，再归档任务", 409)
    paper.archived = True
    paper.save(update_fields=["archived", "updated_at"])
    return JsonResponse({"paper": paper_json(paper), "archived": True})


@csrf_exempt
def paper_detail(request, paper_id):
    paper = get_object_or_404(Paper, pk=paper_id)
    if request.method == "PATCH":
        rejected = _guard(request)
        if rejected:
            return rejected
        payload = _body(request)
        name = _valid_paper_name(payload.get("name") if payload is not None else None)
        if name is None:
            return _error("任务名需为 1–255 个字符，且不能包含换行或控制字符")
        paper, changed = library.rename_paper(paper, name)
        return JsonResponse({"paper": paper_json(paper), "changed": changed})
    if request.method == "DELETE":
        rejected = _guard(request, json_body=False)
        if rejected:
            return rejected
        paper_id_text = str(paper.id)
        data_root = settings.DATA_ROOT.resolve()
        folder = (data_root / paper_id_text).resolve()
        if folder.parent != data_root:
            return _error("任务文件位置不安全，未执行删除", 500)
        staged = data_root / f".deleting-{paper_id_text}-{uuid.uuid4().hex}"
        try:
            with transaction.atomic():
                paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
                if paper.status not in {Paper.Status.READY, Paper.Status.FAILED, Paper.Status.NEEDS_GROUPING}:
                    return _error("任务还在处理中；只有待终审、待确认结构或失败的任务可以删除")
                if paper.publications.exists():
                    return _error("这项任务已有正式题库记录，为保留来源追溯不能删除")
                structure = paper.structure or {}
                if structure.get("split_from") or structure.get("split_children") or Paper.objects.filter(
                    structure__split_from=paper_id_text,
                ).exists():
                    return _error("这项任务属于拆分资料，必须保留原稿与追溯关系；可以归档，但不能永久删除")
                if folder.exists():
                    folder.replace(staged)
                paper.delete()
        except OSError:
            if staged.exists() and not folder.exists():
                try:
                    staged.replace(folder)
                except OSError:
                    pass
            return _error("任务文件正在被占用，暂时无法删除；请关闭正在查看的原卷后再试")
        except Exception:
            if staged.exists() and not folder.exists():
                try:
                    staged.replace(folder)
                except OSError:
                    pass
            raise
        warning = ""
        if staged.exists():
            try:
                shutil.rmtree(staged)
            except OSError:
                warning = "任务已删除，但有残留文件暂时被占用；关闭程序并重新启动后会继续清理"
        return JsonResponse({"deleted": paper_id_text, "warning": warning})
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "PATCH", "DELETE"])
    return JsonResponse({
        "paper": paper_json(paper),
        "questions": [
            question_json(q)
            for q in paper.questions.select_related("group").order_by("group__sequence", "number", "id")
        ],
    })


@csrf_exempt
def paper_questions_delete(request, paper_id):
    """Move one explicit multi-selection to the recycle bin as one undo unit."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    raw_ids = payload.get("question_ids") if payload is not None else None
    if (not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 1000
            or any(type(value) is not int or value < 1 for value in raw_ids)):
        return _error("请选择 1–1000 道需要删除的题目")
    question_ids = list(dict.fromkeys(raw_ids))
    paper = get_object_or_404(Paper, pk=paper_id)
    try:
        batch, deleted = _soft_delete_questions(paper, question_ids)
    except LookupError as error:
        return _error(str(error), 404)
    except ValueError as error:
        return _error(str(error), 409)
    batch.refresh_from_db()
    return JsonResponse({
        "deleted": deleted,
        "already_deleted": deleted == 0,
        "undo_batch": _question_trash_json(batch),
        "paper": paper_json(Paper.objects.get(pk=paper_id)),
    })


def paper_question_trash(request, paper_id):
    """List unrestored deletion gestures for the task's recycle bin."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    paper = get_object_or_404(Paper, pk=paper_id)
    batches = QuestionDeletionBatch.objects.filter(
        paper=paper,
        restored_at__isnull=True,
        questions__deleted_at__isnull=False,
    ).distinct().order_by("-created_at")
    return JsonResponse({"batches": [_question_trash_json(batch) for batch in batches]})


@csrf_exempt
def question_deletion_restore(request, paper_id, batch_id):
    """Restore exactly one deletion gesture; repeated requests are no-ops."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    if _body(request) is None:
        return _error("请求内容不正确")
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        if paper.status not in _QUESTION_MUTATION_STATUSES:
            return _error("任务正在处理中；只有待终审状态可以恢复题卡", 409)
        batch = get_object_or_404(
            QuestionDeletionBatch.objects.select_for_update(), pk=batch_id, paper=paper,
        )
        if batch.restored_at is not None:
            restored = 0
        else:
            rows = Question.all_objects.select_for_update().filter(
                paper=paper,
                deletion_batch=batch,
                deleted_at__isnull=False,
            )
            restored = rows.count()
            rows.update(deleted_at=None, deletion_batch=None)
            batch.restored_at = timezone.now()
            batch.save(update_fields=["restored_at"])
            _sync_paper_question_counts(paper)
    batch.refresh_from_db()
    return JsonResponse({
        "restored": restored,
        "already_restored": restored == 0,
        "undo_batch": _question_trash_json(batch),
        "questions": [
            question_json(question)
            for question in Question.objects.filter(pk__in=batch.question_ids)
            .select_related("group").order_by("group__sequence", "number", "id")
        ],
        "paper": paper_json(Paper.objects.get(pk=paper_id)),
    })


def page_preview(request, paper_id, page: int):
    paper = get_object_or_404(Paper, pk=paper_id)
    if page not in {p["page_idx"] for p in paper.pages}:
        raise Http404()
    return _file(PageStore(paper).preview(page), "image/jpeg")


@csrf_exempt
def paper_retry(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    requested_type = payload.get("material_type")
    if requested_type is not None and requested_type not in Paper.MaterialType.values:
        return _error("资料类型不正确")
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        if paper.status != Paper.Status.FAILED:
            return _error("只有处理失败的任务需要重试")
        has_blocks = paper.blocks.exists()
        has_questions = paper.questions.exists()
        fields = ["status", "error", "updated_at"]
        if requested_type is not None and requested_type != paper.material_type:
            # 0008 及更旧版本没有“试卷/教材”字段，迁移时只能保守地按试卷处理。
            # 允许人在还没有任何解析成果时明确改成教材，不靠文件名猜测。
            if paper.kind != "pdf":
                return _error("只有 PDF 任务可以切换试卷/教材模式")
            if has_blocks or has_questions or paper.import_chunks.exists() or paper.publications.exists():
                return _error("这项任务已有解析或审核记录，为保留来源不能再改资料类型")
            paper.material_type = requested_type
            fields.append("material_type")
            if requested_type == Paper.MaterialType.BOOK:
                note = "已明确按教材模式重试：PDF 在本机每 100 页稳定分片。"
                paper.notes = [*(paper.notes or []), note]
                fields.append("notes")
        # 0009 之前失败的教材没有 ImportChunk 记录。重试时按现行策略补建，
        # 避免再次把整本书作为一次 MinerU 请求发送。
        if paper.kind == "pdf" and paper.pages and not paper.import_chunks.exists() and \
                import_planning.pdf_requires_chunks(
                    len(paper.pages), paper.material_type, mineru.MAX_PDF_PAGES,
                ):
            chunk_limit = import_planning.pdf_chunk_page_limit(
                paper.material_type, mineru.MAX_PDF_PAGES,
            )
            ImportChunk.objects.bulk_create([
                ImportChunk(paper=paper, **chunk.as_record())
                for chunk in import_planning.plan_pdf_chunks(len(paper.pages), chunk_limit)
            ])
        paper.status = Paper.Status.SEGMENTING if has_blocks and not paper.questions.exists() else \
            Paper.Status.READING if has_questions else Paper.Status.QUEUED
        paper.error = ""
        paper.save(update_fields=fields)
        paper.questions.filter(state=Question.State.RED).update(state=Question.State.WAITING)
    return JsonResponse({
        "paper": paper_json(paper),
        "message": "已按教材模式分片重试" if requested_type == Paper.MaterialType.BOOK
        else "已重试处理",
    })


@csrf_exempt
def paper_resegment_preview(request, paper_id):
    """Pure read-only comparison; never invokes readers or mutates the task."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    if _body(request) is None:
        return _error("请求内容不正确")
    paper = get_object_or_404(Paper, pk=paper_id)
    error = _resegment_safety_error(paper)
    if error:
        return _error(error, 409)
    try:
        report = preview_resegment(paper)
    except RuntimeError as exc:
        return _error(str(exc), 400)
    return JsonResponse({"paper_id": str(paper.pk), "report": report})


def _resegment_safety_error(paper: Paper) -> str:
    if paper.status != Paper.Status.READY:
        return "只有已经完成识读、处于待终审状态的任务可以重新切题"
    if not paper.blocks.exists():
        return "这项任务没有 MinerU 解析结果，不能重新切题"
    if Question.all_objects.filter(paper=paper, deleted_at__isnull=False).exists():
        return "回收站里还有题卡；请先恢复这些题卡，再重新切题"
    if paper.questions.filter(
        models.Q(state__in=[Question.State.WAITING, Question.State.READING])
        | models.Q(reread_requested=True)
    ).exists():
        return "仍有题卡正在识读或等待重读；完成后再重新切题"
    return ""


@csrf_exempt
def paper_resegment(request, paper_id):
    """按最新规则重新切题；内容没变的题卡（包括已通过的）原样保留。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    if _body(request) is None:
        return _error("请求内容不正确")
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        error = _resegment_safety_error(paper)
        if error:
            return _error(error, 409)
        paper.status = Paper.Status.SEGMENTING
        paper.error = ""
        paper.save(update_fields=["status", "error", "updated_at"])
    return JsonResponse({"paper": paper_json(paper)})


@csrf_exempt
def approve_green(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if paper.status == Paper.Status.NEEDS_GROUPING:
        return _error("请先确认资料结构或拆分任务，再标记题卡通过")
    now = timezone.now()
    changed = []
    with transaction.atomic():
        questions = list(paper.questions.select_for_update().select_related("paper").filter(
            state=Question.State.GREEN,
        ))
        for question in questions:
            if (not question.stem.strip() or library.approval_is_current(question)
                    or blocks_approval(stored_or_derived_review(question))):
                continue
            question.approved = True
            question.approved_at = now
            question.approved_content_hash = library.approval_hash(question)
            question.updated_at = now
            changed.append(question)
        Question.objects.bulk_update(changed, ["approved", "approved_at", "approved_content_hash", "updated_at"])
    count = len(changed)
    return JsonResponse({"approved": count, "paper": paper_json(paper)})


@csrf_exempt
def publish_paper(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if paper.status == Paper.Status.NEEDS_GROUPING:
        return _error("请先确认资料结构或拆分任务，再入库")
    created, unchanged, problems = 0, 0, []
    for question in paper.questions.filter(approved=True):
        try:
            _, is_new = library.publish(question)
        except ValueError as error:
            problems.append(str(error))
            continue
        created += int(is_new)
        unchanged += int(not is_new)
    return JsonResponse({"created": created, "unchanged": unchanged, "problems": problems,
                         "paper": paper_json(paper)})


@csrf_exempt
def add_question(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if paper.status == Paper.Status.NEEDS_GROUPING:
        return _error("请先确认资料结构或拆分任务，再补录题目")
    payload = _body(request)
    number = payload.get("number") if payload else None
    regions = _valid_regions(paper, payload.get("regions")) if payload else None
    if type(number) is not int or not 1 <= number <= 999 or regions is None:
        return _error("需要题号（1–999）和原卷范围")
    region_pages = {item["page_idx"] for item in regions}
    all_groups = list(paper.question_groups.order_by("sequence", "id"))
    group_pages: dict[int, set[int]] = {}
    for group in all_groups:
        pages = (group.metadata or {}).get("pages")
        if not isinstance(pages, list):
            pages = list(range((group.page_start or 1) - 1, group.page_end or len(paper.pages)))
        group_pages[group.pk] = {page for page in pages if type(page) is int}

    requested_group_id = payload.get("group_id") if payload else None
    if requested_group_id is not None:
        if type(requested_group_id) is not int:
            return _error("题组编号格式不正确")
        group = next((item for item in all_groups if item.pk == requested_group_id), None)
        if group is None:
            return _error("所选题组不属于这项任务")
        if not region_pages.issubset(group_pages[group.pk]):
            return _error("所框范围不在所选题组的页面内，请重新选择题组或范围")
    else:
        matching_groups = [
            group for group in all_groups if region_pages.issubset(group_pages[group.pk])
        ]
        if all_groups and len(matching_groups) > 1:
            return _error("这一页包含多个题组；请在题号旁明确选择题组后再添加")
        if all_groups and not matching_groups:
            return _error("这道题的范围跨越题组，请缩小范围后再添加")
        group = matching_groups[0] if matching_groups else None
    existing_source = Question.all_objects.filter(paper=paper, number=number)
    if group is not None and existing_source.filter(group=group).exists():
        return _error(f"已经有第 {number} 题了；如果它在回收站中，请先恢复")
    if group is None and existing_source.filter(group__isnull=True).exists():
        return _error(f"已经有第 {number} 题了；如果它在回收站中，请先恢复")
    question = Question.objects.create(
        paper=paper, group=group, number=number, regions=regions, regions_auto=regions, start_source="manual",
        source_kind=Question.SourceKind.MANUAL, source_anchor_seq=None,
        figure_candidates=candidates_in(paper, regions), reread_requested=True,
    )
    return JsonResponse({"question": question_json(question)}, status=201)


# ---------------------------------------------------------------- 题卡

def _question(question_id) -> Question:
    return get_object_or_404(Question.objects.select_related("paper"), pk=question_id)


def _clear_approval(question: Question) -> None:
    question.approved = False
    question.approved_at = None
    question.approved_content_hash = ""


def _apply_figure_review(question: Question, review: dict) -> None:
    """Store a local decision and keep legacy flags/state in sync for old clients."""
    question.figure_review = review
    question.flags = [flag for flag in (question.flags or []) if not figure_flag(flag)]
    if review.get("status") == BLOCKED_MISSING:
        question.flags.append(FLAG_NO_FIGURE if review.get("cue_matches") and not question.figures
                              else FLAG_UNFOUND_FIGURE)
    elif review.get("status") == CONFLICT:
        question.flags.append(
            FLAG_UNFOUND_FIGURE if "candidate_unclassified" in (review.get("signals") or [])
            else FLAG_UNCUED_FIGURE
        )
    if question.state in library.REVIEWABLE_STATES:
        question.state = Question.State.YELLOW if question.flags else Question.State.GREEN


@csrf_exempt
def question_action(request, question_id, action: str):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    now = timezone.now()
    with transaction.atomic():
        question = get_object_or_404(
            Question.objects.select_for_update().select_related("paper"), pk=question_id,
        )
        if action == "approve":
            if question.paper.status == Paper.Status.NEEDS_GROUPING:
                return _error("请先确认资料结构或拆分任务，再标记题卡通过")
            value = payload.get("approved", True)
            if type(value) is not bool:
                return _error("approved 必须是 true 或 false")
            if value and question.state not in library.REVIEWABLE_STATES:
                return _error("这道题尚未进入可审核状态，请先完成识读或人工修正")
            if value and not question.stem.strip():
                return _error("题干为空，请先改字")
            review = stored_or_derived_review(question)
            if value and blocks_approval(review):
                return _error(f"这道题暂时不能通过：{blocking_message(review)}。请先补配图，或确认本题确实无图")
            if value:
                question.approved = True
                question.approved_at = now
                question.approved_content_hash = library.approval_hash(question)
            else:
                _clear_approval(question)
        elif action == "text":
            previous_ignored = _saved_ignored_candidates(question)
            stem = payload.get("stem")
            options = payload.get("options", {})
            if not isinstance(stem, str) or not stem.strip() or len(stem) > 20000:
                return _error("题干不能为空")
            if not isinstance(options, dict) or set(options) - set(library.OPTION_KEYS) \
                    or not all(isinstance(v, str) and len(v) <= 4000 for v in options.values()):
                return _error("选项格式不正确")
            kind = payload.get("question_type", question.question_type)
            if kind not in TYPES:
                return _error("题型不正确")
            for key in ("answer", "analysis"):
                if key in payload and (not isinstance(payload[key], str) or len(payload[key]) > 20000):
                    return _error("答案或解析格式不正确")
            question.stem = fix_symbols(stem.strip())
            question.options = {k: fix_symbols(v.strip()) for k, v in options.items() if v.strip()}
            question.question_type = kind
            question.answer = fix_symbols(payload.get("answer", question.answer).strip())
            question.analysis = fix_symbols(payload.get("analysis", question.analysis).strip())
            question.edited = True
            question.text_source = "human"
            question.flags = [f for f in question.flags if not figure_flag(f) and "截图" in f]
            question.figure_review = {}
            review = stored_or_derived_review(question, ignored_candidates=previous_ignored)
            if previous_ignored:
                review = {
                    **review,
                    "ignored_candidates": previous_ignored,
                    "excluded_count": len(previous_ignored),
                }
            _apply_figure_review(question, review)
            question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
            question.error = ""
            # 保存编辑和终审是两个独立动作；人必须看到保存后的最终版本再点“通过”。
            _clear_approval(question)
        elif action == "regions":
            regions = _valid_regions(question.paper, payload.get("regions"))
            if regions is None:
                return _error("范围不正确")
            question.regions = regions
            question.figure_candidates = candidates_in(question.paper, regions)
            question.figures = []
            question.figure_review = {}
            question.edited = False
            _clear_approval(question)
            question.flags = []
            question.state = Question.State.WAITING
            question.reread_requested = True
        elif action == "reread":
            question.edited = False
            current_review = question.figure_review if isinstance(question.figure_review, dict) else {}
            has_manual_figure = any(
                isinstance(figure, dict) and figure.get("source") == "manual"
                for figure in (question.figures or [])
            )
            has_human_no_figure = (
                current_review.get("source") == "human"
                and current_review.get("status") == CONFIRMED_NO_FIGURE
            )
            if not has_manual_figure and not has_human_no_figure:
                question.figure_review = {}
            _clear_approval(question)
            question.state = Question.State.WAITING
            question.reread_requested = True
        elif action == "figures":
            figures = payload.get("figures")
            if not isinstance(figures, list) or len(figures) > 12:
                return _error("配图格式不正确")
            candidate_keys = _candidate_keys(question)
            previous_ignored = _saved_ignored_candidates(question)
            ignored_candidates = payload.get("ignored_candidates", previous_ignored)
            if (not isinstance(ignored_candidates, list) or len(ignored_candidates) > 200
                    or not all(isinstance(value, str) and value in candidate_keys
                               for value in ignored_candidates)):
                return _error("无关候选图格式不正确")
            ignored_candidates = sorted(set(ignored_candidates))
            pages = {p["page_idx"] for p in question.paper.pages}
            cleaned = []
            for item in figures:
                bbox = _valid_bbox(item.get("bbox")) if isinstance(item, dict) else None
                if bbox is None or item.get("page_idx") not in pages or item.get("slot") not in SLOTS:
                    return _error("配图格式不正确")
                cleaned_item = {
                    "slot": item["slot"], "page_idx": item["page_idx"], "bbox": bbox, "source": "manual",
                }
                if "label_offset" in item:
                    label_offset = _valid_label_offset(item.get("label_offset"))
                    if label_offset is None:
                        return _error("配图标签位置格式不正确")
                    cleaned_item["label_offset"] = label_offset
                if "candidate_key" in item:
                    candidate_identity = item.get("candidate_key")
                    if not isinstance(candidate_identity, str) or candidate_identity not in candidate_keys:
                        return _error("配图候选来源格式不正确")
                    cleaned_item["candidate_key"] = candidate_identity
                else:
                    # Exact legacy/automatic boxes can recover their candidate
                    # provenance without asking the user to redraw anything.
                    candidate_identity = _candidate_key(cleaned_item)
                    if candidate_identity in candidate_keys:
                        cleaned_item["candidate_key"] = candidate_identity
                cleaned.append(cleaned_item)
            selected_candidate_keys = {
                item["candidate_key"] for item in cleaned if "candidate_key" in item
            }
            if selected_candidate_keys.intersection(ignored_candidates):
                return _error("同一张候选图不能同时设为配图和无关")
            current_review = stored_or_derived_review(question)
            if (cleaned and "candidate_unclassified" in (current_review.get("signals") or [])):
                remaining = _unclassified_candidate_details(
                    question, figures=cleaned, ignored_candidates=ignored_candidates,
                )
                if remaining:
                    pages = sorted({int(item["page_idx"]) + 1 for item in remaining
                                    if isinstance(item.get("page_idx"), int)})
                    page_text = f"（第 {'、'.join(map(str, pages[:6]))}{' 等页' if len(pages) > 6 else ' 页'}）" if pages else ""
                    return _error(
                        f"还有 {len(remaining)} 张候选图尚未处理{page_text}；"
                        "请逐张选择题干/选项/无关，或明确确认其余候选均无关"
                    )
            previous_figures = list(question.figures or [])
            question.figures = cleaned
            if cleaned:
                review = {
                    "status": OK, "source": "human", "reason": "配图已经由人工设置",
                    "signals": ["manual_figure"], "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": len(ignored_candidates),
                    "ignored_candidates": ignored_candidates,
                    "confirmed_at": now.isoformat(),
                }
            else:
                review = {
                    "status": CONFIRMED_NO_FIGURE, "source": "human", "reason": "已人工确认本题确实无图",
                    "signals": ["human_confirmed_no_figure"],
                    "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": len(question.figure_candidates or []), "confirmed_at": now.isoformat(),
                    "ignored_candidates": sorted(candidate_keys),
                    "previous_ignored_candidates": previous_ignored,
                    "previous_figures": previous_figures,
                }
            _apply_figure_review(question, review)
            _clear_approval(question)
        elif action == "figure-review":
            decision = payload.get("decision")
            if decision not in {"confirm_no_figure", "reset"}:
                return _error("配图确认操作不正确")
            if decision == "confirm_no_figure":
                if question.state not in library.REVIEWABLE_STATES or not question.stem.strip():
                    return _error("这道题尚未完成识读，暂时不能确认无图")
                previous_figures = list(question.figures or [])
                previous_ignored = _saved_ignored_candidates(question)
                question.figures = []
                _apply_figure_review(question, {
                    "status": CONFIRMED_NO_FIGURE, "source": "human", "reason": "已人工确认本题确实无图",
                    "signals": ["human_confirmed_no_figure"],
                    "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": len(question.figure_candidates or []), "confirmed_at": now.isoformat(),
                    "ignored_candidates": sorted(_candidate_keys(question)),
                    "previous_ignored_candidates": previous_ignored,
                    "previous_figures": previous_figures,
                })
            else:
                current_review = stored_or_derived_review(question)
                if current_review.get("status") != CONFIRMED_NO_FIGURE:
                    return _error("这道题没有可撤销的无图确认")
                pages = {page["page_idx"] for page in question.paper.pages}
                restored = []
                for item in current_review.get("previous_figures") or []:
                    bbox = _valid_bbox(item.get("bbox")) if isinstance(item, dict) else None
                    if (bbox is not None and item.get("page_idx") in pages and item.get("slot") in SLOTS
                            and item.get("source") in {"auto", "manual", "other", "row"}):
                        restored.append({
                            "slot": item["slot"], "page_idx": item["page_idx"], "bbox": bbox,
                            "source": item["source"],
                            **({"label_offset": item["label_offset"]}
                               if _valid_label_offset(item.get("label_offset")) is not None else {}),
                            **({"candidate_key": item["candidate_key"]}
                               if isinstance(item.get("candidate_key"), str)
                               and item["candidate_key"] in _candidate_keys(question) else {}),
                        })
                previous_ignored = _saved_ignored_candidates(
                    question,
                    {"ignored_candidates": current_review.get("previous_ignored_candidates", [])},
                )
                question.figures = restored
                question.figure_review = {}
                review = stored_or_derived_review(question, ignored_candidates=previous_ignored)
                if previous_ignored:
                    review = {
                        **review,
                        "ignored_candidates": previous_ignored,
                        "excluded_count": len(previous_ignored),
                    }
                _apply_figure_review(question, review)
            _clear_approval(question)
        else:
            raise Http404()
        question.save()
    return JsonResponse({"question": question_json(question), "paper": paper_json(question.paper)})


@csrf_exempt
def question_delete(request, question_id):
    if request.method != "DELETE":
        return HttpResponseNotAllowed(["DELETE"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    question = get_object_or_404(Question.all_objects.select_related("paper"), pk=question_id)
    paper = question.paper
    try:
        batch, deleted = _soft_delete_questions(paper, [question.pk])
    except ValueError as error:
        return _error(str(error), 409)
    batch.refresh_from_db()
    return JsonResponse({
        "deleted": bool(deleted),
        "already_deleted": deleted == 0,
        "undo_batch": _question_trash_json(batch),
        "paper": paper_json(Paper.objects.get(pk=paper.pk)),
    })


def question_figure(request, question_id, index: int):
    question = _question(question_id)
    if not 0 <= index < len(question.figures):
        raise Http404()
    return _file(library.figure_file(question, index), "image/png")


# ---------------------------------------------------------------- M3 导入

@csrf_exempt
def m3_papers(request):
    if request.method == "GET":
        return JsonResponse({"papers": m3import.list_m3_papers()})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request) or {}
    try:
        paper = m3import.import_m3_paper(str(payload.get("id", "")))
    except ValueError as error:
        return _error(str(error), 404)
    return JsonResponse({"paper": paper_json(paper)}, status=201)


# ---------------------------------------------------------------- 正式题库

def _library_rows(request):
    rows = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
    document = request.GET.get("document", "").strip()
    if document:
        try:
            document_id = uuid.UUID(document)
        except (ValueError, AttributeError):
            rows = rows.none()
        else:
            rows = rows.filter(paper_id=document_id)
    kind = request.GET.get("type", "").strip()
    if kind:
        rows = rows.filter(question_type=kind)
    for term in request.GET.get("q", "").split()[:8]:
        key = library.search_key(term)
        if key:
            rows = rows.filter(search_text__contains=key)
    return rows


def library_list(request):
    try:
        limit = min(100, max(1, int(request.GET.get("limit", "50"))))
        offset = max(0, int(request.GET.get("offset", "0")))
    except ValueError:
        return _error("limit 与 offset 必须是整数")
    rows = _library_rows(request).order_by("source_filename", "number", "-version")
    live = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
    sources, types = {}, {}
    for row in live.values("paper_id", "source_filename", "question_type"):
        key = str(row["paper_id"]) if row["paper_id"] else ""
        entry = sources.setdefault(key, {"document_id": key or None, "filename": row["source_filename"], "count": 0})
        entry["count"] += 1
        types[row["question_type"]] = types.get(row["question_type"], 0) + 1
    return JsonResponse({
        "total": rows.count(),
        "items": [library.publication_json(item) for item in rows[offset:offset + limit]],
        "facets": {"sources": sorted(sources.values(), key=lambda item: item["filename"]), "types": types},
    })


def library_detail(request, publication_id):
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    history = PublishedQuestion.objects.filter(question_id=publication.question_id).exclude(pk=publication.pk) \
        if publication.question_id else PublishedQuestion.objects.none()
    return JsonResponse({
        "publication": library.publication_json(publication),
        "versions": [{"id": str(i.id), "version": i.version, "status": i.status,
                      "published_at": i.published_at.isoformat()} for i in history.order_by("-version")],
    })


def library_figure(request, publication_id, name):
    get_object_or_404(PublishedQuestion, pk=publication_id)
    if not re.fullmatch(r"figure-\d{1,3}\.png", name):
        raise Http404()
    return _file(settings.DATA_ROOT / "library" / str(publication_id) / name, "image/png")


@csrf_exempt
def library_withdraw(request, publication_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    library.withdraw(publication)
    return JsonResponse({"publication": library.publication_json(publication)})
