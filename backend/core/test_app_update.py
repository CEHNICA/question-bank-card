"""官网更新：只认 tiyouju.com，安装包必须对上 SHA-256。"""

import hashlib
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from django.test import SimpleTestCase, override_settings

from core import app_update
from core.version import APP_VERSION


class _Body:
    def __init__(self, payload: bytes, url: str, status: int = 200, headers: dict | None = None):
        self.payload = payload
        self.url = url
        self.status = status
        self.headers = headers or {}
        self.offset = 0

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _response_for(url: str, latest: str, installer: bytes, checksum: str, final_url: str | None = None):
    path = urlparse(url).path
    final = final_url or url
    if path == "/api/latest-release":
        body = json.dumps(
            {
                "version": latest,
                "published_at": "2026-11-26",
                "release_url": "https://github.com/CEHNICA/question-bank-card/releases/tag/v9.9.9",
                "notes": "## 更新亮点\n1. 新增右下角悬浮更新弹窗。",
                "highlights": ["新增右下角悬浮更新弹窗", "自动接入国内 AI Agent", 123, "  "],
            }
        ).encode()
    elif path == "/download/SHA256SUMS.txt":
        body = f"{checksum} *TiYouJu-Setup-{latest}.exe\n".encode()
    elif path == "/download/installer":
        body = installer
    else:
        raise AssertionError(path)
    return _Body(body, final)


