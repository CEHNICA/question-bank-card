"""Saved option geometry survives policy upgrades without duplicating figures."""

from copy import deepcopy
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from . import figure_policy
from .models import Paper, Question


CANDIDATE = {"label": "1", "seq": 7, "page_idx": 0, "bbox": [130, 100, 260, 210]}


def reading(role="stem", slot="A", applied=True):
    key = figure_policy.candidate_key(CANDIDATE)
    return {
        "stem": "下列图形正确的是（ ）", "type": "single_choice", "options": {},
        "figures": {"1": role}, "figure_layout": {
            "candidate_keys": {"1": key},
            "evidence": {"1": {
                "slot": slot, "page_idx": 0, "marker_bbox": [100, 100, 120, 120],
                "marker_seq": 6, "source": "printed_option_marker",
                "applied": applied, "candidate_key": key,
            }},
            "conflicts": [],
        },
    }


def question_stub(**changes):
    values = {
        "number": 1, "stem": "下列图形正确的是（ ）", "options": {}, "flags": [],
        "question_type": "single_choice", "state": "yellow", "figure_review": {},
        "read_a": reading(), "read_b": {}, "read_c": {},
        "figure_candidates": [deepcopy(CANDIDATE)],
        "figures": [{"slot": "A", "page_idx": 0, "bbox": CANDIDATE["bbox"], "source": "auto"}],
    }
    values.update(changes)
    return SimpleNamespace(**values)


class ValidatedLayoutPolicyTests(SimpleTestCase):
    def test_validated_roles_are_a_copy_and_keep_raw_reader_evidence(self):
        raw = reading()
        roles = {"1": "stem"}
        before = deepcopy((raw, roles))
        resolved = figure_policy.validated_layout_assignments(raw, [CANDIDATE], roles)
        self.assertEqual(resolved, {"1": "A"})
        self.assertIsNot(resolved, roles)
        self.assertEqual((raw, roles), before)

    def test_malformed_layout_values_are_ignored(self):
        for value in ("old-layout", [], True, 1, {"evidence": []}, {"evidence": {"1": "bad"}}):
            with self.subTest(value=value):
                raw = reading()
                raw["figure_layout"] = value
                self.assertEqual(figure_policy.validated_layout_assignments(raw, [CANDIDATE], {"1": "stem"}),
                                 {"1": "stem"})
                figure_policy.stored_or_derived_review(question_stub(read_a=raw))

    def test_stale_crop_wrong_source_and_invalid_metadata_are_ignored(self):
        for field, value in (("candidate_key", "0:200,100,330,210"), ("source", "model_guess"),
                             ("slot", "q3"), ("slot", []), ("applied", 1), ("page_idx", 1)):
            with self.subTest(field=field, value=value):
                raw = reading()
                raw["figure_layout"]["evidence"]["1"][field] = value
                self.assertEqual(figure_policy.validated_layout_assignments(raw, [CANDIDATE], {"1": "stem"}),
                                 {"1": "stem"})

    def test_protected_roles_and_manual_candidates_are_not_changed(self):
        raw = reading()
        for role in ("B", "none", "decoration", "q3", "table", "old_custom_role"):
            with self.subTest(role=role):
                self.assertEqual(figure_policy.validated_layout_assignments(raw, [CANDIDATE], {"1": role}),
                                 {"1": role})
        for source in ("manual", "human"):
            self.assertEqual(figure_policy.validated_layout_assignments(raw, [{**CANDIDATE, "source": source}],
                                                                       {"1": "stem"}), {"1": "stem"})

    def test_duplicate_candidate_labels_are_not_used(self):
        self.assertEqual(figure_policy.validated_layout_assignments(
            reading(), [CANDIDATE, {**CANDIDATE, "bbox": [530, 100, 660, 210]}], {"1": "stem"}),
            {"1": "stem"})

    def test_get_upgrade_preserves_validated_evidence_and_conflicts(self):
        raw = reading(role="A", slot="B", applied=False)
        conflict = {"candidate_label": "1", "current_role": "A", "proposed_role": "B",
                    "reason": "printed_option_marker_disagrees"}
        raw["figure_layout"]["conflicts"] = [conflict]
        question = question_stub(read_a=raw)
        before_raw = deepcopy(question.read_a)
        review = figure_policy.stored_or_derived_review(question)
        self.assertEqual(review["assignment_evidence"], raw["figure_layout"]["evidence"])
        self.assertEqual(review["assignment_conflicts"], [conflict])
        self.assertEqual(question.read_a, before_raw)
        question.stem += " 请选出答案。"
        review = figure_policy.stored_or_derived_review(question)
        self.assertEqual(review["assignment_conflicts"], [conflict])

    def test_anonymous_conflict_requires_the_same_candidate_inventory(self):
        raw = reading()
        raw["figure_layout"]["conflicts"] = [{"slot": "A", "reason": "ambiguous_marker_geometry"}]
        review = figure_policy.stored_or_derived_review(question_stub(read_a=raw))
        self.assertEqual(review["assignment_conflicts"], raw["figure_layout"]["conflicts"])
        raw["figure_layout"]["candidate_keys"] = {"1": "0:1,2,3,4"}
        review = figure_policy.stored_or_derived_review(question_stub(read_a=raw))
        self.assertNotIn("assignment_conflicts", review)

    def test_get_discards_old_diagnostics_even_when_the_review_hash_matches(self):
        question = question_stub()
        current = figure_policy.stored_or_derived_review(question)
        question.figure_review = {**current, "assignment_conflicts": [{"reason": "old-stale"}]}
        review = figure_policy.stored_or_derived_review(question)
        self.assertNotIn("assignment_conflicts", review)

    def test_human_review_remains_authoritative(self):
        human = {"status": "ok", "source": "human", "reason": "已人工确认", "signals": []}
        question = question_stub(figure_review=human)
        self.assertIs(figure_policy.stored_or_derived_review(question), human)


class PersistedLayoutPolicyTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="labels.pdf", kind="pdf", sha256="l" * 64,
                                          source_path="data/labels/source.pdf")

    def make_question(self, **changes):
        stub = question_stub()
        fields = {key: deepcopy(getattr(stub, key)) for key in (
            "number", "stem", "options", "flags", "question_type", "state", "figure_review",
            "read_a", "read_b", "read_c", "figure_candidates", "figures",
        )}
        fields.update(changes)
        return Question.objects.create(paper=self.paper, **fields)

    def test_saved_upgrade_does_not_restore_the_raw_stem_role(self):
        question = self.make_question()
        original = deepcopy(question.read_a)
        figure_policy.persist_automatic_review_upgrades(Question.objects.filter(pk=question.pk))
        question.refresh_from_db()
        self.assertEqual([item["slot"] for item in question.figures], ["A"])
        self.assertEqual(question.read_a, original)
        self.assertEqual(question.figure_review["assignment_evidence"], original["figure_layout"]["evidence"])

    def test_saved_upgrade_restores_missing_crop_with_local_option_role(self):
        question = self.make_question(figures=[])
        figure_policy.persist_automatic_review_upgrades(Question.objects.filter(pk=question.pk))
        question.refresh_from_db()
        self.assertEqual([item["slot"] for item in question.figures], ["A"])

    def test_saved_upgrade_keeps_human_figures_and_review(self):
        human = {"status": "ok", "source": "human", "reason": "已人工确认"}
        figure = {"slot": "stem", "page_idx": 0, "bbox": CANDIDATE["bbox"], "source": "manual"}
        question = self.make_question(figure_review=human, figures=[figure])
        stats = figure_policy.persist_automatic_review_upgrades(Question.objects.filter(pk=question.pk))
        question.refresh_from_db()
        self.assertEqual(stats["human_skipped"], 1)
        self.assertEqual(question.figure_review, human)
        self.assertEqual(question.figures, [figure])
