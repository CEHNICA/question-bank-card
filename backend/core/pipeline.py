"""自动流水线：解析 → 切题 → 读题 → 待终审。全程无需人工干预。"""

from __future__ import annotations

import logging
import os
import shutil
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone
from PIL import Image

from . import imaging, photos, readers, segment
from .figure_policy import (
    BLOCKED_MISSING, CONFIRMED_NO_FIGURE, CONFLICT, FLAG_NO_FIGURE, FLAG_UNCUED_FIGURE,
    FLAG_UNFOUND_FIGURE, OK, automatic_review, figure_flag,
    recheck_automatic_review, stored_or_derived_review,
)
from .mineru import MineruError, load_blocks, request_extract
from .models import Block, Paper, Question
from .textnorm import same_reading
from .word import convert_docx_to_pdf

logger = logging.getLogger(__name__)
PARALLEL = max(1, min(8, int(os.environ.get("QB_PARALLEL", "4"))))


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
                _, old_image = self.memory.popitem(last=False)
                old_image.close()
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


# ---------------------------------------------------------------- 1. 解析

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
    archive = Path(paper.zip_path) if paper.zip_path else folder / "mineru_result.zip"
    if not archive.is_file():
        token = os.environ.get("MINERU_TOKEN", "").strip()
        if not token:
            raise MineruError("没有配置 MinerU Token，无法解析新试卷")
        archive = request_extract(paper, token, render)
    try:
        blocks = load_blocks(archive, len(paper.pages))
    except MineruError:
        # Only remove the per-paper cache we own. A failed/oversized download used
        # to leave a file behind, causing every retry to reopen the same bad ZIP.
        owned_archive = archive.name == "mineru_result.zip" and archive.parent.resolve() == folder.resolve()
        token = os.environ.get("MINERU_TOKEN", "").strip()
        if not owned_archive or not token:
            raise
        archive.unlink(missing_ok=True)
        archive = request_extract(paper, token, render)
        blocks = load_blocks(archive, len(paper.pages))
    if paper.photos:
        blocks = arrange_photo_pages(paper, blocks)
    with transaction.atomic():
        paper.blocks.all().delete()
        Block.objects.bulk_create([Block(paper=paper, **block) for block in blocks], batch_size=300)
        _set(paper, zip_path=str(archive), status=Paper.Status.SEGMENTING)
    store = PageStore(paper)
    for page in paper.pages:
        store.preview(page["page_idx"])


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
    ranges = photos.page_ranges(imaging.page_sizes(Path(paper.render_path), "pdf"),
                                [photos.remap_page(mapping, b) for b in blocks])
    info["notes"] = [_page_note(info, ranges, "已手动调整。"),
                     *[note for note in info.get("notes", []) if not note.startswith(PAGE_NOTE)]]
    _store_ranges(info, ranges)
    with transaction.atomic():
        changed = list(paper.blocks.all())
        for block in changed:
            block.page_idx = mapping[block.page_idx]
        Block.objects.bulk_update(changed, ["page_idx"], batch_size=300)
        for question in paper.questions.all():
            for field in ("regions", "regions_auto", "figures", "figure_candidates"):
                setattr(question, field, [photos.remap_page(mapping, item) for item in getattr(question, field)])
            if not any(figure.get("source") == "manual" for figure in question.figures):
                question.figure_review = {}
            _invalidate_approval(question)
            question.save(update_fields=[
                "regions", "regions_auto", "figures", "figure_candidates", "figure_review",
                "approved", "approved_at", "approved_content_hash", "updated_at",
            ])
        _set(paper, photos=info, pages=imaging.page_sizes(Path(paper.render_path), "pdf"),
             status=Paper.Status.SEGMENTING, error="")


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
    layout, starts = segment.analyse(paper.pages, blocks)
    store = PageStore(paper)
    notes = locate_missing(paper, layout, starts, store)
    questions = segment.build_questions(layout, starts, blocks)
    if not questions:
        raise RuntimeError("没有在试卷里找到印刷题号，无法切题")
    existing = {q.number: q for q in paper.questions.all()}
    kept = reread = 0
    with transaction.atomic():
        numbers = set()
        for item in questions:
            numbers.add(item["number"])
            regions = imaging.trim_regions(item["regions"], store.load) if item["regions"] else []
            candidates = _label_candidates(item["figure_candidates"])
            question = existing.get(item["number"])
            if question is None:
                Question.objects.create(
                    paper=paper, number=item["number"], section=item["section"][:120],
                    question_type=item["question_type"], regions=regions, regions_auto=regions,
                    start_source=item["start"]["source"], figure_candidates=candidates,
                )
                continue
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
        for number, question in existing.items():
            if number not in numbers and question.start_source != "manual" and not question.publications.exists():
                question.delete()
        if existing:
            notes.append(f"重新切题：{kept} 张题卡内容没变，原样保留；{reread} 张范围变了，已重新识读。")
        _set(paper, status=Paper.Status.READING, notes=notes, progress=0, total=paper.questions.count())


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
        return {"state": Question.State.RED, "error": "没有配置 MiniMax API Key，无法读题", "flags": []}
    if not snapshot["regions"]:
        return {"state": Question.State.RED, "flags": [],
                "error": "没有切出这道题的原卷范围，请点“调整范围”在原卷上框出来"}
    clean, _ = imaging.stack_regions(snapshot["regions"], store.load)
    marks = [{"label": c["label"], "page_idx": c["page_idx"], "bbox": c["bbox"]} for c in snapshot["candidates"]]
    marked = imaging.stack_regions(snapshot["regions"], store.load, marks)[0] if marks else clean
    clean_url, marked_url = imaging.jpeg_data_url(clean), imaging.jpeg_data_url(marked)

    results: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for name, engine, url, figures in (("a", primary, marked_url, True), ("b", checker, clean_url, False)):
        try:
            results[name] = readers.read_question(engine, url, number, with_figures=figures)
        except readers.ReaderError as error:
            errors[name] = str(error)
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
            c = readers.arbitrate(primary, clean_url, number, a_text, b_text)
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
            foreign.append({"number": int(role[1:]), **box})   # 属于别的题的图，交给那道题
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
    return {"id": question.id, "number": question.number, "regions": question.regions,
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
        target = paper.questions.filter(number=item["number"]).first()
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
