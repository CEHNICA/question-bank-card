"""结构化检索服务：找题、计数、命中解释。

三个规矩，改之前先看一眼：

**一、正式结果、旧记录、待核对线索是三件事，分开数。**
「已确认匹配」是人工确认过、依据当前有效的正式关联。「旧标签命中」是旧记录，
没定位到具体解法，不能冒充精确的方法证明。「待核对线索」是 AI 候选或全文提及，
不计入正式匹配数。

**二、没整理不等于没有。**
排除某个方法时，题目没整理过要进「未知」，不能当成「确认没用这个方法」。
所以每条结果都带三态：确认匹配／确认不匹配／未知。

**三、方法 A 和方法 B 默认要同一个解法里都有。**
用户找「分别有两种解法」的题得显式说，否则暗地里把两种语义混在一起，
结果就骗人了。

自然语言入口（``library_nlquery``）只是这上面的一个翻译层，不是找题的前置条件。
没有 API key 也照样能找题。
"""

from __future__ import annotations

from typing import Any

from .models import (
    LibraryChapter,
    LibraryCollectionMember,
    LibraryQuestionPlacement,
    LibrarySavedSearch,
    LibrarySolutionVariant,
    LibrarySubquestion,
    LibraryTagAssignment,
    LibraryTagRelation,
    LibraryTagSuggestion,
    PublishedQuestion,
)

QUERY_VERSION = 2

#: 用途分组。查「主要考查」时，辅助用到和题面出现都不能冒充。
# 用 str() 包一层：TextChoices 的成员在运行时是字符串，在类型检查眼里却是
# (值, 标签) 二元组。包成 str 之后两边说的都是同一件事。
ROLE_PRIMARY = str(LibraryTagAssignment.Role.PRIMARY)
ROLE_SUPPORTING = str(LibraryTagAssignment.Role.SUPPORTING)
ROLE_METHOD = str(LibraryTagAssignment.Role.METHOD)
ROLE_FEATURE = str(LibraryTagAssignment.Role.FEATURE)
ROLE_LEGACY = str(LibraryTagAssignment.Role.LEGACY)

ALL_ROLES = (ROLE_PRIMARY, ROLE_SUPPORTING, ROLE_METHOD, ROLE_FEATURE, ROLE_LEGACY)

#: 用途的中文说法。命中原因里要写出来，不能只丢一个 role 出去。
ROLE_LABELS = {
    ROLE_PRIMARY: "主要考查",
    ROLE_SUPPORTING: "辅助用到",
    ROLE_METHOD: "解法采用",
    ROLE_FEATURE: "题面特征",
    ROLE_LEGACY: "旧关联，作用未细分",
}


class QueryError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# 查询解析
# ---------------------------------------------------------------------------


