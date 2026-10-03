"""1.10.8：排队中或在等 MinerU 的任务可以“停止处理”，停下来以后就能删除。

全部离线：模拟 MinerU 的接口回答，不联网。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase

from . import mineru, pipeline
from . import test_v110_types_origin as v110
from .models import Paper
from .pipeline import paper_dir
from .test_v1107_reparse import fake_api


class PollingStopsTests(SimpleTestCase):
    def test_a_stop_request_ends_the_wait(self):
        folder = Path(tempfile.mkdtemp())
        source = folder / "paper.pdf"
        source.write_bytes(b"%PDF-1.4")
        asked = iter([False, True])
        with mock.patch.object(mineru, "_api_json", side_effect=fake_api([{"state": "pending"}] * 5)), \
                mock.patch.object(mineru.requests.Session, "put", return_value=mock.Mock(ok=True)), \
                mock.patch.object(mineru.time, "sleep"), \
                self.assertRaises(mineru.MineruCancelled):
            mineru.request_extract_file("token", source, folder / "r.zip", 4, cancel=lambda: next(asked))


class StopTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def post(self, action):
        return Client().post(f"/api/papers/{self.paper.id}/{action}", data=json.dumps({}),
                             content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_a_queued_paper_stops_at_once_and_can_be_deleted(self):
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.QUEUED)
        response = self.post("stop")
        self.assertEqual(response.status_code, 200, response.content)
        shown = response.json()["paper"]
        self.assertEqual((shown["status"], shown["stopped"], shown["status_label"]), ("failed", True, "已停止"))
        deleted = Client().delete(f"/api/papers/{self.paper.id}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertFalse(Paper.objects.filter(pk=self.paper.pk).exists())

    def test_a_paper_waiting_on_minerU_is_stopped_by_the_worker(self):
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.PARSING)
        response = self.post("stop")
        self.assertEqual(response.json()["stopped"], True)
        cancel_file = paper_dir(self.paper) / mineru.CANCEL_FILE
        self.assertTrue(cancel_file.exists())

        def extract(_source, archive, _pages, heartbeat=None, on_state=None, restart=None, cancel=None):
            assert cancel()
            raise mineru.MineruCancelled()

        self.paper.refresh_from_db()
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract):
            pipeline.process_paper(self.paper)
        self.paper.refresh_from_db()
        self.assertEqual((self.paper.status, self.paper.error), (Paper.Status.FAILED, mineru.STOPPED_MESSAGE))
        # No live worker is required; an obsolete worker must leave the stop
        # marker alone. Retry removes it for the new generation.
        self.assertTrue(cancel_file.exists())
        # 重试 starts it again; a stop request the worker never saw does not stop the retry.
        cancel_file.write_text("stop", encoding="utf-8")
        self.assertEqual(self.post("retry").status_code, 200)
        self.assertFalse(cancel_file.exists())

    def test_finished_papers_need_no_stop(self):
        self.assertEqual(self.post("stop").status_code, 409)
