"""找回「范围里明明有图，却没有候选图」的题卡，消除由此产生的「可能漏图」误报。

背景（2026-11-18 对真库 1369 张在库题卡只读实测）：
- 全库 47 张 ``blocked_missing``（黄色「可能漏图」）题卡的 ``figure_candidates`` 全为空，
  但 ``core_block`` 中往往保留着 MinerU 识别出的印刷图块（多为历史按章节重提取或无候选识读遗留）。
- 缺少 ``figure_candidates`` 直接送图识读时，截图上没有蓝色候选框，模型只能按题号返回悬空标签
  或直接输出「缺图」，导致实际有图却被判成「可能漏图」。

本模块提供两部分能力：
1. 公共几何与归属辅助函数（供识读前防复发回填与存量修复共用）：不抢占他题已占图块、
   尊重教材（中心点落入范围）与试卷（重叠规则）各自切题边界。
2. 存量修复函数 ``repair_lost_figure_candidates``：仅针对「黄色 + blocked_missing」且
   无人工配图决定的题卡，按当前范围找回候选图并复用 ``figure_policy.persist_automatic_review_upgrades``
   重算配图状态；每张卡在独立短事务/保存点内试算，未能真正解除漏图（``status != "ok"``）即整张回滚，
   保证修复只减黄、不增黄。

默认只预演（整批回滚），传 ``apply=True`` 才逐卡写库。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from . import figure_policy, segment
from .models import Paper, Question

logger = logging.getLogger(__name__)


@dataclass
class RepairItem:
    """一张被找回候选图的题卡及其前后变化，供命令行和测试查看。"""

    question_id: int
    number: int
    paper_filename: str
    candidate_seqs: list[int]
    status_before: str
    status_after: str = ""
    state_before: str = ""
    state_after: str = ""


@dataclass
class RepairReport:
    """一次修复（或预演）的汇总。"""

    applied: bool
    scanned: int = 0
    skipped_human: int = 0
    skipped_has_figure: int = 0
    skipped_no_regions: int = 0
    skipped_nothing_found: int = 0
    skipped_not_missing: int = 0
    skipped_unresolved: int = 0
    items: list[RepairItem] = field(default_factory=list)

    @property
    def recovered(self) -> int:
        return len(self.items)

    @property
    def cleared_to_green(self) -> int:
        """修复后由黄转绿的张数（真正消除误报的数量）。"""
        return sum(1 for item in self.items if item.state_before == "yellow" and item.state_after == "green")


def has_human_figure_decision(question: Any) -> bool:
    """人工配图或人工复核过配图（含「原卷确实无图」）的题卡不能被自动流程改动。"""
    review = getattr(question, "figure_review", None)
    if isinstance(review, dict) and review.get("source") == "human":
        return True
    figures = getattr(question, "figures", None) or []
    return any(isinstance(item, dict) and item.get("source") == "manual" for item in figures)


def belongs_to_regions(material_type: str, page_idx: int, bbox: list[float], regions: list[dict]) -> bool:
    """判断图块是否属于本题原卷范围。

    NOTE: 教材（book）切题时规定「图块中心必须落在本题范围内」，因为下一题的题号
    可能紧贴着上一题的大图底部；试卷则使用更宽松的重叠判定（图常常挂在题干行下面）。
    这里必须沿用同样的规则，否则会把切题阶段刻意排除的邻题图块又拿回来。
    """
    if material_type == Paper.MaterialType.BOOK:
        return segment.center_in_regions(page_idx, bbox, regions)
    return segment.figure_in_regions(page_idx, bbox, regions)


def _seq_int(value: Any) -> int | None:
    """将有效的候选序号（排除布尔值）转为整数。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def owned_question_ids_by_seq(entries: Iterable[tuple[int, list[dict] | None]]) -> dict[int, set[int]]:
    """建立「图块 seq -> 占用它的题卡 ID 集合」索引，避免把别的题已占用的图块抢过来。"""
    owned: dict[int, set[int]] = defaultdict(set)
    for question_pk, candidates in entries:
        for candidate in candidates or []:
            if not isinstance(candidate, dict):
                continue
            seq = _seq_int(candidate.get("seq"))
            if seq is not None:
                owned[seq].add(question_pk)
    return owned


def unowned_figure_candidates_for_question(
    *,
    material_type: str,
    question_pk: int,
    regions: list[dict],
    figure_blocks: list[dict[str, Any]],
    owned_by_seq: dict[int, set[int]],
) -> list[dict[str, Any]]:
    """按题目范围筛选未被他题占用的图块，并按现行格式编号（label 从 ``"1"`` 起）。"""
    found: list[dict[str, Any]] = []
    for block in figure_blocks:
        seq = _seq_int(block.get("seq"))
        if seq is not None and (owned_by_seq.get(seq, set()) - {question_pk}):
            continue
        page_idx = block.get("page_idx")
        bbox = block.get("bbox")
        if page_idx is None or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if belongs_to_regions(material_type, int(page_idx), bbox, regions):
            found.append(block)
    return [
        {"label": str(index), "seq": block.get("seq"), "page_idx": int(block["page_idx"]), "bbox": list(block["bbox"])}
        for index, block in enumerate(found, start=1)
    ]


