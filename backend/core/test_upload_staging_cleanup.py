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

from .apps import (
    DELETE_STAGING_MIN_AGE_SECONDS,
    UPLOAD_STAGING_MIN_AGE_SECONDS,
    discard_interrupted_deletes,
    discard_interrupted_uploads,
)


class _StagingRootMixin:
    """两条清理规则共用的脚手架：一个临时数据目录，加上「造目录」「改年龄」两个动作。"""

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


class DiscardInterruptedUploadsTests(_StagingRootMixin, SimpleTestCase):
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
        entry = self.age(self.staging(".uploading-writing-now"), 3600)  # 目录看起来很旧
        self.age(entry / "source.pdf", 2)  # 可文件刚刚还在写
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
        self.assertEqual(discard_interrupted_uploads(), [])  # 不抛异常
        self.assertTrue(entry.exists())

    def test_a_missing_data_root_is_not_an_error(self):
        with override_settings(DATA_ROOT=self.root / "nope"):
            self.assertEqual(discard_interrupted_uploads(), [])

    def test_the_age_guard_is_long_enough_to_cover_a_slow_upload(self):
        # 200MB 的卷在慢盘上写几分钟很正常，暂存目录刚建好不该被启动清理扫掉。
        self.assertGreaterEqual(UPLOAD_STAGING_MIN_AGE_SECONDS, 600)


class DiscardInterruptedDeletesTests(_StagingRootMixin, SimpleTestCase):
    """删任务时残留的目录，界面上承诺「重启后会继续清理」——以前没人做这件事。

    删一项任务先把卷子目录改名成 ``.deleting-<uuid>-<hex>``，再删数据库记录、最后删
    文件。最后一步在 Windows 上会被「文件正被打开」打断，``views.paper`` 于是返回
    「任务已删除，但有残留文件暂时被占用；关闭程序并重新启动后会继续清理」。
    启动清理当时只认 ``.uploading-``，那句话一直是假的。
    """

    def test_a_delete_that_never_finished_is_removed_on_startup(self):
        entry = self.age(self.staging(".deleting-3f9c1d2e-4a5b-" + "b" * 32), 3600)
        self.assertEqual(discard_interrupted_deletes(), [entry.name])
        self.assertFalse(entry.exists())

    def test_a_just_renamed_dir_is_left_alone(self):
        # 改完名、事务还没提交的那一瞬：这里扫掉它，回滚就再也还原不回去了。
        entry = self.staging(".deleting-3f9c1d2e-4a5b-" + "c" * 32)
        self.assertEqual(discard_interrupted_deletes(), [])
        self.assertTrue((entry / "source.pdf").exists())

    def test_renaming_does_not_refresh_the_timestamp(self):
        # 整条规则的地基：目录改名**不刷新时间戳**（实测 mtime 和 ctime 都还是上传
        # 那天的），光看 mtime 分不出「刚改名」和「上次没删干净」。真实卷子可能是三天
        # 前传的，改完名看起来就像陈旧目录。
        source = self.staging("3f9c1d2e-4a5b-4b61-aa11-000000000001")
        self.age(source, 86400)
        staged = self.root / (".deleting-3f9c1d2e-4a5b-4b61-aa11-000000000001-" + "d" * 32)
        source.replace(staged)
        self.assertLess(staged.stat().st_mtime, time.time() - 3600)  # 看起来很旧

        # views.paper 改完名立刻 os.utime 一下，扫描才分得出新旧。
        os.utime(staged, None)
        self.assertEqual(discard_interrupted_deletes(), [])
        self.assertTrue((staged / "source.pdf").exists())

    def test_a_live_paper_folder_is_never_swept(self):
        paper = self.staging("3f9c1d2e-4a5b", b"%PDF-1.4 real")
        self.age(paper, 86400)
        self.assertEqual(discard_interrupted_deletes(), [])
        self.assertTrue((paper / "source.pdf").exists())

    def test_an_upload_staging_dir_is_left_to_the_other_sweep(self):
        entry = self.age(self.staging(".uploading-abc-def"), 3600)
        self.assertEqual(discard_interrupted_deletes(), [])
        self.assertEqual(discard_interrupted_uploads(), [".uploading-abc-def"])

    def test_a_locked_delete_dir_does_not_stop_startup(self):
        entry = self.age(self.staging(".deleting-locked-" + "e" * 32), 3600)
        self._deny_removal(entry)
        self.assertEqual(discard_interrupted_deletes(), [])  # 不抛异常
        self.assertTrue(entry.exists())

    def test_the_age_guard_is_long_enough_to_cover_an_in_flight_delete(self):
        self.assertGreaterEqual(DELETE_STAGING_MIN_AGE_SECONDS, 600)

    def test_a_missing_data_root_is_not_an_error(self):
        with override_settings(DATA_ROOT=self.root / "nope"):
            self.assertEqual(discard_interrupted_deletes(), [])
