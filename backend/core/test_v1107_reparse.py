"""1.10.7：一份试卷在 MinerU 那里等太久，可以“重新交给 MinerU 解析”。

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
from .models import ImportChunk, Paper
from .pipeline import paper_dir


def fake_api(states):
    answers = iter(states)

    def api(_session, _token, path, payload=None):
        if path == "file-urls/batch":
            return {"batch_id": "b1", "file_urls": ["https://example.invalid/upload"]}, "t"
        return {"extract_result": [next(answers)]}, "t"
    return api


class PollingStopsOnRequestTests(SimpleTestCase):
    def test_a_restart_request_stops_waiting_for_the_old_task(self):
        folder = Path(tempfile.mkdtemp())
        source = folder / "paper.pdf"
        source.write_bytes(b"%PDF-1.4")
        asked = iter([False, True])
        with mock.patch.object(mineru, "_api_json", side_effect=fake_api([{"state": "pending"}] * 5)), \
                mock.patch.object(mineru.requests.Session, "put", return_value=mock.Mock(ok=True)), \
                mock.patch.object(mineru.time, "sleep"), \
                self.assertRaises(mineru.MineruRestart):
            mineru.request_extract_file("token", source, folder / "r.zip", 4, restart=lambda: next(asked))


class PipelineSendsAgainTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def test_the_file_is_uploaded_again_and_the_request_is_cleared(self):
        folder = paper_dir(self.paper)
        restart_file = folder / mineru.RESTART_FILE
        calls = []

        def extract(_source, archive, _pages, heartbeat=None, on_state=None, restart=None):
            calls.append(restart())
            if len(calls) == 1:
                restart_file.write_text("restart", encoding="utf-8")  # the page asks while MinerU is slow
                assert restart()
                raise mineru.MineruRestart()
            archive.write_bytes(b"zip")

        blocks = [{"seq": 1, "type": "text", "page_idx": 0, "bbox": [20, 30, 900, 80], "text": "1. 已知"}]
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract), \
                mock.patch.object(pipeline, "load_blocks", return_value=blocks), \
                mock.patch.object(pipeline, "_plan_structure", return_value=({}, False)):
            pipeline.parse(self.paper)
        self.assertEqual(calls, [False, False])
        self.assertFalse(restart_file.exists())

    def test_a_leftover_request_does_not_cancel_a_fresh_parse(self):
        folder = paper_dir(self.paper)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / mineru.RESTART_FILE).write_text("restart", encoding="utf-8")
        seen = []

        def extract(_source, archive, _pages, heartbeat=None, on_state=None, restart=None):
            seen.append(restart())
            archive.write_bytes(b"zip")

        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract), \
                mock.patch.object(pipeline, "load_blocks", return_value=[]), \
                mock.patch.object(pipeline, "_plan_structure", return_value=({}, False)):
            pipeline.parse(self.paper)
        self.assertEqual(seen, [False])


class ReparseApiTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def post(self):
        return Client().post(f"/api/papers/{self.paper.id}/reparse", data=json.dumps({}),
                             content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_only_a_paper_waiting_on_minerU_can_be_sent_again(self):
        self.assertEqual(self.post().status_code, 409)  # ready
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.PARSING)
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue((paper_dir(self.paper) / mineru.RESTART_FILE).exists())

    def test_a_book_in_chunks_is_refused(self):
        Paper.objects.filter(pk=self.paper.pk).update(status=Paper.Status.PARSING)
        ImportChunk.objects.create(paper=self.paper, sequence=1, source_page_start=1, source_page_end=100)
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse((paper_dir(self.paper) / mineru.RESTART_FILE).exists())