def normalize_query(raw: dict | None) -> dict:
    """把外部传进来的查询整理成一份完整、有版本的表达式。

    缺的地方填默认值，不猜。存下来的搜索条件必须完整到能原样还原。
    """
    data = raw if isinstance(raw, dict) else {}
    version = int(data.get("query_version") or QUERY_VERSION)
    if version > QUERY_VERSION:
        raise QueryError(f"这条搜索条件的版本是 {version}，本机只认到 {QUERY_VERSION}")
    filters: dict[str, Any] = {}
    raw_filters = data.get("filters")
    if isinstance(raw_filters, dict):
        filters = raw_filters
    concept_groups: list[Any] = []
    raw_groups = data.get("concept_groups")
    if isinstance(raw_groups, list):
        concept_groups = raw_groups
    method_groups: list[Any] = []
    raw_methods = data.get("method_groups")
    if isinstance(raw_methods, list):
        method_groups = raw_methods
    exclusions: dict[str, Any] = {}
    raw_exclusions = data.get("exclusions")
    if isinstance(raw_exclusions, dict):
        exclusions = raw_exclusions

    normalized: dict[str, Any] = {
        "query_version": QUERY_VERSION,
        "keywords": [str(item).strip() for item in (data.get("keywords") or []) if str(item).strip()][:16],
        "filters": {
            "source": str(filters.get("source") or ""),
            "textbook": str(filters.get("textbook") or ""),
            "chapter": str(filters.get("chapter") or ""),
            "stage": str(filters.get("stage") or ""),
            "question_type": str(filters.get("question_type") or ""),
            "collection": str(filters.get("collection") or ""),
            "difficulty": str(filters.get("difficulty") or ""),
        },
        "concept_groups": [],
        "method_groups": [],
        "exclusions": {
            "conditions": [],
            "handling_of_unknown": str(exclusions.get("handling_of_unknown") or "separate"),
        },
        "include_clues": bool(data.get("include_clues")),
        "sort": str(data.get("sort") or "recent"),
    }

    for group in concept_groups:
        if not isinstance(group, dict):
            continue
        ids = [str(item) for item in (group.get("ids") or []) if str(item)]
        if not ids:
            continue
        normalized["concept_groups"].append(
            {
                "ids": ids,
                "roles": [str(item) for item in (group.get("roles") or []) if str(item) in ALL_ROLES] or [ROLE_PRIMARY],
                "all_or_any": "all" if str(group.get("all_or_any") or "all") == "all" else "any",
                "include_descendants": bool(group.get("include_descendants")),
            }
        )

    for group in method_groups:
        if not isinstance(group, dict):
            continue
        ids = [str(item) for item in (group.get("ids") or []) if str(item)]
        if not ids:
            continue
        cooccurrence = str(group.get("cooccurrence") or "same_variant")
        if cooccurrence not in {"same_variant", "separate"}:
            raise QueryError("cooccurrence 只能是 same_variant 或 separate")
        normalized["method_groups"].append(
            {
                "ids": ids,
                "subquestion": str(group.get("subquestion") or ""),
                "variant_scope": str(group.get("variant_scope") or ""),
                "cooccurrence": cooccurrence,
            }
        )

    for condition in exclusions.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        ids = [str(item) for item in (condition.get("ids") or []) if str(item)]
        if not ids:
            continue
        normalized["exclusions"]["conditions"].append(
            {
                "ids": ids,
                "roles": [str(item) for item in (condition.get("roles") or []) if str(item) in ALL_ROLES]
                or [ROLE_METHOD],
                "include_descendants": bool(condition.get("include_descendants")),
            }
        )

    if normalized["exclusions"]["handling_of_unknown"] not in {"separate", "exclude"}:
        normalized["exclusions"]["handling_of_unknown"] = "separate"

    return normalized


# ---------------------------------------------------------------------------
# 概念展开
# ---------------------------------------------------------------------------


def descendant_tag_ids(tag_ids: list[str], *, include_descendants: bool) -> set[str]:
    """展开下级概念。

    只扩展检索范围 —— 不给题目复制父级标签，也不说这道题考查了父概念的全部性质。
    """
    result = {str(item) for item in tag_ids}
    if not include_descendants:
        return result
    frontier = list(result)
    seen = set(result)
    while frontier:
        current = frontier.pop()
        children = LibraryTagRelation.objects.filter(
            parent_id=current, relation_type=LibraryTagRelation.Type.HIERARCHY
        ).values_list("child_id", flat=True)
        for child in children:
            # values_list 对 UUID 字段返回的是 UUID 实例，不是字符串。
            # 不转成字符串的话，这个集合里就混着两种类型，比较永远不成立。
            child = str(child)
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return seen


# ---------------------------------------------------------------------------
# 命中判定
# ---------------------------------------------------------------------------


def _assignments_for(publication_ids: list[str]) -> list[LibraryTagAssignment]:
    if not publication_ids:
        return []
    return list(
        LibraryTagAssignment.objects.filter(publication_id__in=publication_ids)
        .select_related("tag", "subquestion", "variant")
        .order_by("created_at", "id")
    )


