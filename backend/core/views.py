from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import shutil
import unicodedata
import threading
import uuid
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from django.conf import settings
from django.db import models, transaction
from django.db.models import Prefetch, Q
from django.db.models.functions import Cast
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from PIL import Image

from .version import APP_VERSION
from . import (
    credential_settings, demo, features, imaging, import_planning, knowledge, library, library_jobs, m3import,
    mineru, photos, preferences, prose, qtypes, readers, region_reads, tables, intake, source_images, continue_ai_cut,
)
from .figure_policy import (
    BLOCKED_MISSING, CONFIRMED_NO_FIGURE, CONFLICT, DECISION_FLAGS, FLAG_FOREIGN_FIGURE, FLAG_NO_FIGURE,
    FLAG_ROW_FIGURE, FLAG_UNCUED_FIGURE,
    FLAG_UNFOUND_FIGURE, OK, blocking_message, blocks_approval, candidate_key as figure_candidate_key,
    cue_matches, figure_flag,
    reusing_reviews, stored_or_derived_review,
)
from .models import (
    Block, ImportChunk, LibraryJob, Paper, PublishedQuestion, Question, QuestionDeletionBatch, QuestionGroup,
    RegionRead,
)
from .pipeline import (
    CUT_PRODUCED_NOTHING, TEXT_DRAFT_FLAGS, PageStore, candidates_in, check_spots, paper_dir,
    preview_resegment, reorder_photo_pages, promote_saved_readings,
)
from .textnorm import fix_reading_symbols, fix_symbols, witness_key

