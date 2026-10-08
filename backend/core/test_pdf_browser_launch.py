"""Real browser ownership and PDF checks for Edge's compatibility relaunch.

Run on Windows with QB_EXPORT_BROWSER_TEST=1. Edge is required for this
regression; Chrome is also checked when installed. Every document, image and
profile is synthesized here, without a database or user credentials.
"""

import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from unittest import mock, skipUnless

from django.test import SimpleTestCase

from . import library_pdf as pdf


@skipUnless(os.name == "nt" and os.environ.get("QB_EXPORT_BROWSER_TEST") == "1",
            "Windows: set QB_EXPORT_BROWSER_TEST=1 for real browser ownership checks")
class PdfBrowserLaunchIntegrationTests(SimpleTestCase):
    def browser(self, name, *, required):
        relative = {"edge": "Microsoft/Edge/Application/msedge.exe",
                    "chrome": "Google/Chrome/Application/chrome.exe"}[name]
        for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            root = os.environ.get(key)
            if root:
                candidate = Path(root) / relative
                if candidate.is_file():
                    return candidate.resolve()
        if required:
            self.fail("Microsoft Edge is required: this regression must not silently use Chrome")
        self.skipTest("Google Chrome is not installed; required Edge tests still run")

    def cleanup_profile(self, profile):
        """Catch a relaunched child even if the original Popen exited already.

        This fallback only belongs to the test. Its random, independently owned
        profile protects ordinary browser windows and other exports on failure.
        """
        root = profile.parent.resolve()
        self.assertEqual(root.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(root.name.startswith("tiyouju-pdf-"))
        marker = str(profile).replace("'", "''")
        script = ("$marker='" + marker + "'; "
                  "Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
                  "Where-Object { $_.CommandLine -and $_.CommandLine.Contains($marker) } | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
        self.assertEqual(result.returncode, 0, "Cannot clean this test's independent browser profile")

    def check_lifecycle(self, executable):
        process = client = None
        with tempfile.TemporaryDirectory(prefix="tiyouju-pdf-browser-regression-") as directory:
            root = Path(directory).resolve()
            profile = root / "profile"
            try:
                # Reproduce the launcher environment on every Windows test host,
                # rather than relying on that host's own compatibility settings.
                with mock.patch.dict(os.environ, {"__COMPAT_LAYER": "DetectorsAppHealth"}):
                    process = pdf._launch_browser(executable, profile)
                deadline = time.monotonic() + 15
                port = pdf._wait_debug_port(profile, process, deadline)
                target = pdf._wait_page_target(pdf.build_opener(pdf.ProxyHandler({})), port, process, deadline)
                self.assertIsNone(process.poll(), "The owned browser exited while its child kept running")
                match = re.fullmatch(rf"ws://(?:127\.0\.0\.1|localhost):{port}(/devtools/page/[A-Za-z0-9_-]+)",
                                     target["webSocketDebuggerUrl"])
                self.assertIsNotNone(match)
                client = pdf._CDP(port, match[1], deadline)
                value = client.call("Runtime.evaluate", {"expression": "6 * 7", "returnByValue": True})
                self.assertEqual(value["result"]["value"], 42)
                self.assertIsNone(process.poll(), "CDP must belong to the process returned by launch")
                try:
                    client.call("Browser.close")
                except ConnectionError:
                    # Browser.close may close its transport before sending its reply.
                    pass
                self.assertEqual(process.wait(timeout=5), 0)
                client.close()
                client = None
            finally:
                pdf._cleanup_browser(client, process)
                self.cleanup_profile(profile)
            # Strict TemporaryDirectory cleanup must succeed, unlike the
            # production best-effort cleanup which preserves completed output.
        self.assertFalse(root.exists(), "The owned browser left its profile locked")

    def check_pdf(self, executable):
        from PIL import Image, ImageDraw
        import pymupdf

        image = Image.new("RGB", (200, 120), "white")
        drawing = ImageDraw.Draw(image)
        drawing.line((15, 105, 15, 15, 180, 105, 15, 105), fill=(0, 90, 180), width=4)
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        captured = [{"id": "00000000-0000-0000-0000-000000000001", "type": "free_response",
                     "content": {"stem": r"合成测试：求 $x^2+\frac{1}{3}$ 的值，并观察三角形配图。", "options": {}},
                     "selected": {}, "ai": False, "solution_images": [],
                     "images": [{"slot": "stem", "bytes": stream.getvalue(), "size": image.size}]}]
        document = pdf._html_document(captured, "浏览器导出回归", pdf._print_options({"student_info": False}))
        profiles = []
        original_launch = pdf._launch_browser

        def launch(binary, profile):
            profiles.append(profile)
            return original_launch(binary, profile)

        try:
            with mock.patch.dict(os.environ, {"__COMPAT_LAYER": "DetectorsAppHealth"}), \
                    mock.patch.object(pdf, "_browser_path", return_value=executable), \
                    mock.patch.object(pdf, "_launch_browser", side_effect=launch):
                data, page_count = pdf._render(document)
            self.assertEqual(page_count, 1)
            self.assertEqual(len(profiles), 1, "One export must render exactly once")
            with pymupdf.open(stream=data, filetype="pdf") as result:
                self.assertEqual(result.page_count, 1)
                page = result[0]
                text = page.get_text()
                self.assertIn("浏览器导出回归", text)
                self.assertIn("合成测试", text)
                self.assertIn("三角形配图", text)
                self.assertNotIn(r"\frac", text)
                self.assertIn("x", text)
                self.assertIn("3", text)
                self.assertTrue(any("katex" in font[3].lower() for font in page.get_fonts()),
                                "The formula must use real embedded KaTeX glyphs")
                images = page.get_images(full=True)
                self.assertEqual(len(images), 1)
                bitmap = pymupdf.Pixmap(result, images[0][0])
                self.assertEqual((bitmap.width, bitmap.height), image.size)
                rgb = pymupdf.Pixmap(pymupdf.csRGB, bitmap)
                coloured = sum(1 for red, green, blue in zip(rgb.samples[0::3], rgb.samples[1::3], rgb.samples[2::3])
                               if blue > red + 40)
                self.assertGreater(coloured, 100, "The exported image must retain its coloured triangle")
            self.assertTrue(all(not profile.parent.exists() for profile in profiles),
                            "Successful export must release and remove every temporary profile")
        finally:
            for profile in profiles:
                self.cleanup_profile(profile)
                # A failing mutant may have left a relaunched child holding the
                # directory during production cleanup. Remove only this verified
                # test-owned directory after terminating that child.
                if profile.parent.exists():
                    shutil.rmtree(profile.parent)

    def test_edge_keeps_owned_process_under_compatibility_layer_and_closes(self):
        self.check_lifecycle(self.browser("edge", required=True))

    def test_edge_exports_real_chinese_formula_and_picture_pdf_under_compatibility_layer(self):
        self.check_pdf(self.browser("edge", required=True))

    def test_chrome_keeps_owned_process_under_compatibility_layer_and_closes(self):
        self.check_lifecycle(self.browser("chrome", required=False))

    def test_chrome_exports_real_chinese_formula_and_picture_pdf_under_compatibility_layer(self):
        self.check_pdf(self.browser("chrome", required=False))
