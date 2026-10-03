"""One-action manual fallback: real local pages, offline late-result races."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from unittest import mock

from PIL import Image
from django.test import TestCase, override_settings
from django.utils import timezone

from . import intake, library, mineru, pipeline, region_reads, readers
from . import test_local_intake as local_tests, test_manual_intake_review as review_tests
from .models import Block, ImportChunk, Paper, Question, RegionRead


class SwitchToManualTests(TestCase):
    paper = review_tests.ManualIntakeReviewTests.paper
    run_read = review_tests.ManualIntakeReviewTests.run_read

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        config = override_settings(DATA_ROOT=self.root)
        config.enable()
        self.addCleanup(config.disable)

    def switch(self, paper, **payload):
        return self.client.post(f"/api/papers/{paper.pk}/processing", json.dumps({"mode": "manual", **payload}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")

    def card(self, paper, number=1, **fields):
        return Question.objects.create(paper=paper, number=number, question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}], **fields)

    def test_old_parsing_task_switches_immediately_without_worker_or_cloud(self):
        paper = self.paper(mode="mineru", status="parsing")
        original = Path(paper.source_path).read_bytes()
        Paper.objects.filter(pk=paper.pk).update(updated_at=timezone.now() - timedelta(hours=16))
        with mock.patch.object(intake, "_prepare_manual_pages") as prepare, \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud, \
                mock.patch.object(readers, "chat") as read:
            result = self.switch(paper)
        self.assertEqual(result.status_code, 200, result.content)
        shown = result.json()
        self.assertTrue(shown["manual_ready"] and shown["changed"])
        self.assertEqual((shown["paper"]["status"], shown["paper"]["parse_mode"]), ("ready", "manual"))
        self.assertEqual(len(shown["paper"]["pages"]), 2)
        self.assertIsNone(shown["paper"]["processing"])
        self.assertTrue((pipeline.paper_dir(paper) / mineru.CANCEL_FILE).exists())
        self.assertEqual(Path(paper.source_path).read_bytes(), original)
        prepare.assert_not_called()
        cloud.assert_not_called()
        read.assert_not_called()

    def test_switch_preserves_edited_body_figures_approval_and_publication_bytes(self):
        paper = self.paper(mode="mineru", status="parsing")
        stem = "如图，Human mathematical body $x^2$"
        question = self.card(paper, stem=stem, state="green", edited=True,
            options={"A": "human option"}, figures=[{"page_idx": 0, "bbox": [200, 100, 600, 350], "slot": "stem"}])
        figures = deepcopy(question.figures)
        library.approve(question, now=timezone.now())
        question.save()
        publication, _ = library.publish(question)
        snapshot = deepcopy(publication.content)
        publication_files = {str(path.relative_to(self.root)): path.read_bytes()
            for path in (self.root / "library" / str(publication.pk)).rglob("*") if path.is_file()}
        key, approved_hash = question.source_key, question.approved_content_hash
        block = Block.objects.create(paper=paper, seq=0, type="text", page_idx=0, text="previous layout")
        archive = pipeline.paper_dir(paper) / "completed.zip"
        archive.write_bytes(b"previous successful parsed result")
        chunk = ImportChunk.objects.create(paper=paper, sequence=1, source_page_start=1,
            source_page_end=2, page_map=[1, 2], status="parsed", artifact_path=str(archive))
        paper.progress, paper.total = 7, 9
        paper.save(update_fields=["progress", "total"])
        self.assertEqual(self.switch(paper).status_code, 200)
        question.refresh_from_db()
        paper.refresh_from_db()
        publication.refresh_from_db()
        chunk.refresh_from_db()
        self.assertEqual((question.stem, question.options, question.figures),
            (stem, {"A": "human option"}, figures))
        self.assertEqual((question.source_key, question.approved_content_hash), (key, approved_hash))
        self.assertTrue(question.edited and question.approved and library.approval_is_current(question))
        self.assertEqual(publication.content, snapshot)
        self.assertEqual(publication_files, {str(path.relative_to(self.root)): path.read_bytes()
            for path in (self.root / "library" / str(publication.pk)).rglob("*") if path.is_file()})
        self.assertEqual((paper.progress, paper.total), (7, 9))
        self.assertTrue(Block.objects.filter(pk=block.pk, text="previous layout").exists())
        self.assertEqual(chunk.status, "parsed")
        self.assertEqual(archive.read_bytes(), b"previous successful parsed result")

    def test_switch_invalidates_pending_reads_but_retains_finished_region_results(self):
        paper = self.paper(mode="mineru", status="reading")
        question = self.card(paper, state="reading", ocr_pending=True, reread_requested=True)
        queued = RegionRead.objects.create(question=question, page_idx=0, bbox=[30, 30, 100, 100], target="stem")
        running = RegionRead.objects.create(question=question, page_idx=0, bbox=[30, 30, 100, 100],
            target="stem", status="running")
        done = RegionRead.objects.create(question=question, page_idx=0, bbox=[30, 30, 100, 100],
            target="stem", status="done", text="previous usable reading")
        self.assertEqual(self.switch(paper).status_code, 200)
        question.refresh_from_db()
        self.assertEqual((question.state, question.body_mode, question.processing_mode), ("yellow", "source_image", "manual"))
        self.assertFalse(question.ocr_pending or question.reread_requested)
        self.assertEqual(question.content_revision, 1)
        region_reads._finish(running, "done", text="late text")
        queued.refresh_from_db()
        running.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual((queued.status, running.status, running.text), ("failed", "failed", ""))
        self.assertEqual((done.status, done.text), ("done", "previous usable reading"))

    def test_repeated_click_is_idempotent_and_does_not_cancel_new_manual_reread(self):
        paper = self.paper(mode="mineru", status="parsing")
        question = self.card(paper, stem="Existing draft", state="yellow")
        self.assertTrue(self.switch(paper, revision=0).json()["changed"])
        paper.refresh_from_db()
        question.refresh_from_db()
        plan = deepcopy(paper.processing_plan)
        question.reread_requested, question.ocr_pending = True, True
        question.save()
        before = Question.objects.values().get(pk=question.pk)
        result = self.switch(paper, revision=0)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.json()["changed"])
        paper.refresh_from_db()
        self.assertEqual(paper.processing_plan, plan)
        self.assertEqual(Question.objects.values().get(pk=question.pk), before)

    def test_missing_pages_prepare_locally_after_cancellation_and_keep_old_cards_blocks(self):
        paper = self.paper(mode="mineru", status="parsing")
        paper.pages = []
        paper.save(update_fields=["pages"])
        question = self.card(paper, stem="Existing manual correction", state="green", edited=True)
        block = Block.objects.create(paper=paper, seq=0, type="text", page_idx=0, text="existing native or cloud block")
        real_sizes = intake.imaging.page_sizes
        def size_after_stop(*args):
            current = Paper.objects.get(pk=paper.pk)
            self.assertEqual((current.processing_plan["mode"], current.processing_plan["revision"]), ("manual", 1))
            self.assertTrue((pipeline.paper_dir(current) / mineru.CANCEL_FILE).exists())
            return real_sizes(*args)
        with mock.patch.object(intake.imaging, "page_sizes", side_effect=size_after_stop), \
                mock.patch.object(intake, "prepare", side_effect=AssertionError("New import must not replace existing results")), \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud:
            result = self.switch(paper)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertTrue(result.json()["manual_ready"])
        self.assertEqual(len(result.json()["paper"]["pages"]), 2)
        question.refresh_from_db()
        self.assertEqual((question.stem, question.edited), ("Existing manual correction", True))
        self.assertTrue(Block.objects.filter(pk=block.pk).exists())
        self.assertEqual(Paper.objects.get(pk=paper.pk).question_groups.count(), 1)
        cloud.assert_not_called()

    def test_local_preparation_failure_still_stops_and_can_retry_without_erasing_results(self):
        paper = self.paper(mode="mineru", status="parsing")
        paper.pages = []
        paper.save(update_fields=["pages"])
        question = self.card(paper, stem="Retained text", edited=True, state="yellow")
        source = Path(paper.source_path)
        original = source.read_bytes()
        source.unlink()
        result = self.switch(paper)
        self.assertEqual(result.status_code, 409, result.content)
        self.assertFalse(result.json()["manual_ready"])
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("failed", "manual"))
        self.assertFalse(pipeline._run_current(paper.pk, 0))
        question.refresh_from_db()
        self.assertEqual(question.stem, "Retained text")
        source.write_bytes(original)
        with mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud:
            retry = self.client.post(f"/api/papers/{paper.pk}/retry", "{}",
                content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(retry.status_code, 200, retry.content)
        self.assertEqual((retry.json()["paper"]["status"], retry.json()["paper"]["parse_mode"]), ("ready", "manual"))
        self.assertEqual(len(retry.json()["paper"]["pages"]), 2)
        self.assertTrue(Question.objects.filter(pk=question.pk, stem="Retained text").exists())
        cloud.assert_not_called()

    def test_photo_fallback_prepares_originals_with_separate_render_and_no_ocr(self):
        paper = self.paper(mode="mineru", status="parsing")
        folder = pipeline.paper_dir(paper)
        source = folder / "photo.jpg"
        Image.new("RGB", (180, 260), "white").save(source)
        data = source.read_bytes()
        paper.kind, paper.source_path, paper.pages = "image", str(source), []
        paper.photos = {"files": [{"name": "photo.jpg", "file": "photo.jpg"}], "order": [0], "enhance": False}
        paper.save()
        with mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud, \
                mock.patch.object(readers, "chat") as read:
            result = self.switch(paper)
        self.assertEqual(result.status_code, 200, result.content)
        paper.refresh_from_db()
        self.assertEqual(len(paper.pages), 1)
        self.assertTrue(Path(paper.render_path).name.startswith("pages-manual-"))
        self.assertEqual(source.read_bytes(), data)
        cloud.assert_not_called()
        read.assert_not_called()

    def test_docx_conversion_uses_different_path_from_old_parser(self):
        paper = self.paper(mode="mineru", status="parsing")
        folder = pipeline.paper_dir(paper)
        source = folder / "source.docx"
        source.write_bytes(b"original docx test bytes")
        paper.kind, paper.source_path, paper.pages = "docx", str(source), []
        paper.save()
        def convert(original, target):
            self.assertEqual(original, source)
            self.assertNotEqual(target, folder / "converted.pdf")
            self.assertEqual(Paper.objects.get(pk=paper.pk).processing_plan["mode"], "manual")
            target.write_bytes(review_tests.original_pdf(pages=1))
        with mock.patch.object(pipeline, "convert_docx_to_pdf", side_effect=convert), \
                mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud:
            result = self.switch(paper)
        self.assertEqual(result.status_code, 200, result.content)
        paper.refresh_from_db()
        self.assertEqual(len(paper.pages), 1)
        self.assertEqual(Path(paper.render_path).name, "converted-manual-1.pdf")
        self.assertEqual(source.read_bytes(), b"original docx test bytes")
        cloud.assert_not_called()

    def test_late_mineru_success_cannot_change_manual_status_or_promote_artifact(self):
        paper = self.paper(mode="mineru", status="queued")
        original = Path(paper.source_path).read_bytes()
        block = Block.objects.create(paper=paper, seq=0, type="text", page_idx=0, text="retained block")
        def extract(_source, target, _count, **kwargs):
            self.assertEqual(self.switch(paper).status_code, 200)
            self.assertTrue(kwargs["cancel"]())
            target.write_bytes(b"late obsolete MinerU result")
            kwargs["on_state"]({"state": "done"})
        with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract), \
                mock.patch.object(pipeline, "load_blocks", return_value=[]):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"], paper.error), ("ready", "manual", ""))
        self.assertTrue(Block.objects.filter(pk=block.pk, text="retained block").exists())
        self.assertFalse((pipeline.paper_dir(paper) / "mineru_result.zip").exists())
        self.assertFalse(list(pipeline.paper_dir(paper).glob(".parse-*")))
        self.assertEqual(Path(paper.source_path).read_bytes(), original)
        self.assertTrue((pipeline.paper_dir(paper) / mineru.CANCEL_FILE).exists())

    def test_late_mineru_failure_and_parse_lane_cannot_restore_failed_status(self):
        for ahead in (False, True):
            with self.subTest(ahead=ahead):
                paper = self.paper(mode="mineru", status="queued")
                def extract(*args, **kwargs):
                    self.assertEqual(self.switch(paper).status_code, 200)
                    raise mineru.MineruError("obsolete remote failure")
                with mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract):
                    if ahead:
                        pipeline.parse_ahead(paper)
                    else:
                        pipeline.process_paper(paper)
                paper.refresh_from_db()
                self.assertEqual((paper.status, paper.processing_plan["mode"], paper.error), ("ready", "manual", ""))

    def test_late_chunk_does_not_replace_existing_success_or_restore_cloud_work(self):
        paper = self.paper(mode="mineru", status="parsing")
        folder = pipeline.paper_dir(paper) / "chunks"
        folder.mkdir()
        archive = folder / "chunk_001.zip"
        archive.write_bytes(b"completed original artifact")
        completed = ImportChunk.objects.create(paper=paper, sequence=1, source_page_start=1,
            source_page_end=1, page_map=[1], status="parsed", artifact_path=str(archive))
        pending = ImportChunk.objects.create(paper=paper, sequence=2, source_page_start=2,
            source_page_end=2, page_map=[2])
        def extract(_source, target, _count, **kwargs):
            self.assertEqual(self.switch(paper).status_code, 200)
            self.assertTrue(kwargs["cancel"]())
            target.write_bytes(b"obsolete chunk")
        def cut(_source, target, *args):
            target.write_bytes(b"offline local slice")
        with mock.patch.object(pipeline, "ThreadPoolExecutor", local_tests.InlineExecutor), \
                mock.patch.object(pipeline, "account_pool", return_value=mock.Mock(size=1)), \
                mock.patch.object(pipeline, "write_pdf_slice", side_effect=cut), \
                mock.patch.object(pipeline, "load_blocks", return_value=[]), \
                mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=extract):
            with self.assertRaises(mineru.MineruCancelled):
                pipeline._chunk_blocks(paper, Path(paper.source_path))
        pending.refresh_from_db()
        completed.refresh_from_db()
        paper.refresh_from_db()
        self.assertNotEqual(pending.status, "parsed")
        self.assertEqual(completed.status, "parsed")
        self.assertEqual(archive.read_bytes(), b"completed original artifact")
        self.assertFalse((folder / "chunk_002.zip").exists())
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))

    def test_late_card_read_after_manual_switch_cannot_overwrite_edited_text(self):
        paper = self.paper(mode="mineru", status="reading")
        question = self.card(paper, stem="Saved human correction", state="waiting", edited=True)
        def finish(*args):
            self.assertEqual(self.switch(paper).status_code, 200)
            return {"stem": "obsolete OCR replacement", "options": {}, "state": "green", "flags": []}
        self.run_read(question, finish)
        question.refresh_from_db()
        self.assertEqual((question.stem, question.processing_mode, question.state), ("Saved human correction", "manual", "yellow"))
        self.assertFalse(question.ocr_pending or question.reread_requested)

    def test_restarted_worker_and_old_claim_do_not_resume_mineru_or_read_cards(self):
        paper = self.paper(mode="mineru", status="parsing")
        self.assertEqual(self.switch(paper).status_code, 200)
        with mock.patch.object(pipeline, "request_extract_file_from_pool") as cloud, \
                mock.patch.object(pipeline, "read_card") as read:
            pipeline.process_paper(paper)
            self.assertFalse(pipeline.parse_ahead(paper))
            with self.assertRaises(mineru.MineruCancelled):
                pipeline.parse(paper, revision=0)
        cloud.assert_not_called()
        read.assert_not_called()
        paper.refresh_from_db()
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "manual"))

    def test_archived_and_stale_revision_requests_do_not_invalidate_newer_task(self):
        paper = self.paper(mode="mineru", status="parsing")
        before = Paper.objects.values().get(pk=paper.pk)
        self.assertEqual(self.switch(paper, revision=9).status_code, 409)
        self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)
        self.assertFalse((pipeline.paper_dir(paper) / mineru.CANCEL_FILE).exists())
        paper.archived = True
        paper.save(update_fields=["archived"])
        before = Paper.objects.values().get(pk=paper.pk)
        self.assertEqual(self.switch(paper).status_code, 409)
        self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)

    def test_request_guards_reject_invalid_payload_without_mutation(self):
        paper = self.paper(mode="mineru", status="parsing")
        before = Paper.objects.values().get(pk=paper.pk)
        for payload in ({"pages": [0]}, {"revision": True}, {"revision": -1}, {"revision": "0"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.switch(paper, **payload).status_code, 400)
        url = f"/api/papers/{paper.pk}/processing"
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, '{"mode":"manual"}', content_type="application/json").status_code, 403)
        self.assertEqual(Paper.objects.values().get(pk=paper.pk), before)
