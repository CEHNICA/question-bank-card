"""Offline tests for stored history and exact, read-only snapshot comparison."""

from copy import deepcopy
from unittest import mock

from django.test import Client, TestCase

from . import library
from .models import PublishedQuestion
from .test_v110_types_origin import TempDataMixin


class LibraryHistoryTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.question = self.card(self.paper, question_type="single_choice", stem="求 $x+1$ 的值。")
        self.content = {
            "stem": "求 $x+1$ 的值。", "question_type": "single_choice", "options": {"A": "1", "B": "2"},
            "answer": "A", "analysis": "原卷解析", "origin": "演示题源", "number": 2,
            "sources": [{"page_idx": 0, "bbox": [50, 100, 480, 300], "type": "text", "source": "mineru"}],
            "figures": [{"slot": "stem", "page_idx": 0, "bbox": [50, 200, 300, 400],
                         "source": "human", "parts": [], "file": "figure-1.png", "url": "/api/library/old/figures/figure-1.png"}],
        }
        self.first = self.publish(1, self.content, status="superseded", reviewer="ai")
        changed = deepcopy(self.content)
        changed.update(stem="求 $x-1$ 的值。", answer="B")
        changed["figures"][0]["url"] = "/api/library/new/figures/figure-1.png"
        self.second = self.publish(2, changed)

    def publish(self, version, content, *, status="published", reviewer="human", question=None):
        return PublishedQuestion.objects.create(
            question=question or self.question, paper=self.paper, source_filename="历史演示卷.pdf",
            number=2, question_type="single_choice", version=version, status=status,
            content=content, content_hash=library.content_hash(content), review_source=reviewer,
            review_agent="演示助手" if reviewer == "ai" else "",
        )

    def detail(self, publication, **params):
        return Client().get(f"/api/library/{publication.id}", params)

    def test_history_contains_self_and_only_versions_of_the_exact_card(self):
        another = self.card(self.paper, number=2, question_type="free_response", stem="无关的同号题")
        self.publish(1, self.content, question=another)
        body = self.detail(self.first).json()
        self.assertEqual([row["version"] for row in body["history"]], [2, 1])
        self.assertEqual([row["id"] for row in body["versions"]], [str(self.second.id)])
        self.assertEqual(body["history"][0]["changes"], ["题干", "答案"])
        self.assertEqual(body["history"][0]["previous_id"], str(self.first.id))
        self.assertEqual(body["history"][1]["review"], {"source": "ai", "agent": "演示助手"})

    def test_comparison_keeps_snapshots_and_highlights_the_changed_math_symbol(self):
        before = deepcopy(self.first.content)
        after = deepcopy(self.second.content)
        # A mutable draft and mutable AI extras must not replace old evidence.
        self.question.stem = "题卡还未入库的修改"
        self.question.save()
        self.second.extras = {"ai_answer": {"answer": "不可当成历史原卷答案"}}
        self.second.save()
        comparison = self.detail(self.second, compare=str(self.first.id)).json()["comparison"]
        self.assertEqual(comparison["publication"]["content"], before)
        self.assertEqual([field["key"] for field in comparison["changes"]], ["stem", "answer"])
        segments = comparison["changes"][0]["segments"]
        self.assertTrue(any(s["before"] == "+" and s["after"] == "-" for s in segments))
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.content, self.second.content), (before, after))
        self.assertEqual(self.first.status, "superseded")

    def test_figure_urls_are_ignored_but_cross_page_crops_are_compared(self):
        self.assertNotIn("配图", [field["label"] for field in library.publication_changes(self.first, self.second)])
        changed = deepcopy(self.second.content)
        changed["figures"][0]["parts"] = [{"page_idx": 1, "bbox": [50, 50, 300, 150]}]
        third = self.publish(3, changed)
        self.assertEqual(library.publication_changes(self.second, third), [{"key": "figures", "label": "配图"}])
        changed["sources"][0]["bbox"] = [50, 110, 480, 300]
        third.content = changed
        self.assertEqual([field["key"] for field in library.publication_changes(self.second, third)], ["figures", "sources"])

    def test_comparison_refuses_invalid_or_unrelated_version(self):
        other_card = self.card(self.paper, number=2)
        unrelated = self.publish(1, self.content, question=other_card)
        self.assertEqual(self.detail(self.second, compare="bad-id").status_code, 400)
        self.assertEqual(self.detail(self.second, compare=str(unrelated.id)).status_code, 404)
        self.assertEqual(Client().post(f"/api/library/{self.first.id}").status_code, 405)

    def test_orphan_snapshots_are_not_grouped_together(self):
        PublishedQuestion.objects.all().update(question=None)
        body = self.detail(self.first).json()
        self.assertEqual(len(body["history"]), 1)
        self.assertEqual(body["versions"], [])
        self.assertEqual(self.detail(self.first, compare=str(self.second.id)).status_code, 400)

    def test_long_text_has_full_preview_without_unbounded_character_diff(self):
        self.second.content = {**self.content, "stem": "长题干" * 8000}
        self.second.save()
        with mock.patch("core.library.SequenceMatcher", side_effect=AssertionError("unexpected long diff")):
            changes = library.publication_changes(self.first, self.second)
        self.assertEqual(changes[0]["after"], "长题干" * 8000)
        self.assertNotIn("segments", changes[0])

    def test_history_summaries_do_not_compute_character_diffs(self):
        with mock.patch("core.library.SequenceMatcher", side_effect=AssertionError("summary must stay cheap")):
            self.assertEqual(self.detail(self.second).status_code, 200)
