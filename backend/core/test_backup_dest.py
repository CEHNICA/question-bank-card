"""Test external backup destination and WAL mode."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from django.db import connection
from django.test import SimpleTestCase, TestCase

import backup_question_bank
import tiyouju_cli


class SqliteWalTuneTests(TestCase):
    def test_wal_mode_is_active(self):
        """确认数据库连接调优后已启用 WAL 模式或返回有效 journal 模式。"""
        if connection.vendor != "sqlite":
            return
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode")
            mode = cursor.fetchone()[0]
            # 在测试数据库中可能是 wal 或 memory
            self.assertIn(str(mode).lower(), ("wal", "memory"))


class BackupDestCliTests(SimpleTestCase):
    def test_cmd_backup_with_nonexistent_dest_raises(self):
        client = MagicMock()
        args = MagicMock()
        args.dest = "Z:\\nonexistent_folder_abc_123"
        with self.assertRaises(tiyouju_cli.CliError) as ctx:
            tiyouju_cli.cmd_backup(client, args)
        self.assertIn("不存在", str(ctx.exception))

    def test_cmd_backup_with_valid_dest_calls_export(self):
        client = MagicMock()
        args = MagicMock()
        with tempfile.TemporaryDirectory() as temp_dir:
            args.dest = temp_dir
            client.post.return_value = {"file": "题有据备份-test.zip", "size": 1024}
            res = tiyouju_cli.cmd_backup(client, args)
            client.post.assert_called_once_with("/api/backup/export", {"directory": str(Path(temp_dir).resolve())})
            self.assertEqual(res["file"], "题有据备份-test.zip")

    def test_cmd_backup_without_dest_raises(self):
        client = MagicMock()
        args = MagicMock()
        args.dest = None
        args.papers = None
        args.folders = None
        with self.assertRaises(tiyouju_cli.CliError) as ctx:
            tiyouju_cli.cmd_backup(client, args)
        self.assertIn("--dest", str(ctx.exception))
        client.post.assert_not_called()

    def test_cmd_backup_with_papers_sends_paper_ids(self):
        client = MagicMock()
        args = MagicMock()
        with tempfile.TemporaryDirectory() as temp_dir:
            args.dest = temp_dir
            args.papers = ["paper-uuid-1"]
            args.folders = None
            client.post.return_value = {"file": "题有据题包-test.zip", "size": 512}
            tiyouju_cli.cmd_backup(client, args)
            client.post.assert_called_once_with(
                "/api/backup/export", {"directory": str(Path(temp_dir).resolve()), "paper_ids": ["paper-uuid-1"]}
            )

    def test_cmd_backup_with_folders_sends_tag_ids(self):
        client = MagicMock()
        args = MagicMock()
        with tempfile.TemporaryDirectory() as temp_dir:
            args.dest = temp_dir
            args.papers = None
            args.folders = ["函数与导数"]
            client.post.return_value = {"file": "题有据题包-函数与导数.zip", "size": 512}
            tiyouju_cli.cmd_backup(client, args)
            client.post.assert_called_once_with(
                "/api/backup/export", {"directory": str(Path(temp_dir).resolve()), "tag_ids": ["函数与导数"]}
            )

    def test_cmd_backup_selective_requires_dest_and_single_mode(self):
        client = MagicMock()
        args = MagicMock()
        args.dest = None
        args.papers = ["paper-uuid-1"]
        args.folders = None
        with self.assertRaises(tiyouju_cli.CliError):
            tiyouju_cli.cmd_backup(client, args)

        with tempfile.TemporaryDirectory() as temp_dir:
            args.dest = temp_dir
            args.papers = ["paper-uuid-1"]
            args.folders = ["函数与导数"]
            with self.assertRaises(tiyouju_cli.CliError):
                tiyouju_cli.cmd_backup(client, args)
