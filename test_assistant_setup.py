"""Offline consent, file ownership and recovery checks for assistant setup."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import assistant_setup as setup


class FileShortcuts:
    """A portable fake of COM whose result survives copies and renames."""

    def __init__(self):
        self.created = 0
        self.wrong_target = False

    def read(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def create(self, path, app):
        self.created += 1
        path.write_text(json.dumps({"target": str(app if not self.wrong_target else app.parent / "other.exe"),
                                    "arguments": "", "working_dir": str(app.parent)}), encoding="utf-8")


class AssistantSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "bundled" / "tiyouju"
        (self.source / "references").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("# 题有据\n", encoding="utf-8")
        (self.source / "references" / "commands.md").write_text("Use the real CLI.\n", encoding="utf-8")
        (self.source / "assets").mkdir()
        (self.source / "assets" / "bytes.bin").write_bytes(bytes(range(128)))
        (self.source / "empty-resource-folder").mkdir()
        self.parent = self.root / "assistant" / "skills"
        self.app = self.root / "installed" / "QuestionBankCard.exe"
        self.app.parent.mkdir()
        self.app.write_bytes(b"not a real PE; version reader is injected")
        self.desktop = self.root / "OneDrive" / "Desktop"
        self.desktop.mkdir(parents=True)
        self.shortcuts = FileShortcuts()

    def run_setup(self, **changes):
        options = {
            "resources": self.source,
            "desktop_path": self.desktop,
            "shortcuts": self.shortcuts,
            "version_reader": lambda _app: "1.10.14",
        }
        options.update(changes)
        return setup.assistant_setup(self.app, **options)

    def test_default_only_reports_and_asks_both_choices(self):
        before = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        with mock.patch.object(setup.shutil, "copytree", side_effect=AssertionError("unconfirmed copy")), \
                mock.patch.object(setup, "_rename", side_effect=AssertionError("unconfirmed move")):
            result = self.run_setup()
        self.assertEqual(result["software"]["version"], "1.10.14")
        self.assertTrue(result["software"]["version_verified"])
        self.assertEqual(result["skill"]["status"], "not_requested")
        self.assertTrue(result["skill"]["available"])
        self.assertEqual([item["id"] for item in result["questions"]], ["install_skill", "desktop_icon"])
        self.assertEqual(self.shortcuts.created, 0)
        self.assertFalse(self.parent.exists())
        self.assertEqual(before, sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")))
        self.assertIn("以后有试卷", result["invitation"])

    def test_missing_or_unverifiable_program_never_claims_installed_version(self):
        result = setup.assistant_setup(None, resources=self.source)
        self.assertFalse(result["software"]["installed"])
        self.assertIn("尚未找到", setup.format_report(result))
        result = self.run_setup(version_reader=mock.Mock(side_effect=setup.SetupError("bad version")))
        self.assertTrue(result["software"]["installed"])
        self.assertFalse(result["software"]["version_verified"])
        self.assertIsNone(result["software"]["version"])
        self.assertNotIn("已确认电脑上安装", setup.format_report(result))

    def test_skill_requires_absolute_parent_and_explicit_replace_destination(self):
        for parent in (Path("relative/skills"), self.root / "other" / ".." / "skills"):
            with self.subTest(parent=parent), self.assertRaises(setup.SetupError):
                self.run_setup(skill_dir=parent)
        with self.assertRaises(setup.SetupError):
            self.run_setup(replace_skill=True)
        self.assertFalse(self.parent.exists())

    def test_skill_source_cannot_contain_its_installation_destination(self):
        previous = setup._manifest(self.source)
        with self.assertRaisesRegex(setup.SetupError, "相互包含"):
            self.run_setup(skill_dir=self.source / "nested" / "skills")
        self.assertEqual(setup._manifest(self.source), previous)
        self.assertFalse((self.source / "nested").exists())

    def test_complete_skill_installation_and_same_content_are_idempotent(self):
        first = self.run_setup(skill_dir=self.parent)
        installed = self.parent / "tiyouju"
        self.assertEqual(setup._manifest(installed), setup._manifest(self.source))
        self.assertTrue(first["skill"]["verified"])
        self.assertEqual(first["skill"]["file_count"], 3)
        self.assertEqual([item["id"] for item in first["questions"]], ["desktop_icon"])
        self.assertFalse((self.desktop / "题有据.lnk").exists())
        with mock.patch.object(setup.shutil, "copytree", side_effect=AssertionError("duplicate copy")):
            second = self.run_setup(skill_dir=self.parent)
        self.assertEqual(second["skill"]["status"], "already_installed")
        self.assertFalse(second["skill"]["changed"])
        self.assertFalse(list(self.parent.glob("tiyouju.backup-*")))

    def test_custom_skill_is_preserved_without_replace_and_backed_up_with_it(self):
        self.run_setup(skill_dir=self.parent)
        user_file = self.parent / "tiyouju" / "my-settings.json"
        user_file.write_text('{"custom": true}', encoding="utf-8")
        previous = setup._manifest(self.parent / "tiyouju")
        with self.assertRaisesRegex(setup.SetupError, "未覆盖"):
            self.run_setup(skill_dir=self.parent)
        self.assertEqual(setup._manifest(self.parent / "tiyouju"), previous)
        result = self.run_setup(skill_dir=self.parent, replace_skill=True)
        backup = Path(result["skill"]["backup"])
        self.assertEqual(setup._manifest(backup), previous)
        self.assertEqual(setup._manifest(self.parent / "tiyouju"), setup._manifest(self.source))
        self.assertTrue(user_file.name in {path.name for path in backup.iterdir()})

    def test_refuses_links_in_destination_ancestors_and_skill_resources(self):
        forbidden = self.root / "linked"
        original = setup._is_link
        with mock.patch.object(setup, "_is_link", side_effect=lambda path: path == forbidden or original(path)):
            with self.assertRaisesRegex(setup.SetupError, "符号链接"):
                self.run_setup(skill_dir=forbidden / "skills")
        resource = self.source / "references" / "commands.md"
        with mock.patch.object(setup, "_is_link", side_effect=lambda path: path == resource or original(path)):
            with self.assertRaisesRegex(setup.SetupError, "符号链接"):
                self.run_setup(skill_dir=self.parent)
        self.assertFalse(self.parent.exists())

    def test_onedrive_cloud_placeholders_are_not_confused_with_junctions(self):
        for tag, denied in ((0x9000001A, False), (0xA0000003, True), (0xA000000C, True), (0, True)):
            info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400, st_reparse_tag=tag)
            with self.subTest(tag=hex(tag)), mock.patch.object(Path, "lstat", return_value=info):
                self.assertEqual(setup._is_link(self.desktop), denied)

    def test_copy_failure_does_not_move_existing_skill(self):
        self.run_setup(skill_dir=self.parent)
        target = self.parent / "tiyouju"
        (target / "custom.txt").write_text("mine", encoding="utf-8")
        previous = setup._manifest(target)
        with mock.patch.object(setup.shutil, "copytree", side_effect=OSError("disk full")), \
                self.assertRaisesRegex(setup.SetupError, "未完成"):
            self.run_setup(skill_dir=self.parent, replace_skill=True)
        self.assertEqual(setup._manifest(target), previous)
        self.assertFalse(list(self.parent.glob("tiyouju.backup-*")))

    def test_failed_final_move_restores_existing_skill(self):
        self.run_setup(skill_dir=self.parent)
        target = self.parent / "tiyouju"
        (target / "custom.txt").write_text("mine", encoding="utf-8")
        previous = setup._manifest(target)
        original = setup._rename

        def fail_stage(source, destination):
            if source.name.startswith(".tiyouju-install-"):
                raise OSError("simulated locked destination")
            return original(source, destination)

        with mock.patch.object(setup, "_rename", side_effect=fail_stage), self.assertRaises(setup.SetupError):
            self.run_setup(skill_dir=self.parent, replace_skill=True)
        self.assertEqual(setup._manifest(target), previous)
        self.assertFalse(list(self.parent.glob(".tiyouju-install-*")))

    def test_corrupted_copy_is_not_reported_as_success(self):
        original = setup.shutil.copytree

        def corrupt(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if Path(source) == self.source:
                (Path(target) / "references" / "commands.md").write_text("corrupt", encoding="utf-8")
            return result

        with mock.patch.object(setup.shutil, "copytree", side_effect=corrupt), \
                self.assertRaisesRegex(setup.SetupError, "不一致"):
            self.run_setup(skill_dir=self.parent)
        self.assertFalse((self.parent / "tiyouju").exists())
        self.assertFalse(list(self.parent.glob(".tiyouju-install-*")))

    def test_post_install_verification_failure_preserves_the_failed_copy_and_restores_backup(self):
        self.run_setup(skill_dir=self.parent)
        target = self.parent / "tiyouju"
        (target / "custom.txt").write_text("mine", encoding="utf-8")
        previous = setup._manifest(target)
        original = setup._rename

        def corrupt_installed(source, destination):
            original(source, destination)
            if source.name.startswith(".tiyouju-install-"):
                (destination / "SKILL.md").write_text("modified during final move", encoding="utf-8")

        with mock.patch.object(setup, "_rename", side_effect=corrupt_installed), \
                self.assertRaisesRegex(setup.SetupError, "原技能已恢复"):
            self.run_setup(skill_dir=self.parent, replace_skill=True)
        self.assertEqual(setup._manifest(target), previous)
        failed = list(self.parent.glob("tiyouju.failed-*"))
        self.assertEqual(len(failed), 1)
        self.assertEqual((failed[0] / "SKILL.md").read_text(encoding="utf-8"), "modified during final move")
        self.assertFalse(list(self.parent.glob(".tiyouju-install-*")))

    def test_desktop_show_hide_and_restore_keep_only_the_owned_link(self):
        unrelated = self.desktop / "别的软件.lnk"
        unrelated.write_text("untouched", encoding="utf-8")
        first = self.run_setup(desktop="show")
        path = self.desktop / "题有据.lnk"
        self.assertEqual(first["desktop"]["status"], "shown")
        self.assertTrue(first["desktop"]["verified"])
        self.assertEqual(self.shortcuts.read(path)["target"], str(self.app))
        second = self.run_setup(desktop="show")
        self.assertFalse(second["desktop"]["changed"])
        self.assertIn("桌面图标已显示", setup.format_report(second))
        self.assertEqual(self.shortcuts.created, 1)
        previous = path.read_bytes()
        hidden = self.run_setup(desktop="hide")
        self.assertFalse(path.exists())
        backup = Path(hidden["desktop"]["backup"])
        self.assertEqual(backup.read_bytes(), previous)
        again = self.run_setup(desktop="hide")
        self.assertFalse(again["desktop"]["changed"])
        self.assertIn("桌面图标已隐藏", setup.format_report(again))
        restored = self.run_setup(desktop="show")
        self.assertEqual(path.read_bytes(), previous)
        self.assertEqual(restored["desktop"]["backup"], str(backup))
        self.assertTrue(backup.exists())
        self.assertEqual(self.shortcuts.created, 1)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "untouched")
        self.assertEqual([item["id"] for item in first["questions"]], ["install_skill"])

    def test_same_name_foreign_link_or_extra_arguments_are_never_modified(self):
        path = self.desktop / "题有据.lnk"
        for target, arguments in ((self.app.parent / "other.exe", ""), (self.app, "--something")):
            path.write_text(json.dumps({"target": str(target), "arguments": arguments}), encoding="utf-8")
            before = path.read_bytes()
            report = self.run_setup()
            self.assertEqual(report["desktop"]["status"], "conflict")
            for mode in ("show", "hide"):
                with self.subTest(mode=mode, arguments=arguments), self.assertRaisesRegex(setup.SetupError, "其他用途"):
                    self.run_setup(skill_dir=self.parent, desktop=mode)
                self.assertEqual(path.read_bytes(), before)
                self.assertFalse(self.parent.exists())
        self.assertFalse((self.app.parent / "assistant-shortcut-backups").exists())

    def test_shortcut_creation_is_verified_before_it_can_become_the_desktop_icon(self):
        self.shortcuts.wrong_target = True
        with self.assertRaisesRegex(setup.SetupError, "校验失败"):
            self.run_setup(desktop="show")
        self.assertFalse((self.desktop / "题有据.lnk").exists())
        self.assertFalse(list(self.desktop.glob(".tiyouju-shortcut-*")))

    def test_both_explicit_choices_clear_the_pending_questions(self):
        result = self.run_setup(skill_dir=self.parent, desktop="show")
        self.assertEqual(result["questions"], [])
        self.assertIn("技能已安装并核验", setup.format_report(result))
        self.assertIn("桌面图标已显示", setup.format_report(result))

    def test_desktop_failure_reports_completed_skill_and_preserved_backup_without_undoing_it(self):
        self.run_setup(skill_dir=self.parent)
        target = self.parent / "tiyouju"
        (target / "custom.txt").write_text("preserve my settings", encoding="utf-8")
        previous = setup._manifest(target)
        with mock.patch.object(self.shortcuts, "create", side_effect=setup.SetupError("COM 保存失败")), \
                self.assertRaises(setup.SetupError) as caught:
            self.run_setup(skill_dir=self.parent, replace_skill=True, desktop="show")
        backups = list(self.parent.glob("tiyouju.backup-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(setup._manifest(backups[0]), previous)
        self.assertEqual(setup._manifest(target), setup._manifest(self.source))
        self.assertIn(f"技能已完成核验：{target}", str(caught.exception))
        self.assertIn(str(backups[0]), str(caught.exception))
        self.assertIn("桌面图标未确认设置成功", str(caught.exception))
        self.assertIn("COM 保存失败", str(caught.exception))
        self.assertFalse((self.desktop / "题有据.lnk").exists())

    def test_missing_source_skill_fails_before_any_skill_is_installed(self):
        (self.source / "SKILL.md").unlink()
        result = self.run_setup()
        self.assertFalse(result["skill"]["available"])
        with self.assertRaisesRegex(setup.SetupError, "SKILL.md"):
            self.run_setup(skill_dir=self.parent)
        self.assertFalse(self.parent.exists())

    def test_source_and_frozen_resource_locations_are_separate(self):
        self.assertEqual(setup.bundled_skill(), Path(setup.__file__).resolve().parent / "skills" / "tiyouju")
        with mock.patch.object(setup.sys, "frozen", True, create=True), \
                mock.patch.object(setup.sys, "_MEIPASS", str(self.root / "_internal"), create=True):
            self.assertEqual(setup.bundled_skill(), self.root / "_internal" / "skills" / "tiyouju")


class SetupAgentsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.data_root = self.root / "appdata"
        self.source = self.root / "bundled" / "tiyouju"
        (self.source / "references").mkdir(parents=True)
        (self.source / "SKILL.md").write_text("---\nname: tiyouju\ndescription: demo\n---\n", encoding="utf-8")
        (self.source / "references" / "commands.md").write_text("tiyouju skill\n", encoding="utf-8")
        self.app = self.root / "programs" / "QuestionBankCard.exe"
        self.app.parent.mkdir()
        self.app.write_bytes(b"exe")

    def test_discovers_only_present_agent_homes_plus_universal_agents(self):
        (self.home / ".claude").mkdir()
        (self.home / ".gemini" / "antigravity").mkdir(parents=True)
        (self.home / ".trae-cn").mkdir()
        (self.home / ".qwen").mkdir()
        (self.home / ".codebuddy").mkdir()
        (self.home / ".workbuddy").mkdir()
        (self.home / ".minimax").mkdir()
        (self.home / ".kimi").mkdir()
        (self.home / ".qoder").mkdir()
        (self.home / ".lingma").mkdir()
        (self.home / ".openclaw").mkdir()
        discovered = dict(setup.discover_agent_skill_parents(self.home))
        for expected in (
            "universal_agents",
            "claude_code",
            "antigravity",
            "gemini_config",
            "trae_cn",
            "qwen_code",
            "codebuddy",
            "workbuddy",
            "minimax",
            "kimi",
            "qoder",
            "lingma",
            "openclaw",
        ):
            self.assertIn(expected, discovered)
        self.assertNotIn("codex", discovered)
        self.assertNotIn("cursor", discovered)
        self.assertNotIn("iflow", discovered)

    def test_discovers_and_registers_mcp_configs_for_domestic_and_global_agents(self):
        cli_exe = self.app.parent / "tiyouju.exe"
        cli_exe.write_bytes(b"cli")
        appdata = self.root / "appdata_roaming"
        (appdata / "Trae CN" / "User").mkdir(parents=True)
        (appdata / "MiniMax Agent").mkdir(parents=True)
        (appdata / "Claude").mkdir(parents=True)
        (self.home / ".cursor").mkdir()
        (self.home / ".qwen").mkdir()
        (self.home / ".codebuddy").mkdir()
        (self.home / ".codebuddy" / ".mcp.json").write_text("{}", encoding="utf-8")
        (self.home / ".workbuddy").mkdir()
        (self.home / ".minimax").mkdir()
        (self.home / ".claude.json").write_text('{"other": 1}', encoding="utf-8")

        trae_cfg = appdata / "Trae CN" / "User" / "mcp.json"
        trae_cfg.write_text('{"mcpServers": {"other_tool": {"command": "node"}}}', encoding="utf-8")

        bad_cfg = self.home / ".codebuddy" / "mcp.json"
        bad_cfg.write_text("{not valid json", encoding="utf-8")

        targets = dict(setup.discover_mcp_config_targets(home=self.home, appdata=appdata))
        for client_id in (
            "cursor",
            "trae_cn",
            "claude_desktop",
            "claude_code",
            "qwen_code",
            "codebuddy",
            "codebuddy_dot",
            "workbuddy",
            "minimax",
            "minimax_agent",
        ):
            self.assertIn(client_id, targets)
        self.assertNotIn("trae", targets)
        self.assertNotIn("kimi", targets)

        first = setup.setup_agents(
            self.app,
            data_root=self.data_root,
            home=self.home,
            appdata=appdata,
            resources=self.source,
            update_path=False,
        )
        self.assertTrue(first["skill_zip"]["verified"])
        zip_path = Path(first["skill_zip"]["path"])
        self.assertTrue(zip_path.is_file())
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            self.assertIn("tiyouju/SKILL.md", names)
            self.assertIn("tiyouju/references/commands.md", names)
        mcp_map = {item["client"]: item for item in first["mcp"]}
        self.assertEqual(mcp_map["trae_cn"]["status"], "configured")
        self.assertEqual(mcp_map["workbuddy"]["status"], "configured")
        self.assertEqual(mcp_map["codebuddy_dot"]["status"], "configured")
        self.assertEqual(mcp_map["minimax"]["status"], "configured")
        self.assertEqual(mcp_map["minimax_agent"]["status"], "configured")
        self.assertTrue(mcp_map["trae_cn"]["verified"])
        self.assertEqual(mcp_map["claude_code"]["status"], "configured")
        self.assertEqual(mcp_map["codebuddy"]["status"], "skipped_invalid_json")
        self.assertEqual(bad_cfg.read_text(encoding="utf-8"), "{not valid json")

        trae_saved = json.loads(trae_cfg.read_text(encoding="utf-8"))
        self.assertIn("other_tool", trae_saved["mcpServers"])
        self.assertEqual(trae_saved["mcpServers"]["tiyouju"], {"command": str(cli_exe), "args": ["mcp"]})

        # Custom conflicting entry is preserved unless replace=True
        cursor_cfg = self.home / ".cursor" / "mcp.json"
        cursor_cfg.write_text(
            json.dumps({"mcpServers": {"tiyouju": {"command": "python", "args": ["custom.py"]}}}),
            encoding="utf-8",
        )
        second = setup.setup_agents(
            self.app,
            data_root=self.data_root,
            home=self.home,
            appdata=appdata,
            resources=self.source,
            update_path=False,
        )
        second_map = {item["client"]: item for item in second["mcp"]}
        self.assertEqual(second_map["trae_cn"]["status"], "already_configured")
        self.assertEqual(second_map["cursor"]["status"], "skipped_custom")

        replaced = setup.setup_agents(
            self.app,
            data_root=self.data_root,
            home=self.home,
            appdata=appdata,
            resources=self.source,
            update_path=False,
            replace=True,
        )
        replaced_map = {item["client"]: item for item in replaced["mcp"]}
        self.assertEqual(replaced_map["cursor"]["status"], "updated")
        self.assertTrue(Path(replaced_map["cursor"]["backup"]).is_file())

        inspected = setup.inspect_mcp_config(self.app, home=self.home, appdata=appdata)
        client_statuses = {item["client"]: item["status"] for item in inspected["clients"]}
        self.assertEqual(client_statuses["cursor"], "configured")
        self.assertEqual(client_statuses["codebuddy"], "invalid_json")
        self.assertIn("codex mcp add tiyouju", inspected["codex_command"])

        no_mcp = setup.setup_agents(
            self.app,
            data_root=self.data_root,
            home=self.home,
            appdata=appdata,
            resources=self.source,
            update_path=False,
            register_mcp=False,
        )
        self.assertEqual(no_mcp["mcp"], [])

    def test_setup_agents_links_skills_idempotently_and_preserves_custom_skill(self):
        (self.home / ".claude").mkdir()
        custom_dir = self.home / ".codex" / "skills" / "tiyouju"
        custom_dir.mkdir(parents=True)
        (custom_dir / "SKILL.md").write_text("custom", encoding="utf-8")

        first = setup.setup_agents(
            self.app,
            data_root=self.data_root,
            home=self.home,
            resources=self.source,
            update_path=False,
        )
        self.assertEqual(first["canonical"]["status"], "synced")
        agents = {item["agent"]: item for item in first["agents"]}
        self.assertTrue(agents["universal_agents"]["verified"])
        self.assertTrue(agents["claude_code"]["verified"])
        self.assertEqual(agents["codex"]["status"], "skipped_custom")
        self.assertEqual((custom_dir / "SKILL.md").read_text(encoding="utf-8"), "custom")

        second = setup.setup_agents(
            self.app,
            data_root=self.data_root,
            home=self.home,
            resources=self.source,
            update_path=False,
        )
        self.assertEqual(second["canonical"]["status"], "up_to_date")
        agents_again = {item["agent"]: item for item in second["agents"]}
        self.assertEqual(agents_again["claude_code"]["status"], "already_linked")

    def test_ensure_user_path_appends_once_and_ignores_trailing_slash(self):
        stored = {"val": "C:\\Windows\\System32;D:\\Existing\\", "type": 2}
        broadcasts = []

        def reader():
            return stored["val"], stored["type"]

        def writer(val, reg_type):
            stored["val"] = val
            stored["type"] = reg_type

        added = setup.ensure_user_path(
            self.app.parent,
            read_env=reader,
            write_env=writer,
            broadcast=lambda: broadcasts.append(True),
        )
        self.assertEqual(added["status"], "added")
        self.assertEqual(len(broadcasts), 1)
        self.assertIn(str(self.app.parent), stored["val"])

        again = setup.ensure_user_path(
            Path(str(self.app.parent) + "\\"),
            read_env=reader,
            write_env=writer,
            broadcast=lambda: broadcasts.append(True),
        )
        self.assertEqual(again["status"], "already_present")
        self.assertEqual(len(broadcasts), 1)


class BundledOneNoteReviewTests(unittest.TestCase):
    def test_review_resources_survive_install_sync_and_zip_export(self):
        source = Path(__file__).resolve().parent / "skills" / "tiyouju"
        resources = [f"references/onenote-review/{name}" for name in ("SKILL.md", "windows-access.md", "mathml.md")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installed = setup.install_skill(source, root / "assistant")
            canonical = root / "canonical" / "tiyouju"
            setup.sync_canonical_skill(source, canonical)
            archive = root / "tiyouju-skill.zip"
            setup.build_skill_zip(canonical, archive)
            with zipfile.ZipFile(archive) as zipped:
                for name in resources:
                    original = (source / name).read_bytes()
                    self.assertEqual((Path(installed["target"]) / name).read_bytes(), original)
                    self.assertEqual((canonical / name).read_bytes(), original)
                    self.assertEqual(zipped.read("tiyouju/" + name), original)


if __name__ == "__main__":
    unittest.main()
