"""Pure, zero-network planning helpers for long PDFs and repeated question numbers.

This module deliberately does not import Django, MinerU, or a model client.  Upload
views can therefore run these checks before starting any paid/slow recognition.
All source PDF page numbers in this module are one-based and inclusive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


MATERIAL_EXAM = "exam"
MATERIAL_BOOK = "book"
VALID_MATERIAL_TYPES = frozenset({MATERIAL_EXAM, MATERIAL_BOOK})
# 教材往往包含大量图片和复杂版式。即使没有触及 MinerU 的单文件硬上限，
# 固定按 100 页处理也能让失败重试只重跑一个稳定的小分片。
BOOK_CHUNK_PAGES = 100


class ChunkCoverageError(ValueError):
    """The proposed PDF chunks overlap, leave a gap, or map to wrong source pages."""


@dataclass(frozen=True)
class PageChunk:
    """One MinerU-sized processing unit mapped back to the original PDF."""

    sequence: int
    source_page_start: int
    source_page_end: int
    page_map: tuple[int, ...]

    @property
    def page_count(self) -> int:
        return self.source_page_end - self.source_page_start + 1

    def as_record(self) -> dict:
        """Fields accepted by ``ImportChunk`` (apart from ``paper``)."""

        return {
            "sequence": self.sequence,
            "source_page_start": self.source_page_start,
            "source_page_end": self.source_page_end,
            "page_map": list(self.page_map),
        }


def pdf_chunk_page_limit(material_type: str, hard_max_pages: int) -> int:
    """Return the deterministic per-request page limit for one PDF.

    ``hard_max_pages`` remains the provider's absolute ceiling. Books use a
    deliberately smaller local processing unit so a retry never depends on the
    size of the original book; ordinary exam papers keep the provider ceiling.
    """

    if material_type not in VALID_MATERIAL_TYPES:
        raise ValueError(f"未知资料模式：{material_type}")
    if hard_max_pages <= 0:
        raise ValueError("PDF 单次解析页数上限必须大于 0")
    if material_type == MATERIAL_BOOK:
        return min(BOOK_CHUNK_PAGES, hard_max_pages)
    return hard_max_pages


def pdf_requires_chunks(total_pages: int, material_type: str, hard_max_pages: int) -> bool:
    """Whether this PDF should use local slices before remote parsing."""

    if total_pages <= 0:
        raise ValueError("PDF 页数必须大于 0")
    chunk_limit = pdf_chunk_page_limit(material_type, hard_max_pages)
    # Every book follows the same resumable path, including a book that fits in
    # one 100-page slice. This keeps retry semantics independent of book length.
    return material_type == MATERIAL_BOOK or total_pages > chunk_limit


def plan_pdf_chunks(
    total_pages: int,
    max_pages: int,
    *,
    start_page: int = 1,
    end_page: int | None = None,
) -> list[PageChunk]:
    """Split a selected PDF range into contiguous, non-overlapping chunks.

    ``total_pages`` describes the original file, while ``start_page`` and
    ``end_page`` allow the upload screen to exclude covers, contents or answers.
    No file is modified here; this function only creates a deterministic plan.
    """

    if total_pages <= 0:
        raise ValueError("PDF 页数必须大于 0")
    if max_pages <= 0:
        raise ValueError("每个分片的页数上限必须大于 0")
    if end_page is None:
        end_page = total_pages
    if start_page <= 0 or end_page <= 0:
        raise ValueError("PDF 页码从 1 开始")
    if start_page > end_page:
        raise ValueError("起始页不能晚于结束页")
    if end_page > total_pages:
        raise ValueError("选择的结束页超过 PDF 总页数")

    chunks: list[PageChunk] = []
    cursor = start_page
    sequence = 1
    while cursor <= end_page:
        chunk_end = min(cursor + max_pages - 1, end_page)
        chunks.append(
            PageChunk(
                sequence=sequence,
                source_page_start=cursor,
                source_page_end=chunk_end,
                page_map=tuple(range(cursor, chunk_end + 1)),
            )
        )
        sequence += 1
        cursor = chunk_end + 1

    validate_chunk_coverage(chunks, expected_start=start_page, expected_end=end_page)
    return chunks


def _chunk_value(chunk: PageChunk | Mapping, name: str):
    if isinstance(chunk, Mapping):
        return chunk[name]
    return getattr(chunk, name)


def validate_chunk_coverage(
    chunks: Iterable[PageChunk | Mapping],
    *,
    expected_start: int,
    expected_end: int,
) -> bool:
    """Require an ordered, exact cover of the selected original-page interval.

    Returning ``True`` makes this convenient in guards and tests. Invalid input
    raises ``ChunkCoverageError`` with a user-loggable reason; it is never silently
    sorted or repaired because doing so could hide a lost or duplicated page.
    """

    if expected_start <= 0 or expected_end < expected_start:
        raise ValueError("待覆盖的原始页码范围无效")

    items = list(chunks)
    if not items:
        raise ChunkCoverageError("没有任何 PDF 分片")

    next_page = expected_start
    for expected_sequence, chunk in enumerate(items, start=1):
        sequence = int(_chunk_value(chunk, "sequence"))
        start = int(_chunk_value(chunk, "source_page_start"))
        end = int(_chunk_value(chunk, "source_page_end"))
        page_map = tuple(int(page) for page in _chunk_value(chunk, "page_map"))

        if sequence != expected_sequence:
            raise ChunkCoverageError(
                f"分片序号应为 {expected_sequence}，实际为 {sequence}"
            )
        if end < start:
            raise ChunkCoverageError(f"第 {sequence} 个分片的页码范围倒置")
        if start < next_page:
            raise ChunkCoverageError(
                f"第 {sequence} 个分片从第 {start} 页开始，与前一分片重叠"
            )
        if start > next_page:
            raise ChunkCoverageError(
                f"第 {sequence} 个分片从第 {start} 页开始，漏掉了第 {next_page} 页"
            )

        expected_map = tuple(range(start, end + 1))
        if page_map != expected_map:
            raise ChunkCoverageError(
                f"第 {sequence} 个分片的原始页映射与页码范围不一致"
            )
        next_page = end + 1

    if next_page - 1 < expected_end:
        raise ChunkCoverageError(f"分片在第 {next_page - 1} 页结束，未覆盖到第 {expected_end} 页")
    if next_page - 1 > expected_end:
        raise ChunkCoverageError(f"分片覆盖到第 {next_page - 1} 页，超过第 {expected_end} 页")
    return True


@dataclass(frozen=True)
class NumberingPage:
    """Printed question-number evidence found on one original source page."""

    source_page: int
    numbers: tuple[int, ...]


@dataclass(frozen=True)
class NumberingSignal:
    """A restart or overlap that may mark a new exam/chapter/exercise."""

    kind: str
    source_page: int
    numbers: tuple[int, ...]
    overlapping_numbers: tuple[int, ...]
    previous_max: int | None
    message: str


@dataclass(frozen=True)
class SuggestedQuestionGroup:
    sequence: int
    page_start: int
    page_end: int
    reason: str


@dataclass(frozen=True)
class QuestionGroupingPlan:
    material_type: str
    needs_confirmation: bool
    groups: tuple[SuggestedQuestionGroup, ...]
    signals: tuple[NumberingSignal, ...]

    @property
    def has_numbering_restart(self) -> bool:
        return bool(self.signals)


def _normalize_numbering_pages(
    pages: Iterable[NumberingPage | Mapping | Sequence[int]],
) -> list[NumberingPage]:
    normalized: list[NumberingPage] = []
    previous_page = 0
    for fallback_page, item in enumerate(pages, start=1):
        if isinstance(item, NumberingPage):
            source_page = item.source_page
            raw_numbers = item.numbers
        elif isinstance(item, Mapping):
            source_page = int(item.get("source_page", fallback_page))
            raw_numbers = item.get("numbers", ())
        else:
            source_page = fallback_page
            raw_numbers = item

        if source_page <= previous_page:
            raise ValueError("题号证据必须按递增的原始页码传入")
        previous_page = source_page

        numbers = tuple(sorted({int(number) for number in raw_numbers}))
        if any(number <= 0 for number in numbers):
            raise ValueError("印刷题号必须是正整数")
        normalized.append(NumberingPage(source_page=source_page, numbers=numbers))
    return normalized


def analyze_question_number_structure(
    pages: Iterable[NumberingPage | Mapping | Sequence[int]],
    *,
    material_type: str = MATERIAL_EXAM,
) -> QuestionGroupingPlan:
    """Detect restarts/overlaps without deleting or merging any question.

    For an exam, a restart is a structural conflict that must be confirmed before
    cards are generated. For a book it is a harmless chapter/exercise boundary
    suggestion: repeated ``第 1 题`` values remain valid and need no conflict state.
    """

    if material_type not in VALID_MATERIAL_TYPES:
        raise ValueError(f"未知资料模式：{material_type}")
    normalized = _normalize_numbering_pages(pages)
    if not normalized:
        return QuestionGroupingPlan(material_type, False, (), ())

    signals: list[NumberingSignal] = []
    boundaries: list[tuple[int, str]] = []
    seen: set[int] = set()
    maximum: int | None = None

    for page in normalized:
        if not page.numbers:
            continue
        overlaps = tuple(sorted(seen.intersection(page.numbers)))
        restarted = maximum is not None and min(page.numbers) < maximum
        if seen and (overlaps or restarted):
            kind = "restart_and_overlap" if overlaps and restarted else "overlap" if overlaps else "restart"
            if overlaps and restarted:
                message = "题号从较小数字重新开始，并与前面页的题号重合"
            elif overlaps:
                message = "这一页与前面页出现了相同题号"
            else:
                message = "题号从较小数字重新开始"
            signals.append(
                NumberingSignal(
                    kind=kind,
                    source_page=page.source_page,
                    numbers=page.numbers,
                    overlapping_numbers=overlaps,
                    previous_max=maximum,
                    message=message,
                )
            )
            boundaries.append((page.source_page, message))
            # A suggested new scope starts here. The same numbers are deliberately
            # allowed in the new scope rather than being deduplicated.
            seen = set()
            maximum = None

        seen.update(page.numbers)
        maximum = max(page.numbers) if maximum is None else max(maximum, max(page.numbers))

    first_page = normalized[0].source_page
    last_page = normalized[-1].source_page
    groups: list[SuggestedQuestionGroup] = []
    group_start = first_page
    group_reason = "资料起始页"
    for boundary_page, reason in boundaries:
        groups.append(
            SuggestedQuestionGroup(
                sequence=len(groups),
                page_start=group_start,
                page_end=boundary_page - 1,
                reason=group_reason,
            )
        )
        group_start = boundary_page
        group_reason = reason
    groups.append(
        SuggestedQuestionGroup(
            sequence=len(groups),
            page_start=group_start,
            page_end=last_page,
            reason=group_reason,
        )
    )

    return QuestionGroupingPlan(
        material_type=material_type,
        needs_confirmation=material_type == MATERIAL_EXAM and bool(signals),
        groups=tuple(groups),
        signals=tuple(signals),
    )


def analyze_page_number_ranges(
    page_ranges: Iterable[tuple[int, int] | None],
    *,
    material_type: str = MATERIAL_EXAM,
) -> QuestionGroupingPlan:
    """Adapter for photo-page evidence represented as inclusive ``(low, high)`` ranges."""

    pages: list[tuple[int, ...]] = []
    for number_range in page_ranges:
        if number_range is None:
            pages.append(())
            continue
        low, high = (int(value) for value in number_range)
        if low <= 0 or high < low:
            raise ValueError("页面题号范围无效")
        pages.append(tuple(range(low, high + 1)))
    return analyze_question_number_structure(pages, material_type=material_type)
