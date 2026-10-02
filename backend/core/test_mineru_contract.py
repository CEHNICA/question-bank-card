"""Current precise API boundaries and malformed-response checks, fully offline."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from . import mineru, provider_catalog
from .test_resilience import _ApiSession


class MineruContractTests(SimpleTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source = self.folder / "source.pdf"
        self.source.write_bytes(b"offline fixture")
        self.target = self.folder / "result.zip"

    @staticmethod
    def submission(**changes):
        data = {"batch_id": "test-batch", "file_urls": ["https://upload.invalid/file?signature=PRIVATE_URL"]}
        data.update(changes)
        return {"code": 0, "trace_id": "a" * 32, "data": data}

    @staticmethod
    def done():
        return {"code": 0, "data": {"extract_result": [{
            "state": "done", "full_zip_url": "https://download.invalid/result.zip?signature=PRIVATE_URL",
        }]}}

    def session(self, answers):
        session = _ApiSession(answers)
        session.request = mock.Mock(wraps=session.request)
        session.put = mock.Mock(wraps=session.put)
        return session

    def extract(self, session, pages=1):
        with mock.patch.object(mineru.requests, "Session", return_value=session), \
                mock.patch.object(mineru, "_download_zip") as download:
            result = mineru.request_extract_file("PRIVATE_TOKEN", self.source, self.target, pages)
        return result, download

    def assert_safe(self, error):
        for private in ("PRIVATE_URL", "PRIVATE_TOKEN", "upload.invalid", "download.invalid"):
            self.assertNotIn(private, str(error))

    def test_valid_200_page_file_uses_v4_vlm_and_unsigned_put(self):
        session = self.session([self.submission(), self.done()])
        result, download = self.extract(session, pages=200)
        self.assertEqual(result, self.target)
        calls = session.request.call_args_list
        self.assertEqual([call.args[0] for call in calls], ["POST", "GET"])
        self.assertEqual(calls[0].args[1], "https://mineru.net/api/v4/file-urls/batch")
        self.assertEqual(calls[0].kwargs["json"], {"files": [{"name": "source.pdf"}], "model_version": "vlm"})
        self.assertEqual(calls[0].kwargs["headers"]["Authorization"], "Bearer PRIVATE_TOKEN")
        session.put.assert_called_once()
        self.assertNotIn("headers", session.put.call_args.kwargs)
        download.assert_called_once()

    def test_201_pages_and_invalid_page_counts_are_rejected_before_any_request(self):
        for pages in (201, 0, -1, True, "200"):
            with self.subTest(pages=pages), mock.patch.object(mineru.requests, "Session") as session:
                with self.assertRaises(mineru.MineruError) as caught:
                    mineru.request_extract_file("PRIVATE_TOKEN", self.source, self.target, pages)
                session.assert_not_called()
                self.assert_safe(caught.exception)
                if pages == 201:
                    self.assertIn("200 页", str(caught.exception))

    def test_empty_or_oversized_files_are_rejected_before_submission(self):
        self.source.write_bytes(b"")
        with mock.patch.object(mineru.requests, "Session") as session, self.assertRaisesRegex(mineru.MineruError, "为空"):
            mineru.request_extract_file("PRIVATE_TOKEN", self.source, self.target, 1)
        session.assert_not_called()
        self.source.write_bytes(b"four")
        with mock.patch.object(mineru, "MAX_SOURCE_BYTES", 3), \
                mock.patch.object(mineru.requests, "Session") as session, \
                self.assertRaisesRegex(mineru.MineruError, "200 MB"):
            mineru.request_extract_file("PRIVATE_TOKEN", self.source, self.target, 1)
        session.assert_not_called()

    def test_malformed_batch_id_stops_after_one_submission_without_uploading(self):
        for batch in (None, "", "  ", 1, False, [], {}, "bad\nPRIVATE_TOKEN"):
            with self.subTest(batch=batch):
                session = self.session([self.submission(batch_id=batch)])
                with self.assertRaisesRegex(mineru.MineruError, "批次编号格式不正确") as caught:
                    self.extract(session)
                self.assert_safe(caught.exception)
                self.assertEqual(caught.exception.stage, "申请上传地址")
                self.assertEqual(caught.exception.trace_id, "a" * 32)
                session.request.assert_called_once()
                session.put.assert_not_called()

    def test_malformed_upload_list_stops_without_uploading_or_resubmitting(self):
        for urls in (None, "https://upload.invalid/PRIVATE_URL", {}, [], ["https://a.invalid", "https://b.invalid"]):
            with self.subTest(urls=urls):
                session = self.session([self.submission(file_urls=urls)])
                with self.assertRaisesRegex(mineru.MineruError, "上传地址列表") as caught:
                    self.extract(session)
                self.assert_safe(caught.exception)
                session.request.assert_called_once()
                session.put.assert_not_called()

    def test_upload_url_requires_https_and_a_host_without_echoing_signed_values(self):
        for url in (None, 12, {}, "http://upload.invalid/PRIVATE_URL", "https://", "https:///PRIVATE_URL",
                    "https://user:PRIVATE_TOKEN@upload.invalid/file", "https://upload.invalid:bad/PRIVATE_URL",
                    "https://upload.invalid/file\nPRIVATE_URL"):
            with self.subTest(url=url):
                session = self.session([self.submission(file_urls=[url])])
                with self.assertRaisesRegex(mineru.MineruError, "HTTPS 地址") as caught:
                    self.extract(session)
                self.assert_safe(caught.exception)
                session.request.assert_called_once()
                session.put.assert_not_called()

    def test_malformed_result_list_has_a_safe_actionable_error_without_new_submission(self):
        for rows in (None, {}, "PRIVATE_URL", [None], [[]], [{"state": "pending"}, {"state": "done"}]):
            with self.subTest(rows=rows):
                session = self.session([self.submission(), {"code": 0, "trace_id": "b" * 32,
                                                          "data": {"extract_result": rows}}])
                with self.assertRaisesRegex(mineru.MineruError, "任务列表格式不正确") as caught:
                    self.extract(session)
                self.assert_safe(caught.exception)
                self.assertEqual(caught.exception.stage, "查询解析结果")
                self.assertEqual(caught.exception.trace_id, "b" * 32)
                self.assertEqual([call.args[0] for call in session.request.call_args_list], ["POST", "GET"])

    def test_missing_or_non_string_state_is_not_treated_as_a_long_running_task(self):
        for state in (None, "", "  ", {}, 1, False):
            with self.subTest(state=state):
                session = self.session([self.submission(), {"code": 0, "data": {
                    "extract_result": [{"state": state}],
                }}])
                with self.assertRaisesRegex(mineru.MineruError, "有效的任务状态"):
                    self.extract(session)
                self.assertEqual(session.request.call_count, 2)

    def test_empty_result_list_can_wait_for_the_same_batch_then_complete(self):
        session = self.session([self.submission(), {"code": 0, "data": {"extract_result": []}}, self.done()])
        with mock.patch.object(mineru.time, "sleep"):
            result, download = self.extract(session)
        self.assertEqual(result, self.target)
        self.assertEqual([call.args[0] for call in session.request.call_args_list], ["POST", "GET", "GET"])
        self.assertEqual(session.request.call_args_list[1].args[1], session.request.call_args_list[2].args[1])
        download.assert_called_once()

    def test_completed_task_requires_a_valid_https_download_url_before_download(self):
        for url in (None, {}, "http://download.invalid/PRIVATE_URL", "https://"):
            with self.subTest(url=url):
                response = self.done()
                response["data"]["extract_result"][0]["full_zip_url"] = url
                session = self.session([self.submission(), response])
                with mock.patch.object(mineru, "_download_zip") as download, \
                        self.assertRaisesRegex(mineru.MineruError, "HTTPS 地址") as caught:
                    self.extract(session)
                self.assert_safe(caught.exception)
                self.assertEqual(caught.exception.stage, "解析包下载")
                download.assert_not_called()

    def test_invalid_success_code_or_data_never_reaches_put(self):
        for code, data in ((False, {}), (0.0, {}), ("0", {}), (0, []), (0, None)):
            with self.subTest(code=code, data=data):
                session = self.session([{"code": code, "data": data}])
                with self.assertRaises(mineru.MineruError) as caught:
                    self.extract(session)
                self.assert_safe(caught.exception)
                session.request.assert_called_once()
                session.put.assert_not_called()

    def test_token_expiry_and_priority_quota_text_do_not_invent_fixed_limits(self):
        hint = mineru.ERROR_HINTS["A0211"]
        self.assertIn("已过期", hint)
        self.assertIn("管理页", hint)
        self.assertNotIn("14", hint)
        note = provider_catalog.MINERU["note"]
        self.assertIn("1000 页最高优先级", note)
        self.assertIn("降低优先级", note)
        self.assertNotIn("14", note)
        self.assertIn("任务数量", mineru.ERROR_HINTS["-60018"])
