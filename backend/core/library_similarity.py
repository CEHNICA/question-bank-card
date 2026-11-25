"""Pure local offline question similarity, multi-source evidence archiving, and variant groups.

Zero external LLM/network calls: runs in milliseconds using exact normalization,
numeric-skeleton normalization, and a character-trigram inverted index.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import unicodedata
import uuid
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from django.conf import settings
from django.db import models, transaction
from django.http import Http404, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import imaging, library, qtypes, textnorm
from .models import LibrarySolution, LibraryTagAssignment, Paper, PublishedQuestion, Question
from .pipeline import PageStore

logger = logging.getLogger(__name__)

OPTION_LETTERS = tuple("ABCDEFGH")
DUPLICATE_THRESHOLD = 0.91
VARIANT_SKELETON_THRESHOLD = 0.78
MIN_COMPARE_LEN = 6
MIN_VARIANT_LEN = 8
MAX_REVIEW_MATCHES = 3

_PREFIX_TYPE_RE = re.compile(
    r"^\s*(?:[（(\[【]\s*(?:多选题?|单选题?|不定项|填空题?|判断题?|解答题?|计算题?|证明题?)\s*[）)\]】]\s*)+"
)
_OPT_LEAD_RE = re.compile(r"^\s*[A-H]\s*[\.．、:：\)]\s*")
_NUM_RE = re.compile(r"(?<![A-Za-z_])\d+(?:\.\d+)?(?![A-Za-z_])")
_TOKEN_KEEP_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9+\-*/=<>≤≥≠≈≡^_]+")

_SIMILARITY_BUMP = 0
_INDEX_LOCK = threading.Lock()
_DOC_CACHE: dict[tuple[str, str, str, str], _IndexedDoc | None] = {}
_INV_CACHE: tuple[tuple[tuple[str, str, str, str], ...], dict[str, _IndexedDoc], dict[str, list[str]]] | None = None


@dataclass(frozen=True, slots=True)
class _IndexedDoc:
    pub_id: str
    short_id: str
    question_id: int | None
    paper_id: str
    source_filename: str
    number: int | None
    question_type: str
    stem: str
    options: dict[str, str]
    answer: str
    has_answer: bool
    tags: list[str]
    exact: str
    skeleton: str
    numbers: tuple[str, ...]
    exact_trigrams: frozenset[str]
    skeleton_trigrams: frozenset[str]


def bump_similarity_revision() -> None:
    """Invalidate cached ETag token and inverted index signature."""
    global _SIMILARITY_BUMP, _INV_CACHE
    with _INDEX_LOCK:
        _SIMILARITY_BUMP += 1
        _INV_CACHE = None


def library_revision_token() -> tuple[Any, ...]:
    """Cheap DB signature representing the current published question set."""
    agg = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).aggregate(
        count=models.Count("id"), latest=models.Max("published_at")
    )
    latest = agg.get("latest")
    latest_str = latest.isoformat() if latest is not None else ""
    return (int(agg.get("count") or 0), latest_str, _SIMILARITY_BUMP)


def _unify_latex_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("$", "")
    # Canonicalize synonymous LaTeX commands before stripping remaining slashes.
    replacements = (
        (r"\dfrac", r"\frac"),
        (r"\tfrac", r"\frac"),
        (r"\cfrac", r"\frac"),
        (r"\leqslant", "<="),
        (r"\leqq", "<="),
        (r"\leq", "<="),
        (r"\le", "<="),
        ("≤", "<="),
        (r"\geqslant", ">="),
        (r"\geqq", ">="),
        (r"\geq", ">="),
        (r"\ge", ">="),
        ("≥", ">="),
        (r"\neq", "!="),
        (r"\ne", "!="),
        ("≠", "!="),
        (r"\approx", "≈"),
        (r"\cdot", "*"),
        (r"\times", "*"),
        ("·", "*"),
        ("×", "*"),
        (r"\left", ""),
        (r"\right", ""),
        (r"\limits", ""),
        (r"\mathrm", ""),
        (r"\text", ""),
        (r"\mathbf", ""),
        (r"\textbf", ""),
    )
    for src, dst in replacements:
        text = text.replace(src, dst)
    text = re.sub(r"\\([A-Za-z]+)", lambda m: m.group(1).lower(), text)
    return text.lower()


def normalize_exact(stem: str, options: dict[str, Any] | None = None) -> str:
    """Normalize a question stem + options for duplicate detection (strips origin/type/punctuation/synonymous LaTeX)."""
    raw_stem = str(stem or "").strip()
    if not raw_stem:
        return ""
    stripped_stem, _origin = textnorm.split_origin(raw_stem)
    if not stripped_stem:
        stripped_stem = raw_stem
    stripped_stem = _PREFIX_TYPE_RE.sub("", stripped_stem).strip()
    core = _unify_latex_text(stripped_stem)
    parts = ["".join(_TOKEN_KEEP_RE.findall(core))]
    if isinstance(options, dict) and options:
        opt_parts: list[str] = []
        for key in OPTION_LETTERS:
            val = options.get(key)
            if val:
                cleaned_val = _OPT_LEAD_RE.sub("", str(val))
                norm_val = "".join(_TOKEN_KEEP_RE.findall(_unify_latex_text(cleaned_val)))
                if norm_val:
                    opt_parts.append(f"{key.lower()}:{norm_val}")
        if opt_parts:
            parts.extend(opt_parts)
    return "|".join(p for p in parts if p)


def normalize_skeleton(exact_text: str) -> str:
    """Replace numeric constants with `<N>` so variant numbers map to the same skeleton."""
    if not exact_text:
        return ""
    return _NUM_RE.sub("N", exact_text)


def _numeric_tokens(exact_text: str) -> tuple[str, ...]:
    return tuple(_NUM_RE.findall(exact_text))


def _trigrams(text: str) -> frozenset[str]:
    if len(text) < 3:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[i : i + 3] for i in range(len(text) - 2))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if inter == 0:
        return 0.0
    return inter / (len(a) + len(b) - inter)


def _classify_pair(
    exact_a: str,
    skel_a: str,
    nums_a: tuple[str, ...],
    exact_tri_a: frozenset[str],
    skel_tri_a: frozenset[str],
    doc_b: _IndexedDoc,
) -> tuple[str, float] | None:
    """Return ('duplicate'|'variant', score) or None if below similarity thresholds."""
    if len(exact_a) < MIN_COMPARE_LEN or len(doc_b.exact) < MIN_COMPARE_LEN:
        return None
    if exact_a == doc_b.exact:
        return ("duplicate", 1.0)

    exact_tri = _jaccard(exact_tri_a, doc_b.exact_trigrams)
    skel_tri = _jaccard(skel_tri_a, doc_b.skeleton_trigrams)
    if max(exact_tri, skel_tri) < 0.38:
        return None

    exact_ratio = SequenceMatcher(None, exact_a, doc_b.exact).ratio()
    if exact_ratio >= DUPLICATE_THRESHOLD and exact_tri >= 0.62:
        if Counter(nums_a) == Counter(doc_b.numbers):
            return ("duplicate", round(exact_ratio, 4))

    if len(skel_a) < MIN_VARIANT_LEN or len(doc_b.skeleton) < MIN_VARIANT_LEN:
        return None

    skel_ratio = SequenceMatcher(None, skel_a, doc_b.skeleton).ratio()
    if (skel_ratio >= VARIANT_SKELETON_THRESHOLD and skel_tri >= 0.44) or (exact_ratio >= 0.78 and exact_tri >= 0.48):
        score = round(max(skel_ratio, exact_ratio), 4)
        return ("variant", score)
    return None


def _build_indexed_doc_from_parts(
    pub_id: str,
    question_id: int | None,
    paper_id: str,
    source_filename: str,
    number: int | None,
    question_type: str,
    content: dict[str, Any],
    extras: dict[str, Any],
) -> _IndexedDoc | None:
    stem = str(content.get("stem") or "").strip()
    if content.get("body_mode") == "source_image" and not stem:
        return None
    if not stem:
        return None
    raw_opts = content.get("options")
    options: dict[str, Any] = raw_opts if isinstance(raw_opts, dict) else {}
    exact = normalize_exact(stem, options)
    if len(exact) < MIN_COMPARE_LEN:
        return None
    skeleton = normalize_skeleton(exact)
    answer = str(content.get("answer") or "").strip()
    has_answer = bool(answer) or bool(str(extras.get("solution_id") or "").strip())
    return _IndexedDoc(
        pub_id=pub_id,
        short_id=library.short_id_for(pub_id),
        question_id=question_id,
        paper_id=paper_id,
        source_filename=source_filename,
        number=number,
        question_type=question_type,
        stem=stem,
        options={str(k): str(v) for k, v in options.items() if v},
        answer=answer[:240],
        has_answer=has_answer,
        tags=list(library.tags_of(extras)),
        exact=exact,
        skeleton=skeleton,
        numbers=_numeric_tokens(exact),
        exact_trigrams=_trigrams(exact),
        skeleton_trigrams=_trigrams(skeleton),
    )


def _indexed_library() -> tuple[dict[str, _IndexedDoc], dict[str, list[str]]]:
    """Build or reuse normalized docs and inverted trigram index with constant 1-query DB cost."""
    global _INV_CACHE
    rows = list(
        PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
        .order_by("id")
        .values_list(
            "id",
            "question_id",
            "paper_id",
            "source_filename",
            "number",
            "question_type",
            "content_hash",
            "tags_text",
            "content",
            "extras",
        )
    )
    signature_items: list[tuple[str, str, str, str]] = []
    for row in rows:
        pub_id = str(row[0])
        c_hash = str(row[6] or "")
        t_text = str(row[7] or "")
        ext = row[9] if isinstance(row[9], dict) else {}
        sol_id = str(ext.get("solution_id") or "")
        signature_items.append((pub_id, c_hash, t_text, sol_id))
    signature = tuple(signature_items)

    with _INDEX_LOCK:
        cached = _INV_CACHE
        if cached is not None and cached[0] == signature:
            return cached[1], cached[2]

    docs: dict[str, _IndexedDoc] = {}
    with _INDEX_LOCK:
        if len(_DOC_CACHE) > 20_000:
            _DOC_CACHE.clear()
        for row, cache_key in zip(rows, signature_items):
            if cache_key in _DOC_CACHE:
                item = _DOC_CACHE[cache_key]
            else:
                content = row[8] if isinstance(row[8], dict) else {}
                extras = row[9] if isinstance(row[9], dict) else {}
                item = _build_indexed_doc_from_parts(
                    pub_id=cache_key[0],
                    question_id=row[1],
                    paper_id=str(row[2]) if row[2] else "",
                    source_filename=str(row[3] or ""),
                    number=row[4],
                    question_type=str(row[5] or "unknown"),
                    content=content,
                    extras=extras,
                )
                _DOC_CACHE[cache_key] = item
            if item is not None:
                docs[item.pub_id] = item

    inv: dict[str, list[str]] = defaultdict(list)
    for pid, doc in docs.items():
        for tri in doc.skeleton_trigrams:
            inv[tri].append(pid)

    with _INDEX_LOCK:
        _INV_CACHE = (signature, docs, inv)
    return docs, inv


def _candidate_ids_for(skel_trigrams: frozenset[str], inv: dict[str, list[str]], total_docs: int) -> list[str]:
    if not skel_trigrams or total_docs == 0:
        return []
    hits: Counter[str] = Counter()
    max_df = max(20, total_docs // 3)
    for tri in skel_trigrams:
        postings = inv.get(tri)
        if postings and len(postings) <= max_df:
            hits.update(postings)
    if not hits:
        for tri in skel_trigrams:
            postings = inv.get(tri)
            if postings:
                hits.update(postings)
    min_shared = max(2, len(skel_trigrams) // 4)
    return [pid for pid, cnt in hits.most_common(40) if cnt >= min_shared]


def _doc_match_payload(doc: _IndexedDoc, kind: str, score: float) -> dict[str, Any]:
    return {
        "publication_id": doc.pub_id,
        "short_id": doc.short_id,
        "kind": kind,
        "score": score,
        "source_filename": doc.source_filename,
        "number": doc.number,
        "question_type": doc.question_type,
        "question_type_label": qtypes.TYPE_LABELS.get(doc.question_type, "题型未定"),
        "stem": doc.stem,
        "options": doc.options,
        "answer": doc.answer,
        "has_answer": doc.has_answer,
        "tags": doc.tags,
    }


def find_matches_for_paper(paper: Paper, rows: list[Question] | None = None) -> dict[int, list[dict[str, Any]]]:
    """Match every non-empty text card on a review paper against published library items locally."""
    docs, inv = _indexed_library()
    if not docs:
        return {}
    questions = (
        rows
        if rows is not None
        else list(paper.questions.prefetch_related(library.live_publications_prefetch()).order_by("number", "id"))
    )
    total_docs = len(docs)
    result: dict[int, list[dict[str, Any]]] = {}
    for q in questions:
        stem = str(q.stem or "").strip()
        if q.body_mode == "source_image" and not stem:
            continue
        if not stem:
            continue
        exact = normalize_exact(stem, q.options if isinstance(q.options, dict) else {})
        if len(exact) < MIN_COMPARE_LEN:
            continue
        skel = normalize_skeleton(exact)
        nums = _numeric_tokens(exact)
        exact_tri = _trigrams(exact)
        skel_tri = _trigrams(skel)
        read_a = q.read_a if isinstance(q.read_a, dict) else {}
        dismissed = {str(x) for x in (read_a.get("dismissed_similarity_ids") or []) if x}
        own_live = {
            str(pub.pk)
            for pub in (getattr(q, "live_publications", None) or [])
            if getattr(pub, "status", None) == PublishedQuestion.Status.PUBLISHED
        }
        matches: list[tuple[int, float, dict[str, Any]]] = []
        for pid in _candidate_ids_for(skel_tri, inv, total_docs):
            if pid in dismissed or pid in own_live:
                continue
            doc_b = docs.get(pid)
            if doc_b is None or doc_b.question_id == q.pk:
                continue
            classified = _classify_pair(exact, skel, nums, exact_tri, skel_tri, doc_b)
            if classified is None:
                continue
            kind, score = classified
            priority = 0 if kind == "duplicate" else 1
            matches.append((priority, -score, _doc_match_payload(doc_b, kind, score)))
        if matches:
            matches.sort(key=lambda item: (item[0], item[1], item[2]["short_id"]))
            result[q.pk] = [item[2] for item in matches[:MAX_REVIEW_MATCHES]]
    return result


def scan_library_clusters() -> dict[str, Any]:
    """Scan all active library items for unmerged duplicates, variant suggestions, and existing variant groups."""
    docs, inv = _indexed_library()
    live_meta = {
        str(pk): (extras if isinstance(extras, dict) else {})
        for pk, extras in PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).values_list(
            "id", "extras"
        )
    }
    dismissed_map: dict[str, set[str]] = {}
    group_members: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pub_group: dict[str, str] = {}
    for pid, extras in live_meta.items():
        dismissed_map[pid] = {str(x) for x in (extras.get("dismissed_similarity_ids") or []) if x}
        vg = str(extras.get("variant_group_id") or "").strip()
        if vg:
            pub_group[pid] = vg
            doc = docs.get(pid)
            if doc is not None:
                group_members[vg].append(_doc_match_payload(doc, "variant", 1.0))

    dup_adj: dict[str, set[str]] = defaultdict(set)
    dup_scores: dict[tuple[str, str], float] = {}
    variant_pairs: list[tuple[float, str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    total_docs = len(docs)

    for pid_a, doc_a in docs.items():
        for pid_b in _candidate_ids_for(doc_a.skeleton_trigrams, inv, total_docs):
            if pid_b <= pid_a:
                continue
            pair = (pid_a, pid_b)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            if pid_b in dismissed_map.get(pid_a, set()) or pid_a in dismissed_map.get(pid_b, set()):
                continue
            doc_b = docs.get(pid_b)
            if doc_b is None:
                continue
            classified = _classify_pair(
                doc_a.exact, doc_a.skeleton, doc_a.numbers, doc_a.exact_trigrams, doc_a.skeleton_trigrams, doc_b
            )
            if classified is None:
                continue
            kind, score = classified
            if kind == "duplicate":
                dup_adj[pid_a].add(pid_b)
                dup_adj[pid_b].add(pid_a)
                dup_scores[pair] = score
            elif not (pub_group.get(pid_a) and pub_group.get(pid_a) == pub_group.get(pid_b)):
                variant_pairs.append((score, pid_a, pid_b))

    visited: set[str] = set()
    duplicate_groups: list[dict[str, Any]] = []
    for seed in sorted(dup_adj):
        if seed in visited:
            continue
        stack = [seed]
        comp: list[str] = []
        visited.add(seed)
        while stack:
            cur = stack.pop()
            comp.append(cur)
            for nxt in dup_adj[cur]:
                if nxt not in visited:
                    visited.add(nxt)
                    stack.append(nxt)
        if len(comp) < 2:
            continue
        comp.sort(key=lambda pid: (not docs[pid].has_answer, -len(docs[pid].tags), docs[pid].short_id))
        best_score = max(
            (dup_scores.get((min(a, b), max(a, b)), 0.92) for idx, a in enumerate(comp) for b in comp[idx + 1 :]),
            default=0.92,
        )
        duplicate_groups.append(
            {
                "keep_suggested_id": comp[0],
                "score": best_score,
                "items": [_doc_match_payload(docs[pid], "duplicate", best_score) for pid in comp],
            }
        )
    duplicate_groups.sort(key=lambda g: -g["score"])

    variant_pairs.sort(key=lambda row: (-row[0], row[1], row[2]))
    variant_suggestions = [
        {
            "score": score,
            "a": _doc_match_payload(docs[pid_a], "variant", score),
            "b": _doc_match_payload(docs[pid_b], "variant", score),
        }
        for score, pid_a, pid_b in variant_pairs[:80]
    ]
    variant_groups: list[dict[str, Any]] = [
        {"variant_group_id": vg, "items": sorted(items, key=lambda it: str(it.get("short_id") or ""))}
        for vg, items in sorted(group_members.items())
        if len(items) >= 2
    ]
    return {
        "duplicate_groups": duplicate_groups,
        "variant_suggestions": variant_suggestions,
        "variant_groups": variant_groups,
    }


class SimilarityError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _first_page_idx(regions: list[dict[str, Any]], content: dict[str, Any] | None = None) -> int:
    for item in regions:
        if isinstance(item, dict) and type(item.get("page_idx")) is int and item["page_idx"] >= 0:
            return int(item["page_idx"])
    if isinstance(content, dict):
        for item in content.get("sources") or []:
            if isinstance(item, dict) and type(item.get("page_idx")) is int and item["page_idx"] >= 0:
                return int(item["page_idx"])
    return 0


def snapshot_question_evidence(
    target_pub: PublishedQuestion,
    *,
    paper: Paper | None,
    source_filename: str,
    question_id: int | None,
    number: int | None,
    regions: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    content: dict[str, Any] | None = None,
    fallback_image_path: Path | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Snapshot the source question's handwritten crop PNG into target_pub's library folder."""
    ev_id = uuid.uuid4().hex[:12]
    crop_name = f"evidence-{ev_id}.png"
    target_dir = Path(settings.DATA_ROOT) / "library" / str(target_pub.pk)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved_crop_name = ""

    valid_regions = [
        {"page_idx": int(r["page_idx"]), "bbox": [float(v) for v in r["bbox"]]}
        for r in (regions or [])
        if isinstance(r, dict)
        and type(r.get("page_idx")) is int
        and r["page_idx"] >= 0
        and isinstance(r.get("bbox"), list)
        and len(r["bbox"]) == 4
    ]
    if paper is not None and valid_regions:
        try:
            store = PageStore(paper)
            rendered, _layout = imaging.stack_regions(valid_regions, store.load)
            rendered.save(target_dir / crop_name, format="PNG", optimize=False, compress_level=3)
            saved_crop_name = crop_name
        except Exception:  # noqa: BLE001 — fall back when source pages are missing
            saved_crop_name = ""

    if not saved_crop_name and fallback_image_path is not None and fallback_image_path.is_file():
        try:
            shutil.copyfile(fallback_image_path, target_dir / crop_name)
            saved_crop_name = crop_name
        except OSError:
            saved_crop_name = ""

    clean_figures = [
        {"slot": f.get("slot") or "stem", "page_idx": f.get("page_idx"), "bbox": list(f.get("bbox") or [])}
        for f in (figures or [])
        if isinstance(f, dict) and type(f.get("page_idx")) is int and isinstance(f.get("bbox"), list)
    ]
    return {
        "id": ev_id,
        "paper_id": str(paper.pk) if paper is not None else "",
        "source_filename": str(source_filename or "未命名资料"),
        "question_id": question_id,
        "number": number,
        "page_idx": _first_page_idx(valid_regions, content),
        "regions": valid_regions,
        "figures": clean_figures,
        "crop_file": saved_crop_name,
        "crop_url": f"/api/library/{target_pub.pk}/evidence/{ev_id}.png" if saved_crop_name else "",
        "note": str(note or "")[:200],
        "archived_at": timezone.now().isoformat(),
    }