FRONTEND = settings.FRONTEND_ROOT
UPLOAD_KINDS = {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".docx": "docx"}
TYPES = set(qtypes.TYPES)
SLOTS = {"stem", "A", "B", "C", "D", "E"}


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
        # The lower half of a table joined to a figure is as used as the figure.
        pieces = [figure, *[part for part in (figure.get("parts") or []) if isinstance(part, dict)]]
        for piece in pieces:
            explicit = piece.get("candidate_key")
            if isinstance(explicit, str) and explicit in available:
                selected.add(explicit)
                continue
            exact = _candidate_key(piece)
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
    # 跟 figure_policy.automatic_review 同步：任何「不是本题的图」都视为已分清。
    # 老的只认 {"none", "table"} + q<digits>，把 decoration/row/other/page_border
    # 等都漏成「未处理」，与策略口径冲突。strict 正则卡住 q<digits>，避免
    # q / qOtherQuestion 这种坏值被放过。
    import re as _re
    _foreign_q = _re.compile(r"^q\d+$")
    _bound = {"stem", "A", "B", "C", "D", "E"}
    resolved_elsewhere_labels = {
        label for label, role in assignments.items()
        if role not in _bound and role != "table" and not _foreign_q.match(role)
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
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        return None
    pages = {page["page_idx"] for page in paper.pages}
    regions = []
    for item in value:
        if not isinstance(item, dict) or type(item.get("page_idx")) is not int or item.get("page_idx") not in pages:
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
        else:
            # 1.10.6: one file at MinerU — show what MinerU says (queueing, page n of N…).
            note = mineru.read_state(paper_dir(paper) / mineru.MINERU_STATE_FILE)
            if note:
                progress["mineru"] = note
                if note["state"] == "running" and note.get("total_pages"):
                    progress.update({"determinate": True, "completed": note["pages"],
                                     "total": note["total_pages"], "unit": "page"})
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


# Whether each card's approval still holds and whether its figures block it,
# remembered per card against the card's raw database row (1.10.3).  The paper
# list is fetched every 15 seconds and every tick returns the paper's counts;
# for a 600-card textbook working these out again decoded every card's saved
# readings and took a second or more each time.  Any change to the row, the
# paper's name or the card's group gives a new fingerprint, so a stale answer
# is never reused.
_VERDICTS: "OrderedDict[int, tuple[str, str, bool, bool, bool]]" = OrderedDict()
_VERDICTS_LIMIT = 50_000
_VERDICTS_LOCK = threading.Lock()


def _live_publications(paper: Paper) -> dict[int, PublishedQuestion]:
    """The version the library is currently serving for each card, newest first."""
    latest: dict[int, PublishedQuestion] = {}
    for publication in PublishedQuestion.objects.filter(
            paper=paper, status=PublishedQuestion.Status.PUBLISHED).order_by("question_id", "-version"):
        latest.setdefault(publication.question_id, publication)
    return latest


def _verdict(row: Question, live: PublishedQuestion | None = None) -> tuple[str, bool, bool, bool]:
    """(state, approval current, figures block approval, in the library as shown).

    The state is read after the figure review: a review saved under an older
    rule can turn a yellow card green on screen (“已自动排除疑似多余图”), and the
    counts must say the same.  The last flag is what settles a card: it is in the
    library *and* the library holds exactly what is on screen, so there is
    nothing left for the teacher to look at.  A card edited after it was
    published hashes differently and stays unsettled on purpose.
    """
    approved, blocked = library.approval_is_current(row), blocks_approval(source_images.review(row))
    settled = live is not None and live.content_hash == library.content_hash(library.final_content(row))
    return row.state, approved, blocked, settled


def card_verdicts(paper: Paper, rows: list[Question] | None = None) -> list[tuple[int, str, bool, bool, bool]]:
    """(id, state, approval current, figures block approval, settled) for each card."""
    live = _live_publications(paper)
    if rows is not None:
        with reusing_reviews():
            return [(row.pk, *_verdict(row, live.get(row.pk))) for row in rows]
    json_fields = [field.attname for field in Question._meta.concrete_fields if isinstance(field, models.JSONField)]
    plain_fields = [field.attname for field in Question._meta.concrete_fields
                    if not isinstance(field, models.JSONField)]
    raw = list(
        paper.questions.annotate(**{f"raw_{name}": Cast(name, models.TextField()) for name in json_fields})
        .values_list(*plain_fields, *[f"raw_{name}" for name in json_fields])
    )
    groups = {group.pk: (group.title, group.sequence) for group in paper.question_groups.all()}
    pk_at, group_at = (plain_fields.index(name) for name in ("id", "group_id"))
    # The library copy is part of the answer, so a card that was published (or
    # withdrawn) must not be answered from a cache entry taken before it was.
    def served(pk: int) -> tuple:
        publication = live.get(pk)
        return () if publication is None else (publication.pk, publication.version, publication.content_hash)

    fingerprints = {
        row[pk_at]: hashlib.sha1(repr((row, groups.get(row[group_at]), paper.display_name, served(row[pk_at]))).encode()).hexdigest()
        for row in raw
    }
    known: dict[int, tuple[str, bool, bool, bool]] = {}
    with _VERDICTS_LOCK:
        for pk, fingerprint in fingerprints.items():
            held = _VERDICTS.get(pk)
            if held and held[0] == fingerprint:
                known[pk] = held[1:]
                _VERDICTS.move_to_end(pk)
    missing = [pk for pk in fingerprints if pk not in known]
    if missing:
        with reusing_reviews():
            for start in range(0, len(missing), 500):
                for row in paper.questions.select_related("paper", "group").filter(pk__in=missing[start:start + 500]):
                    known[row.pk] = _verdict(row, live.get(row.pk))
        with _VERDICTS_LOCK:
            for pk in missing:
                if pk in known:
                    _VERDICTS[pk] = (fingerprints[pk], *known[pk])
                    _VERDICTS.move_to_end(pk)
            while len(_VERDICTS) > _VERDICTS_LIMIT:
                _VERDICTS.popitem(last=False)
    return [(row[pk_at], *known[row[pk_at]]) for row in raw if row[pk_at] in known]


def paper_json(paper: Paper, *, with_counts: bool = True, rows: list[Question] | None = None) -> dict:
    info = paper.photos or {}
    quota_paused = (
        paper.status == Paper.Status.FAILED
        and paper.error == readers.TOKEN_PLAN_EXHAUSTED_MESSAGE
    )
    stopped = paper.status == Paper.Status.FAILED and paper.error == mineru.STOPPED_MESSAGE
    data = {
        "id": str(paper.id), "name": paper.display_name, "filename": paper.filename,
        "original_filename": paper.filename, "kind": paper.kind, "status": paper.status,
        "material_type": paper.material_type, "archived": paper.archived,
        "status_label": "额度不足，已暂停" if quota_paused else "已停止" if stopped
        else Paper.Status(paper.status).label,
        "recoverable_pause": quota_paused,
        "stopped": stopped,
        "progress": paper.progress, "total": paper.total,
        "trash_count": Question.all_objects.filter(paper=paper, deleted_at__isnull=False).count(),
        "error": paper.error, "notes": [*(info.get("notes") or []), *paper.notes], "pages": paper.pages,
        "structure": paper.structure or {},
        "processing_plan": paper.processing_plan or {},
        "parse_mode": (paper.processing_plan or {}).get("mode", "mineru"),
        "demo": demo.is_demo(paper),
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
        # ``rows``: the cards the caller already loaded (the review page), so they are not read twice.
        verdicts = card_verdicts(paper, rows)
        rows = [SimpleNamespace(pk=pk, state=state) for pk, state, _approved, _blocked, _settled in verdicts]
        approved_ids = {pk for pk, _state, approved, _blocked, _settled in verdicts if approved}
        figure_blocked_ids = {pk for pk, _state, _approved, blocked, _settled in verdicts if blocked}
        # A card the library already serves exactly as it appears is finished:
        # it must not keep being counted as “N 张要看”, or the same paper says
        # “已入库 10” and “3 张要看” on the same line.
        settled_ids = {pk for pk, _state, _approved, _blocked, settled in verdicts if settled}
        # ``published`` stays “the library holds a record of it”: deleting a task
        # that has one is refused so the source stays traceable.  ``settled`` is
        # the stronger claim the card prints — the library holds *exactly* what
        # is on screen, so there is nothing left to look at.
        published = PublishedQuestion.objects.filter(paper=paper, status=PublishedQuestion.Status.PUBLISHED)\
            .values("question_id").distinct().count()
        pending = approved_ids | settled_ids
        waiting = sum(1 for r in rows if r.state in (Question.State.WAITING, Question.State.READING))
        data["counts"] = {
            "total": len(rows),
            "green": sum(1 for r in rows if r.state == Question.State.GREEN and r.pk not in approved_ids
                         and r.pk not in figure_blocked_ids and r.pk not in settled_ids),
            "yellow": sum(1 for r in rows if (r.state == Question.State.YELLOW or r.pk in figure_blocked_ids)
                          and r.pk not in pending),
            "red": sum(1 for r in rows if r.state == Question.State.RED and r.pk not in pending),
            "waiting": waiting,
            "approved": len(approved_ids),
            "settled": len(settled_ids),
            "published": published,
            # 「不用再看」/「还要看」是试卷列表和审核页共用的唯一口径：
            # 打了勾的、或者题库里已经原样放着的，算不用再看；还在识读的谁都没看过，
            # 两边都不算；剩下的——识读干净的、识读有疑问的、识读失败的——都要人看一眼。
            # 列表和审核页各自数一遍就会出现「列表说 4 张要看、进去却是 25 张要看」。
            "done": len(pending),
            "todo": max(0, len(rows) - len(pending) - waiting),
        }
    return data


def _reading(value: dict) -> dict:
    value = fix_reading_symbols(value)
    return {k: value.get(k) for k in ("engine", "stem", "options", "error", "witness", "chosen", "objections", "answers",
                                      "spotwise")
            if k in value}


def question_json(question: Question, table_blocks: list[dict] | None = None) -> dict:
    figure_review = source_images.review(question)
    if table_blocks is None and question.figures:
        table_blocks = tables.table_blocks(question.paper)
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
        digest = hashlib.sha1(library.figure_identity(figure).encode()).hexdigest()[:10]
        shown_figure = {**figure}
        if shown_figure.get("candidate_key") not in valid_candidate_keys:
            shown_figure.pop("candidate_key", None)
        if "parts" in shown_figure:
            shown_figure["parts"] = [
                {"page_idx": part["page_idx"], "bbox": part["bbox"],
                 **({"candidate_key": part["candidate_key"]}
                    if part.get("candidate_key") in valid_candidate_keys else {})}
                for part in (figure.get("parts") or [])
                if isinstance(part, dict) and "page_idx" in part and "bbox" in part
            ]
        # MinerU read this crop as a table: it can become a text table.
        if table_blocks and tables.table_for_figure(question.paper, figure, table_blocks):
            shown_figure["table"] = True
        figures.append({**shown_figure, "url": f"/api/questions/{question.id}/figures/{index}?v={digest}"})
    question_images = []
    if source_images.is_image(question) or question.processing_mode == "manual":
        try:
            question_images = source_images.assets(question)
        except (OSError, ValueError, IndexError, RuntimeError):
            pass  # Missing originals block approval, but the task remains editable.
    suggestion = deepcopy(question.ocr_suggestion or {})
    if suggestion.get("revision") == question.content_revision:
        suggestion["figures"] = [{**figure, "url": f"/api/questions/{question.pk}/ocr-figures/{index}?v={question.content_revision}"}
                                 for index, figure in enumerate(suggestion.get("figures") or [])]
    else:
        suggestion = {}
    return {
        "id": question.id, "source_key": str(question.source_key), "number": question.number,
        "body_mode": question.body_mode, "processing_mode": question.processing_mode,
        "content_revision": question.content_revision, "ocr_suggestion": suggestion,
        "ocr_pending": question.ocr_pending,
        "question_images": question_images,
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
        # human = 人对照原卷打的勾；ai = AI 助手打的勾（显示成“AI 已通过 · 待你核对”）。
        "approved_by": library.approval_source(question),
        "approval_agent": question.approval_agent if library.approval_source(question) == "ai" else "",
        "approved_at": question.approved_at.isoformat() if question.approved_at else None,
        "answer": question.answer, "analysis": question.analysis, "origin": question.origin,
        "type_blocked": library.type_blocks_approval(question),
        "reads": {"a": _reading(question.read_a), "b": _reading(question.read_b), "c": _reading(question.read_c)},
        # 1.10.2: the spots “MinerU 读法不同” names — boxed on the crop, marked in the text.
        "check_spots": check_spots(question),
        # 1.10.2: 框选识读 — the last region a person asked to read on its own.
        "region_read": region_reads.latest_json(question),
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
library_workspace_script = _frontend("library-workspace.js", "application/javascript; charset=utf-8")
library_solutions_script = _frontend("library-solutions.js", "application/javascript; charset=utf-8")
library_question_editor_script = _frontend("library-question-editor.js", "application/javascript; charset=utf-8")
library_answer_editor_script = _frontend("library-answer-editor.js", "application/javascript; charset=utf-8")
library_ai_script = _frontend("library-ai-settings.js", "application/javascript; charset=utf-8")
export_settings_script = _frontend("export-settings.js", "application/javascript; charset=utf-8")
library_question_viewer_script = _frontend("library-question-viewer.js", "application/javascript; charset=utf-8")
browser_interactions_script = _frontend("browser-interactions.js", "application/javascript; charset=utf-8")
site_drawer_script = _frontend("site-drawer.js", "application/javascript; charset=utf-8")
exam_export_script = _frontend("exam-export.js", "application/javascript; charset=utf-8")
exam_layout_script = _frontend("exam-layout.js", "application/javascript; charset=utf-8")
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
    readiness = readers.primary_readiness(applied_preferences)
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
            "plans": saved_preferences["plans"],
        }
        current = {**engines["selected"], "models": engines["models"], "plans": engines["plans"]}
        engines["pending_change"] = engines["saved"] != current
    return JsonResponse({
        # AI 助手读题只需要 MinerU：题卡先用 MinerU 的文字，再由 AI 助手对照原卷核对。
        # Both follow the saved choice, which the next paper will use.
        "upload_enabled": True,
        "automatic_parse_ready": readers.configured("mineru") and _reading_ready(),
        "assistant_mode": readers.assistant_mode(saved_preferences)
        if saved_preferences is not None else readers.assistant_mode(applied_preferences),
        "mineru": readers.configured("mineru"),
        "reader": primary.label if primary else None,
        "checker": checker.label if checker else None,
        "arbiter": arbiter.label if arbiter else None,
        "independent_checker": bool(checker and primary and checker.provider != primary.provider),
        "engines": engines,
        # Why the reader can or cannot run, and which service will really read:
        # a switch the user did not type has to be visible, not silent.
        "reader_readiness": {key: readiness[key] for key in
                             ("ready", "reason", "selected", "label", "used", "fallback_available")},
        "m3_available": m3import.m3_backend() is not None,
        "app_version": APP_VERSION,
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
        elif mineru_verification in {"unverified", "unavailable"}:
            message = "API 配置已加密保存；本次 MinerU Token 尚未核验。是否有效和服务是否可用，请查看 API 管理页及下一次解析任务的实际返回。"
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
        # Older clients may still send these roles. Newer UI exposes one reader;
        # omitted legacy roles return to their neutral defaults.
        "checker_engine": payload.get("checker", preferences.DEFAULTS["checker_engine"]),
        "arbiter_engine": payload.get("arbiter", preferences.DEFAULTS["arbiter_engine"]),
    }
    normalized = preferences.normalize(roles)
    if normalized is None:
        return _error("模型选择不受支持")
    try:
        current = preferences.load_configuration()
    except preferences.PreferenceError:
        current = {"roles": dict(preferences.DEFAULTS), "models": dict(preferences.DEFAULT_MODELS),
                   "plans": dict(preferences.DEFAULT_PLANS)}
    raw_models = payload.get("models", current["models"])
    normalized_models = preferences.normalize_models(raw_models, defaults=current["models"])
    if normalized_models is None:
        return _error("模型 ID 格式不正确：只能使用 1–160 位字母、数字及 . _ : / + -，且不能填写网址")
    normalized_plans = preferences.normalize_plans(payload.get("plans"), defaults=current["plans"])
    if normalized_plans is None:
        return _error("MiniMax 会员档位不受支持")
    # A service without a key may still be chosen: the worker then reads with
    # the first service that has one, and the status page says which.
    try:
        saved = preferences.save_configuration(normalized, normalized_models, normalized_plans)
    except preferences.PreferenceError as exc:
        return _error(str(exc), 500)
    saved_roles = saved["roles"]
    return JsonResponse({
        "saved": {
            "primary": saved_roles["primary_engine"],
            "checker": saved_roles["checker_engine"],
            "arbiter": saved_roles["arbiter_engine"],
            "models": saved["models"],
            "plans": saved["plans"],
        },
        "restart_required": False,
        "message": "模型选择已保存；下一份任务或下一次重读开始时生效，正在处理的任务不会中途换模型。",
    })


# ---------------------------------------------------------------- 试卷

@csrf_exempt
def _find_duplicate(sha256_value, material_type: str):
    """同一份原件只应有一份任务，归档不等于可以重录。

    1.12.6 之前这里带 ``archived=False``，于是归档过的卷再传一次就会新建任务，
    题库里留下两份同名来源。命中时优先回未归档的那份，那是用户还看得见的。
    """
    return Paper.objects.filter(sha256=sha256_value, material_type=material_type)\
        .exclude(Q(status=Paper.Status.FAILED) & ~Q(error__startswith=CUT_PRODUCED_NOTHING))\
        .order_by("archived", "-created_at").first()


def _distinct_task_name(filename: str) -> str:
    """重录同名原件时给新任务加序号，题库里两条来源必须能分开。"""
    stem, suffix = Path(filename).stem[:248], Path(filename).suffix
    for index in range(2, 100):
        candidate = f"{stem} ({index}){suffix}"
        if not Paper.objects.filter(task_name=candidate).exists():
            return candidate
    return f"{stem} ({uuid.uuid4().hex[:6]}){suffix}"


def papers(request):
    if request.method == "GET":
        if request.GET.get("archived") == "only":
            try:
                offset = int(request.GET.get("offset", "0"))
            except (TypeError, ValueError):
                return _error("归档列表的位置不正确")
            if not 0 <= offset <= 2_147_483_647:
                return _error("归档列表的位置不正确")
            rows = list(Paper.objects.filter(archived=True).order_by("-created_at", "-id")[offset:offset + 201])
            return JsonResponse({
                "papers": [paper_json(p) for p in rows[:200]],
                "next_offset": offset + 200 if len(rows) > 200 else None,
            })
        include_archived = request.GET.get("archived") == "1"
        queryset = Paper.objects.all() if include_archived else Paper.objects.filter(archived=False)
        return JsonResponse({"papers": [paper_json(p) for p in queryset[:200]]})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    mode = request.POST.get("parse_mode", "auto")
    if mode not in intake.MODES:
        return _error("请选择自动准备、手工框题、本地文字 PDF 或 MinerU 自动解析")
    if mode == "mineru" and (not readers.configured("mineru") or not _reading_ready()):
        return _error("上传新资料需要 MinerU Token，以及一家看图读题的密钥（魔搭有免费的）；"
                      "也可以在“设置 → 读题模型”里选“AI 助手读题”，只用 MinerU。"
                      "密钥在“设置 → 服务与密钥”里填写")
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
        return _upload_photos(request, uploads, material_type=material_type, parse_mode=mode)
    upload = uploads[0]
    suffix = Path(upload.name).suffix.lower()
    kind = kinds[0]
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    existing = _find_duplicate(digest.hexdigest(), material_type)
    if existing and not (existing.archived and request.POST.get("force") == "1"):
        return JsonResponse({"paper": paper_json(existing), "duplicate": True,
                             "archived": bool(existing.archived)})
    filename = Path(upload.name).name[:255]
    paper = Paper(
        filename=filename, kind=kind, sha256=digest.hexdigest(),
        material_type=material_type,
        task_name=_distinct_task_name(filename) if existing else "",
        processing_plan={"schema": 1, "mode": mode, "revision": 0},
        status=Paper.Status.READY if mode != "mineru" else Paper.Status.QUEUED,
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
            if mode == "mineru" and kind == "pdf" and import_planning.pdf_requires_chunks(
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
    if mode != "mineru":
        try:
            if mode == "auto":
                paper = intake.prepare_auto(paper, allow_cloud=request.POST.get("allow_cloud") == "1",
                    cloud_ready=_cloud_cut_ready())
            else:
                intake.prepare(paper, mode)
        except Exception as exc:
            paper.status, paper.error = Paper.Status.FAILED, f"本地准备失败（{type(exc).__name__}），原文件已保留，可转手工重试。"
            paper.save(update_fields=["status", "error", "updated_at"])
    return JsonResponse({"paper": paper_json(paper)}, status=201)


def _upload_photos(request, uploads, *, material_type: str = Paper.MaterialType.EXAM, parse_mode: str = "auto") -> JsonResponse:
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
    # “自动切题没切出题”不算失败到可以重传的形状：这份卷已经在库里了，再传一次
    # 应该打开它，而不是多出一份一模一样的任务。
    combined = hashlib.sha256(
        f"photos:{material_type}:{int(enhance)}:{','.join(sorted(digests))}".encode()
    ).hexdigest()
    existing = _find_duplicate(combined, material_type)
    if existing and not (existing.archived and request.POST.get("force") == "1"):
        return JsonResponse({"paper": paper_json(existing), "duplicate": True,
                             "archived": bool(existing.archived)})
    first = Path(uploads[0].name).name
    name = first if len(uploads) == 1 else f"{Path(first).stem} 等 {len(uploads)} 张照片"
    paper = Paper(filename=name[:255], kind="image", sha256=combined, material_type=material_type,
                  task_name=_distinct_task_name(name) if existing else "",
                  processing_plan={"schema": 1, "mode": parse_mode, "revision": 0},
                  status=Paper.Status.READY if parse_mode != "mineru" else Paper.Status.QUEUED)
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
    if parse_mode != "mineru":
        try:
            if parse_mode == "auto":
                paper = intake.prepare_auto(paper, allow_cloud=request.POST.get("allow_cloud") == "1",
                    cloud_ready=_cloud_cut_ready())
            else:
                intake.prepare(paper, parse_mode)
        except Exception as exc:
            paper.status, paper.error = Paper.Status.FAILED, f"照片本地准备失败（{type(exc).__name__}），原照片已保留。"
            paper.save(update_fields=["status", "error", "updated_at"])
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
            or (not paper.blocks.exists() and (paper.processing_plan or {}).get("mode") not in {"manual", "native"}):
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
        body_mode=question.body_mode,
        processing_mode=question.processing_mode,
        content_revision=question.content_revision + 1,
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
                    status=(Paper.Status.READY if (paper.processing_plan or {}).get("mode") in {"manual", "native"}
                            else Paper.Status.SEGMENTING if selected_blocks else Paper.Status.QUEUED),
                    processing_plan=({"schema": 1, "revision": 0, "mode": paper.processing_plan["mode"],
                        "pages": [{"page_idx": page_mapping[item["page_idx"]], "mode": item.get("mode", "manual"),
                                   "warnings": deepcopy(item.get("warnings", []))}
                                  for item in paper.processing_plan.get("pages", []) if item["page_idx"] in page_mapping]}
                        if (paper.processing_plan or {}).get("mode") in {"manual", "native"} else {}),
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
def paper_restore(request, paper_id):
    """Restore visibility only: reviewed cards, snapshots and processing state stay intact."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    restored = paper.archived
    if restored:
        paper.archived = False
        paper.save(update_fields=["archived", "updated_at"])
    return JsonResponse({"paper": paper_json(paper), "archived": False, "restored": restored})


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
                # 只有题库里还活着的题才拦着删。撤回过的记录连同题面快照都留在
                # 题库里：paper 是 SET_NULL，卷名字符串也抄在记录自己身上，所以
                # 删掉这份原卷不会丢掉「这道错题出自我哪份资料」。以前这里数的是
                # 全部记录，题全部撤回之后仍然删不掉，只能归档。
                live_publications = paper.publications.filter(status=PublishedQuestion.Status.PUBLISHED)
                if live_publications.exists():
                    count = live_publications.values("question_id").distinct().count()
                    return _error(
                        f"这项任务还有 {count} 道题在正式题库里；先在正式题库撤回这几道，才能删除这项任务")
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
    paper_tables = tables.table_blocks(paper)
    rows = list(
        paper.questions.select_related("group")
        .prefetch_related("region_reads", library.live_publications_prefetch())
        .order_by("group__sequence", "number", "id")
    )
    # A textbook has 600+ cards: each card's figure review is derived once for
    # the card, its approval, its publication and the counts (1.10.3).
    with reusing_reviews():
        questions = [question_json(q, paper_tables) for q in rows]
        data = paper_json(paper, rows=rows)
    return JsonResponse({"paper": data, "questions": questions})


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
def paper_reparse(request, paper_id):
    """重新解析 (1.10.7): MinerU has been slow on this file; send it again.

    Only while one file is waiting on MinerU.  The worker sees the request at
    its next poll, stops waiting for the old task and uploads the file again.
    A failed paper uses 重试 instead; a book in chunks retries its chunks.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if demo.is_demo(paper):
        return _error("示例练习不调用云服务，请继续手工核对。", 409)
    if paper.status != Paper.Status.PARSING:
        return _error("只有正在等 MinerU 的试卷能重新解析；处理失败的试卷请点“重试”", 409)
    if paper.import_chunks.exists():
        return _error("这份资料是分片交给 MinerU 的；某一片出错后可以单独重跑那一片", 409)
    folder = paper_dir(paper)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / mineru.RESTART_FILE).write_text("restart", encoding="utf-8")
    except OSError:
        return _error("没能通知后台重新解析，请稍后再试", 500)
    return JsonResponse({"paper": paper_json(paper), "message": "已让后台重新把文件交给 MinerU"})


@csrf_exempt
def paper_stop(request, paper_id):
    """Stop locally even when the worker is absent or a remote request is stuck."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        already_stopped = paper.status == Paper.Status.FAILED and paper.error == mineru.STOPPED_MESSAGE
        if not already_stopped:
            if paper.status not in _ACTIVE_PAPER_STATUSES:
                return _error("任务已完成或失败，可以直接查看已有成果", 409)
            plan = deepcopy(paper.processing_plan or {})
            plan["revision"] = int(plan.get("revision", 0)) + 1
            paper.processing_plan = plan
            paper.status, paper.error = Paper.Status.FAILED, mineru.STOPPED_MESSAGE
            paper.save(update_fields=["processing_plan", "status", "error", "updated_at"])
            # Pending reads become obsolete; completed/manual bodies and every
            # approval/publication remain untouched.
            paper.questions.filter(models.Q(ocr_pending=True) | models.Q(reread_requested=True) | models.Q(
                processing_mode="auto", state__in=[Question.State.WAITING, Question.State.READING]
            )).update(content_revision=models.F("content_revision") + 1,
                reread_requested=False, ocr_pending=False)
            RegionRead.objects.filter(question__paper=paper, status__in=region_reads.ACTIVE).update(
                status=RegionRead.Status.FAILED, error="已停止本机处理，框选结果不再采用。", updated_at=timezone.now())
        try:
            # Also wake callers holding an account lease in this worker. The
            # revision is authoritative, so failure to write cannot block stop.
            (paper_dir(paper) / mineru.CANCEL_FILE).write_text("stop", encoding="utf-8")
        except OSError:
            pass
    return JsonResponse({"paper": paper_json(paper), "stopped": True,
        "message": "已停止本机处理，原文件和已有成果已保留。可以重试或删除未入库任务。"})


@csrf_exempt
def paper_read_cut_questions(request, paper_id):
    """Recognize saved crops into normal reviewable drafts; never approve."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    ids = payload.get("question_ids", [])
    if (not isinstance(ids, list) or len(ids) > 2000
            or any(type(value) is not int or value < 1 for value in ids) or len(set(ids)) != len(ids)):
        return _error("请提供不重复的题目编号列表")
    revisions = payload.get("revisions", {})
    if (not isinstance(revisions, dict) or any(not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key)
            or type(value) is not int or value < 0 for key, value in revisions.items())):
        return _error("题目版本格式不正确")
    skipped, queued_ids, question_revisions = [], [], {}
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        plan_revision = int((paper.processing_plan or {}).get("revision", 0))
        if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] != plan_revision):
            return _error("原卷处理方式已发生变化，请刷新后再识读", 409)
        if paper.archived or paper.status != Paper.Status.READY:
            return _error("请先完成原卷处理、继续手工或重试，并确认资料结构，再识读已切题目", 409)
        query = paper.questions.select_for_update().select_related("paper")
        if ids:
            query = query.filter(pk__in=ids)
        questions = list(query.order_by("number", "id"))
        selected_ids = {question.pk for question in questions}
        if (ids and selected_ids != set(ids)) or not {int(key) for key in revisions}.issubset(selected_ids):
            return _error("所选题目不属于当前资料或已在回收站，请刷新后重选", 409)
        if demo.is_demo(paper):
            return JsonResponse({"paper": paper_json(paper), "queued": 0, "queued_ids": [],
                "converted_ids": [], "question_revisions": {}, "skipped": [],
                "message": "练习范围已保存，可直接对照原图核对；练习不调用 AI 识读。"})
        converted_ids = promote_saved_readings(paper.pk)
        if converted_ids:
            questions = list(query.order_by("number", "id"))
        published_ids = set(PublishedQuestion.objects.filter(question_id__in=selected_ids).values_list("question_id", flat=True))
        candidates = []
        for question in questions:
            suggestion = question.ocr_suggestion if isinstance(question.ocr_suggestion, dict) else {}
            if question.approved or question.pk in published_ids:
                reason = "已审核或有入库记录，已保留"
            elif not source_images.is_image(question):
                reason = "已有文字正文，已保留"
            elif question.ocr_pending or question.reread_requested:
                reason = "已在识读队列中"
            elif (suggestion.get("revision") == question.content_revision and not suggestion.get("error")
                    and isinstance(suggestion.get("stem"), str) and suggestion["stem"].strip()):
                reason = "已有识读结果，但当前原卷或人工内容受保护，请对照原卷检查"
            elif question.edited and (question.stem.strip() or question.options):
                reason = "已有人工修改的文字，已保留"
            elif not source_images.valid_regions(paper, question.regions):
                reason = "尚未保存有效切题范围"
            else:
                reason = ""
            if reason:
                skipped.append({"id": question.pk, "reason": reason})
            else:
                expected_revision = revisions.get(str(question.pk), question.content_revision)
                if expected_revision != question.content_revision:
                    return _error("所选题目的范围或内容已发生变化，请刷新后再识读", 409)
                candidates.append(question)
        if candidates and not _vision_ready(paper):
            return _error(_reader_unavailable_sentence(paper)
                + "请在“设置 → 服务与密钥”里补上密钥；也可以直接原图审核，"
                "或由当前 AI 助手对照原图改字。", 409)
        now = timezone.now()
        for question in candidates:
            # Same invalidation as a single reread, once per newly queued card.
            # Keep the ordered regions, body, manual figures and review evidence.
            question.content_revision += 1
            question.reread_requested = True
            question.ocr_pending = True
            question.updated_at = now
            question.save(update_fields=["content_revision", "reread_requested", "ocr_pending", "updated_at"])
            queued_ids.append(question.pk)
            question_revisions[str(question.pk)] = question.content_revision
    message = (f"已提交 {len(queued_ids)} 道已切题目，识读完成后可直接审核。"
               if queued_ids else "没有新的题目需要识读；可直接查看、审核已有结果。")
    if converted_ids:
        message = f"已恢复 {len(converted_ids)} 道已有识读结果，无需重新识读。" + message
    return JsonResponse({"paper": paper_json(paper), "queued": len(queued_ids), "queued_ids": queued_ids,
                         "converted_ids": converted_ids, "question_revisions": question_revisions,
                         "skipped": skipped, "message": message})


@csrf_exempt
def paper_stop_cut_reading(request, paper_id):
    """Stop pending saved-crop reads, including their later manual text rereads."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    ids = payload.get("question_ids", [])
    if (not isinstance(ids, list) or len(ids) > 2000
            or any(type(value) is not int or value < 1 for value in ids) or len(set(ids)) != len(ids)):
        return _error("请提供不重复的题目编号列表")
    revisions = payload.get("revisions", {})
    if (not isinstance(revisions, dict) or any(not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*", key)
            or type(value) is not int or value < 0 for key, value in revisions.items())):
        return _error("题目版本格式不正确")
    stopped_ids, question_revisions = [], {}
    with transaction.atomic():
        paper = get_object_or_404(Paper.objects.select_for_update(), pk=paper_id)
        plan_revision = int((paper.processing_plan or {}).get("revision", 0))
        if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] != plan_revision):
            return _error("原卷处理方式已发生变化，请刷新后再停止识读", 409)
        query = paper.questions.select_for_update()
        if ids:
            query = query.filter(pk__in=ids)
        questions = list(query)
        selected_ids = {question.pk for question in questions}
        if (ids and selected_ids != set(ids)) or not {int(key) for key in revisions}.issubset(selected_ids):
            return _error("所选题目不属于当前资料或已在回收站，请刷新后重选", 409)
        pending = [question for question in questions if
                   (source_images.is_image(question) and (question.ocr_pending or question.reread_requested))
                   or (question.processing_mode == "manual" and question.body_mode == "text" and question.ocr_pending)]
        if any(revisions.get(str(question.pk), question.content_revision) != question.content_revision for question in pending):
            return _error("识读请求已发生变化，请刷新后再停止", 409)
        for question in pending:
            question.content_revision += 1
            question.ocr_pending = False
            question.reread_requested = False
            if not source_images.is_image(question):
                question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
            question.save(update_fields=["content_revision", "ocr_pending", "reread_requested", "state", "updated_at"])
            stopped_ids.append(question.pk)
            question_revisions[str(question.pk)] = question.content_revision
    return JsonResponse({"paper": paper_json(paper), "stopped": len(stopped_ids), "stopped_ids": stopped_ids,
        "question_revisions": question_revisions,
        "message": "已停止本机识读，原图和已有成果已保留；本机不再采用本轮结果，已发出的远端请求可能仍在结束。"
                   if stopped_ids else "没有正在等待的已切题目识读，已有成果已保留。"})


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
        if demo.is_demo(paper):
            return _error("示例练习不调用云服务，请继续手工核对或重新开始练习。", 409)
        if paper.status != Paper.Status.FAILED:
            return _error("只有处理失败的任务需要重试")
        local_mode = (paper.processing_plan or {}).get("mode")
        if local_mode in {"manual", "native"}:
            try:
                if local_mode == "manual":
                    paper, _ = intake.switch_to_manual(paper)
                elif paper.questions.exists() or paper.blocks.exists():
                    paper = intake.select_manual(paper)
                else:
                    intake.prepare(paper, local_mode)
            except Exception as exc:
                return _error(f"本地准备未完成（{type(exc).__name__}），原文件和已有成果已保留", 409)
            (paper_dir(paper) / mineru.CANCEL_FILE).unlink(missing_ok=True)
            return JsonResponse({"paper": paper_json(paper), "message": "已重新准备本地原卷，可继续手工切题"})
        has_blocks = paper.blocks.exists()
        has_questions = paper.questions.exists()
        plan = deepcopy(paper.processing_plan or {})
        plan["revision"] = int(plan.get("revision", 0)) + 1
        if plan.get("continue_preserve_existing") is True:
            plan["continue_revision"] = plan["revision"]
        paper.processing_plan = plan
        fields = ["status", "error", "processing_plan", "updated_at"]
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
        # Continuing missing cuts keeps old manual cards alongside a fresh
        # MinerU parse.  Their presence is not evidence that parsing succeeded:
        # a failed submit/poll/download must retry parsing before reading cards.
        retry_continued_parse = not has_blocks and plan.get("continue_preserve_existing") is True
        paper.status = Paper.Status.QUEUED if retry_continued_parse else \
            Paper.Status.SEGMENTING if has_blocks and not has_questions else \
            Paper.Status.READING if has_questions else Paper.Status.QUEUED
        paper.error = ""
        paper.save(update_fields=fields)
        # A stop request the worker never saw must not stop the retry (1.10.8).
        (paper_dir(paper) / mineru.CANCEL_FILE).unlink(missing_ok=True)
        retry_cards = paper.questions.filter(state=Question.State.RED)
        if plan.get("continue_preserve_existing") is True:
            retry_cards = retry_cards.exclude(pk__in=plan.get("continue_existing_question_ids") or [])
        retry_cards.update(state=Question.State.WAITING)
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
    if demo.is_demo(paper):
        return "示例练习不重新自动切题，请用手工切题继续练习。"
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
        fields = ["status", "error", "updated_at"]
        if (paper.processing_plan or {}).get("continue_preserve_existing") is True:
            plan = deepcopy(paper.processing_plan)
            for key in ("continue_preserve_existing", "continue_revision", "continue_existing_question_ids", "continue_render_sha256"):
                plan.pop(key, None)
            paper.processing_plan = plan
            fields.append("processing_plan")
        paper.save(update_fields=fields)
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
    approver = _approver(_body(request) or {})
    if approver is None:
        return _error("by 只能是 human 或 ai")
    now = timezone.now()
    changed = []
    publication_problems = []
    # 1.12.5：通过不了的题逐条说明原因，不再静默跳过。
    skipped = []
    with transaction.atomic():
        # Yellow too: a figure review saved under an older rule can make a card
        # green on screen (“已自动排除疑似多余图”) while the database still says
        # yellow.  Such cards were shown as green and left out here (1.10.4).
        questions = list(paper.questions.select_for_update().select_related("paper").filter(
            state__in=[Question.State.GREEN, Question.State.YELLOW],
        ))
        # Approving changes nothing the figure review reads, so each card's is worked out once.
        with reusing_reviews():
            for question in questions:
                review = stored_or_derived_review(question)
                # ``stored_or_derived_review`` may rewrite question.state in
                # memory (an upgraded figure review drops the card back to
                # yellow).  Such a card used to be dropped without a word, which
                # is why bulk approval looked like it did nothing.
                if question.state != Question.State.GREEN:
                    pending = [str(flag) for flag in (question.flags or [])][:3]
                    skipped.append({
                        "number": question.number,
                        "reason": ("还有待核查的提醒：" + "；".join(pending)) if pending
                                  else "这道题还有待核查的提醒，请先处理",
                    })
                    continue
                if not question.stem.strip():
                    skipped.append({"number": question.number, "reason": "还没读出题干，请先改字或重新识读"})
                    continue
                if blocks_approval(review):
                    skipped.append({"number": question.number, "reason": blocking_message(review)})
                    continue
                if library.type_blocks_approval(question):
                    skipped.append({"number": question.number, "reason": "题型还没定，请先在题号旁边选一下题型"})
                    continue
                # Already passed by the same kind of reviewer (or by a person): nothing to do.
                if library.approval_is_current(question) \
                        and library.approval_source(question) in {approver[0], "human"}:
                    continue
                if not library.approve(question, now=now, source=approver[0], agent=approver[1]):
                    skipped.append({"number": question.number, "reason": "这道题的通过状态没能更新，请重试"})
                    continue
                question.updated_at = now
                changed.append(question)
        Question.objects.bulk_update(changed, [
            "approved", "approved_at", "approved_content_hash", "approval_source", "approval_agent", "updated_at",
            # the review as shown (and approved), so the card stays green
            "figure_review", "flags", "state",
        ])
        for question in changed:
            library.confirm_published_review(question)
            if not demo.is_demo(paper):
                try:
                    library.publish(question)
                except (ValueError, OSError):
                    # A card is not passed if its immutable bank copy cannot be
                    # saved. Other successfully passed cards stay intact.
                    _clear_approval(question)
                    question.save()
                    publication_problems.append(f"第 {question.number} 题保存未完成，请重试或修复来源图片。")
    count = sum(question.approved for question in changed)
    return JsonResponse({"approved": count, "problems": publication_problems,
                         "skipped": skipped, "paper": paper_json(paper)})


@csrf_exempt
def publish_paper(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if demo.is_demo(paper):
        return JsonResponse({"error": demo.PUBLISH_REFUSED, "demo": True}, status=409)
    if paper.status == Paper.Status.NEEDS_GROUPING:
        return _error("请先确认资料结构或拆分任务，再入库")
    payload = _body(request) or {}
    # 1.10.5: the page sends the cards in small batches (question_ids) so it can
    # show how far it got; without them every approved card is taken (tiyouju).
    ids = payload.get("question_ids")
    if ids is not None and (not isinstance(ids, list) or len(ids) > 200
                            or not all(isinstance(value, int) and not isinstance(value, bool) for value in ids)):
        return _error("question_ids 需为最多 200 个题卡编号")
    questions = paper.questions.filter(approved=True).select_related("paper", "group").prefetch_related(
        Prefetch("publications", queryset=PublishedQuestion.objects.only(*library.LIVE_PUBLICATION_FIELDS,
                                                                          "review_source").order_by("-version"),
                 to_attr="versions"))
    if ids is not None:
        questions = questions.filter(pk__in=ids)
    created, unchanged, problems = 0, 0, []
    for question in questions:
        if library.already_published(question):
            unchanged += 1
            continue
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
def demo_paper(request):
    """Open the practice paper for 新手教学 (``reset`` starts it over)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request) or {}
    try:
        course = payload.get("course", "full")
        if course not in {"full", "basics"}:
            return _error("请选择基础练习或完整示例")
        paper = demo.create_demo_paper(reset=payload.get("reset") is True, course=course)
    except FileNotFoundError as error:
        return _error(str(error), 500)
    return JsonResponse({"paper": paper_json(paper),
        "restart_required": course == "basics" and (paper.structure or {}).get("course") != "basics",
        "practice": {
        "library_url": f"/practice/{paper.pk}", "export_url": f"/api/demo/{paper.pk}/export-pdf"}}, status=201)


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
    default_mode = "manual" if (paper.processing_plan or {}).get("mode") in {"manual", "native"} else "auto"
    processing_mode = payload.get("processing_mode", default_mode)
    body_mode = payload.get("body_mode", "source_image" if processing_mode == "manual" else "text")
    kind = payload.get("question_type", "unknown")
    if processing_mode not in {"manual", "auto", "assistant"} or body_mode not in {"text", "source_image"} or kind not in TYPES:
        return _error("题目正文模式或题型不正确")
    if demo.is_demo(paper):
        processing_mode = "manual"
    manual = processing_mode != "auto" or body_mode == "source_image"
    question = Question.objects.create(
        paper=paper, group=group, number=number, regions=regions, regions_auto=regions, start_source="manual",
        source_kind=Question.SourceKind.MANUAL, source_anchor_seq=None,
        figure_candidates=candidates_in(paper, regions), reread_requested=not manual,
        processing_mode=processing_mode, body_mode=body_mode, question_type=kind,
        type_locked=manual and qtypes.decided(kind),
        state=Question.State.YELLOW if manual else Question.State.WAITING,
        flags=["请对照原卷确认范围完整。"] if manual else [],
    )
    return JsonResponse({"question": question_json(question)}, status=201)


