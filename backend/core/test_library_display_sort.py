"""题库工作台首屏「首批先画 + 后台补拉」的后端契约。

背景（8768 服务在副本库 810 道题上实测）：

    旧：GET /api/library?bulk=1&limit=1000&offset=0&slim=1
        1.36 MB / 服务端 202 ms（其中整行 filesort 344 ms 那一档最贵）
    新：GET /api/library?bulk=1&limit=150&offset=0&slim=1&sort=display
        216 KB / 服务端 62 ms，剩下的后台补

后端这边要保证两件事：

① 批次边界跟显示顺序对齐。
   前端列表真正在用的顺序是「按题号稳定排序」，同号时按入库时间新的在前。
   服务端没有这个排序键，首屏只能按 recent / number 取批 —— 那批次边界跟
   屏上显示顺序对不上，后台补进来的题会挤到已画出来那批前面，把卡片顺序搅乱。
   所以加了 sort=display = (number, -published_at, -id)。

② 取批的方式不能改变结果。
   取整批行再排序/filesort 在 810 道题上要多花一百多毫秒（SQLite 的 filesort
   会把 content 这些大字段也带着走）。改成「先只排编号取这一批的 id，
   再按编号回表取整行」，省掉大字段排序，但结果必须一模一样。

这些测试盯的是**契约**，不是耗时（耗时会受机器影响）。
"""

from __future__ import annotations

import json

from django.test import Client, TestCase

from core import library_browse
from core.models import Paper, PublishedQuestion, Question


def _make_publication(paper, number, *, stem, when=None):
    """造一道在库题。when 用来固定 published_at（模型上是 auto_now_add）。"""
    question = Question.objects.create(paper=paper, number=number, question_type="fill_blank", stem=stem, options={})
    publication = PublishedQuestion.objects.create(
        question=question,
        paper=paper,
        source_filename=paper.filename,
        number=number,
        question_type="fill_blank",
        version=1,
        status=PublishedQuestion.Status.PUBLISHED,
        review_source="human",
        content={"stem": stem, "options": {}, "answer": "甲", "section": "第一章"},
        content_hash="a" * 64,
        search_text="",
        extras={},
        tags_text="",
    )
    if when is not None:
        # auto_now_add 会覆盖，所以入库后直接改列。
        PublishedQuestion.objects.filter(pk=publication.pk).update(published_at=when)
        publication.refresh_from_db()
    return publication


