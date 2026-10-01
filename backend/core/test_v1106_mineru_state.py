"""1.10.6：交给 MinerU 以后，页面显示 MinerU 自己报的状态（排队、第几页、打包）。

全部离线：模拟 MinerU 的接口回答，不联网。
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase

from . import mineru
from . import test_v110_types_origin as v110
from .models import Paper
from .pipeline import paper_dir


class StateNoteTests(SimpleTestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / mineru.MINERU_STATE_FILE

    def test_queue_time_counts_from_the_upload_and_pages_are_kept(self):
        with mock.patch.object(mineru.time, "time", return_value=1000.0):
            mineru.record_state(self.path, {"state": "submitted"})
        with mock.patch.object(mineru.time, "time", return_value=1060.0):
            mineru.record_state(self.path, {"state": "pending"})
        with mock.patch.object(mineru.time, "time", return_value=1090.0):
            self.assertEqual(mineru.read_state(self.path), {"state": "pending", "for_seconds": 90})
            mineru.record_state(self.path, {"state": "running", "pages": 9, "total_pages": 4})
        with mock.patch.object(mineru.time, "time", return_value=1100.0):
            self.assertEqual(mineru.read_state(self.path),
                             {"state": "running", "for_seconds": 10, "pages": 4, "total_pages": 4})

    def test_an_old_or_broken_note_is_ignored(self):
        with mock.patch.object(mineru.time, "time", return_value=1000.0):
            mineru.record_state(self.path, {"state": "pending"})
        with mock.patch.object(mineru.time, "time", return_value=1000.0 + mineru.STATE_MAX_AGE + 1):
            self.assertIsNone(mineru.read_state(self.path))
        self.path.write_text("not json", encoding="utf-8")
        self.assertIsNone(mineru.read_state(self.path))
        self.assertIsNone(mineru.read_state(self.path.with_name("missing.json")))


class PollingReportsStateTests(SimpleTestCase):
    def test_the_states_minerus_answers_are_passed_on(self):
        answers = iter([
            {"extract_result": [{"state": "pending"}]},
            {"extract_result": [{"state": "running", "extract_progress": {"extracted_pages": 2, "total_pages": 4}}]},
            {"extract_result": [{"state": "converting"}]},
            {"extract_result": [{"state": "done", "full_zip_url": "https://example.invalid/r.zip"}]},
        ])

        def api(_session, _token, path, payload=None):
            if path == "file-urls/batch":
                return {"batch_id": "b1", "file_urls": ["https://example.invalid/upload"]}, "t"
            return next(answers), "t"

        folder = Path(tempfile.mkdtemp())
        source = folder / "paper.pdf"
        source.write_bytes(b"%PDF-1.4")
        seen = []
        ok = mock.Mock(ok=True, status_code=200)
        with mock.patch.object(mineru, "_api_json", side_effect=api), \
                mock.patch.object(mineru.requests.Session, "put", return_value=ok), \
                mock.patch.object(mineru, "_download_zip"), \
                mock.patch.object(mineru.time, "sleep"):
            mineru.request_extract_file("token", source, folder / "r.zip", 4, on_state=seen.append)
        self.assertEqual([item["state"] for item in seen],
                         ["uploading", "submitted", "pending", "running", "converting", "downloading"])
        self.assertEqual(seen[3], {"state": "running", "pages": 2, "total_pages": 4})

    def test_a_failing_callback_does_not_stop_parsing(self):
        def api(_session, _token, path, payload=None):
            if path == "file-urls/batch":
                return {"batch_id": "b1", "file_urls": ["https://example.invalid/upload"]}, "t"
            return {"extract_result": [{"state": "done", "full_zip_url": "https://example.invalid/r.zip"}]}, "t"

        folder = Path(tempfile.mkdtemp())
        source = folder / "paper.pdf"
        source.write_bytes(b"%PDF-1.4")
        with mock.patch.object(mineru, "_api_json", side_effect=api), \
                mock.patch.object(mineru.requests.Session, "put", return_value=mock.Mock(ok=True)), \
                mock.patch.object(mineru, "_download_zip"):
            target = mineru.request_extract_file("token", source, folder / "r.zip", 1,
                                                 on_state=mock.Mock(side_effect=OSError("disk")))
        self.assertEqual(target, folder / "r.zip")


class PaperShowsMinerUStateTests(v110.TempDataMixin, TestCase):
    def test_the_page_gets_minerus_state_while_parsing(self):
        self.use_temp_data()
        paper = self.make_paper()
        Paper.objects.filter(pk=paper.pk).update(status=Paper.Status.PARSING)
        paper.refresh_from_db()
        note = paper_dir(paper) / mineru.MINERU_STATE_FILE
        mineru.record_state(note, {"state": "running", "pages": 1, "total_pages": 4})
        processing = Client().get(f"/api/papers/{paper.id}").json()["paper"]["processing"]
        self.assertEqual(processing["mineru"]["state"], "running")
        self.assertEqual((processing["determinate"], processing["completed"], processing["total"], processing["unit"]),
                         (True, 1, 4, "page"))
        note.unlink()
        processing = Client().get(f"/api/papers/{paper.id}").json()["paper"]["processing"]
        self.assertNotIn("mineru", processing)
        self.assertFalse(processing["determinate"])
