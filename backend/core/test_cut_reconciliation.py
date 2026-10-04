"""自动切题必须对账：原卷声明了多少题，和真的切出多少题对得上。

24 题的卷子带两张卡显示成“待你终审”，不是因为切题抛了异常——它安静地
少切了：缺号检测只看两个已定位题号之间的洞，只切出 1、2 时后面没有
任何东西可比，3–24 就永远不被提起。纯扫描件更彻底：一句文字都读不出来，
机器没有任何依据说自己切全了。这两种失败在界面上必须长得不一样。
"""

import hashlib
import io
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from PIL import Image
import pymupdf as fitz

from . import intake, pipeline, segment, views
from .models import Block, Paper, Question, QuestionGroup
from . import test_manual_intake_review as manual_review


def scan_pdf() -> bytes:
    """A real page with no text layer: the shape of every phone photo import."""
    buffer = io.BytesIO()
    Image.new("RGB", (595, 842), "white").save(buffer, format="PNG")
    with fitz.open() as document:
        document.new_page(width=595, height=842).insert_image(
            fitz.Rect(0, 0, 595, 842), stream=buffer.getvalue())
        return document.tobytes()


class ReconciliationCase(TestCase):
    setUp = manual_review.ManualIntakeReviewTests.setUp
    paper = manual_review.ManualIntakeReviewTests.paper

    def scanned_paper(self, *, count, printed, data=None):
        paper = self.paper(data=data or scan_pdf())
        group = QuestionGroup.objects.create(paper=paper, title=paper.display_name, sequence=0,
            page_start=1, page_end=len(paper.pages), metadata={"pages": [0], "source": "manual"})
        for index in range(1, printed + 1):
            Block.objects.create(paper=paper, page_idx=0, seq=index, type="text",
                bbox=[40.0, 60.0, 540.0, 200.0], text=f"{index}. 设 x = {index}，求 y 的值。")
        for number in range(1, count + 1):
            Question.objects.create(paper=paper, group=group, number=number, body_mode="source_image",
                processing_mode="manual", question_type="free_response", state=Question.State.YELLOW,
                regions=[{"page_idx": 0, "bbox": [40.0, 60.0, 540.0, 200.0]}])
        return paper


class PrintedNumbersTests(ReconciliationCase):
    """The other half of gap detection: what the paper itself prints."""

    def test_numbers_printed_after_the_last_located_start_are_still_reported(self):
        paper = self.scanned_paper(count=0, printed=6)
        located = [segment.Start(number=number, page=0, x=40, y=60 * number, seq=number, score=3.0)
                   for number in (1, 2)]
        # missing_numbers only ever sees a hole *between* two starts, so a run
        # that stops at 第 2 题 leaves 3–6 structurally unreportable.
        self.assertEqual(segment.missing_numbers(located), [])
        self.assertEqual(segment.printed_numbers(pipeline._block_dicts(paper)), [1, 2, 3, 4, 5, 6])

    def test_a_scan_with_no_text_declares_nothing_rather_than_nothing_wrong(self):
        paper = self.scanned_paper(count=0, printed=0)
        self.assertEqual(segment.printed_numbers(pipeline._block_dicts(paper)), [])


class ReconcileCutTests(ReconciliationCase):
    def test_two_cards_out_of_a_six_question_paper_is_degraded_not_finished(self):
        paper = self.scanned_paper(count=2, printed=6)
        result = pipeline.reconcile_cut(paper)
        self.assertEqual(result["verdict"], pipeline.CUT_DEGRADED)
        self.assertEqual((result["expected"], result["found"]), (6, 2))
        # “少了几张”不是老师能行动的话；没切出的题号必须被点名。
        self.assertEqual(result["missing"], [3, 4, 5, 6])
        self.assertIn("3、4、5、6", result["message"])

    def test_every_printed_number_covered_is_complete(self):
        paper = self.scanned_paper(count=6, printed=6)
        result = pipeline.reconcile_cut(paper)
        self.assertEqual(result["verdict"], pipeline.CUT_COMPLETE)
        self.assertEqual((result["expected"], result["found"], result["missing"], result["message"]),
                         (6, 6, [], ""))

    def test_a_cut_that_produced_nothing_is_a_hard_failure(self):
        paper = self.scanned_paper(count=0, printed=6)
        result = pipeline.reconcile_cut(paper, reason="自动切题没有切出任何题目：本地文字层没有可靠题卡。")
        self.assertEqual(result["verdict"], pipeline.CUT_FAILED)
        self.assertEqual(result["found"], 0)
        self.assertIn("本地文字层没有可靠题卡", result["message"])

    def test_a_scan_with_cards_cannot_claim_to_be_complete(self):
        # 读不出文字不等于没有题：结论必须说“请核对”，不能说“切好了”。
        paper = self.scanned_paper(count=2, printed=0)
        result = pipeline.reconcile_cut(paper)
        self.assertEqual(result["verdict"], pipeline.CUT_UNVERIFIED)
        self.assertEqual((result["expected"], result["source"]), (0, "no_text"))
        self.assertIn("核对", result["message"])


