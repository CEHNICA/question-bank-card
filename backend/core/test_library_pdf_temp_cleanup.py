"""导出 PDF 的临时目录：文件被占用时必须重试，而不是整棵 6 MB 留在磁盘上。

背景（实测）：Windows 上文件被别的进程打开着就删不掉，
``shutil.rmtree`` 会抛 ``PermissionError [WinError 32]``，而且撞上一个删不掉的
文件就整棵放弃。导出 PDF 用的是一整个浏览器 profile（一次 6 MB 上下），
以前清理只试一次：失败就只记一条日志，目录永远留在系统临时盘里没人回收。
"""

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from core import library_pdf as pdf


class _FlakyDirectory:
    """第一次 cleanup 撞上「文件被占用」，第二次才删得掉。"""

    def __init__(self, path):
        self.name = str(path)
        self.calls = 0

    def cleanup(self):
        self.calls += 1
        if self.calls == 1:
            raise PermissionError(13, "另一个程序正在使用此文件")
        shutil.rmtree(self.name, ignore_errors=True)


class _StubbornDirectory:
    """怎么都删不掉。"""

    def __init__(self, path):
        self.name = str(path)
        self.calls = 0

    def cleanup(self):
        self.calls += 1
        raise PermissionError(13, "另一个程序正在使用此文件")


class PdfTempDirectoryCleanupTests(SimpleTestCase):
    def test_a_transient_lock_is_retried_and_the_directory_ends_up_gone(self):
        """第一次删不掉不算完：隔一会儿再试，目录必须删干净。"""
        root = Path(tempfile.mkdtemp(prefix="fake-pdf-temp-"))
        flaky = _FlakyDirectory(root)
        with mock.patch.object(pdf.tempfile, "TemporaryDirectory", return_value=flaky):
            with mock.patch.object(pdf, "CLEANUP_BACKOFF_SECONDS", (0, 0)):
                with pdf._temporary_directory() as directory:
                    self.assertEqual(directory, str(root))
                    (Path(directory) / "profile").mkdir()
                    (Path(directory) / "profile" / "cache.bin").write_bytes(b"cache")
        self.assertEqual(flaky.calls, 2, "第一次失败后必须再试一次")
        self.assertFalse(root.exists(), "临时目录必须删干净，不能留在磁盘上")

    def test_a_locked_file_on_windows_is_recovered_by_the_retry(self):
        """真锁：文件被打开着就删不掉，Windows 上实测 PermissionError。

        浏览器退出后句柄是异步释放的，所以这里模拟「过一会儿松手」：
        第一次清理撞上锁失败，隔一会儿重试时句柄已经松开，目录就该被删掉。
        """
        if os.name != "nt":
            self.skipTest("只有 Windows 才会出现「文件被占用删不掉」")
        released = threading.Event()
        handle = None
        with pdf._temporary_directory() as directory:
            target = Path(directory) / "profile" / "cache.bin"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"cache")
            handle = open(target, "r+b")

            def release_later():
                time.sleep(0.3)
                handle.close()
                released.set()

            thread = threading.Thread(target=release_later)
            thread.start()
        # 出 with 块时清理已经重试过几轮；线程这时应该已经松了手。
        thread.join(timeout=10)
        self.assertTrue(released.is_set(), "模拟的句柄必须松开了")
        self.assertFalse(Path(directory).exists(), "锁松开后重试必须把目录删干净")

    def test_cleanup_that_never_succeeds_leaves_the_directory_for_the_sweeper(self):
        """删不掉时不能把导出搞崩：目录留给启动清扫回收，只记一条告警。"""
        root = Path(tempfile.mkdtemp(prefix="fake-pdf-stubborn-"))
        stubborn = _StubbornDirectory(root)
        body_ran = []
        with mock.patch.object(pdf.tempfile, "TemporaryDirectory", return_value=stubborn):
            with mock.patch.object(pdf, "CLEANUP_BACKOFF_SECONDS", (0, 0)):
                with mock.patch.object(pdf, "_cleanup_warning") as warning:
                    with pdf._temporary_directory() as directory:
                        body_ran.append(directory)
        self.assertEqual(body_ran, [str(root)], "清理失败不能把导出本身搞崩")
        self.assertEqual(stubborn.calls, pdf.CLEANUP_ATTEMPTS, "重试到上限就该放弃")
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[0], "temporary_directory")
        shutil.rmtree(root, ignore_errors=True)
