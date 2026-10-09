"""中断的上传不该把老师的整卷副本留在磁盘上。

上传视图先写 ``DATA_ROOT/.uploading-<uuid>/`` 再校验文件，校验不过就在同一个
请求里删掉。可 rmtree 在 Windows 上会因为文件被占用而失败，进程被强杀时更是走
不到那一步，目录就留下了。上传视图里那句「下次启动会按 .uploading-* 规则继续
清理」说的就是 :func:`core.apps.discard_interrupted_uploads`——它以前并不存在。
"""
import os
import tempfile
import time
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from .apps import UPLOAD_STAGING_MIN_AGE_SECONDS, discard_interrupted_uploads


class DiscardInterruptedUploadsTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(self._remove)
        self._root_settings = override_settings(DATA_ROOT=self.root)
        self._root_settings.enable()
        self.addCleanup(self._root_settings.disable)

    def _remove(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def staging(self, name: str, payload: bytes = b"%PDF-1.4 x") -> Path:
        entry = self.root / name
        entry.mkdir()
        (entry / "source.pdf").write_bytes(payload)
        return entry

    def age(self, entry: Path, seconds: float) -> Path:
        """把 entry 底下所有东西都改成 seconds 秒前。"""
        stamp = time.time() - seconds
        for path in [entry, *entry.rglob("*")]:
            os.utime(path, (stamp, stamp))
        return entry

    def test_an_interrupted_upload_is_removed_on_startup(self):
        entry = self.age(self.staging(".uploading-abc-def"), 3600)
        self.assertEqual(discard_interrupted_uploads(), [".uploading-abc-def"])
        self.assertFalse(entry.exists())

    def test_a_fresh_staging_dir_is_left_alone(self):
        # 正常上传也是先建暂存目录再校验；同一时刻另一个进程正在传，不能删它的。
        entry = self.staging(".uploading-live-now")
        self.assertEqual(discard_interrupted_uploads(), [])
        self.assertTrue(entry.exists())
        self.assertTrue((entry / "source.pdf").exists())

    def test_a_still_being_written_dir_is_never_removed(self):
        # 200MB 的卷在慢盘上能写十几分钟，而目录是上传开始时建的。
        # 只看目录自己的时间会把它当成陈旧目录，连正在写的文件一起删掉。
        entry = self.age(self.staging(".uploading-writing-now"), 3600)   # 目录看起来很旧
        self.age(entry / "source.pdf", 2)                                # 可文件刚刚还在写
        self.assertEqual(discard_interrupted_uploads(), [])
        self.assertTrue((entry / "source.pdf").exists())

    def test_real_paper_folders_are_never_touched(self):
        paper = self.staging("3f9c1d2e-4a5b", b"%PDF-1.4 real")
        self.age(paper, 86400)
        self.assertEqual(discard_interrupted_uploads(), [])
        self.assertTrue((paper / "source.pdf").exists())

    def test_other_junk_is_never_touched(self):
        keep = self.root / ".uploading-not-a-directory"
        keep.write_bytes(b"just a file")
        other = self.staging("tmp")
        self.age(other, 3600)
        discard_interrupted_uploads()
        self.assertTrue(keep.exists())
        self.assertTrue(other.exists())

    def test_a_locked_dir_does_not_stop_startup(self):
        entry = self.age(self.staging(".uploading-locked"), 3600)
        self._deny_removal(entry)
        self.assertEqual(discard_interrupted_uploads(), [])   # 不抛异常
        self.assertTrue(entry.exists())

    @staticmethod
    def _deny_removal(entry: Path):
        import shutil as real_shutil
        from unittest import mock
        original = real_shutil.rmtree

        def guarded(path, *args, **kwargs):
            if Path(path) == entry:
                raise OSError(5, "Access is denied")
            return original(path, *args, **kwargs)

        patcher = mock.patch("core.apps.shutil.rmtree", guarded)
        patcher.start()
        return patcher

    def test_a_missing_data_root_is_not_an_error(self):
        with override_settings(DATA_ROOT=self.root / "nope"):
            self.assertEqual(discard_interrupted_uploads(), [])

    def test_the_age_guard_is_long_enough_to_cover_a_slow_upload(self):
        # 200MB 的卷在慢盘上写几分钟很正常，暂存目录刚建好不该被启动清理扫掉。
        self.assertGreaterEqual(UPLOAD_STAGING_MIN_AGE_SECONDS, 600)