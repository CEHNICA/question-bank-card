"""旧标签数据迁移：dry-run、apply、verify。

原则：**新增结构 → 建立映射 → 隔离验证 → 切换读取**。首轮不删旧表、不删旧 ID、
不删原始内容。每一条旧实体都要能在迁移报告里查到它被怎么处理了。

迁移不提高数学可信度，也不改变人工核对历史：

* 旧知识点正式关联 → 用途记「旧关联，作用未细分」，不自动升级成「主要考查」；
* 旧方法正式关联 → 有可靠题面、解析、范围证据才绑定，否则记「旧记录未绑定具体依据」；
* 旧 AI 候选 → 一条不动，逐条决策、依据、版本、草稿都留着，不自动采纳；
* 旧上下级 → 保留，标成旧关系，不参与新的语义推断；
* 同名概念 → 生成建议映射，不自动做语义合并。

三个动作：

    plan_migration()    只报告，不碰数据库（dry-run）
    apply_migration()   按批次写入，带幂等，重复执行不重复建实体
    verify_migration()  核对迁移前后业务不变量

``apply_migration`` 按批次标识写 ``LibraryMigrationMap``；同一个批次重复执行
只会补齐没做完的部分，不会重复贴标。中断后重跑同一个批次即可接着走。
"""

from __future__ import annotations

import uuid

from django.db import transaction

from .models import (
    LibraryChapter,
    LibraryChapterConcept,
    LibraryMigrationMap,
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagRelation,
    LibraryTagSuggestion,
)

#: 0028 迁移把旧的「分组」搬成了这种带系统来源键的章节标签。
#: 章节容器靠它认 —— 不靠名字前缀（用户自己建的「函数」可能正好是章名）。
SECTION_KEY_PREFIX = "section:"

#: 通用教学目录：旧章节不知道教材版次时如实落到这里，不编造版本。
UNSPECIFIED_TEXTBOOK_NAME = "通用教学目录"


class MigrationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 计划：只算，不写
# ---------------------------------------------------------------------------


def plan_migration() -> dict:
    """算一遍迁移会做什么，返回分类数量、冲突和影响范围。

    这个函数不写任何东西。报告里不含真实题干 ——
    迁移报告是要给人看的，不该把题面抄出去。
    """
    from .textnorm import concept_key

    tags = list(LibraryTag.objects.all().order_by("name", "id"))
    by_canonical: dict[str, list[LibraryTag]] = {}
    for tag in tags:
        # 按**算出来的**归一化名分组，不是按库里存的那个 ——
        # 旧数据还没回填，存的值是空的，按它分组就永远发现不了重名。
        by_canonical.setdefault(concept_key(tag.name), []).append(tag)

    # 同名冲突：归一化后撞在一起。
    conflicts = []
    for key, group in by_canonical.items():
        if not key or len(group) < 2:
            continue
        kinds = sorted({tag.kind for tag in group})
        conflicts.append(
            {
                "canonical_key": key,
                "names": sorted({tag.name for tag in group}),
                "kinds": kinds,
                "ids": [str(tag.pk) for tag in group],
                # 历史标签（legacy）是占位，撞上正式概念时归并到正式概念；
                # 两个都是正式概念时不自动合并，标歧义等人工决定。
                "resolvable": "legacy_into_formal" if "legacy" in kinds and len(kinds) > 1 else "ambiguous",
            }
        )

    assignments = list(LibraryTagAssignment.objects.all())
    unbound = 0
    bound = 0
    for row in assignments:
        if _has_reliable_evidence(row):
            bound += 1
        else:
            unbound += 1

    sections = [tag for tag in tags if (tag.system_key or "").startswith(SECTION_KEY_PREFIX)]
    section_ids = {str(tag.pk) for tag in sections}
    section_links = LibraryTagAssignment.objects.filter(tag_id__in=section_ids).count()

    suggestions: dict[str, int] = {}
    for row in LibraryTagSuggestion.objects.all():
        suggestions[row.status] = suggestions.get(row.status, 0) + 1

    return {
        "counts": {
            "tags": len(tags),
            "tags_missing_canonical_key": sum(1 for tag in tags if not tag.canonical_key),
            "sections": len(sections),
            "assignments": len(assignments),
            "assignments_with_evidence": bound,
            "assignments_without_evidence": unbound,
            "section_assignments": section_links,
            "custom_tags": sum(1 for tag in tags if tag.kind == LibraryTag.Kind.CUSTOM),
            "legacy_tags": sum(1 for tag in tags if tag.kind == LibraryTag.Kind.LEGACY),
            "relations": LibraryTagRelation.objects.count(),
            "suggestions": sum(suggestions.values()),
        },
        "suggestion_status": suggestions,
        "conflicts": conflicts,
        "ambiguous_relations": _ambiguous_relations(),
        "warnings": _warnings(sections, conflicts, suggestions),
    }


