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

        # 8-level max depth enforcement (1..8 allowed, 9 -> fail)
        parent = child_id
        for level in range(3, 9):
            code, _, _ = _run_json("folders", "create", f"第{level}层", "--parent", parent)
            self.assertEqual(code, 0)
            parent = f"第{level}层"
        code, err9, _ = _run_json("folders", "create", "第9层", "--parent", "第8层")
        self.assertEqual(code, 1)
        self.assertIn("8 层", err9["error"])

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
        self.assertEqual(len(undo_del["user_folders"]), 8)

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
                    {"id": "uf_a", "name": "甲", "parentId": None, "questionIds": [], "createdAt": 1, "sortOrder": 20},
                    {"id": "uf_b", "name": "乙", "parentId": None, "questionIds": [], "createdAt": 2, "sortOrder": 10},
                ],
                "smart_folders": [
                    {"id": "csf_a", "name": "视图", "parentId": None, "filter": {}, "createdAt": 3, "sortOrder": 5}
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

        sorted_res = library_folders.mutate({"action": "set_sort", "target_id": "section:folders", "mode": "manual"})
        self.assertEqual(sorted_res["sort_modes"]["section:folders"], "manual")

    def test_move_doc_unified_tree_cycle_depth_and_delete_release(self):
        book_node = f"book:{self.book.pk}"
        exam_node = f"paper:{self.exam.pk}"
        code, created, _ = _run_json("folders", "create", "一轮复习")
        self.assertEqual(code, 0, created)
        topic_id = created["user_folders"][0]["id"]

        # Move book into topic folder -> tree shows it inside placed_docs, parent count includes book's questions
        code, moved, _ = _run_json("folders", "move", book_node, "--parent", topic_id)
        self.assertEqual(code, 0, moved)
        self.assertEqual(moved["doc_placements"][str(self.book.pk)]["parentId"], topic_id)
        topic_tree = moved["tree"]["user_folders"][0]
        self.assertEqual(len(topic_tree["placed_docs"]), 1)
        self.assertEqual(topic_tree["placed_docs"][0]["id"], str(self.book.pk))
        self.assertEqual(topic_tree["count"], 2)

        # Prevent cycle: cannot move topic folder inside book that is already inside topic folder
        code, cycle_err, _ = _run_json("folders", "move", topic_id, "--parent", book_node)
        self.assertEqual(code, 1)
        self.assertIn("自身或其子文件夹", cycle_err["error"])

        # Move exam to section:folders root
        code, root_doc, _ = _run_json("folders", "move", exam_node)
        self.assertEqual(code, 0, root_doc)
        self.assertEqual(root_doc["doc_placements"][str(self.exam.pk)]["parentId"], "section:folders")
        self.assertEqual([d["id"] for d in root_doc["tree"]["root_placed_docs"]], [str(self.exam.pk)])

        # Deleting topic folder releases book back to default group without deleting book or questions
        code, deleted, _ = _run_json("folders", "delete", topic_id)
        self.assertEqual(code, 0, deleted)
        self.assertNotIn(str(self.book.pk), deleted["doc_placements"])
        self.assertEqual(PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).count(), 3)

        # Undo restores both folder and doc placement
        code, undone, _ = _run_json("folders", "undo")
        self.assertEqual(code, 0, undone)
        self.assertEqual(undone["doc_placements"][str(self.book.pk)]["parentId"], topic_id)

        # Reset back to default group
        code, reset_doc, _ = _run_json("folders", "move", book_node, "--parent", "default")
        self.assertEqual(code, 0, reset_doc)
        self.assertNotIn(str(self.book.pk), reset_doc["doc_placements"])

        # Depth limit guard when moving deep subtree into nested folder
        cur_parent = topic_id
        for idx in range(6):
            code, res_c, _ = _run_json("folders", "create", f"深层{idx}", "--parent", cur_parent)
            self.assertEqual(code, 0, res_c)
            cur_parent = res_c["user_folders"][-1]["id"]
        # topic(1) + 6 levels = 7 levels total; placing book under level 7 -> depth 8 ok; placing inside another child fails
        code, deep_ok, _ = _run_json("folders", "move", book_node, "--parent", cur_parent)
        self.assertEqual(code, 0, deep_ok)
        code, err_over, _ = _run_json("folders", "create", "超限子夹", "--parent", book_node)
        self.assertEqual(code, 1)
        self.assertIn("8 层", err_over["error"])

    def test_reorder_mixed_siblings_and_custom_tree_toggle(self):
        code, r1, _ = _run_json("folders", "create", "专题甲")
        self.assertEqual(code, 0, r1)
        code, r2, _ = _run_json("folders", "create", "专题乙")
        self.assertEqual(code, 0, r2)
        exam_node = f"paper:{self.exam.pk}"
        code, _, _ = _run_json("folders", "move", exam_node)
        self.assertEqual(code, 0)

        code, reordered, _ = _run_json("folders", "reorder", "section:folders", exam_node, "专题乙", "专题甲")
        self.assertEqual(code, 0, reordered)
        self.assertEqual(reordered["sort_modes"]["section:folders"], "manual")
        self.assertEqual(reordered["doc_placements"][str(self.exam.pk)]["sortOrder"], 0)
        by_name = {f["name"]: f for f in reordered["user_folders"]}
        self.assertEqual(by_name["专题乙"]["sortOrder"], 10)
        self.assertEqual(by_name["专题甲"]["sortOrder"], 20)

        # Reject non-sibling item in reorder
        code, err_non, _ = _run_json("folders", "reorder", "section:folders", "不存在的文件夹")
        self.assertEqual(code, 1)
        self.assertIn("不属于目标父级", err_non["error"])

        # Custom tree toggle on book
        code, ct_on, _ = _run_json("folders", "custom-tree", str(self.book.pk)[:8], "on")
        self.assertEqual(code, 0, ct_on)
        self.assertIn(str(self.book.pk), ct_on["custom_tree_docs"])
        book_entry = [b for b in ct_on["tree"]["books"] if b["id"] == str(self.book.pk)][0]
        self.assertTrue(book_entry["custom_tree"])
        self.assertEqual(book_entry["sections"], [])

        code, ct_off, _ = _run_json("folders", "custom-tree", str(self.book.pk)[:8], "off")
        self.assertEqual(code, 0, ct_off)
        self.assertNotIn(str(self.book.pk), ct_off["custom_tree_docs"])

    def test_apply_tree_dry_run_id_stability_sync_sections_and_single_undo(self):
        book_node = f"book:{self.book.pk}"
        q1_short = library.short_id_for(self.q1.pk)
        tree_file = self.temp / "book_tree.json"
        tree_payload = [
            {
                "name": "第1篇 函数与不等式",
                "children": [
                    {
                        "name": "第1章 基本不等式",
                        "children": [
                            {
                                "name": "核心题型",
                                "children": [
                                    {"name": "最值问题", "children": [{"name": "例题组", "questions": [q1_short]}]}
                                ],
                            }
                        ],
                    },
                    {"name": "第2章 空章节保留"},
                ],
            }
        ]
        tree_file.write_text(json.dumps(tree_payload, ensure_ascii=False), encoding="utf-8")

        # 1. Dry run leaves state untouched
        code, dry_res, _ = _run_json(
            "folders", "apply-tree", "--file", str(tree_file), "--root", book_node, "--sync-sections", "--dry-run"
        )
        self.assertEqual(code, 0, dry_res)
        self.assertTrue(dry_res["dry_run"])
        self.assertEqual(dry_res["summary"]["folders_created"], 6)
        self.assertEqual(dry_res["summary"]["questions_assigned"], 1)
        self.assertEqual(dry_res["summary"]["sections_to_update"], 1)
        self.assertEqual(dry_res["summary"]["max_depth_used"], 7)  # book(2) + 5 levels = 7 <= 8
        self.assertEqual(library_folders.overview()["user_folders"], [])

        # 2. Real apply with --sync-sections
        code, applied, _ = _run_json(
            "folders", "apply-tree", "--file", str(tree_file), "--root", book_node, "--sync-sections"
        )
        self.assertEqual(code, 0, applied)
        self.assertIn(str(self.book.pk), applied["custom_tree_docs"])
        self.assertEqual(applied["sort_modes"][book_node], "manual")
        self.assertEqual(len(applied["user_folders"]), 6)

        # Verify section synced without creating a new PublishedQuestion version or losing approval
        self.q1.refresh_from_db()
        self.assertIn("第1篇 函数与不等式 / 第1章 基本不等式", self.q1.content["section"])
        self.assertEqual(PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).count(), 3)
        q1_card = Question.objects.get(pk=self.q1.question_id)
        self.assertTrue(library.approval_is_current(q1_card))

        # 3. Re-apply same tree preserves folder IDs (folders_updated=6, created=0)
        old_ids = {f["name"]: f["id"] for f in applied["user_folders"]}
        code, reapplied, _ = _run_json("folders", "apply-tree", "--file", str(tree_file), "--root", book_node)
        self.assertEqual(code, 0, reapplied)
        self.assertEqual(reapplied["summary"]["folders_updated"], 6)
        self.assertEqual(reapplied["summary"]["folders_created"], 0)
        new_ids = {f["name"]: f["id"] for f in reapplied["user_folders"]}
        self.assertEqual(old_ids, new_ids)

        # 4. Merge mode preserves unmentioned sibling
        merge_file = self.temp / "merge_tree.json"
        merge_file.write_text(
            json.dumps([{"name": "第2篇 新增篇", "questions": [str(self.q2.pk)]}], ensure_ascii=False), encoding="utf-8"
        )
        code, merged, _ = _run_json(
            "folders", "apply-tree", "--file", str(merge_file), "--root", book_node, "--mode", "merge"
        )
        self.assertEqual(code, 0, merged)
        self.assertEqual(len(merged["user_folders"]), 7)

        # Undo merge, undo reapplied, undo initial apply (which also rolls back section!)
        _run_json("folders", "undo")
        _run_json("folders", "undo")
        code, final_undo, _ = _run_json("folders", "undo")
        self.assertEqual(code, 0, final_undo)
        self.assertEqual(final_undo["user_folders"], [])
        self.assertNotIn(str(self.book.pk), final_undo["custom_tree_docs"])
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.content["section"], "例1")

    def test_mcp_apply_folder_tree_and_doc_tools(self):
        client = cli.Client(self.live_server_url, agent="测试MCP")
        reply = cli.mcp_handle(
            client,
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "apply_folder_tree",
                    "arguments": {
                        "root": "section:folders",
                        "tree": [
                            {
                                "name": "高一数学总库",
                                "docs": [f"book:{self.book.pk}"],
                                "children": [{"name": "必刷题", "questions": [library.short_id_for(self.q3.pk)]}],
                            }
                        ],
                    },
                },
            },
        )
        self.assertFalse(reply["result"]["isError"], reply)
        res = json.loads(reply["result"]["content"][0]["text"])
        root_uf = [f for f in res["user_folders"] if f["name"] == "高一数学总库"][0]
        self.assertEqual(res["doc_placements"][str(self.book.pk)]["parentId"], root_uf["id"])

        reply2 = cli.mcp_handle(
            client,
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "manage_folder",
                    "arguments": {
                        "action": "reorder",
                        "parent_id": root_uf["id"],
                        "order": [f"book:{self.book.pk}", "必刷题"],
                    },
                },
            },
        )
        self.assertFalse(reply2["result"]["isError"], reply2)
        res2 = json.loads(reply2["result"]["content"][0]["text"])
        self.assertEqual(res2["doc_placements"][str(self.book.pk)]["sortOrder"], 0)
        child_uf = [f for f in res2["user_folders"] if f["name"] == "必刷题"][0]
        self.assertEqual(child_uf["sortOrder"], 10)