def _is_confirmed(row: LibraryTagAssignment) -> bool:
    """正式匹配：人工确认过，且依据当前有效。"""
    return row.review_status == LibraryTagAssignment.ReviewStatus.CONFIRMED and not row.needs_review


def _is_legacy(row: LibraryTagAssignment) -> bool:
    """旧记录：人工确认过，但没绑定到具体依据（例如没绑定具体解法）。"""
    return row.evidence_status == LibraryTagAssignment.EvidenceStatus.UNBOUND


def _scope_matches(row: LibraryTagAssignment, *, subquestion: str, variant_scope: str) -> bool:
    """方法范围匹配。

    ``subquestion`` 空表示不限；给了就只认落在那个小问的。
    ``variant_scope`` 空表示不限；给了就只认那个解法分支的。
    """
    if subquestion:
        if row.subquestion_id is None:
            return False
        if str(row.subquestion_id) != str(subquestion):
            # subquestion 可能是展示编号（「1」），也可能是小问的真实 ID。
            # 老师写的是「第（1）问」，而库里存的是 UUID —— 得对得上。
            label = (row.subquestion.display_label if row.subquestion_id else "") or ""
            label = label.strip("（）() \t　")
            key = (row.subquestion.stable_key if row.subquestion_id else "") or ""
            if label != str(subquestion) and key not in {f"sq:{subquestion}", str(subquestion)}:
                return False
    if variant_scope:
        if row.variant_id is None:
            return False
        if str(row.variant_id) != str(variant_scope):
            return False
    return True


def _method_group_match(rows: list[LibraryTagAssignment], group: dict, *, roles: set[str]) -> dict:
    """一组方法条件是否在某些题上成立。

    ``same_variant``：同一个解法分支（可选同一个小问）里同时用到这些方法。
    ``separate``：只要分别存在就行 —— 这是显式要求，不是默认值。

    ``roles`` 决定这次找的是哪一层：

    * ``{ROLE_METHOD}`` —— 精确的「解法采用」。这是真正的方法证明。
    * ``{ROLE_LEGACY}`` —— 旧记录。旧库里绝大多数方法标签没绑定到具体解法，
      它们能说明「曾经标过这个方法」，但不能冒充精确的方法证明。

    两层分开，是为了让「旧标签命中，未定位具体解法」单独计数，
    而不是混进正式匹配数里。
    """
    tag_ids = set(group["ids"])
    picked = [row for row in rows if str(row.tag_id) in tag_ids and row.role in roles]
    if not picked:
        return {"matched": False, "reason": "", "variant_id": "", "subquestion_id": ""}

    if group.get("cooccurrence") == "separate":
        found = {str(row.tag_id) for row in picked}
        if tag_ids.issubset(found):
            sample = picked[0]
            return {
                "matched": True,
                "reason": "分别记录了这些方法（不是同一个解法）",
                "variant_id": str(sample.variant_id or ""),
                "subquestion_id": str(sample.subquestion_id or ""),
            }
        return {"matched": False, "reason": "", "variant_id": "", "subquestion_id": ""}

    # same_variant：按解法分支分组，同一分支内要集齐全部方法。
    buckets: dict[str, list[LibraryTagAssignment]] = {}
    for row in picked:
        if not _scope_matches(
            row, subquestion=group.get("subquestion") or "", variant_scope=group.get("variant_scope") or ""
        ):
            continue
        buckets.setdefault(str(row.variant_id or ""), []).append(row)
    for variant_id, bucket in buckets.items():
        found = {str(row.tag_id) for row in bucket}
        if tag_ids.issubset(found):
            sample = bucket[0]
            return {
                "matched": True,
                "reason": "同一个解法里用到了这些方法",
                "variant_id": variant_id,
                "subquestion_id": str(sample.subquestion_id or ""),
            }
    return {"matched": False, "reason": "", "variant_id": "", "subquestion_id": ""}


