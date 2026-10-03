"""Independent regression review: local source bodies and late OCR boundaries."""
from concurrent.futures import Future
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest import mock

import pymupdf as fitz
from PIL import Image, ImageChops
from django.test import TestCase, override_settings
from django.utils import timezone

from . import imaging, intake, library, native_pdf, pipeline, source_images
from .models import Paper, PublishedQuestion, Question


class ImmediateExecutor:
    """Exercise persistence deterministically without a second DB connection."""
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except BaseException as error:
            future.set_exception(error)
        return future


def original_pdf(*, text=True, pages=2, diagram=False):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)
        if text:
            page.insert_text((40, 60), "1. Find the area of the triangle shown below.", fontsize=12)
        if diagram:
            shape = page.new_shape()
            shape.draw_polyline([(350, 240), (300, 360), (430, 360), (350, 240)])
            shape.finish(color=(0, 0, 1), width=3)
            shape.commit()
        else:
            page.draw_rect(fitz.Rect(50, 90, 470, 330), color=(1, 0, 0), fill=(index / 3, .3, .7))
    result = document.tobytes()
    document.close()
    return result


class ManualIntakeReviewTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        setting = override_settings(DATA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)

    def paper(self, *, mode="manual", status=Paper.Status.READY, data=None):
        data = data or original_pdf()
        paper = Paper.objects.create(filename="review-original.pdf", kind="pdf",
                    sha256=hashlib.sha256(data).hexdigest(), status=status,
                    processing_plan={"schema": 1, "mode": mode, "revision": 0})
        folder = self.root / str(paper.pk)
        folder.mkdir()
        source = folder / "source.pdf"
        source.write_bytes(data)
        paper.source_path = str(source)
        with fitz.open(stream=data, filetype="pdf") as document:
            paper.pages = [{"page_idx": i, "width": 595, "height": 842}
                           for i in range(len(document))]
        paper.save()
        return paper

    def image_question(self):
        paper = self.paper()
        return Question.objects.create(paper=paper, number=1, body_mode="source_image",
            processing_mode="manual", question_type="free_response", state=Question.State.YELLOW,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]},
                     {"page_idx": 1, "bbox": [50, 50, 900, 420]}])

    def post(self, path, payload):
        return self.client.post(path, json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def run_read(self, question, answer):
        with mock.patch.object(pipeline, "ThreadPoolExecutor", ImmediateExecutor), \
                mock.patch.object(pipeline, "close_old_connections"), \
                mock.patch.object(pipeline, "_reader_parallelism", return_value=1), \
                mock.patch.object(pipeline.readers, "assistant_mode", return_value=False), \
                mock.patch.object(pipeline, "read_card", side_effect=answer):
            pipeline.read_questions(question.paper, [question])

    def test_manual_save_review_and_publish_make_zero_network_calls(self):
        upload = io.BytesIO(original_pdf())
        upload.name = "local.pdf"
        with mock.patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected cloud request")), \
                mock.patch.object(pipeline, "parse", side_effect=AssertionError("Unexpected automatic parse")), \
                mock.patch.object(pipeline.readers, "chat", side_effect=AssertionError("Unexpected model call")):
            result = self.client.post("/api/papers", {"file": upload, "parse_mode": "manual"}, HTTP_X_QB_REQUEST="1")
            self.assertEqual(result.status_code, 201, result.content)
            paper = Paper.objects.get(pk=result.json()["paper"]["id"])
            self.assertEqual(paper.status, Paper.Status.READY)
            result = self.post(f"/api/papers/{paper.pk}/questions", {
                "number": 1, "question_type": "free_response", "body_mode": "source_image",
                "processing_mode": "manual", "regions": [{"page_idx": 0, "bbox": [50, 50, 900, 420]}]})
            self.assertEqual(result.status_code, 201, result.content)
            question = paper.questions.get()
            result = self.post(f"/api/questions/{question.pk}/regions", {
                "revision": question.content_revision,
                "regions": [{"page_idx": 0, "bbox": [45, 45, 920, 450]}]})
            self.assertEqual(result.status_code, 200, result.content)
            question.refresh_from_db()
            self.assertFalse(question.reread_requested)
            result = self.post(f"/api/questions/{question.pk}/approve", {"approved": True})
            self.assertEqual(result.status_code, 200, result.content)
            question.refresh_from_db()
            publication = PublishedQuestion.objects.get(question=question, version=1)
            repeated, created = library.publish(question)
            self.assertFalse(created)
            self.assertEqual(repeated.pk, publication.pk)
            self.assertEqual(question.publications.count(), 1)
            self.assertEqual(len(publication.content["question_images"]), 1)

    def test_every_fragment_is_required_and_published_pixels_are_immutable(self):
        question = self.image_question()
        library.approve(question, now=timezone.now())
        question.save()
        publication, _ = library.publish(question)
        expected = deepcopy(publication.content)
        self.assertEqual(len(expected["question_images"]), 2)
        saved = [(self.root / "library" / str(publication.pk) / item["file"]).read_bytes()
                 for item in expected["question_images"]]
        question.regions = [{"page_idx": 0, "bbox": [100, 100, 700, 250]}]
        question.content_revision += 1
        question.save()
        self.assertFalse(library.approval_is_current(question))
        publication.refresh_from_db()
        self.assertEqual(publication.content, expected)
        self.assertEqual(saved, [(self.root / "library" / str(publication.pk) / item["file"]).read_bytes()
                                 for item in expected["question_images"]])
        question.regions = [{"page_idx": 0, "bbox": [50, 50, 900, 420]},
                            {"page_idx": 99, "bbox": [50, 50, 900, 420]}]
        question.save()
        self.assertFalse(source_images.body_valid(question))
        with self.assertRaises(ValueError):
            library.publish(question)

    def test_explicit_default_text_mode_preserves_legacy_content_hash(self):
        old = {"number": 1, "question_type": "free_response", "stem": "test",
               "options": {}, "figures": [], "sources": [], "origin": ""}
        new = {**old, "body_mode": "text", "question_images": [], "content_revision": 15}
        self.assertEqual(library.content_hash(old), library.content_hash(new))

    def test_truncated_cached_fragment_cannot_pass_body_validation(self):
        question = self.image_question()
        image = source_images.assets(question)[0]
        target = self.root / str(question.paper_id) / "question-images" / image["file"]
        data = target.read_bytes()
        marker = data.index(b"IDAT")
        length = int.from_bytes(data[marker - 4:marker], "big")
        # PNG header/dimensions remain readable, but its pixels cannot decode.
        target.write_bytes(data[:marker + 4 + max(1, length // 2)])
        self.assertFalse(source_images.body_valid(question))

    def test_image_ocr_cannot_overwrite_approved_or_published_original(self):
        question = self.image_question()
        library.approve(question, now=timezone.now())
        question.reread_requested = True
        question.save()
        publication, _ = library.publish(question)
        expected_publication = deepcopy(publication.content)
        answer = {"stem": "Recognized draft", "options": {}, "question_type": "free_response",
                  "figures": [], "figure_review": {"status": "ok"}, "flags": [], "state": Question.State.YELLOW}
        self.run_read(question, lambda *args: deepcopy(answer))
        question.refresh_from_db()
        self.assertEqual(question.body_mode, "source_image")
        self.assertEqual(question.stem, "")
        self.assertTrue(library.approval_is_current(question))
        self.assertFalse(question.ocr_suggestion or question.reread_requested or question.ocr_pending)
        result = self.post(f"/api/questions/{question.pk}/reread", {"revision": question.content_revision})
        self.assertEqual(result.status_code, 409, result.content)
        publication.refresh_from_db()
        self.assertEqual(publication.content, expected_publication)

    def test_late_question_read_cannot_overwrite_transfer_to_manual(self):
        paper = self.paper(mode="mineru")
        question = Question.objects.create(paper=paper, number=1, question_type="free_response",
            stem="Original human draft", processing_mode="auto", state=Question.State.WAITING,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])
        def finish(*args):
            intake.select_manual(paper)
            return {"stem": "Late cloud reply", "options": {}, "flags": [], "state": Question.State.GREEN}
        self.run_read(question, finish)
        question.refresh_from_db()
        self.assertEqual(question.stem, "Original human draft")
        self.assertEqual(question.processing_mode, "manual")
        self.assertEqual(question.state, Question.State.YELLOW)

    def test_late_parse_failure_cannot_mark_manual_task_failed(self):
        paper = self.paper(mode="mineru", status=Paper.Status.QUEUED)
        def fail(*args, **kwargs):
            intake.select_manual(paper)
            raise RuntimeError("Old cloud call failed after transfer")
        with mock.patch.object(pipeline, "parse", side_effect=fail):
            pipeline.process_paper(paper)
        paper.refresh_from_db()
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertEqual(paper.status, Paper.Status.READY)
        self.assertEqual(paper.error, "")

    def test_late_parse_ahead_failure_cannot_mark_manual_task_failed(self):
        paper = self.paper(mode="mineru", status=Paper.Status.QUEUED)
        def fail(*args, **kwargs):
            intake.select_manual(paper)
            raise RuntimeError("Old parse lane failed after transfer")
        with mock.patch.object(pipeline, "parse", side_effect=fail):
            pipeline.parse_ahead(paper)
        paper.refresh_from_db()
        self.assertEqual(paper.processing_plan["mode"], "manual")
        self.assertEqual(paper.status, Paper.Status.READY)
        self.assertEqual(paper.error, "")

    def test_scan_page_is_marked_manual_without_invented_blocks(self):
        paper = self.paper(data=original_pdf(text=False, pages=1, diagram=True))
        result = native_pdf.extract(Path(paper.source_path))
        self.assertEqual(result["pages"][0]["mode"], "manual")
        self.assertFalse(any(item["type"] == "text" for item in result["blocks"]))

    def test_native_question_body_must_not_crop_away_below_text_diagram(self):
        paper = self.paper(mode="native", data=original_pdf(pages=1, diagram=True))
        intake.prepare(paper, "native")
        questions = list(paper.questions.all())
        # A conservative fallback to manual is acceptable. If a whole-image
        # question is proposed, its source crop must include the real diagram.
        if not questions:
            return
        diagram = [300 / 595 * 1000, 240 / 842 * 1000, 430 / 595 * 1000, 360 / 842 * 1000]
        self.assertTrue(any(region["page_idx"] == 0 and region["bbox"][0] <= diagram[0]
                            and region["bbox"][1] <= diagram[1] and region["bbox"][2] >= diagram[2]
                            and region["bbox"][3] >= diagram[3]
                            for region in questions[0].regions), questions[0].regions)

    def test_native_question_cannot_skip_an_intervening_scan_page(self):
        document = fitz.open()
        document.new_page(width=595, height=842).insert_text(
            (40, 60), "1. This question continues on the next page.", fontsize=12)
        scan_page = document.new_page(width=595, height=842)
        raster = io.BytesIO()
        Image.new("RGB", (200, 300), (30, 80, 160)).save(raster, format="PNG")
        scan_page.insert_image(scan_page.rect, stream=raster.getvalue())
        document.new_page(width=595, height=842).insert_text(
            (40, 60), "2. Next question starts here.", fontsize=12)
        paper = self.paper(mode="native", data=document.tobytes())
        document.close()
        intake.prepare(paper, "native")
        question = paper.questions.filter(number=1).first()
        # The ambiguous predecessor may be handed to manual work. It must
        # never be proposed as a complete body while omitting its scan page.
        if question is not None:
            self.assertIn(1, [item["page_idx"] for item in question.regions], question.regions)

    def test_rotated_cropped_native_geometry_agrees_with_actual_page_pixels(self):
        document = fitz.open(stream=original_pdf(pages=1, diagram=True), filetype="pdf")
        document[0].set_cropbox(fitz.Rect(20, 40, 560, 800))
        document[0].set_rotation(90)
        paper = self.paper(mode="native", data=document.tobytes())
        document.close()
        result = native_pdf.extract(Path(paper.source_path))
        visuals = [block for block in result["blocks"] if block["type"] == "image"]
        self.assertTrue(visuals, result)
        raster = imaging.render_source_page(Path(paper.source_path), "pdf", 0).convert("RGB")
        red, green, blue = raster.split()
        colored_pixels = ImageChops.subtract(blue, red).point(lambda value: 255 if value > 150 else 0).getbbox()
        self.assertIsNotNone(colored_pixels)
        expected = [colored_pixels[0] / raster.width * 1000, colored_pixels[1] / raster.height * 1000,
                    colored_pixels[2] / raster.width * 1000, colored_pixels[3] / raster.height * 1000]
        # Rendered stroke width extends slightly outside the geometric path.
        self.assertTrue(any(all(abs(a - b) < 5 for a, b in zip(block["bbox"], expected)) for block in visuals),
                        {"actual_pixel_bounds": expected, "native_visuals": visuals})

    def test_two_columns_keep_native_original_bodies_separate(self):
        document = fitz.open()
        page = document.new_page(width=595, height=842)
        page.insert_text((35, 70), "1. Left column question", fontsize=11)
        page.insert_text((330, 70), "2. Right column question", fontsize=11)
        page.draw_rect(fitz.Rect(70, 150, 250, 320), color=(0, 0, 1), width=3)
        paper = self.paper(mode="native", data=document.tobytes())
        document.close()
        intake.prepare(paper, "native")
        questions = {question.number: question for question in paper.questions.all()}
        if not questions:
            return  # a conservative manual fallback is valid
        self.assertEqual(set(questions), {1, 2})
        left, right = questions[1], questions[2]
        right_start = [330 / 595 * 1000, 70 / 842 * 1000]
        self.assertFalse(any(region["bbox"][0] <= right_start[0] <= region["bbox"][2]
                             and region["bbox"][1] <= right_start[1] <= region["bbox"][3]
                             for region in left.regions), left.regions)
        self.assertTrue(any(region["bbox"][0] <= 70 / 595 * 1000 and region["bbox"][2] >= 250 / 595 * 1000
                            and region["bbox"][1] <= 150 / 842 * 1000 and region["bbox"][3] >= 320 / 842 * 1000
                            for region in left.regions), left.regions)
        self.assertNotEqual(left.regions, right.regions)
