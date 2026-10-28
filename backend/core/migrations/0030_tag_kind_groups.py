"""把「知识点 / 解题方法 / 自定义 / 历史标签」变成真正的标签分组。

1.27.0：老师说「系统的这个分组，其实就是一个普通的标签分组」。
之前它们只是按类型（kind）筛出来的**假分组** —— 点得动、筛得出，但不能改名、
不能删、不能往里放标签，跟老师自建的组不是一回事。

这里把它们落成四个真的分组，并把每颗标签按类型归进去：4 个组 + 306 条归属关系。
分组建好之后它们和老师自建的组完全一样 —— 改名、删除、往里放标签都行，每一步
都能撤销。

**反向迁移只删这四个组**：标签本身不动，老师自建的组不动，教材章节那套
（`seed-group:*`）也不动。

两个要小心的地方：

* 「历史标签」这个名字，1.25 之前已经被一个系统分组占着（`system:legacy-tags`，
  装的正是那 39 颗旧标签）。同名但装的是同一批标签 —— 直接把它接管成内置组，
  不另建一个同名组（数据库里组名是唯一的）。
* 老师自己建过同名的组（系统键为空）—— 那是他的，不占。碰到就跳过这个类型，
  它只留在「按类型浏览」里。
"""

from __future__ import annotations

from django.db import migrations

# (system_key, 组名, 对应的标签类型)
BUILTIN_GROUPS = [
    ("builtin:knowledge", "知识点", "knowledge"),
    ("builtin:method", "解题方法", "method"),
    ("builtin:custom", "自定义", "custom"),
    ("builtin:legacy", "历史标签", "legacy"),
]


def _group_model(apps):
    return apps.get_model("core", "LibraryTagGroup")


def _tag_model(apps):
    return apps.get_model("core", "LibraryTag")


def forwards(apps, schema_editor):
    groups = _group_model(apps)
    tags = _tag_model(apps)
    for order, (key, name, kind) in enumerate(BUILTIN_GROUPS):
        group = groups.objects.filter(system_key=key).first()
        if group is None:
            same_name = groups.objects.filter(name=name).first()
            if same_name is not None:
                # 装的是同一批标签就接管过来（1.25 之前那个历史标签组就是这种情况），
                # 否则是老师自己的组 —— 不占他的，这个类型就不进分组。
                kinds_held = set(same_name.tags.values_list("kind", flat=True))
                if kinds_held != {kind}:
                    continue
                same_name.system_key = key
                same_name.position = order
                same_name.save(update_fields=["system_key", "position"])
                group = same_name
            else:
                group = groups.objects.create(name=name, system_key=key, color="", position=order)
        members = list(tags.objects.filter(kind=kind))
        if members:
            group.tags.add(*members)


def backwards(apps, schema_editor):
    groups = _group_model(apps)
    for key, _name, _kind in BUILTIN_GROUPS:
        group = groups.objects.filter(system_key=key).first()
        if group is not None:
            group.delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0029_realign_untouched_suggestions")]
    operations = [migrations.RunPython(forwards, backwards)]
