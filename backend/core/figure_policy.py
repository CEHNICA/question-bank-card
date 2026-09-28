"""Zero-latency figure safeguards built from data the pipeline already has.

This module must stay local and deterministic: it never renders pages and never
calls MinerU or a vision model.  It combines the recognised question text with
the candidate assignments returned by the existing first reader.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
import unicodedata


BLOCKED_MISSING = "blocked_missing"
AUTO_EXCLUDED = "auto_excluded"
CONFLICT = "conflict"
CONFIRMED_NO_FIGURE = "confirmed_no_figure"
OK = "ok"
VALID_STATUSES = {BLOCKED_MISSING, AUTO_EXCLUDED, CONFLICT, CONFIRMED_NO_FIGURE, OK}
BLOCKING_STATUSES = {BLOCKED_MISSING, CONFLICT}

# Increment this whenever a deterministic cue or decision rule changes.  The
# value lives inside the JSON review so existing databases do not need a schema
# migration: old automatic decisions can be recognised and rebuilt from the
# question data already on disk.
FIGURE_REVIEW_POLICY_VERSION = 6

FLAG_NO_FIGURE = "题干说有图，但还没有配图，请点“配图”框出"
FLAG_UNFOUND_FIGURE = "原卷可能有图没有被找到，请点“配图”框出"
FLAG_UNCUED_FIGURE = "题目文字没有发现图像提示词，但识读判断有印刷配图，请确认配图或确认本题确实无图"
LEGACY_FLAG_NO_FIGURE = "题干说“如图”，但还没有配图，请点“配图”框出"


# Only phrases that refer to an existing visual are included.  Bare words such
# as “图象/graph” are deliberately excluded because “画出函数图象 / sketch a
# graph” asks the student to create one and does not prove the paper supplies it.
_CHINESE_CUE = re.compile(
    r"(?:"
    r"如\s*(?:下|上|左|右)?\s*图(?:\s*[甲乙丙丁①②③④⑤⑥⑦⑧⑨1-9A-Za-z])?"
    r"(?:\s*(?:所示|显示|给出|为|是)|(?=$|[\s，,。:：；;（(]|可知|可得))|"
    r"(?:下|上|左|右)\s*图|"
    r"(?:下列|以下|所给)\s*[^，,。:：；;\n]{0,12}?(?:图像|图象|图形)(?:中)?"
    r"(?=$|[\s，,。:：；;（(与和])|"
    r"(?:下列|以下|所给)\s*(?:的\s*)?(?:图像|图象|图形|图示|图案|示意图|简图)|"
    r"(?:下列|以下|所给)\s*[^，,。:：；;\n]{0,12}?(?:曲线|图线)(?:中)?(?=$|[，,。:：；;（(])|"
    r"图\s*(?:中|上|下|左|右|所示)|"
    r"(?:见|看|读)\s*(?:下|上|左|右)?\s*图"
    r"(?=$|[\s，,。:：；;（(①②③④⑤⑥⑦⑧⑨1-9]|所示|中|可知|可得)|"
    r"观察\s*(?:下|上|左|右)?\s*图|"
    r"(?:根据|依据|结合)\s*(?:下|上|左|右)?\s*图(?:\s*(?:中|所示))?"
    r"(?=$|[\s，,。:：；;（(]|可知|可得|显示)|"
    r"(?:由|从)\s*(?:下|上|左|右)?\s*图(?:\s*中)?\s*(?:可知|可得|看出|得出)|"
    r"(?:参照|参考)\s*(?:下|上|左|右|左侧|右侧)?\s*(?:的\s*)?"
    r"[^，,。:：；;\n]{0,24}?(?:示意图|简图|统计图|折线图|柱状图|扇形图|电路图|"
    r"结构图|装置图|流程图|函数图(?:像|象)|坐标图|路线图|地图|图)"
    r"(?=$|[\s，,。:：；;（(])|"
    r"(?<![\u4e00-\u9fffA-Za-z0-9])图\s*为|"
    r"图\s*(?:[（(]\s*)?[①②③④⑤⑥⑦⑧⑨一二三四五六七八九1-9][A-Za-z]?\s*[)）]?|"
    r"(?<!不)如\s*表(?!格)(?:\s*所示)?(?=$|[\s，,。:：；;（(])|"
    r"(?:下|上|左|右)\s*表|见\s*(?:下|上)?\s*表"
    r"(?=$|[\s，,。:：；;（(1-9]|所示|中|可知|可得)|表\s*(?:中|所示)|"
    r"(?:对应值|函数值|取值|数值)\s*表"
    r"(?=$|[\s，,。:：；;（(]|中|内|如下|所示)|"
    r"(?<![\u4e00-\u9fffA-Za-z0-9])表\s*\d+(?:\s*[.．·\-－—]\s*\d+){1,3}|"
    r"(?:填\s*(?:写\s*)?表|填写\s*(?:下|上)?表)|"
    r"(?:上述|前述)\s*(?:两|三|四|各|若干)?\s*(?:个|组|幅|张)?\s*表|"
    r"(?:下列|以下)\s*(?:图形|图示|图案|示意图|简图)|"
    r"(?:图像|图象|图形)\s*(?:大致|可能)?\s*是\s*[（(]|"
    r"(?:[（(]\s*[一二三四五六七八九1-9]\s*[)）]\s*){2,}\s*"
    r"分别\s*(?:为|是)[^，,。:：；;\n]{0,48}?(?:图像|图象|图形)|"
    r"(?:示意图|简图|统计图|折线图|柱状图|扇形图|电路图|结构图|装置图|流程图|"
    r"函数图(?:像|象)|坐标图|路线图|地图)\s*(?:中|上|下|所示|显示|如下)"
    r")"
)


# Requests to create a visual are not evidence that the source paper already
# contains one.  This matcher is intentionally separate from ``_CHINESE_CUE``:
# a question may both provide one figure and ask the student to draw another,
# in which case the supplied-figure cue still wins.
_STUDENT_DRAWING_REQUEST = re.compile(
    r"(?:"
    r"(?:请\s*)?(?:画|作|绘|描)\s*(?:出|制|作)?[^。；;\n]{0,160}?"
    r"(?:图像|图象|图形|示意图|简图|统计图|折线图|柱状图|扇形图|函数图|曲线)|"
    r"(?:尺规\s*)?作图(?:题)?|"
    r"\b(?:draw|sketch|plot|construct)\b[^.?!\n]{0,48}\b"
    r"(?:figure|diagram|image|graph|chart|curve)\b"
    r")",
    re.IGNORECASE,
)

# Some exercises introduce a printed data table only as “下列数据：”.  That
# phrase alone is not enough to demand an image—the values could be ordinary
# text—so it is accepted only when a concrete crop is already bound.
_BOUND_VISUAL_CUE = re.compile(
    r"(?:下列|以下|如下|所给)\s*(?:的\s*)?(?:两|三|若干)?\s*(?:组\s*)?数据\s*[:：]"
)

_ENGLISH_CUE = re.compile(
    r"(?:"
    r"\b(?:the\s+)?(?:following|above|below|accompanying)\s+"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b|"
    r"\b(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\s+"
    r"(?:above|below|shows?|illustrates?|depicts?|is\s+shown)\b|"
    r"\b(?:as\s+)?(?:shown|illustrated|depicted)\s+(?:above|below)\b|"
    r"\bas\s+shown(?=\s*[,.:;]|$)|"
    r"\b(?:as\s+)?(?:shown|illustrated|depicted)\s+(?:in|on)\s+"
    r"(?:the\s+|this\s+|following\s+)?"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b"
    r"(?!\s+of\s+(?:speech|merit)\b)|"
    r"\bin\s+(?:the\s+|this\s+|the\s+following\s+|the\s+above\s+|the\s+below\s+)"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b"
    r"(?!\s+of\s+(?:speech|merit)\b)|"
    r"\brefer(?:ring)?\s+to\s+(?:the\s+|this\s+|following\s+)?"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b"
    r"(?!\s+(?:theory|method)\b|\s+of\s+(?:speech|merit)\b)|"
    r"\baccording\s+to\s+(?:the\s+|this\s+|following\s+)?"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b"
    r"(?=\s*(?:above|below|provided|shown|[,.:;]|$))|"
    r"\b(?:using|use)\s+(?:the\s+|this\s+|following\s+)?"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b"
    r"(?=\s*(?:above|below|provided|shown|to\b|[,.:;]|$))|"
    r"\b(?:fig\.?|figure)\s*\d+[A-Za-z]?\b|"
    r"\bwhich\s+(?:of\s+the\s+following\s+)?(?:figure|diagram|image|picture|graph|chart|circuit)s?\b|"
    r"\b(?:xy[- ]plane|coordinate\s+plane|number\s+line|grid|circuit)\s+(?:above|below)\b|"
    r"\b(?:not\s+drawn\s+to\s+scale|not\s+accurately\s+drawn|diagram\s+not\s+accurately\s+drawn|"
    r"(?:the\s+)?(?:drawing|figure|diagram)\s+is\s+not\s+to\s+scale)\b"
    r")",
    re.IGNORECASE,
)

# Backwards-compatible public matcher used by older tests and callers.
MENTIONS_FIGURE = re.compile(
    rf"(?:{_CHINESE_CUE.pattern}|{_ENGLISH_CUE.pattern})",
    re.IGNORECASE,
)


def _question_text(stem: str, options: dict | None = None) -> str:
    values = [str(stem or "")]
    if isinstance(options, dict):
        values.extend(str(options.get(key, "") or "") for key in ("A", "B", "C", "D"))
    return unicodedata.normalize("NFKC", "\n".join(values))


def cue_matches(stem: str, options: dict | None = None) -> list[str]:
    """Return a small, stable list of phrases proving the text refers to a visual."""
    text = _question_text(stem, options)
    # Remove the part that asks the student to create a visual before looking
    # for evidence that the paper already supplies one.  For example,
    # ``画出下列函数的图象`` contains the lexical shape ``下列……图象`` but
    # plainly does not refer to a printed figure.  A real supplied cue outside
    # the drawing command is retained: ``根据下图画出……`` becomes ``根据下图``.
    text = _STUDENT_DRAWING_REQUEST.sub("", text)
    matches = [match.group(0).strip() for pattern in (_CHINESE_CUE, _ENGLISH_CUE)
               for match in pattern.finditer(text)]
    result: list[str] = []
    for value in matches:
        if value and value.casefold() not in {item.casefold() for item in result}:
            result.append(value)
        if len(result) >= 12:
            break
    return result


def has_figure_cue(stem: str, options: dict | None = None) -> bool:
    return bool(cue_matches(stem, options))


def asks_student_to_draw(stem: str, options: dict | None = None) -> bool:
    """Return whether the task asks the student to create the visual.

    This never cancels an explicit reference such as ``根据下图画出……``.  The
    caller uses it only when no supplied-visual cue was found.
    """

    return bool(_STUDENT_DRAWING_REQUEST.search(_question_text(stem, options)))


def _has_textbook_section_badge_geometry(item: dict) -> bool:
    """Return whether a saved crop has the audited left-margin badge shape."""

    if not isinstance(item, dict):
        return False
    bbox = item.get("bbox")
    if (not isinstance(bbox, list) or len(bbox) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in bbox)):
        return False
    x0, _y0, x1, _y1 = map(float, bbox)
    width, height = x1 - x0, float(bbox[3]) - float(bbox[1])
    return (
        88 <= x0 <= 115
        and 124 <= x1 <= 151
        and 30 <= width <= 42
        and 20 <= height <= 32
    )


def is_likely_textbook_section_badge(candidate: dict) -> bool:
    """Recognise the tiny left-margin exercise-section ornaments.

    The audited textbook repeats these raster badges at almost exactly the
    same page-normalised position and size (roughly 34--39 by 22--29).  Keep
    the limits deliberately narrow: a small mathematical diagram elsewhere on
    the page is not decoration merely because it is small.
    """

    if not isinstance(candidate, dict) or candidate.get("recovered_input") is True:
        return False
    if candidate.get("source") == "manual" or not isinstance(candidate.get("seq"), int):
        return False
    return _has_textbook_section_badge_geometry(candidate)


def without_automatic_textbook_badges(figures: list[dict]) -> list[dict]:
    """Drop only automatic copies of the repeated textbook section badge.

    Manual crops are never touched.  This helper is deliberately geometry-
    specific and is used only on explicit write paths or on a newly produced
    reading, so an ordinary GET cannot silently edit the database.
    """

    return [
        figure for figure in (figures or [])
        if not (
            isinstance(figure, dict)
            and figure.get("source") in {"auto", "other"}
            and figure.get("slot") not in {"A", "B", "C", "D"}
            and _has_textbook_section_badge_geometry(figure)
        )
    ]


def _automatic_decoration_labels(
    *, candidates: list[dict], assignments: dict[str, str], kind: str, options: dict | None,
) -> set[str]:
    """Return only unassigned badges that are safe to auto-exclude.

    Image-choice questions with no textual options are the risky case: an
    unclassified tiny crop may really be option A.  Preserve every such crop
    unless another candidate has already been assigned to an option, which is
    the characteristic layout of the audited four-large-options-plus-one-badge
    pages.  Any candidate already assigned by the reader is also preserved.
    """

    choice_kind = kind in {"single_choice", "multiple_choice"}
    has_text_options = bool(options)
    has_assigned_option = any(role in {"A", "B", "C", "D"} for role in assignments.values())
    labels: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("label") is None:
            continue
        label = str(candidate["label"])
        assigned_role = assignments.get(label)
        if assigned_role in {"A", "B", "C", "D"}:
            # A genuinely image-based choice may itself be small.  Never
            # override a reader's explicit option assignment.
            continue
        if assigned_role and assigned_role not in {"stem", "none", "decoration"}:
            continue
        if choice_kind and not has_text_options and not has_assigned_option:
            continue
        if is_likely_textbook_section_badge(candidate):
            labels.add(label)
    return labels


def resolve_automatic_figure_assignments(
    *, stem: str, options: dict | None, kind: str,
    candidates: list[dict], assignments: dict | None,
) -> dict[str, str]:
    """Combine saved reader labels with stronger local layout evidence."""

    resolved = {str(label): str(role) for label, role in (assignments or {}).items()}
    decorations = _automatic_decoration_labels(
        candidates=candidates,
        assignments=resolved,
        kind=str(kind or "unknown"),
        options=options or {},
    )
    resolved.update({label: "decoration" for label in decorations})

    if has_figure_cue(stem, options):
        for candidate in candidates:
            if not isinstance(candidate, dict) or candidate.get("recovered_input") is not True:
                continue
            label = candidate.get("label")
            if label is not None and str(label) not in decorations:
                resolved[str(label)] = "stem"
        bound_roles = {"stem", "A", "B", "C", "D"}
        if str(kind or "unknown") not in {"single_choice", "multiple_choice"} \
                and not any(role in bound_roles for role in resolved.values()):
            # If a free-response stem explicitly says a supplied figure exists
            # and exactly one ordinary candidate remains unclassified, that
            # crop is stronger evidence than the reader's omission.  Explicit
            # ``none``/foreign labels and textbook badges are not reconsidered.
            plausible = [
                candidate for candidate in candidates
                if isinstance(candidate, dict)
                and candidate.get("label") is not None
                and str(candidate.get("label")) not in resolved
                and not is_likely_textbook_section_badge(candidate)
            ]
            if len(plausible) == 1:
                resolved[str(plausible[0]["label"])] = "stem"
    return resolved


def repaired_automatic_figures(
    *, figures: list[dict], candidates: list[dict], assignments: dict[str, str],
) -> list[dict]:
    """Remove badges and restore locally resolved automatic candidate crops."""

    repaired = without_automatic_textbook_badges(figures)
    existing = {
        (item.get("slot"), candidate_key(item))
        for item in repaired if isinstance(item, dict)
    }
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        label = str(candidate.get("label"))
        role = assignments.get(label)
        if role not in {"stem", "A", "B", "C", "D"}:
            continue
        key = candidate_key(candidate)
        if key is None or (role, key) in existing:
            continue
        repaired.append({
            "slot": role,
            "page_idx": candidate["page_idx"],
            "bbox": candidate["bbox"],
            "source": "auto",
        })
        existing.add((role, key))
    return repaired


def candidate_key(item: dict) -> str | None:
    """Return the stable page/bbox identity shared with the figure editor."""

    if not isinstance(item, dict) or not isinstance(item.get("page_idx"), int):
        return None
    bbox = item.get("bbox")
    if (not isinstance(bbox, list) or len(bbox) != 4
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in bbox)):
        return None
    x0, y0, x1, y1 = (round(float(value), 1) for value in bbox)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(1000, x1), min(1000, y1)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return None

    def shown(number: float) -> str:
        return f"{number:.1f}".rstrip("0").rstrip(".")

    return f"{item['page_idx']}:" + ",".join(shown(value) for value in (x0, y0, x1, y1))


def missing_choice_figure_slots(
    *, kind: str, options: dict | None, figures: list[dict], readings: list[dict] | None = None,
) -> set[str]:
    """Return image-option slots that still need a bound crop.

    A reader can occasionally label an ordinary graph question as multiple
    choice even though a second reader calls it free response.  With no option
    text and only a stem figure, treating that disagreement as four missing
    image options creates a false missing-figure warning.  Suppress only that
    narrow conflict; a single reader, reader consensus, or any bound option
    figure still keeps the existing A-D completeness safeguard.
    """
    option_slots = {"A", "B", "C", "D"}
    if kind not in {"single_choice", "multiple_choice"} or options:
        return set()
    bound = {
        figure.get("slot") for figure in figures
        if isinstance(figure, dict) and figure.get("slot") in option_slots
    }
    has_stem_figure = any(
        isinstance(figure, dict) and figure.get("slot") == "stem" for figure in figures
    )
    declared = {
        str(reading.get("type") or "unknown")
        for reading in (readings or []) if isinstance(reading, dict)
    }
    choice_types = {"single_choice", "multiple_choice"}
    non_choice_types = {"fill_blank", "free_response"}
    if has_stem_figure and not bound and declared & choice_types and declared & non_choice_types:
        return set()
    return option_slots - bound


def _automatic_input_hash(
    *,
    stem: str,
    options: dict | None,
    candidate_labels: set[str],
    assignments: dict[str, str],
    figures: list[dict],
    reader_missing: bool,
    described_slots: set[str] | None,
) -> str:
    """Identify the saved inputs behind an automatic decision.

    This is not a security hash.  It lets a later text/figure change invalidate
    a cached decision even when a caller forgot to clear ``figure_review``.
    """
    payload = {
        "stem": unicodedata.normalize("NFKC", str(stem or "")),
        "options": options if isinstance(options, dict) else {},
        "candidate_labels": sorted(str(value) for value in candidate_labels),
        "assignments": dict(sorted((str(key), str(value)) for key, value in assignments.items())),
        "figures": figures,
        "reader_missing": bool(reader_missing),
        "described_slots": sorted(str(value) for value in (described_slots or set())),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _versioned_automatic_review(review: dict, *, input_hash: str) -> dict:
    return {
        **review,
        "source": "automatic",
        "policy_version": FIGURE_REVIEW_POLICY_VERSION,
        "input_hash": input_hash,
    }


def automatic_review(
    *,
    stem: str,
    options: dict | None,
    candidate_labels: set[str],
    assignments: dict[str, str],
    figures: list[dict],
    reader_missing: bool = False,
    described_slots: set[str] | None = None,
) -> dict:
    """Combine existing text/reader results without doing any additional recognition."""
    input_hash = _automatic_input_hash(
        stem=stem,
        options=options,
        candidate_labels=candidate_labels,
        assignments=assignments,
        figures=figures,
        reader_missing=reader_missing,
        described_slots=described_slots,
    )
    cues = cue_matches(stem, options)
    if figures:
        text = _question_text(stem, options)
        for match in _BOUND_VISUAL_CUE.finditer(text):
            value = match.group(0).strip()
            if value and value.casefold() not in {item.casefold() for item in cues}:
                cues.append(value)
    bound_slots = {
        figure.get("slot") for figure in figures
        if figure.get("slot") == "stem" or figure.get("slot") in {"A", "B", "C", "D"}
    }
    missing_descriptions = sorted((described_slots or set()) - bound_slots)
    foreign_labels = {label for label, role in assignments.items() if role.startswith("q") and role[1:].isdigit()}
    bound_labels = {
        label for label, role in assignments.items()
        if role == "stem" or role in {"A", "B", "C", "D"}
    }
    decoration_labels = {label for label, role in assignments.items() if role == "decoration"}
    explicitly_excluded = {
        label for label, role in assignments.items() if role in {"none", "decoration"}
    }
    unclassified = candidate_labels - foreign_labels - bound_labels - explicitly_excluded
    excluded_count = len(explicitly_excluded)
    drawing_request = asks_student_to_draw(stem, options)
    # ``missing_figure`` is a coarse reader boolean.  A concrete bound crop is
    # stronger evidence that the request has been satisfied; explicit missing
    # slot descriptions below still block if an additional A/B/C/D crop is
    # absent.  Likewise, a pure instruction to draw does not mean the paper
    # omitted an input image.
    unresolved_reader_missing = bool(reader_missing) and not figures
    drawing_only_missing = unresolved_reader_missing and drawing_request and not cues \
        and not missing_descriptions
    if drawing_only_missing:
        unresolved_reader_missing = False

    signals: list[str] = []
    if cues:
        signals.append("text_cue")
    if reader_missing:
        signals.append("reader_missing")
        if not unresolved_reader_missing:
            signals.append("reader_missing_resolved")
    if drawing_request:
        signals.append("student_drawing_request")
    if missing_descriptions:
        signals.append("unbound_figure_description")
    if figures:
        signals.append("bound_figure")
    if excluded_count:
        signals.append("candidate_excluded")
    if decoration_labels:
        signals.append("candidate_decoration")
    if unclassified:
        signals.append("candidate_unclassified")

    if unresolved_reader_missing or missing_descriptions or (cues and not figures):
        return _versioned_automatic_review({
            "status": BLOCKED_MISSING,
            "reason": "题目文字或现有识读表明应有图，但还没有找到完整配图",
            "signals": signals,
            "cue_matches": cues,
            "missing_slots": missing_descriptions,
            "excluded_count": excluded_count,
        }, input_hash=input_hash)
    if unclassified:
        return _versioned_automatic_review({
            "status": CONFLICT,
            "reason": "有候选图尚未被现有识读明确分类，请确认是否属于本题",
            "signals": signals,
            "cue_matches": cues,
            "excluded_count": excluded_count,
            "unclassified_count": len(unclassified),
        }, input_hash=input_hash)
    if figures and not cues:
        return _versioned_automatic_review({
            "status": CONFLICT,
            "reason": "题目文字没有发现图像提示词，但现有识读绑定了印刷配图",
            "signals": [*signals, "bound_figure_without_text_cue"],
            "cue_matches": [],
            "excluded_count": excluded_count,
        }, input_hash=input_hash)
    if not figures and excluded_count:
        return _versioned_automatic_review({
            "status": AUTO_EXCLUDED,
            "reason": "没有发现图像提示词；未绑定的候选图已作为无关内容排除",
            "signals": signals,
            "cue_matches": cues,
            "excluded_count": excluded_count,
        }, input_hash=input_hash)
    return _versioned_automatic_review({
        "status": OK,
        "reason": "配图检查未发现矛盾",
        "signals": signals,
        "cue_matches": cues,
        "excluded_count": excluded_count,
    }, input_hash=input_hash)


def recheck_automatic_review(
    *, stem: str, options: dict | None, figures: list[dict], previous: dict | None,
) -> dict:
    """Re-run local safeguards after a figure is borrowed or restored.

    The earlier read may contain evidence that is not reconstructible from the
    final boxes alone (unclassified candidates, excluded candidates, or an
    explicitly described but unbound slot).  Preserve that evidence while
    checking the new figure set instead of treating one newly added box as a
    blanket resolution.
    """
    previous = previous if isinstance(previous, dict) else {}
    signals = set(previous.get("signals") or [])
    try:
        unclassified_count = max(0, int(previous.get("unclassified_count") or 0))
        excluded_count = max(0, int(previous.get("excluded_count") or 0))
    except (TypeError, ValueError):
        unclassified_count, excluded_count = 0, 0
    if "candidate_unclassified" in signals and not unclassified_count:
        unclassified_count = 1

    pending = {f"pending-{index}" for index in range(unclassified_count)}
    excluded = {f"excluded-{index}" for index in range(excluded_count)}
    assignments = {label: "none" for label in excluded}
    return automatic_review(
        stem=stem,
        options=options,
        candidate_labels=pending | excluded,
        assignments=assignments,
        figures=figures,
        reader_missing="reader_missing" in signals,
        described_slots=set(previous.get("missing_slots") or []),
    )


def _apply_automatic_upgrade_in_memory(question, review: dict) -> dict:
    """Expose an upgraded decision without writing during an ordinary read.

    ``stored_or_derived_review`` is used by list/detail serialization while the
    worker may be saving the same row.  A GET must therefore never update the
    database: doing so could overwrite a newer human/worker decision, or turn a
    harmless page load into a SQLite lock error.  Mutating only this already
    loaded instance keeps its serialized state and flags consistent.  Explicit
    write paths may subsequently save the instance inside their own transaction.
    Published snapshots are separate objects and are never touched here.
    """
    current = getattr(question, "figure_review", None)
    if current == review:
        return review

    question.figure_review = review
    if hasattr(question, "flags"):
        flags = [str(value) for value in (getattr(question, "flags", None) or []) if not figure_flag(str(value))]
        if review.get("status") == BLOCKED_MISSING:
            flags.append(FLAG_NO_FIGURE if review.get("cue_matches") and not getattr(question, "figures", None)
                         else FLAG_UNFOUND_FIGURE)
        elif review.get("status") == CONFLICT:
            flags.append(
                FLAG_UNFOUND_FIGURE if "candidate_unclassified" in (review.get("signals") or [])
                else FLAG_UNCUED_FIGURE
            )
        question.flags = flags
        if getattr(question, "state", None) in {"green", "yellow"}:
            question.state = "yellow" if flags else "green"

    return review


def stored_or_derived_review(question, *, ignored_candidates: list[str] | None = None) -> dict:
    """Return a human decision or the current deterministic automatic decision.

    Human confirmations are immutable here.  An automatic (or legacy
    source-less) review is trusted only when both its policy version and input
    signature match.  Otherwise it is rebuilt from the saved question, reads,
    candidates and figure boxes; this function never invokes a model, renders a
    page, or edits a published snapshot.
    """
    stored = question.figure_review if isinstance(getattr(question, "figure_review", None), dict) else {}
    if stored.get("source") == "human" and stored.get("status") in VALID_STATUSES:
        return stored

    current_figures = list(getattr(question, "figures", None) or [])
    manual = any(figure.get("source") == "manual" for figure in current_figures)
    if manual:
        return {
            "status": OK, "source": "human", "reason": "配图已经由人工设置",
            "signals": ["manual_figure"], "cue_matches": [], "excluded_count": 0,
        }

    # Existing databases already contain everything the new rule needs.  Derive
    # the same decision from those saved reads so old cards gain the safeguard
    # without a reread, page render, MinerU request, or model call.
    readings = [
        value for value in (
            getattr(question, "read_a", None),
            getattr(question, "read_b", None),
            getattr(question, "read_c", None),
        ) if isinstance(value, dict)
    ]
    primary = next((value for value in readings if "stem" in value or "figures" in value), {})
    candidates = list(getattr(question, "figure_candidates", None) or [])
    raw_ignored = ignored_candidates if ignored_candidates is not None else stored.get("ignored_candidates", [])
    ignored_keys = {
        value for value in raw_ignored
        if isinstance(value, str)
    } if isinstance(raw_ignored, list) else set()
    ignored_labels = {
        str(candidate.get("label")) for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("label") is not None
        and candidate_key(candidate) in ignored_keys
    }
    state = str(getattr(question, "state", "") or "")
    if (primary or candidates) and state in {"green", "yellow"} and str(getattr(question, "stem", "")).strip():
        kind = str(primary.get("type") or "unknown")
        if kind == "unknown":
            kind = str(getattr(question, "question_type", "unknown") or "unknown")
        options = getattr(question, "options", None) or {}
        # ``read_a``/``read_b`` are immutable audit evidence.  The pipeline may
        # nevertheless make a conservative local type decision when one reader
        # called a bare (1)(2)... list a choice question and the other called it
        # free response.  Honour that saved decision during later policy
        # upgrades without rewriting either raw reading.
        base_figures = without_automatic_textbook_badges(current_figures)
        if (kind in {"single_choice", "multiple_choice"}
                and str(getattr(question, "question_type", "unknown")) == "free_response"
                and any(str(value.get("type") or "unknown") == "free_response"
                        for value in readings)
                and not options and not candidates and not base_figures):
            kind = "free_response"
        assignments = resolve_automatic_figure_assignments(
            stem=question.stem,
            options=options,
            kind=kind,
            candidates=candidates,
            assignments=primary.get("figures") or {},
        )
        assignments.update({label: "none" for label in ignored_labels})
        review_figures = repaired_automatic_figures(
            figures=base_figures,
            candidates=candidates,
            assignments=assignments,
        )
        missing_option_slots = missing_choice_figure_slots(
            kind=kind,
            options=options,
            figures=review_figures,
            readings=readings,
        )
        described_slots = {
            slot for reading in readings
            for slot in (reading.get("figure_descriptions") or [])
        }
        derived = automatic_review(
            stem=question.stem,
            options=options,
            candidate_labels={
                str(candidate.get("label")) for candidate in candidates
                if isinstance(candidate, dict) and candidate.get("label") is not None
            },
            assignments=assignments,
            figures=review_figures,
            reader_missing=bool(primary.get("missing_figure") or missing_option_slots),
            described_slots=described_slots | missing_option_slots,
        )
    else:
        # If the original raw reads have already been compacted away, retain
        # only concrete safety evidence that cannot be reconstructed from the
        # final boxes (for example, the names of still-unbound option images).
        # The cue decision itself is always rerun under the current policy.
        derived = recheck_automatic_review(
            stem=getattr(question, "stem", ""),
            options=getattr(question, "options", None),
            figures=current_figures,
            previous=stored if stored.get("source") in {None, "automatic"} else {},
        )

    if (stored.get("source") == "automatic"
            and stored.get("status") in VALID_STATUSES
            and stored.get("policy_version") == FIGURE_REVIEW_POLICY_VERSION
            and stored.get("input_hash") == derived.get("input_hash")):
        return stored

    # Very old data and a few defensive call sites can contain a blocking
    # review after all raw reader/candidate evidence has already been removed.
    # Recompute first, but retain that block conservatively when there is truly
    # nothing left from which it can be disproved.  Real cue-rule upgrades have
    # text and/or figure evidence, so they take the freshly derived path above.
    has_reconstructible_evidence = bool(
        any(readings) or candidates or current_figures
        or cue_matches(getattr(question, "stem", ""), getattr(question, "options", None))
    )
    if (not has_reconstructible_evidence
            and stored.get("status") in BLOCKING_STATUSES
            and stored.get("source") in {None, "automatic"}):
        preserved = {**stored}
        preserved.pop("confirmed_at", None)
        preserved.pop("previous_figures", None)
        return _apply_automatic_upgrade_in_memory(
            question,
            _versioned_automatic_review(preserved, input_hash=derived["input_hash"]),
        )

    return _apply_automatic_upgrade_in_memory(question, derived)


def persist_automatic_review_upgrades(questions) -> dict[str, int]:
    """Persist current local policy results for an explicit question iterable.

    The helper performs no recognition and intentionally has no paper-wide
    discovery of its own: callers must pass the exact active queryset they
    mean to refresh, preferably inside their own transaction.  Human reviews,
    manual figures, and non-review workflow states are skipped.  Existing
    non-figure flags survive because ``stored_or_derived_review`` removes only
    flags owned by this module before deriving green/yellow state.
    """

    stats = {"seen": 0, "updated": 0, "unchanged": 0, "human_skipped": 0,
             "state_skipped": 0}
    iterator = questions.iterator() if hasattr(questions, "iterator") else iter(questions)
    for question in iterator:
        stats["seen"] += 1
        stored = getattr(question, "figure_review", None)
        stored = stored if isinstance(stored, dict) else {}
        if stored.get("source") == "human" or any(
                isinstance(item, dict) and item.get("source") == "manual"
                for item in (getattr(question, "figures", None) or [])):
            stats["human_skipped"] += 1
            continue
        if str(getattr(question, "state", "") or "") not in {"green", "yellow"}:
            stats["state_skipped"] += 1
            continue

        before_review = deepcopy(stored)
        before_flags = deepcopy(getattr(question, "flags", None) or [])
        before_state = getattr(question, "state", None)
        before_figures = deepcopy(getattr(question, "figures", None) or [])
        candidates = list(getattr(question, "figure_candidates", None) or [])
        readings = [
            value for value in (
                getattr(question, "read_a", None),
                getattr(question, "read_b", None),
                getattr(question, "read_c", None),
            ) if isinstance(value, dict)
        ]
        primary = next((value for value in readings if "stem" in value or "figures" in value), {})
        kind = str(primary.get("type") or getattr(question, "question_type", "unknown") or "unknown")
        assignments = resolve_automatic_figure_assignments(
            stem=str(getattr(question, "stem", "") or ""),
            options=getattr(question, "options", None) or {},
            kind=kind,
            candidates=candidates,
            assignments=primary.get("figures") or {},
        )
        raw_ignored = stored.get("ignored_candidates", [])
        ignored_keys = set(raw_ignored) if isinstance(raw_ignored, list) else set()
        assignments.update({
            str(candidate.get("label")): "none"
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("label") is not None
            and candidate_key(candidate) in ignored_keys
        })
        repaired_figures = repaired_automatic_figures(
            figures=before_figures,
            candidates=candidates,
            assignments=assignments,
        )
        if repaired_figures != before_figures:
            question.figures = repaired_figures
        review = stored_or_derived_review(question)
        if review.get("source") != "automatic":
            stats["human_skipped"] += 1
            continue

        changed_fields: list[str] = []
        if getattr(question, "figure_review", None) != before_review:
            changed_fields.append("figure_review")
        if hasattr(question, "flags") and (getattr(question, "flags", None) or []) != before_flags:
            changed_fields.append("flags")
        if hasattr(question, "state") and getattr(question, "state", None) != before_state:
            changed_fields.append("state")
        if hasattr(question, "figures") and (getattr(question, "figures", None) or []) != before_figures:
            changed_fields.append("figures")
        if not changed_fields:
            stats["unchanged"] += 1
            continue
        question.save(update_fields=changed_fields)
        stats["updated"] += 1
    return stats


def blocks_approval(review: dict | None) -> bool:
    return isinstance(review, dict) and review.get("status") in BLOCKING_STATUSES


def blocking_message(review: dict | None) -> str:
    if isinstance(review, dict) and review.get("reason"):
        return str(review["reason"])
    return "配图状态还需要确认"


def figure_flag(flag: str) -> bool:
    return flag in {FLAG_NO_FIGURE, LEGACY_FLAG_NO_FIGURE, FLAG_UNFOUND_FIGURE, FLAG_UNCUED_FIGURE} \
        or "选项是图" in flag