# ---------------------------------------------------------------- 题卡

def _question(question_id) -> Question:
    return get_object_or_404(Question.objects.select_related("paper"), pk=question_id)


def _saved_configuration() -> dict | None:
    try:
        return preferences.load_configuration() if preferences.preference_path().is_file() else None
    except preferences.PreferenceError:
        return None


def _reader_scope(paper: Paper | None = None):
    """The service scope this paper's reading worker will run in."""
    return readers.selected_services_only(bool((paper.processing_plan or {}).get("auto_fallback")) if paper else False)


def _reader_readiness(paper: Paper | None = None) -> dict:
    """The same readiness answer the worker will give, for this paper.

    An automatic import that fell back to manual keeps ``auto_fallback``, and
    the worker then keeps that scope: a service that already answered may not be
    swapped for another when its call fails.  A service that was never usable is
    not in that scope, so asking here and asking in the worker now give the same
    answer instead of “可以读” followed by “没有配置所选主读模型的 API Key”.
    """
    with _reader_scope(paper):
        return readers.primary_readiness(_saved_configuration())


def _vision_ready(paper: Paper | None = None) -> bool:
    """A vision service can read this paper (框选识读 and 识读这题 both need one;
    AI-assistant reading has none and is handled by its own branch)."""
    with _reader_scope(paper):
        return readers.primary_engine(_saved_configuration()) is not None


