r"""把「全屏保存往返验证」这类测试标记从库副本里清掉。

只对开发用的库副本跑（QB_DATABASE 指到 tmp 下的那份），不碰真实安装版。
题有据没有删除已保存答案解析的接口，所以往返验证留下的那一行只能在这里清。

用法（在 backend 目录下）： python ..\tools\clean_roundtrip_marks.py --mark 全屏保存往返验证
"""

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mark", default="全屏保存往返验证")
    args = parser.parse_args()
    database = os.environ.get("QB_DATABASE", "")
    if "tmp" not in database.replace("\\", "/"):
        raise SystemExit(f"Refusing to touch {database or '(unset)'}: not a tmp copy")

    import django
    django.setup()
    from core.models import LibrarySolution, PublishedQuestion

    hits = LibrarySolution.objects.filter(answer__contains=args.mark) | \
        LibrarySolution.objects.filter(analysis__contains=args.mark)
    hit_ids = list(hits.values_list("id", flat=True))
    print("带标记的已保存解析:", len(hit_ids))
    for publication in PublishedQuestion.objects.filter(
            extras__solution_id__in=[str(value) for value in hit_ids]):
        extras = dict(publication.extras or {})
        extras.pop("solution_id", None)
        extras.pop("solution_revision", None)
        publication.extras = extras
        publication.save(update_fields=["extras"])
        print("  清掉", publication.pk)
    LibrarySolution.objects.filter(id__in=hit_ids).delete()
    left = LibrarySolution.objects.filter(answer__contains=args.mark).count() \
        + LibrarySolution.objects.filter(analysis__contains=args.mark).count()
    print("剩下的标记行:", left)
    if not left:
        # SQLite keeps deleted bytes in free pages, so a later grep would still find
        # the marker in the raw file. Reclaim the space.
        from django.db import connection
        connection.cursor().execute("VACUUM")
        print("已 VACUUM，文件里也找不到了")
    return 0 if left == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
