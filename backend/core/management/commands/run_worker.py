"""后台工作者：自动把每份试卷跑完解析 → 切题 → 读题，并处理单题重读。"""

import logging
import os
import sys
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from core import credential_settings, library_job_control, library_jobs, preferences, region_reads
from core.account_pool import lease_priority, reset_account_pools
from core.models import Paper, Question
from core.pipeline import parse_ahead, process_paper, process_rereads, reading_tail, recover_interrupted_readings
from core.worker_lock import SingleInstance

ACTIVE = [Paper.Status.QUEUED, Paper.Status.PARSING, Paper.Status.SEGMENTING, Paper.Status.READING]
# One reread batch at a time, whichever lane runs it.
REREAD_LOCK = threading.Lock()


def reread_lane(stop: threading.Event, interval: float = 1.5) -> None:
    """Serve single-card rereads while the main lane is busy with a long task.

    Without this lane a range adjustment on a finished exam waited for an
    entire book upload to finish.  It only touches papers that are not being
    processed, and uses the credentials already loaded for the worker.
    """
    logger = logging.getLogger("core")
    while not stop.is_set():
        try:
            close_old_connections()
            if Question.objects.filter(reread_requested=True).exists() and REREAD_LOCK.acquire(blocking=False):
                try:
                    process_rereads(idle_papers_only=True)
                finally:
                    REREAD_LOCK.release()
            # 框选识读：人在题卡前等着，不等主线上的长任务。
            if region_reads.pending():
                region_reads.process_pending()
        except Exception:  # the lane must never take the worker down
            logger.exception("reread lane error")
        finally:
            close_old_connections()
        stop.wait(interval)


# The main lane's current paper, the paper the overlap lane is reading, and
# the papers the parse lane is working on.
LANES_LOCK = threading.Lock()
MAIN_CURRENT: dict = {"paper": None}
OVERLAP_CURRENT: dict = {"paper": None}
PARSING_AHEAD: set = set()
# Saved settings files as they were when the worker last applied them.
SETTINGS_APPLIED: dict = {"signature": None}


def paper_priority(paper) -> float:
    """Older uploads first when two papers share the reading accounts."""
    created = getattr(paper, "created_at", None)
    try:
        return float(created.timestamp()) if created is not None else 0.0
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return 0.0


def settings_signature() -> tuple:
    paths = [preferences.preference_path()]
    if os.environ.get("QB_CREDENTIAL_HOT_RELOAD") == "1":
        try:
            paths.append(credential_settings.store.credential_path())
        except Exception:  # an unreadable location is reported when applied
            pass
    signature = []
    for path in paths:
        try:
            stat = Path(path).stat()
            signature.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            signature.append((str(path), None, None))
    return tuple(signature)


def settings_pending() -> bool:
    return SETTINGS_APPLIED["signature"] != settings_signature()


def other_lane_busy() -> bool:
    """Whether the overlap lane is in the middle of a paper."""
    with LANES_LOCK:
        return OVERLAP_CURRENT["paper"] is not None


def parse_lane(stop: threading.Event, interval: float = 2.0) -> None:
    """Send the next queued papers to MinerU while the main lane reads.

    A batch of papers used to run strictly one after another: MinerU for
    paper 2 started only after every card of paper 1 was read.  Parsing
    uses no reading-model quota, so it can run ahead.  Only papers still
    QUEUED are taken, and never the one the main lane is on.
    """
    logger = logging.getLogger("core")
    while not stop.is_set():
        claimed = None
        try:
            close_old_connections()
            with LANES_LOCK:
                current = MAIN_CURRENT["paper"]
            if current is not None:
                candidate = (
                    Paper.objects.filter(status=Paper.Status.QUEUED)
                    .exclude(processing_plan__mode__in=["manual", "native"], processing_plan__mode__isnull=False)
                    .exclude(pk=current)
                    .order_by("created_at")
                    .first()
                )
                if candidate is not None:
                    with LANES_LOCK:
                        if MAIN_CURRENT["paper"] != candidate.pk:
                            PARSING_AHEAD.add(candidate.pk)
                            claimed = candidate
            if claimed is not None:
                parse_ahead(claimed)
        except Exception:  # the lane must never take the worker down
            logger.exception("parse lane error")
        finally:
            if claimed is not None:
                with LANES_LOCK:
                    PARSING_AHEAD.discard(claimed.pk)
            close_old_connections()
        if claimed is None:
            stop.wait(interval)


