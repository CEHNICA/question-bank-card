"""Isolated fictional data for tag ancestry and atomic per-question corrections."""
import json
import uuid
from unittest.mock import patch

from django.http import QueryDict
from django.test import TestCase

from core import library, library_browse, library_tagging
from core.models import (LibraryTag, LibraryTagAssignment, LibraryTagRelation,
                         LibraryTagSuggestion, LibraryTagOperation, Paper, Question, PublishedQuestion)


class LibraryTagHierarchyTests(TestCase):
    def setUp(self):
        library_tagging.catalogue_state()
        self.paper = Paper.objects.create(filename="虚构分级卷.pdf", kind="pdf", sha256="d" * 64)
        self.number = 0

    def tag(self, name, kind="knowledge"):
        return LibraryTag.objects.create(name=name, kind=kind, levels=["高中"])

    def publication(self):
        self.number += 1
        question = Question.objects.create(paper=self.paper, number=self.number, stem="虚构题", state="green")
        content = {"stem": "虚构题", "options": {}, "figures": [], "answer": "", "analysis": "",
                   "source_filename": self.paper.filename, "question_type": "single_choice", "origin": "", "sources": []}
        return PublishedQuestion.objects.create(question=question, paper=self.paper, source_filename=self.paper.filename,
            number=self.number, question_type="single_choice", version=1, content=content,
            content_hash=library.content_hash(content), status="published")

    def mutate(self, action, **values):
        return library_tagging.catalogue_mutation({"action": action, "revision": library_tagging.catalogue_state().revision,
            "request_id": str(uuid.uuid4()), **values})

    def parents(self, child, *parents):
        return self.mutate("set_parents", tag_id=str(child.pk), parent_ids=[str(tag.pk) for tag in parents])

    def relation(self, parent, child, kind="hierarchy"):
        return LibraryTagRelation.objects.create(parent=parent, child=child, relation_type=kind)

    def link(self, publication, tag, **values):
        return LibraryTagAssignment.objects.create(publication=publication, tag=tag, scope=values.pop("scope", {"whole": True}), **values)

    def selected(self, **filters):
        values = QueryDict("", mutable=True)
        values.update(filters)
        return set(library_browse.library_rows(values).values_list("pk", flat=True))

    def replacement(self, publication, source, target):
        return {"publication_ids": [str(publication.pk)], "replace": {"from_tag_id": str(source.pk), "to_tag_id": str(target.pk)},
            "expected_revisions": {str(publication.pk): publication.tag_revision},
            "expected_catalogue_revision": library_tagging.catalogue_state().revision, "request_id": str(uuid.uuid4())}

    def post(self, url, payload):
        return self.client.post(url, json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_multi_parent_cycle_and_kind_rollback(self):
        a, b, c = self.tag("函数"), self.tag("方程"), self.tag("零点")
        self.parents(c, a, b)
        revision = library_tagging.catalogue_state().revision
        with self.assertRaises(library_tagging.TaggingError):
            self.parents(a, c)
        self.assertEqual(library_tagging.catalogue_state().revision, revision)
        self.assertEqual(LibraryTagRelation.objects.count(), 2)

    def test_a_tag_can_hang_under_a_tag_of_another_kind(self):
        """1.26.27：上下级不再限同一类。老师的标签是他自己的一棵树，
        「分类讨论」既是知识点也是解题方法 —— 硬按类型分家只会让他在两个地方各贴一次。"""
        knowledge, method = self.tag("分类讨论"), self.tag("分类讨论法", "method")
        self.parents(method, knowledge)
        self.assertEqual(
            set(LibraryTagRelation.objects.filter(child=method).values_list("parent_id", flat=True)),
            {knowledge.pk})
        row = next(tag for tag in library_tagging._catalogue_rows()["tags"] if tag["id"] == str(method.pk))
        self.assertEqual(row["parent_ids"], [str(knowledge.pk)], "目录里也要看得见这个上级")
        # 环还是要拦住的 —— 跟类型无关。
        with self.assertRaises(library_tagging.TaggingError):
            self.parents(knowledge, method)

    def test_relation_neighbors_create_move_remove_undo(self):
        a, b = self.tag("三角形"), self.tag("圆")
        result = self.mutate("create_tag", name="角", kind="knowledge", parent_ids=[str(a.pk)])
        child = LibraryTag.objects.get(name="角")
        moved = self.mutate("move_parent", tag_id=str(child.pk), from_parent_id=str(a.pk), to_parent_id=str(b.pk))
        response = self.client.get(f"/api/library/tags/{child.pk}/relations", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["parents"][0]["id"], str(b.pk))
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=moved["operation_id"]))
        self.assertEqual(LibraryTagRelation.objects.get().parent_id, a.pk)
        removed = self.mutate("remove_parent", tag_id=str(child.pk), parent_id=str(a.pk))
        self.assertFalse(LibraryTagRelation.objects.exists())
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=removed["operation_id"]))
        self.assertEqual(LibraryTagRelation.objects.get().parent_id, a.pk)
        self.assertIsNotNone(result["operation_id"])

    def test_sibling_shares_one_parent_and_can_be_undone(self):
        """1.26.24：题卡上把一颗标签拖到另一颗上面时，除了「成为子标签」，
        还能选「并列」——两颗共享同一个上级。并列靠的就是 set_parents
        （把上级整组换掉），所以空上级（挪到最外层）和撤销都得验。"""
        root, left, right = self.tag("几何"), self.tag("全等"), self.tag("相似")
        self.parents(left, root)
        self.parents(right, root)
        self.assertEqual(set(left.incoming_relations.values_list("parent_id", flat=True)), {root.pk})
        self.assertEqual(set(right.incoming_relations.values_list("parent_id", flat=True)), {root.pk})
        # 并列到最外层：上级整组换成空的（不是「不传」）。
        moved = self.parents(left)
        self.assertFalse(left.incoming_relations.exists())
        self.assertEqual(set(right.incoming_relations.values_list("parent_id", flat=True)), {root.pk})
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=moved["operation_id"]))
        self.assertEqual(set(left.incoming_relations.values_list("parent_id", flat=True)), {root.pk})
        self.assertEqual(set(right.incoming_relations.values_list("parent_id", flat=True)), {root.pk})
        # 把上级换成自己的后代 → 成环，整组不落库。
        child = self.tag("全等判定")
        self.parents(child, left)
        with self.assertRaises(library_tagging.TaggingError):
            self.parents(left, child)
        self.assertEqual(set(left.incoming_relations.values_list("parent_id", flat=True)), {root.pk})

    def test_related_method_never_inherits_and_all_nodes_can_be_attached(self):
        root, child, method = self.tag("平面几何"), self.tag("中位线"), self.tag("添辅助线", "method")
        self.parents(child, root)
        self.mutate("set_related_methods", tag_id=str(root.pk), related_method_ids=[str(method.pk)])
        p1, p2, p3 = self.publication(), self.publication(), self.publication()
        self.link(p1, root); self.link(p2, child); self.link(p3, method)
        self.assertEqual(self.selected(tag_all=str(root.pk)), {p1.pk, p2.pk})
        self.assertEqual(self.selected(tag_all=str(child.pk)), {p2.pk})
        row = next(tag for tag in library_tagging._catalogue_rows()["tags"] if tag["id"] == str(root.pk))
        self.assertEqual((row["direct_count"], row["subtree_count"]), (1, 2))
        self.assertEqual(row["related_method_ids"], [str(method.pk)])

    def test_descendant_filters_and_unique_counts(self):
        root, left, right, other = self.tag("函数"), self.tag("单调"), self.tag("奇偶"), self.tag("几何")
        self.parents(left, root); self.parents(right, root)
        a, b = self.publication(), self.publication()
        self.link(a, left); self.link(a, right); self.link(a, other); self.link(b, root)
        self.assertEqual(self.selected(tag_all=f"{root.pk},{other.pk}"), {a.pk})
        self.assertEqual(self.selected(tag_any=f"{root.pk},{other.pk}"), {a.pk, b.pk})
        self.assertEqual(self.selected(tag_not=str(left.pk)), {b.pk})
        self.assertEqual(self.selected(tag_not=str(root.pk)), set())
        counts = library_browse.facet_counts(QueryDict(""))["tags"]
        self.assertEqual(next(row for row in counts if row["id"] == str(root.pk))["count"], 2)

    def test_pending_tags_do_not_match_exclusion_or_missing_knowledge(self):
        p = self.publication()
        pending, custom = self.tag("未核对知识"), self.tag("收藏题", "custom")
        self.link(p, pending, needs_review=True); self.link(p, custom)
        self.assertEqual(self.selected(tag_not=str(pending.pk)), {p.pk})
        self.assertEqual(self.selected(untagged_knowledge="true"), {p.pk})

    def test_delete_and_reset_restore_relations_and_assignment_identity(self):
        a, b, c = self.tag("几何"), self.tag("四边形"), self.tag("菱形")
        self.parents(b, a); self.parents(c, b)
        p = self.publication(); link = self.link(p, b)
        impact = library_tagging._impact_data("delete_tag", b.pk)
        result = self.mutate("delete_tag", tag_id=str(b.pk), impact_token=impact["token"], confirmed=True)
        self.assertTrue(LibraryTag.objects.filter(pk=c.pk).exists())
        self.assertFalse(LibraryTagRelation.objects.exists())
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=result["operation_id"]))
        self.assertEqual(LibraryTagRelation.objects.count(), 2)
        self.assertTrue(LibraryTagAssignment.objects.filter(pk=link.pk).exists())
        impact = library_tagging._impact_data("reset_empty")
        result = self.mutate("reset_empty", impact_token=impact["token"], confirmed=True)
        self.assertFalse(LibraryTagRelation.objects.exists())
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=result["operation_id"]))
        self.assertEqual(LibraryTagRelation.objects.count(), 2)

    def test_merge_redirects_relations_and_undo_restores(self):
        root, source, target, child = self.tag("函数"), self.tag("旧函数"), self.tag("新函数"), self.tag("最值")
        self.parents(source, root); self.parents(child, source)
        p = self.publication(); self.link(p, source)
        impact = library_tagging._impact_data("merge_tags_batch", source_ids=[str(source.pk)], target_id=str(target.pk))
        result = self.mutate("merge_tags_batch", source_ids=[str(source.pk)], target_id=str(target.pk),
            impact_token=impact["token"], confirmed=True)
        self.assertEqual(library_tagging.descendant_ids([root.pk]), {str(root.pk), str(target.pk), str(child.pk)})
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=result["operation_id"]))
        self.assertEqual(library_tagging.descendant_ids([root.pk]), {str(root.pk), str(source.pk), str(child.pk)})

    def test_merge_that_would_create_cycle_rolls_back(self):
        a, b, c = self.tag("甲"), self.tag("乙"), self.tag("丙")
        self.parents(b, a); self.parents(c, b)
        impact = library_tagging._impact_data("merge_tags_batch", source_ids=[str(a.pk)], target_id=str(c.pk))
        with self.assertRaises(library_tagging.TaggingError):
            self.mutate("merge_tags_batch", source_ids=[str(a.pk)], target_id=str(c.pk), impact_token=impact["token"], confirmed=True)
        self.assertEqual(LibraryTag.objects.count(), 3)
        self.assertEqual(LibraryTagRelation.objects.count(), 2)

    def test_replace_is_one_operation_with_retry_and_undo(self):
        source, target = self.tag("函数"), self.tag("二次函数")
        p = self.publication()
        self.link(p, source, source="ai_accepted", generation_source="虚构模型", rationale="旧理由")
        payload = self.replacement(p, source, target)
        result = library_tagging.assign(payload)
        retry = library_tagging.assign(payload)
        self.assertTrue(retry["replayed"])
        self.assertEqual(LibraryTagOperation.objects.filter(action="replace").count(), 1)
        link = p.tag_assignments.get()
        self.assertEqual(link.tag_id, target.pk)
        self.assertEqual(link.source, "human")
        self.assertEqual(link.source_snapshot["replaced_assignments"][0]["source"], "ai_accepted")
        response = self.post("/api/library/tags/undo", {"operation_id": result["operation_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(p.tag_assignments.get().source, "ai_accepted")
        self.assertEqual(p.tag_assignments.get().rationale, "旧理由")

    def test_replace_method_unions_scopes_and_preserves_target(self):
        source, target = self.tag("辅助线", "method"), self.tag("坐标法", "method")
        p = self.publication()
        self.link(p, source, scope={"subquestions": ["1"]})
        old_target = self.link(p, target, scope={"subquestions": ["2"]}, source="ai_accepted", rationale="第二问依据")
        library_tagging.assign(self.replacement(p, source, target))
        link = p.tag_assignments.get()
        self.assertEqual(link.pk, old_target.pk)
        self.assertEqual(link.scope, {"subquestions": ["1", "2"]})
        self.assertEqual(link.rationale, "")
        self.assertEqual(link.source_snapshot["target_before_replacement"]["rationale"], "第二问依据")
        self.assertEqual(link.source_snapshot["target_before_replacement"]["scope"], {"subquestions": ["2"]})

    def test_replace_method_whole_scope_and_limit(self):
        a, b = self.tag("方法甲", "method"), self.tag("方法乙", "method")
        p = self.publication(); self.link(p, a); self.link(p, b, scope={"subquestions": ["1"]})
        library_tagging.assign(self.replacement(p, a, b))
        self.assertEqual(p.tag_assignments.get().scope, {"whole": True})
        p2 = self.publication()
        self.link(p2, a, scope={"subquestions": [str(n) for n in range(1, 8)]})
        self.link(p2, b, scope={"subquestions": [str(n) for n in range(8, 14)]})
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.assign(self.replacement(p2, a, b))
        self.assertEqual(p2.tag_assignments.count(), 2)

    def test_replace_pending_target_or_wrong_kind_rejected(self):
        a, b, method = self.tag("甲"), self.tag("乙"), self.tag("方法", "method")
        p = self.publication(); self.link(p, a); self.link(p, b, needs_review=True)
        for target in (b, method):
            with self.assertRaises(library_tagging.TaggingError):
                library_tagging.assign(self.replacement(p, a, target))
        self.assertEqual(p.tag_assignments.count(), 2)

    def test_replace_stale_catalogue_source_missing_noop_and_request_lookup(self):
        a, b = self.tag("甲"), self.tag("乙")
        p = self.publication(); self.link(p, a)
        payload = self.replacement(p, a, b)
        self.parents(b, a)
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.assign(payload)
        noop = self.replacement(p, a, a)
        self.assertTrue(library_tagging.assign(noop)["noop"])
        p.refresh_from_db(); self.assertEqual(p.tag_revision, 0)
        response = self.client.get("/api/library/tags/operations", {"request_id": noop["request_id"]}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.json()["operations"][0]["revisions"], {str(p.pk): 0})
        source_missing = self.replacement(p, b, a)
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.assign(source_missing)

    def test_replace_undo_conflict_and_no_implicit_ai(self):
        a, b, c = self.tag("甲"), self.tag("乙"), self.tag("丙")
        p = self.publication(); self.link(p, a)
        operation = library_tagging.assign(self.replacement(p, a, b))
        p.refresh_from_db()
        library_tagging.assign(self.replacement(p, b, c))
        response = self.post("/api/library/tags/undo", {"operation_id": operation["operation_id"]})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(p.jobs.exists())

    def test_new_ai_candidates_validate_parents_and_preserve_snapshot(self):
        a, method = self.tag("函数"), self.tag("换元", "method")
        raw = {"status": "ok", "suggestions": [{"kind": "knowledge", "new_name": "二次函数最值",
            "levels": ["高中"], "parent_ids": [str(a.pk)], "scope": {"whole": True}, "rationale": "虚构理由", "evidence": "虚构题面"}]}
        normalized = library_tagging.normalize_ai_output(raw, [a, method])
        self.assertEqual(normalized["suggestions"][0]["parent_ids"], [str(a.pk)])
        p = self.publication()
        suggestion = LibraryTagSuggestion.objects.create(publication=p, taxonomy_revision=library_tagging.catalogue_state().revision,
            assignment_revision=0, suggestions=normalized["suggestions"], catalogue_snapshot={"tags": [{"id": str(a.pk), "name": "函数"}]})
        library_tagging.resolve_suggestion(suggestion.pk, {"action": "accept", "indices": [0], "expected_revision": 0,
            "request_id": str(uuid.uuid4())})
        self.assertEqual(p.tag_assignments.get().tag.incoming_relations.get().parent_id, a.pk)
        self.assertEqual(library_tagging.suggestion_json(suggestion)["catalogue_snapshot"]["tags"][0]["name"], "函数")
        raw["suggestions"][0]["parent_ids"] = [str(method.pk)]
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.normalize_ai_output(raw, [a, method])
        raw["suggestions"][0]["parent_ids"] = []
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.normalize_ai_output(raw, [a])
        self.assertEqual(library_tagging.normalize_ai_output(raw, [])["suggestions"][0]["parent_ids"], [])

    def test_batch_filter_probe_uses_descendants_and_current_matches(self):
        a, b = self.tag("函数"), self.tag("二次函数")
        self.parents(b, a)
        p1, p2 = self.publication(), self.publication()
        self.link(p1, b)
        response = self.post("/api/library/batch", {"ids": [str(p1.pk), str(p2.pk)], "filters": {"tag_all": str(a.pk)}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["matches"], {str(p1.pk): True, str(p2.pk): False})
        self.assertEqual(response.json()["total"], 1)

    def test_hierarchy_edit_keeps_pending_ai_reviewable_and_can_be_undone(self):
        a, b = self.tag("函数"), self.tag("二次函数")
        p = self.publication()
        revision = library_tagging.catalogue_state().revision
        suggestion = LibraryTagSuggestion.objects.create(publication=p, taxonomy_revision=revision,
            status="pending", suggestions=[{"tag_id": str(a.pk), "kind": "knowledge"}])
        result = self.parents(b, a)
        moved = library_tagging.catalogue_state().revision
        self.assertGreater(moved, revision)
        suggestion.refresh_from_db(); self.assertEqual(suggestion.status, "pending")
        self.assertEqual(suggestion.taxonomy_revision, moved)
        library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=result["operation_id"]))
        self.assertFalse(LibraryTagRelation.objects.exists())
        suggestion.refresh_from_db(); self.assertEqual(suggestion.status, "pending")
        self.assertEqual(suggestion.taxonomy_revision, library_tagging.catalogue_state().revision)

    def test_new_ai_candidate_missing_parent_retains_pending_and_no_creation(self):
        self.tag("函数")
        p = self.publication()
        proposal = {"kind": "knowledge", "new_name": "新候选", "name": "新候选", "levels": ["高中"],
            "scope": {"whole": True}, "rationale": "虚构理由", "evidence": "虚构条件"}
        suggestion = LibraryTagSuggestion.objects.create(publication=p, status="pending", assignment_revision=0,
            taxonomy_revision=library_tagging.catalogue_state().revision, suggestions=[proposal])
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.resolve_suggestion(suggestion.pk, {"action": "accept", "indices": [0],
                "expected_revision": 0, "request_id": str(uuid.uuid4())})
        suggestion.refresh_from_db(); p.refresh_from_db()
        self.assertEqual(suggestion.status, "pending")
        self.assertNotIn("decision", suggestion.suggestions[0])
        self.assertFalse(LibraryTag.objects.filter(name="新候选").exists())
        self.assertEqual(p.tag_revision, 0)

    def test_template_dependencies_are_imported_and_repeat_preserves_user_hierarchy(self):
        blueprint = {"sections": [{"key": "root", "name": "根章节", "kind": "knowledge", "position": 0, "color": ""},
                                  {"key": "child", "name": "细章节", "kind": "knowledge", "position": 1, "color": ""}],
            "tags": [{"key": "a", "name": "模板函数", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["root"], "parent_keys": []},
                      {"key": "b", "name": "模板最值", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["child"], "parent_keys": ["a"]}]}
        # The fake blueprint carries its own parent table so the test does not
        # depend on the real curriculum's shape.
        with patch("core.tag_catalogue_seed.template_blueprint", return_value=blueprint), \
                patch("core.tag_catalogue_seed.blueprint_parents", return_value={"a": ["root"], "b": ["a", "child"]}):
            result = self.mutate("apply_template", section_keys=["child"])
            # 细章节 + 模板函数（依赖上级）+ 模板最值。未选的根章节不会被顺手建出来。
            self.assertEqual(set(LibraryTag.objects.values_list("system_key", flat=True)),
                             {"child", "a", "b"})
            self.assertEqual(LibraryTagRelation.objects.count(), 2)
            operation = LibraryTagOperation.objects.get(pk=result["operation_id"])
            self.assertEqual(operation.after_snapshot["dependency_tag_count"], 1)
            library_tagging.undo_catalogue_operation(operation)
            self.assertFalse(LibraryTag.objects.exists())
            self.assertFalse(LibraryTagRelation.objects.exists())
            self.mutate("apply_template", section_keys=["child"])
            child = LibraryTag.objects.get(system_key="b")
            own = self.tag("我自己加的上级")
            self.parents(child, own)
            self.mutate("apply_template", section_keys=["child"])
            self.assertEqual(set(child.incoming_relations.values_list("parent_id", flat=True)),
                             {own.pk, LibraryTag.objects.get(system_key="a").pk,
                              LibraryTag.objects.get(system_key="child").pk})

    def test_chapter_without_a_concept_parent_hangs_the_tag_directly(self):
        blueprint = {"sections": [{"key": "g", "name": "模板章节", "kind": "knowledge", "position": 0, "color": ""}],
            "tags": [{"key": "a", "name": "模板函数", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["g"], "parent_keys": []}]}
        with patch("core.tag_catalogue_seed.template_blueprint", return_value=blueprint), \
                patch("core.tag_catalogue_seed.blueprint_parents", return_value={"a": ["g"]}):
            self.mutate("apply_template", section_keys=["g"])
        section = LibraryTag.objects.get(system_key="g")
        leaf = LibraryTag.objects.get(system_key="a")
        self.assertEqual(section.kind, "knowledge")
        self.assertEqual(section.description, "教材章节，用来给标签目录分层。")
        self.assertEqual(list(leaf.incoming_relations.values_list("parent_id", flat=True)), [section.pk])

    def test_explicit_template_augments_flat_identity_without_overwriting_user_fields(self):
        old = self.tag("我修改的概念")
        old.system_key = "b"; old.save()
        own = self.tag("我自己加的上级")
        self.parents(old, own)
        p = self.publication(); original = self.link(p, old)
        blueprint = {"sections": [{"key": "g", "name": "模板章节", "kind": "knowledge", "position": 0, "color": ""}],
            "tags": [{"key": "a", "name": "模板函数", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["g"], "parent_keys": []},
                      {"key": "b", "name": "系统原概念", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["g"], "parent_keys": ["a"]}]}
        with patch("core.tag_catalogue_seed.template_blueprint", return_value=blueprint), \
                patch("core.tag_catalogue_seed.blueprint_parents", return_value={"a": ["g"], "b": ["a"]}):
            self.mutate("apply_template", section_keys=["g"])
            self.mutate("apply_template", section_keys=["g"])
        old.refresh_from_db()
        self.assertEqual(old.name, "我修改的概念")
        self.assertEqual(set(old.incoming_relations.values_list("parent_id", flat=True)),
                         {own.pk, LibraryTag.objects.get(system_key="a").pk})
        self.assertEqual(p.tag_assignments.get().pk, original.pk)

    def test_empty_directory_accepts_multiple_new_candidates_in_one_transaction(self):
        p = self.publication()
        values = {"status": "ok", "suggestions": [{"kind": "knowledge", "new_name": name,
            "levels": ["高中"], "parent_ids": [], "scope": {"whole": True},
            "rationale": "虚构理由", "evidence": "虚构题面"} for name in ("概念甲", "概念乙")]}
        normalized = library_tagging.normalize_ai_output(values, [])
        suggestion = LibraryTagSuggestion.objects.create(publication=p, status="pending", assignment_revision=0,
            taxonomy_revision=library_tagging.catalogue_state().revision, suggestions=normalized["suggestions"])
        result = library_tagging.resolve_suggestion(suggestion.pk, {"action": "accept", "indices": [0, 1],
            "expected_revision": 0, "request_id": str(uuid.uuid4())})
        self.assertEqual(LibraryTag.objects.count(), 2)
        self.assertEqual(p.tag_assignments.count(), 2)
        self.assertFalse(LibraryTagRelation.objects.exists())
        response = self.post("/api/library/tags/undo", {"operation_id": result["operation_id"]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(LibraryTag.objects.exists())

    def test_processed_method_does_not_block_remaining_knowledge_after_solution_changes(self):
        knowledge, method = self.tag("函数"), self.tag("构造函数", "method")
        p = self.publication(); self.link(p, method)
        suggestion = LibraryTagSuggestion.objects.create(publication=p, status="pending", assignment_revision=0,
            taxonomy_revision=library_tagging.catalogue_state().revision, method_source={"solution_id": str(uuid.uuid4())},
            suggestions=[{"kind": "method", "tag_id": str(method.pk), "decision": "accepted"},
                         {"kind": "knowledge", "tag_id": str(knowledge.pk), "scope": {"whole": True}, "rationale": "虚构", "evidence": "虚构"}])
        library_tagging.resolve_suggestion(suggestion.pk, {"action": "accept", "indices": [1],
            "expected_revision": 0, "request_id": str(uuid.uuid4())})
        self.assertTrue(p.tag_assignments.filter(tag=knowledge).exists())
        suggestion.refresh_from_db(); self.assertEqual(suggestion.status, "accepted")

    def test_manual_template_identity_binding_undo_and_repeat_after_rename(self):
        old = self.tag("模板最值")
        p = self.publication(); link = self.link(p, old)
        blueprint = {"sections": [{"key": "g", "name": "模板章节", "kind": "knowledge", "position": 0, "color": ""}],
            "tags": [{"key": "a", "name": "模板函数", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["g"], "parent_keys": []},
                      {"key": "b", "name": "模板最值", "kind": "knowledge", "description": "", "levels": ["高中"],
                       "aliases": [], "section_keys": ["g"], "parent_keys": ["a"]}]}
        with patch("core.tag_catalogue_seed.template_blueprint", return_value=blueprint), \
                patch("core.tag_catalogue_seed.blueprint_parents", return_value={"a": ["g"], "b": ["a"]}):
            result = self.mutate("apply_template", section_keys=["g"])
            old.refresh_from_db(); self.assertEqual(old.system_key, "b")
            library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=result["operation_id"]))
            old.refresh_from_db(); self.assertIsNone(old.system_key)
            self.assertEqual(p.tag_assignments.get().pk, link.pk)
            self.assertEqual(LibraryTag.objects.count(), 1)
            result = self.mutate("apply_template", section_keys=["g"])
            self.mutate("update_tag", tag_id=str(old.pk), name="我改过名的最值")
            self.mutate("apply_template", section_keys=["g"])
            self.assertEqual(LibraryTag.objects.count(), 3)
            self.assertEqual(LibraryTag.objects.get(system_key="b").pk, old.pk)
            with self.assertRaises(library_tagging.TaggingError):
                library_tagging.undo_catalogue_operation(LibraryTagOperation.objects.get(pk=result["operation_id"]))
