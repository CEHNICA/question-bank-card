"""新标签系统的服务层：概念身份、用途关联、教材目录、专题、小问与解法分支。

和 ``library_tagging`` 的分工：

* ``library_tagging`` 管「目录里有哪些词」「旧协议怎么贴标」，历史包袱都在那边；
* 这里管「这条关联是什么用途」「落在哪个范围」「依据现在还成不成立」。

两条写库路径都走事务、修订检查、幂等请求和操作历史。正式写入只有一个入口，
前端不许绕过服务层直接改库。

几条不能破的规矩：

* 用途（主要考查／辅助用到／解法采用／题面特征）记在关联上，不记在概念身份上。
  同一个「分类讨论」在不同题里可以是主考点，也可以只是解法手段。
* 范围必须落成规范化键（``scope_key``），不能是 ``None`` —— SQLite 的 NULL
  不参与唯一约束，那样整题记录会重复。
* 小问、解法分支、证据必须属于同一道题，跨题绑定服务端直接拒绝。
* 没贴标签不代表没有该知识或方法；没检查过也不代表检查过没有。
"""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from .models import (
    LibraryAssertionEvidence,
    LibraryChapter,
    LibraryCollection,
    LibraryCollectionMember,
    LibraryCoverage,
    LibrarySolutionVariant,
    LibrarySubquestion,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagOperation,
    LibraryTagRelation,
    LibraryTextbook,
    PublishedQuestion,
)
from .textnorm import concept_key

# ---------------------------------------------------------------------------
# 范围键
# ---------------------------------------------------------------------------

SCOPE_WHOLE = "whole"


def scope_key_for(*, whole: bool = True, subquestion=None, variant=None) -> str:
    """把范围压成一个非空字符串。

    这是唯一性约束的组成部分，也是搜索时判断「同一小问、同一解法」的依据。
    用字符串而不是 NULL：SQLite 认为 NULL 不等于 NULL，约束会漏。

    * 整题：``whole``
    * 某小问：``sub:<小问稳定ID>``
    * 某解法分支：``variant:<分支ID>``
    * 某小问的某解法：``sub:<小问ID>+variant:<分支ID>``
    """
    parts: list[str] = []
    if not whole:
        if subquestion is None:
            raise ValueError("不是整题就必须给出小问")
        parts.append(f"sub:{subquestion.pk if hasattr(subquestion, 'pk') else subquestion}")
    if variant is not None:
        parts.append(f"variant:{variant.pk if hasattr(variant, 'pk') else variant}")
    if not parts:
        return SCOPE_WHOLE
    return "+".join(parts)


def parse_scope_key(scope_key: str) -> dict:
    """把范围键拆回结构。搜索和界面都要读它。"""
    text = str(scope_key or SCOPE_WHOLE)
    result = {"whole": True, "subquestion_id": "", "variant_id": ""}
    if text == SCOPE_WHOLE:
        return result
    for piece in text.split("+"):
        if piece.startswith("sub:"):
            result["whole"] = False
            result["subquestion_id"] = piece[4:]
        elif piece.startswith("variant:"):
            result["variant_id"] = piece[8:]
    return result


# ---------------------------------------------------------------------------
# 概念身份
# ---------------------------------------------------------------------------


def canonical_key_for(name: str) -> str:
    """概念的规范身份键。空名不算概念。"""
    return concept_key(name)


