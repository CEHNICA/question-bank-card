"""Regression tests for recoverable local/cloud failure paths."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.test import SimpleTestCase

from .mineru import MineruError, _download_zip, load_blocks


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


class MineruRecoveryTests(SimpleTestCase):
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
