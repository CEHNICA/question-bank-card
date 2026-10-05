"""零配图 + 明确其余候选都无关，是一个完整的决定：记成「已人工确认本题无图」。

1.13.3：配图冲突面板的文案一直承诺「或明确确认其余候选均与本题无关」，但题面本来
就没有图的题（AI 多框了几张手写或别题的图）在界面上点不到这个动作 —— 按钮被「有配图
才显示」挡住，前端那个确认函数遇到零配图又直接把它弹回去。

这几个测试钉住后端本来就有的行为：空配图 + 全部候选进 ignored，落
confirmed_no_figure、解除阻止。前端补按钮后必须走通这条路，不能再在前端把它拦下。
"""
from __future__ import annotations

import json

from django.test import TestCase

from core.models import Paper, Question

from .test_v110_types_origin import TempDataMixin


class ZeroFigureConfirmTests(TempDataMixin, TestCase):
    """一道题一张配图都没有，但原卷上留着两张候选（手写痕迹 / 邻题的东西）。"""

    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        self.paper.status = Paper.Status.READY
        self.paper.save(update_fields=["status"])
        self.first = {"label": "1", "page_idx": 0, "bbox": [100, 120, 300, 320]}
        self.second = {"label": "2", "page_idx": 0, "bbox": [400, 420, 700, 720]}
        self.first_key = "0:100,120,300,320"
        self.second_key = "0:400,420,700,720"
        self.question = self.card(
            self.paper, number=1, question_type="single_choice", stem="下列说法正确的是。",
            options={"A": "甲"}, figures=[], figure_candidates=[self.first, self.second],
            figure_review={
                "status": "conflict", "signals": ["candidate_unclassified"],
                "unclassified_count": 2, "excluded_count": 0,
            },
            state=Question.State.YELLOW,
        )

    def post_figures(self, *, figures, ignored):
        return self.client.post(
            f"/api/questions/{self.question.pk}/figures",
            data=json.dumps({"figures": figures, "ignored_candidates": ignored}),
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )

    def test_confirming_every_candidate_irrelevant_lands_on_no_figure(self):
        response = self.post_figures(figures=[], ignored=[self.first_key, self.second_key])
        self.assertEqual(response.status_code, 200, response.content)
        question = response.json()["question"]
        self.assertEqual(question["figures"], [])
        review = question["figure_review"]
        self.assertEqual(review["status"], "confirmed_no_figure")
        self.assertIn("human_confirmed_no_figure", review["signals"])
        self.assertEqual(review["source"], "human")
        # 全部候选都要记成无关 —— 否则下次重算又会把它们当成漏网的冒出来。
        self.assertEqual(sorted(review["ignored_candidates"]), sorted([self.first_key, self.second_key]))
        self.assertEqual(review["excluded_count"], 2)

    def test_no_figure_confirm_clears_the_block_that_kept_the_question_unapproved(self):
        before = self.client.get(f"/api/papers/{self.paper.pk}")
        self.assertEqual(before.status_code, 200, before.content)
        self.assertEqual(before.json()["questions"][0]["figure_review"]["status"], "conflict")
        self.assertEqual(
            self.post_figures(figures=[], ignored=[self.first_key, self.second_key]).status_code, 200)
        approved = self.client.post(f"/api/questions/{self.question.pk}/approve",
                                    json.dumps({"approved": True}),
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(approved.status_code, 200, approved.content)
        self.question.refresh_from_db()
        self.assertTrue(self.question.publications.exists())

    def test_ignored_list_comes_back_complete_even_when_the_client_sends_none(self):
        """前端可能只发它当下看到的那几张；后端按题库里的候选补全，不留尾巴。"""
        response = self.post_figures(figures=[], ignored=[])
        self.assertEqual(response.status_code, 200, response.content)
        review = response.json()["question"]["figure_review"]
        self.assertEqual(review["status"], "confirmed_no_figure")
        self.assertEqual(sorted(review["ignored_candidates"]), sorted([self.first_key, self.second_key]))

    def test_unknown_candidate_keys_are_still_refused(self):
        """补全不等于放行：题库里根本没有的 key 仍然是格式错误。"""
        response = self.post_figures(figures=[], ignored=["0:1,1,2,2"])
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("无关候选图", response.json()["error"])
        self.question.refresh_from_db()
        # 被拒之后这道题的判断必须原样留着，不能顺手改成「已确认无图」。
        self.assertEqual(self.question.figure_review.get("status"), "conflict")
        self.assertEqual(self.question.figure_review.get("signals"), ["candidate_unclassified"])
        self.assertEqual(self.question.figures, [])
