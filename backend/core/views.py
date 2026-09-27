from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import unicodedata
import uuid
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from PIL import Image

from . import imaging, library, m3import, mineru, photos, readers
from .figure_policy import (
    BLOCKED_MISSING, CONFIRMED_NO_FIGURE, CONFLICT, FLAG_NO_FIGURE, FLAG_UNCUED_FIGURE,
    FLAG_UNFOUND_FIGURE, OK, blocking_message, blocks_approval, cue_matches, figure_flag,
    stored_or_derived_review,
)
from .models import Paper, PublishedQuestion, Question
from .pipeline import PageStore, candidates_in, reorder_photo_pages
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

def paper_json(paper: Paper, *, with_counts: bool = True) -> dict:
    info = paper.photos or {}
    data = {
        "id": str(paper.id), "name": paper.display_name, "filename": paper.filename,
        "original_filename": paper.filename, "kind": paper.kind, "status": paper.status,
        "status_label": Paper.Status(paper.status).label, "progress": paper.progress, "total": paper.total,
        "error": paper.error, "notes": [*(info.get("notes") or []), *paper.notes], "pages": paper.pages,
        "pages_version": photos.order_version(info),
        "imported_from_m3": bool(paper.imported_from), "created_at": paper.created_at.isoformat(),
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
        rows = list(paper.questions.select_related("paper"))
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
    return {k: value.get(k) for k in ("engine", "stem", "options", "error") if k in value}


def question_json(question: Question) -> dict:
    figure_review = stored_or_derived_review(question)
    approval_valid = library.approval_is_current(question)
    figures = []
    for index, figure in enumerate(question.figures):
        digest = hashlib.sha1(json.dumps([figure["page_idx"], figure["bbox"]]).encode()).hexdigest()[:10]
        figures.append({**figure, "url": f"/api/questions/{question.id}/figures/{index}?v={digest}"})
    return {
        "id": question.id, "number": question.number, "section": question.section,
        "question_type": question.question_type, "regions": question.regions,
        "regions_changed": question.regions != question.regions_auto, "start_source": question.start_source,
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
    checker = readers.checker_engine()
    primary = readers.primary_engine()
    return JsonResponse({
        "upload_enabled": readers.configured("mineru") and primary is not None,
        "mineru": readers.configured("mineru"),
        "reader": primary.label if primary else None,
        "checker": checker.label if checker else None,
        "independent_checker": bool(checker and primary and checker.provider != primary.provider),
        "m3_available": m3import.m3_backend() is not None,
    })


# ---------------------------------------------------------------- 试卷

@csrf_exempt
def papers(request):
    if request.method == "GET":
        return JsonResponse({"papers": [paper_json(p) for p in Paper.objects.all()[:200]]})
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])
    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    if not readers.configured("mineru") or readers.primary_engine() is None:
        return _error("上传新试卷需要同时配置 MinerU Token 和 MiniMax API Key（请打开“题库题卡版 - 配置 API”设置）")
    uploads = request.FILES.getlist("file")
    if not uploads:
        return _error("请选择文件")
    kinds = []
    for item in uploads:
        kind = UPLOAD_KINDS.get(Path(item.name).suffix.lower())
        if kind is None:
            return _error(f"{Path(item.name).name}：只支持 PDF、JPG、PNG、WEBP 或 DOCX")
        if item.size > settings.MAX_UPLOAD_BYTES:
            return _error(f"{Path(item.name).name} 超过 50 MB")
        kinds.append(kind)
    if kinds[0] == "image" or len(uploads) > 1:
        if any(kind != "image" for kind in kinds):
            return _error("几个文件一起上传时只能都是照片（合成一份试卷）；PDF 和 Word 请一份一份上传")
        return _upload_photos(request, uploads)
    upload = uploads[0]
    suffix = Path(upload.name).suffix.lower()
    kind = kinds[0]
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    existing = Paper.objects.filter(sha256=digest.hexdigest()).exclude(status=Paper.Status.FAILED).first()
    if existing:
        return JsonResponse({"paper": paper_json(existing), "duplicate": True})
    paper = Paper(filename=Path(upload.name).name[:255], kind=kind, sha256=digest.hexdigest())
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
            mineru.validate_page_count(len(pages), "这份 PDF")
        except mineru.MineruError as exc:
            try:
                shutil.rmtree(staged)
            except OSError:
                pass  # 下次启动会按 .uploading-* 规则继续清理。
            return _error(str(exc))
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
        paper.save()
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


