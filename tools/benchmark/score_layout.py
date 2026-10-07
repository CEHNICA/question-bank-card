"""Offline question identity, crop coverage and figure-assignment scoring.

This module uses only the Python standard library. It never invokes readers,
loads credentials, or changes the application database.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys

Rect = tuple[float, float, float, float]
Geometry = dict[int, list[Rect]]
Key = tuple[int | None, str, int]
FIGURE_IOU_THRESHOLD = 0.5


@dataclass
class Figure:
    slot: str
    geometry: Geometry


@dataclass
class Card:
    key: Key
    regions: Geometry
    figures: list[Figure]


def _integer(value, path: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"{path}: expected a nonnegative integer")
    return value


def _region(value, path: str) -> tuple[int, Rect]:
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object with page_idx and bbox")
    page = _integer(value.get("page_idx"), f"{path}.page_idx")
    raw = value.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError(f"{path}.bbox: expected [x0, y0, x1, y1]")
    box = []
    for index, coordinate in enumerate(raw):
        try:
            finite = type(coordinate) in {int, float} and math.isfinite(coordinate)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError(f"{path}.bbox[{index}]: expected a finite number")
        box.append(float(coordinate))
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"{path}.bbox: bounds are reversed or have zero area")
    area = (x1 - x0) * (y1 - y0)
    if not math.isfinite(area) or area <= 0:
        raise ValueError(f"{path}.bbox: area must be positive and finite")
    return page, tuple(box)


def _geometry(values, path: str) -> Geometry:
    if not isinstance(values, list):
        raise ValueError(f"{path}: expected a list")
    geometry: Geometry = {}
    for index, value in enumerate(values):
        page, box = _region(value, f"{path}[{index}]")
        geometry.setdefault(page, []).append(box)
    return geometry


def _cards(document, name: str) -> list[Card]:
    if not isinstance(document, dict) or not isinstance(document.get("cards"), list):
        raise ValueError(f"{name}.cards: expected a list in a JSON object")
    cards = []
    for index, value in enumerate(document["cards"]):
        path = f"{name}.cards[{index}]"
        if not isinstance(value, dict):
            raise ValueError(f"{path}: expected an object")
        if "group_sequence" not in value:
            raise ValueError(f"{path}.group_sequence: required; dynamic group IDs cannot be matched across runs")
        group = _integer(value["group_sequence"], f"{path}.group_sequence", nullable=True)
        number = _integer(value.get("number"), f"{path}.number")
        source_kind = value.get("source_kind")
        if not isinstance(source_kind, str) or not source_kind.strip():
            raise ValueError(f"{path}.source_kind: expected a nonempty string")
        regions = _geometry(value.get("regions"), f"{path}.regions")
        raw_figures = value.get("figures")
        if not isinstance(raw_figures, list):
            raise ValueError(f"{path}.figures: expected a list")
        figures = []
        for figure_index, figure in enumerate(raw_figures):
            figure_path = f"{path}.figures[{figure_index}]"
            if not isinstance(figure, dict):
                raise ValueError(f"{figure_path}: expected an object")
            slot = figure.get("slot")
            if not isinstance(slot, str) or not slot.strip():
                raise ValueError(f"{figure_path}.slot: expected a nonempty string")
            parts = figure.get("parts", [])
            if not isinstance(parts, list):
                raise ValueError(f"{figure_path}.parts: expected a list")
            page, box = _region(figure, figure_path)
            geometry = _geometry(parts, f"{figure_path}.parts")
            geometry.setdefault(page, []).append(box)
            figures.append(Figure(slot=slot, geometry=geometry))
        cards.append(Card(key=(group, source_kind, number), regions=regions, figures=figures))
    return cards


def _union_area(rectangles: list[Rect]) -> float:
    """Integrate disjoint vertical strips, merging overlapping y intervals."""
    xs = sorted({coordinate for box in rectangles for coordinate in (box[0], box[2])})
    strips = []
    for left, right in zip(xs, xs[1:]):
        intervals = sorted((box[1], box[3]) for box in rectangles if box[0] < right and box[2] > left)
        if not intervals:
            continue
        lower, upper = intervals[0]
        height = 0.0
        for next_lower, next_upper in intervals[1:]:
            if next_lower > upper:
                height += upper - lower
                lower, upper = next_lower, next_upper
            else:
                upper = max(upper, next_upper)
        height += upper - lower
        strips.append((right - left) * height)
    return math.fsum(strips)


def _area(geometry: Geometry) -> float:
    return math.fsum(_union_area(boxes) for boxes in geometry.values())


def _intersection_area(left: Geometry, right: Geometry) -> float:
    intersections: Geometry = {}
    for page in left.keys() & right.keys():
        for first in left[page]:
            for second in right[page]:
                box = (max(first[0], second[0]), max(first[1], second[1]),
                       min(first[2], second[2]), min(first[3], second[3]))
                if box[0] < box[2] and box[1] < box[3]:
                    intersections.setdefault(page, []).append(box)
    return _area(intersections)


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _figure_matches(truth: list[Figure], result: list[Figure]) -> list[dict]:
    """Maximum one-to-one matching; a broad candidate cannot claim two figures."""
    edges: list[list[tuple[int, float]]] = []
    for predicted in result:
        candidates = []
        for truth_index, expected in enumerate(truth):
            if predicted.slot != expected.slot or predicted.geometry.keys() != expected.geometry.keys():
                continue
            intersection = _intersection_area(expected.geometry, predicted.geometry)
            union = _area(expected.geometry) + _area(predicted.geometry) - intersection
            iou = intersection / union
            if iou >= FIGURE_IOU_THRESHOLD:
                candidates.append((truth_index, iou))
        edges.append(sorted(candidates, key=lambda edge: (-edge[1], edge[0])))
    owners: dict[int, int] = {}

    def assign(result_index: int, visited: set[int]) -> bool:
        for truth_index, _iou in edges[result_index]:
            if truth_index in visited:
                continue
            visited.add(truth_index)
            if truth_index not in owners or assign(owners[truth_index], visited):
                owners[truth_index] = result_index
                return True
        return False

    for result_index in range(len(result)):
        assign(result_index, set())
    return [{"truth_index": truth_index, "result_index": result_index,
             "iou": next(iou for index, iou in edges[result_index] if index == truth_index)}
            for truth_index, result_index in sorted(owners.items())]


def _key_dict(key: Key) -> dict:
    return {"group_sequence": key[0], "source_kind": key[1], "number": key[2]}


def score(truth_document: dict, result_document: dict) -> dict:
    truth, result = _cards(truth_document, "truth"), _cards(result_document, "result")
    truth_counts = Counter(card.key for card in truth)
    if any(count > 1 for count in truth_counts.values()):
        raise ValueError("truth.cards: duplicate question keys make the annotation ambiguous")
    result_counts = Counter(card.key for card in result)
    first_result = {}
    for index, card in enumerate(result):
        first_result.setdefault(card.key, index)
    truth_keys = set(truth_counts)
    selected = {key: index for key, index in first_result.items() if key in truth_keys}
    extra = [{"result_index": index, **_key_dict(card.key)} for index, card in enumerate(result)
             if selected.get(card.key) != index]
    body_truth = math.fsum(_area(card.regions) for card in truth)
    body_result = math.fsum(_area(card.regions) for card in result)
    body_intersections, figure_matched, per_card = [], 0, []
    for expected in truth:
        result_index = selected.get(expected.key)
        predicted = result[result_index] if result_index is not None else Card(expected.key, {}, [])
        truth_area, result_area = _area(expected.regions), _area(predicted.regions)
        intersection = _intersection_area(expected.regions, predicted.regions)
        body_intersections.append(intersection)
        matches = _figure_matches(expected.figures, predicted.figures)
        figure_matched += len(matches)
        matched_truth = {match["truth_index"] for match in matches}
        matched_result = {match["result_index"] for match in matches}
        per_card.append({
            **_key_dict(expected.key), "result_index": result_index,
            "body": {"truth_area": truth_area, "result_area": result_area, "intersection_area": intersection,
                     "coverage": _ratio(intersection, truth_area), "precision": _ratio(intersection, result_area)},
            "figures": {"truth_count": len(expected.figures), "result_count": len(predicted.figures),
                        "matched_count": len(matches), "matches": matches,
                        "missing_indices": [index for index in range(len(expected.figures)) if index not in matched_truth],
                        "extra_indices": [index for index in range(len(predicted.figures)) if index not in matched_result]},
        })
    body_intersection = math.fsum(body_intersections)
    figure_truth, figure_result = sum(len(card.figures) for card in truth), sum(len(card.figures) for card in result)
    matched = len(selected)
    return {
        "schema_version": 1, "metric_scope": "question_identity_and_geometry_only",
        "figure_iou_threshold": FIGURE_IOU_THRESHOLD,
        "question_numbers": {
            "truth_count": len(truth), "result_count": len(result), "matched_count": matched,
            "precision": _ratio(matched, len(result)), "recall": _ratio(matched, len(truth)),
            "missing_count": len(truth) - matched, "extra_count": len(extra),
            "duplicate_question_count": sum(count - 1 for count in result_counts.values()),
            "missing": [_key_dict(card.key) for card in truth if card.key not in selected], "extra": extra,
            "duplicate_question_keys": [{**_key_dict(key), "count": count}
                                        for key, count in result_counts.items() if count > 1],
        },
        "body_regions": {"truth_area": body_truth, "result_area": body_result, "intersection_area": body_intersection,
                         "coverage": _ratio(body_intersection, body_truth), "precision": _ratio(body_intersection, body_result)},
        "figure_relations": {"truth_count": figure_truth, "result_count": figure_result, "matched_count": figure_matched,
                             "precision": _ratio(figure_matched, figure_result), "recall": _ratio(figure_matched, figure_truth)},
        "cards": per_card,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, required=True, help="manually annotated JSON with cards")
    parser.add_argument("--result", type=Path, required=True, help="predicted JSON with cards")
    parser.add_argument("--output", type=Path, help="write report JSON here; default is stdout")
    args = parser.parse_args(argv)
    try:
        truth = json.loads(args.truth.read_text(encoding="utf-8-sig"))
        result = json.loads(args.result.read_text(encoding="utf-8-sig"))
        report = score(truth, result)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
    except (OSError, ValueError, OverflowError) as error:
        print(f"score_layout: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
