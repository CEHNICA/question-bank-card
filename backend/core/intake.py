"""Accept and prepare original documents without calling recognition services."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, is_dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import imaging, native_pdf, segment, source_images
from .models import Block, Paper, Question, QuestionGroup, RegionRead

MODES = {"auto", "manual", "native", "mineru"}
BOOK_MANUAL_REASON = "教材或讲义中的题号可能按章节重新开始，本机暂不自动切题；已保留完整原页供手工框题，避免把不同章节的题合并。"
MULTI_PAPER_MANUAL_REASON = "这份文件的题号多次从头开始，可能包含多套试卷。已保留完整原页供手工框题，避免把不同试卷的题合并。"


def _native_segmentation_review(result: dict, blocks: list[dict]) -> tuple[list[dict], dict[int, list[str]], list[str]]:
    """Keep local boundary evidence visible without locating gaps remotely."""
    from .pipeline import merged_question_flag

    diagnostics, messages = [], []
    flags: dict[int, list[str]] = {}
    blocks_by_seq = {block.get("seq"): block for block in blocks}
    for item in result.get("questions", []):
        number = item["number"]
        notes = list(dict.fromkeys(str(note) for note in item.get("segmentation_notes") or [] if note))
        flags[number] = list(dict.fromkeys([
            *(str(flag) for flag in item.get("segmentation_flags") or [] if flag), *notes,
        ]))
        messages.extend(notes)
        if notes or flags[number] or item.get("segmentation"):
            diagnostics.append({
                "code": "question_segmentation_review", "question_number": number,
                "notes": notes, "flags": list(flags[number]),
                "metadata": deepcopy(item.get("segmentation") or {}),
            })
        for diagnostic in item.get("segmentation_diagnostics") or []:
            if isinstance(diagnostic, dict):
                diagnostics.append({**deepcopy(diagnostic), "question_number": number})

    gaps: dict[int, dict] = {}
    for number, previous in result.get("missing") or []:
        record = gaps.setdefault(previous.number, {
            "code": "missing_question_numbers", "question_number": previous.number,
            "previous_number": previous.number, "numbers": [], "page_idx": previous.page,
            "source_anchor_seq": previous.seq,
        })
        if number not in record["numbers"]:
            record["numbers"].append(number)
        warning = merged_question_flag(number)
        previous_flags = flags.setdefault(previous.number, [])
        if warning not in previous_flags:
            previous_flags.append(warning)
    for record in gaps.values():
        block = blocks_by_seq.get(record["source_anchor_seq"])
        if block and block.get("bbox"):
            record["bbox"] = list(block["bbox"])
        record["numbers"].sort()
        numbers = "、".join(str(number) for number in record["numbers"])
        record["message"] = (f"没有找到第 {numbers} 题的题号，可能和第 {record['previous_number']} 题在同一张卡里；"
                             "请对照原卷，必要时调整范围或手工补题。")
        messages.append(record["message"])
        diagnostics.append(record)

    leading = result.get("leading")
    leading = asdict(leading) if is_dataclass(leading) else leading
    if isinstance(leading, dict) and leading.get("status") in {"repaired", "suspected"}:
        # The shared segmenter also serves MinerU imports. A native intake is
        # still an original-image draft, never a promise of a subsequent read.
        message = str(leading.get("message") or "").replace("MinerU 漏读了", "本地文字层未读到")
        message = message.replace("新增题卡仍按正常流程识读。", "")
        number = 1 if leading["status"] == "repaired" else leading.get("first_detected")
        diagnostic = {**deepcopy(leading), "code": "leading_question_check",
                      "question_number": number, "message": message}
        candidate = blocks_by_seq.get(leading.get("candidate_seq"))
        if candidate:
            diagnostic.update(page_idx=candidate["page_idx"], bbox=list(candidate["bbox"]))
        diagnostics.append(diagnostic)
        if message:
            messages.append(message)
            if number is not None and message not in flags.setdefault(number, []):
                flags[number].append(message)
    return diagnostics, flags, list(dict.fromkeys(messages))


def prepare(paper: Paper, mode: str) -> Paper:
    if mode not in {"manual", "native"}:
        raise ValueError("请选择手工框题或本地文字 PDF")
    # Reuse only local rendering/conversion, never pipeline.parse.
    from .pipeline import prepare_photos, convert_docx_to_pdf
    if paper.photos and not paper.render_path:
        prepare_photos(paper)
    if paper.kind == "docx" and not paper.render_path:
        target = Path(settings.DATA_ROOT) / str(paper.pk) / "converted.pdf"
        convert_docx_to_pdf(Path(paper.source_path), target)
        paper.render_path = str(target)
    source, kind, digest = source_images.source_identity(paper)
    paper.pages = imaging.page_sizes(source, kind)
    if not paper.pages:
        raise ValueError("原文件没有有效页面")
    plan = deepcopy(paper.processing_plan or {})
    revision = int(plan.get("revision", 0)) + 1
    plan.update({"schema": 1, "revision": revision, "mode": mode, "render_sha256": digest})
    if mode == "native" and kind == "pdf":
        extracted = native_pdf.extract(source)
        plan["pages"] = extracted["pages"]
    else:
        extracted = {"blocks": []}
        plan["pages"] = [{"page_idx": p["page_idx"], "mode": "manual", "warnings": []} for p in paper.pages]
        if mode == "native":
            plan["warnings"] = ["这份资料没有 PDF 文字层，已保留原页供手工框题。"]
    if mode == "native" and paper.material_type == Paper.MaterialType.BOOK:
        # The exam segmenter assumes monotonically increasing question numbers.
        # A book may restart them in each chapter (or example/exercise scope),
        # so keep its local text as evidence but do not build merged exam cards.
        plan.update(mode="manual", native_book_fallback=True, fallback_reason=BOOK_MANUAL_REASON)
        plan.setdefault("warnings", []).append(BOOK_MANUAL_REASON)
        plan["pages"] = [{**page, "mode": "manual"} for page in plan["pages"]]
    elif mode == "native" and extracted["blocks"]:
        usable = {page["page_idx"] for page in plan["pages"] if page["mode"] == "native"}
        scopes = segment.numbering_scopes(paper.pages, [
            block for block in extracted["blocks"] if block["page_idx"] in usable])
        if len(scopes) > 1:
            # Reuse the existing restart detector before the single-exam
            # segmenter can absorb a later paper into the previous one's tail.
            plan.update(mode="manual", native_numbering_fallback=True,
                local_scope_count=len(scopes), fallback_reason=MULTI_PAPER_MANUAL_REASON)
            plan.setdefault("warnings", []).append(MULTI_PAPER_MANUAL_REASON)
            plan["pages"] = [{**page, "mode": "manual"} for page in plan["pages"]]
    with transaction.atomic():
        locked = Paper.objects.select_for_update().get(pk=paper.pk)
        # The intake path is for a new file. Re-preparing an existing task
        # must use select_manual below, never discard blocks or manual cards.
        if locked.questions.exists() or locked.blocks.exists():
            raise ValueError("已有题卡或解析结果，请选择转手工；现有成果不会重新覆盖。")
        paper.processing_plan = plan
        paper.status, paper.error = Paper.Status.READY, ""
        paper.save(update_fields=["processing_plan", "render_path", "pages", "status", "error", "updated_at"])
        group = QuestionGroup.objects.create(paper=paper, title=paper.display_name,
                    kind=QuestionGroup.Kind.EXAM if paper.material_type == "exam" else QuestionGroup.Kind.OTHER,
                    sequence=0, page_start=1, page_end=len(paper.pages),
                    metadata={"pages": list(range(len(paper.pages))), "source": "manual"})
        if extracted["blocks"]:
            Block.objects.bulk_create([Block(paper=paper, **block) for block in extracted["blocks"]], batch_size=300)
        if extracted["blocks"] and plan["mode"] == "native":
            # Pure segmentation: gap location and cloud reading are deliberately
            # not called. A scan page remains available for manual completion.
            usable = {p["page_idx"] for p in plan["pages"] if p["mode"] == "native"}
            result = segment.segment(paper.pages, [b for b in extracted["blocks"] if b["page_idx"] in usable])
            items = result.get("questions", [])
            diagnostics, segmentation_flags, messages = _native_segmentation_review(result, extracted["blocks"])
            plan["warnings"] = list(dict.fromkeys([*(plan.get("warnings") or []), *messages]))
            created_numbers = set()
            for index, item in enumerate(items):
                if Question.all_objects.filter(paper=paper, group=group, number=item["number"]).exists():
                    continue
                regions = item["regions"]
                start_page = min(region["page_idx"] for region in regions)
                next_page = (min(region["page_idx"] for region in items[index + 1]["regions"])
                             if index + 1 < len(items) else len(paper.pages))
                if any(page not in usable for page in range(start_page, next_page)):
                    # The layout builder omits unreadable slots, so merely
                    # checking its output regions cannot reveal a skipped scan
                    # continuation. A question before that boundary is manual.
                    warning = f"第 {item['number']} 题后方有无法读取的页面，未自动截成不完整题卡；请手工框出全部续页。"
                    plan["warnings"].append(warning)
                    diagnostics.append({"code": "unreadable_continuation", "question_number": item["number"],
                                        "page_indices": [page for page in range(start_page, next_page) if page not in usable],
                                        "message": warning})
                    continue
                # Do not extend a partial text-layer question onto a scan page.
                if not all(r["page_idx"] in usable for r in regions):
                    warning = f"第 {item['number']} 题的范围包含无法读取的页面，未自动截成不完整题卡；请手工框出全部续页。"
                    plan["warnings"].append(warning)
                    diagnostics.append({"code": "unreadable_continuation", "question_number": item["number"],
                                        "page_indices": sorted({r["page_idx"] for r in regions if r["page_idx"] not in usable}),
                                        "message": warning})
                    continue
                draft = segment._text_in_regions(extracted["blocks"], regions)
                Question.objects.create(paper=paper, group=group, number=item["number"],
                    section=item.get("section", ""), question_type=item.get("question_type", "unknown"),
                    regions=regions, regions_auto=regions, start_source="native", body_mode="source_image",
                    processing_mode="manual", stem=draft, text_source="native", state=Question.State.YELLOW,
                    flags=["本地文字层切题初稿，请对照原卷确认范围；正文保留原图。",
                           *segmentation_flags.get(item["number"], [])],
                    source_anchor_seq=item.get("source_anchor_seq"))
                created_numbers.add(item["number"])
            for diagnostic in diagnostics:
                diagnostic["question_created"] = diagnostic.get("question_number") in created_numbers
            plan["segmentation_diagnostics"] = diagnostics
            plan["warnings"] = list(dict.fromkeys(plan["warnings"]))
            paper.total = paper.questions.count()
            paper.progress = paper.total
            paper.processing_plan = plan
            paper.save(update_fields=["total", "progress", "processing_plan", "updated_at"])
    return paper


def prepare_auto(paper: Paper, *, allow_cloud: bool = False, cloud_ready: bool = False) -> Paper:
    """Use real local text first; cloud use requires this import's explicit consent.

    A paper whose local attempt produced no card must not arrive at the review
    page looking like a finished one, so it is failed here with the reason the
    teacher needs.  How many questions the sheet *should* have had is not
    guessed: a 答案/解析 section prints the same numbers as the questions, so
    that comparison named missing questions that were never missing.
    """
    from . import pipeline
    try:
        prepare(paper, "native")
    except Exception:
        # A damaged text layer or failed segmentation must not make a valid
        # locally viewable original dependent on cloud service recovery.
        paper.refresh_from_db()
        if paper.pages:
            paper = select_manual(paper)
        else:
            prepare(paper, "manual")
    with transaction.atomic():
        paper = Paper.objects.select_for_update().get(pk=paper.pk)
        plan = deepcopy(paper.processing_plan or {})
        plan.update(requested_mode="auto", cloud_authorized=bool(allow_cloud), auto_fallback=True)
        if paper.questions.exists():
            plan["fallback_reason"] = "已从本地文字层准备题卡，请对照原卷核对；未切出的页面可手工补充。"
        elif allow_cloud and cloud_ready:
            plan.update(mode="mineru", revision=int(plan.get("revision", 0)) + 1)
            plan["fallback_reason"] = "本地文字层没有可靠题卡，按本次授权尝试已配置的 MinerU。"
            paper.status = Paper.Status.QUEUED
        else:
            plan["mode"] = "manual"
            if plan.get("native_book_fallback"):
                plan["fallback_reason"] = BOOK_MANUAL_REASON
            elif plan.get("native_numbering_fallback"):
                plan["fallback_reason"] = MULTI_PAPER_MANUAL_REASON
            else:
                # 1.12.7：照片本来就没有文字层，说「本地文字层没有可靠题卡」像是在说
                # 老师的材料坏了。材料性质（没文字层）和能力问题（本机切不出题）分开说。
                if paper.kind == "image":
                    plan["fallback_reason"] = ("这份资料是照片，本机没有可用的文字层来切题，已保留原页供手工切题。"
                        if not allow_cloud else "这份资料是照片，本机切不出题；云端切题未就绪，已保留原页供手工切题。")
                else:
                    plan["fallback_reason"] = ("本地文字层没有可靠题卡，已保留原页供手工切题。"
                        if not allow_cloud else "当前自动解析服务未就绪，已保留原页供手工切题。")
            plan["pages"] = [{**page, "mode": "manual"} for page in plan.get("pages", [])]
        paper.processing_plan = plan
        paper.save(update_fields=["processing_plan", "status", "updated_at"])
    if plan.get("mode") != "mineru":
        # Nothing will be queued for this run, so the worker never comes back to
        # check it.  The reason the local attempt gave up is the sentence the
        # teacher actually needs, so it is not overwritten by the generic one.
        pipeline._fail_when_nothing_was_cut(
            paper, int(plan.get("revision", 0)),
            reason=f"自动切题没有切出任何题目：{plan.get('fallback_reason', '')}".strip(),
        )
        paper.refresh_from_db()
    return paper


def fallback_manual(paper: Paper, revision: int, reason: str) -> bool:
    """Recover one failed automatic run without replacing any successful draft."""
    with transaction.atomic():
        current = Paper.objects.select_for_update().filter(pk=paper.pk).first()
        if current is None or int((current.processing_plan or {}).get("revision", 0)) != revision:
            return False
        if not current.pages:
            prepare(current, "manual")
            current.refresh_from_db()
        else:
            current = select_manual(current)
        plan = deepcopy(current.processing_plan or {})
        plan.update(mode="manual", requested_mode="auto", fallback_reason=reason[:500])
        current.processing_plan = plan
        current.save(update_fields=["processing_plan", "updated_at"])
        paper.refresh_from_db()
        return True


def _select_manual_locked(paper: Paper, pages: list[int], *, whole: bool) -> Paper:
    """Invalidate an old generation under its paper lock; keep every result."""
    plan = deepcopy(paper.processing_plan or {})
    plan.update({"schema": 1, "revision": int(plan.get("revision", 0)) + 1,
                 "mode": "manual" if whole else plan.get("mode", "mineru")})
    recorded = {item["page_idx"]: item for item in plan.get("pages", [])}
    for page in pages:
        recorded[page] = {**recorded.get(page, {}), "page_idx": page, "mode": "manual"}
    plan["pages"] = [recorded[p] for p in sorted(recorded)]
    paper.processing_plan = plan
    # The new revision is authoritative even if a cloud call cannot be killed.
    if whole or paper.status in {Paper.Status.FAILED, Paper.Status.NEEDS_GROUPING}:
        paper.status, paper.error = Paper.Status.READY, ""
    paper.save(update_fields=["processing_plan", "status", "error", "updated_at"])
    for question in paper.questions.select_for_update():
        if whole or any(r["page_idx"] in pages for r in question.regions):
            was_queued = question.ocr_pending or question.reread_requested
            question.processing_mode = "manual"
            question.content_revision += 1
            question.reread_requested = False
            question.ocr_pending = False
            if question.state in {Question.State.WAITING, Question.State.READING, Question.State.RED}:
                question.state = Question.State.YELLOW
                if not question.stem.strip():
                    question.body_mode = "source_image"
            # The card is no longer waiting for a read, so a last reading failure
            # would keep a “无法读题” sentence on a card that now only needs a
            # human to check the original image.  The record itself stays in
            # ocr_suggestion and in 识读记录.
            if was_queued:
                question.error = ""
            question.save(update_fields=["processing_mode", "content_revision", "reread_requested", "ocr_pending", "state", "body_mode", "error", "updated_at"])
    _ensure_manual_group(paper)
    return paper


def _ensure_manual_group(paper: Paper) -> None:
    if paper.pages and not paper.question_groups.exists():
        QuestionGroup.objects.create(paper=paper, title=paper.display_name, sequence=0,
            page_start=1, page_end=len(paper.pages), metadata={"pages": list(range(len(paper.pages))), "source": "manual"})


def select_manual(paper: Paper, selected_pages: list[int] | None = None) -> Paper:
    """Stop automatic work for a page or task while retaining every draft."""
    with transaction.atomic():
        paper = Paper.objects.select_for_update().get(pk=paper.pk)
        pages = list(range(len(paper.pages))) if selected_pages is None else selected_pages
        if not pages or any(type(p) is not int or not 0 <= p < len(paper.pages) for p in pages):
            raise ValueError("页面范围不正确")
        return _select_manual_locked(paper, pages, whole=selected_pages is None)


class ManualSwitchConflict(ValueError):
    """No switch was authorized for the current task generation."""


class ManualPreparationError(RuntimeError):
    """The old cloud task is stopped, but local original pages need repair."""


def _prepare_manual_pages(paper: Paper, revision: int) -> Paper:
    """Prepare missing original pages without replacing old blocks or cards."""
    from .pipeline import prepare_photos, convert_docx_to_pdf, _set_if_plan_current
    from . import mineru

    if paper.photos and not paper.render_path:
        target = Path(settings.DATA_ROOT) / str(paper.pk) / f"pages-manual-{revision}.pdf"
        prepare_photos(paper, revision=revision, render_target=target)
    if paper.kind == "docx" and not paper.render_path:
        # A previous parser may still be converting to converted.pdf. Its
        # local file write must not replace the new manual rendering either.
        target = Path(settings.DATA_ROOT) / str(paper.pk) / f"converted-manual-{revision}.pdf"
        convert_docx_to_pdf(Path(paper.source_path), target)
        if not _set_if_plan_current(paper, revision, render_path=str(target)):
            raise mineru.MineruCancelled()
    source, kind, digest = source_images.source_identity(paper)
    pages = imaging.page_sizes(source, kind)
    if not pages:
        raise ValueError("原文件没有有效页面")
    with transaction.atomic():
        current = Paper.objects.select_for_update().get(pk=paper.pk)
        plan = deepcopy(current.processing_plan or {})
        if plan.get("mode") != "manual" or int(plan.get("revision", 0)) != revision:
            raise ManualSwitchConflict("原卷处理方式已发生变化，请刷新后再继续")
        plan.update(render_sha256=digest,
                    pages=[{"page_idx": page["page_idx"], "mode": "manual", "warnings": []} for page in pages])
        current.pages = pages
        current.processing_plan = plan
        current.status, current.error = Paper.Status.READY, ""
        current.save(update_fields=["pages", "processing_plan", "status", "error", "updated_at"])
        _ensure_manual_group(current)
    return current


def switch_to_manual(paper: Paper, *, expected_revision: int | None = None) -> tuple[Paper, bool]:
    """Stop immediately, then make originals usable for cutting; never read."""
    from . import mineru
    from .pipeline import paper_dir

    with transaction.atomic():
        current = Paper.objects.select_for_update().get(pk=paper.pk)
        if current.archived:
            raise ManualSwitchConflict("请先恢复已归档的试卷，再继续手工切题")
        plan = current.processing_plan or {}
        if plan.get("mode") == "manual" and current.status == Paper.Status.READY and current.pages:
            # A repeated click must not cancel a later manual card reread.
            return current, False
        revision = int(plan.get("revision", 0))
        if expected_revision is not None and expected_revision != revision:
            raise ManualSwitchConflict("原卷处理方式已发生变化，请刷新后再继续")
        current = _select_manual_locked(current, list(range(len(current.pages))), whole=True)
        revision = int(current.processing_plan["revision"])
        RegionRead.objects.filter(question__paper=current,
            status__in=[RegionRead.Status.QUEUED, RegionRead.Status.RUNNING]).update(
                status=RegionRead.Status.FAILED, error="已转为手工切题，原框选识读已停止。", updated_at=timezone.now())
    # Commit cancellation before any rendering. No worker or remote reply is
    # required; callbacks and result writes reject the obsolete revision.
    try:
        (paper_dir(current) / mineru.CANCEL_FILE).write_text("manual", encoding="utf-8")
    except OSError:
        pass
    if current.pages:
        return current, True
    try:
        return _prepare_manual_pages(current, revision), True
    except ManualSwitchConflict:
        raise
    except Exception as exc:
        with transaction.atomic():
            latest = Paper.objects.select_for_update().get(pk=current.pk)
            if int((latest.processing_plan or {}).get("revision", 0)) == revision:
                latest.status = Paper.Status.FAILED
                latest.error = "已停止 MinerU，但原文件暂时无法准备；请检查原件后继续手工切题。"
                latest.save(update_fields=["status", "error", "updated_at"])
        raise ManualPreparationError("已停止 MinerU，但原文件暂时无法准备；请检查原件后继续手工切题。") from exc
