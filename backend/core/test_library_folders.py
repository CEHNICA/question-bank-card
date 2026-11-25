"""Tests for persistent Eagle folders, book section organization, personal tag groups, CLI & MCP."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from django.test import LiveServerTestCase, override_settings
from django.utils import timezone

from . import library, library_folders, library_tag_groups
from .models import LibraryTag, LibraryTagGroup, Paper, PublishedQuestion, Question

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("tiyouju_cli", ROOT / "tiyouju_cli.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("tiyouju_cli", cli)
_spec.loader.exec_module(cli)


def _run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(cli, "configure_streams"):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _run_json(*argv: str):
    code, out, err = _run_cli(*argv, "--json")
    return code, (json.loads(out) if out.strip() else None), err


@override_settings(STATIC_URL="/static/")
class LibraryFoldersAndTagGroupsTests(LiveServerTestCase):
    host = "127.0.0.1"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        override = override_settings(DATA_ROOT=self.temp / "data")
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict(os.environ, {"TIYOUJU_URL": self.live_server_url, "TIYOUJU_AGENT": ""})
        env.start()
        self.addCleanup(env.stop)

        self.book = Paper.objects.create(
            filename="高中数学思想方法导引.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="a" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
        )
        self.exam = Paper.objects.create(
            filename="高一期中卷.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.EXAM,
            sha256="b" * 64,
            status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
        )

        self.q1 = self._publish_question(self.book, 1, "已知 $x > 0$，求 $x + 1/x$ 的最小值。", section="例1")
        self.q2 = self._publish_question(self.book, 2, "解不等式 $x^2 - 3x + 2 < 0$。", section="练习1")
        self.q3 = self._publish_question(self.exam, 1, "求 $\\sin(\\pi/6)$ 的值。", section="一、选择题")

        self.tag_a = LibraryTag.objects.create(name="基本不等式", kind=LibraryTag.Kind.KNOWLEDGE, levels=["高中"])
        self.tag_b = LibraryTag.objects.create(name="配方法", kind=LibraryTag.Kind.METHOD, levels=["高中"])

    def _publish_question(self, paper: Paper, number: int, stem: str, *, section: str = "") -> PublishedQuestion:
        q = Question.objects.create(
            paper=paper,
            number=number,
            section=section,
            question_type="free_response",
            state=Question.State.GREEN,
            stem=stem,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 300]}],
        )
        library.approve(q, now=timezone.now(), source="human")
        q.save()
        pub, _ = library.publish(q)
        return pub

    def test_folder_lifecycle_depth_guard_sort_and_undo(self):
        # 1. Create root & child folders via CLI
        code, res1, _ = _run_json("folders", "create", "高一专题")
        self.assertEqual(code, 0, res1)
        root_id = res1["user_folders"][0]["id"]

        code, res2, _ = _run_json("folders", "create", "不等式专题", "--parent", "高一专题")
        self.assertEqual(code, 0, res2)
        child_id = [f["id"] for f in res2["user_folders"] if f["name"] == "不等式专题"][0]

        # 4-level max depth enforcement (root=1, child=2, g3=3, g4=4, g5 -> fail)
        code, _, _ = _run_json("folders", "create", "第3层", "--parent", child_id)
        self.assertEqual(code, 0)
        code, _, _ = _run_json("folders", "create", "第4层", "--parent", "第3层")
        self.assertEqual(code, 0)
        code, err5, _ = _run_json("folders", "create", "第5层", "--parent", "第4层")
        self.assertEqual(code, 1)
        self.assertIn("4 层", err5["error"])

        # 2. Prevent cycle move
        code, cycle_err, _ = _run_json("folders", "move", root_id, "--parent", child_id)
        self.assertEqual(code, 1)
        self.assertIn("自身或其子文件夹", cycle_err["error"])

        # 3. Add & remove questions by short ID and UUID, then set sort
        q1_short = library.short_id_for(self.q1.pk)
        code, added, _ = _run_json("folders", "add", "不等式专题", q1_short, str(self.q2.pk))
        self.assertEqual(code, 0, added)
        target_uf = [f for f in added["user_folders"] if f["id"] == child_id][0]
        self.assertEqual(set(target_uf["questionIds"]), {str(self.q1.pk), str(self.q2.pk)})

        code, removed, _ = _run_json("folders", "remove", child_id, q1_short)
        self.assertEqual(code, 0, removed)
        target_uf = [f for f in removed["user_folders"] if f["id"] == child_id][0]
        self.assertEqual(target_uf["questionIds"], [str(self.q2.pk)])

        # Undo remove -> q1 is back in folder
        code, undone, _ = _run_json("folders", "undo")
        self.assertEqual(code, 0, undone)
        target_uf = [f for f in undone["user_folders"] if f["id"] == child_id][0]
        self.assertEqual(set(target_uf["questionIds"]), {str(self.q1.pk), str(self.q2.pk)})

        # Sort mode + default reset
        code, sorted_res, _ = _run_json("folders", "sort", "section:folders", "name")
        self.assertEqual(code, 0, sorted_res)
        self.assertEqual(sorted_res["sort_modes"]["section:folders"], "name")

        code, reset_res, _ = _run_json("folders", "sort", "section:folders", "default")
        self.assertEqual(code, 0, reset_res)
        self.assertNotIn("section:folders", reset_res["sort_modes"])

        # Rename & delete + undo delete
        code, ren_res, _ = _run_json("folders", "rename", child_id, "基本不等式")
        self.assertEqual(code, 0)
        self.assertEqual([f["name"] for f in ren_res["user_folders"] if f["id"] == child_id][0], "基本不等式")

        code, del_res, _ = _run_json("folders", "delete", root_id)
        self.assertEqual(code, 0)
        self.assertEqual(del_res["user_folders"], [])

        code, undo_del, _ = _run_json("folders", "undo")
        self.assertEqual(code, 0)
        self.assertEqual(len(undo_del["user_folders"]), 4)

    def test_set_section_updates_book_tree_keeps_approval_and_supports_undo(self):
        map_file = self.temp / "sections.json"
        map_file.write_text(
            json.dumps(
                {
                    library.short_id_for(self.q1.pk): "第1节 基本不等式 · 例题",
                    str(self.q2.pk): "第1节 基本不等式 · 巩固练习",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        code, res, _ = _run_json("folders", "set-section", "--map-file", str(map_file))
        self.assertEqual(code, 0, res)

        self.q1.refresh_from_db()
        self.q2.refresh_from_db()
        self.assertEqual(self.q1.content["section"], "第1节 基本不等式 · 例题")
        self.assertEqual(self.q2.content["section"], "第1节 基本不等式 · 巩固练习")
        # Does not create a new publication version and keeps linked Question approval current
        self.assertEqual(PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).count(), 3)
        q1_card = Question.objects.get(pk=self.q1.question_id)
        self.assertEqual(q1_card.section, "第1节 基本不等式 · 例题")
        self.assertTrue(library.approval_is_current(q1_card))

        # Tree endpoint aggregates compound sections for the book
        books = res["tree"]["books"]
        self.assertEqual(len(books), 1)
        self.assertEqual(books[0]["sections"][0]["name"], "第1节 基本不等式")
        sub_names = {c["name"] for c in books[0]["sections"][0]["children"]}
        self.assertEqual(sub_names, {"例题", "巩固练习"})

        # Undo restores original section values
        code, undone, _ = _run_json("folders", "undo")
        self.assertEqual(code, 0, undone)
        self.q1.refresh_from_db()
        self.q2.refresh_from_db()
        self.assertEqual(self.q1.content["section"], "例1")
        self.assertEqual(self.q2.content["section"], "练习1")

    def test_paper_rename_type_switch_and_fix_section(self):
        # Switch exam -> book and rename
        code, res, _ = _run_json("folders", "paper", str(self.exam.pk)[:8], "--name", "高一期中汇编", "--type", "book")
        self.assertEqual(code, 0, res)
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.display_name, "高一期中汇编")
        self.assertEqual(self.exam.material_type, Paper.MaterialType.BOOK)

        # Draft card fix --section
        draft = Question.objects.create(
            paper=self.exam,
            number=99,
            question_type="fill_blank",
            state=Question.State.GREEN,
            stem="填空 $1+1=$____。",
            regions=[{"page_idx": 0, "bbox": [50, 350, 900, 500]}],
        )
        code, fix_res, _ = _run_json("fix", str(self.exam.pk)[:8], f"#{draft.pk}", "--section", "第2章 · 练习")
        self.assertEqual(code, 0, fix_res)
        draft.refresh_from_db()
        self.assertEqual(draft.section, "第2章 · 练习")

    def test_personal_tag_groups_crud_and_undo(self):
        code, created, _ = _run_json(
            "folders", "tag-group", "create", "期末高频", "--tags", "基本不等式", str(self.tag_b.pk)
        )
        self.assertEqual(code, 0, created)
        self.assertEqual(len(created["groups"]), 1)
        gid = created["groups"][0]["id"]
        self.assertEqual({t["name"] for t in created["groups"][0]["tags"]}, {"基本不等式", "配方法"})

        code, removed, _ = _run_json("folders", "tag-group", "remove-tags", "期末高频", "基本不等式")
        self.assertEqual(code, 0, removed)
        self.assertEqual([t["name"] for t in removed["groups"][0]["tags"]], ["配方法"])

        code, renamed, _ = _run_json("folders", "tag-group", "rename", gid, "考前必刷")
        self.assertEqual(code, 0, renamed)
        self.assertEqual(renamed["groups"][0]["name"], "考前必刷")

        code, undone, _ = _run_json("folders", "tag-group", "undo")
        self.assertEqual(code, 0, undone)
        self.assertEqual(undone["groups"][0]["name"], "期末高频")

        # Catalogue endpoint remains free of legacy groups field
        code, cat = 0, self.client.get("/api/library/tags", HTTP_X_QB_REQUEST="1").json()
        self.assertEqual(code, 0)
        self.assertNotIn("groups", cat)

    def test_mcp_folder_tools_and_sync_stale_guard(self):
        client = cli.Client(self.live_server_url, agent="测试MCP")
        # 1. list_folders via MCP
        reply = cli.mcp_handle(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_folders", "arguments": {}}},
        )
        self.assertFalse(reply["result"]["isError"])
        data = json.loads(reply["result"]["content"][0]["text"])
        self.assertIn("tree", data)

        # 2. manage_folder create + set_question_sections + update_paper_folder + manage_tag_groups
        reply2 = cli.mcp_handle(
            client,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "manage_folder",
                    "arguments": {
                        "action": "create",
                        "name": "MCP文件夹",
                        "question_ids": [library.short_id_for(self.q3.pk)],
                    },
                },
            },
        )
        self.assertFalse(reply2["result"]["isError"])
        res2 = json.loads(reply2["result"]["content"][0]["text"])
        self.assertEqual(res2["user_folders"][0]["questionIds"], [str(self.q3.pk)])

        # Stale frontend sync with older revision does not overwrite server changes
        stale_sync = library_folders.mutate({"action": "sync", "revision": 0, "user_folders": []})
        self.assertTrue(stale_sync["stale"])
        self.assertEqual(len(stale_sync["user_folders"]), 1)

        reply3 = cli.mcp_handle(
            client,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "set_question_sections",
                    "arguments": {"section": "第3章 · 综合", "question_ids": [library.short_id_for(self.q1.pk)]},
                },
            },
        )
        self.assertFalse(reply3["result"]["isError"])
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.content["section"], "第3章 · 综合")

        reply4 = cli.mcp_handle(
            client,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "update_paper_folder",
                    "arguments": {"paper": str(self.exam.pk)[:8], "material_type": "book"},
                },
            },
        )
        self.assertFalse(reply4["result"]["isError"])
        self.exam.refresh_from_db()
        self.assertEqual(self.exam.material_type, Paper.MaterialType.BOOK)

        reply5 = cli.mcp_handle(
            client,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "manage_tag_groups",
                    "arguments": {"action": "create_group", "name": "MCP分组", "tag_ids": ["基本不等式"]},
                },
            },
        )
        self.assertFalse(reply5["result"]["isError"])
        self.assertEqual(LibraryTagGroup.objects.filter(name="MCP分组").count(), 1)

    def test_sync_and_remove_preserve_doc_override_sections(self):
        book_id = str(self.book.pk)
        q1_id = str(self.q1.pk)
        synced = library_folders.mutate(
            {
                "action": "sync",
                "revision": 0,
                "doc_overrides": {
                    book_id: {
                        "added": [q1_id],
                        "removed": [],
                        "sections": {
                            q1_id: {
                                "chapterKey": "1",
                                "chapterNo": 1,
                                "sectionName": "1.1 集合的概念",
                                "sectionMatchMode": "exact",
                                "label": "1.1 集合的概念",
                            }
                        },
                    }
                },
            }
        )
        self.assertEqual(synced["doc_overrides"][book_id]["added"], [q1_id])
        self.assertEqual(synced["doc_overrides"][book_id]["sections"][q1_id]["sectionName"], "1.1 集合的概念")

        removed = library_folders.mutate(
            {"action": "remove_questions", "target_id": f"book:{book_id}", "question_ids": [q1_id]}
        )
        self.assertEqual(removed["doc_overrides"][book_id]["removed"], [q1_id])
        self.assertNotIn("sections", removed["doc_overrides"][book_id])

    def test_sync_preserves_sort_order_and_manual_mode(self):
        synced = library_folders.mutate(
            {
                "action": "sync",
                "revision": 0,
                "user_folders": [
                    {
                        "id": "uf_a",
                        "name": "甲",
                        "parentId": None,
                        "questionIds": [],
                        "createdAt": 1,
                        "sortOrder": 20,
                    },
                    {
                        "id": "uf_b",
                        "name": "乙",
                        "parentId": None,
                        "questionIds": [],
                        "createdAt": 2,
                        "sortOrder": 10,
                    },
                ],
                "smart_folders": [
                    {
                        "id": "csf_a",
                        "name": "视图",
                        "parentId": None,
                        "filter": {},
                        "createdAt": 3,
                        "sortOrder": 5,
                    }
                ],
                "sort_modes": {"section:folders": "manual"},
                "paper_sort_mode": "manual",
            }
        )
        by_id = {f["id"]: f for f in synced["user_folders"]}
        self.assertEqual(by_id["uf_a"]["sortOrder"], 20)
        self.assertEqual(by_id["uf_b"]["sortOrder"], 10)
        self.assertEqual(synced["smart_folders"][0]["sortOrder"], 5)
        self.assertEqual(synced["sort_modes"]["section:folders"], "manual")
        self.assertEqual(synced["paper_sort_mode"], "manual")

        sorted_res = library_folders.mutate(
            {"action": "set_sort", "target_id": "section:folders", "mode": "manual"}
        )
        self.assertEqual(sorted_res["sort_modes"]["section:folders"], "manual")
