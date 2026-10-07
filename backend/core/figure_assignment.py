"""Conservative option-image ownership from text markers outside the images.

Coordinates are page-normalised (0--1000).  This module never reads files,
changes an input object, or asks a model to infer a missing option label.
"""

from __future__ import annotations

import math
import re
import unicodedata


OPTION_SLOTS = frozenset("ABCDE")
CHOICE_KINDS = frozenset({"single_choice", "multiple_choice"})
TEXT_TYPES = frozenset({"", "text", "paragraph", "list", "list_item", "image_caption"})
MAX_CANDIDATES = 32
MAX_BLOCKS = 1024
MAX_MARKERS = 20
AMBIGUITY_MARGIN = 12.0
_MARKER = re.compile(r"^(?:\(([A-E])\)|([A-E])\s*[.、:)])(?:\s*|$)")
_EMBEDDED_MARKER = re.compile(r"(?:^|\s)(?:\([A-E]\)|[A-E]\s*[.、:)])")


def _page(item: dict) -> int | None:
    page = item.get("page_idx")
    return page if type(page) is int and page >= 0 else None


def _box(item: dict) -> tuple[float, float, float, float] | None:
    box = item.get("bbox")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in box):
        return None
    x0, y0, x1, y1 = map(float, box)
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000):
        return None
    return x0, y0, x1, y1


def _manual(item: dict) -> bool:
    return item.get("source") in {"manual", "human"} or item.get("manual") is True


def _intersection(a: tuple, b: tuple) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) \
        * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _marker(block: dict) -> dict | None:
    if (block.get("type", "") not in TEXT_TYPES or _manual(block)
            or block.get("handwritten") is True or block.get("is_handwritten") is True):
        return None
    page, box = _page(block), _box(block)
    if page is None or box is None or box[3] - box[1] > 65:
        return None
    text = unicodedata.normalize("NFKC", str(block.get("text") or "")).strip()
    if not text or len(text) > 160 or "\n" in text:
        return None
    explicit = _MARKER.match(text)
    if explicit:
        slot = explicit.group(1) or explicit.group(2)
        # A box containing several option labels has no exact per-label bbox.
        if _EMBEDDED_MARKER.search(text[explicit.end():]):
            return None
    elif text in OPTION_SLOTS:
        slot = text
    else:
        return None
    return {"slot": slot, "page_idx": page, "bbox": box,
            "seq": block.get("seq"), "explicit": explicit is not None}


def _distance(marker: dict, candidate: dict) -> float | None:
    """Accept only a nearby label to the left, above, or below its image."""
    if marker["page_idx"] != candidate["page_idx"]:
        return None
    mx0, my0, mx1, my1 = marker["bbox"]
    x0, y0, x1, y1 = candidate["bbox"]
    mcx, mcy = (mx0 + mx1) / 2, (my0 + my1) / 2
    costs = []
    left_gap = x0 - mx1
    center_y = (y0 + y1) / 2
    vertical_tolerance = max(18.0, (y1 - y0) * 0.25)
    top_aligned = y0 - 20 <= mcy <= y0 + min(60, (y1 - y0) / 2)
    center_aligned = abs(mcy - center_y) <= vertical_tolerance
    if -2 <= left_gap <= 50 and (top_aligned or center_aligned):
        vertical_offset = min(
            abs(mcy - y0) if top_aligned else float("inf"),
            abs(mcy - center_y) if center_aligned else float("inf"),
        )
        costs.append(max(0.0, left_gap) + 0.5 * vertical_offset)
    horizontal_overlap = max(0.0, min(mx1, x1) - max(mx0, x0))
    if horizontal_overlap >= 0.6 * min(mx1 - mx0, x1 - x0):
        above_gap, below_gap = y0 - my1, my0 - y1
        # A caption centred under a figure is common; a text option may sit above it.
        if 0 <= above_gap <= 35:
            costs.append(above_gap + 0.1 * abs(mcx - (x0 + x1) / 2))
        if 0 <= below_gap <= 30:
            costs.append(below_gap + 0.1 * abs(mcx - (x0 + x1) / 2))
    return min(costs) if costs else None


