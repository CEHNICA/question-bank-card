"""Regression coverage for resolving unclassified figure candidates."""

from __future__ import annotations

import json
from copy import deepcopy

from django.test import TestCase

from core import figure_policy
from core.models import Paper, Question


class FigureCandidateResolutionTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="book.pdf", kind="pdf", sha256="f" * 64, source_path="book.pdf",
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}], status=Paper.Status.READY,
        )
        self.first = {"label": "1", "page_idx": 0, "bbox": [100, 120, 300, 320]}
        self.second = {"label": "2", "page_idx": 0, "bbox": [400, 420, 700, 720]}
        self.first_key = "0:100,120,300,320"
        self.second_key = "0:400,420,700,720"
        reading = {
            "stem": "如图，选择正确图形。", "options": {"A": ""}, "type": "single_choice",
            "figures": {"1": "A"}, "missing_figure": False, "figure_descriptions": [],
        }
        self.question = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem=reading["stem"],
            options={"A": "图 A"},
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 900]}],
            figure_candidates=[self.first, self.second],
            figures=[{"slot": "A", "page_idx": 0, "bbox": self.first["bbox"], "source": "auto"}],
            read_a=reading, read_b=reading, state=Question.State.YELLOW,
        )

    def post_figures(self, *, ignored: list[str]):
        return self.client.post(
            f"/api/questions/{self.question.pk}/figures",
            data=json.dumps({
                "figures": [{
                    "slot": "A", "page_idx": 0, "bbox": self.first["bbox"],
                    "candidate_key": self.first_key,
                }],
                "ignored_candidates": ignored,
            }),
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )

    def test_detail_names_the_concrete_unclassified_candidate(self):
        detail = self.client.get(f"/api/papers/{self.paper.pk}")
        self.assertEqual(detail.status_code, 200, detail.content)
        review = detail.json()["questions"][0]["figure_review"]
        self.assertEqual(review["status"], "conflict")
        self.assertEqual(review["unclassified_count"], 1)
        self.assertEqual(review["unclassified_candidates"][0]["key"], self.second_key)

    def test_selected_figure_cannot_hide_an_unresolved_candidate(self):
        response = self.post_figures(ignored=[])
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("还有 1 张候选图尚未处理", response.json()["error"])
        self.question.refresh_from_db()
        self.assertNotEqual(self.question.figure_review.get("source"), "human")

    def test_selected_plus_explicitly_ignored_resolves_and_preserves_option_slot(self):
        response = self.post_figures(ignored=[self.second_key])
        self.assertEqual(response.status_code, 200, response.content)
        question = response.json()["question"]
        self.assertEqual(question["figures"][0]["slot"], "A")
        self.assertEqual(question["figures"][0]["candidate_key"], self.first_key)
        self.assertEqual(question["figure_review"]["status"], "ok")
        self.assertEqual(question["figure_review"]["source"], "human")
        self.assertEqual(question["figure_review"]["ignored_candidates"], [self.second_key])


class FigureRoleVocabularyTests(TestCase):
    """所有写入过的角色都不能被算成 conflict 候选 —— 这是 policy 守口的一部分。
    老代码只认 {"none", "decoration"} 为「已排除」，把 row / other / foreign /
    candidate / page_border / q / qOtherQuestion / q1a 都误判成 conflict。
    """

    def test_unknown_role_in_assignments_does_not_block_approval(self):
        candidates = [
            {"label": "1", "page_idx": 0, "bbox": [10, 10, 100, 100]},
            {"label": "2", "page_idx": 0, "bbox": [200, 200, 300, 300]},
        ]
        for role in ("none", "decoration", "table", "q3", "row", "other", "foreign",
                     "candidate", "page_border"):
            with self.subTest(role=role):
                # candidate 1 是本题的题干配图；candidate 2 用了被测词表。
                review = figure_policy.automatic_review(
                    stem="如图，选择一个正确的图形。",
                    options={"A": "图 A"},
                    candidate_labels={"1", "2"},
                    assignments={"1": "stem", "2": role},
                    figures=[{"slot": "stem", "page_idx": 0, "bbox": [0, 0, 5, 5],
                              "source": "auto"}],
                    own_number=1,
                )
                self.assertNotIn("candidate_unclassified", review.get("signals", []),
                                 f"role={role} 仍被算成未分类：{review}")

    def test_malformed_q_role_does_not_match_as_foreign(self):
        # q / qOtherQuestion / q1a 不是合法 foreign 角色，不能像 qN 那样被划进
        # 「已分配给别题」。但只要 assignments 里写过，就视为已分清。
        for role in ("q", "qOtherQuestion", "q1a", "q12.5"):
                with self.subTest(role=role):
                    review = figure_policy.automatic_review(
                        stem="如图，选择一个正确的图形。",
                        options={"A": "图 A"},
                        candidate_labels={"1", "2"},
                        assignments={"1": "stem", "2": role},
                        figures=[{"slot": "stem", "page_idx": 0, "bbox": [0, 0, 5, 5],
                                  "source": "auto"}],
                        own_number=1,
                    )
                    self.assertNotIn("candidate_unclassified", review.get("signals", []),
                                     f"role={role} 被算成未分类：{review}")

    def test_strict_q_role_with_other_question_number_is_foreign(self):
        review = figure_policy.automatic_review(
            stem="如图，选择一个正确的图形。",
            options={"A": "图 A"},
            candidate_labels={"1", "2"},
            assignments={"1": "stem", "2": "q3"},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [0, 0, 5, 5],
                      "source": "auto"}],
            own_number=1,
        )
        self.assertNotIn("candidate_unclassified", review.get("signals", []),
                         "q3 落进未分类：{review}".replace("已", "已"))

    def test_q_role_for_own_question_treated_as_stem(self):
        # 有人手动写了 q<own_number> 也得当成 stem，不然它会落进「未分类」。
        review = figure_policy.automatic_review(
            stem="如图，选择一个正确的图形。",
            options={"A": "图 A"},
            candidate_labels={"1", "2"},
            assignments={"1": "q1", "2": "none"},
            figures=[{"slot": "stem", "page_idx": 0, "bbox": [0, 0, 5, 5],
                      "source": "auto"}],
            own_number=1,
        )
        self.assertNotIn("candidate_unclassified", review.get("signals", []),
                         f"q<own> 仍被算成未分类：{review}")
