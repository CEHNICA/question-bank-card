"""Atomic original-page edits, durable receipts and conservative undo.

This service never recognizes, publishes, deletes files or restores approval.
The first write is a paper-token CAS; on SQLite this acquires the writer before
reading source rows. Every legacy topology write also advances that token.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone

from .models import (LibraryJob, Paper, PublishedQuestion, Question,
                     QuestionDeletionBatch, QuestionGroup, QuestionLayoutOperation, RegionRead)

PALETTE_SIZE = 6
ACTIVE_JOBS = (LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING)
ACTIVE_READS = (RegionRead.Status.QUEUED, RegionRead.Status.RUNNING)
NO_CHANGE = "范围未变化，无需撤销。"
CANCELLED = "原卷范围或题目结构已改变，本轮结果不再采用。"


class LayoutError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def canonical(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False))


def digest(value):
    data = json.dumps(value, cls=DjangoJSONEncoder, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def bump(paper_id):
    Paper.objects.filter(pk=paper_id).update(layout_revision=F("layout_revision") + 1)


def source_epoch(paper):
    source = Path(paper.render_path or paper.source_path)
    try:
        stat = source.stat()
        stamp = [stat.st_size, stat.st_mtime_ns] if source.is_file() else None
    except OSError:
        stamp = None
    return digest([paper.layout_page_epoch, paper.pages, paper.sha256, str(source), stamp])


def snapshot(question):
    return canonical({field.attname: getattr(question, field.attname)
                      for field in Question._meta.concrete_fields
                      if field.name not in {"created_at", "updated_at"}})


def publication_records(question_ids):
    return canonical(list(PublishedQuestion.objects.filter(question_id__in=question_ids).order_by("id")
        .values("id", "question_id", "version", "status", "content_hash", "withdrawn_at", "extras")))


def job_records(question_ids):
    # No configuration or credentials are copied into layout history.
    return canonical(list(LibraryJob.objects.filter(publication__question_id__in=question_ids).order_by("id")
        .values("id", "publication_id", "kind", "status", "executor", "solution_scope", "result", "error", "updated_at")))


def region_records(question_ids):
    return canonical(list(RegionRead.objects.filter(question_id__in=question_ids).order_by("id")
        .values("id", "question_id", "page_idx", "bbox", "target", "status", "text", "error", "engine",
                "recommendation", "updated_at")))


def fingerprint(question):
    # Unlike content_revision, this also detects approval, failed OCR and
    # region-read changes that do not increment the content counter.
    dependencies = getattr(question, "_layout_dependencies", None)
    if dependencies is None:
        dependencies = {"publications": publication_records([question.pk]),
                        "jobs": job_records([question.pk]), "region_reads": region_records([question.pk])}
    return digest({"question": snapshot(question), **dependencies,
                   "source_epoch": source_epoch(question.paper)})


def prepare_fingerprints(questions):
    """Request-local bulk dependencies; never cache a mutable card's digest.

    Paper responses often contain hundreds of cards. The same exact evidence
    must be read in three bulk queries rather than three queries per card.
    These instances are only used to serialize that response, not to mutate.
    """
    rows = {row.pk: row for row in questions}
    if not rows:
        return
    dependencies = {key: {"publications": [], "jobs": [], "region_reads": []} for key in rows}
    for record in publication_records(rows):
        dependencies[record["question_id"]]["publications"].append(record)
    for record in region_records(rows):
        dependencies[record["question_id"]]["region_reads"].append(record)
    jobs = LibraryJob.objects.filter(publication__question_id__in=rows).order_by("id").values(
        "publication__question_id", "id", "publication_id", "kind", "status", "executor",
        "solution_scope", "result", "error", "updated_at")
    for record in jobs:
        question_id = record.pop("publication__question_id")
        dependencies[question_id]["jobs"].append(canonical(record))
    for question_id, row in rows.items():
        row._layout_dependencies = dependencies[question_id]


def _distance(regions, other):
    result = math.inf
    for first in regions or []:
        for second in other or []:
            if first.get("page_idx") != second.get("page_idx"):
                continue
            a, b = first.get("bbox"), second.get("bbox")
            if not isinstance(a, list) or not isinstance(b, list) or len(a) != 4 or len(b) != 4:
                continue
            dx = max(a[0] - b[2], b[0] - a[2], 0)
            dy = max(a[1] - b[3], b[1] - a[3], 0)
            result = min(result, dx * dx + dy * dy)
    return result


def choose_color(paper_id, regions, exclude=()):
    rows = list(Question.objects.filter(paper_id=paper_id, color_index__isnull=False)
                .exclude(pk__in=exclude).values("id", "regions", "color_index"))
    counts = Counter(row["color_index"] for row in rows)
    neighbours = sorted(rows, key=lambda row: (_distance(regions, row["regions"]), row["id"]))
    avoid = {row["color_index"] for row in neighbours[:3] if math.isfinite(_distance(regions, row["regions"]))}
    return min(range(PALETTE_SIZE), key=lambda value: (value in avoid, counts[value], value))


def ordinary_deleted(paper):
    return Question.all_objects.filter(paper=paper, deleted_at__isnull=False).exclude(
        deletion_batch__origin=QuestionDeletionBatch.Origin.LAYOUT)


def _regions(paper, value):
    if not isinstance(value, list) or not 1 <= len(value) <= 12:
        raise LayoutError("每道题需要 1–12 个原卷片段。")
    pages = {page["page_idx"] for page in paper.pages}
    result = []
    for item in value:
        if not isinstance(item, dict) or type(item.get("page_idx")) is not int or item["page_idx"] not in pages:
            raise LayoutError("原卷页码不正确。")
        box = item.get("bbox")
        if (not isinstance(box, list) or len(box) != 4 or any(type(v) not in (int, float)
                or not math.isfinite(v) or not 0 <= v <= 1000 for v in box)):
            raise LayoutError("范围坐标需要在 0–1000 内。")
        box = [round(float(v), 1) for v in box]
        if box[2] - box[0] < 3 or box[3] - box[1] < 3:
            raise LayoutError("范围太小或方向不正确。")
        region = {"page_idx": item["page_idx"], "bbox": box}
        if region in result:
            raise LayoutError("同一道题不能重复保存完全相同的片段。")
        result.append(region)
    return result


def _group(paper, target, regions, default=None):
    groups = list(QuestionGroup.objects.filter(paper=paper).order_by("sequence", "id"))
    requested = target.get("group_id", default)
    region_pages = {region["page_idx"] for region in regions}
    def covers(group):
        pages = (group.metadata or {}).get("pages")
        if not isinstance(pages, list):
            pages = list(range((group.page_start or 1) - 1, group.page_end or len(paper.pages)))
        return region_pages.issubset({page for page in pages if type(page) is int})
    if requested is not None:
        if type(requested) is not int:
            raise LayoutError("题组编号格式不正确。")
        group = next((group for group in groups if group.pk == requested), None)
        if group is None or not covers(group):
            raise LayoutError("所选范围不属于当前题组。")
        return group
    matches = [group for group in groups if covers(group)]
    if groups and len(matches) != 1:
        raise LayoutError("请明确选择题组，且范围不能跨越题组。")
    return matches[0] if matches else None


def _clear_approval(question):
    question.approved = False
    question.approved_at = None
    question.approved_content_hash = ""
    question.approval_source = question.approval_agent = ""


def _cancel_reads(question_ids):
    RegionRead.objects.filter(question_id__in=question_ids, status__in=ACTIVE_READS).update(
        status=RegionRead.Status.FAILED, error=CANCELLED, updated_at=timezone.now())


def _withdraw(question_ids):
    from . import library
    from .library_job_control import CANCEL_MESSAGE
    publications = list(PublishedQuestion.objects.select_for_update()
                        .filter(question_id__in=question_ids).order_by("id"))
    for publication in publications:
        library.withdraw(publication)
    LibraryJob.objects.filter(publication_id__in=[publication.pk for publication in publications],
                             status__in=ACTIVE_JOBS).update(
        status=LibraryJob.Status.FAILED, error=CANCEL_MESSAGE, updated_at=timezone.now())


def _latest(paper):
    return paper.layout_operations.exclude(blocked_reason=NO_CHANGE).order_by("-created_at", "-id").first()


def undo_reason(operation, paper=None):
    paper = paper or Paper.objects.get(pk=operation.paper_id)
    if operation.undone_at:
        return "这次操作已经撤销。"
    if operation.blocked_reason:
        return operation.blocked_reason
    if paper.archived or paper.status != Paper.Status.READY:
        return "请先恢复资料并完成原卷处理，再撤销。"
    if operation.page_epoch != paper.layout_page_epoch or operation.source_epoch != source_epoch(paper):
        return "原卷或页序已经变化，旧布局不能覆盖当前原卷。"
    latest = _latest(paper)
    if latest is None or latest.pk != operation.pk or operation.after_layout_revision != paper.layout_revision:
        return "之后已有布局操作，无法覆盖当前布局。"
    rows = {row.pk: row for row in Question.all_objects.filter(pk__in=[int(key) for key in operation.after_fingerprints])
            .select_related("paper")}
    if len(rows) != len(operation.after_fingerprints):
        return "相关题卡已不存在，未撤销。"
    if any(fingerprint(rows[int(key)]) != value for key, value in operation.after_fingerprints.items()):
        return "题目已编辑、审核、入库或识读结果已变化，未覆盖当前内容。"
    return ""


def operation_json(operation, paper=None, *, snapshots=True):
    reason = undo_reason(operation, paper)
    result = {"id": str(operation.pk), "kind": operation.kind,
              "client_request_id": str(operation.client_request_id), "source_ids": operation.source_ids,
              "target_ids": operation.target_ids, "created_at": operation.created_at.isoformat(),
              "undone_at": operation.undone_at.isoformat() if operation.undone_at else None,
              "can_undo": not reason, "blocked_reason": reason,
              "layout_revision": operation.after_layout_revision}
    if snapshots:
        result.update(before_snapshot=operation.before_snapshot, after_snapshot=operation.after_snapshot)
    return result


def history(paper, request_id=None):
    if request_id is not None:
        try:
            identity = uuid.UUID(request_id)
        except (ValueError, TypeError, AttributeError):
            raise LayoutError("请求编号需要 UUID。") from None
        return list(paper.layout_operations.filter(client_request_id=identity))
    return list(paper.layout_operations.order_by("-created_at", "-id")[:20])


def _revision(payload):
    revision = payload.get("layout_revision")
    if type(revision) is not int or revision < 0:
        raise LayoutError("请提供当前原卷布局版本。")
    return revision


def _request(payload, request_payload=None):
    if not isinstance(payload, dict):
        raise LayoutError("请求内容不正确。")
    try:
        identity = uuid.UUID(payload.get("client_request_id"))
    except (ValueError, TypeError, AttributeError):
        raise LayoutError("请求编号需要 UUID。") from None
    try:
        request_hash = digest(payload if request_payload is None else request_payload)
    except (ValueError, TypeError):
        raise LayoutError("请求包含无效数字或内容。") from None
    return identity, request_hash


def _existing(paper_id, identity, request_hash):
    operation = QuestionLayoutOperation.objects.filter(paper_id=paper_id, client_request_id=identity).first()
    if operation and operation.request_hash != request_hash:
        raise LayoutError("同一请求编号不能用于不同操作。", 409)
    return operation


def _claim(paper_id, revision):
    # A same-value update is intentional: acquire SQLite's writer before any
    # source-row read, without advancing the token for an unchanged receipt.
    changed = Paper.objects.filter(pk=paper_id, layout_revision=revision).update(layout_revision=F("layout_revision"))
    if not changed:
        if not Paper.objects.filter(pk=paper_id).exists():
            raise LayoutError("资料不存在。", 404)
        raise LayoutError("原卷布局已在其他窗口变化，请刷新后重试；本次未覆盖。", 409)
    paper = Paper.objects.select_for_update().get(pk=paper_id)
    if paper.archived or paper.status != Paper.Status.READY:
        raise LayoutError("请先恢复资料并完成原卷处理，再修改题目范围。", 409)
    return paper


def _sources(paper, payload, kind):
    entries = payload.get("sources")
    limits = {"regions": (1, 1), "add": (0, 0), "split": (1, 1), "merge": (2, 12)}
    low, high = limits[kind]
    if not isinstance(entries, list) or not low <= len(entries) <= high:
        raise LayoutError("所选源题数量不正确。")
    ids = []
    for entry in entries:
        if (not isinstance(entry, dict) or type(entry.get("id")) is not int
                or type(entry.get("revision")) is not int or not isinstance(entry.get("fingerprint"), str)):
            raise LayoutError("请提供源题编号、内容版本和指纹。")
        ids.append(entry["id"])
    if len(set(ids)) != len(ids):
        raise LayoutError("源题不能重复。")
    rows = {row.pk: row for row in Question.objects.select_for_update().filter(paper=paper, pk__in=ids)
            .select_related("paper", "group").order_by("id")}
    if len(rows) != len(ids):
        raise LayoutError("所选源题已变化、已删除或不属于当前资料。", 409)
    for entry in entries:
        row = rows[entry["id"]]
        if row.content_revision != entry["revision"] or fingerprint(row) != entry["fingerprint"]:
            raise LayoutError("源题已编辑、审核或识读结果已变化；本次未覆盖。", 409)
        if kind in {"split", "merge"} and (row.state in {Question.State.WAITING, Question.State.READING}
                or row.ocr_pending or row.reread_requested):
            raise LayoutError("所选题目仍在识读，请先停止或完成识读。", 409)
    result = [rows[identity] for identity in ids]
    if kind == "merge" and len({row.group_id for row in result}) != 1:
        raise LayoutError("只能合并同一题组的题目。", 409)
    return result


def _targets(paper, payload, kind, sources):
    from . import qtypes
    entries = payload.get("targets")
    low, high = (2, 12) if kind == "split" else (1, 1)
    if not isinstance(entries, list) or not low <= len(entries) <= high:
        raise LayoutError("结果题目数量不正确。")
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise LayoutError("结果题目格式不正确。")
        regions = _regions(paper, entry.get("regions"))
        if kind == "regions":
            result.append({"regions": regions})
            continue
        number, question_type = entry.get("number"), entry.get("question_type", "unknown")
        if type(number) is not int or not 1 <= number <= 999 or question_type not in qtypes.TYPES:
            raise LayoutError("请提供题号（1–999）和有效题型。")
        default = sources[0].group_id if sources else None
        group = _group(paper, entry, regions, default)
        if sources and (group.pk if group else None) != default:
            raise LayoutError("拆合结果必须保留原题组。", 409)
        result.append({"number": number, "group": group, "question_type": question_type, "regions": regions})
    if kind != "regions":
        keys = [(item["group"].pk if item["group"] else None, item["number"]) for item in result]
        if len(set(keys)) != len(keys):
            raise LayoutError("结果题号不能重复。", 409)
        occupied = Question.all_objects.filter(paper=paper).exclude(pk__in=[row.pk for row in sources])
        occupied = occupied.exclude(deleted_at__isnull=False, deletion_batch__origin=QuestionDeletionBatch.Origin.LAYOUT)
        for group, number in keys:
            if occupied.filter(group_id=group, number=number).exists():
                raise LayoutError("结果题号已被当前题卡或普通回收站题目占用。", 409)
    return result


def _counts(paper):
    active = paper.questions.all()
    Paper.objects.filter(pk=paper.pk).update(total=active.count(), progress=active.exclude(
        state__in=(Question.State.WAITING, Question.State.READING)).count(), updated_at=timezone.now())


def mutate(paper_id, payload, *, request_payload=None):
    # Legacy routes hydrate optional tokens from the current DB. Hash their
    # original request instead so the same UUID remains a replay after success.
    # This argument is internal; HTTP callers cannot choose their own hash.
    identity, request_hash = _request(payload, request_payload)
    existing = _existing(paper_id, identity, request_hash)
    if existing:
        return existing, True
    revision = _revision(payload)
    kind = payload.get("kind")
    if kind not in {"regions", "add", "split", "merge"}:
        raise LayoutError("请选择保存范围、补题、拆题或合题。")
    try:
        with transaction.atomic():
            paper = _claim(paper_id, revision)
            existing = _existing(paper_id, identity, request_hash)
            if existing:
                return existing, True
            sources = _sources(paper, payload, kind)
            targets = _targets(paper, payload, kind, sources)
            ids = [row.pk for row in sources]
            before = {"questions": [snapshot(row) for row in sources], "publications": publication_records(ids),
                      "region_reads": region_records(ids), "library_jobs": job_records(ids)}
            operation = QuestionLayoutOperation(paper=paper, client_request_id=identity, request_hash=request_hash,
                kind=kind, source_ids=ids, before_snapshot=before, page_epoch=paper.layout_page_epoch,
                source_epoch=source_epoch(paper))
            changed_rows = list(sources)
            if kind == "regions":
                question = sources[0]
                if question.regions == targets[0]["regions"]:
                    operation.blocked_reason = NO_CHANGE
                else:
                    from .pipeline import candidates_in
                    question.regions = targets[0]["regions"]
                    question.figure_candidates = candidates_in(paper, question.regions)
                    question.content_revision += 1
                    question.ocr_pending = question.reread_requested = False
                    question.ocr_suggestion = {}
                    question.state, question.error = Question.State.YELLOW, ""
                    question.flags = list(dict.fromkeys([*(question.flags or []), "原卷范围已修改，请重新对照确认。"] ))
                    _clear_approval(question)
                    _cancel_reads(ids)
                    question.save()
                operation.target_ids = ids
            else:
                if ids:
                    _withdraw(ids)
                    _cancel_reads(ids)
                    batch = QuestionDeletionBatch.objects.create(paper=paper, question_ids=ids,
                        origin=QuestionDeletionBatch.Origin.LAYOUT, reason="原卷拆合操作的完整源题历史")
                    operation.deletion_batch = batch
                    for question in sources:
                        question.content_revision += 1
                        question.ocr_pending = question.reread_requested = False
                        question.ocr_suggestion = {}
                        _clear_approval(question)
                        question.deleted_at, question.deletion_batch = timezone.now(), batch
                        question.save()
                from .pipeline import candidates_in
                from . import qtypes
                new = []
                for target in targets:
                    question = Question.objects.create(paper=paper, **target,
                        regions_auto=deepcopy(target["regions"]), start_source="manual",
                        source_kind=Question.SourceKind.MANUAL, processing_mode="manual", body_mode="source_image",
                        figure_candidates=candidates_in(paper, target["regions"]),
                        type_locked=qtypes.decided(target["question_type"]), state=Question.State.YELLOW,
                        flags=["原图题范围已保存，请对照原卷核对；识读需主动提交。"])
                    new.append(question)
                operation.target_ids = [row.pk for row in new]
                changed_rows += new
            _counts(paper)
            paper.refresh_from_db()
            operation.after_layout_revision = paper.layout_revision
            operation.after_snapshot = {"questions": [snapshot(row) for row in changed_rows],
                                        "no_change": operation.blocked_reason == NO_CHANGE}
            operation.after_fingerprints = {str(row.pk): fingerprint(row) for row in changed_rows}
            operation.save()
            return operation, False
    except (OperationalError, IntegrityError) as error:
        existing = _existing(paper_id, identity, request_hash)
        if existing:
            return existing, True
        raise LayoutError("另一项操作正在保存，请刷新后重试；本次未部分保存。", 409) from error


def undo(paper_id, operation_id, payload):
    if not isinstance(payload, dict):
        raise LayoutError("请求内容不正确。")
    operation = QuestionLayoutOperation.objects.filter(pk=operation_id, paper_id=paper_id).first()
    if operation is None:
        raise LayoutError("布局操作不存在。", 404)
    if operation.undone_at:
        return operation, True
    revision = _revision(payload)
    try:
        with transaction.atomic():
            paper = _claim(paper_id, revision)
            operation = QuestionLayoutOperation.objects.select_for_update().get(pk=operation_id, paper=paper)
            if operation.undone_at:
                return operation, True
            rows = list(Question.all_objects.select_for_update().filter(
                pk__in=[int(key) for key in operation.after_fingerprints]).select_related("paper").order_by("id"))
            reason = undo_reason(operation, paper)
            if reason:
                raise LayoutError(reason, 409)
            before = {item["id"]: item for item in operation.before_snapshot.get("questions", [])}
            _cancel_reads([row.pk for row in rows])
            batch = None
            if operation.kind != "regions":
                batch = QuestionDeletionBatch.objects.create(paper=paper, question_ids=operation.target_ids,
                    origin=QuestionDeletionBatch.Origin.LAYOUT, reason="已撤销补题或拆合后的结果历史")
            excluded = {"id", "paper_id", "source_key", "content_revision", "approved", "approved_at",
                        "approved_content_hash", "approval_source", "approval_agent", "deleted_at", "deletion_batch_id",
                        "ocr_pending", "reread_requested", "ocr_suggestion"}
            for question in rows:
                if question.pk in before:
                    for key, value in before[question.pk].items():
                        if key not in excluded:
                            setattr(question, key, deepcopy(value))
                    question.deleted_at = question.deletion_batch_id = None
                    question.state = Question.State.YELLOW
                    question.flags = list(dict.fromkeys([*(question.flags or []), "已撤销原卷布局操作，请重新对照核对。"] ))
                    question.error = ""
                else:
                    question.deleted_at, question.deletion_batch = timezone.now(), batch
                question.content_revision += 1
                question.ocr_pending = question.reread_requested = False
                question.ocr_suggestion = {}
                _clear_approval(question)
                question.save()
            if operation.deletion_batch_id:
                QuestionDeletionBatch.objects.filter(pk=operation.deletion_batch_id).update(restored_at=timezone.now())
            operation.undone_at = timezone.now()
            operation.save(update_fields=["undone_at"])
            _counts(paper)
            return operation, False
    except (OperationalError, IntegrityError) as error:
        operation.refresh_from_db()
        if operation.undone_at:
            return operation, True
        raise LayoutError("另一项操作正在保存，请刷新后重试；本次未覆盖当前内容。", 409) from error


def invalidate_page_epoch(paper):
    Paper.objects.filter(pk=paper.pk).update(layout_page_epoch=F("layout_page_epoch") + 1,
                                           layout_revision=F("layout_revision") + 1)
    paper.layout_operations.filter(undone_at__isnull=True).exclude(blocked_reason=NO_CHANGE).update(
        blocked_reason="原卷页序或页面来源已变化，旧布局不能覆盖当前原卷。")
