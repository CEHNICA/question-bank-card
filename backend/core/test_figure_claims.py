"""Cross-card figure relationships with isolated source geometry."""
from copy import deepcopy
from unittest import mock

from PIL import Image

from django.test import SimpleTestCase, TestCase

from . import figure_claims, figure_policy, pipeline
from .models import Block, Paper, Question, QuestionGroup


BOX = [100, 200, 450, 420]


def diagram(**extra):
    return {"page_idx": 0, "bbox": list(BOX), "slot": "stem", "source": "auto", **extra}


def caption(text="第3～5题共用下图", **extra):
    return {"seq": 50, "type": "text", "page_idx": 0, "bbox": [100, 425, 450, 445],
            "text": text, **extra}


class FigureLedgerTests(SimpleTestCase):
    def card(self, pk, number, **extra):
        return {"id": pk, "number": number, "group_id": 1, "figures": [diagram()], **extra}

    def test_competing_claims_are_visible_without_changing_figures(self):
        cards = [self.card(1, 3), self.card(2, 4)]
        original = deepcopy(cards)
        ledger = figure_claims.build_ledger(cards)
        self.assertEqual(set(ledger["conflicts"]), {"1", "2"})
        self.assertEqual(ledger["figures"][0]["status"], "conflict")
        self.assertEqual(cards, original)

    def test_printed_shared_caption_allows_many_questions(self):
        ledger = figure_claims.build_ledger([self.card(i, i) for i in (3, 4, 5)], [caption()])
        self.assertFalse(ledger["conflicts"])
        self.assertEqual(ledger["figures"][0]["status"], "shared")

    def test_another_group_or_option_cannot_use_shared_permission(self):
        for other in (self.card(2, 4, group_id=2), self.card(2, 4, figures=[diagram(slot="A")])):
            ledger = figure_claims.build_ledger([self.card(1, 3), other], [caption()])
            self.assertTrue(ledger["conflicts"])

    def test_one_image_cannot_be_two_options_in_one_question(self):
        ledger = figure_claims.build_ledger([self.card(1, 3, figures=[diagram(slot="A"), diagram(slot="B")])])
        self.assertIn("1", ledger["conflicts"])

    def test_joined_piece_cannot_hide_another_cards_claim(self):
        piece = {"page_idx": 1, "bbox": [100, 100, 450, 250]}
        cards = [self.card(1, 3, figures=[diagram(parts=[piece])]),
                 self.card(2, 4, figures=[{**piece, "slot": "stem"}])]
        self.assertEqual(set(figure_claims.build_ledger(cards)["conflicts"]), {"1", "2"})

    def test_wrong_page_inside_graph_and_handwriting_captions_are_not_authority(self):
        for block in (caption(page_idx=1), caption(bbox=[100, 250, 450, 275]),
                      caption(handwritten=True), caption(bbox=[600, 425, 900, 445])):
            self.assertIsNone(figure_claims.shared_caption(diagram(), [block]))

    def test_inconsistent_shared_captions_are_not_guessed(self):
        self.assertIsNone(figure_claims.shared_caption(diagram(), [caption(), caption("第3、6题共用下图")]))

    def test_mixed_range_and_list_is_expanded_completely(self):
        self.assertEqual(figure_claims.shared_caption(diagram(), [caption("第3—5、7题共用图")])["numbers"],
                         [3, 4, 5, 7])

    def test_duplicate_shared_numbers_and_large_ranges_are_rejected(self):
        for text in ("第3、3题共用图", "第3～300题共用图", "第5～3题共用图"):
            self.assertIsNone(figure_claims.shared_caption(diagram(), [caption(text)]))

    def test_wide_caption_does_not_guess_which_image_it_labels(self):
        second = diagram(bbox=[500, 200, 800, 420])
        cards = [self.card(1, 3, figures=[diagram(), second]), self.card(2, 4, figures=[diagram(), second])]
        ledger = figure_claims.build_ledger(cards, [caption(bbox=[100, 425, 800, 445])])
        self.assertTrue(ledger["conflicts"])
        self.assertTrue(all(item.get("ambiguous_shared_caption") for item in ledger["figures"]))

    def test_invalid_geometry_is_not_an_inventory_entry(self):
        bad = self.card(1, 3, figures=[diagram(bbox=[0, 0, float("nan"), 20]), diagram(page_idx=True)])
        self.assertEqual(figure_claims.build_ledger([bad])["figures"], [])

    def test_reading_pipeline_uses_marker_and_preserves_the_original_model_role(self):
        candidate = {"label": "9", "page_idx": 0, "bbox": [130, 100, 260, 210]}
        snapshot = {"id": 1, "number": 3, "question_type": "single_choice",
                    "regions": [{"page_idx": 0, "bbox": [50, 50, 900, 900]}],
                    "candidates": [candidate], "witness_blocks": [
                        {"page_idx": 0, "bbox": [100, 100, 120, 120], "text": "A.", "type": "text"}]}
        reading = {"stem": "请选择正确的选项。", "options": {slot: slot + "选项" for slot in "ABCD"},
                   "type": "single_choice", "figures": {"9": "stem"}, "missing_figure": False}
        image = Image.new("RGB", (500, 500), "white")
        with mock.patch.object(pipeline.readers, "primary_engine", return_value=mock.Mock()), \
                mock.patch.object(pipeline.readers, "read_question", return_value=reading) as reader, \
                mock.patch.object(pipeline.imaging, "stack_regions", return_value=(image, [])):
            result = pipeline.read_card(snapshot, mock.Mock())
        self.assertEqual(reader.call_count, 1)
        self.assertEqual(result["figures"][0]["slot"], "A")
        self.assertEqual(result["read_a"]["figures"], {"9": "stem"})
        self.assertEqual(result["read_a"]["figure_layout"]["evidence"]["9"]["slot"], "A")

    def test_conflicting_marker_requires_review_and_retains_the_model_candidate(self):
        candidates = [{"label": "9", "page_idx": 0, "bbox": [130, 100, 260, 210]}]
        result = pipeline._resolve_automatic_figure_assignments(
            stem="请选择", options={}, kind="single_choice", candidates=candidates,
            assignments={"9": "A"}, blocks=[{"page_idx": 0, "bbox": [100, 100, 120, 120], "text": "B."}])
        self.assertEqual(result, {"9": "A"})


class FigureClaimIntegrationTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="fixture.pdf", kind="pdf", sha256="c" * 64,
                                          pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
                                          status="ready", processing_plan={"revision": 1})
        self.group = QuestionGroup.objects.create(paper=self.paper, title="卷一", kind="exam", sequence=0)

    def question(self, number, **extra):
        values = dict(paper=self.paper, group=self.group, number=number, stem="如图，求面积",
                      question_type="free_response", figures=[diagram()], state="green",
                      figure_review={"status": "ok", "source": "automatic"}, flags=[])
        values.update(extra)
        return Question.objects.create(**values)

    def test_conflicts_flag_both_cards_and_clear_after_correction(self):
        first, second = self.question(3), self.question(4)
        ledger = pipeline.audit_figure_claims(self.paper, revision=1)
        self.assertEqual(len(ledger["conflicts"]), 2)
        for item in (first, second):
            item.refresh_from_db()
            self.assertEqual(item.state, "yellow")
            self.assertIn(figure_policy.FLAG_FIGURE_CLAIM_CONFLICT, item.flags)
        second.figures = [diagram(bbox=[600, 200, 900, 420])]
        second.save()
        pipeline.audit_figure_claims(self.paper, revision=1)
        first.refresh_from_db()
        self.assertNotIn(figure_policy.FLAG_FIGURE_CLAIM_CONFLICT, first.flags)

    def test_shared_caption_adds_only_unique_referenced_cards(self):
        self.question(3, figure_candidates=[{**diagram(), "label": "1"}])
        second, third = self.question(4, figures=[]), self.question(5, figures=[])
        Block.objects.create(paper=self.paper, **caption())
        ledger = pipeline.audit_figure_claims(self.paper, revision=1)
        self.assertFalse(ledger["conflicts"])
        for item in (second, third):
            item.refresh_from_db()
            self.assertEqual(item.figures[0]["source"], "shared")
            self.assertEqual(item.figures[0]["shared_numbers"], [3, 4, 5])
            self.assertIn(figure_policy.FLAG_SHARED_FIGURE, item.flags)
        self.paper.refresh_from_db()
        self.assertEqual(self.paper.processing_plan["figure_assignment_audit"]["schema"], 1)

    def test_approved_edited_and_human_decisions_are_never_changed(self):
        protected = [self.question(3, approved=True), self.question(4, edited=True),
                     self.question(5, figure_review={"source": "human", "status": "ok"})]
        before = deepcopy([(item.figures, item.flags, item.approved, item.figure_review) for item in protected])
        pipeline.audit_figure_claims(self.paper, revision=1)
        for item, expected in zip(protected, before):
            item.refresh_from_db()
            self.assertEqual((item.figures, item.flags, item.approved, item.figure_review), expected)

    def test_stale_worker_cannot_write_audit_or_shared_figures(self):
        self.question(3, figure_candidates=[diagram()])
        self.question(4, figures=[])
        self.question(5, figures=[])
        Block.objects.create(paper=self.paper, **caption())
        self.assertEqual(pipeline.audit_figure_claims(self.paper, revision=0), {})
        self.paper.refresh_from_db()
        self.assertNotIn("figure_assignment_audit", self.paper.processing_plan)

    def test_missing_referenced_question_does_not_fabricate_shared_cards(self):
        self.question(3, figure_candidates=[diagram()])
        other = self.question(4, figures=[])
        Block.objects.create(paper=self.paper, **caption())
        pipeline.audit_figure_claims(self.paper, revision=1)
        other.refresh_from_db()
        self.assertEqual(other.figures, [])

    def test_foreign_figure_cannot_revoke_approval(self):
        approved = self.question(3, approved=True)
        pipeline.assign_foreign_figures(self.paper, [{"number": 3, "page_idx": 0, "bbox": [600, 200, 900, 420]}])
        approved.refresh_from_db()
        self.assertTrue(approved.approved)
        self.assertEqual(approved.figures, [diagram()])

    def test_formula_and_shared_decision_survive_reading_snapshot(self):
        warning = pipeline.segment.AMBIGUOUS_CONTINUATION_FLAG_PREFIX + "请核查第3题续页"
        question = self.question(3, flags=[warning, figure_policy.FLAG_SHARED_FIGURE])
        self.assertEqual(pipeline._snapshot(question)["segmentation_flags"],
                         [warning, figure_policy.FLAG_SHARED_FIGURE])

    def test_borrowed_figure_recheck_retains_current_label_evidence(self):
        question = self.question(3, figures=[diagram(source="shared")])
        evidence = {"1": {"slot": "A", "source": "printed_option_marker"}}
        fields = {"figures": [diagram(slot="A", bbox=[600, 200, 900, 420])],
                  "figure_review": {"status": "ok", "assignment_evidence": evidence},
                  "flags": [], "state": "green"}
        pipeline._apply_reading_fields(question, fields)
        question.refresh_from_db()
        self.assertEqual(question.figure_review["assignment_evidence"], evidence)
        self.assertTrue(any(item["source"] == "shared" for item in question.figures))