def _concept_group_match(rows: list[LibraryTagAssignment], group: dict, expanded: dict[str, set[str]]) -> dict:
    """一组概念条件。``all`` 要全中，``any`` 命中一个就行。

    只认已确认、依据当前有效的关联 —— 没核对过的候选不是正式匹配。

    ``expanded`` 给出每个被问的概念「自身 + 下级」的范围。开了「包含下级」时，
    父概念自身或它的任一下级被考查，就算父概念这一条成立。
    这只是把检索范围放宽，不给题目复制父级标签。
    """
    allowed: set[str] = set()
    for wanted in group["ids"]:
        allowed |= expanded.get(wanted, {wanted})
    roles = set(group["roles"])
    picked = [row for row in rows if str(row.tag_id) in allowed and row.role in roles and _is_confirmed(row)]
    if not picked:
        # 压根没这条关联 → 不匹配；有，但还没确认 → **未知**。
        # 这两件事混在一起，「未整理」就成了一个没有意义的数字。
        pending = [row for row in rows if str(row.tag_id) in allowed and row.role in roles]
        return {"matched": False, "pending": bool(pending), "roles": [], "tag_ids": []}
    found_tags = {str(row.tag_id) for row in picked}
    if group["all_or_any"] == "all":
        for wanted in group["ids"]:
            if not (expanded.get(wanted, {wanted}) & found_tags):
                return {"matched": False, "pending": False, "roles": [], "tag_ids": []}
    return {
        "matched": True,
        "pending": False,
        "roles": sorted({row.role for row in picked}),
        "tag_ids": sorted(found_tags),
        "names": sorted({row.tag.name for row in picked}),
    }


