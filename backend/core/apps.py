import logging
import shutil
import time
from pathlib import Path

from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

UPLOAD_STAGING_PREFIX = ".uploading-"
UPLOAD_STAGING_MIN_AGE_SECONDS = 600


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


def _newest_mtime(folder: Path) -> float:
    """目录里最新的一个时间戳；空目录就是它自己的时间。"""
    newest = folder.stat().st_mtime
    for path in folder.rglob("*"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        try:
            discard_interrupted_uploads()
        except Exception as exc:  # 清理失败绝不能挡住软件启动。
            logger.warning("启动时清理上传暂存目录未完成：%s", exc)
