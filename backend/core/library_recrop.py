"""In-library original-page recropping and AI re-OCR for published questions.

Allows teachers in the library workbench (/eagle) to adjust missed or clipped
crop boxes directly on the high-resolution source paper:
- replace_figure / add_figure: immediately update stem/option figure crops;
- replace_source_image: update original-image question regions and re-crop;
- ocr_preview / apply_ocr: re-run vision OCR on adjusted regions, preview the
  candidate side-by-side against the current card, and publish a new version
  once adopted (preserving tags, OneNote history, and active solutions).
"""

from __future__ import annotations

import shutil
from copy import deepcopy

from django.conf import settings
from django.db import OperationalError, transaction
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import imaging, library, library_question_editor, library_solutions, prose, qtypes, readers, source_images
from .figure_policy import figure_flag
from .models import LibrarySolution, Paper, PublishedQuestion, Question
from .pipeline import PageStore
from .textnorm import fix_symbols


class RecropError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _parse_regions(paper: Paper, raw_regions) -> list[dict]:
    if not isinstance(raw_regions, list) or not 1 <= len(raw_regions) <= 12:
        raise RecropError("请先在原卷上框选 1–12 个有效区域")
    cleaned: list[dict] = []
    for item in raw_regions:
        if not isinstance(item, dict):
            raise RecropError("原卷框选坐标格式不正确")
        page_idx = item.get("page_idx")
        box = item.get("bbox")
        if type(page_idx) is not int or not isinstance(box, list) or len(box) != 4:
            raise RecropError("原卷页码或矩形坐标格式不正确")
        try:
            normalized_box = [round(float(v), 2) for v in box]
        except (TypeError, ValueError):
            raise RecropError("原卷矩形坐标必须为数字") from None
        cleaned.append({"page_idx": page_idx, "bbox": normalized_box})
    if not source_images.valid_regions(paper, cleaned):
        raise RecropError("原卷裁剪区域太小或超出当前试卷页码范围")
    return cleaned


def _clone_active_solution(old_pub: PublishedQuestion, new_pub: PublishedQuestion) -> None:
    """Carry over the previous version's active LibrarySolution record and images."""
    old_sol_id = str((old_pub.extras or {}).get("solution_id") or "").strip()
    if not old_sol_id:
        return
    old_sol = LibrarySolution.objects.filter(pk=old_sol_id, publication=old_pub).first()
    if old_sol is None:
        return
    new_figures = deepcopy(old_sol.figures or [])
    if new_figures:
        old_dir = settings.DATA_ROOT / "library-solutions" / str(old_pub.pk)
        new_dir = settings.DATA_ROOT / "library-solutions" / str(new_pub.pk)
        new_dir.mkdir(parents=True, exist_ok=True)
        kept = []
        for fig in new_figures:
            if not isinstance(fig, dict) or not fig.get("id"):
                continue
            aid = str(fig["id"])
            for src in old_dir.glob(f"{aid}*"):
                if src.is_file():
                    shutil.copyfile(src, new_dir / src.name)
            spec = new_dir / f"{aid}.json"
            if spec.is_file():
                import json

                try:
                    data = json.loads(spec.read_text(encoding="utf-8"))
                    data["publication_id"] = str(new_pub.pk)
                    spec.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                except (OSError, ValueError):
                    pass
            kept.append(fig)
        new_figures = kept
    cloned = LibrarySolution.objects.create(
        publication=new_pub,
        parent=old_sol,
        fingerprint=library.generation_fingerprint(new_pub.content, new_pub.pk),
        answer=old_sol.answer,
        analysis=old_sol.analysis,
        figures=new_figures,
    )
    extras = {**deepcopy(new_pub.extras or {}), "solution_id": str(cloned.pk)}
    library.save_extras(new_pub, extras)


def _load_locked_pair(publication_id):
    try:
        old_pub = PublishedQuestion.objects.select_for_update().get(pk=publication_id)
    except PublishedQuestion.DoesNotExist:
        raise RecropError("题库中找不到这道题", 404) from None
    if old_pub.status != PublishedQuestion.Status.PUBLISHED:
        raise RecropError("这道入库题已撤回或已被新版替代，请刷新题库", 409)
    try:
        paper = Paper.objects.select_for_update().get(pk=old_pub.paper_id)
        question = (
            Question.objects.select_for_update()
            .select_related("paper", "group")
            .get(pk=old_pub.question_id, paper=paper)
        )
    except (Paper.DoesNotExist, Question.DoesNotExist):
        raise RecropError("来源原卷或题卡记录已删除，无法重新裁剪", 409) from None
    latest = question.publications.order_by("-version").first()
    if latest is None or latest.pk != old_pub.pk:
        raise RecropError("题库已有更新的版本，请刷新后重裁", 409)
    return paper, question, old_pub


