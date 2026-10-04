"""Exercise isolation at the shared queue and publishing entry points."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, TransactionTestCase, override_settings

from . import demo, library, pipeline, region_reads
from .models import LibraryJob, Paper, PublishedQuestion, Question, RegionRead


class PracticeIsolationTests(TransactionTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        configured = override_settings(DATA_ROOT=Path(temp.name))
        configured.enable()
        self.addCleanup(configured.disable)
        self.paper = demo.create_demo_paper(course="basics")
        self.question = self.paper.questions.get(number=2)

    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_explicit_auto_addition_stays_manual_and_does_not_queue(self):
        result = self.post(f"/api/papers/{self.paper.pk}/questions", {
            "number": 4, "regions": self.question.regions, "processing_mode": "auto",
            "body_mode": "text", "question_type": "single_choice"})
        self.assertEqual(result.status_code, 201, result.content)
        added = self.paper.questions.get(number=4)
        self.assertEqual(added.processing_mode, "manual")
        self.assertFalse(added.reread_requested)
        self.assertFalse(added.ocr_pending)
        with mock.patch("core.pipeline.read_questions") as reading:
            self.assertEqual(pipeline.process_rereads(idle_papers_only=True), 0)
        reading.assert_not_called()

    def test_range_change_preserves_text_and_figures_without_automatic_reading(self):
        self.question.stem = "人工改过的题干"
        self.question.figures = [{"slot": "body", "page_idx": 0, "bbox": [10, 10, 40, 40], "source": "manual"}]
        self.question.edited = True
        self.question.save()
        previous = (self.question.stem, self.question.figures)
        for body in ({"regions": self.question.regions},
                     {"regions": self.question.regions, "processing_mode": "auto"}):
            response = self.post(f"/api/questions/{self.question.pk}/regions", body)
            self.assertEqual(response.status_code, 200, response.content)
            self.question.refresh_from_db()
            self.assertEqual((self.question.stem, self.question.figures), previous)
            self.assertTrue(self.question.edited)
            self.assertEqual(self.question.processing_mode, "manual")
            self.assertFalse(self.question.reread_requested)

    def test_publishing_rejects_demo_even_through_shared_library_function(self):
        question = self.paper.questions.get(number=9)
        response = self.post(f"/api/questions/{question.pk}/approve", {"approved": True})
        self.assertEqual(response.status_code, 200, response.content)
        question.refresh_from_db()
        self.assertTrue(library.approval_is_current(question))
        with self.assertRaisesRegex(ValueError, "示例试卷"):
            library.publish(question)
        self.assertEqual(PublishedQuestion.objects.count(), 0)
        self.assertEqual(LibraryJob.objects.count(), 0)

    def test_worker_reread_skips_old_demo_queue_but_keeps_ordinary_paper(self):
        self.question.reread_requested = True
        self.question.save(update_fields=["reread_requested"])
        ordinary = Paper.objects.create(filename="普通卷.pdf", status=Paper.Status.READY, structure={})
        card = Question.objects.create(paper=ordinary, number=1, reread_requested=True)
        with mock.patch("core.pipeline.read_questions") as reading:
            self.assertEqual(pipeline.process_rereads(idle_papers_only=True), 1)
        self.assertEqual(reading.call_count, 1)
        self.assertEqual(reading.call_args.args[0].pk, ordinary.pk)
        self.assertEqual([q.pk for q in reading.call_args.args[1]], [card.pk])

    def test_shared_cloud_entry_points_do_not_start_demo_work(self):
        # Old/full exercises have no manual plan, so a manual-mode check alone
        # cannot provide the practice boundary.
        self.paper.processing_plan = {}
        self.paper.status = Paper.Status.QUEUED
        self.paper.save(update_fields=["processing_plan", "status"])
        with mock.patch("core.pipeline._reader_parallelism", side_effect=AssertionError("reader started")), \
                mock.patch("core.pipeline.request_extract_file_from_pool", side_effect=AssertionError("upload started")), \
                mock.patch("core.pipeline.parse", wraps=pipeline.parse) as parsing:
            pipeline.read_questions(self.paper, [self.question])
            pipeline.process_paper(self.paper)
            self.assertFalse(pipeline.parse_ahead(self.paper))
            parsing.assert_not_called()
            pipeline.parse(self.paper)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.QUEUED)

    def test_region_worker_and_direct_region_reader_reject_demo(self):
        job = RegionRead.objects.create(question=self.question, page_idx=0, bbox=[10, 10, 40, 40], target="stem")
        with mock.patch("core.region_reads.readers.primary_engine", side_effect=AssertionError("reader selected")):
            with self.assertRaisesRegex(region_reads.RegionError, "示例练习"):
                region_reads.run(job)
        with mock.patch("core.region_reads.run", side_effect=AssertionError("demo was read")):
            self.assertEqual(region_reads.process_pending(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, RegionRead.Status.FAILED)
        self.assertIn("示例练习", job.error)
        self.assertFalse(region_reads.pending())

    def test_retry_reparse_resegment_and_continue_cannot_queue_demo(self):
        for action, state in (("retry", Paper.Status.FAILED), ("reparse", Paper.Status.PARSING),
                              ("resegment", Paper.Status.READY), ("resegment/preview", Paper.Status.READY),
                              ("continue-ai-cut", Paper.Status.READY)):
            self.paper.status = state
            self.paper.save(update_fields=["status"])
            response = self.post(f"/api/papers/{self.paper.pk}/{action}", {"revision": 0, "allow_cloud": True})
            self.assertEqual(response.status_code, 409, (action, response.content))
            self.paper.refresh_from_db()
            self.assertEqual(self.paper.status, state)
