"""Edited solution overlays, immutable draft choices, and one-off AI boundaries."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import zipfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import features, library, library_assistant, library_drafts, library_export, library_jobs, library_pdf, library_solutions
from .models import LibraryJob, LibrarySolution, Paper, PublishedQuestion, Question
from .test_library_export import document_xml, field, math_field, NS


@override_settings(ROOT_URLCONF="qb_server.urls")
class LibrarySolutionTests(TransactionTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        state = override_settings(DATA_ROOT=self.root)
        state.enable()
        self.addCleanup(state.disable)
        env = mock.patch.dict(os.environ, {
            "QB_FEATURES_FILE": str(self.root / "features.json"),
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.root / "ai.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.root / "key.dat"),
        })
        env.start()
        self.addCleanup(env.stop)
        self.client = Client()
        self.pub = self.publication()

    def publication(self, *, stem="合成题甲", answer="原卷 B", analysis="", qtype="single_choice"):
        source = self.root / (uuid.uuid4().hex + ".png")
        Image.new("RGB", (300, 400), "white").save(source)
        paper = Paper.objects.create(filename="离线合成卷.png", source_path=str(source), sha256="a" * 64,
                                    kind="image", pages=[{"page_idx": 0, "width": 300, "height": 400}])
        question = Question.objects.create(paper=paper, number=1, stem=stem, answer=answer, analysis=analysis,
                                           question_type=qtype, options={"A": "1", "B": "2"}, approved=True, approved_at=timezone.now())
        content = library.final_content(question)
        return PublishedQuestion.objects.create(paper=paper, question=question, source_filename=paper.filename,
            number=1, question_type=qtype, version=1, content=content, content_hash=library.content_hash(content))

    def test_editor_get_exposes_existing_valid_ai_draft_without_saving_overlay(self):
        fingerprint = library.generation_fingerprint(self.pub.content, self.pub.pk)
        self.pub.extras = {"ai_answer": {"answer": "参考 C", "analysis": "所有小问步骤",
                                       "fingerprint": fingerprint, "engine": "doubao", "checked": False}}
        self.pub.save(update_fields=["extras"])
        data = self.client.get(f"/api/library/{self.pub.pk}/solution").json()
        self.assertEqual(data["ai_answer"]["analysis"], "所有小问步骤")
        self.assertFalse(data["ai_answer_stale"])
        self.assertEqual(data["origin"]["answer"], "原卷 B")
        self.assertIsNone(data["solution"])
        self.assertEqual(LibrarySolution.objects.count(), 0)

    def test_stale_ai_draft_is_not_prefilled_even_when_origin_selected(self):
        self.pub.extras = {"ai_answer": {"answer": "旧答案", "analysis": "旧步骤", "fingerprint": "0" * 64}}
        self.pub.save(update_fields=["extras"])
        data = self.client.get(f"/api/library/{self.pub.pk}/solution?revision=origin").json()
        self.assertIsNone(data["ai_answer"])
        self.assertTrue(data["ai_answer_stale"])
        self.assertEqual(data["origin"]["answer"], "原卷 B")
        self.assertEqual(LibrarySolution.objects.count(), 0)

    def save(self, pub=None, **changes):
        pub = pub or self.pub
        return library_solutions.save(pub, {"answer": "C", "analysis": "第一段\n\n第二段", "figures": [],
                                            "base_revision": None, "sync_library": False, **changes})

    def asset(self, pub=None, color="red"):
        return library_solutions._store_image(pub or self.pub, Image.new("RGB", (240, 120), color))

    def post(self, path, payload):
        return self.client.post(path, json.dumps(payload), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def payload(self, pubs=None, solutions=None, *, mode="combined", layout="inline", output="docx"):
        pubs, solutions = pubs or [self.pub], solutions or {}
        rendered = {}
        for pub in pubs:
            selected, _ai, _row = library_export._solution_selection(pub, False, solutions.get(str(pub.pk)))
            fields = {name: field(str(pub.content.get(name) or "")) for name in ("stem", "origin")}
            fields.update({"options." + k: field(v) for k, v in pub.content["options"].items()})
            fields.update({name: field(str(selected.get(name) or "")) for name in ("answer", "analysis")})
            rendered[str(pub.pk)] = fields
        return {"ids": [str(pub.pk) for pub in pubs], "title": "离线教师卷", "format": output,
                "print_options": {"document": mode, "answer_layout": layout, "answer_space": "medium"},
                "solutions": solutions, "rendered_fields": rendered}

    def test_exam_only_solution_preserves_entire_question_and_original_snapshot(self):
        before = Question.all_objects.filter(pk=self.pub.question_id).values().get()
        content, extras, checksum = deepcopy(self.pub.content), deepcopy(self.pub.extras), self.pub.content_hash
        row = self.save()
        self.pub.refresh_from_db()
        self.assertEqual((self.pub.content, self.pub.extras, self.pub.content_hash), (content, extras, checksum))
        self.assertEqual(Question.all_objects.filter(pk=self.pub.question_id).values().get(), before)
        self.assertEqual(row.answer, "C")
        self.assertIsNone(library_solutions.selected(self.pub))

    def test_sync_conflict_preserves_both_versions_and_original(self):
        first = self.save(sync_library=True)
        self.pub.refresh_from_db()
        second = self.save(base_revision=str(first.pk), sync_library=True, answer="D")
        with self.assertRaisesRegex(library_solutions.SolutionError, "其他窗口"):
            self.save(base_revision=str(first.pk), sync_library=True, answer="错误迟到的覆盖")
        self.assertEqual(LibrarySolution.objects.count(), 2)
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.extras["solution_id"], str(second.pk))
        self.assertEqual(self.pub.content["answer"], "原卷 B")
        self.assertEqual(library_solutions.selected(self.pub, str(first.pk)).answer, "C")

    def test_get_null_and_saved_revision_are_explicit_and_cannot_cross_questions(self):
        url = f"/api/library/{self.pub.pk}/solution"
        body = self.client.get(url).json()
        self.assertIsNone(body["solution"])
        self.assertIsNone(body["base_revision"])
        self.assertEqual(body["origin"]["answer"], "原卷 B")
        row = self.save()
        self.assertEqual(self.client.get(url + "?revision=" + str(row.pk)).json()["solution"]["answer"], "C")
        other = self.publication()
        self.assertEqual(self.client.get(f"/api/library/{other.pk}/solution?revision={row.pk}").status_code, 409)
        with self.assertRaises(library_solutions.SolutionError):
            self.save(pub=other, base_revision=str(row.pk))

    def test_invalid_assets_and_values_never_create_a_solution(self):
        foreign = self.asset(self.publication())
        cases = [{"figures": [foreign["id"]]}, {"figures": [{"id": "../../private"}]},
                 {"figures": [{"id": self.asset()["id"], "display_width": float("nan")}]},
                 {"figures": [{"id": self.asset()["id"], "position": "remote"}]},
                 {"answer": "", "analysis": "", "figures": []}, {"sync_library": "true"}]
        for change in cases:
            with self.subTest(change=change), self.assertRaises(library_solutions.SolutionError):
                self.save(**change)
        self.assertEqual(LibrarySolution.objects.count(), 0)

    def test_upload_normalizes_real_pixels_and_checks_checksum(self):
        output = io.BytesIO()
        Image.new("RGB", (80, 40), "blue").save(output, format="JPEG")
        response = self.client.post(f"/api/library/{self.pub.pk}/solution-images",
            {"image": SimpleUploadedFile("图.jpg", output.getvalue(), content_type="image/jpeg")}, HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        figure = response.json()["figure"]
        view = self.client.get(figure["url"])
        self.assertEqual(view["Content-Type"], "image/png")
        with Image.open(io.BytesIO(view.content)) as image:
            self.assertEqual(image.size, (80, 40))
        target = self.root / "library-solutions" / str(self.pub.pk) / (figure["id"] + ".png")
        target.write_bytes(b"broken")
        self.assertEqual(self.client.get(figure["url"]).status_code, 409)
        with self.assertRaises(library_solutions.SolutionError):
            self.save(figures=[figure["id"]])

    def test_upload_requires_explicit_local_header_and_real_image(self):
        url = f"/api/library/{self.pub.pk}/solution-images"
        self.assertEqual(self.client.post(url, {"image": SimpleUploadedFile("a.png", b"bad")}).status_code, 403)
        self.assertEqual(self.client.post(url, {"image": SimpleUploadedFile("a.png", b"bad")}, HTTP_X_QB_REQUEST="1").status_code, 409)

    def test_invalid_surrogate_and_width_do_not_save_unexportable_content(self):
        for changes in ({"answer": "\ud800"}, {"analysis": "😀" * 60000},
                        {"figures": [{"id": self.asset()["id"], "display_width": 4}]}):
            with self.subTest(changes=list(changes)), self.assertRaises(library_solutions.SolutionError):
                self.save(**changes)
        self.assertEqual(LibrarySolution.objects.count(), 0)
        row = self.save(figures=[{"id": self.asset()["id"], "display_width": 5}])
        self.assertEqual(row.figures[0]["display_width"], 5)

    def test_broken_old_image_does_not_block_current_solution_or_prose_history(self):
        figure = self.asset()
        first = self.save(figures=[figure["id"]], sync_library=True)
        self.pub.refresh_from_db()
        healthy = self.save(answer="后来的好解析", base_revision=str(first.pk), sync_library=True)
        (self.root / "library-solutions" / str(self.pub.pk) / (figure["id"] + ".json")).unlink()
        response = self.client.get(f"/api/library/{self.pub.pk}/solution")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["solution"]["id"], str(healthy.pk))
        damaged = next(row for row in response.json()["history"] if row["id"] == str(first.pk))
        self.assertTrue(damaged["asset_error"])
        self.assertEqual(damaged["analysis"], "第一段\n\n第二段")

    def test_public_listing_uses_metadata_without_decoding_all_solution_images(self):
        self.save(figures=[self.asset()["id"]], sync_library=True)
        self.pub.refresh_from_db()
        with mock.patch.object(library_solutions.Image, "open", side_effect=AssertionError("listing must not decode full image")):
            data = library.publication_json(self.pub)
        self.assertEqual(data["solution"]["answer"], "C")
        self.assertTrue(data["solution"]["figures"][0]["url"])

    def test_source_crop_preserves_original_and_validates_page_bbox(self):
        source = Path(self.pub.paper.source_path)
        original = source.read_bytes()
        url = f"/api/library/{self.pub.pk}/solution-images"
        response = self.post(url, {"page_idx": 0, "bbox": [200, 200, 700, 700]})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()["figure"]["width"], response.json()["figure"]["height"]), (150, 200))
        self.assertEqual(source.read_bytes(), original)
        for data in ({"page_idx": 99, "bbox": [0, 0, 500, 500]}, {"page_idx": 0, "bbox": [0, 0, 1001, 500]},
                     {"page_idx": True, "bbox": [0, 0, 500, 500]}, {"page_idx": 0, "bbox": [0, 0, 0, 500]}):
            self.assertEqual(self.post(url, data).status_code, 400)

    def test_drafts_store_fixed_solutions_and_old_layout_stays_appendix(self):
        row = self.save()
        saved = library_drafts._save({"ids": [str(self.pub.pk)], "solutions": {str(self.pub.pk): str(row.pk)}})
        self.assertEqual(saved["print_options"]["answer_layout"], "inline")
        self.assertEqual(library_drafts._read()["drafts"][0]["solutions"], {str(self.pub.pk): str(row.pk)})
        raw = json.loads(library_drafts.draft_path().read_text(encoding="utf-8"))
        del raw["drafts"][0]["print_options"]["answer_layout"]
        del raw["drafts"][0]["solutions"]
        library_drafts.draft_path().write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(library_drafts._read()["drafts"][0]["print_options"]["answer_layout"], "appendix")
        self.assertEqual(library_drafts._read()["drafts"][0]["solutions"], {str(self.pub.pk): "origin"})
        updated = library_drafts._save({"ids": []}, saved["id"])
        self.assertEqual(updated["solutions"], {})

    def test_legacy_origin_draft_cannot_pick_up_a_later_library_solution(self):
        saved = library_drafts._save({"ids": [str(self.pub.pk)], "print_options": {"answer_layout": "appendix"}})
        raw = json.loads(library_drafts.draft_path().read_text(encoding="utf-8"))
        del raw["drafts"][0]["solutions"]
        del raw["drafts"][0]["print_options"]["answer_layout"]
        library_drafts.draft_path().write_text(json.dumps(raw), encoding="utf-8")
        self.save(sync_library=True, answer="后补的 C", analysis="后来修改的过程")
        self.pub.refresh_from_db()
        old = library_drafts._read()["drafts"][0]
        self.assertEqual(old["print_options"]["answer_layout"], "appendix")
        self.assertEqual(old["solutions"], {str(self.pub.pk): "origin"})
        payload = self.payload(solutions=old["solutions"], layout=old["print_options"]["answer_layout"])
        data, *_ = library_export.export(payload)
        text = "".join(document_xml(data).xpath("//w:t/text()", namespaces=NS))
        self.assertIn("原卷 B", text)
        self.assertNotIn("后补的 C", text)
        with mock.patch.object(library_pdf, "_render", return_value=(b"offline", 1)) as render:
            library_pdf.export({**payload, "format": "pdf"})
        body = json.loads(re.search(r'<script[^>]+id="examData"[^>]*>(.*?)</script>', render.call_args.args[0], re.S)[1])
        self.assertEqual(body["items"][0]["selected"]["answer"], "原卷 B")
        origin = self.client.get(f"/api/library/{self.pub.pk}/solution?revision=origin").json()
        self.assertIsNone(origin["solution"])
        self.assertEqual(origin["base_revision"], self.pub.extras["solution_id"])

    def test_export_uses_fixed_solution_even_after_syncing_new_revision(self):
        old = self.save(sync_library=True)
        self.pub.refresh_from_db()
        payload = self.payload(solutions={str(self.pub.pk): str(old.pk)})
        self.save(base_revision=str(old.pk), sync_library=True, answer="后来的 D")
        data, _name, _mime, _count = library_export.export(payload)
        text = "".join(document_xml(data).xpath("//w:t/text()", namespaces=NS))
        self.assertIn("C", text)
        self.assertNotIn("后来的 D", text)
        self.assertNotIn("原卷 B", text)

    def test_inline_teacher_has_answers_between_questions_without_appendix_or_space(self):
        second = self.publication(stem="合成题乙", answer="第二题答案", qtype="free_response")
        row = self.save()
        data, *_ = library_export.export(self.payload([self.pub, second], {str(self.pub.pk): str(row.pk)}))
        root = document_xml(data)
        text = "".join(root.xpath("//w:t/text()", namespaces=NS))
        self.assertLess(text.index("第一段"), text.index("合成题乙"))
        self.assertNotIn("参考答案与解析", text)
        self.assertEqual(root.xpath("count(//w:br[@w:type='page'])", namespaces=NS), 0)
        self.assertNotIn("1701", root.xpath("//w:spacing/@w:after", namespaces=NS))
        data, *_ = library_export.export(self.payload([self.pub, second], {str(self.pub.pk): str(row.pk)}, layout="appendix"))
        text = "".join(document_xml(data).xpath("//w:t/text()", namespaces=NS))
        self.assertGreater(text.index("第一段"), text.index("合成题乙"))
        self.assertIn("参考答案与解析", text)

    def test_image_only_solution_is_valid_and_student_never_contains_solution_image(self):
        image = self.asset()
        row = self.save(answer="", analysis="", figures=[{"id": image["id"], "display_width": 40}])
        versions = {str(self.pub.pk): str(row.pk)}
        for mode in ("combined", "answers", "questions"):
            data, *_ = library_export.export(self.payload(solutions=versions, mode=mode))
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                media = [name for name in archive.namelist() if name.startswith("word/media/")]
            self.assertEqual(len(media), 0 if mode == "questions" else 1)
            if mode != "questions":
                self.assertNotIn("原卷未提供答案", "".join(document_xml(data).xpath("//w:t/text()", namespaces=NS)))

    def test_analysis_images_keep_order_width_and_paragraph_placement(self):
        figures = [self.asset(color=color) for color in ("red", "green", "blue")]
        row = self.save(figures=[{"id": figures[0]["id"], "display_width": 35, "position": "before"},
                                {"id": figures[1]["id"], "display_width": 45, "position": "paragraph", "paragraph": 0},
                                {"id": figures[2]["id"], "display_width": 55, "position": "after"}])
        data, *_ = library_export.export(self.payload(solutions={str(self.pub.pk): str(row.pk)}))
        root = document_xml(data)
        rows = []
        for child in root.find("w:body", namespaces=NS):
            text = "".join(child.xpath(".//w:t/text()", namespaces=NS))
            rows.append("image" if child.xpath(".//a:blip", namespaces=NS) else text)
        first = rows.index("image")
        self.assertIn("第一段", rows[first + 1])
        self.assertEqual(rows[first + 2], "image")
        self.assertIn("第二段", rows[first + 3])
        self.assertEqual(rows[first + 4], "image")

    def test_pdf_input_uses_same_solution_images_and_student_excludes_them(self):
        row = self.save(figures=[{"id": self.asset()["id"], "display_width": 43, "position": "paragraph", "paragraph": 0}])
        for mode in ("combined", "answers", "questions"):
            with mock.patch.object(library_pdf, "_render", return_value=(b"offline", 1)) as render:
                library_pdf.export(self.payload(solutions={str(self.pub.pk): str(row.pk)}, mode=mode, output="pdf"))
            html = render.call_args.args[0]
            body = json.loads(re.search(r'<script[^>]+id="examData"[^>]*>(.*?)</script>', html, re.S)[1])
            item = body["items"][0]
            self.assertEqual(len(item["solution_images"]), 0 if mode == "questions" else 1)
            if mode != "questions":
                self.assertEqual(item["selected"]["answer"], "C")
                self.assertEqual(item["solution_images"][0]["display_width"], 43)
                self.assertTrue(item["solution_images"][0]["file"].startswith("data:image/png;base64,"))
                self.assertNotIn("http", item["solution_images"][0]["file"])

    def test_forged_rendered_solution_text_and_changed_figure_reject_whole_download(self):
        figure = self.asset()
        row = self.save(figures=[figure["id"]])
        payload = self.payload(solutions={str(self.pub.pk): str(row.pk)})
        payload["rendered_fields"][str(self.pub.pk)]["answer"] = field("未保存的篡改")
        with self.assertRaises(library_export.ExportError):
            library_export.export(payload)
        payload = self.payload(solutions={str(self.pub.pk): str(row.pk)})
        target = self.root / "library-solutions" / str(self.pub.pk) / (figure["id"] + ".png")
        with mock.patch.object(library_export, "_document", side_effect=lambda *args: (target.write_bytes(b"tampered"), b"no-download")[1]):
            with self.assertRaisesRegex(library_export.ExportError, "校验"):
                library_export.export(payload)

    def test_stale_solution_is_not_applied_after_math_changes(self):
        row = self.save(sync_library=True)
        self.pub.refresh_from_db()
        self.pub.content["stem"] = "完全改变题面"
        self.pub.save(update_fields=["content"])
        with self.assertRaisesRegex(library_solutions.SolutionError, "题面"):
            library_solutions.selected(self.pub, str(row.pk))
        body = library.publication_json(self.pub)
        self.assertTrue(body["solution_needs_review"])
        self.assertIsNone(body["solution"])

    def test_scoped_assistant_keeps_global_switch_off_and_original_extras_unchanged(self):
        self.assertFalse(features.enabled("ai_answer"))
        response = self.post("/api/library/jobs", {"kind": "answer", "ids": [str(self.pub.pk)], "solution_scope": True})
        self.assertEqual(response.status_code, 200, response.content)
        job = LibraryJob.objects.get()
        self.assertTrue(job.solution_scope)
        self.assertFalse(features.enabled("ai_answer"))
        before = deepcopy(self.pub.extras)
        prepared = library_assistant.prepare({"publication_id": str(self.pub.pk), "kinds": ["answer"], "agent": "离线测试助手"})
        task = prepared["jobs"][0]
        result = library_assistant.complete({"job_id": task["id"], "fingerprint": task["fingerprint"], "agent": task["agent"],
                                             "answer": "2", "analysis": "逐步计算"})
        self.assertEqual(result["job"]["result"]["analysis"], "逐步计算")
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.extras, before)
        self.assertEqual(self.pub.content["answer"], "原卷 B")
        self.assertFalse(features.enabled("ai_answer"))
        body = self.client.get(f"/api/library/jobs?ids={self.pub.pk}&solution_scope=true").json()
        self.assertEqual(body["jobs"][0]["status"], "done")
        self.assertEqual(body["jobs"][0]["result"]["answer"], "2")

    def test_scoped_active_dedup_failed_retry_and_no_global_missing_batch(self):
        request = {"kind": "answer", "ids": [str(self.pub.pk)], "solution_scope": True}
        self.assertEqual(self.post("/api/library/jobs", request).status_code, 200)
        self.assertEqual(self.post("/api/library/jobs", request).status_code, 200)
        self.assertEqual(LibraryJob.objects.count(), 1)
        LibraryJob.objects.update(status="failed", error="离线失败")
        self.assertEqual(self.post("/api/library/jobs", request).status_code, 200)
        self.assertEqual(LibraryJob.objects.count(), 2)
        self.assertEqual(self.post("/api/library/jobs", {"kind": "answer", "missing": True, "solution_scope": True}).status_code, 400)
        self.assertEqual(self.post("/api/library/jobs", {"kind": "tags", "ids": [str(self.pub.pk)], "solution_scope": True}).status_code, 400)
        self.assertEqual(self.post("/api/library/jobs", {"kind": "answer", "ids": [str(self.pub.pk)]}).status_code, 200)
        self.assertFalse(features.enabled("ai_answer"))

    def test_scoped_api_result_is_isolated_and_late_withdrawal_still_blocks_it(self):
        job = LibraryJob.objects.create(publication=self.pub, kind="answer", executor="api", solution_scope=True,
            fingerprint=library.generation_fingerprint(self.pub.content, self.pub.pk), api_snapshot={"synthetic": True})
        with mock.patch.object(library_jobs.library_ai_settings, "ensure_ready", return_value={"ready": True}), \
                mock.patch.object(library_jobs.library_ai_settings, "require_snapshot"), \
                mock.patch.object(library_jobs, "run_answer", return_value={"answer": "建议", "analysis": "建议过程"}):
            self.assertEqual(library_jobs.process_pending(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, "done")
        self.assertEqual(job.result["analysis"], "建议过程")
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.extras, {})
        other = LibraryJob.objects.create(publication=self.pub, kind="answer", executor="api", solution_scope=True,
            fingerprint=job.fingerprint, api_snapshot={"synthetic": True})
        def delayed(_publication, **_kwargs):
            PublishedQuestion.objects.filter(pk=self.pub.pk).update(status="withdrawn")
            return {"answer": "迟到结果", "analysis": "不应保存"}
        with mock.patch.object(library_jobs.library_ai_settings, "ensure_ready", return_value={"ready": True}), \
                mock.patch.object(library_jobs.library_ai_settings, "require_snapshot"), mock.patch.object(library_jobs, "run_answer", side_effect=delayed):
            library_jobs.process_pending()
        other.refresh_from_db()
        self.assertEqual(other.status, "failed")
        self.assertEqual(other.result, {})


def create_export_fixture(folder):
    """Explicit synthetic-only fixture for UI/PDF inspection on a private QA DB.

    Caller must set QB_DATABASE and QB_DATA_ROOT to its temporary QA directory.
    Never call against the installed user's database. Returns the exact export
    source/MathML contract and four saved immutable synthetic publications.
    """
    from django.conf import settings
    folder = Path(folder).resolve()
    actual = Path(os.environ.get("LOCALAPPDATA", "")) / "QuestionBankCard"
    if (not os.environ.get("QB_DATABASE") or not os.environ.get("QB_DATA_ROOT")
            or folder != Path(settings.DATA_ROOT).resolve() or folder == actual.resolve()
            or not Path(os.environ["QB_DATABASE"]).resolve().is_relative_to(folder.parent)):
        raise ValueError("Fixture requires an explicit isolated QA data/database directory")
    folder.mkdir(parents=True, exist_ok=True)
    helper = LibrarySolutionTests()
    helper.root = folder
    first = helper.publication(stem="$x+1$", answer="原卷 B")
    helper.pub = first
    figures = []
    target = folder / "library" / str(first.pk)
    target.mkdir(parents=True, exist_ok=True)
    for index, slot in enumerate("ABCD", 1):
        name = f"figure-{index}.png"
        Image.new("RGB", (240, 120), (index * 40, 20, 240 - index * 30)).save(target / name)
        figures.append({"slot": slot, "file": name, "page_idx": 0, "bbox": [50, 50, 500, 500]})
    first.content["figures"] = figures
    first.content["options"] = {key: str(index) for index, key in enumerate("ABCD", 1)}
    first.content_hash = library.content_hash(first.content)
    first.save(update_fields=["content", "content_hash"])
    edited = helper.save(analysis="由条件逐步计算。\n\n第二步：代入数值得到结论。",
        figures=[{"id": helper.asset()["id"], "display_width": 45, "position": "paragraph", "paragraph": 0}])
    long = helper.publication(stem="第二题长解析跨页测试", answer="2", qtype="free_response")
    long_solution = helper.save(pub=long, answer="2", analysis="\n\n".join(f"第 {n} 步：保留这一行详细过程，验证长解析正常跨页。" for n in range(1, 61)))
    image_only = helper.publication(stem="第三题仅图片解析", answer="")
    picture = helper.save(pub=image_only, answer="", analysis="", figures=[helper.asset(image_only, "green")["id"]])
    missing = helper.publication(stem="第四题缺答案解析", answer="")
    pubs = [first, long, image_only, missing]
    versions = {str(item.publication_id): str(item.pk) for item in (edited, long_solution, picture)}
    request = helper.payload(pubs, versions)
    request["rendered_fields"][str(first.pk)]["stem"] = math_field("x+1", "<mrow><mi>x</mi><mo>+</mo><mn>1</mn></mrow>")
    draft = library_drafts._save({"title": "离线教师与学生卷测试", "ids": request["ids"], "solutions": versions,
                                "print_options": request["print_options"]})
    return {"payload": request, "draft_id": draft["id"], "ids": request["ids"], "solutions": versions}