class DisplaySortTests(TestCase):
    """sort=display：批次边界必须跟前端显示顺序一致。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="月考卷.pdf", kind="pdf", sha256="g" * 64)

    def get(self, query):
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content.decode("utf-8"))

    def test_display_is_an_accepted_sort_key(self):
        payload = self.get("/api/library?bulk=1&limit=10&sort=display")
        self.assertEqual(payload["sort"], "display", "display 必须是一个合法的排序键")

    def test_an_unknown_sort_is_still_rejected(self):
        response = self.client.get("/api/library?bulk=1&limit=10&sort=nope")
        self.assertEqual(response.status_code, 400, "非法排序键还是得 400")

    def test_display_orders_by_number_then_newer_first(self):
        """同号时入库时间新的在前 —— 前端稳定排序时就是这个顺序。"""
        import datetime

        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        _make_publication(self.paper, 1, stem="1 号", when=base)
        _make_publication(self.paper, 1, stem="1 号新", when=base + datetime.timedelta(days=1))
        _make_publication(self.paper, 2, stem="2 号", when=base)

        payload = self.get("/api/library?bulk=1&limit=10&sort=display")
        stems = [item["content"]["stem"] for item in payload["items"]]
        self.assertEqual(stems, ["1 号新", "1 号", "2 号"], "display 排序要是：题号升序 → 同号新的在前")

    def test_display_sorts_the_whole_library_in_one_go(self):
        """display 跟 number 的差别只在同号时的次级键：跨资料同号不该被拆开。"""
        import datetime

        base = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
        other = Paper.objects.create(filename="月考卷二.pdf", kind="pdf", sha256="h" * 64)
        _make_publication(self.paper, 1, stem="甲卷 1", when=base)
        _make_publication(other, 1, stem="乙卷 1", when=base + datetime.timedelta(days=1))

        payload = self.get("/api/library?bulk=1&limit=10&sort=display")
        stems = [item["content"]["stem"] for item in payload["items"]]
        self.assertEqual(stems, ["乙卷 1", "甲卷 1"], "同号时新的在前，不按资料名拆开")


class BatchPickKeepsResultsIdenticalTests(TestCase):
    """取批方式换了，结果不许变。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="月考卷.pdf", kind="pdf", sha256="i" * 64)
        for number in range(1, 21):
            _make_publication(self.paper, number, stem=f"第 {number} 题")

    def get(self, query):
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content.decode("utf-8"))

    def test_a_batch_is_exactly_the_same_slice_as_a_plain_query(self):
        """「先排编号取 id 再回表」的结果必须跟直接切片一模一样。"""
        payload = self.get("/api/library?bulk=1&limit=5&offset=0&sort=display")
        ids = [item["id"] for item in payload["items"]]
        self.assertEqual(len(ids), 5, "首批要给出 5 道题")
        self.assertEqual(
            ids,
            [
                str(pk)
                for pk in library_browse.library_rows({})
                .order_by(*library_browse.SORTS["display"])[:5]
                .values_list("id", flat=True)
            ],
            "取批的结果必须跟按 display 排序后直接切片一致",
        )

    def test_batches_do_not_overlap_or_skip(self):
        """后台补拉是按 offset 往后接的，两批之间不许重叠，也不许漏题。"""
        first = self.get("/api/library?bulk=1&limit=8&offset=0&sort=display")
        second = self.get("/api/library?bulk=1&limit=8&offset=8&sort=display")
        third = self.get("/api/library?bulk=1&limit=8&offset=16&sort=display")

        first_ids = [item["id"] for item in first["items"]]
        second_ids = [item["id"] for item in second["items"]]
        third_ids = [item["id"] for item in third["items"]]

        self.assertEqual(len(set(first_ids) & set(second_ids)), 0, "两批不许重叠")
        self.assertEqual(len(set(second_ids) & set(third_ids)), 0, "两批不许重叠")
        self.assertEqual(len(first_ids + second_ids + third_ids), 20, "三批加起来要正好 20 道")
        self.assertEqual(len(set(first_ids + second_ids + third_ids)), 20, "不许重复")

    def test_the_whole_library_concatenated_matches_one_big_batch(self):
        """分批拼起来必须跟一次拉完完全一致（顺序也一样）。"""
        whole = self.get("/api/library?bulk=1&limit=1000&sort=display")
        whole_ids = [item["id"] for item in whole["items"]]

        paged = []
        for offset in (0, 7, 14):
            payload = self.get(f"/api/library?bulk=1&limit=7&offset={offset}&sort=display")
            paged.extend(item["id"] for item in payload["items"])

        self.assertEqual(paged, whole_ids, "分批拼起来的顺序必须跟一次拉完一样")

    def test_the_total_count_stays_the_whole_library(self):
        """首批要拿到整库题数，前端才知道后台还有多少要补。"""
        payload = self.get("/api/library?bulk=1&limit=5&offset=0&sort=display")
        self.assertEqual(payload["total"], 20, "total 是整库题数，不是这一批的条数")
        self.assertTrue(payload["has_more"], "还有下一页")
        self.assertEqual(payload["next_offset"], 5, "下一批从 5 开始接")


class WantedIdsPathStillWorksTests(TestCase):
    """「只刷新这几道题」那条路径不能被取批改造带坏。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="月考卷.pdf", kind="pdf", sha256="j" * 64)
        self.publications = [_make_publication(self.paper, number, stem=f"第 {number} 题") for number in range(1, 6)]

    def get(self, query):
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200, response.content)
        return json.loads(response.content.decode("utf-8"))

    def test_wanted_ids_returns_exactly_those_questions(self):
        wanted = str(self.publications[0].pk)
        payload = self.get(f"/api/library?ids={wanted}&limit=10")
        self.assertEqual(len(payload["items"]), 1, "只要哪几道就给哪几道")
        self.assertEqual(str(payload["items"][0]["id"]), str(self.publications[0].pk))