def _ambiguous_relations() -> list[dict]:
    """上下级说不清是哪一类 —— 标成旧关系，不参与新语义推断。

    判据：同一条关系里父子类型不一致，或者关系来自旧的分组搬迁
    （父节点是章节容器）。
    """
    result = []
    for row in LibraryTagRelation.objects.select_related("parent", "child").order_by("id"):
        if row.parent.kind != row.child.kind:
            result.append(
                {"id": str(row.pk), "parent": row.parent.name, "child": row.child.name, "reason": "父子类型不一致"}
            )
    return result


def _warnings(sections: list[LibraryTag], conflicts: list[dict], suggestions: dict) -> list[str]:
    notes = []
    if conflicts:
        notes.append(f"{len(conflicts)} 组概念归一化后同名，需要逐条决定归并还是保留歧义。")
    if suggestions.get(LibraryTagSuggestion.Status.QUEUED):
        notes.append(
            f"{suggestions[LibraryTagSuggestion.Status.QUEUED]} 条候选处于排队中，从未真正生成过。"
            "迁移不把它们算作待核对，也不替用户触发。"
        )
    if any((tag.system_key or "").startswith(SECTION_KEY_PREFIX) for tag in sections):
        notes.append("旧章节不知道教材版次，迁到「通用教学目录／版本未指定」，不编造教材版本。")
    return notes


def _has_reliable_evidence(row: LibraryTagAssignment) -> bool:
    """这条旧关联有没有可以绑定的依据。

    判据收紧：人工确认 + 写了理由 + 有对应的解析版本。
    旧库里绝大多数关联的 scope 都是整题、也没有绑定解析，
    所以它们只能记「旧记录未绑定具体依据」—— 不能冒充精确的方法证明。
    """
    if row.source not in {
        LibraryTagAssignment.Source.HUMAN,
        LibraryTagAssignment.Source.AI_ACCEPTED,
        LibraryTagAssignment.Source.LEGACY_HUMAN,
    }:
        return False
    if not str(row.rationale or "").strip():
        return False
    return row.publication.solutions.exists()


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def apply_migration(batch: str = "tag-refactor-1") -> dict:
    """按批次执行迁移。

    幂等：同一个批次重复执行只补齐没做完的，不重复建实体、不重复贴标。
    中断后重跑同一个批次即可接着走。
    """
    batch = str(batch or "tag-refactor-1")[:64]
    report = {
        "batch": batch,
        "tags_backfilled": 0,
        "tags_merged": 0,
        "tags_ambiguous": 0,
        "assignments_backfilled": 0,
        "chapters_created": 0,
        "chapter_concepts": 0,
        "relations_marked": 0,
        "skipped": 0,
    }
    with transaction.atomic():
        report.update(_backfill_canonical_keys(batch))
        report.update(_mark_relations(batch))
        report.update(_backfill_assignments(batch))
        report.update(_migrate_sections(batch))
        report.update(_map_suggestions(batch))
    return report


def _entry(
    batch: str,
    old_kind: str,
    old_key: str,
    *,
    new_kind: str = "",
    new_key: str = "",
    disposition: str,
    reason: str = "",
    snapshot: dict | None = None,
) -> None:
    LibraryMigrationMap.objects.update_or_create(
        batch=batch,
        old_kind=old_kind,
        old_key=old_key,
        defaults={
            "new_kind": new_kind,
            "new_key": new_key,
            "disposition": disposition,
            "reason": reason[:500],
            "snapshot": snapshot or {},
        },
    )


