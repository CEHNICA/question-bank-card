"""页面自绘的窗口按钮：最小化与关闭。

宿主窗口去掉系统标题栏之后，这两个按钮是唯一的窗口控制入口，所以这条通道必须
真的能用：写出来的动作和 nonce 启动器读得懂，外来的请求拿不到。
"""

import json
import tempfile
import unittest
from pathlib import Path

from django.test import Client, TestCase, override_settings

from . import window_control


class WindowControlTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = override_settings(DATA_ROOT=self.root)
        state.enable()
        self.addCleanup(state.disable)
        self.client = Client()

    def request(self, action, **extra):
        return self.client.post(f"/api/window/{action}", **extra)

    def test_minimize_and_close_write_a_readable_control_file(self):
        for action in ("minimize", "close"):
            response = self.request(action, HTTP_X_QB_REQUEST="1")
            self.assertEqual(response.status_code, 200, response.content)
            payload = json.loads(window_control.control_path().read_text(encoding="utf-8"))
            self.assertEqual(payload["action"], action)
            self.assertTrue(payload["nonce"])

    def test_each_request_carries_a_fresh_nonce_so_the_launcher_does_not_replay_it(self):
        seen = set()
        for _ in range(3):
            self.request("minimize", HTTP_X_QB_REQUEST="1")
            seen.add(json.loads(window_control.control_path().read_text(encoding="utf-8"))["nonce"])
        self.assertEqual(len(seen), 3, "nonce 必须每次都变，否则启动器会把上一次的动作重放")

    def test_requests_outside_the_page_are_refused(self):
        for action in ("minimize", "close"):
            # 缺自定义请求头：跨站请求会被浏览器默认拦下，这里也要自己挡一道。
            self.assertEqual(self.request(action).status_code, 403)
            self.assertFalse(window_control.control_path().exists())

    def test_remote_callers_cannot_drive_the_window(self):
        response = self.request("close", HTTP_X_QB_REQUEST="1", REMOTE_ADDR="203.0.113.7")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(window_control.control_path().exists())

    def test_unknown_actions_are_not_guessed_at(self):
        response = self.request("restart", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(window_control.control_path().exists())

    def test_a_get_does_not_write_anything(self):
        response = self.client.get("/api/window/close", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 405)
        self.assertFalse(window_control.control_path().exists())


if __name__ == "__main__":
    unittest.main()
