"""测试系统临时目录（剪贴板出图、排版截图、PDF 排版 profile）的安全清理机制。"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from core.apps import discard_stale_system_temps


class SystemTempCleanupTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.fake_temp = Path(tempfile.mkdtemp(prefix="fake-sys-temp-"))

    def tearDown(self):
        shutil.rmtree(self.fake_temp, ignore_errors=True)
        super().tearDown()

    def _age(self, path, seconds):
        """把这个目录和里面所有文件的时间戳往前推。"""
        stamp = time.time() - seconds
        os.utime(path, (stamp, stamp))
        for child in path.rglob("*"):
            os.utime(child, (stamp, stamp))

    def test_stale_clipboard_and_shot_temps_are_cleaned(self):
        """测试超过 1 小时的 tiyouju-clip-* 与 tiyouju-shot-* 目录被正常清理。"""
        stale_clip = self.fake_temp / "tiyouju-clip-stale123"
        stale_clip.mkdir()
        (stale_clip / "image-0.png").write_bytes(b"png-data")

        stale_shot = self.fake_temp / "tiyouju-shot-stale456"
        stale_shot.mkdir()
        (stale_shot / "profile").mkdir()

        # 将时间戳设为 2 小时前
        two_hours_ago = time.time() - 7200
        os.utime(stale_clip / "image-0.png", (two_hours_ago, two_hours_ago))
        os.utime(stale_clip, (two_hours_ago, two_hours_ago))
        os.utime(stale_shot / "profile", (two_hours_ago, two_hours_ago))
        os.utime(stale_shot, (two_hours_ago, two_hours_ago))

        # 刚生成不久的新目录（比如 5 分钟前）
        fresh_clip = self.fake_temp / "tiyouju-clip-fresh789"
        fresh_clip.mkdir()
        (fresh_clip / "image-0.png").write_bytes(b"fresh-data")

        # 其他程序的临时目录
        other_dir = self.fake_temp / "other-app-temp"
        other_dir.mkdir()

        with mock.patch("tempfile.gettempdir", return_value=str(self.fake_temp)):
            removed = discard_stale_system_temps()

        self.assertIn("tiyouju-clip-stale123", removed)
        self.assertIn("tiyouju-shot-stale456", removed)
        self.assertNotIn("tiyouju-clip-fresh789", removed)
        self.assertNotIn("other-app-temp", removed)

        self.assertFalse(stale_clip.exists())
        self.assertFalse(stale_shot.exists())
        self.assertTrue(fresh_clip.exists())
        self.assertTrue(other_dir.exists())

    def test_stale_pdf_export_profile_is_reclaimed(self):
        """导出 PDF 留下的浏览器 profile 目录也必须被回收。

        一次 PDF 导出就是 6 MB 上下（整个浏览器 profile）。以前这个前缀不在
        启动清扫的名单里，导出中途被掐断留下的目录永远没人管 —— 实测能攒下
        十几个、几十 MB。所以这里钉住：跟剪贴板那套一个待遇。
        """
        stale_pdf = self.fake_temp / "tiyouju-pdf-stale789"
        stale_pdf.mkdir()
        (stale_pdf / "profile").mkdir()
        (stale_pdf / "profile" / "Default").mkdir()
        (stale_pdf / "profile" / "Default" / "Cache").write_bytes(b"cache")

        self._age(stale_pdf, 7200)

        fresh_pdf = self.fake_temp / "tiyouju-pdf-fresh321"
        fresh_pdf.mkdir()
        (fresh_pdf / "profile").mkdir()

        with mock.patch("tempfile.gettempdir", return_value=str(self.fake_temp)):
            removed = discard_stale_system_temps()

        self.assertIn("tiyouju-pdf-stale789", removed)
        self.assertNotIn("tiyouju-pdf-fresh321", removed)
        self.assertFalse(stale_pdf.exists())
        self.assertTrue(fresh_pdf.exists())

    def test_a_pdf_export_in_progress_is_never_swept(self):
        """正在导出时那个 profile 一直在写文件 —— 看最新的文件时间，不能被当成陈旧目录。"""
        exporting = self.fake_temp / "tiyouju-pdf-running"
        exporting.mkdir()
        profile = exporting / "profile"
        profile.mkdir()
        # 目录自身的时间很老，但里面的文件是刚刚写的。
        os.utime(exporting, (time.time() - 7200, time.time() - 7200))
        os.utime(profile, (time.time() - 7200, time.time() - 7200))
        (profile / "DevToolsActivePort").write_bytes(b"12345\n/devtools/browser/x")

        with mock.patch("tempfile.gettempdir", return_value=str(self.fake_temp)):
            removed = discard_stale_system_temps()

        self.assertNotIn("tiyouju-pdf-running", removed)
        self.assertTrue(exporting.exists())