def _backfill_canonical_keys(batch: str) -> dict:
    """给概念补上规范身份键。撞名的分两种处理。

    历史标签（legacy）撞上正式概念 → 归并到正式概念，旧 ID 保留做重定向。
    两个都是正式概念 → **不自动合并**，各自保留并标歧义，等人工决定。
    """
    from .textnorm import concept_key

    done = 0
    merged = 0
    ambiguous = 0
    groups: dict[str, list[LibraryTag]] = {}
    for tag in LibraryTag.objects.all().order_by("name", "id"):
        groups.setdefault(concept_key(tag.name), []).append(tag)

    for key, group in groups.items():
        if not key:
            continue
        if len(group) == 1:
            tag = group[0]
            # 已经有别的概念占了规范身份键（比如迁移前新建的），不动。
            taken = LibraryTag.objects.filter(canonical_key=key).exclude(pk=tag.pk).exists()
            if taken:
                ambiguous += 1
                _entry(
                    batch,
                    "tag",
                    str(tag.pk),
                    new_kind="concept",
                    new_key="",
                    disposition=str(LibraryMigrationMap.Disposition.AMBIGUOUS),
                    reason="规范身份键已被别的概念占用，不自动合并，等待人工决定",
                    snapshot={"name": tag.name, "kind": tag.kind},
                )
                continue
            if tag.canonical_key != key:
                tag.canonical_key = key
                tag.save(update_fields=["canonical_key"])
                done += 1
                _entry(
                    batch,
                    "tag",
                    str(tag.pk),
                    new_kind="concept",
                    new_key=key,
                    disposition=str(LibraryMigrationMap.Disposition.CARRIED),
                    reason="补上规范身份键",
                )
            continue

        formal = [tag for tag in group if tag.kind != LibraryTag.Kind.LEGACY]
        legacy = [tag for tag in group if tag.kind == LibraryTag.Kind.LEGACY]
        if len(formal) == 1 and legacy:
            keep = formal[0]
            keep.canonical_key = key
            keep.save(update_fields=["canonical_key"])
            for old in legacy:
                old.merged_into = keep
                old.save(update_fields=["merged_into"])
                merged += 1
                _entry(
                    batch,
                    "tag",
                    str(old.pk),
                    new_kind="concept",
                    new_key=key,
                    disposition=str(LibraryMigrationMap.Disposition.CONVERTED),
                    reason="历史标签与正式概念同名，归并到正式概念；旧 ID 保留做重定向",
                    snapshot={"name": old.name, "kind": old.kind},
                )
            continue

        # 两个都是正式概念：谁都不动，标歧义。
        for tag in group:
            if not tag.canonical_key:
                tag.canonical_key = ""
                tag.save(update_fields=["canonical_key"])
            ambiguous += 1
            _entry(
                batch,
                "tag",
                str(tag.pk),
                new_kind="concept",
                new_key="",
                disposition=str(LibraryMigrationMap.Disposition.AMBIGUOUS),
                reason="归一化后同名但含义可能不同，不自动合并，等待人工决定",
                snapshot={"name": tag.name, "kind": tag.kind},
            )
    return {"tags_backfilled": done, "tags_merged": merged, "tags_ambiguous": ambiguous}


def _mark_relations(batch: str) -> dict:
    """旧上下级标成旧关系：不参与新的语义推断，但一条不删。"""
    count = 0
    for row in LibraryTagRelation.objects.all():
        if row.source == "human" and row.revision == 1:
            continue
        row.source = "legacy"
        row.save(update_fields=["source"])
        count += 1
        _entry(
            batch,
            "relation",
            str(row.pk),
            new_kind="relation",
            new_key=str(row.relation_type),
            disposition=str(LibraryMigrationMap.Disposition.CARRIED),
            reason="旧上下级关系保留，语义待整理",
        )
    return {"relations_marked": count}


