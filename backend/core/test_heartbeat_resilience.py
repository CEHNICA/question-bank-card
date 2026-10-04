"""一次心跳失败不该杀死一次正在正常进行的 MinerU 解析。

worker.log 里出现过两次 `OperationalError: database is locked`，栈是
`mineru.request_extract_file → pipeline.heartbeat → _paper_heartbeat`：等
MinerU 的时候另一个连接正写库，心跳一撞上就抛出，等于把一个本来正常的
云端任务判成失败。心跳是整轮运行里唯一没有人等待的写入，丢掉它只会让
“正在解析”多停几秒；让它冒出去则赔上整轮。
"""

from unittest import mock

from django.db import connection
from django.db.utils import OperationalError
from django.test import TestCase, TransactionTestCase, override_settings

from . import pipeline
from .models import Paper
from . import test_manual_intake_review as manual_review


class HeartbeatSurvivesALockedDatabaseTests(TestCase):
    paper = manual_review.ManualIntakeReviewTests.paper
    setUp = manual_review.ManualIntakeReviewTests.setUp

    def test_a_locked_database_costs_one_heartbeat_not_the_run(self):
        paper = self.paper()
        with mock.patch.object(pipeline.Paper.objects, "select_for_update",
                               side_effect=OperationalError("database is locked")):
            # The wait loop calls this on a timer; returning normally is the
            # whole point.  Raising here is what killed a working MinerU job.
            pipeline._paper_heartbeat(paper.pk, revision=0)
            pipeline._paper_heartbeat(paper.pk, progress=1, total=2, revision=0)

    def test_a_dropped_connection_is_not_handed_to_the_next_query(self):
        # A rolled-back atomic block leaves this connection mid-transaction;
        # the next statement on it would fail for an unrelated reason.
        paper = self.paper()
        with mock.patch.object(pipeline.Paper.objects, "select_for_update",
                               side_effect=OperationalError("database is locked")), \
                mock.patch.object(pipeline.connection, "close") as closed:
            pipeline._paper_heartbeat(paper.pk, revision=0)
        closed.assert_called_once()

    def test_a_healthy_heartbeat_still_saves_progress(self):
        paper = self.paper()
        pipeline._paper_heartbeat(paper.pk, progress=3, total=7, revision=0)
        paper.refresh_from_db()
        self.assertEqual((paper.progress, paper.total), (3, 7))

    def test_a_run_the_teacher_moved_on_is_still_not_touched(self):
        paper = self.paper()
        paper.processing_plan = {**paper.processing_plan, "revision": 4}
        paper.save()
        pipeline._paper_heartbeat(paper.pk, progress=9, revision=3)
        paper.refresh_from_db()
        self.assertEqual(paper.progress, 0)


class SqliteWritesQueueInsteadOfFailingTests(TransactionTestCase):
    """A deferred transaction cannot honour the busy timeout; IMMEDIATE can.

    ``BEGIN DEFERRED`` opens as a reader and only asks for the write lock when
    it first writes.  If another connection has written in between, SQLite
    reports "database is locked" immediately and never enters the busy handler,
    so ``timeout: 30`` buys nothing.  ``transaction_mode: IMMEDIATE`` takes the
    write lock at BEGIN, which is what turns a lost race into a wait.
    """

    databases = {"default"}

    def test_write_transactions_begin_immediate(self):
        options = connection.settings_dict["OPTIONS"]
        self.assertEqual(options.get("transaction_mode"), "IMMEDIATE")
        self.assertEqual(options.get("timeout"), 30)
