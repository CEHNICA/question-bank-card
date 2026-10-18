"""Read-only library browsing and ordered retrieval of published snapshots."""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Count, F
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import features, library, library_jobs, library_ai_settings, qtypes
from .models import LibraryJob, PublishedQuestion, LibraryTagAssignment

MAX_BATCH_IDS = 500
DEFAULT_LIMIT = 40
# 「原卷顺序」承诺的是这份资料上印出来的先后。试卷的题号本身就是那个先后，
# 教材不是：每章的习题都从 1 重新编，真库两本教材里「第 1 题」各有 94 / 82 张，
# 按题号排等于把各章的「第 1 题」堆在一起（698 张里只有 14 张落在书上位置）。
# source_anchor_seq 是题目起始所在的版面块序号，也就是原卷上的先后；14 份
# 资料按它排 100% 等于原卷顺序，试卷按它排和按题号排一模一样。锚点为空
# （人工框选、切题后没有版面块）时排到最后，不打乱其余题的顺序。
_SOURCE_ANCHOR = models.F("question__source_anchor_seq").asc(nulls_last=True)
SORTS = {
    "recent": ("-published_at", "-id"),
    "source": ("source_filename", _SOURCE_ANCHOR, "number", "-version", "id"),
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
    def tag_ids(key):
        raw = params.get(key, "")
        values = raw if isinstance(raw, list) else str(raw or "").split(",")
        result = []
        for value in values:
            try:
                result.append(str(uuid.UUID(str(value))))
            except (ValueError, TypeError, AttributeError):
                continue
        return list(dict.fromkeys(result))

    if omit != "tag":
        all_ids, any_ids, excluded = tag_ids("tag_all"), tag_ids("tag_any"), tag_ids("tag_not")
        from .library_tagging import descendant_ids, relation_context
        context = relation_context() if all_ids or any_ids or excluded else {}
        if all_ids:
            for tag_id in all_ids:
                rows = rows.filter(tag_assignments__tag_id__in=descendant_ids([tag_id], context=context),
                                   tag_assignments__needs_review=False)
        if any_ids:
            rows = rows.filter(tag_assignments__tag_id__in=descendant_ids(any_ids, context=context),
                               tag_assignments__needs_review=False).distinct()
        if excluded:
            rows = rows.exclude(tag_assignments__in=LibraryTagAssignment.objects.filter(
                tag_id__in=descendant_ids(excluded, context=context), needs_review=False))
        if all_ids:
            rows = rows.distinct()
        legacy = params.get("tag", "").strip()
        if legacy:
            rows = rows.filter(tags_text__contains=library.tags_text([legacy]))
        if params.get("untagged_knowledge") == "true":
            rows = rows.exclude(tag_assignments__in=LibraryTagAssignment.objects.filter(
                tag__kind="knowledge", needs_review=False))
        if params.get("tag_review") == "true":
            rows = rows.filter(models.Q(tag_suggestions__status="pending")
                               | models.Q(tag_assignments__needs_review=True)).distinct()
    for term in params.get("q", "").split()[:8]:
        key = library.search_key(term)
        if key:
            rows = rows.filter(search_text__contains=key)
    return rows


def facet_counts(params) -> dict:
    sources, types, reviews, answers, tags = {}, {}, {"human": 0, "ai": 0}, {"yes": 0, "no": 0}, {}
    for row in library_rows(params, omit="document").values("paper_id", "source_filename", "published_at"):
        key = str(row["paper_id"]) if row["paper_id"] else ""
        entry = sources.setdefault(key, {"document_id": key or None, "filename": row["source_filename"],
            "count": 0, "first_published_at": row["published_at"], "last_published_at": row["published_at"]})
        entry["count"] += 1
        if row["published_at"] and (not entry["first_published_at"] or row["published_at"] < entry["first_published_at"]):
            entry["first_published_at"] = row["published_at"]
        if row["published_at"] and (not entry["last_published_at"] or row["published_at"] > entry["last_published_at"]):
            entry["last_published_at"] = row["published_at"]
    for kind in library_rows(params, omit="type").values_list("question_type", flat=True):
        types[kind] = types.get(kind, 0) + 1
    for source in library_rows(params, omit="review").values_list("review_source", flat=True):
        key = source if source in reviews else "human"
        reviews[key] += 1
    for value in library_rows(params, omit="answer").values_list("content__answer", flat=True):
        answers["yes" if str(value or "").strip() else "no"] += 1
    from .models import LibraryTag
    from .library_tagging import descendant_ids, relation_context
    context = relation_context()
    publications_by_tag = {}
    for tag_id, publication_id in LibraryTagAssignment.objects.filter(
            publication__in=library_rows(params, omit="tag"), needs_review=False).values_list("tag_id", "publication_id"):
        publications_by_tag.setdefault(str(tag_id), set()).add(str(publication_id))
    for tag in LibraryTag.objects.all():
        key, matching = str(tag.pk), set()
        for child in descendant_ids([key], context=context):
            matching.update(publications_by_tag.get(child, set()))
        if matching:
            tags[key] = {"id": key, "tag": tag.name, "count": len(matching),
                         "direct_count": len(publications_by_tag.get(key, set())), "subtree_count": len(matching)}
    return {
        "sources": sorted(sources.values(), key=lambda item: (item["filename"], item["document_id"] or "")),
        "types": types, "reviews": reviews, "answers": answers,
        "tags": sorted(tags.values(), key=lambda item: (-item["count"], item["tag"])),
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


def tag_work_counts() -> dict:
    """Tag work a teacher can act on, kept apart from the backlog that cannot be reached.

    A pending suggestion only becomes acceptable while the tag catalogue has not moved
    since it was produced. One flat "pending" total therefore looks like a pile of
    reviewable work while almost none of it can actually be accepted, so the reachable
    part is reported separately instead of being hidden inside the same number.

    The same care applies to the number a bulk button prints. "Missing a knowledge
    label" and "would queue if you pressed it" are different sets: questions whose
    suggestion is already waiting or running are still untagged, yet re-queueing them
    does nothing. A button that reports the wider set promises work it will not do.
    """
    from . import library_tagging
    from .models import LibraryTagAssignment, LibraryTagSuggestion
    published = PublishedQuestion.Status.PUBLISHED
    revision = library_tagging.catalogue_state().revision
    pending = LibraryTagSuggestion.objects.filter(status=LibraryTagSuggestion.Status.PENDING,
                                                  publication__status=published)
    waiting = set(pending.values_list("publication_id", flat=True))
    reachable = set(pending.filter(taxonomy_revision=revision,
                                   assignment_revision=F("publication__tag_revision"))
                     .values_list("publication_id", flat=True))
    flagged = set(LibraryTagAssignment.objects.filter(needs_review=True, publication__status=published)
                  .values_list("publication_id", flat=True))
    live = PublishedQuestion.objects.filter(status=published)
    untagged = live.exclude(tag_assignments__in=LibraryTagAssignment.objects.filter(
        tag__kind="knowledge", needs_review=False))
    # 「还没打标签」和「点一下真能排上队」是两个数，不能混用。
    # 筛选按钮要的是前者；批量按钮要的是后者 —— 已经有建议在等或在跑的题，
    # 点了也不会再排一次。混用会让按钮写着 805、点下去却一道也没排上。
    already_asked = LibraryTagSuggestion.objects.filter(
        status__in=[LibraryTagSuggestion.Status.QUEUED, LibraryTagSuggestion.Status.RUNNING,
                    LibraryTagSuggestion.Status.PENDING]).values("publication_id")
    missing_answers = sum(1 for content, extras in live.values_list("content", "extras")
                          if not str((content or {}).get("answer") or "").strip()
                          and not (extras or {}).get("ai_answer"))
    processing = LibraryJob.objects.filter(executor=LibraryJob.Executor.API,
                                           status__in=library_jobs.ACTIVE).aggregate(
        tags=Count("pk", filter=models.Q(kind=LibraryJob.Kind.TAGS)),
        answers=Count("pk", filter=models.Q(kind=LibraryJob.Kind.ANSWER)))
    return {"untagged_knowledge": untagged.distinct().count(),
            "untagged_ready": untagged.exclude(pk__in=already_asked).distinct().count(),
            "missing_answer": missing_answers,
            "queued_tags": processing["tags"],
            "queued_answers": processing["answers"],
            "pending_review": len(waiting | flagged),
            "pending_actionable": len(reachable),
            "outdated_suggestions": len(waiting - reachable)}


def dimension_presence() -> dict:
    """这几个维度在题库里到底有没有数据 —— 不受任何筛选影响。

    「更多筛选」里一张卡片出不出现，必须看这个，不能看当前筛选下的数字。
    facet_counts 每一维都是「换到这个维度后还剩多少题」，所以老师选两三个标签、
    筛到 0 道题时，这些数字会一起变 0；更糟的是 emptied 那条兜底会把本来该藏起来的
    卡片也顶出来（这一维在题库里从来就没有过数据），面板于是重排。老师的原话是
    「我选择了两三个标签之后，这个界面就变了，格局就变了」。

    这里返回的是题库的事实：有没有 AI 审核入库的题、有没有缺答案的题、
    有没有打上正式标签的题。改筛选动不了它。
    """
    papers = library_rows({})
    missing = models.Q(content__answer="") | models.Q(content__answer__isnull=True)
    from .models import LibraryTagAssignment
    return {
        "review_ai": papers.filter(review_source="ai").count(),
        "answer_missing": papers.filter(missing).count(),
        "tags": (LibraryTagAssignment.objects.filter(needs_review=False)
                 .values("tag_id").distinct().count()),
    }


def selected_tag_names(params) -> dict:
    """当前正选着的标签，各自叫什么名字 —— 不受筛选影响。

    筛到 0 道题时，可选列表里一个标签都不剩，但老师手上那几个标签还得看得见、
    还得能取消。以前这里退而显示编号前 8 位（「已选标签 · 531aebeb6」），
    那是数据库里的内部编号，老师看不懂也说不出口。

    只看 tag_all / tag_any / tag_not 这三个按编号筛的键。`tag` 是按名字筛的老写法，
    前端那边本来就握着那个名字（它直接拿名字当选项显示），不需要这里查；
    而且它要是塞进 pk__in，会把整个列表页打成 500。
    """
    from .models import LibraryTag
    ids = []
    for key in ("tag_all", "tag_any", "tag_not"):
        for value in str(params.get(key, "") or "").split(","):
            try:
                ids.append(str(uuid.UUID(str(value))))
            except (ValueError, TypeError, AttributeError):
                continue
    if not ids:
        return {}
    names = {str(pk): name for pk, name in LibraryTag.objects.filter(pk__in=ids).values_list("pk", "name")}
    return {tag_id: names[tag_id] for tag_id in dict.fromkeys(ids) if tag_id in names}


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
    tag_work = tag_work_counts()
    return JsonResponse({
        "total": total, "items": items, "facets": facet_counts(request.GET),
        "presence": dimension_presence(),
        "selected_tag_names": selected_tag_names(request.GET),
        "features": features.load(), "type_names": qtypes.TYPE_LABELS, "tag_work": tag_work,
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
    if payload is None or "ids" not in payload or set(payload) - {"ids", "filters"}:
        return JsonResponse({"error": "请提供 ids 题目编号列表和可选的 filters"}, status=400)
    try:
        ids = normalize_ids(payload["ids"])
    except BrowseError as error:
        return JsonResponse({"error": str(error)}, status=400)
    result = resolve_ids(ids)
    if "filters" in payload:
        filters = payload["filters"]
        allowed = {"q", "document", "type", "review", "answer", "tag", "tag_all", "tag_any", "tag_not",
                   "tag_mode", "untagged_knowledge", "tag_review"}
        if (not isinstance(filters, dict) or set(filters) - allowed
                or any(not isinstance(value, str) and not (key in {"tag_all", "tag_any", "tag_not"} and isinstance(value, list))
                       for key, value in filters.items())):
            return JsonResponse({"error": "筛选条件格式不正确"}, status=400)
        rows = library_rows(filters)
        matching = set(map(str, rows.filter(pk__in=ids).values_list("id", flat=True)))
        result.update(matches={key: key in matching for key in ids}, total=rows.count(), facets=facet_counts(filters),
            tag_work=tag_work_counts())
    return JsonResponse(result)
