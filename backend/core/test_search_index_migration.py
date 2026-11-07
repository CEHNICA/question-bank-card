"""升级时自动补写题卡搜索索引。

1.26.44 让卡面能印出教材小节名（「5.5.2 简单的三角恒等变换」），但老卡片存的还是
旧规则算的搜索索引 —— 印得出来、搜不到。靠人手动补是补不过来的：每台机器升级后
都会欠一次。所以把补写放进升级流程（0031），软件升级时自己补齐。

只重算搜索索引那一列，题干、答案、解析、图片、旗标、标签一个字都不动。
"""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from core import library
from core.models import Paper, PublishedQuestion

MIGRATION = "core.migrations.0031_refresh_search_index"


class RefreshSearchIndexMigrationTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="必修第一册.pdf", kind="pdf", sha256="d" * 64)
        self.serial = 0
        self.forwards = import_module(MIGRATION).forwards

    def publication(self, *, section="5.5.2 简单的三角恒等变换", search_text="", filename="必修第一册.pdf", stem=None):
        self.serial += 1
        content = {
            "stem": stem if stem is not None else f"求 $\\sin 30^\\circ$ 的值（第 {self.serial} 题）。",
            "options": {},
            "answer": "30°",
            "analysis": "因为 sin30 等于二分之一。",
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

    def test_the_section_name_ends_up_searchable(self):
        item = self.publication(search_text="入库时算的旧索引")
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertIn("三角恒等变换", item.search_text, "卡面上印着的章节名要能搜到")

    def test_only_the_search_index_column_is_rewritten(self):
        item = self.publication(search_text="入库时算的旧索引")
        before_content = dict(item.content)
        before_tags = item.tags_text
        before_hash = item.content_hash
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertEqual(item.content, before_content, "题干快照一个字都不能动")
        self.assertEqual(item.tags_text, before_tags, "标签那一列也不该动")
        self.assertEqual(item.content_hash, before_hash, "内容指纹不该动")
        self.assertEqual(item.status, PublishedQuestion.Status.PUBLISHED, "状态不该动")

    def test_the_stem_and_answer_stay_searchable(self):
        item = self.publication(search_text="旧")
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertIn("sin30", item.search_text.replace(" ", ""), "题干照旧搜得到")
        self.assertIn("30°", item.search_text, "答案照旧搜得到")

    def test_a_card_already_matching_is_left_alone(self):
        item = self.publication(section="复习题")
        PublishedQuestion.objects.filter(pk=item.pk).update(search_text=library._search_text(item.content, item.extras))
        before = PublishedQuestion.objects.get(pk=item.pk).search_text
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertEqual(item.search_text, before, "本来就一致的卡不该被动")

    def test_running_twice_changes_nothing_the_second_time(self):
        item = self.publication(search_text="旧")
        self.forwards(django_apps, None)
        once = PublishedQuestion.objects.get(pk=item.pk).search_text
        self.forwards(django_apps, None)
        twice = PublishedQuestion.objects.get(pk=item.pk).search_text
        self.assertEqual(once, twice, "补写要能重复跑，升级流程重试也不该出事")

    def test_an_old_card_missing_fields_does_not_crash_the_upgrade(self):
        item = self.publication(search_text="旧")
        content = dict(item.content)
        content.pop("answer", None)
        content.pop("analysis", None)
        PublishedQuestion.objects.filter(pk=item.pk).update(content=content)
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertIn("三角恒等变换", item.search_text, "缺字段的老卡片也要算出来，不能报错跳过")

    def test_an_old_card_without_the_source_filename_does_not_crash_the_upgrade(self):
        """很老的一批卡片，快照里根本没有「资料文件名」这个键。

        算索引时要用到它，缺了就 KeyError —— 那会让升级直接中断，比搜不到还糟。
        这时候拿卡片自己那一列的文件名顶上。
        """
        item = self.publication(search_text="旧")
        content = dict(item.content)
        content.pop("source_filename", None)
        PublishedQuestion.objects.filter(pk=item.pk).update(content=content)
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertIn("三角恒等变换", item.search_text, "缺字段的老卡片也要算出来，不能报错跳过")
        self.assertIn("必修第一册.pdf", item.search_text, "文件名要顶上缺掉的那个")

    def test_each_card_keeps_its_own_content(self):
        """多张卡一起补时，每张卡自己的题面都得留着 ——
        不能把某一张的题面写到别的卡上去。

        卡面里存的是原始文本（带 $ 和 \\cos 这种写法原样留着），
        去掉空格、换写法那是搜索的时候才做的事。
        """
        first_stem = "第一道题：求 $\\cos 60^\\circ$ 的值。"
        second_stem = "第二道题：求 $\\tan 45^\\circ$ 的值。"
        first = self.publication(search_text="旧", stem=first_stem)
        second = self.publication(search_text="旧", stem=second_stem)
        self.forwards(django_apps, None)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.content["stem"], first_stem, "第一张卡的题面还是自己的")
        self.assertEqual(second.content["stem"], second_stem, "第二张卡的题面还是自己的")
        self.assertNotIn("tan45", first.search_text, "第一张卡的索引里没有第二张卡的内容")
        self.assertNotIn("cos60", second.search_text, "第二张卡的索引里没有第一张卡的内容")
        self.assertIn("cos60", first.search_text.replace(" ", ""), "第一张卡照旧搜得到")
        self.assertIn("tan45", second.search_text.replace(" ", ""), "第二张卡照旧搜得到")

    def test_cards_of_every_source_are_covered(self):
        mine = self.publication(filename="必修第一册.pdf", search_text="旧")
        other = self.publication(filename="月考试卷.pdf", search_text="旧")
        self.forwards(django_apps, None)
        mine.refresh_from_db()
        other.refresh_from_db()
        self.assertNotEqual(mine.search_text, "旧")
        self.assertNotEqual(other.search_text, "旧", "每份资料都要补到，不能只补一份")

    def test_a_long_exam_instruction_is_not_pulled_into_the_index(self):
        """试卷的大题说明不进搜索：它带着「本题共 8 小题」这类套话，
        进了搜索会把一堆不相干的卷子都搜出来。"""
        item = self.publication(
            section="一、选择题：本大题共8小题，每小题5分，共计40分.请把正确的选项填涂在答题卡相应的位置上.",
            search_text="旧",
        )
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertNotIn("本大题共8小题", item.search_text, "大题说明不该进搜索")
        self.assertIn("sin30", item.search_text.replace(" ", ""), "题干照旧在里面")
