"""按现在的规则重算题卡的搜索索引。

卡面上印着的章节名要能搜到，靠的是入库时算好的那一列搜索索引。改了搜索规则以后，
老卡片还存着旧索引 —— 印得出来、搜不到。1.26.44 实测：真库 1267 张里 358 张的索引
是旧规则算的，其中 169 张只差章节名这一项。

这个命令按现规则重算一遍，**只写搜索索引那一列**：题干、答案、解析、图片、旗标、
标签一个字都不动，也不重新切题、不调用识读服务。

    manage.py refresh_search_index              # 只看会改哪些，不写
    manage.py refresh_search_index --apply      # 真写
    manage.py refresh_search_index --paper 必修第一册.pdf
"""

from django.core.management.base import BaseCommand

from core import library
from core.models import PublishedQuestion

# 老卡片的快照里可能缺字段，补齐再算，免得算一半报错。
DEFAULTS = {"stem": "", "options": {}, "answer": "", "analysis": ""}


class Command(BaseCommand):
    help = "按现在的规则重算题卡的搜索索引（只写索引这一列，题干答案图片不动）"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="真的写回数据库；不加就只列出哪些会变")
        parser.add_argument("--paper", default="", help="只处理这一份资料的文件名（逗号分隔，可重复）")

    def handle(self, *args, **options):
        queryset = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED)
        names = [name.strip() for name in str(options.get("paper") or "").split(",") if name.strip()]
        if names:
            queryset = queryset.filter(source_filename__in=names)

        changed = []
        for publication in queryset.only("id", "content", "extras", "search_text", "source_filename").iterator():
            content = dict(publication.content or {})
            for key, blank in DEFAULTS.items():
                content.setdefault(key, blank)
            fresh = library._search_text(content, publication.extras)
            if fresh == (publication.search_text or ""):
                continue
            changed.append((publication, fresh))

        if not changed:
            self.stdout.write("没有要改的：每张卡的搜索索引都跟现规则一致。")
            return

        if options.get("apply"):
            for publication, fresh in changed:
                PublishedQuestion.objects.filter(pk=publication.pk).update(search_text=fresh)
            self.stdout.write(f"已更新 {len(changed)} 张卡的搜索索引（题干、答案、图片未动）。")
            return

        self.stdout.write(f"有 {len(changed)} 张卡的搜索索引跟现规则对不上（还没写）：")
        for publication, _ in changed[:20]:
            section = str((publication.content or {}).get("section") or "").strip()
            self.stdout.write(f"  {publication.source_filename} · {section or '（无章节名）'}")
        if len(changed) > 20:
            self.stdout.write(f"  …… 还有 {len(changed) - 20} 张")
        self.stdout.write("确认无误再加 --apply 写回。")
