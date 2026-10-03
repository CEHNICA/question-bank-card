"""Original-image questions must survive actual Word/PDF export without OCR."""
import hashlib
import io
import zipfile
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import path
from PIL import Image

from . import library, library_export, library_pdf, library_assistant, library_jobs
from . import test_library_export as fixtures

urlpatterns = [path("api/library/export-docx", library_export.export_docx_view),
               path("api/library/export-pdf", library_pdf.export_pdf_view)]


@override_settings(ROOT_URLCONF=__name__)
class SourceImageExportTests(TestCase):
    setUp = fixtures.LibraryExportTests.setUp
    publication = fixtures.LibraryExportTests.publication
    payload = fixtures.LibraryExportTests.payload
    post = fixtures.LibraryExportTests.post

    def image_question(self):
        pub = self.publication(stem="", kind="single_choice")
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True)
        images = []
        for index, (size, color) in enumerate((((1200, 400), "red"), ((1200, 300), "blue")), 1):
            target = folder / f"question-{index}.png"
            Image.new("RGB", size, color).save(target)
            images.append({"file": target.name, "width": size[0], "height": size[1],
                           "page_idx": index - 1, "bbox": [100, 100, 900, 400], "order": index - 1,
                           "image_sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        # A separately extracted figure is kept for later transcription; do not
        # insert it again while the original image is the full question body.
        pub.content.update(body_mode="source_image", question_images=images,
                           figures=[{"file": "figure-1.png", "slot": "B"}])
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        return pub, folder

    def test_word_contains_all_ordered_fragments_with_empty_stem_and_no_duplicate_figure(self):
        pub, folder = self.image_question()
        before = pub.content_hash
        result = self.post(self.payload([pub]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        xml = fixtures.document_xml(result.content)
        self.assertEqual(len(xml.findall(".//a:blip", fixtures.NS)), 2)
        self.assertNotIn("题干为空", "".join(xml.itertext()))
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            media = [archive.read(name) for name in archive.namelist() if name.startswith("word/media/")]
        self.assertEqual(media, [(folder / "question-1.png").read_bytes(), (folder / "question-2.png").read_bytes()])
        pub.refresh_from_db()
        self.assertEqual(pub.content_hash, before)

    def test_missing_fragment_and_changed_pixels_abort_export(self):
        pub, folder = self.image_question()
        (folder / "question-2.png").unlink()
        self.assertEqual(self.post(self.payload([pub])).status_code, 409)
        Image.new("RGB", (1200, 300), "green").save(folder / "question-2.png")
        self.assertEqual(self.post(self.payload([pub])).status_code, 409)

    def test_empty_image_body_and_untrusted_asset_path_are_rejected(self):
        pub, _ = self.image_question()
        for images in ([], [{**pub.content["question_images"][0], "file": "../question-1.png"}]):
            pub.content["question_images"] = images
            pub.save(update_fields=["content"])
            self.assertEqual(self.post(self.payload([pub])).status_code, 409)

    def test_pdf_embeds_snapshot_fragments_and_rechecks_them_after_rendering(self):
        pub, folder = self.image_question()
        payload = self.payload([pub], output_format="pdf")
        def change(document):
            self.assertIn('"body_mode": "source_image"', document)
            self.assertEqual(document.count('"file": "data:image/png;base64,'), 2)
            self.assertNotIn('"slot": "B"', document)
            Image.new("RGB", (1200, 400), "green").save(folder / "question-1.png")
            return b"%PDF-1.7\n%%EOF\n", 1
        with mock.patch.object(library_pdf, "_render", side_effect=change):
            result = self.client.post("/api/library/export-pdf", __import__("json").dumps(payload),
                                      content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(result.status_code, 409)

    def test_body_image_dimensions_must_match_reviewed_snapshot(self):
        pub, _ = self.image_question()
        pub.content["question_images"][0]["width"] = 900
        pub.save(update_fields=["content"])
        self.assertEqual(self.post(self.payload([pub])).status_code, 409)

    def test_optional_assistant_and_model_receive_actual_image_body(self):
        pub, _ = self.image_question()
        prepared = library_assistant._images(pub)
        self.assertEqual(len(prepared["question_images"]), 2)
        self.assertEqual(prepared["figures"], [])
        with mock.patch.object(library_jobs.library_ai_settings, "chat", return_value=("【答案】D\n【解析】测试", "test")) as chat:
            result = library_jobs.run_answer(pub)
        self.assertEqual(result["answer"], "D")
        self.assertIn("原卷截图", chat.call_args.args[0])
        self.assertEqual(len(chat.call_args.args[1]), 2)
        self.assertTrue(all(image.startswith("data:image/jpeg;base64,") for image in chat.call_args.args[1]))