def overlap_lane(stop: threading.Event, interval: float = 1.0) -> None:
    """Start reading the next paper while the current one finishes its last cards.

    Each paper's reading ends with a tail: the final few cards wait on their
    second reading or an arbiter while most reading slots sit idle.  Across a
    batch that idle time added up to about a quarter of the run.  Once the
    main lane's paper has handed out its last card, this lane starts the next
    already-parsed paper.  Leases are ordered by upload time, so the older
    paper still gets every slot it asks for.  The lane waits while saved
    settings are pending, so the main lane soon reaches a boundary where no
    paper is mid-read and applies them there.
    """
    logger = logging.getLogger("core")
    while not stop.is_set():
        claimed = None
        try:
            close_old_connections()
            with LANES_LOCK:
                current = MAIN_CURRENT["paper"]
            if current is not None and reading_tail(current) and not settings_pending():
                candidate = (
                    Paper.objects.filter(status__in=ACTIVE)
                    .exclude(processing_plan__mode__in=["manual", "native"], processing_plan__mode__isnull=False)
                    .exclude(pk=current)
                    .order_by("created_at")
                    .first()
                )
                # Strictly in upload order: never jump past a paper still at MinerU.
                if candidate is not None and candidate.status in (Paper.Status.SEGMENTING, Paper.Status.READING):
                    with LANES_LOCK:
                        if (
                            MAIN_CURRENT["paper"] == current
                            and candidate.pk not in PARSING_AHEAD
                            and OVERLAP_CURRENT["paper"] is None
                        ):
                            OVERLAP_CURRENT["paper"] = candidate.pk
                            claimed = candidate
            if claimed is not None:
                claimed.refresh_from_db()
                if claimed.status in ACTIVE:
                    with lease_priority(paper_priority(claimed)):
                        process_paper(claimed)
        except Exception:  # the lane must never take the worker down
            logger.exception("overlap lane error")
            claimed = None  # back off instead of retrying at once
        finally:
            with LANES_LOCK:
                OVERLAP_CURRENT["paper"] = None
            close_old_connections()
        if claimed is None:
            stop.wait(interval)


def library_job_lane(stop: threading.Event, interval: float = 1.5) -> None:
    """独立后台并行通道：并发处理题库标签与答案任务，并在空闲时温和补齐无标签题目。"""
    logger = logging.getLogger("core")
    while not stop.is_set():
        worked = 0
        try:
            close_old_connections()
            library_job_control.touch_worker_heartbeat()
            if settings_pending():
                apply_saved_settings()
            library_jobs.enqueue_untagged_background()
            if library_jobs.pending():
                if settings_pending():
                    apply_saved_settings()
                worked = library_jobs.process_parallel(stop=stop)
        except Exception:  # the lane must never take the worker down
            logger.exception("library job lane error")
        finally:
            close_old_connections()
        if not worked:
            stop.wait(interval)


def claim_for_main_lane(paper) -> bool:
    """The main lane takes a paper unless another lane has it or is mid-read.

    While the overlap lane is still handing out cards of an older paper, the
    main lane waits: starting a third paper then would only queue behind it.
    """
    key = getattr(paper, "pk", None)
    with LANES_LOCK:
        if key is not None and key in PARSING_AHEAD:
            return False
        overlap = OVERLAP_CURRENT["paper"]
        if overlap is not None and (overlap == key or not reading_tail(overlap)):
            return False
        MAIN_CURRENT["paper"] = key
        return True


def release_main_lane() -> None:
    with LANES_LOCK:
        MAIN_CURRENT["paper"] = None


def apply_saved_settings() -> bool:
    """Apply saved credentials and model choices at a task boundary.

    Skipped while the overlap lane is mid-paper: switching models or resetting
    the account pools under it would mix two configurations in one paper.
    """
    if other_lane_busy():
        return False
    signature = settings_signature()
    apply_saved_credentials()
    apply_saved_model_preferences()
    SETTINGS_APPLIED["signature"] = signature
    return True


def apply_saved_credentials():
    """Load one DPAPI snapshot before a paper/reread batch starts.

    Failures keep the worker's last known-good environment.  The exception is
    intentionally not logged with a traceback: no submitted credential value
    should ever appear in worker logs, even during recovery.
    """

    try:
        return credential_settings.apply_worker_environment()
    except credential_settings.CredentialStoreError:
        logging.getLogger("core").error("saved API credentials could not be loaded; keeping current task settings")
        return None
    finally:
        # A confirmed provider quota error disables that process-local account
        # so concurrent card jobs stop immediately.  Every paper/reread batch is
        # a fresh recovery boundary: reset even for source deployments that use
        # environment variables and do not opt into desktop credential reload.
        reset_account_pools()