def _exclusion_state(rows: list[LibraryTagAssignment], condition: dict) -> str:
    """排除条件在某题上的三态：命中／确认不匹配／未知。

    「未知」就是「还没查过」或「查过但依据已失效」——
    不能当成「确认没用」。这是排除条件最容易骗人的地方。
    """
    tag_ids = descendant_tag_ids(condition["ids"], include_descendants=condition.get("include_descendants", False))
    roles = set(condition.get("roles") or [ROLE_METHOD])
    picked = [row for row in rows if str(row.tag_id) in tag_ids and row.role in roles]
    if not picked:
        return "unknown"
    if any(_is_confirmed(row) for row in picked):
        return "hit"
    if any(_is_legacy(row) for row in picked):
        return "legacy"
    return "unknown"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_query(raw_query: dict | None, *, limit: int = 200, offset: int = 0) -> dict:
    """执行一次检索。

    返回的每条结果都带 ``reasons``：为什么命中。计数分四类：
    ``confirmed``（已确认匹配）、``legacy``（旧记录命中）、
    ``clue``（待核对线索）、``unknown``（排除条件下的未知）。
    """
    query = normalize_query(raw_query)
    publications = list(
        PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).order_by("-published_at")
    )
    if not publications:
        return _empty_result(query)

    ids = [str(row.pk) for row in publications]
    by_id = {str(row.pk): row for row in publications}

    # ---- 关键词与基础筛选 -------------------------------------------------
    candidates: list[str] = ids
    keywords = query["keywords"]
    if keywords:
        needle = " ".join(keywords).strip()
        candidates = [pid for pid in candidates if needle in (by_id[pid].search_text or "")]

    filters = query["filters"]
    if filters["question_type"]:
        candidates = [pid for pid in candidates if by_id[pid].question_type == filters["question_type"]]
    if filters["source"]:
        candidates = [pid for pid in candidates if by_id[pid].source_filename == filters["source"]]
    if filters["collection"]:
        member_ids = {
            str(row.publication_id)
            for row in LibraryCollectionMember.objects.filter(collection_id=filters["collection"])
        }
        candidates = [pid for pid in candidates if pid in member_ids]
    if filters["chapter"]:
        placed = {
            str(row.publication_id) for row in LibraryQuestionPlacement.objects.filter(chapter_id=filters["chapter"])
        }
        candidates = [pid for pid in candidates if pid in placed]
    if filters["textbook"]:
        chapter_ids = {
            str(row.id)
            for row in LibraryChapter.objects.filter(textbook_id=filters["textbook"]).values_list("id", flat=True)
        }
        if chapter_ids:
            placed = {
                str(row.publication_id) for row in LibraryQuestionPlacement.objects.filter(chapter_id__in=chapter_ids)
            }
            candidates = [pid for pid in candidates if pid in placed]

    rows = _assignments_for(candidates)
    by_publication: dict[str, list[LibraryTagAssignment]] = {}
    for row in rows:
        by_publication.setdefault(str(row.publication_id), []).append(row)

    # ---- 正向条件判定：已确认匹配 / 旧记录 / 未知 ------------------------
    #
    # 三态必须分清：
    #   已确认匹配 —— 有满足条件、依据当前有效的正式关联。
    #   旧记录命中 —— 有旧记录，但没绑定到具体解法，不能冒充精确方法证明。
    #   未知       —— 还没整理、依据需复核，或者方法依据没绑定。
    #
    # 「未知」和「已确认不匹配」的区别在排除条件那边最要命：
    # 排除一个方法时，未知不能当成「确认没用」。
    confirmed_ids: list[str] = []
    legacy_ids: list[str] = []
    unknown_ids: list[str] = []
    concept_results: dict[str, list[dict]] = {}
    method_results: dict[str, list[dict]] = {}
    legacy_method_results: dict[str, list[dict]] = {}

    # 「自身 + 下级」的展开范围按组算一次，别按题算 —— 目录上千道题时这是差一个数量级。
    expanded_by_group = []
    for group in query["concept_groups"]:
        expanded_by_group.append(
            {
                wanted: descendant_tag_ids([wanted], include_descendants=group["include_descendants"])
                for wanted in group["ids"]
            }
        )

    for pid in candidates:
        rows_here = by_publication.get(pid, [])

        concept_ok = True
        concept_pending = False
        for group, expanded in zip(query["concept_groups"], expanded_by_group):
            matched = _concept_group_match(rows_here, group, expanded)
            if not matched["matched"]:
                concept_ok = False
                concept_pending = concept_pending or bool(matched.get("pending"))
                break
            concept_results.setdefault(pid, []).append(matched)
        if not concept_ok:
            # 不匹配就不该出现在结果里。只有「有这条关联但还没确认」才算未知。
            if concept_pending:
                unknown_ids.append(pid)
            continue

        method_ok = True
        legacy_hit = False
        method_pending = False
        for group in query["method_groups"]:
            exact = _method_group_match(rows_here, group, roles={ROLE_METHOD})
            if exact["matched"]:
                method_results.setdefault(pid, []).append(exact)
                continue
            method_ok = False
            old_record = _method_group_match(rows_here, group, roles={ROLE_LEGACY})
            if old_record["matched"]:
                legacy_method_results.setdefault(pid, []).append(old_record)
                legacy_hit = True
                continue
            # 有这个方法的关联，但还没确认依据 —— 未知，不是「没这个方法」。
            unconfirmed = [
                row
                for row in rows_here
                if str(row.tag_id) in set(group["ids"]) and row.role == ROLE_METHOD and not _is_confirmed(row)
            ]
            method_pending = method_pending or bool(unconfirmed)
        if not method_ok:
            if legacy_hit:
                legacy_ids.append(pid)
            elif method_pending:
                unknown_ids.append(pid)
            continue

        if any(_is_confirmed(row) for row in rows_here):
            confirmed_ids.append(pid)
        elif any(_is_legacy(row) for row in rows_here):
            legacy_ids.append(pid)
        else:
            unknown_ids.append(pid)

    # ---- 排除条件（三态） ------------------------------------------------
    excluded_ids: list[str] = []
    if query["exclusions"]["conditions"]:
        keep_confirmed = []
        keep_legacy = []
        for pid in confirmed_ids:
            states = [_exclusion_state(by_publication.get(pid, []), item) for item in query["exclusions"]["conditions"]]
            if "hit" in states:
                excluded_ids.append(pid)
            elif "legacy" in states:
                keep_legacy.append(pid)
            elif "unknown" in states:
                keep_confirmed.append(pid)
                unknown_ids.append(pid)
            else:
                keep_confirmed.append(pid)
        confirmed_ids = keep_confirmed
        legacy_ids = keep_legacy + [pid for pid in legacy_ids if pid not in excluded_ids]

        if query["exclusions"]["handling_of_unknown"] == "exclude":
            drop = set()
            for pid in confirmed_ids:
                states = [
                    _exclusion_state(by_publication.get(pid, []), item) for item in query["exclusions"]["conditions"]
                ]
                if "unknown" in states:
                    drop.add(pid)
            confirmed_ids = [pid for pid in confirmed_ids if pid not in drop]
            unknown_ids = [pid for pid in unknown_ids if pid not in drop] + list(drop)

    # ---- 待核对线索 ------------------------------------------------------
    clue_ids: list[str] = []
    if query["include_clues"]:
        pending = {
            str(row.publication_id)
            for row in LibraryTagSuggestion.objects.filter(status=LibraryTagSuggestion.Status.PENDING)
        }
        for pid in confirmed_ids + legacy_ids:
            if pid not in confirmed_ids and pid in pending:
                clue_ids.append(pid)

    # ---- 排序与分页 ------------------------------------------------------
    order = _sort_key(query["sort"])
    result_ids = confirmed_ids + legacy_ids
    # 没有排除条件时，「未整理」的题也要看得见 —— 空查询就是「全部」。
    # 它们单独一区，不混进正式匹配数。
    unreviewed_ids: list[str] = []
    # 「未整理」这一区只在没有正向条件时才有意义：空查询就是「全部」，
    # 于是没整理过的题也能看见。有条件时，不满足条件的题是不匹配，不该冒出来。
    has_filters = any(str(value or "").strip() for value in query["filters"].values())
    has_positive = bool(query["concept_groups"] or query["method_groups"] or has_filters or query["keywords"])
    if not has_positive and not query["exclusions"]["conditions"]:
        unreviewed_ids = [pid for pid in candidates if pid not in set(result_ids)]
        result_ids = result_ids + unreviewed_ids
    result_ids = sorted(result_ids, key=order)
    total = len(result_ids)
    page = result_ids[offset : offset + limit]

    items = []
    for pid in page:
        reasons = _reasons_for(
            pid,
            by_publication.get(pid, []),
            concept_results.get(pid, []),
            method_results.get(pid, []) + legacy_method_results.get(pid, []),
            by_id[pid],
        )
        if pid in confirmed_ids:
            bucket = "confirmed"
        elif pid in legacy_ids:
            bucket = "legacy"
        else:
            bucket = "unreviewed"
        items.append(
            {
                "publication_id": pid,
                "number": by_id[pid].number,
                "question_type": by_id[pid].question_type,
                "source_filename": by_id[pid].source_filename,
                "bucket": bucket,
                "reasons": reasons,
            }
        )

    return {
        "query": query,
        "items": items,
        "count": len(confirmed_ids),
        "total_count": total,
        "confirmed_count": len(confirmed_ids),
        "legacy_count": len(legacy_ids),
        "clue_count": len(clue_ids),
        "unknown_count": len(set(unknown_ids)),
        "unreviewed_count": len(unreviewed_ids),
        "excluded_count": len(excluded_ids),
    }