def _dedup_evidence_append(existing: list[dict[str, Any]], entry: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    items = [deepcopy(item) for item in existing if isinstance(item, dict)]
    pid = str(entry.get("paper_id") or "")
    qid = entry.get("question_id")
    if pid and qid is not None:
        for cur in items:
            if str(cur.get("paper_id") or "") == pid and cur.get("question_id") == qid:
                return items, False
    items.append(entry)
    return items, True


def _adopt_answer_if_missing(target_pub: PublishedQuestion, answer_text: str, analysis_text: str) -> None:
    if library.has_formal_answer(target_pub.content, target_pub.extras):
        return
    clean_ans = str(answer_text or "").strip()
    clean_ana = str(analysis_text or "").strip()
    if not clean_ans and not clean_ana:
        return
    content = deepcopy(target_pub.content or {})
    if clean_ans:
        content["answer"] = clean_ans
    if clean_ana and not str(content.get("analysis") or "").strip():
        content["analysis"] = clean_ana
    target_pub.content = content
    target_pub.content_hash = library.content_hash(content)
    target_pub.search_text = library._search_text(content, target_pub.extras)


def merge_review_question_into_publication(question_id: int, target_publication_id: str) -> dict[str, Any]:
    """Archive a review card's handwritten crop into an existing library question without duplicating."""
    from .views import _soft_delete_questions, invalidate_paper_detail_cache, paper_json

    try:
        target_uuid = uuid.UUID(str(target_publication_id))
    except (ValueError, AttributeError):
        raise SimilarityError("目标题库题目编号不正确") from None

    with transaction.atomic():
        question = get_object_or_404(Question.objects.select_for_update().select_related("paper"), pk=question_id)
        if question.paper.status != Paper.Status.READY:
            raise SimilarityError("任务正在处理中，请完成后再合并证据", 409)
        if PublishedQuestion.objects.filter(question=question, status=PublishedQuestion.Status.PUBLISHED).exists():
            raise SimilarityError("这道题已经独立入库；请直接在题库整理中合并", 409)
        target_pub = get_object_or_404(
            PublishedQuestion.objects.select_for_update(), pk=target_uuid, status=PublishedQuestion.Status.PUBLISHED
        )
        regions = list(question.regions or [])
        figures = list(question.figures or [])
        extras = deepcopy(target_pub.extras if isinstance(target_pub.extras, dict) else {})
        existing = list(extras.get("evidence_sources") or [])
        existing_match = next(
            (
                item
                for item in existing
                if isinstance(item, dict)
                and str(item.get("paper_id") or "") == str(question.paper_id)
                and item.get("question_id") == question.pk
            ),
            None,
        )
        if existing_match is None:
            fallback_crop = None
            if question.body_mode == "source_image":
                imgs = (question.read_a or {}).get("question_images") or []
                if imgs and isinstance(imgs[0], dict) and imgs[0].get("file"):
                    fallback_crop = (
                        Path(settings.DATA_ROOT) / str(question.paper_id) / "question-images" / str(imgs[0]["file"])
                    )
            entry = snapshot_question_evidence(
                target_pub,
                paper=question.paper,
                source_filename=question.paper.display_name,
                question_id=question.pk,
                number=question.number,
                regions=regions,
                figures=figures,
                fallback_image_path=fallback_crop,
            )
            extras["evidence_sources"], _added = _dedup_evidence_append(existing, entry)
        else:
            entry = existing_match
        target_pub.extras = extras
        _adopt_answer_if_missing(target_pub, question.answer, question.analysis)
        target_pub.search_text = library._search_text(target_pub.content, target_pub.extras)
        target_pub.save(update_fields=["content", "content_hash", "extras", "search_text"])

        batch, deleted = _soft_delete_questions(question.paper, [question.pk])
        short = library.short_id_for(target_pub.pk)
        batch.reason = f"已合并手写证据至题库 {short}"[:300]
        batch.save(update_fields=["reason"])

    invalidate_paper_detail_cache(question.paper_id)
    bump_similarity_revision()
    return {
        "ok": True,
        "deleted": deleted,
        "target_publication_id": str(target_pub.pk),
        "target_short_id": library.short_id_for(target_pub.pk),
        "evidence": entry,
        "undo_batch_id": str(batch.pk),
        "paper": paper_json(Paper.objects.get(pk=question.paper_id)),
    }


def merge_library_publications(keep_id: str, merge_ids: list[str]) -> dict[str, Any]:
    """Merge duplicate published items into keep_id, preserving all handwritten evidence & formal tags."""
    from . import library_tagging

    try:
        keep_uuid = uuid.UUID(str(keep_id))
        merge_uuids = [uuid.UUID(str(x)) for x in (merge_ids or []) if str(x) != str(keep_uuid)]
    except (ValueError, AttributeError):
        raise SimilarityError("请提供合法的题目编号") from None
    if not merge_uuids:
        raise SimilarityError("请至少选择一道要合并的重复题")

    with transaction.atomic():
        keep_pub = get_object_or_404(
            PublishedQuestion.objects.select_for_update(), pk=keep_uuid, status=PublishedQuestion.Status.PUBLISHED
        )
        victims = list(
            PublishedQuestion.objects.select_for_update()
            .select_related("paper")
            .filter(pk__in=merge_uuids, status=PublishedQuestion.Status.PUBLISHED)
        )
        if not victims:
            raise SimilarityError("要合并的题目不存在或已被撤回", 404)

        extras = deepcopy(keep_pub.extras if isinstance(keep_pub.extras, dict) else {})
        sources = list(extras.get("evidence_sources") or [])
        existing_tag_ids = set(
            LibraryTagAssignment.objects.filter(publication=keep_pub).values_list("tag_id", flat=True)
        )
        tags_adopted = False
        keep_dir = Path(settings.DATA_ROOT) / "library" / str(keep_pub.pk)

        for victim in victims:
            v_content = victim.content if isinstance(victim.content, dict) else {}
            v_extras = victim.extras if isinstance(victim.extras, dict) else {}
            # 1. Copy over any archived evidence already living on victim (multi-hop merge).
            for prev_ev in v_extras.get("evidence_sources") or []:
                if not isinstance(prev_ev, dict):
                    continue
                ev_copy = deepcopy(prev_ev)
                old_crop = str(ev_copy.get("crop_file") or "").strip()
                if old_crop:
                    src_path = Path(settings.DATA_ROOT) / "library" / str(victim.pk) / old_crop
                    if src_path.is_file():
                        new_ev_id = uuid.uuid4().hex[:12]
                        new_crop = f"evidence-{new_ev_id}.png"
                        keep_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src_path, keep_dir / new_crop)
                        ev_copy["id"] = new_ev_id
                        ev_copy["crop_file"] = new_crop
                        ev_copy["crop_url"] = f"/api/library/{keep_pub.pk}/evidence/{new_ev_id}.png"
                    else:
                        ev_copy["crop_file"] = ""
                        ev_copy["crop_url"] = ""
                sources, _ = _dedup_evidence_append(sources, ev_copy)

            # 2. Snapshot victim's own original paper crop/coordinates into keep_pub.
            fallback_crop = None
            q_imgs = v_content.get("question_images") or []
            if q_imgs and isinstance(q_imgs[0], dict) and q_imgs[0].get("file"):
                fallback_crop = Path(settings.DATA_ROOT) / "library" / str(victim.pk) / str(q_imgs[0]["file"])
            elif v_content.get("figures") and isinstance(v_content["figures"][0], dict):
                f0 = v_content["figures"][0].get("file")
                if f0:
                    fallback_crop = Path(settings.DATA_ROOT) / "library" / str(victim.pk) / str(f0)
            entry = snapshot_question_evidence(
                keep_pub,
                paper=victim.paper,
                source_filename=victim.source_filename or (victim.paper.display_name if victim.paper else "已入库原题"),
                question_id=victim.question_id,
                number=victim.number,
                regions=list(v_content.get("sources") or []),
                figures=list(v_content.get("figures") or []),
                content=v_content,
                fallback_image_path=fallback_crop,
            )
            sources, _ = _dedup_evidence_append(sources, entry)

            # 3. Adopt formal answer & solution/tags if keep_pub lacks them.
            _adopt_answer_if_missing(keep_pub, v_content.get("answer") or "", v_content.get("analysis") or "")
            if not library.has_formal_answer(keep_pub.content, extras):
                v_sol_id = v_extras.get("solution_id")
                if v_sol_id:
                    old_sol = LibrarySolution.objects.filter(pk=v_sol_id, publication=victim).first()
                    if old_sol is not None:
                        copied_sol = LibrarySolution.objects.create(
                            publication=keep_pub,
                            parent=None,
                            fingerprint=library.generation_fingerprint(keep_pub.content, keep_pub.pk),
                            answer=old_sol.answer,
                            analysis=old_sol.analysis,
                            figures=[],
                        )
                        extras["solution_id"] = str(copied_sol.pk)

            for assign in LibraryTagAssignment.objects.filter(publication=victim, needs_review=False):
                if assign.tag_id not in existing_tag_ids:
                    adopted_row = LibraryTagAssignment.objects.create(
                        publication=keep_pub,
                        tag_id=assign.tag_id,
                        scope=deepcopy(assign.scope or {}),
                        source=assign.source,
                        generation_source=assign.generation_source,
                        rationale=assign.rationale,
                        evidence=assign.evidence,
                        needs_review=False,
                    )
                    library_tagging.restore_assignment_timestamps(adopted_row, assign.created_at, assign.updated_at)
                    existing_tag_ids.add(assign.tag_id)
                    tags_adopted = True
                elif assign.created_at is not None:
                    kept_row = LibraryTagAssignment.objects.filter(publication=keep_pub, tag_id=assign.tag_id).first()
                    if kept_row is not None and (
                        kept_row.created_at is None or assign.created_at < kept_row.created_at
                    ):
                        library_tagging.restore_assignment_timestamps(kept_row, created_at=assign.created_at)

            library.withdraw(victim)

        extras["evidence_sources"] = sources
        keep_pub.extras = extras
        keep_pub.search_text = library._search_text(keep_pub.content, keep_pub.extras)
        keep_pub.save(update_fields=["content", "content_hash", "extras", "search_text"])
        if tags_adopted:
            library_tagging._sync_projection(keep_pub.pk)
            keep_pub.refresh_from_db()

    bump_similarity_revision()
    return {
        "ok": True,
        "keep": library.publication_json(keep_pub),
        "merged_ids": [str(v.pk) for v in victims],
        "evidence_count": len((keep_pub.extras or {}).get("evidence_sources") or []),
    }


