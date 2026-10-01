"""1.10.3：600 多题的教材打开、打勾、刷新列表都要快，算出来的结果和以前一样。

全部离线：虚构题目，不调用任何服务。
"""

from __future__ import annotations

import json

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from . import library, views
from . import test_v110_types_origin as v110
from .figure_policy import reusing_reviews, stored_or_derived_review
from .models import Question

STEM = "如图，已知 $AB=AC$，求 $\\angle B$ 的度数"


class ReviewMemoTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()

    def test_inside_the_memo_each_card_is_derived_once_and_gets_its_own_copy(self):
        card = self.card(self.paper, stem=STEM, figure_review={})
        plain = stored_or_derived_review(card)
        with reusing_reviews():
            first = stored_or_derived_review(card)
            first["status"] = "changed by a caller"
            second = stored_or_derived_review(card)
        self.assertEqual(second, plain)
        self.assertNotEqual(first, second)

    def test_outside_the_memo_a_changed_card_is_seen_at_once(self):
        card = self.card(self.paper, stem="已知 $a>b$，求证 $a+1>b+1$", figure_review={})
        before = stored_or_derived_review(card)
        card.stem = STEM
        after = stored_or_derived_review(card)
        self.assertNotEqual(before.get("cue_matches"), after.get("cue_matches"))


class PaperSpeedTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        now = timezone.now()
        self.cards = []
        for number in range(1, 13):
            card = self.card(self.paper, number=number, question_type="free_response",
                             stem=f"第 {number} 题：已知 $x>{number}$，求 $x^2$ 的范围", flags=[])
            if number <= 6:
                library.approve(card, now=now)
                card.save()
            if number <= 3:
                library.publish(card)
            self.cards.append(card)

    def detail(self):
        return Client().get(f"/api/papers/{self.paper.id}").json()

    def test_the_page_needs_the_same_few_queries_however_many_cards(self):
        with CaptureQueriesContext(connection) as few:
            self.detail()
        for number in range(13, 40):
            self.card(self.paper, number=number, question_type="free_response", stem=f"第 {number} 题", flags=[])
        with CaptureQueriesContext(connection) as many:
            data = self.detail()
        self.assertEqual(len(many), len(few))
        self.assertEqual(len(data["questions"]), 39)

    def test_publication_and_counts_are_what_they_were(self):
        data = self.detail()
        shown = {item["number"]: item for item in data["questions"]}
        for card in self.cards:
            card.refresh_from_db()
            self.assertEqual(shown[card.number]["publication"], library.publication_state(card))
            self.assertEqual(shown[card.number]["approved"], library.approval_is_current(card))
        self.assertEqual(data["paper"]["counts"]["approved"], 6)
        self.assertEqual(data["paper"]["counts"]["published"], 3)
        # The list computes the same counts its own (remembered) way.
        listed = next(item for item in Client().get("/api/papers").json()["papers"] if item["id"] == str(self.paper.id))
        self.assertEqual(listed["counts"], data["paper"]["counts"])

    def test_a_remembered_verdict_follows_every_change_to_the_card(self):
        def counts():
            return views.paper_json(self.paper)["counts"]

        self.assertEqual(counts()["approved"], 6)
        # Through the page: un-tick one.
        response = Client().post(f"/api/questions/{self.cards[0].id}/approve", data=json.dumps({"approved": False}),
                                 content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.json()["paper"]["counts"]["approved"], 5)
        # Behind its back, without touching updated_at: the text of an approved card changes.
        Question.objects.filter(pk=self.cards[1].pk).update(stem="改过的题干")
        self.assertEqual(counts()["approved"], 4)
        # The paper's name is part of every approved version.
        self.paper.task_name = "改名后的试卷"
        self.paper.save(update_fields=["task_name"])
        self.paper.refresh_from_db()
        renamed = counts()
        expected = sum(1 for card in Question.objects.filter(paper=self.paper).select_related("paper", "group")
                       if library.approval_is_current(card))
        self.assertEqual(renamed["approved"], expected)

    def test_remembered_and_fresh_verdicts_agree(self):
        rows = list(self.paper.questions.select_related("paper", "group"))
        self.assertEqual(sorted(views.card_verdicts(self.paper)), sorted(views.card_verdicts(self.paper, rows)))
        self.assertEqual(sorted(views.card_verdicts(self.paper)), sorted(views.card_verdicts(self.paper, rows)))
