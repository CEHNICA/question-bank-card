"""题库打开得快不快 —— 1.26.47 的三件事。

老师的话：「读取题库的速度最好快一点，最好是瞬间你就把界面加载出来。」

这一版动了三处：
  ① 接口回的中文不再转义成 \\u —— 以前题库列表这一屏有四万多处转义，
     响应体凭空大了五倍多（实测 61 万字节 → 14 万字节）。
  ② 标签关联、AI 建议、退役标签名、上下级关系改成整页一次查完 ——
     以前一道题查一轮，一页四十道题要查一百多次库。
  ③ 「多少道题没答案、多少道题只有 AI 初稿」不再把全库题面 JSON 读进内存数一遍。

这一份测试盯的是**结果没变**：
  - 回给浏览器的内容跟以前一模一样（只是不再转义）；
  - 整页一次查出来的题，跟一道题一道题查出来的题完全一样；
  - 缺答案这两个数，跟老算法一个字符不差（空串、空白、0、空数组都算「没有」）；
  - 题库页一屏的查询次数不随题目多少往上蹿。
"""

import json

from django.db import connection
from django.http import JsonResponse
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from core import library, library_browse, library_tagging
from core.json_response import json_response
from core.models import LibraryTag, LibraryTagAssignment, LibraryTagSuggestion, Paper, PublishedQuestion


class JsonResponseTests(TestCase):
    """中文直接写出去，不再转义。"""

    def test_chinese_is_written_as_is(self):
        response = json_response({"名字": "三角恒等变换"})
        body = response.content.decode("utf-8")
        self.assertNotIn("\\u", body, "中文不该再被转义")
        self.assertIn("三角恒等变换", body, "中文要原样出现")
        self.assertEqual(response["Content-Type"], "application/json")

    def test_the_content_is_the_same_as_djangons_own(self):
        payload = {"数": 1, "名": ["甲", None, True], "里": {"外": "乙"}}
        mine = json.loads(json_response(payload).content.decode("utf-8"))
        theirs = json.loads(JsonResponse(payload).content.decode("utf-8"))
        self.assertEqual(mine, theirs, "内容跟 Django 自己回的一样")

    def test_dates_and_uuids_still_work(self):
        import uuid

        payload = {"编号": uuid.uuid4(), "时刻": "2026-11-07T00:00:00+08:00"}
        self.assertEqual(json.loads(json_response(payload).content.decode("utf-8"))["编号"], str(payload["编号"]))


class AnswerInventoryTests(TestCase):
    """「缺答案」和「只有 AI 初稿」这两个数，口径跟老算法一个字符不差。"""

    def setUp(self):
        self.paper = Paper.objects.create(filename="必修第一册.pdf", kind="pdf", sha256="a" * 64)

    def publication(self, *, answer="__absent__", ai_answer="__absent__"):
        content = {"stem": "求 $\\sin 30^\\circ$ 的值。", "options": {}}
        if answer != "__absent__":
            content["answer"] = answer
        extras = {}
        if ai_answer != "__absent__":
            extras["ai_answer"] = ai_answer
        return PublishedQuestion.objects.create(
            paper=self.paper,
            source_filename="必修第一册.pdf",
            number=PublishedQuestion.objects.count() + 1,
            question_type="fill_blank",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            review_source="human",
            content=content,
            content_hash="b" * 64,
            search_text="",
            extras=extras,
            tags_text="",
        )

    def old_way(self):
        """1.26.47 之前就是这么数的 —— 留着当尺子。"""
        missing = draft = 0
        for content, extras in PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).values_list(
            "content", "extras"
        ):
            if str((content or {}).get("answer") or "").strip():
                continue
            if (extras or {}).get("ai_answer"):
                draft += 1
            else:
                missing += 1
        return missing, draft

    def test_the_two_numbers_match_the_old_way_on_awkward_answers(self):
        """空答案、空白答案、答案是 0、答案是空数组 —— 都算「没有答案」。"""
        self.publication()  # 压根没有答案这一项
        self.publication(answer="")  # 空答案
        self.publication(answer="   ")  # 全是空白
        self.publication(answer=0)  # 0
        self.publication(answer=[])  # 空数组
        self.publication(answer={})  # 空对象
        self.publication(answer=False)  # 假
        self.publication(answer=5)  # 真有答案
        self.publication(answer="30°")  # 真有答案
        self.publication(answer=[1, 2])  # 真有答案
        self.publication(answer={"a": 1})  # 真有答案
        self.publication(answer=True)  # 真有答案
        self.publication(answer="甲", ai_answer="AI 草稿")  # 有 AI 初稿
        self.publication(answer="乙", ai_answer="")  # AI 初稿是空的
        self.publication(answer="丙", ai_answer=[])  # AI 初稿是空数组
        self.publication(answer="丁", ai_answer={})  # AI 初稿是空对象
        self.publication(answer="戊", ai_answer=0)  # AI 初稿是 0
        self.publication(answer="己", ai_answer=["草稿"])  # AI 初稿有内容
        self.publication(answer="庚", ai_answer={"a": 1})  # AI 初稿有内容
        self.publication(answer="辛", ai_answer=True)  # AI 初稿有内容
        self.publication(answer="壬", ai_answer=False)  # AI 初稿是假的
        self.assertEqual(
            library_browse.answer_inventory(PublishedQuestion.Status.PUBLISHED),
            self.old_way(),
            "这两个数必须跟老算法一模一样",
        )

    def test_withdrawn_questions_are_not_counted(self):
        self.publication(answer="甲")
        gone = self.publication(answer="乙")
        gone.status = PublishedQuestion.Status.WITHDRAWN
        gone.save(update_fields=["status"])
        self.assertEqual(
            library_browse.answer_inventory(PublishedQuestion.Status.PUBLISHED), self.old_way(), "撤回的题不算在题库里"
        )