def search_concepts(query: str, *, limit: int = 24) -> list[dict]:
    """按名字和别名找概念。

    命中多个同名概念时不默认选第一个 —— 每条都带上范围说明，让界面显示歧义。
    """
    text = str(query or "").strip()
    if not text:
        return []
    key = canonical_key_for(text)
    rows = []
    for tag in LibraryTag.objects.filter(active=True).order_by("name", "id"):
        names = [tag.name, *(tag.aliases or [])]
        keys = [canonical_key_for(name) for name in names if name]
        exact = key in keys
        hit = exact or any(key and key in candidate for candidate in keys)
        if not hit:
            continue
        rows.append(
            {
                "id": str(tag.pk),
                "name": tag.name,
                "kind": tag.kind,
                "type_hints": list(tag.type_hints or []),
                "scope_note": tag.scope_note,
                "aliases": list(tag.aliases or []),
                "description": tag.description,
                "match": "exact" if exact else "alias",
                "ambiguous": False,
            }
        )
        if len(rows) >= limit * 4:
            break
    # 同名概念（规范名归一化后相同）标成歧义，由界面提示，不自动合并。
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(canonical_key_for(row["name"]), []).append(row)
    for group in by_key.values():
        if len(group) > 1:
            for row in group:
                row["ambiguous"] = True
    rows.sort(key=lambda row: (0 if row["match"] == "exact" else 1, row["name"]))
    return rows[:limit]


def find_concepts_by_name(name: str) -> list[LibraryTag]:
    """精确命中规范名或别名的概念。多个都返回，不替用户挑。"""
    key = canonical_key_for(name)
    if not key:
        return []
    hits = []
    for tag in LibraryTag.objects.filter(active=True).order_by("name", "id"):
        names = [tag.name, *(tag.aliases or [])]
        if key in [canonical_key_for(item) for item in names if item]:
            hits.append(tag)
    return hits


def concept_json(tag: LibraryTag, *, count: int | None = None) -> dict:
    return {
        "id": str(tag.pk),
        "name": tag.name,
        "kind": tag.kind,
        "type_hints": list(tag.type_hints or []),
        "scope_note": tag.scope_note,
        "aliases": list(tag.aliases or []),
        "description": tag.description,
        "active": tag.active,
        "favorite": tag.favorite,
        "count": count,
    }


# ---------------------------------------------------------------------------
# 小问与解法分支
# ---------------------------------------------------------------------------


def list_subquestions(publication: PublishedQuestion) -> list[dict]:
    rows = LibrarySubquestion.objects.filter(publication=publication).order_by("position", "id")
    return [
        {
            "id": str(row.pk),
            "stable_key": row.stable_key,
            "display_label": row.display_label,
            "position": row.position,
            "locator": row.locator or {},
        }
        for row in rows
    ]


def ensure_subquestions(publication: PublishedQuestion) -> list[LibrarySubquestion]:
    """按题干文本拆出小问，拆不出就整题记录 —— 不虚构编号。

    编号靠 ``qtypes.subquestion_labels`` 认，但落库时给的是稳定键：
    以后小问重排，编号变了，稳定键不变，绑定不会跟着跑到别的小问去。
    """
    from . import qtypes

    stem = str((publication.content or {}).get("stem") or "")
    labels = qtypes.subquestion_labels(stem)
    existing = {row.stable_key: row for row in LibrarySubquestion.objects.filter(publication=publication)}
    if not labels:
        return list(existing.values())
    for index, label in enumerate(labels):
        stable_key = f"sq:{index + 1}"
        row = existing.get(stable_key)
        if row is None:
            row = LibrarySubquestion.objects.create(
                publication=publication,
                stable_key=stable_key,
                display_label=f"（{label}）",
                position=index,
                locator={"text_label": str(label)},
            )
        elif row.display_label != f"（{label}）":
            # 编号变了就改显示名，稳定键不动。老绑定留在原来的小问上。
            row.display_label = f"（{label}）"
            row.save(update_fields=["display_label"])
    return list(LibrarySubquestion.objects.filter(publication=publication).order_by("position", "id"))


