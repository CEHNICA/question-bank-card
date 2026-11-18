"""统一回收站：已归档的资料 + 已下架的试题。

Eagle 工作台左下角那个「🗑️ 回收站」要一份总账：现在看不见、但还在库里，
并且能原样放回去。它要回答三个问题：

  1. 哪些资料被归档了（Paper.archived）；
  2. 哪些题被下架了（PublishedQuestion.status = withdrawn）；
  3. 怎么把它们放回去，怎么彻底删掉。

之前这条链从头到尾是断的，所以面板永远是空的：
  - GET /api/library/trash 压根没有这条路由，前端 catch 掉 404 装作空的；
  - 恢复、彻底删除的四个端点同样不存在，四个按钮点下去全是 404。

口径跟题库其它地方保持一致：
  - 改动作必须来自本机题库页面（X-QB-Request 头），跟守卫同一条规矩；
  - 批量操作逐条回报：放回了几条、哪几条没放回、为什么。不静默吞掉，
    否则老师以为放好了，其实有一半还躺在回收站里。
"""

from __future__ import annotations

import shutil

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

from . import library
from .models import Paper, PublishedQuestion

# 面板上给试题预览的文字长度。够认出是哪道题，又不至于把整段题干塞进一行。
STEM_PREVIEW_LIMIT = 120


def _guard(request, *, json_body: bool = True):
    # 懒导入：views 会 import 本模块，模块顶层再 import views 会绕成环。
    from .views import _guard as guard

    return guard(request, json_body=json_body)


def _body(request):
    from .views import _body as body

    return body(request)


def _error(message: str, status: int = 400):
    from .views import _error as error

    return error(message, status)


def _paper_names(paper_ids) -> dict:
    """一次把资料名查齐。

    别用 select_related("paper")：Paper 上有 pages / structure /
    processing_plan / photos 四个 JSON 字段，一份教材的 structure 就有
    28 万字节。join 一次，445 道下架题就把同一份资料的 JSON 重新解析 445 遍
    —— 回收站接口 2.5 秒里 2.2 秒花在这上面，真正常用的字段只要 20 毫秒。

    名字的口径跟 Paper.display_name 一致：人改过的任务名优先，没有就用文件名。
    """
    if not paper_ids:
        return {}
    names = {}
    for pk, task_name, filename in Paper.objects.filter(pk__in=paper_ids).values_list("pk", "task_name", "filename"):
        names[str(pk)] = (task_name or "").strip() or (filename or "")
    return names


def _paper_counts(paper_ids) -> dict:
    """每份资料下还有几道在库里、几道在下架。"""
    if not paper_ids:
        return {}
    rows = (
        PublishedQuestion.objects.filter(paper_id__in=paper_ids)
        .values("paper_id")
        .annotate(
            published=Count("pk", filter=Q(status=PublishedQuestion.Status.PUBLISHED)),
            withdrawn=Count("pk", filter=Q(status=PublishedQuestion.Status.WITHDRAWN)),
        )
    )
    return {str(row["paper_id"]): {"published": row["published"], "withdrawn": row["withdrawn"]} for row in rows}


def _paper_json(paper: Paper, counts: dict) -> dict:
    """面板上要印的资料名、类型、归档时间。

    名字取 display_name（人改过的任务名优先），filename 留着 —— 题库卡片上的
    「出处」用的是 filename，两边对不上会让人以为找错了卷。
    """
    archived_at = paper.updated_at.isoformat() if paper.updated_at else None
    return {
        "id": str(paper.id),
        "name": paper.display_name,
        # 资料和试题用同一套「资料名」字段名：前端两边都读 paperName，
        # 少一个别名就得在两处各写一遍 fallback。
        "paperName": paper.display_name,
        "paper_name": paper.display_name,
        "task_name": paper.task_name,
        "filename": paper.filename,
        "material_type": paper.material_type,
        "materialType": paper.material_type,
        "archived": True,
        "archived_at": archived_at,
        "archivedAt": archived_at,
        "published": counts.get("published", 0),
        "withdrawn": counts.get("withdrawn", 0),
        "counts": counts,
    }


def _question_json(publication: PublishedQuestion, paper_name: str) -> dict:
    """面板上要印的试题行。

    定位字段（number / section / page）一并给出来：教材里「第 1 题」有一百多处，
    只印题号认不出是哪道题，回收站里同样认不出。
    """
    content = publication.content or {}
    stem = str(content.get("stem") or content.get("question") or "").strip()
    paper_id = str(publication.paper_id) if publication.paper_id else None
    page = library.first_page(content.get("sources"))
    section = str(content.get("section") or "")
    return {
        "id": str(publication.id),
        "paperName": paper_name,
        "paper_name": paper_name,
        "source_filename": publication.source_filename,
        "documentId": paper_id,
        "document_id": paper_id,
        "questionNumber": publication.number,
        "number": publication.number,
        "questionType": publication.question_type,
        "question_type": publication.question_type,
        "stemPreview": stem[:STEM_PREVIEW_LIMIT],
        "section": section,
        "page": page,
        "withdrawn_at": publication.withdrawn_at.isoformat() if publication.withdrawn_at else None,
    }


def _archived_paper_rows() -> list[dict]:
    # 只取面板要印的那几列。资料本身的 pages/structure/planning/photos 是
    # 几百 KB 的 JSON，回收站一个字节都用不上。
    papers = list(
        Paper.objects.filter(archived=True)
        .only("id", "task_name", "filename", "material_type", "archived", "updated_at")
        .order_by("-updated_at")
    )
    counts = _paper_counts([paper.pk for paper in papers])
    return [_paper_json(paper, counts.get(str(paper.id), {})) for paper in papers]


