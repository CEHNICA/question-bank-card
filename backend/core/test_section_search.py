"""卡面上印着的章节名，搜得到吗？

教材每一章的习题都从 1 重新编，「原卷第 1 题」这句话在九年级上册里有 94 张。
4342a68 起卡面多印一段章节名（「习题 6.1 · 原卷第 3 题」），老师照着这句话
回原卷核对、再照着这句话搜，两步都得走得通。实测隔离库 713 张有章节名的卡
一张都搜不到——search_text 只装了题干/选项/答案/题源/文件名，从来没装过章节名。

只收「卡面上真的印出来的那一段」。试卷的 section 是一整段大题说明，
它带着「本题共 N 小题」这类套话，进搜索会把一堆不相干的卷子都捞出来。

卡面那边（frontend/library.js 的 cardLocator）认的是同一条规则，两边的测试
都读 frontend/testdata/section_labels.json 这一份取值，改一边就会红。
"""

import json
import pathlib
import tempfile

from django.test import TestCase, override_settings

from core import library, library_browse
from core.models import Paper, PublishedQuestion, Question

FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "testdata" / "section_labels.json"


class SectionLabelFixtureTests(TestCase):
    def test_section_label_agrees_with_the_fixture_the_card_view_also_reads(self):
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
        self.assertGreaterEqual(len(cases), 10, "fixture 至少要覆盖教材短标签、试卷长说明和 12/13 字边界")
        for case in cases:
            with self.subTest(section=case["section"]):
                self.assertEqual(library.section_label(case["section"]), case["label"])


class SectionIsSearchableTests(TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        settings = override_settings(DATA_ROOT=pathlib.Path(folder.name))
        settings.enable()
        self.addCleanup(settings.disable)
        self.paper = Paper.objects.create(filename="九年级上册.pdf", kind="pdf", sha256="b" * 64)
        self.serial = 0

    def publication(self, *, section, stem=None):
        self.serial += 1
        stem = stem or f"虚构题干 {self.serial}：求 $\\sqrt{{9}}$ 的值。"
        content = {
            "stem": stem,
            "options": {},
            "answer": "",
            "analysis": "",
            "figures": [],
            "origin": "",
            "source_filename": "九年级上册.pdf",
            "document_id": str(self.paper.id),
            "question_type": "fill_blank",
            "sources": [],
            "section": section,
        }
        question = Question.objects.create(
            paper=self.paper, number=self.serial, question_type="fill_blank", stem=stem, section=section, state="green"
        )
        return PublishedQuestion.objects.create(
            paper=self.paper,
            question=question,
            number=self.serial,
            source_filename="九年级上册.pdf",
            question_type="fill_blank",
            version=1,
            status="published",
            review_source="human",
            content=content,
            content_hash=library.content_hash(content),
            search_text=library._search_text(content, None),
            extras={},
            tags_text="",
        )

    def found(self, query):
        return set(library_browse.library_rows({"q": query}).values_list("id", flat=True))

    def test_a_section_printed_on_the_card_can_be_searched(self):
        item = self.publication(section="习题 6.1")
        # 老师卡面上看见的就是这两个字：带空格的「习题 6.1」和连写的「习题6.1」，
        # 搜索框两种都会敲，两种都得能找到这张卡。
        self.assertIn(item.id, self.found("习题 6.1"))
        self.assertIn(item.id, self.found("习题6.1"))

    def test_the_twelve_character_boundary_still_searches(self):
        # 真库实测：最长的会显示章节名 12 字，最短的不显示 13 字。12 这一侧必须能搜。
        item = self.publication(section="二. 填空题（共6小题）")
        self.assertEqual(library.section_label("二. 填空题（共6小题）"), "二. 填空题（共6小题）")
        self.assertIn(item.id, self.found("二. 填空题（共6小题）"))

    def test_a_section_never_shown_on_the_card_never_enters_the_search(self):
        # 试卷的大题说明卡面上一个字都不显示，也不该被搜出来——
        # 它带着「本题共 N 小题」，进了搜索会把所有卷子的卡都捞出来。
        item = self.publication(section="一. 选择题（共10小题）")
        self.assertEqual(library.section_label(item.content["section"]), "")
        self.assertNotIn(item.id, self.found("共10小题"))

    def test_the_long_exam_intro_cannot_swamp_a_common_word_search(self):
        noisy = "一、选择题：本题共 12 小题，每小题 5 分，共 60 分，在每小题给出的四个选项中，只有一项是符合题目要求的"
        item = self.publication(section=noisy)
        for word in ("本题", "共60分", "符合题目要求"):
            with self.subTest(word=word):
                self.assertNotIn(item.id, self.found(word))

    def test_searching_a_section_does_not_pull_in_other_sections(self):
        wanted = self.publication(section="复习题")
        other = self.publication(section="习题 9.14")
        self.assertEqual(self.found("复习题"), {wanted.id})
        self.assertIn(other.id, self.found("习题 9.14"))
        self.assertNotIn(other.id, self.found("复习题"))

    def test_searching_a_section_does_not_pull_in_the_body_text_of_another_card(self):
        section = self.publication(section="第二章")
        bystander = self.publication(section="第三章", stem="这一段正文里正好提到「第二章」这个词。")
        self.assertIn(section.id, self.found("第二章"))
        self.assertIn(bystander.id, self.found("第二章"), "正文本来就提到第二章时也该命中，这不是这次改动造成的")

    def test_editing_tags_refreshes_the_search_index_of_an_existing_card(self):
        # 章节名是入库时算进 search_text 的。老卡片要等下一次写回才会补上，
        # 改个标签正好会触发一次（save_extras 走同一条 _search_text）。
        item = self.publication(section="习题 8.6")
        stale = PublishedQuestion.objects.get(pk=item.pk)
        stale.search_text = library.search_key(stale.content["stem"])
        stale.save(update_fields=["search_text"])
        self.assertNotIn(item.id, self.found("习题8.6"))

        refreshed = library.save_extras(stale, {"tags": ["二次函数"]})
        self.assertIn(refreshed.id, self.found("习题8.6"))
