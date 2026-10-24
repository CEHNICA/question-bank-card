"""1.26.17：本机自动备份。

每周打一个备份包放在软件自己的文件夹里，旧的只留最近十份；老师也能随时
"从这一份恢复"（走的是和上传备份包一样的合并流程：只增不减）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from . import library_transfer


class AutoBackupTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.tmp = Path(self.enterClassContext(__import__("tempfile").TemporaryDirectory()))
        self.root = self.tmp / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        patch = mock.patch.object(library_transfer, "data_root", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def _make_backup(self, stamp: int) -> Path:
        root = library_transfer.backups_root()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{library_transfer.AUTO_BACKUP_PREFIX}-202610{stamp:02d}-050000.zip"
        path.write_bytes(b"x" * 16)
        return path

    def test_backups_live_under_the_app_data_folder(self):
        self.assertEqual(library_transfer.backups_root(), self.root / "backups")

    def test_listing_is_newest_first(self):
        for day in (1, 2, 3):
            self._make_backup(day)
        items = library_transfer.list_backups()
        self.assertEqual(len(items), 3)
        self.assertTrue(items[0]["created_at"] >= items[-1]["created_at"])
        self.assertTrue(items[0]["name"].startswith(library_transfer.AUTO_BACKUP_PREFIX))

    def test_no_backup_means_it_is_due(self):
        self.assertTrue(library_transfer.backup_due())

    def test_a_recent_backup_means_not_due(self):
        self._make_backup(1)
        self.assertFalse(library_transfer.backup_due())

    def test_a_week_old_backup_is_due_again(self):
        path = self._make_backup(1)
        old = time.time() - 8 * 86400
        import os
        os.utime(path, (old, old))
        self.assertTrue(library_transfer.backup_due())

    def test_only_the_newest_ten_are_kept(self):
        root = library_transfer.backups_root()
        root.mkdir(parents=True, exist_ok=True)
        for index in range(12):
            path = root / f"{library_transfer.AUTO_BACKUP_PREFIX}-2026{index:04d}-050000.zip"
            path.write_bytes(b"x")
            import os
            os.utime(path, (1000000 + index, 1000000 + index))
        removed = library_transfer.prune_backups()
        self.assertEqual(len(removed), 2)
        self.assertEqual(len(library_transfer.list_backups()), 10)

    def test_a_backup_name_outside_the_folder_is_refused(self):
        with self.assertRaises(library_transfer.TransferError):
            library_transfer.backup_path("../../secrets.zip")
        with self.assertRaises(library_transfer.TransferError):
            library_transfer.backup_path("任意文件.zip")

    def test_backup_path_only_resolves_inside_the_backups_folder(self):
        path = self._make_backup(5)
        self.assertEqual(library_transfer.backup_path(path.name), path)

    def test_a_backup_package_never_contains_another_backup(self):
        """1.26.19：备份包绝不能把旧备份包打进去。

        真出过事：自动备份把上一次的包也打了进去，每打一次包就大一倍，
        最后写出 63 GB 把磁盘塞满，题库都打不开了。
        """
        root = library_transfer.backups_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / f"{library_transfer.AUTO_BACKUP_PREFIX}-20261020-050000.zip").write_bytes(b"old")
        bundle_files = list(library_transfer._bundle_files(self.root))
        assert not any(path.name.endswith(".zip") and "backups" in path.parts
                       for path in bundle_files), "备份包里不能有别的备份包"
        assert not any("imports" in path.parts for path in bundle_files), "合并记录也不进包"

    def test_a_full_disk_stops_the_backup(self):
        """磁盘不够的时候，宁可不备份，也不能把盘塞满。"""
        with mock.patch.object(library_transfer, "_free_bytes", return_value=1024):
            with self.assertRaises(library_transfer.TransferError) as caught:
                library_transfer.run_auto_backup()
        assert "磁盘" in str(caught.exception)

    def test_the_needed_size_covers_the_library(self):
        needed = library_transfer._backup_bytes_needed()
        assert needed >= 2 * 1024 * 1024 * 1024, "至少按 2 GB 算，别拿磁盘冒险"

    def test_a_failed_backup_leaves_nothing_behind(self):
        """1.26.20：备份写坏了不能留残骸。

        真出过事：备份写到一半软件被关掉，目录里留下一个 16.9 GB 的半个包 ——
        程序打不开、还白占着磁盘。写坏了当场删掉。
        """
        out = self.tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(library_transfer, "_manifest", side_effect=RuntimeError("断电了")):
            with self.assertRaises(RuntimeError):
                library_transfer.export_bundle(out)
        self.assertEqual(list(out.iterdir()), [], "写坏的地方不能留下半个包")

    def test_a_finished_backup_is_a_readable_package(self):
        out = self.tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        result = library_transfer.export_bundle(out)
        package = Path(result["file"])
        self.assertTrue(package.is_file())
        self.assertEqual([p.name for p in out.iterdir()], [package.name],
                         "临时文件要清干净，只留最终的包")
        with library_transfer.zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
        self.assertIn(library_transfer.MANIFEST_NAME, names)
        self.assertIn(library_transfer.DATABASE_NAME, names)

    def test_only_one_backup_runs_at_a_time(self):
        """界面点「现在就打」和后台每周那次不能撞在一起。"""
        out = self.tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        with library_transfer._backup_lock():
            with self.assertRaises(library_transfer.TransferError):
                library_transfer.export_bundle(out)


class AutoBackupApiTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def test_listing_is_local_only(self):
        response = self.client.get("/api/backup/auto", REMOTE_ADDR="192.0.2.10")
        self.assertEqual(response.status_code, 403)

    def test_listing_reports_the_backups(self):
        response = self.client.get("/api/backup/auto")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("backups", body)
        self.assertIn("due", body)
        self.assertEqual(body["keep"], library_transfer.BACKUP_KEEP)

    def test_running_one_now_is_refused_without_consent(self):
        response = self.client.post(
            "/api/backup/auto", data=json.dumps({}), content_type="application/json",
        )
        self.assertIn(response.status_code, {400, 403})

    def test_restoring_an_unknown_backup_says_so(self):
        response = self.client.post(
            "/api/backup/auto/import", data=json.dumps({"id": "题有据自动备份-19700101-000000.zip"}),
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("找不到", response.json()["error"])