def _withdrawn_question_rows() -> list[dict]:
    # 只取面板要印的那几列：题面要读（stem/sources/section 都在 content 里），
    # extras 一个字段都用不上，别读。
    publications = list(
        PublishedQuestion.objects.filter(status=PublishedQuestion.Status.WITHDRAWN).only(
            "id", "number", "question_type", "paper_id", "source_filename", "published_at", "withdrawn_at", "content"
        )
    )
    names = _paper_names({item.paper_id for item in publications if item.paper_id})
    result = []
    for publication in publications:
        paper_name = names.get(str(publication.paper_id), "") or (publication.source_filename or "未知资料")
        result.append(_question_json(publication, paper_name))
    return result


def trash_payload() -> dict:
    papers = _archived_paper_rows()
    questions = _withdrawn_question_rows()
    return {
        "archived_papers": papers,
        "withdrawn_questions": questions,
        "counts": {"papers": len(papers), "questions": len(questions)},
    }


def trash_summary_payload() -> dict:
    """只要计数。

    首屏要的只是左下角那个「🗑️ 回收站」徽标上的一个数字。
    明细是 10 份资料 + 445 道下架题（真库实测 331 KB），首屏一个字节都用不上，
    却占了首屏网络里最大的一块 —— 比 /api/library/tags 还大。

    所以数个数就行，不读题面 content、不读 extras、更不拼明细行。
    """
    return {
        "counts": {
            "papers": Paper.objects.filter(archived=True).count(),
            "questions": PublishedQuestion.objects.filter(status=PublishedQuestion.Status.WITHDRAWN).count(),
        }
    }


# ------------------------------------------------------------------ 读


@csrf_exempt
def trash_view(request):
    """回收站总账。

    ?summary=1 只回计数 —— 首屏只要徽标数字时走这条，别把明细拖回来。
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    from .json_response import json_response

    # 只认「恰好是 1」：summary=0 / yes 都不算，那会让面板拿到空明细。
    if request.GET.get("summary") == "1":
        return json_response(trash_summary_payload())
    return json_response(trash_payload())


# ------------------------------------------------------------------ 写


def _restore_one(publication: PublishedQuestion) -> str:
    """放回一道题。返回空串表示成功，否则是一句人话（为什么没放回去）。"""
    if publication.status != PublishedQuestion.Status.WITHDRAWN:
        return "这道题不在回收站里"
    if publication.paper_id and publication.paper and publication.paper.archived:
        return "这份资料还在回收站里，先把资料放回来"
    if publication.question_id:
        newer = PublishedQuestion.objects.filter(
            question_id=publication.question_id, status=PublishedQuestion.Status.PUBLISHED
        ).exists()
        if newer:
            return "这道题已有新版本在库里，不能重复上架"
    publication.status = PublishedQuestion.Status.PUBLISHED
    publication.withdrawn_at = None
    publication.save(update_fields=["status", "withdrawn_at"])
    return ""


def _purge_one(publication: PublishedQuestion) -> str:
    """彻底删掉一道题（连带它的磁盘目录）。返回空串表示成功。"""
    if publication.status != PublishedQuestion.Status.WITHDRAWN:
        return "这道题不在回收站里，不能永久删除"
    folder = settings.DATA_ROOT / "library" / str(publication.pk)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    publication.delete()
    return ""


def _run_batch(ids, action) -> dict:
    """逐条处理一批题：成功几条、哪几条没成、为什么。"""
    done, skipped = [], []
    with transaction.atomic():
        for value in ids:
            publication = PublishedQuestion.objects.select_related("paper").filter(pk=value).first()
            if publication is None:
                skipped.append({"id": value, "reason": "题库里已经没有这道题"})
                continue
            reason = action(publication)
            if reason:
                skipped.append({"id": value, "reason": reason})
            else:
                done.append(value)
    return {"restored": done, "purged": done, "skipped": skipped}


def _parse_ids(request) -> tuple[list[str], object]:
    from . import library_browse

    payload = _body(request)
    if payload is None or "ids" not in payload:
        return [], _error("请提供 ids 题目编号列表")
    try:
        ids = library_browse.normalize_ids(payload.get("ids"))
    except library_browse.BrowseError as error:
        return [], _error(str(error))
    if not ids:
        return [], _error("请先勾选要处理的题目")
    return ids, None


@csrf_exempt
def restore_batch_view(request):
    """把一批下架的题放回题库。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    ids, failure = _parse_ids(request)
    if failure is not None:
        return failure
    result = _run_batch(ids, _restore_one)
    return _ok(
        {"restored": result["restored"], "restored_count": len(result["restored"]), "skipped": result["skipped"]}
    )


@csrf_exempt
def purge_batch_view(request):
    """把一批下架的题彻底删掉。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    ids, failure = _parse_ids(request)
    if failure is not None:
        return failure
    result = _run_batch(ids, _purge_one)
    return _ok({"purged": result["purged"], "purged_count": len(result["purged"]), "skipped": result["skipped"]})


@csrf_exempt
def restore_view(request, publication_id):
    """放回一道题。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    publication = PublishedQuestion.objects.select_related("paper").filter(pk=publication_id).first()
    if publication is None:
        return _error("题库里已经没有这道题", 404)
    reason = _restore_one(publication)
    if reason:
        return _error(reason)
    return _ok({"id": str(publication.id)})


@csrf_exempt
def purge_view(request, publication_id):
    """彻底删掉一道题。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    publication = PublishedQuestion.objects.select_related("paper").filter(pk=publication_id).first()
    if publication is None:
        return _error("题库里已经没有这道题", 404)
    reason = _purge_one(publication)
    if reason:
        return _error(reason)
    return _ok({"id": str(publication_id)})


def _ok(payload: dict):
    from .json_response import json_response

    return json_response({"ok": True, **payload})
