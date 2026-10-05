"""知识点标签能改、能删、能重打。

标签打错以前没有出���：题面上的标签只能看不能动，「打知识点标签」按钮只在
没有标签时出现。撤回重录是唯一出路。这里钉住三条：

1. 改标签只动 extras，不新建版本、不重新审核（题面是题面，标签是标签）。
2. 人工选的词必须在知识点目录里，上限 3 个，重复的只算一个。
3. 清空标签后，「打知识点标签」按钮的条件自动成立——不需要新的接口。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import Client, TestCase, override_settings
from django.urls import path
from django.utils import timezone

from core import knowledge, library, library_tags
from core.models import Paper, PublishedQuestion, Question

urlpatterns = [
    path("api/library/<uuid:publication_id>/tags", library_tags.tags_view),
]


@override_settings(ROOT_URLCONF=__name__)
class LibraryTagsEditTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        settings = override_settings(DATA_ROOT=Path(self.folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.client = Client()
        self.paper = Paper.objects.create(filename="甲卷.pdf", kind="pdf", sha256="a" * 64)
        self.question = Question.objects.create(paper=self.paper, number=1, question_type="single_choice",
                                                stem="虚构题：若 $x+1=3$，求 $x$。", state="green")
        self.serial = 0
        self.item = self.publication()

    def publication(self, *, tags=()):
        self.serial += 1
        stem = f"虚构题 {self.serial}：若 $x+1=3$，求 $x$。"
        content = {"stem": stem, "options": {}, "answer": "", "analysis": "", "figures": [],
                   "origin": "虚构出处", "source_filename": "甲卷.pdf", "document_id": str(self.paper.id),
                   "question_type": "single_choice", "sources": []}
        question = Question.objects.create(paper=self.paper, number=self.serial,
                                           question_type="single_choice", stem=stem, state="green")
        return PublishedQuestion.objects.create(
            paper=self.paper, question=question, number=self.serial, source_filename="甲卷.pdf",
            question_type="single_choice", version=1, status="published", review_source="human",
            content=content, content_hash=library.content_hash(content), search_text=library.search_key(stem),
            extras={"tags": list(tags)}, tags_text=library.tags_text(tags))

    def post_tags(self, item, tags, *, header=True, content_type="application/json", raw=None):
        body = raw if raw is not None else json.dumps({"tags": tags})
        return self.client.post(f"/api/library/{item.id}/tags", data=body, content_type=content_type,
                                **({"HTTP_X_QB_REQUEST": "1"} if header else {}))

    def reload(self, item):
        return PublishedQuestion.objects.get(pk=item.pk)

    def test_editing_tags_keeps_the_same_version_and_approval(self):
        before = self.reload(self.item)
        body = self.post_tags(self.item, ["集合间的基本关系"]).json()
        self.assertTrue(body["ok"])
        after = self.reload(self.item)
        self.assertEqual((after.id, after.version, after.status, after.review_source),
                         (before.id, before.version, "published", "human"))
        self.assertEqual(after.content, before.content)
        self.assertEqual(library.tags_of(after.extras), ["集合间的基本关系"])
        self.assertEqual(after.extras["tags_source"], "human")
        self.assertEqual(PublishedQuestion.objects.count(), 1)

    def test_saved_tags_drive_the_filter_index_and_search_text(self):
        self.post_tags(self.item, ["函数的基本性质", "导数的运算"])
        after = self.reload(self.item)
        self.assertEqual(after.tags_text, library.tags_text(["函数的基本性质", "导数的运算"]))
        self.assertIn("函数的基本性质", after.search_text)
        self.assertIn("导数的运算", after.search_text)

    def test_clearing_tags_leaves_no_trace_and_reopens_the_generate_button(self):
        self.post_tags(self.item, ["集合"])
        cleared = self.post_tags(self.item, []).json()
        self.assertEqual(cleared["tags"], [])
        after = self.reload(self.item)
        self.assertEqual(library.tags_of(after.extras), [])
        self.assertEqual(after.tags_text, "")
        self.assertNotIn("tags_source", after.extras)
        self.assertNotIn("tags_fingerprint", after.extras)
        self.assertNotIn("tags", after.extras)
        # library.js 只在「没有标签」时渲染「打知识点标签」——清空即重新可打。
        self.assertFalse(library.tags_of(after.extras))

    def test_tags_outside_the_catalogue_are_rejected_and_change_nothing(self):
        self.post_tags(self.item, ["集合的概念"])
        response = self.post_tags(self.item, ["集合的概念", "自创的词"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("知识点目录", response.json()["error"])
        self.assertEqual(library.tags_of(self.reload(self.item).extras), ["集合的概念"])

    def test_duplicates_collapse_and_the_third_tag_is_the_ceiling(self):
        self.post_tags(self.item, ["集合的概念", "集合的概念", " 集合的概念 "])
        self.assertEqual(library.tags_of(self.reload(self.item).extras), ["集合的概念"])
        response = self.post_tags(self.item, ["集合的概念", "函数的概念及其表示", "导数的概念及其意义", "随机抽样"])
        self.assertEqual(response.status_code, 400)
        self.assertIn(str(knowledge.MAX_TAGS), response.json()["error"])
        self.assertEqual(library.tags_of(self.reload(self.item).extras), ["集合的概念"])

    def test_get_returns_the_catalogue_the_editor_chooses_from(self):
        body = self.client.get(f"/api/library/{self.item.id}/tags").json()
        self.assertEqual(body["tags"], [])
        self.assertEqual(body["max"], knowledge.MAX_TAGS)
        self.assertIn("函数的概念及其表示", [item["point"] for item in body["catalogue"]])

    def test_unreadable_body_does_not_read_as_clear_every_tag(self):
        self.post_tags(self.item, ["集合的概念"])
        response = self.client.post(f"/api/library/{self.item.id}/tags", data="{oops",
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(library.tags_of(self.reload(self.item).extras), ["集合的概念"])

    def test_requests_without_the_local_page_header_are_refused(self):
        self.assertEqual(self.post_tags(self.item, ["集合的概念"], header=False).status_code, 403)
        self.assertEqual(library.tags_of(self.reload(self.item).extras), [])

    def test_human_tags_survive_republishing_the_same_task(self):
        self.post_tags(self.item, ["函数的应用（一）"])
        extras = library.carried_extras(self.reload(self.item), dict(self.item.content))
        self.assertEqual(extras.get("tags"), ["函数的应用（一）"])
        self.assertEqual(extras.get("tags_source"), "human")

    def test_editing_keeps_the_saved_answer_and_its_revision(self):
        item = self.item
        extras = dict(item.extras or {})
        extras["ai_answer"] = {"answer": "2", "analysis": "移项即可", "fingerprint": "abc"}
        extras["solution_id"] = "11111111-1111-1111-1111-111111111111"
        item.extras = extras
        item.save(update_fields=["extras"])
        self.post_tags(item, ["随机抽样"])
        after = self.reload(item)
        self.assertEqual(after.extras["ai_answer"]["answer"], "2")
        self.assertEqual(after.extras["solution_id"], "11111111-1111-1111-1111-111111111111")

    def test_stale_ai_run_metadata_is_dropped_when_a_person_rewrites_the_tags(self):
        item = self.item
        extras = dict(item.extras or {})
        extras.update({"tags": ["集合的概念"], "tags_source": "minimax API · m2", "tags_agent": "gpt-4o",
                       "tags_executor": "api", "tags_checked": timezone.now().isoformat()})
        item.extras = extras
        item.save(update_fields=["extras"])
        self.post_tags(item, ["随机抽样"])
        after = self.reload(item)
        self.assertEqual((after.extras["tags_source"], after.extras["tags_executor"]), ("human", "human"))
        for key in ("tags_agent", "tags_checked"):
            self.assertNotIn(key, after.extras)
