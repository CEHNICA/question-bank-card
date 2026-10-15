"""Formal catalogue, multi-tag filtering and human-reviewed AI proposals."""

from __future__ import annotations

import json
import importlib
import tempfile
import uuid
from pathlib import Path

from django.apps import apps as current_apps
from django.http import QueryDict
from django.test import Client, TestCase, override_settings
from django.urls import path

from core import library, library_browse, library_tagging
from core.library_tag_reports import legacy_tag_migration_rows
from core.models import (LibraryJob, LibraryTag, LibraryTagAssignment, LibraryTagCatalogue, LibraryTagGroup,
                         LibraryTagOperation,
                         LibraryTagSuggestion, Paper, PublishedQuestion, Question)

urlpatterns = [
    path("api/library/tags", library_tagging.catalogue_view),
    path("api/library/tags/templates", library_tagging.templates_view),
    path("api/library/tags/impact", library_tagging.impact_view),
    path("api/library/tags/undo", library_tagging.undo_view),
    path("api/library/tags/operations", library_tagging.operations_view),
    path("api/library/tags/suggestions", library_tagging.suggestions_view),
    path("api/library/tags/migration-report", library_tagging.migration_report_view),
    path("api/library/tags/undo", library_tagging.undo_view),
    path("api/library/tags/assignments/batch", library_tagging.batch_assignments_view),
    path("api/library/<uuid:publication_id>/tags", library_tagging.legacy_tags_view),
    path("api/library/tag-suggestions/<uuid:suggestion_id>/resolve", library_tagging.resolve_suggestion_view),
]


