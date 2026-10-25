"""新标签系统的 HTTP 端点。

规矩跟老端点一样，不因为是新功能就松一格：

* 改动必须来自本机题库页面 —— 走 ``views._guard``（自定义请求头 + JSON 内容类型）。
* 凭据相关的限制继续有效。
* 正式写入只走服务层（``library_concepts`` / ``library_query`` / ``library_tag_migration``），
  这里只做参数转换和错误翻译。

出错时给的是人能看懂的话：``标签版本已变化，请刷新后重试``，
而不是 ``IntegrityError``。
"""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import library_concepts, library_nlquery, library_query, library_tag_migration, library_tagging
from .models import PublishedQuestion


def _error(message: str, status: int = 400):
    return JsonResponse({"error": message}, status=status)


def _body(request):
    from .views import _body as read_body

    return read_body(request)


def _handle(view):
    """把服务层抛的错翻成人话。

    服务层抛三种：``library_tagging.TaggingError``（带 status）、
    ``library_concepts.AssertionError_``（带 status）、
    ``library_query.QueryError``（带 status）。
    """

    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except library_tagging.TaggingError as error:
            return _error(str(error), getattr(error, "status", 400))
        except library_concepts.AssertionError_ as error:
            return _error(str(error), error.status)
        except library_query.QueryError as error:
            return _error(str(error), error.status)
        except ValueError as error:
            return _error(str(error), 400)

    return wrapper


# ---------------------------------------------------------------------------
# 概念
# ---------------------------------------------------------------------------


@csrf_exempt
@_handle
def concepts_view(request):
    """搜概念、按名字找概念。命中多个同名的不默认挑第一个。"""
    if request.method != "GET":
        return _error("不支持的请求方式", 405)
    query = request.GET.get("q", "")
    if not query:
        return JsonResponse({"concepts": [], "ambiguous": False})
    hits = library_concepts.search_concepts(query, limit=int(request.GET.get("limit") or 24))
    return JsonResponse({"concepts": hits, "ambiguous": any(item["ambiguous"] for item in hits)})


@csrf_exempt
@_handle
def concept_detail_view(request, tag_id):
    """读一个概念，或改它的名字、别名、范围说明、关系。"""
    from .models import LibraryTag, LibraryTagRelation

    if request.method == "GET":
        tag = LibraryTag.objects.filter(pk=tag_id).first()
        if tag is None:
            return _error("找不到这个概念", 404)
        payload = library_concepts.concept_json(tag)
        payload["relations"] = [
            {
                "id": str(row.pk),
                "relation_type": row.relation_type,
                "parent_id": str(row.parent_id),
                "parent_name": row.parent.name,
                "child_id": str(row.child_id),
                "child_name": row.child.name,
            }
            for row in LibraryTagRelation.objects.filter(parent=tag).select_related("parent", "child")
        ]
        payload["children"] = [
            {"id": str(row.pk), "name": row.child.name}
            for row in LibraryTagRelation.objects.filter(parent=tag, relation_type=LibraryTagRelation.Type.HIERARCHY)
        ]
        return JsonResponse(payload)

    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    tag = LibraryTag.objects.filter(pk=tag_id).first()
    if tag is None:
        return _error("找不到这个概念", 404)
    action = str(payload.get("action") or "update")
    if action == "relate":
        result = library_concepts.relate_concepts(
            str(tag.pk),
            str(payload.get("child_id") or ""),
            relation_type=str(payload.get("relation_type") or "related"),
        )
        return JsonResponse(result)
    return _error(f"不认识的动作 {action}", 400)


# ---------------------------------------------------------------------------
# 关联
# ---------------------------------------------------------------------------


@csrf_exempt
@_handle
def assertions_view(request):
    """建关联或移除关联。"""
    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    action = str(payload.get("action") or "attach")
    if action == "attach":
        return JsonResponse(library_concepts.attach_assertion(payload))
    if action == "detach":
        return JsonResponse(library_concepts.detach_assertion(payload))
    if action == "undo":
        return JsonResponse(library_concepts.undo_assertion(payload))
    return _error(f"不认识的动作 {action}", 400)


@csrf_exempt
@_handle
def publication_assertions_view(request, publication_id):
    """一道题的全部关联、它的整理程度、它的小问和解法分支。"""
    publication = PublishedQuestion.objects.filter(pk=publication_id).first()
    if publication is None:
        return _error("找不到这道题", 404)
    if request.method == "GET":
        return JsonResponse(
            {
                "publication_id": str(publication.pk),
                "assertions": library_concepts.list_assertions(publication),
                "coverage": library_concepts.coverage_for(publication),
                "subquestions": library_concepts.list_subquestions(publication),
                "variants": library_concepts.list_variants(publication),
                "operations": library_concepts.recent_assertion_operations(str(publication.pk)),
                "tag_revision": publication.tag_revision,
            }
        )
    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    action = str(payload.get("action") or "")
    if action == "ensure_subquestions":
        library_concepts.ensure_subquestions(publication)
        return JsonResponse({"ok": True, "subquestions": library_concepts.list_subquestions(publication)})
    if action == "ensure_variant":
        library_concepts.ensure_variant(
            publication, str(payload.get("name") or ""), origin=str(payload.get("origin") or "saved")
        )
        return JsonResponse({"ok": True, "variants": library_concepts.list_variants(publication)})
    if action == "mark_coverage":
        result = library_concepts.mark_coverage(
            publication,
            scope_key=str(payload.get("scope_key") or "whole"),
            dimension=str(payload.get("dimension") or "primary"),
            status=str(payload.get("status") or "unreviewed"),
            basis=str(payload.get("basis") or ""),
        )
        return JsonResponse(result)
    return _error(f"不认识的动作 {action}", 400)


