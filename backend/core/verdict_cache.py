"""判定结果的落盘复用 —— 让软件第二次打开时不用再解析几千道题的 JSON。

背景
----
打开软件时，后台服务要列出所有试卷。每份卷都要给每道题算一次「判定」
（这道题能不能通过、配图有没有挡住、题库里是不是已经是这一版）。第一次算的时候，
系统要把几千道题的 JSON 字段整个解析一遍 —— 实测冷启动 3.9 秒，热点全在
``json.loads``（8725 次）。之后同一个进程里再问就走内存缓存，只要 0.05 秒。

于是「打开慢」实际上是「每次打开都重算一遍」。而算出来的判定结果，
只要题目本身没动过，就还是同一个 —— 指纹已经包含了每一道题的全部字段、
题组、卷名和题库里的版本号。指纹一样，判定就一定一样。

做法
----
把判定结果存到磁盘（``DATA_ROOT/runtime/verdict-cache.json``），
下次启动先读回来填进内存缓存，指纹对得上就不用重算。

不改 ``views.card_verdicts``：它是模块级 ``OrderedDict``，这里直接把那个对象
换成一个同样接口、但多带一层落盘的实现。

安全
----
* 落盘的文件只存「题目编号 → 指纹 + 判定」，不含题干、不含答案、不含图片。
* 指纹变了就重算，指纹是题目全部字段算出来的，任何改动都会换指纹。
* 判定规则本身变了（``_verdict`` 的算法改了），指纹算不出区别，
  所以下面这个版本号必须跟着改，否则会拿旧规则的结果当新的用。
  **改了 ``_verdict`` 的判定逻辑，就把 ``VERDICT_RULES_VERSION`` 加一。**
* 落盘失败、文件损坏、版本对不上，一律当作没有缓存，重新算。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 判定规则变了就加一。旧缓存会被整份丢掉，重新算一遍。
VERDICT_RULES_VERSION = 1

CACHE_FILE_NAME = "verdict-cache.json"
# 落盘最多隔多久写一次。写盘期间要占 CPU，冷启动那一波几百条一次写完最好。
FLUSH_INTERVAL_SECONDS = 2.0
# 缓存文件最大多少字节。超过就整份丢掉重来（说明题库大变了）。
MAX_CACHE_BYTES = 32 * 1024 * 1024


def cache_path() -> Path:
    from django.conf import settings

    return Path(settings.DATA_ROOT) / "runtime" / CACHE_FILE_NAME


def _enabled() -> bool:
    """测试和命令行工具不落盘，免得把老师的真数据目录当测试沙箱。"""
    argv = list(sys.argv)
    if "test" in argv:
        return False
    # 一次性命令不该写缓存（跑完就退出，写了也没用）。
    one_shot = {
        "test",
        "migrate",
        "makemigrations",
        "shell",
        "collectstatic",
        "dumpdata",
        "loaddata",
        "createsuperuser",
    }
    if any(item in one_shot for item in argv):
        return False
    try:
        from django.conf import settings

        if getattr(settings, "QA_TESTING", False):
            return False
    except Exception:  # pragma: no cover - 设置还没准备好
        return False
    return True


class DiskVerdictCache(OrderedDict):
    """内存判定缓存 + 落盘一层。

    接口和 ``collections.OrderedDict`` 完全一样，``views.card_verdicts``
    怎么用它，这里就怎么用；多的只是：算过的结果会存到磁盘，下次启动直接复用。
    """

    def __init__(self, *args: Any, path: Path | str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._dirty = False
        if path is not None:
            self._path = Path(path)
        else:
            self._path = cache_path() if _enabled() else None
        self._load()

    # ---- 读 -------------------------------------------------------

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            if not self._path.is_file():
                return
            if self._path.stat().st_size > MAX_CACHE_BYTES:
                logger.info("判定缓存太大，不复用，重新算一遍。")
                return
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.info("判定缓存读不了，重新算一遍：%s", exc)
            return
        if not isinstance(payload, dict):
            return
        if payload.get("rules") != VERDICT_RULES_VERSION:
            logger.info("判定规则变了，缓存作废，重新算一遍。")
            return
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            return
        restored = 0
        for key, value in entries.items():
            try:
                pk = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, list) or len(value) != 5:
                continue
            fingerprint, state, approved, blocked, settled = value
            if not isinstance(fingerprint, str) or not isinstance(state, str):
                continue
            self[pk] = (fingerprint, state, bool(approved), bool(blocked), bool(settled))
            restored += 1
        logger.info("判定缓存读回来了：%d 条", restored)
        # 读回来的东西不算「刚改过」，省得启动后马上又写一遍盘。
        self._dirty = False

    # ---- 写 -------------------------------------------------------

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self._dirty = True

    def flush(self) -> None:
        """把当前内容写到磁盘。写坏了也只影响下次启动快慢，不影响功能。"""
        if self._path is None or not self._dirty:
            return
        payload = {
            "rules": VERDICT_RULES_VERSION,
            "saved_at": time.time(),
            "entries": {str(pk): list(value) for pk, value in self.items()},
        }
        self._dirty = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError as exc:
            logger.info("判定缓存写不下去，下次启动会重算：%s", exc)


class _Flusher(threading.Thread):
    """后台定期把判定缓存写下去。写得慢不要紧，下次启动早就有。"""

    def __init__(self, cache: DiskVerdictCache) -> None:
        super().__init__(name="qb-verdict-flush", daemon=True)
        self._cache = cache

    def run(self) -> None:
        while True:
            time.sleep(FLUSH_INTERVAL_SECONDS)
            try:
                self._cache.flush()
            except Exception as exc:  # pragma: no cover - 落盘失败不该影响软件
                logger.info("判定缓存定时写盘失败：%s", exc)


def install() -> bool:
    """把 ``views`` 里的内存判定缓存换成带落盘的那一份。

    返回是否换成功。换不了也不影响软件能用 —— 只是下次打开还是慢一点。
    """
    if not _enabled():
        return False
    # 只挂在开网页服务那一个进程上。别的进程（后台干活的、工具脚本、
    # 一次性命令）用不着它 —— 免得几个进程同时写同一个文件，
    # 把刚写好的新结果盖成旧的。
    argv = list(sys.argv)
    if "runserver" not in argv:
        return False
    try:
        from core import views

        existing = views._VERDICTS
        if isinstance(existing, DiskVerdictCache):
            return True
        cache = DiskVerdictCache(existing)
        views._VERDICTS = cache
        _Flusher(cache).start()
        logger.info("判定缓存已接上落盘。")
        return True
    except Exception as exc:  # pragma: no cover - 接不上就还用原来的
        logger.info("判定缓存没接上（不影响使用）：%s", exc)
        return False
