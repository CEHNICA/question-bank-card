"""回收站别在首屏把 445 行明细全拉回来。

背景（8768 服务在真库上实测）：

    GET /api/library/trash        -> 331 KB / 稳态 50~110 ms
    GET /api/library/trash?summary=1 -> 几十字节 / 1 ms

Eagle 工作台首屏只要一个数字：左下角「🗑️ 回收站」徽标上写几项。
那份明细（10 份资料 + 445 道下架题，含每道题的题干预览、资料名、页码）
首屏一个字节都用不上，却占了首屏网络里最大的一块 —— 比 /api/library/tags 还大。

所以拆成两个模式：
  - 默认（全量）：面板真打开、恢复/删除操作要用明细时，才走这一条。
  - ?summary=1（摘要）：只要计数，首屏用。

这两个测试盯的是**根因**，不是耗时（耗时会受机器影响）：
  - 摘要模式不许读出明细行，也不许把明细塞进响应；
  - 摘要里的计数必须跟全量模式的计数一致（不然徽标会跟面板对不上）。
"""

import json

from django.test import Client, TestCase

from core import library_trash
from core.models import Paper, PublishedQuestion


class TrashSummaryModeTests(TestCase):
    """?summary=1 只回计数，不回明细。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="月考卷.pdf", kind="pdf", sha256="d" * 64)
        for number in range(1, 4):
            PublishedQuestion.objects.create(
                paper=self.paper,
                source_filename="月考卷.pdf",
                number=number,
                question_type="fill_blank",
                version=1,
                status=PublishedQuestion.Status.WITHDRAWN,
                review_source="human",
                content={"stem": f"第 {number} 题", "options": {}, "answer": "甲", "section": "第一章"},
                content_hash=f"{number}".ljust(64, "c"),
                search_text="",
            )

    def summary(self, **kwargs):
        return self.client.get("/api/library/trash?summary=1", HTTP_X_QB_REQUEST="1", **kwargs)

    def full(self, **kwargs):
        return self.client.get("/api/library/trash", HTTP_X_QB_REQUEST="1", **kwargs)

    def test_summary_mode_exists(self):
        """首屏要有一条只回计数的路。"""
        response = self.summary()
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertIn("counts", body)

    def test_summary_mode_returns_no_detail_rows(self):
        """摘要模式不许把 445 行明细塞进响应 —— 那正是首屏要省的东西。"""
        body = self.summary().json()
        self.assertNotIn("archived_papers", body, "摘要模式不该带资料明细")
        self.assertNotIn("withdrawn_questions", body, "摘要模式不该带试题明细")

    def test_summary_counts_match_the_full_payload(self):
        """徽标上的数字必须跟面板里看到的对得上。"""
        full_counts = self.full().json()["counts"]
        summary_counts = self.summary().json()["counts"]
        self.assertEqual(summary_counts, full_counts, "摘要计数必须和全量计数一致")

    def test_summary_counts_are_right(self):
        """计数本身要对。"""
        counts = self.summary().json()["counts"]
        self.assertEqual(counts["questions"], 3, "三道下架题")
        self.assertEqual(counts["papers"], 0, "这份资料没归档")

        self.paper.archived = True
        self.paper.save(update_fields=["archived"])
        counts = self.summary().json()["counts"]
        self.assertEqual(counts["papers"], 1, "归档了就得算一份资料")

    def test_summary_response_is_tiny(self):
        """摘要响应要小到可以忽略。"""
        raw = self.summary().content
        self.assertLess(len(raw), 400, f"摘要响应不该有几百字节：{len(raw)}")

    def test_summary_still_needs_the_same_guard(self):
        """摘要不是后门，守卫照旧。"""
        self.assertEqual(self.client.get("/api/library/trash?summary=1").status_code, 403)

    def test_summary_never_reads_the_heavy_question_columns(self):
        """下架题的 content / extras 是两个大头，摘要模式一个都不该读。"""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            library_trash.trash_summary_payload()
        sql = " ".join(query["sql"] for query in captured.captured_queries)
        self.assertNotIn('"core_publishedquestion"."content"', sql, "摘要不该读题面 content")
        self.assertNotIn('"core_publishedquestion"."extras"', sql, "摘要不该读 extras")


class TrashSummaryDoesNotBreakTheFullModeTests(TestCase):
    """加了摘要模式，全量模式不许被带坏。"""

    def setUp(self):
        self.client = Client()

    def test_full_mode_still_returns_detail_rows(self):
        """全量模式照旧给明细（面板、恢复、删除都要用）。"""
        body = self.client.get("/api/library/trash", HTTP_X_QB_REQUEST="1").json()
        self.assertIn("archived_papers", body)
        self.assertIn("withdrawn_questions", body)
        self.assertIn("counts", body)

    def test_summary_flag_is_only_honoured_when_exactly_one(self):
        """?summary=0 / ?summary=yes 不许被当成摘要 —— 那会让面板拿到空明细。"""
        for flag in ("0", "no", "true", "yes", ""):
            body = self.client.get(f"/api/library/trash?summary={flag}", HTTP_X_QB_REQUEST="1").json()
            self.assertIn("archived_papers", body, f"summary={flag!r} 不是摘要模式，必须照旧给全量明细")
