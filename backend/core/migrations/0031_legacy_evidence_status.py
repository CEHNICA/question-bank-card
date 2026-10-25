"""旧关联的证据状态：没有新结构的依据，就不能说它「当前有效」。

0030 加字段时给的默认值是 ``current`` —— 对**新增**的关联没问题，
但迁移前就存在的那些旧关联，一条都没绑定到新的结构化依据
（没绑具体解法、没绑小问），拿「依据当前有效」当它们的身份是骗人。

计划第 3.5 节把两件事分开：

* ``review_status`` —— 人有没有确认过。旧的人工标签是确认过的，这个不动。
* ``evidence_status`` —— 依据现在还成不成立。旧记录没有可核对的依据，
  所以是「旧记录未绑定具体依据」。

这两条分开之后，检索时旧记录会落进「旧标签命中」那一桶，
不冒充「已确认匹配」—— 第 6.4 节要求的三态就是这样分的。
"""

from django.db import migrations

# 迁移前就有的关联：0030 给它们的 role 是默认值 legacy（就是「作用未细分」）。
LEGACY_ROLE = "legacy"
UNBOUND = "unbound"


def reclassify_legacy_assignments(apps, schema_editor):
    Assignment = apps.get_model("core", "LibraryTagAssignment")
    updated = Assignment.objects.filter(role=LEGACY_ROLE).update(evidence_status=UNBOUND)
    return updated


def restore_legacy_assignments(apps, schema_editor):
    """回滚：把这批行的依据状态放回 0030 给的默认值。"""
    Assignment = apps.get_model("core", "LibraryTagAssignment")
    Assignment.objects.filter(role=LEGACY_ROLE).update(evidence_status="current")


class Migration(migrations.Migration):
    dependencies = [("core", "0030_tag_system_refactor")]

    operations = [
        migrations.RunPython(reclassify_legacy_assignments, restore_legacy_assignments),
    ]
