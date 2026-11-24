"""外部 AI 助手自主探查与批量切题引擎（Agent-First Intake）。

允许外部助手在阅读原页截图（`pages`）与版面块（`blocks`）后，按以下双轨一次落库题卡：
1. **直录轨（传入 `stem`）**：助手直接视觉识读并提交规范化题干/选项/答案/解析，
   `body_mode="text"`、`text_source="assistant"`、`edited=True`，跳过内置 OCR 队列。
2. **切图待识读轨（不传 `stem`）**：助手仅提交精确的切题框（`regions` / `figures`），
   `body_mode="source_image"`，后续可随时由人工核对或交给 `read-cut-questions` 队列识读。

所有助手切出的题卡均停留于 `YELLOW` 待人工核对状态，绝不自动通过、绝不自动入库。
重切（`replace_scope`）受硬保护：绝不删除或覆盖已通过（`approved`）、
已入库（在用 `PublishedQuestion`）或已有人工/助手编辑（`edited`）的题卡，且支持整批撤销。
"""

from __future__ import annotations

import math
import uuid
from copy import deepcopy
from typing import Any

from django.db import IntegrityError, OperationalError, transaction
from django.utils import timezone

from . import figure_policy, library, prose, qtypes, question_layout
from .models import Paper, PublishedQuestion, Question, QuestionDeletionBatch, QuestionGroup, QuestionLayoutOperation
from .pipeline import candidates_in
from .textnorm import fix_symbols

REPLACE_SCOPES = {"none", "page", "group", "paper", "question_ids"}
SLOTS = {"stem", *library.OPTION_KEYS}
TYPES = set(qtypes.TYPES)


