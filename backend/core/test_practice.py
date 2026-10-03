"""Practice cannot publish, send clouds, mix real questions, or export stale content."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from . import demo, practice
from .models import Paper, PublishedQuestion, Question, LibraryJob, RegionRead


class PracticeTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        settings = override_settings(DATA_ROOT=Path(temp.name))
        settings.enable()
        self.addCleanup(settings.disable)
        data = self.post("/api/demo", {"course": "basics"}).json()
        self.paper_id = data["paper"]["id"]
        self.base = f"/api/demo/{self.paper_id}"
        fixture = json.loads((demo.data_root() / "demo-paper.json").read_text(encoding="utf-8"))
        one = next(x for x in fixture["questions"] if x["number"] == 1)
        result = self.post(f"/api/papers/{self.paper_id}/questions", {"number": 1,
            "regions": one["regions"], "processing_mode": "manual", "body_mode": "source_image",
            "question_type": "single_choice"})
        self.assertEqual(result.status_code, 201, result.content)
        self.question = Question.objects.get(pk=result.json()["question"]["id"])
        response = self.post(f"/api/questions/{self.question.pk}/approve", {"approved": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.question.refresh_from_db()
        self.key = str(self.question.source_key)
        self.payload = {"ids": [self.key], "fingerprints": {self.key: self.question.approved_content_hash}}

    def post(self, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_basics_waits_for_real_crop_then_has_three_questions(self):
        self.assertEqual(list(Question.objects.filter(paper_id=self.paper_id).order_by("number").values_list("number", flat=True)), [1, 2, 9])
        self.assertEqual(self.post("/api/demo", {"course": "basics"}).json()["paper"]["id"], self.paper_id)
        self.assertEqual(PublishedQuestion.objects.count(), 0)
        self.assertEqual(LibraryJob.objects.count(), 0)

    def test_old_course_needs_explicit_reset_and_real_papers_are_preserved(self):
        real = Paper.objects.create(filename="真实卷.pdf", status="ready")
        full = self.post("/api/demo", {"reset": True}).json()["paper"]
        response = self.post("/api/demo", {"course": "basics"}).json()
        self.assertTrue(response["restart_required"])
        self.assertEqual(response["paper"]["id"], full["id"])
        self.assertEqual(Question.objects.filter(paper_id=full["id"]).count(), 12)
        new = self.post("/api/demo", {"course": "basics", "reset": True}).json()
        self.assertFalse(new["restart_required"])
        self.assertEqual(Question.objects.filter(paper_id=new["paper"]["id"]).count(), 2)
        self.assertTrue(Paper.objects.filter(pk=real.pk).exists())

    def test_only_current_approved_demo_questions_are_selectable(self):
        data = self.client.get(self.base + "/library").json()
        self.assertEqual([x["id"] for x in data["items"]], [self.key])
        self.assertEqual(data["items"][0]["content"]["body_mode"], "source_image")
        self.assertTrue(data["items"][0]["content"]["question_images"])
        self.assertEqual(PublishedQuestion.objects.count(), 0)

    def test_preview_uses_pdf_document_and_real_crop_without_writing_formal_library(self):
        response = self.post(self.base + "/preview", self.payload)
        self.assertEqual(response.status_code, 200, response.content[:500])
        self.assertIn(b"ExamLayout.paginate", response.content)
        self.assertIn(b"data:image/png;base64,", response.content)
        self.assertEqual(response["X-Question-Count"], "1")
        self.assertEqual(PublishedQuestion.objects.count(), 0)
        self.assertEqual(LibraryJob.objects.count(), 0)

    @mock.patch("core.library_pdf._render", return_value=(b"%PDF-practice", 1))
    def test_export_generates_download_without_using_user_export_directory(self, render):
        with mock.patch("core.export_preferences.deliver", side_effect=AssertionError("Practice must not write user's export location")):
            response = self.post(self.base + "/export-pdf", self.payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["X-Page-Count"], "1")
        render.assert_called_once()
        self.assertEqual(PublishedQuestion.objects.count(), 0)

    def test_real_paper_and_unapproved_foreign_and_duplicate_ids_are_rejected(self):
        real = Paper.objects.create(filename="真实卷.pdf", status="ready")
        self.assertEqual(self.client.get(f"/practice/{real.pk}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/demo/{real.pk}/library").status_code, 404)
        two = Question.objects.get(paper_id=self.paper_id, number=2)
        for ids, fingerprints in [([str(two.source_key)], {str(two.source_key): ""}),
                                  ([str(real.pk)], {str(real.pk): "x"}),
                                  ([self.key, self.key], {self.key: self.question.approved_content_hash})]:
            response = self.post(self.base + "/preview", {"ids": ids, "fingerprints": fingerprints})
            self.assertIn(response.status_code, (400, 409))

    def test_changed_question_after_preview_is_rejected_without_attachment(self):
        Question.objects.filter(pk=self.question.pk).update(regions=[])
        self.assertEqual(self.post(self.base + "/export-pdf", self.payload).status_code, 409)

    def test_change_during_render_is_rejected(self):
        def changed(_document):
            Question.objects.filter(pk=self.question.pk).update(approved=False)
            return b"%PDF-practice", 1
        with mock.patch("core.library_pdf._render", side_effect=changed):
            response = self.post(self.base + "/export-pdf", self.payload)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("Content-Disposition", response)

    def test_all_demo_reading_paths_stay_offline(self):
        with mock.patch("core.views._vision_ready", side_effect=AssertionError("No API probing")):
            response = self.post(f"/api/papers/{self.paper_id}/read-cut-questions", {"question_ids": [self.question.pk]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["queued"], 0)
        self.assertEqual(self.post(f"/api/questions/{self.question.pk}/reread", {}).status_code, 409)
        self.assertEqual(self.post(f"/api/questions/{self.question.pk}/region-read", {}).status_code, 409)
        self.assertFalse(Question.objects.filter(paper_id=self.paper_id, reread_requested=True).exists())
        self.assertEqual(RegionRead.objects.count(), 0)

    def test_export_requires_local_guard_and_refuses_executable_input(self):
        raw = self.client.post(self.base + "/preview", json.dumps(self.payload), content_type="application/json")
        self.assertEqual(raw.status_code, 403)
        response = self.post(self.base + "/preview", {**self.payload, "html": "<script>unsafe()</script>"})
        self.assertEqual(response.status_code, 400)
