"""自动切题什么也没切出来时，必须失败并且说清楚为什么。

这一版不再对账。原来会把卷面上印着的题号跟真的切出来的题号比一遍，然后
点名“缺了第几题”。卷末的答案解析同样印着「33.」「51.」，而文字层里没有
任何东西能说明哪一行是题、哪一行是答案——实测一份 25 题的卷子，答案解析里
的两条被当成了漏题。假警报比不报更糟：老师看到一次不实的“漏题”，就再也不
会相信旁边那句同样重要的“待你终审”。

留下的是一句不依赖任何猜测的事实：这张卷一张题卡都没有。
"""

import io
from unittest import mock

from django.test import TestCase
from PIL import Image
import pymupdf as fitz

from . import intake, pipeline, views
from .models import Paper, Question, QuestionGroup
from . import test_manual_intake_review as manual_review


def scan_pdf() -> bytes:
    """A real page with no text layer: the shape of every phone photo import."""
    buffer = io.BytesIO()
    Image.new("RGB", (595, 842), "white").save(buffer, format="PNG")
    with fitz.open() as document:
        document.new_page(width=595, height=842).insert_image(
            fitz.Rect(0, 0, 595, 842), stream=buffer.getvalue())
        return document.tobytes()


class NothingWasCutCase(TestCase):
    setUp = manual_review.ManualIntakeReviewTests.setUp
    paper = manual_review.ManualIntakeReviewTests.paper

    def paper_with_cards(self, paper, *numbers):
        group = QuestionGroup.objects.create(paper=paper, title=paper.display_name, sequence=0,
            page_start=1, page_end=len(paper.pages), metadata={"pages": [0], "source": "manual"})
        for number in numbers:
            Question.objects.create(paper=paper, group=group, number=number, body_mode="source_image",
                processing_mode="manual", question_type="free_response", state=Question.State.YELLOW,
                regions=[{"page_idx": 0, "bbox": [40.0, 60.0, 540.0, 200.0]}])
        return paper


class FailWhenNothingWasCutTests(NothingWasCutCase):
    def test_a_cut_that_produced_no_card_is_a_hard_failure(self):
        paper = self.paper(data=scan_pdf())
        paper.status, paper.processing_plan = Paper.Status.QUEUED, {"revision": 3, "mode": "mineru"}
        paper.save()
        self.assertTrue(pipeline._fail_when_nothing_was_cut(paper, 3))
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.FAILED)
        self.assertIn("自动切题没有切出", paper.error)

    def test_a_paper_that_has_cards_is_left_alone(self):
        paper = self.paper_with_cards(self.paper(data=scan_pdf()), 1, 2)
        paper.status, paper.processing_plan = Paper.Status.READY, {"revision": 3, "mode": "mineru"}
        paper.save()
        self.assertFalse(pipeline._fail_when_nothing_was_cut(paper, 3))
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.READY)
        self.assertEqual(paper.error, "")

    def test_a_stale_run_cannot_fail_a_paper_the_teacher_already_moved_on(self):
        paper = self.paper(data=scan_pdf())
        paper.status, paper.processing_plan = Paper.Status.QUEUED, {"revision": 4, "mode": "mineru"}
        paper.save()
        self.assertFalse(pipeline._fail_when_nothing_was_cut(paper, 3))
        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.QUEUED)

    def test_the_worker_stops_before_reading_when_nothing_was_cut(self):
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


class ImportWithNothingCutTests(NothingWasCutCase):
    def test_an_auto_import_with_no_cards_never_arrives_looking_finished(self):
        paper = self.paper(data=scan_pdf())
        with mock.patch.object(pipeline, "parse", side_effect=AssertionError("unexpected cloud parsing")):
            paper = intake.prepare_auto(paper, cloud_ready=True)
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertEqual(paper.status, Paper.Status.FAILED)
        # 本地为什么没切出来，是老师真正要读的那句话。
        self.assertIn("已保留原页", paper.error)

    def test_an_import_that_cut_something_is_not_failed(self):
        paper = self.paper_with_cards(self.paper(data=scan_pdf()), 1, 2)
        with mock.patch.object(pipeline, "parse", side_effect=AssertionError("unexpected cloud parsing")):
            paper = intake.prepare_auto(paper, cloud_ready=True)
        self.assertNotEqual(paper.status, Paper.Status.FAILED)


class ReimportingTheSameOriginalTests(NothingWasCutCase):
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


class NoVerdictIsClaimedTests(NothingWasCutCase):
    def test_the_api_no_longer_reports_a_cut_verdict(self):
        paper = self.paper_with_cards(self.paper(data=scan_pdf()), 1, 2)
        self.assertNotIn("cut_result", views.paper_json(paper))

    def test_a_partly_cut_paper_keeps_the_ordinary_label(self):
        # 没有对账，也就没有“切题不全”这种点名到题的标签。一张不实的标签比
        # 一句普通的标签更坏：它会让老师先怀疑自己那份卷，再怀疑其他所有提示。
        paper = self.paper_with_cards(self.paper(data=scan_pdf()), 1, 2)
        paper.status = Paper.Status.READY
        paper.save()
        self.assertEqual(views.paper_json(paper)["status_label"], "待你终审")
