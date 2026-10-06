"""PDF export boundaries; renderer tests use synthesized local documents only."""
from copy import deepcopy
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import re
import socket
import struct
import threading
import time
from types import SimpleNamespace
from unittest import mock
from urllib.error import URLError

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

    def test_page_count_must_match_the_paper_the_teacher_checked_on_screen(self):
        pub = self.publication()
        payload = self.payload([pub], output_format="pdf")
        payload["preview_page_count"] = 2
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 2)):
            response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["X-Page-Count"], "2")
        # 屏幕上核对的是 3 页，发下来的是 2 页：那份文件不是他看过的那份，不发。
        payload["preview_page_count"] = 3
        with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 2)):
            response = self.post(payload)
        self.assertEqual(response.status_code, 409)
        self.assertIn("重新预览", response.json()["error"])
        self.assertNotEqual(response.get("Content-Type"), "application/pdf")
        # 没带这个数的老客户端照旧放行，不能因为新字段把别人挡在门外。
        for absent in ({}, {"preview_page_count": None}):
            payload.pop("preview_page_count", None)
            payload.update(absent)
            with mock.patch.object(pdf, "_render", return_value=(COMPLETE, 2)):
                self.assertEqual(self.post(payload).status_code, 200, absent)
        # 说不清是几页的数一律拒，不能拿它去比。
        for bad in (0, -1, "2", 2.5, [2]):
            payload["preview_page_count"] = bad
            with mock.patch.object(pdf, "_render") as renderer:
                self.assertEqual(self.post(payload).status_code, 400, bad)
            renderer.assert_not_called()


