"""页面自绘的窗口按钮：最小化、最大化与关闭。

1.26.0 起这套按钮又回到了页面右上角：宿主窗口虽然有系统标题栏，但窗口拖到
屏幕边缘、最大化、整屏看题时，标题栏不在眼前 —— 老师就说"按钮没了，
怎么关、怎么缩"。页面这一份任何时候都在。

这条通道必须真的能用：写出来的动作和 nonce 启动器读得懂，外来的请求拿不到；
页面只在真的由桌面程序启动时才显示这组按钮。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_minimize_maximize_and_close_write_a_readable_control_file(self):
        for action in ("minimize", "maximize", "close"):
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
        for action in ("minimize", "maximize", "close"):
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

    def test_the_page_asks_whether_a_desktop_window_is_behind_it(self):
        """页面只在真的由桌面程序启动时才显示那三个按钮。"""
        with mock.patch.dict(os.environ, {"QB_DESKTOP_EXPORT": "1"}):
            response = self.client.get("/api/window/available")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(json.loads(response.content)["available"])
        with mock.patch.dict(os.environ, {"QB_DESKTOP_EXPORT": "0"}):
            response = self.client.get("/api/window/available")
            self.assertFalse(json.loads(response.content)["available"])
        self.assertFalse(window_control.control_path().exists(), "看一眼不该写控制文件")


if __name__ == "__main__":
    unittest.main()