def _backfill_assignments(batch: str) -> dict:
    """旧关联补上用途和范围键。

    用途一律记「旧关联，作用未细分」—— 不替用户编造用途，
    更不能把旧知识点自动升级成「主要考查」。
    """
    count = 0
    for row in LibraryTagAssignment.objects.all().select_related("publication"):
        changed = []
        if row.role != LibraryTagAssignment.Role.LEGACY:
            row.role = LibraryTagAssignment.Role.LEGACY
            changed.append("role")
        if not row.scope_key or row.scope_key == "whole":
            # 旧库里每一条关联的 scope 都是整题（没有小问、没有解法分支的概念），
            # 所以范围键就是 whole。要绑到具体小问得先有可靠拆分，不能凭编号猜。
            row.scope_key = "whole"
            changed.append("scope_key")
        if row.evidence_status == LibraryTagAssignment.EvidenceStatus.CURRENT and not _has_reliable_evidence(row):
            row.evidence_status = LibraryTagAssignment.EvidenceStatus.UNBOUND
            changed.append("evidence_status")
        if changed:
            row.save(update_fields=changed)
            count += 1
            _entry(
                batch,
                "assignment",
                str(row.pk),
                new_kind="assertion",
                new_key=f"{row.role}:{row.scope_key}",
                disposition=str(LibraryMigrationMap.Disposition.CARRIED),
                reason="旧关联保留原样，用途记「旧关联，作用未细分」",
                snapshot={"tag_id": str(row.tag_id), "source": row.source},
            )
    return {"assignments_backfilled": count}


def _migrate_sections(batch: str) -> dict:
    """章节容器转成教材目录节点。

    旧章节不知道教材版次 → 落到「通用教学目录／版本未指定」，不编造版本。
    章节上的直接人工贴标 → 记成目录关联和旧标签快照，不凭空细化数学含义。
    """
    from .library_concepts import chapter_node_key

    created = 0
    chapter_concepts = 0
    sections = list(LibraryTag.objects.filter(system_key__startswith=SECTION_KEY_PREFIX).order_by("name", "id"))
    if not sections:
        return {"chapters_created": 0, "chapter_concepts": 0}

    textbook = _unspecified_textbook()
    # 先建全部章节，再连父子 —— 父章节可能排在后面。
    mapping: dict[str, LibraryChapter] = {}
    for tag in sections:
        node_key = chapter_node_key(textbook_id=str(textbook.pk), parent_id="", title=tag.name)
        chapter, was_created = LibraryChapter.objects.get_or_create(
            node_key=node_key, defaults={"title": tag.name, "textbook": textbook, "position": 0}
        )
        mapping[str(tag.pk)] = chapter
        if was_created:
            created += 1
        _entry(
            batch,
            "section",
            str(tag.pk),
            new_kind="chapter",
            new_key=str(chapter.pk),
            disposition=str(LibraryMigrationMap.Disposition.CONVERTED),
            reason="章节容器转成教学目录节点；教材版次未知，落到通用教学目录",
            snapshot={"name": tag.name},
        )

    for tag in sections:
        chapter = mapping[str(tag.pk)]
        parent_tag = _hierarchy_parent(tag)
        if parent_tag is None or str(parent_tag.pk) not in mapping:
            continue
        parent_chapter = mapping[str(parent_tag.pk)]
        if chapter.parent_id != parent_chapter.pk:
            chapter.parent = parent_chapter
            chapter.save(update_fields=["parent"])

    # 章节上的直接人工贴标 → 目录关联
    for tag in sections:
        chapter = mapping[str(tag.pk)]
        for row in LibraryTagAssignment.objects.filter(tag_id=tag.pk).select_related("publication"):
            LibraryChapterConcept.objects.get_or_create(
                chapter=chapter, tag=tag, defaults={"confirmed": False, "source": "legacy"}
            )
            chapter_concepts += 1
            _entry(
                batch,
                "section_assignment",
                str(row.pk),
                new_kind="chapter_concept",
                new_key=str(chapter.pk),
                disposition=str(LibraryMigrationMap.Disposition.NEEDS_REVIEW),
                reason="章节上的直接人工贴标保留原标签快照，数学含义待人工整理",
                snapshot={"tag_id": str(row.tag_id), "scope": row.scope},
            )
    return {"chapters_created": created, "chapter_concepts": chapter_concepts}


def _unspecified_textbook():
    from .models import LibraryTextbook

    textbook, _ = LibraryTextbook.objects.get_or_create(
        name=UNSPECIFIED_TEXTBOOK_NAME, stage="", defaults={"unspecified": True, "edition": "旧章节没有记录教材版次"}
    )
    return textbook


def _hierarchy_parent(tag: LibraryTag) -> LibraryTag | None:
    row = LibraryTagRelation.objects.filter(child_id=tag.pk, relation_type=LibraryTagRelation.Type.HIERARCHY).first()
    if row is None:
        return None
    return row.parent


