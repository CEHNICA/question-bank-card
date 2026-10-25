"""把标签「分组」改写成真正的上下级关系。

分组一直和父子关系并存，实际上是把同一件事说了两遍：目录的层级由分组承担，
上下级却几乎是空的。这套关系把分组整条搬进上下级，之后目录只有一种组织方式。

转完的样子：每个章节变成一个标签，下面挂原来属于它的知识点。系统要求父子同
类型，所以一个章节的标签类型必须清一色才转；不一律的原样不动，也不硬凑。
「历史标签」不是章节而是待核对状态，本来就没有章节归属，不转。
已经有父级的标签（比如挂在某个知识点下面的）保留原父级，不被章节覆盖。
"""

from __future__ import annotations

import uuid

from django.db import migrations

LEGACY_GROUP_KEY = "system:legacy-tags"
SECTION_KEY_PREFIX = "section:"
HIERARCHY = "hierarchy"
SECTION_DESCRIPTION = "教材章节，用来给标签目录分层。"


def forwards(apps, schema_editor):
    LibraryTag = apps.get_model("core", "LibraryTag")
    LibraryTagGroup = apps.get_model("core", "LibraryTagGroup")
    LibraryTagRelation = apps.get_model("core", "LibraryTagRelation")

    # 转换开始之前就有的上下级：那是知识点之间本来就有的层级，要原样保留。
    # 这里必须取快照——同一个标签后面还要接到别的章节去，不能拿「现在已经
    # 有上级」当理由把它挡掉，否则同时属于初中和高中两章的知识点（函数图象、
    # 平面直角坐标系、统计图表）只会挂到其中一章，另一章就再也找不到它。
    placed_before = set(LibraryTagRelation.objects.filter(relation_type=HIERARCHY).values_list("child_id", flat=True))

    changed = False
    for group in (
        LibraryTagGroup.objects.exclude(system_key=LEGACY_GROUP_KEY)
        .prefetch_related("tags")
        .order_by("position", "name")
    ):
        members = list(group.tags.all())
        if not members:
            continue

        # 父子必须同类型：混在一组的标签没法共用一个章节父标签。
        kinds = {tag.kind for tag in members}
        if len(kinds) != 1:
            continue
        kind = kinds.pop()
        # 学段跟模板导入用的是同一套判断，免得同一章在两种安装路径下长得不一样。
        if group.name.startswith("方法 · "):
            levels = ["初中", "高中"]
        else:
            levels = ["初中"] if group.name.startswith("初中") else ["高中"]

        section, created = LibraryTag.objects.get_or_create(
            system_key=f"{SECTION_KEY_PREFIX}{group.system_key or group.name}"[:100],
            defaults={
                "id": uuid.uuid4(),
                "name": group.name,
                "kind": kind,
                "description": SECTION_DESCRIPTION,
                "color": group.color or "",
                "levels": levels,
                "active": True,
            },
        )
        if created:
            changed = True

        for member in members:
            if member.pk in placed_before:
                continue
            _, made = LibraryTagRelation.objects.get_or_create(
                parent_id=section.pk, child_id=member.pk, relation_type=HIERARCHY
            )
            changed = changed or made

    if changed:
        # 目录结构变了，版本号必须跟着走：否则界面上仍然拿着旧版本号编辑，
        # 一次保存就能把这次转换的结果覆盖掉。
        state = apps.get_model("core", "LibraryTagCatalogue").objects.first()
        if state is not None:
            state.revision = state.revision + 1
            state.save(update_fields=["revision"])


def backwards(apps, schema_editor):
    """退回分组：删掉由分组转出来的章节和关系，原分组数据本来就还在。"""
    LibraryTag = apps.get_model("core", "LibraryTag")
    LibraryTagRelation = apps.get_model("core", "LibraryTagRelation")

    sections = list(LibraryTag.objects.filter(system_key__startswith=SECTION_KEY_PREFIX))
    if not sections:
        return
    section_ids = [tag.pk for tag in sections]
    LibraryTagRelation.objects.filter(relation_type=HIERARCHY, parent_id__in=section_ids).delete()
    LibraryTag.objects.filter(pk__in=section_ids).delete()

    state = apps.get_model("core", "LibraryTagCatalogue").objects.first()
    if state is not None:
        state.revision = state.revision + 1
        state.save(update_fields=["revision"])


class Migration(migrations.Migration):
    dependencies = [("core", "0027_librarytagrelation")]

    operations = [migrations.RunPython(forwards, backwards)]
