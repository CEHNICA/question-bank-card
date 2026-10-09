"""Local originals only: isolated files, generated PDFs and mocked readers."""
from __future__ import annotations

from concurrent.futures import Future
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest import mock

import pymupdf as fitz
from PIL import Image
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from . import imaging, intake, library, native_pdf, pipeline, segment, source_images, views
from .models import Paper, PublishedQuestion, Question, QuestionGroup, Block


class InlineExecutor:
    def __init__(self, **kwargs):
        pass
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def submit(self, func, *args):
        future = Future()
        try:
            future.set_result(func(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


class LocalIntakeTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        config = override_settings(DATA_ROOT=self.folder)
        config.enable()
        self.addCleanup(config.disable)
        self.factory = RequestFactory()

    def pdf(self, name="original.pdf", *, scan=False, mixed=False, rotation=0):
        path = self.folder / name
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=600)
            if not scan:
                page.insert_text((30, 55), "1. Find x when x + 2 = 5.")
                page.insert_text((30, 180), "2. Compute the area of this triangle.")
            else:
                png = io.BytesIO()
                Image.new("RGB", (120, 180), "white").save(png, "PNG")
                page.insert_image(page.rect, stream=png.getvalue())
            page.set_rotation(rotation)
            if mixed:
                page = pdf.new_page(width=400, height=600)
                png = io.BytesIO()
                Image.new("RGB", (120, 180), "lightblue").save(png, "PNG")
                page.insert_image(page.rect, stream=png.getvalue())
            pdf.save(path)
        return path

    def paper(self, **kwargs):
        source = self.pdf(**kwargs)
        return Paper.objects.create(filename=source.name, kind="pdf", source_path=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(), status=Paper.Status.READY)

    def question(self, paper, **kwargs):
        intake.prepare(paper, "manual")
        defaults = dict(paper=paper, group=paper.question_groups.first(), number=1,
            body_mode="source_image", processing_mode="manual", question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [30, 50, 950, 300]}], state=Question.State.YELLOW)
        defaults.update(kwargs)
        return Question.objects.create(**defaults)

    def action(self, question, action, payload=None):
        request = self.factory.post("/", json.dumps(payload or {}), content_type="application/json", HTTP_X_QB_REQUEST="1")
        return views.question_action(request, question.pk, action)

    def run_read(self, question, result):
        with mock.patch("core.pipeline.ThreadPoolExecutor", InlineExecutor), \
                mock.patch("core.pipeline._reader_parallelism", return_value=1), \
                mock.patch("core.pipeline.close_old_connections"), \
                mock.patch("core.pipeline.readers.assistant_mode", return_value=False), \
                mock.patch("core.pipeline.read_card", side_effect=result if callable(result) else None,
                           return_value=result if not callable(result) else None):
            pipeline.read_questions(question.paper, [question])

    def test_upload_without_credentials_prepares_original_without_cloud(self):
        source = self.pdf()
        upload = io.BytesIO(source.read_bytes())
        upload.name = "a.pdf"
        request = self.factory.post("/api/papers", {"file": upload}, HTTP_X_QB_REQUEST="1")
        with mock.patch("core.views.readers.configured", return_value=False), \
                mock.patch("core.views._reading_ready", return_value=False), \
                mock.patch("core.pipeline.request_extract_file_from_pool") as cloud:
            response = views.papers(request)
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get()
        self.assertEqual(paper.status, "ready")
        self.assertEqual(paper.processing_plan["mode"], "native")
        self.assertEqual(paper.processing_plan["requested_mode"], "auto")
        self.assertEqual(len(paper.pages), 1)
        self.assertEqual(paper.question_groups.count(), 1)
        self.assertFalse(paper.import_chunks.exists())
        cloud.assert_not_called()

    def test_explicit_mineru_still_requires_configuration(self):
        upload = io.BytesIO(self.pdf().read_bytes())
        upload.name = "a.pdf"
        request = self.factory.post("/api/papers", {"file": upload, "parse_mode": "mineru"}, HTTP_X_QB_REQUEST="1")
        with mock.patch("core.views.readers.configured", return_value=False):
            response = views.papers(request)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Paper.objects.exists())

    def test_manual_photo_upload_and_page_reorder_need_no_recognition(self):
        uploads = []
        for index, color in enumerate(("white", "lightblue")):
            upload = io.BytesIO()
            Image.new("RGB", (100, 150), color).save(upload, "PNG")
            upload.seek(0)
            upload.name = f"photo-{index}.png"
            uploads.append(upload)
        request = self.factory.post("/api/papers", {"file": uploads, "parse_mode": "manual", "enhance": "0"}, HTTP_X_QB_REQUEST="1")
        with mock.patch("core.pipeline.request_extract_file_from_pool") as cloud:
            response = views.papers(request)
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get()
        self.assertEqual(len(paper.pages), 2)
        question = Question.objects.create(paper=paper, group=paper.question_groups.first(), number=1,
            processing_mode="manual", body_mode="source_image", state="yellow", question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [10, 10, 900, 900]}])
        original_pixels = source_images.asset_file(question, 0).read_bytes()
        request = self.factory.post("/", json.dumps({"order": [1, 0]}), content_type="application/json", HTTP_X_QB_REQUEST="1")
        response = views.paper_page_order(request, paper.pk)
        self.assertEqual(response.status_code, 200, response.content)
        paper.refresh_from_db()
        question.refresh_from_db()
        self.assertEqual(paper.status, "ready")
        self.assertEqual(question.regions[0]["page_idx"], 1)
        self.assertEqual(source_images.asset_file(question, 0).read_bytes(), original_pixels)
        self.assertEqual(question.content_revision, 1)
        cloud.assert_not_called()

    def test_manual_empty_text_can_be_reviewed_and_published_with_verified_images(self):
        question = self.question(self.paper(mixed=True), regions=[
            {"page_idx": 1, "bbox": [10, 10, 500, 500]}, {"page_idx": 0, "bbox": [10, 30, 900, 300]}],
            figure_review={"status": "blocked_missing", "source": "auto"})
        key = question.source_key
        self.assertEqual(self.action(question, "approve").status_code, 200)
        question.refresh_from_db()
        publication = PublishedQuestion.objects.get(question=question, version=1)
        repeated, created = library.publish(question)
        self.assertFalse(created)
        self.assertEqual(repeated.pk, publication.pk)
        self.assertEqual(question.publications.count(), 1)
        self.assertEqual(publication.content["body_mode"], "source_image")
        self.assertEqual(publication.content["stem"], "")
        self.assertEqual(publication.content["figures"], [])
        self.assertEqual([i["page_idx"] for i in publication.content["question_images"]], [1, 0])
        for item in publication.content["question_images"]:
            path = self.folder / "library" / str(publication.pk) / item["file"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), item["image_sha256"])
            with Image.open(path) as image:
                self.assertEqual(image.size, (item["width"], item["height"]))
        repeated, created = library.publish(question)
        self.assertFalse(created)
        self.assertEqual(repeated.pk, publication.pk)
        self.assertEqual(question.source_key, key)

    def test_manual_range_change_preserves_human_text_type_and_figure(self):
        question = self.question(self.paper(), body_mode="text", stem="Manually corrected text", edited=True,
            type_locked=True, figures=[{"slot": "stem", "page_idx": 0, "bbox": [40, 80, 180, 160], "source": "manual"}])
        old = list(question.figures)
        response = self.action(question, "regions", {"regions": [{"page_idx": 0, "bbox": [20, 20, 980, 400]}]})
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.stem, "Manually corrected text")
        self.assertEqual(question.figures, old)
        self.assertTrue(question.type_locked)
        self.assertTrue(question.edited)
        self.assertFalse(question.reread_requested)
        self.assertEqual(question.content_revision, 1)

    def test_editing_image_body_explicitly_adopts_text_without_changing_source(self):
        question = self.question(self.paper())
        key, regions = question.source_key, question.regions
        response = self.action(question, "text", {"stem": "A verified question", "question_type": "free_response"})
        self.assertEqual(response.status_code, 200)
        question.refresh_from_db()
        self.assertEqual(question.body_mode, "text")
        self.assertEqual(question.source_key, key)
        self.assertEqual(question.regions, regions)

    def test_reordered_crops_invalidate_approval_and_old_snapshot_is_immutable(self):
        question = self.question(self.paper(mixed=True), regions=[
            {"page_idx": 0, "bbox": [20, 20, 950, 300]}, {"page_idx": 1, "bbox": [20, 20, 950, 300]}])
        library.approve(question, now=timezone.now())
        question.save()
        old, _ = library.publish(question)
        original = json.dumps(old.content, sort_keys=True)
        original_files = [(self.folder / "library" / str(old.pk) / item["file"]).read_bytes()
                          for item in old.content["question_images"]]
        question.regions.reverse()
        question.save()
        self.assertFalse(library.approval_is_current(question))
        library.approve(question, now=timezone.now())
        question.save()
        new, _ = library.publish(question)
        self.assertEqual(new.version, 2)
        old.refresh_from_db()
        self.assertEqual(json.dumps(old.content, sort_keys=True), original)
        self.assertEqual([(self.folder / "library" / str(old.pk) / item["file"]).read_bytes()
                          for item in old.content["question_images"]], original_files)
        self.assertIn("原图正文", [change["label"] for change in library.publication_changes(old, new)])

    def test_source_change_invalidates_cached_body_and_approval(self):
        question = self.question(self.paper())
        library.approve(question, now=timezone.now())
        question.save()
        old = source_images.assets(question)[0]
        path = Path(question.paper.source_path)
        with fitz.open(path) as pdf:
            pdf[0].insert_text((70, 100), "Additional source content")
            pdf.saveIncr()
        fresh = source_images.assets(question)[0]
        self.assertNotEqual(old["file"], fresh["file"])
        self.assertFalse(library.approval_is_current(question))

    def test_image_question_without_valid_original_cannot_be_approved(self):
        question = self.question(self.paper())
        Path(question.paper.source_path).unlink()
        self.assertEqual(self.action(question, "approve").status_code, 400)
        question.refresh_from_db()
        self.assertFalse(question.approved)

    def test_non_integer_and_non_finite_page_coordinates_are_rejected(self):
        question = self.question(self.paper())
        for page, box in [(True, [1, 1, 300, 300]), (0, [1, float("nan"), 300, 300]), (999, [1, 1, 300, 300])]:
            self.assertEqual(self.action(question, "regions", {"regions": [{"page_idx": page, "bbox": box}]}).status_code, 400)

    def test_local_native_extracts_real_rotated_coordinates_without_formula_invention(self):
        path = self.pdf(rotation=90)
        result = native_pdf.extract(path)
        self.assertTrue(result["blocks"])
        with fitz.open(path) as pdf:
            line = pdf[0].get_text("dict", sort=True)["blocks"][0]["lines"][0]
            expected = native_pdf.normalized_bbox(pdf[0], line["bbox"])
        self.assertEqual(result["blocks"][0]["bbox"], expected)
        self.assertIn("x + 2 = 5", result["blocks"][0]["text"])
        self.assertNotIn("\\frac", result["blocks"][0]["text"])
        self.assertTrue(result["pages"][0]["warnings"])

    def test_scanned_page_remains_manual_and_local_success_is_retained(self):
        paper = self.paper(mixed=True)
        with mock.patch("core.pipeline.request_extract_file_from_pool") as cloud, \
                mock.patch("core.pipeline.read_card") as vision:
            intake.prepare(paper, "native")
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual([page["mode"] for page in paper.processing_plan["pages"]], ["native", "manual"])
        self.assertTrue(paper.blocks.exists())
        self.assertTrue(paper.questions.exists())
        self.assertTrue(all(question.body_mode == "source_image" for question in paper.questions.all()))
        cloud.assert_not_called()
        vision.assert_not_called()

    def test_native_missing_numbers_warn_on_previous_card_and_group_diagnostics(self):
        path = self.folder / "gap.pdf"
        with fitz.open() as pdf:
            page = pdf.new_page(width=400, height=600)
            page.insert_text((30, 55), "1. Find x when x + 2 = 5.")
            page.insert_text((30, 180), "4. Compute the area of this triangle.")
            pdf.save(path)
        paper = Paper.objects.create(filename=path.name, kind="pdf", source_path=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), status=Paper.Status.READY)
        with mock.patch("core.pipeline.request_extract_file_from_pool") as cloud, \
                mock.patch("core.pipeline.read_card") as vision:
            intake.prepare(paper, "native")
        paper.refresh_from_db()
        self.assertEqual(list(paper.questions.values_list("number", flat=True)), [1, 4])
        previous = paper.questions.get(number=1)
        following = paper.questions.get(number=4)
        self.assertIn(pipeline.merged_question_flag(2), previous.flags)
        self.assertIn(pipeline.merged_question_flag(3), previous.flags)
        self.assertNotIn(pipeline.merged_question_flag(2), following.flags)
        gaps = [item for item in paper.processing_plan["segmentation_diagnostics"]
                if item["code"] == "missing_question_numbers"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["numbers"], [2, 3])
        self.assertEqual(gaps[0]["previous_number"], 1)
        self.assertEqual(gaps[0]["page_idx"], 0)
        self.assertTrue(gaps[0]["question_created"])
        self.assertTrue(gaps[0]["bbox"])
        self.assertTrue(any("第 2、3 题" in warning for warning in paper.processing_plan["warnings"]))
        cloud.assert_not_called()
        vision.assert_not_called()

    def test_native_preserves_boundary_flags_notes_and_location_diagnostics(self):
        paper = self.paper()
        source = Path(paper.source_path)
        blocks = native_pdf.extract(source)["blocks"]
        result = segment.segment(imaging.page_sizes(source, "pdf"), blocks)
        first = result["questions"][0]
        first["segmentation_flags"] = ["续段范围需要核对", "续段范围需要核对"]
        first["segmentation_notes"] = ["纯算式续段已保留，请对照原卷核对", "纯算式续段已保留，请对照原卷核对"]
        detail = {"code": "ambiguous_formula_continuation", "page_idx": 0, "bbox": [30, 80, 300, 100],
                  "reason": "无法确认是否为草稿", "action": "retained"}
        first["segmentation_diagnostics"] = [detail]
        first["segmentation"] = {"range_limited": False, "retained_continuation": True}
        with mock.patch("core.intake.segment.segment", return_value=result):
            intake.prepare(paper, "native")
        paper.refresh_from_db()
        question = paper.questions.get(number=1)
        self.assertEqual(question.flags.count("续段范围需要核对"), 1)
        self.assertEqual(question.flags.count("纯算式续段已保留，请对照原卷核对"), 1)
        self.assertEqual(paper.processing_plan["warnings"].count("纯算式续段已保留，请对照原卷核对"), 1)
        diagnostics = paper.processing_plan["segmentation_diagnostics"]
        preserved = next(item for item in diagnostics if item["code"] == detail["code"])
        self.assertEqual(preserved, {**detail, "question_number": 1, "question_created": True})
        review = next(item for item in diagnostics if item["code"] == "question_segmentation_review"
                      and item["question_number"] == 1)
        self.assertEqual(review["metadata"], first["segmentation"])
        self.assertEqual(review["notes"], ["纯算式续段已保留，请对照原卷核对"])
        self.assertNotIn("question_created", detail)

    def test_native_leading_suspicion_is_visible_on_paper_and_first_card(self):
        paper = self.paper()
        source = Path(paper.source_path)
        blocks = native_pdf.extract(source)["blocks"]
        result = segment.segment(imaging.page_sizes(source, "pdf"), blocks)
        result["questions"] = result["questions"][1:]
        message = "首个检测到的题号是第 2 题，前方还有疑似题目正文，但证据不足，可能漏了组首题。"
        result["leading"] = segment.LeadingQuestionCheck(status="suspected", first_detected=2,
            candidate_seq=blocks[0]["seq"], message=message)
        with mock.patch("core.intake.segment.segment", return_value=result):
            intake.prepare(paper, "native")
        paper.refresh_from_db()
        self.assertEqual(paper.questions.count(), 1)
        self.assertIn(message, paper.questions.get(number=2).flags)
        self.assertIn(message, paper.processing_plan["warnings"])
        leading = next(item for item in paper.processing_plan["segmentation_diagnostics"]
                       if item["code"] == "leading_question_check")
        self.assertEqual(leading["status"], "suspected")
        self.assertEqual(leading["question_number"], 2)
        self.assertEqual(leading["bbox"], blocks[0]["bbox"])
        self.assertTrue(leading["question_created"])

    def test_native_keeps_diagnostics_for_card_skipped_before_unreadable_page(self):
        paper = self.paper(mixed=True)
        source = Path(paper.source_path)
        blocks = native_pdf.extract(source)["blocks"]
        result = segment.segment(imaging.page_sizes(source, "pdf"), [block for block in blocks if block["page_idx"] == 0])
        last = result["questions"][-1]
        last["segmentation_flags"] = ["可能包含未识别的下一题"]
        last["segmentation_diagnostics"] = [{"code": "ambiguous_formula_continuation", "page_idx": 0,
                                             "bbox": [30, 300, 200, 350], "action": "retained"}]
        with mock.patch("core.intake.segment.segment", return_value=result):
            intake.prepare(paper, "native")
        paper.refresh_from_db()
        self.assertFalse(paper.questions.filter(number=last["number"]).exists())
        relevant = [item for item in paper.processing_plan["segmentation_diagnostics"]
                    if item["question_number"] == last["number"]]
        self.assertTrue(relevant)
        self.assertTrue(all(item["question_created"] is False for item in relevant))
        unreadable = next(item for item in relevant if item["code"] == "unreadable_continuation")
        self.assertEqual(unreadable["page_indices"], [1])
        self.assertTrue(any("未自动截成不完整题卡" in warning for warning in paper.processing_plan["warnings"]))

    def test_native_inferred_first_number_does_not_promise_a_cloud_read(self):
        paper = self.paper()
        source = Path(paper.source_path)
        blocks = native_pdf.extract(source)["blocks"]
        result = segment.segment(imaging.page_sizes(source, "pdf"), blocks)
        result["leading"] = segment.LeadingQuestionCheck(status="repaired", first_detected=2,
            candidate_seq=blocks[0]["seq"], message=("MinerU 漏读了第 1 题题号，已用本地版面规则补出第 1 题。"
                                                     "新增题卡仍按正常流程识读。题号由本地规则补出，请对照原卷核对。"))
        with mock.patch("core.intake.segment.segment", return_value=result), \
                mock.patch("core.pipeline.read_card") as vision:
            intake.prepare(paper, "native")
        paper.refresh_from_db()
        inferred = paper.questions.get(number=1)
        self.assertTrue(any("本地文字层未读到第 1 题题号" in flag for flag in inferred.flags))
        self.assertTrue(all("正常流程识读" not in flag and "MinerU" not in flag for flag in inferred.flags))
        diagnostic = next(item for item in paper.processing_plan["segmentation_diagnostics"]
                          if item["code"] == "leading_question_check")
        self.assertEqual(diagnostic["status"], "repaired")
        self.assertEqual(diagnostic["first_detected"], 2)
        self.assertEqual(diagnostic["question_number"], 1)
        self.assertTrue(diagnostic["question_created"])
        vision.assert_not_called()

    def test_page_transfer_retains_success_manual_edits_and_publications(self):
        question = self.question(self.paper(mixed=True), body_mode="text", stem="Retained answer", edited=True)
        other = Question.objects.create(paper=question.paper, group=question.group, number=2,
            stem="Successful automatic question", state="green", processing_mode="auto", question_type="free_response",
            regions=[{"page_idx": 1, "bbox": [50, 50, 900, 400]}])
        library.approve(question, now=timezone.now())
        question.save()
        publication, _ = library.publish(question)
        intake.select_manual(question.paper, [0])
        question.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(question.stem, "Retained answer")
        self.assertEqual(other.processing_mode, "auto")
        self.assertEqual(other.stem, "Successful automatic question")
        self.assertTrue(library.approval_is_current(question))
        self.assertEqual(question.publications.get().pk, publication.pk)

    def test_processing_api_rejects_page_only_mode_without_mutation_or_new_queue(self):
        paper = self.paper(mixed=True)
        intake.prepare(paper, "manual")
        paper.processing_plan = {"schema": 1, "revision": 3, "mode": "mineru"}
        paper.save(update_fields=["processing_plan"])
        request = self.factory.post("/", json.dumps({"mode": "manual", "pages": [0]}),
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        with mock.patch("core.views.intake.select_manual") as transfer, \
                mock.patch("core.pipeline.request_extract_file_from_pool") as cloud:
            response = views.paper_processing(request, paper.pk)
        self.assertEqual(response.status_code, 400)
        self.assertIn("本版仅支持整份", json.loads(response.content)["error"])
        paper.refresh_from_db()
        self.assertEqual(paper.processing_plan, {"schema": 1, "revision": 3, "mode": "mineru"})
        self.assertEqual(paper.status, "ready")
        self.assertFalse(paper.import_chunks.exists())
        transfer.assert_not_called()
        cloud.assert_not_called()

    def test_processing_api_whole_manual_transfer_preserves_success_and_stops_cloud(self):
        question = self.question(self.paper(mixed=True), body_mode="text", stem="Successful verified text", edited=True)
        paper = question.paper
        paper.processing_plan = {"schema": 1, "revision": 3, "mode": "mineru"}
        paper.status = Paper.Status.FAILED
        paper.save(update_fields=["processing_plan", "status"])
        source_key = question.source_key
        request = self.factory.post("/", json.dumps({"mode": "manual"}),
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        with mock.patch("core.pipeline.request_extract_file_from_pool") as cloud, \
                mock.patch("core.pipeline.read_card") as vision:
            response = views.paper_processing(request, paper.pk)
            paper.refresh_from_db()
            pipeline.process_paper(paper)
            self.assertFalse(pipeline.parse_ahead(paper))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertEqual(paper.status, "ready")
        question.refresh_from_db()
        self.assertEqual(question.stem, "Successful verified text")
        self.assertEqual(question.source_key, source_key)
        self.assertEqual(question.processing_mode, "manual")
        cloud.assert_not_called()
        vision.assert_not_called()

    def test_manual_card_is_not_automatically_read(self):
        question = self.question(self.paper())
        with mock.patch("core.pipeline.read_card") as vision:
            pipeline.read_questions(question.paper, [question])
        vision.assert_not_called()
        question.refresh_from_db()
        self.assertEqual(question.state, "yellow")

    def test_requested_image_reading_cannot_overwrite_a_previously_approved_body(self):
        question = self.question(self.paper(), reread_requested=True)
        library.approve(question, now=timezone.now())
        question.save()
        original_hash, key = question.approved_content_hash, question.source_key
        result = {"stem": "Recognized question", "options": {}, "question_type": "free_response",
                  "state": "green", "flags": [], "figures": [], "figure_review": {"status": "ok"}}
        self.run_read(question, result)
        question.refresh_from_db()
        self.assertEqual(question.body_mode, "source_image")
        self.assertEqual(question.stem, "")
        self.assertEqual(question.approved_content_hash, original_hash)
        self.assertTrue(library.approval_is_current(question))
        self.assertFalse(question.ocr_suggestion or question.ocr_pending or question.reread_requested)
        self.assertEqual(self.action(question, "reread", {"revision": 0}).status_code, 409)
        self.assertEqual(question.source_key, key)

    def test_late_reader_cannot_overwrite_manual_revision_even_same_coordinates(self):
        question = self.question(self.paper(), body_mode="text", processing_mode="auto", stem="Original", reread_requested=True)
        def result(snapshot, store):
            self.action(question, "regions", {"processing_mode": "manual", "regions": question.regions})
            self.action(question, "text", {"stem": "Newest human correction", "question_type": "free_response"})
            return {"stem": "Late cloud result", "state": "green", "flags": []}
        self.run_read(question, result)
        question.refresh_from_db()
        self.assertEqual(question.stem, "Newest human correction")
        # An unchanged range is a no-op; it neither switches the saved mode
        # nor advances the revision. The explicit human edit rejects late OCR.
        self.assertEqual(question.processing_mode, "auto")
        self.assertEqual(question.content_revision, 1)

    def test_expired_suggestion_cannot_be_adopted_after_range_edit(self):
        question = self.question(self.paper(), ocr_suggestion={"revision": 0, "stem": "Stale suggestion"})
        self.action(question, "regions", {"regions": [{"page_idx": 0, "bbox": [10, 20, 950, 350]}]})
        self.assertEqual(self.action(question, "apply-reading", {"revision": 0}).status_code, 409)

    def test_legacy_worker_query_includes_empty_plan_and_excludes_manual(self):
        legacy = self.paper()
        manual = self.paper(name="manual.pdf")
        manual.processing_plan = {"mode": "manual"}
        manual.save()
        selected = Paper.objects.exclude(processing_plan__mode__in=["manual", "native"], processing_plan__mode__isnull=False)
        self.assertIn(legacy.pk, selected.values_list("pk", flat=True))
        self.assertNotIn(manual.pk, selected.values_list("pk", flat=True))
