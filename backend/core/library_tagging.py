"""Formal tag catalogue, assignment, migration compatibility and AI review."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, F
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from . import library
from .models import (
    LibraryJob,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagCatalogue,
    LibraryTagGroup,
    LibraryTagOperation,
    LibraryTagRelation,
    LibraryTagSuggestion,
    PublishedQuestion,
)
from .pinyin_initials import initial_for

MAX_NAME = 100
MAX_ALIASES = 20
# 一次建一棵目录树的上限：一本教材的目录也就几百条。
MAX_BATCH_NODES = 800


class TaggingError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class StaleTagSuggestionError(TaggingError):
    """A proposal can no longer be reviewed against the current question or catalogue."""


def catalogue_state() -> LibraryTagCatalogue:
    state, _ = LibraryTagCatalogue.objects.get_or_create(pk=1, defaults={"revision": 1, "initialized": False})
    return state


def normalized(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).casefold()


def tag_name_candidates(name: str, kind: str):
    """The library's own records for a proposed name: an active tag, and a retired one.

    Retired labels were never deleted, only switched off, so the directory still holds
    them under the old ``legacy`` kind. The model is only ever shown active labels, so it
    keeps re-proposing these words as brand new names and the interface then asks for a
    parent that cannot exist. Returning both lets a human accept the existing record
    instead.
    """
    key = normalized(name)
    if not key:
        return None, None
    active = retired = None
    for tag in LibraryTag.objects.filter(kind__in=[kind, LibraryTag.Kind.LEGACY]):
        if normalized(tag.name) != key:
            continue
        if tag.kind == kind and tag.active:
            active = active or tag
        elif tag.kind == LibraryTag.Kind.LEGACY and not tag.active:
            retired = retired or tag
    return active, retired


def _clean_name(value, label="标签名") -> str:
    if not isinstance(value, str):
        raise TaggingError(f"{label}必须是文字")
    name = unicodedata.normalize("NFKC", value).strip()
    if not name or len(name) > MAX_NAME or "|" in name or any(ord(char) < 32 for char in name):
        raise TaggingError(f"{label}不能为空，且不能超过 {MAX_NAME} 字")
    return name


def _clean_aliases(value) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_ALIASES:
        raise TaggingError(f"别名应为不超过 {MAX_ALIASES} 项的列表")
    result, keys = [], set()
    for item in value:
        alias = _clean_name(item, "别名")
        key = normalized(alias)
        if key not in keys:
            result.append(alias)
            keys.add(key)
    return result


def _clean_color(value) -> str:
    color = str(value or "").strip()
    if color and not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise TaggingError("颜色格式应为 #RRGGBB")
    return color


def _clean_group_name(value) -> str:
    """群组名。跟标签名一样要限长，但允许重名以外的规矩 —— 重名由数据库挡。"""
    return _clean_name(value, "分组名")


def _clean_group_position(value) -> int:
    try:
        position = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(position, 9999))


def _group_snapshot(group) -> dict:
    """群组的一份可还原快照 —— 撤销靠它把名字、颜色、顺序放回去。"""
    return {
        "id": str(group.pk),
        "name": group.name,
        "color": group.color,
        "position": group.position,
        "system_key": group.system_key,
    }


def _clean_scope(tag, value):
    if value is None:
        value = {"whole": True}
    if value == {"whole": True}:
        return value
    if not isinstance(value, dict):
        raise TaggingError("解题方法请明确选择整题或 1–12 个小问")
    subquestions = value.get("subquestions")
    if (
        tag.kind != LibraryTag.Kind.METHOD
        or set(value) != {"subquestions"}
        or not isinstance(subquestions, list)
        or not 1 <= len(subquestions) <= 12
        or any(not isinstance(part, str) or not re.fullmatch(r"[1-9][0-9]?", part) for part in subquestions)
        or len(set(subquestions)) != len(subquestions)
    ):
        raise TaggingError("解题方法请明确选择整题或 1–12 个小问")
    return {"subquestions": subquestions}


def _relation_snapshot():
    return [
        {"parent_id": str(parent), "child_id": str(child), "relation_type": kind}
        for parent, child, kind in LibraryTagRelation.objects.order_by(
            "relation_type", "parent_id", "child_id"
        ).values_list("parent_id", "child_id", "relation_type")
    ]


def relation_context():
    result = {}
    for row in _relation_snapshot():
        parent = result.setdefault(row["parent_id"], {"parents": [], "children": [], "methods": []})
        child = result.setdefault(row["child_id"], {"parents": [], "children": [], "methods": []})
        if row["relation_type"] == LibraryTagRelation.Type.HIERARCHY:
            parent["children"].append(row["child_id"])
            child["parents"].append(row["parent_id"])
        else:
            parent["methods"].append(row["child_id"])
    return result


def descendant_ids(tag_ids, *, context=None):
    """Only hierarchy implies membership. Related methods never do."""
    context = relation_context() if context is None else context
    visited, todo = set(), [str(value) for value in tag_ids]
    while todo:
        value = todo.pop()
        if value in visited:
            continue
        visited.add(value)
        todo.extend(context.get(value, {}).get("children", []))
    return visited


def _relation_ids(value):
    if not isinstance(value, list) or len(value) > 100:
        raise TaggingError("上下级或相关方法应为最多 100 个标签编号")
    try:
        result = [str(uuid.UUID(str(item))) for item in value]
    except (ValueError, TypeError, AttributeError):
        raise TaggingError("关系标签编号格式不正确") from None
    if len(set(result)) != len(result):
        raise TaggingError("关系标签不能重复")
    return result


def _validate_relations(rows):
    kinds = {str(key): kind for key, kind in LibraryTag.objects.values_list("id", "kind")}
    children = {}
    for row in rows:
        parent, child, relation_type = row["parent_id"], row["child_id"], row["relation_type"]
        if parent not in kinds or child not in kinds:
            raise TaggingError("关系中的标签已不存在，请刷新后重试", 409)
        if parent == child:
            raise TaggingError("标签不能成为自己的上级")
        if relation_type == LibraryTagRelation.Type.HIERARCHY:
            # 1.26.27：上下级不再要求同一类。老师的标签是他自己的一棵树，
            # 「分类讨论」既是知识点也是解题方法 —— 那就是同一棵树上的两个位置，
            # 硬按类型分家只会让他在两个地方各贴一次。
            # 分类仍然有用：它决定「相关方法」这类专属关系，以及解题方法的小问范围。
            children.setdefault(parent, set()).add(child)
        elif relation_type == LibraryTagRelation.Type.RELATED_METHOD:
            if kinds[parent] != LibraryTag.Kind.KNOWLEDGE or kinds[child] != LibraryTag.Kind.METHOD:
                raise TaggingError("相关方法须从知识点连接到解题方法")
        else:
            raise TaggingError("标签关系类型不正确")
    # Iterative topological sorting avoids recursion limits in user-built trees.
    indegree = dict.fromkeys(kinds, 0)
    for values in children.values():
        for child in values:
            indegree[child] += 1
    todo = [key for key, degree in indegree.items() if not degree]
    removed = 0
    while todo:
        parent = todo.pop()
        removed += 1
        for child in children.get(parent, set()):
            indegree[child] -= 1
            if not indegree[child]:
                todo.append(child)
    if removed != len(kinds):
        raise TaggingError("这个上下级关系会形成循环，请选择其他上级", 409)


def _restore_relations(rows):
    _validate_relations(rows)
    LibraryTagRelation.objects.all().delete()
    LibraryTagRelation.objects.bulk_create([LibraryTagRelation(**row) for row in rows])


def _set_relation_neighbors(tag, ids, relation_type):
    ids = _relation_ids(ids)
    rows = _relation_snapshot()
    if relation_type == LibraryTagRelation.Type.HIERARCHY:
        rows = [row for row in rows if not (row["child_id"] == str(tag.pk) and row["relation_type"] == relation_type)]
        rows.extend({"parent_id": key, "child_id": str(tag.pk), "relation_type": relation_type} for key in ids)
    else:
        rows = [row for row in rows if not (row["parent_id"] == str(tag.pk) and row["relation_type"] == relation_type)]
        rows.extend({"parent_id": str(tag.pk), "child_id": key, "relation_type": relation_type} for key in ids)
    _restore_relations(rows)


def _redirect_relations(source_ids, target_id):
    source_ids, target_id = set(map(str, source_ids)), str(target_id)
    rewritten = set()
    for row in _relation_snapshot():
        parent = target_id if row["parent_id"] in source_ids else row["parent_id"]
        child = target_id if row["child_id"] in source_ids else row["child_id"]
        if parent != child:
            rewritten.add((parent, child, row["relation_type"]))
    _restore_relations(
        [{"parent_id": parent, "child_id": child, "relation_type": kind} for parent, child, kind in sorted(rewritten)]
    )


def tag_json(tag: LibraryTag, *, count=None, context=None) -> dict:
    row = {
        "id": str(tag.pk),
        "name": tag.name,
        "kind": tag.kind,
        "description": tag.description,
        "color": tag.color,
        "aliases": tag.aliases or [],
        "levels": tag.levels or [],
        "conditions": tag.conditions,
        "active": tag.active,
        "favorite": tag.favorite,
        "system_key": tag.system_key,
        # 标签管理窗口按分组归类：这几项决定它落在哪一栏。
        # 取的是 pk —— groups.all() 给的是模型对象，str() 出来是
        # 「LibraryTagGroup object (…)」，前端按 id 匹配就全落空。
        "group_ids": sorted(str(value.pk) for value in tag.groups.all()),
        "initial": initial_for(tag.name),
    }
    context = relation_context() if context is None else context
    neighbors = context.get(str(tag.pk), {})
    row.update(
        parent_ids=neighbors.get("parents", []),
        child_ids=neighbors.get("children", []),
        related_method_ids=neighbors.get("methods", []),
    )
    if count is not None:
        row["count"] = count
        row["direct_count"] = count
    return row


def relations_view(request, tag_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    tag = get_object_or_404(LibraryTag, pk=tag_id)
    context = relation_context()
    neighbors = context.get(str(tag.pk), {})

    def tags(values):
        return [tag_json(item, context=context) for item in LibraryTag.objects.filter(pk__in=values)]

    return JsonResponse(
        {
            "revision": catalogue_state().revision,
            "tag": tag_json(tag, context=context),
            "parents": tags(neighbors.get("parents", [])),
            "children": tags(neighbors.get("children", [])),
            "related_methods": tags(neighbors.get("methods", [])),
        }
    )


def formal_assignments(publication: PublishedQuestion):
    return LibraryTagAssignment.objects.filter(publication=publication, needs_review=False).select_related("tag")


def formal_tag_names(publication: PublishedQuestion) -> list[str]:
    if not publication.pk:
        return library.tags_of(publication.extras)
    return list(formal_assignments(publication).order_by("created_at", "id").values_list("tag__name", flat=True))


def _chapter_names(tag, context, by_id, seen=frozenset()):
    """The chapters of a knowledge point are its topmost ancestors.

    Chapter used to be read from a stored group; the hierarchy carries it now,
    so a point listed under two school stages still offers both chapters.
    """
    parents = [
        by_id[value]
        for value in context.get(str(tag.pk), {}).get("parents", [])
        if value in by_id and value not in seen
    ]
    if not parents:
        return {tag.name}
    nested = seen | {str(tag.pk)}
    return set().union(*(_chapter_names(parent, context, by_id, nested) for parent in parents))


def knowledge_directory(*, include_inactive=False) -> list[dict]:
    every = {str(tag.pk): tag for tag in LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE)}
    context = relation_context()
    rows = LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE)
    if not include_inactive:
        rows = rows.filter(active=True)
    result = []
    for tag in rows:
        chapters = sorted(_chapter_names(tag, context, every))
        result.append(
            {
                "id": str(tag.pk),
                "point": tag.name,
                "chapter": "、".join(chapters),
                "description": tag.description,
                "aliases": tag.aliases or [],
                "levels": tag.levels or [],
                "active": tag.active,
            }
        )
    return result


def _retired_match_index(entries) -> dict:
    """Retired labels these entries re-propose by name, keyed by normalised name.

    One query for the whole batch so the interface can say "this label was already in
    your library" instead of asking for a parent that does not exist.
    """
    names = {normalized(str(entry.get("new_name") or entry.get("name") or "")) for entry in entries}
    names.discard("")
    index = {}
    if not names:
        return index
    for tag in LibraryTag.objects.filter(kind=LibraryTag.Kind.LEGACY, active=False):
        key = normalized(tag.name)
        if key in names:
            index.setdefault(
                key,
                {"id": str(tag.pk), "name": tag.name, "kind": tag.kind, "levels": tag.levels or [], "active": False},
            )
    return index


def suggestion_json(row: LibraryTagSuggestion) -> dict:
    fresh = row.publication.status == PublishedQuestion.Status.PUBLISHED
    entries = copy.deepcopy(row.suggestions or [])
    retired = _retired_match_index(entries)
    for entry in entries:
        if entry.get("tag_id"):
            continue
        match = retired.get(normalized(str(entry.get("new_name") or entry.get("name") or "")))
        if match is not None:
            entry["retired_tag"] = match
    return {
        "id": str(row.pk),
        "job_id": str(row.job_id) if row.job_id else None,
        "publication_id": str(row.publication_id),
        "status": "stale" if not fresh else row.status,
        "message": row.message,
        "suggestions": entries,
        "catalogue_snapshot": copy.deepcopy(row.catalogue_snapshot or {}),
        "prompt_version": row.prompt_version,
        "generation_source": row.generation_source,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def attach_to_publication_json(publication: PublishedQuestion, data: dict) -> dict:
    assignments = list(
        LibraryTagAssignment.objects.filter(publication=publication).select_related("tag").order_by("created_at", "id")
    )
    data["tags"] = [row.tag.name for row in assignments if not row.needs_review]
    data["tag_revision"] = publication.tag_revision
    data["tag_assignments"] = [
        {
            "tag": tag_json(row.tag),
            "scope": row.scope,
            "source": row.source,
            "generation_source": row.generation_source,
            "rationale": row.rationale,
            "evidence": row.evidence,
            "needs_review": row.needs_review,
        }
        for row in assignments
    ]
    candidate = LibraryTagSuggestion.objects.filter(publication=publication).order_by("-created_at", "-id").first()
    data["tag_suggestion"] = suggestion_json(candidate) if candidate else None
    return data


def _sync_projection(publication_id):
    publication = PublishedQuestion.objects.get(pk=publication_id)
    extras = copy.deepcopy(publication.extras) if isinstance(publication.extras, dict) else {}
    names = formal_tag_names(publication)
    extras["tags"] = names
    if names:
        if any(row.source == LibraryTagAssignment.Source.AI_ACCEPTED for row in formal_assignments(publication)):
            extras["tags_source"] = "AI 建议（人工采纳）"
        else:
            extras["tags_source"] = "human"
        extras["tags_at"] = timezone.now().isoformat()
        extras["tags_publication_id"] = str(publication.pk)
        extras["tags_fingerprint"] = library.generation_fingerprint(publication.content or {}, publication.pk)
    else:
        for key in (
            "tags_source",
            "tags_agent",
            "tags_executor",
            "tags_checked",
            "tags_at",
            "tags_publication_id",
            "tags_fingerprint",
        ):
            extras.pop(key, None)
    publication.tags_text = library.tags_text(names)
    publication.search_text = library._search_text(publication.content or {}, extras)
    PublishedQuestion.objects.filter(pk=publication.pk).update(
        extras=extras, tags_text=publication.tags_text, search_text=publication.search_text
    )


def _reindex_tag_name(tag):
    publication_ids = list(
        LibraryTagAssignment.objects.filter(tag=tag, publication__status=PublishedQuestion.Status.PUBLISHED)
        .values_list("publication_id", flat=True)
        .distinct()
    )
    for publication_id in publication_ids:
        publication = PublishedQuestion.objects.get(pk=publication_id, status=PublishedQuestion.Status.PUBLISHED)
        if (
            PublishedQuestion.objects.filter(pk=publication_id, tag_revision=publication.tag_revision).update(
                tag_revision=F("tag_revision") + 1
            )
            == 1
        ):
            _sync_projection(publication_id)


def _snapshot_assignments(publication):
    return [
        {
            "tag_id": str(row.tag_id),
            "scope": copy.deepcopy(row.scope),
            "source": row.source,
            "generation_source": row.generation_source,
            "rationale": row.rationale,
            "evidence": row.evidence,
            "needs_review": row.needs_review,
            "source_snapshot": copy.deepcopy(row.source_snapshot),
        }
        for row in LibraryTagAssignment.objects.filter(publication=publication).order_by("created_at", "id")
    ]


def _request_id(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise TaggingError("缺少有效的操作编号，请重试") from None


def _request_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _expected_revision(value):
    if type(value) is not int or value < 0:
        raise TaggingError("标签版本已变化，请刷新后重试", 409)
    return value


def _bump_publication(publication, expected: int):
    changed = PublishedQuestion.objects.filter(
        pk=publication.pk, status=PublishedQuestion.Status.PUBLISHED, tag_revision=expected
    ).update(tag_revision=F("tag_revision") + 1)
    if changed != 1:
        raise TaggingError("这道题的标签已在别处修改，请刷新后重试", 409)


def assign(payload: dict) -> dict:
    if "replace" in payload:
        return replace_assignment(payload)
    publication_ids = payload.get("publication_ids")
    add_ids, remove_ids = payload.get("add", []), payload.get("remove", [])
    expected = payload.get("expected_revisions")
    if not isinstance(publication_ids, list) or not publication_ids or len(publication_ids) > 500:
        raise TaggingError("请勾选 1–500 道题")
    try:
        ids = list(dict.fromkeys(str(uuid.UUID(str(value))) for value in publication_ids))
    except (ValueError, TypeError):
        raise TaggingError("题库条目编号格式不正确") from None
    if not isinstance(add_ids, list) or not isinstance(remove_ids, list) or len(add_ids) + len(remove_ids) > 200:
        raise TaggingError("本次标签选择不正确")
    try:
        adds = [str(uuid.UUID(str(value))) for value in add_ids]
        removes = [str(uuid.UUID(str(value))) for value in remove_ids]
    except (ValueError, TypeError):
        raise TaggingError("标签编号格式不正确") from None
    if len(set(adds)) != len(adds) or len(set(removes)) != len(removes) or set(adds) & set(removes):
        raise TaggingError("同一标签不能同时添加和移除")
    scopes = payload.get("scopes", {})
    if not isinstance(scopes, dict) or set(scopes) - set(adds):
        raise TaggingError("标签适用范围不正确")
    if not isinstance(expected, dict) or set(expected) != set(ids):
        raise TaggingError("标签版本已变化，请刷新后重试", 409)
    for key in ids:
        _expected_revision(expected.get(key))
    request_id = _request_id(payload.get("request_id"))
    safe_body = {
        "publication_ids": ids,
        "add": adds,
        "remove": removes,
        "scopes": scopes,
        "expected_revisions": expected,
    }
    digest = _request_hash(safe_body)
    with transaction.atomic():
        prior = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
        if prior:
            if prior.request_hash != digest:
                raise TaggingError("同一操作编号不能用于不同的标签修改", 409)
            return {
                "ok": True,
                "operation_id": str(prior.pk),
                "replayed": True,
                "revisions": prior.after_snapshot.get("revisions", {}),
            }
        publications = list(PublishedQuestion.objects.filter(pk__in=ids, status=PublishedQuestion.Status.PUBLISHED))
        if {str(row.pk) for row in publications} != set(ids):
            raise TaggingError("有题目已不在正式题库，请刷新后重试", 409)
        tags = {str(row.pk): row for row in LibraryTag.objects.filter(pk__in=set(adds + removes))}
        if len(tags) != len(set(adds + removes)):
            raise TaggingError("有标签已不存在，请刷新后重试", 409)
        for tag in tags.values():
            if not tag.active:
                raise TaggingError(f"“{tag.name}”已停用，不能再贴到题目上", 409)
        scopes_by_tag = {tag_id: _clean_scope(tags[tag_id], scopes.get(tag_id)) for tag_id in adds}
        before, after_revisions = {}, {}
        for publication in publications:
            key = str(publication.pk)
            before[key] = _snapshot_assignments(publication)
            _bump_publication(publication, expected[key])
            current = {str(row.tag_id): row for row in LibraryTagAssignment.objects.filter(publication=publication)}
            for tag_id in removes:
                existing = current.get(tag_id)
                if existing:
                    existing.delete()
            for tag_id in adds:
                existing = current.get(tag_id)
                if existing is None:
                    LibraryTagAssignment.objects.create(
                        publication=publication,
                        tag=tags[tag_id],
                        source=LibraryTagAssignment.Source.HUMAN,
                        scope=scopes_by_tag[tag_id],
                    )
                elif existing.needs_review:
                    existing.needs_review = False
                    existing.source = LibraryTagAssignment.Source.HUMAN
                    existing.scope = scopes_by_tag[tag_id]
                    existing.save(update_fields=["needs_review", "source", "scope", "updated_at"])
                elif existing.scope != scopes_by_tag[tag_id]:
                    existing.scope = scopes_by_tag[tag_id]
                    existing.source = LibraryTagAssignment.Source.HUMAN
                    existing.save(update_fields=["scope", "source", "updated_at"])
            _sync_projection(publication.pk)
            after_revisions[key] = expected[key] + 1
        op = LibraryTagOperation.objects.create(
            client_request_id=request_id,
            request_hash=digest,
            action="assign",
            publication_ids=ids,
            before_snapshot={"assignments": before, "revisions": expected},
            after_snapshot={"revisions": after_revisions},
        )
    return {"ok": True, "operation_id": str(op.pk), "revisions": after_revisions, "message": "标签已保存"}


def _replacement_result(operation, publication, *, replayed=False):
    return {
        "ok": True,
        "operation_id": str(operation.pk),
        "replayed": replayed,
        "noop": bool(operation.after_snapshot.get("noop")),
        "revisions": operation.after_snapshot.get("revisions", {}),
        "revision": publication.tag_revision,
        "publication_id": str(publication.pk),
        "assignments": [
            {
                "tag": tag_json(row.tag),
                "scope": row.scope,
                "source": row.source,
                "generation_source": row.generation_source,
                "rationale": row.rationale,
                "evidence": row.evidence,
                "needs_review": row.needs_review,
            }
            for row in publication.tag_assignments.select_related("tag").order_by("created_at", "id")
        ],
    }


def replace_assignment(payload):
    ids, replacement = payload.get("publication_ids"), payload.get("replace")
    if (
        not isinstance(ids, list)
        or len(ids) != 1
        or not isinstance(replacement, dict)
        or set(replacement) != {"from_tag_id", "to_tag_id"}
    ):
        raise TaggingError("请明确选择一道题上的原标签和替换标签")
    if payload.get("add") or payload.get("remove") or payload.get("scopes"):
        raise TaggingError("标签替换不能同时执行另一组增删")
    try:
        publication_id = str(uuid.UUID(str(ids[0])))
        source_id = str(uuid.UUID(str(replacement["from_tag_id"])))
        target_id = str(uuid.UUID(str(replacement["to_tag_id"])))
    except (ValueError, TypeError, AttributeError):
        raise TaggingError("题目或标签编号格式不正确") from None
    revisions = payload.get("expected_revisions")
    if not isinstance(revisions, dict) or set(revisions) != {publication_id}:
        raise TaggingError("题目标签版本已变化，请刷新后重试", 409)
    expected = _expected_revision(revisions[publication_id])
    catalogue_revision = payload.get("expected_catalogue_revision")
    if type(catalogue_revision) is not int or catalogue_revision < 1:
        raise TaggingError("标签目录版本已变化，请刷新后重试", 409)
    request_id, digest = _request_id(payload.get("request_id")), _request_hash(payload)
    with transaction.atomic():
        prior = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
        if prior:
            if prior.request_hash != digest:
                raise TaggingError("同一操作编号不能用于不同的标签替换", 409)
            publication = PublishedQuestion.objects.get(pk=publication_id)
            return _replacement_result(prior, publication, replayed=True)
        if LibraryTagCatalogue.objects.filter(pk=1, revision=catalogue_revision).update(revision=F("revision")) != 1:
            raise TaggingError("标签目录已变化，请刷新后重新选择", 409)
        publication = PublishedQuestion.objects.filter(
            pk=publication_id, status=PublishedQuestion.Status.PUBLISHED
        ).first()
        if publication is None or publication.tag_revision != expected:
            raise TaggingError("题目或标签版本已变化，请刷新后重试", 409)
        source = (
            LibraryTagAssignment.objects.select_related("tag").filter(publication=publication, tag_id=source_id).first()
        )
        target = LibraryTag.objects.filter(pk=target_id, active=True).first()
        if source is None or target is None:
            raise TaggingError("原标签已移除或目标标签已停用，请刷新后重试", 409)
        if source.tag.kind != target.kind:
            raise TaggingError("请在相同标签类型中选择替换目标")
        if source.needs_review:
            raise TaggingError("原标签尚待核对，请先处理核对状态", 409)
        existing = LibraryTagAssignment.objects.filter(publication=publication, tag=target).first()
        if existing is not None and existing.needs_review:
            raise TaggingError("目标标签已在本题等待核对，请先核对再替换", 409)
        if source_id == target_id:
            op = LibraryTagOperation.objects.create(
                client_request_id=request_id,
                request_hash=digest,
                action="replace",
                after_snapshot={"revisions": {publication_id: expected}, "noop": True},
                catalogue_revision=catalogue_revision,
            )
            return _replacement_result(op, publication)
        before = _snapshot_assignments(publication)
        scope = _merged_scope(target, [source, existing] if existing else [source])
        provenance = copy.deepcopy(existing.source_snapshot or {}) if existing else {}
        provenance.setdefault("replaced_assignments", []).append(_assignment_snapshot(source))
        if existing is None:
            LibraryTagAssignment.objects.create(
                publication=publication,
                tag=target,
                scope=scope,
                source=LibraryTagAssignment.Source.HUMAN,
                source_snapshot=provenance,
            )
        else:
            provenance["target_before_replacement"] = _assignment_snapshot(existing)
            existing.source_snapshot = provenance
            if existing.scope != scope:
                existing.scope, existing.source = scope, LibraryTagAssignment.Source.HUMAN
                # The old explanation covered only the old scope. Keep it in
                # target_before_replacement, rather than presenting it as AI
                # evidence for the newly enlarged set of subquestions.
                existing.generation_source, existing.rationale, existing.evidence = "", "", ""
            existing.save(
                update_fields=[
                    "scope",
                    "source",
                    "source_snapshot",
                    "generation_source",
                    "rationale",
                    "evidence",
                    "updated_at",
                ]
            )
        source.delete()
        _bump_publication(publication, expected)
        _sync_projection(publication.pk)
        publication.refresh_from_db()
        op = LibraryTagOperation.objects.create(
            client_request_id=request_id,
            request_hash=digest,
            action="replace",
            publication_ids=[publication_id],
            before_snapshot={"assignments": {publication_id: before}, "revisions": {publication_id: expected}},
            after_snapshot={"revisions": {publication_id: expected + 1}},
            catalogue_revision=catalogue_revision,
        )
        return _replacement_result(op, publication)


def _catalogue_rows():
    state = catalogue_state()
    # 1.25.12：这两段以前各写了一遍同一个 filter。留着两份一模一样的条件，就只能靠
    # 「谁忘了改」来提醒，那正好是出错的地方。收成一个。
    live = LibraryTagAssignment.objects.filter(
        publication__status=PublishedQuestion.Status.PUBLISHED, needs_review=False
    )
    counts = {
        str(row["tag_id"]): row["count"]
        for row in live.values("tag_id").annotate(count=Count("publication_id", distinct=True))
    }
    context = relation_context()
    publications_by_tag = {}
    for tag_id, publication_id in live.values_list("tag_id", "publication_id"):
        publications_by_tag.setdefault(str(tag_id), set()).add(str(publication_id))
    tags = []
    for tag in LibraryTag.objects.prefetch_related("groups").all():
        row = tag_json(tag, count=counts.get(str(tag.pk), 0), context=context)
        publications = set()
        for key in descendant_ids([tag.pk], context=context):
            publications.update(publications_by_tag.get(key, set()))
        row["subtree_count"] = len(publications)
        tags.append(row)
    # 1.27.0：标签群组。模型和撤销一直都在，只是 1.25 之后界面上没有入口了 ——
    # 老师要的是「把这些标签归到一组」，不是「改标签的上下级」。
    #
    # 系统分组（system_key 非空）是教材章节那套，界面上由类型树呈现，
    # 不再在「标签群组」里重复列一遍。老师自建的分组才是这里的主角。
    groups = []
    user_group_ids = set()
    for group in LibraryTagGroup.objects.all():
        members = list(group.tags.all())
        system = bool(group.system_key)
        if not system:
            user_group_ids.add(str(group.pk))
        groups.append(
            {
                "id": str(group.pk),
                "name": group.name,
                "color": group.color,
                "position": group.position,
                "system_key": group.system_key,
                "system": system,
                "tag_count": len(members),
            }
        )
    # 「未分类」说的是没归到**老师自建分组**里的那些。
    ungrouped_count = sum(1 for row in tags if not (set(row["group_ids"]) & user_group_ids))
    # 1.25.12：标签管理页以前只说「299 个标签」，然后每行自己写「0 题」。走进来的人
    # 只看见一整屏 0，既不知道题库里到底有多少道题，也不知道其实只有两道贴过标签 ——
    # 更不知道后台已经攒着几百条能直接采纳的 AI 建议（采纳不花钱）。
    #
    # 1.25.13：「待核对」这个数不能在这里另数一遍。原来数的是建议条数（443），而题库
    # 页那个筛选数的是有建议的题目数（439）—— 一道题可能挂两条建议。两个数摆在两个
    # 页面上，人来回一看只会以为其中一个算错了。所以直接取题库页用的那个函数，
    # 同一个函数算两遍也不会漂。
    from .library_browse import tag_work_counts

    tag_work = tag_work_counts()
    tagged_questions = len({publication_id for ids in publications_by_tag.values() for publication_id in ids})
    return {
        "revision": state.revision,
        "tags": tags,
        "groups": groups,
        "ungrouped_count": ungrouped_count,
        "total": len(tags),
        "max_name": MAX_NAME,
        "initialized": state.initialized,
        "published_questions": PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).count(),
        "tagged_questions": tagged_questions,
        "questions_pending_review": tag_work["pending_review"],
        "questions_pending_actionable": tag_work["pending_actionable"],
    }


def template_rows():
    from .tag_catalogue_seed import template_blueprint

    blueprint = template_blueprint()
    current_tags = {tag.system_key: tag for tag in LibraryTag.objects.exclude(system_key=None)}
    for section in blueprint["sections"]:
        section["already_imported"] = bool(current_tags.get(section["key"]))
        section["tag_count"] = sum(1 for tag in blueprint["tags"] if section["key"] in tag["section_keys"])
    for tag in blueprint["tags"]:
        tag["already_imported"] = bool(
            current_tags.get(tag["key"])
            or LibraryTag.objects.filter(kind=tag["kind"], name__iexact=tag["name"]).exists()
        )
    packages = []
    for key, label, prefix in (
        ("middle", "初中数学", "初中 · "),
        ("high", "高中数学", "高中 · "),
        ("methods", "解题方法", "方法 · "),
    ):
        section_keys = [item["key"] for item in blueprint["sections"] if item["name"].startswith(prefix)]
        packages.append(
            {
                "key": key,
                "name": label,
                "section_keys": section_keys,
                "tag_count": sum(1 for tag in blueprint["tags"] if set(tag["section_keys"]) & set(section_keys)),
            }
        )
    return {"packages": packages, **blueprint}


def templates_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    return JsonResponse(template_rows())


def catalogue_view(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    from .views import _body, _guard

    if request.method == "GET":
        rejected = _guard(request, json_body=False)
        if rejected:
            return rejected
        return JsonResponse(_catalogue_rows())
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求内容无法读取"}, status=400)
    try:
        return JsonResponse(catalogue_mutation(payload))
    except TaggingError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except IntegrityError:
        return JsonResponse({"error": "同类型标签已存在，请使用现有标签"}, status=409)


def _catalogue_revision(value):
    if type(value) is not int or value < 1:
        raise TaggingError("标签目录版本已变化，请刷新后重试", 409)
    state = catalogue_state()
    if state.revision != value:
        raise TaggingError("标签目录已在别处修改，请刷新后重试", 409)
    return state


def _assignment_snapshot(row):
    return {
        "id": str(row.pk),
        "publication_id": str(row.publication_id),
        "tag_id": str(row.tag_id),
        "scope": copy.deepcopy(row.scope),
        "source": row.source,
        "generation_source": row.generation_source,
        "rationale": row.rationale,
        "evidence": row.evidence,
        "needs_review": row.needs_review,
        "source_snapshot": copy.deepcopy(row.source_snapshot),
    }


def _publication_guard(publication):
    extras = copy.deepcopy(publication.extras) if isinstance(publication.extras, dict) else {}
    for key in (
        "tags",
        "tags_source",
        "tags_agent",
        "tags_executor",
        "tags_checked",
        "tags_at",
        "tags_publication_id",
        "tags_fingerprint",
    ):
        extras.pop(key, None)
    return _request_hash(
        {
            "content_hash": publication.content_hash,
            "status": publication.status,
            "version": publication.version,
            "review_source": publication.review_source,
            "review_agent": publication.review_agent,
            "extras": extras,
        }
    )


def _full_tag_snapshot(tag):
    return {
        "id": str(tag.pk),
        "name": tag.name,
        "kind": tag.kind,
        "description": tag.description,
        "color": tag.color,
        "aliases": copy.deepcopy(tag.aliases or []),
        "levels": copy.deepcopy(tag.levels or []),
        "conditions": tag.conditions,
        "active": tag.active,
        "favorite": tag.favorite,
        "system_key": tag.system_key,
        "group_ids": sorted(str(value) for value in tag.groups.values_list("id", flat=True)),
    }


def _tag_ids(value, label="标签"):
    if not isinstance(value, list) or not 1 <= len(value) <= 500:
        raise TaggingError(f"请选择 1–500 个{label}")
    try:
        ids = [str(uuid.UUID(str(item))) for item in value]
    except (ValueError, TypeError, AttributeError):
        raise TaggingError(f"{label}编号格式不正确") from None
    if len(set(ids)) != len(ids):
        raise TaggingError(f"{label}选择不能重复")
    return sorted(ids)


def _selected_tags(value):
    ids = _tag_ids(value)
    tags = list(LibraryTag.objects.filter(pk__in=ids).order_by("id"))
    if len(tags) != len(ids):
        raise TaggingError("所选标签已变化，请刷新后重试", 409)
    return tags


def _merge_tags_selection(source_ids, target_id):
    sources = _selected_tags(source_ids)
    try:
        target_id = uuid.UUID(str(target_id))
    except (ValueError, TypeError, AttributeError):
        raise TaggingError("合并目标编号格式不正确") from None
    target = LibraryTag.objects.filter(pk=target_id).first()
    if target is None:
        raise TaggingError("合并目标已不存在，请刷新后重试", 409)
    for source in sources:
        if source.pk == target.pk:
            raise TaggingError("合并目标不能同时作为待合并标签")
        # 1.26.27：不再按类型拦合并。老师的标签是他自己的一棵树，
        # 「分类讨论」既是知识点也是解题方法 —— 那就合成一颗，类型跟着「保留的那颗」走。
        # 唯一会变的是适用范围：知识点是整题，合成到解题方法上时，
        # 原来「只对第 2 小问有效」的方法会变成整题（确认框里会说明）。
    return sources, target


def _impact_data(action, tag_id=None, *, tag_ids=None, source_ids=None, target_id=None):
    target = None
    if action == "delete_tag":
        tags = _selected_tags([tag_id])
        group_snapshots = []
    elif action == "delete_tags":
        tags = _selected_tags(tag_ids)
        group_snapshots = []
    elif action == "merge_tags_batch":
        tags, target = _merge_tags_selection(source_ids, target_id)
        group_snapshots = []
    elif action == "reset_empty":
        tags = list(LibraryTag.objects.prefetch_related("groups").all())
        group_snapshots = [
            {
                "id": str(group.pk),
                "name": group.name,
                "color": group.color,
                "position": group.position,
                "system_key": group.system_key,
            }
            for group in LibraryTagGroup.objects.all()
        ]
    else:
        raise TaggingError("影响预览操作不正确")
    tag_ids = [tag.pk for tag in tags]
    tag_rows = [_full_tag_snapshot(tag) for tag in tags]
    assignments = list(
        LibraryTagAssignment.objects.filter(tag_id__in=tag_ids)
        .select_related("publication")
        .order_by("publication_id", "created_at", "id")
    )
    if target is not None:
        publication_ids = {row.publication_id for row in assignments}
        assignments = list(
            LibraryTagAssignment.objects.filter(publication_id__in=publication_ids, tag_id__in=[*tag_ids, target.pk])
            .select_related("publication")
            .order_by("publication_id", "created_at", "id")
        )
    assignment_rows = [_assignment_snapshot(row) for row in assignments]
    publications = {str(row.publication_id): row.publication for row in assignments}
    revisions = {
        key: {
            "revision": publication.tag_revision,
            "status": publication.status,
            "guard": _publication_guard(publication),
        }
        for key, publication in publications.items()
    }
    state = catalogue_state()
    snapshot = {
        "action": action,
        "revision": state.revision,
        "tags": tag_rows,
        "groups": group_snapshots,
        "assignments": assignment_rows,
        "publications": revisions,
        "target": _full_tag_snapshot(target) if target else None,
        "relations": _relation_snapshot(),
    }
    published_ids = {
        str(row.publication_id) for row in assignments if row.publication.status == PublishedQuestion.Status.PUBLISHED
    }
    historical_ids = set(publications) - published_ids
    return {
        "action": action,
        "tag_id": str(tag_id) if tag_id else None,
        "tag_ids": [str(tag.pk) for tag in tags],
        "target_id": str(target.pk) if target else None,
        "target_name": target.name if target else None,
        "revision": state.revision,
        "token": _request_hash(snapshot),
        "tag_count": len(tags),
        "group_count": len(group_snapshots),
        "relation_count": sum(
            row["parent_id"] in {str(value) for value in tag_ids}
            or row["child_id"] in {str(value) for value in tag_ids}
            for row in snapshot["relations"]
        ),
        "assignment_count": len(assignments),
        "question_count": len(published_ids),
        "historical_count": len(historical_ids),
    }


def impact_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    try:
        action = request.GET.get("action", "")

        def selected_ids(key):
            values = request.GET.getlist(key)
            return [part for value in values for part in value.split(",") if part]

        return JsonResponse(
            _impact_data(
                action,
                request.GET.get("tag_id"),
                tag_ids=selected_ids("tag_ids"),
                source_ids=selected_ids("source_ids"),
                target_id=request.GET.get("target_id"),
            )
        )
    except TaggingError as error:
        return JsonResponse({"error": str(error)}, status=error.status)


def _merged_scope(target, rows):
    if target.kind != LibraryTag.Kind.METHOD:
        return {"whole": True}
    scopes = [_clean_scope(target, row.scope or {"whole": True}) for row in rows]
    if any(scope == {"whole": True} for scope in scopes):
        return {"whole": True}
    parts = sorted({part for scope in scopes for part in scope["subquestions"]}, key=int)
    return _clean_scope(target, {"subquestions": parts})


def _merge_catalogue_tags(source_ids, target_id):
    sources, target = _merge_tags_selection(source_ids, target_id)
    ids = [source.pk for source in sources]
    _redirect_relations(ids, target.pk)
    publication_ids = list(
        LibraryTagAssignment.objects.filter(tag_id__in=ids).values_list("publication_id", flat=True).distinct()
    )
    rows = list(
        LibraryTagAssignment.objects.filter(publication_id__in=publication_ids, tag_id__in=[*ids, target.pk])
        .select_related("publication", "tag")
        .order_by("publication_id", "created_at", "id")
    )
    publications = {str(row.publication_id): row.publication for row in rows}
    before = {
        "sources": [_full_tag_snapshot(source) for source in sources],
        "target": _full_tag_snapshot(target),
        "assignments": [_assignment_snapshot(row) for row in rows],
        "revisions": {key: publication.tag_revision for key, publication in publications.items()},
        "publication_guards": {key: _publication_guard(publication) for key, publication in publications.items()},
    }
    by_publication = {}
    for row in rows:
        by_publication.setdefault(str(row.publication_id), []).append(row)
    after_revisions = {}
    for key, assignments in by_publication.items():
        publication = publications[key]
        if (
            PublishedQuestion.objects.filter(pk=publication.pk, tag_revision=publication.tag_revision).update(
                tag_revision=F("tag_revision") + 1
            )
            != 1
        ):
            raise TaggingError("合并期间题目标签版本发生变化，请重新查看影响范围", 409)
        target_row = next((row for row in assignments if row.tag_id == target.pk), None)
        base = target_row or next((row for row in assignments if not row.needs_review), assignments[0])
        # Preserve each original association and its AI/manual provenance, even
        # when several source associations become a single target association.
        provenance = copy.deepcopy(base.source_snapshot or {})
        provenance["merged_assignments"] = [_assignment_snapshot(row) for row in assignments]
        legacy_only = base.tag.kind == LibraryTag.Kind.LEGACY
        fields = {
            "scope": _merged_scope(target, assignments),
            "source": LibraryTagAssignment.Source.LEGACY_HUMAN if legacy_only else base.source,
            "generation_source": base.generation_source,
            "rationale": base.rationale,
            "evidence": base.evidence,
            "source_snapshot": provenance,
            "needs_review": any(row.needs_review and row.tag.kind != LibraryTag.Kind.LEGACY for row in assignments),
        }
        if legacy_only:
            fields["rationale"] = ((base.rationale + "；") if base.rationale else "") + "人工整理历史标签"
        LibraryTagAssignment.objects.filter(publication_id=key, tag_id__in=ids).delete()
        if target_row is None:
            LibraryTagAssignment.objects.create(publication_id=key, tag=target, **fields)
        else:
            for field, value in fields.items():
                setattr(target_row, field, value)
            target_row.save(update_fields=[*fields, "updated_at"])
        after_revisions[key] = publication.tag_revision + 1
        _sync_projection(key)
    alias_candidates = [*(target.aliases or [])]
    for source in sources:
        alias_candidates.extend([source.name, *(source.aliases or [])])
    aliases, seen = [], {normalized(target.name)}
    for alias in alias_candidates:
        if normalized(alias) not in seen:
            aliases.append(alias)
            seen.add(normalized(alias))
    # The complete names remain in the operation and association provenance;
    # retain the target's existing aliases before filling its bounded search aliases.
    target.aliases = _clean_aliases(aliases[:MAX_ALIASES])
    target.save(update_fields=["aliases", "updated_at"])
    LibraryTag.objects.filter(pk__in=ids).delete()
    after = {
        "revisions": after_revisions,
        "target": _full_tag_snapshot(target),
        "assignments": [
            _assignment_snapshot(row)
            for row in LibraryTagAssignment.objects.filter(publication_id__in=publication_ids, tag=target).order_by(
                "publication_id", "created_at", "id"
            )
        ],
        "publication_guards": {key: _publication_guard(PublishedQuestion.objects.get(pk=key)) for key in publications},
    }
    return before, after


def catalogue_mutation(payload: dict) -> dict:
    action = str(payload.get("action") or "")
    revision = payload.get("revision")
    if type(revision) is not int or revision < 1:
        raise TaggingError("标签目录版本已变化，请刷新后重试", 409)
    request_id = _request_id(payload.get("request_id"))
    digest = _request_hash(payload)
    with transaction.atomic():
        prior = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
        if prior:
            if prior.request_hash != digest:
                raise TaggingError("同一操作编号不能用于不同的目录修改", 409)
            return {
                "ok": True,
                "operation_id": str(prior.pk),
                "revision": prior.after_snapshot.get("revision"),
                "replayed": True,
            }
        _catalogue_revision(revision)
        before_relations = _relation_snapshot()
        impact_preview = None
        if action in {"delete_tag", "delete_tags", "reset_empty", "merge_tags_batch"}:
            impact_preview = _impact_data(
                action,
                payload.get("tag_id") if action == "delete_tag" else None,
                tag_ids=payload.get("tag_ids"),
                source_ids=payload.get("source_ids"),
                target_id=payload.get("target_id"),
            )
            if impact_preview["revision"] != revision or payload.get("impact_token") != impact_preview["token"]:
                raise TaggingError("目录或题目标签已发生变化，请重新查看影响范围", 409)
            if not payload.get("confirmed"):
                raise TaggingError("请先确认目录操作影响范围", 400)
        if LibraryTagCatalogue.objects.filter(pk=1, revision=revision).update(revision=F("revision") + 1) != 1:
            raise TaggingError("标签目录已在别处修改，请刷新后重试", 409)
        new_revision = revision + 1
        before, after = {}, {}
        if action == "create_tag":
            name = _clean_name(payload.get("name"))
            kind = payload.get("kind")
            if kind not in {LibraryTag.Kind.KNOWLEDGE, LibraryTag.Kind.METHOD, LibraryTag.Kind.CUSTOM}:
                raise TaggingError("请选择知识点、解题方法或自定义标签")
            levels = payload.get("levels", [])
            if (
                not isinstance(levels, list)
                or len(levels) > 2
                or any(level not in {"初中", "高中"} for level in levels)
            ):
                raise TaggingError("适用学段应选择初中或高中")
            tag = LibraryTag.objects.create(
                name=name,
                kind=kind,
                description=str(payload.get("description") or "").strip()[:300],
                color=_clean_color(payload.get("color")),
                aliases=_clean_aliases(payload.get("aliases", [])),
                levels=levels,
                conditions=str(payload.get("conditions") or "").strip()[:2000],
            )
            if "parent_ids" in payload:
                _set_relation_neighbors(tag, payload["parent_ids"], LibraryTagRelation.Type.HIERARCHY)
            if "related_method_ids" in payload:
                _set_relation_neighbors(tag, payload["related_method_ids"], LibraryTagRelation.Type.RELATED_METHOD)
            after = {"tag": tag_json(tag)}
        elif action == "create_tags_batch":
            # 1.26.6：照着课本目录一次建一棵树。
            # 老师的原话：「目录能不能自己根据教材目录来编写」—— 一个个建标签太慢，
            # 所以这里一次建一整棵。已经存在的同名同类型标签直接接上去，不重复建。
            kind = payload.get("kind") or LibraryTag.Kind.KNOWLEDGE
            if kind not in {LibraryTag.Kind.KNOWLEDGE, LibraryTag.Kind.METHOD, LibraryTag.Kind.CUSTOM}:
                raise TaggingError("请选择知识点、解题方法或自定义标签")
            nodes = payload.get("nodes")
            if not isinstance(nodes, list) or not nodes:
                raise TaggingError("请把课本目录贴进来")

            def count_nodes(items):
                total = 0
                for item in items:
                    if isinstance(item, dict):
                        total += 1 + count_nodes(item.get("children") or [])
                return total

            if count_nodes(nodes) > MAX_BATCH_NODES:
                raise TaggingError(f"一次最多建 {MAX_BATCH_NODES} 个节点，请分几次建")
            created_tags = []
            reused_tags = []

            def build(items, parent):
                for item in items:
                    if not isinstance(item, dict):
                        raise TaggingError("目录结构看不懂，请检查每一行")
                    name = _clean_name(item.get("name"))
                    existing = LibraryTag.objects.filter(name=name, kind=kind).first()
                    if existing is None:
                        existing = LibraryTag.objects.create(name=name, kind=kind, levels=[])
                        created_tags.append(existing)
                    else:
                        reused_tags.append(existing)
                    if parent is not None:
                        _set_relation_neighbors(existing, [str(parent.pk)], LibraryTagRelation.Type.HIERARCHY)
                    children = item.get("children") or []
                    if children and not isinstance(children, list):
                        raise TaggingError("目录结构看不懂，请检查每一行")
                    build(children, existing)

            build(nodes, None)
            after = {
                "created_tags": [tag_json(tag) for tag in created_tags],
                "created_tag_ids": [str(tag.pk) for tag in created_tags],
                "reused_tag_ids": [str(tag.pk) for tag in reused_tags],
            }
        elif action in {"set_parents", "add_parent", "remove_parent", "move_parent", "set_related_methods"}:
            tag = get_object_or_404(LibraryTag, pk=payload.get("tag_id"))
            before = {"tag": tag_json(tag)}
            if action == "set_related_methods":
                _set_relation_neighbors(tag, payload.get("related_method_ids"), LibraryTagRelation.Type.RELATED_METHOD)
            else:
                parents = list(relation_context().get(str(tag.pk), {}).get("parents", []))
                if action == "set_parents":
                    parents = payload.get("parent_ids")
                elif action == "add_parent":
                    value = _relation_ids([payload.get("parent_id")])[0]
                    if value not in parents:
                        parents.append(value)
                elif action == "remove_parent":
                    value = _relation_ids([payload.get("parent_id")])[0]
                    if value not in parents:
                        raise TaggingError("原上级关系已不存在，请刷新后重试", 409)
                    parents.remove(value)
                else:
                    old = _relation_ids([payload.get("from_parent_id")])[0]
                    new = _relation_ids([payload.get("to_parent_id")])[0]
                    if old not in parents:
                        raise TaggingError("原上级关系已不存在，请刷新后重试", 409)
                    parents = [value for value in parents if value != old]
                    if new not in parents:
                        parents.append(new)
                _set_relation_neighbors(tag, parents, LibraryTagRelation.Type.HIERARCHY)
            after = {"tag": tag_json(tag)}
        elif action in {"update_tag", "deactivate_tag", "activate_tag", "favorite_tag"}:
            tag = get_object_or_404(LibraryTag, pk=payload.get("tag_id"))
            before = {"tag": tag_json(tag)}
            old_name = tag.name
            if action == "update_tag":
                if "name" in payload:
                    name = _clean_name(payload["name"])
                    if name != tag.name:
                        tag.aliases = _clean_aliases([*(tag.aliases or []), tag.name])
                        tag.name = name
                if "aliases" in payload:
                    tag.aliases = _clean_aliases(payload["aliases"])
                for field, maximum in (("description", 300), ("conditions", 2000)):
                    if field in payload:
                        setattr(tag, field, str(payload[field] or "").strip()[:maximum])
                if "color" in payload:
                    tag.color = _clean_color(payload["color"])
                if "levels" in payload:
                    if not isinstance(payload["levels"], list):
                        raise TaggingError("適用學段應為列表")
                    tag.levels = payload["levels"][:8]
                tag.save()
                if "parent_ids" in payload:
                    _set_relation_neighbors(tag, payload["parent_ids"], LibraryTagRelation.Type.HIERARCHY)
                if "related_method_ids" in payload:
                    _set_relation_neighbors(tag, payload["related_method_ids"], LibraryTagRelation.Type.RELATED_METHOD)
            elif action == "deactivate_tag":
                tag.active = False
                tag.save(update_fields=["active", "updated_at"])
            elif action == "activate_tag":
                tag.active = True
                tag.save(update_fields=["active", "updated_at"])
            else:
                tag.favorite = not tag.favorite
                tag.save(update_fields=["favorite", "updated_at"])
            if tag.name != old_name:
                _reindex_tag_name(tag)
            after = {"tag": tag_json(tag)}
        elif action == "merge_tags":
            source = get_object_or_404(LibraryTag, pk=payload.get("source_id"))
            target = get_object_or_404(LibraryTag, pk=payload.get("target_id"))
            if source.pk == target.pk:
                raise TaggingError("合并目标不能同时作为待合并标签")
            # 1.26.27：跨类型也允许合并（类型跟着保留的那颗走）。
            _redirect_relations([source.pk], target.pk)
            before = {"source": tag_json(source), "target": tag_json(target), "assignments": []}
            rows = list(
                LibraryTagAssignment.objects.filter(
                    tag=source, publication__status=PublishedQuestion.Status.PUBLISHED
                ).select_related("publication")
            )
            for assignment in rows:
                snapshot = {
                    "publication_id": str(assignment.publication_id),
                    "revision": assignment.publication.tag_revision,
                    "row": {
                        "tag_id": str(assignment.tag_id),
                        "scope": assignment.scope,
                        "source": assignment.source,
                        "generation_source": assignment.generation_source,
                        "rationale": assignment.rationale,
                        "evidence": assignment.evidence,
                        "needs_review": assignment.needs_review,
                        "source_snapshot": assignment.source_snapshot,
                    },
                }
                other = LibraryTagAssignment.objects.filter(
                    publication_id=assignment.publication_id, tag=target
                ).first()
                snapshot["target_before"] = (
                    {
                        "tag_id": str(other.tag_id),
                        "scope": other.scope,
                        "source": other.source,
                        "generation_source": other.generation_source,
                        "rationale": other.rationale,
                        "evidence": other.evidence,
                        "needs_review": other.needs_review,
                        "source_snapshot": other.source_snapshot,
                    }
                    if other
                    else None
                )
                before["assignments"].append(snapshot)
                if other:
                    other.needs_review = False
                    other.source = LibraryTagAssignment.Source.LEGACY_HUMAN
                    other.rationale = (other.rationale + "；" if other.rationale else "") + "人工整理历史标签"
                    other.save(update_fields=["needs_review", "source", "rationale", "updated_at"])
                    assignment.delete()
                else:
                    assignment.tag = target
                    assignment.needs_review = False
                    assignment.source = LibraryTagAssignment.Source.LEGACY_HUMAN
                    assignment.rationale = (
                        assignment.rationale + "；" if assignment.rationale else ""
                    ) + "人工整理历史标签"
                    assignment.save(update_fields=["tag", "needs_review", "source", "rationale", "updated_at"])
                changed = PublishedQuestion.objects.filter(
                    pk=assignment.publication_id,
                    status=PublishedQuestion.Status.PUBLISHED,
                    tag_revision=assignment.publication.tag_revision,
                ).update(tag_revision=F("tag_revision") + 1)
                if changed != 1:
                    raise TaggingError("合并期间题目标签版本发生变化，请刷新后重试", 409)
                _sync_projection(assignment.publication_id)
            target.aliases = _clean_aliases([*(target.aliases or []), source.name, *(source.aliases or [])])
            target.save(update_fields=["aliases", "updated_at"])
            source.active = False
            source.save(update_fields=["active", "updated_at"])
            after = {
                "source": tag_json(source),
                "target": tag_json(target),
                "assignment_revisions": {
                    item["publication_id"]: PublishedQuestion.objects.get(pk=item["publication_id"]).tag_revision
                    for item in before["assignments"]
                },
            }
        elif action == "merge_tags_batch":
            before, after = _merge_catalogue_tags(payload.get("source_ids"), payload.get("target_id"))
            after["affected"] = impact_preview
        elif action == "apply_template":
            from .tag_catalogue_seed import blueprint_parents, template_blueprint

            was_initialized = catalogue_state().initialized
            # `group_keys` was the pre-1.25 name; a page opened before the
            # upgrade still sends it, and the chapters it meant are the same.
            keys = payload.get("section_keys", payload.get("group_keys"))
            blueprint = template_blueprint()
            if not isinstance(keys, list) or not keys or len(keys) > len(blueprint["sections"]):
                raise TaggingError("请选择要导入的模板章节")
            keys = list(dict.fromkeys(str(value) for value in keys))
            section_by_key = {row["key"]: row for row in blueprint["sections"]}
            if any(key not in section_by_key for key in keys):
                raise TaggingError("所选系统模板不存在，请刷新后重试", 409)
            created, linked = 0, 0
            created_tag_ids, template_bindings_added = [], []
            live_tags, live_sections = {}, {}

            def bind(spec, *, levels=None, description=None):
                """Find or create the tag for one blueprint row and remember it."""
                nonlocal created
                tag = LibraryTag.objects.filter(system_key=spec["key"]).first()
                if tag is None:
                    tag = LibraryTag.objects.filter(kind=spec["kind"], name__iexact=spec["name"]).first()
                if tag is None:
                    defaults = {
                        "name": spec["name"],
                        "kind": spec["kind"],
                        "description": description or spec.get("description", ""),
                        "color": spec.get("color", ""),
                        "aliases": spec.get("aliases", []),
                        "levels": levels if levels is not None else spec.get("levels", []),
                        "system_key": spec["key"],
                    }
                    if "conditions" in spec:
                        defaults["conditions"] = spec["conditions"]
                    tag = LibraryTag.objects.create(**defaults)
                    created += 1
                    created_tag_ids.append(str(tag.pk))
                elif tag.system_key is None:
                    # Explicit import can reuse a manually created concept.
                    # Bind its identity so a later rename is still recognized
                    # on repeat import; preserve and undo this metadata change.
                    template_bindings_added.append({"tag_id": str(tag.pk), "before": None, "after": spec["key"]})
                    tag.system_key = spec["key"]
                    tag.save(update_fields=["system_key", "updated_at"])
                return tag

            for key in keys:
                spec = section_by_key[key]
                name = spec["name"]
                if name.startswith("方法 · "):
                    levels = ["初中", "高中"]
                else:
                    levels = ["初中"] if name.startswith("初中") else ["高中"]
                section_tag = bind(spec, levels=levels, description="教材章节，用来给标签目录分层。")
                live_sections[key] = section_tag
                live_tags[key] = section_tag
            chosen = set(keys)
            spec_by_key = {spec["key"]: spec for spec in blueprint["tags"]}
            selected_keys = {spec["key"] for spec in blueprint["tags"] if chosen.intersection(spec["section_keys"])}
            direct_selection = set(selected_keys)
            todo = list(selected_keys)
            while todo:
                spec = spec_by_key[todo.pop()]
                for parent_key in spec.get("parent_keys", []):
                    if parent_key not in spec_by_key:
                        raise TaggingError("模板上级依赖不存在，不能导入", 409)
                    if parent_key not in selected_keys:
                        selected_keys.add(parent_key)
                        todo.append(parent_key)
            for spec in blueprint["tags"]:
                if spec["key"] not in selected_keys:
                    continue
                live_tags[spec["key"]] = bind(spec)
            relation_rows = _relation_snapshot()
            wanted_parents = blueprint_parents()
            for spec_key, tag in live_tags.items():
                if spec_key in live_sections:
                    continue
                for parent_key in wanted_parents[spec_key]:
                    if parent_key not in live_tags:
                        continue
                    relation_rows.append(
                        {
                            "parent_id": str(live_tags[parent_key].pk),
                            "child_id": str(tag.pk),
                            "relation_type": LibraryTagRelation.Type.HIERARCHY,
                        }
                    )
                for method_key in spec_by_key[spec_key].get("related_method_keys", []):
                    method = live_tags.get(method_key) or LibraryTag.objects.filter(system_key=method_key).first()
                    if method:
                        relation_rows.append(
                            {
                                "parent_id": str(tag.pk),
                                "child_id": str(method.pk),
                                "relation_type": LibraryTagRelation.Type.RELATED_METHOD,
                            }
                        )
            # An explicit template import augments existing identities too,
            # while retaining every user-made relationship and field value.
            relation_rows = [
                {"parent_id": parent, "child_id": child, "relation_type": kind}
                for parent, child, kind in sorted(
                    {(row["parent_id"], row["child_id"], row["relation_type"]) for row in relation_rows}
                )
            ]
            _restore_relations(relation_rows)
            linked = sum(1 for row in relation_rows if row["relation_type"] == LibraryTagRelation.Type.HIERARCHY)
            before = {"initialized": was_initialized}
            after = {
                "created_tags": created,
                "created_sections": len(live_sections),
                "hierarchy_count": linked,
                "section_keys": keys,
                "created_tag_ids": created_tag_ids,
                "dependency_tag_count": len(selected_keys - direct_selection),
                "template_bindings_added": template_bindings_added,
            }
        elif action in {"delete_tag", "delete_tags", "reset_empty"}:
            target_id = payload.get("tag_id") if action == "delete_tag" else None
            preview = impact_preview
            tags_query = LibraryTag.objects.all()
            if action == "delete_tag":
                tags = list(tags_query.filter(pk=target_id))
            elif action == "delete_tags":
                tags = _selected_tags(payload.get("tag_ids"))
            else:
                tags = list(tags_query.all())
            tag_ids = [tag.pk for tag in tags]
            assignments = list(
                LibraryTagAssignment.objects.filter(tag_id__in=tag_ids)
                .select_related("publication")
                .order_by("publication_id", "created_at", "id")
            )
            publications = {str(row.publication_id): row.publication for row in assignments}
            before = {
                "initialized": catalogue_state().initialized,
                "tags": [_full_tag_snapshot(tag) for tag in tags],
                "groups": (
                    [
                        {
                            "id": str(group.pk),
                            "name": group.name,
                            "color": group.color,
                            "position": group.position,
                            "system_key": group.system_key,
                        }
                        for group in LibraryTagGroup.objects.all()
                    ]
                    if action == "reset_empty"
                    else []
                ),
                "assignments": [_assignment_snapshot(row) for row in assignments],
                "revisions": {key: publication.tag_revision for key, publication in publications.items()},
                "publication_guards": {
                    key: _publication_guard(publication) for key, publication in publications.items()
                },
            }
            after_revisions = {}
            for key, publication in publications.items():
                expected_pub_revision = publication.tag_revision
                if (
                    PublishedQuestion.objects.filter(pk=publication.pk, tag_revision=expected_pub_revision).update(
                        tag_revision=F("tag_revision") + 1
                    )
                    != 1
                ):
                    raise TaggingError("删除期间题目标签版本发生变化，请重新查看影响范围", 409)
                after_revisions[key] = expected_pub_revision + 1
            LibraryTagAssignment.objects.filter(tag_id__in=tag_ids).delete()
            for tag in tags:
                tag.delete()
            if action == "reset_empty":
                LibraryTagGroup.objects.all().delete()
                # Tag work bound to the old directory cannot finish or be re-applied.
                active_jobs = LibraryJob.objects.filter(
                    kind=LibraryJob.Kind.TAGS, status__in=[LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING]
                )
                active_jobs.update(
                    status=LibraryJob.Status.FAILED, error="标签目录已清空，任务已取消。", updated_at=timezone.now()
                )
                LibraryTagSuggestion.objects.filter(
                    status__in=[
                        LibraryTagSuggestion.Status.QUEUED,
                        LibraryTagSuggestion.Status.RUNNING,
                        LibraryTagSuggestion.Status.PENDING,
                    ]
                ).update(
                    status=LibraryTagSuggestion.Status.STALE,
                    resolved_at=timezone.now(),
                    message="标签目录已清空；旧建议保留在历史中。",
                    updated_at=timezone.now(),
                )
            for publication_id in publications:
                _sync_projection(publication_id)
            after = {
                "revisions": after_revisions,
                "affected": preview,
                "initialized": True,
                "publication_guards": {
                    key: _publication_guard(PublishedQuestion.objects.get(pk=key)) for key in publications
                },
            }
        elif action == "create_group":
            # 1.27.0：建一个标签群组。老师要的是「把标签归到一组自己管」，
            # 不是「改标签的上下级」—— 这两件事在界面上是分开的。
            name = _clean_group_name(payload.get("name"))
            group = LibraryTagGroup.objects.create(
                name=name,
                color=_clean_color(payload.get("color")),
                position=_clean_group_position(payload.get("position")),
            )
            after = {"group": _group_snapshot(group)}
        elif action == "update_group":
            group = get_object_or_404(LibraryTagGroup, pk=payload.get("group_id"))
            before = {"group": _group_snapshot(group)}
            if "name" in payload:
                group.name = _clean_group_name(payload["name"])
            if "color" in payload:
                group.color = _clean_color(payload["color"])
            if "position" in payload:
                group.position = _clean_group_position(payload["position"])
            group.save()
            after = {"group": _group_snapshot(group)}
        elif action == "delete_group":
            group = get_object_or_404(LibraryTagGroup, pk=payload.get("group_id"))
            # 删组之前记下组里有哪些标签 —— 撤销要把它们放回原处。
            before = {
                "group": _group_snapshot(group),
                "tag_ids": sorted(str(value) for value in group.tags.values_list("id", flat=True)),
            }
            group.tags.clear()
            group.delete()
            after = {}
        elif action == "group_membership":
            group = get_object_or_404(LibraryTagGroup, pk=payload.get("group_id"))
            tag = get_object_or_404(LibraryTag, pk=payload.get("tag_id"))
            wanted = bool(payload.get("present"))
            present = group.tags.filter(pk=tag.pk).exists()
            if wanted == present:
                # 本来就是这样，不写操作记录 —— 撤销没有可还原的东西。
                before = {"group_id": str(group.pk), "tag_id": str(tag.pk), "present": present}
                after = {"group_id": str(group.pk), "tag_id": str(tag.pk), "present": present}
            else:
                before = {"group_id": str(group.pk), "tag_id": str(tag.pk), "present": present}
                group.tags.add(tag) if wanted else group.tags.remove(tag)
                after = {"group_id": str(group.pk), "tag_id": str(tag.pk), "present": wanted}
        elif action == "group_membership_batch":
            group = get_object_or_404(LibraryTagGroup, pk=payload.get("group_id"))
            raw_ids = payload.get("tag_ids")
            if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 500:
                raise TaggingError("请选择要归组的标签")
            tag_ids = [str(value) for value in raw_ids]
            tags = list(LibraryTag.objects.filter(pk__in=tag_ids))
            if len(tags) != len(set(tag_ids)):
                raise TaggingError("选中的标签有不存在或重复的，请刷新后重试", 409)
            wanted = bool(payload.get("present"))
            memberships = [{"tag_id": str(tag.pk), "present": group.tags.filter(pk=tag.pk).exists()} for tag in tags]
            before = {"group_id": str(group.pk), "memberships": memberships}
            for tag in tags:
                group.tags.add(tag) if wanted else group.tags.remove(tag)
            after = {
                "group_id": str(group.pk),
                "memberships": [{"tag_id": str(tag.pk), "present": wanted} for tag in tags],
            }
        else:
            raise TaggingError("不认识的目录操作")
        state = catalogue_state()
        if not state.initialized:
            state.initialized = True
            state.save(update_fields=["initialized"])
        stale_suggestions = LibraryTagSuggestion.objects.filter(
            taxonomy_revision=revision,
            status__in=[
                LibraryTagSuggestion.Status.QUEUED,
                LibraryTagSuggestion.Status.RUNNING,
                LibraryTagSuggestion.Status.PENDING,
            ],
        )
        _rebase_after_catalogue_change(stale_suggestions, new_revision)
        operation = LibraryTagOperation.objects.create(
            client_request_id=request_id,
            request_hash=digest,
            action="catalogue:" + action,
            before_snapshot={**before, "relations": before_relations},
            after_snapshot={**after, "revision": new_revision, "relations": _relation_snapshot()},
            catalogue_revision=new_revision,
        )
    return {"ok": True, "operation_id": str(operation.pk), "revision": new_revision, "catalogue": _catalogue_rows()}


def _split_by_catalogue_damage(candidates):
    """Split pending proposals into the ones a catalogue change broke and the ones it did not.

    A proposal only points at labels through the identifiers stored on each of its own
    entries: the label it wants to attach, and the label it wants to sit under. It goes
    stale when one of those is gone or no longer in use. Everything else - adding a
    label, renaming one, regrouping the tree - leaves it exactly as reviewable as before,
    and retiring the whole queue for that is what made the review impossible to work
    through: one new label threw away several hundred questions' worth of pending
    decisions. Untouched proposals are re-stamped onto the new revision instead.
    """
    alive = {str(tag.pk): tag for tag in LibraryTag.objects.all()}
    broken, intact = [], []
    for pk, entries in candidates.values_list("id", "suggestions"):
        damage = False
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            referenced = [entry.get("tag_id")] + list(entry.get("parent_ids") or [])
            for tag_id in referenced:
                if not tag_id:
                    continue
                tag = alive.get(str(tag_id))
                if tag is None or not tag.active:
                    damage = True
        (broken if damage else intact).append(pk)
    return broken, intact


def _rebase_after_catalogue_change(candidates, new_revision):
    """Keep proposals that survive a catalogue change actionable; retire the ones it broke."""
    broken, intact = _split_by_catalogue_damage(candidates)
    if broken:
        stale_rows = LibraryTagSuggestion.objects.filter(pk__in=broken)
        stale_job_ids = list(stale_rows.exclude(job_id=None).values_list("job_id", flat=True))
        stale_rows.update(
            status=LibraryTagSuggestion.Status.STALE,
            resolved_at=timezone.now(),
            message="标签目录已变化；旧建议保留在 AI 标签记录中。",
            updated_at=timezone.now(),
        )
        LibraryJob.objects.filter(
            pk__in=stale_job_ids, status__in=[LibraryJob.Status.QUEUED, LibraryJob.Status.RUNNING]
        ).update(status=LibraryJob.Status.FAILED, error="标签目录已变化，旧任务已取消。", updated_at=timezone.now())
    if intact:
        LibraryTagSuggestion.objects.filter(pk__in=intact).update(
            taxonomy_revision=new_revision, updated_at=timezone.now()
        )
    return len(broken), len(intact)


def assignments_view(request, publication_id):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    from .views import _body, _guard

    if request.method == "GET":
        rejected = _guard(request, json_body=False)
        if rejected:
            return rejected
        publication = get_object_or_404(PublishedQuestion, pk=publication_id)
        return JsonResponse(
            {
                "publication": str(publication.pk),
                "revision": publication.tag_revision,
                "assignments": [
                    {
                        "tag": tag_json(row.tag),
                        "scope": row.scope,
                        "source": row.source,
                        "generation_source": row.generation_source,
                        "rationale": row.rationale,
                        "evidence": row.evidence,
                        "needs_review": row.needs_review,
                    }
                    for row in LibraryTagAssignment.objects.filter(publication=publication)
                    .select_related("tag")
                    .order_by("created_at", "id")
                ],
                "suggestions": [suggestion_json(row) for row in publication.tag_suggestions.all()[:20]],
            }
        )
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求内容无法读取"}, status=400)
    payload = {**payload, "publication_ids": [str(publication_id)]}
    try:
        return JsonResponse(assign(payload))
    except TaggingError as error:
        return JsonResponse({"error": str(error)}, status=error.status)


def batch_assignments_view(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    from .views import _body, _guard

    if request.method == "GET":
        rejected = _guard(request, json_body=False)
        if rejected:
            return rejected
        raw = request.GET.get("ids", "")
        try:
            ids = list(dict.fromkeys(str(uuid.UUID(value)) for value in raw.split(",") if value))
        except (ValueError, TypeError):
            return JsonResponse({"error": "题库条目编号格式不正确"}, status=400)
        if not 1 <= len(ids) <= 500:
            return JsonResponse({"error": "请明确提供 1–500 道题"}, status=400)
        rows = list(PublishedQuestion.objects.filter(pk__in=ids, status=PublishedQuestion.Status.PUBLISHED))
        if {str(row.pk) for row in rows} != set(ids):
            return JsonResponse({"error": "有题目已不在正式题库，请刷新后重试"}, status=409)
        return JsonResponse(
            {
                "items": {
                    str(row.pk): {
                        "revision": row.tag_revision,
                        "assignments": [
                            {
                                "tag": tag_json(link.tag),
                                "scope": link.scope,
                                "source": link.source,
                                "needs_review": link.needs_review,
                            }
                            for link in LibraryTagAssignment.objects.filter(publication=row).select_related("tag")
                        ],
                    }
                    for row in rows
                }
            }
        )
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求内容无法读取"}, status=400)
    try:
        return JsonResponse(assign(payload))
    except TaggingError as error:
        return JsonResponse({"error": str(error)}, status=error.status)


def _legacy_data(publication):
    data = {
        "publication": str(publication.pk),
        "tags": formal_tag_names(publication),
        "catalogue": [],
        "max": None,
        "source": str((publication.extras or {}).get("tags_source") or ""),
        "at": str((publication.extras or {}).get("tags_at") or ""),
        "revision": publication.tag_revision,
    }
    every = {str(tag.pk): tag for tag in LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE)}
    context = relation_context()
    for tag in every.values():
        data["catalogue"].append({"point": tag.name, "chapter": "、".join(sorted(_chapter_names(tag, context, every)))})
    return data


@csrf_exempt
def legacy_tags_view(request, publication_id):
    """Compatibility endpoint for readers; legacy writes require CAS and only edit knowledge tags."""
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    from .views import _body, _guard

    if request.method == "GET":
        rejected = _guard(request, json_body=False)
        if rejected:
            return rejected
        return JsonResponse(_legacy_data(get_object_or_404(PublishedQuestion, pk=publication_id)))
    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if not isinstance(payload, dict) or "tags" not in payload:
        return JsonResponse({"error": "旧版保存请求缺少标签版本号，请刷新页面后重试"}, status=409)
    try:
        publication = get_object_or_404(PublishedQuestion, pk=publication_id)
        if type(payload.get("revision")) is not int or payload["revision"] != publication.tag_revision:
            raise TaggingError("标签已在别处修改，请刷新后重试", 409)
        values = payload["tags"]
        if not isinstance(values, list) or len(values) > 200:
            raise TaggingError("标签列表格式不正确，最多 200 项")
        current = set(formal_tag_names(publication))
        desired = {str(value) for value in values}
        if any(LibraryTag.objects.filter(name=value, kind=LibraryTag.Kind.KNOWLEDGE).count() != 1 for value in desired):
            raise TaggingError("所选知识点不在目录中")
        add_ids = [
            str(tag.pk) for tag in LibraryTag.objects.filter(name__in=desired - current, kind=LibraryTag.Kind.KNOWLEDGE)
        ]
        remove_ids = [
            str(tag.pk) for tag in LibraryTag.objects.filter(name__in=current - desired, kind=LibraryTag.Kind.KNOWLEDGE)
        ]
        result = assign(
            {
                "publication_ids": [str(publication.pk)],
                "add": add_ids,
                "remove": remove_ids,
                "expected_revisions": {str(publication.pk): publication.tag_revision},
                "request_id": payload.get("request_id") or uuid.uuid4(),
            }
        )
        publication.refresh_from_db()
        result.update({"tags": formal_tag_names(publication), "publication": library.publication_json(publication)})
        return JsonResponse(result)
    except TaggingError as error:
        return JsonResponse({"error": str(error)}, status=error.status)


def undo_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _guard

    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request) or {}
    try:
        operation_id = uuid.UUID(str(payload.get("operation_id")))
    except (ValueError, TypeError):
        return JsonResponse({"error": "撤销记录编号不正确"}, status=400)
    operation = get_object_or_404(LibraryTagOperation, pk=operation_id)
    if operation.undone_at:
        return JsonResponse({"ok": True, "replayed": True})
    if operation.action.startswith("catalogue:"):
        try:
            return JsonResponse(undo_catalogue_operation(operation))
        except (TaggingError, LibraryTag.DoesNotExist, LibraryTagGroup.DoesNotExist) as error:
            return JsonResponse({"error": str(error)}, status=getattr(error, "status", 409))
    if operation.action not in {"assign", "replace", "suggestion:accept", "suggestion:reject"}:
        return JsonResponse({"error": "此类标签操作不能撤销"}, status=409)
    before = operation.before_snapshot.get("assignments", {})
    after_revisions = operation.after_snapshot.get("revisions", {})
    undone = {}
    try:
        with transaction.atomic():
            created_tag_ids = operation.after_snapshot.get("created_tag_ids", [])
            retired_tags = operation.after_snapshot.get("retired_tags", [])
            suggestion_snapshot = operation.before_snapshot.get("suggestion") or {}
            if (created_tag_ids or retired_tags) and LibraryTagCatalogue.objects.filter(
                pk=1, revision=operation.catalogue_revision
            ).update(
                revision=F("revision") + 1, initialized=suggestion_snapshot.get("catalogue_initialized", True)
            ) != 1:
                raise TaggingError("目录后来有过修改，新建或转正的标签不能安全撤销", 409)
            for publication_id in operation.publication_ids:
                expected = after_revisions.get(publication_id)
                publication = PublishedQuestion.objects.get(
                    pk=publication_id, status=PublishedQuestion.Status.PUBLISHED
                )
                if publication.tag_revision != expected:
                    raise TaggingError("这道题后来又改过标签，不能直接撤销覆盖", 409)
                _bump_publication(publication, expected)
                LibraryTagAssignment.objects.filter(publication=publication).delete()
                for entry in before.get(publication_id, []):
                    LibraryTagAssignment.objects.create(
                        publication=publication,
                        tag=LibraryTag.objects.get(pk=entry["tag_id"]),
                        scope=entry.get("scope") or {},
                        source=entry.get("source", LibraryTagAssignment.Source.HUMAN),
                        generation_source=entry.get("generation_source", ""),
                        rationale=entry.get("rationale", ""),
                        evidence=entry.get("evidence", ""),
                        needs_review=entry.get("needs_review", False),
                        source_snapshot=entry.get("source_snapshot") or {},
                    )
                _sync_projection(publication.pk)
                undone[publication_id] = expected + 1
                if operation.action.startswith("suggestion:"):
                    snapshot = suggestion_snapshot
                    if snapshot:
                        suggestion = LibraryTagSuggestion.objects.get(pk=snapshot["id"])
                        suggestion.suggestions = snapshot.get("suggestions", [])
                        suggestion.status = snapshot.get("status", LibraryTagSuggestion.Status.PENDING)
                        suggestion.resolved_at = (
                            datetime.fromisoformat(snapshot["resolved_at"]) if snapshot.get("resolved_at") else None
                        )
                        suggestion.assignment_revision = expected + 1
                        if created_tag_ids or retired_tags:
                            suggestion.taxonomy_revision = catalogue_state().revision
                        suggestion.save(
                            update_fields=[
                                "suggestions",
                                "status",
                                "resolved_at",
                                "assignment_revision",
                                "taxonomy_revision",
                                "updated_at",
                            ]
                        )
                    for tag_id in created_tag_ids:
                        tag = LibraryTag.objects.filter(pk=tag_id).first()
                        if tag is not None and not tag.assignments.exists():
                            tag.delete()
            for value in retired_tags:
                retired = LibraryTag.objects.filter(pk=value["id"]).first()
                if retired is None:
                    continue
                retired.kind = value.get("kind", retired.kind)
                retired.levels = value.get("levels", [])
                retired.active = value.get("active", True)
                retired.save(update_fields=["kind", "levels", "active", "updated_at"])
            operation.undone_at = timezone.now()
            operation.save(update_fields=["undone_at"])
    except (TaggingError, PublishedQuestion.DoesNotExist, LibraryTag.DoesNotExist) as error:
        return JsonResponse({"error": str(error)}, status=getattr(error, "status", 409))
    return JsonResponse({"ok": True, "revisions": undone, "message": "标签修改已撤销"})


def _restore_tag(tag, value):
    tag.name = value["name"]
    tag.kind = value["kind"]
    tag.description = value.get("description", "")
    tag.color = value.get("color", "")
    tag.aliases = value.get("aliases", [])
    tag.levels = value.get("levels", [])
    tag.conditions = value.get("conditions", "")
    tag.active = value.get("active", True)
    tag.favorite = value.get("favorite", False)
    tag.system_key = value.get("system_key")
    tag.save()
    tag.groups.set(value.get("group_ids", [item["id"] for item in value.get("groups", [])]))


def _create_snapshot_tag(value):
    tag = LibraryTag.objects.create(id=value["id"], name=value["name"], kind=value["kind"])
    _restore_tag(tag, value)
    return tag


def undo_catalogue_operation(operation):
    expected = operation.catalogue_revision
    with transaction.atomic():
        if operation.undone_at:
            return {"ok": True, "replayed": True}
        if LibraryTagCatalogue.objects.filter(pk=1, revision=expected).update(revision=F("revision") + 1) != 1:
            raise TaggingError("标签目录后来又有修改，不能直接撤销覆盖", 409)
        action = operation.action.removeprefix("catalogue:")
        before, after = operation.before_snapshot, operation.after_snapshot
        if "relations" in after and _relation_snapshot() != after["relations"]:
            raise TaggingError("标签关系后来有变化，不能直接撤销覆盖", 409)
        if action in {"set_parents", "add_parent", "remove_parent", "move_parent", "set_related_methods"}:
            pass  # Shared relationship snapshot is restored below.
        elif action == "create_tag":
            tag = LibraryTag.objects.get(pk=after["tag"]["id"])
            if tag.assignments.exists():
                tag.active = False
                tag.save(update_fields=["active", "updated_at"])
            else:
                tag.delete()
        elif action == "create_tags_batch":
            # 1.26.6：整棵树一起撤。已经贴到题上的标签不删（那会让题的标签凭空消失），
            # 只停用；没用过的直接删掉。上下级关系由共享的关系快照统一还原。
            for tag_id in after.get("created_tag_ids") or []:
                tag = LibraryTag.objects.filter(pk=tag_id).first()
                if tag is None:
                    continue
                if tag.assignments.exists():
                    tag.active = False
                    tag.save(update_fields=["active", "updated_at"])
                else:
                    tag.delete()
        elif action in {"update_tag", "deactivate_tag", "activate_tag", "favorite_tag"}:
            tag = LibraryTag.objects.get(pk=before["tag"]["id"])
            old_name = tag.name
            _restore_tag(tag, before["tag"])
            if tag.name != old_name:
                _reindex_tag_name(tag)
        elif action == "merge_tags":
            revisions = after.get("assignment_revisions", {})
            for item in before.get("assignments", []):
                publication = PublishedQuestion.objects.get(
                    pk=item["publication_id"], status=PublishedQuestion.Status.PUBLISHED
                )
                if publication.tag_revision != revisions.get(item["publication_id"]):
                    raise TaggingError("合并涉及的题目后来改过标签，不能直接撤销", 409)
                _bump_publication(publication, publication.tag_revision)
                row = item["row"]
                tag = LibraryTag.objects.get(pk=row["tag_id"])
                existing = LibraryTagAssignment.objects.filter(publication=publication, tag=tag).first()
                if existing:
                    for field in (
                        "scope",
                        "source",
                        "generation_source",
                        "rationale",
                        "evidence",
                        "needs_review",
                        "source_snapshot",
                    ):
                        setattr(existing, field, row.get(field, {} if field in {"scope", "source_snapshot"} else ""))
                    existing.save()
                else:
                    LibraryTagAssignment.objects.create(
                        publication=publication,
                        tag=tag,
                        scope=row.get("scope") or {},
                        source=row.get("source", LibraryTagAssignment.Source.HUMAN),
                        generation_source=row.get("generation_source", ""),
                        rationale=row.get("rationale", ""),
                        evidence=row.get("evidence", ""),
                        needs_review=row.get("needs_review", False),
                        source_snapshot=row.get("source_snapshot") or {},
                    )
                target_before = item.get("target_before")
                if target_before:
                    target_tag = LibraryTag.objects.get(pk=target_before["tag_id"])
                    target_row, _ = LibraryTagAssignment.objects.get_or_create(publication=publication, tag=target_tag)
                    for field in (
                        "scope",
                        "source",
                        "generation_source",
                        "rationale",
                        "evidence",
                        "needs_review",
                        "source_snapshot",
                    ):
                        setattr(
                            target_row,
                            field,
                            target_before.get(field, {} if field in {"scope", "source_snapshot"} else ""),
                        )
                    target_row.save()
                _sync_projection(publication.pk)
            _restore_tag(LibraryTag.objects.get(pk=before["source"]["id"]), before["source"])
            _restore_tag(LibraryTag.objects.get(pk=before["target"]["id"]), before["target"])
        elif action == "merge_tags_batch":
            target_id = before["target"]["id"]
            target = LibraryTag.objects.filter(pk=target_id).first()
            if target is None or _full_tag_snapshot(target) != after.get("target"):
                raise TaggingError("合并目标后来有变化，不能直接撤销覆盖", 409)
            revisions = after.get("revisions", {})
            for publication_id, expected_revision in revisions.items():
                publication = PublishedQuestion.objects.filter(pk=publication_id).first()
                if publication is None or publication.tag_revision != expected_revision:
                    raise TaggingError("合并涉及的题目后来改过标签，不能直接撤销", 409)
                if _publication_guard(publication) != after.get("publication_guards", {}).get(publication_id):
                    raise TaggingError("题目内容、审核状态或答案后来发生变化，不能直接撤销覆盖", 409)
            current_rows = [
                _assignment_snapshot(row)
                for row in LibraryTagAssignment.objects.filter(publication_id__in=revisions, tag_id=target_id).order_by(
                    "publication_id", "created_at", "id"
                )
            ]
            if current_rows != after.get("assignments", []):
                raise TaggingError("合并后的标签关联后来有变化，不能直接撤销覆盖", 409)
            for source in before["sources"]:
                if (
                    LibraryTag.objects.filter(pk=source["id"]).exists()
                    or LibraryTag.objects.filter(kind=source["kind"], name__iexact=source["name"]).exists()
                ):
                    raise TaggingError("已有相同编号或同名标签，不能安全撤销合并", 409)
                _create_snapshot_tag(source)
            _restore_tag(target, before["target"])
            LibraryTagAssignment.objects.filter(publication_id__in=revisions, tag_id=target_id).delete()
            for row in before.get("assignments", []):
                LibraryTagAssignment.objects.create(
                    id=row["id"],
                    publication_id=row["publication_id"],
                    tag_id=row["tag_id"],
                    scope=row.get("scope") or {},
                    source=row.get("source", LibraryTagAssignment.Source.HUMAN),
                    generation_source=row.get("generation_source", ""),
                    rationale=row.get("rationale", ""),
                    evidence=row.get("evidence", ""),
                    needs_review=row.get("needs_review", False),
                    source_snapshot=row.get("source_snapshot") or {},
                )
            for publication_id, expected_revision in revisions.items():
                if (
                    PublishedQuestion.objects.filter(pk=publication_id, tag_revision=expected_revision).update(
                        tag_revision=F("tag_revision") + 1
                    )
                    != 1
                ):
                    raise TaggingError("撤销期间题目标签版本发生变化，请重试", 409)
                _sync_projection(publication_id)
        elif action == "create_group":
            group = LibraryTagGroup.objects.get(pk=after["group"]["id"])
            if group.tags.exists():
                raise TaggingError("这个分组已被标签使用，请先移出标签再撤销", 409)
            group.delete()
        elif action == "update_group":
            group = LibraryTagGroup.objects.get(pk=before["group"]["id"])
            group.name, group.color, group.position = (
                before["group"]["name"],
                before["group"]["color"],
                before["group"]["position"],
            )
            group.save()
        elif action == "group_membership":
            group = LibraryTagGroup.objects.get(pk=before["group_id"])
            tag = LibraryTag.objects.get(pk=before["tag_id"])
            group.tags.add(tag) if before["present"] else group.tags.remove(tag)
        elif action == "group_membership_batch":
            group = LibraryTagGroup.objects.get(pk=before["group_id"])
            for membership in before["memberships"]:
                tag = LibraryTag.objects.get(pk=membership["tag_id"])
                group.tags.add(tag) if membership["present"] else group.tags.remove(tag)
        elif action == "apply_template":
            # Assignment edits do not change the catalogue revision, so guard
            # newly created template tags explicitly before removal.  Chapters
            # are ordinary tags now, so deleting created_tag_ids removes them
            # too; the hierarchy itself is restored from before["relations"].
            created_ids = after.get("created_tag_ids", [])
            if LibraryTagAssignment.objects.filter(tag_id__in=created_ids).exists():
                raise TaggingError("模板中的新标签已经用于题目，不能直接撤销", 409)
            for binding in after.get("template_bindings_added", []):
                tag = LibraryTag.objects.filter(pk=binding["tag_id"]).first()
                if tag is None or tag.system_key != binding["after"]:
                    raise TaggingError("模板身份关联后来有变化，不能直接撤销覆盖", 409)
                tag.system_key = binding["before"]
                tag.save(update_fields=["system_key", "updated_at"])
            for tag_id in after.get("created_tag_ids", []):
                LibraryTag.objects.filter(pk=tag_id).delete()
            state = catalogue_state()
            state.initialized = before.get("initialized", False)
            state.save(update_fields=["initialized"])
        elif action == "delete_group":
            # Pre-1.25 operations only.  Groups can no longer be created, so
            # this branch exists purely to repair an old operation record.
            group_data = before["group"]
            if LibraryTagGroup.objects.filter(pk=group_data["id"]).exists():
                raise TaggingError("同一分组编号已被重新使用，不能安全撤销", 409)
            if LibraryTagGroup.objects.filter(name=group_data["name"]).exists():
                raise TaggingError("已有同名分组，不能安全撤销", 409)
            group = LibraryTagGroup.objects.create(
                id=group_data["id"],
                name=group_data["name"],
                color=group_data["color"],
                position=group_data["position"],
                system_key=group_data.get("system_key"),
            )
            group.tags.set(before.get("tag_ids", []))
        elif action in {"delete_tag", "delete_tags", "reset_empty"}:
            after_revisions = after.get("revisions", {})
            guards = after.get("publication_guards", {})
            for publication_id, expected_revision in after_revisions.items():
                publication = PublishedQuestion.objects.filter(pk=publication_id).first()
                if publication is None or publication.tag_revision != expected_revision:
                    raise TaggingError("受影响题目后来有过标签修改，不能直接撤销覆盖", 409)
                if _publication_guard(publication) != guards.get(publication_id):
                    raise TaggingError("题目内容、审核状态或答案后来发生变化，不能直接撤销覆盖", 409)
            if action == "reset_empty" and (LibraryTag.objects.exists() or LibraryTagGroup.objects.exists()):
                raise TaggingError("目录已经有新内容，不能安全恢复清空前目录", 409)
            for group_data in before.get("groups", []):
                if LibraryTagGroup.objects.filter(name=group_data["name"]).exists():
                    raise TaggingError("已有同名分组，不能安全撤销", 409)
                LibraryTagGroup.objects.create(
                    id=group_data["id"],
                    name=group_data["name"],
                    color=group_data["color"],
                    position=group_data["position"],
                    system_key=group_data.get("system_key"),
                )
            for tag_data in before.get("tags", []):
                if LibraryTag.objects.filter(kind=tag_data["kind"], name=tag_data["name"]).exists():
                    raise TaggingError("已有同名标签，不能安全撤销", 409)
                tag = LibraryTag.objects.create(
                    id=tag_data["id"],
                    name=tag_data["name"],
                    kind=tag_data["kind"],
                    description=tag_data.get("description", ""),
                    color=tag_data.get("color", ""),
                    aliases=tag_data.get("aliases", []),
                    levels=tag_data.get("levels", []),
                    conditions=tag_data.get("conditions", ""),
                    active=tag_data.get("active", True),
                    favorite=tag_data.get("favorite", False),
                    system_key=tag_data.get("system_key"),
                )
                tag.groups.set(tag_data.get("group_ids", []))
            for row in before.get("assignments", []):
                LibraryTagAssignment.objects.create(
                    id=row.get("id") or uuid.uuid4(),
                    publication_id=row["publication_id"],
                    tag_id=row["tag_id"],
                    scope=row.get("scope") or {},
                    source=row.get("source", LibraryTagAssignment.Source.HUMAN),
                    generation_source=row.get("generation_source", ""),
                    rationale=row.get("rationale", ""),
                    evidence=row.get("evidence", ""),
                    needs_review=row.get("needs_review", False),
                    source_snapshot=row.get("source_snapshot") or {},
                )
            for publication_id, expected_revision in after_revisions.items():
                publication = PublishedQuestion.objects.get(pk=publication_id)
                if (
                    PublishedQuestion.objects.filter(pk=publication.pk, tag_revision=expected_revision).update(
                        tag_revision=F("tag_revision") + 1
                    )
                    != 1
                ):
                    raise TaggingError("撤销期间题目标签版本发生变化，请重试", 409)
                _sync_projection(publication_id)
            state = catalogue_state()
            state.initialized = before.get("initialized", True)
            state.save(update_fields=["initialized"])
        else:
            raise TaggingError("此类目录操作不能撤销", 409)
        if "relations" in before:
            _restore_relations(before["relations"])
        # Undo only ever moves the catalogue forward in revision, so every still-pending
        # proposal would be left stamped on a revision that no longer exists and could
        # never be accepted again. Re-align them against the restored directory.
        _rebase_after_catalogue_change(
            LibraryTagSuggestion.objects.filter(
                status__in=[
                    LibraryTagSuggestion.Status.QUEUED,
                    LibraryTagSuggestion.Status.RUNNING,
                    LibraryTagSuggestion.Status.PENDING,
                ]
            ),
            expected + 1,
        )
        operation.undone_at = timezone.now()
        operation.save(update_fields=["undone_at"])
    return {"ok": True, "revision": expected + 1, "message": "目录操作已撤销", "catalogue": _catalogue_rows()}


def operations_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    rows = LibraryTagOperation.objects.order_by("-created_at", "-id")
    if request.GET.get("request_id"):
        try:
            rows = rows.filter(client_request_id=_request_id(request.GET["request_id"]))
        except TaggingError as error:
            return JsonResponse({"error": str(error)}, status=error.status)
    rows = rows[:100]
    return JsonResponse(
        {
            "operations": [
                {
                    "id": str(row.pk),
                    "action": row.action,
                    "request_id": str(row.client_request_id),
                    "revisions": row.after_snapshot.get("revisions", {}),
                    "after_snapshot": copy.deepcopy(row.after_snapshot),
                    "publication_ids": row.publication_ids,
                    "undone": bool(row.undone_at),
                    "blocked_reason": row.blocked_reason,
                    "created_at": row.created_at.isoformat(),
                    "catalogue_revision": row.catalogue_revision,
                    "can_undo": not row.undone_at,
                }
                for row in rows
            ]
        }
    )


def migration_report_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    from .library_tag_reports import legacy_tag_migration_markdown

    response = HttpResponse(legacy_tag_migration_markdown(), content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = "attachment; filename=library-tag-migration.md"
    return response


def suggestions_view(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from .views import _guard

    rejected = _guard(request, json_body=False)
    if rejected:
        return rejected
    status = request.GET.get("status", "pending")
    allowed = {choice for choice, _label in LibraryTagSuggestion.Status.choices}
    if status not in allowed | {"all"}:
        return JsonResponse({"error": "建议状态不正确"}, status=400)
    try:
        limit = min(100, max(1, int(request.GET.get("limit", "40"))))
        offset = max(0, int(request.GET.get("offset", "0")))
    except ValueError:
        return JsonResponse({"error": "limit 和 offset 必须是整数"}, status=400)
    rows = LibraryTagSuggestion.objects.select_related("publication").order_by("-created_at", "-id")
    publication_id = request.GET.get("publication_id", "").strip()
    if publication_id:
        try:
            publication_id = str(uuid.UUID(publication_id))
        except (ValueError, TypeError):
            return JsonResponse({"error": "题目编号格式不正确"}, status=400)
        rows = rows.filter(publication_id=publication_id)
    if status != "all":
        rows = rows.filter(status=status)
    total = rows.count()
    items = []
    for row in rows[offset : offset + limit]:
        content = row.publication.content or {}
        question = row.publication.question if row.publication.question_id else None
        group = question.group if question and question.group_id else None
        items.append(
            {
                **suggestion_json(row),
                "number": row.publication.number,
                # 题组的字段叫 title（1.26.7：这里写成了 .name，只要那道题属于某个题组
                # ——也就是书里的题 —— 这个接口就 500，页面拿到 HTML 就报"不是合法 JSON"）。
                "group": group.title if group else "",
                "source_filename": row.publication.source_filename,
                "question": str(content.get("stem") or content.get("body") or "原图题，暂不支持文字全文搜索")[:500],
                "revision": row.publication.tag_revision,
            }
        )
    return JsonResponse({"status": status, "total": total, "items": items, "has_more": offset + len(items) < total})


def resolve_suggestion_view(request, suggestion_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _guard

    rejected = _guard(request)
    if rejected:
        return rejected
    payload = _body(request)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求内容无法读取"}, status=400)
    try:
        result = resolve_suggestion(suggestion_id, payload)
        return JsonResponse(result)
    except StaleTagSuggestionError as error:
        # resolve_suggestion runs atomically, so its stale marker is rolled back
        # with the rejected write. Persist the terminal state after that rollback.
        with transaction.atomic():
            suggestion = LibraryTagSuggestion.objects.select_for_update().filter(pk=suggestion_id).first()
            if suggestion and suggestion.status == LibraryTagSuggestion.Status.PENDING:
                suggestion.status = LibraryTagSuggestion.Status.STALE
                suggestion.message = str(error)
                suggestion.resolved_at = timezone.now()
                suggestion.save(update_fields=["status", "message", "resolved_at", "updated_at"])
        return JsonResponse({"error": str(error), "stale": True}, status=error.status)
    except TaggingError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except IntegrityError:
        return JsonResponse({"error": "同类型标签已存在，请选择现有标签"}, status=409)


def resolve_suggestion(suggestion_id, payload):
    action = payload.get("action")
    if action not in {"accept", "reject"}:
        raise TaggingError("请选择采纳或拒绝")
    indices = payload.get("indices")
    if (
        not isinstance(indices, list)
        or not indices
        or len(indices) > 6
        or any(type(value) is not int for value in indices)
    ):
        raise TaggingError("请选择一条或多条建议")
    request_id = _request_id(payload.get("request_id"))
    digest = _request_hash(
        {
            "suggestion_id": str(suggestion_id),
            "action": action,
            "indices": indices,
            "expected_revision": payload.get("expected_revision"),
            "new_tags": payload.get("new_tags", {}),
        }
    )
    with transaction.atomic():
        prior = LibraryTagOperation.objects.filter(client_request_id=request_id).first()
        if prior:
            if prior.request_hash != digest:
                raise TaggingError("同一操作编号不能用于不同的建议处理", 409)
            return {"ok": True, "replayed": True}
        suggestion = LibraryTagSuggestion.objects.select_for_update().get(pk=suggestion_id)
        publication = PublishedQuestion.objects.select_for_update().get(pk=suggestion.publication_id)
        if suggestion.status != LibraryTagSuggestion.Status.PENDING:
            raise TaggingError("这批建议当前不在待核对状态", 409)
        if publication.status != PublishedQuestion.Status.PUBLISHED:
            raise StaleTagSuggestionError("题目已被替换，建议保留供查看，不能采纳", 409)
        expected = _expected_revision(payload.get("expected_revision"))
        if publication.tag_revision != expected or suggestion.assignment_revision != expected:
            raise StaleTagSuggestionError("题目标签后来有过修改，这批建议已过期，请重新建议。", 409)
        if catalogue_state().revision != suggestion.taxonomy_revision:
            raise StaleTagSuggestionError("标签目录已变化，请按新目录重新建议。", 409)
        if suggestion.question_fingerprint and suggestion.question_fingerprint != library.generation_fingerprint(
            publication.content or {}, publication.pk
        ):
            raise StaleTagSuggestionError("题面或配图已变化，请重新生成建议。", 409)
        requested_new_tags = payload.get("new_tags") if isinstance(payload.get("new_tags"), dict) else {}
        entries = copy.deepcopy(suggestion.suggestions or [])
        if len(set(indices)) != len(indices) or min(indices) < 0 or max(indices) >= len(entries):
            raise TaggingError("所选建议已变化，请刷新后重试", 409)
        if any(entries[index].get("decision") for index in indices):
            raise TaggingError("所选建议已处理，请刷新后选择未处理的项目", 409)
        needs_method_source = any(entries[index].get("kind") == LibraryTag.Kind.METHOD for index in indices)
        needs_method_source = needs_method_source or any(
            isinstance(requested_new_tags.get(str(index)), dict)
            and requested_new_tags[str(index)].get("kind") == LibraryTag.Kind.METHOD
            for index in indices
        )
        if needs_method_source:
            context = current_method_source(publication)
            current_source = (
                {
                    key: context[key]
                    for key in ("source", "solution_id", "publication_id", "analysis_hash", "image_hashes")
                    if key in context
                }
                if context
                else {}
            )
            if current_source != (suggestion.method_source or {}):
                raise StaleTagSuggestionError("采用的解析版本或配图已变化，这批方法建议已过期，请重新建议。", 409)
        before = _snapshot_assignments(publication)
        suggestion_before = {
            "id": str(suggestion.pk),
            "suggestions": copy.deepcopy(suggestion.suggestions),
            "status": suggestion.status,
            "resolved_at": suggestion.resolved_at.isoformat() if suggestion.resolved_at else None,
            "catalogue_initialized": catalogue_state().initialized,
        }
        new_catalogue_tag = False
        created_tag_ids = []
        retired_tags = []
        directory_was_empty = not LibraryTag.objects.exists()
        if action == "accept":
            new_values = payload.get("new_tags", {})
            if not isinstance(new_values, dict):
                raise TaggingError("新标签信息不正确")
            for index in indices:
                item = entries[index]
                tag = None
                if item.get("tag_id"):
                    tag = LibraryTag.objects.filter(pk=item["tag_id"], active=True).first()
                if tag is None and item.get("new_name"):
                    override = new_values.get(str(index), {})
                    if not isinstance(override, dict):
                        raise TaggingError("新标签信息不正确")
                    if override.get("tag_id"):
                        tag = LibraryTag.objects.filter(pk=override["tag_id"], active=True).first()
                        if tag is None or tag.kind != item.get("kind"):
                            raise TaggingError("请选择同类型的有效标签", 409)
                    else:
                        name = _clean_name(override.get("name", item["new_name"]))
                        kind = override.get("kind", item.get("kind"))
                        if kind not in {LibraryTag.Kind.KNOWLEDGE, LibraryTag.Kind.METHOD}:
                            raise TaggingError("新 AI 标签类型应为知识点或解题方法")
                        scope = override.get("scope", item.get("scope") or {"whole": True})
                        if kind == LibraryTag.Kind.KNOWLEDGE and scope != {"whole": True}:
                            raise TaggingError("改为知识点后，适用范围必须是整题")
                        if kind == LibraryTag.Kind.METHOD and scope != {"whole": True}:
                            subquestions = scope.get("subquestions") if isinstance(scope, dict) else None
                            if (
                                not isinstance(subquestions, list)
                                or not 1 <= len(subquestions) <= 12
                                or any(
                                    not isinstance(part, str) or not re.fullmatch(r"[1-9][0-9]?", part)
                                    for part in subquestions
                                )
                            ):
                                raise TaggingError("改为解题方法后，请选择整题或具体小问")
                        if kind == LibraryTag.Kind.METHOD and not suggestion.method_source:
                            raise TaggingError("没有可核对的已保存解析，不能把建议改为解题方法")
                        tag = LibraryTag.objects.filter(kind=kind, name__iexact=name).first()
                        if tag is None:
                            matches = [
                                candidate
                                for candidate in LibraryTag.objects.filter(kind=kind, active=True)
                                if any(normalized(alias) == normalized(name) for alias in candidate.aliases or [])
                            ]
                            tag = matches[0] if len(matches) == 1 else None
                        if tag is None:
                            # The exact name may already sit in the library under a spelling
                            # the database comparison above cannot match, or as a label the
                            # directory retired instead of deleting.
                            same_name_active, retired = tag_name_candidates(name, kind)
                            if same_name_active is not None:
                                tag = same_name_active
                            elif retired is not None:
                                # Putting a retired label back into use is the review it was
                                # waiting for, so the teacher chooses the school stage. The
                                # stage carried by the old proposal is never reused here: those
                                # rows came across unverified.
                                levels = override.get("levels")
                                if (
                                    not isinstance(levels, list)
                                    or not levels
                                    or any(level not in {"初中", "高中"} for level in levels)
                                ):
                                    raise TaggingError("请先选择这个旧标签适用的学段（初中或高中）")
                                retired_tags.append(
                                    {
                                        "id": str(retired.pk),
                                        "kind": retired.kind,
                                        "levels": retired.levels or [],
                                        "active": retired.active,
                                    }
                                )
                                retired.kind, retired.levels, retired.active = kind, levels, True
                                retired.save(update_fields=["kind", "levels", "active", "updated_at"])
                                tag = retired
                                # Deliberately no `new_catalogue_tag`: a retired label was never
                                # in the directory the model was shown, so switching it back on
                                # cannot invalidate any proposal. Every other pending proposal
                                # naming it only becomes easier to accept, and bumping the
                                # revision here would throw away the whole review queue.
                        if tag is None:
                            levels = override.get("levels", item.get("levels", []))
                            if (
                                not isinstance(levels, list)
                                or not levels
                                or any(level not in {"初中", "高中"} for level in levels)
                            ):
                                raise TaggingError("适用学段不正确")
                            tag = LibraryTag.objects.create(
                                name=name,
                                kind=kind,
                                description=str(override.get("description") or item.get("description") or "")[:300],
                                aliases=_clean_aliases(override.get("aliases", [])),
                                conditions=str(override.get("conditions") or "")[:2000],
                                levels=levels,
                            )
                            new_catalogue_tag = True
                            created_tag_ids.append(str(tag.pk))
                            parents = override.get("parent_ids", item.get("parent_ids", []))
                            if not parents and not directory_was_empty and override.get("top_level") is not True:
                                raise TaggingError("请给新标签选择上级，或明确设为顶级标签")
                            parent_ids = _relation_ids(parents)
                            parent_rows = list(LibraryTag.objects.filter(pk__in=parent_ids, active=True, kind=kind))
                            if len(parent_rows) != len(parent_ids):
                                raise TaggingError("新标签上级须为同类型的在用标签", 409)
                            _set_relation_neighbors(tag, parent_ids, LibraryTagRelation.Type.HIERARCHY)
                        item["scope"] = scope
                if not tag:
                    raise TaggingError("建议中的标签已停用或不存在，请改选后重试", 409)
                existing = LibraryTagAssignment.objects.filter(publication=publication, tag=tag).first()
                if existing and existing.needs_review:
                    existing.needs_review = False
                    existing.source = LibraryTagAssignment.Source.AI_ACCEPTED
                    existing.generation_source = suggestion.generation_source
                    existing.rationale = str(item.get("rationale") or "")[:500]
                    existing.evidence = str(item.get("evidence") or "")[:500]
                    existing.scope = item.get("scope") or {"whole": True}
                    existing.source_snapshot = {
                        "suggestion_id": str(suggestion.pk),
                        "method_source": suggestion.method_source,
                    }
                    existing.save()
                elif not existing:
                    LibraryTagAssignment.objects.create(
                        publication=publication,
                        tag=tag,
                        source=LibraryTagAssignment.Source.AI_ACCEPTED,
                        generation_source=suggestion.generation_source,
                        rationale=str(item.get("rationale") or "")[:500],
                        evidence=str(item.get("evidence") or "")[:500],
                        scope=item.get("scope") or {"whole": True},
                        source_snapshot={
                            "suggestion_id": str(suggestion.pk),
                            "method_source": suggestion.method_source,
                        },
                    )
                entries[index]["decision"] = "accepted"
                entries[index]["accepted_tag_id"] = str(tag.pk)
        else:
            for index in indices:
                entries[index]["decision"] = "rejected"
        remaining = [entry for entry in entries if not entry.get("decision")]
        if new_catalogue_tag:
            if (
                LibraryTagCatalogue.objects.filter(pk=1, revision=suggestion.taxonomy_revision).update(
                    revision=F("revision") + 1, initialized=True
                )
                != 1
            ):
                raise TaggingError("标签目录已在别处修改，请刷新后重试", 409)
            suggestion.taxonomy_revision += 1
            stale_suggestions = LibraryTagSuggestion.objects.exclude(pk=suggestion.pk).filter(
                taxonomy_revision=suggestion.taxonomy_revision - 1,
                status__in=[
                    LibraryTagSuggestion.Status.QUEUED,
                    LibraryTagSuggestion.Status.RUNNING,
                    LibraryTagSuggestion.Status.PENDING,
                ],
            )
            _rebase_after_catalogue_change(stale_suggestions, suggestion.taxonomy_revision)
        _bump_publication(publication, expected)
        suggestion.assignment_revision = expected + 1
        suggestion.suggestions = entries
        suggestion.status = (
            LibraryTagSuggestion.Status.PENDING
            if remaining
            else (
                LibraryTagSuggestion.Status.ACCEPTED
                if any(entry.get("decision") == "accepted" for entry in entries)
                else LibraryTagSuggestion.Status.REJECTED
            )
        )
        suggestion.resolved_at = None if remaining else timezone.now()
        suggestion.save(
            update_fields=[
                "suggestions",
                "status",
                "resolved_at",
                "taxonomy_revision",
                "assignment_revision",
                "updated_at",
            ]
        )
        _sync_projection(publication.pk)
        after_rev = expected + 1
        op = LibraryTagOperation.objects.create(
            client_request_id=request_id,
            request_hash=digest,
            action="suggestion:" + action,
            publication_ids=[str(publication.pk)],
            before_snapshot={
                "assignments": {str(publication.pk): before},
                "revisions": {str(publication.pk): expected},
                "suggestion": suggestion_before,
            },
            after_snapshot={
                "revisions": {str(publication.pk): after_rev},
                "created_tag_ids": created_tag_ids,
                "retired_tags": retired_tags,
            },
            catalogue_revision=catalogue_state().revision,
        )
    return {
        "ok": True,
        "operation_id": str(op.pk),
        "status": suggestion.status,
        "accepted": sum(1 for entry in entries if entry.get("decision") == "accepted"),
        "revision": after_rev,
        "message": "已采纳所选建议" if action == "accept" else "已拒绝所选建议",
    }


def normalize_ai_output(value, catalogue):
    """Validate the structured model response; never turn prose substrings into tags."""
    if isinstance(value, str):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", value.strip(), flags=re.I)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            raise TaggingError("AI 标签结果格式无法识别", 502) from None
    if not isinstance(value, dict) or value.get("status") not in {"ok", "no_match", "review_required"}:
        raise TaggingError("AI 标签结果缺少有效状态", 502)
    if value["status"] != "ok":
        return {"status": value["status"], "message": str(value.get("message") or "")[:500], "suggestions": []}
    rows = value.get("suggestions")
    if not isinstance(rows, list) or len(rows) > 6:
        raise TaggingError("AI 标签建议数量不正确", 502)
    by_id = {str(item.pk): item for item in catalogue}
    clean_rows, seen = [], set()
    counts = {LibraryTag.Kind.KNOWLEDGE: 0, LibraryTag.Kind.METHOD: 0}
    for row in rows:
        if not isinstance(row, dict) or row.get("kind") not in counts:
            raise TaggingError("AI 返回了无法识别的标签类型", 502)
        kind = row["kind"]
        counts[kind] += 1
        if counts[LibraryTag.Kind.KNOWLEDGE] > 3 or counts[LibraryTag.Kind.METHOD] > 3:
            raise TaggingError("AI 标签建议超过允许数量", 502)
        rationale = str(row.get("rationale") or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        if not rationale or not evidence:
            raise TaggingError("AI 建议缺少理由或题目依据", 502)
        scope = row.get("scope")
        if not isinstance(scope, dict):
            raise TaggingError("AI 建议的适用范围不正确", 502)
        if kind == LibraryTag.Kind.KNOWLEDGE:
            if scope != {"whole": True}:
                raise TaggingError("知识点建议必须注明适用于整题", 502)
        elif scope != {"whole": True}:
            subquestions = scope.get("subquestions")
            if (
                set(scope) != {"subquestions"}
                or not isinstance(subquestions, list)
                or not 1 <= len(subquestions) <= 12
                or any(not isinstance(part, str) or not re.fullmatch(r"[1-9][0-9]?", part) for part in subquestions)
                or len(set(subquestions)) != len(subquestions)
            ):
                raise TaggingError("方法建议必须注明整题或具体小问", 502)
        item = {"kind": kind, "rationale": rationale[:500], "evidence": evidence[:500], "scope": scope}
        if row.get("tag_id"):
            tag = by_id.get(str(row["tag_id"]))
            if not tag or tag.kind != kind or not tag.active:
                raise TaggingError("AI 建议引用了不存在或类型不符的标签", 502)
            key = str(tag.pk)
            item.update({"tag_id": key, "name": tag.name, "description": tag.description})
        else:
            new_name = _clean_name(row.get("new_name"), "新标签建议")
            # The model is only ever shown active tags, so it re-proposing one of them by
            # name is not a new label. Bind the existing tag instead of opening a duplicate
            # that later demands a parent the model had no way to pick.
            existing = next(
                (
                    candidate
                    for candidate in catalogue
                    if candidate.kind == kind
                    and candidate.active
                    and normalized(candidate.name) == normalized(new_name)
                ),
                None,
            )
            if existing is not None:
                item.update({"tag_id": str(existing.pk), "name": existing.name, "description": existing.description})
                key = str(existing.pk)
            else:
                item["new_name"] = new_name
                item["name"] = new_name
                item["description"] = str(row.get("description") or "")[:300]
                levels = row.get("levels", [])
                if not isinstance(levels, list) or not levels or any(level not in {"初中", "高中"} for level in levels):
                    raise TaggingError("AI 新标签的适用学段不正确", 502)
                item["levels"] = levels
                parent_ids = _relation_ids(row.get("parent_ids", []))
                if not parent_ids and by_id:
                    raise TaggingError("AI 新标签建议缺少具体上级，请重新建议", 502)
                if any(key not in by_id or by_id[key].kind != kind or not by_id[key].active for key in parent_ids):
                    raise TaggingError("AI 新标签上级不存在、已停用或类型不符", 502)
                item["parent_ids"] = parent_ids
                key = kind + ":" + normalized(new_name)
        if key in seen:
            raise TaggingError("AI 重复推荐了同一个标签", 502)
        seen.add(key)
        clean_rows.append(item)
    if not clean_rows:
        return {"status": "no_match", "message": "没有找到有充分依据的标签。", "suggestions": []}
    return {"status": "ok", "message": str(value.get("message") or "")[:500], "suggestions": clean_rows}


def current_method_source(publication):
    from PIL import Image

    from . import imaging, library_solutions

    image_data_urls = []
    image_hashes = []
    try:
        solution = library_solutions.selected(publication)
    except library_solutions.SolutionError:
        return None
    if solution is not None:
        if not solution.analysis.strip():
            return None
        try:
            images = library_solutions.capture_images(solution)
        except library_solutions.SolutionError:
            return None
        image_hashes = [item["sha256"] for item in images]
        for item in images:
            with Image.open(io.BytesIO(item["bytes"])) as image:
                image_data_urls.append(imaging.jpeg_data_url(image, long_side=1400))
        material = solution.analysis + "|" + "|".join(image_hashes)
        return {
            "source": "solution",
            "solution_id": str(solution.pk),
            "analysis": solution.analysis,
            "analysis_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
            "image_hashes": image_hashes,
            "image_data_urls": image_data_urls,
        }
    content = publication.content or {}
    analysis = str(content.get("analysis") or "").strip()
    if not analysis:
        return None
    # The origin snapshot contains only explicitly assigned analysis figures.
    figures = [
        figure
        for figure in content.get("figures", [])
        if isinstance(figure, dict) and figure.get("slot") in {"analysis", "solution"}
    ]
    folder = Path(settings.DATA_ROOT) / "library" / str(publication.pk)
    for figure in figures:
        name = str(figure.get("file") or "")
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            return None
        target = folder / name
        try:
            if target.is_symlink() or not target.is_file():
                return None
            data = target.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            expected = str(figure.get("sha256") or "")
            if expected and expected != digest:
                return None
            with Image.open(io.BytesIO(data)) as image:
                image_data_urls.append(imaging.jpeg_data_url(image, long_side=1400))
            image_hashes.append(digest)
        except (OSError, ValueError):
            return None
    material = (
        analysis
        + "|"
        + json.dumps(
            [{key: item.get(key) for key in ("page_idx", "bbox", "parts", "source")} for item in figures],
            ensure_ascii=False,
            sort_keys=True,
        )
        + "|"
        + "|".join(image_hashes)
    )
    return {
        "source": "origin",
        "publication_id": str(publication.pk),
        "analysis": analysis,
        "analysis_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
        "image_hashes": image_hashes,
        "image_data_urls": image_data_urls,
        "figures": [
            {key: item.get(key) for key in ("page_idx", "bbox", "parts", "source", "file")} for item in figures
        ],
    }