def _reading_ready() -> bool:
    """A new paper can be read: some vision service has a key (the worker
    falls back to it), or the saved choice is AI-assistant reading."""
    return _vision_ready() or readers.assistant_mode(_saved_configuration())


def _cloud_cut_ready() -> bool:
    """Cloud cutting only needs MinerU.

    1.12.7: this used to be ``configured("mineru") and _reading_ready()``, which
    tied two different jobs together.  MinerU reads the page and cuts the cards
    by itself; a vision service is only needed afterwards, when someone asks to
    read one specific card.  Requiring it here meant a machine with a perfectly
    good MinerU token silently refused to cut photos and scans at all — the
    teacher got "处理失败" with no way to tell what was missing.  The missing
    reader is now reported where it actually matters, at the reading step.
    """
    return readers.configured("mineru")


def _reader_unavailable_sentence(paper: Paper | None) -> str:
    """Say which service is missing instead of “配置一家模型”.

    A teacher can act on “还没有配置任何看图读题服务”; “请先配置一家看图读题模型”
    makes them open the settings and compare lists themselves.  Reaching this
    sentence means nothing at all is configured — a configured service is
    always used, even when the chosen one has no key.  The original image and
    every saved question stay either way.
    """
    readiness = _reader_readiness(paper)
    service = str(readiness.get("label") or "")
    return (f"还没有可用的看图读题模型：所选的“{service}”还没有 API Key，本机也没有其他已配置的服务。" if service
        else "还没有可用的看图读题模型，本机没有已配置的服务。")


