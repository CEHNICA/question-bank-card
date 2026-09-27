"""Zero-latency figure safeguards built from data the pipeline already has.

This module must stay local and deterministic: it never renders pages and never
calls MinerU or a vision model.  It combines the recognised question text with
the candidate assignments returned by the existing first reader.
"""

from __future__ import annotations

import re
import unicodedata


BLOCKED_MISSING = "blocked_missing"
AUTO_EXCLUDED = "auto_excluded"
CONFLICT = "conflict"
CONFIRMED_NO_FIGURE = "confirmed_no_figure"
OK = "ok"
VALID_STATUSES = {BLOCKED_MISSING, AUTO_EXCLUDED, CONFLICT, CONFIRMED_NO_FIGURE, OK}
BLOCKING_STATUSES = {BLOCKED_MISSING, CONFLICT}

FLAG_NO_FIGURE = "题干说有图，但还没有配图，请点“配图”框出"
FLAG_UNFOUND_FIGURE = "原卷可能有图没有被找到，请点“配图”框出"
FLAG_UNCUED_FIGURE = "题目文字没有发现图像提示词，但识读判断有印刷配图，请确认配图或确认本题确实无图"
LEGACY_FLAG_NO_FIGURE = "题干说“如图”，但还没有配图，请点“配图”框出"


# Only phrases that refer to an existing visual are included.  Bare words such
# as “图象/graph” are deliberately excluded because “画出函数图象 / sketch a
# graph” asks the student to create one and does not prove the paper supplies it.
_CHINESE_CUE = re.compile(
    r"(?:"
    r"如\s*(?:下|上|左|右)?\s*图(?:\s*[甲乙丙丁①②③④⑤⑥⑦⑧⑨1-9A-Za-z])?(?:\s*所示)?"
    r"(?=$|[\s，,。:：；;（(])|"
    r"(?:下|上|左|右)\s*图|"
    r"图\s*(?:中|上|下|左|右|所示)|"
    r"见\s*图|看\s*图|读\s*图|观察\s*(?:下|上|左|右)?\s*图|"
    r"(?:根据|依据|结合)\s*(?:下|上|左|右)?\s*图(?:\s*(?:中|所示))?"
    r"(?=$|[\s，,。:：；;（(]|可知|可得|显示)|"
    r"(?:由|从)\s*(?:下|上|左|右)?\s*图(?:\s*中)?\s*(?:可知|可得|看出|得出)|"
    r"(?<![\u4e00-\u9fffA-Za-z0-9])图\s*为|"
    r"图\s*[①②③④⑤⑥⑦⑧⑨一二三四五六七八九1-9][A-Za-z]?|"
    r"(?<!不)如\s*表(?!格)(?:\s*所示)?(?=$|[\s，,。:：；;（(])|"
    r"(?:下|上|左|右)\s*表|表\s*(?:中|所示)|"
    r"(?:下列|以下)\s*(?:图形|图示|图案|示意图|简图)|"
    r"(?:图像|图象|图形)\s*(?:大致|可能)\s*是\s*[（(]|"
    r"(?:示意图|简图|统计图|折线图|柱状图|扇形图|电路图|结构图|装置图|流程图|"
    r"函数图(?:像|象)|坐标图|路线图|地图)\s*(?:中|上|下|所示|显示|如下)"
    r")"
)

