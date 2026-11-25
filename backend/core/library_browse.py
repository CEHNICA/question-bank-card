"""Read-only library browsing and ordered retrieval of published snapshots."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import models
from django.db.models import Count, Exists, F, Max, Min, OuterRef, Subquery
from django.http import HttpResponseNotAllowed
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import features, library, library_ai_settings, library_jobs, qtypes
from .json_response import json_response
from .models import LibraryJob, LibraryTagAssignment, PublishedQuestion

MAX_BATCH_IDS = 500
DEFAULT_LIMIT = 40
# bulk=1 时的单次上限：整库一次拉走用。题库工作台首屏要全量题目，
# 拆成九次请求的话每次都重算一遍跟分页无关的统计，光 API 就要十二秒。
BULK_LIMIT_CAP = 1000
# slim=1：列表瘦身。题库工作台首屏一次把整库拉走，列表真正用得上的只有
# 题干、选项、配图、页码、标签名。下面这些是详情页才要的：
#   content.review        每题的配图核验快照（bbox/page_idx/status）。真库 810 道题
#                         合计 37 万字节 —— 占整个列表响应的四分之一，列表一个字节都不读。
#   content.source_group  资料组信息，列表从不读。
#   标签对象里的 description/aliases/levels/parent_ids/... 以及 AI 的
#                         rationale/evidence/generation_source/scope
#                         列表的标签芯片只要 id/name/kind/color/source/needs_review。
# 丢掉这些不影响列表显示；展开一道题时前端按 publication_id 另拉详情补齐，
# 那条详情接口走的是完整载荷，一个字段都没少。
SLIM_DROP_CONTENT_KEYS = ("review", "source_group")
SLIM_TAG_KEYS = ("id", "name", "kind", "color")
SLIM_ASSIGNMENT_KEYS = ("source", "needs_review", "created_at", "updated_at")
TAG_AGE_PRESETS: dict[str, tuple[str, int]] = {
    "within_7d": ("gte", 7),
    "within_30d": ("gte", 30),
    "older_30d": ("lt", 30),
    "older_90d": ("lt", 90),
    "older_180d": ("lt", 180),
    "older_365d": ("lt", 365),
}
TAG_TIME_SORTS = {"tagged_desc", "tagged_asc"}
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
    # 1.25.31：按题号。跨资料的题号本来会重号，所以第二排序键仍是资料名，
    # 同一份卷里才是真正的「按题号」。
    "number": ("number", "source_filename", "-published_at", "id"),
    # 题库工作台首屏的「显示顺序」：按题号，同号按入库时间新的在前。
    # 这是前端列表真正在用的顺序（前端按题号稳定排序，后到的批次排在后面），
    # 有了这个键，首屏就能只取一批先把卡片画出来，剩下的后台补 —— 批次边界
    # 必须跟显示顺序对齐，不然补进来的题会把屏幕上这批挤下去。
    "display": ("number", "-published_at", "-id"),
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


def _missing_formal_answer_q() -> models.Q:
    """匹配既没有原卷答案、也没有绑定已保存解析版本的题目。"""
    no_origin = models.Q(content__answer="") | models.Q(content__answer__isnull=True)
    no_solution = models.Q(extras__solution_id="") | models.Q(extras__solution_id__isnull=True)
    return no_origin & no_solution


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
        kinds = [part.strip() for part in kind.split(",") if part.strip()]
        if len(kinds) == 1:
            rows = rows.filter(question_type=kinds[0])
        elif len(kinds) > 1:
            rows = rows.filter(question_type__in=kinds)
    has_analysis = str(params.get("has_analysis", "")).strip()
    if has_analysis == "yes":
        no_analysis = (models.Q(content__analysis="") | models.Q(content__analysis__isnull=True)) & (
            models.Q(extras__solution_id="") | models.Q(extras__solution_id__isnull=True)
        )
        rows = rows.exclude(no_analysis)
    review = params.get("review", "").strip()
    if review in library.APPROVAL_SOURCES and omit != "review":
        rows = rows.filter(review_source=review)
    answer = params.get("answer", "").strip()
    if answer in {"yes", "no"} and omit != "answer":
        missing = _missing_formal_answer_q()
        rows = rows.exclude(missing) if answer == "yes" else rows.filter(missing)
    body_mode = params.get("body_mode", "").strip()
    if body_mode == "source_image" and omit != "body_mode":
        rows = rows.filter(content__body_mode="source_image")
    elif body_mode == "text" and omit != "body_mode":
        # 文字题的快照通常不写入 body_mode 键，SQLite 下直接 exclude(content__body_mode="source_image")
        # 会因 NULL != 'source_image' 为 UNKNOWN 而漏掉全部文字题。
        rows = rows.filter(models.Q(content__body_mode__isnull=True) | models.Q(content__body_mode="text"))

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

    def _tag_age_filter_kwargs(raw: str) -> dict:
        spec = TAG_AGE_PRESETS.get(str(raw or "").strip())
        if spec is None:
            return {}
        op, days = spec
        cutoff = timezone.now() - timedelta(days=days)
        return {f"created_at__{op}": cutoff}

    if omit != "tag":
        all_ids, any_ids, excluded = tag_ids("tag_all"), tag_ids("tag_any"), tag_ids("tag_not")
        age_kwargs = _tag_age_filter_kwargs(params.get("tag_age", ""))
        from .library_tagging import descendant_ids, relation_context

        context = relation_context() if all_ids or any_ids or excluded else {}
        if all_ids:
            for tag_id in all_ids:
                rows = rows.filter(
                    Exists(
                        LibraryTagAssignment.objects.filter(
                            publication_id=OuterRef("pk"),
                            tag_id__in=descendant_ids([tag_id], context=context),
                            needs_review=False,
                            **age_kwargs,
                        )
                    )
                )
        if any_ids:
            rows = rows.filter(
                Exists(
                    LibraryTagAssignment.objects.filter(
                        publication_id=OuterRef("pk"),
                        tag_id__in=descendant_ids(any_ids, context=context),
                        needs_review=False,
                        **age_kwargs,
                    )
                )
            )
        if age_kwargs and not all_ids and not any_ids:
            rows = rows.filter(
                Exists(
                    LibraryTagAssignment.objects.filter(publication_id=OuterRef("pk"), needs_review=False, **age_kwargs)
                )
            )
        if excluded:
            rows = rows.exclude(
                Exists(
                    LibraryTagAssignment.objects.filter(
                        publication_id=OuterRef("pk"),
                        tag_id__in=descendant_ids(excluded, context=context),
                        needs_review=False,
                    )
                )
            )
        legacy = params.get("tag", "").strip()
        if legacy:
            rows = rows.filter(tags_text__contains=library.tags_text([legacy]))
        if params.get("untagged_knowledge") == "true":
            rows = rows.exclude(
                tag_assignments__in=LibraryTagAssignment.objects.filter(tag__kind="knowledge", needs_review=False)
            )
        if params.get("tag_review") == "true":
            rows = rows.filter(
                models.Q(tag_suggestions__status="pending") | models.Q(tag_assignments__needs_review=True)
            ).distinct()
    from . import library_fts

    rows, _search_engine = library_fts.apply_fts_search(rows, params.get("q", ""))
    if omit != "usage":
        exclude_days = str(params.get("exclude_used_days", "")).strip()
        exclude_batch = str(params.get("exclude_used_batch", "")).strip()
        if exclude_days or exclude_batch:
            from . import library_usage

            used_excluded: set[str] = set()
            if exclude_days == "all":
                used_excluded |= library_usage.excluded_ids_within_days(0)
            elif exclude_days:
                try:
                    days_val = int(exclude_days)
                    if days_val > 0:
                        used_excluded |= library_usage.excluded_ids_within_days(days_val)
                except ValueError:
                    pass
            if exclude_batch:
                used_excluded |= library_usage.excluded_ids_for_batch(exclude_batch)
            if used_excluded:
                excluded_uuids = []
                for raw_id in used_excluded:
                    try:
                        excluded_uuids.append(uuid.UUID(raw_id))
                    except ValueError:
                        continue
                if excluded_uuids:
                    rows = rows.exclude(pk__in=excluded_uuids)
    return rows


def _active_facets(params) -> set[str]:
    """Identify which facet filters are actually active so inactive facets share one base query."""
    active: set[str] = set()
    if str(params.get("document", "")).strip():
        active.add("document")
    if str(params.get("type", "")).strip():
        active.add("type")
    if str(params.get("review", "")).strip() in library.APPROVAL_SOURCES:
        active.add("review")
    if str(params.get("answer", "")).strip() in {"yes", "no"}:
        active.add("answer")
    if str(params.get("body_mode", "")).strip() in {"source_image", "text"}:
        active.add("body_mode")
    if (
        str(params.get("tag", "")).strip()
        or str(params.get("tag_age", "")).strip() in TAG_AGE_PRESETS
        or params.get("untagged_knowledge") == "true"
        or params.get("tag_review") == "true"
    ):
        active.add("tag")
    else:
        for key in ("tag_all", "tag_any", "tag_not"):
            raw = params.get(key, "")
            values = raw if isinstance(raw, list) else str(raw or "").split(",")
            for value in values:
                try:
                    uuid.UUID(str(value))
                except (ValueError, TypeError, AttributeError):
                    continue
                active.add("tag")
                break
            if "tag" in active:
                break
    return active


def facet_counts(params) -> dict:
    sources: dict[str, dict] = {}
    types: dict[str, int] = {}
    reviews = {"human": 0, "ai": 0}
    answers = {"yes": 0, "no": 0}
    tags: dict[str, dict] = {}
    active = _active_facets(params)
    base_rows = library_rows(params)

    def rows_for(facet: str):
        return library_rows(params, omit=facet) if facet in active else base_rows

    for row in (
        rows_for("document")
        .values("paper_id", "source_filename")
        .annotate(count=Count("id"), first_published_at=Min("published_at"), last_published_at=Max("published_at"))
    ):
        key = str(row["paper_id"]) if row["paper_id"] else ""
        entry = sources.setdefault(
            key,
            {
                "document_id": key or None,
                "filename": row["source_filename"],
                "count": 0,
                "first_published_at": row["first_published_at"],
                "last_published_at": row["last_published_at"],
            },
        )
        entry["count"] += row["count"]
        if row["first_published_at"] and (
            not entry["first_published_at"] or row["first_published_at"] < entry["first_published_at"]
        ):
            entry["first_published_at"] = row["first_published_at"]
        if row["last_published_at"] and (
            not entry["last_published_at"] or row["last_published_at"] > entry["last_published_at"]
        ):
            entry["last_published_at"] = row["last_published_at"]

    for row in rows_for("type").values("question_type").annotate(count=Count("id")):
        types[row["question_type"]] = row["count"]

    for row in rows_for("review").values("review_source").annotate(count=Count("id")):
        key = row["review_source"] if row["review_source"] in reviews else "human"
        reviews[key] += row["count"]

    answer_rows = rows_for("answer")
    missing_answer = _missing_formal_answer_q()
    total_answers = answer_rows.count()
    yes_answers = sum(
        1
        for answer_val, sol_id in answer_rows.exclude(missing_answer).values_list(
            "content__answer", "extras__solution_id"
        )
        if str(answer_val or "").strip() or str(sol_id or "").strip()
    )
    answers["yes"] = yes_answers
    answers["no"] = total_answers - yes_answers

    body_mode_rows = rows_for("body_mode")
    source_image_count = body_mode_rows.filter(content__body_mode="source_image").count()
    body_modes = {"source_image": source_image_count, "text": body_mode_rows.count() - source_image_count}

    from .library_tagging import descendant_ids, relation_context
    from .models import LibraryTag

    context = relation_context()
    publications_by_tag: dict[str, set[str]] = {}
    for tag_id, publication_id in LibraryTagAssignment.objects.filter(
        publication__in=rows_for("tag"), needs_review=False
    ).values_list("tag_id", "publication_id"):
        publications_by_tag.setdefault(str(tag_id), set()).add(str(publication_id))
    for tag_pk, tag_name in LibraryTag.objects.values_list("pk", "name"):
        key = str(tag_pk)
        if key not in publications_by_tag and not context.get(key, {}).get("children"):
            continue
        matching: set[str] = set()
        for child in descendant_ids([key], context=context):
            matching.update(publications_by_tag.get(child, ()))
        if matching:
            tags[key] = {
                "id": key,
                "tag": tag_name,
                "count": len(matching),
                "direct_count": len(publications_by_tag.get(key, ())),
                "subtree_count": len(matching),
            }
    return {
        "sources": sorted(sources.values(), key=lambda item: (item["filename"], item["document_id"] or "")),
        "types": types,
        "reviews": reviews,
        "answers": answers,
        "body_modes": body_modes,
        "tags": sorted(tags.values(), key=lambda item: (-item["count"], item["tag"])),
    }


def _slim_content(content):
    """只丢列表根本不读的 content 子字段。

    content 是模型上那一份 dict（除非配图缺失才会复制），所以这里必须新建
    一个 dict，绝不能就地 del —— 否则会把这个 PublishedQuestion 实例上的
    content 掏空，后面谁再拿它渲染就炸了。
    """
    if not isinstance(content, dict):
        return content
    return {key: value for key, value in content.items() if key not in SLIM_DROP_CONTENT_KEYS}


def _slim_assignments(assignments):
    slim = []
    for row in assignments:
        if not isinstance(row, dict):
            continue
        entry = {key: row.get(key) for key in SLIM_ASSIGNMENT_KEYS if key in row}
        tag = row.get("tag")
        if isinstance(tag, dict):
            entry["tag"] = {key: tag.get(key) for key in SLIM_TAG_KEYS if key in tag}
        slim.append(entry)
    return slim


def slim_item(data: dict) -> dict:
    """把一道题的行载荷削成列表用得上的样子。其余字段一律原样保留。"""
    if "content" in data:
        data["content"] = _slim_content(data.get("content"))
    if "tag_assignments" in data:
        data["tag_assignments"] = _slim_assignments(data.get("tag_assignments"))
    return data


def serialize_items(publications, *, slim: bool = False) -> list[dict]:
    publications = list(publications)
    ids = [item.id for item in publications]
    waiting = library_jobs.pending_kinds(ids)
    failed = library_jobs.last_errors(ids)
    details: dict[str, list[dict]] = {}
    for row in LibraryJob.objects.filter(publication_id__in=ids, status__in=library_jobs.ACTIVE).values(
        "publication_id", "kind", "executor", "status"
    ):
        details.setdefault(str(row["publication_id"]), []).append(
            {key: row[key] for key in ("kind", "executor", "status")}
        )
    # 1.26.47：标签关联、建议、退役标签名、上下级关系，整页一次查完。
    # 以前是一道题查一轮（一页 40 道题要查一百多次），页面得多等半秒。
    items = library.publications_json(publications)
    for item, data in zip(publications, items):
        data["jobs"] = waiting.get(str(item.id), [])
        data["job_errors"] = failed.get(str(item.id), {})
        data["job_details"] = details.get(str(item.id), [])
    if slim:
        for data in items:
            slim_item(data)
    return items


def resolve_ids(ids: list[str], *, include_items: bool = True) -> dict:
    """Report unavailable IDs explicitly; never silently substitute another version."""
    rows = PublishedQuestion.objects.filter(pk__in=ids)
    if not include_items:
        rows = rows.only("id", "status", "question_id")
    found = {str(item.id): item for item in rows}
    superseded_questions = {
        item.question_id
        for item in found.values()
        if item.status == PublishedQuestion.Status.SUPERSEDED and item.question_id
    }
    replacements: dict[int, str] = {}
    if superseded_questions:
        for item in (
            PublishedQuestion.objects.filter(
                question_id__in=superseded_questions, status=PublishedQuestion.Status.PUBLISHED
            )
            .only("id", "question_id")
            .order_by("-version", "-published_at", "-id")
        ):
            replacements.setdefault(item.question_id, str(item.id))
    live, missing = [], []
    messages = {
        "not_found": "这道题已不存在",
        "withdrawn": "这道题已撤回",
        "superseded": "这道题已有新版本，请核对后选择当前版本",
    }
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


def _json_truthy(kind, value, length) -> bool:
    """JSON 值在 Python 里算不算「有东西」。

    Python 把 0、""、False、空列表、空字典都算「没有」；JSON 里这些都长得不一样，
    所以光看抽出来的值会算错（空数组抽出来是 "[]"，看着像有内容，其实没有）。
    这里照 Python 的规矩复原一遍。
    """
    if kind is None or kind == "null":
        return False
    if kind == "true":
        return True
    if kind == "false":
        return False
    if kind in ("array", "object"):
        return bool(length)
    if kind in ("integer", "real"):
        return value != 0
    return bool(value)


def _answer_present(kind, value, length) -> bool:
    """答案算不算「有」。

    原来的口径是 str(答案 or "").strip() —— 空串、空白、0 都算「没有」，
    其余算「有」。这里照搬，一处不差。
    """
    if not _json_truthy(kind, value, length):
        return False
    if kind == "text":
        return bool(str(value).strip())
    return True


def answer_inventory(status) -> tuple[int, int]:
    """数一数：多少道题没入库答案、多少道题只有 AI 初稿。

    1.26.47：以前是把全库题面 JSON 整个读进内存里数一遍（实测 24 毫秒）。
    现在让数据库自己抽出答案那一段，只把结果拿回来（实测 6 毫秒）。
    口径一个字符没变 —— 空答案、空白答案、答案写成 0，都算「没有」。
    数据库不支持这个抽法时，老老实实退回原来那种数法。
    """
    from django.db import connection

    table = PublishedQuestion._meta.db_table
    # 空数组、空对象在 Python 里都算「没有」，非空的就算「有」——
    # 所以数组和对象要把长度也一起问出来，只看抽出来的值会判反。
    sql = (
        "SELECT json_extract(content, '$.answer'), json_type(content, '$.answer'), "
        "CASE json_type(content, '$.answer') "
        "WHEN 'array' THEN json_array_length(content, '$.answer') "
        "WHEN 'object' THEN (SELECT count(*) FROM json_each(content, '$.answer')) "
        "ELSE NULL END, "
        "json_extract(extras, '$.solution_id'), json_type(extras, '$.solution_id'), "
        "json_extract(extras, '$.ai_answer'), json_type(extras, '$.ai_answer'), "
        "CASE json_type(extras, '$.ai_answer') "
        "WHEN 'array' THEN json_array_length(extras, '$.ai_answer') "
        "WHEN 'object' THEN (SELECT count(*) FROM json_each(extras, '$.ai_answer')) "
        "ELSE NULL END "
        f"FROM {table} WHERE status = %s"
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, [status])
            rows = cursor.fetchall()
    except Exception:  # noqa: BLE001 —— 数据库没有这个抽法，就用老办法数
        rows = None
    if rows is not None:
        missing = draft = 0
        for answer, answer_kind, answer_length, sol_id, sol_kind, ai_answer, ai_kind, ai_length in rows:
            if _answer_present(answer_kind, answer, answer_length) or _answer_present(sol_kind, sol_id, None):
                continue
            if _json_truthy(ai_kind, ai_answer, ai_length):
                draft += 1
            else:
                missing += 1
        return missing, draft
    missing = draft = 0
    for content, extras in PublishedQuestion.objects.filter(status=status).values_list("content", "extras"):
        if library.has_formal_answer(content, extras):
            continue
        if (extras or {}).get("ai_answer"):
            draft += 1
        else:
            missing += 1
    return missing, draft


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
    pending = LibraryTagSuggestion.objects.filter(
        status=LibraryTagSuggestion.Status.PENDING, publication__status=published
    )
    waiting = set(pending.values_list("publication_id", flat=True))
    reachable = set(
        pending.filter(taxonomy_revision=revision, assignment_revision=F("publication__tag_revision")).values_list(
            "publication_id", flat=True
        )
    )
    flagged = set(
        LibraryTagAssignment.objects.filter(needs_review=True, publication__status=published).values_list(
            "publication_id", flat=True
        )
    )
    live = PublishedQuestion.objects.filter(status=published)
    untagged = live.exclude(
        tag_assignments__in=LibraryTagAssignment.objects.filter(tag__kind="knowledge", needs_review=False)
    )
    # 「还没打标签」和「点一下真能排上队」是两个数，不能混用。
    # 筛选按钮要的是前者；批量按钮要的是后者 —— 已经有建议在等或在跑的题，
    # 点了也不会再排一次。混用会让按钮写着 805、点下去却一道也没排上。
    already_asked = LibraryTagSuggestion.objects.filter(
        status__in=[
            LibraryTagSuggestion.Status.QUEUED,
            LibraryTagSuggestion.Status.RUNNING,
            LibraryTagSuggestion.Status.PENDING,
        ]
    ).values("publication_id")
    # 「没入库答案」和「连 AI 初稿都没有」是两个数，差值是「已经有 AI 初稿」的那些。
    # 三个数都按全库算：拿筛选后的数去减全库的数会减出负数（原来「-37 题」就是这么来的）。
    # 1.26.47：改成让数据库自己从题面里抽出答案那一段，别把八百多行题面 JSON
    # 整个读进内存里数一遍（口径一个字符没变，见 answer_inventory）。
    missing_answers, ai_draft_answers = answer_inventory(published)
    processing = LibraryJob.objects.filter(executor=LibraryJob.Executor.API, status__in=library_jobs.ACTIVE).aggregate(
        tags=Count("pk", filter=models.Q(kind=LibraryJob.Kind.TAGS)),
        answers=Count("pk", filter=models.Q(kind=LibraryJob.Kind.ANSWER)),
    )
    return {
        "untagged_knowledge": untagged.distinct().count(),
        "untagged_ready": untagged.exclude(pk__in=already_asked).distinct().count(),
        "missing_answer": missing_answers,
        "missing_answer_total": missing_answers + ai_draft_answers,
        "ai_draft_answers": ai_draft_answers,
        "queued_tags": processing["tags"],
        "queued_answers": processing["answers"],
        # 1.26.46：「待核对」这批货其实是两种 —— AI 建议，和从旧标签留底里接回来的
        # 旧标签。两者的数目、处理方式都不同，合在一起报一个数，界面就没法把话说明白：
        # 旧标签一批接回来之后，AI 建议其实一条都不剩，芯片却还写着「AI 建议待核对」。
        # 分开报，界面才能照实说。
        "pending_review": len(waiting | flagged),
        "pending_suggestions": len(waiting),
        "pending_legacy": len(flagged),
        "pending_actionable": len(reachable),
        "outdated_suggestions": len(waiting - reachable),
    }


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
    missing = _missing_formal_answer_q()
    from .models import LibraryTagAssignment

    counts = papers.aggregate(
        review_ai=Count("id", filter=models.Q(review_source="ai")), answer_missing=Count("id", filter=missing)
    )
    return {
        "review_ai": counts["review_ai"],
        "answer_missing": counts["answer_missing"],
        "tags": (LibraryTagAssignment.objects.filter(needs_review=False).values("tag_id").distinct().count()),
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


def _apply_sort(rows, sort: str, params=None):
    """应用列表排序；打标时间排序在有选中标签时优先按命中标签的打标时间排。"""
    if sort in TAG_TIME_SORTS:
        target_ids: list[str] = []
        if params is not None:
            for key in ("tag_all", "tag_any"):
                raw = params.get(key, "")
                values = raw if isinstance(raw, list) else str(raw or "").split(",")
                for value in values:
                    try:
                        target_ids.append(str(uuid.UUID(str(value))))
                    except (ValueError, TypeError, AttributeError):
                        continue
        assign_qs = LibraryTagAssignment.objects.filter(publication_id=OuterRef("pk"), needs_review=False)
        if target_ids:
            from .library_tagging import descendant_ids, relation_context

            assign_qs = assign_qs.filter(tag_id__in=descendant_ids(target_ids, context=relation_context()))
        if sort == "tagged_desc":
            subq = Subquery(assign_qs.order_by("-created_at", "-id").values("created_at")[:1])
            return rows.annotate(_tag_sort_ts=subq).order_by(
                F("_tag_sort_ts").desc(nulls_last=True), "-published_at", "-id"
            )
        subq = Subquery(assign_qs.order_by("created_at", "id").values("created_at")[:1])
        return rows.annotate(_tag_sort_ts=subq).order_by(F("_tag_sort_ts").asc(nulls_last=True), "published_at", "id")
    return rows.order_by(*SORTS[sort])


def library_list(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    # bulk=1：整库一次拉走。
    # 题库工作台首屏要全量题目。以前拆成 limit=100 的九次请求，每次都把
    # facets / presence / tag_work 这些跟分页无关的统计重算一遍 —— 九次请求
    # 在同一个进程里排成一队，光 API 就要十二秒。改成一次给完。
    bulk = request.GET.get("bulk") == "1"
    try:
        limit = min(BULK_LIMIT_CAP if bulk else 100, max(1, int(request.GET.get("limit", str(DEFAULT_LIMIT)))))
        offset = max(0, int(request.GET.get("offset", "0")))
    except ValueError:
        return json_response({"error": "limit 与 offset 必须是整数"}, status=400)
    sort = request.GET.get("sort", "recent")
    if sort not in SORTS and sort not in TAG_TIME_SORTS:
        return json_response({"error": "sort 只能是 recent、source 或 number"}, status=400)
    query_raw = str(request.GET.get("q", "") or "").strip()
    search_engine = None
    if query_raw:
        from . import library_fts

        base_rows = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
        rows = library_rows(request.GET)
        _, search_engine = library_fts.apply_fts_search(base_rows, query_raw)
        rows = _apply_sort(rows, sort, request.GET)
    else:
        rows = _apply_sort(library_rows(request.GET), sort, request.GET)
    # 1.25.31：只取指定的几道题。后台刷新任务进度时用它，只读有任务的那几行，
    # 不再把屏上几百道题整屏重读一遍。编号先校验，非法值直接 400，不交给 ORM。
    wanted_param = request.GET.get("ids", "").strip()
    if wanted_param:
        try:
            wanted = normalize_ids([part for part in wanted_param.split(",") if part])
        except BrowseError as error:
            return json_response({"error": str(error)}, status=400)
        rows = rows.filter(id__in=wanted)
    if request.GET.get("ids_only") == "1":
        # 「选中当前筛选的全部」：只回编号，不受分页限制。
        ids = [str(pk) for pk in rows.values_list("id", flat=True)]
        return json_response({"total": len(ids), "ids": ids})
    total = rows.count()
    # slim=1：列表瘦身，只给列表用得上的字段。题库工作台首屏拉全库时用，
    # 详情页那条接口走完整载荷，展开一道题时补齐。
    slim = request.GET.get("slim") == "1"
    # 先只排编号取这一批，再按编号回表取整行。
    # 整行排序在 810 道题上要多花一百多毫秒：SQLite 的 filesort 会把
    # content 这些大字段也带着走。只排编号时排序器只带 id 和排序键，
    # 810 道题两毫秒排完，回表取 150 行七毫秒。
    batch_ids = list(rows.values_list("id", flat=True)[offset : offset + limit])
    picked = _apply_sort(rows.filter(id__in=batch_ids), sort, request.GET)
    items = serialize_items(picked, slim=slim)
    next_offset = offset + len(items)
    if wanted_param:
        # 只为刷新而读：不带全库统计、排队数量这些重活，前端也只合并这几行。
        return json_response(
            {
                "total": total,
                "items": items,
                "sort": sort,
                "limit": limit,
                "offset": offset,
                "has_more": next_offset < total,
                "next_offset": next_offset if next_offset < total else None,
            }
        )
    if bulk:
        # 批量模式只给题目本身。facets / presence / ai 这些是全库统计，
        # 跟分页无关 —— 批量请求再算一遍就是白算，题库工作台也不用它们。
        payload = {
            "total": total,
            "items": items,
            "sort": sort,
            "limit": limit,
            "offset": offset,
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
        }
        if search_engine is not None:
            payload["search_engine"] = search_engine
        return json_response(payload)
    ai_status = library_ai_settings.public_status()
    tag_work = tag_work_counts()
    return json_response(
        {
            "total": total,
            "items": items,
            "facets": facet_counts(request.GET),
            "presence": dimension_presence(),
            "selected_tag_names": selected_tag_names(request.GET),
            "features": features.load(),
            "type_names": qtypes.TYPE_LABELS,
            "tag_work": tag_work,
            # 题库页要分清「还没填密钥」「填了但没测过」「测过了」三种情况，
            # 才能告诉老师下一步去哪。只给一个 api_ready，前端只能写一句笼统的话，
            # 老师看了只会以为功能坏了。
            "ai": {
                key: ai_status.get(key)
                for key in (
                    "mode",
                    "provider",
                    "message",
                    "api_ready",
                    "key_configured",
                    "configured",
                    "status",
                    "verified_at",
                    "unverified_reason",
                )
            },
            "sort": sort,
            "limit": limit,
            "offset": offset,
            "has_more": next_offset < total,
            "next_offset": next_offset if next_offset < total else None,
            **({"search_engine": search_engine} if search_engine is not None else {}),
        }
    )


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
        return json_response({"error": "请提供 ids 题目编号列表和可选的 filters"}, status=400)
    try:
        ids = normalize_ids(payload["ids"])
    except BrowseError as error:
        return json_response({"error": str(error)}, status=400)
    result = resolve_ids(ids)
    if "filters" in payload:
        filters = payload["filters"]
        allowed = {
            "q",
            "document",
            "type",
            "review",
            "answer",
            "tag",
            "tag_all",
            "tag_any",
            "tag_not",
            "tag_mode",
            "untagged_knowledge",
            "tag_review",
            "has_analysis",
            "exclude_used_days",
            "exclude_used_batch",
        }
        if (
            not isinstance(filters, dict)
            or set(filters) - allowed
            or any(
                not isinstance(value, str)
                and not (key in {"tag_all", "tag_any", "tag_not"} and isinstance(value, list))
                for key, value in filters.items()
            )
        ):
            return json_response({"error": "筛选条件格式不正确"}, status=400)
        rows = library_rows(filters)
        matching = set(map(str, rows.filter(pk__in=ids).values_list("id", flat=True)))
        result.update(
            matches={key: key in matching for key in ids},
            total=rows.count(),
            facets=facet_counts(filters),
            tag_work=tag_work_counts(),
        )
    return json_response(result)
