"""Real isolated originals and offline race boundaries; never a user API/key."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
from unittest import mock
import zipfile

from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone

from . import account_pool, intake, library, mineru, pipeline, readers, views
from .models import Block, ImportChunk, Paper, Question
from . import test_local_intake as local_tests, test_manual_intake_review as review_tests
from .test_resilience import _Session
from .test_v1107_reparse import fake_api


class StopAndAutoIntakeTests(TestCase):
    paper = review_tests.ManualIntakeReviewTests.paper
    pdf = local_tests.LocalIntakeTests.pdf

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = self.folder = Path(directory.name)
        config = override_settings(DATA_ROOT=self.root)
        config.enable()
        self.addCleanup(config.disable)

    def action(self, paper, name):
        return self.client.post(f"/api/papers/{paper.pk}/{name}", "{}",
            content_type="application/json", HTTP_X_QB_REQUEST="1")

    def upload(self, *, scan=False, allow_cloud=None, services=True):
        file = io.BytesIO(self.pdf(scan=scan).read_bytes())
        file.name = "original.pdf"
        data = {"file": file, "parse_mode": "auto"}
        if allow_cloud is not None:
            data["allow_cloud"] = allow_cloud
        with mock.patch.object(views.readers, "configured", return_value=services), \
                mock.patch.object(views, "_reading_ready", return_value=services), \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud:
            response = self.client.post("/api/papers", data, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        cloud.assert_not_called()
        return Paper.objects.get(pk=response.json()["paper"]["id"])

    def test_sixteen_hour_stale_stop_completes_without_worker_and_is_idempotent(self):
        paper = self.paper(mode="mineru", status="parsing")
        original = Path(paper.source_path).read_bytes()
        Paper.objects.filter(pk=paper.pk).update(updated_at=timezone.now() - timedelta(hours=16))
        (pipeline.paper_dir(paper) / mineru.CANCEL_FILE).write_text("old stop")
        response = self.action(paper, "stop")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["stopped"])
        self.assertNotIn("几秒", response.json()["message"])
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.error), ("failed", mineru.STOPPED_MESSAGE))
        revision = paper.processing_plan["revision"]
        self.assertTrue(self.action(paper, "stop").json()["stopped"])
        paper.refresh_from_db()
        self.assertEqual(paper.processing_plan["revision"], revision)
        self.assertEqual(Path(paper.source_path).read_bytes(), original)
        deleted = self.client.delete(f"/api/papers/{paper.pk}", HTTP_X_QB_REQUEST="1")
        self.assertEqual(deleted.status_code, 200, deleted.content)

    def test_stop_all_active_phases_and_keep_completed_chunk_cache(self):
        for status in ("queued", "parsing", "segmenting", "reading"):
            with self.subTest(status=status):
                paper = self.paper(mode="mineru", status=status)
                archive = pipeline.paper_dir(paper) / "completed.zip"
                archive.write_bytes(b"saved successful result")
                chunk = ImportChunk.objects.create(paper=paper, sequence=1, source_page_start=1,
                    source_page_end=2, page_map=[1, 2], status="parsed", artifact_path=str(archive))
                self.assertEqual(self.action(paper, "stop").status_code, 200)
                chunk.refresh_from_db()
                self.assertEqual(chunk.status, "parsed")
                self.assertEqual(archive.read_bytes(), b"saved successful result")

    def test_stop_preserves_human_body_approval_and_published_history(self):
        paper = self.paper()
        question = Question.objects.create(paper=paper, number=1, processing_mode="manual",
            body_mode="source_image", state="yellow", question_type="free_response", edited=True,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        library.approve(question, now=timezone.now())
        question.save()
        publication, _ = library.publish(question)
        old = dict(publication.content)
        key = question.source_key
        paper.status = "reading"
        paper.save(update_fields=["status"])
        self.action(paper, "stop")
        question.refresh_from_db()
        publication.refresh_from_db()
        self.assertTrue(library.approval_is_current(question))
        self.assertEqual(question.source_key, key)
        self.assertEqual(publication.content, old)
        self.assertEqual(self.client.delete(f"/api/papers/{paper.pk}", HTTP_X_QB_REQUEST="1").status_code, 400)

    def test_late_chunk_success_after_stop_retry_keeps_previous_success_only(self):
        paper = self.paper(mode="mineru", status="parsing")
        folder = pipeline.paper_dir(paper) / "chunks"
        folder.mkdir()
        completed = folder / "chunk_001.zip"
        completed.write_bytes(b"previous successful chunk")
        ImportChunk.objects.create(paper=paper, sequence=1, source_page_start=1,
            source_page_end=1, page_map=[1], status="parsed", artifact_path=str(completed))
        pending = ImportChunk.objects.create(paper=paper, sequence=2, source_page_start=2,
            source_page_end=2, page_map=[2])
        def extract(_source, target, _count, **kwargs):
            self.action(paper, "stop")
            self.action(paper, "retry")
            target.write_bytes(b"obsolete result")
        def cut(_source, target, *args):
            target.write_bytes(b"local slice")
        with mock.patch.object(pipeline, "ThreadPoolExecutor", local_tests.InlineExecutor), \
                mock.patch.object(pipeline, "account_pool", return_value=mock.Mock(size=1)), \
                mock.patch.object(pipeline, "write_pdf_slice", side_effect=cut), \
                mock.patch.object(pipeline, "load_blocks", return_value=[]), \
                mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract):
            with self.assertRaises(mineru.MineruCancelled):
                pipeline._chunk_blocks(paper, Path(paper.source_path))
        pending.refresh_from_db()
        paper.refresh_from_db()
        self.assertEqual(paper.status, "queued")
        self.assertNotEqual(pending.status, "parsed")
        self.assertFalse((folder / "chunk_002.zip").exists())
        self.assertEqual(completed.read_bytes(), b"previous successful chunk")

    def test_stop_between_worker_claim_and_parse_cannot_restart_or_adopt_retry(self):
        for retry in (False, True):
            with self.subTest(retry=retry):
                paper = self.paper(mode="mineru", status="queued")
                real_parse = pipeline.parse
                def at_boundary(current, *, revision):
                    self.action(paper, "stop")
                    if retry:
                        self.action(paper, "retry")
                    return real_parse(current, revision=revision)
                with mock.patch.object(pipeline, "parse", side_effect=at_boundary), \
                        mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud:
                    pipeline.process_paper(paper)
                cloud.assert_not_called()
                paper.refresh_from_db()
                self.assertEqual(paper.status, "queued" if retry else "failed")
                self.assertFalse(paper.blocks.exists())

    def test_late_success_after_stop_retry_is_not_promoted_or_duplicated(self):
        paper = self.paper(mode="mineru", status="queued")
        original = Path(paper.source_path).read_bytes()
        def extract(_source, target, _count, **kwargs):
            self.action(paper, "stop")
            self.action(paper, "retry")
            self.assertTrue(kwargs["cancel"]())  # marker cleared, revision still obsolete
            target.write_bytes(b"late obsolete zip")
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract), \
                mock.patch.object(pipeline, "load_blocks", return_value=[]):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual(paper.status, "queued")
        self.assertFalse(paper.blocks.exists())
        self.assertFalse(paper.questions.exists())
        self.assertFalse((pipeline.paper_dir(paper) / "mineru_result.zip").exists())
        self.assertEqual(Path(paper.source_path).read_bytes(), original)
        self.assertFalse(list(pipeline.paper_dir(paper).glob(".parse-*")))

    def test_late_failure_cannot_undo_stop_or_new_retry(self):
        paper = self.paper(mode="mineru", status="queued")
        def extract(*args, **kwargs):
            self.action(paper, "stop")
            self.action(paper, "retry")
            raise mineru.MineruError("old failed request")
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.error), ("queued", ""))

    def test_deleted_task_cannot_be_recreated_by_late_success(self):
        paper = self.paper(mode="mineru", status="queued")
        paper_id = paper.pk
        def extract(*args, **kwargs):
            self.action(paper, "stop")
            self.assertEqual(self.client.delete(f"/api/papers/{paper.pk}", HTTP_X_QB_REQUEST="1").status_code, 200)
            return None
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract):
            pipeline.process_paper(paper)
        self.assertFalse(Paper.objects.filter(pk=paper_id).exists())
        self.assertFalse((self.root / str(paper_id)).exists())

    def test_stop_rejects_pending_question_result_without_clearing_completed_text(self):
        paper = self.paper(mode="mineru", status="reading")
        question = Question.objects.create(paper=paper, number=1, stem="saved human text", edited=True,
            state="reading", regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        key = question.source_key
        def read(*args):
            self.action(paper, "stop")
            return {"stem": "late OCR replacement", "state": "green", "flags": []}
        with mock.patch.object(pipeline, "ThreadPoolExecutor", local_tests.InlineExecutor), \
                mock.patch.object(pipeline, "_reader_parallelism", return_value=1), \
                mock.patch.object(pipeline, "close_old_connections"), \
                mock.patch.object(pipeline, "read_card", side_effect=read):
            pipeline.read_questions(paper, [question])
        question.refresh_from_db()
        paper.refresh_from_db()
        self.assertEqual(question.stem, "saved human text")
        self.assertEqual(question.source_key, key)
        self.assertFalse(question.ocr_pending)
        self.assertEqual(paper.status, "failed")

    def test_auto_text_pdf_keeps_local_candidates_even_when_cloud_is_authorized(self):
        paper = self.upload(allow_cloud="1")
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "native"))
        self.assertEqual(paper.questions.count(), 2)
        self.assertTrue(all(q.body_mode == "source_image" and not q.approved for q in paper.questions.all()))

    def test_auto_reader_scope_reaches_card_threads_and_preserves_successful_text(self):
        paper = self.paper(mode="mineru", status="reading")
        paper.processing_plan = {**paper.processing_plan, "auto_fallback": True}
        paper.save(update_fields=["processing_plan"])
        question = Question.objects.create(paper=paper, number=1, state="waiting",
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        def read(*args):
            self.assertFalse(readers._fallback_enabled())
            return {"stem": "selected service result", "state": "yellow", "flags": []}
        with mock.patch.object(pipeline, "ThreadPoolExecutor", local_tests.InlineExecutor), \
                mock.patch.object(pipeline, "_reader_parallelism", return_value=1), \
                mock.patch.object(pipeline, "close_old_connections"), \
                mock.patch.object(pipeline, "read_card", side_effect=read) as reading:
            pipeline.read_questions(paper, [question])
        reading.assert_called_once()
        question.refresh_from_db()
        self.assertEqual(question.stem, "selected service result")

    def test_scan_pdf_without_explicit_consent_never_queues_configured_cloud(self):
        paper = self.upload(scan=True, allow_cloud="true")
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))
        self.assertFalse(paper.processing_plan["cloud_authorized"])
        self.assertEqual(len(paper.pages), 1)

    def test_authorized_scan_pdf_with_missing_services_is_manual(self):
        paper = self.upload(scan=True, allow_cloud="1", services=False)
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))

    def test_only_authorized_scan_pdf_with_existing_services_queues_mineru(self):
        paper = self.upload(scan=True, allow_cloud="1")
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("queued", "mineru"))
        self.assertTrue(paper.processing_plan["cloud_authorized"])

    def test_failed_authorized_cloud_returns_to_manual_and_preserves_saved_draft(self):
        paper = self.upload(scan=True, allow_cloud="1")
        original = Path(paper.source_path).read_bytes()
        question = Question.objects.create(paper=paper, number=1, body_mode="source_image",
            processing_mode="manual", state="yellow", question_type="free_response", edited=True,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        key, regions = question.source_key, question.regions
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=mineru.MineruError("service unavailable")):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))
        self.assertEqual(question.source_key, key)
        self.assertEqual(question.regions, regions)
        self.assertEqual(paper.questions.count(), 1)
        self.assertEqual(Path(paper.source_path).read_bytes(), original)

    def test_local_native_failure_retains_a_viewable_original(self):
        with mock.patch.object(intake.native_pdf, "extract", side_effect=RuntimeError("bad text layer")):
            paper = self.upload()
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))
        self.assertEqual(len(paper.pages), 1)

    def test_local_retry_cannot_queue_cloud(self):
        paper = self.paper(mode="manual", status="failed")
        with mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud:
            response = self.action(paper, "retry")
        self.assertEqual(response.status_code, 200, response.content)
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))
        cloud.assert_not_called()


class CancelNetworkTests(SimpleTestCase):
    def test_cancelled_done_response_cannot_begin_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.pdf"
            source.write_bytes(b"original")
            stopped = False
            api = fake_api([{"state": "done", "full_zip_url": "https://example.invalid/result"}])
            def response(*args, **kwargs):
                nonlocal stopped
                answer = api(*args, **kwargs)
                if "extract-results" in args[2]:
                    stopped = True
                return answer
            with mock.patch.object(mineru, "_api_json", side_effect=response), \
                    mock.patch.object(mineru.requests.Session, "put", return_value=mock.Mock(ok=True)), \
                    mock.patch.object(mineru, "_download_zip") as download:
                with self.assertRaises(mineru.MineruCancelled):
                    mineru.request_extract_file("test-only", source, Path(temporary) / "result.zip", 1,
                        cancel=lambda: stopped)
            download.assert_not_called()

    def test_download_cancel_removes_partial_and_keeps_previous_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "result.zip"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("existing", "successful result")
            before = target.read_bytes()
            stopped = False
            class Session(_Session):
                def get(self, *args, **kwargs):
                    response = super().get(*args, **kwargs)
                    def chunks(**kwargs):
                        nonlocal stopped
                        yield b"part of new result"
                        stopped = True
                        yield b"late bytes"
                    response.iter_content = chunks
                    return response
            with self.assertRaises(mineru.MineruCancelled):
                mineru._download_zip(Session([]), "https://example.invalid/result.zip", target,
                    cancel=lambda: stopped)
            self.assertEqual(target.read_bytes(), before)
            self.assertFalse(target.with_name(target.name + ".part").exists())

    def test_cancel_unblocks_an_account_wait_without_releasing_another_job(self):
        pool = account_pool.AccountPool("mineru", ("test-only",))
        cancel = threading.Event()
        started = threading.Event()
        result = []
        def waiter():
            started.set()
            try:
                with pool.lease(cancel=cancel.is_set):
                    result.append("unexpected lease")
            except account_pool.AccountPoolCancelled:
                result.append("cancelled")
        with pool.lease():
            thread = threading.Thread(target=waiter)
            thread.start()
            self.assertTrue(started.wait(1))
            cancel.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result, ["cancelled"])
            self.assertEqual(pool.spare, 0)
        with pool.lease():
            pass
