import logging
import shutil
import time
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings

from core import verdict_cache

logger = logging.getLogger(__name__)

UPLOAD_STAGING_PREFIX = ".uploading-"
UPLOAD_STAGING_MIN_AGE_SECONDS = 600
# 删除任务时先把卷子目录改名成这个，再删数据库记录、最后删文件。
DELETE_STAGING_PREFIX = ".deleting-"
DELETE_STAGING_MIN_AGE_SECONDS = 600


def discard_interrupted_uploads() -> list[str]:
    """删掉上一次没能收尾的上传暂存目录。

    ``views.papers`` 先把文件写进 ``DATA_ROOT/.uploading-<uuid>/`` 再校验；校验不过
    就在同一个请求里删掉它。可 ``rmtree`` 在 Windows 上会因为文件被占用而失败，
    进程被强杀时更是根本走不到那一步——目录就留在磁盘上，里面是老师那份卷子的
    完整副本（PDF 上限 200 MB）。上传视图里那句「下次启动会按 .uploading-* 规则继续
    清理」说的就是这里：这个函数以前并不存在，所以那句话一直是假的。

    只处理 ``DATA_ROOT`` 下一层、名字以 ``.uploading-`` 开头的目录，并且放过还在
    写的——200 MB 的卷在慢盘上能写十几分钟，而目录是上传**开始时**建的，看目录自身
    的时间会把它当成陈旧目录，连正在写的文件一起删掉。所以年龄取「里面最新的那个
    文件」的时间：正在写就一直新鲜。另外放过刚建好不到 10 分钟的，万一同一时刻
    有另一个进程在传。启动路径上抛异常就是软件起不来，所以全部吞掉。
    """
    removed: list[str] = []
    root = Path(settings.DATA_ROOT)
    if not root.is_dir():
        return removed
    now = time.time()
    for entry in root.iterdir():
        if not entry.name.startswith(UPLOAD_STAGING_PREFIX) or not entry.is_dir():
            continue
        try:
            if now - _newest_mtime(entry) < UPLOAD_STAGING_MIN_AGE_SECONDS:
                continue
            shutil.rmtree(entry)
        except OSError as exc:
            logger.warning("清理残留的上传暂存目录 %s 失败：%s", entry, exc)
            continue
        removed.append(entry.name)
    if removed:
        logger.info("已清理 %d 个中断的上传暂存目录：%s", len(removed), "、".join(removed))
    return removed


def discard_interrupted_deletes() -> list[str]:
    """删掉上一次没删干净的「正在删除」目录。

    删一项任务时先把它的卷子目录改名成 ``.deleting-<uuid>-<hex>``，再删数据库记录、
    最后删文件。第三步在 Windows 上会因为文件正被打开而失败，界面于是告诉老师
    「关闭程序并重新启动后会继续清理」——**那句话以前是假的**：启动清理只认
    ``.uploading-``，``.deleting-`` 没有人管，那份原卷（PDF 上限 200 MB）就永远留在
    磁盘上。现在这句话有兑现的地方了。

    年龄判断比上传那条更要紧：目录是改**名**得来的，改名不刷新时间戳（实测 mtime 和
    ctime 都还是上传那天的），一份三天前传的老卷子被改名后看起来就像陈旧目录。所以
    ``views.paper`` 改完名会顺手 ``os.utime`` 一下，这里才有「刚改名」和「上次没删
    干净」的分界。也因此同一个 10 分钟的门槛不能省：刚改完名、事务还没提交的目录要是
    被这里扫掉，回滚时 ``staged.replace(folder)`` 就会失败，留下一条文件已经没了的
    数据库记录。
    """
    removed: list[str] = []
    root = Path(settings.DATA_ROOT)
    if not root.is_dir():
        return removed
    now = time.time()
    for entry in root.iterdir():
        if not entry.name.startswith(DELETE_STAGING_PREFIX) or not entry.is_dir():
            continue
        try:
            if now - _newest_mtime(entry) < DELETE_STAGING_MIN_AGE_SECONDS:
                continue
            shutil.rmtree(entry)
        except OSError as exc:
            logger.warning("清理残留的待删除目录 %s 失败：%s", entry, exc)
            continue
        removed.append(entry.name)
    if removed:
        logger.info("已清理 %d 个上次没删干净的任务目录：%s", len(removed), "、".join(removed))
    return removed


def _newest_mtime(folder: Path) -> float:
    """目录里最新的一个时间戳；空目录就是它自己的时间。"""
    newest = folder.stat().st_mtime
    for path in folder.rglob("*"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _tune_sqlite(sender, connection, **kwargs):
    """把 SQLite 的内存映射和页缓存调大。

    1.26.36（性能）：题库列表一页 100 道、那份 323 页卷子的详情 762 道，
    都要反复读同一批行。SQLite 默认页缓存只有 2 MB（-2000），这些行一遍遍从磁盘
    读出来；mmap 让它直接映射文件、页缓存给到 64 MB，翻页和列表查询明显快。
    """
    if connection.vendor != "sqlite":
        return
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA mmap_size=268435456")  # 256 MB
        cursor.execute("PRAGMA cache_size=-65536")  # 64 MB
        cursor.execute("PRAGMA temp_store=MEMORY")


def _start_background_work() -> None:
    try:
        from django.db.backends.signals import connection_created

        connection_created.connect(_tune_sqlite, dispatch_uid="qb_tune_sqlite")
    except Exception as exc:
        logger.warning("SQLite 调优没挂上：%s", exc)
    # 1.26.50：这里原来会后台先把 /api/papers 跑一遍。现在不跑了 ——
    # 老师打开软件时页面本来就要拉这个接口，两边各算一遍反而更慢；
    # 而且判定结果现在会落盘复用（core/verdict_cache.py），第二次打开本来就快。


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        # 1.26.50：判定结果存到磁盘复用 —— 第二次打开软件不用再解析几千道题
        # 的 JSON 字段（冷启动那一遍要 3.9 秒）。接不上也不影响软件能用。
        verdict_cache.install()
        for sweep in (discard_interrupted_uploads, discard_interrupted_deletes):
            try:
                sweep()
            except Exception as exc:  # 清理失败绝不能挡住软件启动。
                logger.warning("启动时清理残留目录未完成：%s", exc)
        _start_background_work()
