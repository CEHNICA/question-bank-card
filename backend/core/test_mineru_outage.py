"""MinerU outages at a loopback HTTP server, through the real local pipeline.

Only a synthetic PDF and test token enter the fixture. HTTPS storage URLs are
rewritten by the test-only Session to the same loopback server; no cloud is used.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
from unittest import mock

import requests
from django.test import TestCase
from django.utils import timezone

from . import account_pool, library, mineru, pipeline, readers
from .models import Block, Paper, PublishedQuestion, Question
from . import test_manual_intake_review as originals


class _Clock:
    def __init__(self):
        self.elapsed = 0

    def monotonic(self):
        return self.elapsed

    def time(self):
        return 1_700_000_000 + self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


@contextmanager
def outage_transport(scenario):
    """Real HTTP refusal, 503, read timeout, empty queue or partial download."""
    counts = {"submit": 0, "upload": 0, "poll": 0, "download": 0}
    stopped = threading.Event()
    clock = _Clock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, payload, status=200):
            data = json.dumps(payload).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ConnectionError, OSError):
                pass  # A simulated read timeout has already closed its socket.

        def do_POST(self):
            counts["submit"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if scenario == "submit_timeout":
                stopped.wait(2)
                return
            if scenario == "submit_503":
                self.reply({"code": -10001}, 503)
                return
            self.reply({"code": 0, "data": {"batch_id": "fixture-only",
                "file_urls": ["https://fixture.invalid/upload"]}})

        def do_PUT(self):
            counts["upload"] += 1
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.reply({})

        def do_GET(self):
            if self.path == "/download":
                counts["download"] += 1
                if scenario == "download_503":
                    self.reply({}, 503)
                else:
                    # The response body disconnects before its stated length.
                    self.send_response(200)
                    self.send_header("Content-Length", "1000")
                    self.end_headers()
                    self.wfile.write(b"partial")
                    self.close_connection = True
                return
            counts["poll"] += 1
            if scenario == "poll_timeout":
                stopped.wait(2)
                return
            if scenario == "queue_empty":
                rows = []
            elif scenario == "queue_pending":
                rows = [{"state": "pending"}]
            else:
                rows = [{"state": "done", "full_zip_url": "https://fixture.invalid/download"}]
            self.reply({"code": 0, "data": {"extract_result": rows}})

    server = None
    if scenario == "refused":
        # A bound, non-listening port deterministically rejects connections.
        reserved = socket.socket()
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    else:
        reserved = None
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        port = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    origin = f"http://127.0.0.1:{port}"
    real_session = requests.Session

    class LocalSession(real_session):
        def __init__(self):
            super().__init__()
            self.trust_env = False

        def request(self, method, url, **kwargs):
            if url.startswith("https://fixture.invalid/"):
                url = origin + url[len("https://fixture.invalid"):]
            if not url.startswith(origin + "/"):
                raise AssertionError("Outage tests must not contact a remote host")
            kwargs["timeout"] = (0.1, 0.04)
            return super().request(method, url, **kwargs)

    try:
        with ExitStack() as patches:
            patches.enter_context(mock.patch.object(mineru.requests, "Session", LocalSession))
            patches.enter_context(mock.patch.object(mineru, "API_ROOT", origin + "/api/v4"))
            patches.enter_context(mock.patch.object(mineru, "time", clock))
            patches.enter_context(mock.patch.object(mineru, "WAIT_SILENT", 4))
            patches.enter_context(mock.patch.object(mineru, "WAIT_ALIVE", 6))
            patches.enter_context(mock.patch.dict("os.environ", {
                "MINERU_TOKEN": "offline-fixture-token", "MINERU_TOKENS_JSON": ""}))
            patches.enter_context(mock.patch.object(pipeline.logger, "exception"))
            account_pool.reset_account_pools()
            yield counts
    finally:
        account_pool.reset_account_pools()
        stopped.set()
        if server:
            server.shutdown()
            server.server_close()
            thread.join(2)
        if reserved:
            reserved.close()


class MineruOutagePipelineTests(TestCase):
    setUp = originals.ManualIntakeReviewTests.setUp
    paper = originals.ManualIntakeReviewTests.paper

    def post(self, paper, suffix, payload):
        return self.client.post(f"/api/papers/{paper.pk}/{suffix}", json.dumps(payload),
            content_type="application/json", HTTP_X_QB_REQUEST="1")

    def protected_paper(self, *, continuation=False, auto_fallback=False):
        paper = self.paper(mode="mineru", status="queued")
        approved = Question.objects.create(paper=paper, number=1, stem="Saved human answer $x=3$",
            options={"A": "human option"}, state="green", processing_mode="manual", edited=True,
            question_type="free_response", regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        library.approve(approved, now=timezone.now())
        approved.save()
        publication, _ = library.publish(approved)
        draft = Question.objects.create(paper=paper, number=2, stem="Retained manual correction",
            state="red", processing_mode="manual", edited=True, question_type="free_response",
            regions=[{"page_idx": 1, "bbox": [50, 50, 900, 420]}])
        paper.processing_plan.update(auto_fallback=auto_fallback)
        if continuation:
            paper.processing_plan.update(continue_preserve_existing=True,
                continue_revision=0, continue_existing_question_ids=[approved.pk, draft.pk])
        paper.save()
        return paper, publication

    def snapshot(self, paper, publication):
        return {
            "questions": list(paper.questions.order_by("pk").values()),
            "publication": PublishedQuestion.objects.values().get(pk=publication.pk),
            "source": Path(paper.source_path).read_bytes(),
            "pages": deepcopy(paper.pages),
            "blocks": list(paper.blocks.order_by("seq").values()),
            "files": {str(path.relative_to(self.root)): path.read_bytes()
                for path in (self.root / "library" / str(publication.pk)).rglob("*") if path.is_file()},
        }

    def assert_protected(self, paper, publication, before, *, switched=False):
        paper.refresh_from_db()
        after = self.snapshot(paper, publication)
        if switched:
            # Switching lanes intentionally invalidates OCR and makes red cards
            # manually reviewable. Every content and approval field is retained.
            before = deepcopy(before)
            for rows in (before["questions"], after["questions"]):
                for row in rows:
                    for key in ("processing_mode", "content_revision", "reread_requested",
                            "ocr_pending", "state", "updated_at"):
                        row.pop(key, None)
        self.assertEqual(after, before)
        self.assertFalse((pipeline.paper_dir(paper) / "mineru_result.zip").exists())
        self.assertFalse(list(pipeline.paper_dir(paper).glob(".parse-*")))

    def fail_then_manual(self, scenario, expected, counts_expected):
        paper, publication = self.protected_paper()
        Block.objects.create(paper=paper, seq=0, type="text", page_idx=0, text="Previous usable layout")
        before = self.snapshot(paper, publication)
        with outage_transport(scenario) as counts:
            pipeline.process_paper(paper)
            paper.refresh_from_db()
            self.assertEqual(paper.status, "failed")
            self.assertIn(expected, paper.error)
            for key, value in counts_expected.items():
                self.assertEqual(counts[key], value, (scenario, counts))
            self.assert_protected(paper, publication, before)
            shown = self.client.get(f"/api/papers/{paper.pk}").json()["paper"]
            self.assertEqual(len(shown["pages"]), 2)
            self.assertIsNone(shown["processing"])
            with mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud, \
                    mock.patch.object(readers, "chat") as read:
                switched = self.post(paper, "processing", {"mode": "manual", "revision": 0})
            self.assertEqual(switched.status_code, 200, switched.content)
            self.assertTrue(switched.json()["manual_ready"])
            self.assertEqual((switched.json()["paper"]["status"], switched.json()["paper"]["parse_mode"]),
                ("ready", "manual"))
            self.assert_protected(paper, publication, before, switched=True)
            cloud.assert_not_called()
            read.assert_not_called()

    def test_connection_refusal_keeps_originals_cards_and_publications(self):
        self.fail_then_manual("refused", "接口连接失败", {"submit": 0, "upload": 0})

    def test_service_503_is_bounded_and_does_not_blame_saved_credentials(self):
        self.fail_then_manual("submit_503", "服务暂时异常", {"submit": 3, "upload": 0})

    def test_continuous_submit_timeout_is_bounded_and_allows_manual(self):
        self.fail_then_manual("submit_timeout", "接口连接失败", {"submit": 3, "upload": 0})

    def test_continuous_poll_timeout_does_not_resubmit_original(self):
        self.fail_then_manual("poll_timeout", "接口连接失败", {"submit": 1, "upload": 1, "poll": 3})

    def test_empty_queue_has_deadline_and_keeps_local_manual_pages(self):
        self.fail_then_manual("queue_empty", "解析超时", {"submit": 1, "upload": 1, "poll": 2})

    def test_pending_queue_has_deadline_without_silent_resubmission(self):
        self.fail_then_manual("queue_pending", "排了一个小时", {"submit": 1, "upload": 1, "poll": 3})

    def test_done_result_download_503_does_not_adopt_partial_cache(self):
        self.fail_then_manual("download_503", "下载失败（HTTP 503）", {"submit": 1, "download": 3})

    def test_done_result_download_disconnect_does_not_adopt_partial_cache(self):
        self.fail_then_manual("download_disconnect", "下载失败（ChunkedEncodingError）", {"submit": 1, "download": 3})

    def test_retry_continued_parse_with_old_manual_cards_returns_to_mineru(self):
        paper, publication = self.protected_paper(continuation=True)
        before = self.snapshot(paper, publication)
        with outage_transport("submit_503") as first:
            pipeline.process_paper(paper)
        self.assertEqual(first["submit"], 3)
        self.assert_protected(paper, publication, before)
        with mock.patch("requests.sessions.Session.request", side_effect=AssertionError("Retry only queues work")):
            retry = self.post(paper, "retry", {})
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual(retry.json()["paper"]["status"], "queued")
        paper.refresh_from_db()
        self.assertTrue(pipeline._preserve_continued_cards(paper))
        self.assert_protected(paper, publication, before)
        # A still unavailable service is actually contacted by the queued retry;
        # old cards cannot make the task claim its missing cuts were completed.
        with outage_transport("submit_503") as second:
            pipeline.process_paper(paper)
        self.assertEqual(second["submit"], 3)
        paper.refresh_from_db()
        self.assertEqual(paper.status, "failed")
        self.assert_protected(paper, publication, before)

    def test_auto_fallback_after_outage_preserves_all_previous_results(self):
        paper, publication = self.protected_paper(auto_fallback=True)
        before = self.snapshot(paper, publication)
        with outage_transport("submit_503"):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))
        self.assert_protected(paper, publication, before, switched=True)

    def test_stop_while_remote_queue_is_live_does_not_adopt_late_progress(self):
        paper, publication = self.protected_paper()
        before = self.snapshot(paper, publication)
        real_record = mineru.record_state
        switches = []

        def queued_then_manual(path, info):
            real_record(path, info)
            if info["state"] == "pending":
                switches.append(self.post(paper, "processing", {"mode": "manual", "revision": 0}))

        with outage_transport("queue_pending") as counts, \
                mock.patch.object(mineru, "record_state", side_effect=queued_then_manual):
            pipeline.process_paper(paper)
        self.assertEqual(len(switches), 1)
        self.assertEqual(switches[0].status_code, 200, switches[0].content)
        self.assertEqual(counts["poll"], 1)
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"], paper.error), ("ready", "manual", ""))
        self.assert_protected(paper, publication, before, switched=True)

    def test_retry_after_reading_failure_retains_continuation_protection(self):
        paper, publication = self.protected_paper(continuation=True)
        Block.objects.create(paper=paper, seq=0, type="text", page_idx=0, text="Saved parsing block")
        paper.status, paper.error = "failed", "Synthetic reader failure"
        paper.save()
        before = self.snapshot(paper, publication)
        result = self.post(paper, "retry", {})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(result.json()["paper"]["status"], "reading")
        paper.refresh_from_db()
        self.assertTrue(pipeline._preserve_continued_cards(paper))
        with mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud, \
                mock.patch.object(pipeline, "read_questions", wraps=pipeline.read_questions) as reading:
            pipeline.process_paper(paper)
        cloud.assert_not_called()
        self.assertEqual(reading.call_args.args[1], [])
        paper.refresh_from_db()
        self.assertEqual(paper.status, "ready")
        self.assert_protected(paper, publication, before)
