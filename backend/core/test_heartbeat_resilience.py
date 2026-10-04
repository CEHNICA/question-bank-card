"""MinerU heartbeats are optional writes and must survive real SQLite contention."""

import sqlite3
import time
from unittest import mock

from django.db import connection
from django.db.models.query import QuerySet
from django.db.utils import OperationalError
from django.test import TransactionTestCase

from . import pipeline
from . import test_manual_intake_review as manual_review
from .models import Paper


class HeartbeatSurvivesALockedDatabaseTests(TransactionTestCase):
    databases = {"default"}
    paper = manual_review.ManualIntakeReviewTests.paper
    setUp = manual_review.ManualIntakeReviewTests.setUp

    def test_a_real_second_connection_lock_drops_one_tick_and_the_next_succeeds(self):
        database = str(connection.settings_dict["NAME"])
        paper = self.paper()
        blocker = sqlite3.connect(database, timeout=0.1, isolation_level=None, uri=database.startswith("file:"))
        self.addCleanup(blocker.close)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            started = time.monotonic()
            with self.assertLogs("core.pipeline", level="WARNING") as captured:
                pipeline._paper_heartbeat(paper.pk, progress=1, total=2, revision=0)
                pipeline._paper_heartbeat(paper.pk, progress=1, total=2, revision=0)
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertEqual(len(captured.records), 1, "repeated lock misses should not flood the log")
            paper.refresh_from_db()
            self.assertEqual((paper.progress, paper.total), (0, 0), "a skipped tick cannot claim to be saved")
        finally:
            blocker.rollback()

        pipeline._paper_heartbeat(paper.pk, progress=2, total=2, revision=0)
        paper.refresh_from_db()
        self.assertEqual((paper.progress, paper.total), (2, 2))
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA busy_timeout")
            self.assertEqual(cursor.fetchone()[0], 30000, "the heartbeat timeout must be restored")

    def test_non_lock_operational_errors_still_surface(self):
        paper = self.paper()
        with mock.patch.object(QuerySet, "update", side_effect=OperationalError("database schema is corrupt")):
            with self.assertRaisesRegex(OperationalError, "schema is corrupt"):
                pipeline._paper_heartbeat(paper.pk, progress=3, total=7, revision=0)

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

    def test_a_legacy_plan_without_revision_is_revision_zero(self):
        paper = self.paper()
        paper.processing_plan = {key: value for key, value in paper.processing_plan.items() if key != "revision"}
        paper.save()
        pipeline._paper_heartbeat(paper.pk, progress=1, revision=0)
        paper.refresh_from_db()
        self.assertEqual(paper.progress, 1)

    def test_sqlite_transaction_mode_remains_application_default(self):
        options = connection.settings_dict["OPTIONS"]
        self.assertEqual(options.get("timeout"), 30)
        self.assertNotIn("transaction_mode", options)
