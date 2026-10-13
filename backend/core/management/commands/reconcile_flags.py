# -*- coding: utf-8 -*-
"""只补兜底提示，不重新切题、不重新识读。

「重新切题」跑完末尾会顺带补几条兜底提示：几何挂图、卡号那一行认不出、
填空题却没填空位置、一题的小问排成两栏却只读到左栏、题干点名的图号被切在配图框外。
这些判据其实只跟卡上已有的范围、题型、旗标有关，跟重切没有半点关系——
却只能等一次全量重切才补得上，一次几分钟到十几分钟，还要重新读一遍题。

    manage.py reconcile_flags                    # 六条都补（与重切时的行为一致）
    manage.py reconcile_flags --only fill_blank,anchor
    manage.py reconcile_flags --paper 数学九年级上册.pdf

真库实测（2026-11-04，959 张）：三条一起跑 0.6 秒、零模型调用，
84 张卡挂上新提示、绿卡 720 → 667，**题干一个字没改，已通过的一张没掉**。
2026-10-11 又加了第四条（subitem）：一题的小问排成左右两栏、切题只罩到左栏，
右栏那几条在成品卷上直接消失。2026-11-04 把这条的判据换成了更准的证据
（回原书看那一行印在哪、谁罩着它，而不是量范围宽窄），命中从 6 张变成 9 张。
2026-11-04 加了第五条（caption）：教材在图的正下方印一行「图 4-1」，切图只圈了
图形本身，编号差几 pt 落在框外。全库命中 42 张、4.3 秒，全在两本教材，试卷零命中。
2026-10-14 加了第六条（slotted）：不是选择题，配图却被分到了 A/B/C/D/E 槽。
两条导出路都照槽印字母（Word 的 `_write_question` 每个 ABCDE 槽都写一个
``f"{letter}. "`，PDF 的 `LibraryQuestion` 同理），跟题型无关。全库命中 1 张：
九上第 1 题 #1426，8 张视图配 4 个物体的配对填空题，成品卷上凭空多出 A. B. C. D.，
而题干说的括号没有，学生当四选一做但原题根本不是选择题。

⚠️ 「几何挂图」那条在存量卡上误报明显：22 张里 18 张原本是绿卡，
抽 3 张（#786/#1083/#1121）导成品卷看纸面，图都紧跟题干、位置正确。
要少白转 18 张黄卡，就用 --only 跳过它。
"""

import time
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from core import pipeline
from core.models import Paper
from core.worker_lock import SingleInstance

CHECKS = {
    "geometric": ("几何挂图落在本题范围外", pipeline.reconcile_geometric_figure_flags),
    "anchor": ("卡号那一行认不出", pipeline.reconcile_anchor_reading_flags),
    "fill_blank": ("题型标成填空题却没有空格", pipeline.reconcile_fill_blank_slot_flags),
    "subitem": ("小问排成两栏、只读到了左栏", pipeline.reconcile_subitem_column_flags),
    "caption": ("题干点名的图号被切在配图框外", pipeline.reconcile_figure_caption_flags),
    "slotted": ("非选择题却把图挂进了 A~D 选项槽", pipeline.reconcile_slotted_figure_flags),
}
ORDER = ("geometric", "anchor", "fill_blank", "subitem", "caption", "slotted")


class Command(BaseCommand):
    help = "只给存量题卡补兜底提示，不重新切题、不调用识读服务"

    def add_arguments(self, parser):
        parser.add_argument(
            "--only", default="",
            help="逗号分隔，只跑这几条：" + "、".join(ORDER) + "（默认五条都跑）")
        parser.add_argument(
            "--paper", default="",
            help="只处理这一份资料的文件名（可重复用逗号分隔）；默认全部资料")
        parser.add_argument(
            "--list", action="store_true", dest="list_only",
            help="只列出这份资料里每条会命中多少张，不写库")

    def handle(self, *args, only="", paper="", list_only=False, **options):
        wanted = [name.strip() for name in only.split(",") if name.strip()] or list(ORDER)
        unknown = [name for name in wanted if name not in CHECKS]
        if unknown:
            raise CommandError(
                f"不知道这几条检查：{'、'.join(unknown)}；可选：" + "、".join(ORDER))

        papers = Paper.objects.order_by("filename")
        names = [name.strip() for name in paper.split(",") if name.strip()]
        if names:
            papers = papers.filter(filename__in=names)
            missing = set(names) - {p.filename for p in papers}
            if missing:
                raise CommandError("找不到这些资料：" + "、".join(sorted(missing)))
        papers = list(papers)
        if not papers:
            raise CommandError("没有可处理的资料。")

        lock = SingleInstance(settings.DATA_ROOT / "worker.lock")  # noqa: F841 — 持有到进程结束
        close_old_connections()

        started = time.monotonic()
        before_green = self._green()
        totals = Counter()
        for name in ORDER:
            if name not in wanted:
                continue
            label, check = CHECKS[name]
            hits = 0
            for item in papers:
                hits += check(item, dry_run=list_only)
            totals[name] = hits
            self.stdout.write(f"{label}：命中 {hits} 张")
            if list_only and hits:
                self.stdout.write("  （--list 没有写库；要真补就去掉 --list）")

        self.stdout.write(
            f"\n{len(papers)} 份资料，用时 {time.monotonic() - started:.1f}s，"
            f"共挂上 {sum(totals.values())} 处提示（未调用识读服务）")
        if not list_only:
            after_green = self._green()
            self.stdout.write(f"绿卡 {before_green} → {after_green}")
        if list_only:
            self.stdout.write("这是 --list 的预览，库没有改动。")

    @staticmethod
    def _green() -> int:
        from core.models import Question

        return Question.objects.filter(state="green", deleted_at__isnull=True).count()
