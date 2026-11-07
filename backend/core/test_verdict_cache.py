"""判定结果落盘复用的测试。

盯住四件事：
  1. 算过的判定能存到磁盘，下次启动读回来（这才有「第二次打开快」）
  2. 指纹变了就不用旧结果（题目改过，判定必须重算）
  3. 判定规则变了，整份缓存作废（版本号对不上就重来）
  4. 缓存文件坏了 / 写不下去，都不能让软件起不来
"""

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from core import verdict_cache


class VerdictCacheTests(SimpleTestCase):
    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(self._folder.name)
        self.settings = override_settings(QB_DATA_ROOT=self.root)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.addCleanup(self._folder.cleanup)

    def cache_file(self) -> Path:
        return self.root / "runtime" / "verdict-cache.json"

    def make_cache(self) -> verdict_cache.DiskVerdictCache:
        return verdict_cache.DiskVerdictCache(path=self.cache_file())

    def test_results_are_written_and_read_back(self) -> None:
        first = self.make_cache()
        first[7] = ("abc123", "通过", True, False, True)
        first.flush()

        self.assertTrue(self.cache_file().is_file(), "判定结果应该写到磁盘上")

        second = self.make_cache()
        self.assertEqual(second.get(7), ("abc123", "通过", True, False, True))

    def test_nothing_is_written_when_nothing_changed(self) -> None:
        cache = self.make_cache()
        cache.flush()
        self.assertFalse(self.cache_file().exists(), "没有新结果时不该凭空写文件")

    def test_broken_cache_file_does_not_break_startup(self) -> None:
        self.cache_file().parent.mkdir(parents=True, exist_ok=True)
        self.cache_file().write_text("{ 这不是 json", encoding="utf-8")

        cache = self.make_cache()
        self.assertEqual(len(cache), 0, "坏文件要当作没有缓存")

    def test_cache_from_other_rules_is_dropped(self) -> None:
        payload = {
            "rules": verdict_cache.VERDICT_RULES_VERSION + 1,
            "entries": {"7": ["abc123", "通过", True, False, True]},
        }
        self.cache_file().parent.mkdir(parents=True, exist_ok=True)
        self.cache_file().write_text(json.dumps(payload), encoding="utf-8")

        cache = self.make_cache()
        self.assertEqual(len(cache), 0, "判定规则变了，旧结果不能用")

    def test_entries_without_the_expected_shape_are_skipped(self) -> None:
        payload = {
            "rules": verdict_cache.VERDICT_RULES_VERSION,
            "entries": {"7": ["abc123", "通过", True, False, True], "8": ["只有指纹"], "9": "不是列表"},
        }
        self.cache_file().parent.mkdir(parents=True, exist_ok=True)
        self.cache_file().write_text(json.dumps(payload), encoding="utf-8")

        cache = self.make_cache()
        self.assertEqual(len(cache), 1, "只有形状对得上的那条能进来")
        self.assertEqual(cache.get(7), ("abc123", "通过", True, False, True))

    def test_writing_to_an_unwritable_place_does_not_raise(self) -> None:
        cache = verdict_cache.DiskVerdictCache(path=self.root / "runtime" / "no-such-dir" / "x" / "cache.json")
        cache[3] = ("abc", "通过", True, False, False)
        cache.flush()  # 不抛异常就算过

    def test_cache_file_never_contains_question_text(self) -> None:
        cache = self.make_cache()
        cache[1] = ("fingerprint", "通过", True, False, False)
        cache.flush()

        written = self.cache_file().read_text(encoding="utf-8")
        self.assertNotIn("题干", written)
        self.assertIn("fingerprint", written)


class VerdictCacheInstallTests(SimpleTestCase):
    """谁可以挂上这份缓存。"""

    def test_only_the_web_server_process_gets_the_cache(self):
        for argv, expected in (
            (["manage.py", "runserver", "127.0.0.1:8768"], True),
            (["manage.py", "run_worker"], False),
            (["manage.py", "shell"], False),
            (["manage.py", "test", "core"], False),
        ):
            with mock.patch.object(verdict_cache.sys, "argv", argv):
                with mock.patch.object(verdict_cache, "_enabled", return_value=True):
                    # 只换缓存落盘的位置，类本身不动 ——
                    # install() 里要 isinstance 判断它是不是已经接过。
                    with mock.patch.object(verdict_cache, "cache_path", return_value=self._path):
                        with mock.patch.object(verdict_cache, "_Flusher"):
                            with mock.patch("core.views._VERDICTS", {}):
                                attached = verdict_cache.install()
            self.assertEqual(attached, expected, f"{argv} 这个进程{'应该' if expected else '不应该'}挂缓存")

    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(self._folder.name)
        self._path = self.root / "runtime" / "verdict-cache.json"
        self.addCleanup(self._folder.cleanup)


class VerdictCacheWriteOnceTests(SimpleTestCase):
    """读回来的东西不该被当成「刚改过」。"""

    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.root = Path(self._folder.name)
        self.cache_file = self.root / "runtime" / "verdict-cache.json"
        self.addCleanup(self._folder.cleanup)

    def test_reading_the_cache_back_does_not_make_it_dirty(self):
        first = verdict_cache.DiskVerdictCache(path=self.cache_file)
        first[7] = ("abc123", "通过", True, False, True)
        first.flush()
        before = self.cache_file.read_bytes()

        second = verdict_cache.DiskVerdictCache(path=self.cache_file)
        self.assertEqual(len(second), 1, "读回来的判定要还在")
        self.assertFalse(second._dirty, "读回来的东西不算「刚改过」")

        # 真要是被当成刚改过，这一行就会把文件重写一遍。
        second.flush()
        self.assertEqual(self.cache_file.read_bytes(), before, "读回来之后不该白写一遍盘")