def _clear_approval(question: Question) -> None:
    question.approved = False
    question.approved_at = None
    question.approved_content_hash = ""
    question.approval_source = ""
    question.approval_agent = ""


def _decided_by(review: dict, actor: tuple[str, str]) -> dict:
    """A figure decision made through the API stays a manual decision (so the
    automatic check never overrides it); an AI assistant's is labelled as such."""
    if actor[0] != "ai":
        return review
    reason = review.get("reason", "")
    reason = reason.replace("已人工确认", f"{actor[1]}确认").replace("由人工设置", f"由{actor[1]}设置")
    return {**review, "reason": reason, "decided_by": "ai", "agent": actor[1]}


def _approver(payload: dict) -> tuple[str, str] | None:
    """Who is approving: the page is a person; tiyouju/MCP say ``by: "ai"``."""
    source = payload.get("by", "human")
    if source not in library.APPROVAL_SOURCES:
        return None
    return source, library.agent_name(payload.get("agent")) if source == "ai" else ""


def _apply_figure_review(question: Question, review: dict) -> None:
    """Store a local decision and keep legacy flags/state in sync for old clients."""
    question.figure_review = review
    question.flags = [flag for flag in (question.flags or []) if not figure_flag(flag)]
    if review.get("source") == "human":
        # A person picked the figures or confirmed there is none: “别的题认为有一张图
        # 属于本题，请确认是否需要” is answered (it stayed on cards confirmed 无图).
        question.flags = [flag for flag in question.flags if flag not in DECISION_FLAGS]
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
def question_region_read(request, question_id):
    """框选识读：POST 排队读原卷上框出的一小块（只读，不改题卡）；DELETE 收起结果。"""
    if request.method not in {"POST", "DELETE"}:
        return HttpResponseNotAllowed(["POST", "DELETE"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    question = get_object_or_404(Question.objects.select_related("paper"), pk=question_id)
    if request.method == "POST" and demo.is_demo(question.paper):
        return _error("示例练习不调用云服务。可以练习画框，再对照原卷手工改字。", 409)
    request_id = payload.get("client_request_id")
    if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id)):
        return _error("识读请求编号不正确")
    if request.method == "DELETE":
        read_id = payload.get("read_id")
        if read_id is not None and (type(read_id) is not int or read_id < 1):
            return _error("识读记录编号不正确")
        with transaction.atomic():
            question = get_object_or_404(Question.objects.select_for_update().select_related("paper"), pk=question_id)
            jobs = question.region_reads.all()
            if read_id is not None:
                jobs = jobs.filter(pk=read_id)
            if request_id is not None:
                matched = jobs.filter(recommendation__client_request_id=request_id).first()
                if matched is None and not question.region_reads.filter(recommendation__client_request_id=request_id).exists():
                    matched = RegionRead(question=question, page_idx=0, bbox=[], target="auto")
                if matched is not None:
                    matched.status, matched.error = RegionRead.Status.FAILED, "已取消本机等待"
                    matched.recommendation = {"client_request_id": request_id, "cancelled": True}
                    matched.save()
            else:
                jobs.delete()
        return JsonResponse({"question": question_json(question)})
    if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] != question.content_revision):
        return _error("题目已发生变化，请刷新后重新框选", 409)
    target = payload.get("target", "auto")
    if target not in region_reads.TARGETS:
        return _error("请选择读出来的文字填到题干还是哪个选项")
    page_idx = payload.get("page_idx")
    pages = {int(page["page_idx"]) for page in question.paper.pages or []}
    if type(page_idx) is not int or page_idx not in pages:
        return _error("页码不正确")
    bbox = _valid_bbox(payload.get("bbox"))
    if bbox is None or bbox[2] - bbox[0] < 4 or bbox[3] - bbox[1] < 2:
        return _error("框太小了，请框住要识读的整行字")
    if not _vision_ready(question.paper):
        return _error(region_reads.NO_ENGINE)
    with transaction.atomic():
        question = get_object_or_404(Question.objects.select_for_update().select_related("paper"), pk=question_id)
        if "revision" in payload and payload["revision"] != question.content_revision:
            return _error("题目已发生变化，请刷新后重新框选", 409)
        if request_id is not None and question.region_reads.filter(recommendation__client_request_id=request_id).exists():
            return JsonResponse({"question": question_json(question)})
        question.region_reads.exclude(recommendation__cancelled=True, recommendation__has_key="cancelled").delete()
        recommendation = region_reads.queued_recommendation(question)
        if request_id is not None:
            recommendation["client_request_id"] = request_id
        RegionRead.objects.create(question=question, page_idx=page_idx, bbox=bbox, target=target,
                                  recommendation=recommendation)
    return JsonResponse({"question": question_json(question)})


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
        actor = _approver(payload)
        if actor is None:
            return _error("by 只能是 human 或 ai")
        if "revision" in payload and (type(payload["revision"]) is not int or payload["revision"] != question.content_revision):
            return _error("题目已发生变化，请刷新后再操作", 409)
        if action == "approve":
            if question.paper.status == Paper.Status.NEEDS_GROUPING:
                return _error("请先确认资料结构或拆分任务，再标记题卡通过")
            value = payload.get("approved", True)
            if type(value) is not bool:
                return _error("approved 必须是 true 或 false")
            approver = actor
            if value and question.state not in library.REVIEWABLE_STATES:
                return _error("这道题尚未进入可审核状态，请先完成识读或人工修正")
            if value and not source_images.body_valid(question):
                return _error("缺少有效正文或原卷裁片，请先修正范围或改字")
            review = source_images.review(question)
            if value and blocks_approval(review):
                return _error(f"这道题暂时不能通过：{blocking_message(review)}。请先补配图，或确认本题确实无图")
            if value and library.type_blocks_approval(question):
                return _error(f"这道题暂时不能通过：{qtypes.BLOCK_MESSAGE}（单选、多选、填空、判断或解答）")
            if value:
                library.approve(question, now=now, source=approver[0], agent=approver[1])
                library.confirm_published_review(question)
            elif approver[0] == "ai" and library.approval_source(question) == "human":
                return _error("这道题是人工通过的，AI 助手不能撤销；需要的话请使用者自己在题卡上取消", 409)
            else:
                _clear_approval(question)
        elif action == "apply-reading":
            return _error("识读结果会直接写入待审核题面，无需再采用；请刷新查看题目。", 409)
        elif action == "body" or action == "manual":
            body_mode = payload.get("body_mode", "source_image" if action == "manual" else None)
            if body_mode not in {"text", "source_image"}:
                return _error("请选择原图正文或文字正文")
            if body_mode == "text" and not question.stem.strip():
                return _error("文字正文还为空，请先改字填写题干，或完成 AI 识读")
            if body_mode == "source_image" and not source_images.valid_regions(question.paper, question.regions):
                return _error("原图正文需要有效的原卷范围")
            question.body_mode = body_mode
            question.processing_mode = "manual"
            question.reread_requested = False
            question.state = Question.State.YELLOW
            question.error = ""
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
            if "origin" in payload and (not isinstance(payload["origin"], str) or len(payload["origin"]) > 400):
                return _error("题源格式不正确")
            origin = prose.clean_origin(payload["origin"]) if "origin" in payload else question.origin
            tidied = prose.tidy_fields({
                "stem": fix_symbols(stem.strip()),
                "options": {k: fix_symbols(v.strip()) for k, v in options.items() if v.strip()},
                "question_type": kind,
                "answer": fix_symbols(payload.get("answer", question.answer).strip()),
                "analysis": fix_symbols(payload.get("analysis", question.analysis).strip()),
            }, origin=origin)
            previous_type = question.question_type
            question.stem = tidied["stem"].strip()
            question.options = tidied["options"]
            question.question_type = tidied.get("question_type", kind)
            question.answer = tidied["answer"]
            question.analysis = tidied["analysis"]
            question.origin = tidied["origin"]
            if question.question_type != previous_type and qtypes.decided(question.question_type):
                question.type_locked = True
            question.edited = True
            question.body_mode = "text"
            question.reread_requested = False
            # “assistant”：AI 助手（tiyouju）改的字，题卡上不说成“人工修改”。
            question.text_source = "assistant" if actor[0] == "ai" else "human"
            # 只留下截图范围的提醒；“MinerU 初稿”这类提醒随这次改字一起解决。
            question.flags = [f for f in question.flags
                              if not figure_flag(f) and "截图" in f and f not in TEXT_DRAFT_FLAGS]
            question.figure_review = {}
            review = stored_or_derived_review(question, ignored_candidates=previous_ignored)
            if previous_ignored:
                review = {
                    **review,
                    "ignored_candidates": previous_ignored,
                    "excluded_count": len(previous_ignored),
                }
            _apply_figure_review(question, review)
            question.flags = qtypes.with_flag(question.flags, question.question_type)
            question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
            question.error = ""
            # 保存编辑和终审是两个独立动作；人必须看到保存后的最终版本再点“通过”。
            _clear_approval(question)
        elif action == "type":
            # 只改题型（题卡头上的题型下拉、题库里“题型待核对”点进来）。改了题型，
            # 题目内容就变了，和改字一样要重新标记通过。
            kind = payload.get("question_type")
            if kind not in qtypes.DECIDED_TYPES:
                return _error("请选单选、多选、填空、判断或解答")
            if question.state in {Question.State.WAITING, Question.State.READING}:
                return _error("这道题还在识读，读完再选题型")
            if kind != question.question_type:
                question.question_type = kind
                question.flags, question.state = qtypes.sync(question.flags, question.state, kind)
                _clear_approval(question)
            question.type_locked = True
        elif action == "regions":
            regions = _valid_regions(question.paper, payload.get("regions"))
            if regions is None:
                return _error("范围不正确")
            question.regions = regions
            question.figure_candidates = candidates_in(question.paper, regions)
            _clear_approval(question)
            manual = demo.is_demo(question.paper) or payload.get("processing_mode", question.processing_mode) in {"manual", "assistant"} or source_images.is_image(question)
            if manual:
                question.processing_mode = "manual"
                # Range changes do not erase manually corrected text, type or
                # illustrations. Whole-image bodies only use the new regions.
                question.state = Question.State.YELLOW
                question.flags = qtypes.with_flag(["原卷范围已修改，请重新对照确认。"], question.question_type)
                question.reread_requested = False
                question.error = ""
            else:
                question.figures = []
                question.figure_review = {}
                question.edited = False
                question.type_locked = False
                question.flags = []
                question.state = Question.State.WAITING
                question.reread_requested = True
        elif action == "reread":
            if demo.is_demo(question.paper):
                return _error("示例练习不调用云服务。请对照原卷手工改字。", 409)
            if source_images.is_image(question) and (question.paper.archived or question.paper.status != Paper.Status.READY):
                return _error("请先完成原卷处理、继续手工或重试，再识读已切题目。", 409)
            if (source_images.is_image(question) and (question.approved or question.publications.exists()
                    or (question.edited and (question.stem.strip() or question.options)))):
                return _error("这道原图题已有审核、入库或人工文字，已保留；请对照原卷手动修改。", 409)
            if (not source_images.is_image(question) and actor[0] == "ai"
                    and library.approval_source(question) == "human" and library.approval_is_current(question)
                    and payload.get("force") is not True):
                return _error("这道题已由使用者人工通过；需要使用者明确要求重新识读，才能撤销当前审核。", 409)
            if (source_images.is_image(question) or question.processing_mode == "manual") and (
                    not _vision_ready(question.paper) or readers.assistant_mode()):
                return _error(_reader_unavailable_sentence(question.paper)
                    + "请在“设置 → 服务与密钥”里补上密钥后重新识读；也可以直接由当前 AI 助手对照原图改字，原图始终保留。", 409)
            if not source_images.is_image(question):
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
            if not source_images.is_image(question):
                _clear_approval(question)
                question.state = Question.State.WAITING
            question.reread_requested = True
            question.ocr_pending = source_images.is_image(question) or question.processing_mode == "manual"
        elif action == "figure-table":
            # A crop MinerU read as a table becomes a text table in the stem.
            index = payload.get("figure")
            if type(index) is not int or not 0 <= index < len(question.figures or []):
                return _error("找不到这张配图")
            blocks = tables.table_blocks(question.paper)
            block = tables.table_for_figure(question.paper, question.figures[index], blocks)
            table_text = tables.to_text(block["html"]) if block else ""
            if not table_text:
                return _error("这张图没有可用的表格文字；请用“改字”手动输入表格")
            pieces = [block, *tables.continuations(block, blocks)]
            removed, kept = [], []
            for figure in question.figures:
                figure_pieces = [figure, *[p for p in (figure.get("parts") or []) if isinstance(p, dict)]]
                target = kept
                if figure is question.figures[index] or any(
                        tables.covers(piece, item) for piece in pieces for item in figure_pieces):
                    target = removed
                target.append(figure)
            before = [
                text for text in Block.objects.filter(paper=question.paper, seq__lt=block["seq"])
                .exclude(type__in=["image", "table", "chart", "header", "footer", "page_number",
                                   "page_footnote", "aside_text"])
                .order_by("-seq").values_list("text", flat=True)[:2]
                if text and text.strip()
            ]
            question.stem = tables.insert_table(question.stem, table_text, before, key=witness_key)
            candidate_keys = _candidate_keys(question)
            resolved = {
                key for piece in pieces
                for candidate in (question.figure_candidates or [])
                if tables.covers(piece, candidate) and (key := _candidate_key(candidate)) in candidate_keys
            } | _selected_candidate_keys(question, removed)
            previous_ignored = _saved_ignored_candidates(question)
            ignored = sorted(set(previous_ignored) | resolved)
            question.figures = kept
            question.edited = True
            question.text_source = "human"
            question.figure_review = {}
            review = stored_or_derived_review(question, ignored_candidates=ignored)
            review = {**review, "ignored_candidates": ignored, "excluded_count": len(ignored)}
            _apply_figure_review(question, review)
            if question.state in library.REVIEWABLE_STATES:
                question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
            _clear_approval(question)
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
                # A table cut by a page break: the pieces below are joined to
                # this figure and shown as one image.
                if "parts" in item:
                    raw_parts = item.get("parts")
                    if not isinstance(raw_parts, list) or len(raw_parts) > library.MAX_FIGURE_PARTS:
                        return _error("拼接配图格式不正确")
                    parts = []
                    for raw_part in raw_parts:
                        part_bbox = _valid_bbox(raw_part.get("bbox")) if isinstance(raw_part, dict) else None
                        if part_bbox is None or raw_part.get("page_idx") not in pages:
                            return _error("拼接配图格式不正确")
                        part = {"page_idx": raw_part["page_idx"], "bbox": part_bbox}
                        part_key = raw_part.get("candidate_key")
                        if part_key is not None:
                            if not isinstance(part_key, str) or part_key not in candidate_keys:
                                return _error("配图候选来源格式不正确")
                            part["candidate_key"] = part_key
                        elif (exact_key := _candidate_key(part)) in candidate_keys:
                            part["candidate_key"] = exact_key
                        parts.append(part)
                    if parts:
                        cleaned_item["parts"] = parts
                cleaned.append(cleaned_item)
            selected_candidate_keys = {
                piece["candidate_key"]
                for item in cleaned for piece in (item, *item.get("parts", []))
                if "candidate_key" in piece
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
            _apply_figure_review(question, _decided_by(review, actor))
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
                _apply_figure_review(question, _decided_by({
                    "status": CONFIRMED_NO_FIGURE, "source": "human", "reason": "已人工确认本题确实无图",
                    "signals": ["human_confirmed_no_figure"],
                    "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": len(question.figure_candidates or []), "confirmed_at": now.isoformat(),
                    "ignored_candidates": sorted(_candidate_keys(question)),
                    "previous_ignored_candidates": previous_ignored,
                    "previous_figures": previous_figures,
                }, actor))
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
                # The figures put back still came from another card's reading or a shared row.
                for source, flag in (("other", FLAG_FOREIGN_FIGURE), ("row", FLAG_ROW_FIGURE)):
                    if any(item.get("source") == source for item in restored) and flag not in question.flags:
                        question.flags.append(flag)
                if question.state in library.REVIEWABLE_STATES:
                    question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
            _clear_approval(question)
        else:
            raise Http404()
        if action != "approve":
            question.content_revision += 1
            if action != "reread":
                question.ocr_suggestion = {}
                question.ocr_pending = False
        question.save()
        if action == "approve" and value and not demo.is_demo(question.paper):
            try:
                library.publish(question)
            except (ValueError, OSError) as error:
                transaction.set_rollback(True)
                return _error(f"通过未保存，题库写入失败：{error}。请重试，原题保留。", 409)
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


def question_crop(request, question_id):
    """This question's original crop as one PNG, for AI assistants (tiyouju show).

    ``?marks=1`` draws the figure candidates as blue boxes numbered 1, 2…
    (tiyouju calls them 图1、图2…), so an assistant can name one by number.
    The labels are plain digits: the label font may have no Chinese glyphs.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    question = _question(question_id)
    if not question.regions:
        return _error("这道题还没有原卷范围", 404)
    marks = None
    if request.GET.get("marks") == "1":
        marks = [
            {"label": str(index + 1), "page_idx": candidate["page_idx"], "bbox": candidate["bbox"]}
            for index, candidate in enumerate(question.figure_candidates or [])
            if isinstance(candidate, dict) and _valid_bbox(candidate.get("bbox")) is not None
        ]
    store = PageStore(question.paper)
    try:
        image, _layout = imaging.stack_regions(question.regions, store.load, marks or None)
    except (OSError, ValueError, KeyError):
        return _error("原卷截图暂时生成不了，请稍后再试", 409)
    image.thumbnail((2000, 2000))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "PNG", optimize=True)
    response = HttpResponse(buffer.getvalue(), content_type="image/png")
    response["Cache-Control"] = "no-store"
    return response


def question_figure(request, question_id, index: int):
    question = _question(question_id)
    if not 0 <= index < len(question.figures):
        raise Http404()
    return _file(library.figure_file(question, index), "image/png")


def question_image(request, question_id, index: int):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    question = _question(question_id)
    if not source_images.is_image(question) and question.processing_mode != "manual":
        raise Http404()
    try:
        return _file(source_images.asset_file(question, index), "image/png")
    except (OSError, ValueError, IndexError, RuntimeError):
        return _error("原卷裁片无法生成，请检查原文件和范围", 409)


def question_ocr_figure(request, question_id, index: int):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    question = _question(question_id)
    suggestion = question.ocr_suggestion or {}
    figures = suggestion.get("figures") or []
    if suggestion.get("revision") != question.content_revision or not 0 <= index < len(figures):
        raise Http404()
    question.figures = figures
    return _file(library.figure_file(question, index), "image/png")


@csrf_exempt
def paper_continue_ai_cut(request, paper_id):
    return continue_ai_cut.continue_ai_cut(request, paper_id)


@csrf_exempt
def paper_processing(request, paper_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if payload is None or payload.get("mode") != "manual":
        return _error("目前支持保留成果并转为手工框题")
    pages = payload.get("pages")
    if pages is not None:
        # Per-page read suppression is insufficient: the current cloud parser
        # still uploads the whole original document. Do not advertise a page
        # isolation contract until providers honor it before uploading bytes.
        return _error("本版仅支持整份转为手工框题；已有成功题目和人工修改会保留。")
    revision = payload.get("revision")
    if revision is not None and (type(revision) is not int or revision < 0):
        return _error("原卷版本格式不正确")
    paper = get_object_or_404(Paper, pk=paper_id)
    try:
        paper, changed = intake.switch_to_manual(paper, expected_revision=revision)
    except (intake.ManualSwitchConflict, intake.ManualPreparationError) as exc:
        paper.refresh_from_db()
        return JsonResponse({"error": str(exc), "paper": paper_json(paper), "manual_ready": False}, status=409)
    return JsonResponse({"paper": paper_json(paper), "changed": changed, "manual_ready": True,
        "message": "已停止 MinerU，原卷和已有成果已保留，可以继续手工切题。" if changed else "已可继续手工切题。"})


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
    review = request.GET.get("review", "").strip()
    if review in library.APPROVAL_SOURCES:
        rows = rows.filter(review_source=review)
    answer = request.GET.get("answer", "").strip()
    if answer in {"yes", "no"}:
        missing = models.Q(content__answer="") | models.Q(content__answer__isnull=True)
        rows = rows.exclude(missing) if answer == "yes" else rows.filter(missing)
    tag = request.GET.get("tag", "").strip()
    if tag:
        rows = rows.filter(tags_text__contains=library.tags_text([tag]))
    for term in request.GET.get("q", "").split()[:8]:
        key = library.search_key(term)
        if key:
            rows = rows.filter(search_text__contains=key)
    return rows


def library_list(request):
    from .library_browse import library_list as browse
    return browse(request)


def library_detail(request, publication_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    history = library.publication_history(publication)
    body = {
        "publication": library.publication_json(publication),
        "versions": [row for row in history if row["id"] != str(publication.id)],
        "history": history,
    }
    body.update(library.related_publication_sources(publication))
    if compare_id := request.GET.get("compare"):
        try:
            compare_id = uuid.UUID(compare_id)
        except ValueError:
            return _error("对照版本编号不正确")
        # Cross-card comparison would give unrelated questions a false history.
        if not publication.question_id:
            return _error("这份历史题面已没有关联题卡，无法比较其他版本")
        before = get_object_or_404(PublishedQuestion, pk=compare_id, question_id=publication.question_id)
        body["comparison"] = {
            "publication": library.publication_json(before),
            "changes": library.publication_changes(before, publication),
        }
    return JsonResponse(body)


def library_figure(request, publication_id, name):
    get_object_or_404(PublishedQuestion, pk=publication_id)
    if not re.fullmatch(r"figure-\d{1,3}\.png", name):
        raise Http404()
    return _file(settings.DATA_ROOT / "library" / str(publication_id) / name, "image/png")


def library_question_image(request, publication_id, name):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    if not re.fullmatch(r"question-\d{1,2}\.png", name):
        raise Http404()
    image = next((item for item in (publication.content or {}).get("question_images", []) if item.get("file") == name), None)
    if (publication.content or {}).get("body_mode") != "source_image" or image is None:
        raise Http404()
    path = settings.DATA_ROOT / "library" / str(publication_id) / name
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != image["image_sha256"]:
            return _error("正式题原图校验失败，请检查快照文件", 409)
    except (OSError, KeyError):
        raise Http404()
    return _file(path, "image/png")


def library_source_page(request, publication_id, page):
    """Only pages referenced by this published snapshot, never draft regions."""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    sources = (publication.content or {}).get("sources") or []
    if not publication.paper_id or not any(isinstance(source, dict) and source.get("page_idx") == page for source in sources):
        raise Http404("这道入库题没有引用这一页原卷")
    return page_preview(request, publication.paper_id, page)


def library_crop(request, publication_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from . import library_assistant
    try:
        response = HttpResponse(library_assistant.crop_png(publication_id), content_type="image/png")
        response["Cache-Control"] = "no-store"
        return response
    except library_assistant.AssistantError as error:
        return _error(str(error), getattr(error, "status", 409))


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


@csrf_exempt
def library_withdraw_batch(request):
    """一批题一起从题库撤下。

    1.12.6 之前只有单题撤回，清一份 25 题的卷要点 25 下。这里一个事务里逐条
    撤回并逐条回报结果：哪几条撤了、哪几条没撤、为什么没撤。不静默吞掉，
    否则用户会以为 25 道全撤了，其实有几道根本没动。
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    from . import library_browse
    try:
        ids = library_browse.normalize_ids((_body(request) or {}).get("ids"))
    except library_browse.BrowseError as error:
        return _error(str(error))
    if not ids:
        return _error("请先勾选要撤回的题目")
    found = {str(pk): pk for pk in PublishedQuestion.objects.filter(pk__in=ids).values_list("pk", flat=True)}
    withdrawn, skipped = [], []
    with transaction.atomic():
        for value in ids:
            if value not in found:
                skipped.append({"id": value, "reason": "题库里已经没有这道题"})
                continue
            publication = PublishedQuestion.objects.select_related("paper").get(pk=found[value])
            if publication.status == PublishedQuestion.Status.WITHDRAWN:
                skipped.append({"id": value, "reason": "这道题已经撤回过了"})
                continue
            if publication.status == PublishedQuestion.Status.SUPERSEDED:
                skipped.append({"id": value, "reason": "已被新版替代，请撤回当前那一版"})
                continue
            library.withdraw(publication)
            withdrawn.append(value)
    return JsonResponse({"withdrawn": withdrawn, "withdrawn_count": len(withdrawn), "skipped": skipped})


# ---------------------------------------------------------------- 功能开关与题库任务

@csrf_exempt
def library_ai_settings_view(request):
    from . import library_ai_settings
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("标签与答案设置只能在本机使用", 403)
    if request.method == "GET":
        return JsonResponse(library_ai_settings.public_status())
    rejected = _guard(request)
    if rejected:
        return rejected
    try:
        return JsonResponse(library_ai_settings.save(_body(request)))
    except library_ai_settings.ServiceError as error:
        return _error(str(error), 409)
    except library_ai_settings.SettingsError as error:
        return _error(str(error), 400)


@csrf_exempt
def library_ai_share_reading_key_view(request):
    """Copy the reading side's MiniMax key into the answers API store.

    One key, typed once.  Separate POST because it overwrites whatever the
    answers side had: the teacher has to mean it.
    """
    from . import library_ai_settings
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("共用密钥只能在本机使用", 403)
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request) or {}
    try:
        return JsonResponse(library_ai_settings.share_reading_key(payload.get("provider", "minimax")))
    except library_ai_settings.ServiceError as error:
        return _error(str(error), 409)
    except library_ai_settings.SettingsError as error:
        return _error(str(error), 400)