def remove_evidence_source(publication_id: str, evidence_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", str(evidence_id or "")):
        raise SimilarityError("证据编号格式不正确")
    with transaction.atomic():
        pub = get_object_or_404(PublishedQuestion.objects.select_for_update(), pk=publication_id)
        extras = deepcopy(pub.extras if isinstance(pub.extras, dict) else {})
        sources = [item for item in (extras.get("evidence_sources") or []) if isinstance(item, dict)]
        kept: list[dict[str, Any]] = []
        removed: dict[str, Any] | None = None
        for item in sources:
            if item.get("id") == evidence_id:
                removed = item
            else:
                kept.append(item)
        if removed is None:
            raise SimilarityError("找不到该条核验证据", 404)
        extras["evidence_sources"] = kept
        pub.extras = extras
        pub.save(update_fields=["extras"])
        crop_file = str(removed.get("crop_file") or "").strip()
        if crop_file:
            path = Path(settings.DATA_ROOT) / "library" / str(pub.pk) / crop_file
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    bump_similarity_revision()
    return {"ok": True, "evidence_sources": kept}


def bind_variant_group(publication_ids: list[str]) -> dict[str, Any]:
    """Bind 2+ active published questions into one shared variant group."""
    try:
        uuids = list(dict.fromkeys(uuid.UUID(str(x)) for x in (publication_ids or [])))
    except (ValueError, AttributeError):
        raise SimilarityError("请提供合法的题目编号") from None
    if len(uuids) < 2:
        raise SimilarityError("至少需要两道题目才能互绑为变式题组")

    with transaction.atomic():
        pubs = list(
            PublishedQuestion.objects.select_for_update().filter(
                pk__in=uuids, status=PublishedQuestion.Status.PUBLISHED
            )
        )
        if len(pubs) < 2:
            raise SimilarityError("选中的题目中可绑定数不足两道（已撤回题目不能绑定）", 409)
        existing_groups = {
            str((p.extras or {}).get("variant_group_id") or "").strip()
            for p in pubs
            if isinstance(p.extras, dict) and str(p.extras.get("variant_group_id") or "").strip()
        }
        group_id = sorted(existing_groups)[0] if existing_groups else uuid.uuid4().hex[:12]
        # Pull in any existing peers belonging to other merged variant_group_ids cleanly.
        if len(existing_groups) > 1:
            extra_peers = list(
                PublishedQuestion.objects.select_for_update().filter(
                    status=PublishedQuestion.Status.PUBLISHED, extras__variant_group_id__in=list(existing_groups)
                )
            )
            seen = {p.pk for p in pubs}
            pubs.extend(peer for peer in extra_peers if peer.pk not in seen)

        for pub in pubs:
            extras = deepcopy(pub.extras if isinstance(pub.extras, dict) else {})
            extras["variant_group_id"] = group_id
            pub.extras = extras
            pub.save(update_fields=["extras"])

    bump_similarity_revision()
    return {"ok": True, "variant_group_id": group_id, "publication_ids": [str(p.pk) for p in pubs]}


def unbind_variant(publication_id: str) -> dict[str, Any]:
    """Remove a question from its variant group (cleaning up any leftover singleton)."""
    try:
        pub_uuid = uuid.UUID(str(publication_id))
    except (ValueError, AttributeError):
        raise SimilarityError("题目编号格式不正确") from None

    with transaction.atomic():
        pub = get_object_or_404(PublishedQuestion.objects.select_for_update(), pk=pub_uuid)
        extras = deepcopy(pub.extras if isinstance(pub.extras, dict) else {})
        group_id = str(extras.pop("variant_group_id", "") or "").strip()
        pub.extras = extras
        pub.save(update_fields=["extras"])
        if group_id:
            remaining = list(
                PublishedQuestion.objects.select_for_update().filter(
                    status=PublishedQuestion.Status.PUBLISHED, extras__variant_group_id=group_id
                )
            )
            if len(remaining) == 1:
                solo = remaining[0]
                solo_extras = deepcopy(solo.extras if isinstance(solo.extras, dict) else {})
                solo_extras.pop("variant_group_id", None)
                solo.extras = solo_extras
                solo.save(update_fields=["extras"])

    bump_similarity_revision()
    return {"ok": True, "publication_id": str(pub_uuid)}


def dismiss_similarity(a_id: str, b_id: str) -> dict[str, Any]:
    """Mark two library publications as unrelated / intentionally kept separate."""
    try:
        uid_a, uid_b = uuid.UUID(str(a_id)), uuid.UUID(str(b_id))
    except (ValueError, AttributeError):
        raise SimilarityError("题目编号格式不正确") from None
    if uid_a == uid_b:
        raise SimilarityError("不能对同一道题忽略相似")

    with transaction.atomic():
        pubs = list(
            PublishedQuestion.objects.select_for_update().filter(
                pk__in=[uid_a, uid_b], status=PublishedQuestion.Status.PUBLISHED
            )
        )
        by_id = {p.pk: p for p in pubs}
        pub_a, pub_b = by_id.get(uid_a), by_id.get(uid_b)
        if pub_a is None or pub_b is None:
            raise SimilarityError("目标题目不存在或已撤回", 404)
        for left, right in ((pub_a, pub_b), (pub_b, pub_a)):
            extras = deepcopy(left.extras if isinstance(left.extras, dict) else {})
            dismissed = list(dict.fromkeys([*(extras.get("dismissed_similarity_ids") or []), str(right.pk)]))
            extras["dismissed_similarity_ids"] = dismissed
            left.extras = extras
            left.save(update_fields=["extras"])

    bump_similarity_revision()
    return {"ok": True}


def set_review_similarity_preference(question_id: int, target_publication_id: str, mode: str) -> dict[str, Any]:
    """Dismiss a review-card similarity banner or mark it to auto-bind as a variant on publish."""
    from .views import invalidate_paper_detail_cache

    if mode not in {"keep_independent", "bind_variant"}:
        raise SimilarityError("操作类型不正确")
    try:
        target_uuid = uuid.UUID(str(target_publication_id))
    except (ValueError, AttributeError):
        raise SimilarityError("目标题库题目编号不正确") from None

    with transaction.atomic():
        question = get_object_or_404(Question.objects.select_for_update(), pk=question_id)
        get_object_or_404(PublishedQuestion, pk=target_uuid, status=PublishedQuestion.Status.PUBLISHED)
        read_a = deepcopy(question.read_a if isinstance(question.read_a, dict) else {})
        if mode == "bind_variant":
            read_a["bind_variant_pub_id"] = str(target_uuid)
            read_a.pop("dismissed_similarity_ids", None)
        else:
            dismissed = list(dict.fromkeys([*(read_a.get("dismissed_similarity_ids") or []), str(target_uuid)]))
            read_a["dismissed_similarity_ids"] = dismissed
            if read_a.get("bind_variant_pub_id") == str(target_uuid):
                read_a.pop("bind_variant_pub_id", None)
        question.read_a = read_a
        question.save(update_fields=["read_a", "updated_at"])

    invalidate_paper_detail_cache(question.paper_id)
    return {
        "ok": True,
        "mode": mode,
        "bind_variant_pub_id": read_a.get("bind_variant_pub_id"),
        "dismissed_similarity_ids": read_a.get("dismissed_similarity_ids") or [],
    }


# ---------------------------------------------------------------- HTTP Views


def _json_error(error: Exception, status: int = 400) -> JsonResponse:
    code = getattr(error, "status", status)
    return JsonResponse({"error": str(error)}, status=code)


@csrf_exempt
def merge_duplicate_view(request, question_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    target_id = str(payload.get("target_publication_id") or "").strip()
    if not target_id:
        return _error("请指定要合并进的原题编号")
    try:
        return JsonResponse(merge_review_question_into_publication(question_id, target_id))
    except (SimilarityError, ValueError, LookupError) as error:
        return _json_error(error)


@csrf_exempt
def review_similarity_preference_view(request, question_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    target_id = str(payload.get("target_publication_id") or "").strip()
    mode = str(payload.get("mode") or "keep_independent").strip()
    try:
        return JsonResponse(set_review_similarity_preference(question_id, target_id, mode))
    except (SimilarityError, ValueError) as error:
        return _json_error(error)


def dedup_scan_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return JsonResponse(scan_library_clusters())


@csrf_exempt
def dedup_merge_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    keep_id = str(payload.get("keep_id") or "").strip()
    merge_ids = payload.get("merge_ids")
    if not isinstance(merge_ids, list):
        return _error("merge_ids 必须是编号列表")
    try:
        return JsonResponse(merge_library_publications(keep_id, [str(x) for x in merge_ids]))
    except SimilarityError as error:
        return _json_error(error)


@csrf_exempt
def dedup_dismiss_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    try:
        return JsonResponse(dismiss_similarity(str(payload.get("a_id") or ""), str(payload.get("b_id") or "")))
    except SimilarityError as error:
        return _json_error(error)


@csrf_exempt
def variants_bind_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None or not isinstance(payload.get("publication_ids"), list):
        return _error("请提供 publication_ids 列表")
    try:
        return JsonResponse(bind_variant_group([str(x) for x in payload["publication_ids"]]))
    except SimilarityError as error:
        return _json_error(error)


@csrf_exempt
def variants_unbind_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _error, _guard

    rejected = _guard(request)
    if rejected is not None:
        return rejected
    payload = _body(request)
    if payload is None:
        return _error("请求内容不正确")
    try:
        return JsonResponse(unbind_variant(str(payload.get("publication_id") or "")))
    except SimilarityError as error:
        return _json_error(error)


def evidence_image_view(request, publication_id, evidence_id: str):
    if request.method == "DELETE":
        from .views import _guard

        rejected = _guard(request, json_body=False)
        if rejected is not None:
            return rejected
        try:
            return JsonResponse(remove_evidence_source(str(publication_id), evidence_id))
        except SimilarityError as error:
            return _json_error(error)
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET", "DELETE"])
    from .views import _file

    pub = get_object_or_404(PublishedQuestion, pk=publication_id)
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", str(evidence_id or "")):
        raise Http404
    base_dir = (Path(settings.DATA_ROOT) / "library" / str(pub.pk)).resolve()
    path = (base_dir / f"evidence-{evidence_id}.png").resolve()
    if path.parent != base_dir or not path.is_file():
        raise Http404
    return _file(path, "image/png")
