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
