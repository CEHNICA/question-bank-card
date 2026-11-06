"""重算搜索索引的命令。

卡面上印着的章节名要能搜到，靠的是入库时算好的那一列搜索索引。改了搜索规则以后，
老卡片还存着旧索引 —— 印得出来、搜不到（1.26.44 实测：真库 1267 张里 358 张的索引
是旧规则算的）。这个命令按现规则重算一遍，只写索引那一列。

    manage.py refresh_search_index              # 只看会改哪些，不写
    manage.py refresh_search_index --apply      # 真写
"""

import pathlib
import tempfile

from django.core.management import call_command
from django.test import TestCase, override_settings

from core import library
from core.models import Paper, PublishedQuestion


class RefreshSearchIndexTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(DATA_ROOT=pathlib.Path(folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.paper = Paper.objects.create(filename="必修第一册.pdf", kind="pdf", sha256="c" * 64)
        self.serial = 0

    def publication(self, *, section="5.5.2 简单的三角恒等变换", search_text="", filename="必修第一册.pdf"):
        self.serial += 1
        content = {
            "stem": f"求 $\\sin 30^\\circ$ 的值（第 {self.serial} 题）。",
            "options": {},
            "answer": "",
            "analysis": "",
            "figures": [],
            "origin": "",
            "source_filename": filename,
            "document_id": str(self.paper.id),
            "question_type": "fill_blank",
            "sources": [],
            "section": section,
        }
        return PublishedQuestion.objects.create(
            paper=self.paper,
            source_filename=filename,
            number=self.serial,
            question_type="fill_blank",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            review_source="human",
            content=content,
            content_hash=library.content_hash(content),
            search_text=search_text,
            extras={"tags": ["三角恒等变换"]},
            tags_text="|三角恒等变换|",
        )

    def test_listing_alone_does_not_touch_the_database(self):
        item = self.publication(search_text="入库时算的旧索引")
        call_command("refresh_search_index")
        item.refresh_from_db()
        self.assertEqual(item.search_text, "入库时算的旧索引", "不加 --apply 就不该写")

    def test_apply_rewrites_only_the_search_index(self):
        item = self.publication(search_text="入库时算的旧索引")
        call_command("refresh_search_index", apply=True)
        item.refresh_from_db()
        self.assertNotEqual(item.search_text, "入库时算的旧索引")
        self.assertIn("三角恒等变换", item.search_text, "章节名进了索引，卡面上印着的就搜得到")
        self.assertIn("sin30", item.search_text.replace(" ", ""), "题干照旧在里面")
        self.assertIn("三角恒等变换", item.search_text, "知识点标签照旧在里面")
        self.assertEqual(item.content["section"], "5.5.2 简单的三角恒等变换", "题干快照一个字没动")
        self.assertEqual(item.tags_text, "|三角恒等变换|", "标签那一列也不该动")

    def test_a_card_already_matching_is_left_alone(self):
        item = self.publication(section="复习题")
        PublishedQuestion.objects.filter(pk=item.pk).update(search_text=library._search_text(item.content, item.extras))
        before = PublishedQuestion.objects.get(pk=item.pk).search_text
        call_command("refresh_search_index", apply=True)
        item.refresh_from_db()
        self.assertEqual(item.search_text, before, "本来就一致的卡不该被动")

    def test_only_the_named_paper_is_processed(self):
        mine = self.publication(filename="必修第一册.pdf", search_text="旧")
        other = self.publication(filename="月考试卷.pdf", search_text="旧")
        call_command("refresh_search_index", apply=True, paper="必修第一册.pdf")
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertNotEqual(mine.search_text, "旧")
        self.assertEqual(other.search_text, "旧", "别的资料不该动")

    def test_an_old_card_missing_fields_does_not_crash_the_command(self):
        item = self.publication(search_text="旧")
        content = dict(item.content)
        content.pop("answer", None)
        content.pop("analysis", None)
        PublishedQuestion.objects.filter(pk=item.pk).update(content=content)
        call_command("refresh_search_index", apply=True)
        item.refresh_from_db()
        self.assertIn("三角恒等变换", item.search_text, "缺字段的老卡片也要算出来，不能报错跳过")
