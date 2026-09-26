from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import backup_question_bank as backup


class BackupTests(unittest.TestCase):
    def test_backup_is_copied_and_hash_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "db.sqlite3"
            data = root / "data"
            backups = root / "backups"
            data.mkdir()
            (data / "sample.txt").write_text("题目", encoding="utf-8")
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT)")
                connection.execute("INSERT INTO sample(value) VALUES ('ok')")
                connection.commit()
            with patch.object(backup, "DATABASE", database), patch.object(backup, "DATA", data), \
                    patch.object(backup, "BACKUPS", backups), patch.object(backup, "_service_is_running", return_value=False):
                target = backup.create_backup()
                backup.verify(target)
                (target / "data" / "sample.txt").write_text("被改动", encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, "校验失败"):
                    backup.verify(target)

    def test_running_service_blocks_backup(self):
        with patch.object(backup, "_service_is_running", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "仍在运行"):
                backup.create_backup()


if __name__ == "__main__":
    unittest.main()