def _upload_photos(request, uploads) -> JsonResponse:
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
    combined = hashlib.sha256(f"photos:{int(enhance)}:{','.join(sorted(digests))}".encode()).hexdigest()
    existing = Paper.objects.filter(sha256=combined).exclude(status=Paper.Status.FAILED).first()
    if existing:
        return JsonResponse({"paper": paper_json(existing), "duplicate": True})
    first = Path(uploads[0].name).name
    name = first if len(uploads) == 1 else f"{Path(first).stem} 等 {len(uploads)} 张照片"
    paper = Paper(filename=name[:255], kind="image", sha256=combined)
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
    if paper.status not in (Paper.Status.READY, Paper.Status.FAILED) or not paper.blocks.exists():
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
                if paper.status != Paper.Status.FAILED:
                    return _error("只有处理失败的任务可以删除")
                if paper.publications.exists():
                    return _error("这项任务已有正式题库记录，为保留来源追溯不能删除")
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
    return JsonResponse({"paper": paper_json(paper),
                         "questions": [question_json(q) for q in paper.questions.all()]})


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
    paper = get_object_or_404(Paper, pk=paper_id)
    if paper.status != Paper.Status.FAILED:
        return _error("只有失败的试卷需要重试")
    has_blocks = paper.blocks.exists()
    paper.status = Paper.Status.SEGMENTING if has_blocks and not paper.questions.exists() else \
        Paper.Status.READING if paper.questions.exists() else Paper.Status.QUEUED
    paper.error = ""
    paper.save(update_fields=["status", "error", "updated_at"])
    paper.questions.filter(state=Question.State.RED).update(state=Question.State.WAITING)
    return JsonResponse({"paper": paper_json(paper)})


@csrf_exempt
def paper_resegment(request, paper_id):
    """按最新规则重新切题；内容没变的题卡（包括已通过的）原样保留。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    rejected = _guard(request)
    if rejected:
        return rejected
    paper = get_object_or_404(Paper, pk=paper_id)
    if paper.status not in (Paper.Status.READY, Paper.Status.FAILED) or not paper.blocks.exists():
        return _error("这份试卷还在处理中，或还没有解析结果")
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
    payload = _body(request)
    number = payload.get("number") if payload else None
    regions = _valid_regions(paper, payload.get("regions")) if payload else None
    if type(number) is not int or not 1 <= number <= 999 or regions is None:
        return _error("需要题号（1–999）和原卷范围")
    if paper.questions.filter(number=number).exists():
        return _error(f"已经有第 {number} 题了")
    question = Question.objects.create(
        paper=paper, number=number, regions=regions, regions_auto=regions, start_source="manual",
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
            _apply_figure_review(question, stored_or_derived_review(question))
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
            question.figure_review = {}
            _clear_approval(question)
            question.state = Question.State.WAITING
            question.reread_requested = True
        elif action == "figures":
            figures = payload.get("figures")
            if not isinstance(figures, list) or len(figures) > 12:
                return _error("配图格式不正确")
            pages = {p["page_idx"] for p in question.paper.pages}
            cleaned = []
            for item in figures:
                bbox = _valid_bbox(item.get("bbox")) if isinstance(item, dict) else None
                if bbox is None or item.get("page_idx") not in pages or item.get("slot") not in SLOTS:
                    return _error("配图格式不正确")
                cleaned.append({"slot": item["slot"], "page_idx": item["page_idx"], "bbox": bbox, "source": "manual"})
            previous_figures = list(question.figures or [])
            question.figures = cleaned
            if cleaned:
                review = {
                    "status": OK, "source": "human", "reason": "配图已经由人工设置",
                    "signals": ["manual_figure"], "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": 0, "confirmed_at": now.isoformat(),
                }
            else:
                review = {
                    "status": CONFIRMED_NO_FIGURE, "source": "human", "reason": "已人工确认本题确实无图",
                    "signals": ["human_confirmed_no_figure"],
                    "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": len(question.figure_candidates or []), "confirmed_at": now.isoformat(),
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
                question.figures = []
                _apply_figure_review(question, {
                    "status": CONFIRMED_NO_FIGURE, "source": "human", "reason": "已人工确认本题确实无图",
                    "signals": ["human_confirmed_no_figure"],
                    "cue_matches": cue_matches(question.stem, question.options),
                    "excluded_count": len(question.figure_candidates or []), "confirmed_at": now.isoformat(),
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
                            and item.get("source") in {"auto", "manual", "other"}):
                        restored.append({
                            "slot": item["slot"], "page_idx": item["page_idx"], "bbox": bbox,
                            "source": item["source"],
                        })
                question.figures = restored
                question.figure_review = {}
                _apply_figure_review(question, stored_or_derived_review(question))
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
    question = _question(question_id)
    if question.publications.filter(status=PublishedQuestion.Status.PUBLISHED).exists():
        return _error("这道题已经入库，请先在正式题库里撤回")
    paper = question.paper
    question.delete()
    return JsonResponse({"deleted": True, "paper": paper_json(paper)})


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