def _valid_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
        return None
    x0, y0, x1, y1 = (round(float(v), 1) for v in value)
    x0, y0, x1, y1 = max(0.0, x0), max(0.0, y0), min(1000.0, x1), min(1000.0, y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None
    return [x0, y0, x1, y1]


def _normalize_figures(paper: Paper, raw_figures: Any) -> list[dict]:
    if raw_figures is None:
        return []
    if not isinstance(raw_figures, list) or len(raw_figures) > 20:
        raise question_layout.LayoutError("配图列表格式不正确（最多 20 张）。")
    valid_pages = {p["page_idx"] for p in paper.pages}
    cleaned: list[dict] = []
    for item in raw_figures:
        if not isinstance(item, dict):
            raise question_layout.LayoutError("配图格式不正确。")
        slot = item.get("slot") or "stem"
        bbox = _valid_bbox(item.get("bbox"))
        page_idx = item.get("page_idx")
        if slot not in SLOTS or type(page_idx) is not int or page_idx not in valid_pages or bbox is None:
            raise question_layout.LayoutError("配图槽位、页码或坐标（0–1000）格式不正确。")
        cleaned.append({"slot": slot, "page_idx": page_idx, "bbox": bbox, "source": "manual"})
    return cleaned


def _validate_section(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise question_layout.LayoutError("章节目录格式不正确。")
    section = value.strip()
    if len(section) > 120 or any(ord(char) < 32 for char in section):
        raise question_layout.LayoutError("章节目录格式不正确（最多 120 字，且不能包含控制字符）。")
    return section


def _resolve_group(paper: Paper, item: dict, regions: list[dict], groups_cache: list[QuestionGroup]) -> QuestionGroup:
    """解析或按章节标题创建所属 QuestionGroup。"""
    region_pages = sorted({region["page_idx"] for region in regions})

    def covers(group: QuestionGroup) -> bool:
        pages = (group.metadata or {}).get("pages")
        if not isinstance(pages, list):
            pages = list(range((group.page_start or 1) - 1, group.page_end or len(paper.pages)))
        return set(region_pages).issubset({page for page in pages if type(page) is int})

    group_id = item.get("group_id")
    if group_id is not None:
        if type(group_id) is not int:
            raise question_layout.LayoutError("题组编号格式不正确。")
        found = next((group for group in groups_cache if group.pk == group_id), None)
        if found is None or not covers(found):
            raise question_layout.LayoutError("所选切题范围不属于指定的题组。")
        return found

    raw_title = item.get("group_title")
    if raw_title is not None:
        if not isinstance(raw_title, str) or not raw_title.strip() or len(raw_title.strip()) > 255:
            raise question_layout.LayoutError("题组标题格式不正确（1–255 字）。")
        title = raw_title.strip()
        same_title = next((group for group in groups_cache if group.title == title), None)
        if same_title is not None:
            # 若已有同名题组且未覆盖当前跨页范围，扩充其页码集合，保持章节归属一致
            if not covers(same_title):
                meta = dict(same_title.metadata or {})
                existing_pages = meta.get("pages")
                if not isinstance(existing_pages, list):
                    existing_pages = list(
                        range((same_title.page_start or 1) - 1, same_title.page_end or len(paper.pages))
                    )
                merged_pages = sorted({*(p for p in existing_pages if type(p) is int), *region_pages})
                meta["pages"] = merged_pages
                same_title.metadata = meta
                same_title.page_start = min(merged_pages) + 1
                same_title.page_end = max(merged_pages) + 1
                same_title.save(update_fields=["metadata", "page_start", "page_end"])
            return same_title
        next_seq = max((group.sequence for group in groups_cache), default=-1) + 1
        created_group = QuestionGroup.objects.create(
            paper=paper,
            title=title,
            kind=(
                QuestionGroup.Kind.CHAPTER
                if paper.material_type == Paper.MaterialType.BOOK
                else QuestionGroup.Kind.EXAM
            ),
            sequence=next_seq,
            page_start=min(region_pages) + 1,
            page_end=max(region_pages) + 1,
            metadata={"pages": region_pages, "source": "assistant"},
        )
        groups_cache.append(created_group)
        return created_group

    matches = [group for group in groups_cache if covers(group)]
    if len(matches) == 1:
        return matches[0]
    if not groups_cache:
        all_pages = list(range(len(paper.pages)))
        created_group = QuestionGroup.objects.create(
            paper=paper,
            title=paper.display_name[:255],
            kind=(
                QuestionGroup.Kind.CHAPTER
                if paper.material_type == Paper.MaterialType.BOOK
                else QuestionGroup.Kind.EXAM
            ),
            sequence=0,
            page_start=1,
            page_end=max(1, len(all_pages)),
            metadata={"pages": all_pages, "source": "assistant"},
        )
        groups_cache.append(created_group)
        return created_group
    raise question_layout.LayoutError("当前存在多个覆盖该页的题组，请提供 group_id 或 group_title。")


def _select_replace_candidates(paper: Paper, payload: dict, incoming_pages: set[int]) -> tuple[list[Question], str]:
    """按 replace_scope 收集可能需要被替换的旧草稿题卡。"""
    scope = payload.get("replace_scope") or "none"
    if not isinstance(scope, str) or scope not in REPLACE_SCOPES:
        raise question_layout.LayoutError("replace_scope 只能是 none、page、group、paper 或 question_ids。")
    if scope == "none":
        return [], scope

    base_qs = Question.objects.select_for_update().filter(paper=paper).select_related("paper", "group").order_by("id")
    if scope == "paper":
        return list(base_qs), scope
    if scope == "page":
        raw_pages = payload.get("replace_pages")
        if raw_pages is None:
            target_pages = incoming_pages
        else:
            valid_pages = {p["page_idx"] for p in paper.pages}
            if (
                not isinstance(raw_pages, list)
                or not raw_pages
                or any(type(p) is not int or p not in valid_pages for p in raw_pages)
            ):
                raise question_layout.LayoutError("replace_pages 格式或页码范围不正确。")
            target_pages = set(raw_pages)
        matched = [
            question
            for question in base_qs
            if any(
                isinstance(region, dict) and region.get("page_idx") in target_pages
                for region in (question.regions or [])
            )
        ]
        return matched, scope
    if scope == "group":
        group_id = payload.get("replace_group_id")
        if type(group_id) is not int or not paper.question_groups.filter(pk=group_id).exists():
            raise question_layout.LayoutError("replace_group_id 不存在或不属于当前资料。")
        return list(base_qs.filter(group_id=group_id)), scope

    # scope == "question_ids"
    raw_ids = payload.get("replace_question_ids")
    if (
        not isinstance(raw_ids, list)
        or not raw_ids
        or len(raw_ids) > 500
        or any(type(qid) is not int or qid < 1 for qid in raw_ids)
    ):
        raise question_layout.LayoutError("replace_question_ids 需提供 1–500 个整数题号 ID。")
    unique_ids = list(dict.fromkeys(raw_ids))
    rows = list(base_qs.filter(pk__in=unique_ids))
    if len(rows) != len(unique_ids):
        raise question_layout.LayoutError("replace_question_ids 中有题目不存在或已删除。", 404)
    return rows, scope


def _partition_protected(candidates: list[Question]) -> tuple[list[Question], list[dict]]:
    """把受保护题卡（已通过 / 已入库 / 已编辑）与可替换草稿严格分离。"""
    if not candidates:
        return [], []
    candidate_ids = [question.pk for question in candidates]
    live_published = set(
        PublishedQuestion.objects.filter(
            question_id__in=candidate_ids, status=PublishedQuestion.Status.PUBLISHED
        ).values_list("question_id", flat=True)
    )
    replaceable: list[Question] = []
    skipped_protected: list[dict] = []
    for question in candidates:
        if question.approved:
            skipped_protected.append({"id": question.pk, "number": question.number, "reason": "已审核通过"})
        elif question.pk in live_published:
            skipped_protected.append({"id": question.pk, "number": question.number, "reason": "已在正式题库中"})
        elif question.edited:
            skipped_protected.append({"id": question.pk, "number": question.number, "reason": "已有人工或助手编辑内容"})
        else:
            replaceable.append(question)
    return replaceable, skipped_protected


def _prepare_text_fields(item: dict) -> dict | None:
    """若助手提交了 stem，则按直录轨校验并清洗全部文本字段；未传 stem 时返回 None（走原图轨）。"""
    raw_stem = item.get("stem")
    if raw_stem is None:
        return None
    if not isinstance(raw_stem, str):
        raise question_layout.LayoutError("stem 必须是字符串。")
    if not raw_stem.strip():
        return None
    if len(raw_stem) > 20000:
        raise question_layout.LayoutError("题干长度不能超过 20000 字。")

    options = item.get("options") if item.get("options") is not None else {}
    if (
        not isinstance(options, dict)
        or set(options) - set(library.OPTION_KEYS)
        or not all(isinstance(v, str) and len(v) <= 4000 for v in options.values())
    ):
        raise question_layout.LayoutError("options 格式不正确（仅支持 A–H 键与字符串值）。")

    question_type = item.get("question_type") or "unknown"
    if question_type not in TYPES:
        raise question_layout.LayoutError("question_type 题型不正确。")

    for field_name in ("answer", "analysis"):
        if (
            field_name in item
            and item[field_name] is not None
            and (not isinstance(item[field_name], str) or len(item[field_name]) > 20000)
        ):
            raise question_layout.LayoutError(f"{field_name} 格式或长度不正确。")
    raw_origin = item.get("origin")
    if raw_origin is not None and (not isinstance(raw_origin, str) or len(raw_origin) > 400):
        raise question_layout.LayoutError("origin 题源格式或长度不正确。")

    origin = prose.clean_origin(raw_origin) if raw_origin else ""
    answer_val = (item.get("answer") or "").strip()
    analysis_val = (item.get("analysis") or "").strip()
    tidied = prose.tidy_fields(
        {
            "stem": fix_symbols(raw_stem.strip()),
            "options": {k: fix_symbols(v.strip()) for k, v in options.items() if v.strip()},
            "question_type": question_type,
            "answer": fix_symbols(answer_val),
            "analysis": fix_symbols(analysis_val),
        },
        origin=origin,
    )
    resolved_type = tidied.get("question_type", question_type)
    return {
        "stem": tidied["stem"].strip(),
        "options": tidied["options"],
        "question_type": resolved_type,
        "answer": tidied["answer"],
        "analysis": tidied["analysis"],
        "origin": tidied["origin"],
        "type_locked": qtypes.decided(resolved_type),
    }


def apply_cuts(paper_id: Any, payload: Any) -> tuple[Any, bool, list[int], list[int], list[dict]]:
    """执行助手批量切题（支持双轨与受保护重切），返回 `(operation, repeated, created_ids, replaced_ids, skipped_protected)`。"""
    if not isinstance(payload, dict):
        raise question_layout.LayoutError("请求内容不正确。")
    normalized_payload = dict(payload)
    if not normalized_payload.get("client_request_id"):
        normalized_payload["client_request_id"] = str(uuid.uuid4())
    identity, request_hash = question_layout._request(normalized_payload)
    existing = question_layout._existing(paper_id, identity, request_hash)
    if existing:
        return (
            existing,
            True,
            list(existing.target_ids or []),
            list(existing.source_ids or []),
            list((existing.after_snapshot or {}).get("skipped_protected") or []),
        )

    raw_items = normalized_payload.get("questions")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 500:
        raise question_layout.LayoutError("questions 需为包含 1–500 道题目的列表。")

    has_revision = "layout_revision" in normalized_payload and normalized_payload["layout_revision"] is not None
    requested_revision = question_layout._revision(normalized_payload) if has_revision else None
    agent_name = library.agent_name(normalized_payload.get("agent"))

    try:
        with transaction.atomic():
            if requested_revision is not None:
                paper = question_layout._claim(paper_id, requested_revision)
            else:
                paper = Paper.objects.select_for_update().filter(pk=paper_id).first()
                if paper is None:
                    raise question_layout.LayoutError("资料不存在。", 404)
                if paper.archived or paper.status != Paper.Status.READY:
                    raise question_layout.LayoutError("请先恢复资料并完成原卷处理，再切分题目。", 409)

            existing = question_layout._existing(paper_id, identity, request_hash)
            if existing:
                return (
                    existing,
                    True,
                    list(existing.target_ids or []),
                    list(existing.source_ids or []),
                    list((existing.after_snapshot or {}).get("skipped_protected") or []),
                )

            # 1. 先校验全部待建题卡参数，任一题校验失败即整批回滚
            groups_cache = list(QuestionGroup.objects.filter(paper=paper).order_by("sequence", "id"))
            prepared_specs: list[dict] = []
            incoming_pages: set[int] = set()
            for item in raw_items:
                if not isinstance(item, dict):
                    raise question_layout.LayoutError("每道题目必须是对象。")
                regions = question_layout._regions(paper, item.get("regions"))
                incoming_pages.update(region["page_idx"] for region in regions)
                figures = _normalize_figures(paper, item.get("figures"))
                confirm_no_figure = bool(item.get("confirm_no_figure"))
                if confirm_no_figure and figures:
                    raise question_layout.LayoutError("已提供配图坐标时，不能同时标记 confirm_no_figure。")
                section = _validate_section(item.get("section"))
                source_kind = item.get("source_kind") or Question.SourceKind.MANUAL
                if source_kind not in Question.SourceKind.values:
                    raise question_layout.LayoutError("source_kind 只能是 example、exercise、manual 或 unknown。")
                raw_number = item.get("number")
                if raw_number is not None and (type(raw_number) is not int or raw_number < 1):
                    raise question_layout.LayoutError("number 题号必须是大于等于 1 的整数。")
                text_fields = _prepare_text_fields(item)
                raw_type = item.get("question_type") or "unknown"
                if raw_type not in TYPES:
                    raise question_layout.LayoutError("question_type 题型不正确。")
                raw_origin = item.get("origin") or ""
                if not isinstance(raw_origin, str) or len(raw_origin) > 400:
                    raise question_layout.LayoutError("origin 题源格式不正确。")
                group = _resolve_group(paper, item, regions, groups_cache)
                prepared_specs.append(
                    {
                        "group": group,
                        "number": raw_number,
                        "regions": regions,
                        "figures": figures,
                        "confirm_no_figure": confirm_no_figure,
                        "section": section,
                        "source_kind": source_kind,
                        "text_fields": text_fields,
                        "question_type": text_fields["question_type"] if text_fields else raw_type,
                        "origin": text_fields["origin"] if text_fields else prose.clean_origin(raw_origin),
                    }
                )

            # 2. 处理 replace_scope：先隔离受保护题卡，再软删除可替换草稿
            candidates, scope_name = _select_replace_candidates(paper, normalized_payload, incoming_pages)
            replaceable, skipped_protected = _partition_protected(candidates)
            strict_replace = bool(normalized_payload.get("strict_replace")) or scope_name == "question_ids"
            if skipped_protected and strict_replace:
                reasons = "；".join(
                    f"第{entry['number']}题(#{entry['id']}){entry['reason']}" for entry in skipped_protected[:5]
                )
                raise question_layout.LayoutError(f"包含受保护题目，无法重切覆盖：{reasons}", 409)

            replaceable_ids = [question.pk for question in replaceable]
            before = {
                "questions": [question_layout.snapshot(row) for row in replaceable],
                "publications": question_layout.publication_records(replaceable_ids),
                "region_reads": question_layout.region_records(replaceable_ids),
                "library_jobs": question_layout.job_records(replaceable_ids),
            }
            operation = QuestionLayoutOperation(
                paper=paper,
                client_request_id=identity,
                request_hash=request_hash,
                kind="agent_cut",
                source_ids=replaceable_ids,
                before_snapshot=before,
                page_epoch=paper.layout_page_epoch,
                source_epoch=question_layout.source_epoch(paper),
            )
            if replaceable_ids:
                question_layout._withdraw(replaceable_ids)
                question_layout._cancel_reads(replaceable_ids)
                batch = QuestionDeletionBatch.objects.create(
                    paper=paper,
                    question_ids=replaceable_ids,
                    origin=QuestionDeletionBatch.Origin.LAYOUT,
                    reason="助手批量重切前的原卷草稿历史",
                )
                operation.deletion_batch = batch
                for row in replaceable:
                    row.content_revision += 1
                    row.ocr_pending = row.reread_requested = False
                    row.ocr_suggestion = {}
                    question_layout._clear_approval(row)
                    row.deleted_at, row.deletion_batch = timezone.now(), batch
                    row.save()

            # 3. 按题组计算未指定题号时的递增顺号
            group_max_numbers: dict[int, int] = {}
            for group in groups_cache:
                existing_max = (
                    Question.objects.filter(paper=paper, group=group)
                    .order_by("-number")
                    .values_list("number", flat=True)
                    .first()
                    or 0
                )
                group_max_numbers[group.pk] = existing_max

            created_rows: list[Question] = []
            for spec in prepared_specs:
                group = spec["group"]
                if spec["number"] is not None:
                    number = spec["number"]
                    if number > group_max_numbers.get(group.pk, 0):
                        group_max_numbers[group.pk] = number
                else:
                    number = group_max_numbers.get(group.pk, 0) + 1
                    group_max_numbers[group.pk] = number

                fig_candidates = candidates_in(paper, spec["regions"])
                figure_review: dict[str, Any] = {}
                if spec["confirm_no_figure"]:
                    figure_review = {
                        "status": figure_policy.CONFIRMED_NO_FIGURE,
                        "source": "ai",
                        "agent": agent_name,
                        "reason": "助手切题时确认本题无印刷配图",
                    }
                elif spec["figures"]:
                    figure_review = {
                        "status": figure_policy.OK,
                        "source": "ai",
                        "agent": agent_name,
                        "reason": "助手切题时已直接框选配图",
                    }

                text_fields = spec["text_fields"]
                is_direct_text = text_fields is not None
                question = Question.objects.create(
                    paper=paper,
                    group=group,
                    number=number,
                    section=spec["section"],
                    question_type=spec["question_type"],
                    regions=spec["regions"],
                    regions_auto=deepcopy(spec["regions"]),
                    figures=spec["figures"],
                    figure_candidates=fig_candidates,
                    figure_review=figure_review,
                    start_source="assistant",
                    source_kind=spec["source_kind"],
                    processing_mode="manual",
                    body_mode="text" if is_direct_text else "source_image",
                    stem=text_fields["stem"] if is_direct_text else "",
                    options=text_fields["options"] if is_direct_text else {},
                    answer=text_fields["answer"] if is_direct_text else "",
                    analysis=text_fields["analysis"] if is_direct_text else "",
                    origin=spec["origin"],
                    edited=is_direct_text,
                    text_source="assistant" if is_direct_text else "",
                    type_locked=(
                        text_fields["type_locked"] if is_direct_text else qtypes.decided(spec["question_type"])
                    ),
                    state=Question.State.YELLOW,
                    approved=False,
                    color_index=question_layout.choose_color(paper.pk, spec["regions"]),
                    flags=(
                        ["由 AI 助手切题并直接录入文字，请对照原卷核对。"]
                        if is_direct_text
                        else ["原图题范围已由助手切分保存，请对照原卷核对；如需识读可提交识读队列。"]
                    ),
                )
                created_rows.append(question)

            created_ids = [row.pk for row in created_rows]
            operation.target_ids = created_ids
            question_layout._counts(paper)
            paper.refresh_from_db()
            changed_rows = [*replaceable, *created_rows]
            question_layout.prepare_fingerprints(changed_rows)
            operation.after_layout_revision = paper.layout_revision
            operation.after_snapshot = {
                "questions": [question_layout.snapshot(row) for row in changed_rows],
                "skipped_protected": skipped_protected,
                "no_change": False,
            }
            operation.after_fingerprints = {str(row.pk): question_layout.fingerprint(row) for row in changed_rows}
            operation.save()
            return operation, False, created_ids, replaceable_ids, skipped_protected
    except (OperationalError, IntegrityError) as error:
        existing = question_layout._existing(paper_id, identity, request_hash)
        if existing:
            return (
                existing,
                True,
                list(existing.target_ids or []),
                list(existing.source_ids or []),
                list((existing.after_snapshot or {}).get("skipped_protected") or []),
            )
        raise question_layout.LayoutError("另一项操作正在保存，请刷新后重试；本次未部分保存。", 409) from error