class ImportRecordsTheReconciliationTests(ReconciliationCase):
    def test_an_auto_import_with_no_cards_never_arrives_looking_finished(self):
        paper = self.paper(data=scan_pdf())
        with mock.patch.object(pipeline, "parse", side_effect=AssertionError("unexpected cloud parsing")):
            paper = intake.prepare_auto(paper, cloud_ready=True)
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertEqual(paper.processing_plan["cut_result"]["verdict"], pipeline.CUT_FAILED)
        self.assertEqual(paper.status, Paper.Status.FAILED)
        # 本地为什么没切出来，是老师真正要读的那句话。
        self.assertIn("已保留原页", paper.error)

    def test_the_worker_reconciles_after_segmenting_and_stops_before_reading(self):
        paper = self.paper(data=scan_pdf())
        paper.status, paper.processing_plan = Paper.Status.QUEUED, {"revision": 3, "mode": "mineru"}
        paper.save()
        with mock.patch.object(pipeline, "parse",
                side_effect=lambda target, revision: pipeline._set(target, status=Paper.Status.SEGMENTING)), \
                mock.patch.object(pipeline, "segment_paper",
                side_effect=lambda target: pipeline._set(target, status=Paper.Status.READING)), \
                mock.patch.object(pipeline, "read_questions") as reading:
            pipeline.process_paper(paper)
        reading.assert_not_called()
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.FAILED)
        self.assertEqual(paper.processing_plan["cut_result"]["verdict"], pipeline.CUT_FAILED)

    def test_a_stale_run_cannot_overwrite_a_paper_the_teacher_already_moved_on(self):
        paper = self.paper(data=scan_pdf())
        paper.status, paper.processing_plan = Paper.Status.QUEUED, {"revision": 3, "mode": "mineru"}
        paper.save()
        # A newer run (转手工 or 重新切题) has already taken the paper over.
        paper.processing_plan = {**paper.processing_plan, "revision": 4}
        paper.save()
        self.assertIsNone(pipeline._record_cut_result(paper, 3))
        paper.refresh_from_db()
        self.assertNotIn("cut_result", paper.processing_plan or {})


class ReimportingTheSameOriginalTests(ReconciliationCase):
    def upload(self, data, **extra):
        handle = io.BytesIO(data)
        handle.name = "scan.pdf"
        return self.client.post("/api/papers", {"file": [handle], **extra}, HTTP_X_QB_REQUEST="1")

    def test_a_paper_whose_automatic_cut_found_nothing_still_counts_as_already_imported(self):
        # 这份卷已经在库里了，只是没切出题。再传一次同一份文件应该打开它，
        # 而不是多出一份一模一样的任务让老师自己分辨哪份是真的。
        same = scan_pdf()
        first = self.upload(same, parse_mode="auto", allow_cloud="0")
        self.assertEqual(first.status_code, 201, first.content)
        again = self.upload(same, parse_mode="auto", allow_cloud="0")
        self.assertEqual(again.status_code, 200, again.content)
        self.assertTrue(again.json().get("duplicate"))
        self.assertEqual(Paper.objects.count(), 1)


class StatusLabelDoesNotLieTests(ReconciliationCase):
    def label(self, verdict, missing=()):
        paper = self.paper(data=scan_pdf())
        paper.status, paper.processing_plan = Paper.Status.READY, {
            "revision": 1, "mode": "manual",
            "cut_result": {"verdict": verdict, "missing": list(missing), "found": 1, "expected": 4,
                           "message": "自动切题没有切出任何题目", "source": "printed_numbers"}}
        paper.save()
        return views.paper_json(paper)["status_label"]

    def test_an_incomplete_cut_is_never_labelled_as_awaiting_final_review(self):
        for verdict, missing in (("failed", ()), ("degraded", [3, 4]), ("unverified", ())):
            label = self.label(verdict, missing)
            assert label != "待你终审", f"{verdict} must not read as finished"
        self.assertEqual(self.label("degraded", [3, 4]), "切题不全")
        self.assertEqual(self.label("failed"), "自动切题失败")

    def test_a_cut_whose_missing_questions_are_now_cut_reads_as_complete_again(self):
        # 老师自己把第 3、4 题框出来了，就不该继续被告知它们没切出。
        paper = self.paper(data=scan_pdf())
        group = QuestionGroup.objects.create(paper=paper, title=paper.display_name, sequence=0,
            page_start=1, page_end=1, metadata={"pages": [0], "source": "manual"})
        for number in (1, 3, 4):
            Question.objects.create(paper=paper, group=group, number=number, body_mode="source_image",
                processing_mode="manual", question_type="free_response", state=Question.State.YELLOW,
                regions=[{"page_idx": 0, "bbox": [40.0, 60.0, 540.0, 200.0]}])
        paper.status, paper.processing_plan = Paper.Status.READY, {
            "revision": 1, "mode": "manual",
            "cut_result": {"verdict": "degraded", "missing": [2, 3, 4], "found": 1, "expected": 4,
                           "message": "原卷上印着第 2、3、4 题的题号", "source": "printed_numbers"}}
        paper.save()
        data = views.paper_json(paper)
        self.assertEqual(data["cut_result"]["missing"], [2])
        self.assertEqual(data["status_label"], "切题不全")

    def test_a_complete_cut_keeps_the_ordinary_label(self):
        self.assertEqual(self.label("complete"), "待你终审")

    def test_the_message_travels_with_the_label_so_the_page_can_explain_itself(self):
        paper = self.paper(data=scan_pdf())
        paper.status, paper.processing_plan = Paper.Status.READY, {
            "revision": 1, "mode": "manual",
            "cut_result": {"verdict": "degraded", "missing": [3, 4], "found": 2, "expected": 6,
                           "message": "原卷上印着第 3、4 题的题号，但这次没有切出对应的题卡。", "source": "printed_numbers"}}
        paper.save()
        self.assertEqual(views.paper_json(paper)["cut_result"]["missing"], [3, 4])
