"""0029：只把目录版本落后的「完好」建议重新对齐，别把坏了的一起救回来。

0028 把分组转成章节父标签时抬了一格版本号，却没把还在等核对的建议一起抬上去，
439 条待核对建议因此集体变成「现在就能采纳 = 0」，用户一点采纳就撞报错。
判据必须和应用层 `_split_by_catalogue_damage` 一致：引用的标签还在就照样能采纳。
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path

from django.apps import apps as current_apps
from django.test import TestCase, override_settings

from core import library, library_browse, library_tagging
from core.models import (
    LibraryTag,
    LibraryTagAssignment,
    LibraryTagCatalogue,
    LibraryTagSuggestion,
    Paper,
    PublishedQuestion,
    Question,
)

migration_0029 = importlib.import_module("core.migrations.0029_realign_untouched_suggestions")


@override_settings(DATA_ROOT=Path(tempfile.gettempdir()))
class RealignUntouchedSuggestionsTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        setting = override_settings(DATA_ROOT=Path(self.temp.name))
        setting.enable()
        self.addCleanup(setting.disable)
        library_tagging.catalogue_state()
        self.paper = Paper.objects.create(filename="虚构卷.pdf", kind="pdf", sha256="b" * 64)
        self.serial = 0

    def publication(self):
        self.serial += 1
        stem = "虚构数学题 %s：已知三角形的内角和，求第三角。" % self.serial
        question = Question.objects.create(paper=self.paper, number=self.serial, question_type="single_choice",
                                           stem=stem, state="green")
        content = {"stem": stem, "options": {}, "answer": "", "analysis": "",
                   "figures": [], "sources": [], "origin": "虚构出处", "source_filename": "虚构卷.pdf",
                   "question_type": "single_choice"}
        return PublishedQuestion.objects.create(paper=self.paper, question=question, number=self.serial,
            source_filename="虚构卷.pdf", question_type="single_choice", version=1, status="published",
            review_source="human", content=content, content_hash=library.content_hash(content),
            search_text=library.search_key(stem), extras={})

    def tag(self, name):
        return LibraryTag.objects.create(name=name, kind="knowledge", levels=["高中"])

    def suggestion(self, publication, proposals, revision=None):
        return LibraryTagSuggestion.objects.create(publication=publication,
            question_fingerprint=library.generation_fingerprint(publication.content, publication.pk),
            taxonomy_revision=revision if revision is not None
            else LibraryTagCatalogue.objects.get(pk=1).revision,
            assignment_revision=publication.tag_revision, method_source={}, suggestions=proposals,
            status=LibraryTagSuggestion.Status.PENDING, message="逐条核对 AI 候选。",
            generation_source="隔离测试模型")

    def _touch_catalogue(self):
        """模拟 0028 干的事：重组层级树，版本号 +1，不动任何已有标签。"""
        catalogue = LibraryTagCatalogue.objects.get(pk=1)
        before = catalogue.revision
        catalogue.revision += 1
        catalogue.save(update_fields=["revision"])
        return before, catalogue.revision

    def test_a_tree_regrouping_leaves_every_untouched_proposal_actionable(self):
        publication = self.publication()
        tag = self.tag("三角形内角和")
        suggestion = self.suggestion(publication,
            [{"kind": "knowledge", "tag_id": str(tag.pk), "name": tag.name,
              "rationale": "题面直接考内角和。", "evidence": "题干", "scope": {"whole": True}}])
        self._touch_catalogue()
        # 迁移之前：目录版本抬了，建议没跟上，界面上「现在就能采纳」是 0。
        self.assertEqual(library_browse.tag_work_counts()["pending_actionable"], 0)
        self.assertEqual(library_browse.tag_work_counts()["outdated_suggestions"], 1)

        migration_0029.forwards(current_apps, None)

        suggestion.refresh_from_db()
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.PENDING)
        self.assertEqual(suggestion.taxonomy_revision, LibraryTagCatalogue.objects.get(pk=1).revision)
        work = library_browse.tag_work_counts()
        self.assertEqual(work["pending_actionable"], 1)
        self.assertEqual(work["outdated_suggestions"], 0)

    def test_a_proposal_whose_label_retired_stays_out_of_reach(self):
        publication = self.publication()
        alive = self.tag("还活着的知识点")
        retired = self.tag("已经停用的知识点")
        alive_suggestion = self.suggestion(publication,
            [{"kind": "knowledge", "tag_id": str(alive.pk), "name": alive.name, "scope": {"whole": True}}])
        broken_suggestion = self.suggestion(self.publication(),
            [{"kind": "knowledge", "tag_id": str(retired.pk), "name": retired.name, "scope": {"whole": True}}])
        retired.active = False
        retired.save(update_fields=["active"])
        self._touch_catalogue()

        migration_0029.forwards(current_apps, None)

        alive_suggestion.refresh_from_db()
        broken_suggestion.refresh_from_db()
        current = LibraryTagCatalogue.objects.get(pk=1).revision
        self.assertEqual(alive_suggestion.taxonomy_revision, current)
        # 引用的标签已经停用的，不能因为「顺手对齐」就变成能采纳的。
        self.assertNotEqual(broken_suggestion.taxonomy_revision, current)
        self.assertEqual(library_browse.tag_work_counts()["pending_actionable"], 1)

    def test_a_proposal_pointing_at_a_removed_parent_is_not_rescued_either(self):
        publication = self.publication()
        child = self.tag("子概念")
        gone_parent = self.tag("已经不存在的父概念")
        suggestion = self.suggestion(publication,
            [{"kind": "knowledge", "new_name": "新概念", "name": "新概念",
              "parent_ids": [str(gone_parent.pk)], "scope": {"whole": True}}])
        gone_parent.delete()
        self._touch_catalogue()

        migration_0029.forwards(current_apps, None)

        suggestion.refresh_from_db()
        self.assertNotEqual(suggestion.taxonomy_revision, LibraryTagCatalogue.objects.get(pk=1).revision)
        self.assertEqual(library_browse.tag_work_counts()["pending_actionable"], 0)

    def test_running_twice_changes_nothing(self):
        publication = self.publication()
        tag = self.tag("幂等检查用知识点")
        suggestion = self.suggestion(publication,
            [{"kind": "knowledge", "tag_id": str(tag.pk), "name": tag.name, "scope": {"whole": True}}])
        self._touch_catalogue()

        migration_0029.forwards(current_apps, None)
        first = LibraryTagSuggestion.objects.get(pk=suggestion.pk).taxonomy_revision
        migration_0029.forwards(current_apps, None)
        self.assertEqual(LibraryTagSuggestion.objects.get(pk=suggestion.pk).taxonomy_revision, first)
        self.assertFalse(LibraryTagAssignment.objects.filter(publication=publication).exists(),
                         "重新对齐只是把版本号追平，不应该顺手贴上任何标签")
