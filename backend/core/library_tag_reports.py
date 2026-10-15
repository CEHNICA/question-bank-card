"""Privacy-preserving reports for legacy tag migration and cleanup."""

from __future__ import annotations

from collections import defaultdict

from .models import LibraryTag, LibraryTagAssignment, LibraryTagSuggestion


def legacy_tag_migration_rows():
    counts = defaultdict(set)
    for row in LibraryTagAssignment.objects.select_related("tag").iterator():
        snapshot = row.source_snapshot if isinstance(row.source_snapshot, dict) else {}
        old_name = snapshot.get("legacy_name")
        if not old_name and row.source in {LibraryTagAssignment.Source.LEGACY_HUMAN,
                                           LibraryTagAssignment.Source.LEGACY_UNKNOWN}:
            names = snapshot.get("names", [])
            if isinstance(names, list) and len(names) == 1:
                old_name = names[0]
            elif row.tag.kind == LibraryTag.Kind.LEGACY:
                old_name = row.tag.name
        if not old_name:
            continue
        if row.tag.kind == LibraryTag.Kind.LEGACY:
            status = "历史标签／待人工整理"
        elif row.needs_review:
            status = "旧标签待核对"
        else:
            status = "已映射为正式标签"
        counts[(str(old_name), row.tag.name, status)].add(str(row.publication_id))
    for suggestion in LibraryTagSuggestion.objects.iterator():
        old_source = (str(suggestion.generation_source or "").startswith("旧版")
                      or str(suggestion.message or "").startswith("从旧版"))
        if not old_source:
            continue
        for item in suggestion.suggestions or []:
            if not isinstance(item, dict) or not item.get("legacy"):
                continue
            old_name = item.get("legacy_name") or item.get("new_name") or item.get("name")
            target = item.get("name") or item.get("new_name") or "（未命名）"
            if item.get("decision") == "accepted":
                accepted = LibraryTag.objects.filter(pk=item.get("accepted_tag_id")).first()
                target = accepted.name if accepted else target
                status = "旧 AI 候选／已人工采纳"
            elif item.get("decision") == "rejected":
                status = "旧 AI 候选／已拒绝"
            else:
                status = "旧 AI 候选／待核对"
            if old_name:
                counts[(str(old_name), str(target), status)].add(str(suggestion.publication_id))
    return [(old, target, status, len(publication_ids))
            for (old, target, status), publication_ids in sorted(counts.items())]


def legacy_tag_migration_markdown():
    rows = legacy_tag_migration_rows()
    lines = ["# 旧标签迁移清单", "",
             "本清单不包含题目正文、图片或题目编号。待核对条目仍不是正式标签。", "",
             "| 旧标签 | 目标标签 | 处理状态 | 涉及题目数 |", "|---|---|---|---:|"]
    lines.extend(f"| {_cell(old)} | {_cell(target)} | {_cell(status)} | {count} |"
                 for old, target, status, count in rows)
    if not rows:
        lines.append("| （没有旧标签记录） | — | — | 0 |")
    lines.append("")
    return "\n".join(lines)


def _cell(value):
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("|", "／").strip()