_ENGLISH_CUE = re.compile(
    r"(?:"
    r"\b(?:the\s+)?(?:following|above|below|accompanying)\s+"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b|"
    r"\b(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\s+"
    r"(?:above|below|shows?|illustrates?|depicts?|is\s+shown)\b|"
    r"\b(?:as\s+)?(?:shown|illustrated|depicted)\s+(?:above|below)\b|"
    r"\b(?:as\s+)?(?:shown|illustrated|depicted)\s+(?:in|on)\s+"
    r"(?:the\s+|this\s+|following\s+)?"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b|"
    r"\brefer(?:ring)?\s+to\s+(?:the\s+|this\s+|following\s+)?"
    r"(?:figure|diagram|image|picture|illustration|graph|chart|table|map)\b"
    r"(?!\s+(?:theory|method)\b)|"
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
    cues = cue_matches(stem, options)
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
    explicitly_excluded = {label for label, role in assignments.items() if role == "none"}
    unclassified = candidate_labels - foreign_labels - bound_labels - explicitly_excluded
    excluded_count = len(explicitly_excluded)

    signals: list[str] = []
    if cues:
        signals.append("text_cue")
    if reader_missing:
        signals.append("reader_missing")
    if missing_descriptions:
        signals.append("unbound_figure_description")
    if figures:
        signals.append("bound_figure")
    if excluded_count:
        signals.append("candidate_excluded")
    if unclassified:
        signals.append("candidate_unclassified")

    if reader_missing or missing_descriptions or (cues and not figures):
        return {
            "status": BLOCKED_MISSING,
            "source": "automatic",
            "reason": "题目文字或现有识读表明应有图，但还没有找到完整配图",
            "signals": signals,
            "cue_matches": cues,
            "missing_slots": missing_descriptions,
            "excluded_count": excluded_count,
        }
    if unclassified:
        return {
            "status": CONFLICT,
            "source": "automatic",
            "reason": "有候选图尚未被现有识读明确分类，请确认是否属于本题",
            "signals": signals,
            "cue_matches": cues,
            "excluded_count": excluded_count,
            "unclassified_count": len(unclassified),
        }
    if figures and not cues:
        return {
            "status": CONFLICT,
            "source": "automatic",
            "reason": "题目文字没有发现图像提示词，但现有识读绑定了印刷配图",
            "signals": [*signals, "bound_figure_without_text_cue"],
            "cue_matches": [],
            "excluded_count": excluded_count,
        }
    if not figures and excluded_count:
        return {
            "status": AUTO_EXCLUDED,
            "source": "automatic",
            "reason": "没有发现图像提示词；未绑定的候选图已作为无关内容排除",
            "signals": signals,
            "cue_matches": cues,
            "excluded_count": excluded_count,
        }
    return {
        "status": OK,
        "source": "automatic",
        "reason": "配图检查未发现矛盾",
        "signals": signals,
        "cue_matches": cues,
        "excluded_count": 0,
    }


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


def stored_or_derived_review(question) -> dict:
    """Return persisted evidence, or derive a safe result for pre-migration/read data."""
    stored = question.figure_review if isinstance(getattr(question, "figure_review", None), dict) else {}
    if stored.get("status") in VALID_STATUSES:
        return stored

    flags = [str(value) for value in (getattr(question, "flags", None) or [])]
    current_figures = list(getattr(question, "figures", None) or [])
    manual = any(figure.get("source") == "manual" for figure in current_figures)
    if manual:
        return {
            "status": OK, "source": "human", "reason": "配图已经由人工设置",
            "signals": ["manual_figure"], "cue_matches": [], "excluded_count": 0,
        }
    if (any(flag in {FLAG_NO_FIGURE, LEGACY_FLAG_NO_FIGURE, FLAG_UNFOUND_FIGURE} for flag in flags)
            or any("选项是图" in flag for flag in flags)):
        return {
            "status": BLOCKED_MISSING, "source": "automatic",
            "reason": "题目文字或现有识读表明应有图，但还没有找到完整配图",
            "signals": ["existing_missing_flag"],
            "cue_matches": cue_matches(question.stem, question.options), "excluded_count": 0,
        }
    if FLAG_UNCUED_FIGURE in flags:
        return {
            "status": CONFLICT, "source": "automatic",
            "reason": "题目文字没有发现图像提示词，但现有识读绑定了印刷配图",
            "signals": ["existing_conflict_flag"], "cue_matches": [], "excluded_count": 0,
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
    state = str(getattr(question, "state", "") or "")
    if (primary or candidates) and state in {"green", "yellow"} and str(getattr(question, "stem", "")).strip():
        kind = str(primary.get("type") or "unknown")
        if kind == "unknown":
            kind = str(getattr(question, "question_type", "unknown") or "unknown")
        options = getattr(question, "options", None) or {}
        option_slots = {
            figure.get("slot") for figure in current_figures
            if figure.get("slot") in {"A", "B", "C", "D"}
        }
        missing_option_slots = (
            {"A", "B", "C", "D"} - option_slots
            if kind in {"single_choice", "multiple_choice"} and not options else set()
        )
        described_slots = {
            slot for reading in readings
            for slot in (reading.get("figure_descriptions") or [])
        }
        return automatic_review(
            stem=question.stem,
            options=options,
            candidate_labels={
                str(candidate.get("label")) for candidate in candidates
                if isinstance(candidate, dict) and candidate.get("label") is not None
            },
            assignments={str(label): role for label, role in (primary.get("figures") or {}).items()},
            figures=current_figures,
            reader_missing=bool(primary.get("missing_figure") or missing_option_slots),
            described_slots=described_slots | missing_option_slots,
        )

    cues = cue_matches(question.stem, question.options)
    figures = current_figures
    if cues and not figures:
        return {
            "status": BLOCKED_MISSING, "source": "automatic",
            "reason": "题目文字表明应有图，但题卡还没有配图",
            "signals": ["text_cue"], "cue_matches": cues, "excluded_count": 0,
        }
    if figures and not cues:
        return {
            "status": CONFLICT, "source": "automatic",
            "reason": "题目文字没有发现图像提示词，但题卡带有自动配图",
            "signals": ["bound_figure_without_text_cue"], "cue_matches": [], "excluded_count": 0,
        }
    return {
        "status": OK, "source": "automatic", "reason": "配图检查未发现矛盾",
        "signals": [], "cue_matches": cues, "excluded_count": 0,
    }


def blocks_approval(review: dict | None) -> bool:
    return isinstance(review, dict) and review.get("status") in BLOCKING_STATUSES


def blocking_message(review: dict | None) -> str:
    if isinstance(review, dict) and review.get("reason"):
        return str(review["reason"])
    return "配图状态还需要确认"


def figure_flag(flag: str) -> bool:
    return flag in {FLAG_NO_FIGURE, LEGACY_FLAG_NO_FIGURE, FLAG_UNFOUND_FIGURE, FLAG_UNCUED_FIGURE} \
        or "选项是图" in flag
