"""整库一次拉走 + 回收站别拖胖 JSON —— 两处慢活的回归测试。

背景（都是 8768 服务在真库上实测出来的，810 道题）：

① GET /api/library/trash 要 2.5 秒。
   根因不是数据多，是 Paper 的胖 JSON 被重复解析：Paper 上有 pages /
   structure / processing_plan / photos 四个 JSON 字段，其中一份教材的
   structure 有 28 万字节。回收站以前用 select_related("paper")，445 道下架题
   就把同一份资料的 JSON 重新解析了 445 遍 —— 2.2 秒全花在这上面，
   真正要用的字段加起来只要 20 毫秒。

② 首屏把 810 道题拆成 limit=100 的九次请求，每次都把 facets 这些跟分页
   无关的统计重算一遍。九次请求在同一个进程里排成一队，光 API 就 12 秒。
   改成一次 bulk 请求，一次给完。

这两个测试盯的是**根因**，不是耗时（耗时会受机器影响）：
  - 回收站不许 SELECT 资料的胖 JSON 列，也不许 SELECT 题目的 extras；
  - bulk 模式一次能给出超过一百道题，而且不再算那些白算的全库统计。
"""

import json

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext

from core import library_trash
from core.models import Paper, PublishedQuestion, Question

# Paper 上这几个 JSON 字段，加起来能有几百 KB。回收站一个都不需要。
FAT_PAPER_COLUMNS = ("pages", "structure", "processing_plan", "photos", "notes")


def _paper_with_fat_columns():
    return Paper.objects.create(
        filename="高中数学思想方法导引.pdf",
        kind="pdf",
        sha256="a" * 64,
        task_name="高中数学思想方法导引",
        material_type="book",
        pages=[{"page": index, "path": f"p{index}.png"} for index in range(300)],
        structure={"groups": [{"title": "第 %d 章" % index, "body": "x" * 400} for index in range(60)]},
        processing_plan={"steps": [{"name": "切题", "detail": "y" * 300} for _ in range(80)]},
        photos={"files": [{"name": "1.jpg", "meta": "z" * 200}]},
        notes=[{"note": "w" * 200}],
    )


class TrashPayloadStaysCheapTests(TestCase):
    """回收站不许把资料的胖 JSON 拖进来。"""

    def setUp(self):
        self.client = Client()
        self.paper = _paper_with_fat_columns()
        question = Question.objects.create(
            paper=self.paper, number=1, question_type="fill_blank", stem="第 1 题", options={}
        )
        self.publication = PublishedQuestion.objects.create(
            question=question,
            paper=self.paper,
            source_filename=self.paper.filename,
            number=1,
            question_type="fill_blank",
            version=1,
            status=PublishedQuestion.Status.WITHDRAWN,
            review_source="human",
            content={"stem": "第 1 题", "options": {}, "answer": "甲", "section": "第一章"},
            content_hash="b" * 64,
            search_text="",
            extras={"ai_answer": "AI 草稿"},
            tags_text="",
        )

    def test_the_trash_never_selects_the_fat_paper_columns(self):
        """同一份资料的 JSON 被每一道题重新解析一遍，是回收站变慢的根因。"""
        with CaptureQueriesContext(connection) as captured:
            library_trash.trash_payload()
        sql = " ".join(query["sql"] for query in captured.captured_queries)
        for column in FAT_PAPER_COLUMNS:
            self.assertNotIn(
                f'"core_paper"."{column}"',
                sql,
                f"回收站不该读 core_paper.{column}（一份资料几百 KB，每道题都读一遍就是几十秒）",
            )

    def test_the_trash_never_selects_the_extras_blob(self):
        """题目的 extras 平均七百字节，回收站一个字段都用不上。"""
        with CaptureQueriesContext(connection) as captured:
            library_trash.trash_payload()
        sql = " ".join(query["sql"] for query in captured.captured_queries)
        self.assertNotIn('"core_publishedquestion"."extras"', sql, "回收站不该读题目的 extras")

    def test_the_trash_still_names_the_paper(self):
        """少读字段不等于少给东西：资料名、题号、页码、章节都要在。"""
        payload = library_trash.trash_payload()
        self.assertEqual(payload["counts"]["questions"], 1)
        row = payload["withdrawn_questions"][0]
        self.assertEqual(row["paperName"], "高中数学思想方法导引", "资料名要是人改过的那个")
        self.assertEqual(row["number"], 1, "原卷题号要在")
        self.assertEqual(row["section"], "第一章", "章节要在")

    def test_the_trash_counts_stay_right(self):
        """少读字段不能把计数弄错。"""
        payload = library_trash.trash_payload()
        self.assertEqual(payload["counts"]["questions"], 1)
        self.assertEqual(payload["counts"]["papers"], 0, "这份资料没归档")
        self.paper.archived = True
        self.paper.save(update_fields=["archived"])
        payload = library_trash.trash_payload()
        self.assertEqual(payload["counts"]["papers"], 1, "归档了就得进回收站")
        self.assertEqual(payload["archived_papers"][0]["withdrawn"], 1, "下架题数要对")
        self.assertEqual(payload["archived_papers"][0]["published"], 0, "在库题数要对")


class BulkLibraryListTests(TestCase):
    """整库一次拉走：一次给完，别把全库统计算九遍。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="月考卷.pdf", kind="pdf", sha256="c" * 64)
        for number in range(1, 121):
            PublishedQuestion.objects.create(
                paper=self.paper,
                source_filename="月考卷.pdf",
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

    def get(self, query):
        response = self.client.get(query)
        self.assertEqual(response.status_code, 200)
        return json.loads(response.content.decode("utf-8"))

    def test_bulk_gives_back_more_than_one_hundred_at_once(self):
        """一次请求就该把整库给完，不用拆成九次。"""
        payload = self.get("/api/library?bulk=1&limit=1000")
        self.assertEqual(payload["total"], 120, "总数要对")
        self.assertEqual(len(payload["items"]), 120, "一次给完 120 道题")
        self.assertFalse(payload["has_more"], "没有下一页了")

    def test_the_cap_stays_at_one_hundred_without_bulk(self):
        """不加 bulk 的老调用方不受影响，还是一页一百。"""
        payload = self.get("/api/library?limit=1000")
        self.assertEqual(len(payload["items"]), 100, "普通模式还是一页一百道")

    def test_bulk_does_not_recompute_the_whole_library_statistics(self):
        """facets 这些跟分页无关，批量请求不该再算一遍。"""
        with CaptureQueriesContext(connection) as normal:
            self.get("/api/library?limit=100")
        with CaptureQueriesContext(connection) as bulk:
            payload = self.get("/api/library?bulk=1&limit=1000")
        self.assertNotIn("facets", payload, "批量模式不算 facets")
        self.assertLess(len(bulk.captured_queries), len(normal.captured_queries), "批量请求的查库次数必须比普通请求少")

    def test_bulk_and_normal_agree_on_the_same_questions(self):
        """两种模式给回来的题目必须一模一样。"""
        normal = self.get("/api/library?limit=100")
        bulk = self.get("/api/library?bulk=1&limit=1000")
        first_of_bulk = bulk["items"][0]
        self.assertEqual(normal["items"][0]["id"], first_of_bulk["id"], "第一道题要对得上")
        self.assertEqual(normal["items"][0]["content"], first_of_bulk["content"], "题面内容不能因为换了模式就变了")
