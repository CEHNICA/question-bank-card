"""Offline library editing: immutable history, exact locks and source assets."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw

from . import library, library_question_editor as editor, library_solutions
from .models import LibraryJob, LibrarySolution, Paper, PublishedQuestion, Question


class LibraryQuestionEditorTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
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
        (self.root / "features.json").write_text(json.dumps({"ai_answer": False, "knowledge_tags": False}), encoding="utf-8")
        transport = mock.patch("requests.sessions.Session.request", side_effect=AssertionError("offline tests cannot call a cloud API"))
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        self.factory = RequestFactory()
        self.paper, self.question, self.pub = self.fixture()

    def fixture(self, *, figures=False, image_body=False):
        source = self.root / ("source-" + str(Paper.objects.count()) + ".png")
        image = Image.new("RGB", (300, 400), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((10, 30, 95, 125), fill="red")
        draw.rectangle((130, 130, 245, 220), fill="blue")
        image.save(source)
        paper = Paper.objects.create(filename="离线编辑原卷.png", kind="image", source_path=str(source),
            sha256=hashlib.sha256(source.read_bytes()).hexdigest(), status=Paper.Status.READY,
            pages=[{"page_idx": 0, "width": 300, "height": 400}, {"page_idx": 1, "width": 300, "height": 400}])
        folder = self.root / str(paper.pk) / "pages"
        folder.mkdir(parents=True)
        image.save(folder / "page_0.png")
        image.save(folder / "page_1.png")
        boxes = [{"slot": "stem", "page_idx": 0, "bbox": [30, 75, 320, 315], "source": "manual",
                  "label_offset": {"x": 4, "y": 5}, "parts": [{"page_idx": 1, "bbox": [30, 75, 320, 315]}]},
                 {"slot": "B", "page_idx": 0, "bbox": [430, 325, 820, 550], "source": "manual"}] if figures else []
        question = Question.objects.create(paper=paper, number=1, question_type="single_choice",
            stem="已知 $x^2=4$，则 $x$ 的值是（ ）" if not image_body else "", options={"A": "1", "B": "2", "C": "$\\pm2$", "D": "4"},
            answer="原卷 B", analysis="原卷保留的解析 $x=2$", origin="2025原卷",
            regions=[{"page_idx": 0, "bbox": [0, 0, 900, 650]}], state=Question.State.GREEN,
            body_mode="source_image" if image_body else "text", figures=boxes,
            read_a={"stem": "AI 原始读法", "original": True}, read_b={"stem": "第二次原始读法"},
            figure_review={"status": "ok" if figures else "confirmed_no_figure", "source": "human",
                           "reason": "已由人工确认", "confirmed_at": "2026-01-01T00:00:00+00:00"})
        library.approve(question, now=timezone.now())
        question.save()
        publication, created = library.publish(question)
        self.assertTrue(created)
        return paper, question, publication

    def payload(self, pub=None, **changes):
        pub = pub or self.pub
        data = editor.editor_data(pub)
        return {key: data[key] for key in ("revision", "content_hash", "stem", "options", "question_type", "body_mode")} | changes

    def request(self, pub=None, *, payload=None, headers=True):
        pub = pub or self.pub
        request = self.factory.post("/unused", json.dumps(payload or self.payload(pub)), content_type="application/json",
                                    **({"HTTP_X_QB_REQUEST": "1"} if headers else {}))
        return editor.question_editor(request, pub.pk)

    def unchanged(self, question=None, pub=None):
        question, pub = question or self.question, pub or self.pub
        return Question.all_objects.filter(pk=question.pk).values().get(), PublishedQuestion.objects.filter(pk=pub.pk).values().get()

    def test_get_returns_only_snapshot_fields_with_exact_source_lock(self):
        response = editor.question_editor(self.factory.get("/unused"), self.pub.pk)
        data = json.loads(response.content)
        self.assertTrue(data["editable"])
        self.assertEqual(data["revision"], 0)
        self.assertEqual(data["content_hash"], self.pub.content_hash)
        self.assertEqual(data["stem"], self.pub.content["stem"])
        self.assertEqual(data["publication"]["id"], str(self.pub.pk))

    def test_human_save_publishes_new_snapshot_and_preserves_original_readings(self):
        original_content, original_extras = deepcopy(self.pub.content), deepcopy(self.pub.extras)
        original_source = Path(self.paper.source_path).read_bytes()
        response = self.request(payload=self.payload(stem="计算 $x^2=9$。", options={"A": "3", "B": "$\\pm3$"}, question_type="multiple_choice"))
        self.assertEqual(response.status_code, 201, response.content)
        data = json.loads(response.content)
        updated = PublishedQuestion.objects.get(pk=data["publication"]["id"])
        self.assertTrue(data["created"])
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.content["stem"], "计算 $x^2=9$。")
        self.assertEqual(updated.content["question_type"], "multiple_choice")
        self.assertEqual(updated.review_source, "human")
        self.assertEqual(updated.content["answer"], "原卷 B")
        self.assertEqual(updated.content["analysis"], original_content["analysis"])
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.status, PublishedQuestion.Status.SUPERSEDED)
        self.assertEqual((self.pub.content, self.pub.extras), (original_content, original_extras))
        self.question.refresh_from_db()
        self.assertEqual(self.question.read_a, {"stem": "AI 原始读法", "original": True})
        self.assertEqual(self.question.read_b, {"stem": "第二次原始读法"})
        self.assertEqual(self.question.regions, [{"page_idx": 0, "bbox": [0, 0, 900, 650]}])
        self.assertEqual(updated.content["sources"], original_content["sources"])
        self.assertTrue(library.approval_is_current(self.question))
        self.assertEqual(self.question.content_revision, 1)
        self.assertTrue(self.question.type_locked)
        self.assertEqual(self.question.text_source, "human")
        self.assertEqual(Path(self.paper.source_path).read_bytes(), original_source)
        self.assertFalse(LibraryJob.objects.exists())
        self.transport.assert_not_called()

    def test_noop_keeps_snapshot_version_but_invalidates_late_read_revision(self):
        updated, created, revision = editor.save(self.pub, self.payload())
        self.assertEqual(updated.pk, self.pub.pk)
        self.assertFalse(created)
        self.assertEqual(revision, 1)
        self.assertEqual(PublishedQuestion.objects.count(), 1)
        with self.assertRaises(editor.EditorError) as caught:
            editor.save(self.pub, self.payload() | {"revision": 0})
        self.assertEqual(caught.exception.status, 409)

    def test_two_windows_and_superseded_version_cannot_overwrite_newer_body(self):
        stale = self.payload()
        updated, _, _ = editor.save(self.pub, stale | {"stem": "窗口一的新题干"})
        with self.assertRaises(editor.EditorError) as caught:
            editor.save(self.pub, stale | {"stem": "窗口二的旧题干"})
        self.assertEqual(caught.exception.status, 409)
        self.assertFalse(editor.editor_data(PublishedQuestion.objects.get(pk=self.pub.pk))["editable"])
        self.question.refresh_from_db()
        self.assertEqual(self.question.stem, "窗口一的新题干")
        self.assertEqual(PublishedQuestion.objects.count(), 2)
        self.assertEqual(editor.editor_data(updated)["revision"], 1)

    def test_unpublished_source_changes_are_blocked_even_when_client_reads_new_revision(self):
        self.question.stem = "审核页保存但未入库的内容"
        self.question.content_revision += 1
        self.question.approved = False
        self.question.save()
        before = self.unchanged()
        data = editor.editor_data(self.pub)
        self.assertFalse(data["editable"])
        self.assertIn("尚未入库", data["reason"])
        response = self.request(payload=self.payload(stem="旧题库窗口覆盖"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unchanged(), before)

    def test_pending_ocr_and_unapproved_identical_source_do_not_allow_edit(self):
        for flag in ("reread_requested", "ocr_pending", "approved"):
            with self.subTest(flag=flag):
                Question.objects.filter(pk=self.question.pk).update(**{flag: flag != "approved"})
                response = self.request()
                self.assertEqual(response.status_code, 409)
                Question.objects.filter(pk=self.question.pk).update(**{flag: flag == "approved"})
        for state in (Question.State.WAITING, Question.State.READING, Question.State.RED):
            Question.objects.filter(pk=self.question.pk).update(state=state)
            self.assertEqual(self.request().status_code, 409)
        self.assertEqual(PublishedQuestion.objects.count(), 1)

    def test_same_body_but_changed_source_revision_requires_fresh_window(self):
        stale = self.payload()
        Question.objects.filter(pk=self.question.pk).update(content_revision=5)
        response = self.request(payload=stale | {"stem": "迟到的输入"})
        self.assertEqual(response.status_code, 409)
        self.question.refresh_from_db()
        self.assertEqual(self.question.content_revision, 5)
        self.assertEqual(self.question.stem, self.pub.content["stem"])

    def test_rename_after_opening_is_caught_by_content_hash(self):
        stale = self.payload()
        library.rename_paper(self.paper, "新的试卷名称")
        response = self.request(payload=stale | {"stem": "旧窗口输入"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PublishedQuestion.objects.count(), 1)

    def test_reorder_move_and_delete_figures_preserves_original_geometry_and_bytes(self):
        paper, question, pub = self.fixture(figures=True)
        originals = deepcopy(pub.content)
        source_figures = deepcopy(question.figures)
        folder = self.root / "library" / str(pub.pk)
        old_bytes = [(folder / item["file"]).read_bytes() for item in pub.content["figures"]]
        edited, _, _ = editor.save(pub, self.payload(pub, figures=[
            {"file": "figure-2.png", "slot": "A"}, {"file": "figure-1.png", "slot": "D"},
        ]))
        question.refresh_from_db()
        self.assertEqual(question.figures, [{**source_figures[1], "slot": "A"}, {**source_figures[0], "slot": "D"}])
        new_folder = self.root / "library" / str(edited.pk)
        self.assertEqual((new_folder / "figure-1.png").read_bytes(), old_bytes[1])
        self.assertEqual((new_folder / "figure-2.png").read_bytes(), old_bytes[0])
        pub.refresh_from_db()
        self.assertEqual(pub.content, originals)
        self.assertEqual([(folder / item["file"]).read_bytes() for item in originals["figures"]], old_bytes)
        emptied, _, _ = editor.save(edited, self.payload(edited, figures=[]))
        self.assertEqual(emptied.content["figures"], [])
        self.assertEqual(emptied.content["review"]["figure_review"]["status"], "confirmed_no_figure")

    def test_external_duplicate_path_and_malformed_asset_roles_are_rejected(self):
        _, question, pub = self.fixture(figures=True)
        before = self.unchanged(question, pub)
        invalid = [[{"file": "../source.png", "slot": "stem"}],
            [{"file": "figure-1.png", "slot": "stem"}, {"file": "figure-1.png", "slot": "A"}],
            [{"file": "figure-1.png", "slot": []}], [{"file": "figure-1.png", "slot": "F"}],
            [{"file": "figure-1.png", "slot": "stem", "bbox": [0, 0, 2, 2]}],
            [{"file": "figure-999.png", "slot": "stem"}], None]
        for figures in invalid:
            with self.subTest(figures=figures):
                response = self.request(pub, payload=self.payload(pub, figures=figures))
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(self.unchanged(question, pub), before)

    def test_changed_or_missing_retained_asset_does_not_save_source_card(self):
        _, question, pub = self.fixture(figures=True)
        before = self.unchanged(question, pub)
        asset = self.root / "library" / str(pub.pk) / "figure-1.png"
        original = asset.read_bytes()
        asset.write_bytes(b"tampered snapshot")
        response = self.request(pub, payload=self.payload(pub, stem="不应写入"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unchanged(question, pub), before)
        self.assertNotIn(str(self.root), response.content.decode())
        asset.write_bytes(original)
        library.figure_file(question, 0).write_bytes(b"tampered cached crop")
        self.assertEqual(self.request(pub).status_code, 409)
        self.assertEqual(self.unchanged(question, pub), before)
        asset.unlink()
        self.assertEqual(self.request(pub).status_code, 409)

    def test_publish_failure_rolls_back_question_and_leaves_original_folder(self):
        _, question, pub = self.fixture(figures=True)
        before = self.unchanged(question, pub)
        folders = {path.name for path in (self.root / "library").iterdir()}
        with mock.patch("core.library.shutil.copyfile", side_effect=OSError("private sensitive path")):
            response = self.request(pub, payload=self.payload(pub, stem="不能部分保存"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unchanged(question, pub), before)
        self.assertEqual({path.name for path in (self.root / "library").iterdir()}, folders)
        self.assertNotIn("sensitive", response.content.decode())

    def test_unsafe_crop_cache_directory_is_checked_before_any_crop(self):
        _, question, pub = self.fixture(figures=True)
        before = self.unchanged(question, pub)
        target = self.root / str(question.paper_id) / "figures"
        original_check = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", autospec=True,
                               side_effect=lambda path: path == target or original_check(path)), \
                mock.patch("core.library.figure_file", side_effect=AssertionError("do not create or access an unsafe crop")):
            response = self.request(pub)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unchanged(question, pub), before)

    def test_changed_math_invalidates_generated_extras_and_flags_solution_without_moving_it(self):
        fingerprint = library.generation_fingerprint(self.pub.content, self.pub.pk)
        solution = library_solutions.save(self.pub, {"answer": "C", "analysis": "独立人工解析", "figures": [],
                                                    "base_revision": None, "sync_library": True})
        self.pub.refresh_from_db()
        extras = {**self.pub.extras, "tags": ["函数"], "tags_fingerprint": fingerprint,
                  "ai_answer": {"answer": "D", "analysis": "旧模型解析", "fingerprint": fingerprint}}
        library.save_extras(self.pub, extras)
        old = deepcopy(self.pub.content)
        edited, _, _ = editor.save(self.pub, self.payload(stem="完全不同的题 $x+1=0$。"))
        self.assertNotIn("tags", edited.extras)
        self.assertNotIn("ai_answer", edited.extras)
        self.assertNotIn("solution_id", edited.extras)
        self.assertEqual(edited.extras["solution_needs_review_id"], str(solution.pk))
        self.pub.refresh_from_db()
        self.assertEqual(self.pub.content, old)
        self.assertEqual(self.pub.extras, extras)
        self.assertEqual(library_solutions.selected(self.pub).pk, solution.pk)
        self.assertEqual(LibrarySolution.objects.count(), 1)

    def test_image_body_type_edit_keeps_original_crop_and_explicit_text_conversion(self):
        _, question, pub = self.fixture(image_body=True)
        original_images = deepcopy(pub.content["question_images"])
        original_bytes = (self.root / "library" / str(pub.pk) / "question-1.png").read_bytes()
        edited, _, _ = editor.save(pub, self.payload(pub, question_type="free_response"))
        self.assertEqual(edited.content["body_mode"], "source_image")
        self.assertEqual(edited.content["question_images"][0]["image_sha256"], original_images[0]["image_sha256"])
        self.assertEqual((self.root / "library" / str(edited.pk) / "question-1.png").read_bytes(), original_bytes)
        converted, _, _ = editor.save(edited, self.payload(edited, stem="人工输入完整题干", body_mode="text"))
        self.assertNotIn("body_mode", converted.content)
        self.assertEqual(converted.content["stem"], "人工输入完整题干")
        self.assertEqual((self.root / "library" / str(pub.pk) / "question-1.png").read_bytes(), original_bytes)

    def test_image_body_ignores_old_hidden_ocr_figures_and_keeps_complete_crop(self):
        _, question, pub = self.fixture(image_body=True)
        # These stale OCR figure boxes never formed part of the published body.
        question.figures = [{"slot": "stem", "page_idx": 0, "bbox": [10, 10, 80, 80], "source": "auto"}]
        question.save()
        self.assertTrue(library.approval_is_current(question))
        edited, _, _ = editor.save(pub, self.payload(pub, question_type="multiple_choice"))
        self.assertEqual(edited.content["figures"], [])
        self.assertEqual(edited.content["body_mode"], "source_image")
        self.assertEqual(edited.content["question_images"][0]["image_sha256"], pub.content["question_images"][0]["image_sha256"])

    def test_withdrawn_deleted_and_disconnected_sources_are_readonly(self):
        base = self.payload()
        self.question.deleted_at = timezone.now()
        self.question.save()
        self.assertFalse(editor.editor_data(self.pub)["editable"])
        self.assertEqual(self.request(payload=base).status_code, 409)
        self.question.deleted_at = None
        self.question.save()
        library.withdraw(self.pub)
        self.assertEqual(self.request(payload=base).status_code, 409)
        self.pub.question = None
        self.pub.save()
        self.assertFalse(editor.editor_data(self.pub)["editable"])
        self.assertEqual(self.request(payload=base).status_code, 409)

    def test_payload_validation_and_local_guard_do_not_mutate_snapshot(self):
        before = self.unchanged()
        invalid = [{"revision": True}, {"revision": -1}, {"content_hash": "fake"}, {"stem": ""},
            {"stem": "x" * 20001}, {"stem": "\x00bad"}, {"stem": "\ud800"}, {"options": {"F": "1"}},
            {"options": {"A": 1}}, {"options": {"A": "x" * 4001}}, {"question_type": "unknown"},
            {"question_type": []}, {"body_mode": []}, {"answer": "偷偷改答案"}, {"by": "ai"},
            {"regions": []}, {"extras": {"tags": ["偷偷改标签"]}}]
        for changes in invalid:
            with self.subTest(changes=str(changes)[:80]):
                response = self.request(payload=self.payload() | changes)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(self.unchanged(), before)
        self.assertEqual(self.request(headers=False).status_code, 403)
        request = self.factory.post("/unused", "{}", content_type="text/plain", HTTP_X_QB_REQUEST="1")
        self.assertEqual(editor.question_editor(request, self.pub.pk).status_code, 415)
        self.assertEqual(editor.question_editor(self.factory.delete("/unused"), self.pub.pk).status_code, 405)