@override_settings(ROOT_URLCONF=__name__)
class FormalLibraryTaggingTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(DATA_ROOT=Path(self.temp.name))
        setting.enable()
        self.addCleanup(setting.disable)
        self.client = Client()
        library_tagging.catalogue_state()
        from core.tag_catalogue_seed import seed_catalogue
        seed_catalogue()
        self.paper = Paper.objects.create(filename="虚构卷.pdf", kind="pdf", sha256="a" * 64)
        self.serial = 0

    def publication(self):
        self.serial += 1
        stem = "虚构数学题 %s：已知二次函数图象，求其最值。" % self.serial
        question = Question.objects.create(paper=self.paper, number=self.serial, question_type="single_choice",
                                           stem=stem, state="green")
        content = {"stem": stem, "options": {}, "answer": "", "analysis": "",
                   "figures": [], "sources": [], "origin": "虚构出处", "source_filename": "虚构卷.pdf",
                   "question_type": "single_choice"}
        return PublishedQuestion.objects.create(paper=self.paper, question=question, number=self.serial,
            source_filename="虚构卷.pdf", question_type="single_choice", version=1, status="published",
            review_source="human", content=content, content_hash=library.content_hash(content),
            search_text=library.search_key(stem), extras={})

    def tag(self, name, kind="custom"):
        return LibraryTag.objects.create(name=name, kind=kind, levels=["高中"])

    def post_json(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def tag_request(self, publication, *, add=(), remove=(), request_id=None):
        return self.post_json("/api/library/tags/assignments/batch", {
            "publication_ids": [str(publication.pk)], "add": [str(tag.pk) for tag in add],
            "remove": [str(tag.pk) for tag in remove],
            "expected_revisions": {str(publication.pk): publication.tag_revision},
            "request_id": str(request_id or uuid.uuid4()),
        })

    def suggestion(self, publication, proposals):
        return LibraryTagSuggestion.objects.create(publication=publication,
            question_fingerprint=library.generation_fingerprint(publication.content, publication.pk),
            taxonomy_revision=LibraryTagCatalogue.objects.get(pk=1).revision,
            assignment_revision=publication.tag_revision, method_source={}, suggestions=proposals,
            status=LibraryTagSuggestion.Status.PENDING, message="逐条核对 AI 候选。", generation_source="隔离测试模型")

    def test_directory_has_curriculum_and_method_groups_with_stable_tags(self):
        body = self.client.get("/api/library/tags", HTTP_X_QB_REQUEST="1").json()
        self.assertGreater(len(body["groups"]), 10)
        self.assertTrue(any(tag["kind"] == "knowledge" for tag in body["tags"]))
        self.assertTrue(any(tag["kind"] == "method" for tag in body["tags"]))
        self.assertTrue(any(tag["name"] == "构造中位线" and tag["conditions"] for tag in body["tags"]))
        self.assertTrue(all("id" in tag for tag in body["tags"]))
        for name in ("函数图象", "平面直角坐标系", "统计图表"):
            shared = [tag for tag in body["tags"] if tag["name"] == name]
            self.assertEqual(len(shared), 1)
            self.assertEqual(set(shared[0]["levels"]), {"初中", "高中"})
            self.assertGreaterEqual(len(shared[0]["groups"]), 2)

    def test_blank_directory_stays_blank_until_explicit_template_import(self):
        LibraryTagAssignment.objects.all().delete()
        LibraryTag.objects.all().delete()
        from core.models import LibraryTagGroup
        LibraryTagGroup.objects.all().delete()
        state = LibraryTagCatalogue.objects.get(pk=1)
        state.initialized = False
        state.save(update_fields=["initialized"])
        before = LibraryTag.objects.count()
        templates = self.client.get("/api/library/tags/templates", HTTP_X_QB_REQUEST="1").json()
        self.assertGreater(len(templates["tags"]), 50)
        self.assertEqual(LibraryTag.objects.count(), before)
        blank = self.client.get("/api/library/tags", HTTP_X_QB_REQUEST="1").json()
        self.assertEqual(blank["tags"], [])
        self.assertEqual(blank["groups"], [])
        self.assertFalse(blank["initialized"])
        key = templates["packages"][0]["group_keys"][0]
        applied = self.post_json("/api/library/tags", {"action": "apply_template",
            "group_keys": [key], "revision": blank["revision"], "request_id": str(uuid.uuid4())})
        self.assertEqual(applied.status_code, 200, applied.content)
        self.assertTrue(applied.json()["catalogue"]["initialized"])
        imported = LibraryTag.objects.count()
        again = self.post_json("/api/library/tags", {"action": "apply_template",
            "group_keys": [key], "revision": applied.json()["revision"], "request_id": str(uuid.uuid4())})
        self.assertEqual(again.status_code, 200, again.content)
        self.assertEqual(LibraryTag.objects.count(), imported)

    def test_delete_tag_removes_all_links_and_guarded_undo_restores_them(self):
        publication = self.publication()
        historical = self.publication()
        historical.status = PublishedQuestion.Status.WITHDRAWN
        historical.save(update_fields=["status"])
        tag = self.tag("可撤销删除标签")
        self.assertEqual(self.tag_request(publication, add=[tag]).status_code, 200)
        LibraryTagAssignment.objects.create(publication=historical, tag=tag,
            source=LibraryTagAssignment.Source.HUMAN, scope={"whole": True})
        impact = self.client.get("/api/library/tags/impact?action=delete_tag&tag_id=" + str(tag.pk),
                                 HTTP_X_QB_REQUEST="1").json()
        self.assertEqual(impact["question_count"], 1)
        self.assertEqual(impact["historical_count"], 1)
        result = self.post_json("/api/library/tags", {"action": "delete_tag", "tag_id": str(tag.pk),
            "impact_token": impact["token"], "confirmed": True, "revision": impact["revision"],
            "request_id": str(uuid.uuid4())})
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(LibraryTag.objects.filter(pk=tag.pk).exists())
        self.assertFalse(LibraryTagAssignment.objects.filter(tag_id=tag.pk).exists())
        publication.refresh_from_db()
        self.assertEqual(publication.tags_text, "")
        undone = self.post_json("/api/library/tags/undo", {"operation_id": result.json()["operation_id"]})
        self.assertEqual(undone.status_code, 200, undone.content)
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag_id=tag.pk).exists())
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=historical, tag_id=tag.pk).exists())
        self.assertEqual(list(library_tagging.formal_tag_names(publication)), [tag.name])

    def test_batch_group_membership_is_atomic_idempotent_and_restores_mixed_membership(self):
        first, second = self.tag("批量分组甲"), self.tag("批量分组乙")
        group = LibraryTagGroup.objects.create(name="批量目标组")
        group.tags.add(first)
        publication = self.publication()
        self.assertEqual(self.tag_request(publication, add=[first]).status_code, 200)
        request = {"action": "group_membership_batch", "tag_ids": [str(first.pk), str(second.pk)],
            "group_id": str(group.pk), "mode": "add", "revision": library_tagging.catalogue_state().revision,
            "request_id": str(uuid.uuid4())}
        saved = self.post_json("/api/library/tags", request)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual(set(group.tags.values_list("id", flat=True)), {first.pk, second.pk})
        replay = self.post_json("/api/library/tags", request)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(LibraryTagOperation.objects.filter(client_request_id=request["request_id"]).count(), 1)
        changed_request = {**request, "mode": "remove"}
        self.assertEqual(self.post_json("/api/library/tags", changed_request).status_code, 409)
        publication.refresh_from_db()
        self.assertEqual(publication.tag_revision, 1)
        undo = self.post_json("/api/library/tags/undo", {"operation_id": saved.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertEqual(list(group.tags.values_list("id", flat=True)), [first.pk])
        remove = self.post_json("/api/library/tags", {**request, "mode": "remove",
            "revision": undo.json()["revision"], "request_id": str(uuid.uuid4())})
        self.assertEqual(remove.status_code, 200, remove.content)
        self.assertFalse(group.tags.exists())
        restored = self.post_json("/api/library/tags/undo", {"operation_id": remove.json()["operation_id"]})
        self.assertEqual(restored.status_code, 200, restored.content)
        self.assertEqual(list(group.tags.values_list("id", flat=True)), [first.pk])

    def test_batch_group_membership_rejects_missing_tag_without_partial_change(self):
        tag = self.tag("分组失败回滚")
        group = LibraryTagGroup.objects.create(name="失败目标组")
        revision = library_tagging.catalogue_state().revision
        result = self.post_json("/api/library/tags", {"action": "group_membership_batch",
            "tag_ids": [str(tag.pk), str(uuid.uuid4())], "group_id": str(group.pk), "mode": "add",
            "revision": revision, "request_id": str(uuid.uuid4())})
        self.assertEqual(result.status_code, 409, result.content)
        self.assertFalse(group.tags.exists())
        self.assertEqual(library_tagging.catalogue_state().revision, revision)

    def test_batch_delete_counts_unique_questions_and_restores_all_historical_links(self):
        publication, historical = self.publication(), self.publication()
        historical.status = PublishedQuestion.Status.WITHDRAWN
        historical.save(update_fields=["status"])
        first, second, keep = self.tag("批量删除甲"), self.tag("批量删除乙"), self.tag("保留标签")
        self.assertEqual(self.tag_request(publication, add=[first, second, keep]).status_code, 200)
        for tag in (first, second):
            LibraryTagAssignment.objects.create(publication=historical, tag=tag,
                source=LibraryTagAssignment.Source.AI_ACCEPTED, generation_source="虚构模型",
                source_snapshot={"original": tag.name})
        url = "/api/library/tags/impact?action=delete_tags&tag_ids=" + str(second.pk) + "," + str(first.pk)
        preview = self.client.get(url, HTTP_X_QB_REQUEST="1")
        self.assertEqual(preview.status_code, 200, preview.content)
        impact = preview.json()
        self.assertEqual((impact["question_count"], impact["historical_count"], impact["tag_count"]), (1, 1, 2))
        request = {"action": "delete_tags", "tag_ids": [str(first.pk), str(second.pk)],
            "impact_token": impact["token"], "confirmed": True, "revision": impact["revision"],
            "request_id": str(uuid.uuid4())}
        saved = self.post_json("/api/library/tags", request)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertFalse(LibraryTag.objects.filter(pk__in=[first.pk, second.pk]).exists())
        publication.refresh_from_db(); historical.refresh_from_db()
        self.assertEqual((publication.tag_revision, historical.tag_revision), (2, 1))
        self.assertEqual(publication.extras["tags"], [keep.name])
        replay = self.post_json("/api/library/tags", request)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()["replayed"])
        undo = self.post_json("/api/library/tags/undo", {"operation_id": saved.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertEqual(LibraryTagAssignment.objects.filter(tag_id__in=[first.pk, second.pk]).count(), 4)
        restored = LibraryTagAssignment.objects.get(publication=historical, tag_id=first.pk)
        self.assertEqual(restored.generation_source, "虚构模型")
        self.assertEqual(restored.source_snapshot, {"original": first.name})

    def test_batch_delete_old_preview_cannot_remove_new_assignments(self):
        publication = self.publication()
        first, second = self.tag("预览冲突甲"), self.tag("预览冲突乙")
        preview = library_tagging._impact_data("delete_tags", tag_ids=[str(first.pk), str(second.pk)])
        self.tag_request(publication, add=[second])
        response = self.post_json("/api/library/tags", {"action": "delete_tags",
            "tag_ids": [str(first.pk), str(second.pk)], "impact_token": preview["token"],
            "confirmed": True, "revision": preview["revision"], "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(LibraryTag.objects.filter(pk__in=[first.pk, second.pk]).count(), 2)
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag=second).exists())

    def test_batch_merge_removes_sources_and_undo_restores_provenance_and_versions(self):
        publication, historical = self.publication(), self.publication()
        historical.status = PublishedQuestion.Status.WITHDRAWN
        historical.save(update_fields=["status"])
        first, second, target = self.tag("合并方法甲", "method"), self.tag("合并方法乙", "method"), self.tag("合并目标", "method")
        first_group = LibraryTagGroup.objects.create(name="合并来源组")
        target_group = LibraryTagGroup.objects.create(name="合并目标组")
        first.groups.add(first_group); target.groups.add(target_group)
        original_ids = []
        for pub, tag, part in ((publication, first, "1"), (publication, second, "2"),
                               (publication, target, "3"), (historical, first, "1"), (historical, second, "2")):
            row = LibraryTagAssignment.objects.create(publication=pub, tag=tag, scope={"subquestions": [part]},
                source=LibraryTagAssignment.Source.AI_ACCEPTED, generation_source="虚构模型",
                rationale="已核对小问" + part, evidence="虚构解析" + part, source_snapshot={"part": part})
            original_ids.append(row.pk)
        url = "/api/library/tags/impact?action=merge_tags_batch&source_ids=" + str(first.pk) + "," + str(second.pk) + "&target_id=" + str(target.pk)
        preview = self.client.get(url, HTTP_X_QB_REQUEST="1")
        self.assertEqual(preview.status_code, 200, preview.content)
        impact = preview.json()
        self.assertEqual((impact["question_count"], impact["historical_count"]), (1, 1))
        request = {"action": "merge_tags_batch", "source_ids": [str(first.pk), str(second.pk)],
            "target_id": str(target.pk), "impact_token": impact["token"], "confirmed": True,
            "revision": impact["revision"], "request_id": str(uuid.uuid4())}
        saved = self.post_json("/api/library/tags", request)
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertFalse(LibraryTag.objects.filter(pk__in=[first.pk, second.pk]).exists())
        publication.refresh_from_db(); historical.refresh_from_db(); target.refresh_from_db()
        self.assertEqual((publication.tag_revision, historical.tag_revision), (1, 1))
        merged = LibraryTagAssignment.objects.get(publication=publication, tag=target)
        self.assertEqual(merged.scope, {"subquestions": ["1", "2", "3"]})
        self.assertEqual(merged.generation_source, "虚构模型")
        self.assertEqual(len(merged.source_snapshot["merged_assignments"]), 3)
        self.assertEqual(set(target.groups.values_list("id", flat=True)), {first_group.pk, target_group.pk})
        replay = self.post_json("/api/library/tags", request)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertTrue(replay.json()["replayed"])
        undo = self.post_json("/api/library/tags/undo", {"operation_id": saved.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertEqual(set(LibraryTagAssignment.objects.filter(publication__in=[publication, historical])
            .values_list("id", flat=True)), set(original_ids))
        self.assertFalse(LibraryTagAssignment.objects.filter(publication=historical, tag=target).exists())
        restored = LibraryTagAssignment.objects.get(publication=publication, tag=first)
        self.assertEqual((restored.scope, restored.source_snapshot), ({"subquestions": ["1"]}, {"part": "1"}))
        target.refresh_from_db()
        self.assertEqual(list(target.groups.values_list("id", flat=True)), [target_group.pk])
        self.assertEqual(target.aliases, [])

    def test_batch_merge_conflicts_on_changed_answer_and_undo_is_atomic(self):
        publication = self.publication()
        source, target = self.tag("合并冲突来源"), self.tag("合并冲突目标")
        self.tag_request(publication, add=[source])
        preview = library_tagging._impact_data("merge_tags_batch", source_ids=[str(source.pk)], target_id=str(target.pk))
        publication.refresh_from_db()
        publication.extras = {**publication.extras, "analysis": "虚构并发修改解析"}
        publication.save(update_fields=["extras"])
        request = {"action": "merge_tags_batch", "source_ids": [str(source.pk)], "target_id": str(target.pk),
            "impact_token": preview["token"], "confirmed": True, "revision": preview["revision"],
            "request_id": str(uuid.uuid4())}
        self.assertEqual(self.post_json("/api/library/tags", request).status_code, 409)
        self.assertTrue(LibraryTag.objects.filter(pk=source.pk).exists())
        refreshed = library_tagging._impact_data("merge_tags_batch", source_ids=[str(source.pk)], target_id=str(target.pk))
        saved = self.post_json("/api/library/tags", {**request, "impact_token": refreshed["token"]})
        self.assertEqual(saved.status_code, 200, saved.content)
        publication.refresh_from_db()
        publication.extras = {**publication.extras, "analysis": "虚构后续修改解析"}
        publication.save(update_fields=["extras"])
        revision = library_tagging.catalogue_state().revision
        undo = self.post_json("/api/library/tags/undo", {"operation_id": saved.json()["operation_id"]})
        self.assertEqual(undo.status_code, 409, undo.content)
        self.assertEqual(library_tagging.catalogue_state().revision, revision)
        self.assertFalse(LibraryTag.objects.filter(pk=source.pk).exists())
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag=target).exists())

    def test_batch_merge_invalid_type_and_methods_scope_overflow_roll_back(self):
        wrong, target = self.tag("不同类别来源", "knowledge"), self.tag("不同类别目标", "custom")
        response = self.client.get("/api/library/tags/impact?action=merge_tags_batch&source_ids="
            + str(wrong.pk) + "&target_id=" + str(target.pk), HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400, response.content)
        publication = self.publication()
        first, second, method_target = self.tag("范围甲", "method"), self.tag("范围乙", "method"), self.tag("范围目标", "method")
        LibraryTagAssignment.objects.create(publication=publication, tag=first,
            scope={"subquestions": [str(value) for value in range(1, 13)]})
        LibraryTagAssignment.objects.create(publication=publication, tag=second, scope={"subquestions": ["13"]})
        preview = library_tagging._impact_data("merge_tags_batch", source_ids=[str(first.pk), str(second.pk)], target_id=str(method_target.pk))
        saved = self.post_json("/api/library/tags", {"action": "merge_tags_batch",
            "source_ids": [str(first.pk), str(second.pk)], "target_id": str(method_target.pk),
            "impact_token": preview["token"], "confirmed": True, "revision": preview["revision"],
            "request_id": str(uuid.uuid4())})
        self.assertEqual(saved.status_code, 400, saved.content)
        self.assertEqual(library_tagging.catalogue_state().revision, preview["revision"])
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=publication).count(), 2)
        publication.refresh_from_db()
        self.assertEqual(publication.tag_revision, 0)

    def test_suggestion_history_can_be_filtered_by_question_and_paginated(self):
        publication = self.publication()
        self.suggestion(publication, [{"kind": "knowledge", "new_name": "候选一", "name": "候选一",
            "rationale": "依据", "evidence": "题面", "scope": {"whole": True}}])
        response = self.client.get("/api/library/tags/suggestions?publication_id=" + str(publication.pk)
            + "&status=all&limit=1&offset=0", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["items"]), 1)
        self.assertFalse(body["has_more"])

    def test_group_delete_keeps_tag_and_question_assignment(self):
        publication = self.publication()
        from core.models import LibraryTagGroup
        group = LibraryTagGroup.objects.create(name="仅组织的分组")
        tag = self.tag("分组删除保留标签")
        tag.groups.add(group)
        self.tag_request(publication, add=[tag])
        response = self.post_json("/api/library/tags", {"action": "delete_group", "group_id": str(group.pk),
            "revision": LibraryTagCatalogue.objects.get(pk=1).revision, "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(LibraryTag.objects.filter(pk=tag.pk).exists())
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag=tag).exists())
        undo = self.post_json("/api/library/tags/undo", {"operation_id": response.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertTrue(tag.groups.filter(pk=group.pk).exists())

    def test_reset_empty_stales_tag_suggestions_and_is_undoable_without_restarting_them(self):
        publication = self.publication()
        tag = self.tag("清空后可恢复")
        self.tag_request(publication, add=[tag])
        suggestion = self.suggestion(publication, [{"kind": "knowledge", "tag_id": str(tag.pk),
            "name": tag.name, "rationale": "测试", "evidence": "测试", "scope": {"whole": True}}])
        job = LibraryJob.objects.create(publication=publication, kind=LibraryJob.Kind.TAGS,
            executor=LibraryJob.Executor.API, tag_scope=True, status=LibraryJob.Status.QUEUED,
            fingerprint=library.generation_fingerprint(publication.content, publication.pk))
        suggestion.job = job
        suggestion.status = LibraryTagSuggestion.Status.QUEUED
        suggestion.save(update_fields=["job", "status", "updated_at"])
        impact = self.client.get("/api/library/tags/impact?action=reset_empty", HTTP_X_QB_REQUEST="1").json()
        cleared = self.post_json("/api/library/tags", {"action": "reset_empty", "impact_token": impact["token"],
            "confirmed": True, "revision": impact["revision"], "request_id": str(uuid.uuid4())})
        self.assertEqual(cleared.status_code, 200, cleared.content)
        self.assertEqual(LibraryTag.objects.count(), 0)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.STALE)
        job.refresh_from_db()
        self.assertEqual(job.status, LibraryJob.Status.FAILED)
        self.assertEqual(self.client.get("/api/library/tags", HTTP_X_QB_REQUEST="1").json()["tags"], [])
        undo = self.post_json("/api/library/tags/undo", {"operation_id": cleared.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag_id=tag.pk).exists())
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.STALE)

    def test_manual_tags_have_no_three_tag_limit_and_do_not_change_question_version(self):
        publication = self.publication()
        tags = [self.tag("隔离自定义标签%s" % number) for number in range(4)]
        before = (publication.version, publication.content_hash, publication.status, publication.review_source)
        result = self.tag_request(publication, add=tags).json()
        publication.refresh_from_db()
        self.assertEqual(len(publication.tag_assignments.filter(needs_review=False)), 4)
        self.assertEqual((publication.version, publication.content_hash, publication.status, publication.review_source), before)
        self.assertEqual(set(publication.extras["tags"]), {tag.name for tag in tags})
        self.assertEqual(result["revisions"][str(publication.pk)], 1)

    def test_manual_method_tags_keep_the_selected_subquestion_scope(self):
        publication = self.publication(); method = self.tag("隔离方法", "method")
        response = self.post_json("/api/library/tags/assignments/batch", {
            "publication_ids": [str(publication.pk)], "add": [str(method.pk)], "remove": [],
            "scopes": {str(method.pk): {"subquestions": ["1", "3"]}},
            "expected_revisions": {str(publication.pk): 0}, "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(LibraryTagAssignment.objects.get(publication=publication, tag=method).scope,
                         {"subquestions": ["1", "3"]})
        knowledge = self.tag("知识点范围必须为整题", "knowledge")
        invalid = self.post_json("/api/library/tags/assignments/batch", {
            "publication_ids": [str(publication.pk)], "add": [str(knowledge.pk)], "remove": [],
            "scopes": {str(knowledge.pk): {"subquestions": ["2"]}},
            "expected_revisions": {str(publication.pk): 1}, "request_id": str(uuid.uuid4())})
        self.assertEqual(invalid.status_code, 400)

    def test_tag_color_is_persisted_and_undoable(self):
        state = library_tagging.catalogue_state()
        result = library_tagging.catalogue_mutation({"action": "create_tag", "name": "彩色标签", "kind": "custom",
            "color": "#cc5577", "revision": state.revision, "request_id": str(uuid.uuid4())})
        tag = LibraryTag.objects.get(name="彩色标签")
        self.assertEqual(tag.color, "#cc5577")
        self.assertEqual(next(row for row in result["catalogue"]["tags"] if row["id"] == str(tag.pk))["color"], "#cc5577")
        operation = library_tagging.LibraryTagOperation.objects.get(pk=result["operation_id"])
        library_tagging.undo_catalogue_operation(operation)
        self.assertFalse(LibraryTag.objects.filter(pk=tag.pk).exists())

    def test_only_proposals_pointing_at_a_retired_label_are_swept(self):
        state = library_tagging.catalogue_state()
        kept, doomed = self.tag("仍然在用的标签"), self.tag("被停用的标签")
        survivor = self.suggestion(self.publication(), [{"tag_id": str(kept.pk), "name": kept.name}])
        casualty = self.suggestion(self.publication(), [{"tag_id": str(doomed.pk), "name": doomed.name}])
        library_tagging.catalogue_mutation({"action": "create_tag", "name": "顺手新建的标签", "kind": "custom",
            "revision": state.revision, "request_id": str(uuid.uuid4())})
        after_add = library_tagging.catalogue_state().revision
        self.assertGreater(after_add, state.revision)
        for proposal in (survivor, casualty):
            proposal.refresh_from_db()
            self.assertEqual(proposal.status, LibraryTagSuggestion.Status.PENDING)
            self.assertEqual(proposal.taxonomy_revision, after_add)
        library_tagging.catalogue_mutation({"action": "deactivate_tag", "tag_id": str(doomed.pk),
            "revision": after_add, "request_id": str(uuid.uuid4())})
        survivor.refresh_from_db(); casualty.refresh_from_db()
        self.assertEqual(survivor.status, LibraryTagSuggestion.Status.PENDING)
        self.assertEqual(survivor.taxonomy_revision, library_tagging.catalogue_state().revision)
        self.assertEqual(casualty.status, LibraryTagSuggestion.Status.STALE)

    def test_batch_undo_refuses_to_overwrite_a_later_edit(self):
        first, second = self.publication(), self.publication()
        a, b = self.tag("批量甲"), self.tag("批量乙")
        request = {"publication_ids": [str(first.pk), str(second.pk)], "add": [str(a.pk)], "remove": [],
            "expected_revisions": {str(first.pk): 0, str(second.pk): 0}, "request_id": str(uuid.uuid4())}
        saved = self.post_json("/api/library/tags/assignments/batch", request).json()
        self.assertEqual(LibraryTagAssignment.objects.filter(tag=a).count(), 2)
        second.refresh_from_db()
        self.tag_request(second, add=[b])
        conflict = self.post_json("/api/library/tags/undo", {"operation_id": saved["operation_id"]})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(LibraryTagAssignment.objects.filter(tag=a).count(), 2)
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=second, tag=b).exists())

    def test_batch_undo_restores_all_selected_questions(self):
        first, second = self.publication(), self.publication()
        tag = self.tag("批量撤销")
        request = {"publication_ids": [str(first.pk), str(second.pk)], "add": [str(tag.pk)], "remove": [],
            "expected_revisions": {str(first.pk): 0, str(second.pk): 0}, "request_id": str(uuid.uuid4())}
        saved = self.post_json("/api/library/tags/assignments/batch", request).json()
        undo = self.post_json("/api/library/tags/undo", {"operation_id": saved["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertFalse(LibraryTagAssignment.objects.filter(tag=tag).exists())

    def test_old_name_endpoint_requires_revision_and_accepts_more_than_three_tags(self):
        publication = self.publication()
        tags = list(LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE, active=True)[:4])
        no_revision = self.post_json("/api/library/%s/tags" % publication.pk, {"tags": [tag.name for tag in tags]})
        self.assertEqual(no_revision.status_code, 409)
        saved = self.post_json("/api/library/%s/tags" % publication.pk,
            {"tags": [tag.name for tag in tags], "revision": publication.tag_revision})
        self.assertEqual(saved.status_code, 200)
        publication.refresh_from_db()
        self.assertEqual(len(publication.tag_assignments.filter(needs_review=False)), 4)

    def test_legacy_migration_maps_only_clear_names_and_is_repeatable(self):
        human = self.publication()
        human.extras = {"tags": ["二次函数的最值"], "tags_source": "human"}
        original_content = dict(human.content)
        original_version = human.version
        human.save(update_fields=["extras"])

        broad = self.publication()
        broad.extras = {"tags": ["函数"], "tags_source": "human"}
        broad.save(update_fields=["extras"])

        unknown = self.publication()
        unknown.extras = {"tags": ["来源不明旧词"]}
        unknown.save(update_fields=["extras"])

        ai = self.publication()
        ai.extras = {"tags": ["二次函数的最值"], "tags_source": "旧版视觉模型", "tags_executor": "api",
                     "tags_checked": False, "tags_fingerprint": "old-fingerprint"}
        ai.save(update_fields=["extras"])

        migration = importlib.import_module("core.migrations.0022_library_tags_v2")
        migration.seed_and_migrate_tags(current_apps, None)

        human.refresh_from_db(); broad.refresh_from_db(); unknown.refresh_from_db(); ai.refresh_from_db()
        known = LibraryTag.objects.get(kind=LibraryTag.Kind.KNOWLEDGE, name="二次函数的最值")
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=human, tag=known,
            source=LibraryTagAssignment.Source.LEGACY_HUMAN, needs_review=False).exists())
        self.assertEqual(human.content, original_content)
        self.assertEqual(human.version, original_version)
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=broad, tag__kind=LibraryTag.Kind.LEGACY,
            tag__name="函数", needs_review=False).exists())
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=unknown, tag__kind=LibraryTag.Kind.LEGACY,
            tag__name="来源不明旧词", source=LibraryTagAssignment.Source.LEGACY_UNKNOWN, needs_review=True).exists())
        self.assertFalse(ai.tag_assignments.exists())
        self.assertEqual(ai.extras["tags"], [])
        self.assertIn("tags_legacy_original", ai.extras)
        proposal = LibraryTagSuggestion.objects.get(publication=ai)
        self.assertEqual(proposal.status, LibraryTagSuggestion.Status.PENDING)
        self.assertEqual(proposal.suggestions[0]["tag_id"], str(known.pk))
        assignments_before = LibraryTagAssignment.objects.count()
        suggestions_before = LibraryTagSuggestion.objects.count()

        migration.seed_and_migrate_tags(current_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.count(), assignments_before)
        self.assertEqual(LibraryTagSuggestion.objects.count(), suggestions_before)

    def test_filters_support_all_any_exclusion_and_do_not_count_review_candidates(self):
        first, second, third = self.publication(), self.publication(), self.publication()
        a, b, c = self.tag("篩選甲"), self.tag("篩選乙"), self.tag("篩選丙")
        self.tag_request(first, add=[a, b]); self.tag_request(second, add=[a]); self.tag_request(third, add=[b, c])
        params = QueryDict("", mutable=True); params["tag_all"] = str(a.pk) + "," + str(b.pk)
        self.assertEqual(set(library_browse.library_rows(params).values_list("id", flat=True)), {first.pk})
        params = QueryDict("", mutable=True); params["tag_any"] = str(a.pk) + "," + str(b.pk); params["tag_not"] = str(c.pk)
        self.assertEqual(set(library_browse.library_rows(params).values_list("id", flat=True)), {first.pk, second.pk})
        candidate = self.suggestion(second, [{"kind": "knowledge", "tag_id": str(c.pk), "name": c.name}])
        self.assertNotIn(c.name, second.extras.get("tags", []))
        params = QueryDict("tag_review=true")
        self.assertEqual(set(library_browse.library_rows(params).values_list("id", flat=True)), {second.pk})

    def test_ai_candidate_is_not_formal_until_accepted_and_acceptance_keeps_existing_human_tag(self):
        publication = self.publication()
        human, proposed = self.tag("人工保留"), self.tag("AI 候选", "knowledge")
        self.tag_request(publication, add=[human]); publication.refresh_from_db()
        suggestion = self.suggestion(publication, [{"kind": "knowledge", "tag_id": str(proposed.pk), "name": proposed.name,
            "rationale": "題干询问顶点最值。", "evidence": "题干给出二次函数图象", "scope": {"whole": True}}])
        self.assertEqual(set(publication.extras["tags"]), {human.name})
        response = self.post_json("/api/library/tag-suggestions/%s/resolve" % suggestion.pk,
            {"action": "accept", "indices": [0], "expected_revision": publication.tag_revision, "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.content)
        links = {row.tag_id: row for row in LibraryTagAssignment.objects.filter(publication=publication)}
        self.assertEqual(links[human.pk].source, LibraryTagAssignment.Source.HUMAN)
        self.assertEqual(links[proposed.pk].source, LibraryTagAssignment.Source.AI_ACCEPTED)
        self.assertEqual(set(PublishedQuestion.objects.get(pk=publication.pk).extras["tags"]), {human.name, proposed.name})
        undone = self.post_json("/api/library/tags/undo", {"operation_id": response.json()["operation_id"]})
        self.assertEqual(undone.status_code, 200, undone.content)
        self.assertEqual(set(LibraryTagAssignment.objects.filter(publication=publication).values_list("tag__name", flat=True)), {human.name})
        suggestion.refresh_from_db(); self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.PENDING)

    def test_catalogue_change_persists_stale_state_when_acceptance_is_rejected(self):
        publication = self.publication()
        existing = self.tag("已经存在的正式标签")
        self.tag_request(publication, add=[existing])
        publication.refresh_from_db()
        tag = self.tag("目录变化后的旧候选", "knowledge")
        suggestion = self.suggestion(publication, [{"kind": "knowledge", "tag_id": str(tag.pk),
            "name": tag.name, "rationale": "测试理由", "evidence": "测试依据", "scope": {"whole": True}}])
        catalogue = LibraryTagCatalogue.objects.get(pk=1)
        catalogue.revision += 1
        catalogue.save(update_fields=["revision"])

        response = self.post_json("/api/library/tag-suggestions/%s/resolve" % suggestion.pk,
            {"action": "accept", "indices": [0], "expected_revision": publication.tag_revision,
             "request_id": str(uuid.uuid4())})

        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["stale"])
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.STALE)
        self.assertIn("标签目录已变化", suggestion.message)
        self.assertFalse(LibraryTagAssignment.objects.filter(publication=publication, tag=tag).exists())
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag=existing).exists())
        self.assertNotIn(publication.pk,
            library_browse.library_rows(QueryDict("tag_review=true")).values_list("id", flat=True))

        history = self.client.get("/api/library/tags/suggestions?publication_id=%s&status=all" % publication.pk,
                                  HTTP_X_QB_REQUEST="1").json()
        self.assertEqual(history["items"][0]["status"], "stale")

    def test_tag_work_counts_keep_the_outdated_backlog_out_of_the_actionable_number(self):
        tag = self.tag("统计用知识点", "knowledge")
        outdated = self.suggestion(self.publication(), [{"kind": "knowledge", "tag_id": str(tag.pk), "name": tag.name}])
        catalogue = LibraryTagCatalogue.objects.get(pk=1)
        catalogue.revision += 1
        catalogue.save(update_fields=["revision"])
        reachable = self.suggestion(self.publication(), [{"kind": "knowledge", "tag_id": str(tag.pk), "name": tag.name}])
        self.assertEqual(outdated.taxonomy_revision + 1, reachable.taxonomy_revision)

        work = library_browse.tag_work_counts()
        self.assertEqual(work["pending_review"], 2)
        self.assertEqual(work["pending_actionable"], 1)
        self.assertEqual(work["outdated_suggestions"], 1)

        # 题目核对状态也算一件待办，但和 AI 建议是同一个键，不能把同一道题数两次。
        LibraryTagAssignment.objects.filter(publication_id=reachable.publication_id).update(needs_review=True)
        self.assertEqual(library_browse.tag_work_counts()["pending_review"], 2)

        # 题目后来改过标签的，也不能算「现在就能采纳」，否则数字又变回骗人。
        retagged = PublishedQuestion.objects.get(pk=reachable.publication_id)
        retagged.tag_revision += 1
        retagged.save(update_fields=["tag_revision"])
        work = library_browse.tag_work_counts()
        self.assertEqual(work["pending_review"], 2)
        self.assertEqual(work["pending_actionable"], 0)
        self.assertEqual(work["outdated_suggestions"], 2)

    def test_accepting_a_proposal_naming_a_retired_label_reinstates_it_and_undo_puts_it_back(self):
        retired = self.tag("旧目录里的椭圆", "legacy")
        retired.active = False
        retired.levels = []
        retired.save(update_fields=["active", "levels"])
        publication = self.publication()
        suggestion = self.suggestion(publication, [{"kind": "knowledge", "new_name": retired.name,
            "name": retired.name, "levels": ["高中"], "scope": {"whole": True},
            "rationale": "题面给出椭圆定义。", "evidence": "题干中的标准方程"}])
        survivor = self.suggestion(self.publication(), [{"kind": "knowledge", "tag_id": str(retired.pk),
            "name": retired.name, "rationale": "另一道题也用它。", "evidence": "另一处题面"}])

        # 界面上先要看见「这个标签你的库里本来就有」，否则还是会被当成全新标签。
        served = library_tagging.suggestion_json(suggestion)["suggestions"][0]
        self.assertEqual(served["retired_tag"]["id"], str(retired.pk))
        self.assertFalse(served["retired_tag"]["active"])

        base = {"action": "accept", "indices": [0], "expected_revision": publication.tag_revision,
                "request_id": str(uuid.uuid4())}
        without = self.post_json("/api/library/tag-suggestions/%s/resolve" % suggestion.pk, dict(base))
        self.assertEqual(without.status_code, 400, without.content)
        self.assertIn("学段", without.json()["error"])
        retired.refresh_from_db()
        self.assertFalse(retired.active)

        response = self.post_json("/api/library/tag-suggestions/%s/resolve" % suggestion.pk,
                                  dict(base, request_id=str(uuid.uuid4()), new_tags={"0": {"levels": ["初中", "高中"]}}))
        self.assertEqual(response.status_code, 200, response.content)
        retired.refresh_from_db()
        self.assertTrue(retired.active)
        self.assertEqual(retired.kind, "knowledge")
        self.assertEqual(retired.levels, ["初中", "高中"])
        self.assertTrue(publication.tag_assignments.filter(tag=retired).exists())

        # 转正一个旧标签不能把整队待核对建议带走，否则第一笔采纳就把剩下的全作废。
        survivor.refresh_from_db()
        self.assertEqual(survivor.status, LibraryTagSuggestion.Status.PENDING)

        undo = self.post_json("/api/library/tags/undo", {"operation_id": response.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        retired.refresh_from_db()
        self.assertFalse(retired.active)
        self.assertEqual(retired.kind, "legacy")
        self.assertEqual(retired.levels, [])
        self.assertFalse(publication.tag_assignments.filter(tag=retired).exists())

    def test_new_ai_tag_is_created_only_when_human_accepts_and_can_be_undone(self):
        publication = self.publication()
        group = LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE).first().groups.first()
        proposal = {"kind": "knowledge", "new_name": "新概念候选", "name": "新概念候选", "levels": ["高中"],
            "rationale": "题目使用该概念。", "evidence": "题干中的条件", "scope": {"whole": True}}
        suggestion = self.suggestion(publication, [proposal])
        self.assertFalse(LibraryTag.objects.filter(name="新概念候选").exists())
        response = self.post_json("/api/library/tag-suggestions/%s/resolve" % suggestion.pk,
            {"action": "accept", "indices": [0], "expected_revision": 0, "new_tags": {"0": {"name": "新概念候选", "kind": "knowledge", "levels": ["高中"], "group_ids": [str(group.pk)], "top_level": True}}, "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.content)
        created = LibraryTag.objects.get(name="新概念候选")
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag=created).exists())
        self.assertTrue(created.groups.filter(pk=group.pk).exists())
        undo = self.post_json("/api/library/tags/undo", {"operation_id": response.json()["operation_id"]})
        self.assertEqual(undo.status_code, 200, undo.content)
        self.assertFalse(LibraryTag.objects.filter(pk=created.pk).exists())
        self.assertEqual(PublishedQuestion.objects.get(pk=publication.pk).tag_assignments.count(), 0)

    def test_partial_ai_acceptance_persists_revision_for_remaining_review(self):
        publication = self.publication()
        accepted_tag = self.tag("部分采纳的知识点", "knowledge")
        rejected_tag = self.tag("随后拒绝的知识点", "knowledge")
        proposals = [{"kind": "knowledge", "tag_id": str(tag.pk), "name": tag.name,
            "rationale": "合成题面中的条件支持该候选。", "evidence": "仅用于部分核对回归测试。",
            "scope": {"whole": True}} for tag in (accepted_tag, rejected_tag)]
        suggestion = self.suggestion(publication, proposals)
        endpoint = "/api/library/tag-suggestions/%s/resolve" % suggestion.pk

        accepted = self.post_json(endpoint, {"action": "accept", "indices": [0],
            "expected_revision": publication.tag_revision, "request_id": str(uuid.uuid4())})
        self.assertEqual(accepted.status_code, 200, accepted.content)
        publication.refresh_from_db()
        suggestion.refresh_from_db()
        self.assertEqual(publication.tag_revision, 1)
        self.assertEqual(suggestion.assignment_revision, publication.tag_revision)
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.PENDING)
        self.assertEqual(suggestion.suggestions[0]["decision"], "accepted")
        self.assertNotIn("decision", suggestion.suggestions[1])
        accepted_link = LibraryTagAssignment.objects.get(publication=publication, tag=accepted_tag)
        self.assertEqual(accepted_link.source, LibraryTagAssignment.Source.AI_ACCEPTED)

        rejected = self.post_json(endpoint, {"action": "reject", "indices": [1],
            "expected_revision": publication.tag_revision, "request_id": str(uuid.uuid4())})
        self.assertEqual(rejected.status_code, 200, rejected.content)
        publication.refresh_from_db()
        suggestion.refresh_from_db()
        self.assertEqual(publication.tag_revision, 2)
        self.assertEqual(suggestion.assignment_revision, publication.tag_revision)
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.ACCEPTED)
        self.assertEqual([entry["decision"] for entry in suggestion.suggestions], ["accepted", "rejected"])
        self.assertEqual(LibraryTagAssignment.objects.get(publication=publication, tag=accepted_tag).pk,
                         accepted_link.pk)
        self.assertFalse(LibraryTagAssignment.objects.filter(publication=publication, tag=rejected_tag).exists())
        self.assertEqual(publication.extras["tags"], [accepted_tag.name])

    def test_legacy_ai_candidate_can_be_edited_and_accepted_without_formalizing_early(self):
        publication = self.publication()
        suggestion = self.suggestion(publication, [{"kind": "knowledge", "name": "旧宽泛词", "new_name": "旧宽泛词",
            "legacy_name": "旧宽泛词", "legacy": True, "levels": ["初中", "高中"], "rationale": "旧版候选待核对。",
            "evidence": "请回原题核对。", "scope": {"whole": True}}])
        response = self.post_json("/api/library/tag-suggestions/%s/resolve" % suggestion.pk,
            {"action": "accept", "indices": [0], "expected_revision": 0,
             "new_tags": {"0": {"name": "细化后的知识点", "kind": "knowledge", "levels": ["高中"], "group_ids": [], "top_level": True}},
             "request_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(LibraryTagAssignment.objects.filter(publication=publication, tag__name="细化后的知识点", needs_review=False).exists())
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.suggestions[0]["decision"], "accepted")

    def test_legacy_merge_promotes_review_relation_and_undo_restores_both_sides(self):
        publication = self.publication()
        old = LibraryTag.objects.create(name="历史宽泛词", kind=LibraryTag.Kind.LEGACY, active=False)
        target = self.tag("人工确认目标", "knowledge")
        LibraryTagAssignment.objects.create(publication=publication, tag=old, needs_review=True,
            source=LibraryTagAssignment.Source.LEGACY_UNKNOWN, source_snapshot={"legacy_name": old.name})
        LibraryTagAssignment.objects.create(publication=publication, tag=target, needs_review=False,
            source=LibraryTagAssignment.Source.HUMAN, rationale="原有人工标签")
        state = library_tagging.catalogue_state()
        result = library_tagging.catalogue_mutation({"action": "merge_tags", "source_id": str(old.pk),
            "target_id": str(target.pk), "revision": state.revision, "request_id": str(uuid.uuid4())})
        link = LibraryTagAssignment.objects.get(publication=publication, tag=target)
        self.assertFalse(link.needs_review)
        self.assertEqual(link.source, LibraryTagAssignment.Source.LEGACY_HUMAN)
        operation = library_tagging.LibraryTagOperation.objects.get(pk=result["operation_id"])
        library_tagging.undo_catalogue_operation(operation)
        self.assertTrue(LibraryTagAssignment.objects.get(publication=publication, tag=old).needs_review)
        restored_target = LibraryTagAssignment.objects.get(publication=publication, tag=target)
        self.assertEqual(restored_target.source, LibraryTagAssignment.Source.HUMAN)
        self.assertEqual(restored_target.rationale, "原有人工标签")

    def test_legacy_report_lists_only_tag_names_and_statuses(self):
        publication = self.publication()
        legacy = LibraryTag.objects.create(name="未归类旧词", kind=LibraryTag.Kind.LEGACY, active=False)
        LibraryTagAssignment.objects.create(publication=publication, tag=legacy,
            source=LibraryTagAssignment.Source.LEGACY_UNKNOWN, needs_review=True,
            source_snapshot={"legacy_name": "未归类旧词"})
        rows = legacy_tag_migration_rows()
        self.assertIn(("未归类旧词", "未归类旧词", "历史标签／待人工整理", 1), rows)
        report = self.client.get("/api/library/tags/migration-report", HTTP_X_QB_REQUEST="1")
        self.assertEqual(report.status_code, 200)
        self.assertIn("未归类旧词", report.content.decode("utf-8"))
        self.assertNotIn("虚构数学题", report.content.decode("utf-8"))

    def test_ai_validator_requires_explicit_evidence_and_valid_method_scope(self):
        knowledge = LibraryTag.objects.filter(kind=LibraryTag.Kind.KNOWLEDGE, active=True).first()
        good = {"status": "ok", "suggestions": [{"kind": "knowledge", "tag_id": str(knowledge.pk),
            "rationale": "与题目目标对应。", "evidence": "题干要求求最值。", "scope": {"whole": True}}]}
        self.assertEqual(len(library_tagging.normalize_ai_output(good, [knowledge])["suggestions"]), 1)
        bad = json.loads(json.dumps(good)); bad["suggestions"][0]["evidence"] = ""
        with self.assertRaises(library_tagging.TaggingError): library_tagging.normalize_ai_output(bad, [knowledge])
        bad = json.loads(json.dumps(good)); bad["suggestions"][0]["scope"] = {"whole": False}
        with self.assertRaises(library_tagging.TaggingError): library_tagging.normalize_ai_output(bad, [knowledge])

    def test_ai_reproposing_an_active_tag_by_name_binds_it_and_asks_for_no_parent(self):
        existing = self.tag("椭圆", "knowledge")
        raw = {"status": "ok", "suggestions": [{"kind": "knowledge", "new_name": "椭圆",
            "levels": ["高中"], "scope": {"whole": True}, "rationale": "题面给出椭圆定义。", "evidence": "题干中的标准方程"}]}
        item = library_tagging.normalize_ai_output(raw, [existing])["suggestions"][0]
        self.assertEqual(item["tag_id"], str(existing.pk))
        self.assertEqual(item["name"], existing.name)
        self.assertNotIn("new_name", item)
        self.assertNotIn("parent_ids", item)
        spaced = json.loads(json.dumps(raw))
        spaced["suggestions"][0]["new_name"] = "  椭 圆  "
        self.assertEqual(library_tagging.normalize_ai_output(spaced, [existing])["suggestions"][0]["tag_id"],
                         str(existing.pk))

    def test_an_inactive_legacy_tag_is_not_silently_bound_as_a_new_label(self):
        legacy = self.tag("抛物线", "legacy")
        legacy.active = False
        legacy.save(update_fields=["active"])
        raw = {"status": "ok", "suggestions": [{"kind": "knowledge", "new_name": "抛物线",
            "levels": ["高中"], "scope": {"whole": True}, "rationale": "题面出现抛物线。", "evidence": "题干中的抛物线"}]}
        # Promoting an inactive legacy record is an unresolved product decision, so the
        # proposal must keep falling through to the new-tag branch instead of binding it.
        with self.assertRaises(library_tagging.TaggingError):
            library_tagging.normalize_ai_output(raw, [legacy])

    def test_read_only_picker_lists_aliases_and_tag_usage_count(self):
        publication = self.publication(); tag = self.tag("别名统计测试")
        tag.aliases = ["统计别名"]; tag.save(update_fields=["aliases"])
        self.tag_request(publication, add=[tag])
        body = self.client.get("/api/library/tags", HTTP_X_QB_REQUEST="1").json()
        found = next(item for item in body["tags"] if item["id"] == str(tag.pk))
        self.assertEqual(found["aliases"], ["统计别名"])
        self.assertEqual(found["count"], 1)
