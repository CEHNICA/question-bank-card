"""自动流水线：解析 → 切题 → 读题 → 待终审。全程无需人工干预。"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from collections import OrderedDict, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone
from PIL import Image

from . import imaging, import_planning, photos, readers, segment
from .account_pool import AccountPoolError, account_pool
from .figure_policy import (
    BLOCKED_MISSING, CONFIRMED_NO_FIGURE, CONFLICT, FLAG_NO_FIGURE, FLAG_UNCUED_FIGURE,
    FLAG_UNFOUND_FIGURE, OK, automatic_review, figure_flag,
    recheck_automatic_review, stored_or_derived_review,
)
from .mineru import (
    MAX_PDF_PAGES, MineruError, load_blocks, request_extract_file_from_pool, write_pdf_slice,
)
from .models import Block, ImportChunk, Paper, Question, QuestionGroup
from .textnorm import same_reading
from .word import convert_docx_to_pdf

logger = logging.getLogger(__name__)
PARALLEL = readers._parallel_limit()
MINERU_HEARTBEAT_SECONDS = 5.0
FLAG_RESEGMENT_PRESERVED = "重新切题未再找到这张人工题卡，已保留；请核对题号与原卷范围"


def _invalidate_approval(question: Question) -> None:
    question.approved = False
    question.approved_at = None
    question.approved_content_hash = ""


def _flags_after_figure_review(flags: list[str], review: dict, figures: list[dict]) -> list[str]:
    """Replace only figure-policy flags; preserve all unrelated review warnings."""
    result = [flag for flag in (flags or []) if not figure_flag(flag)]
    if review.get("status") == BLOCKED_MISSING:
        result.append(FLAG_NO_FIGURE if review.get("cue_matches") and not figures else FLAG_UNFOUND_FIGURE)
    elif review.get("status") == CONFLICT:
        result.append(FLAG_UNFOUND_FIGURE if "candidate_unclassified" in (review.get("signals") or [])
                      else FLAG_UNCUED_FIGURE)
    return result


# ---------------------------------------------------------------- 文件与页面

def paper_dir(paper: Paper) -> Path:
    return settings.DATA_ROOT / str(paper.id)


def render_source(paper: Paper) -> tuple[Path, str]:
    # Word 转成的 PDF、照片合成的 PDF 都记在 render_path；老的单张图片卷直接用原图。
    path = Path(paper.render_path or paper.source_path)
    return path, ("pdf" if path.suffix.lower() == ".pdf" else "image")


def clear_page_cache(paper: Paper) -> None:
    """页面变了（例如调整了页序）：删掉缓存的页面图、预览图和配图裁图，下次用到时重新生成。"""
    for name in ("pages", "figures"):
        shutil.rmtree(paper_dir(paper) / name, ignore_errors=True)


class PageStore:
    """每页只渲染一次：高清原页（读题、裁图）和网页预览图都缓存在磁盘上。"""

    _lock = threading.Lock()
    MAX_MEMORY_PAGES = 4

    def __init__(self, paper: Paper):
        self.paper = paper
        self.folder = paper_dir(paper) / "pages"
        self.memory: OrderedDict[int, Image.Image] = OrderedDict()

    def path(self, page_idx: int) -> Path:
        return self.folder / f"page_{page_idx}.png"

    def preview_path(self, page_idx: int) -> Path:
        return self.folder / f"preview_{page_idx}.jpg"

    def load(self, page_idx: int) -> Image.Image:
        with self._lock:
            if page_idx in self.memory:
                self.memory.move_to_end(page_idx)
                return self.memory[page_idx]
            target = self.path(page_idx)
            if target.is_file():
                image = Image.open(target)
                image.load()
                image = image.convert("RGB")
            else:
                source, kind = render_source(self.paper)
                image = imaging.render_source_page(source, kind, page_idx)
                self.folder.mkdir(parents=True, exist_ok=True)
                image.save(target, format="PNG", optimize=False, compress_level=3)
            self.memory[page_idx] = image
            while len(self.memory) > self.MAX_MEMORY_PAGES:
                # 只移除缓存引用，不主动 close：并行识读线程可能正在裁剪这一页。
                # 待调用者的临时引用释放后，Pillow 对象会自然回收。
                self.memory.popitem(last=False)
            return image

    def preview(self, page_idx: int) -> Path:
        target = self.preview_path(page_idx)
        if not target.is_file():
            image = self.load(page_idx).copy()
            image.thumbnail((imaging.PREVIEW_LONG_SIDE, imaging.PREVIEW_LONG_SIDE), Image.Resampling.LANCZOS)
            target.parent.mkdir(parents=True, exist_ok=True)
            image.save(target, format="JPEG", quality=85, optimize=True)
        return target


def _set(paper: Paper, **fields) -> None:
    for key, value in fields.items():
        setattr(paper, key, value)
    paper.save(update_fields=[*fields, "updated_at"])


def _paper_heartbeat(paper_id, *, progress: int | None = None, total: int | None = None) -> None:
    """Touch one paper from the orchestration thread, optionally saving progress."""

    fields = {"updated_at": timezone.now()}
    if progress is not None:
        fields["progress"] = progress
    if total is not None:
        fields["total"] = total
    Paper.objects.filter(pk=paper_id).update(**fields)


# ---------------------------------------------------------------- 1. 解析

def _ensure_import_chunks(paper: Paper) -> list[ImportChunk]:
    """为需要本地切片的 PDF 建立确定、可重试的分片计划。"""
    chunks = list(paper.import_chunks.order_by("sequence"))
    if not chunks:
        chunk_limit = import_planning.pdf_chunk_page_limit(
            paper.material_type, MAX_PDF_PAGES,
        )
        plan = import_planning.plan_pdf_chunks(len(paper.pages), chunk_limit)
        chunks = ImportChunk.objects.bulk_create([
            ImportChunk(paper=paper, **item.as_record()) for item in plan
        ])
    import_planning.validate_chunk_coverage(
        chunks, expected_start=1, expected_end=len(paper.pages),
    )
    return list(paper.import_chunks.order_by("sequence"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _chunk_error_message(error: Exception) -> str:
    """Keep user-safe MinerU details, but never persist local paths from exceptions."""

    if isinstance(error, MineruError):
        return str(error)[:500]
    return f"分片处理失败（{type(error).__name__}）"


def _chunk_blocks(paper: Paper, render: Path) -> list[dict]:
    """并行解析缺失分片，并按原始顺序无损合并结果。

    主线程先顺序生成本地分片；工作线程只执行 MinerU 请求和读取结果 ZIP，
    所有 ORM 更新也都由主线程完成。这样一个分片失败时，其他已完成分片
    仍能安全持久化，下次重试只提交失败或缺失的部分。
    """
    chunks = _ensure_import_chunks(paper)
    folder = paper_dir(paper) / "chunks"
    folder.mkdir(parents=True, exist_ok=True)
    results: dict[int, list[dict]] = {}
    jobs: list[dict] = []

    for chunk in chunks:
        source = folder / f"chunk_{chunk.sequence:03d}.pdf"
        archive = folder / f"chunk_{chunk.sequence:03d}.zip"
        page_count = chunk.source_page_end - chunk.source_page_start + 1
        blocks = None
        try:
            blocks = load_blocks(archive, page_count) if archive.is_file() else None
        except MineruError:
            # 只丢弃本程序拥有的分片缓存；原始 PDF 永不改动。
            archive.unlink(missing_ok=True)
        if blocks is not None:
            results[chunk.sequence] = blocks
            ImportChunk.objects.filter(pk=chunk.pk).update(
                status=ImportChunk.Status.PARSED,
                artifact_path=str(archive),
                error="",
                updated_at=timezone.now(),
            )
            continue
        jobs.append({
            "pk": chunk.pk,
            "sequence": chunk.sequence,
            "source_page_start": chunk.source_page_start,
            "source_page_end": chunk.source_page_end,
            "page_map": tuple(int(page) for page in chunk.page_map),
            "page_count": page_count,
            "source": source,
            "archive": archive,
            "attempts": chunk.attempts + 1,
        })

    _paper_heartbeat(paper.pk, progress=len(results), total=len(chunks))

    failures: list[tuple[int, Exception]] = []
    if jobs:
        ready_jobs: list[dict] = []
        for job in jobs:
            ImportChunk.objects.filter(pk=job["pk"]).update(
                status=ImportChunk.Status.PARSING,
                attempts=job["attempts"],
                artifact_path=str(job["archive"]),
                error="",
                updated_at=timezone.now(),
            )
            try:
                if not job["source"].is_file():
                    # PyMuPDF opens the same original file for every slice, so do
                    # this deterministically on the main thread before networking.
                    write_pdf_slice(
                        render,
                        job["source"],
                        job["source_page_start"] - 1,
                        job["source_page_end"],
                    )
            except Exception as exc:
                failures.append((job["sequence"], exc))
                ImportChunk.objects.filter(pk=job["pk"]).update(
                    status=ImportChunk.Status.FAILED,
                    error=_chunk_error_message(exc),
                    updated_at=timezone.now(),
                )
            else:
                ready_jobs.append(job)

        def run(job: dict) -> list[dict]:
            request_extract_file_from_pool(
                job["source"], job["archive"], job["page_count"],
            )
            return load_blocks(job["archive"], job["page_count"])

        if ready_jobs:
            try:
                token_pool = account_pool("mineru")
            except AccountPoolError as exc:
                raise MineruError(str(exc)) from None
            max_workers = min(token_pool.size, len(ready_jobs))
            if max_workers <= 0:
                raise MineruError("MinerU 账号池中没有可用账号")

        if ready_jobs:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_jobs = {executor.submit(run, job): job for job in ready_jobs}
                pending = set(future_jobs)
                while pending:
                    done, pending = wait(
                        pending,
                        timeout=MINERU_HEARTBEAT_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        _paper_heartbeat(paper.pk)
                        continue
                    for future in done:
                        job = future_jobs[future]
                        try:
                            blocks = future.result()
                            digest = _file_sha256(job["source"])
                        except Exception as exc:
                            failures.append((job["sequence"], exc))
                            ImportChunk.objects.filter(pk=job["pk"]).update(
                                status=ImportChunk.Status.FAILED,
                                error=_chunk_error_message(exc),
                                updated_at=timezone.now(),
                            )
                        else:
                            results[job["sequence"]] = blocks
                            ImportChunk.objects.filter(pk=job["pk"]).update(
                                status=ImportChunk.Status.PARSED,
                                sha256=digest,
                                artifact_path=str(job["archive"]),
                                error="",
                                updated_at=timezone.now(),
                            )
                        _paper_heartbeat(paper.pk, progress=len(results), total=len(chunks))

    if failures:
        # Futures are all drained before reaching here, so later successes and
        # their ZIP files/statuses are already durable. Report the first source
        # chunk failure deterministically rather than completion-order roulette.
        failures.sort(key=lambda item: item[0])
        raise failures[0][1]

    merged: list[dict] = []
    for chunk in sorted(chunks, key=lambda item: item.sequence):
        blocks = results.get(chunk.sequence)
        if blocks is None:
            raise MineruError(f"第 {chunk.sequence} 个分片没有可用解析结果")
        for block in blocks:
            local_page = block["page_idx"]
            if not 0 <= local_page < len(chunk.page_map):
                raise MineruError(f"第 {chunk.sequence} 个分片返回了无效页码")
            merged.append({
                **block,
                "seq": len(merged),
                "page_idx": int(chunk.page_map[local_page]) - 1,
            })
    return merged


def _plan_structure(paper: Paper, blocks: list[dict]) -> tuple[dict, bool]:
    """用已有 MinerU 块本地判断题号结构；不调用任何识读模型。"""
    ranges = photos.page_ranges(paper.pages, blocks)
    ordered_ranges = [ranges.get(page["page_idx"]) for page in paper.pages]
    plan = import_planning.analyze_page_number_ranges(
        ordered_ranges, material_type=paper.material_type,
    )
    scopes = segment.numbering_scopes(paper.pages, blocks)
    scoped_restart = len(scopes) > 1
    suggested_groups = [scope["pages"] for scope in scopes] if scoped_restart else [
        list(range(group.page_start - 1, group.page_end)) for group in plan.groups
    ]
    structure = dict(paper.structure or {})
    structure.update({
        "page_ranges": [list(value) if value else None for value in ordered_ranges],
        "suggested_groups": suggested_groups,
        "suggested_scopes": scopes if scoped_restart else [],
        "signals": [
            {
                "kind": signal.kind,
                "page": signal.source_page - 1,
                "numbers": list(signal.numbers),
                "overlap": list(signal.overlapping_numbers),
                "message": signal.message,
            }
            for signal in plan.signals
        ],
    })
    if scoped_restart and not structure["signals"]:
        structure["signals"] = [{
            "kind": "numbering_restart",
            "page": scopes[1]["start_page"],
            "numbers": [scopes[1]["first_number"]],
            "overlap": [],
            "message": "同一资料中题号重新开始；已按独立题组保留，不会覆盖前面的同号题",
        }]
    confirmed = bool(structure.get("confirmed"))
    needs_confirmation = (
        (plan.needs_confirmation or (paper.material_type == Paper.MaterialType.EXAM and scoped_restart))
        and not confirmed
    )
    return structure, needs_confirmation


def _question_group_specs(paper: Paper) -> list[dict]:
    """Return the current confirmed/suggested scopes in a model-ready form."""
    structure = paper.structure or {}
    scopes = structure.get("confirmed_scopes") or structure.get("suggested_scopes") or []
    page_groups = structure.get("confirmed_groups") or \
        structure.get("suggested_groups") or [list(range(len(paper.pages)))]
    specs: list[dict] = []
    source_groups = scopes if scopes else [{"pages": pages} for pages in page_groups]
    for sequence, scope in enumerate(source_groups):
        pages = scope.get("pages") if isinstance(scope, dict) else None
        if not isinstance(pages, list):
            continue
        valid = sorted({int(page) for page in pages if type(page) is int and 0 <= page < len(paper.pages)})
        if not valid:
            continue
        title = ("第 %d 组" % (sequence + 1)) if paper.material_type == Paper.MaterialType.BOOK \
            else (paper.display_name if sequence == 0 else f"{paper.display_name}（{sequence + 1}）")
        specs.append({
            "title": title[:255],
            "kind": (QuestionGroup.Kind.CHAPTER if paper.material_type == Paper.MaterialType.BOOK
                     else QuestionGroup.Kind.EXAM),
            "sequence": sequence,
            "page_start": min(valid) + 1,
            "page_end": max(valid) + 1,
            "metadata": {
                "pages": valid,
                **({"seq_start": scope.get("seq_start"), "seq_end": scope.get("seq_end")}
                   if isinstance(scope, dict) and (scope.get("seq_start") is not None
                                                   or scope.get("seq_end") is not None) else {}),
            },
        })
    if not specs:
        raise RuntimeError("没有可用于切题的页面组")
    return specs


def _group_pages(group: QuestionGroup) -> tuple[int, ...]:
    pages = (group.metadata or {}).get("pages")
    if not isinstance(pages, list):
        pages = list(range((group.page_start or 1) - 1, group.page_end or 0))
    return tuple(sorted({int(page) for page in pages if type(page) is int}))


def _ensure_question_groups(paper: Paper) -> list[QuestionGroup]:
    """把已确认/自动建议的页组落成题号作用域。

    A human page-order change can produce a different confirmed scope plan after
    cards already exist.  In that case reuse the group whose pages still match,
    update/create the remaining groups, and move cards only when their regions
    identify exactly one new scope.  This preserves source keys without guessing
    between two scopes that share a physical page.
    """
    existing = list(paper.question_groups.order_by("sequence", "id"))
    structure = dict(paper.structure or {})
    rebuild = bool(structure.get("groups_need_rebuild"))
    if existing and not rebuild:
        return existing

    specs = _question_group_specs(paper)
    if not existing:
        QuestionGroup.objects.bulk_create([
            QuestionGroup(paper=paper, **spec) for spec in specs
        ])
        if rebuild:
            structure["groups_need_rebuild"] = False
            structure["groups_applied_at"] = structure.get("confirmed_at") or timezone.now().isoformat()
            Paper.objects.filter(pk=paper.pk).update(structure=structure, updated_at=timezone.now())
            paper.structure = structure
        return list(paper.question_groups.order_by("sequence", "id"))

    with transaction.atomic():
        # Match exact page membership first. This lets a cross-group page reorder
        # swap group sequence while each card keeps the same stable source group.
        unused = list(existing)
        selected: list[QuestionGroup | None] = []
        for spec in specs:
            wanted = tuple(spec["metadata"]["pages"])
            match = next((group for group in unused if _group_pages(group) == wanted), None)
            if match is not None:
                unused.remove(match)
            selected.append(match)

        # A former single scope may have split into several confirmed scopes.
        # Reuse one remaining group for the first unmatched scope and create the rest.
        for index, group in enumerate(selected):
            if group is None and unused:
                selected[index] = unused.pop(0)

        # Avoid transient unique_group_sequence collisions when two existing
        # groups exchange order.
        sequence_offset = max([group.sequence for group in existing] + [0]) + len(existing) + len(specs) + 1
        QuestionGroup.objects.filter(paper=paper).update(sequence=F("sequence") + sequence_offset)

        desired: list[QuestionGroup] = []
        for spec, group in zip(specs, selected):
            if group is None:
                group = QuestionGroup.objects.create(paper=paper, **spec)
            else:
                metadata = dict(group.metadata or {})
                metadata.update(spec["metadata"])
                for key in ("seq_start", "seq_end"):
                    if key not in spec["metadata"]:
                        metadata.pop(key, None)
                group.sequence = spec["sequence"]
                group.page_start = spec["page_start"]
                group.page_end = spec["page_end"]
                group.metadata = metadata
                # Keep a human/previous source title on a matched group. Only a
                # newly created group needs the generated title/kind.
                group.save(update_fields=[
                    "sequence", "page_start", "page_end", "metadata", "updated_at",
                ])
            desired.append(group)

        desired_pages = {group.pk: set(_group_pages(group)) for group in desired}
        changed_questions: list[Question] = []
        for question in paper.questions.all():
            region_pages = {
                item.get("page_idx") for item in (question.regions or question.regions_auto or [])
                if isinstance(item, dict) and type(item.get("page_idx")) is int
            }
            matches = [
                group for group in desired
                if region_pages and region_pages.issubset(desired_pages[group.pk])
            ]
            if len(matches) == 1 and question.group_id != matches[0].pk:
                question.group = matches[0]
                changed_questions.append(question)
        if changed_questions:
            Question.objects.bulk_update(changed_questions, ["group"])

        structure["groups_need_rebuild"] = False
        structure["groups_applied_at"] = structure.get("confirmed_at") or timezone.now().isoformat()
        Paper.objects.filter(pk=paper.pk).update(structure=structure, updated_at=timezone.now())
        paper.structure = structure
    return desired

def parse(paper: Paper) -> None:
    _set(paper, status=Paper.Status.PARSING, error="")
    folder = paper_dir(paper)
    source = Path(paper.source_path)
    if paper.kind == "docx" and not paper.render_path:
        target = folder / "converted.pdf"
        convert_docx_to_pdf(source, target)
        _set(paper, render_path=str(target))
    if paper.photos and not paper.render_path:
        prepare_photos(paper)
    render, kind = render_source(paper)
    if not paper.pages:
        _set(paper, pages=imaging.page_sizes(render, kind))
    chunked = kind == "pdf" and (
        import_planning.pdf_requires_chunks(
            len(paper.pages), paper.material_type, MAX_PDF_PAGES,
        )
        or paper.import_chunks.exists()
    )
    archive: Path | None = None
    if chunked:
        blocks = _chunk_blocks(paper, render)
    else:
        archive = Path(paper.zip_path) if paper.zip_path else folder / "mineru_result.zip"

        def heartbeat() -> None:
            _paper_heartbeat(paper.pk)

        if not archive.is_file():
            request_extract_file_from_pool(
                render, archive, len(paper.pages), heartbeat=heartbeat,
            )
        try:
            blocks = load_blocks(archive, len(paper.pages))
        except MineruError:
            # Only remove the per-paper cache we own. A failed/oversized download used
            # to leave a file behind, causing every retry to reopen the same bad ZIP.
            owned_archive = archive.name == "mineru_result.zip" and archive.parent.resolve() == folder.resolve()
            if not owned_archive:
                raise
            archive.unlink(missing_ok=True)
            request_extract_file_from_pool(
                render, archive, len(paper.pages), heartbeat=heartbeat,
            )
            blocks = load_blocks(archive, len(paper.pages))
    if paper.photos:
        blocks = arrange_photo_pages(paper, blocks)
        paper.refresh_from_db(fields=["photos", "pages", "structure", "updated_at"])
    structure, needs_confirmation = _plan_structure(paper, blocks)
    with transaction.atomic():
        paper.blocks.all().delete()
        Block.objects.bulk_create([Block(paper=paper, **block) for block in blocks], batch_size=300)
        _set(
            paper,
            zip_path=str(archive) if archive is not None else "",
            structure=structure,
            status=Paper.Status.NEEDS_GROUPING if needs_confirmation else Paper.Status.SEGMENTING,
            progress=0,
            total=0,
        )


# ---------------------------------------------------------------- 1½. 手机照片：合成、排页序

PAGE_NOTE = "页序："


def prepare_photos(paper: Paper) -> None:
    """照片：拉正、扫描件效果，按初步顺序（拍摄时间/文件名）合成 PDF，交给 MinerU。"""
    folder = paper_dir(paper)
    info = dict(paper.photos)
    info["notes"] = photos.prepare_pages(folder, info)
    target = folder / "pages.pdf"
    photos.build_pdf(folder, info, target)
    info["mineru_order"] = list(info["order"])
    _set(paper, photos=info, render_path=str(target), pages=imaging.page_sizes(target, "pdf"))


def _page_note(info: dict, ranges: dict[int, tuple | None] | None, prefix: str) -> str:
    names = [info["files"][index]["name"] for index in info["order"]]
    parts = []
    for page, name in enumerate(names):
        span = (ranges or {}).get(page)
        if span:
            label = f"第 {span[0]} 题" if span[0] == span[1] else f"第 {span[0]}–{span[1]} 题"
            parts.append(f"第 {page + 1} 页 {name}（{label}）")
        else:
            parts.append(f"第 {page + 1} 页 {name}")
    return f"{PAGE_NOTE}{prefix}" + "；".join(parts) + "。"


def _store_ranges(info: dict, ranges: dict[int, tuple | None]) -> None:
    """每张照片上的题号范围跟着照片走（按照片编号存），调整页序时网页上能看到每页是第几题到第几题。"""
    info["ranges"] = {str(info["order"][page]): list(span) if span else None for page, span in ranges.items()}


def arrange_photo_pages(paper: Paper, blocks: list[dict]) -> list[dict]:
    """MinerU 读完后、切题之前：按卷面题号把照片排成正确的页序。

    blocks 的页码是交给 MinerU 时的页序；返回按最终页序改好页码的 blocks。
    """
    info = dict(paper.photos)
    current = info["order"]
    parsed = info.get("mineru_order") or current
    position = {file_index: page for page, file_index in enumerate(current)}
    blocks = [photos.remap_page({page: position[index] for page, index in enumerate(parsed)}, b) for b in blocks]
    if len(current) < 2:
        return blocks
    ranges = photos.page_ranges(paper.pages, blocks)
    notes = [note for note in info.get("notes", []) if not note.startswith(PAGE_NOTE)]
    if info.get("manual"):
        info["notes"] = [_page_note(info, ranges, "已手动调整。"), *notes]
        _store_ranges(info, ranges)
        _set(paper, photos=info)
        return blocks
    order, check = photos.arrange(ranges, list(range(len(current))))
    info["check"] = check
    if order != list(range(len(current))):
        mapping = {old: new for new, old in enumerate(order)}
        blocks = [photos.remap_page(mapping, b) for b in blocks]
        ranges = {mapping[page]: span for page, span in ranges.items()}
        info["order"] = [current[page] for page in order]
        photos.build_pdf(paper_dir(paper), info, Path(paper.render_path))
        clear_page_cache(paper)
        paper.pages = imaging.page_sizes(Path(paper.render_path), "pdf")
        basis = info.get("basis", "选择顺序")
        prefix = f"已按卷面题号排好（和{basis}不同）。" if not check else "请确认。"
    else:
        prefix = "按卷面题号核对无误。" if not check else "请确认。"
    info["notes"] = [_page_note(info, ranges, prefix), *notes]
    _store_ranges(info, ranges)
    _set(paper, photos=info, pages=paper.pages)
    return blocks


def reorder_photo_pages(paper: Paper, order: list[int]) -> None:
    """人工调整页序：order 是当前页码的新排列。改好页面、内容块和题卡里的页码，然后重新切题。"""
    mapping = {old: new for new, old in enumerate(order)}
    info = dict(paper.photos)
    info["order"] = [info["order"][page] for page in order]
    info["manual"] = True
    info["check"] = ""
    photos.build_pdf(paper_dir(paper), info, Path(paper.render_path))
    clear_page_cache(paper)
    blocks = _block_dicts(paper)
    # Block.seq is the stable content order used to divide two numbering scopes
    # that share a page. Once whole pages move, the old global seq order is no
    # longer the new reading order, so rebuild it while preserving order inside
    # each page. Otherwise a reverse page move can create seq_end=-1 and drop a
    # complete scope after structure confirmation.
    ordered_blocks = sorted(blocks, key=lambda block: (mapping[block["page_idx"]], block["seq"]))
    seq_mapping = {block["seq"]: sequence for sequence, block in enumerate(ordered_blocks)}
    remapped_blocks = [
        {**photos.remap_page(mapping, block), "seq": seq_mapping[block["seq"]]}
        for block in blocks
    ]
    new_pages = imaging.page_sizes(Path(paper.render_path), "pdf")
    ranges = photos.page_ranges(new_pages, remapped_blocks)
    info["notes"] = [_page_note(info, ranges, "已手动调整。"),
                     *[note for note in info.get("notes", []) if not note.startswith(PAGE_NOTE)]]
    _store_ranges(info, ranges)
    with transaction.atomic():
        changed = list(paper.blocks.all())
        # Two-phase renumbering avoids a transient collision on the
        # (paper, seq) unique constraint when seq values exchange places.
        if changed:
            offset = max(block.seq for block in changed) + len(changed) + 1
            paper.blocks.update(seq=F("seq") + offset)
        for block in changed:
            old_seq = block.seq
            block.page_idx = mapping[block.page_idx]
            block.seq = seq_mapping[old_seq]
        Block.objects.bulk_update(changed, ["page_idx", "seq"], batch_size=300)
        for question in paper.questions.all():
            for field in ("regions", "regions_auto", "figures", "figure_candidates"):
                remapped = [photos.remap_page(mapping, item) for item in getattr(question, field)]
                if field == "figure_candidates":
                    remapped = [
                        {**item, **({"seq": seq_mapping[item["seq"]]}
                                   if item.get("seq") in seq_mapping else {})}
                        for item in remapped
                    ]
                setattr(question, field, remapped)
            if not any(figure.get("source") == "manual" for figure in question.figures):
                question.figure_review = {}
            _invalidate_approval(question)
            question.save(update_fields=[
                "regions", "regions_auto", "figures", "figure_candidates", "figure_review",
                "approved", "approved_at", "approved_content_hash", "updated_at",
            ])
        # Question groups are source scopes, so their page membership must move
        # with the pages just like blocks and cards.  Leaving this stale can put a
        # chapter's cards under another chapter after a cross-group reorder.
        for group in paper.question_groups.select_for_update():
            metadata = dict(group.metadata or {})
            old_pages = metadata.get("pages")
            if not isinstance(old_pages, list):
                old_pages = list(range((group.page_start or 1) - 1, group.page_end or len(paper.pages)))
            old_pages = [page for page in old_pages if type(page) is int and page in mapping]
            new_group_pages = sorted({mapping[page] for page in old_pages if page in mapping})
            if not new_group_pages:
                continue
            source_pages = metadata.get("source_pages")
            if isinstance(source_pages, list) and len(source_pages) == len(old_pages):
                source_by_old_page = dict(zip(old_pages, source_pages))
                metadata["source_pages"] = [
                    source_by_old_page[old_page]
                    for old_page in sorted(old_pages, key=mapping.get)
                ]
            for key in ("seq_start", "seq_end"):
                old_seq = metadata.get(key)
                if type(old_seq) is int and old_seq in seq_mapping:
                    metadata[key] = seq_mapping[old_seq]
                elif key in metadata:
                    metadata.pop(key, None)
            metadata["pages"] = new_group_pages
            group.metadata = metadata
            group.page_start = min(new_group_pages) + 1
            group.page_end = max(new_group_pages) + 1
            group.save(update_fields=["metadata", "page_start", "page_end", "updated_at"])
        # Re-evaluate the conflict after the human page-order change. A reorder
        # must not silently bypass the safeguard that prevented two exams from
        # being merged in the first place.
        paper.pages = new_pages
        structure_before = dict(paper.structure or {})
        for key in ("confirmed", "confirmed_at", "confirmed_groups", "confirmed_scopes"):
            structure_before.pop(key, None)
        source_pages = structure_before.get("source_pages")
        if isinstance(source_pages, list) and len(source_pages) == len(order):
            structure_before["source_pages"] = [source_pages[old_page] for old_page in order]
        paper.structure = structure_before
        structure, needs_confirmation = _plan_structure(paper, remapped_blocks)
        _set(
            paper,
            photos=info,
            pages=new_pages,
            structure=structure,
            status=Paper.Status.NEEDS_GROUPING if needs_confirmation else Paper.Status.SEGMENTING,
            error="",
        )


# ---------------------------------------------------------------- 2. 切题

def _block_dicts(paper: Paper) -> list[dict]:
    return [{"seq": b.seq, "type": b.type, "page_idx": b.page_idx, "bbox": b.bbox, "text": b.text}
            for b in paper.blocks.all()]


def _snap_to_gap(image: Image.Image, row: int, band_height: float) -> int:
    """把 AI 给出的横带位置吸附到附近最空的一行（两行字之间的空隙）。"""
    gray = image.convert("L")
    top = max(0, int(row - band_height))
    bottom = min(image.height - 1, int(row + band_height * 0.5))
    if bottom <= top:
        return row
    strip = gray.crop((0, top, image.width, bottom + 1)).resize((max(1, image.width // 4), bottom - top + 1))
    pixels = strip.load()
    best_row, best_ink = row, None
    for y in range(strip.height):
        ink = sum(1 for x in range(strip.width) if pixels[x, y] < 150)
        # 同样空时取更靠近题号的一行
        if best_ink is None or ink < best_ink or (ink == best_ink and abs(top + y - row) < abs(best_row - row)):
            best_row, best_ink = top + y, ink
    return best_row


def locate_missing(paper: Paper, layout, starts: list[segment.Start], store: PageStore) -> list[str]:
    """MinerU 漏掉的题号：把前一题到后一题之间的原卷交给 AI，只问"第 N 题的题号在第几格"。"""
    notes = []
    engine = readers.primary_engine()
    for number, previous in segment.missing_numbers(starts):
        ordered = sorted(starts, key=segment.Start.key)
        following = next((s for s in ordered if s.key() > previous.key() and s.number > number), None)
        regions = segment.region_regions_between(layout, previous, following)
        if engine is None or not regions:
            notes.append(f"没有找到第 {number} 题的印刷题号，它可能和第 {previous.number} 题在同一张卡里。")
            continue
        try:
            image, placed = imaging.stack_regions(regions, store.load)
            bands = max(12, min(40, image.height // 45))
            ruled, bands = imaging.add_ruler(image, bands)
            band = readers.locate_band(engine, imaging.jpeg_data_url(ruled, long_side=2200), number)
        except readers.ReaderError as error:
            notes.append(f"定位第 {number} 题失败（{error}），它暂时和第 {previous.number} 题在同一张卡里。")
            continue
        if not band or not 1 <= band <= bands:
            notes.append(f"AI 没有找到第 {number} 题的题号，它可能和第 {previous.number} 题在同一张卡里。")
            continue
        band_height = image.height / bands
        row = _snap_to_gap(image, int((band - 1) * band_height), band_height)
        position = imaging.band_to_page(placed, 1 + row / band_height, bands, image.height)
        if position is None:
            continue
        page_idx, y = position
        region = next((r for r in placed if r["page_idx"] == page_idx and r["bbox"][1] <= y <= r["bbox"][3] + 1), None)
        if region is None:
            continue
        x = region["bbox"][0] + 5
        col = segment.column_of(layout.splits.get(page_idx, []), x + 1)
        # y 是题号上方的空隙：前一题到此为止，本题从这里（再往上留一点）开始。
        starts.append(segment.Start(number=number, page=page_idx, x=x, y=y + segment.END_GAP,
                                    seq=None, source="located", col=col))
        notes.append(f"第 {number} 题的题号 MinerU 没读出来，已由 AI 在原卷上定位。")
    return notes


def segment_paper(paper: Paper) -> None:
    """切题。已有题卡时（重新切题）：内容没变的题卡原样保留（包括已通过的），变了的才重读；
    人工调整过范围或手动补的题卡不动。"""
    blocks = _block_dicts(paper)
    store = PageStore(paper)
    groups = _ensure_question_groups(paper)
    notes: list[str] = []
    questions: list[dict] = []
    for group in groups:
        metadata = group.metadata or {}
        pages_in_group = metadata.get("pages")
        if not isinstance(pages_in_group, list):
            start = (group.page_start or 1) - 1
            end = group.page_end or len(paper.pages)
            pages_in_group = list(range(start, end))
        page_ids = {int(page) for page in pages_in_group if type(page) is int}
        group_pages = [page for page in paper.pages if page["page_idx"] in page_ids]
        seq_start, seq_end = metadata.get("seq_start"), metadata.get("seq_end")
        group_blocks = [
            block for block in blocks
            if block["page_idx"] in page_ids
            and (seq_start is None or block["seq"] >= seq_start)
            and (seq_end is None or block["seq"] <= seq_end)
        ]
        if not group_pages or not group_blocks:
            continue
        layout, starts = segment.analyse(group_pages, group_blocks)
        group_notes = locate_missing(paper, layout, starts, store)
        notes.extend([f"{group.title}：{note}" if len(groups) > 1 else note for note in group_notes])
        for item in segment.build_questions(layout, starts, group_blocks):
            questions.append({**item, "group": group})
    if not questions:
        raise RuntimeError("没有在试卷里找到印刷题号，无法切题")
    existing: dict[tuple[int | None, int], list[Question]] = defaultdict(list)
    existing_questions = list(paper.questions.all())
    for question in existing_questions:
        group_id = question.group_id or (groups[0].id if len(groups) == 1 else None)
        existing[(group_id, question.number)].append(question)
    for bucket in existing.values():
        bucket.sort(key=lambda question: (_region_position(question.regions), question.pk))
    kept = reread = preserved = 0
    with transaction.atomic():
        matched_ids: set[int] = set()
        for item in questions:
            group = item["group"]
            identity = (group.id, item["number"])
            regions = imaging.trim_regions(item["regions"], store.load) if item["regions"] else []
            candidates = _label_candidates(item["figure_candidates"])
            bucket = existing.get(identity, [])
            question = min(
                bucket,
                key=lambda candidate: (
                    abs(_region_position(candidate.regions) - _region_position(regions)),
                    candidate.pk,
                ),
                default=None,
            )
            if question is not None:
                bucket.remove(question)
                matched_ids.add(question.pk)
            if question is None:
                Question.objects.create(
                    paper=paper, group=group, number=item["number"], section=item["section"][:120],
                    question_type=item["question_type"], regions=regions, regions_auto=regions,
                    start_source=item["start"]["source"], figure_candidates=candidates,
                )
                continue
            if question.group_id != group.id:
                question.group = group
            if question.start_source == "manual" or (question.regions and question.regions != question.regions_auto):
                continue  # 人工框的范围优先
            regions_changed = question.regions != regions
            # MinerU text blocks are only a locator. A moved image range may add a
            # formula, diagram or printed line that MinerU never represented, so a
            # range change must be reread even when the block-id set looks equal.
            unchanged = (not regions_changed and question.regions and question.state != Question.State.RED
                         and segment.text_blocks_in(blocks, question.regions) == segment.text_blocks_in(blocks, regions))
            new_section = item["section"][:120]
            new_start_source = item["start"]["source"]
            new_question_type = question.question_type if question.edited else item["question_type"]
            source_changed = (
                regions_changed
                or question.section != new_section
                or question.start_source != new_start_source
                or question.question_type != new_question_type
            )
            question.regions = regions
            question.regions_auto = regions
            question.section = new_section
            question.start_source = new_start_source
            question.figure_candidates = candidates
            if not question.edited:
                question.question_type = new_question_type
            if unchanged:
                kept += 1
                if source_changed:
                    _invalidate_approval(question)
            else:
                reread += 1
                question.figures = [f for f in question.figures if f.get("source") == "manual"]
                question.figure_review = ({
                    "status": OK, "source": "human", "reason": "配图已经由人工设置",
                    "signals": ["manual_figure"], "cue_matches": [], "excluded_count": 0,
                } if question.figures else {})
                question.state = Question.State.WAITING
                _invalidate_approval(question)
                question.flags = []
                question.error = ""
            question.save()
        for question in existing_questions:
            if question.pk in matched_ids or question.start_source == "manual":
                continue
            if question.edited or question.approved or question.publications.exists():
                # 自动重切不能物理删除人工改字、已通过草稿或已入库的来源卡。
                # 但新结构已经找不到它，也不能悄悄沿用旧“通过”状态。
                question.state = Question.State.YELLOW
                question.flags = [
                    *[flag for flag in (question.flags or []) if flag != FLAG_RESEGMENT_PRESERVED],
                    FLAG_RESEGMENT_PRESERVED,
                ]
                question.error = ""
                question.reread_requested = False
                _invalidate_approval(question)
                question.save(update_fields=[
                    "state", "flags", "error", "reread_requested",
                    "approved", "approved_at", "approved_content_hash", "updated_at",
                ])
                preserved += 1
                continue
            question.delete()
        desired_group_ids = [group.pk for group in groups]
        paper.question_groups.exclude(pk__in=desired_group_ids).filter(questions__isnull=True).delete()
        if existing:
            notes.append(f"重新切题：{kept} 张题卡内容没变，原样保留；{reread} 张范围变了，已重新识读。")
        if preserved:
            notes.append(
                f"重新切题时有 {preserved} 张人工改字、已通过或已入库题卡未被新结构命中；"
                "已保留并标黄，请对照原卷核对。"
            )
        _set(paper, status=Paper.Status.READING, notes=notes, progress=0, total=paper.questions.count())


def _region_position(regions: list[dict] | None) -> float:
    """Comparable source position used only to pair repeated display numbers.

    A question number is not an identity: books routinely restart at 1, and OCR
    may miss the boundary that should have created a new group.  Pairing each
    new slice with the nearest still-unmatched old slice preserves distinct
    cards and their UUIDs instead of repeatedly overwriting one dictionary row.
    """

    if not regions:
        return 1e30
    first = regions[0] if isinstance(regions[0], dict) else {}
    bbox = first.get("bbox") if isinstance(first, dict) else None
    page = first.get("page_idx", 0) if isinstance(first, dict) else 0
    try:
        x, y = float(bbox[0]), float(bbox[1])
        return float(page) * 1_000_000.0 + y * 1_000.0 + x
    except (TypeError, ValueError, IndexError):
        return 1e30


def _label_candidates(candidates: list[dict]) -> list[dict]:
    return [{"label": str(index), **candidate} for index, candidate in enumerate(candidates, start=1)]


def candidates_in(paper: Paper, regions: list[dict]) -> list[dict]:
    found = [{"seq": block.seq, "page_idx": block.page_idx, "bbox": block.bbox}
             for block in paper.blocks.filter(type__in=segment.FIGURE_TYPES)
             if block.bbox and segment.overlaps_regions(block.page_idx, block.bbox, regions)]
    return _label_candidates(found)


# ---------------------------------------------------------------- 3. 读题


def _figure_slots(reading: dict | None) -> set[str]:
    """主读者明确归到题干或 A–D 的候选图槽位。"""
    return {
        role for role in ((reading or {}).get("figures") or {}).values()
        if role == "stem" or role in readers.OPTION_KEYS
    }


def _without_inferred_figure_text(reading: dict | None, figure_reading: dict | None) -> dict | None:
    """配图内容以裁图为准，不采用模型生成的选项说明或 Markdown 表格。

    只有完整的括号式图片说明，或已知的严格数轴说明句式才会删除。不能仅凭
    主读者把文字留空，就删除另一读者识别到的任意文字；图旁仍可能有必须保留
    的印刷字。归到题干的 Markdown 表格只保留裁图。返回清理后的副本，原始
    read_a/read_b/read_c 仍原样留作审计。
    """
    if reading is None:
        return None
    slots = _figure_slots(figure_reading)
    option_slots = slots & set(readers.OPTION_KEYS)
    primary_options = (figure_reading or {}).get("options") or {}
    # 至少找到一个选项图，且主读 A–D 全空，是“纯图片选择题”的强证据。
    # 这时次读未绑定槽位里的严格图片说明也要清理；稍后会为这些未绑定图加黄旗。
    pure_figure_choice = bool(option_slots) and not any(
        str(primary_options.get(slot, "")).strip() for slot in readers.OPTION_KEYS
    )
    # 纯图片选择题检查全部 A–D；混合题只检查已经绑定裁图的槽位。实际删除
    # 仍须通过严格说明模式，因此“向右”等真实短语不会因主读漏字而被清掉。
    eligible_slots = set(readers.OPTION_KEYS) if pure_figure_choice else option_slots
    remove = {
        slot for slot in eligible_slots
        if readers.is_figure_description((reading.get("options") or {}).get(slot, ""))
    }
    stem = reading.get("stem", "")
    table_removed = False
    if "stem" in slots:
        stem, table_removed = readers.strip_markdown_tables(stem)
    if not remove and not table_removed:
        return reading
    cleaned = dict(reading)
    cleaned["stem"] = stem
    cleaned["options"] = {
        key: value for key, value in (reading.get("options") or {}).items()
        if key not in remove
    }
    if remove:
        cleaned["figure_descriptions"] = sorted(
            set(cleaned.get("figure_descriptions") or []) | remove,
            key=lambda slot: (slot != "stem", slot),
        )
    cleaned["unclear"] = "[?]" in cleaned.get("stem", "") or any(
        "[?]" in value for value in cleaned["options"].values()
    )
    return cleaned


def read_card(snapshot: dict, store: PageStore) -> dict:
    """纯计算，不碰数据库（在线程里运行）。返回要写回题卡的字段。"""
    number = snapshot["number"]
    primary, checker = readers.primary_engine(), readers.checker_engine()
    if primary is None:
        return {"state": Question.State.RED, "error": "没有配置所选主读模型的 API Key，无法读题", "flags": []}
    if not snapshot["regions"]:
        return {"state": Question.State.RED, "flags": [],
                "error": "没有切出这道题的原卷范围，请点“调整范围”在原卷上框出来"}
    clean, _ = imaging.stack_regions(snapshot["regions"], store.load)
    marks = [{"label": c["label"], "page_idx": c["page_idx"], "bbox": c["bbox"]} for c in snapshot["candidates"]]
    marked = imaging.stack_regions(snapshot["regions"], store.load, marks)[0] if marks else clean
    clean_url, marked_url = imaging.jpeg_data_url(clean), imaging.jpeg_data_url(marked)

    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    jobs = [
        (name, engine, url, figures)
        for name, engine, url, figures in (
            ("a", primary, marked_url, True),
            ("b", checker, clean_url, False),
        )
        if engine is not None
    ]
    if checker is None:
        errors["b"] = "所选复核模型没有可用的 API Key"

    def run_reader(job: tuple[str, readers.Engine, str, bool]) -> tuple[str, dict | None, str]:
        name, engine, url, figures = job
        try:
            return name, readers.read_question(engine, url, number, with_figures=figures), ""
        except readers.ReaderError as error:
            return name, None, str(error)

    # 主读和复核彼此独立；两个提供商或同提供商多账号时
    # 可同时进行。若只有一个账号，AccountPool 会在内部自动串行。
    if len(jobs) == 1:
        completed = [run_reader(jobs[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [executor.submit(run_reader, job) for job in jobs]
            completed = [future.result() for future in futures]
    for name, result, error in completed:
        if result is not None:
            results[name] = result
        else:
            errors[name] = error
    flags: list[str] = []
    update: dict = {"read_a": results.get("a", {"error": errors.get("a", "")}),
                    "read_b": results.get("b", {"error": errors.get("b", "")}), "read_c": {}}
    if not results:
        return {**update, "state": Question.State.RED, "error": errors.get("a") or errors.get("b") or "识读失败",
                "flags": []}
    a, b = results.get("a"), results.get("b")
    figure_source = a or {}
    a_text = _without_inferred_figure_text(a, figure_source)
    b_text = _without_inferred_figure_text(b, figure_source)
    normalized_results = [result for result in (a_text, b_text) if result]
    if a and b and same_reading(a_text, b_text):
        final, source = a_text, "agree"
    elif a and b:
        try:
            arbiter = readers.arbiter_engine(primary, checker)
            if arbiter is None:
                raise readers.ReaderError("没有可用的分歧裁决模型")
            c = readers.arbitrate(arbiter, clean_url, number, a_text, b_text)
            update["read_c"] = c
            c_text = _without_inferred_figure_text(c, figure_source)
            normalized_results.append(c_text)
            if same_reading(c_text, a_text):
                final, source = a_text, "majority"
            elif same_reading(c_text, b_text):
                final, source = b_text, "majority"
            else:
                final, source = c_text, "arbiter"
                flags.append("两次识读不一致，已由第三次识读裁决，请看标黄的地方")
        except readers.ReaderError as error:
            final, source = a_text, "single"
            update["read_c"] = {"error": str(error)}
            flags.append("两次识读不一致，裁决失败，请看标黄的地方")
    else:
        final = a_text or b_text
        source = "single"
        flags.append(f"只有一次识读成功（另一次：{errors.get('b') or errors.get('a')}）")

    figures, foreign = [], []
    labels = {c["label"]: c for c in snapshot["candidates"]}
    for label, role in (figure_source.get("figures") or {}).items():
        if label not in labels:
            continue
        box = {"page_idx": labels[label]["page_idx"], "bbox": labels[label]["bbox"]}
        if role in {"stem", "A", "B", "C", "D"}:
            figures.append({"slot": role, **box, "source": "auto"})
        elif role.startswith("q") and role[1:].isdigit():
            foreign.append({
                "number": int(role[1:]),
                "group_id": snapshot.get("group_id"),
                **box,
            })   # 属于同一题组内别的题的图，交给那道题
    option_figure_slots = {f["slot"] for f in figures if f["slot"] in readers.OPTION_KEYS}
    audited_results = list(results.values()) + normalized_results
    if isinstance(update.get("read_c"), dict):
        audited_results.append(update["read_c"])
    described_slots = {
        slot for result in audited_results
        for slot in (result.get("figure_descriptions") or [])
    }
    # The text reader sees the complete question and is more reliable than the
    # segmentation heuristic for sub-numbered free-response questions such as
    # “(1)…(2)…”.  Keep the segment type only as a fallback.
    kind = final.get("type") or "unknown"
    if kind == "unknown":
        kind = snapshot["question_type"]
    choice_missing_slots = set()
    if kind in {"single_choice", "multiple_choice"} and not final.get("options"):
        choice_missing_slots = set(readers.OPTION_KEYS) - option_figure_slots
    choice_missing = bool(choice_missing_slots)
    policy_stem = snapshot.get("stem", "") if snapshot.get("edited") else final.get("stem", "")
    policy_options = snapshot.get("options", {}) if snapshot.get("edited") else final.get("options") or {}
    review = automatic_review(
        stem=policy_stem,
        options=policy_options,
        candidate_labels=set(labels),
        assignments=figure_source.get("figures") or {},
        figures=figures,
        reader_missing=bool(figure_source.get("missing_figure") or choice_missing),
        described_slots=described_slots | choice_missing_slots,
    )
    if review["status"] == BLOCKED_MISSING:
        if choice_missing:
            flags.append("选择题没有读出选项；如果选项是图，请点“配图”把 A–D 各框一下")
            flags.append(FLAG_UNFOUND_FIGURE)
        elif review.get("cue_matches") and not figures:
            flags.append(FLAG_NO_FIGURE)
        else:
            flags.append(FLAG_UNFOUND_FIGURE)
    elif review["status"] == CONFLICT:
        flags.append(FLAG_UNFOUND_FIGURE if "candidate_unclassified" in review.get("signals", [])
                     else FLAG_UNCUED_FIGURE)
    if final.get("unclear"):
        flags.append("有看不清的字（[?]），请对照原卷补上")
    # 两位读者都说看到了别的题号才提示；能用"第N题图"解释的不算。
    seen_a, seen_b = set((a or {}).get("others", [])), set((b or {}).get("others", []))
    others = (seen_a & seen_b) if a and b else (seen_a | seen_b)
    others -= {item["number"] for item in foreign}
    if others:
        flags.append(f"截图里还露出了第 {'、'.join(map(str, sorted(others)))} 题，范围可能需要调整")
    seen = (a or b).get("number_seen")
    if seen and seen != number:
        flags.append(f"AI 看到的题号是 {seen}，请确认")
    return {
        **update,
        "stem": final.get("stem", ""),
        "options": final.get("options") or {},
        "question_type": kind,
        "text_source": source,
        "figures": figures,
        "figure_review": review,
        "foreign_figures": foreign,
        "flags": flags,
        "error": "",
        "state": Question.State.GREEN if not flags else Question.State.YELLOW,
    }


def _snapshot(question: Question) -> dict:
    return {"id": question.id, "number": question.number, "group_id": question.group_id,
            "start_source": question.start_source, "regions": question.regions,
            "candidates": question.figure_candidates, "question_type": question.question_type,
            "stem": question.stem, "options": question.options, "edited": question.edited}


def read_questions(paper: Paper, questions: list[Question]) -> None:
    if not questions:
        return
    store = PageStore(paper)
    snapshots = [_snapshot(q) for q in questions]
    Question.objects.filter(pk__in=[q.id for q in questions]).update(
        state=Question.State.READING,
        reread_requested=False,
        approved=False,
        approved_at=None,
        approved_content_hash="",
    )

    def work(snapshot: dict) -> tuple[int, dict]:
        try:
            return snapshot["id"], read_card(snapshot, store)
        except Exception as error:  # 单题失败不影响其他题
            logger.exception("read failed")
            detail = str(error).strip() or type(error).__name__
            return snapshot["id"], {"state": Question.State.RED, "error": f"识读出错：{detail}"[:280],
                                    "flags": []}
        finally:
            close_old_connections()

    foreign: list[dict] = []
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        futures = [pool.submit(work, snapshot) for snapshot in snapshots]
        for future in as_completed(futures):
            question_id, fields = future.result()
            foreign.extend(fields.pop("foreign_figures", []))
            question = Question.objects.filter(pk=question_id).first()
            if question is None:
                continue
            borrowed = [f for f in question.figures if f.get("source") == "other"]
            if borrowed and "figures" in fields:
                fields["figures"] = fields["figures"] + [f for f in borrowed if not _same_box(f, fields["figures"])]
                review_stem = question.stem if question.edited else fields.get("stem", question.stem)
                review_options = question.options if question.edited else fields.get("options", question.options)
                fields["figure_review"] = recheck_automatic_review(
                    stem=review_stem,
                    options=review_options,
                    figures=fields["figures"],
                    previous=fields.get("figure_review"),
                )
                fields["flags"] = _flags_after_figure_review(
                    fields.get("flags", []), fields["figure_review"], fields["figures"],
                )
                if fields.get("state") in {Question.State.GREEN, Question.State.YELLOW}:
                    fields["state"] = Question.State.YELLOW if fields["flags"] else Question.State.GREEN
            if question.edited:
                # 人工改过的文字不被覆盖，只更新识读记录与配图建议。
                fields = {k: v for k, v in fields.items() if k not in {"stem", "options", "text_source"}}
                fields["flags"] = [f for f in fields.get("flags", []) if "识读" not in f and "[?]" not in f]
                if fields.get("state") == Question.State.YELLOW and not fields["flags"]:
                    fields["state"] = Question.State.GREEN
            if question.figures and any(f.get("source") == "manual" for f in question.figures):
                fields.pop("figures", None)
                fields["flags"] = [f for f in fields.get("flags", []) if not figure_flag(f)]
                fields["figure_review"] = {
                    "status": OK, "source": "human", "reason": "配图已经由人工设置",
                    "signals": ["manual_figure"], "cue_matches": [], "excluded_count": 0,
                }
            elif stored_or_derived_review(question).get("status") == CONFIRMED_NO_FIGURE:
                fields["figures"] = []
                fields["flags"] = [f for f in fields.get("flags", []) if not figure_flag(f)]
                fields["figure_review"] = stored_or_derived_review(question)
            if fields.get("state") == Question.State.YELLOW and not fields.get("flags"):
                fields["state"] = Question.State.GREEN
            for key, value in fields.items():
                setattr(question, key, value)
            question.save()
            Paper.objects.filter(pk=paper.pk).update(progress=paper.questions.exclude(
                state__in=[Question.State.WAITING, Question.State.READING]).count(), updated_at=timezone.now())
    assign_foreign_figures(paper, foreign)


def _same_box(figure: dict, others: list[dict]) -> bool:
    return any(o["page_idx"] == figure["page_idx"] and all(abs(a - b) < 1 for a, b in zip(o["bbox"], figure["bbox"]))
               for o in others)


def assign_foreign_figures(paper: Paper, foreign: list[dict]) -> None:
    """读 A 题时发现某张图印着"第 N 题图"：把它交给第 N 题（常见于几道题的图排在同一行）。"""
    for item in foreign:
        targets = paper.questions.filter(number=item["number"])
        if item.get("group_id") is not None:
            targets = targets.filter(group_id=item["group_id"])
        # 老数据可能没有题组。遇到同题号多于一张时宁可不猜，也不能把图跨章节贴错。
        if targets.count() != 1:
            continue
        target = targets.first()
        if target is None or any(f.get("source") == "manual" for f in target.figures):
            continue
        if _same_box(item, target.figures):
            continue
        previous_review = stored_or_derived_review(target)
        target.figures = target.figures + [{"slot": "stem", "page_idx": item["page_idx"], "bbox": item["bbox"],
                                            "source": "other"}]
        target.figure_review = recheck_automatic_review(
            stem=target.stem,
            options=target.options,
            figures=target.figures,
            previous=previous_review,
        )
        target.flags = _flags_after_figure_review(target.flags, target.figure_review, target.figures)
        if target.state in {Question.State.GREEN, Question.State.YELLOW}:
            target.state = Question.State.YELLOW if target.flags else Question.State.GREEN
        _invalidate_approval(target)
        target.save()


# ---------------------------------------------------------------- 总控

def process_paper(paper: Paper) -> None:
    try:
        if paper.status in (Paper.Status.QUEUED, Paper.Status.PARSING):
            parse(paper)
            paper.refresh_from_db()
        if paper.status == Paper.Status.SEGMENTING:
            segment_paper(paper)
            paper.refresh_from_db()
        if paper.status == Paper.Status.READING:
            pending = list(paper.questions.filter(state__in=[Question.State.WAITING, Question.State.READING]))
            read_questions(paper, pending)
            _set(paper, status=Paper.Status.READY)
    except Exception as error:
        logger.exception("paper failed")
        message = str(error) if isinstance(error, (MineruError, readers.ReaderError, RuntimeError)) else \
            f"处理出错：{type(error).__name__}"
        _set(paper, status=Paper.Status.FAILED, error=message[:500])


def process_rereads() -> int:
    """人工调整范围或点"重读"后的单题重读。"""
    count = 0
    for paper in Paper.objects.filter(questions__reread_requested=True).distinct():
        questions = list(paper.questions.filter(reread_requested=True))
        read_questions(paper, questions)
        count += len(questions)
    return count
