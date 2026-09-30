"""The worker's reread lane serves finished papers while a long task runs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import pipeline, readers
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

        def fake_parse(paper):
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
