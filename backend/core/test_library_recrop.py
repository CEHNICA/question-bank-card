"""Tests for in-library original-page recropping and AI re-OCR (library_recrop)."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings
from PIL import Image, ImageDraw

from . import library, library_solutions, library_tagging
from .models import LibrarySolution, LibraryTag, Paper, PublishedQuestion, Question

HEADERS = {"HTTP_X_QB_REQUEST": "1"}


def _fake_page(path: Path, color: str = "white") -> None:
    img = Image.new("RGB", (600, 800), color)
    draw = ImageDraw.Draw(img)
    draw.rectangle((40, 40, 560, 320), outline="black", width=3)
    draw.rectangle((60, 120, 300, 280), fill="navy")
    img.save(path, format="PNG")


class LibraryRecropTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_root = Path(self.tmp.name)
        self.override = override_settings(DATA_ROOT=self.data_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        page_path = self.data_root / "page_0.png"
        _fake_page(page_path)
        self.paper = Paper.objects.create(
            filename="2026测试卷.pdf",
            kind="pdf",
            source_path=str(page_path),
            render_path=str(page_path),
            pages=[{"page_idx": 0, "width": 600, "height": 800, "path": str(page_path)}],
            status=Paper.Status.READY,
        )
        self.question = Question.objects.create(
            paper=self.paper,
            number=1,
            question_type="single_choice",
            body_mode="text",
            stem="已知几何图形如图所示（少裁了一角），求面积。",
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 380]}],
            figures=[{"page_idx": 0, "bbox": [100, 150, 400, 300], "source": "manual", "slot": "stem"}],
            state=Question.State.GREEN,
            approved=True,
            approved_content_hash="",
        )
        from django.utils import timezone

        library.approve(self.question, now=timezone.now(), source="human")
        self.question.save()
        self.pub, _ = library.publish(self.question)

        # 挂载正式标签、OneNote 元数据与激活解析，验证新版本发布后全部保留。
        tag = LibraryTag.objects.create(name="函数与几何", kind="knowledge")
        library_tagging.assign(
            {
                "publication_ids": [str(self.pub.pk)],
                "add": [str(tag.pk)],
                "remove": [],
                "expected_revisions": {str(self.pub.pk): self.pub.tag_revision},
                "request_id": str(uuid.uuid4()),
            }
        )
        library.save_extras(self.pub, {**(self.pub.extras or {}), "onenote": {"page_id": "pg-1", "title": "第1题笔记"}})
        library_solutions.save(
            self.pub,
            {
                "base_revision": None,
                "answer": "C",
                "analysis": "既有解析需要跟着新版一起走。",
                "figures": [],
                "sync_library": True,
            },
        )
        self.pub.refresh_from_db()
        self.client = Client()

    def test_replace_figure_publishes_new_version_and_preserves_metadata(self):
        resp = self.client.post(
            f"/api/library/{self.pub.pk}/recrop",
            data=json.dumps(
                {"action": "replace_figure", "page_idx": 0, "bbox": [80, 140, 520, 360], "figure_index": 0}
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["previous_id"], str(self.pub.pk))
        new_id = body["publication"]["id"]
        self.assertNotEqual(new_id, str(self.pub.pk))

        old_pub = PublishedQuestion.objects.get(pk=self.pub.pk)
        new_pub = PublishedQuestion.objects.get(pk=new_id)
        self.assertEqual(old_pub.status, PublishedQuestion.Status.SUPERSEDED)
        self.assertEqual(new_pub.status, PublishedQuestion.Status.PUBLISHED)
        self.assertEqual(new_pub.version, 2)
        self.assertEqual(new_pub.content["figures"][0]["bbox"], [80.0, 140.0, 520.0, 360.0])
        self.assertEqual((new_pub.extras or {}).get("onenote", {}).get("page_id"), "pg-1")
        self.assertEqual(len(body["publication"]["tags"]), 1)
        sol = LibrarySolution.objects.get(pk=new_pub.extras["solution_id"])
        self.assertEqual(sol.publication_id, new_pub.pk)
        self.assertEqual(sol.analysis, "既有解析需要跟着新版一起走。")

    def test_replace_source_image_updates_regions_and_publishes_source_image(self):
        resp = self.client.post(
            f"/api/library/{self.pub.pk}/recrop",
            data=json.dumps(
                {"action": "replace_source_image", "regions": [{"page_idx": 0, "bbox": [40, 40, 950, 420]}]}
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(resp.status_code, 200, resp.content.decode())
        body = resp.json()
        new_pub = PublishedQuestion.objects.get(pk=body["publication"]["id"])
        self.assertEqual(new_pub.content["body_mode"], "source_image")
        self.assertEqual(len(new_pub.content["question_images"]), 1)
        self.assertEqual(new_pub.content["question_images"][0]["bbox"], [40.0, 40.0, 950.0, 420.0])

    @mock.patch("core.library_recrop.readers.assistant_mode", return_value=False)
    @mock.patch("core.library_recrop.readers.primary_engine")
    @mock.patch("core.library_recrop.readers.read_question")
    def test_ocr_preview_and_apply_ocr_flow(self, mock_read, mock_engine, _mock_assist):
        mock_engine.return_value = mock.MagicMock(label="TestVision")
        mock_read.return_value = {
            "stem": "补全之后的新题干：已知 $\\triangle ABC$，求角度。",
            "options": {"A": "30°", "B": "45°", "C": "60°", "D": "90°"},
            "question_type": "single_choice",
            "engine": "TestVision",
            "unreadable": False,
        }

        preview_resp = self.client.post(
            f"/api/library/{self.pub.pk}/recrop",
            data=json.dumps({"action": "ocr_preview", "regions": [{"page_idx": 0, "bbox": [40, 40, 950, 420]}]}),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(preview_resp.status_code, 200, preview_resp.content.decode())
        preview_data = preview_resp.json()["preview"]
        self.assertIn("补全之后的新题干", preview_data["stem"])
        self.assertTrue(preview_data["image_data_url"].startswith("data:image/jpeg;base64,"))
        # 预览阶段绝不提前覆盖正式题库版本
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.status, PublishedQuestion.Status.PUBLISHED)
        self.assertIn("少裁了一角", self.pub.content["stem"])

        apply_resp = self.client.post(
            f"/api/library/{self.pub.pk}/recrop",
            data=json.dumps(
                {
                    "action": "apply_ocr",
                    "regions": preview_data["regions"],
                    "stem": preview_data["stem"],
                    "options": preview_data["options"],
                    "question_type": preview_data["question_type"],
                }
            ),
            content_type="application/json",
            **HEADERS,
        )
        self.assertEqual(apply_resp.status_code, 200, apply_resp.content.decode())
        applied = apply_resp.json()["publication"]
        self.assertEqual(applied["version"], 2)
        self.assertIn("补全之后的新题干", applied["content"]["stem"])
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.status, PublishedQuestion.Status.SUPERSEDED)
