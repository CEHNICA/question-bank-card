"""AI assistants (tiyouju / MCP) can pass cards, but never pass as a person."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw

from . import library
from .models import Paper, PublishedQuestion, Question
from .pipeline import paper_dir


def page_image() -> Image.Image:
    image = Image.new("RGB", (1000, 1000), "white")
    ImageDraw.Draw(image).rectangle((600, 300, 900, 600), outline="black", width=4)
    return image


class AiReviewTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="ai.pdf", kind="pdf", sha256="a" * 64,
            source_path=str(Path(temp.name) / "ai.pdf"), render_path=str(Path(temp.name) / "ai.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}], status=Paper.Status.READY,
        )
        pages = paper_dir(self.paper) / "pages"
        pages.mkdir(parents=True)
        page_image().save(pages / "page_0.png")
        self.green = self.card(1, Question.State.GREEN, "已知 $x+1=2$，求 $x$。")
        self.yellow = self.card(2, Question.State.YELLOW, "计算 $3+4$。", flags=["两次识读不一致"])

    def card(self, number, state, stem, flags=None):
        return Question.objects.create(
            paper=self.paper, number=number, question_type="free_response", state=state, stem=stem,
            flags=flags or [], regions=[{"page_idx": 0, "bbox": [50, 50 + number * 100, 950, 140 + number * 100]}],
            figure_candidates=[{"page_idx": 0, "bbox": [600, 300, 900, 600]}],
        )

    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def approve(self, question, **body):
        return self.post(f"/api/questions/{question.pk}/approve", body)

    def test_an_ai_tick_is_recorded_as_ai_with_the_assistant_name(self):
        response = self.approve(self.yellow, approved=True, by="ai", agent="  豆包\n助手 ")
        self.assertEqual(response.status_code, 200, response.content)
        card = response.json()["question"]
        self.assertTrue(card["approved"])
        self.assertEqual((card["approved_by"], card["approval_agent"]), ("ai", "豆包 助手"))
        # The page (no "by") is a person.
        card = self.approve(self.green, approved=True).json()["question"]
        self.assertEqual((card["approved_by"], card["approval_agent"]), ("human", ""))
        self.assertEqual(self.approve(self.green, approved=True, by="robot").status_code, 400)

    def test_an_ai_never_overrides_or_undoes_a_person(self):
        self.approve(self.green, approved=True)
        card = self.approve(self.green, approved=True, by="ai", agent="豆包").json()["question"]
        self.assertEqual(card["approved_by"], "human")
        response = self.approve(self.green, approved=False, by="ai")
        self.assertEqual(response.status_code, 409)
        self.assertIn("人工通过", response.json()["error"])
        self.green.refresh_from_db()
        self.assertTrue(self.green.approved)

    def test_a_person_confirms_an_ai_tick_and_edits_clear_it(self):
        self.approve(self.yellow, approved=True, by="ai")
        card = self.approve(self.yellow, approved=True).json()["question"]
        self.assertEqual(card["approved_by"], "human")
        self.approve(self.green, approved=True, by="ai")
        card = self.post(f"/api/questions/{self.green.pk}/text", {
            "stem": "已知 $x+1=3$，求 $x$。", "options": {}, "question_type": "free_response",
        }).json()["question"]
        self.assertFalse(card["approved"])
        self.assertEqual(card["approved_by"], "")
        self.green.refresh_from_db()
        self.assertEqual((self.green.approval_source, self.green.approval_agent), ("", ""))
        # An assistant may take back its own tick.
        self.approve(self.yellow, approved=False)  # the person undoes theirs
        self.approve(self.yellow, approved=True, by="ai")
        self.assertEqual(self.approve(self.yellow, approved=False, by="ai").status_code, 200)

    def test_green_batch_by_ai_then_by_a_person(self):
        response = self.post(f"/api/papers/{self.paper.pk}/approve-green", {"by": "ai", "agent": "豆包"})
        self.assertEqual(response.json()["approved"], 1)
        self.green.refresh_from_db()
        self.assertEqual((self.green.approval_source, self.green.approval_agent), ("ai", "豆包"))
        self.assertEqual(self.post(f"/api/papers/{self.paper.pk}/approve-green", {"by": "ai"}).json()["approved"], 0)
        # The person's batch turns the assistant's ticks into theirs.
        self.assertEqual(self.post(f"/api/papers/{self.paper.pk}/approve-green", {}).json()["approved"], 1)
        self.green.refresh_from_db()
        self.assertEqual(self.green.approval_source, "human")

    def test_publish_carries_who_reviewed_and_the_library_can_filter(self):
        self.approve(self.green, approved=True, by="ai", agent="豆包")
        self.approve(self.yellow, approved=True)
        self.assertEqual(self.post(f"/api/papers/{self.paper.pk}/publish", {}).json()["created"], 2)
        reviews = {item.number: (item.review_source, item.review_agent) for item in PublishedQuestion.objects.all()}
        self.assertEqual(reviews, {1: ("ai", "豆包"), 2: ("human", "")})

        listing = self.client.get("/api/library").json()
        self.assertEqual(listing["facets"]["reviews"], {"human": 1, "ai": 1})
        self.assertEqual({item["number"]: item["review"]["source"] for item in listing["items"]}, {1: "ai", 2: "human"})
        only_people = self.client.get("/api/library?review=human").json()
        self.assertEqual([item["number"] for item in only_people["items"]], [2])

        # The person checks the card later: the library copy of that same
        # version becomes human-reviewed at once, without publishing again.
        self.approve(self.green, approved=True)
        live = PublishedQuestion.objects.get(number=1, status=PublishedQuestion.Status.PUBLISHED)
        self.assertEqual((live.review_source, live.review_agent, live.version), ("human", "", 1))
        self.assertEqual(self.post(f"/api/papers/{self.paper.pk}/publish", {}).json()["created"], 0)
        self.assertEqual(PublishedQuestion.objects.filter(number=1).count(), 1)

    def test_crop_is_served_as_png_with_numbered_candidates_on_request(self):
        plain = self.client.get(f"/api/questions/{self.green.pk}/crop")
        self.assertEqual(plain.status_code, 200)
        self.assertEqual(plain["Content-Type"], "image/png")
        image = Image.open(io.BytesIO(plain.content))
        self.assertEqual(image.size, (900, 90))
        marked = self.client.get(f"/api/questions/{self.green.pk}/crop?marks=1")
        self.assertEqual(marked.status_code, 200)
        Question.objects.filter(pk=self.yellow.pk).update(regions=[])
        self.assertEqual(self.client.get(f"/api/questions/{self.yellow.pk}/crop").status_code, 404)
        self.assertEqual(self.client.post(f"/api/questions/{self.green.pk}/crop").status_code, 405)

    def test_earlier_approvals_count_as_human(self):
        Question.objects.filter(pk=self.green.pk).update(approved=True, approval_source="")
        migration = __import__("core.migrations.0013_approval_source", fromlist=["existing_approvals_are_human"])
        migration.existing_approvals_are_human(django_apps, None)
        self.green.refresh_from_db()
        self.assertEqual(self.green.approval_source, "human")
        self.yellow.refresh_from_db()
        self.assertEqual(self.yellow.approval_source, "")
        self.assertEqual(library.agent_name(""), library.DEFAULT_AGENT)
        self.assertEqual(len(library.agent_name("x" * 80)), 40)
