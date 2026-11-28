"""官网更新：只认 tiyouju.com，安装包必须对上 SHA-256。"""

import hashlib
import json
import re
import tempfile
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


if __name__ == "__main__":
    unittest.main()
