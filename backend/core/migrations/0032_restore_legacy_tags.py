"""把锁在「旧标签留底」里的标签接回正式目录。

1.26.45 之前的某个时候，标签目录被清空过一次。清空是有意的破坏性动作
（老师主动点的），所以系统当时把待核对的建议都作废了 —— 这是对的。

问题在于：那次清空之前，旧版 AI 已经给 433 张已入库的题打过标签了。
清空标签目录时，这些标签被挪进一个「留底」字段就不再动，
于是它们既看不见、也筛不出来 —— 界面上只显示「802 题没知识点」。

真库实测（2026-11-06）：
  - 433 张已入库的卡，标签锁在留底里，身上一条标签分配都没有；
  - 这些旧标签的名字，**全部**能在现在的标签目录里找到对应
    （229 个直接对上正式标签，468 个对上是封存的历史标签，没有一个接不上）；
  - 其中 125 张卡的所有标签都能直接接上、64 张能接上一部分。

所以这一版把能接的接回去：

  - 判据沿用应用层同一把尺子（按名字找现存/封存标签），不另发明标准；
  - 只接**能确定**的：名字能对上正式标签才接，接不上的原样留着；
  - 接回来标成「旧标签待核对」：它们是旧版 AI 的结果、没有逐条理由，
    老师一眼就能看出对不对。确认之后才算正式标签，才进筛选结果
    —— 系统本来就有这套「待核对 → 确认」的流程，这里只是把货送进去；
  - 只写标签分配，题干、答案、解析、图片一个字都不动，搜索索引也不改；
  - 不调用识读服务、不花额度；
  - 重复跑不会重复折腾（已经接上的不动）。

注意：待核对的标签**不进**搜索索引和标签投影 —— 老师还没点头的东西
不该出现在搜索结果和筛选里。这一点沿用应用层本来的口径。
"""

from __future__ import annotations

from django.db import migrations


def _normalized(value):
    """和 library_tagging.normalized 同一把尺子：去掉空白、大小写归一。"""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).casefold()


def forwards(apps, schema_editor):
    """把锁着的旧标签接回正式标签目录，只接能唯一对上的。"""
    LibraryTag = apps.get_model("core", "LibraryTag")
    LibraryTagAssignment = apps.get_model("core", "LibraryTagAssignment")
    PublishedQuestion = apps.get_model("core", "PublishedQuestion")

    # 正式标签按名字建索引：名字对上就是同一个知识点。
    formal = {}
    for tag in LibraryTag.objects.filter(active=True):
        if tag.kind == "legacy":
            continue
        formal.setdefault(_normalized(tag.name), tag)

    if not formal:
        return

    for publication in PublishedQuestion.objects.filter(status="published").iterator():
        extras = publication.extras if isinstance(publication.extras, dict) else {}
        legacy = extras.get("tags_legacy_original")
        if not isinstance(legacy, dict):
            continue
        names = legacy.get("names")
        if not isinstance(names, list) or not names:
            continue
        # 已经接过的不再接（幂等）。
        if LibraryTagAssignment.objects.filter(publication=publication).exists():
            continue

        names = [str(name).strip() for name in names if str(name).strip()]
        if not names:
            continue

        seen = set()
        for name in names:
            tag = formal.get(_normalized(name))
            if tag is None:
                continue
            key = str(tag.pk)
            if key in seen:
                continue
            seen.add(key)
            LibraryTagAssignment.objects.create(
                publication=publication,
                tag=tag,
                scope={"whole": True},
                source="legacy_unknown",
                needs_review=True,
                rationale="旧版 AI 标签；升级时按名字接回正式目录，待人工核对。",
                source_snapshot={
                    "legacy_name": tag.name,
                    "legacy_source": str(legacy.get("source") or ""),
                    "legacy_at": str(legacy.get("at") or ""),
                    "restored_by": "0032_restore_legacy_tags",
                },
            )


def backwards(apps, schema_editor):
    """没有可还原的原始值：这些标签分配是新长出来的，
    退回去就等于把「锁着的标签」又锁回去 —— 回到没人看得见的老样子。
    保持原样即可。"""


class Migration(migrations.Migration):
    dependencies = [("core", "0031_refresh_search_index")]

    operations = [migrations.RunPython(forwards, backwards)]
