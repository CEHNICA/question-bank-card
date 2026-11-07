"""升级时自动把题卡的搜索索引补齐。

1.26.44 让卡面能印出「5.5.2 简单的三角恒等变换」这种教材小节名。卡面印了，
老师理所当然会拿这段字去搜——但搜索靠的是入库时算好的那一列索引，老卡片还存着
旧规则算的索引，于是印得出来、搜不到（1.26.44 真库实测：1267 张里 318 张对不上）。

靠人记得手动补是补不过来的：每台机器升级后都会欠一次，欠了就一直搜不到。
所以把补写放进升级流程 —— 软件升级时自己补齐，跟别的数据升级一样。

只重算搜索索引那一列：题干、答案、解析、图片、旗标、标签一个字都不动，
不重新切题、不调用识读服务、不花额度。

判据沿用应用层同一套（core.library._search_text），不另发明标准：
算出来的跟存着的一样就不动，省得白写一遍。
"""

from __future__ import annotations

from django.db import migrations


def forwards(apps, schema_editor):
    """按现规则重算每一张已入库题卡的搜索索引，只写对不上的那些。"""
    from core import library

    PublishedQuestion = apps.get_model("core", "PublishedQuestion")

    # 老卡片的快照里可能缺字段，补齐再算，免得算一半报错。
    # 这几个是搜索索引必读的键，少一个都会让升级中断。
    defaults = {"stem": "", "options": {}, "answer": "", "analysis": "", "source_filename": ""}

    stale = []
    queryset = PublishedQuestion.objects.only("id", "content", "extras", "search_text", "source_filename").iterator()
    for publication in queryset:
        content = dict(publication.content or {})
        for key, blank in defaults.items():
            content.setdefault(key, blank)
        if not isinstance(content.get("options"), dict):
            content["options"] = {}
        if not str(content.get("source_filename") or "").strip():
            content["source_filename"] = publication.source_filename or ""
        fresh = library._search_text(content, publication.extras)
        if fresh == (publication.search_text or ""):
            continue
        stale.append((publication.pk, fresh))

    if not stale:
        return

    PublishedQuestion.objects.bulk_update(
        [PublishedQuestion(id=pk, search_text=fresh) for pk, fresh in stale], ["search_text"], batch_size=200
    )


def backwards(apps, schema_editor):
    """没有可还原的原始值：每张卡当初是按哪一版规则算的索引并不相同，
    逐条记下来才退得回去，为一次幂等的补写专门建表不值得。

    而且退回去也没意义——退回去就是回到「印得出来、搜不到」的老样子。
    保持原样即可。"""


class Migration(migrations.Migration):
    dependencies = [("core", "0030_assistant_proposals_and_leases")]

    operations = [migrations.RunPython(forwards, backwards)]
