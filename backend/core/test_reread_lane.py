"""The worker's reread lane serves finished papers while a long task runs."""

from __future__ import annotations

import shutil
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.test import TransactionTestCase, override_settings

from . import pipeline, readers
from .management.commands import run_worker
from .models import Paper, Question
from .tests import PAGES, ScriptedChat, fake_page_pdf, tagged


class RereadLaneTests(TransactionTestCase):
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

    def test_lane_runs_in_background_and_stops_cleanly(self):
        ready = self.paper(Paper.Status.READY)
        card = self.card(ready)
        stop = threading.Event()
        chat = ScriptedChat({("*", 1): tagged("已知 $x=1$，求 $y$ 的值。")})
        with mock.patch.object(readers, "chat", chat):
            lane = threading.Thread(target=run_worker.reread_lane, args=(stop, 0.05), daemon=True)
            lane.start()
            for _ in range(100):
                card.refresh_from_db()
                if not card.reread_requested:
                    break
                stop.wait(0.05)
            stop.set()
            lane.join(timeout=5)
        self.assertFalse(lane.is_alive())
        self.assertFalse(card.reread_requested)