def _empty_result(query: dict) -> dict:
    return {
        "query": query,
        "items": [],
        "count": 0,
        "total_count": 0,
        "confirmed_count": 0,
        "legacy_count": 0,
        "clue_count": 0,
        "unknown_count": 0,
        "excluded_count": 0,
    }


def _sort_key(sort: str):
    if sort == "number":
        return lambda pid: (pid,)
    return lambda pid: pid


def _reasons_for(
    pid: str,
    rows: list[LibraryTagAssignment],
    concepts: list[dict],
    methods: list[dict],
    publication: PublishedQuestion,
) -> list[dict]:
    """一条结果为什么命中。界面上要能点进去看依据。"""
    reasons: list[dict] = []
    for group in concepts:
        reasons.append(
            {
                "kind": "concept",
                "text": "概念匹配：" + "、".join(group.get("names") or []),
                "roles": group.get("roles") or [],
                "tag_ids": group.get("tag_ids") or [],
                "scope_keys": sorted(
                    {row.scope_key for row in rows if str(row.tag_id) in set(group.get("tag_ids") or [])}
                ),
            }
        )
    for group in methods:
        reasons.append(
            {
                "kind": "method",
                "text": group.get("reason") or "方法匹配",
                "variant_id": group.get("variant_id") or "",
                "subquestion_id": group.get("subquestion_id") or "",
                "tag_ids": [],
            }
        )
    for row in rows:
        if _is_legacy(row):
            reasons.append(
                {
                    "kind": "legacy",
                    "text": f"旧标签命中，未定位具体解法：{row.tag.name}",
                    "roles": [row.role],
                    "tag_ids": [str(row.tag_id)],
                    "scope_keys": [row.scope_key],
                }
            )
    if not reasons:
        # 没有具体条件时（或者条件全在别处命中了），也要说清这道题上有什么。
        # 「为什么这条在这儿」不能空着 —— 空着老师只能自己一题题翻。
        confirmed = [row for row in rows if _is_confirmed(row)]
        if confirmed:
            reasons.append(
                {
                    "kind": "tags",
                    "text": "已确认的关联："
                    + "、".join(f"{row.tag.name}（{ROLE_LABELS.get(str(row.role), row.role)}）" for row in confirmed),
                    "roles": sorted({str(row.role) for row in confirmed}),
                    "tag_ids": [str(row.tag_id) for row in confirmed],
                    "scope_keys": sorted({row.scope_key for row in confirmed}),
                }
            )
        else:
            reasons.append(
                {
                    "kind": "unreviewed",
                    "text": "这道题还没整理过 —— 不是「没有」，只是还没查。",
                    "roles": [],
                    "tag_ids": [],
                    "scope_keys": [],
                }
            )
    return reasons