# ---------------------------------------------------------------------------
# 教材目录与专题
# ---------------------------------------------------------------------------


@csrf_exempt
@_handle
def textbooks_view(request):
    if request.method != "GET":
        return _error("不支持的请求方式", 405)
    return JsonResponse({"textbooks": library_concepts.list_textbooks()})


@csrf_exempt
@_handle
def chapters_view(request):
    """教材目录。建章节必须明确是哪个教材下的哪一节。"""
    if request.method == "GET":
        return JsonResponse({"chapters": library_concepts.list_chapters(textbook_id=request.GET.get("textbook", ""))})
    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    chapter = library_concepts.ensure_chapter(
        str(payload.get("title") or ""),
        textbook_id=str(payload.get("textbook_id") or ""),
        parent_id=str(payload.get("parent_id") or ""),
        position=int(payload.get("position") or 0),
    )
    return JsonResponse({"ok": True, "chapter_id": str(chapter.pk)})


@csrf_exempt
@_handle
def collections_view(request):
    """个人专题。专题只存题目的引用，不复制题面。"""
    if request.method == "GET":
        return JsonResponse({"collections": library_concepts.list_collections()})
    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    action = str(payload.get("action") or "")
    if action == "add":
        return JsonResponse(
            library_concepts.add_to_collection(
                str(payload.get("collection_id") or ""),
                str(payload.get("publication_id") or ""),
                note=str(payload.get("note") or ""),
            )
        )
    if action == "remove":
        return JsonResponse(
            library_concepts.remove_from_collection(
                str(payload.get("collection_id") or ""), str(payload.get("publication_id") or "")
            )
        )
    if action == "create":
        from .models import LibraryCollection

        name = str(payload.get("name") or "").strip()[:120]
        if not name:
            return _error("专题需要一个名字")
        row, _ = LibraryCollection.objects.get_or_create(
            name=name, defaults={"description": str(payload.get("description") or "")[:300]}
        )
        return JsonResponse(
            {"ok": True, "collection_id": str(row.pk), "collections": library_concepts.list_collections()}
        )
    return _error(f"不认识的动作 {action}", 400)


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------


@csrf_exempt
@_handle
def search_view(request):
    """执行检索。命中原因一并给出。"""
    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取", 400)
    action = str(payload.get("action") or "")
    if action == "convert":
        # 中文条件的本地转换。认不出来的部分留在关键词里，不猜成正式事实。
        conversion = library_nlquery.convert(str(payload.get("text") or ""))
        result = conversion.as_dict()
        result["supported_phrases"] = library_nlquery.supported_phrases()
        return JsonResponse(result)
    query = payload.get("query") if isinstance(payload.get("query"), dict) else payload
    result = library_query.run_query(
        query, limit=int(payload.get("limit") or 200), offset=int(payload.get("offset") or 0)
    )
    return JsonResponse(result)


@csrf_exempt
@_handle
def saved_searches_view(request):
    """保存和取回完整搜索条件。"""
    if request.method == "GET":
        return JsonResponse({"saved_searches": library_query.list_saved_searches()})
    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    action = str(payload.get("action") or "save")
    if action == "save":
        result = library_query.save_search(str(payload.get("name") or ""), payload.get("query") or {})
        result["saved_searches"] = library_query.list_saved_searches()
        return JsonResponse(result)
    if action == "delete":
        result = library_query.delete_saved_search(str(payload.get("id") or ""))
        result["saved_searches"] = library_query.list_saved_searches()
        return JsonResponse(result)
    return _error(f"不认识的动作 {action}", 400)


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------


@csrf_exempt
@_handle
def migration_view(request):
    """迁移演练、状态、报告与校验。

    ``plan`` 只算不写；``apply`` 按批次写，重复执行不重复建实体；
    ``verify`` 核对迁移前后的业务不变量。
    """
    if request.method == "GET":
        action = str(request.GET.get("action") or "plan")
        batch = str(request.GET.get("batch") or "tag-refactor-1")
        if action == "verify":
            return JsonResponse(library_tag_migration.verify_migration(batch))
        if action == "report":
            return JsonResponse(library_tag_migration.migration_report(batch))
        return JsonResponse(library_tag_migration.plan_migration())

    if request.method != "POST":
        return _error("不支持的请求方式", 405)
    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容无法读取，什么都没改", 400)
    action = str(payload.get("action") or "plan")
    batch = str(payload.get("batch") or "tag-refactor-1")
    if action == "apply":
        result = library_tag_migration.apply_migration(batch)
        result["verify"] = library_tag_migration.verify_migration(batch)
        return JsonResponse(result)
    if action == "verify":
        return JsonResponse(library_tag_migration.verify_migration(batch))
    return JsonResponse(library_tag_migration.plan_migration())


def _guard(request):
    from .views import _guard as guard

    return guard(request)