def list_variants(publication: PublishedQuestion) -> list[dict]:
    """解法分支列表。

    界面写「已记录的其他解法」，不写「所有解法」——
    没有任何一套流程能证明已经穷尽这道题的全部解法。
    """
    from .models import LibrarySolution

    rows = LibrarySolutionVariant.objects.filter(publication=publication).order_by("display_order", "created_at", "id")
    result = []
    for row in rows:
        result.append(
            {
                "id": str(row.pk),
                "name": row.name,
                "origin": row.origin,
                "status": row.status,
                "scope": row.scope or {},
                "solution_ids": [
                    str(item.pk) for item in LibrarySolution.objects.filter(variant=row).order_by("-created_at")
                ],
            }
        )
    return result


def ensure_variant(publication: PublishedQuestion, name: str, *, origin: str = "saved") -> LibrarySolutionVariant:
    """按名字取或建解法分支。同名不重复建。"""
    text = str(name or "").strip()[:120]
    if not text:
        raise ValueError("解法分支需要一个名字")
    row, _ = LibrarySolutionVariant.objects.get_or_create(
        publication=publication, name=text, defaults={"origin": origin, "status": LibrarySolutionVariant.Status.ACTIVE}
    )
    return row


# ---------------------------------------------------------------------------
# 关联（断言）
# ---------------------------------------------------------------------------


