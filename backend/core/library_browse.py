"""Read-only library browsing and ordered retrieval of published snapshots."""

from __future__ import annotations

import uuid

from django.db import models
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import features, library, library_jobs, library_ai_settings, qtypes
from .models import LibraryJob, PublishedQuestion

MAX_BATCH_IDS = 500
DEFAULT_LIMIT = 40
SORTS = {
    "recent": ("-published_at", "-id"),
    "source": ("source_filename", "number", "-version", "id"),
}


class BrowseError(ValueError):
    pass


def normalize_ids(values) -> list[str]:
    """Validate, canonicalize and de-duplicate IDs without changing their order."""
    if not isinstance(values, list) or len(values) > MAX_BATCH_IDS:
        raise BrowseError(f"题目编号应为列表，每次最多 {MAX_BATCH_IDS} 道题")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            raise BrowseError("题目编号格式不正确")
        try:
            key = str(uuid.UUID(value))
        except (ValueError, AttributeError):
            raise BrowseError("题目编号格式不正确") from None
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def library_rows(params, *, omit: str = ""):
    """Each facet omits its own condition, retaining all other active filters."""
    rows = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
    document = params.get("document", "").strip()
    if document and omit != "document":
        try:
            rows = rows.filter(paper_id=uuid.UUID(document))
        except (ValueError, AttributeError):
            rows = rows.none()
    kind = params.get("type", "").strip()
    if kind and omit != "type":
        rows = rows.filter(question_type=kind)
    review = params.get("review", "").strip()
    if review in library.APPROVAL_SOURCES and omit != "review":
        rows = rows.filter(review_source=review)
    answer = params.get("answer", "").strip()
    if answer in {"yes", "no"} and omit != "answer":
        missing = models.Q(content__answer="") | models.Q(content__answer__isnull=True)
        rows = rows.exclude(missing) if answer == "yes" else rows.filter(missing)
    tag = params.get("tag", "").strip()
    if tag and omit != "tag":
        rows = rows.filter(tags_text__contains=library.tags_text([tag]))
    for term in params.get("q", "").split()[:8]:
        key = library.search_key(term)
        if key:
            rows = rows.filter(search_text__contains=key)
    return rows


def facet_counts(params) -> dict:
    sources, types, reviews, answers, tags = {}, {}, {"human": 0, "ai": 0}, {"yes": 0, "no": 0}, {}
    for row in library_rows(params, omit="document").values("paper_id", "source_filename"):
        key = str(row["paper_id"]) if row["paper_id"] else ""
        entry = sources.setdefault(key, {"document_id": key or None, "filename": row["source_filename"], "count": 0})
        entry["count"] += 1
    for kind in library_rows(params, omit="type").values_list("question_type", flat=True):
        types[kind] = types.get(kind, 0) + 1
    for source in library_rows(params, omit="review").values_list("review_source", flat=True):
        key = source if source in reviews else "human"
        reviews[key] += 1
    for value in library_rows(params, omit="answer").values_list("content__answer", flat=True):
        answers["yes" if str(value or "").strip() else "no"] += 1
    for text in library_rows(params, omit="tag").values_list("tags_text", flat=True):
        for tag in set((text or "").strip("|").split("|")) - {""}:
            tags[tag] = tags.get(tag, 0) + 1
    return {
        "sources": sorted(sources.values(), key=lambda item: (item["filename"], item["document_id"] or "")),
        "types": types, "reviews": reviews, "answers": answers,
        "tags": sorted(({"tag": key, "count": value} for key, value in tags.items()),
                       key=lambda item: (-item["count"], item["tag"])),
    }


def serialize_items(publications) -> list[dict]:
    publications = list(publications)
    ids = [item.id for item in publications]
    waiting = library_jobs.pending_kinds(ids)
    failed = library_jobs.last_errors(ids)
    details = {}
    for row in LibraryJob.objects.filter(publication_id__in=ids, status__in=library_jobs.ACTIVE) \
            .values("publication_id", "kind", "executor", "status"):
        details.setdefault(str(row["publication_id"]), []).append({key: row[key] for key in ("kind", "executor", "status")})
    items = []
    for item in publications:
        data = library.publication_json(item)
        data["jobs"] = waiting.get(str(item.id), [])
        data["job_errors"] = failed.get(str(item.id), {})
        data["job_details"] = details.get(str(item.id), [])
        items.append(data)
    return items


def resolve_ids(ids: list[str], *, include_items: bool = True) -> dict:
    """Report unavailable IDs explicitly; never silently substitute another version."""
    rows = PublishedQuestion.objects.filter(pk__in=ids)
    if not include_items:
        rows = rows.only("id", "status", "question_id")
    found = {str(item.id): item for item in rows}
    superseded_questions = {item.question_id for item in found.values()
                            if item.status == PublishedQuestion.Status.SUPERSEDED and item.question_id}
    replacements = {}
    if superseded_questions:
        for item in PublishedQuestion.objects.filter(question_id__in=superseded_questions,
                                                    status=PublishedQuestion.Status.PUBLISHED).only("id", "question_id") \
                .order_by("-version", "-published_at", "-id"):
            replacements.setdefault(item.question_id, str(item.id))
    live, missing = [], []
    messages = {"not_found": "这道题已不存在", "withdrawn": "这道题已撤回",
                "superseded": "这道题已有新版本，请核对后选择当前版本"}
    for key in ids:
        item = found.get(key)
        if item is not None and item.status == PublishedQuestion.Status.PUBLISHED:
            live.append(item)
            continue
        reason = item.status if item is not None else "not_found"
        entry = {"id": key, "reason": reason, "message": messages.get(reason, "这道题当前不能使用")}
        if item is not None:
            entry["status"] = item.status
            if item.question_id in replacements:
                entry["replacement_id"] = replacements[item.question_id]
        missing.append(entry)
    result = {"requested_ids": list(ids), "missing": missing, "available_count": len(live)}
    if include_items:
        result["items"] = serialize_items(live)
    return result


def library_list(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        limit = min(100, max(1, int(request.GET.get("limit", str(DEFAULT_LIMIT)))))
        offset = max(0, int(request.GET.get("offset", "0")))
    except ValueError:
        return JsonResponse({"error": "limit 与 offset 必须是整数"}, status=400)
    sort = request.GET.get("sort", "recent")
    if sort not in SORTS:
        return JsonResponse({"error": "sort 只能是 recent 或 source"}, status=400)
    rows = library_rows(request.GET).order_by(*SORTS[sort])
    total = rows.count()
    items = serialize_items(rows[offset:offset + limit])
    next_offset = offset + len(items)
    ai_status = library_ai_settings.public_status()
    return JsonResponse({
        "total": total, "items": items, "facets": facet_counts(request.GET),
        "features": features.load(), "type_names": qtypes.TYPE_LABELS,
        "ai": {key: ai_status.get(key) for key in ("mode", "provider", "message", "api_ready")},
        "sort": sort, "limit": limit, "offset": offset,
        "has_more": next_offset < total, "next_offset": next_offset if next_offset < total else None,
    })


@csrf_exempt
def library_batch(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    # Import lazily so views may delegate to this module without an import cycle.
    from .views import _body, _guard
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None or set(payload) != {"ids"}:
        return JsonResponse({"error": "请只提供 ids 题目编号列表"}, status=400)
    try:
        ids = normalize_ids(payload["ids"])
    except BrowseError as error:
        return JsonResponse({"error": str(error)}, status=400)
    return JsonResponse(resolve_ids(ids))