class BatchSerializationTests(TestCase):
    """整页一次查 —— 内容不变，查询次数不往上蹿。"""

    def setUp(self):
        self.paper = Paper.objects.create(filename="必修第一册.pdf", kind="pdf", sha256="c" * 64)
        self.tag = LibraryTag.objects.create(name="三角恒等变换", kind="knowledge", active=True)
        self.second_tag = LibraryTag.objects.create(name="导数的运算", kind="knowledge", active=True)

    def publication(self, number):
        return PublishedQuestion.objects.create(
            paper=self.paper,
            source_filename="必修第一册.pdf",
            number=number,
            question_type="fill_blank",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            review_source="human",
            content={"stem": f"第 {number} 题", "options": {}, "answer": "甲"},
            content_hash="d" * 64,
            search_text="",
            extras={},
            tags_text="",
        )

    def test_the_page_is_exactly_what_one_by_one_gives(self):
        """整页查出来的题，跟一道题一道题查出来的题，一个字段都不差。"""
        first, second, third = self.publication(1), self.publication(2), self.publication(3)
        LibraryTagAssignment.objects.create(
            publication=first, tag=self.tag, scope={"whole": True}, source=LibraryTagAssignment.Source.HUMAN
        )
        # 第一道题挂两个标签 —— 只剩一个的话，下面那条比对就露馅了。
        LibraryTagAssignment.objects.create(
            publication=first, tag=self.second_tag, scope={"whole": True}, source=LibraryTagAssignment.Source.HUMAN
        )
        LibraryTagAssignment.objects.create(
            publication=second,
            tag=self.tag,
            scope={"whole": True},
            source=LibraryTagAssignment.Source.LEGACY_UNKNOWN,
            needs_review=True,
        )
        LibraryTagSuggestion.objects.create(
            publication=third,
            taxonomy_revision=library_tagging.catalogue_state().revision,
            assignment_revision=0,
            suggestions=[{"name": "三角恒等变换", "tag_id": str(self.tag.pk)}],
            catalogue_snapshot={"tags": [{"id": str(self.tag.pk), "name": "三角恒等变换"}]},
            status=LibraryTagSuggestion.Status.PENDING,
        )
        publications = [first, second, third]
        one_by_one = [library.publication_json(item) for item in publications]
        whole_page = library.publications_json(publications)
        self.assertEqual(len(whole_page), 3)
        for single, batched in zip(one_by_one, whole_page):
            single = dict(single)
            batched = dict(batched)
            # 列表这一屏不捎带标签目录快照（见下面那个测试），这里把它对上再比。
            if single["tag_suggestion"]:
                single["tag_suggestion"] = dict(single["tag_suggestion"])
                single["tag_suggestion"]["catalogue_snapshot"] = None
            self.assertEqual(single, batched, "整页查出来的内容必须跟一道题一道题查的一样")

    def test_the_query_count_does_not_grow_with_the_number_of_questions(self):
        """十道题和二十道题，查库的次数不能差出十份。"""
        ten = [self.publication(index) for index in range(1, 11)]
        with CaptureQueriesContext(connection) as small:
            library.publications_json(ten)
        twenty = ten + [self.publication(index) for index in range(11, 21)]
        with CaptureQueriesContext(connection) as big:
            library.publications_json(twenty)
        self.assertLessEqual(
            len(big.captured_queries),
            len(small.captured_queries) + 2,
            "题目翻一倍，查库次数不能跟着翻倍（原来是一题一查）",
        )

    def test_the_newest_suggestion_is_the_one_that_shows(self):
        """一道题有多条 AI 记录时，露出来的要是最新那条。"""
        import datetime

        item = self.publication(1)
        older = LibraryTagSuggestion.objects.create(
            publication=item,
            taxonomy_revision=library_tagging.catalogue_state().revision,
            suggestions=[{"name": "旧的一条"}],
            status=LibraryTagSuggestion.Status.PENDING,
        )
        newer = LibraryTagSuggestion.objects.create(
            publication=item,
            taxonomy_revision=library_tagging.catalogue_state().revision,
            suggestions=[{"name": "新的一条"}],
            status=LibraryTagSuggestion.Status.PENDING,
        )
        moment = datetime.datetime(2026, 11, 7, 10, 0, tzinfo=datetime.UTC)
        LibraryTagSuggestion.objects.filter(pk=older.pk).update(created_at=moment)
        LibraryTagSuggestion.objects.filter(pk=newer.pk).update(created_at=moment + datetime.timedelta(minutes=1))
        row = library.publications_json([item])[0]
        self.assertEqual(row["tag_suggestion"]["id"], str(newer.pk), "露出来的要是最新那条")
        self.assertEqual(row["tag_suggestion"]["suggestions"][0]["name"], "新的一条")
        single = library.publication_json(item)
        self.assertEqual(
            row["tag_suggestion"]["id"],
            single["tag_suggestion"]["id"],
            "整页查出来的跟一道题一道题查出来的，要挑同一条",
        )

    def test_the_list_does_not_carry_the_whole_catalogue_snapshot(self):
        """标签目录快照一份八千多字节，列表里用不着，别捎带着。"""
        item = self.publication(1)
        LibraryTagSuggestion.objects.create(
            publication=item,
            taxonomy_revision=library_tagging.catalogue_state().revision,
            assignment_revision=0,
            suggestions=[{"name": "三角恒等变换"}],
            catalogue_snapshot={"tags": [{"id": str(self.tag.pk), "name": "三角恒等变换"}]},
            status=LibraryTagSuggestion.Status.PENDING,
        )
        row = library.publications_json([item])[0]
        self.assertIsNone(row["tag_suggestion"]["catalogue_snapshot"], "列表里不该捎带整份标签目录")
        # 要看目录快照的地方还是看得到 —— 「AI 标签记录」那个窗口靠它。
        suggestion = LibraryTagSuggestion.objects.get(publication=item)
        snapshot = library_tagging.suggestion_json(suggestion)["catalogue_snapshot"]
        self.assertEqual(snapshot["tags"][0]["name"], "三角恒等变换", "记录窗口还是要有生成时的目录")


class LibraryListResponseTests(TestCase):
    """题库列表这一屏，回出去的东西不再转义。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="必修第一册.pdf", kind="pdf", sha256="e" * 64)
        PublishedQuestion.objects.create(
            paper=self.paper,
            source_filename="必修第一册.pdf",
            number=1,
            question_type="fill_blank",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            review_source="human",
            content={"stem": "求 $\\sin 30^\\circ$ 的值。", "options": {}, "answer": "30°"},
            content_hash="f" * 64,
            search_text="",
            extras={},
            tags_text="",
        )

    def test_the_response_carries_no_escapes(self):
        response = self.client.get("/api/library")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("\\u", body, "回给浏览器的中文不该再被转义")
        self.assertIn("必修第一册.pdf", body, "中文要原样出现在回给浏览器的内容里")
        self.assertIn("单选题", body, "中文也要原样出现在题型名字里")
        payload = json.loads(body)
        self.assertEqual(payload["items"][0]["content"]["answer"], "30°", "内容没变")
        self.assertEqual(payload["tag_work"]["missing_answer"], 0, "有答案的题不算缺答案")