def _publish_response(new_pub: PublishedQuestion, old_pub: PublishedQuestion) -> dict:
    sol = library_solutions.selected(new_pub)
    return {
        "ok": True,
        "previous_id": str(old_pub.pk),
        "publication": library.publication_json(new_pub),
        "solution": library_solutions.solution_json(sol) if sol is not None else None,
    }


def _publish_question_update(question: Question, old_pub: PublishedQuestion) -> PublishedQuestion:
    question.error = ""
    question.reread_requested = False
    question.ocr_pending = False
    question.ocr_suggestion = {}
    question.content_revision += 1
    library.approve(question, now=timezone.now(), source="human")
    question.save()
    new_pub, _created = library.publish(question)
    if new_pub.pk != old_pub.pk:
        _clone_active_solution(old_pub, new_pub)
        new_pub.refresh_from_db()
    return new_pub


def replace_or_add_figure(publication_id, payload: dict) -> dict:
    action = str(payload.get("action") or "").strip()
    is_replace = action == "replace_figure"
    regions = _parse_regions(get_object_or_404(PublishedQuestion, pk=publication_id).paper, [payload])
    region = regions[0]
    figure_index = payload.get("figure_index", 0)
    if is_replace and (type(figure_index) is not int or figure_index < 0):
        raise RecropError("要替换的配图序号不正确")
    slot = str(payload.get("slot") or payload.get("role") or "").strip()

    with transaction.atomic():
        paper, question, old_pub = _load_locked_pair(publication_id)
        if question.body_mode == "source_image":
            raise RecropError("原图题整题本身即为原卷截图，请点击「设为原图正文并更新」或「按新框重新 AI 识别」", 409)
        figures = deepcopy(list(question.figures or []))
        if is_replace and figure_index < len(figures):
            target_slot = slot or str(figures[figure_index].get("slot") or "stem")
            entry = {
                "page_idx": region["page_idx"],
                "bbox": region["bbox"],
                "source": "manual",
                "confirmed": True,
                "slot": target_slot if target_slot in {"stem", *library.OPTION_KEYS} else "stem",
            }
            figures[figure_index] = entry
        else:
            target_slot = slot if slot in {"stem", *library.OPTION_KEYS} else "stem"
            if len(figures) >= 12:
                raise RecropError("每道题最多支持 12 张配图")
            figures.append(
                {
                    "page_idx": region["page_idx"],
                    "bbox": region["bbox"],
                    "source": "manual",
                    "confirmed": True,
                    "slot": target_slot,
                }
            )
        previous_figures = deepcopy(question.figures)
        question.figures = figures
        # Pre-render the cropped figure file right away so invalid coords fail fast inside transaction.
        target_idx = figure_index if (is_replace and figure_index < len(figures)) else len(figures) - 1
        try:
            library.figure_file(question, target_idx)
        except (OSError, ValueError, IndexError, RuntimeError) as error:
            raise RecropError("无法从原卷裁出新配图，请确认原卷页面清晰且完整", 409) from error
        library_question_editor._review_figures(question, previous_figures, timezone.now())
        question.flags = [flag for flag in question.flags or [] if not figure_flag(flag)]
        if question.state not in library.REVIEWABLE_STATES or not question.flags:
            question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
        question.edited = True
        new_pub = _publish_question_update(question, old_pub)
        return _publish_response(new_pub, old_pub)


def replace_source_image(publication_id, payload: dict) -> dict:
    with transaction.atomic():
        paper, question, old_pub = _load_locked_pair(publication_id)
        regions = _parse_regions(paper, payload.get("regions"))
        question.regions = regions
        question.body_mode = "source_image"
        question.figures = []
        question.flags = [flag for flag in question.flags or [] if not figure_flag(flag)]
        question.edited = True
        question.state = Question.State.GREEN
        if not source_images.body_valid(question):
            raise RecropError("无法从原卷新范围生成完整截图，请重试或换一个范围", 409)
        new_pub = _publish_question_update(question, old_pub)
        return _publish_response(new_pub, old_pub)


