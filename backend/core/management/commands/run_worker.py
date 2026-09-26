"""后台工作者：自动把每份试卷跑完解析 → 切题 → 读题，并处理单题重读。"""

import logging
import os
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core.models import Paper
from core.pipeline import process_paper, process_rereads

ACTIVE = [Paper.Status.QUEUED, Paper.Status.PARSING, Paper.Status.SEGMENTING, Paper.Status.READING]


class SingleInstance:
    """同一个数据目录只允许一个工作者，避免两个进程抢同一份卷。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit("已有一个题卡版工作者在运行，本进程退出。")


class Command(BaseCommand):
    help = "处理排队的试卷和重读请求"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="处理完当前任务就退出")

    def handle(self, *args, once=False, **options):
        lock = SingleInstance(settings.DATA_ROOT / "worker.lock")  # noqa: F841 — 持有到进程结束
        while True:
            close_old_connections()
            worked = False
            try:
                for paper in Paper.objects.filter(status__in=ACTIVE).order_by("created_at"):
                    self.stdout.write(f"处理试卷 {paper.display_name}（{paper.get_status_display()}）")
                    sys.stdout.flush()
                    process_paper(paper)
                    worked = True
                if process_rereads():
                    worked = True
            except Exception:  # 工作者不能因为一次意外就退出
                logging.getLogger("core").exception("worker loop error")
                time.sleep(5)
            if once and not worked:
                return
            if not worked:
                time.sleep(2)
