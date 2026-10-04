"""Manual cuts must keep their recognized content when readers become unavailable."""
from copy import deepcopy
from unittest import mock

from django.test import TransactionTestCase
from django.utils import timezone
from PIL import Image
import pymupdf as fitz

from . import intake, library, library_jobs, pipeline, readers, source_images, views
from .models import Paper, Question
from . import test_direct_cut_reading as direct_cut
from . import test_tiyouju_cli as cli_tests


class RecentIntakeRegressionsTests(TransactionTestCase):
    # Reuse only the isolated original-document setup and meaningful pipeline
    # harness; do not inherit and rerun the unrelated direct-reading tests.
    setUp = direct_cut.DirectCutReadingTests.setUp
    paper = direct_cut.DirectCutReadingTests.paper
    run_read = direct_cut.DirectCutReadingTests.run_read
    cut = direct_cut.DirectCutReadingTests.cut
    post = direct_cut.DirectCutReadingTests.post
    reading = direct_cut.DirectCutReadingTests.reading

    def book(self):
        with fitz.open() as document:
            for chapter in (1, 2):
                page = document.new_page(width=595, height=842)
                page.insert_text((40, 50), f"Chapter {chapter}")
                page.insert_text((40, 100), f"1. Find chapter {chapter} x when x + 2 = 5.")
                page.insert_text((40, 300), f"2. Compute chapter {chapter} triangle area.")
            paper = self.paper(data=document.tobytes())
        paper.material_type = Paper.MaterialType.BOOK
        paper.save()
        return paper

    def exam(self, *, repeated=True):
        with fitz.open() as document:
            for index in range(2):
                page = document.new_page(width=595, height=842)
                for offset in range(2):
                    number = offset + 1 if repeated else index * 2 + offset + 1
                    page.insert_text((40, 100 + offset * 200),
                        f"{number}. Find paper {index + 1} question {offset + 1} x when x + 2 = 5.")
            return self.paper(data=document.tobytes())

    def test_combined_exam_pdf_keeps_all_pages_without_merging_restarted_numbering(self):
        paper = self.exam()
        original = source_images.source_identity(paper)
        with mock.patch.object(intake.segment, "segment", side_effect=AssertionError("unexpected single-exam segmentation")), \
                mock.patch.object(pipeline, "parse", side_effect=AssertionError("unexpected cloud parsing")):
            paper = intake.prepare_auto(paper, cloud_ready=True)
        # 自动切题一题没切出就是硬失败，不能以“待你终审”的样子交到审核页。
        self.assertEqual((paper.material_type, paper.status, paper.processing_plan["mode"]), ("exam", "failed", "manual"))
        self.assertEqual(paper.processing_plan["cut_result"]["verdict"], "failed")
        self.assertTrue(paper.processing_plan["native_numbering_fallback"])
        self.assertEqual(paper.processing_plan["local_scope_count"], 2)
        self.assertFalse(paper.questions.exists() or paper.processing_plan["cloud_authorized"])
        self.assertEqual(len(paper.pages), 2)
        self.assertEqual(source_images.source_identity(paper), original)
        text = "\n".join(paper.blocks.values_list("text", flat=True))
        for paper_number in (1, 2):
            for question_number in (1, 2):
                self.assertIn(f"paper {paper_number} question {question_number}", text)
        self.assertIn("多套试卷", paper.processing_plan["fallback_reason"])

    def test_combined_exam_only_enters_cloud_route_with_explicit_permission_and_service(self):
        for allowed, ready, mode in ((False, True, "manual"), (True, False, "manual"), (True, True, "mineru")):
            with self.subTest(allowed=allowed, ready=ready):
                paper = intake.prepare_auto(self.exam(), allow_cloud=allowed, cloud_ready=ready)
                self.assertEqual(paper.processing_plan["mode"], mode)
                # 只有真的会去解析的路线才是 queued；转手工的路线一题没切出，
                # 状态必须说失败，否则这份卷会以完成的样子躺在列表里。
                self.assertEqual(paper.status, "queued" if mode == "mineru" else "failed")
                self.assertEqual(paper.processing_plan.get("cut_result", {}).get("verdict"),
                                 None if mode == "mineru" else "failed")
                self.assertEqual(paper.processing_plan["cloud_authorized"], allowed)
                self.assertFalse(paper.questions.exists())
                self.assertTrue(paper.processing_plan["native_numbering_fallback"])
                self.assertEqual(len(paper.pages), 2)

    def test_explicit_native_combined_exam_preserves_sources_for_manual_cutting(self):
        paper = intake.prepare(self.exam(), "native")
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertTrue(paper.processing_plan["native_numbering_fallback"])
        self.assertFalse(paper.questions.exists())
        self.assertTrue(paper.blocks.exists())
        self.assertEqual(len(paper.pages), 2)

    def test_single_exam_with_monotonic_numbers_keeps_existing_native_path(self):
        paper = intake.prepare_auto(self.exam(repeated=False), cloud_ready=True)
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("ready", "native"))
        self.assertFalse(paper.processing_plan.get("native_numbering_fallback"))
        self.assertEqual(list(paper.questions.values_list("number", flat=True)), [1, 2, 3, 4])
        self.assertFalse(paper.processing_plan["cloud_authorized"])

    def test_local_book_does_not_merge_repeated_chapter_numbers_into_exam_cards(self):
        paper = self.book()
        original = source_images.source_identity(paper)
        with mock.patch.object(pipeline, "parse", side_effect=AssertionError("unexpected cloud parsing")), \
                mock.patch.object(pipeline, "read_card", side_effect=AssertionError("unexpected reading")), \
                mock.patch.object(intake.segment, "segment", side_effect=AssertionError("unexpected exam segmentation")):
            paper = intake.prepare_auto(paper, cloud_ready=True)
        self.assertEqual((paper.status, paper.processing_plan["mode"]), ("failed", "manual"))
        self.assertEqual(paper.processing_plan["cut_result"]["verdict"], "failed")
        self.assertFalse(paper.questions.exists())
        self.assertEqual(len(paper.pages), 2)
        self.assertEqual(source_images.source_identity(paper), original)
        text = "\n".join(paper.blocks.values_list("text", flat=True))
        for chapter in (1, 2):
            self.assertIn(f"Find chapter {chapter}", text)
            self.assertIn(f"Compute chapter {chapter}", text)
        self.assertIn("不同章节", paper.processing_plan["fallback_reason"])
        self.assertFalse(paper.processing_plan["cloud_authorized"])
        response = self.client.get(f"/api/papers/{paper.pk}/pages/0/preview")
        try:
            self.assertEqual(response.status_code, 200)
        finally:
            response.close()

    def test_local_book_cloud_route_requires_explicit_permission_and_configured_service(self):
        for allow_cloud, cloud_ready, expected in ((False, True, "manual"), (True, False, "manual"), (True, True, "mineru")):
            with self.subTest(allow_cloud=allow_cloud, cloud_ready=cloud_ready):
                paper = intake.prepare_auto(self.book(), allow_cloud=allow_cloud, cloud_ready=cloud_ready)
                self.assertEqual(paper.processing_plan["mode"], expected)
                self.assertEqual(paper.status, "queued" if expected == "mineru" else "failed")
                self.assertEqual(paper.processing_plan["cloud_authorized"], allow_cloud)
                self.assertFalse(paper.questions.exists())
                self.assertEqual(len(paper.pages), 2)

    def test_explicit_native_book_keeps_all_pages_and_local_text_without_exam_segmentation(self):
        paper = intake.prepare(self.book(), "native")
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertTrue(paper.processing_plan["native_book_fallback"])
        self.assertFalse(paper.questions.exists())
        self.assertTrue(paper.blocks.exists())
        self.assertEqual(len(paper.pages), 2)

    def test_cli_and_mcp_book_upload_keep_local_default_and_explicit_cloud_contract(self):
        paper = self.book()
        source = paper.source_path
        for interface, allow_cloud in (("cli", False), ("cli", True), ("mcp", False), ("mcp", True)):
            with self.subTest(interface=interface, allow_cloud=allow_cloud):
                client = mock.Mock()
                client.upload.return_value = {"paper": {"id": str(paper.pk), "filename": "book.pdf", "status": "ready"}}
                if interface == "mcp":
                    cli_tests.cli.mcp_call(client, "upload_paper", {"paths": [source], "book": True, "allow_cloud": allow_cloud})
                else:
                    args = cli_tests.cli.build_parser().parse_args(["upload", source, "--book", *(["--allow-cloud"] if allow_cloud else [])])
                    cli_tests.cli.cmd_upload(client, args)
                parameters = client.upload.call_args.args[1]
                self.assertEqual(parameters, {"material_type": "book", "parse_mode": "auto", "allow_cloud": "1" if allow_cloud else "0"})

    def published_text(self):
        question = self.cut(body_mode="text", stem="Published recognized question",
            text_source="single", state="green")
        library.approve(question, now=timezone.now())
        question.save()
        with mock.patch.object(library_jobs, "queue_on_intake"):
            publication, _ = library.publish(question)
        return question, publication

    def test_human_reread_of_published_manual_text_updates_only_the_review_draft(self):
        question, publication = self.published_text()
        published = deepcopy(publication.content)
        digest = publication.content_hash
        originals = deepcopy(source_images.assets(question))
        with mock.patch.object(readers, "assistant_mode", return_value=False):
            result = self.post(f"/api/questions/{question.pk}/reread", {"by": "human"})
        self.assertEqual(result.status_code, 200, result.content)
        question.refresh_from_db()
        self.assertTrue(question.reread_requested and question.ocr_pending)
        self.assertFalse(question.approved)
        self.run_read(question, lambda *args: self.reading(stem="Corrected rereading"))
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.state), ("text", "Corrected rereading", "green"))
        self.assertFalse(question.approved or question.ocr_pending or question.reread_requested)
        publication.refresh_from_db()
        self.assertEqual((publication.content, publication.content_hash), (published, digest))
        self.assertEqual(source_images.assets(question), originals)

    def test_ai_reread_of_human_reviewed_manual_text_requires_explicit_force(self):
        question, publication = self.published_text()
        original = deepcopy(Question.objects.values().get(pk=question.pk))
        published = deepcopy(publication.content)
        with mock.patch.object(readers, "assistant_mode", return_value=False):
            refused = self.post(f"/api/questions/{question.pk}/reread", {"by": "ai", "agent": "test"})
            self.assertEqual(refused.status_code, 409, refused.content)
            self.assertEqual(Question.objects.values().get(pk=question.pk), original)
            allowed = self.post(f"/api/questions/{question.pk}/reread",
                {"by": "ai", "agent": "test", "force": True})
        self.assertEqual(allowed.status_code, 200, allowed.content)
        question.refresh_from_db()
        self.assertTrue(question.reread_requested and question.ocr_pending)
        self.assertFalse(question.approved)
        publication.refresh_from_db()
        self.assertEqual(publication.content, published)

    def test_missing_service_keeps_published_manual_text_and_approval_unchanged(self):
        question, publication = self.published_text()
        before = deepcopy(Question.objects.values().get(pk=question.pk))
        content = deepcopy(publication.content)
        with mock.patch.object(views, "_vision_ready", return_value=False):
            result = self.post(f"/api/questions/{question.pk}/reread", {"by": "human"})
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(Question.objects.values().get(pk=question.pk), before)
        publication.refresh_from_db()
        self.assertEqual(publication.content, content)

    def test_manual_text_reread_without_vision_service_is_rejected_without_changes(self):
        question = self.cut(body_mode="text", edited=True, stem="Existing manual correction",
            options={"A": "1", "B": "2"}, origin="Printed source", type_locked=True,
            text_source="human", state="green",
            figures=[{"page_idx": 0, "bbox": [100, 100, 250, 250], "slot": "stem", "source": "manual"}],
            read_a={"stem": "Old recognition"})
        before = deepcopy(Question.objects.values().get(pk=question.pk))
        for available, assistant in ((False, False), (True, True)):
            with self.subTest(available=available, assistant=assistant), \
                    mock.patch.object(views, "_vision_ready", return_value=available), \
                    mock.patch.object(readers, "assistant_mode", return_value=assistant), \
                    mock.patch.object(pipeline, "read_card") as read:
                result = self.post(f"/api/questions/{question.pk}/reread")
                self.assertEqual(result.status_code, 409, result.content)
                self.assertIn("看图读题模型", result.json()["error"])
                self.assertEqual(Question.objects.values().get(pk=question.pk), before)
                read.assert_not_called()

    def test_outage_on_recognized_manual_cut_keeps_text_options_and_manual_figure(self):
        figure = {"page_idx": 0, "bbox": [100, 100, 250, 250], "slot": "stem", "source": "manual"}
        question = self.cut(body_mode="text", stem="Existing recognized question",
            question_type="single_choice", options={"A": "1", "B": "2", "C": "3", "D": "4"},
            origin="Printed source", text_source="single", type_locked=True, state="green",
            figures=[figure], reread_requested=True, ocr_pending=True)
        primary = readers.Engine("minimax", "mock-model")
        actual_read = pipeline.read_card
        with mock.patch.object(readers, "primary_engine", return_value=primary), \
                mock.patch.object(readers, "read_question", side_effect=readers.ReaderUnavailable("Simulated outage")), \
                mock.patch.object(pipeline.features, "enabled", return_value=False):
            self.run_read(question, actual_read)
        question.refresh_from_db()
        self.assertEqual((question.stem, question.options, question.origin, question.question_type),
            ("Existing recognized question", {"A": "1", "B": "2", "C": "3", "D": "4"},
             "Printed source", "single_choice"))
        self.assertEqual(question.figures, [figure])
        self.assertEqual((question.state, question.error), ("red", "Simulated outage"))
        self.assertEqual(question.read_a["error"], "Simulated outage")
        self.assertFalse(question.ocr_pending or question.reread_requested or question.approved)

    def test_nonempty_local_ocr_fallback_is_still_available_to_existing_pipeline(self):
        primary = readers.Engine("minimax", "mock-model")
        snapshot = {"id": 1, "number": 1, "question_type": "free_response", "candidates": [],
            "regions": [{"page_idx": 0, "bbox": [40, 40, 900, 480]}], "double_read": False,
            "fallback_draft": "1. Existing printed OCR question"}
        with mock.patch.object(readers, "primary_engine", return_value=primary), \
                mock.patch.object(readers, "read_question", side_effect=readers.ReaderUnavailable("Simulated outage")), \
                mock.patch.object(pipeline.imaging, "stack_regions", return_value=(Image.new("RGB", (80, 80), "white"), [])):
            result = pipeline.read_card(snapshot, store=mock.Mock())
        self.assertEqual(result["stem"], "Existing printed OCR question")
        self.assertEqual(result["state"], "yellow")
        self.assertIn(pipeline.FLAG_READERS_DOWN, result["flags"])