class AssertionError_(ValueError):
    """关联写不进去。名字带下划线是为了不跟 models 里的概念冲突。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _same_publication(publication: PublishedQuestion, *, subquestion=None, variant=None) -> None:
    """小问和分支必须属于这道题。跨题绑定直接拒绝。"""
    if subquestion is not None and subquestion.publication_id != publication.pk:
        raise AssertionError_("小问不属于这道题，不能绑到这里")
    if variant is not None and variant.publication_id != publication.pk:
        raise AssertionError_("解法分支不属于这道题，不能绑到这里")


def attach_assertion(payload: dict) -> dict:
    """建立一条正式关联：用途 + 范围 + 依据。

    这是唯一的正式写入口之一（另一个是目录写入）。幂等靠 client_request_id，
    冲突靠 expected_revision。
    """
    from . import library_tagging

    publication = PublishedQuestion.objects.filter(pk=payload.get("publication_id")).first()
    if publication is None:
        raise AssertionError_("找不到这道题", 404)

    tag_ids = payload.get("tag_ids") or []
    if not isinstance(tag_ids, list) or not tag_ids:
        raise AssertionError_("没说要贴哪个概念")
    tags = list(LibraryTag.objects.filter(pk__in=tag_ids))
    if len(tags) != len({str(item) for item in tag_ids}):
        raise AssertionError_("有概念不存在")

    role = str(payload.get("role") or LibraryTagAssignment.Role.PRIMARY)
    if role not in dict(LibraryTagAssignment.Role.choices):
        raise AssertionError_(f"用途 {role} 不认")
    if role == LibraryTagAssignment.Role.METHOD and not payload.get("variant_id"):
        raise AssertionError_("「解法采用」必须指明是哪个解法分支")

    subquestion = None
    if payload.get("subquestion_id"):
        subquestion = LibrarySubquestion.objects.filter(pk=payload["subquestion_id"]).first()
        if subquestion is None:
            raise AssertionError_("小问不存在", 404)
    variant = None
    if payload.get("variant_id"):
        variant = LibrarySolutionVariant.objects.filter(pk=payload["variant_id"]).first()
        if variant is None:
            raise AssertionError_("解法分支不存在", 404)
    _same_publication(publication, subquestion=subquestion, variant=variant)

    scope_key = scope_key_for(whole=not payload.get("subquestion_id"), subquestion=subquestion, variant=variant)

    request_id = payload.get("client_request_id")
    if not request_id:
        raise AssertionError_("缺 client_request_id，重复提交会贴两次")
    request_hash = library_tagging._request_hash(payload)
    existing = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AssertionError_("同一个请求 ID 带了不同的内容", 409)
        return {"ok": True, "replayed": True, "operation": str(existing.pk)}

    expected_revision = library_tagging._expected_revision(payload.get("expected_revision"))
    with transaction.atomic():
        library_tagging._bump_publication(publication, expected_revision)
        created = []
        for tag in tags:
            row, was_created = LibraryTagAssignment.objects.get_or_create(
                publication=publication,
                tag=tag,
                role=role,
                scope_key=scope_key,
                defaults={
                    "scope": payload.get("scope") or {"whole": not payload.get("subquestion_id")},
                    "source": LibraryTagAssignment.Source.HUMAN,
                    "rationale": str(payload.get("rationale") or "")[:500],
                    "evidence": str(payload.get("evidence") or "")[:500],
                    "review_status": LibraryTagAssignment.ReviewStatus.CONFIRMED,
                    "evidence_status": LibraryTagAssignment.EvidenceStatus.CURRENT,
                    "variant": variant,
                    "subquestion": subquestion,
                    "source_snapshot": {"scope_key": scope_key, "role": role},
                },
            )
            if was_created:
                created.append(row)
            _write_evidence(row, publication, payload, variant=variant)
        operation = LibraryTagOperation.objects.create(
            client_request_id=uuid.UUID(str(request_id)),
            request_hash=request_hash,
            action="attach_assertion",
            publication_ids=[str(publication.pk)],
            before_snapshot={},
            after_snapshot={
                "role": role,
                "scope_key": scope_key,
                "tag_ids": [str(tag.pk) for tag in tags],
                "variant_id": str(variant.pk) if variant else "",
                "subquestion_id": str(subquestion.pk) if subquestion else "",
                # 记下每条关联的编号和当时的模样，撤销的时候才知道
                # 该动哪一条，以及它有没有被别人改过。
                "assignment_ids": [str(row.pk) for row in created],
                "assignments": [
                    {"id": str(row.pk), "tag_id": str(row.tag_id), "role": row.role, "scope_key": row.scope_key}
                    for row in created
                ],
            },
        )
        library_tagging._sync_projection(publication.pk)
    return {
        "ok": True,
        "replayed": False,
        "created": [str(row.pk) for row in created],
        "operation": str(operation.pk),
        "scope_key": scope_key,
    }


def _write_evidence(
    assignment: LibraryTagAssignment, publication: PublishedQuestion, payload: dict, *, variant=None
) -> None:
    """依据必须能回跳到题面或解析，不能只存一句与原文脱离的总结。"""
    excerpt = str(payload.get("evidence_excerpt") or payload.get("evidence") or "")
    locator = payload.get("evidence_locator") or {}
    solution = None
    if payload.get("solution_id"):
        from .models import LibrarySolution

        solution = LibrarySolution.objects.filter(pk=payload["solution_id"]).first()
        if solution is not None and solution.publication_id != publication.pk:
            solution = None
    if not excerpt and solution is None:
        return
    LibraryAssertionEvidence.objects.create(
        assignment=assignment,
        publication=publication,
        solution=solution,
        excerpt=excerpt[:4000],
        locator=locator,
        snapshot_digest=str(publication.content_hash or "")[:64],
    )


def undo_assertion(payload: dict) -> dict:
    """撤销一次关联改动。

    撤销不是「再点一次删除」—— 它有真正的逆操作，而且带冲突检查：

    * 撤销「贴标签」→ 把那几条关联移除；如果其中一条已经被人改过或删了，
      这次撤销就**不**做，并说清楚哪一条对不上。
    * 撤销「移除」→ 按快照把那条关联重新建回来；如果同一个位置已经有别的
      关联了，同样不做。

    撤销本身也记一条操作，所以能一直往回退。
    """
    from . import library_tagging

    operation = LibraryTagOperation.objects.filter(pk=payload.get("operation_id")).first()
    if operation is None:
        raise AssertionError_("找不到这次操作", 404)
    if operation.undone_at:
        raise AssertionError_("这次操作已经撤销过了", 409)
    if operation.action not in {"attach_assertion", "detach_assertion"}:
        raise AssertionError_("这次操作不是贴标签，不能在这儿撤销")

    request_id = payload.get("client_request_id")
    if not request_id:
        raise AssertionError_("缺 client_request_id")
    request_hash = library_tagging._request_hash(payload)
    existing = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AssertionError_("同一个请求 ID 带了不同的内容", 409)
        return {"ok": True, "replayed": True, "operation": str(existing.pk)}

    expected_revision = library_tagging._expected_revision(payload.get("expected_revision"))
    with transaction.atomic():
        if operation.action == "attach_assertion":
            snapshot = operation.after_snapshot or {}
            ids = [str(item) for item in snapshot.get("assignment_ids") or []]
            rows = list(LibraryTagAssignment.objects.filter(pk__in=ids)) if ids else []
            if len(rows) != len(ids):
                raise AssertionError_("这次贴的标签已经被改过或删掉了，撤销会盖掉别人的改动，所以没做", 409)
            # 逐条比对当时的模样：行还在但内容被改过，同样不能撤销。
            expected = {str(item.get("id")): item for item in snapshot.get("assignments") or []}
            for row in rows:
                want = expected.get(str(row.pk))
                if want is None:
                    continue
                if (
                    str(row.tag_id) != str(want.get("tag_id"))
                    or row.role != want.get("role")
                    or row.scope_key != want.get("scope_key")
                ):
                    raise AssertionError_("这次贴的标签已经被改过，撤销会盖掉那个改动，所以没做", 409)
            publication = rows[0].publication if rows else None
            if publication is not None:
                library_tagging._bump_publication(publication, expected_revision)
            for row in rows:
                row.delete()
            if publication is not None:
                library_tagging._sync_projection(publication.pk)
            inverse = {"action": "attach_assertion", "assignment_ids": ids}
        else:
            snapshot = operation.before_snapshot or {}
            publication = PublishedQuestion.objects.filter(pk=(operation.publication_ids or [""])[0]).first()
            if publication is None:
                raise AssertionError_("找不到这道题", 404)
            library_tagging._bump_publication(publication, expected_revision)
            tag = LibraryTag.objects.filter(pk=snapshot.get("tag_id")).first()
            if tag is None:
                raise AssertionError_("原来那个概念已经不在了，撤销没做", 409)
            scope_key = str(snapshot.get("scope_key") or "whole")
            if LibraryTagAssignment.objects.filter(
                publication=publication, tag=tag, role=snapshot.get("role"), scope_key=scope_key
            ).exists():
                raise AssertionError_("同一个位置已经又有别的关联了，撤销会盖掉它，所以没做", 409)
            row = LibraryTagAssignment.objects.create(
                publication=publication,
                tag=tag,
                scope={"whole": scope_key == "whole"},
                scope_key=scope_key,
                role=snapshot.get("role") or LibraryTagAssignment.Role.LEGACY,
                source=snapshot.get("source") or LibraryTagAssignment.Source.HUMAN,
                rationale=str(snapshot.get("rationale") or "")[:500],
                review_status=LibraryTagAssignment.ReviewStatus.CONFIRMED,
                evidence_status=LibraryTagAssignment.EvidenceStatus.CURRENT,
                variant=LibrarySolutionVariant.objects.filter(pk=snapshot.get("variant_id")).first()
                if snapshot.get("variant_id")
                else None,
                subquestion=LibrarySubquestion.objects.filter(pk=snapshot.get("subquestion_id")).first()
                if snapshot.get("subquestion_id")
                else None,
            )
            library_tagging._sync_projection(publication.pk)
            inverse = {"action": "detach_assertion", "assignment_id": str(row.pk)}

        operation.undone_at = timezone.now()
        operation.save(update_fields=["undone_at"])
        LibraryTagOperation.objects.create(
            client_request_id=uuid.UUID(str(request_id)),
            request_hash=request_hash,
            action="undo_assertion",
            publication_ids=operation.publication_ids,
            before_snapshot=dict(operation.after_snapshot or {}),
            after_snapshot=inverse,
        )
    return {"ok": True, "replayed": False, "undone": operation.action}


def recent_assertion_operations(publication_id: str, *, limit: int = 20) -> list[dict]:
    """一道题上的关联改动历史。界面用它摆「撤销」。"""
    rows = LibraryTagOperation.objects.filter(
        publication_ids__contains=str(publication_id),
        action__in=["attach_assertion", "detach_assertion", "undo_assertion"],
    ).order_by("-created_at")[:limit]
    return [
        {
            "id": str(row.pk),
            "action": row.action,
            "undone": bool(row.undone_at),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


def detach_assertion(payload: dict) -> dict:
    """移除一条关联。范围和用途要一起指明，不能整题一锅端。"""
    from . import library_tagging

    assignment = LibraryTagAssignment.objects.filter(pk=payload.get("assignment_id")).first()
    if assignment is None:
        raise AssertionError_("找不到这条关联", 404)
    request_id = payload.get("client_request_id")
    if not request_id:
        raise AssertionError_("缺 client_request_id")
    request_hash = library_tagging._request_hash(payload)
    existing = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise AssertionError_("同一个请求 ID 带了不同的内容", 409)
        return {"ok": True, "replayed": True}

    expected_revision = library_tagging._expected_revision(payload.get("expected_revision"))
    with transaction.atomic():
        library_tagging._bump_publication(assignment.publication, expected_revision)
        snapshot = {
            "assignment_id": str(assignment.pk),
            "tag_id": str(assignment.tag_id),
            "role": assignment.role,
            "scope_key": assignment.scope_key,
            "variant_id": str(assignment.variant_id or ""),
            "subquestion_id": str(assignment.subquestion_id or ""),
            # 撤销要按原样建回来，所以来源和理由也得留着。
            "source": assignment.source,
            "rationale": assignment.rationale,
        }
        assignment.delete()
        operation = LibraryTagOperation.objects.create(
            client_request_id=uuid.UUID(str(request_id)),
            request_hash=request_hash,
            action="detach_assertion",
            publication_ids=[str(assignment.publication_id)],
            before_snapshot=snapshot,
            after_snapshot={},
        )
        library_tagging._sync_projection(assignment.publication_id)
    return {"ok": True, "operation": str(operation.pk)}


def list_assertions(publication: PublishedQuestion) -> list[dict]:
    """一道题的全部关联，带用途和范围。界面按这个渲染。"""
    rows = (
        LibraryTagAssignment.objects.filter(publication=publication)
        .select_related("tag", "variant", "subquestion")
        .order_by("created_at", "id")
    )
    result = []
    for row in rows:
        parsed = parse_scope_key(row.scope_key)
        result.append(
            {
                "id": str(row.pk),
                "tag_id": str(row.tag_id),
                "tag_name": row.tag.name,
                "role": row.role,
                "scope_key": row.scope_key,
                "whole": parsed["whole"],
                "subquestion_id": parsed["subquestion_id"],
                # 给人看的名字，不是内部 ID。小问和解法分支都得说清楚是哪一个。
                "subquestion_label": row.subquestion.display_label if row.subquestion_id else "",
                "variant_id": parsed["variant_id"],
                "variant_name": row.variant.name if row.variant_id else "",
                "review_status": row.review_status,
                "evidence_status": row.evidence_status,
                "source": row.source,
                "rationale": row.rationale,
                "evidence": row.evidence,
                "needs_review": row.needs_review,
                "evidences": [
                    {
                        "id": str(item.pk),
                        "excerpt": item.excerpt,
                        "locator": item.locator,
                        "solution_id": str(item.solution_id or ""),
                    }
                    for item in row.evidences.all()
                ],
            }
        )
    return result


# ---------------------------------------------------------------------------
# 教材目录
# ---------------------------------------------------------------------------


def list_textbooks() -> list[dict]:
    return [
        {
            "id": str(row.pk),
            "name": row.name,
            "stage": row.stage,
            "volume": row.volume,
            "edition": row.edition,
            "unspecified": row.unspecified,
        }
        for row in LibraryTextbook.objects.all().order_by("position", "name")
    ]


def list_chapters(*, textbook_id: str = "") -> list[dict]:
    rows = LibraryChapter.objects.select_related("textbook").order_by("position", "title", "id")
    result = []
    for row in rows:
        if textbook_id and str(row.textbook_id or "") != str(textbook_id):
            continue
        result.append(
            {
                "id": str(row.pk),
                "title": row.title,
                "textbook_id": str(row.textbook_id or ""),
                "textbook_name": row.textbook.name if row.textbook else "通用教学目录",
                "parent_id": str(row.parent_id or ""),
                "node_key": row.node_key,
                "position": row.position,
            }
        )
    return result


def chapter_node_key(*, textbook_id: str = "", parent_id: str = "", title: str) -> str:
    """章节的规范化键。

    SQLite 的 NULL 不参与唯一约束，「无教材的顶层章节」用 (NULL, NULL, 标题)
    是拦不住重复的。所以整条父子链落成一个非空串。
    """
    parts = ["tb:" + str(textbook_id)] if textbook_id else ["generic"]
    if parent_id:
        parts.append(str(parent_id))
    parts.append(concept_key(title))
    return ":".join(parts)


def ensure_chapter(title: str, *, textbook_id: str = "", parent_id: str = "", position: int = 0) -> LibraryChapter:
    text = str(title or "").strip()[:160]
    if not text:
        raise AssertionError_("章节需要一个名字")
    textbook = LibraryTextbook.objects.filter(pk=textbook_id).first() if textbook_id else None
    parent = LibraryChapter.objects.filter(pk=parent_id).first() if parent_id else None
    if parent is not None and parent.textbook_id != (textbook.pk if textbook else None):
        raise AssertionError_("子章节必须和父章节在同一本教材下")
    node_key = chapter_node_key(
        textbook_id=str(textbook.pk) if textbook else "", parent_id=str(parent.pk) if parent else "", title=text
    )
    row, _ = LibraryChapter.objects.get_or_create(
        node_key=node_key, defaults={"title": text, "textbook": textbook, "parent": parent, "position": position}
    )
    return row


def ensure_textbook(name: str, *, stage: str = "", volume: str = "", unspecified: bool = False) -> LibraryTextbook:
    text = str(name or "").strip()[:120]
    if not text:
        raise AssertionError_("教材需要一个名字")
    row, _ = LibraryTextbook.objects.get_or_create(
        name=text, stage=str(stage or "")[:24], defaults={"volume": str(volume or "")[:60], "unspecified": unspecified}
    )
    return row


# ---------------------------------------------------------------------------
# 个人专题
# ---------------------------------------------------------------------------


def list_collections() -> list[dict]:
    return [
        {
            "id": str(row.pk),
            "name": row.name,
            "description": row.description,
            "color": row.color,
            "member_count": row.members.count(),
        }
        for row in LibraryCollection.objects.all().order_by("position", "name")
    ]


def add_to_collection(collection_id: str, publication_id: str, *, note: str = "") -> dict:
    collection = LibraryCollection.objects.filter(pk=collection_id).first()
    if collection is None:
        raise AssertionError_("找不到这个专题", 404)
    publication = PublishedQuestion.objects.filter(pk=publication_id).first()
    if publication is None:
        raise AssertionError_("找不到这道题", 404)
    member, created = LibraryCollectionMember.objects.get_or_create(
        collection=collection, publication=publication, defaults={"note": str(note or "")[:300]}
    )
    return {"ok": True, "created": created, "member": str(member.pk)}


def remove_from_collection(collection_id: str, publication_id: str) -> dict:
    """移出专题不删题 —— 题还在题库里，只是不在这个专题里。"""
    deleted, _ = LibraryCollectionMember.objects.filter(
        collection_id=collection_id, publication_id=publication_id
    ).delete()
    return {"ok": True, "removed": deleted}


# ---------------------------------------------------------------------------
# 整理程度
# ---------------------------------------------------------------------------


def coverage_for(publication: PublishedQuestion) -> dict:
    """这道题整理到什么程度。

    没有这个，「未贴标签」和「确认过没有」就分不开 ——
    排除条件也不能把未知当成「确实没有」。
    """
    rows = LibraryCoverage.objects.filter(publication=publication).order_by("scope_key", "dimension")
    return {
        "coverage": [
            {
                "scope_key": row.scope_key,
                "dimension": row.dimension,
                "status": row.status,
                "basis": row.basis,
                "checked_at": row.checked_at.isoformat() if row.checked_at else "",
            }
            for row in rows
        ],
        "unreviewed": not rows.exists(),
    }


def mark_coverage(
    publication: PublishedQuestion, *, scope_key: str, dimension: str, status: str, basis: str = ""
) -> dict:
    if status not in dict(LibraryCoverage.Status.choices):
        raise AssertionError_(f"整理程度 {status} 不认")
    row, _ = LibraryCoverage.objects.update_or_create(
        publication=publication,
        scope_key=scope_key or SCOPE_WHOLE,
        dimension=dimension or "primary",
        defaults={
            "status": status,
            "basis": str(basis or "")[:500],
            "checked_at": timezone.now() if status != LibraryCoverage.Status.UNREVIEWED else None,
        },
    )
    return {"ok": True, "id": str(row.pk), "status": row.status}


# ---------------------------------------------------------------------------
# 概念关系
# ---------------------------------------------------------------------------


def relate_concepts(parent_id: str, child_id: str, *, relation_type: str = "related") -> dict:
    """概念之间的关系。

    ``hierarchy`` 是分类从属（参与下级继承）；``related`` 是「相关／使用」，
    独立显示，不参与继承。「中位线定理」和「构造中位线」用后者连，不合并。
    """
    if relation_type not in dict(LibraryTagRelation.Type.choices):
        raise AssertionError_(f"关系类型 {relation_type} 不认")
    parent = LibraryTag.objects.filter(pk=parent_id).first()
    child = LibraryTag.objects.filter(pk=child_id).first()
    if parent is None or child is None:
        raise AssertionError_("概念不存在", 404)
    if parent.pk == child.pk:
        raise AssertionError_("概念不能和自己有关系")
    if relation_type == LibraryTagRelation.Type.HIERARCHY and _creates_cycle(parent, child):
        raise AssertionError_("这样会绕成圈")
    row, created = LibraryTagRelation.objects.get_or_create(
        parent=parent, child=child, relation_type=relation_type, defaults={"source": "human", "revision": 1}
    )
    return {"ok": True, "created": created, "relation": str(row.pk)}


def _creates_cycle(parent: LibraryTag, child: LibraryTag) -> bool:
    """把 child 挂到 parent 下，会不会绕成圈。"""
    stack = [child.pk]
    seen = set()
    while stack:
        current = stack.pop()
        if current == parent.pk:
            return True
        if current in seen:
            continue
        seen.add(current)
        stack.extend(
            LibraryTagRelation.objects.filter(
                parent_id=current, relation_type=LibraryTagRelation.Type.HIERARCHY
            ).values_list("child_id", flat=True)
        )
    return False


def descendant_tag_ids(tag_ids: list[str], *, include_descendants: bool = True) -> set[str]:
    """概念自身加上它的下级。

    只用来扩展检索范围 —— 不给题目复制父级标签，
    也不表示这道题考查了父概念的全部性质。
    """
    from .library_query import descendant_tag_ids as _descendants

    return _descendants(tag_ids, include_descendants=include_descendants)
