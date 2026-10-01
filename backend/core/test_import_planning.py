from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from .import_planning import (
    BOOK_CHUNK_PAGES,
    ChunkCoverageError,
    PageChunk,
    analyze_question_number_structure,
    pdf_chunk_page_limit,
    pdf_requires_chunks,
    plan_pdf_chunks,
    validate_chunk_coverage,
)
from .models import ImportChunk, Paper, Question, QuestionGroup


class ImportStructureMigrationTests(TransactionTestCase):
    """The additive migration must preserve cards made by every earlier release."""

    migrate_from = [("core", "0008_question_figure_review")]
    migrate_to = [("core", "0009_import_structure")]

    def tearDown(self):
        # Leave the latest schema behind for the test classes that run later.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        OldPaper = old_apps.get_model("core", "Paper")
        OldQuestion = old_apps.get_model("core", "Question")
        paper = OldPaper.objects.create(
            filename="旧试卷.pdf",
            kind="pdf",
            sha256="c" * 64,
            source_path="data/old.pdf",
        )
        self.paper_id = paper.pk
        self.question_id = OldQuestion.objects.create(paper=paper, number=1).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def test_existing_card_gets_a_default_group_and_stable_source_key(self):
        MigratedPaper = self.apps.get_model("core", "Paper")
        MigratedQuestion = self.apps.get_model("core", "Question")
        MigratedGroup = self.apps.get_model("core", "QuestionGroup")

        paper = MigratedPaper.objects.get(pk=self.paper_id)
        question = MigratedQuestion.objects.get(pk=self.question_id)
        group = MigratedGroup.objects.get(paper_id=self.paper_id)

        self.assertEqual(paper.material_type, "exam")
        self.assertFalse(paper.archived)
        self.assertEqual(paper.structure, {})
        self.assertEqual(group.title, "默认题组")
        self.assertEqual(question.group_id, group.pk)
        self.assertIsNotNone(question.source_key)


