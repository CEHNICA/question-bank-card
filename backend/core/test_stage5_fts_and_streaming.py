"""Stage 5 tests: SQLite FTS5 full-text search, live/bulk sync, facets parity, and display index."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from django.db import connection
from django.test import TestCase, override_settings

from . import library, library_browse, library_fts, library_tagging, library_usage
from .models import LibraryTag, LibraryTagAssignment, Paper, PublishedQuestion, Question


class Stage5FtsSearchTests(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._override = override_settings(DATA_ROOT=Path(self.tmpdir.name))
        self._override.enable()
        self.addCleanup(self._override.disable)
        with library_usage._LOCK:
            library_usage._CACHE = None
        self.headers = {"HTTP_X_QB_REQUEST": "1", "HTTP_REFERER": "http://127.0.0.1/eagle"}
        self.paper = Paper.objects.create(filename="高三一轮总复习.pdf", kind="pdf", sha256="d" * 64)
        self.counter = 0

    def _pub(
        self,
        stem: str,
        *,
        kind: str = "single_choice",
        answer: str = "A",
        section: str = "",
        extras: dict | None = None,
        status: str = PublishedQuestion.Status.PUBLISHED,
    ) -> PublishedQuestion:
        self.counter += 1
        q = Question.objects.create(
            paper=self.paper,
            number=self.counter,
            question_type=kind,
            stem=stem,
            answer=answer,
            analysis="",
            options={"A": "x", "B": "y"} if kind == "single_choice" else {},
            section=section,
            state="green",
        )
        content = {
            "stem": stem,
            "answer": answer,
            "analysis": "",
            "options": q.options,
            "question_type": kind,
            "origin": "2025海淀期中",
            "figures": [],
            "sources": [],
            "section": section,
            "document_id": str(self.paper.id),
            "source_filename": self.paper.filename,
        }
        ex = extras or {}
        return PublishedQuestion.objects.create(
            paper=self.paper,
            question=q,
            number=self.counter,
            source_filename=self.paper.filename,
            question_type=kind,
            version=1,
            status=status,
            content=content,
            content_hash=library.content_hash(content),
            search_text=library._search_text(content, ex),
            extras=ex,
            tags_text=library.tags_text(library.tags_of(ex)),
        )

    def test_tokenize_and_build_query_cover_cjk_ascii_substrings_and_symbols(self) -> None:
        doc = library_fts.tokenize_for_fts("已知函数arcsin(x)导数极值2025海淀期中a∥b")
        self.assertIn("导", doc)
        self.assertIn("arcsin", doc)
        self.assertIn("si", doc)
        self.assertIn("in", doc)
        self.assertIn("20", doc)

        self.assertEqual(library_fts.build_fts_query("圆"), '"圆"')
        self.assertEqual(library_fts.build_fts_query("导数"), '"导 数"')
        self.assertEqual(library_fts.build_fts_query("sin"), '"si" AND "in"')
        self.assertIsNone(library_fts.build_fts_query("∥"))

    def test_signal_withdraw_supersede_and_bulk_sync_fts5(self) -> None:
        pub = self._pub("正弦定理应用特殊标记甲")
        res = self.client.get("/api/library", {"q": "特殊标记甲"}, **self.headers).json()
        self.assertEqual([x["id"] for x in res["items"]], [str(pub.id)])
        self.assertEqual(res.get("search_engine"), "fts5")

        pub.status = PublishedQuestion.Status.WITHDRAWN
        pub.save()
        after_withdraw = self.client.get("/api/library", {"q": "特殊标记甲"}, **self.headers).json()
        self.assertEqual(after_withdraw["items"], [])

        live = self._pub("椭圆离心率求解题干", section="习题 6.1")
        tag = LibraryTag.objects.create(kind=LibraryTag.Kind.KNOWLEDGE, name="解析几何核心点")
        LibraryTagAssignment.objects.create(publication=live, tag=tag, needs_review=False)
        library_tagging._sync_projection(live.pk)
        by_tag = self.client.get("/api/library", {"q": "解析几何核心点"}, **self.headers).json()
        self.assertEqual([x["id"] for x in by_tag["items"]], [str(live.id)])

        library.update_published_sections({str(live.id): "章节八"})
        by_sec = self.client.get("/api/library", {"q": "章节八"}, **self.headers).json()
        self.assertEqual([x["id"] for x in by_sec["items"]], [str(live.id)])

        library.rename_paper(self.paper, "新高三冲刺卷")
        by_paper = self.client.get("/api/library", {"q": "冲刺卷"}, **self.headers).json()
        self.assertIn(str(live.id), [x["id"] for x in by_paper["items"]])

    def test_fts5_parity_facets_exclude_used_and_scale(self) -> None:
        filler = [
            PublishedQuestion(
                paper=self.paper,
                number=100 + i,
                source_filename=self.paper.filename,
                question_type="single_choice",
                version=1,
                status=PublishedQuestion.Status.PUBLISHED,
                content={"stem": f"常规练习第{i}题", "options": {"A": "1"}, "answer": "A", "analysis": ""},
                content_hash=f"h-{i}",
                search_text=library.search_key(f"常规练习第{i}题"),
                extras={},
            )
            for i in range(600)
        ]
        PublishedQuestion.objects.bulk_create(filler)
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {library_fts.FTS_TABLE}")

        t1 = self._pub("已知函数 arcsin(x) 导数极值靶标一")
        t2 = self._pub("向量垂直与平行关系 a∥b 靶标二", kind="free_response")
        tag = LibraryTag.objects.create(kind=LibraryTag.Kind.KNOWLEDGE, name="函数专题")
        LibraryTagAssignment.objects.create(publication=t1, tag=tag, needs_review=False)

        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {library_fts.FTS_TABLE}")
        self.assertTrue(library_fts.ensure_populated())

        for query, expected in [
            ("导数极值靶标一", [str(t1.id)]),
            ("sin", [str(t1.id)]),
            ("∥", [str(t2.id)]),
            ("第599题", [str(filler[-1].id)]),
        ]:
            like_ids = {
                str(pk)
                for pk in PublishedQuestion.objects.filter(
                    status=PublishedQuestion.Status.PUBLISHED, search_text__contains=library.search_key(query)
                ).values_list("id", flat=True)
            }
            fts_ids = {str(pk) for pk in library_browse.library_rows({"q": query}).values_list("id", flat=True)}
            self.assertEqual(fts_ids, like_ids)
            self.assertEqual(sorted(fts_ids), sorted(expected))

        bulk_res = self.client.get(
            "/api/library?bulk=1&limit=200&offset=0&slim=1&sort=display&q=导数极值靶标一", **self.headers
        ).json()
        self.assertEqual(bulk_res.get("search_engine"), "fts5")
        self.assertEqual([x["id"] for x in bulk_res["items"]], [str(t1.id)])

        facets_res = self.client.get("/api/library?q=导数极值靶标一", **self.headers).json()
        self.assertEqual(facets_res["facets"]["tags"][0]["tag"], "函数专题")
        self.assertEqual(facets_res["facets"]["tags"][0]["count"], 1)

        library_usage.record_exam_export(ids=[str(t1.id)], title="最近测试卷", export_format="pdf")
        excl = self.client.get("/api/library?ids_only=1&q=导数极值靶标一&exclude_used_days=30", **self.headers).json()
        self.assertEqual(excl["ids"], [])

        multi_type = self.client.get(
            "/api/library?ids_only=1&type=single_choice,free_response&q=靶标", **self.headers
        ).json()
        self.assertEqual(set(multi_type["ids"]), {str(t1.id), str(t2.id)})

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='pubq_status_display'")
            self.assertIsNotNone(cursor.fetchone())
