"""Persistence checks for the in-app figure editor's presentation metadata."""

from __future__ import annotations

import json

from django.test import TestCase

from core.models import Paper, Question


class FigureEditorPersistenceTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="demo.pdf", kind="pdf", sha256="a" * 64,
            source_path="demo.pdf", render_path="demo.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status=Paper.Status.READY,
        )
        self.first = {"page_idx": 0, "bbox": [100, 200, 500, 600], "label": "图1"}
        self.second = {"page_idx": 0, "bbox": [600, 200, 900, 600], "label": "图2"}
        self.question = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice",
            regions=[{"page_idx": 0, "bbox": [50, 100, 950, 700]}],
            regions_auto=[{"page_idx": 0, "bbox": [50, 100, 950, 700]}],
            figure_candidates=[self.first, self.second], stem="如图是正方体的展开图",
            options={"A": "甲", "B": "乙"}, state=Question.State.YELLOW,
        )

    def post(self, action: str, body: dict):
        return self.client.post(
            f"/api/questions/{self.question.pk}/{action}",
            data=json.dumps(body), content_type="application/json", HTTP_X_QB_REQUEST="1",
        )

    def test_label_offset_and_irrelevant_candidate_survive_reload(self):
        first_key = "0:100,200,500,600"
        ignored_key = "0:600,200,900,600"
        response = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": self.first["bbox"], "slot": "stem",
                "label_offset": {"x": 36, "y": -28},
                "candidate_key": first_key,
            }],
            "ignored_candidates": [ignored_key],
        })
        self.assertEqual(response.status_code, 200, response.content)
        figure = response.json()["question"]["figures"][0]
        self.assertEqual(figure["label_offset"], {"x": 36.0, "y": -28.0})
        self.assertEqual(figure["candidate_key"], first_key)
        review = response.json()["question"]["figure_review"]
        self.assertEqual(review["ignored_candidates"], [ignored_key])
        self.assertEqual(review["excluded_count"], 1)

        self.question.refresh_from_db()
        self.assertEqual(self.question.figures[0]["label_offset"], {"x": 36.0, "y": -28.0})
        self.assertEqual(self.question.figures[0]["candidate_key"], first_key)
        self.assertEqual(self.question.figure_review["ignored_candidates"], [ignored_key])

    def test_candidate_provenance_survives_bbox_adjustment(self):
        first_key = "0:100,200,500,600"
        adjusted = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": [120, 220, 540, 640], "slot": "stem",
                "candidate_key": first_key,
            }],
            # The second detected candidate is still unresolved; explicitly
            # classifying it is now required before the conflict can clear.
            "ignored_candidates": ["0:600,200,900,600"],
        })
        self.assertEqual(adjusted.status_code, 200, adjusted.content)
        self.assertEqual(adjusted.json()["question"]["figures"][0]["candidate_key"], first_key)

    def test_used_candidate_cannot_also_be_saved_as_ignored(self):
        first_key = "0:100,200,500,600"
        response = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": [120, 220, 540, 640], "slot": "stem",
                "candidate_key": first_key,
            }],
            "ignored_candidates": [first_key],
        })
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("不能同时", response.json()["error"])
        self.question.refresh_from_db()
        self.assertEqual(self.question.figures, [])

    def test_changed_candidate_set_hides_stale_provenance_and_ignored_keys(self):
        first_key = "0:100,200,500,600"
        ignored_key = "0:600,200,900,600"
        saved = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": self.first["bbox"], "slot": "stem",
                "candidate_key": first_key,
            }],
            "ignored_candidates": [ignored_key],
        })
        self.assertEqual(saved.status_code, 200, saved.content)
        self.question.refresh_from_db()
        self.question.figure_candidates = []
        self.question.save(update_fields=["figure_candidates"])

        detail = self.client.get(f"/api/papers/{self.paper.pk}")
        self.assertEqual(detail.status_code, 200, detail.content)
        shown = detail.json()["questions"][0]
        self.assertNotIn("candidate_key", shown["figures"][0])
        self.assertEqual(shown["figure_review"]["ignored_candidates"], [])

        resaved = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": shown["figures"][0]["bbox"], "slot": "stem",
            }],
            "ignored_candidates": [],
        })
        self.assertEqual(resaved.status_code, 200, resaved.content)

    def test_omitted_ignored_field_and_later_text_edit_preserve_human_choice(self):
        ignored_key = "0:600,200,900,600"
        initial = self.post("figures", {
            "figures": [{"page_idx": 0, "bbox": self.first["bbox"], "slot": "stem"}],
            "ignored_candidates": [ignored_key],
        })
        self.assertEqual(initial.status_code, 200, initial.content)

        legacy_client_save = self.post("figures", {
            "figures": [{"page_idx": 0, "bbox": self.first["bbox"], "slot": "stem"}],
        })
        self.assertEqual(legacy_client_save.status_code, 200, legacy_client_save.content)
        self.assertEqual(
            legacy_client_save.json()["question"]["figure_review"]["ignored_candidates"],
            [ignored_key],
        )

        edited = self.post("text", {
            "stem": "如图是修改后的题干。",
            "options": {"A": "甲", "B": "乙"},
            "question_type": "single_choice",
        })
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(edited.json()["question"]["figure_review"]["ignored_candidates"], [ignored_key])

    def test_explicit_reread_keeps_manual_ignored_choices_for_worker(self):
        ignored_key = "0:600,200,900,600"
        saved = self.post("figures", {
            "figures": [{"page_idx": 0, "bbox": self.first["bbox"], "slot": "stem"}],
            "ignored_candidates": [ignored_key],
        })
        self.assertEqual(saved.status_code, 200, saved.content)

        queued = self.post("reread", {})
        self.assertEqual(queued.status_code, 200, queued.content)
        self.question.refresh_from_db()
        self.assertTrue(self.question.reread_requested)
        self.assertEqual(self.question.figure_review["ignored_candidates"], [ignored_key])

    def test_explicit_reread_keeps_human_no_figure_decision_for_worker(self):
        confirmed = self.post("figures", {"figures": [], "ignored_candidates": []})
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        before = confirmed.json()["question"]["figure_review"]
        self.assertEqual(before["status"], "confirmed_no_figure")
        self.assertEqual(before["source"], "human")

        queued = self.post("reread", {})
        self.assertEqual(queued.status_code, 200, queued.content)
        self.question.refresh_from_db()
        self.assertTrue(self.question.reread_requested)
        self.assertEqual(self.question.state, Question.State.WAITING)
        self.assertEqual(self.question.figure_review, before)

    def test_no_figure_undo_restores_label_offset(self):
        ignored_key = "0:600,200,900,600"
        self.assertEqual(self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": self.first["bbox"], "slot": "stem",
                "label_offset": {"x": 12, "y": 20},
            }],
            "ignored_candidates": [ignored_key],
        }).status_code, 200)
        self.assertEqual(self.post("figure-review", {"decision": "confirm_no_figure"}).status_code, 200)
        restored = self.post("figure-review", {"decision": "reset"})
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertEqual(restored.json()["question"]["figures"][0]["label_offset"], {"x": 12.0, "y": 20.0})
        self.assertEqual(restored.json()["question"]["figure_review"]["ignored_candidates"], [ignored_key])

    def test_text_edit_reuses_ignored_candidates_in_local_recalculation(self):
        no_figure = self.post("figures", {"figures": [], "ignored_candidates": []})
        self.assertEqual(no_figure.status_code, 200, no_figure.content)
        self.assertEqual(no_figure.json()["question"]["figure_review"]["status"], "confirmed_no_figure")

        edited = self.post("text", {
            "stem": "计算 1+1，并写出过程。",
            "options": {},
            "question_type": "free_response",
        })
        self.assertEqual(edited.status_code, 200, edited.content)
        review = edited.json()["question"]["figure_review"]
        self.assertEqual(review["status"], "auto_excluded")
        self.assertEqual(review["excluded_count"], 2)
        self.assertNotIn("candidate_unclassified", review.get("signals", []))

    def test_rejects_untrusted_label_offsets_and_candidate_keys(self):
        invalid_offset = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": self.first["bbox"], "slot": "stem",
                "label_offset": {"x": "secret", "y": 0},
            }],
            "ignored_candidates": [],
        })
        self.assertEqual(invalid_offset.status_code, 400)
        invalid_candidate = self.post("figures", {
            "figures": [{"page_idx": 0, "bbox": self.first["bbox"], "slot": "stem"}],
            "ignored_candidates": ["0:1,2,3,4"],
        })
        self.assertEqual(invalid_candidate.status_code, 400)
        invalid_provenance = self.post("figures", {
            "figures": [{
                "page_idx": 0, "bbox": self.first["bbox"], "slot": "stem",
                "candidate_key": "0:1,2,3,4",
            }],
            "ignored_candidates": [],
        })
        self.assertEqual(invalid_provenance.status_code, 400)
