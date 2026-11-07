"""把锁在「旧标签留底」里的标签接回正式目录。

真库实测（2026-11-06）：433 张已入库的卡，标签被挪进一个「留底」字段就不再动，
既看不见、也筛不出来。这些旧标签的名字全部能在现在的标签目录里找到对应 ——
所以它们是可以接回来的，接回来老师才看得到、才用得上。

这一版只做「接回来」这一件事：
  - 名字能对上正式标签就接，对不上就原样留着（不猜、不硬凑）；
  - 接回来标成「待核对」——旧版 AI 的结果没有逐条理由，老师确认后才算正式标签；
  - 重复跑不重复折腾；
  - 题干、答案、解析、图片一个字都不动。
"""

from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from core.models import LibraryTag, LibraryTagAssignment, Paper, PublishedQuestion

MIGRATION = "core.migrations.0032_restore_legacy_tags"


class RestoreLegacyTagsMigrationTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="必修第一册.pdf", kind="pdf", sha256="e" * 64)
        self.forwards = import_module(MIGRATION).forwards
        self.formal = LibraryTag.objects.create(name="三角恒等变换", kind="knowledge", active=True)
        self.other = LibraryTag.objects.create(name="导数的运算", kind="knowledge", active=True)

    def publication(self, *, names, status=PublishedQuestion.Status.PUBLISHED, filename="必修第一册.pdf"):
        content = {
            "stem": "求 $\\sin 30^\\circ$ 的值。",
            "options": {},
            "answer": "30°",
            "analysis": "因为 sin30 等于二分之一。",
            "source_filename": filename,
            "document_id": str(self.paper.id),
            "question_type": "fill_blank",
            "section": "5.5.2 简单的三角恒等变换",
        }
        return PublishedQuestion.objects.create(
            paper=self.paper,
            source_filename=filename,
            number=1,
            question_type="fill_blank",
            version=1,
            status=status,
            review_source="human",
            content=content,
            content_hash="c" * 64,
            search_text="旧索引",
            extras={"tags_legacy_original": {"names": list(names), "source": "minimax api", "at": "2026-10-08"}},
            tags_text="",
        )

    def test_the_locked_tag_comes_back_and_becomes_visible(self):
        item = self.publication(names=["三角恒等变换"])
        self.forwards(django_apps, None)
        rows = LibraryTagAssignment.objects.filter(publication=item)
        self.assertEqual(rows.count(), 1, "锁着的标签要接回来")
        self.assertEqual(rows.first().tag_id, self.formal.pk, "接回的是同一个知识点")
        self.assertTrue(rows.first().needs_review, "旧版 AI 的结果没有逐条理由，要等老师核对")

    def test_the_tag_becomes_filterable_once_confirmed(self):
        """确认之后筛选才认它 —— 跟人工贴的标签一个待遇。"""
        item = self.publication(names=["三角恒等变换"])
        self.forwards(django_apps, None)
        assignment = LibraryTagAssignment.objects.get(publication=item)
        assignment.needs_review = False
        assignment.save(update_fields=["needs_review"])
        hit = PublishedQuestion.objects.filter(tag_assignments__tag=self.formal, tag_assignments__needs_review=False)
        self.assertIn(item, hit, "确认后的标签要能筛出来")

    def test_a_name_that_does_not_match_anything_is_left_alone(self):
        item = self.publication(names=["目录里根本没有的知识点"])
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=item).count(), 0, "对不上的不硬凑")

    def test_partly_matching_names_take_only_what_matches(self):
        item = self.publication(names=["三角恒等变换", "目录里根本没有的知识点"])
        self.forwards(django_apps, None)
        rows = LibraryTagAssignment.objects.filter(publication=item)
        self.assertEqual(rows.count(), 1, "能对上的接回来，对不上的不接")
        self.assertEqual(rows.first().tag_id, self.formal.pk)

    def test_several_tags_on_one_card_all_come_back(self):
        item = self.publication(names=["三角恒等变换", "导数的运算"])
        self.forwards(django_apps, None)
        rows = LibraryTagAssignment.objects.filter(publication=item)
        self.assertEqual(rows.count(), 2, "一张卡上的多个标签都要接回来")
        self.assertEqual({row.tag_id for row in rows}, {self.formal.pk, self.other.pk})

    def test_the_same_tag_twice_is_only_attached_once(self):
        item = self.publication(names=["三角恒等变换", "三角恒等变换"])
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=item).count(), 1, "同一个标签不贴两遍")

    def test_running_twice_changes_nothing_the_second_time(self):
        item = self.publication(names=["三角恒等变换"])
        self.forwards(django_apps, None)
        before = LibraryTagAssignment.objects.count()
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.count(), before, "重复跑不重复折腾")

    def test_cards_that_already_have_tags_are_left_alone(self):
        item = self.publication(names=["三角恒等变换"])
        LibraryTagAssignment.objects.create(publication=item, tag=self.other, source="human", scope={"whole": True})
        self.forwards(django_apps, None)
        rows = LibraryTagAssignment.objects.filter(publication=item)
        self.assertEqual(rows.count(), 1, "已经有标签的卡不动，不覆盖老师贴过的")
        self.assertEqual(rows.first().tag_id, self.other.pk)

    def test_retired_cards_are_not_touched(self):
        """回收站里的卡不接 —— 老师看不到的卡不该动。"""
        item = self.publication(names=["三角恒等变换"], status=PublishedQuestion.Status.WITHDRAWN)
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=item).count(), 0)

    def test_a_card_without_legacy_tags_is_left_alone(self):
        content = {
            "stem": "题干",
            "options": {},
            "answer": "答案",
            "analysis": "解析",
            "source_filename": "必修第一册.pdf",
        }
        item = PublishedQuestion.objects.create(
            paper=self.paper,
            source_filename="必修第一册.pdf",
            number=2,
            question_type="fill_blank",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            review_source="human",
            content=content,
            content_hash="f" * 64,
            search_text="旧索引",
            extras={},
        )
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=item).count(), 0)

    def test_the_question_text_is_not_touched(self):
        item = self.publication(names=["三角恒等变换"])
        before_content = dict(item.content)
        before_search = item.search_text
        before_hash = item.content_hash
        self.forwards(django_apps, None)
        item.refresh_from_db()
        self.assertEqual(item.content, before_content, "题干快照一个字都不能动")
        self.assertEqual(item.search_text, before_search, "待核对的标签不进搜索索引")
        self.assertEqual(item.content_hash, before_hash, "内容指纹不该动")

    def test_a_retired_label_does_not_get_restored(self):
        """封存的历史标签不是正式标签，不能接回来。"""
        LibraryTag.objects.create(name="旧标签", kind="legacy", active=False)
        item = self.publication(names=["旧标签"])
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=item).count(), 0)

    def test_a_retired_label_is_not_restored_even_when_it_looks_alive(self):
        """封存的历史标签就算被人重新启用，也不能当成正式标签接回来。

        「只收启用中的标签」和「跳过历史标签」是两道防护，各挡各的。
        这里造一个「历史标签但启用中」的 —— 前一道防护对它无效，
        后一道必须挡住。
        """
        LibraryTag.objects.create(name="旧标签", kind="legacy", active=True)
        item = self.publication(names=["旧标签"])
        self.forwards(django_apps, None)
        self.assertEqual(LibraryTagAssignment.objects.filter(publication=item).count(), 0, "历史标签不能接回来")

    def test_the_old_tag_snapshot_is_kept_for_audit(self):
        item = self.publication(names=["三角恒等变换"])
        self.forwards(django_apps, None)
        assignment = LibraryTagAssignment.objects.get(publication=item)
        self.assertEqual(assignment.source, "legacy_unknown", "来源要标清楚是旧标签")
        self.assertIn("legacy_name", assignment.source_snapshot, "要留底，方便日后查")
