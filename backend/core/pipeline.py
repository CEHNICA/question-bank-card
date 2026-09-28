"""自动流水线：解析 → 切题 → 读题 → 待终审。全程无需人工干预。"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import threading
from collections import OrderedDict, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone
from PIL import Image

from . import imaging, import_planning, photos, readers, segment, textnorm
from .account_pool import AccountPoolError, account_pool
from .figure_policy import (
    BLOCKED_MISSING, CONFIRMED_NO_FIGURE, CONFLICT, FLAG_NO_FIGURE, FLAG_UNCUED_FIGURE,
    FLAG_UNFOUND_FIGURE, OK, automatic_review, figure_flag,
    candidate_key, missing_choice_figure_slots, recheck_automatic_review,
    resolve_automatic_figure_assignments, stored_or_derived_review,
    without_automatic_textbook_badges,
)
from .mineru import (
    MAX_PDF_PAGES, MineruError, load_blocks, request_extract_file_from_pool, write_pdf_slice,
)
from .models import Block, ImportChunk, Paper, Question, QuestionDeletionBatch, QuestionGroup
from .textnorm import same_reading
from .word import convert_docx_to_pdf

logger = logging.getLogger(__name__)
PARALLEL = readers._parallel_limit()
MINERU_HEARTBEAT_SECONDS = 5.0
FLAG_RESEGMENT_PRESERVED = "重新切题未再找到这张人工题卡，已保留；请核对题号与原卷范围"
FLAG_RESEGMENT_EXCLUDED = "重新切题未再找到这张自动题卡，已移入回收站；恢复后请对照原书核对"
FLAG_RESEGMENT_RANGE_PROTECTED = "新规则建议了不同原卷范围；这张题卡含人工修改、通过或入库记录，已保留原范围并标黄"
FLAG_MANUAL_FIGURE_OUTSIDE_RANGE = "重新切题后，原人工配图不在新的题目范围内；已保留记录并标黄，请重新确认配图"

# A textbook example is a question source, not a worked-solution archive.  Old
# cards may already contain the printed ``分析/解`` because earlier segmenters
# stopped only at the next card.  The label is strong enough to shorten the
# saved reading locally when the new crop is a strict prefix of the old one;
# no reader/model call is needed for that narrow migration.
_EXAMPLE_SOLUTION_TEXT_RE = re.compile(
    r"(?:^|\n|(?<=[。．.!?！？；;]))\s*(?:\*{1,2}\s*)?(?:[【\[]\s*)?"
    r"(?:分析|解析|解答?|证明|(?:解法|证法)(?:\s*[一二三四五六七八九十0-9]+)?|"
    r"Analysis|Solution|Proof)"
    r"\s*(?:[】\]])?\s*(?:[：:]|(?=\*{1,2})|$)\s*(?:\*{1,2})?",
    re.I | re.M,
)

# A reader sometimes calls a numbered list of statements a multiple-choice
# question even though the source contains no A--D choices at all.  When the
# independent reader calls the same content free response, use the visible
# structure instead of inventing four missing image options.  This is kept
# deliberately narrow so genuine image-choice questions are unaffected.
_NUMERIC_SUBQUESTION_RE = re.compile(r"(?:^|\n|\s)[（(]\s*(\d{1,2})\s*[)）]")
_EXPLICIT_OPTION_LABEL_RE = re.compile(r"(?:^|\n)\s*[A-DＡ-Ｄ]\s*[.．、:：)]", re.I)
_EXERCISE_ACTION_RE = re.compile(
    r"(?:请|试)?(?:求|证明|判断|写出|列举|画出|作出|选择|说明|回答|解答|计算|表示|分析)"
)
_DIRECT_EXERCISE_ACTION_RE = re.compile(
    r"(?:^|[\n。．.!！；;])\s*(?:请|试)?"
    r"(?:求|证明|判断|写出|列举|画出|作出|选择|说明|回答|解答|计算|表示|分析|"
    r"估计|猜想|举例)"
)
_YOU_CAN_EXERCISE_RE = re.compile(
    r"你(?:能|可以)[^。．.!！；;\n]{0,60}?"
    r"(?:绘制|画出|求|说明|探究|认识|比较|判断)"
)


def _invalidate_approval(question: Question) -> None:
    question.approved = False
    question.approved_at = None
    question.approved_content_hash = ""


def _normalise_unlabelled_numeric_choice_type(
    kind: str,
    *,
    final: dict,
    readings: list[dict | None],
    candidates: list[dict],
    figures: list[dict],
) -> str:
    """Treat a bare ``(1)(2)…`` list as free response when readers disagree.

    The rule consumes only the two readings already made.  It requires both a
    choice and a free-response opinion, no textual options, no candidate or
    bound images, the first two numeric item labels, and no printed A--D label.
    Consequently it cannot turn an ordinary or image-based choice question
    into free response merely because one model omitted an option.
    """

    if kind not in {"single_choice", "multiple_choice"}:
        return kind
    reading_types = {
        str(reading.get("type") or "unknown")
        for reading in readings
        if isinstance(reading, dict)
    }
    if not (reading_types & {"single_choice", "multiple_choice"}) \
            or "free_response" not in reading_types:
        return kind
    if final.get("options") or candidates or figures:
        return kind
    stem = str(final.get("stem") or "")
    labels = {int(value) for value in _NUMERIC_SUBQUESTION_RE.findall(stem)}
    if not {1, 2}.issubset(labels) or _EXPLICIT_OPTION_LABEL_RE.search(stem):
        return kind
    return "free_response"


def _has_strong_numbered_exercise_tasks(stem: str) -> bool:
    """Recognise an embedded multi-part task inside explanatory textbook text.

    Some exploration exercises begin with a paragraph of exposition.  A model
    may consequently call the whole crop prose even though the printed source
    anchor is an exercise and the tail contains explicit ``(1)``, ``(2)``
    instructions.  Two numbered parts plus two answer verbs are sufficiently
    specific to trust that structural anchor without another model call.
    """

    labels = {int(value) for value in _NUMERIC_SUBQUESTION_RE.findall(stem or "")}
    return {1, 2}.issubset(labels) and len(_EXERCISE_ACTION_RE.findall(stem or "")) >= 2


def _has_explicit_exercise_task(stem: str, options: dict | None, kind: str) -> bool:
    """Recognise a concrete response request without another model call.

    The source anchor still has to say this is an exercise/example.  We accept
    a question mark, actual choice options, a direct imperative at a sentence
    boundary, or the textbook's common ``你可以……绘制/探究`` wording.  A comma
    before ``求`` is deliberately not enough, so worked explanations such as
    ``在问题 1 中，求……就是计算……`` remain reviewable instead of becoming a
    question merely because they quote the original task.
    """

    text = str(stem or "")
    if "?" in text or "？" in text:
        return True
    if isinstance(options, dict) and bool(options):
        return True
    if str(kind or "unknown") in {"single_choice", "multiple_choice"}:
        return True
    return bool(
        _DIRECT_EXERCISE_ACTION_RE.search(text)
        or _YOU_CAN_EXERCISE_RE.search(text)
        or _has_strong_numbered_exercise_tasks(text)
    )


def _source_kind_has_question_support(
    *, expected_kind: str | None, stem: str, options: dict | None,
    kind: str, readings: list[dict],
) -> bool:
    """Return whether saved evidence supports the local example/exercise anchor."""

    if expected_kind not in {"exercise", "example"}:
        return False
    question_kinds = {"exercise", "example"}
    non_question_kinds = {"prose", "heading"}
    question_votes = sum(
        str(result.get("content_kind") or "unknown") in question_kinds
        for result in readings
    )
    non_question_votes = sum(
        str(result.get("content_kind") or "unknown") in non_question_kinds
        for result in readings
    )
    return (
        _has_explicit_exercise_task(stem, options, kind)
        or question_votes > non_question_votes
        or bool(question_votes and not non_question_votes)
    )


def _number_seen_flag(
    expected: int, readings: list[dict | None], *, clipped_number: bool = False,
) -> str | None:
    """Warn only when neither independent reader found the expected number.

    ``clipped_number`` marks a start the local rules recovered from a number
    whose leading digit was cut off by the scan (“9.” read as 19): a reader
    seeing 9 there confirms the repair rather than contradicting it.
    """

    seen_numbers = {
        value for result in readings
        if isinstance(result, dict)
        for value in [result.get("number_seen")]
        if isinstance(value, int) and not isinstance(value, bool)
    }
    if not seen_numbers or expected in seen_numbers:
        return None
    if clipped_number and all(
            value < expected and str(expected).endswith(str(value)) for value in seen_numbers):
        return None
    rendered = "、".join(str(value) for value in sorted(seen_numbers))
    return f"AI 看到的题号是 {rendered}，请确认"


def _content_kind_review_flag(
    *, source_kind: str, stem: str, options: dict | None,
    kind: str, readings: list[dict],
) -> str | None:
    """Return the current deterministic content-kind warning, if any."""

    content_kinds = {
        str(result.get("content_kind") or "unknown")
        for result in readings
        if str(result.get("content_kind") or "unknown") != "unknown"
    }
    expected_kind = {
        Question.SourceKind.EXAMPLE: "example",
        Question.SourceKind.EXERCISE: "exercise",
    }.get(source_kind)
    non_question_kinds = {"prose", "heading"}
    if _source_kind_has_question_support(
            expected_kind=expected_kind,
            stem=stem,
            options=options,
            kind=kind,
            readings=readings):
        content_kinds = {expected_kind}
    if expected_kind:
        conflicting = sorted(value for value in content_kinds if value in non_question_kinds)
        if conflicting:
            labels = {
                "example": "例题", "exercise": "练习题",
                "prose": "教材正文", "heading": "标题",
            }
            rendered = "、".join(labels.get(value, value) for value in conflicting)
            return f"本地版面规则判为{labels[expected_kind]}，但 AI 判为{rendered}，请对照原书确认"
        return None
    if content_kinds and content_kinds <= non_question_kinds:
        rendered = (
            "教材正文" if content_kinds == {"prose"}
            else "标题" if content_kinds == {"heading"}
            else "正文或标题"
        )
        return f"AI 判断这段更像{rendered}，请删除题卡或调整范围后再确认"
    if content_kinds & non_question_kinds:
        return "两次 AI 对这段是否为题目判断不一致，请对照原书确认"
    return None


def _local_text_review_flag(flag: str) -> bool:
    return (
        flag.startswith("本地版面规则判为")
        or flag.startswith("AI 判断这段更像")
        or flag.startswith("两次 AI 对这段是否为题目")
        or flag.startswith("AI 看到的题号是 ")
    )


def persist_local_text_review_upgrades(questions) -> dict[str, int]:
    """Persist local content/number policy upgrades without rereading cards."""

    stats = {"seen": 0, "updated": 0, "unchanged": 0, "state_skipped": 0,
             "edited_skipped": 0}
    iterator = questions.iterator() if hasattr(questions, "iterator") else iter(questions)
    for question in iterator:
        stats["seen"] += 1
        if str(getattr(question, "state", "") or "") not in {
                Question.State.GREEN, Question.State.YELLOW}:
            stats["state_skipped"] += 1
            continue
        if bool(getattr(question, "edited", False)):
            stats["edited_skipped"] += 1
            continue
        readings = [
            result for result in (
                getattr(question, "read_a", None),
                getattr(question, "read_b", None),
                getattr(question, "read_c", None),
            ) if isinstance(result, dict)
        ]
        flags = [
            str(flag) for flag in (getattr(question, "flags", None) or [])
            if not _local_text_review_flag(str(flag))
        ]
        content_flag = _content_kind_review_flag(
            source_kind=str(getattr(question, "source_kind", "") or ""),
            stem=str(getattr(question, "stem", "") or ""),
            options=getattr(question, "options", None) or {},
            kind=str(getattr(question, "question_type", "unknown") or "unknown"),
            readings=readings,
        )
        if content_flag:
            flags.append(content_flag)
        if number_flag := _number_seen_flag(
                int(getattr(question, "number", 0) or 0),
                [getattr(question, "read_a", None), getattr(question, "read_b", None)],
                clipped_number=getattr(question, "start_source", "") == "repaired"):
            flags.append(number_flag)
        state = Question.State.YELLOW if flags else Question.State.GREEN
        changed_fields: list[str] = []
        if flags != (getattr(question, "flags", None) or []):
            question.flags = flags
            changed_fields.append("flags")
        if state != getattr(question, "state", None):
            question.state = state
            changed_fields.append("state")
        if not changed_fields:
            stats["unchanged"] += 1
            continue
        question.save(update_fields=[*changed_fields, "updated_at"])
        stats["updated"] += 1
    return stats


_CHOICE_BRACKET = re.compile(r"[（(]\s*[）)]\s*[。．.]?\s*$")


def _row_as_choice_options(
    *, stem: str, options: dict, kind: str, candidates: list[dict], assignments: dict,
) -> dict[str, str]:
    """Four printed pictures in one row under a choice stem are options A–D.

    Readers sometimes call all four “题干” (seen on 下面四幅图中，不能证明勾股
    定理的是（ ）), which then asks the reviewer to box every option by hand.
    Only applies when no option has text and exactly four pictures that are not
    already option pictures sit side by side.
    """
    if any(str(value).strip() for value in (options or {}).values()):
        return assignments
    if kind not in {"single_choice", "multiple_choice"} and not _CHOICE_BRACKET.search(stem or ""):
        return assignments
    if any(role in readers.OPTION_KEYS for role in assignments.values()):
        return assignments
    loose = [item for item in candidates or []
             if assignments.get(item.get("label")) in {None, "stem", "none"} and item.get("bbox")]
    row = _single_row(loose) if len(loose) == 4 else None
    if row is None:
        return assignments
    updated = dict(assignments)
    for slot, item in zip(readers.OPTION_KEYS, row):
        updated[item["label"]] = slot
    return updated


def _resolve_automatic_figure_assignments(
    *, stem: str, options: dict, kind: str, candidates: list[dict], assignments: dict,
) -> dict[str, str]:
    """Apply deterministic local evidence before trusting model image labels.

    A repeated tiny left-margin section badge is decoration even when a reader
    loosely called it the stem image.  Conversely, ``recovered_input`` is an
    image the segmenter deliberately pulled into a question whose text refers
    to a supplied visual; it must not be discarded merely because the reader
    selected the nearby badge instead.  Manual figures never pass through this
    automatic path.
    """

    return resolve_automatic_figure_assignments(
        stem=stem,
        options=options,
        kind=kind,
        candidates=candidates,
        assignments=_row_as_choice_options(
            stem=stem, options=options, kind=kind, candidates=candidates, assignments=assignments,
        ),
    )


def _flags_after_figure_review(flags: list[str], review: dict, figures: list[dict]) -> list[str]:
    """Replace only figure-policy flags; preserve all unrelated review warnings."""
    result = [
        flag for flag in (flags or [])
        if not figure_flag(flag) and flag != FLAG_MANUAL_FIGURE_OUTSIDE_RANGE
    ]
    if review.get("status") == BLOCKED_MISSING:
        result.append(FLAG_NO_FIGURE if review.get("cue_matches") and not figures else FLAG_UNFOUND_FIGURE)
    elif review.get("status") == CONFLICT:
        signals = review.get("signals") or []
        if "manual_figure_outside_range" in signals:
            result.append(FLAG_MANUAL_FIGURE_OUTSIDE_RANGE)
        else:
            result.append(FLAG_UNFOUND_FIGURE if "candidate_unclassified" in signals
                          else FLAG_UNCUED_FIGURE)
    return result


def _manual_figures_and_review(question: Question) -> tuple[list[dict], dict]:
    """Keep human figure choices while discarding stale candidate identities."""

    valid_keys = {
        key for item in (question.figure_candidates or [])
        if (key := candidate_key(item)) is not None
    }
    figures: list[dict] = []
    previous = question.figure_review if isinstance(question.figure_review, dict) else {}
    detached: list[dict] = [
        dict(item) for item in (previous.get("detached_manual_figures") or [])
        if isinstance(item, dict)
    ]
    for item in (question.figures or []):
        if not isinstance(item, dict) or item.get("source") != "manual":
            continue
        kept = dict(item)
        if isinstance(kept.get("page_idx"), int) and isinstance(kept.get("bbox"), list) \
                and not segment.center_in_regions(
                    kept["page_idx"], kept["bbox"], question.regions or [],
                ):
            if kept not in detached:
                detached.append(kept)
            continue
        if kept.get("candidate_key") not in valid_keys:
            kept.pop("candidate_key", None)
        figures.append(kept)
    ignored = sorted({
        value for value in previous.get("ignored_candidates", [])
        if isinstance(value, str) and value in valid_keys
    }) if isinstance(previous.get("ignored_candidates"), list) else []
    review = {
        "status": CONFLICT if detached else OK,
        "source": "human",
        "reason": ("重新切题后有人工配图落在新题目范围之外，请重新确认配图"
                   if detached else "配图已经由人工设置"),
        "signals": (["manual_figure", "manual_figure_outside_range"]
                    if detached else ["manual_figure"]),
        "cue_matches": list(previous.get("cue_matches") or []),
        "excluded_count": len(ignored),
        "ignored_candidates": ignored,
    }
    if isinstance(previous.get("confirmed_at"), str):
        review["confirmed_at"] = previous["confirmed_at"]
    if detached:
        review["detached_manual_figures"] = detached
    return figures, review


def _drop_stale_automatic_figures(question: Question, candidates: list[dict]) -> bool:
    """Remove automatic crops that no longer belong to this card.

    Re-segmentation can deterministically move a captioned textbook image to
    the following question without changing either question's text range.  In
    that case keeping an older automatically bound crop would leave the old
    card visibly wrong even though its candidate list is now correct.  Human
    decisions remain authoritative: manual crops, borrowed ``other`` crops and
    an explicit human review are never changed here.
    """

    review = question.figure_review if isinstance(question.figure_review, dict) else {}
    if review.get("source") == "human" or any(
            isinstance(item, dict) and item.get("source") == "manual"
            for item in (question.figures or [])):
        return False
    valid_keys = {
        key for candidate in candidates
        if (key := candidate_key(candidate)) is not None
    }
    kept: list[dict] = []
    changed = False
    for figure in (question.figures or []):
        if not isinstance(figure, dict):
            changed = True
            continue
        if figure.get("source") in {"other", "row"} or candidate_key(figure) in valid_keys:
            kept.append(figure)
        else:
            changed = True
    if changed:
        question.figures = kept
    return changed


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


class ReadOnlyPageStore:
    """Render pages in memory for a dry-run without creating cache files."""

    MAX_MEMORY_PAGES = 4

    def __init__(self, paper: Paper):
        self.paper = paper
        self.memory: OrderedDict[int, Image.Image] = OrderedDict()

    def load(self, page_idx: int) -> Image.Image:
        if page_idx in self.memory:
            self.memory.move_to_end(page_idx)
            return self.memory[page_idx]
        source, kind = render_source(self.paper)
        image = imaging.render_source_page(source, kind, page_idx).convert("RGB")
        self.memory[page_idx] = image
        while len(self.memory) > self.MAX_MEMORY_PAGES:
            self.memory.popitem(last=False)
        return image


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
    scopes = (
        segment.book_numbering_scopes(paper.pages, blocks)
        if paper.material_type == Paper.MaterialType.BOOK
        else segment.numbering_scopes(paper.pages, blocks)
    )
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


def _question_group_specs(paper: Paper, structure: dict | None = None) -> list[dict]:
    """Return the current confirmed/suggested scopes in a model-ready form."""
    structure = structure if structure is not None else (paper.structure or {})
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
                **({
                    "scope_anchor_seq": scope.get("scope_anchor_seq"),
                    "source_kind": scope.get("source_kind"),
                    "scope_version": 1,
                } if isinstance(scope, dict) and scope.get("scope_anchor_seq") is not None else {}),
            },
        })
    if not specs:
        raise RuntimeError("没有可用于切题的页面组")
    return specs


def _planned_book_resegment_structure(paper: Paper, blocks: list[dict]) -> dict:
    """Build the current typed textbook scope plan without writing it.

    Older versions stored exam-style numbering scopes for books.  Reusing
    their ``seq_start``/``seq_end`` bounds can hide examples that sit just
    outside a numbered exercise.  A resegment therefore plans from every
    MinerU block in the book and only applies this structure after the user has
    reviewed the read-only preview.
    """
    scopes = segment.book_numbering_scopes(paper.pages, blocks)
    if not scopes:
        raise RuntimeError("新教材规则没有找到可用的例题或练习，未执行任何改动")
    structure, _needs_confirmation = _plan_structure(paper, blocks)
    page_groups = [scope["pages"] for scope in scopes]
    structure["suggested_groups"] = page_groups
    structure["suggested_scopes"] = scopes
    if structure.get("confirmed"):
        structure["confirmed_groups"] = page_groups
        structure["confirmed_scopes"] = scopes
    structure["groups_need_rebuild"] = True
    return structure


def _prospective_book_groups(paper: Paper, blocks: list[dict]) -> tuple[list[QuestionGroup], dict]:
    """Return unsaved groups for preview plus the structure apply will use."""
    structure = _planned_book_resegment_structure(paper, blocks)
    groups = [
        QuestionGroup(id=-(index + 1), paper=paper, **spec)
        for index, spec in enumerate(_question_group_specs(paper, structure))
    ]
    return groups, structure


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
            wanted_anchor = spec["metadata"].get("scope_anchor_seq")
            wanted_kind = spec["metadata"].get("source_kind")
            match = next((
                group for group in unused
                if wanted_anchor is not None
                and (group.metadata or {}).get("scope_anchor_seq") == wanted_anchor
                and (group.metadata or {}).get("source_kind") == wanted_kind
            ), None)
            if match is None:
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
                group.kind = spec["kind"]
                group.page_start = spec["page_start"]
                group.page_end = spec["page_end"]
                group.metadata = metadata
                # Keep a human/previous source title on a matched group. Only a
                # newly created group needs the generated title/kind.
                group.save(update_fields=[
                    "sequence", "kind", "page_start", "page_end", "metadata", "updated_at",
                ])
            desired.append(group)

        desired_pages = {group.pk: set(_group_pages(group)) for group in desired}
        changed_questions: list[Question] = []
        for question in Question.all_objects.filter(paper=paper):
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
            Question.all_objects.bulk_update(changed_questions, ["group"])

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
        for question in Question.all_objects.filter(paper=paper):
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


def _normalise_source_kind(item: dict) -> str:
    raw = item.get("source_kind") or (item.get("start") or {}).get("source_kind")
    if (item.get("start") or {}).get("source") == "manual":
        return Question.SourceKind.MANUAL
    if raw in {Question.SourceKind.EXAMPLE, Question.SourceKind.EXERCISE,
               Question.SourceKind.MANUAL, Question.SourceKind.UNKNOWN}:
        return raw
    # The generic exam path calls ordinary starts ``question``; the persisted
    # schema deliberately uses ``unknown`` so future classifiers can improve it
    # without pretending an inference was certain.
    return Question.SourceKind.UNKNOWN


def _source_anchor(item: dict) -> int | None:
    value = item.get("source_anchor_seq")
    if value is None:
        value = (item.get("start") or {}).get("source_anchor_seq")
    return value if type(value) is int and value >= 0 else None


def _question_range_is_human_protected(question: Question) -> bool:
    """Whether automatic re-segmentation may replace the text/source crop.

    Figure confirmation is deliberately not a range edit.  A human can select
    a picture or confirm no picture without freezing an unrelated, stale text
    crop forever.  Explicit source ranges, edited text, approvals and
    publications remain strict barriers.
    """

    return bool(
        question.start_source == "manual"
        or question.source_kind == Question.SourceKind.MANUAL
        # Legacy fixtures and migrated cards can have an empty regions_auto even
        # though their visible range was produced automatically.  Only a
        # non-empty automatic baseline can prove that a user moved the range.
        or (question.regions_auto and question.regions != question.regions_auto)
        or question.edited
        or question.text_source == "human"
        or question.approved
        or question.publications.exists()
    )


def _question_is_human_protected(question: Question) -> bool:
    """Broader protection used when a whole card would leave the source set."""

    review = question.figure_review if isinstance(question.figure_review, dict) else {}
    return bool(
        _question_range_is_human_protected(question)
        or any(isinstance(figure, dict) and figure.get("source") == "manual"
               for figure in (question.figures or []))
        or review.get("source") == "human"
    )


def _trim_example_solution_text(value: object) -> tuple[str, bool]:
    """Return the printed question text before an explicit solution label."""

    text = str(value or "")
    matches = list(_EXAMPLE_SOLUTION_TEXT_RE.finditer(text))
    if not matches:
        return text.strip(), False
    match = matches[0]
    # ``例 3 证明：……`` is a proof task, not an empty question followed by a
    # solution.  Readers omit the leading ``例 3`` and therefore the saved stem
    # can begin with ``证明：``; in a worked example the actual proof heading
    # appears a second time after the statements to prove.  Preserve the first
    # imperative label and cut at the second one.  With no second label there
    # is no text-only proof that anything should be removed.
    if not text[:match.start()].strip():
        if "证明" in match.group(0) and len(matches) > 1:
            match = matches[1]
        else:
            # Never turn a card into an empty string.  A leading answer label
            # with no preceding question is evidence of a bad source/read,
            # not proof that the entire saved card may be discarded locally.
            return text.strip(), False
    return text[:match.start()].rstrip(), True


def _saved_example_solution_text_present(question: Question) -> bool:
    values = [question.stem]
    values.extend(
        reading.get("stem", "")
        for reading in (question.read_a, question.read_b, question.read_c)
        if isinstance(reading, dict)
    )
    return any(_trim_example_solution_text(value)[1] for value in values)


def _strict_prefix_regions(old: list[dict], new: list[dict], *, tolerance: float = 4.0) -> bool:
    """Prove that ``new`` only removes the tail of an existing source crop."""

    if not old or not new or len(new) > len(old):
        return False
    shortened = len(new) < len(old)
    for index, fresh in enumerate(new):
        previous = old[index] if index < len(old) else {}
        if fresh.get("page_idx") != previous.get("page_idx"):
            return False
        fresh_box, old_box = fresh.get("bbox"), previous.get("bbox")
        if not (isinstance(fresh_box, list) and isinstance(old_box, list)
                and len(fresh_box) == len(old_box) == 4):
            return False
        try:
            if any(abs(float(fresh_box[pos]) - float(old_box[pos])) > tolerance
                   for pos in (0, 1, 2)):
                return False
            if float(fresh_box[3]) > float(old_box[3]) + tolerance:
                return False
            shortened = shortened or float(fresh_box[3]) < float(old_box[3]) - tolerance
        except (TypeError, ValueError):
            return False
    return shortened


def _regions_contained_in(old: list[dict], new: list[dict], *, tolerance: float = 4.0) -> bool:
    """Prove every new crop is spatially contained by an existing crop."""

    if not old or not new:
        return False
    for fresh in new:
        bbox = fresh.get("bbox") if isinstance(fresh, dict) else None
        if not isinstance(bbox, list) or len(bbox) != 4:
            return False
        contained = False
        for previous in old:
            old_box = previous.get("bbox") if isinstance(previous, dict) else None
            if previous.get("page_idx") != fresh.get("page_idx") \
                    or not isinstance(old_box, list) or len(old_box) != 4:
                continue
            try:
                contained = (
                    float(old_box[0]) - tolerance <= float(bbox[0])
                    and float(old_box[1]) - tolerance <= float(bbox[1])
                    and float(old_box[2]) + tolerance >= float(bbox[2])
                    and float(old_box[3]) + tolerance >= float(bbox[3])
                )
            except (TypeError, ValueError):
                return False
            if contained:
                break
        if not contained:
            return False
    return True


def _solution_only_shortening(question: Question, item: dict, blocks: list[dict]) -> bool:
    """Whether an example can be shortened locally without another AI read."""

    metadata = item.get("segmentation") if isinstance(item.get("segmentation"), dict) else {}
    boundary_seq = metadata.get("solution_boundary_seq")
    if not (
        item.get("source_kind") == Question.SourceKind.EXAMPLE
        and question.source_kind == Question.SourceKind.EXAMPLE
        and metadata.get("solution_trimmed") is True
        and type(boundary_seq) is int
        and question.source_anchor_seq is not None
        and question.source_anchor_seq == item.get("source_anchor_seq")
        and question.state in {Question.State.GREEN, Question.State.YELLOW}
        and str(question.stem or "").strip()
    ):
        return False
    if question.regions == (item.get("regions") or []):
        old_candidate_keys = {
            key for candidate in (question.figure_candidates or [])
            if (key := candidate_key(candidate)) is not None
        }
        new_candidate_keys = {
            key for candidate in (item.get("figure_candidates") or [])
            if (key := candidate_key(candidate)) is not None
        }
        return _saved_example_solution_text_present(question) \
            or old_candidate_keys != new_candidate_keys
    new_regions = item.get("regions") or []
    recovered_count = len(metadata.get("recovered_input_figure_seqs") or [])
    core_regions = new_regions[:-recovered_count] if recovered_count else new_regions
    # A previous version may already have removed the answer text but not yet
    # recovered a numbered input figure printed beside the solution.  Adding
    # only those explicitly marked image crops is the same deterministic fix.
    if recovered_count and question.regions == core_regions:
        return True
    if not (_strict_prefix_regions(question.regions, new_regions)
            or _regions_contained_in(question.regions, new_regions)):
        return False
    old_blocks = segment.text_blocks_in(blocks, question.regions)
    new_blocks = segment.text_blocks_in(blocks, item.get("regions") or [])
    # The source solution label must really belong to the old crop, and the new
    # crop must not introduce any content.  This prevents an unrelated layout
    # change from being mistaken for the safe local migration.
    return boundary_seq in old_blocks and new_blocks.issubset(old_blocks)


def _remap_trimmed_reading(
    reading: object,
    *,
    old_candidates: list[dict],
    new_candidates: list[dict],
) -> dict:
    """Trim one saved reader result and remap surviving candidate labels."""

    if not isinstance(reading, dict):
        return {}
    result = dict(reading)
    result["stem"], _changed = _trim_example_solution_text(result.get("stem", ""))
    old_label_to_key = {
        str(candidate.get("label")): key
        for candidate in old_candidates
        if isinstance(candidate, dict) and candidate.get("label") is not None
        and (key := candidate_key(candidate)) is not None
    }
    new_key_to_label = {
        key: str(candidate.get("label"))
        for candidate in new_candidates
        if isinstance(candidate, dict) and candidate.get("label") is not None
        and (key := candidate_key(candidate)) is not None
    }
    assignments: dict[str, str] = {}
    for old_label, role in (reading.get("figures") or {}).items():
        key = old_label_to_key.get(str(old_label))
        if key in new_key_to_label:
            assignments[new_key_to_label[key]] = role
    for candidate in new_candidates:
        if candidate.get("recovered_input") is True and candidate.get("label") is not None:
            assignments[str(candidate["label"])] = "stem"
    result["figures"] = assignments
    remaining_slots = {
        role for role in assignments.values()
        if role == "stem" or role in readers.OPTION_KEYS
    }
    result["figure_descriptions"] = [
        slot for slot in (reading.get("figure_descriptions") or [])
        if slot in remaining_slots
    ]
    # A missing-image answer derived from the removed solution is stale.  A
    # genuine remaining cue is rediscovered below by the deterministic figure
    # policy, while image-choice completeness is checked from the kept options.
    result["missing_figure"] = False
    options = result.get("options") if isinstance(result.get("options"), dict) else {}
    result["unclear"] = "[?]" in result["stem"] or any(
        "[?]" in str(value) for value in options.values()
    )
    return result


def _apply_local_solution_shortening(
    question: Question,
    item: dict,
    *,
    group: QuestionGroup,
    regions: list[dict],
    candidates: list[dict],
) -> None:
    """Apply a proven solution-tail removal without changing reader latency."""

    old_candidates = list(question.figure_candidates or [])
    question.group = group
    question.regions = regions
    question.regions_auto = regions
    question.section = item.get("section", "")[:120]
    question.start_source = (item.get("start") or {}).get("source", question.start_source)
    question.source_kind = item["source_kind"]
    question.source_anchor_seq = item["source_anchor_seq"]
    question.figure_candidates = candidates
    question.read_a = _remap_trimmed_reading(
        question.read_a, old_candidates=old_candidates, new_candidates=candidates,
    )
    question.read_b = _remap_trimmed_reading(
        question.read_b, old_candidates=old_candidates, new_candidates=candidates,
    )
    question.read_c = _remap_trimmed_reading(
        question.read_c, old_candidates=old_candidates, new_candidates=candidates,
    )
    question.stem, _changed = _trim_example_solution_text(question.stem)
    question.answer = ""
    question.analysis = ""
    valid_candidate_keys = {
        key for candidate in candidates
        if (key := candidate_key(candidate)) is not None
    }
    manual_before = [
        dict(figure) for figure in (question.figures or [])
        if isinstance(figure, dict) and figure.get("source") == "manual"
    ]
    question.figures = [
        figure for figure in (question.figures or [])
        if isinstance(figure, dict)
        and isinstance(figure.get("page_idx"), int)
        and isinstance(figure.get("bbox"), list)
        and (
            candidate_key(figure) in valid_candidate_keys
            or (
                figure.get("source") == "manual"
                and segment.center_in_regions(figure["page_idx"], figure["bbox"], regions)
            )
            or (
                figure.get("source") in {"other", "row"}
                and segment.center_in_regions(figure["page_idx"], figure["bbox"], regions)
            )
        )
    ]
    detached_manual = [
        figure for figure in manual_before
        if isinstance(figure.get("page_idx"), int)
        and isinstance(figure.get("bbox"), list)
        and not segment.center_in_regions(figure["page_idx"], figure["bbox"], regions)
    ]
    if detached_manual:
        question.figure_review = {
            "status": CONFLICT,
            "source": "human",
            "reason": "重新切题后有人工配图落在新题目范围之外，请重新确认配图",
            "signals": ["manual_figure_outside_range"],
            "detached_manual_figures": detached_manual,
        }
    existing_figure_keys = {
        key for figure in question.figures
        if (key := candidate_key(figure)) is not None
    }
    for candidate in candidates:
        key = candidate_key(candidate)
        if candidate.get("recovered_input") is True and key is not None \
                and key not in existing_figure_keys:
            question.figures.append({
                "slot": "stem",
                "page_idx": candidate["page_idx"],
                "bbox": list(candidate["bbox"]),
                "source": "auto",
            })
            existing_figure_keys.add(key)
    flags = list(question.flags or [])
    if question.read_a and question.read_b and same_reading(question.read_a, question.read_b):
        flags = [flag for flag in flags if not str(flag).startswith("两次识读不一致")]
    if "[?]" not in question.stem and not any(
            "[?]" in str(value) for value in (question.options or {}).values()):
        flags = [flag for flag in flags if "有看不清的字" not in str(flag)]
    # Rebuild the decision from the shortened source.  A surviving manual crop
    # remains a manual choice; confirming a picture must not freeze the text
    # range, but neither should a safe range trim silently discard it.
    if any(figure.get("source") == "manual" for figure in question.figures):
        question.figures, review = _manual_figures_and_review(question)
    elif detached_manual:
        review = question.figure_review
    else:
        question.figure_review = {}
        review = stored_or_derived_review(question)
    question.figure_review = review
    question.flags = _flags_after_figure_review(flags, review, question.figures)
    question.state = Question.State.YELLOW if question.flags else Question.State.GREEN
    question.error = ""
    question.reread_requested = False
    _invalidate_approval(question)
    question.save()


def _collect_segmentation_items(
    paper: Paper,
    groups: list[QuestionGroup],
    *,
    locate_gaps: bool,
    page_store: PageStore | ReadOnlyPageStore,
) -> tuple[list[dict], list[dict], list[str], list[dict]]:
    """Build the desired card set.

    With ``locate_gaps=False`` this is a deterministic, read-only computation:
    it never invokes a model and the supplied ReadOnlyPageStore never writes a
    page cache.  The returned diagnostics make that limitation visible.
    """
    blocks = _block_dicts(paper)
    notes: list[str] = []
    diagnostics: list[dict] = []
    questions: list[dict] = []

    if paper.material_type == Paper.MaterialType.BOOK:
        # A book must be analysed as one continuous source.  Legacy versions
        # split books with exam-number ranges; those old seq bounds can cut off
        # the example immediately before/after an exercise.  The prospective
        # typed scopes below are used only to assign each full-book result.
        layout, starts = segment.analyse_book(paper.pages, blocks)
        items = segment.build_book_questions(layout, starts, blocks)
        seen_anchors: set[int] = set()
        for item in items:
            anchor = _source_anchor(item)
            if anchor is None or anchor in seen_anchors:
                raise RuntimeError("教材题源锚点不完整或重复，已停止重新切题")
            seen_anchors.add(anchor)
            matching_groups = []
            for group in groups:
                metadata = group.metadata or {}
                seq_start, seq_end = metadata.get("seq_start"), metadata.get("seq_end")
                if (seq_start is None or anchor >= seq_start) and (seq_end is None or anchor <= seq_end):
                    matching_groups.append(group)
            if len(matching_groups) != 1:
                raise RuntimeError(
                    f"教材题组规划没有唯一覆盖来源锚点 {anchor}，已停止重新切题"
                )
            regions = imaging.trim_regions(item.get("regions") or [], page_store.load)
            questions.append({
                **item,
                "group": matching_groups[0],
                "regions": regions,
                "source_kind": _normalise_source_kind(item),
                "source_anchor_seq": anchor,
            })
        if len(questions) != len(starts):
            raise RuntimeError("教材全书切题结果与来源锚点数量不一致，已停止重新切题")
        return blocks, questions, notes, diagnostics

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
            diagnostics.append({
                "kind": "empty_group", "group_id": group.pk, "group": group.title,
                "message": "这个题组没有可用于切题的页面或 MinerU 内容块。",
            })
            continue
        layout, starts = segment.analyse(group_pages, group_blocks)
        starts, leading = segment.repair_leading_question(layout, starts, group_blocks)
        if leading.message:
            notes.append(f"{group.title}：{leading.message}" if len(groups) > 1 else leading.message)
        missing = segment.missing_numbers(starts)
        if locate_gaps:
            group_notes = locate_missing(paper, layout, starts, page_store)
            notes.extend([f"{group.title}：{note}" if len(groups) > 1 else note
                          for note in group_notes])
        elif missing:
            diagnostics.append({
                "kind": "unlocated_gaps",
                "group_id": group.pk,
                "group": group.title,
                "numbers": [number for number, _previous in missing],
                "message": "预演不会调用模型定位缺号；正式执行时这些缺号仍会按现行规则处理。",
            })
        items = segment.build_questions(layout, starts, group_blocks)
        for item in items:
            regions = imaging.trim_regions(item.get("regions") or [], page_store.load)
            prepared = {
                **item,
                "group": group,
                "regions": regions,
                "source_kind": _normalise_source_kind(item),
                "source_anchor_seq": _source_anchor(item),
            }
            questions.append(prepared)
    return blocks, questions, notes, diagnostics


def _match_segmentation_items(
    existing_questions: list[Question],
    desired: list[dict],
    groups: list[QuestionGroup],
) -> tuple[list[tuple[dict, Question | None]], list[Question]]:
    """Pair desired slices to stable source anchors, then fall back to position.

    Textbooks can contain many ``例 1`` and ``练习 1`` cards.  A MinerU block
    sequence is therefore preferred over the display number; nearest-position
    matching remains as a migration/legacy fallback.
    """
    default_group_id = groups[0].pk if len(groups) == 1 else None
    used: set[int] = set()

    def group_id(question: Question):
        return question.group_id or default_group_id

    def pick(item: dict, candidates: list[Question]) -> Question | None:
        available = [question for question in candidates if question.pk not in used]
        if not available:
            return None
        position = _region_position(item.get("regions"))
        return min(available, key=lambda question: (
            abs(_region_position(question.regions) - position),
            question.deleted_at is not None,
            question.pk,
        ))

    pairs: list[tuple[dict, Question | None]] = []
    for item in desired:
        wanted_group = item["group"].pk
        anchor = item.get("source_anchor_seq")
        kind = item.get("source_kind") or Question.SourceKind.UNKNOWN
        typed_book_anchor = kind in {
            Question.SourceKind.EXAMPLE, Question.SourceKind.EXERCISE,
        }
        question = None
        if anchor is not None:
            exact = [
                candidate for candidate in existing_questions
                if (typed_book_anchor or group_id(candidate) == wanted_group)
                and candidate.source_anchor_seq == anchor
                and candidate.source_kind == kind
            ]
            question = pick(item, exact)
            if question is None:
                anchored = [
                    candidate for candidate in existing_questions
                    if (typed_book_anchor or group_id(candidate) == wanted_group)
                    and candidate.source_anchor_seq == anchor
                ]
                question = pick(item, anchored)
        if question is None and (anchor is None or kind in {
                Question.SourceKind.UNKNOWN, Question.SourceKind.MANUAL}):
            numbered = [
                candidate for candidate in existing_questions
                if group_id(candidate) == wanted_group and candidate.number == item["number"]
            ]
            question = pick(item, numbered)
        if question is not None:
            used.add(question.pk)
        pairs.append((item, question))
    return pairs, [question for question in existing_questions if question.pk not in used]


def _resegment_item_json(item: dict, question: Question | None = None, *, reason: str = "") -> dict:
    pages = sorted({region["page_idx"] + 1 for region in item.get("regions") or []})
    group = item.get("group") if item else getattr(question, "group", None)
    return {
        "question_id": question.pk if question is not None else None,
        "number": item.get("number") if item else question.number,
        "group_id": group.pk if group is not None else None,
        "group": group.title if group is not None else "",
        "source_kind": item.get("source_kind") if item else question.source_kind,
        "source_anchor_seq": item.get("source_anchor_seq") if item else question.source_anchor_seq,
        "pages": pages if item else sorted({region["page_idx"] + 1 for region in question.regions or []}),
        "reason": reason,
    }


def preview_resegment(paper: Paper) -> dict:
    """Return an exact, database-read-only segmentation comparison."""
    if paper.material_type == Paper.MaterialType.BOOK:
        groups, _planned_structure = _prospective_book_groups(paper, _block_dicts(paper))
    else:
        groups = list(paper.question_groups.order_by("sequence", "id"))
        if not groups:
            raise RuntimeError("这项任务没有稳定题组，暂时不能预演重新切题")
        if (paper.structure or {}).get("groups_need_rebuild"):
            raise RuntimeError("题组结构仍待更新，请先确认资料结构")
    blocks, desired, notes, diagnostics = _collect_segmentation_items(
        paper, groups, locate_gaps=False, page_store=ReadOnlyPageStore(paper),
    )
    if not desired:
        raise RuntimeError("新规则没有找到任何题目，未执行任何改动")
    existing = list(
        Question.all_objects.filter(paper=paper).select_related("group").prefetch_related("publications")
    )
    pairs, unmatched = _match_segmentation_items(existing, desired, groups)
    categories: dict[str, list[dict]] = {
        "added": [], "kept": [], "locally_trimmed": [], "range_changed": [],
        "suspected_excluded": [], "protected_unmatched": [], "too_long": [],
    }
    for item, question in pairs:
        flags = list(item.get("segmentation_flags") or [])
        if flags:
            categories["too_long"].append(_resegment_item_json(
                item, question, reason="；".join(flags),
            ))
        if question is None:
            categories["added"].append(_resegment_item_json(item, reason="新规则找到的新题卡"))
            continue
        if (question.start_source == "manual"
                or (question.regions_auto and question.regions != question.regions_auto)):
            categories["kept"].append(_resegment_item_json(
                item, question, reason="人工范围保持不变",
            ))
            continue
        regions_changed = question.regions != item["regions"]
        text_changed = segment.text_blocks_in(blocks, question.regions) != \
            segment.text_blocks_in(blocks, item["regions"])
        solution_text_changed = bool(
            isinstance(item.get("segmentation"), dict)
            and item["segmentation"].get("solution_trimmed") is True
            and _saved_example_solution_text_present(question)
        )
        solution_local_change = _solution_only_shortening(question, item, blocks)
        if (regions_changed or text_changed or solution_text_changed or solution_local_change) \
                and _question_range_is_human_protected(question):
            categories["protected_unmatched"].append(_resegment_item_json(
                item, question,
                reason="新规则建议不同范围；现有人工修改、通过或入库记录将保留原范围并标黄",
            ))
            continue
        if solution_local_change:
            categories["locally_trimmed"].append(_resegment_item_json(
                item, question, reason="例题仅去除分析/解答尾部；本地更新，不调用识读模型",
            ))
            continue
        if regions_changed or text_changed or question.state == Question.State.RED:
            categories["range_changed"].append(_resegment_item_json(
                item, question, reason="原卷范围或范围内文字块发生变化，需要重新识读",
            ))
        else:
            categories["kept"].append(_resegment_item_json(
                item, question,
                reason=("来源锚点和原卷范围已经一致；执行时将清理陈旧的范围冲突提示"
                        if FLAG_RESEGMENT_RANGE_PROTECTED in (question.flags or [])
                        else "来源锚点和原卷范围保持不变"),
            ))
    for question in unmatched:
        item = {
            "number": question.number,
            "group": question.group,
            "source_kind": question.source_kind,
            "source_anchor_seq": question.source_anchor_seq,
            "regions": question.regions,
        }
        if _question_is_human_protected(question):
            categories["protected_unmatched"].append(_resegment_item_json(
                item, question, reason="含人工修改、审批或入库记录，将保留并标黄",
            ))
        else:
            categories["suspected_excluded"].append(_resegment_item_json(
                item, question, reason="新规则未再命中，将移入可恢复的回收站",
            ))
    return {
        "read_only": True,
        "model_calls": 0,
        "summary": {key: len(value) for key, value in categories.items()},
        "items": categories,
        "notes": notes,
        "diagnostics": diagnostics,
    }


def segment_paper(paper: Paper) -> None:
    """切题。已有题卡时（重新切题）：内容没变的题卡原样保留（包括已通过的），变了的才重读；
    人工调整过范围或手动补的题卡不动。"""
    planned_structure: dict | None = None
    if paper.material_type == Paper.MaterialType.BOOK:
        groups, planned_structure = _prospective_book_groups(paper, _block_dicts(paper))
    else:
        groups = _ensure_question_groups(paper)
    blocks, questions, notes, _diagnostics = _collect_segmentation_items(
        paper, groups, locate_gaps=True, page_store=PageStore(paper),
    )
    if not questions:
        raise RuntimeError("没有在试卷里找到印刷题号，无法切题")
    existing_questions = list(
        Question.all_objects.filter(paper=paper).select_related("group").prefetch_related("publications")
    )
    pairs, unmatched = _match_segmentation_items(existing_questions, questions, groups)
    kept = reread = locally_trimmed = preserved = excluded = 0
    with transaction.atomic():
        if planned_structure is not None:
            # Plan, group reconciliation, card migration and final status form
            # one transaction.  If any later safeguard fails, the old 99-style
            # groups and cards remain intact together.
            paper = Paper.objects.select_for_update().get(pk=paper.pk)
            paper.structure = planned_structure
            groups = _ensure_question_groups(paper)
            real_by_sequence = {group.sequence: group for group in groups}
            if len(real_by_sequence) != len(groups):
                raise RuntimeError("教材题组序号不唯一，已停止重新切题")
            for item, _question in pairs:
                sequence = item["group"].sequence
                if sequence not in real_by_sequence:
                    raise RuntimeError("教材题组重建不完整，已停止重新切题")
                item["group"] = real_by_sequence[sequence]
        for item, question in pairs:
            group = item["group"]
            regions = item["regions"]
            candidates = _label_candidates(item["figure_candidates"])
            if question is None:
                Question.objects.create(
                    paper=paper, group=group, number=item["number"], section=item["section"][:120],
                    question_type=item["question_type"], regions=regions, regions_auto=regions,
                    start_source=item["start"]["source"], figure_candidates=candidates,
                    source_kind=item["source_kind"], source_anchor_seq=item["source_anchor_seq"],
                    flags=list(item.get("segmentation_flags") or []),
                )
                continue
            if question.deleted_at is not None:
                continue
            group_changed = question.group_id != group.id
            if group_changed:
                question.group = group
            if question.start_source == "manual" or (
                    question.regions_auto and question.regions != question.regions_auto):
                fields = []
                if group_changed:
                    fields.append("group")
                if question.source_kind != Question.SourceKind.MANUAL:
                    question.source_kind = Question.SourceKind.MANUAL
                    fields.append("source_kind")
                if fields:
                    question.save(update_fields=[*fields, "updated_at"])
                continue  # 人工框的范围优先
            regions_changed = question.regions != regions
            text_changed = segment.text_blocks_in(blocks, question.regions) != \
                segment.text_blocks_in(blocks, regions)
            solution_text_changed = bool(
                isinstance(item.get("segmentation"), dict)
                and item["segmentation"].get("solution_trimmed") is True
                and _saved_example_solution_text_present(question)
            )
            solution_local_change = _solution_only_shortening(question, item, blocks)
            if (regions_changed or text_changed or solution_text_changed or solution_local_change) \
                    and _question_range_is_human_protected(question):
                # A proposed crop is not allowed to overwrite the source range
                # a human already edited/approved or that backs a publication.
                # Keep the complete card, move only its stable scope identity,
                # and require another human look.
                question.group = group
                question.source_kind = item["source_kind"]
                question.source_anchor_seq = item["source_anchor_seq"]
                question.state = Question.State.YELLOW
                question.flags = [
                    *[flag for flag in (question.flags or [])
                      if flag != FLAG_RESEGMENT_RANGE_PROTECTED],
                    FLAG_RESEGMENT_RANGE_PROTECTED,
                ]
                question.error = ""
                question.reread_requested = False
                _invalidate_approval(question)
                question.save()
                preserved += 1
                continue
            if solution_local_change:
                _apply_local_solution_shortening(
                    question,
                    item,
                    group=group,
                    regions=regions,
                    candidates=candidates,
                )
                locally_trimmed += 1
                continue
            # MinerU text blocks are only a locator. A moved image range may add a
            # formula, diagram or printed line that MinerU never represented, so a
            # range change must be reread even when the block-id set looks equal.
            unchanged = (not regions_changed and not text_changed and question.regions
                         and question.state != Question.State.RED)
            keep_human_metadata = unchanged and _question_range_is_human_protected(question)
            new_section = question.section if keep_human_metadata else item["section"][:120]
            new_start_source = question.start_source if keep_human_metadata else item["start"]["source"]
            # “重新切题”承诺未变化的题卡原样保留。题卡完成识读后，
            # question_type 往往比只看 MinerU 版面的初步分类更准确；如果范围
            # 和其中的文字块都没有变化，不能把它降回 unknown。
            new_question_type = (
                question.question_type
                if question.edited or unchanged
                else item["question_type"]
            )
            question.regions = regions
            question.regions_auto = regions
            question.section = new_section
            question.start_source = new_start_source
            question.source_kind = item["source_kind"]
            question.source_anchor_seq = item["source_anchor_seq"]
            question.figure_candidates = candidates
            automatic_figures_changed = _drop_stale_automatic_figures(question, candidates)
            if any(f.get("source") == "manual" for f in (question.figures or [])):
                question.figures, question.figure_review = _manual_figures_and_review(question)
            if not question.edited:
                question.question_type = new_question_type
            if unchanged:
                kept += 1
                old_flags = list(question.flags or [])
                if FLAG_RESEGMENT_RANGE_PROTECTED in old_flags:
                    question.flags = [
                        flag for flag in old_flags
                        if flag != FLAG_RESEGMENT_RANGE_PROTECTED
                    ]
                    # Only the exact stale warning may turn a yellow card back
                    # to green.  Other review warnings and errors remain the
                    # source of truth.
                    if old_flags == [FLAG_RESEGMENT_RANGE_PROTECTED] \
                            and question.state == Question.State.YELLOW \
                            and not question.error:
                        question.state = Question.State.GREEN
                if automatic_figures_changed:
                    # No model call is needed when only deterministic ownership
                    # changed.  Rebuild the local figure decision and invalidate
                    # approval that referred to the removed automatic crop.
                    question.figure_review = {}
                    review = stored_or_derived_review(question)
                    question.figure_review = review
                    question.flags = _flags_after_figure_review(
                        question.flags, review, question.figures,
                    )
                    if question.state in {Question.State.GREEN, Question.State.YELLOW}:
                        question.state = (
                            Question.State.YELLOW if question.flags else Question.State.GREEN
                        )
                    _invalidate_approval(question)
            else:
                reread += 1
                question.figures = [f for f in question.figures if f.get("source") == "manual"]
                if question.figures:
                    question.figures, question.figure_review = _manual_figures_and_review(question)
                elif "manual_figure_outside_range" in (
                        (question.figure_review or {}).get("signals") or []):
                    # _manual_figures_and_review moved the out-of-range crop to
                    # detached audit metadata.  Keep that evidence until the
                    # reviewer explicitly chooses a new picture.
                    pass
                else:
                    question.figure_review = {}
                question.state = Question.State.WAITING
                _invalidate_approval(question)
                question.flags = list(item.get("segmentation_flags") or [])
                if "manual_figure_outside_range" in (
                        (question.figure_review or {}).get("signals") or []):
                    question.flags.append(FLAG_MANUAL_FIGURE_OUTSIDE_RANGE)
                question.error = ""
            question.save()
        system_deleted_ids: list[int] = []
        for question in unmatched:
            if question.deleted_at is not None:
                continue
            if _question_is_human_protected(question):
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
            question.state = Question.State.YELLOW
            question.flags = [
                *[flag for flag in (question.flags or []) if flag != FLAG_RESEGMENT_EXCLUDED],
                FLAG_RESEGMENT_EXCLUDED,
            ]
            question.error = ""
            question.reread_requested = False
            _invalidate_approval(question)
            question.save(update_fields=[
                "state", "flags", "error", "reread_requested",
                "approved", "approved_at", "approved_content_hash", "updated_at",
            ])
            system_deleted_ids.append(question.pk)
        if system_deleted_ids:
            batch = QuestionDeletionBatch.objects.create(
                paper=paper,
                question_ids=sorted(system_deleted_ids),
                origin=QuestionDeletionBatch.Origin.RESEGMENT,
                reason="重新切题后未再命中的自动题卡；未物理删除，可从回收站恢复。",
            )
            Question.all_objects.filter(pk__in=system_deleted_ids).update(
                deleted_at=timezone.now(), deletion_batch=batch,
            )
            excluded = len(system_deleted_ids)
        desired_group_ids = [group.pk for group in groups]
        paper.question_groups.exclude(pk__in=desired_group_ids).filter(questions__isnull=True).delete()
        if existing_questions:
            notes.append(f"重新切题：{kept} 张题卡内容没变，原样保留；{reread} 张范围变了，已重新识读。")
        if locally_trimmed:
            notes.append(
                f"教材例题：{locally_trimmed} 张已在本地去除“分析/解答/证明”尾部；"
                "沿用已有题干识读，不调用模型。"
            )
        if preserved:
            notes.append(
                f"重新切题时有 {preserved} 张人工改字、已通过或已入库题卡与新结构冲突；"
                "已保留并标黄（原内容或原范围不变），请对照原卷核对。"
            )
        if excluded:
            notes.append(
                f"重新切题时有 {excluded} 张自动题卡未被新规则命中；"
                "已移入回收站而非永久删除，需要时可以恢复。"
            )
        _set(paper, status=Paper.Status.READING, notes=notes, progress=0, total=paper.questions.count())


def trim_book_example_solutions_locally(paper: Paper) -> dict[str, int]:
    """Shorten already-read textbook examples without touching other cards.

    This narrow maintenance path is useful when a book has recoverable cards in
    its recycle bin and a full re-segmentation is intentionally blocked.  It
    never calls a reader, changes groups, restores/deletes cards, or updates a
    protected human/published card.
    """

    if paper.material_type != Paper.MaterialType.BOOK:
        raise RuntimeError("只有教材任务需要去除例题解答")
    if paper.questions.filter(state__in=[Question.State.WAITING, Question.State.READING]).exists():
        raise RuntimeError("仍有题卡正在识读；完成后才能本地去除例题解答")
    groups = list(paper.question_groups.order_by("sequence", "id"))
    if not groups:
        raise RuntimeError("这项教材任务没有稳定题组")
    blocks, desired, _notes, _diagnostics = _collect_segmentation_items(
        paper,
        groups,
        locate_gaps=False,
        page_store=PageStore(paper),
    )
    examples = [
        item for item in desired
        if item.get("source_kind") == Question.SourceKind.EXAMPLE
        and isinstance(item.get("segmentation"), dict)
        and item["segmentation"].get("solution_trimmed") is True
    ]
    counts = {
        "found": len(examples), "trimmed": 0, "unchanged": 0,
        "protected": 0, "unsafe": 0,
    }
    with transaction.atomic():
        active = list(
            Question.objects.select_for_update().filter(
                paper=paper,
                source_kind=Question.SourceKind.EXAMPLE,
                source_anchor_seq__isnull=False,
            ).select_related("group").prefetch_related("publications")
        )
        by_anchor: dict[int, list[Question]] = defaultdict(list)
        for question in active:
            by_anchor[question.source_anchor_seq].append(question)
        for item in examples:
            matches = by_anchor.get(item.get("source_anchor_seq"), [])
            if len(matches) != 1:
                counts["unsafe"] += 1
                continue
            question = matches[0]
            if _question_range_is_human_protected(question):
                counts["protected"] += 1
                continue
            solution_text_present = _saved_example_solution_text_present(question)
            old_candidate_keys = {
                key for candidate in (question.figure_candidates or [])
                if (key := candidate_key(candidate)) is not None
            }
            new_candidate_keys = {
                key for candidate in (item.get("figure_candidates") or [])
                if (key := candidate_key(candidate)) is not None
            }
            if (question.regions == item.get("regions") and not solution_text_present
                    and old_candidate_keys == new_candidate_keys):
                counts["unchanged"] += 1
                continue
            if not _solution_only_shortening(question, item, blocks):
                counts["unsafe"] += 1
                continue
            _apply_local_solution_shortening(
                question,
                item,
                group=item["group"],
                regions=item["regions"],
                candidates=_label_candidates(item.get("figure_candidates") or []),
            )
            counts["trimmed"] += 1
        if counts["trimmed"]:
            paper = Paper.objects.select_for_update().get(pk=paper.pk)
            paper.notes = [
                *(paper.notes or []),
                f"教材例题：{counts['trimmed']} 张已在本地去除“分析/解答/证明”尾部；"
                "沿用已有题干识读，未调用模型。",
            ]
            paper.save(update_fields=["notes", "updated_at"])
    return counts


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
    source_kind = snapshot.get("source_kind") or Question.SourceKind.UNKNOWN
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
    witness = str(snapshot.get("witness") or "")
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

    def run_reader(
        job: tuple[str, readers.Engine, str, bool],
    ) -> tuple[str, dict | None, str, readers.ReaderQuotaExhausted | None]:
        name, engine, url, figures = job
        try:
            # Keep the long-standing call contract for ordinary exam cards and
            # older integrations that replace read_question in tests/plugins.
            # Typed textbook cards opt into the richer prompt explicitly.
            if source_kind == Question.SourceKind.UNKNOWN:
                result = readers.read_question(engine, url, number, with_figures=figures)
            else:
                result = readers.read_question(
                    engine, url, number, with_figures=figures, source_kind=source_kind,
                )
            return name, result, "", None
        except readers.ReaderQuotaExhausted as error:
            # The independent reader may use a different provider/account.
            # Preserve that chance: one successful reading remains reviewable;
            # only a card with no usable result escalates the quota signal.
            return name, None, str(error), error
        except readers.ReaderError as error:
            return name, None, str(error), None

    # 有 MinerU 旁证时先只读一次：主读与另一引擎的文字逐字一致，就不必再花一次
    # 视觉调用做同模型复核（实测同一模型复读几乎总是逐字相同，旁证更独立）。
    # 不一致或没有旁证时，照旧请复核读者独立再读一遍。
    witness_first = bool(witness) and len(jobs) == 2 and \
        len(textnorm.witness_key(witness)) >= textnorm.WITNESS_MIN_LENGTH
    if witness_first:
        first = run_reader(jobs[0])
        name, result, _error, _quota = first
        cleaned = _without_inferred_figure_text(result, result) if result is not None else None
        if cleaned is not None and textnorm.witness_agrees(cleaned, witness):
            completed = [first]
            errors.pop("b", None)
        else:
            completed = [first, run_reader(jobs[1])]
    elif len(jobs) == 1:
        completed = [run_reader(jobs[0])]
    else:
        # 主读和复核彼此独立；两个提供商或同提供商多账号时可同时进行。
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            futures = [executor.submit(run_reader, job) for job in jobs]
            completed = [future.result() for future in futures]
    quota_errors: list[readers.ReaderQuotaExhausted] = []
    for name, result, error, quota_error in completed:
        if result is not None:
            results[name] = result
        else:
            errors[name] = error
        if quota_error is not None:
            quota_errors.append(quota_error)
    # The reading pass replaces transient AI/figure warnings, but a warning
    # emitted by the deterministic textbook segmenter must survive rereads.
    flags: list[str] = list(snapshot.get("segmentation_flags") or [])
    update: dict = {"read_a": results.get("a", {"error": errors.get("a", "")}),
                    "read_b": results.get("b", {"error": errors.get("b", "")}), "read_c": {}}
    if not results and quota_errors:
        raise quota_errors[0]
    if not results:
        return {**update, "state": Question.State.RED, "error": errors.get("a") or errors.get("b") or "识读失败",
                "flags": []}
    a, b = results.get("a"), results.get("b")
    figure_source = a or {}
    a_text = _without_inferred_figure_text(a, figure_source)
    b_text = _without_inferred_figure_text(b, figure_source)
    normalized_results = [result for result in (a_text, b_text) if result]
    if a and not b and "b" not in errors and textnorm.witness_agrees(a_text, witness):
        # Cross-engine agreement: the vision reading and MinerU's OCR match.
        final, source = a_text, "witness"
        # Kept as audit evidence and shown in the reading history; it has no
        # ``stem`` so no code path mistakes it for a vision transcription.
        update["read_b"] = {"engine": "MinerU", "witness": witness[:4000], "skipped": "witness"}
    elif a and b and same_reading(a_text, b_text):
        final, source = a_text, "agree"
    elif a and b and (textnorm.witness_agrees(b_text, witness)
                      or textnorm.witness_choice(a_text, b_text, witness) is not None):
        # MinerU (a different engine) settles the disagreement.  An arbiter
        # from the readers' own model tends to repeat their slips: in testing
        # it “confirmed” a misread repeating decimal and kept inserted words
        # (图形的面积 for the printed 图形面积), while MinerU had copied the
        # printed characters.  Every differing spot must side the same way.
        chosen = "b" if textnorm.witness_agrees(b_text, witness) else textnorm.witness_choice(a_text, b_text, witness)
        final, source = (a_text if chosen == "a" else b_text), "majority"
        update["read_c"] = {"engine": "MinerU", "witness": witness[:4000], "skipped": "witness",
                            "chosen": chosen}
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
                flags.append("两次识读不一致，已由第三次识读裁决")
        except readers.ReaderError as error:
            final, source = a_text, "single"
            update["read_c"] = {"error": str(error)}
            flags.append("两次识读不一致，裁决失败，请展开识读记录核对")
    else:
        final = a_text or b_text
        source = "single"
        flags.append(f"只有一次识读成功（另一次：{errors.get('b') or errors.get('a')}）")

    # This is a zero-extra-call guardrail.  Page structure remains the source
    # of truth for explicit 例题/练习 anchors, while model classifications are
    # used to stop prose/title candidates from silently becoming green cards.
    content_readings = [
        result for result in (a, b, update.get("read_c"))
        if isinstance(result, dict)
    ]
    if content_flag := _content_kind_review_flag(
            source_kind=source_kind,
            stem=str(final.get("stem") or ""),
            options=final.get("options") or {},
            kind=str(final.get("type") or snapshot.get("question_type") or "unknown"),
            readings=content_readings):
        flags.append(content_flag)

    figures, foreign = [], []
    labels = {c["label"]: c for c in snapshot["candidates"]}
    provisional_kind = final.get("type") or snapshot["question_type"] or "unknown"
    figure_assignments = _resolve_automatic_figure_assignments(
        stem=final.get("stem", ""),
        options=final.get("options") or {},
        kind=provisional_kind,
        candidates=snapshot["candidates"],
        assignments=figure_source.get("figures") or {},
    )
    for label, role in figure_assignments.items():
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
    figures = without_automatic_textbook_badges(figures)
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
    kind = _normalise_unlabelled_numeric_choice_type(
        kind,
        final=final,
        readings=[a, b],
        candidates=snapshot["candidates"],
        figures=figures,
    )
    choice_missing_slots = missing_choice_figure_slots(
        kind=kind,
        options=final.get("options") or {},
        figures=figures,
        readings=audited_results,
    )
    choice_missing = bool(choice_missing_slots)
    policy_stem = snapshot.get("stem", "") if snapshot.get("edited") else final.get("stem", "")
    policy_options = snapshot.get("options", {}) if snapshot.get("edited") else final.get("options") or {}
    review = automatic_review(
        stem=policy_stem,
        options=policy_options,
        candidate_labels=set(labels),
        assignments=figure_assignments,
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
    if number_flag := _number_seen_flag(
            number, [a, b], clipped_number=snapshot.get("start_source") == "repaired"):
        flags.append(number_flag)
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
            "stem": question.stem, "options": question.options, "edited": question.edited,
            "source_kind": question.source_kind, "source_anchor_seq": question.source_anchor_seq,
            "segmentation_flags": [
                flag for flag in (question.flags or [])
                if str(flag).startswith("书本切题范围超过")
                or flag == FLAG_MANUAL_FIGURE_OUTSIDE_RANGE
            ]}


WITNESS_BLOCK_TYPES = frozenset({"text", "title", "list"})


def _reader_parallelism() -> int:
    """How many cards to read at once.

    The launcher's figure is computed once at start-up.  Accounts saved later
    in Settings reach the worker through hot reload, so the live pool capacity
    may raise it; an explicit user setting (QB_PARALLEL_EXPLICIT=1) never moves.
    """
    base = PARALLEL
    if os.environ.get("QB_PARALLEL_EXPLICIT") == "1":
        return base
    capacity = 0
    seen: set[str] = set()
    for engine in (readers.primary_engine(), readers.checker_engine()):
        if engine is None or engine.provider in seen:
            continue
        seen.add(engine.provider)
        try:
            capacity += account_pool(engine.provider).capacity
        except AccountPoolError:
            continue
    return max(1, min(readers.MAX_PARALLEL_CARDS, max(base, capacity)))


def read_questions(paper: Paper, questions: list[Question]) -> None:
    if not questions:
        return
    workers = _reader_parallelism()
    store = PageStore(paper)
    snapshots = [_snapshot(q) for q in questions]
    # MinerU's own text for each range is an independent second engine.  Only
    # prose blocks are used: on marked papers the student's working is mostly
    # recognised as separate equation blocks, which would never match.
    witness_blocks = [block for block in _block_dicts(paper) if block.get("type") in WITNESS_BLOCK_TYPES]
    for snapshot in snapshots:
        snapshot["witness"] = segment._text_in_regions(witness_blocks, snapshot["regions"] or []) \
            if snapshot.get("regions") else ""
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
        except readers.ReaderQuotaExhausted:
            # This is a task-wide pause signal.  Converting it into one red
            # card would make the worker repeat the same permanent failure for
            # every remaining question.
            raise
        except Exception as error:  # 单题失败不影响其他题
            logger.exception("read failed")
            detail = str(error).strip() or type(error).__name__
            return snapshot["id"], {"state": Question.State.RED, "error": f"识读出错：{detail}"[:280],
                                    "flags": []}
        finally:
            close_old_connections()

    foreign: list[dict] = []
    def persist(question_id: int, fields: dict) -> None:
        foreign.extend(fields.pop("foreign_figures", []))
        question = Question.objects.filter(pk=question_id).first()
        if question is None:
            return
        # Figures handed over from another question's range survive a reread.
        # A row figure that lies in this question's own range is re-decided by
        # the new reading instead.
        own_keys = {candidate_key(item) for item in question.figure_candidates or []}
        borrowed = [
            f for f in question.figures
            if f.get("source") == "other" or (f.get("source") == "row" and candidate_key(f) not in own_keys)
        ]
        if borrowed and "figures" in fields:
            fields["figures"] = fields["figures"] + [
                f for f in borrowed if not _same_box(f, fields["figures"])
            ]
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
            manual_figures, manual_review = _manual_figures_and_review(question)
            fields["figures"] = manual_figures
            fields["flags"] = _flags_after_figure_review(
                fields.get("flags", []), manual_review, manual_figures,
            )
            fields["figure_review"] = manual_review
        elif "manual_figure_outside_range" in (
                (question.figure_review or {}).get("signals") or []):
            fields["figures"] = []
            fields["figure_review"] = question.figure_review
            fields["flags"] = _flags_after_figure_review(
                fields.get("flags", []), question.figure_review, [],
            )
        elif stored_or_derived_review(question).get("status") == CONFIRMED_NO_FIGURE:
            fields["figures"] = []
            fields["flags"] = [f for f in fields.get("flags", []) if not figure_flag(f)]
            fields["figure_review"] = stored_or_derived_review(question)
        if fields.get("state") == Question.State.YELLOW and not fields.get("flags"):
            fields["state"] = Question.State.GREEN
        for key, value in fields.items():
            setattr(question, key, value)
        question.save()
        Paper.objects.filter(pk=paper.pk).update(
            progress=paper.questions.exclude(
                state__in=[Question.State.WAITING, Question.State.READING],
            ).count(),
            updated_at=timezone.now(),
        )

    # Keep at most PARALLEL card jobs in flight.  Submitting the complete book
    # up front prevents a confirmed quota failure from stopping queued work.
    # A bounded window lets us cancel every not-yet-started card immediately;
    # jobs that were already running are allowed to finish but are not written
    # after the pause signal, so they remain safely resumable as READING.
    quota_error: readers.ReaderQuotaExhausted | None = None
    snapshot_iter = iter(snapshots)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = set()
        for _ in range(min(workers, len(snapshots))):
            pending.add(pool.submit(work, next(snapshot_iter)))
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            completed: list[tuple[int, dict]] = []
            for future in done:
                try:
                    completed.append(future.result())
                except readers.ReaderQuotaExhausted as error:
                    quota_error = error
                    break
            if quota_error is not None:
                for future in pending:
                    future.cancel()
                break
            for question_id, fields in completed:
                persist(question_id, fields)
            for _ in completed:
                try:
                    snapshot = next(snapshot_iter)
                except StopIteration:
                    break
                pending.add(pool.submit(work, snapshot))
    assign_foreign_figures(paper, foreign)
    distribute_figure_rows(paper)
    if quota_error is not None:
        raise quota_error


def _same_box(figure: dict, others: list[dict]) -> bool:
    return any(o["page_idx"] == figure["page_idx"] and all(abs(a - b) < 1 for a, b in zip(o["bbox"], figure["bbox"]))
               for o in others)


FLAG_ROW_FIGURE = "几道题的配图印在同一行，已按从左到右的顺序分配，请核对图与题是否对应"


def _single_row(candidates: list[dict]) -> list[dict] | None:
    """Candidates laid out left-to-right on one line of one page, else None."""
    if len(candidates) < 2 or len({item["page_idx"] for item in candidates}) != 1:
        return None
    row = sorted(candidates, key=lambda item: item["bbox"][0])
    for left, right in zip(row, row[1:]):
        top = max(left["bbox"][1], right["bbox"][1])
        bottom = min(left["bbox"][3], right["bbox"][3])
        shorter = min(left["bbox"][3] - left["bbox"][1], right["bbox"][3] - right["bbox"][1])
        if shorter <= 0 or (bottom - top) < 0.5 * shorter or right["bbox"][0] < left["bbox"][2] - 4:
            return None
    return row


def _needs_row_figure(question: Question) -> bool:
    review = stored_or_derived_review(question)
    return (
        not question.figures
        and not question.approved
        and review.get("source") != "human"
        and review.get("status") == BLOCKED_MISSING
        and bool(review.get("cue_matches"))
    )


def distribute_figure_rows(paper: Paper) -> int:
    """Hand out a shared row of figures to the consecutive questions it serves.

    Exams often print the figures of questions 13, 14 and 15 side by side below
    question 15.  Only question 15's range contains them, so 13 and 14 end up
    “missing” a figure while 15 may claim the wrong one.  When the row holds
    exactly one figure per question — the k-1 immediately preceding questions
    all mention a figure yet have none — assign them left to right and flag
    every card so a person confirms the pairing.  No model call is made.
    """
    changed = 0
    questions = list(paper.questions.order_by("group_id", "number", "id"))
    by_key = {(question.group_id, question.number): question for question in questions}
    for owner in questions:
        if owner.approved or any(f.get("source") == "manual" for f in owner.figures or []):
            continue
        row = _single_row([item for item in owner.figure_candidates or [] if item.get("bbox")])
        if row is None:
            continue
        before = [by_key.get((owner.group_id, owner.number - offset)) for offset in range(len(row) - 1, 0, -1)]
        if any(item is None or not _needs_row_figure(item) for item in before):
            continue
        targets = [*before, owner]
        for question, figure in zip(targets, row):
            box = {"slot": "stem", "page_idx": figure["page_idx"], "bbox": list(figure["bbox"]), "source": "row"}
            question.figures = [box]
            question.figure_review = automatic_review(
                stem=question.stem, options=question.options, candidate_labels=set(),
                assignments={}, figures=question.figures,
            )
            flags = [flag for flag in (question.flags or []) if not figure_flag(flag) and flag != FLAG_ROW_FIGURE]
            question.flags = [*flags, FLAG_ROW_FIGURE]
            if question.state in {Question.State.GREEN, Question.State.YELLOW}:
                question.state = Question.State.YELLOW
            _invalidate_approval(question)
            question.save()
            changed += 1
    return changed


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
    except readers.ReaderQuotaExhausted as error:
        # Quota exhaustion is recoverable after the user replenishes the plan.
        # Unfinished cards deliberately remain READING and paper_retry resumes
        # them without parsing, segmenting, or touching completed/protected cards.
        logger.warning("paper paused because the configured vision plan is exhausted")
        _set(paper, status=Paper.Status.FAILED, error=str(error)[:500])
    except Exception as error:
        logger.exception("paper failed")
        message = str(error) if isinstance(error, (MineruError, readers.ReaderError, RuntimeError)) else \
            f"处理出错：{type(error).__name__}"
        _set(paper, status=Paper.Status.FAILED, error=message[:500])


ACTIVE_PAPER_STATUSES = (
    Paper.Status.QUEUED, Paper.Status.PARSING, Paper.Status.SEGMENTING, Paper.Status.READING,
)


def process_rereads(*, idle_papers_only: bool = False) -> int:
    """人工调整范围或点"重读"后的单题重读。

    ``idle_papers_only`` lets the worker's priority lane serve rereads for
    finished papers while a long book is still being processed, without ever
    touching a paper the main lane is working on.
    """
    count = 0
    papers = Paper.objects.filter(questions__reread_requested=True)
    if idle_papers_only:
        papers = papers.exclude(status__in=ACTIVE_PAPER_STATUSES)
    for paper in papers.distinct():
        questions = list(paper.questions.filter(reread_requested=True))
        try:
            read_questions(paper, questions)
        except readers.ReaderQuotaExhausted as error:
            logger.warning("reread paused because the configured vision plan is exhausted")
            _set(paper, status=Paper.Status.FAILED, error=str(error)[:500])
            break
        count += len(questions)
    return count