def _figure_blocks(paper: Paper) -> list[dict[str, Any]]:
    """取出这份资料里所有有坐标的图块（图、表、统计图），按 seq 排序。"""
    return [
        {"seq": block.seq, "type": block.type, "page_idx": block.page_idx, "bbox": list(block.bbox)}
        for block in paper.blocks.filter(type__in=segment.FIGURE_TYPES).order_by("seq")
        if block.bbox and isinstance(block.bbox, list) and len(block.bbox) == 4
    ]


def repair_lost_figure_candidates(papers: list[Paper], *, apply: bool = False) -> RepairReport:
    """为丢了候选图的「黄色漏图」题卡找回候选，并重新推导配图状态。

    :param papers: 要处理的资料列表（调用方决定范围，本函数不做全库扫描）。
    :param apply: ``False`` 时在事务内完成全部计算后回滚，只返回预演结果；
        ``True`` 才逐卡提交写库，避免启动时长时间锁住整个 SQLite 库。
    :returns: 汇总报告，``items`` 里是成功解除误报的题卡及其前后状态。
    """
    report = RepairReport(applied=apply)
    if apply:
        for paper in papers:
            _repair_one_paper(paper, report)
        return report
    with transaction.atomic():
        for paper in papers:
            _repair_one_paper(paper, report)
        # 预演：所有写入都在这个事务里，回滚后库保持原样。
        transaction.set_rollback(True)
    return report


def _repair_one_paper(paper: Paper, report: RepairReport) -> None:
    questions = list(Question.objects.filter(paper=paper, deleted_at__isnull=True).order_by("id"))
    blocks = _figure_blocks(paper)
    owned_by_seq = owned_question_ids_by_seq((question.pk, question.figure_candidates) for question in questions)
    material_type = str(getattr(paper, "material_type", "") or "")
    recovered_before = len(report.items)

    for question in questions:
        report.scanned += 1
        if str(question.state or "") not in {"green", "yellow"}:
            continue
        if question.ocr_pending or question.approved:
            continue
        if has_human_figure_decision(question):
            report.skipped_human += 1
            continue
        if question.figure_candidates or question.figures:
            report.skipped_has_figure += 1
            continue
        status_before = str((question.figure_review or {}).get("status") or "")
        # NOTE: 只处理「黄色 + 漏图」。绿卡没有问题，重算候选反而会把装饰性图块
        # 变成「未分类候选」而翻成黄色（真库预演 4 张），那是新增误报。
        if str(question.state) != "yellow" or status_before != figure_policy.BLOCKED_MISSING:
            report.skipped_not_missing += 1
            continue
        regions = question.regions or []
        if not regions or not blocks:
            report.skipped_no_regions += 1
            continue

        new_candidates = unowned_figure_candidates_for_question(
            material_type=material_type,
            question_pk=question.pk,
            regions=regions,
            figure_blocks=blocks,
            owned_by_seq=owned_by_seq,
        )
        if not new_candidates:
            report.skipped_nothing_found += 1
            continue

        state_before = str(question.state or "")
        # 每张卡独立短事务/保存点：试算后若没有真正解除「漏图」，整张回滚，原样保留。
        rolled_back = False
        with transaction.atomic():
            question.figure_candidates = new_candidates
            question.save(update_fields=["figure_candidates", "updated_at"])
            figure_policy.persist_automatic_review_upgrades([question])
            question.refresh_from_db()
            status_after = str((question.figure_review or {}).get("status") or "")
            if status_after != figure_policy.OK:
                transaction.set_rollback(True)
                rolled_back = True
        if rolled_back:
            report.skipped_unresolved += 1
            continue
        for candidate in new_candidates:
            seq = _seq_int(candidate.get("seq"))
            if seq is not None:
                owned_by_seq[seq].add(question.pk)
        report.items.append(
            RepairItem(
                question_id=question.pk,
                number=question.number,
                paper_filename=paper.filename,
                candidate_seqs=[
                    seq for item in question.figure_candidates if (seq := _seq_int(item.get("seq"))) is not None
                ],
                status_before=status_before,
                status_after=status_after,
                state_before=state_before,
                state_after=str(question.state or ""),
            )
        )
    recovered_in_paper = len(report.items) - recovered_before
    if recovered_in_paper:
        logger.info("资料 %s：解除 %d 张题卡的误报漏图", paper.filename, recovered_in_paper)
