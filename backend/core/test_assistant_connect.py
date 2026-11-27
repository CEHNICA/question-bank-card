"""AI 助手本机接入状态、技能包导出与安全守卫测试。"""

from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings


class AssistantConnectApiTests(SimpleTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        override = override_settings(DATA_ROOT=self.root / "data")
        override.enable()
        self.addCleanup(override.disable)

    def test_connect_status_requires_localhost_and_qb_header(self):
        no_header = self.client.get("/api/assistant/connect")
        self.assertEqual(no_header.status_code, 403)

        remote = self.client.get("/api/assistant/connect", HTTP_X_QB_REQUEST="1", REMOTE_ADDR="192.168.1.99")
        self.assertEqual(remote.status_code, 403)

    def test_connect_status_and_setup_generate_skill_zip(self):
        home = self.root / "home"
        appdata = self.root / "appdata"
        (home / ".workbuddy").mkdir(parents=True)
        with (
            mock.patch.object(Path, "home", return_value=home),
            mock.patch.dict("os.environ", {"APPDATA": str(appdata)}, clear=False),
        ):
            res = self.client.get("/api/assistant/connect", HTTP_X_QB_REQUEST="1")
            self.assertEqual(res.status_code, 200)
            payload = res.json()
            self.assertIn("mcp_servers", payload)
            self.assertIn("mcp_json", payload)
            self.assertIn("tiyouju", payload["mcp_servers"]["mcpServers"])
            self.assertIn("workbuddy", payload["detected_agents"])
            self.assertGreaterEqual(len(payload["example_prompts"]), 3)

            setup_res = self.client.post("/api/assistant/connect/setup", HTTP_X_QB_REQUEST="1")
            self.assertEqual(setup_res.status_code, 200)
            setup_data = setup_res.json()
            self.assertTrue(setup_data["ok"])
            self.assertTrue(setup_data["connect"]["skill_zip_ready"])
            self.assertTrue(Path(setup_data["connect"]["skill_zip_path"]).is_file())
            self.assertTrue((home / ".workbuddy" / "mcp.json").is_file())

    def test_open_and_download_skill_zip(self):
        with mock.patch("core.assistant_connect.subprocess.Popen") as popen:
            open_res = self.client.post("/api/assistant/connect/open-skill-zip", HTTP_X_QB_REQUEST="1")
            self.assertEqual(open_res.status_code, 200)
            open_data = open_res.json()
            self.assertTrue(open_data["ok"])
            self.assertTrue(Path(open_data["path"]).is_file())
            if popen.called:
                self.assertIn("/select,", popen.call_args[0][0][1])

        dl_res = self.client.get("/api/assistant/connect/skill-zip")
        self.assertEqual(dl_res.status_code, 200)
        self.assertEqual(dl_res["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(dl_res.content), "r") as zf:
            self.assertIn("tiyouju/SKILL.md", zf.namelist())
