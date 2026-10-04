"""云端真的读出了内容，自动切题就必须真的切出题卡。

这是本机上最贵的一个 bug，而且它安静了很久：照片和扫描件没有 PDF 文字层，
导入时每一页都被标成“手工”，MinerU 把内容读回来之后没人把那面标记翻过来；
切题保存题卡时跳过所有落在“手工页”上的题，于是十道题被整批丢掉、零张题卡、
零条记录，界面还显示“待你终审”。这一版把标记按云端的真实结果翻过来，并且
一旦整批被丢弃就说出来，而不是交出一张空卷。
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase
from PIL import Image
import pymupdf as fitz

from . import intake, mineru, pipeline, readers, views
from .models import Block, Paper, Question, QuestionGroup
from . import test_manual_intake_review as manual_review


def scan_pdf(pages: int = 1) -> bytes:
    """A real page with no text layer: what a phone photo import produces."""
    buffer = io.BytesIO()
    Image.new("RGB", (595, 842), "white").save(buffer, format="PNG")
    with fitz.open() as document:
        for _ in range(pages):
            document.new_page(width=595, height=842).insert_image(
                fitz.Rect(0, 0, 595, 842), stream=buffer.getvalue())
        return document.tobytes()


CLOUD_BLOCKS = [
    {"page_idx": 0, "seq": 0, "type": "text", "bbox": [40.0, 60.0, 540.0, 200.0],
     "text": "1. 设 x = 1，求 y 的值。"},
    {"page_idx": 0, "seq": 1, "type": "text", "bbox": [40.0, 240.0, 540.0, 380.0],
     "text": "2. 设 x = 2，求 y 的值。"},
    {"page_idx": 0, "seq": 2, "type": "text", "bbox": [40.0, 420.0, 540.0, 560.0],
     "text": "3. 设 x = 3，求 y 的值。"},
]


class CloudContentBecomesAutomaticAgainTests(TestCase):
    setUp = manual_review.ManualIntakeReviewTests.setUp
    paper = manual_review.ManualIntakeReviewTests.paper

    def cloud_parsed_paper(self, *, pages: int = 1):
        """A scan import that is queued for the cloud, exactly as a photo is."""
        paper = self.paper(data=scan_pdf(pages))
        with mock.patch.object(pipeline, "readers"), \
                mock.patch.object(readers, "configured", return_value=True), \
                mock.patch.object(readers, "_reading_ready", return_value=True, create=True):
            paper = intake.prepare_auto(paper, allow_cloud=True, cloud_ready=True)
        self.assertEqual(paper.processing_plan["mode"], "mineru")
        # The import has no text layer, so every page starts out marked manual.
        self.assertEqual({p["mode"] for p in paper.processing_plan["pages"]}, {"manual"})
        return paper

    def run_parse(self, paper, blocks):
        with mock.patch.object(pipeline, "request_extract_file_from_pool"), \
                mock.patch.object(pipeline, "load_blocks", return_value=blocks), \
                mock.patch.object(pipeline.Path, "is_file", return_value=True):
            pipeline.parse(paper, revision=int(paper.processing_plan["revision"]))
        paper.refresh_from_db()
        return paper

    def test_pages_the_cloud_read_become_automatic_pages_again(self):
        paper = self.cloud_parsed_paper()
        self.run_parse(paper, CLOUD_BLOCKS)
        pages = paper.processing_plan["pages"]
        self.assertEqual([p["mode"] for p in pages], ["mineru"],
                         "the cloud read this page, so it is no longer a manual page")
        self.assertTrue(pages[0]["text_characters"] > 0)

    def test_a_page_the_cloud_found_nothing_on_stays_manual_and_says_so(self):
        paper = self.cloud_parsed_paper(pages=2)
        # Only page 0 comes back with content; page 1 is blank to the cloud.
        self.run_parse(paper, CLOUD_BLOCKS)
        modes = [p["mode"] for p in paper.processing_plan["pages"]]
        self.assertEqual(modes, ["mineru", "manual"])
        self.assertIn("云端没有读出这一页", paper.processing_plan["pages"][1]["warnings"][-1])
        self.assertIn("第 2 页", " ".join(paper.processing_plan.get("warnings") or []))

    def test_the_end_to_end_line_cuts_questions_instead_of_dropping_them(self):
        # 导入 → 切题 → 一张题卡都不能少。这是本机真实撞到的那条主线。
        paper = self.cloud_parsed_paper()
        self.run_parse(paper, CLOUD_BLOCKS)
        with mock.patch.object(pipeline, "_record_cut_result",
                               wraps=pipeline._record_cut_result):
            pipeline.segment_paper(paper)
        paper.refresh_from_db()
        self.assertGreaterEqual(paper.questions.count(), 1,
                                "the run had printed question numbers; dropping them all is the bug")
        self.assertEqual(paper.status, Paper.Status.READING)
        verdict = (paper.processing_plan.get("cut_result") or {}).get("verdict")
        self.assertNotEqual(verdict, "failed")

    def test_a_run_that_drops_everything_says_which_pages_why(self):
        # 云端什么也没读回来时，题号可以被算出来，但题卡必须留在原卷上给人框。
        # 以前这里是静默 continue，现在要说出来。
        paper = self.paper(data=scan_pdf())
        group = QuestionGroup.objects.create(paper=paper, title=paper.display_name, sequence=0,
            page_start=1, page_end=1, metadata={"pages": [0], "source": "manual"})
        for block in CLOUD_BLOCKS:
            Block.objects.create(paper=paper, **block)
        paper.status = Paper.Status.SEGMENTING
        paper.processing_plan = {"revision": 3, "mode": "mineru",
                                 "pages": [{"page_idx": 0, "mode": "manual", "warnings": []}]}
        paper.save()
        with self.assertRaises(RuntimeError) as caught:
            pipeline.segment_paper(paper)
        message = str(caught.exception)
        self.assertIn("没有读出", message)
        self.assertIn("继续 AI 切题", message)
        self.assertIn("原卷和已保存的题卡都保留", message)
        paper.refresh_from_db()
        self.assertEqual(paper.questions.count(), 0)
        del group


class TheFailedCutOffersTheWayBackTests(TestCase):
    setUp = manual_review.ManualIntakeReviewTests.setUp
    paper = manual_review.ManualIntakeReviewTests.paper

    def test_a_failed_cut_with_a_saved_parse_is_recoverable_without_paying_again(self):
        paper = self.paper(data=scan_pdf())
        Block.objects.create(paper=paper, page_idx=0, seq=0, type="text",
                             bbox=[40.0, 60.0, 540.0, 200.0], text="1. 设 x = 1，求 y 的值。")
        paper.status = Paper.Status.FAILED
        paper.processing_plan = {"revision": 3, "mode": "mineru",
                                 "cut_result": {"verdict": "failed", "found": 0, "expected": 1,
                                                "missing": [1], "message": "自动切题没有切出任何题目。",
                                                "source": "printed_numbers"}}
        paper.save()
        data = views.paper_json(paper)
        # The parse is still on disk, so 继续 AI 切题 reuses it and no cloud
        # fee is involved; the entry point must say that the paper is retryable.
        self.assertTrue(paper.blocks.exists())
        self.assertEqual(data["cut_result"]["verdict"], "failed")
        self.assertEqual(data["parse_mode"], "mineru")
