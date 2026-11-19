"""找回丢失的候选图，消除「可能漏图」误报（默认只预演，不写库）。

    manage.py repair_figure_candidates                      # 预演：列出会被修的题卡，库不变
    manage.py repair_figure_candidates --paper 某书.pdf       # 只看某份资料
    manage.py repair_figure_candidates --apply              # 真正写库

不调用识读服务、不改题干和读者原始记录；人工配图/人工复核过的题卡一律跳过。
写库前请先关闭桌面应用并备份 db.sqlite3。
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from core.figure_candidate_repair import repair_lost_figure_candidates
from core.models import Paper
from core.worker_lock import SingleInstance


class Command(BaseCommand):
    help = "为丢了候选图的题卡找回候选并重算配图状态（默认只预演）"

    def add_arguments(self, parser):
        parser.add_argument("--paper", default="", help="只处理这些资料的文件名（逗号分隔）；默认全部资料")
        parser.add_argument("--apply", action="store_true", help="真正写库；不加则只预演并回滚")

    def handle(self, *args, paper="", apply=False, **options):
        papers = Paper.objects.order_by("filename")
        names = [name.strip() for name in paper.split(",") if name.strip()]
        if names:
            papers = papers.filter(filename__in=names)
            missing = set(names) - {item.filename for item in papers}
            if missing:
                raise CommandError("找不到这些资料：" + "、".join(sorted(missing)))
        papers = list(papers)
        if not papers:
            raise CommandError("没有可处理的资料。")

        if apply:
            # 写库时和后台识读进程互斥，避免同时改同一批题卡。
            lock = SingleInstance(settings.DATA_ROOT / "worker.lock")  # noqa: F841 — 持有到进程结束

        report = repair_lost_figure_candidates(papers, apply=apply)
        for item in report.items:
            self.stdout.write(
                f"#{item.question_id} 第 {item.number} 题 · {item.paper_filename[:30]}："
                f"{item.status_before or '-'}/{item.state_before} → {item.status_after or '-'}/{item.state_after}"
                f"（候选 seq {item.candidate_seqs}）"
            )
        self.stdout.write(
            f"\n扫描 {report.scanned} 张；解除误报漏图 {report.recovered} 张（其中黄转绿 {report.cleared_to_green} 张）；"
            f"跳过：人工决定 {report.skipped_human}、已有配图 {report.skipped_has_figure}、"
            f"本就不是黄色漏图 {report.skipped_not_missing}、范围里没图 {report.skipped_nothing_found}、"
            f"找回候选后仍无法自动判定（已回滚，原样保留）{report.skipped_unresolved}。"
        )
        self.stdout.write("已写库。" if apply else "这是预演，库没有改动；确认后加 --apply。")
