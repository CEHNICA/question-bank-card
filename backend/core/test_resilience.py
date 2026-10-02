"""Regression tests for recoverable local/cloud failure paths."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .mineru import (
    ERROR_HINTS,
    REMOTE_ERROR_PHRASES,
    MineruError,
    _download_zip,
    _error_message,
    load_blocks,
    request_extract,
)


class _Response:
    ok = True
    status_code = 200

    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_content(self, chunk_size: int):
        del chunk_size
        yield from self.chunks


class _Session:
    def __init__(self, chunks: list[bytes]):
        self.response = _Response(chunks)

    def get(self, *_args, **_kwargs):
        return self.response


class _JsonResponse:
    ok = True
    status_code = 200

    def __init__(self, payload: dict | None = None):
        self.payload = payload or {}

    def json(self):
        return self.payload


class _ApiSession:
    def __init__(self, payloads: list[dict]):
        self.responses = iter(_JsonResponse(payload) for payload in payloads)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def request(self, *_args, **_kwargs):
        return next(self.responses)

    def put(self, *_args, **_kwargs):
        return _JsonResponse()


class MineruRecoveryTests(SimpleTestCase):
    def test_all_official_precise_api_error_codes_have_fixed_local_hints(self):
        official = {"A0202", "A0211", "-500", "-10001", "-10002"}
        official.update(f"-600{number:02d}" for number in range(1, 23))
        self.assertEqual(set(ERROR_HINTS), official)
        for code in sorted(official):
            with self.subTest(code=code):
                message = _error_message("解析", {
                    "code": code,
                    "err_msg": "Bearer TOP_SECRET https://signed.invalid/file?token=LEAK",
                })
                self.assertIn(f"错误码 {code}", message)
                self.assertIn(ERROR_HINTS[code], message)
                self.assertNotIn("TOP_SECRET", message)
                self.assertNotIn("signed.invalid", message)

    def test_documented_remote_phrases_are_classified_without_echoing_them(self):
        for code, phrases in REMOTE_ERROR_PHRASES:
            with self.subTest(code=code, phrase=phrases[0]):
                remote = f"{phrases[0]}; REMOTE_SECRET=https://signed.invalid/?token=LEAK"
                message = _error_message("解析", {"err_msg": remote})
                self.assertIn(ERROR_HINTS[code], message)
                self.assertNotIn("REMOTE_SECRET", message)
                self.assertNotIn("signed.invalid", message)

    def test_page_limit_err_msg_is_classified_without_echoing_remote_text(self):
        message = _error_message("解析", {
            "err_msg": "PDF page count exceeds the page limit; SECRET_REMOTE_TEXT=https://signed.invalid",
        })
        self.assertIn("200 页", message)
        self.assertIn("拆分", message)
        self.assertNotIn("SECRET_REMOTE_TEXT", message)
        self.assertNotIn("signed.invalid", message)

    def test_arbitrary_remote_error_text_is_never_echoed(self):
        message = _error_message("解析", {
            "err_msg": "signed_url=https://signed.invalid/file?token=SUPERSECRET; Bearer TOP_SECRET",
        })
        self.assertIn("检查 MinerU API 配置", message)
        self.assertNotIn("SUPERSECRET", message)
        self.assertNotIn("TOP_SECRET", message)
        self.assertNotIn("signed.invalid", message)

    def test_invalid_trace_id_is_never_exposed(self):
        message = _error_message("解析", {
            "err_msg": "unknown failure",
            "trace_id": "https://signed.invalid/?token=SUPERSECRET",
        })
        self.assertNotIn("signed.invalid", message)
        self.assertNotIn("SUPERSECRET", message)
        self.assertNotIn("追踪号", message)

    def test_known_error_code_takes_priority_over_err_msg_heuristics(self):
        message = _error_message("解析", {
            "code": -60009,
            "err_msg": "page parser rate limit exceeded",
        })
        self.assertIn("任务队列已满", message)
        self.assertNotIn("页数超限", message)

    def test_request_extract_uses_failed_task_err_msg_for_safe_page_limit_hint(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            source.write_bytes(b"test")
            paper = SimpleNamespace(
                pk="paper-id", source_path=str(source),
                pages=[{"page_idx": 0, "width": 1, "height": 1}],
            )
            session = _ApiSession([
                {"code": 0, "data": {
                    "batch_id": "batch-id", "file_urls": ["https://upload.invalid/source.pdf"],
                }},
                {"code": 0, "data": {"extract_result": [{
                    "state": "failed", "err_code": None,
                    "err_msg": "PDF has too many pages; DO_NOT_ECHO_THIS_REMOTE_TEXT",
                }]}},
            ])
            with patch("core.mineru.requests.Session", return_value=session), \
                    patch("core.mineru.Paper.objects.filter"):
                with self.assertRaises(MineruError) as raised:
                    request_extract(paper, "secret-token", source)
            message = str(raised.exception)
            self.assertIn("200 页", message)
            self.assertIn("拆分", message)
            self.assertNotIn("DO_NOT_ECHO_THIS_REMOTE_TEXT", message)

    def test_failed_batch_preserves_safe_trace_and_local_diagnostic_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            source.write_bytes(b"test")
            paper = SimpleNamespace(
                pk="paper-id", source_path=str(source),
                pages=[{"page_idx": 0, "width": 1, "height": 1}],
            )
            trace_id = "a" * 32
            session = _ApiSession([
                {"code": 0, "trace_id": "b" * 32, "data": {
                    "batch_id": "batch-id", "file_urls": ["https://upload.invalid/source.pdf"],
                }},
                {"code": 0, "trace_id": trace_id, "data": {"extract_result": [{
                    "state": "failed",
                    "err_msg": "file format not supported; signed_url=https://signed.invalid/?token=LEAK",
                }]}},
            ])
            with patch("core.mineru.requests.Session", return_value=session), \
                    patch("core.mineru.Paper.objects.filter"):
                with self.assertRaises(MineruError) as raised:
                    request_extract(paper, "secret-token", source)

            error = raised.exception
            message = str(error)
            self.assertEqual(error.code, "")
            self.assertEqual(error.category, "-60002")
            self.assertEqual(error.trace_id, trace_id)
            self.assertEqual(error.metadata["trace_id"], trace_id)
            self.assertRegex(error.diagnostic_id, r"^MU-[0-9A-F]{12}$")
            self.assertIn(error.diagnostic_id, message)
            self.assertIn(trace_id, message)
            self.assertIn("诊断分类 -60002", message)
            self.assertIn("文件格式识别失败", message)
            self.assertNotIn("signed.invalid", message)
            self.assertNotIn("LEAK", message)
            self.assertNotIn("batch-id", message)
            self.assertNotIn("secret-token", message)
            self.assertNotIn("signed.invalid", repr(error.metadata))

    def test_oversized_download_leaves_no_partial_or_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mineru_result.zip"
            with patch("core.mineru.MAX_ZIP_BYTES", 3), self.assertRaises(MineruError):
                _download_zip(_Session([b"four"]), "https://example.invalid/result.zip", target)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_name(target.name + ".part").exists())

    def test_invalid_download_does_not_replace_existing_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "mineru_result.zip"
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("old.txt", "valid")
            before = target.read_bytes()
            with self.assertRaises(MineruError):
                _download_zip(_Session([b"not a zip"]), "https://example.invalid/result.zip", target)
            self.assertEqual(target.read_bytes(), before)

    def test_corrupt_archive_has_actionable_error(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "mineru_result.zip"
            archive.write_bytes(b"broken")
            with self.assertRaisesRegex(MineruError, "已损坏"):
                load_blocks(archive, 1)


class _FlakyUploadSession(_ApiSession):
    """The first PUT stalls like the storage bucket did in a real run."""

    def __init__(self, payloads: list[dict], failures: int):
        super().__init__(payloads)
        self.failures = failures
        self.puts = 0

    def put(self, *_args, **kwargs):
        self.puts += 1
        body = kwargs.get("data")
        assert body is not None and body.read() == b"test", "every attempt must send the whole file"
        if self.puts <= self.failures:
            import requests
            raise requests.ReadTimeout("stalled")
        return _JsonResponse()


class MineruNetworkRetryTests(SimpleTestCase):
    def run_upload(self, failures: int):
        from .mineru import request_extract_file
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            source.write_bytes(b"test")
            session = _FlakyUploadSession([
                {"code": 0, "data": {"batch_id": "b", "file_urls": ["https://upload.invalid/x"]}},
                {"code": 0, "data": {"extract_result": [{"state": "failed", "err_msg": "parsing failed"}]}},
            ], failures)
            with patch("core.mineru.requests.Session", return_value=session), \
                    patch("core.mineru.time.sleep") as sleep:
                with self.assertRaises(MineruError) as raised:
                    request_extract_file("token", source, Path(directory) / "out.zip", 1)
            return session, sleep, str(raised.exception)

    def test_a_stalled_upload_is_retried_with_the_whole_file(self):
        session, sleep, message = self.run_upload(failures=1)
        self.assertEqual(session.puts, 2)
        self.assertNotIn("上传失败", message)   # it went on to the (mocked) parse result
        sleep.assert_any_call(2.0)

    def test_persistent_upload_failure_still_fails_with_a_safe_message(self):
        session, _sleep, message = self.run_upload(failures=5)
        self.assertEqual(session.puts, 3)
        self.assertIn("文件上传失败（ReadTimeout）", message)

    def test_download_retries_transient_errors_only(self):
        import requests

        class Broken:
            calls = 0

            def get(self, *_args, **_kwargs):
                Broken.calls += 1
                raise requests.ConnectionError("reset")

        with tempfile.TemporaryDirectory() as directory, patch("core.mineru.time.sleep"):
            with self.assertRaises(MineruError):
                _download_zip(Broken(), "https://example.invalid/r.zip", Path(directory) / "r.zip")
        self.assertEqual(Broken.calls, 3)
        with tempfile.TemporaryDirectory() as directory, patch("core.mineru.time.sleep") as sleep:
            with self.assertRaises(MineruError):
                _download_zip(_Session([b"not a zip"]), "https://example.invalid/r.zip", Path(directory) / "r.zip")
        sleep.assert_not_called()
