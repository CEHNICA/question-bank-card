"""The practice paper for 新手教学 opens without keys or network and never enters the library."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import demo
from .figure_policy import blocks_approval, stored_or_derived_review
from .models import Paper, PublishedQuestion, Question


class DemoPaperTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)

    def post(self, path, body=None):
        return self.client.post(path, data=json.dumps(body or {}), content_type="application/json",
                                HTTP_X_QB_REQUEST="1")

    def test_practice_paper_is_ready_with_the_planted_situations(self):
        response = self.post("/api/demo")
        self.assertEqual(response.status_code, 201, response.content)
        paper = response.json()["paper"]
        self.assertTrue(paper["demo"])
        self.assertEqual(paper["status"], "ready")
        self.assertEqual(paper["counts"]["total"], 12)
        stored = Paper.objects.get(pk=paper["id"])
        self.assertTrue(Path(stored.source_path).is_file())
        self.assertFalse(Question.objects.filter(paper=stored, reread_requested=True).exists())
        # 第 9 题 kept the misreading, and the other reading says what the paper prints.
        nine = Question.objects.get(paper=stored, number=9)
        self.assertEqual(nine.state, "yellow")
        self.assertIn("3 个单位", nine.stem)
        self.assertIn("5 个单位", nine.read_b["stem"])
        # 第 2 题 still needs its figure.
        two = Question.objects.get(paper=stored, number=2)
        self.assertEqual(two.figures, [])
        self.assertTrue(blocks_approval(stored_or_derived_review(two)))
        # 第 3 题 is a text table.
        self.assertIn("| 人数 |", Question.objects.get(paper=stored, number=3).stem)
        # Pages render from the shipped PDF.
        preview = self.client.get(f"/api/papers/{paper['id']}/pages/0/preview")
        self.assertEqual(preview.status_code, 200)
        b"".join(preview.streaming_content)
        preview.close()  # Windows keeps an open file from being removed

    def test_opening_again_keeps_progress_and_reset_starts_over(self):
        first = self.post("/api/demo").json()["paper"]["id"]
        Question.objects.filter(paper_id=first, number=1).update(approved=True)
        self.assertEqual(self.post("/api/demo").json()["paper"]["id"], first)
        again = self.post("/api/demo", {"reset": True}).json()["paper"]["id"]
        self.assertNotEqual(again, first)
        self.assertFalse(Paper.objects.filter(pk=first).exists())
        self.assertEqual(Paper.objects.filter(pk=again).count(), 1)

    def test_practice_paper_never_enters_the_library(self):
        paper = self.post("/api/demo").json()["paper"]["id"]
        question = Question.objects.get(paper_id=paper, number=1)
        self.assertEqual(self.post(f"/api/questions/{question.pk}/approve", {"approved": True}).status_code, 200)
        response = self.post(f"/api/papers/{paper}/publish")
        self.assertEqual(response.status_code, 409)
        self.assertIn("示例试卷只用来练习", response.json()["error"])
        self.assertEqual(PublishedQuestion.objects.count(), 0)

    @mock.patch.dict("os.environ", {"QB_MINERU_CONFIGURED": "1", "QB_MINIMAX_CONFIGURED": "1"})
    def test_uploading_the_public_demo_pdf_is_a_real_reading(self):
        self.post("/api/demo")
        pdf = (demo.data_root() / "demo-paper.pdf").read_bytes()
        from django.core.files.uploadedfile import SimpleUploadedFile

        response = self.client.post("/api/papers", {"file": SimpleUploadedFile("demo.pdf", pdf)},
                                    HTTP_X_QB_REQUEST="1")
        self.assertIn(response.status_code, (200, 201))
        self.assertFalse(response.json().get("duplicate"))
        self.assertFalse(response.json()["paper"]["demo"])
