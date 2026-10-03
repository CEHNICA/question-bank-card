"""PDF export boundaries; renderer tests use synthesized local documents only."""
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import socket
import struct
import threading
import time
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path

from . import library, library_pdf as pdf
from . import test_library_export as fixtures

urlpatterns = [path("api/library/export-pdf", pdf.export_pdf_view)]
COMPLETE = b"%PDF-1.7\nsynthetic offline test\n%%EOF\n"


@override_settings(ROOT_URLCONF=__name__)
class PdfExportTests(TestCase):
    setUp = fixtures.LibraryExportTests.setUp
    publication = fixtures.LibraryExportTests.publication
    payload = fixtures.LibraryExportTests.payload
    figure = fixtures.LibraryExportTests.figure

    def post(self, payload, **changes):
        return self.client.post("/api/library/export-pdf", json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1", **changes)

    def test_download_counts_filename_and_no_write(self):
        pub = self.publication()
        before = deepcopy(pub.content)
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 2)) as renderer:
            response = self.post(self.payload([pub], output_format="pdf", title="数学练习"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(response["X-Question-Count"], "1")
        self.assertEqual(response["X-Page-Count"], "2")
        self.assertEqual(response.content, COMPLETE)
        self.assertIn("filename*=UTF-8''", response["Content-Disposition"])
        self.assertIn("window.__qbPdfStatus", renderer.call_args.args[0])
        pub.refresh_from_db()
        self.assertEqual(pub.content, before)

    def rendered_input(self, renderer):
        document = renderer.call_args.args[0]
        return json.loads(re.search(r'<script[^>]+id="examData"[^>]*>(.*?)</script>', document, re.S)[1])

    def test_combined_without_selected_answers_renders_only_questions(self):
        blank = self.publication(stem="题干必须保留", answer=" \n ", analysis="\t ")
        optional_ai = self.publication(extras={"ai_answer": {"answer": "未选用的 AI 参考"}})
        payload = self.payload([blank, optional_ai], output_format="pdf", print_options={"document": "combined", "answers": True})
        before = deepcopy(payload)
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 1)) as renderer:
            response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content)
        document = self.rendered_input(renderer)
        self.assertEqual(document["options"]["document"], "questions")
        self.assertFalse(document["options"]["answers"])
        self.assertEqual(len(document["items"]), 2)
        self.assertEqual(document["items"][0]["content"]["stem"], "题干必须保留")
        self.assertTrue(all(not item["selected"] and not item["ai"] for item in document["items"]))
        self.assertEqual(payload, before)

    def test_combined_with_missing_answer_and_analysis_only_keeps_answer_appendix(self):
        missing = self.publication(stem="无答案题")
        explained = self.publication(stem="仅有解析的题", analysis="原卷解析必须保留")
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 2)) as renderer:
            response = self.post(self.payload([missing, explained], output_format="pdf", print_options={"document": "combined"}))
        self.assertEqual(response.status_code, 200)
        document = self.rendered_input(renderer)
        self.assertEqual(document["options"]["document"], "combined")
        self.assertTrue(document["options"]["answers"])
        self.assertEqual([item["content"]["stem"] for item in document["items"]], ["无答案题", "仅有解析的题"])
        self.assertEqual(document["items"][1]["selected"]["analysis"], "原卷解析必须保留")

    def test_selected_ai_reference_keeps_combined_appendix_and_explicit_answer_only_stays_answer_only(self):
        fixtures.features.save({"ai_answer": True})
        pub = self.publication(stem="题干", extras={"ai_answer": {"answer": "已选 AI 参考"}})
        for mode in ("combined", "answers"):
            with self.subTest(mode=mode):
                with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 1)) as renderer:
                    response = self.post(self.payload([pub], output_format="pdf", print_options={"document": mode, "ai_answers": True}))
                self.assertEqual(response.status_code, 200)
                document = self.rendered_input(renderer)
                self.assertEqual(document["options"]["document"], mode)
                self.assertEqual(document["items"][0]["selected"]["answer"], "已选 AI 参考")
                self.assertTrue(document["items"][0]["ai"])

    def test_client_cannot_supply_html_css_urls_or_flags(self):
        pub = self.publication()
        for field in ("html", "css", "layout_pages", "url", "browser", "flags"):
            payload = self.payload([pub], output_format="pdf")
            payload[field] = "https://example.invalid/private"
            with mock.patch.object(pdf, "_render") as renderer:
                response = self.post(payload)
            self.assertEqual(response.status_code, 400)
            renderer.assert_not_called()

    def test_exact_stored_text_required(self):
        pub = self.publication()
        payload = self.payload([pub], output_format="pdf")
        payload["rendered_fields"][str(pub.id)]["stem"]["source"] = "替换题目"
        with mock.patch.object(pdf, "_render") as renderer:
            response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        renderer.assert_not_called()

    def test_metadata_accepts_pdf_math_without_word_converter(self):
        latex = r"\cancel{x}"
        pub = self.publication(stem="$" + latex + "$")
        payload = self.payload([pub], output_format="pdf")
        payload["rendered_fields"][str(pub.id)]["stem"] = fixtures.math_field(latex, '<menclose notation="updiagonalstrike"><mi>x</mi></menclose>')
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 1)), mock.patch("core.library_export._convert_math_root", side_effect=AssertionError("Word converter reached")):
            response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content)

    def test_changed_publication_during_render_is_not_downloaded(self):
        pub = self.publication()
        def change(_):
            pub.status = "withdrawn"
            pub.save(update_fields=["status"])
            return COMPLETE, 1
        with mock.patch.object(pdf, "_render", side_effect=change):
            response = self.post(self.payload([pub], output_format="pdf"))
        self.assertEqual(response.status_code, 409)
        self.assertNotEqual(response.get("Content-Type"), "application/pdf")

    def test_images_come_from_snapshots_and_are_rechecked(self):
        pub = self.publication()
        image = self.figure(pub)
        def change(document):
            self.assertIn("data:image/png;base64,", document)
            self.assertNotIn('src="http', document)
            image.write_bytes(b"changed")
            return COMPLETE, 1
        with mock.patch.object(pdf, "_render", side_effect=change):
            response = self.post(self.payload([pub], output_format="pdf"))
        self.assertEqual(response.status_code, 409)

    def test_untrusted_text_stays_in_escaped_data(self):
        marker = '</script><script src="https://example.invalid/x">alert(1)</script>'
        pub = self.publication(stem=marker)
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 1)) as renderer:
            response = self.post(self.payload([pub], output_format="pdf", title='</title><img src="https://example.invalid/x">'))
        self.assertEqual(response.status_code, 200)
        document = renderer.call_args.args[0]
        self.assertNotIn(marker, document)
        self.assertIn(r"\u003c/script\u003e", document)
        self.assertIn("connect-src &#x27;none&#x27;", document)
        self.assertNotIn('<img src="https:', document)

    def test_request_guard_and_missing_answers(self):
        pub = self.publication()
        payload = self.payload([pub], output_format="pdf")
        with mock.patch.object(pdf, "_render") as renderer:
            self.assertEqual(self.post(payload, REMOTE_ADDR="203.0.113.1").status_code, 403)
            self.assertEqual(self.client.get("/api/library/export-pdf").status_code, 405)
            self.assertEqual(self.client.post("/api/library/export-pdf", json.dumps(payload), content_type="application/json").status_code, 403)
            payload["print_options"] = {"document": "answers"}
            self.assertEqual(self.post(payload).status_code, 400)
        renderer.assert_not_called()

    def test_failure_is_clear_json_not_fake_pdf(self):
        pub = self.publication()
        with mock.patch.object(pdf, "_render", side_effect=pdf.word.ExportError("本机浏览器缺失", 503)):
            response = self.post(self.payload([pub], output_format="pdf"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("浏览器缺失", response.json()["error"])


class PdfTransportTests(SimpleTestCase):
    def test_actual_pdf_pages_and_a4_are_checked(self):
        import pymupdf
        with pymupdf.open() as doc:
            doc.new_page(width=210 / 25.4 * 72, height=297 / 25.4 * 72)
            data = doc.tobytes()
        pdf._validate_pdf(data, 1)
        with self.assertRaises(pdf.word.ExportError):
            pdf._validate_pdf(data, 2)
        with pymupdf.open() as doc:
            doc.new_page(width=612, height=792)
            letter = doc.tobytes()
        with self.assertRaises(pdf.word.ExportError):
            pdf._validate_pdf(letter, 1)

    def test_timeout_and_concurrent_renderer_fail_before_launch(self):
        with self.assertRaises(pdf.word.ExportError):
            pdf._remaining(time.monotonic() - 1)
        pdf._render_lock.acquire()
        try:
            with mock.patch.object(pdf.subprocess, "Popen") as launch, self.assertRaises(pdf.word.ExportError):
                pdf._render("synthesized")
            launch.assert_not_called()
        finally:
            pdf._render_lock.release()

    def test_missing_browser_does_not_install_or_search_path(self):
        with mock.patch.dict(pdf.os.environ, {}, clear=True), self.assertRaisesMessage(pdf.word.ExportError, "Edge"):
            pdf._browser_path()

    def test_connection_path_is_owned_local_target(self):
        for path in ("https://example.invalid", "/devtools/page/x?query=1", "/devtools/page/../secret", "/not-cdp"):
            with mock.patch.object(pdf.socket, "create_connection") as connect, self.assertRaises(pdf.word.ExportError):
                pdf._CDP(12345, path, time.monotonic() + 5)
            connect.assert_not_called()

    def test_masked_request_and_fragmented_large_reply_with_ping(self):
        left, right = socket.socketpair()
        client = pdf._CDP.__new__(pdf._CDP)
        client.socket, client.buffer, client.serial, client.events = left, bytearray(), 0, []
        client.deadline = time.monotonic() + 5
        failures = []
        def read_exact(stream, count):
            data = bytearray()
            while len(data) < count:
                piece = stream.recv(count - len(data))
                if not piece: raise ConnectionError()
                data.extend(piece)
            return bytes(data)
        def frame(data, opcode, final=True):
            size = len(data)
            header = bytes([(128 if final else 0) | opcode, size if size < 126 else 126 if size < 65536 else 127])
            if size >= 126: header += struct.pack("!H" if size < 65536 else "!Q", size)
            return header + data
        def server():
            try:
                first, second = read_exact(right, 2)
                assert first == 129 and second & 128
                length = second & 127
                if length == 126: length = struct.unpack("!H", read_exact(right, 2))[0]
                elif length == 127: length = struct.unpack("!Q", read_exact(right, 8))[0]
                mask = read_exact(right, 4)
                data = read_exact(right, length)
                parsed = json.loads(bytes(c ^ mask[i % 4] for i, c in enumerate(data)))
                assert parsed["params"]["text"] == "a" * 70_000
                reply = json.dumps({"id": parsed["id"], "result": {"text": "b" * 70_000}}).encode()
                right.sendall(frame(reply[:65_540], 1, False) + frame(b"ping", 9) + frame(reply[65_540:], 0))
                first, second = read_exact(right, 2)
                assert first == 138 and second & 128
                mask = read_exact(right, 4); data = read_exact(right, second & 127)
                assert bytes(c ^ mask[i % 4] for i,c in enumerate(data)) == b"ping"
            except BaseException as error:
                failures.append(error)
            finally:
                right.close()
        worker = threading.Thread(target=server, daemon=True);worker.start()
        try:
            result = client.call("Synthetic.large", {"text": "a" * 70_000})
            self.assertEqual(result["text"], "b" * 70_000)
        finally:
            client.close();worker.join(5)
        self.assertFalse(failures, failures)
