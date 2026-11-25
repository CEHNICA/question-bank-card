"""Stage 4 tests: deduplicated usage tracking, exclusion filters, Word A3/seal-line/scoring, and PDF parity."""

from __future__ import annotations

import io
import json
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from . import library, library_drafts, library_export, library_pdf, library_usage
from .models import Paper, PublishedQuestion, Question


def _text_field(value: str) -> dict:
    if not value:
        return {"source": "", "blocks": []}
    return {
        "source": value,
        "blocks": [
            {
                "type": "text",
                "start": 0,
                "end": len(value),
                "segments": [{"type": "text", "start": 0, "end": len(value)}],
            }
        ],
    }


class Stage4UsageAndExamTests(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self._override = override_settings(DATA_ROOT=Path(self.tmpdir.name))
        self._override.enable()
        self.addCleanup(self._override.disable)
        with library_usage._LOCK:
            library_usage._CACHE = None
        self.headers = {"HTTP_X_QB_REQUEST": "1", "HTTP_REFERER": "http://127.0.0.1/eagle"}
        self.paper = Paper.objects.create(filename="期中样卷.pdf")
        self.q1 = self._make_pub(1, "single_choice", "设集合 A={1,2}，下列元素属于 A 的是", {"A": "1", "B": "2"}, "A")
        self.q2 = self._make_pub(2, "free_response", "求函数 f(x)=x+1 在 [0,1] 上的最大值。", {}, "2")

    def _make_pub(self, num: int, kind: str, stem: str, options: dict, answer: str) -> PublishedQuestion:
        q = Question.objects.create(
            paper=self.paper,
            number=num,
            question_type=kind,
            stem=stem,
            answer=answer,
            analysis="解析过程",
            options=options,
            state="green",
        )
        content = {
            "stem": stem,
            "answer": answer,
            "analysis": "解析过程",
            "options": options,
            "question_type": kind,
            "origin": "2025·测试",
            "figures": [],
            "sources": [],
            "section": "",
            "document_id": str(self.paper.id),
            "source_filename": self.paper.filename,
        }
        return PublishedQuestion.objects.create(
            paper=self.paper,
            question=q,
            number=num,
            source_filename=self.paper.filename,
            question_type=kind,
            version=1,
            status="published",
            content=content,
            content_hash=library.content_hash(content),
            extras={},
        )

    def _fields_for(self, pubs: list[PublishedQuestion]) -> dict[str, dict]:
        rendered: dict[str, dict] = {}
        for pub in pubs:
            c = pub.content
            item = {name: _text_field(str(c.get(name) or "")) for name in ("stem", "origin", "answer", "analysis")}
            for k, v in (c.get("options") or {}).items():
                item[f"options.{k}"] = _text_field(str(v))
            rendered[str(pub.id)] = item
        return rendered

    def test_docx_usage_deduplicates_per_exam_and_filters_library_browse(self) -> None:
        batch_key = str(uuid.uuid4())
        before_db = list(PublishedQuestion.objects.values())
        payload = {
            "ids": [str(self.q1.id), str(self.q2.id)],
            "title": "高一期中模拟",
            "rendered_fields": self._fields_for([self.q1, self.q2]),
            "solutions": {},
            "format": "docx",
            "print_options": {
                "paper_spec": "a3_2col",
                "seal_line": True,
                "scoring_enabled": True,
                "exam_duration_minutes": 100,
                "type_scores": {"single_choice": 5},
                "question_scores": {str(self.q2.id): 14},
                "record_usage": True,
                "usage_batch_key": batch_key,
            },
        }
        res1 = self.client.post(
            "/api/library/export-docx",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            **self.headers,
        )
        self.assertEqual(res1.status_code, 200)
        res2 = self.client.post(
            "/api/library/export-docx",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR="127.0.0.1",
            **self.headers,
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(list(PublishedQuestion.objects.values()), before_db)

        usage_res = self.client.get("/api/library/usage", **self.headers)
        self.assertEqual(usage_res.status_code, 200)
        batches = usage_res.json()["batches"]
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["export_count"], 2)
        self.assertEqual(batches[0]["paper_spec"], "a3_2col")
        self.assertEqual(batches[0]["total_score"], 19.0)
        batch_id = batches[0]["id"]

        browse_all = self.client.get("/api/library?limit=50", **self.headers).json()["items"]
        self.assertEqual(len(browse_all), 2)
        self.assertEqual(browse_all[0]["usage_summary"]["count"], 1)

        excl_days = self.client.get("/api/library?exclude_used_days=30", **self.headers).json()["items"]
        self.assertEqual(excl_days, [])

        excl_batch = self.client.get(f"/api/library?exclude_used_batch={batch_id}", **self.headers).json()["items"]
        self.assertEqual(excl_batch, [])

    def test_delete_usage_batch_restores_question_in_exclude_filter(self) -> None:
        batch = library_usage.record_exam_export(
            ids=[str(self.q1.id)], title="周测一", paper_spec="a4", export_format="docx"
        )
        res_excl = self.client.get("/api/library?exclude_used_days=30", **self.headers)
        self.assertEqual([x["id"] for x in res_excl.json()["items"]], [str(self.q2.id)])

        del_res = self.client.delete(f"/api/library/usage/{batch['id']}", **self.headers)
        self.assertEqual(del_res.status_code, 200)
        res_after = self.client.get("/api/library?exclude_used_days=30", **self.headers)
        self.assertEqual(len(res_after.json()["items"]), 2)

    def test_docx_a3_two_column_mirrored_seal_and_scoring_ooxml(self) -> None:
        opts = library_drafts._print_options(
            {
                "paper_spec": "a3_2col",
                "seal_line": True,
                "scoring_enabled": True,
                "exam_duration_minutes": 100,
                "type_scores": {"single_choice": 5},
                "question_scores": {str(self.q2.id): 14},
            }
        )
        payload = {
            "ids": [str(self.q1.id), str(self.q2.id)],
            "title": "A3正规月考卷",
            "rendered_fields": self._fields_for([self.q1, self.q2]),
            "solutions": {},
            "format": "docx",
            "print_options": opts,
        }
        blob, _filename, _mime, count = library_export.export(payload)
        self.assertEqual(count, 2)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = set(zf.namelist())
            for part in (
                "word/document.xml",
                "word/settings.xml",
                "word/header1.xml",
                "word/header2.xml",
                "word/footer1.xml",
                "word/footer2.xml",
            ):
                self.assertIn(part, names)
                ET.fromstring(zf.read(part))
            doc_xml = zf.read("word/document.xml").decode("utf-8")
            set_xml = zf.read("word/settings.xml").decode("utf-8")
            h1_xml = zf.read("word/header1.xml").decode("utf-8")
            h2_xml = zf.read("word/header2.xml").decode("utf-8")
            f2_xml = zf.read("word/footer2.xml").decode("utf-8")
        self.assertIn('w:orient="landscape"', doc_xml)
        self.assertIn('w:num="2"', doc_xml)
        self.assertIn('w:sep="1"', doc_xml)
        self.assertIn("<w:mirrorMargins", set_xml)
        self.assertIn("<w:evenAndOddHeaders", set_xml)
        self.assertIn("tbRl", h1_xml)
        self.assertIn("学校：", h1_xml)
        self.assertIn("密", h1_xml)
        self.assertIn("学校：", h2_xml)
        self.assertNotEqual(h1_xml, h2_xml)
        self.assertIn("PAGE", f2_xml)
        self.assertIn("NUMPAGES", f2_xml)
        self.assertIn("考试时间：100 分钟", doc_xml)
        self.assertIn("满分：19 分", doc_xml)
        self.assertIn("一、选择题（共 1 小题，每小题 5 分，共 5 分）", doc_xml)
        self.assertIn("（14分）", doc_xml)

    def test_pdf_a3_html_driver_and_usage_recording(self) -> None:
        seen_docs: list[str] = []

        def fake_render(document: str) -> tuple[bytes, int]:
            seen_docs.append(document)
            return b"%PDF-1.7\nsynthetic\n%%EOF\n", 2

        payload = {
            "ids": [str(self.q1.id), str(self.q2.id)],
            "title": "A3 PDF 卷",
            "rendered_fields": self._fields_for([self.q1, self.q2]),
            "solutions": {},
            "format": "pdf",
            "print_options": {
                "paper_spec": "a3_2col",
                "seal_line": True,
                "scoring_enabled": True,
                "exam_duration_minutes": 90,
                "question_scores": {str(self.q2.id): 14},
                "record_usage": True,
                "usage_batch_key": str(uuid.uuid4()),
            },
        }
        with patch.object(library_pdf, "_render", side_effect=fake_render):
            res = self.client.post(
                "/api/library/export-pdf",
                data=json.dumps(payload),
                content_type="application/json",
                REMOTE_ADDR="127.0.0.1",
                **self.headers,
            )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(seen_docs), 1)
        html_doc = seen_docs[0]
        self.assertIn("@page{size:420mm 297mm;margin:0}", html_doc)
        self.assertIn("width:180mm", html_doc)
        self.assertIn("#printPaper{width:420mm", html_doc)
        self.assertIn("满分：19 分", html_doc)
        self.assertIn("（14 分）", html_doc)
        batches = library_usage.list_batches()
        self.assertEqual(len(batches), 1)
        self.assertEqual(batches[0]["last_format"], "pdf")
        self.assertEqual(batches[0]["paper_spec"], "a3_2col")