def option_evidence(
    *, stem: str, options: dict, kind: str, candidates: list[dict],
    assignments: dict, blocks: list[dict] | None = None,
) -> dict:
    """Return assignments, marker evidence, and unresolved diagnostic conflicts.

    ``blocks`` must be the text blocks of this question's source ranges.  An
    existing option or exclusion is never replaced.  Evidence records whether
    its proposed role was applied, so callers can retain a visible conflict.
    ``stem`` and ``options`` are accepted for the pipeline's stable interface;
    text mentioning A/B is not used to invent a spatial option marker.
    """
    updated = dict(assignments or {})
    result = {"assignments": updated, "evidence": {}, "conflicts": []}
    if (kind not in CHOICE_KINDS or not isinstance(candidates, list)
            or not isinstance(blocks, list) or not blocks
            or len(candidates) > MAX_CANDIDATES or len(blocks) > MAX_BLOCKS):
        return result

    valid = []
    labels = set()
    duplicate_labels = set()
    for item in candidates:
        if not isinstance(item, dict) or _manual(item):
            continue
        page, box = _page(item), _box(item)
        label = item.get("label")
        if page is None or box is None or label is None:
            continue
        label = str(label)
        if not label:
            continue
        if label in labels:
            duplicate_labels.add(label)
        labels.add(label)
        valid.append({"label": label, "page_idx": page, "bbox": box})
    valid = [item for item in valid if item["label"] not in duplicate_labels]
    if not valid:
        return result

    markers = []
    seen_markers = set()
    # Include manual images in the exclusion geometry: an image's internal A
    # must not become a marker merely because that image was already hand-picked.
    image_boxes = [(_page(item), _box(item)) for item in candidates if isinstance(item, dict)]
    for block in blocks:
        if not isinstance(block, dict) or (marker := _marker(block)) is None:
            continue
        if any(page == marker["page_idx"] and box is not None
               and _intersection(marker["bbox"], box) > 0
               for page, box in image_boxes):
            continue
        key = (marker["slot"], marker["page_idx"], marker["bbox"])
        if key not in seen_markers:
            markers.append(marker)
            seen_markers.add(key)
    if len(markers) > MAX_MARKERS:
        return result

    distinct_slots = {marker["slot"] for marker in markers}
    markers = [marker for marker in markers if marker["explicit"] or len(distinct_slots) >= 2]
    slot_counts = {slot: sum(marker["slot"] == slot for marker in markers) for slot in OPTION_SLOTS}
    proposals = {}
    for marker in markers:
        # Repeated A labels can belong to two different questions.  Their
        # source ranges must be narrowed by the caller instead of guessing here.
        if slot_counts[marker["slot"]] != 1:
            result["conflicts"].append({"slot": marker["slot"], "reason": "duplicate_option_marker"})
            continue
        ranked = sorted(
            (distance, candidate["label"])
            for candidate in valid if (distance := _distance(marker, candidate)) is not None
        )
        if not ranked:
            continue
        if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < AMBIGUITY_MARGIN:
            result["conflicts"].append({"slot": marker["slot"], "reason": "ambiguous_marker_geometry"})
            continue
        distance, label = ranked[0]
        proposals.setdefault(label, []).append((marker, distance))

    for label, proposed in proposals.items():
        if len(proposed) != 1:
            result["conflicts"].append({"candidate_label": label, "reason": "multiple_option_markers"})
            continue
        marker, distance = proposed[0]
        slot, current = marker["slot"], updated.get(label)
        applied = current in {None, "", "stem", slot}
        reason = "printed_option_marker_disagrees" if current in OPTION_SLOTS and current != slot \
            else "protected_assignment"
        result["evidence"][label] = {
            "slot": slot, "page_idx": marker["page_idx"], "marker_bbox": list(marker["bbox"]),
            "marker_seq": marker["seq"], "source": "printed_option_marker",
            "distance": round(distance, 2), "applied": applied,
        }
        if applied:
            updated[label] = slot
        else:
            result["conflicts"].append({"candidate_label": label, "current_role": current,
                                        "proposed_role": slot, "reason": reason})
    return result


def option_assignments(
    *, stem: str, options: dict, kind: str, candidates: list[dict],
    assignments: dict, blocks: list[dict] | None = None,
) -> dict[str, str]:
    """Return a fresh role map, applying only unambiguous external option labels."""
    return option_evidence(stem=stem, options=options, kind=kind, candidates=candidates,
                           assignments=assignments, blocks=blocks)["assignments"]
