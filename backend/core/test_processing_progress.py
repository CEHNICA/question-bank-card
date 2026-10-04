from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from . import library
from .models import ImportChunk, Paper, PublishedQuestion, Question
from .test_v110_types_origin import TempDataMixin
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


class SettledCountsTests(TempDataMixin, TestCase):
    """A card the library already serves exactly as shown is finished.

    ``approved`` counts ticks; ``settled`` counts “nothing left to look at” — a
    card can be in the library with its tick cleared (the teacher unchecked it,
    an AI pass, an earlier session) and still have nothing left to do.  Both
    ``settled`` cards leave ``green``/``yellow``/``red``, so the list card cannot
    print “已入库 10” and “3 张要看” on the same line.
    """

    def publish(self, card):
        library.publish(card, queue_enrichment=False)
        return PublishedQuestion.objects.filter(question=card, status=PublishedQuestion.Status.PUBLISHED)\
            .order_by("-version").first()

    def counts(self, paper):
        return paper_json(paper, with_counts=True)["counts"]

    def test_published_card_with_cleared_tick_is_settled_and_stops_counting_as_todo(self):
        self.use_temp_data()
        paper = self.make_paper()
        cards = [self.card(paper, number=number, question_type="free_response",
                           stem=f"第 {number} 题：求 $x^2$ 的范围")
                 for number in range(1, 11)]
        for card in cards:
            library.approve(card, now=timezone.now())
            card.save()
            self.publish(card)
        # Untick them all, the way an AI pass or a previous session leaves them.
        Question.objects.filter(paper=paper).update(approved=False, approved_at=None,
                                                    approved_content_hash="", approval_source="")

        counts = self.counts(paper)

        self.assertEqual((counts["total"], counts["settled"]), (10, 10))
        self.assertEqual((counts["green"], counts["yellow"], counts["red"]), (0, 0, 0))
        # “the library holds a record” keeps its own meaning: it is what stops a
        # source task from being deleted out from under the library.
        self.assertEqual(counts["published"], 10)

    def test_editing_after_publishing_puts_the_card_back_on_the_todo_list(self):
        self.use_temp_data()
        paper = self.make_paper()
        card = self.card(paper, number=1, question_type="free_response", stem="求 $x^2$ 的范围")
        library.approve(card, now=timezone.now())
        card.save()
        self.publish(card)
        card.stem = "求 $x^3$ 的范围"
        card.save(update_fields=["stem"])

        counts = self.counts(paper)

        self.assertEqual((counts["settled"], counts["published"]), (0, 1))
        self.assertEqual((counts["green"], counts["yellow"]), (1, 0))

    def test_a_tick_without_a_library_copy_still_counts_as_approved_not_settled(self):
        self.use_temp_data()
        paper = self.make_paper()
        card = self.card(paper, number=1, question_type="free_response", stem="求 $x^2$ 的范围")
        library.approve(card, now=timezone.now())
        card.save()

        counts = self.counts(paper)

        self.assertEqual((counts["approved"], counts["settled"], counts["published"]), (1, 0, 0))
        self.assertEqual((counts["green"], counts["yellow"], counts["red"]), (0, 0, 0))
        self.assertEqual((counts["done"], counts["todo"]), (1, 0))

    def test_a_clean_read_still_has_to_be_looked_at_so_the_list_and_the_page_agree(self):
        """The list and the review page must count “还要看” the same way.

        A card the reader read cleanly is green: no doubt, no failure.  It is
        still nobody's job done until a person ticks it.  The list used to count
        only yellow and red, so a paper said “4 张要看” in the sidebar and
        “需要核查 25” as soon as it was opened.
        """
        self.use_temp_data()
        paper = self.make_paper()
        for number in range(1, 26):
            self.card(paper, number=number, question_type="free_response", state=Question.State.GREEN,
                      stem=f"第 {number} 题：求 $x^2$ 的范围")
        Question.objects.filter(paper=paper, number__gt=21).update(state=Question.State.YELLOW)

        counts = self.counts(paper)

        # The reading breakdown still says what it always said …
        self.assertEqual((counts["total"], counts["green"], counts["yellow"], counts["red"]), (25, 21, 4, 0))
        # … but nobody has looked at any of them, so the shared pair is 0 / 25.
        self.assertEqual((counts["done"], counts["todo"]), (0, 25))

    def test_waiting_cards_belong_to_neither_half(self):
        self.use_temp_data()
        paper = self.make_paper()
        for number in range(1, 7):
            self.card(paper, number=number, question_type="free_response", state=Question.State.WAITING,
                      stem=f"第 {number} 题：求 $x^2$ 的范围")
        for number in (7, 8):
            self.card(paper, number=number, question_type="free_response", stem=f"第 {number} 题：求 $x^2$ 的范围")

        counts = self.counts(paper)

        # 6 still being read belong to neither half; the 2 that finished reading
        # are unticked, so they still need a look.
        self.assertEqual((counts["total"], counts["waiting"], counts["done"], counts["todo"]), (8, 6, 0, 2))
        self.assertEqual(counts["done"] + counts["todo"] + counts["waiting"], counts["total"])

    def test_a_read_card_needs_a_tick_before_it_counts_as_done(self):
        self.use_temp_data()
        paper = self.make_paper()
        for number in range(1, 5):
            self.card(paper, number=number, question_type="free_response", stem=f"第 {number} 题")
        ticked = self.card(paper, number=5, question_type="free_response", stem="第 5 题")
        library.approve(ticked, now=timezone.now())
        ticked.save()

        counts = self.counts(paper)

        self.assertEqual((counts["total"], counts["done"], counts["todo"]), (5, 1, 4))
        self.assertEqual(counts["done"] + counts["todo"] + counts["waiting"], counts["total"])

    def test_a_tick_and_a_library_copy_of_the_same_card_count_once(self):
        self.use_temp_data()
        paper = self.make_paper()
        for number in range(1, 4):
            card = self.card(paper, number=number, question_type="free_response", stem=f"第 {number} 题")
            library.approve(card, now=timezone.now())
            card.save()
            self.publish(card)
        Question.objects.filter(paper=paper).update(approved=False, approved_at=None,
                                                    approved_content_hash="", approval_source="")

        counts = self.counts(paper)

        self.assertEqual((counts["approved"], counts["settled"]), (0, 3))
        # “done” is the union, not the sum — three cards, not six.
        self.assertEqual((counts["done"], counts["todo"]), (3, 0))
