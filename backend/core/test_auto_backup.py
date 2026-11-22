"""备份包通用安全不变量与自动备份下线验证。

自动备份（后台定时打备份与界面开关）已下线，仅保留手动备份与恢复：
- 备份包绝不能把 backups / imports 打进去；
- 写坏的备份包当场清理，不留残骸；
- 同一时刻只准一份备份在写；
- 旧 features.json 里的 auto_backup 不再生效，/api/backup/auto 路由已下线。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from . import features, library_transfer


class BackupSafetyAndRetiredAutoBackupTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()
        self.tmp = Path(self.enterClassContext(__import__("tempfile").TemporaryDirectory()))
        self.root = self.tmp / "data"
        self.root.mkdir(parents=True, exist_ok=True)
        patch = mock.patch.object(library_transfer, "data_root", return_value=self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def test_auto_backup_switch_is_retired_even_if_saved_true(self):
        with mock.patch.dict(os.environ, {"QB_FEATURES_FILE": str(self.root / "features.json")}):
            features.path().write_text('{"auto_backup": true, "origin_split": false}', encoding="utf-8")
            self.assertNotIn("auto_backup", features.FEATURES)
            self.assertFalse(features.enabled("auto_backup"))
            response = self.client.get("/api/settings/features")
            self.assertNotIn("auto_backup", {item["key"] for item in response.json()["features"]})
            with self.assertRaises(features.FeatureError):
                features.save({"auto_backup": True})

    def test_worker_has_no_auto_backup_lane(self):
        from .management.commands import run_worker

        self.assertFalse(hasattr(run_worker, "auto_backup_lane"))

    def test_auto_backup_endpoints_are_removed(self):
        self.assertEqual(self.client.get("/api/backup/auto").status_code, 404)
        self.assertEqual(
            self.client.post("/api/backup/auto", data=json.dumps({}), content_type="application/json").status_code, 404
        )
        self.assertEqual(
            self.client.post(
                "/api/backup/auto/inspect",
                data=json.dumps({"id": "x"}),
                content_type="application/json",
                HTTP_X_QB_REQUEST="1",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                "/api/backup/auto/import",
                data=json.dumps({"id": "x"}),
                content_type="application/json",
                HTTP_X_QB_REQUEST="1",
            ).status_code,
            404,
        )

    def test_a_backup_package_never_contains_another_backup(self):
        """1.26.19：备份包绝不能把旧备份包打进去（存量旧包也不进手动备份包）。"""
        root = library_transfer.backups_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "题有据自动备份-20261020-050000.zip").write_bytes(b"old")
        bundle_files = list(library_transfer._bundle_files(self.root))
        assert not any(path.name.endswith(".zip") and "backups" in path.parts for path in bundle_files), (
            "备份包里不能有别的备份包"
        )
        assert not any("imports" in path.parts for path in bundle_files), "合并记录也不进包"

    def test_a_failed_backup_leaves_nothing_behind(self):
        """1.26.20：备份写坏了不能留残骸。"""
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
        self.assertEqual([p.name for p in out.iterdir()], [package.name], "临时文件要清干净，只留最终的包")
        with library_transfer.zipfile.ZipFile(package) as archive:
            names = set(archive.namelist())
        self.assertIn(library_transfer.MANIFEST_NAME, names)
        self.assertIn(library_transfer.DATABASE_NAME, names)

    def test_only_one_backup_runs_at_a_time(self):
        """并发手动导出时用锁互斥，防止写出坏包。"""
        out = self.tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        with library_transfer._backup_lock():
            with self.assertRaises(library_transfer.TransferError):
                library_transfer.export_bundle(out)
