"""把「目录版本落后但建议本身完好」的待核对建议重新对齐。

0028 把分组转成章节父标签时把目录版本号往上抬了一格，却没有把还在等待
核对的建议一起抬上去。于是系统自己的判断（引用的标签还在不在）明明认为
这些建议完好，`_split_by_catalogue_damage` 的注释也写着「重组层级树不算
破坏」，实际上 439 条待核对建议却集体对不上版本号：界面上「现在就能采纳」
变成 0，用户一点采纳就撞上「标签目录已变化，请按新目录重新建议」。

这里补上 0028 漏掉的那一步，判据沿用应用层同一套，不另发明标准：
只有建议引用的标签（要贴的那个，以及要挂在谁下面）消失或停用，才算被破坏；
其他一切变化——新增标签、改名、重新分层级——都不算。
"""

from __future__ import annotations

from django.db import migrations


def _split(apps, entries, alive):
    """和 library_tagging._split_by_catalogue_damage 同一把尺子。"""
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for tag_id in [entry.get("tag_id")] + list(entry.get("parent_ids") or []):
            if tag_id and str(tag_id) not in alive:
                return True
    return False


def _rebase(apps, revision):
    LibraryTag = apps.get_model("core", "LibraryTag")
    LibraryTagSuggestion = apps.get_model("core", "LibraryTagSuggestion")

    alive = {str(tag.pk) for tag in LibraryTag.objects.all() if tag.active}
    candidates = LibraryTagSuggestion.objects.filter(status="pending").exclude(taxonomy_revision=revision)
    intact, broken = [], []
    for pk, entries in candidates.values_list("id", "suggestions"):
        (broken if _split(apps, entries, alive) else intact).append(pk)

    if intact:
        LibraryTagSuggestion.objects.filter(pk__in=intact).update(taxonomy_revision=revision)
    return len(intact), len(broken)


def forwards(apps, schema_editor):
    state = apps.get_model("core", "LibraryTagCatalogue").objects.first()
    if state is None:
        return
    _rebase(apps, state.revision)


def backwards(apps, schema_editor):
    """没有可还原的原始值：每条建议原本各是第几版并不相同，逐条记下来才退得回去，
    为一次幂等且无副作用的对齐专门建表不值得。而且真要退回 0028，被转换出来的
    章节标签会一并删除，那些引用了章节的建议本就该失效；剩下的建议等到下一次
    目录改动时会再被同样地重新对齐。保持原样即可。"""


class Migration(migrations.Migration):
    dependencies = [("core", "0028_tag_groups_to_hierarchy")]

    operations = [migrations.RunPython(forwards, backwards)]