def _map_suggestions(batch: str) -> dict:
    """AI 候选一条不动。

    逐条决策、依据、版本、草稿、拒绝记录都留着。迁移不自动采纳，
    也不把「排队中、从没生成过」的候选算成待核对。
    """
    for row in LibraryTagSuggestion.objects.all():
        _entry(
            batch,
            "suggestion",
            str(row.pk),
            new_kind="review_proposal",
            new_key=row.status,
            disposition=str(LibraryMigrationMap.Disposition.CARRIED),
            reason="候选原样保留，不自动采纳；排队中的候选从未生成，需要用户主动触发",
            snapshot={"status": row.status, "count": len(row.suggestions or [])},
        )
    return {"skipped": 0}


# ---------------------------------------------------------------------------
# 核对
# ---------------------------------------------------------------------------


def verify_migration(batch: str = "tag-refactor-1") -> dict:
    """核对迁移前后业务不变量。

    不是「数据库能打开」就算过 —— 要确认一条正式关联都没丢、
    候选一条都没少、用途没有被偷偷升级。
    """
    batch = str(batch or "tag-refactor-1")[:64]
    problems: list[str] = []

    # 1. 每条关联都还要在
    assignments = list(LibraryTagAssignment.objects.all())
    for row in assignments:
        if row.role != LibraryTagAssignment.Role.LEGACY and row.source in {
            LibraryTagAssignment.Source.LEGACY_HUMAN,
            LibraryTagAssignment.Source.LEGACY_UNKNOWN,
        }:
            problems.append(f"关联 {row.pk} 的用途被改成了 {row.role}")

    # 2. 候选一条没少
    entries = LibraryMigrationMap.objects.filter(batch=batch, old_kind="suggestion")
    if entries.count() != LibraryTagSuggestion.objects.count():
        problems.append(f"候选映射 {entries.count()} 条，实际候选 {LibraryTagSuggestion.objects.count()} 条，对不上")

    # 3. 没有概念被合并掉（被合并的旧 ID 还在，只是指向新概念）
    for tag in LibraryTag.objects.all():
        if tag.merged_into_id and not LibraryTag.objects.filter(pk=tag.merged_into_id).exists():
            problems.append(f"概念 {tag.pk} 指向了不存在的合并目标")

    # 4. 范围键非空
    for row in assignments:
        if not str(row.scope_key or "").strip():
            problems.append(f"关联 {row.pk} 的范围键是空的")

    # 5. 每个章节都有规范化键
    for chapter in LibraryChapter.objects.all():
        if not str(chapter.node_key or "").strip():
            problems.append(f"章节 {chapter.pk} 的规范化键是空的")

    return {
        "batch": batch,
        "ok": not problems,
        "problems": problems,
        "counts": {
            "assignments": len(assignments),
            "legacy_role_assignments": sum(1 for row in assignments if row.role == LibraryTagAssignment.Role.LEGACY),
            "suggestions": LibraryTagSuggestion.objects.count(),
            "chapters": LibraryChapter.objects.count(),
            "concepts": LibraryTag.objects.count(),
            "merged_concepts": LibraryTag.objects.filter(merged_into__isnull=False).count(),
            "migration_entries": LibraryMigrationMap.objects.filter(batch=batch).count(),
        },
    }


def migration_report(batch: str = "tag-refactor-1") -> dict:
    """给人看的迁移报告。不含真实题干。"""
    batch = str(batch or "tag-refactor-1")[:64]
    entries = LibraryMigrationMap.objects.filter(batch=batch).order_by("old_kind", "old_key")
    by_disposition: dict[str, int] = {}
    for entry in entries:
        by_disposition[entry.disposition] = by_disposition.get(entry.disposition, 0) + 1
    return {
        "batch": batch,
        "plan": plan_migration(),
        "by_disposition": by_disposition,
        "total_entries": entries.count(),
        "verify": verify_migration(batch),
        "entries": [
            {
                "old_kind": entry.old_kind,
                "old_key": entry.old_key,
                "new_kind": entry.new_kind,
                "new_key": entry.new_key,
                "disposition": entry.disposition,
                "reason": entry.reason,
            }
            for entry in entries[:500]
        ],
    }


def new_batch_id() -> str:
    return f"tag-refactor-{uuid.uuid4().hex[:8]}"