def apply_saved_model_preferences():
    """Load one atomic snapshot before a paper/reread batch starts.

    Nothing calls this from inside ``process_paper`` or ``process_rereads``, so
    a preference save can never make one active task mix old and new models.
    """
    if not preferences.preference_path().is_file():
        return None
    try:
        return preferences.apply_and_record()
    except preferences.PreferenceError:
        logging.getLogger("core").exception("model preferences could not be loaded; keeping current task settings")
        return None


def rereads_pending() -> bool:
    return Question.objects.filter(reread_requested=True).exists()


def clean_saved_example_labels(out=None) -> None:
    """Tidy cards read by older versions (see library.tidy_saved_cards).

    A failure here must never stop the worker from reading papers."""
    try:
        from core.figure_candidate_repair import repair_lost_figure_candidates
        from core.library import tidy_saved_cards
        from core.models import Paper
        from core.pipeline import promote_saved_readings

        counts = tidy_saved_cards()
        converted = promote_saved_readings()
        # 升级后自动为历史丢失候选图的「可能漏图」题卡找回配图，只减黄、不增黄。
        repaired = repair_lost_figure_candidates(list(Paper.objects.filter(archived=False)), apply=True)
    except Exception:
        logging.getLogger("core").exception("example label cleanup failed")
        return
    if (counts["questions"] or counts["publications"]) and out is not None:
        out.write(f"已按新规则整理题面格式：{counts['questions']} 张题卡、{counts['publications']} 条已入库题目")
    if converted and out is not None:
        out.write(f"已恢复 {len(converted)} 道已有识读结果，可直接审核；未重新调用读题服务。")
    if repaired.recovered and out is not None:
        out.write(
            f"已自动找回 {repaired.recovered} 张历史丢失候选图的配图"
            f"（其中 {repaired.cleared_to_green} 张解除误报转绿）；未重新调用读题服务。"
        )


class Command(BaseCommand):
    help = "处理排队的试卷和重读请求"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="处理完当前任务就退出")

    def handle(self, *args, once=False, **options):
        lock = SingleInstance(settings.DATA_ROOT / "worker.lock")  # noqa: F841 — 持有到进程结束
        # A stale snapshot may survive a previous app run.  Once this worker
        # owns the lock, its startup configuration is authoritative even when
        # there is currently no queued work.
        apply_saved_settings()
        clean_saved_example_labels(self.stdout)
        try:
            library_jobs.recover_interrupted()
            region_reads.recover_interrupted()
            # 题卡这一路以前没有：上一轮识读死在写结果那一步时，题卡会永远停在
            # 「正在识读」，界面上一直转圈，还没有一句能说的话。
            recover_interrupted_readings()
        except Exception:
            logging.getLogger("core").exception("library job recovery failed")
        stop = threading.Event()
        if not once:
            threading.Thread(target=reread_lane, args=(stop,), name="reread-lane", daemon=True).start()
            threading.Thread(target=parse_lane, args=(stop,), name="parse-lane", daemon=True).start()
            threading.Thread(target=overlap_lane, args=(stop,), name="overlap-lane", daemon=True).start()
            threading.Thread(target=library_job_lane, args=(stop,), name="library-job-lane", daemon=True).start()
        while True:
            close_old_connections()
            library_job_control.touch_worker_heartbeat()
            worked = False
            try:
                for paper in (
                    Paper.objects.filter(status__in=ACTIVE)
                    .exclude(processing_plan__mode__in=["manual", "native"], processing_plan__mode__isnull=False)
                    .order_by("created_at")
                ):
                    if not claim_for_main_lane(paper):
                        continue
                    try:
                        # The parse lane may have moved it on since the query ran.
                        if hasattr(paper, "refresh_from_db"):
                            paper.refresh_from_db()
                            if paper.status not in ACTIVE:
                                continue
                        apply_saved_settings()
                        self.stdout.write(f"处理试卷 {paper.display_name}（{paper.get_status_display()}）")
                        sys.stdout.flush()
                        with lease_priority(paper_priority(paper)):
                            process_paper(paper)
                        worked = True
                    finally:
                        release_main_lane()
                if rereads_pending():
                    apply_saved_settings()
                    with REREAD_LOCK:
                        if process_rereads():
                            worked = True
                if region_reads.pending():
                    apply_saved_settings()
                    if region_reads.process_pending():
                        worked = True
                # --once 单轮模式在此同步跑完题库队列；常驻模式由独立的 library-job-lane 并行消费。
                if once and library_jobs.pending():
                    apply_saved_settings()
                    if library_jobs.process_pending():
                        worked = True
            except Exception:  # 工作者不能因为一次意外就退出
                logging.getLogger("core").exception("worker loop error")
                time.sleep(5)
            if once and not worked:
                return
            if not worked:
                time.sleep(2)
