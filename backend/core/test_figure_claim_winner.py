"""One source picture belongs to one card: the card whose text asks for it."""
from copy import deepcopy

from django.test import SimpleTestCase, TestCase

from . import figure_policy, pipeline
from .models import Paper, Question, QuestionGroup


BOX = [100, 200, 450, 420]
CUED = "如图，正六边形的顶点为 ABCDEF，求 ∠1+∠2"
PLAIN = "在一个不透明的袋中有 2 个红球、3 个黄球和 4 个白球，求摸到红球的概率"


def diagram(**extra):
    return {"page_idx": 0, "bbox": list(BOX), "slot": "stem", "source": "auto", **extra}


class ContestWinnerTests(SimpleTestCase):
    def card(self, pk, stem, options=None):
        return {"id": pk, "stem": stem, "options": options or {}}

    def test_only_the_card_that_asks_for_a_picture_keeps_it(self):
        self.assertEqual(
            figure_policy.contest_winner([self.card(1, PLAIN), self.card(2, CUED)]), 2)
        self.assertEqual(
            figure_policy.contest_winner([self.card(2, CUED), self.card(1, PLAIN)]), 2)

    def test_text_that_cannot_separate_them_keeps_both(self):
        self.assertIsNone(figure_policy.contest_winner([self.card(1, CUED), self.card(2, CUED)]))
        self.assertIsNone(figure_policy.contest_winner([self.card(1, PLAIN), self.card(2, PLAIN)]))

    def test_a_cue_hidden_in_the_options_still_counts(self):
        self.assertEqual(
            figure_policy.contest_winner(
                [self.card(1, PLAIN, {"A": "如图"}), self.card(2, PLAIN)]), 1)

    def test_asking_the_student_to_draw_is_not_a_picture(self):
        # 「画出函数图象」是让学生画，不证明原卷印了图，所以它不能抢走别人的图。
        drawn = "画出函数 y=x² 的图象"
        self.assertEqual(figure_policy.contest_winner([self.card(1, drawn), self.card(2, CUED)]), 2)
        self.assertIsNone(figure_policy.contest_winner([self.card(1, drawn), self.card(2, PLAIN)]))


class FigureClaimWinnerIntegrationTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="fixture.pdf", kind="pdf", sha256="d" * 64,
                                          pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
                                          status="ready", processing_plan={"revision": 1})
        self.group = QuestionGroup.objects.create(paper=self.paper, title="卷一", kind="exam", sequence=0)

    def question(self, number, stem, **extra):
        values = dict(paper=self.paper, group=self.group, number=number, stem=stem,
                      question_type="free_response", figures=[diagram()], state="green",
                      figure_review={"status": "ok", "source": "automatic"}, flags=[])
        values.update(extra)
        return Question.objects.create(**values)

    def test_the_same_picture_is_taken_off_the_card_that_never_asked_for_it(self):
        # 48zhou 第 12/13 题：六边形图印在第 13 题「如图」旁边，第 12 题是概率题。
        plain, cued = self.question(12, PLAIN), self.question(13, CUED)
        ledger = pipeline.audit_figure_claims(self.paper, revision=1)
        self.assertFalse(ledger["conflicts"])
        plain.refresh_from_db()
        cued.refresh_from_db()
        self.assertEqual(plain.figures, [])
        self.assertEqual(cued.figures, [diagram()])
        for item in (plain, cued):
            self.assertNotIn(figure_policy.FLAG_FIGURE_CLAIM_CONFLICT, item.flags)

    def test_a_card_that_keeps_its_own_other_picture_does_not_lose_them(self):
        second = diagram(bbox=[600, 200, 900, 420])
        plain = self.question(12, PLAIN, figures=[diagram(), second])
        cued = self.question(13, CUED)
        pipeline.audit_figure_claims(self.paper, revision=1)
        plain.refresh_from_db()
        cued.refresh_from_db()
        self.assertEqual(plain.figures, [second])
        self.assertEqual(cued.figures, [diagram()])

    def test_text_that_cannot_separate_them_is_left_for_a_person(self):
        first, second = self.question(12, CUED), self.question(13, CUED)
        before = deepcopy([(item.figures, item.figure_review) for item in (first, second)])
        ledger = pipeline.audit_figure_claims(self.paper, revision=1)
        self.assertEqual(len(ledger["conflicts"]), 2)
        for item, expected in zip((first, second), before):
            item.refresh_from_db()
            self.assertEqual((item.figures, item.figure_review), expected)
            self.assertIn(figure_policy.FLAG_FIGURE_CLAIM_CONFLICT, item.flags)

    def test_two_cards_that_both_never_mention_a_picture_are_left_alone(self):
        first, second = self.question(12, PLAIN), self.question(13, PLAIN)
        ledger = pipeline.audit_figure_claims(self.paper, revision=1)
        self.assertEqual(len(ledger["conflicts"]), 2)
        for item in (first, second):
            item.refresh_from_db()
            self.assertEqual(item.figures, [diagram()])

    def test_a_person_decided_card_is_never_made_to_give_the_picture_up(self):
        plain = self.question(12, PLAIN, edited=True)
        cued = self.question(13, CUED)
        pipeline.audit_figure_claims(self.paper, revision=1)
        plain.refresh_from_db()
        cued.refresh_from_db()
        self.assertEqual(plain.figures, [diagram()])
        self.assertEqual(cued.figures, [diagram()])