class PdfChunkPlanningTests(TestCase):
    def test_exam_keeps_600_page_hard_limit_boundary(self):
        self.assertEqual(pdf_chunk_page_limit("exam", 600), 600)
        self.assertFalse(pdf_requires_chunks(600, "exam", 600))
        self.assertTrue(pdf_requires_chunks(601, "exam", 600))

    def test_book_uses_stable_100_page_boundary(self):
        self.assertEqual(BOOK_CHUNK_PAGES, 100)
        self.assertEqual(pdf_chunk_page_limit("book", 600), 100)
        self.assertTrue(pdf_requires_chunks(1, "book", 600))
        self.assertTrue(pdf_requires_chunks(100, "book", 600))
        self.assertTrue(pdf_requires_chunks(101, "book", 600))

    def test_270_page_book_plan_is_stable_and_lossless(self):
        chunks = plan_pdf_chunks(270, pdf_chunk_page_limit("book", 600))

        self.assertEqual(
            [(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
            [(1, 100), (101, 200), (201, 270)],
        )
        self.assertEqual([page for chunk in chunks for page in chunk.page_map], list(range(1, 271)))

    def test_260_pages_are_split_without_a_gap_or_overlap(self):
        chunks = plan_pdf_chunks(260, 50)

        self.assertEqual(
            [(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
            [(1, 50), (51, 100), (101, 150), (151, 200), (201, 250), (251, 260)],
        )
        self.assertEqual([page for chunk in chunks for page in chunk.page_map], list(range(1, 261)))
        self.assertTrue(validate_chunk_coverage(chunks, expected_start=1, expected_end=260))

    def test_illegal_overlap_is_rejected_instead_of_being_silently_sorted(self):
        chunks = [
            PageChunk(1, 1, 50, tuple(range(1, 51))),
            PageChunk(2, 50, 80, tuple(range(50, 81))),
        ]

        with self.assertRaisesRegex(ChunkCoverageError, "重叠"):
            validate_chunk_coverage(chunks, expected_start=1, expected_end=80)

    def test_selected_book_range_keeps_original_page_mapping(self):
        chunks = plan_pdf_chunks(300, 40, start_page=21, end_page=125)

        self.assertEqual((chunks[0].source_page_start, chunks[0].page_map[0]), (21, 21))
        self.assertEqual((chunks[-1].source_page_end, chunks[-1].page_map[-1]), (125, 125))
        self.assertEqual(sum(chunk.page_count for chunk in chunks), 105)


class QuestionNumberPlanningTests(TestCase):
    def test_normal_exam_stays_in_one_group(self):
        plan = analyze_question_number_structure([[1, 2], [3], [4, 5]], material_type="exam")

        self.assertFalse(plan.needs_confirmation)
        self.assertFalse(plan.signals)
        self.assertEqual([(group.page_start, group.page_end) for group in plan.groups], [(1, 3)])

    def test_two_exams_are_suggested_as_two_groups_without_dropping_duplicates(self):
        # 第一份卷的第 2、3 题，随后是另一份卷的第 1–2、3 题。
        plan = analyze_question_number_structure([[2], [3], [1, 2], [3]], material_type="exam")

        self.assertTrue(plan.needs_confirmation)
        self.assertEqual([(group.page_start, group.page_end) for group in plan.groups], [(1, 2), (3, 4)])
        self.assertEqual(plan.signals[0].source_page, 3)
        self.assertEqual(plan.signals[0].overlapping_numbers, (2,))

    def test_book_mode_keeps_repeated_numbers_as_valid_group_suggestions(self):
        plan = analyze_question_number_structure([[1, 2], [3], [1, 2], [3]], material_type="book")

        self.assertFalse(plan.needs_confirmation)
        self.assertTrue(plan.has_numbering_restart)
        self.assertEqual([(group.page_start, group.page_end) for group in plan.groups], [(1, 2), (3, 4)])


class ImportStructureModelTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(
            filename="数学书.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256="a" * 64,
            source_path="data/source.pdf",
        )

    def test_repeated_question_numbers_are_not_an_identity_collision(self):
        group = QuestionGroup.objects.create(
            paper=self.paper,
            title="第一章练习",
            kind=QuestionGroup.Kind.EXERCISE,
            sequence=0,
            page_start=10,
            page_end=12,
        )
        first = Question.objects.create(paper=self.paper, group=group, number=1)
        second = Question.objects.create(paper=self.paper, group=group, number=1)

        self.assertEqual(self.paper.questions.filter(number=1).count(), 2)
        self.assertNotEqual(first.source_key, second.source_key)
        stable_key = first.source_key
        first.number = 99
        first.save(update_fields=["number"])
        first.refresh_from_db()
        self.assertEqual(first.source_key, stable_key)

    def test_chunk_record_stores_global_page_mapping_for_single_chunk_retry(self):
        chunk = ImportChunk.objects.create(
            paper=self.paper,
            sequence=1,
            source_page_start=51,
            source_page_end=100,
            page_map=list(range(51, 101)),
            status=ImportChunk.Status.FAILED,
            sha256="b" * 64,
            error="MinerU 页数限制",
            attempts=1,
        )

        self.assertEqual(chunk.page_map[0], 51)
        self.assertEqual(chunk.page_map[-1], 100)
        self.assertEqual(chunk.status, ImportChunk.Status.FAILED)
        self.assertEqual(self.paper.import_chunks.count(), 1)

    def test_pipeline_backfills_legacy_book_with_100_page_chunks(self):
        from .pipeline import _ensure_import_chunks

        self.paper.pages = [
            {"page_idx": index, "width": 595, "height": 842}
            for index in range(270)
        ]
        self.paper.save(update_fields=["pages"])

        chunks = _ensure_import_chunks(self.paper)

        self.assertEqual(
            [(chunk.source_page_start, chunk.source_page_end) for chunk in chunks],
            [(1, 100), (101, 200), (201, 270)],
        )
