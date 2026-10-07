"""Local, page-aware inventory of figure use across question cards."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict


_SHARED = re.compile(
    r"^第?\s*(?P<numbers>\d{1,3}(?:\s*[-—–~～至、,，和及]\s*\d{1,3})+)"
    r"\s*题\s*(?:共用|共同使用|使用同一)[^。\n]{0,30}?(?:图|图形|示意图)"
)


def frame(item: dict) -> tuple[int, tuple[float, ...]] | None:
    if not isinstance(item, dict) or type(item.get("page_idx")) is not int:
        return None
    box = item.get("bbox")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    if any(type(value) not in {int, float} or not math.isfinite(value) for value in box):
        return None
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000) or item["page_idx"] < 0:
        return None
    return item["page_idx"], tuple(round(float(value), 1) for value in box)


def frame_key(item: dict) -> str | None:
    value = frame(item)
    if value is None:
        return None
    page, box = value
    return f"{page}:" + ",".join(f"{coordinate:g}" for coordinate in box)


def figure_frames(item: dict) -> list[dict]:
    if not isinstance(item, dict):
        return []
    pieces = [item, *(item.get("parts") or [])]
    return [{"page_idx": piece["page_idx"], "bbox": list(piece["bbox"])}
            for piece in pieces if frame(piece) is not None]


def shared_caption(candidate: dict, blocks: list[dict]) -> dict | None:
    """Accept only an explicit nearby printed declaration, never a model guess."""
    location = frame(candidate)
    if location is None:
        return None
    page, (x0, y0, x1, y1) = location
    found = []
    for block in blocks:
        other = frame(block)
        if other is None or other[0] != page or block.get("type") in {"image", "table", "chart"}:
            continue
        if block.get("handwritten") is True or block.get("source") == "handwritten":
            continue
        text = unicodedata.normalize("NFKC", str(block.get("text") or "")).strip(" \t\n()[]【】")
        match = _SHARED.match(text)
        if match is None:
            continue
        bx0, by0, bx1, by1 = other[1]
        overlap = min(x1, bx1) - max(x0, bx0)
        # A declaration must be outside the diagram and next to its edge.
        gap = y0 - by1 if by1 <= y0 else by0 - y1 if by0 >= y1 else -1
        if overlap < min(x1 - x0, bx1 - bx0) * .5 or not 0 <= gap <= 45:
            continue
        numbers_text = match.group("numbers")
        numbers = []
        valid = True
        for part in re.split(r"[、,，和及]", numbers_text):
            endpoints = list(map(int, re.findall(r"\d+", part)))
            if len(endpoints) == 2:
                if not 1 <= endpoints[1] - endpoints[0] <= 19:
                    valid = False
                    break
                numbers.extend(range(endpoints[0], endpoints[1] + 1))
            elif len(endpoints) == 1:
                numbers.extend(endpoints)
            else:
                valid = False
                break
        if not valid:
            continue
        if len(numbers) < 2 or len(numbers) > 20 or min(numbers) < 1 or len(set(numbers)) != len(numbers):
            continue
        found.append({"numbers": sorted(numbers), "text": text,
                      "page_idx": page, "bbox": list(other[1]), "seq": block.get("seq")})
    if not found or len({tuple(item["numbers"]) for item in found}) != 1:
        return None
    return found[0]


def build_ledger(cards: list[dict], blocks: list[dict] | None = None) -> dict:
    """Record individual source pieces, so a joined figure cannot hide a clash."""
    by_page = defaultdict(list)
    for block in blocks or []:
        if frame(block) is not None:
            by_page[block["page_idx"]].append(block)
    inventory = {}
    for card in cards:
        for candidate in card.get("candidates") or []:
            key = frame_key(candidate)
            if key is not None:
                inventory.setdefault(key, {"key": key, **figure_frames(candidate)[0], "claims": []})
        for figure in card.get("figures") or []:
            for piece in figure_frames(figure):
                key = frame_key(piece)
                entry = inventory.setdefault(key, {"key": key, **piece, "claims": []})
                claim = {"question_id": card["id"], "group_id": card.get("group_id"),
                         "number": card.get("number"), "slot": figure.get("slot", "stem"),
                         "source": figure.get("source", "auto"), "protected": bool(card.get("protected"))}
                if claim not in entry["claims"]:
                    entry["claims"].append(claim)
    declarations = {}
    declaration_targets = defaultdict(set)
    for entry in inventory.values():
        declaration = shared_caption(entry, by_page[entry["page_idx"]])
        if declaration is not None:
            declarations[entry["key"]] = declaration
            declaration_targets[frame_key(declaration)].add(entry["key"])
    conflicts = defaultdict(list)
    for entry in inventory.values():
        claims = entry["claims"]
        declaration = declarations.get(entry["key"])
        if declaration and len(declaration_targets[frame_key(declaration)]) > 1:
            entry["ambiguous_shared_caption"] = declaration
            declaration = None
        if declaration:
            entry["shared_caption"] = declaration
        question_ids = {claim["question_id"] for claim in claims}
        shared = (len(question_ids) > 1 and declaration is not None
                  and len({claim["group_id"] for claim in claims}) == 1
                  and all(claim["number"] in declaration["numbers"] and claim["slot"] == "stem"
                          for claim in claims))
        slot_conflict = any(len({claim["slot"] for claim in claims if claim["question_id"] == pk}) > 1
                            for pk in question_ids)
        if slot_conflict or (len(question_ids) > 1 and not shared):
            entry["status"] = "conflict"
            for pk in question_ids:
                conflicts[str(pk)].append(entry["key"])
        else:
            entry["status"] = "shared" if shared else "assigned" if claims else "unassigned"
    return {"schema": 1, "figures": sorted(inventory.values(), key=lambda item: item["key"]),
            "conflicts": dict(conflicts)}