@csrf_exempt
def library_ai_test_view(request):
    from . import library_ai_settings
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return _error("连接测试只能在本机使用", 403)
    rejected = _guard(request)
    if rejected:
        return rejected
    try:
        return JsonResponse(library_ai_settings.test_connection(_body(request)))
    except library_ai_settings.ServiceError as error:
        return _error(str(error), 409)
    except library_ai_settings.SettingsError as error:
        return _error(str(error), 400)

@csrf_exempt
def feature_settings(request):
    """GET 读、POST 改设置里的“功能开关”（没有密钥，网页和工作者共用）。"""
    if request.method == "GET":
        return JsonResponse({"features": features.describe(), "knowledge_file": str(knowledge.path())})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    try:
        features.save((payload or {}).get("features"))
    except features.FeatureError as error:
        return _error(str(error))
    except OSError:
        return _error("开关没能保存，请检查数据目录是否可写", 500)
    if features.enabled("knowledge_tags"):
        try:
            knowledge.ensure_file()
        except OSError:
            pass
    message = "已保存。题源和引号的整理对新读的题、改字保存的题立即生效；老题在下次启动时整理。"
    return JsonResponse({"features": features.describe(), "knowledge_file": str(knowledge.path()),
                         "message": message})


@csrf_exempt
def library_jobs_view(request):
    """排队补知识点或 AI 参考答案：{kind, ids} 或 {kind, missing: true}（所有还没有的）。"""
    from . import library_job_control
    if request.method == "GET":
        ids = request.GET.get("ids", "").split(",")
        if not 1 <= len(ids) <= 500 or any(not value for value in ids):
            return _error("请明确提供本次选题编号 ids，最多 500 题")
        try:
            wanted = [uuid.UUID(value) for value in ids]
        except ValueError:
            return _error("题库条目编号格式不正确")
        scoped = request.GET.get("solution_scope") == "true"
        library_job_control.expire_api_jobs()
        rows = list(LibraryJob.objects.filter(publication_id__in=wanted, solution_scope=scoped).order_by("-created_at")[:1000])
        return JsonResponse({"jobs": [library_job_control.job_json(row) for row in rows],
                             "assistant_handoff": library_job_control.assistant_handoff(rows)})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request) or {}
    kind = payload.get("kind")
    if kind not in {LibraryJob.Kind.TAGS, LibraryJob.Kind.ANSWER}:
        return _error("kind 只能是 tags 或 answer")
    solution_scope = payload.get("solution_scope", False)
    if type(solution_scope) is not bool or (solution_scope and (kind != "answer" or payload.get("missing") is True)):
        return _error("本次答案解析建议必须明确选择题目，只能用于 answer")
    if "executor" in payload and (payload["executor"] != "api" or not solution_scope):
        return _error("指定 API 执行只能用于明确选择的答案解析任务")
    if "api_only" in payload and type(payload["api_only"]) is not bool:
        return _error("api_only 应为 true 或 false")
    live = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
    if payload.get("missing") is True:
        if kind == LibraryJob.Kind.TAGS:
            targets = [item for item in live if not library.tags_of(item.extras)
                       and not (item.extras or {}).get("tags_at")]
        else:
            targets = [item for item in live if not str((item.content or {}).get("answer") or "").strip()
                       and not (item.extras or {}).get("ai_answer")]
    else:
        ids = payload.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500:
            return _error("ids 应是 1–500 个题库条目编号")
        try:
            wanted = [uuid.UUID(str(value)) for value in ids]
        except ValueError:
            return _error("题库条目编号格式不正确")
        targets = list(live.filter(pk__in=wanted))
        targets = targets if solution_scope else [item for item in targets if not library.tags_of(item.extras)] if kind == LibraryJob.Kind.TAGS else [
            item for item in targets if not str((item.content or {}).get("answer") or "").strip()
            and not (item.extras or {}).get("ai_answer")]
    requested = len(set(str(value) for value in payload.get("ids", []))) if payload.get("missing") is not True else len(targets)
    queued = 0
    executors = set()
    jobs = []
    try:
        with transaction.atomic():
            for publication in targets:
                options = {"solution_scope": solution_scope}
                if "executor" in payload:
                    options["executor"] = payload["executor"]
                if payload.get("api_only") is True:
                    options["api_only"] = True
                job = library_jobs.enqueue(publication, kind, **options)
                executors.add(job.executor)
                jobs.append(job)
                queued += 1
    except library_jobs.JobError as error:
        return _error(str(error), 409)
    from . import library_ai_settings
    mode = next(iter(executors)) if len(executors) == 1 else "mixed" if executors else library_ai_settings.public_status()["mode"]
    return JsonResponse({"queued": queued, "skipped": requested - queued, "executor": mode,
                         "jobs": [library_job_control.job_json(job) for job in jobs],
                         "assistant_handoff": library_job_control.assistant_handoff(jobs),
                         "message": "已加入待助手处理；请让正在操作软件的豆包或 AI 助手领取并写回。" if mode == "assistant"
                         else "已加入独立模型任务队列。"})


@csrf_exempt
def library_jobs_cancel(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    from . import library_job_control
    try:
        return JsonResponse(library_job_control.cancel_jobs(_body(request)))
    except library_job_control.ControlError as error:
        return _error(str(error), error.status)


@csrf_exempt
def library_assistant_tasks(request):
    """Read the local assistant inbox without starting any cloud generation."""
    from . import library_assistant
    from .library_browse import normalize_ids, BrowseError
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


def _library_assistant_action(request, action):
    from . import library_assistant
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
