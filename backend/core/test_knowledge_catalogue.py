"""知识点目录的只读预览。

打标签只能从目录里选，所以这个目录本来就该看得见。以前入口在「显示与导出」页
底下的「题面整理」里，和题面整理毫无关系，只能拿记事本改。
这里钉住的是：目录读的就是打标签时读的那一份，不是另一份副本。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import Client, TestCase, override_settings
from django.urls import path

from core import knowledge
from core.models import LibraryTag, LibraryTagGroup
from core.views import knowledge_catalogue

urlpatterns = [path("api/settings/knowledge", knowledge_catalogue)]


@override_settings(ROOT_URLCONF=__name__)
class KnowledgeCatalogueTests(TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        settings = override_settings(DATA_ROOT=Path(self.folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.client = Client()
        group = LibraryTagGroup.objects.create(name="正式数学目录")
        tag = LibraryTag.objects.create(name="题库知识点", kind="knowledge", levels=["高中"])
        tag.groups.add(group)

    def body(self):
        response = self.client.get("/api/settings/knowledge", headers={"x-qb-request": "1"})
        return response, json.loads(response.content)

    def test_the_catalogue_is_the_one_tagging_picks_from(self):
        response, body = self.body()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["points"][0]["point"], "题库知识点")
        self.assertEqual(body["points"][0]["chapter"], "正式数学目录")
        self.assertEqual(body["chapters"], 1)
        self.assertEqual(body["source"], "正式题库标签目录")

    def test_legacy_text_file_does_not_override_the_live_catalogue(self):
        knowledge.ensure_file().write_text(
            "# 第一章 自己的章\n甲\n乙\n\n# 第二章 另一个章\n丙\n", encoding="utf-8")
        _, body = self.body()
        self.assertEqual([item["point"] for item in body["points"]], ["题库知识点"])
        self.assertEqual((body["total"], body["chapters"]), (1, 1))

    def test_requests_without_the_local_page_header_are_refused(self):
        response = self.client.get("/api/settings/knowledge")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post("/api/settings/knowledge", data="{}",
                                          content_type="application/json").status_code, 405)

