"""Synthetic geometry counterexamples; no readers or application database."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from . import score_layout


def region(box, page=0):
    return {"page_idx": page, "bbox": list(box)}


def figure(box, slot="stem", page=0, **extra):
    return {**region(box, page), "slot": slot, **extra}


def card(number=1, *, group=0, kind="unknown", regions=None, figures=None):
    return {"group_sequence": group, "source_kind": kind, "number": number,
            "regions": regions if regions is not None else [region([0, 0, 10, 10])],
            "figures": figures if figures is not None else []}


def document(*cards):
    return {"cards": list(cards)}


class ScoreLayoutTests(unittest.TestCase):
    def test_overlapping_crop_boxes_and_pairwise_intersections_are_not_double_counted(self):
        boxes = [region([0, 0, 100, 100]), region([50, 0, 150, 100])]
        truth = document(card(regions=boxes))
        result = document(card(regions=[*boxes, boxes[0]]))
        body = score_layout.score(truth, result)["body_regions"]
        self.assertEqual(body["truth_area"], 15000)
        self.assertEqual(body["result_area"], 15000)
        self.assertEqual(body["intersection_area"], 15000)
        self.assertEqual(body["coverage"], 1)
        self.assertEqual(body["precision"], 1)

    def test_hole_between_boxes_is_not_filled_by_a_bounding_rectangle(self):
        truth = document(card(regions=[region([0, 0, 10, 10]), region([20, 0, 30, 10])]))
        body = score_layout.score(truth, document(card(regions=[region([0, 0, 30, 10])])))["body_regions"]
        self.assertEqual(body["coverage"], 1)
        self.assertAlmostEqual(body["precision"], 2 / 3)

    def test_body_assignment_is_scored_per_question_instead_of_one_page_union(self):
        first, second = [region([0, 0, 10, 10])], [region([0, 10, 10, 20])]
        truth = document(card(1, regions=first), card(2, regions=second))
        result = document(card(1, regions=second), card(2, regions=first))
        report = score_layout.score(truth, result)
        self.assertEqual(report["question_numbers"]["recall"], 1)
        self.assertEqual(report["body_regions"]["coverage"], 0)
        self.assertEqual(report["body_regions"]["precision"], 0)

    def test_missing_and_extra_cards_penalize_question_and_body_metrics(self):
        truth = document(card(1), card(2))
        result = document(card(1), card(3))
        report = score_layout.score(truth, result)
        questions = report["question_numbers"]
        self.assertEqual(questions["precision"], 0.5)
        self.assertEqual(questions["recall"], 0.5)
        self.assertEqual(questions["missing"][0]["number"], 2)
        self.assertEqual(questions["extra"][0]["number"], 3)
        self.assertEqual(questions["missing_count"], 1)
        self.assertEqual(questions["extra_count"], 1)
        self.assertEqual(report["body_regions"]["coverage"], 0.5)
        self.assertEqual(report["body_regions"]["precision"], 0.5)

    def test_repeated_numbers_in_different_groups_and_source_kinds_are_distinct(self):
        truth = document(card(group=0), card(group=1), card(group=1, kind="example"), card(group=None))
        result = deepcopy(truth)
        for index, value in enumerate(result["cards"]):
            value["group"] = f"dynamic-id-{index}"
        report = score_layout.score(truth, result)["question_numbers"]
        self.assertEqual(report["matched_count"], 4)
        self.assertEqual(report["precision"], 1)
        self.assertEqual(report["recall"], 1)
        self.assertEqual(report["duplicate_question_keys"], [])

    def test_duplicate_prediction_is_extra_and_first_card_is_used(self):
        truth = document(card())
        result = document(card(regions=[region([50, 0, 60, 10])]), card())
        report = score_layout.score(truth, result)
        self.assertEqual(report["question_numbers"]["precision"], 0.5)
        self.assertEqual(report["question_numbers"]["recall"], 1)
        self.assertEqual(report["question_numbers"]["duplicate_question_keys"][0]["count"], 2)
        self.assertEqual(report["question_numbers"]["duplicate_question_count"], 1)
        self.assertEqual(report["question_numbers"]["extra"][0]["result_index"], 1)
        self.assertEqual(report["body_regions"]["coverage"], 0)

    def test_correct_geometry_with_swapped_option_slots_is_incorrect_assignment(self):
        truth = document(card(figures=[figure([0, 0, 10, 10], "A"), figure([20, 0, 30, 10], "B")]))
        result = document(card(figures=[figure([0, 0, 10, 10], "B"), figure([20, 0, 30, 10], "A")]))
        metrics = score_layout.score(truth, result)["figure_relations"]
        self.assertEqual(metrics["matched_count"], 0)
        self.assertEqual(metrics["precision"], 0)
        self.assertEqual(metrics["recall"], 0)

    def test_shared_figure_is_a_separate_relation_for_each_question(self):
        shared = figure([0, 0, 10, 10], shared=True)
        truth = document(card(1, figures=[shared]), card(2, figures=[shared]))
        complete = score_layout.score(truth, deepcopy(truth))["figure_relations"]
        self.assertEqual(complete["matched_count"], 2)
        result = document(card(1, figures=[shared]), card(2))
        metrics = score_layout.score(truth, result)["figure_relations"]
        self.assertEqual(metrics["precision"], 1)
        self.assertEqual(metrics["recall"], 0.5)

    def test_multipart_cross_page_figure_requires_the_same_page_set(self):
        expected = figure([0, 0, 10, 10], parts=[region([0, 0, 10, 10], page=1)])
        truth = document(card(figures=[expected]))
        duplicate_parts = deepcopy(expected)
        duplicate_parts["parts"].append(region([0, 0, 10, 10], page=1))
        self.assertEqual(score_layout.score(truth, document(card(figures=[duplicate_parts])))
                         ["figure_relations"]["matched_count"], 1)
        incomplete = document(card(figures=[figure([0, 0, 10, 10])]))
        self.assertEqual(score_layout.score(truth, incomplete)["figure_relations"]["matched_count"], 0)

    def test_multipart_iou_uses_piece_union_instead_of_outer_bbox(self):
        expected = figure([0, 0, 10, 10], parts=[region([90, 0, 100, 10])])
        truth = document(card(figures=[expected]))
        result = document(card(figures=[figure([0, 0, 100, 10])]))
        self.assertEqual(score_layout.score(truth, result)["figure_relations"]["matched_count"], 0)

    def test_duplicate_prediction_cannot_match_one_figure_twice(self):
        expected = figure([0, 0, 10, 10])
        metrics = score_layout.score(document(card(figures=[expected])),
                                     document(card(figures=[expected, expected])))["figure_relations"]
        self.assertEqual(metrics["matched_count"], 1)
        self.assertEqual(metrics["precision"], 0.5)
        self.assertEqual(metrics["recall"], 1)

    def test_maximum_matching_recovers_a_pair_that_greedy_matching_would_lose(self):
        truth = document(card(figures=[figure([0, 0, 10, 10]), figure([6, 0, 16, 10])]))
        result = document(card(figures=[figure([3, 0, 13, 10]), figure([0, 0, 10, 10])]))
        report = score_layout.score(truth, result)
        self.assertEqual(report["figure_relations"]["matched_count"], 2)
        self.assertEqual(report["cards"][0]["figures"]["matches"][0]["result_index"], 1)

    def test_exact_iou_threshold_matches_but_the_wrong_page_does_not(self):
        truth = document(card(figures=[figure([0, 0, 10, 10])]))
        self.assertEqual(score_layout.score(truth, document(card(figures=[figure([0, 0, 5, 10])])))
                         ["figure_relations"]["matched_count"], 1)
        self.assertEqual(score_layout.score(truth, document(card(figures=[figure([0, 0, 10, 10], page=1)])))
                         ["figure_relations"]["matched_count"], 0)

    def test_empty_denominators_are_null_without_claiming_perfect_scores(self):
        empty = score_layout.score(document(), document())
        for category, names in [("question_numbers", ["precision", "recall"]),
                                ("body_regions", ["coverage", "precision"]),
                                ("figure_relations", ["precision", "recall"])]:
            for name in names:
                self.assertIsNone(empty[category][name])
        missed = score_layout.score(document(card()), document())
        self.assertEqual(missed["question_numbers"]["recall"], 0)
        self.assertIsNone(missed["question_numbers"]["precision"])
        self.assertEqual(missed["body_regions"]["coverage"], 0)
        self.assertIsNone(missed["body_regions"]["precision"])

    def test_invalid_coordinates_report_the_affected_input_field(self):
        bad_boxes = [[0, 0, float("nan"), 10], [0, 0, float("inf"), 10],
                     [0, 0, True, 10], [0, 0, "10", 10], [10, 0, 0, 10], [0, 0, 0, 10],
                     [0, 0, 10], [-1e308, -1e308, 1e308, 1e308]]
        for box in bad_boxes:
            with self.subTest(box=box), self.assertRaises(ValueError) as caught:
                score_layout.score(document(card(regions=[region(box)])), document())
            self.assertIn("truth.cards[0].regions[0].bbox", str(caught.exception))
        malformed = card(figures=[figure([0, 0, 10, 10], parts=[region([10, 0, 0, 10], page=1)])])
        with self.assertRaisesRegex(ValueError, r"figures\[0\]\.parts\[0\]\.bbox"):
            score_layout.score(document(malformed), document())

    def test_annotation_keys_and_page_indices_are_validated(self):
        with self.assertRaisesRegex(ValueError, "duplicate question keys"):
            score_layout.score(document(card(), card()), document())
        missing_group = card()
        missing_group.pop("group_sequence")
        with self.assertRaisesRegex(ValueError, "group_sequence: required"):
            score_layout.score(document(missing_group), document())
        for page in [True, -1, 1.5, None]:
            with self.subTest(page=page), self.assertRaisesRegex(ValueError, "page_idx"):
                score_layout.score(document(card(regions=[region([0, 0, 10, 10], page=page)])), document())

    def test_cli_writes_json_and_reports_validation_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            truth_path, result_path, output_path = root / "truth.json", root / "result.json", root / "report.json"
            truth_path.write_text(json.dumps(document(card())), encoding="utf-8-sig")
            result_path.write_text(json.dumps(document(card())), encoding="utf-8")
            args = ["--truth", str(truth_path), "--result", str(result_path)]
            self.assertEqual(score_layout.main([*args, "--output", str(output_path)]), 0)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["question_numbers"]["precision"], 1)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(score_layout.main(args), 0)
            self.assertEqual(json.loads(stdout.getvalue()), report)
            result_path.write_text(json.dumps(document(card(regions=[region([10, 0, 0, 10])]))), encoding="utf-8")
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertEqual(score_layout.main(args), 2)
            self.assertIn("result.cards[0].regions[0].bbox", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
