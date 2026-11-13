"""测试剪贴板与出图临时文件目录的安全清理机制。"""

import os
import shutil
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from core.apps import discard_stale_clipboard_temps


class ClipboardTempCleanupTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        self.fake_temp = Path(tempfile.mkdtemp(prefix="fake-sys-temp-"))

    def tearDown(self):
        shutil.rmtree(self.fake_temp, ignore_errors=True)
        super().tearDown()

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
            removed = discard_stale_clipboard_temps()

        self.assertIn("tiyouju-clip-stale123", removed)
        self.assertIn("tiyouju-shot-stale456", removed)
        self.assertNotIn("tiyouju-clip-fresh789", removed)
        self.assertNotIn("other-app-temp", removed)

        self.assertFalse(stale_clip.exists())
        self.assertFalse(stale_shot.exists())
        self.assertTrue(fresh_clip.exists())
        self.assertTrue(other_dir.exists())
