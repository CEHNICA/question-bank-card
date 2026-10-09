"""Saved layout tokens must survive read-only display derivation and previews."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from unittest import mock
import uuid

from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from . import question_layout as layout, test_manual_intake_review as manual_review
from .models import LibraryJob, Paper, PublishedQuestion, Question, RegionRead


class QuestionLayoutEvidenceTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="tiyouju-layout-evidence-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        configured = override_settings(DATA_ROOT=self.root)
        configured.enable()
        self.addCleanup(configured.disable)
        guard = mock.patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network"))
        guard.start()
        self.addCleanup(guard.stop)
        self.paper = manual_review.ManualIntakeReviewTests.paper(self)
        self.question = Question.objects.create(paper=self.paper, number=1, processing_mode="auto",
            regions=[{"page_idx": 0, "bbox": [40, 60, 900, 420]}],
            regions_auto=[{"page_idx": 0, "bbox": [40, 60, 900, 420]}],
            state="green", stem="如图，求三角形的面积", question_type="free_response",
            figure_review={"status": "ok", "source": "automatic", "policy_version": "obsolete"}, flags=[])

    def test_http_display_derived_review_and_crops_still_supply_a_valid_save_precondition(self):
        before = deepcopy(Question.objects.values().get(pk=self.question.pk))
        data = self.client.get(f"/api/papers/{self.paper.pk}").json()
        shown = data["questions"][0]
        self.assertNotEqual(shown["figure_review"], before["figure_review"])
        self.assertEqual(shown["layout_fingerprint"], layout.fingerprint(Question.objects.get(pk=self.question.pk)))
        for path in (f"/api/questions/{self.question.pk}/crop", f"/api/papers/{self.paper.pk}/pages/0/preview"):
            response = self.client.get(path)
            try:
                content = b"".join(response.streaming_content) if response.streaming else response.content
                self.assertEqual(response.status_code, 200, content[:100])
                self.assertTrue(content, "The original crop/preview must contain actual image bytes")
            finally:
                response.close()
        self.assertEqual(Question.objects.values().get(pk=self.question.pk), before)
        payload = {"kind": "regions", "layout_revision": data["paper"]["layout_revision"],
                   "client_request_id": str(uuid.uuid4()),
                   "sources": [{"id": shown["id"], "revision": shown["content_revision"],
                                "fingerprint": shown["layout_fingerprint"]}],
                   "targets": [{"regions": [{"page_idx": 0, "bbox": [45, 65, 905, 425]}]}]}
        saved = self.client.post(f"/api/papers/{self.paper.pk}/question-layout", json.dumps(payload),
                                 content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(saved.status_code, 200, saved.content)
        latest = saved.json()
        self.assertTrue(latest["operation"]["can_undo"])
        self.assertEqual(latest["questions"][0]["layout_fingerprint"],
                         layout.fingerprint(Question.objects.get(pk=self.question.pk)))

    def test_bulk_evidence_matches_single_fingerprint_with_history_jobs_and_region_reads(self):
        publication = PublishedQuestion.objects.create(paper=self.paper, question=self.question,
            source_filename="synthetic", number=1, question_type="free_response", version=1,
            content_hash="a" * 64, content={}, extras={"synthetic": True})
        LibraryJob.objects.create(publication=publication, kind="answer", result={"answer": "合成答案"})
        RegionRead.objects.create(question=self.question, page_idx=0, bbox=[40, 60, 200, 90],
                                  target="stem", text="合成识读", status="done")
        rows = list(Question.objects.filter(paper=self.paper).select_related("paper"))
        expected = layout.fingerprint(rows[0])
        with CaptureQueriesContext(connection) as queries:
            layout.prepare_fingerprints(rows)
            actual = layout.fingerprint(rows[0])
        self.assertEqual(actual, expected)
        self.assertEqual(len(queries), 3)

    def test_full_save_of_an_older_paper_instance_cannot_overwrite_shared_layout_counters(self):
        stale = Paper.objects.get(pk=self.paper.pk)
        layout.bump(self.paper.pk)
        layout.invalidate_page_epoch(self.paper)
        current = Paper.objects.get(pk=self.paper.pk)
        expected = (current.layout_revision, current.layout_page_epoch)
        stale.notes = ["合成工作者进度"]
        stale.save()
        current.refresh_from_db()
        self.assertEqual((current.layout_revision, current.layout_page_epoch), expected)
        self.assertEqual(current.notes, stale.notes)

    def test_missing_original_directory_changes_do_not_change_source_epoch(self):
        self.paper.source_path = self.paper.render_path = ""
        before = layout.source_epoch(self.paper)
        with tempfile.TemporaryDirectory(dir=self.root):
            self.assertEqual(layout.source_epoch(self.paper), before)