def ocr_preview(publication_id, payload: dict) -> dict:
    publication = get_object_or_404(PublishedQuestion.objects.select_related("paper", "question"), pk=publication_id)
    if publication.status != PublishedQuestion.Status.PUBLISHED:
        raise RecropError("这道入库题已撤回或已被新版替代，请刷新题库", 409)
    paper = publication.paper
    question = publication.question
    if paper is None or question is None:
        raise RecropError("缺少关联的原卷或题卡记录，无法重新识读", 409)
    regions = _parse_regions(paper, payload.get("regions"))
    engine = readers.primary_engine()
    if engine is None or readers.assistant_mode():
        raise RecropError(
            "本机尚未配置可用的看图读题 API：请按 Ctrl+, 打开「偏好设置 → API 配置」填写读题密钥（替换配图或保存为原图正文无需 API）。",
            409,
        )
    store = PageStore(paper)
    try:
        stacked, _ = imaging.stack_regions(regions, store.load)
    except (OSError, ValueError, KeyError) as error:
        raise RecropError("无法合成新框选的原卷截图，请检查原卷页面是否存在", 409) from error
    preview_data_url = imaging.jpeg_data_url(stacked)
    with_figures = bool(question.figures or question.figure_candidates or payload.get("with_figures"))
    try:
        with readers.without_speculative_duplicates():
            reading = readers.read_question(
                engine,
                preview_data_url,
                publication.number,
                with_figures=with_figures,
                source_kind=question.source_kind or Question.SourceKind.UNKNOWN,
            )
    except readers.ReaderQuotaExhausted as error:
        raise RecropError(str(error) or "看图读题服务额度已用尽", 429) from error
    except readers.ReaderError as error:
        raise RecropError(str(error) or "看图读题服务暂时没有返回结果", 502) from error
    if reading.get("unreadable"):
        raise RecropError("读题服务表示没能看清这块新范围中的内容，请放大或把框拉宽后重试", 409)

    raw_stem = fix_symbols(str(reading.get("stem") or "").strip())
    raw_options = {
        k: fix_symbols(str(v).strip())
        for k, v in (reading.get("options") or {}).items()
        if k in library.OPTION_KEYS and str(v).strip()
    }
    raw_type = str(reading.get("question_type") or publication.question_type or "free_response")
    if not qtypes.decided(raw_type):
        raw_type = qtypes.infer(raw_type, raw_stem, raw_options)
        if not qtypes.decided(raw_type):
            raw_type = publication.question_type if qtypes.decided(publication.question_type) else "free_response"
    tidied = prose.tidy_fields(
        {"stem": raw_stem, "options": raw_options, "question_type": raw_type}, origin=question.origin
    )
    return {
        "ok": True,
        "preview": {
            "stem": tidied["stem"].strip(),
            "options": tidied["options"],
            "question_type": raw_type,
            "engine": str(reading.get("engine") or engine.label),
            "image_data_url": preview_data_url,
            "regions": regions,
        },
    }


def apply_ocr(publication_id, payload: dict) -> dict:
    stem = library_question_editor._text(payload.get("stem") or "", "题干", 20000)
    if not stem:
        raise RecropError("识别后的题干不能为空")
    options_raw = payload.get("options") or {}
    if not isinstance(options_raw, dict) or set(options_raw) - set(library.OPTION_KEYS):
        raise RecropError("选项格式不正确（仅支持 A–H）")
    options = {
        k: library_question_editor._text(v, "选项", 4000)
        for k, v in options_raw.items()
        if isinstance(v, str) and v.strip()
    }
    kind = str(payload.get("question_type") or "free_response")
    if kind not in qtypes.DECIDED_TYPES:
        raise RecropError("请选择有效的题型（单选、多选、填空、判断或解答）")

    with transaction.atomic():
        paper, question, old_pub = _load_locked_pair(publication_id)
        regions = (
            _parse_regions(paper, payload.get("regions")) if payload.get("regions") else list(question.regions or [])
        )
        tidied = prose.tidy_fields(
            {
                "stem": fix_symbols(stem),
                "options": {k: fix_symbols(v) for k, v in options.items() if v},
                "question_type": kind,
            },
            origin=question.origin,
        )
        question.regions = regions
        question.body_mode = "text"
        question.stem = tidied["stem"].strip()
        question.options = tidied["options"]
        question.question_type = kind
        question.origin = tidied["origin"]
        question.type_locked = True
        question.edited = True
        question.text_source = "human"
        question.flags = [flag for flag in question.flags or [] if "截图" in flag and not figure_flag(flag)]
        question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
        if not question.stem:
            raise RecropError("整理格式后的题干不能为空")
        new_pub = _publish_question_update(question, old_pub)
        return _publish_response(new_pub, old_pub)


@csrf_exempt
def recrop_view(request, publication_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request, limit=500_000)
    if not isinstance(payload, dict):
        return _error("请提供有效的重框裁剪请求内容")
    action = str(payload.get("action") or "").strip()
    try:
        if action in {"replace_figure", "add_figure"}:
            return JsonResponse(replace_or_add_figure(publication_id, payload))
        if action == "replace_source_image":
            return JsonResponse(replace_source_image(publication_id, payload))
        if action == "ocr_preview":
            return JsonResponse(ocr_preview(publication_id, payload))
        if action == "apply_ocr":
            return JsonResponse(apply_ocr(publication_id, payload))
        raise RecropError("不支持的重框操作类型")
    except RecropError as error:
        return _error(str(error), error.status)
    except OperationalError:
        return _error("题库正忙，请稍后重试", 503)