# ---------------------------------------------------------------------------
# 保存搜索
# ---------------------------------------------------------------------------


def save_search(name: str, query: dict) -> dict:
    """保存完整查询条件。

    只存条件，不存结果列表 —— 条件变了结果就该跟着变。
    """
    text = str(name or "").strip()[:120]
    if not text:
        raise QueryError("这个搜索得有个名字")
    normalized = normalize_query(query)
    row, created = LibrarySavedSearch.objects.update_or_create(
        name=text, defaults={"query": normalized, "query_version": normalized["query_version"]}
    )
    return {"ok": True, "created": created, "id": str(row.pk), "name": row.name}


def list_saved_searches() -> list[dict]:
    return [
        {
            "id": str(row.pk),
            "name": row.name,
            "query_version": row.query_version,
            "query": row.query,
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }
        for row in LibrarySavedSearch.objects.all().order_by("name")
    ]


def delete_saved_search(saved_id: str) -> dict:
    deleted, _ = LibrarySavedSearch.objects.filter(pk=saved_id).delete()
    return {"ok": True, "deleted": deleted}


# ---------------------------------------------------------------------------
# 辅助：给界面用的目录
# ---------------------------------------------------------------------------


def subquestion_options(publication_id: str) -> list[dict]:
    return [
        {"id": str(row.pk), "label": row.display_label or row.stable_key, "stable_key": row.stable_key}
        for row in LibrarySubquestion.objects.filter(publication_id=publication_id).order_by("position", "id")
    ]


def variant_options(publication_id: str) -> list[dict]:
    return [
        {"id": str(row.pk), "name": row.name, "origin": row.origin, "status": row.status}
        for row in LibrarySolutionVariant.objects.filter(publication_id=publication_id).order_by(
            "display_order", "created_at", "id"
        )
    ]
