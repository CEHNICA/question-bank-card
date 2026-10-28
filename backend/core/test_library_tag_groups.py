"""标签群组 + 拼音首字母：老师自己分的组，以及按 A/B/C… 分组显示。

1.25 的时候分组只留在模型里（章节改用父标签表达），界面上没有入口。
1.27.0 把它接回来 —— 分组是「我自己的一堆标签」，跟上下级不是一回事。

这里验的是后端能做什么：建组、改名、删组、归组、移出、撤销，
以及 catalogue 接口把群组清单和每个标签的拼音首字母交出去。
"""

from __future__ import annotations

import uuid

from django.test import TestCase

from core import library_tagging
from core.models import LibraryTag, LibraryTagGroup, LibraryTagOperation, Paper, PublishedQuestion, Question
from core.pinyin_initials import initial_for


class PinyinInitialTests(TestCase):
    """首字母算得对不对 —— 分组全靠它。"""

    def test_chinese_names_use_their_pinyin_initial(self):
        self.assertEqual(initial_for("爱情"), "A")
        self.assertEqual(initial_for("白噪音"), "B")
        self.assertEqual(initial_for("抖音"), "D")
        self.assertEqual(initial_for("感伤"), "G")
        self.assertEqual(initial_for("欢快"), "H")

    def test_latin_names_use_their_own_letter(self):
        self.assertEqual(initial_for("AI"), "A")
        self.assertEqual(initial_for("douyin"), "D")

    def test_symbols_and_digits_are_not_guessed(self):
        self.assertEqual(initial_for("123"), "#")
        self.assertEqual(initial_for(""), "#")
        self.assertEqual(initial_for("---"), "#")

    def test_leading_symbols_do_not_hide_the_name(self):
        # 括号是前缀，不算名字的一部分 —— 按「初」算，C。
        self.assertEqual(initial_for("（初中）函数"), "C")


class TagGroupTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="虚构卷.pdf", kind="pdf", sha256="c" * 64)
        self.serial = 0

    def make_publication(self):
        self.serial += 1
        question = Question.objects.create(
            paper=self.paper, number=self.serial, question_type="free_response", stem="虚构题", state="green"
        )
        return PublishedQuestion.objects.create(
            question=question,
            paper=self.paper,
            source_filename="虚构卷.pdf",
            number=self.serial,
            question_type="free_response",
            version=1,
            content={
                "stem": "虚构题",
                "options": {},
                "answer": "",
                "analysis": "",
                "figures": [],
                "section": {},
                "origin": "",
            },
            content_hash=(f"g{self.serial}" * 16)[:64],
            search_text="虚构题",
        )

    def make_tag(self, name):
        return LibraryTag.objects.create(name=name, kind=LibraryTag.Kind.CUSTOM)

    def revision(self):
        return library_tagging.catalogue_state().revision

    def mutate(self, action, **fields):
        result = library_tagging.catalogue_mutation(
            {"action": action, "revision": self.revision(), "request_id": str(uuid.uuid4()), **fields}
        )
        self.assertTrue(result["ok"])
        return result

    def test_a_group_can_be_created_and_renamed(self):
        self.mutate("create_group", name="上周那套卷子")
        group = LibraryTagGroup.objects.get(name="上周那套卷子")
        self.assertEqual(group.name, "上周那套卷子")

        self.mutate("update_group", group_id=str(group.pk), name="五月卷")
        group.refresh_from_db()
        self.assertEqual(group.name, "五月卷")

    def test_a_tag_can_be_moved_in_and_out_of_a_group(self):
        self.mutate("create_group", name="常用")
        group = LibraryTagGroup.objects.get(name="常用")
        tag = self.make_tag("二次函数的最值")

        self.mutate("group_membership", group_id=str(group.pk), tag_id=str(tag.pk), present=True)
        self.assertEqual(list(group.tags.values_list("pk", flat=True)), [tag.pk])

        self.mutate("group_membership", group_id=str(group.pk), tag_id=str(tag.pk), present=False)
        self.assertEqual(group.tags.count(), 0)

    def test_several_tags_can_be_moved_in_one_go(self):
        self.mutate("create_group", name="常用")
        group = LibraryTagGroup.objects.get(name="常用")
        first = self.make_tag("二次函数的最值")
        second = self.make_tag("配方法")

        self.mutate(
            "group_membership_batch", group_id=str(group.pk), tag_ids=[str(first.pk), str(second.pk)], present=True
        )
        self.assertEqual(group.tags.count(), 2)

    def test_deleting_a_group_keeps_the_tags(self):
        self.mutate("create_group", name="常用")
        group = LibraryTagGroup.objects.get(name="常用")
        tag = self.make_tag("配方法")
        self.mutate("group_membership", group_id=str(group.pk), tag_id=str(tag.pk), present=True)

        self.mutate("delete_group", group_id=str(group.pk))
        self.assertFalse(LibraryTagGroup.objects.filter(pk=group.pk).exists())
        # 标签还在，只是回到「未分类」。
        self.assertTrue(LibraryTag.objects.filter(pk=tag.pk).exists())
        self.assertEqual(list(tag.groups.values_list("pk", flat=True)), [])

    def test_group_changes_can_be_undone(self):
        self.mutate("create_group", name="常用")
        group = LibraryTagGroup.objects.get(name="常用")
        tag = self.make_tag("配方法")
        self.mutate("group_membership", group_id=str(group.pk), tag_id=str(tag.pk), present=True)
        operation = LibraryTagOperation.objects.filter(action="catalogue:group_membership").first()
        self.assertIsNotNone(operation)

        library_tagging.undo_catalogue_operation(operation)
        group.refresh_from_db()
        self.assertEqual(group.tags.count(), 0)

    def test_builtin_kind_groups_are_real_groups(self):
        """1.27.0：类型变成了**真的分组** —— 迁移建好的，和老师自建的一样。

        「未分类」的口径也跟着改成「不属于任何分组」：删掉一个分组，
        里面那批标签就回到这里。
        """
        self.mutate("create_group", name="老师自建的组")

        rows = library_tagging._catalogue_rows()
        names = {group["name"]: group for group in rows["groups"]}
        self.assertTrue(names["知识点"]["builtin"], "知识点是内置分组")
        self.assertTrue(names["历史标签"]["builtin"], "历史标签是内置分组")
        self.assertFalse(names["老师自建的组"]["builtin"], "自建的组不是内置的")
        # 教材章节那套带 section 标记 —— 界面上不当分组显示（那是章节树的事）。
        sections = [row for row in rows["groups"] if row["section"]]
        self.assertTrue(
            all(str(row["system_key"] or "").startswith("seed-group:") for row in sections),
            "带 section 标记的只有教材章节那套",
        )

    def test_catalogue_reports_groups_and_initials(self):
        self.mutate("create_group", name="常用")
        group = LibraryTagGroup.objects.get(name="常用")
        inside = self.make_tag("爱情")
        outside = self.make_tag("白噪音")
        self.mutate("group_membership", group_id=str(group.pk), tag_id=str(inside.pk), present=True)

        rows = library_tagging._catalogue_rows()
        mine = next(row for row in rows["groups"] if row["name"] == "常用")
        self.assertEqual(mine["tag_count"], 1)
        by_id = {row["id"]: row for row in rows["tags"]}
        self.assertEqual(by_id[str(inside.pk)]["initial"], "A")
        self.assertEqual(by_id[str(inside.pk)]["group_ids"], [str(group.pk)])
        self.assertEqual(by_id[str(outside.pk)]["initial"], "B")
        # 「爱情」归了组，「白噪音」还谁都没归 —— 除了内置的类型组（测试库里迁移归的）。
        self.assertNotIn(str(group.pk), by_id[str(outside.pk)]["group_ids"])
