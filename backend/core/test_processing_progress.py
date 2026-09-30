from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .models import ImportChunk, Paper, Question
from .views import paper_json


def make_paper(name: str, status: str) -> Paper:
    return Paper.objects.create(
        filename=name,
        kind="pdf",
        sha256=(name.encode().hex() + "0" * 64)[:64],
        source_path=f"C:/tests/{name}",
        status=status,
    )


class ProcessingProgressTests(TestCase):
    def test_queue_reports_real_ahead_count_and_elapsed_time(self):
        now = timezone.now()
        first = make_paper("first.pdf", Paper.Status.PARSING)
        waiting = make_paper("waiting.pdf", Paper.Status.QUEUED)
        Paper.objects.filter(pk=first.pk).update(created_at=now - timedelta(minutes=8))
        Paper.objects.filter(pk=waiting.pk).update(
            created_at=now - timedelta(minutes=3),
            updated_at=now - timedelta(seconds=14),
        )
        waiting.refresh_from_db()

        progress = paper_json(waiting, with_counts=False)["processing"]

        self.assertEqual(progress["stage"], "queued")
        self.assertEqual(progress["queue_ahead"], 1)
        self.assertGreaterEqual(progress["elapsed_seconds"], 179)
        self.assertGreaterEqual(progress["idle_seconds"], 13)
        self.assertFalse(progress["determinate"])
        self.assertIsNone(progress["completed"])
        self.assertEqual(progress["task_created_at"], waiting.created_at.isoformat())
        self.assertNotIn("task_started_at", progress)
        self.assertNotIn("eta_seconds", progress)

    def test_a_paper_parsed_ahead_says_it_is_waiting_for_its_turn(self):
        now = timezone.now()
        reading = make_paper("reading.pdf", Paper.Status.READING)
        parsed = make_paper("parsed.pdf", Paper.Status.SEGMENTING)
        Paper.objects.filter(pk=reading.pk).update(created_at=now - timedelta(minutes=5))
        parsed.refresh_from_db()
        progress = paper_json(parsed, with_counts=False)["processing"]
        self.assertTrue(progress["parsed_ahead"])
        self.assertEqual(progress["queue_ahead"], 1)
        # Alone in the queue, segmenting is just segmenting.
        Paper.objects.filter(pk=reading.pk).update(status=Paper.Status.READY)
        progress = paper_json(parsed, with_counts=False)["processing"]
        self.assertNotIn("parsed_ahead", progress)

    def test_chunked_mineru_progress_comes_from_persisted_chunk_states(self):
        paper = make_paper("book.pdf", Paper.Status.PARSING)
        for sequence, status in enumerate(
            [ImportChunk.Status.PARSED, ImportChunk.Status.PARSED, ImportChunk.Status.PARSING],
            start=1,
        ):
            start = (sequence - 1) * 100 + 1
            ImportChunk.objects.create(
                paper=paper,
                sequence=sequence,
                source_page_start=start,
                source_page_end=start + 99 if sequence < 3 else 270,
                page_map=list(range(start, (start + 100) if sequence < 3 else 271)),
                status=status,
            )

        progress = paper_json(paper, with_counts=False)["processing"]

        self.assertTrue(progress["determinate"])
        self.assertEqual((progress["completed"], progress["total"], progress["unit"]), (2, 3, "chunk"))
        self.assertEqual(progress["chunks"]["parsed"], 2)
        self.assertEqual(progress["chunks"]["parsing"], 1)
        self.assertEqual(
            progress["chunks"]["active_ranges"],
            [{"sequence": 3, "page_start": 201, "page_end": 270}],
        )

    def test_single_file_mineru_and_local_segmentation_do_not_claim_a_percentage(self):
        parsing = make_paper("exam.pdf", Paper.Status.PARSING)
        segmenting = make_paper("segment.pdf", Paper.Status.SEGMENTING)

        for paper in (parsing, segmenting):
            progress = paper_json(paper, with_counts=False)["processing"]
            self.assertFalse(progress["determinate"])
            self.assertIsNone(progress["completed"])
            self.assertIsNone(progress["total"])

    def test_reading_reports_real_question_counts_and_ready_has_no_processing_payload(self):
        paper = make_paper("reading.pdf", Paper.Status.READING)
        paper.progress = 40
        paper.total = 561
        paper.save(update_fields=["progress", "total", "updated_at"])

        progress = paper_json(paper, with_counts=False)["processing"]

        self.assertTrue(progress["determinate"])
        self.assertEqual((progress["completed"], progress["total"], progress["unit"]), (40, 561, "question"))
        paper.status = Paper.Status.READY
        paper.save(update_fields=["status", "updated_at"])
        self.assertIsNone(paper_json(paper, with_counts=False)["processing"])

    def test_reading_estimate_comes_from_the_measured_pace_of_finished_cards(self):
        paper = make_paper("pace.pdf", Paper.Status.READING)
        now = timezone.now()
        for number in range(1, 11):
            question = Question.objects.create(
                paper=paper, number=number,
                state=Question.State.GREEN if number <= 5 else Question.State.WAITING,
            )
            if number <= 5:   # one card finished every 4 seconds
                Question.all_objects.filter(pk=question.pk).update(
                    updated_at=now - timedelta(seconds=4 * (5 - number)))
        paper.progress, paper.total = 5, 10
        paper.save(update_fields=["progress", "total", "updated_at"])
        progress = paper_json(paper, with_counts=False)["processing"]
        self.assertEqual(progress["eta_seconds"], 20)

        few = make_paper("few.pdf", Paper.Status.READING)
        Question.objects.create(paper=few, number=1, state=Question.State.GREEN)
        few.progress, few.total = 1, 10
        few.save(update_fields=["progress", "total", "updated_at"])
        self.assertNotIn("eta_seconds", paper_json(few, with_counts=False)["processing"])

    def test_quota_exhaustion_is_presented_as_a_recoverable_pause(self):
        paper = make_paper("quota.pdf", Paper.Status.FAILED)
        paper.error = "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑"
        paper.save(update_fields=["error", "updated_at"])

        payload = paper_json(paper, with_counts=False)

        self.assertTrue(payload["recoverable_pause"])
        self.assertEqual(payload["status_label"], "额度不足，已暂停")
        self.assertIsNone(payload["processing"])
