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

from core import credential_settings, preferences
from core.account_pool import lease_priority, reset_account_pools
from core.models import Paper, Question
from core.pipeline import parse_ahead, process_paper, process_rereads, reading_tail

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
                candidate = (Paper.objects.filter(status=Paper.Status.QUEUED).exclude(pk=current)
                             .order_by("created_at").first())
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
                candidate = (Paper.objects.filter(status__in=ACTIVE).exclude(pk=current)
                             .order_by("created_at").first())
                # Strictly in upload order: never jump past a paper still at MinerU.
                if candidate is not None and candidate.status in (
                        Paper.Status.SEGMENTING, Paper.Status.READING):
                    with LANES_LOCK:
                        if (MAIN_CURRENT["paper"] == current and candidate.pk not in PARSING_AHEAD
                                and OVERLAP_CURRENT["paper"] is None):
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
        logging.getLogger("core").error(
            "saved API credentials could not be loaded; keeping current task settings"
        )
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
            raise SystemExit("已有一个题有据工作者在运行，本进程退出。")


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
        stop = threading.Event()
        if not once:
            threading.Thread(target=reread_lane, args=(stop,), name="reread-lane", daemon=True).start()
            threading.Thread(target=parse_lane, args=(stop,), name="parse-lane", daemon=True).start()
            threading.Thread(target=overlap_lane, args=(stop,), name="overlap-lane", daemon=True).start()
        while True:
            close_old_connections()
            worked = False
            try:
                for paper in Paper.objects.filter(status__in=ACTIVE).order_by("created_at"):
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
            except Exception:  # 工作者不能因为一次意外就退出
                logging.getLogger("core").exception("worker loop error")
                time.sleep(5)
            if once and not worked:
                return
            if not worked:
                time.sleep(2)