class PdfBrowserStartupTests(SimpleTestCase):
    def wait_for_port(self, reads, *, polls=None, deadline=.2):
        clock = [0.0]
        process = mock.Mock()
        process.poll = mock.Mock(side_effect=polls) if polls is not None else mock.Mock(return_value=None)
        def sleep(seconds):
            clock[0] += seconds
        with mock.patch.object(Path, "read_text", side_effect=reads), \
                mock.patch.object(pdf.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(pdf.time, "sleep", side_effect=sleep):
            return pdf._wait_debug_port(Path("synthetic-profile"), process, deadline)

    def test_brief_windows_writer_lock_can_recover(self):
        self.assertEqual(self.wait_for_port([PermissionError(13, "writer lock"), "12345\n/devtools/browser/local-id"]), 12345)

    def test_missing_file_can_recover_without_an_exists_read_race(self):
        self.assertEqual(self.wait_for_port([FileNotFoundError(), "12345\n/devtools/browser/local-id"]), 12345)

    def test_empty_and_half_written_file_wait_for_completion(self):
        self.assertEqual(self.wait_for_port(["", "123", "12345\n", "12345\n/devtools/browser/local-id"]), 12345)

    def test_persistent_writer_lock_keeps_original_deadline(self):
        with self.assertRaisesMessage(pdf.word.ExportError, "超时") as caught:
            self.wait_for_port(PermissionError(13, "writer lock"), deadline=.1)
        self.assertEqual(caught.exception.status, 504)

    def test_browser_exit_while_waiting_reports_startup_failure(self):
        with self.assertRaisesMessage(pdf.word.ExportError, "未能启动") as caught:
            self.wait_for_port([FileNotFoundError()], polls=[None, 1])
        self.assertEqual(caught.exception.status, 503)

    def test_complete_invalid_port_and_other_io_errors_are_not_retried(self):
        for value in ("0", "65536", "not-a-port"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.wait_for_port([value + "\n/devtools/browser/local-id"])
        with self.assertRaises(OSError):
            self.wait_for_port([OSError(5, "persistent device error")])


class PdfPageStartupTests(SimpleTestCase):
    target = {"type": "page", "url": "about:blank", "webSocketDebuggerUrl": "ws://127.0.0.1:12345/devtools/page/local-id"}

    def wait_for_target(self, responses, *, polls=None, deadline=.2):
        clock = [0.0]
        process = mock.Mock()
        process.poll = mock.Mock(side_effect=polls) if polls is not None else mock.Mock(return_value=None)
        opener = mock.Mock()
        def reply(value):
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.read.return_value = json.dumps(value).encode()
            return response
        opener.open.side_effect = [value if isinstance(value, BaseException) else reply(value) for value in responses]
        def sleep(seconds):
            clock[0] += seconds
        with mock.patch.object(pdf.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(pdf.time, "sleep", side_effect=sleep):
            return pdf._wait_page_target(opener, 12345, process, deadline)

    def test_real_local_listener_can_publish_page_after_initial_empty_catalog(self):
        requests = []
        target = self.target
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                requests.append(self.path)
                payload = json.dumps([] if len(requests) == 1 else [target]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            process = mock.Mock(); process.poll.return_value = None
            selected = pdf._wait_page_target(pdf.build_opener(pdf.ProxyHandler({})), server.server_port, process, time.monotonic() + 3)
            self.assertEqual(selected, target)
            self.assertEqual(requests, ["/json/list", "/json/list"])
        finally:
            server.shutdown(); server.server_close(); thread.join(2)

    def test_only_initial_connection_refusal_recovers_including_urllib_wrapper(self):
        for error in (ConnectionRefusedError(10061, "not listening yet"), URLError(ConnectionRefusedError(10061, "not listening yet"))):
            with self.subTest(error=type(error).__name__):
                self.assertEqual(self.wait_for_target([error, [self.target]]), self.target)
        with self.assertRaises(URLError):
            self.wait_for_target([URLError("different connection failure")])

    def test_catalog_without_initial_blank_page_keeps_original_deadline(self):
        with self.assertRaisesMessage(pdf.word.ExportError, "超时") as caught:
            self.wait_for_target([[], [{"type": "page", "url": "edge://newtab"}]], deadline=.1)
        self.assertEqual(caught.exception.status, 504)

    def test_browser_exit_between_catalog_queries_stops_waiting(self):
        with self.assertRaisesMessage(pdf.word.ExportError, "未能启动") as caught:
            self.wait_for_target([[]], polls=[None, 1])
        self.assertEqual(caught.exception.status, 503)

    def test_invalid_catalog_is_not_retried(self):
        for catalog in ({"not": "a list"}, ["not a target"]):
            with self.subTest(catalog=catalog), self.assertRaises(ValueError):
                self.wait_for_target([catalog])


class PdfCleanupTests(SimpleTestCase):
    secret_message = "SYNTHETIC_PRIVATE_DOCUMENT_MUST_NOT_APPEAR_IN_LOGS"
    faults = ("socket_close", "temporary_directory", "process_wait", "process_terminate", "process_final_wait")

    def render_with_cleanup_failure(self, fault, primary_status=None, *, log_fails=False):
        directory = mock.Mock()
        directory.name = str(Path(pdf.tempfile.gettempdir()) / "tiyouju-pdf-synthetic-cleanup")
        if fault == "temporary_directory":
            directory.cleanup.side_effect = PermissionError(13, self.secret_message)
        process = mock.Mock(pid=987654)
        exited = [False]
        process.poll.side_effect = lambda: 0 if exited[0] else None
        def wait(timeout):
            if timeout == 2 and fault in {"process_terminate", "process_final_wait"}:
                raise pdf.subprocess.TimeoutExpired("synthetic owned process", timeout)
            if timeout == 2 and fault == "process_wait":
                raise OSError(5, self.secret_message)
            if timeout == 5 and fault == "process_final_wait":
                raise pdf.subprocess.TimeoutExpired("synthetic owned process", timeout)
            exited[0] = True
            return 0
        process.wait.side_effect = wait
        client = mock.Mock(events=[])
        if fault == "socket_close":
            client.close.side_effect = OSError(10038, self.secret_message)
        def call(method, params=None):
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "owned-frame"}}}
            if method == "Runtime.evaluate":
                if primary_status == 504:
                    raise pdf.word.ExportError("synthetic primary deadline", 504)
                status = {"error": "synthetic primary layout"} if primary_status == 422 else {"ready": True, "page_count": 1}
                return {"result": {"value": status}}
            if method == "Page.printToPDF":
                return {"data": pdf.base64.b64encode(COMPLETE).decode()}
            return {}
        client.call.side_effect = call
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(pdf.tempfile, "TemporaryDirectory", return_value=directory))
            stack.enter_context(mock.patch.object(pdf, "os", SimpleNamespace(name="nt")))
            stack.enter_context(mock.patch.object(pdf.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True))
            stack.enter_context(mock.patch.object(pdf, "_browser_path", return_value=Path("synthetic-fixed-browser")))
            stack.enter_context(mock.patch.object(pdf, "_wait_debug_port", return_value=12345))
            stack.enter_context(mock.patch.object(pdf, "_wait_page_target", return_value=PdfPageStartupTests.target))
            stack.enter_context(mock.patch.object(pdf, "_CDP", return_value=client))
            stack.enter_context(mock.patch.object(pdf.subprocess, "Popen", return_value=process))
            terminator = stack.enter_context(mock.patch.object(pdf.subprocess, "run"))
            if fault == "process_terminate":
                terminator.side_effect = pdf.subprocess.TimeoutExpired("synthetic owned termination", 5)
            validate = stack.enter_context(mock.patch.object(pdf, "_validate_pdf"))
            if log_fails:
                logs = SimpleNamespace(output=[], records=[])
                logger_warning = stack.enter_context(mock.patch.object(pdf._logger, "warning", side_effect=OSError(5, self.secret_message)))
            else:
                logs = stack.enter_context(self.assertLogs(pdf._logger, level="WARNING"))
                logger_warning = None
            error = None
            try:
                result = pdf._render("synthetic document with no real questions")
            except pdf.word.ExportError as caught:
                error, result = caught, None
        return SimpleNamespace(result=result, error=error, client=client, process=process, directory=directory,
                               terminator=terminator, validate=validate, logs=logs, logger_warning=logger_warning)

    def test_cleanup_failures_preserve_already_validated_pdf_without_rerendering(self):
        for fault in self.faults:
            with self.subTest(fault=fault):
                outcome = self.render_with_cleanup_failure(fault)
                self.assertIsNone(outcome.error)
                self.assertEqual(outcome.result, (COMPLETE, 1))
                outcome.validate.assert_called_once_with(COMPLETE, 1)
                self.assertEqual(sum(call.args[0] == "Page.setDocumentContent" for call in outcome.client.call.call_args_list), 1)
                self.assertEqual(sum(call.args[0] == "Page.printToPDF" for call in outcome.client.call.call_args_list), 1)

    def test_cleanup_failures_preserve_primary_layout_and_deadline_errors(self):
        for status in (422, 504):
            for fault in self.faults:
                with self.subTest(status=status, fault=fault):
                    outcome = self.render_with_cleanup_failure(fault, status)
                    self.assertIsNone(outcome.result)
                    self.assertEqual(outcome.error.status, status)
                    self.assertIn("synthetic primary", str(outcome.error))
                    outcome.validate.assert_not_called()

    def test_failed_socket_close_still_waits_for_owned_process_and_cleans_directory(self):
        outcome = self.render_with_cleanup_failure("socket_close")
        outcome.client.close.assert_called_once()
        outcome.process.wait.assert_called_once_with(timeout=2)
        outcome.directory.cleanup.assert_called_once()

    def test_failed_termination_still_waits_and_only_targets_own_popen(self):
        outcome = self.render_with_cleanup_failure("process_terminate", 504)
        self.assertEqual(outcome.error.status, 504)
        outcome.terminator.assert_called_once_with(
            ["taskkill", "/PID", "987654", "/T", "/F"], stdout=pdf.subprocess.DEVNULL,
            stderr=pdf.subprocess.DEVNULL, shell=False, timeout=5, creationflags=0x08000000)
        self.assertEqual(outcome.process.wait.call_args_list, [mock.call(timeout=2), mock.call(timeout=5)])
        outcome.directory.cleanup.assert_called_once()
        process = mock.Mock(); process.poll.return_value = 0
        with mock.patch.object(pdf.subprocess, "run") as terminate:
            pdf._cleanup_browser(None, process)
        process.wait.assert_not_called()
        terminate.assert_not_called()

    def test_unfinished_cleanup_records_only_phase_and_exception_type(self):
        for fault in self.faults:
            with self.subTest(fault=fault):
                outcome = self.render_with_cleanup_failure(fault)
                self.assertTrue(any(f"stage={fault} " in line for line in outcome.logs.output))
                self.assertNotIn(self.secret_message, "\n".join(outcome.logs.output))
                for record in outcome.logs.records:
                    self.assertIsNone(record.exc_info)
                    self.assertEqual(len(record.args), 2)

    def test_logging_failure_keeps_pdf_and_primary_error_and_continues_cleanup(self):
        for primary in (None, 422):
            with self.subTest(primary=primary):
                outcome = self.render_with_cleanup_failure("socket_close", primary, log_fails=True)
                if primary is None:
                    self.assertIsNone(outcome.error)
                    self.assertEqual(outcome.result, (COMPLETE, 1))
                    outcome.validate.assert_called_once_with(COMPLETE, 1)
                else:
                    self.assertEqual(outcome.error.status, primary)
                    outcome.validate.assert_not_called()
                outcome.logger_warning.assert_called_once_with(
                    "PDF cleanup step failed: stage=%s error_type=%s", "socket_close", "OSError")
                outcome.process.wait.assert_called_once_with(timeout=2)
                outcome.directory.cleanup.assert_called_once()

    def test_expected_browser_shutdown_has_no_warning_and_still_closes_socket(self):
        for error in (ConnectionError("expected shutdown"), pdf.word.ExportError("expected deadline", 504)):
            with self.subTest(error=type(error).__name__):
                client, process = mock.Mock(), mock.Mock()
                client.call.side_effect = error
                process.poll.return_value = None
                with self.assertNoLogs(pdf._logger, level="WARNING"):
                    pdf._cleanup_browser(client, process)
                client.close.assert_called_once()
                process.wait.assert_called_once_with(timeout=2)

    def test_handshake_socket_close_cannot_replace_primary_deadline(self):
        sock = mock.Mock()
        sock.close.side_effect = OSError(10038, self.secret_message)
        error = pdf.word.ExportError("synthetic primary deadline", 504)
        with mock.patch.object(pdf.socket, "create_connection", return_value=sock), \
                mock.patch.object(pdf, "_remaining", side_effect=[1, error]), \
                self.assertLogs(pdf._logger, level="WARNING") as logs, \
                self.assertRaises(pdf.word.ExportError) as caught:
            pdf._CDP(12345, "/devtools/page/local-id", time.monotonic() + 1)
        self.assertIs(caught.exception, error)
        sock.close.assert_called_once()
        self.assertIn("stage=handshake_socket_close error_type=OSError", logs.output[0])
        self.assertNotIn(self.secret_message, "\n".join(logs.output))


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
