"""Regression coverage for resuming an interrupted reading batch."""

import json
import threading
import time
from unittest import mock

from django.test import TestCase

from . import pipeline
from .models import Paper, Question


class InterruptedReadRecoveryTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="book.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="a" * 64,
            source_path="C:/tests/book.pdf",
            status=Paper.Status.READING,
        )

    def question(self, state: str, number: int, **fields) -> Question:
        return Question.objects.create(
            paper=self.paper,
            number=number,
            state=state,
            stem=fields.pop("stem", f"question {number}"),
            **fields,
        )

    def read_snapshot(self) -> dict:
        return {
            "id": 999,
            "number": 1,
            "group_id": None,
            "start_source": "mineru",
            "regions": [{"page_idx": 0, "bbox": [10, 10, 100, 100]}],
            "candidates": [],
            "question_type": "free_response",
            "stem": "",
            "options": {},
            "edited": False,
            "source_kind": Question.SourceKind.UNKNOWN,
            "source_anchor_seq": 1,
            "segmentation_flags": [],
        }

    def test_quota_exhaustion_is_not_hidden_by_a_second_reader(self):
        primary = pipeline.readers.Engine("minimax", "MiniMax-M3")
        checker = pipeline.readers.Engine("siliconflow", "Qwen/Test")
        successful = pipeline.readers.parse_reading(
            "【内容类型】练习题\n【题号】1\n【题型】解答题\n【题干】计算 $1+1$。",
            1,
        )

        def read(engine, _url, _number, with_figures):
            if engine.provider == "minimax":
                raise pipeline.readers.ReaderQuotaExhausted(
                    "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑"
                )
            return successful

        with mock.patch.object(pipeline.readers, "primary_engine", return_value=primary), \
                mock.patch.object(pipeline.readers, "checker_engine", return_value=checker) as checker_mock, \
                mock.patch.object(pipeline.readers, "read_question", side_effect=read), \
                mock.patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,test"), \
                self.assertRaises(pipeline.readers.ReaderQuotaExhausted):
            pipeline.read_card(self.read_snapshot(), mock.Mock())
        checker_mock.assert_not_called()

    def test_no_reader_result_with_any_quota_error_pauses_the_card(self):
        primary = pipeline.readers.Engine("minimax", "MiniMax-M3")
        checker = pipeline.readers.Engine("siliconflow", "Qwen/Test")

        def read(engine, _url, _number, with_figures):
            if engine.provider == "minimax":
                raise pipeline.readers.ReaderQuotaExhausted(
                    "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑"
                )
            raise pipeline.readers.ReaderError("复核服务暂时不可用")

        with mock.patch.object(pipeline.readers, "primary_engine", return_value=primary), \
                mock.patch.object(pipeline.readers, "checker_engine", return_value=checker) as checker_mock, \
                mock.patch.object(pipeline.readers, "read_question", side_effect=read), \
                mock.patch.object(pipeline.imaging, "stack_regions", return_value=(object(), [])), \
                mock.patch.object(pipeline.imaging, "jpeg_data_url", return_value="data:image/jpeg;base64,test"), \
                self.assertRaises(pipeline.readers.ReaderQuotaExhausted):
            pipeline.read_card(self.read_snapshot(), mock.Mock())
        checker_mock.assert_not_called()

    def test_restart_only_resumes_waiting_and_reading_cards(self):
        approved = self.question(
            Question.State.GREEN,
            1,
            approved=True,
            approved_content_hash="approved-before-restart",
        )
        protected = self.question(
            Question.State.YELLOW,
            2,
            flags=[pipeline.FLAG_RESEGMENT_RANGE_PROTECTED],
        )
        rate_limited = self.question(
            Question.State.RED,
            3,
            error="MiniMax 接口返回 HTTP 429",
            read_a={"error": "MiniMax 接口返回 HTTP 429"},
            read_b={"error": "MiniMax 接口返回 HTTP 429"},
        )
        waiting = self.question(Question.State.WAITING, 4)
        interrupted = self.question(Question.State.READING, 5)
        selected: list[int] = []

        def record(_paper, questions, *, revision):
            self.assertEqual(revision, int((_paper.processing_plan or {}).get("revision", 0)))
            selected.extend(question.pk for question in questions)

        with mock.patch.object(pipeline, "read_questions", side_effect=record):
            pipeline.process_paper(self.paper)

        self.assertCountEqual(selected, [waiting.pk, interrupted.pk])
        self.paper.refresh_from_db()
        approved.refresh_from_db()
        protected.refresh_from_db()
        rate_limited.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.READY)
        self.assertTrue(approved.approved)
        self.assertEqual(approved.approved_content_hash, "approved-before-restart")
        self.assertEqual(protected.flags, [pipeline.FLAG_RESEGMENT_RANGE_PROTECTED])
        self.assertEqual(rate_limited.state, Question.State.RED)
        self.assertEqual(rate_limited.error, "MiniMax 接口返回 HTTP 429")

    def test_confirmed_plan_exhaustion_pauses_with_a_bounded_in_flight_window(self):
        questions = [self.question(Question.State.WAITING, number) for number in range(1, 6)]
        barrier = threading.Barrier(2)
        called: list[int] = []

        def read(snapshot, _store):
            called.append(snapshot["id"])
            barrier.wait(timeout=1)
            if snapshot["id"] == questions[0].pk:
                raise pipeline.readers.ReaderQuotaExhausted(
                    "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑"
                )
            # Keep the other already-running job alive until the quota future
            # has been observed; its result must not be persisted after pause.
            time.sleep(0.05)
            return {"state": Question.State.GREEN, "error": "", "flags": []}

        # An explicit two-card limit must not be raised by live account capacity.
        with mock.patch.dict("os.environ", {"QB_PARALLEL_EXPLICIT": "1"}), \
                mock.patch.object(pipeline, "PARALLEL", 2), \
                mock.patch.object(pipeline, "read_card", side_effect=read):
            pipeline.process_paper(self.paper)

        self.paper.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.FAILED)
        self.assertEqual(
            self.paper.error,
            "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑",
        )
        self.assertEqual(len(called), 2)
        self.assertEqual(
            set(Question.objects.filter(paper=self.paper).values_list("state", flat=True)),
            {Question.State.READING},
        )

    def test_failed_paper_retry_resumes_reading_cards_and_requeues_red_cards(self):
        red = self.question(
            Question.State.RED,
            1,
            error="MiniMax 接口返回 HTTP 429",
        )
        interrupted = self.question(Question.State.READING, 2)
        approved = self.question(
            Question.State.GREEN,
            3,
            approved=True,
            approved_content_hash="approved-before-retry",
        )
        self.paper.status = Paper.Status.FAILED
        self.paper.error = "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑"
        self.paper.save(update_fields=["status", "error", "updated_at"])

        response = self.client.post(
            f"/api/papers/{self.paper.pk}/retry",
            data=json.dumps({}),
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.paper.refresh_from_db()
        red.refresh_from_db()
        interrupted.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(self.paper.status, Paper.Status.READING)
        self.assertEqual(self.paper.error, "")
        self.assertEqual(red.state, Question.State.WAITING)
        self.assertEqual(interrupted.state, Question.State.READING)
        self.assertEqual(approved.state, Question.State.GREEN)
        self.assertTrue(approved.approved)
        self.assertEqual(approved.approved_content_hash, "approved-before-retry")
