"""Regression tests for recoverable local/cloud failure paths."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .mineru import MineruError, _download_zip, _error_message, load_blocks, request_extract


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
            "err_msg": "signed_url=https://signed.invalid/file?token=SUPERSECRET",
        })
        self.assertIn("检查 MinerU API 配置", message)
        self.assertNotIn("SUPERSECRET", message)
        self.assertNotIn("signed.invalid", message)

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
