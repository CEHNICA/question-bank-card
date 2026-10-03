"""The worker's reread lane serves finished papers while a long task runs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import account_pool, pipeline, readers
from .management.commands import run_worker
from .models import Paper, Question
from .tests import PAGES, ScriptedChat, fake_page_pdf, tagged


class RereadLaneTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict("os.environ", {"MINIMAX_API_KEY": "test", "SILICONFLOW_API_KEY": "test2"})
        env.start()
        self.addCleanup(env.stop)

    def paper(self, status: str) -> Paper:
        paper = Paper.objects.create(filename=f"{status}.pdf", kind="pdf", sha256=status[0] * 64, status=status)
        folder = self.temp / str(paper.id)
        folder.mkdir(parents=True)
        fake_page_pdf(folder / "source.pdf")
        paper.source_path = str(folder / "source.pdf")
        paper.pages = PAGES[:1]
        paper.save()
        return paper

    def card(self, paper: Paper) -> Question:
        region = [{"page_idx": 0, "bbox": [50, 100, 480, 200]}]
        return Question.objects.create(
            paper=paper, number=1, question_type="free_response", regions=region, regions_auto=region,
            state=Question.State.WAITING, reread_requested=True,
        )

    def test_idle_only_rereads_skip_papers_still_in_progress(self):
        ready, busy = self.paper(Paper.Status.READY), self.paper(Paper.Status.READING)
        done, waiting = self.card(ready), self.card(busy)
        chat = ScriptedChat({("*", 1): tagged("已知 $x=1$，求 $y$ 的值。")})
        with mock.patch.object(readers, "chat", chat):
            pipeline.process_rereads(idle_papers_only=True)
        done.refresh_from_db()
        waiting.refresh_from_db()
        self.assertFalse(done.reread_requested)
        self.assertIn(done.state, {Question.State.GREEN, Question.State.YELLOW})
        self.assertTrue(waiting.reread_requested)
        self.assertEqual(waiting.state, Question.State.WAITING)

    def test_lane_serves_a_reread_and_stops_when_asked(self):
        ready = self.paper(Paper.Status.READY)
        card = self.card(ready)

        class OneTurn:
            """Stop after a single lane iteration, without real threads."""

            def __init__(self):
                self.turns = 0

            def is_set(self):
                return self.turns > 0

            def wait(self, _interval):
                self.turns += 1

        stop = OneTurn()
        chat = ScriptedChat({("*", 1): tagged("已知 $x=1$，求 $y$ 的值。")})
        with mock.patch.object(readers, "chat", chat), \
                mock.patch.object(run_worker, "close_old_connections"):
            run_worker.reread_lane(stop, 0)
        card.refresh_from_db()
        self.assertEqual(stop.turns, 1)
        self.assertFalse(card.reread_requested)
        self.assertFalse(run_worker.REREAD_LOCK.locked())


class ParseLaneTests(TestCase):
    """While one paper is read, the next queued paper is already parsed."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(run_worker.release_main_lane)

    def paper(self, status: str, name: str) -> Paper:
        return Paper.objects.create(filename=f"{name}.pdf", kind="pdf", sha256=name[0] * 64, status=status)

    class Turns:
        def __init__(self, turns):
            self.left = turns

        def is_set(self):
            return self.left <= 0

        def wait(self, _interval):
            self.left -= 1

    def test_next_queued_paper_is_parsed_while_the_main_lane_reads(self):
        reading = self.paper(Paper.Status.READING, "reading")
        queued = self.paper(Paper.Status.QUEUED, "queued")
        parsed = []

        def fake_parse(paper, **kwargs):
            parsed.append(paper.pk)
            self.assertIn(paper.pk, run_worker.PARSING_AHEAD)
            Paper.objects.filter(pk=paper.pk).update(status=Paper.Status.SEGMENTING)

        self.assertTrue(run_worker.claim_for_main_lane(reading))
        with mock.patch.object(pipeline, "parse", side_effect=fake_parse), \
                mock.patch.object(run_worker, "close_old_connections"):
            run_worker.parse_lane(self.Turns(1), 0)
        queued.refresh_from_db()
        self.assertEqual(parsed, [queued.pk])
        self.assertEqual(queued.status, Paper.Status.SEGMENTING)
        self.assertEqual(run_worker.PARSING_AHEAD, set())

    def test_lane_waits_while_the_main_lane_is_idle(self):
        self.paper(Paper.Status.QUEUED, "queued")
        with mock.patch.object(pipeline, "parse") as parse, \
                mock.patch.object(run_worker, "close_old_connections"):
            run_worker.parse_lane(self.Turns(1), 0)
        parse.assert_not_called()

    def test_main_lane_skips_a_paper_being_parsed_ahead(self):
        queued = self.paper(Paper.Status.QUEUED, "queued")
        run_worker.PARSING_AHEAD.add(queued.pk)
        self.addCleanup(run_worker.PARSING_AHEAD.discard, queued.pk)
        self.assertFalse(run_worker.claim_for_main_lane(queued))
        run_worker.PARSING_AHEAD.discard(queued.pk)
        self.assertTrue(run_worker.claim_for_main_lane(queued))

    def test_a_failed_parse_ahead_is_recorded_like_any_failure(self):
        queued = self.paper(Paper.Status.QUEUED, "queued")
        with mock.patch.object(pipeline, "parse", side_effect=pipeline.MineruError("MinerU 文件上传失败")):
            self.assertFalse(pipeline.parse_ahead(queued))
        queued.refresh_from_db()
        self.assertEqual(queued.status, Paper.Status.FAILED)
        self.assertIn("MinerU", queued.error)
        ready = self.paper(Paper.Status.READY, "ready")
        with mock.patch.object(pipeline, "parse") as parse:
            self.assertFalse(pipeline.parse_ahead(ready))
        parse.assert_not_called()