class OfficialUpdateTests(SimpleTestCase):
    def setUp(self):
        app_update._CANCEL_DOWNLOAD.clear()
        app_update._READY_INSTALLER = None
        app_update._reset_progress(status="idle")
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.database = self.root / "db.sqlite3"
        self.database.write_bytes(b"sqlite-placeholder")
        self.settings = override_settings(
            DATA_ROOT=self.root / "data",
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(self.database)}},
        )
        self.settings.enable()

    def tearDown(self):
        self.settings.disable()
        self.tmp.cleanup()

    def test_a_newer_official_version_is_offered(self):
        installer = b"installer-bytes"
        digest = hashlib.sha256(installer).hexdigest()

        def urlopen(request, timeout):
            return _response_for(request.full_url, "9.9.9", installer, digest)

        with patch.object(app_update.urllib.request, "urlopen", side_effect=urlopen):
            info = app_update.describe_latest()
        self.assertEqual(info["current"], APP_VERSION)
        self.assertTrue(info["update_available"])
        self.assertEqual(info["latest"], "9.9.9")
        self.assertTrue(info["auto_check"])
        self.assertEqual(info["highlights"], ["新增右下角悬浮更新弹窗", "自动接入国内 AI Agent"])
        self.assertIn("悬浮更新弹窗", info["notes"])
        self.assertEqual(info["release_url"], "https://github.com/CEHNICA/question-bank-card/releases/tag/v9.9.9")
        self.assertEqual(app_update._safe_release_url("https://evil.example/payload"), "https://tiyouju.com/")

    def test_auto_check_can_be_turned_off_and_skips_the_network(self):
        app_update.set_auto_check(False)
        self.assertFalse(app_update.auto_check_enabled())
        request = self.client.post("/api/update/check?auto=1", HTTP_X_QB_REQUEST="1")
        self.assertEqual(request.status_code, 200)
        self.assertTrue(request.json()["skipped"])

    def test_install_rejects_a_bad_checksum_and_accepts_the_official_file(self):
        installer = b"official-installer"
        digest = hashlib.sha256(installer).hexdigest()
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return _response_for(request.full_url, "9.9.9", installer, "0" * 64)

        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root), "TMP": str(self.root)}),
            patch.object(app_update.urllib.request, "urlopen", side_effect=urlopen),
            patch.object(app_update.subprocess, "Popen") as launched,
        ):
            with self.assertRaises(app_update.UpdateError):
                app_update.install_latest()
        launched.assert_not_called()

        def good_open(request, timeout):
            return _response_for(request.full_url, "9.9.9", installer, digest)

        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root), "TMP": str(self.root)}),
            patch.object(app_update.urllib.request, "urlopen", side_effect=good_open),
            patch.object(app_update.subprocess, "Popen") as launched,
            patch.object(app_update.os, "name", "nt"),
        ):
            result = app_update.install_latest()
        self.assertEqual(result["version"], "9.9.9")
        launched.assert_called_once()
        self.assertTrue(any(self.root.joinpath("backups").glob("before-update-*.sqlite3")))

    def test_a_redirect_away_from_the_official_site_is_refused(self):
        def urlopen(request, timeout):
            return _response_for(
                request.full_url, "9.9.9", b"x", "ab" * 32, final_url="https://example.invalid/setup.exe"
            )

        with patch.object(app_update.urllib.request, "urlopen", side_effect=urlopen):
            with self.assertRaises(app_update.UpdateError) as caught:
                app_update.describe_latest()
        self.assertIn("tiyouju.com", str(caught.exception))

    def test_parallel_range_download_and_async_progress_endpoints(self):
        installer = bytes((i % 251) for i in range(150_000))
        digest = hashlib.sha256(installer).hexdigest()
        seen_ranges: list[str] = []

        def ranged_open(request, timeout):
            path = urlparse(request.full_url).path
            if path != "/download/installer":
                return _response_for(request.full_url, "9.9.9", installer, digest)
            range_hdr = request.headers.get("Range") or ""
            seen_ranges.append(range_hdr)
            match = re.match(r"^bytes=(\d+)-(\d+)$", range_hdr)
            self.assertIsNotNone(match)
            start = int(match.group(1))
            end = min(len(installer) - 1, int(match.group(2)))
            slice_bytes = installer[start : end + 1]
            return _Body(
                slice_bytes,
                request.full_url,
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{len(installer)}",
                    "Content-Length": str(len(slice_bytes)),
                },
            )

        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root), "TMP": str(self.root)}),
            patch.object(app_update, "_DOWNLOAD_CHUNK_BYTES", 32_768),
            patch.object(app_update, "_READ_BLOCK_BYTES", 8_192),
            patch.object(app_update.urllib.request, "urlopen", side_effect=ranged_open),
            patch.object(app_update.subprocess, "Popen") as launched,
            patch.object(app_update.os, "name", "nt"),
        ):
            start_resp = self.client.post("/api/update/install/start", HTTP_X_QB_REQUEST="1")
            self.assertEqual(start_resp.status_code, 200)
            self.assertTrue(start_resp.json()["ok"])
            progress = {}
            for _ in range(100):
                p_resp = self.client.get("/api/update/progress", HTTP_X_QB_REQUEST="1")
                self.assertEqual(p_resp.status_code, 200)
                progress = p_resp.json()
                if not progress["active"] and progress["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(0.02)

        self.assertEqual(progress["status"], "succeeded", progress)
        self.assertEqual(progress["percent"], 100.0)
        self.assertEqual(progress["bytes_downloaded"], len(installer))
        self.assertEqual(progress["bytes_total"], len(installer))
        self.assertGreater(len(seen_ranges), 1)
        launched.assert_called_once()
        written_exe = self.root / "TiYouJu-Setup-9.9.9.exe"
        self.assertEqual(written_exe.read_bytes(), installer)

    def test_cached_verified_installer_skips_redownload_and_launches_visible_autostart(self):
        installer = b"cached-official-installer-9.9.9"
        digest = hashlib.sha256(installer).hexdigest()
        cached_file = self.root / "TiYouJu-Setup-9.9.9.exe"
        cached_file.write_bytes(installer)
        fetched_urls: list[str] = []

        def fake_open(request, timeout):
            fetched_urls.append(request.full_url)
            if urlparse(request.full_url).path == "/download/installer":
                raise AssertionError("Should not re-download installer when cached SHA-256 matches")
            return _response_for(request.full_url, "9.9.9", installer, digest)

        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root), "TMP": str(self.root)}),
            patch.object(app_update.urllib.request, "urlopen", side_effect=fake_open),
            patch.object(app_update.subprocess, "Popen") as launched,
            patch.object(app_update.os, "name", "nt"),
        ):
            result = app_update.install_latest()

        self.assertEqual(result["version"], "9.9.9")
        self.assertNotIn(app_update.INSTALLER_URL, fetched_urls)
        launched.assert_called_once()
        cmd = launched.call_args.args[0]
        self.assertIn("/SILENT", cmd)
        self.assertIn("/AUTOSTART=1", cmd)
        self.assertNotIn("/VERYSILENT", cmd)

    def test_breakaway_denied_keeps_app_open_and_reports_manual_installer(self):
        installer = b"cached-official-installer-9.9.9"
        digest = hashlib.sha256(installer).hexdigest()
        cached_file = self.root / "TiYouJu-Setup-9.9.9.exe"
        cached_file.write_bytes(installer)

        def fake_open(request, timeout):
            return _response_for(request.full_url, "9.9.9", installer, digest)

        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root), "TMP": str(self.root)}),
            patch.object(app_update.urllib.request, "urlopen", side_effect=fake_open),
            patch.object(app_update.subprocess, "Popen", side_effect=PermissionError("breakaway denied")) as launched,
            patch.object(app_update.os, "name", "nt"),
        ):
            with self.assertRaisesMessage(app_update.UpdateError, str(cached_file)):
                app_update.install_latest()

        launched.assert_called_once()  # 不能回退到仍属于旧 Job 的进程。
        self.assertTrue(cached_file.is_file())
        progress = app_update.get_install_progress()
        self.assertEqual(progress["status"], "failed")
        self.assertIn("软件将保持打开", progress["error"])
        self.assertIn("手动运行", progress["error"])

    def wait_for_update(self):
        for _ in range(250):
            if not app_update._INSTALL_LOCK.locked():
                return app_update.get_install_progress()
            time.sleep(0.01)
        self.fail("更新任务未结束")

    def test_download_waits_for_explicit_confirmation_and_rechecks_file(self):
        installer = b"download-without-install"
        digest = hashlib.sha256(installer).hexdigest()
        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root)}),
            patch.object(
                app_update.urllib.request,
                "urlopen",
                side_effect=lambda request, timeout: _response_for(request.full_url, "9.9.9", installer, digest),
            ),
            patch.object(app_update, "_launch_detached_installer") as launch,
            patch.object(app_update, "_backup_database") as backup,
        ):
            self.client.post("/api/update/download/start", HTTP_X_QB_REQUEST="1")
            progress = self.wait_for_update()
            self.assertEqual(progress["status"], "ready")
            self.assertEqual(progress["percent"], 100)
            self.assertTrue(progress["can_install"])
            launch.assert_not_called()
            backup.assert_not_called()
            self.assertEqual(self.client.post("/api/update/install/confirm", HTTP_X_QB_REQUEST="1").status_code, 400)
            # 下载后被改写的文件不可安装，即使原来的校验通过了。
            (self.root / "TiYouJu-Setup-9.9.9.exe").write_bytes(b"tampered")
            result = self.client.post(
                "/api/update/install/confirm",
                data=json.dumps({"confirmed": True}),
                content_type="application/json",
                HTTP_X_QB_REQUEST="1",
            )
            self.assertEqual(result.status_code, 200)
            progress = self.wait_for_update()
            self.assertEqual(progress["status"], "failed")
            self.assertFalse(progress["can_install"])
            launch.assert_not_called()
            backup.assert_not_called()

    def test_confirmed_install_uses_verified_download_without_network(self):
        installer = b"ready-for-confirmation"
        digest = hashlib.sha256(installer).hexdigest()
        destination = self.root / "ready.exe"
        destination.write_bytes(installer)
        app_update._READY_INSTALLER = (destination, digest, "9.9.9")
        with (
            patch.object(app_update, "_launch_detached_installer") as launch,
            patch.object(app_update, "_backup_database") as backup,
            patch.object(app_update.urllib.request, "urlopen") as network,
            patch.object(app_update.os, "name", "nt"),
        ):
            result = self.client.post(
                "/api/update/install/confirm",
                data=json.dumps({"confirmed": True}),
                content_type="application/json",
                HTTP_X_QB_REQUEST="1",
            )
            self.assertEqual(result.status_code, 200)
            progress = self.wait_for_update()
        self.assertEqual(progress["status"], "succeeded")
        backup.assert_called_once()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0][0], str(destination))
        network.assert_not_called()

    def test_cancel_download_cleans_partial_file_and_prevents_duplicate_worker(self):
        self.cancel_download_case(ranged=False)

    def test_cancel_parallel_ranges_stops_all_workers(self):
        self.cancel_download_case(ranged=True)

    def cancel_download_case(self, *, ranged):
        entered = threading.Event()
        release = threading.Event()
        installer = b"cancel-this-download" * 10_000
        digest = hashlib.sha256(installer).hexdigest()
        reads = []

        class BlockingBody(_Body):
            def read(body_self, size=-1):
                entered.set()
                if not release.wait(3):
                    raise TimeoutError("test response was not released")
                reads.append(True)
                return super().read(size)

        def fake_open(request, timeout):
            if urlparse(request.full_url).path != "/download/installer":
                return _response_for(request.full_url, "9.9.9", installer, digest)
            if ranged:
                match = re.match(r"bytes=(\d+)-(\d+)", request.headers["Range"])
                start, end = int(match[1]), min(int(match[2]), len(installer) - 1)
                return BlockingBody(
                    installer[start : end + 1],
                    request.full_url,
                    status=206,
                    headers={"Content-Range": f"bytes {start}-{end}/{len(installer)}"},
                )
            return BlockingBody(installer, request.full_url)

        with (
            patch.dict(app_update.os.environ, {"TEMP": str(self.root)}),
            patch.object(app_update.urllib.request, "urlopen", side_effect=fake_open),
            patch.object(app_update, "_DOWNLOAD_CHUNK_BYTES", 32_768),
            patch.object(app_update, "_launch_detached_installer") as launch,
            patch.object(app_update, "_backup_database") as backup,
        ):
            try:
                self.client.post("/api/update/download/start", HTTP_X_QB_REQUEST="1")
                self.assertTrue(entered.wait(2))
                again = self.client.post("/api/update/download/start", HTTP_X_QB_REQUEST="1").json()
                self.assertTrue(again["already_running"])
                cancelled = self.client.post("/api/update/download/cancel", HTTP_X_QB_REQUEST="1").json()
                self.assertEqual(cancelled["status"], "cancelling")
                self.assertTrue(cancelled["active"])
            finally:
                release.set()
                progress = self.wait_for_update()
            self.assertEqual(progress["status"], "cancelled")
            self.assertFalse(progress["can_install"])
            self.assertEqual(list(self.root.glob("*.part")), [])
            self.assertFalse((self.root / "TiYouJu-Setup-9.9.9.exe").exists())
            launch.assert_not_called()
            backup.assert_not_called()

    def test_new_update_routes_require_local_page_and_confirmation(self):
        for route in ("download/start", "download/cancel", "install/confirm"):
            self.assertEqual(self.client.get("/api/update/" + route).status_code, 405)
            self.assertEqual(self.client.post("/api/update/" + route).status_code, 403)
            self.assertEqual(
                self.client.post("/api/update/" + route, HTTP_X_QB_REQUEST="1", REMOTE_ADDR="192.0.2.1").status_code,
                403,
            )
        self.assertEqual(
            self.client.post(
                "/api/update/install/confirm",
                data='{"confirmed":true}',
                content_type="application/json",
                HTTP_X_QB_REQUEST="1",
            ).status_code,
            400,
        )
        self.assertEqual(self.client.get("/qb-update.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
