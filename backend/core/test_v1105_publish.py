"""1.10.5：入库分批送，页面能显示进度；内容没变的题不再逐题开事务。

全部离线：虚构题目，不调用任何服务。
"""

from __future__ import annotations

import json

from django.test import Client, TestCase
from django.utils import timezone

from . import library
from . import test_v110_types_origin as v110
from .models import PublishedQuestion, Question


class BatchedPublishTests(v110.TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        self.paper = self.make_paper()
        now = timezone.now()
        self.cards = []
        for number in range(1, 8):
            card = self.card(self.paper, number=number, question_type="free_response",
                             stem=f"第 {number} 题：已知 $x>{number}$，求 $x^2$ 的范围", flags=[])
            library.approve(card, now=now)
            card.save()
            self.cards.append(card)

    def publish(self, body):
        response = Client().post(f"/api/papers/{self.paper.id}/publish", data=json.dumps(body),
                                 content_type="application/json", HTTP_X_QB_REQUEST="1")
        return response

    def live(self):
        return PublishedQuestion.objects.filter(paper=self.paper, status=PublishedQuestion.Status.PUBLISHED)

    def test_a_batch_publishes_only_its_cards(self):
        first = self.publish({"question_ids": [card.id for card in self.cards[:3]]}).json()
        self.assertEqual((first["created"], first["unchanged"], first["problems"]), (3, 0, []))
        self.assertEqual(first["paper"]["counts"]["published"], 3)
        rest = self.publish({"question_ids": [card.id for card in self.cards[3:]]}).json()
        self.assertEqual(rest["created"], 4)
        self.assertEqual(self.live().count(), 7)

    def test_bad_batches_are_refused(self):
        for ids in ("1,2", [1, "2"], [True], list(range(201))):
            self.assertEqual(self.publish({"question_ids": ids}).status_code, 400, ids)
        self.assertFalse(self.live().exists())

    def test_publishing_again_changes_nothing_and_an_edit_makes_a_new_version(self):
        self.assertEqual(self.publish({}).json()["created"], 7)
        again = self.publish({}).json()
        self.assertEqual((again["created"], again["unchanged"]), (0, 7))
        self.assertEqual(PublishedQuestion.objects.filter(paper=self.paper).count(), 7)
        card = self.cards[0]
        card.refresh_from_db()
        card.stem = "改过的题干：已知 $x>1$"
        library.approve(card, now=timezone.now())
        card.save()
        self.assertFalse(library.already_published(card))
        result = self.publish({"question_ids": [card.id]}).json()
        self.assertEqual(result["created"], 1)
        versions = PublishedQuestion.objects.filter(question=card).order_by("version")
        self.assertEqual([(item.version, item.status) for item in versions], [(1, "superseded"), (2, "published")])

    def test_a_person_approving_what_an_ai_passed_still_relabels_the_library_copy(self):
        card = self.card(self.paper, number=20, question_type="free_response", stem="已知 $a>b$，求证 $a+1>b+1$",
                         flags=[])
        library.approve(card, now=timezone.now(), source="ai", agent="豆包")
        card.save()
        self.publish({"question_ids": [card.id]})
        live = self.live().get(question=card)
        self.assertEqual(live.review_source, "ai")
        # Approve as a person without the page's own relabelling, as an older client did.
        Question.objects.filter(pk=card.pk).update(approval_source="human", approval_agent="")
        card.refresh_from_db()
        self.assertFalse(library.already_published(card))
        self.publish({"question_ids": [card.id]})
        live.refresh_from_db()
        self.assertEqual((live.review_source, live.version), ("human", 1))

    def test_a_withdrawn_card_is_published_again(self):
        self.publish({})
        card = self.cards[1]
        library.withdraw(self.live().get(question=card))
        card.refresh_from_db()
        self.assertFalse(library.already_published(card))
        self.assertEqual(self.publish({"question_ids": [card.id]}).json()["created"], 1)