class OverlapLaneTests(TestCase):
    """The next parsed paper starts reading while the current one finishes its tail."""

    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(run_worker.release_main_lane)
        self.addCleanup(run_worker.OVERLAP_CURRENT.__setitem__, "paper", None)
        self.addCleanup(run_worker.SETTINGS_APPLIED.__setitem__, "signature", None)
        run_worker.SETTINGS_APPLIED["signature"] = run_worker.settings_signature()

    def paper(self, status: str, name: str) -> Paper:
        return Paper.objects.create(filename=f"{name}.pdf", kind="pdf", sha256=name[0] * 64, status=status)

    def tail(self, paper, backlog):
        pipeline._set_backlog(paper.pk, backlog)
        self.addCleanup(pipeline._set_backlog, paper.pk, None)

    def run_lane(self):
        seen = []

        def fake_process(paper):
            seen.append(paper.pk)
            self.assertEqual(run_worker.OVERLAP_CURRENT["paper"], paper.pk)
            # Leases taken for this paper queue behind older uploads.
            self.assertEqual(account_pool.current_priority(), run_worker.paper_priority(paper))
            Paper.objects.filter(pk=paper.pk).update(status=Paper.Status.READY)

        with mock.patch.object(run_worker, "process_paper", side_effect=fake_process), \
                mock.patch.object(run_worker, "close_old_connections"):
            run_worker.overlap_lane(ParseLaneTests.Turns(1), 0)
        self.assertIsNone(run_worker.OVERLAP_CURRENT["paper"])
        return seen

    def test_next_parsed_paper_starts_once_the_current_one_is_in_its_tail(self):
        reading = self.paper(Paper.Status.READING, "reading")
        parsed = self.paper(Paper.Status.SEGMENTING, "segmenting")
        self.assertTrue(run_worker.claim_for_main_lane(reading))
        self.tail(reading, 3)
        self.assertEqual(self.run_lane(), [])
        self.tail(reading, 0)
        self.assertEqual(self.run_lane(), [parsed.pk])

    def test_never_jumps_past_a_paper_still_at_mineru(self):
        reading = self.paper(Paper.Status.READING, "reading")
        self.paper(Paper.Status.PARSING, "parsing")
        self.paper(Paper.Status.SEGMENTING, "segmenting")
        self.assertTrue(run_worker.claim_for_main_lane(reading))
        self.tail(reading, 0)
        self.assertEqual(self.run_lane(), [])

    def test_waits_while_saved_settings_are_pending(self):
        reading = self.paper(Paper.Status.READING, "reading")
        self.paper(Paper.Status.SEGMENTING, "segmenting")
        self.assertTrue(run_worker.claim_for_main_lane(reading))
        self.tail(reading, 0)
        run_worker.SETTINGS_APPLIED["signature"] = ("changed",)
        self.assertEqual(self.run_lane(), [])

    def test_main_lane_waits_for_the_overlap_paper_to_reach_its_tail(self):
        overlapping = self.paper(Paper.Status.READING, "overlap")
        later = self.paper(Paper.Status.SEGMENTING, "later")
        run_worker.OVERLAP_CURRENT["paper"] = overlapping.pk
        self.assertFalse(run_worker.claim_for_main_lane(overlapping))
        self.tail(overlapping, 4)
        self.assertFalse(run_worker.claim_for_main_lane(later))
        self.tail(overlapping, 0)
        self.assertTrue(run_worker.claim_for_main_lane(later))

    def test_settings_are_not_switched_under_a_paper_mid_read(self):
        run_worker.OVERLAP_CURRENT["paper"] = 12345
        with mock.patch.object(run_worker, "apply_saved_credentials") as credentials, \
                mock.patch.object(run_worker, "apply_saved_model_preferences") as models:
            self.assertFalse(run_worker.apply_saved_settings())
            credentials.assert_not_called()
            models.assert_not_called()
            run_worker.OVERLAP_CURRENT["paper"] = None
            self.assertTrue(run_worker.apply_saved_settings())
            credentials.assert_called_once()
            models.assert_called_once()
        self.assertFalse(run_worker.settings_pending())

    def test_reading_reports_its_backlog_until_the_last_card_is_handed_out(self):
        paper = self.paper(Paper.Status.READING, "reading")
        questions = [Question.objects.create(paper=paper, number=n, regions=[]) for n in (1, 2, 3)]
        observed = []

        def fake_read(snapshot, _store):
            observed.append(pipeline.reading_tail(paper.pk))
            return {"state": Question.State.RED, "error": "x", "flags": []}

        with mock.patch.object(pipeline, "_reader_parallelism", return_value=1), \
                mock.patch.object(pipeline, "read_card", side_effect=fake_read):
            pipeline.read_questions(paper, questions)
        self.assertEqual(observed, [False, False, True])
        self.assertFalse(pipeline.reading_tail(paper.pk))


class LeasePriorityTests(TestCase):
    def test_older_paper_gets_a_freed_slot_before_a_newer_one(self):
        import threading
        import time

        pool = account_pool.AccountPool("minimax", ("k",), per_account=1)
        order = []
        holder = pool._acquire()

        def wait_for_slot(priority, name):
            with account_pool.lease_priority(priority), pool.lease():
                order.append(name)

        newer = threading.Thread(target=wait_for_slot, args=(200.0, "newer"))
        newer.start()
        time.sleep(0.05)
        older = threading.Thread(target=wait_for_slot, args=(100.0, "older"))
        older.start()
        time.sleep(0.05)
        pool._release(holder)
        newer.join(5)
        older.join(5)
        self.assertEqual(order, ["older", "newer"])

    def test_spare_slots_are_shared_when_nobody_more_urgent_waits(self):
        pool = account_pool.AccountPool("minimax", ("k",), per_account=2)
        with account_pool.lease_priority(100.0), pool.lease():
            with account_pool.lease_priority(200.0), pool.lease():
                self.assertEqual(pool.spare, 0)
