"""Optional second AI reading; isolated fixtures and no real cloud requests."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from . import features, library, library_jobs, pipeline, readers, views, test_manual_intake_review as manual_review
from .models import Question
from .tests import tagged


PRIMARY = readers.Engine("minimax", "isolated-primary")
CHECKER = readers.Engine("siliconflow", "isolated-checker")
STEM = "已知函数 $f(x)=x^2$，求 $f(2)$ 的值。"


class IsolatedData:
    def isolate(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        setting = override_settings(DATA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        environment = mock.patch.dict(os.environ, {
            "QB_FEATURES_FILE": str(self.root / "features.json"),
            "QB_MODEL_PREFERENCES_FILE": str(self.root / "models.json"),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        network = mock.patch("requests.sessions.Session.request",
            side_effect=AssertionError("unexpected real cloud request"))
        network.start()
        self.addCleanup(network.stop)


class DoubleReadSettingsTests(IsolatedData, TestCase):
    def setUp(self):
        self.isolate()

    def test_old_or_missing_settings_keep_second_read_on(self):
        self.assertTrue(features.enabled("double_read"))
        features.path().write_text('{"origin_split": false}', encoding="utf-8")
        self.assertTrue(features.enabled("double_read"))
        self.assertFalse(features.enabled("origin_split"))
        response = self.client.get("/api/settings/features")
        setting = next(item for item in response.json()["features"] if item["key"] == "double_read")
        self.assertTrue(setting["default"] and setting["enabled"])

    def test_api_can_disable_and_restore_without_changing_other_switches(self):
        for enabled in (False, True):
            response = self.client.post("/api/settings/features",
                json.dumps({"features": {"double_read": enabled}}),
                content_type="application/json", HTTP_X_QB_REQUEST="1")
            self.assertEqual(response.status_code, 200, response.content)
            setting = next(item for item in response.json()["features"] if item["key"] == "double_read")
            self.assertEqual(setting["enabled"], enabled)
            self.assertTrue(features.enabled("origin_split"))
            self.assertFalse(features.enabled("knowledge_tags"))


class SingleCardTests(IsolatedData, SimpleTestCase):
    def setUp(self):
        self.isolate()
        self.snapshot = {"id": 1, "number": 1, "question_type": "free_response",
            "regions": [{"page_idx": 0, "bbox": [20, 30, 800, 400]}], "candidates": []}
        for name, result in (("primary_engine", PRIMARY), ("checker_engine", CHECKER)):
            patcher = mock.patch.object(readers, name, return_value=result)
            engine = patcher.start()
            setattr(self, name, engine)
            self.addCleanup(patcher.stop)
        crop = mock.patch.object(pipeline.imaging, "stack_regions",
            return_value=(Image.new("RGB", (120, 100), "white"), []))
        crop.start()
        self.addCleanup(crop.stop)

    def run_card(self, *, raw=None, error=None):
        with mock.patch.object(readers, "chat", return_value=raw or tagged(STEM), side_effect=error) as chat, \
                mock.patch.object(readers, "answered_by", side_effect=lambda engine: engine), \
                mock.patch.object(readers, "arbitrate") as arbiter, \
                mock.patch.object(readers, "spot_check") as spot, \
                mock.patch.object(readers, "classify_figures") as classify:
            result = pipeline.read_card(self.snapshot, store=mock.Mock())
        return result, chat, arbiter, spot, classify

    def test_disabled_reads_once_and_never_compares_or_claims_witness_agreement(self):
        features.save({"double_read": False})
        for witness in (STEM, "已知函数 $f(x)=x^3$，求 $f(2)$ 的值。"):
            self.snapshot["witness"] = witness
            result, chat, arbiter, spot, classify = self.run_card()
            self.assertEqual(chat.call_count, 1)
            self.assertEqual((result["text_source"], result["state"]), ("single", "green"))
            self.assertEqual(result["read_b"], {"skipped": "disabled"})
            self.assertEqual(result["read_c"], {})
            self.assertEqual(result["flags"], [])
            arbiter.assert_not_called()
            spot.assert_not_called()
            classify.assert_not_called()
        self.checker_engine.assert_not_called()

    def test_disabled_failure_does_not_try_checker_or_add_a_missing_checker_error(self):
        features.save({"double_read": False})
        result, chat, arbiter, spot, _ = self.run_card(error=readers.ReaderError("mock primary failure"))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual((result["state"], result["error"]), ("red", "mock primary failure"))
        self.assertEqual(result["read_b"], {"skipped": "disabled"})
        self.assertEqual(result["read_c"], {})
        self.assertEqual(result["flags"], [])
        self.checker_engine.assert_not_called()
        arbiter.assert_not_called()
        spot.assert_not_called()

    def test_missing_primary_does_not_fake_a_successful_single_read(self):
        features.save({"double_read": False})
        self.primary_engine.return_value = None
        result, chat, *_ = self.run_card()
        self.assertEqual(result["state"], "red")
        self.assertIn("主读模型", result["error"])
        chat.assert_not_called()

    def test_old_default_still_reads_twice_and_reports_real_agreement(self):
        result, chat, arbiter, spot, _ = self.run_card()
        self.assertEqual(chat.call_count, 2)
        self.assertEqual((result["text_source"], result["state"]), ("agree", "green"))
        self.assertEqual(result["read_b"]["engine"], CHECKER.label)
        arbiter.assert_not_called()
        spot.assert_not_called()

    def test_single_read_retains_first_read_figure_assignment(self):
        features.save({"double_read": False})
        self.snapshot["candidates"] = [{"label": "1", "seq": 1, "page_idx": 0, "bbox": [50, 80, 100, 120]}]
        result, chat, *_ = self.run_card(raw=tagged("如图，求三角形面积。", figures="1=题干"))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(result["figures"][0]["slot"], "stem")

    def test_unjudged_figure_stays_flagged_without_an_extra_ai_call(self):
        features.save({"double_read": False})
        self.snapshot["candidates"] = [{"label": "1", "seq": 1, "page_idx": 0, "bbox": [50, 80, 100, 120]}]
        candidates = deepcopy(self.snapshot["candidates"])
        result, chat, _, _, classify = self.run_card()
        self.assertEqual(chat.call_count, 1)
        classify.assert_not_called()
        self.assertEqual(result["state"], "yellow")
        self.assertEqual(result["figure_review"]["unclassified_count"], 1)
        self.assertEqual(self.snapshot["candidates"], candidates)

    def test_single_read_keeps_local_missing_option_and_unclear_checks(self):
        features.save({"double_read": False})
        result, chat, *_ = self.run_card(raw=tagged("计算 $[?]+1$ 的值。", options={"A": "1", "C": "3", "D": "4"}))
        self.assertEqual(chat.call_count, 1)
        self.assertEqual(result["state"], "yellow")
        self.assertTrue(any("选项 B" in flag for flag in result["flags"]))
        self.assertTrue(any("看不清" in flag for flag in result["flags"]))
        self.assertFalse(any("另一次" in flag or "两次" in flag for flag in result["flags"]))

    def test_task_snapshot_wins_over_a_later_setting_change(self):
        features.save({"double_read": True})
        self.snapshot["double_read"] = False
        result, chat, *_ = self.run_card()
        self.assertEqual((chat.call_count, result["text_source"]), (1, "single"))
        features.save({"double_read": False})
        self.snapshot["double_read"] = True
        result, chat, *_ = self.run_card()
        self.assertEqual((chat.call_count, result["text_source"]), (2, "agree"))


class SingleReadingWorkflowTests(IsolatedData, TestCase):
    paper = manual_review.ManualIntakeReviewTests.paper

    def setUp(self):
        self.isolate()
        self.original = self.paper()
        features.save({"double_read": False})
        for target, name, value in ((readers, "primary_engine", PRIMARY), (readers, "checker_engine", CHECKER),
                (readers, "assistant_mode", False), (pipeline, "_reader_parallelism", 1),
                (views, "_vision_ready", True), (views, "_reading_ready", True)):
            patcher = mock.patch.object(target, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("ThreadPoolExecutor", manual_review.ImmediateExecutor), ("close_old_connections", mock.Mock())):
            patcher = mock.patch.object(pipeline, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def question(self, number=1, **fields):
        return Question.objects.create(paper=self.original, number=number, question_type="free_response",
            regions=[{"page_idx": 0, "bbox": [40, 40, 850, 450]}], **fields)

    def post(self, path, payload=None):
        return self.client.post(path, json.dumps(payload or {}), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_first_batch_then_explicit_reread_each_use_one_call_and_require_adoption(self):
        question = self.question(body_mode="source_image", processing_mode="manual", state="yellow")
        regions, key = deepcopy(question.regions), question.source_key
        result = self.post(f"/api/papers/{self.original.pk}/read-cut-questions")
        self.assertEqual(result.status_code, 200, result.content)
        with mock.patch.object(readers, "chat", return_value=tagged(STEM)) as chat:
            self.assertEqual(pipeline.process_rereads(), 1)
        self.assertEqual(chat.call_count, 1)
        question.refresh_from_db()
        self.assertEqual((question.body_mode, question.stem, question.regions, question.source_key),
            ("source_image", "", regions, key))
        self.assertEqual(question.ocr_suggestion["text_source"], "single")
        self.assertFalse(question.approved or question.ocr_pending or question.reread_requested)
        result = self.post(f"/api/questions/{question.pk}/reread", {"revision": question.content_revision})
        self.assertEqual(result.status_code, 200, result.content)
        with mock.patch.object(readers, "chat", return_value=tagged(STEM)) as chat:
            self.assertEqual(pipeline.process_rereads(), 1)
        self.assertEqual(chat.call_count, 1)
        question.refresh_from_db()
        result = self.post(f"/api/questions/{question.pk}/apply-reading", {"revision": question.content_revision})
        self.assertEqual(result.status_code, 200, result.content)
        question.refresh_from_db()
        # Explicit adoption remains a human content gesture; the saved reading
        # evidence still records that the second AI pass was disabled.
        self.assertEqual((question.body_mode, question.text_source), ("text", "human"))
        self.assertEqual(question.read_b, {"skipped": "disabled"})
        self.assertFalse(question.approved)
        with self.assertRaisesRegex(ValueError, "还没有通过终审"):
            library.publish(question)

    def test_single_failure_clears_pending_but_never_loops_or_changes_original_body(self):
        question = self.question(body_mode="source_image", processing_mode="manual", state="yellow")
        self.post(f"/api/papers/{self.original.pk}/read-cut-questions")
        with mock.patch.object(readers, "chat", side_effect=readers.ReaderError("mock failure")) as chat:
            self.assertEqual(pipeline.process_rereads(), 1)
            self.assertEqual(pipeline.process_rereads(), 0)
        self.assertEqual(chat.call_count, 1)
        question.refresh_from_db()
        self.assertFalse(question.ocr_pending or question.reread_requested or question.approved)
        self.assertEqual((question.body_mode, question.state, question.stem), ("source_image", "yellow", ""))
        self.assertEqual(question.ocr_suggestion["error"], "mock failure")
        self.assertEqual(question.read_b, {"skipped": "disabled"})

    def test_text_card_and_its_reread_use_single_read_without_automatic_approval(self):
        question = self.question()
        with mock.patch.object(readers, "chat", return_value=tagged(STEM)) as chat:
            pipeline.read_questions(self.original, [question])
        self.assertEqual(chat.call_count, 1)
        question.refresh_from_db()
        self.assertEqual((question.text_source, question.state), ("single", "green"))
        self.assertFalse(question.approved)
        result = self.post(f"/api/questions/{question.pk}/reread")
        self.assertEqual(result.status_code, 200, result.content)
        with mock.patch.object(readers, "chat", return_value=tagged(STEM)) as chat:
            self.assertEqual(pipeline.process_rereads(), 1)
        self.assertEqual(chat.call_count, 1)
        question.refresh_from_db()
        self.assertFalse(question.approved)

    def test_feature_change_cannot_rewrite_existing_approval_or_publication(self):
        question = self.question(body_mode="source_image", processing_mode="manual", state="yellow")
        library.approve(question, now=timezone.now())
        question.save()
        with mock.patch.object(library_jobs, "queue_on_intake"):
            publication, _ = library.publish(question)
        before = deepcopy(Question.objects.values().get(pk=question.pk))
        published = deepcopy(publication.content)
        for enabled in (True, False):
            features.save({"double_read": enabled})
        self.assertEqual(Question.objects.values().get(pk=question.pk), before)
        publication.refresh_from_db()
        self.assertEqual(publication.content, published)

    def test_feature_change_during_batch_is_applied_only_to_the_next_batch(self):
        first, second = self.question(), self.question(2)
        def answer(*args, **kwargs):
            features.save({"double_read": True})
            return tagged(STEM)
        with mock.patch.object(readers, "chat", side_effect=answer) as chat:
            pipeline.read_questions(self.original, [first, second])
        self.assertEqual(chat.call_count, 2)
        for question in (first, second):
            question.refresh_from_db()
            self.assertEqual(question.text_source, "single")
        with mock.patch.object(readers, "chat", return_value=tagged(STEM)) as chat:
            pipeline.read_questions(self.original, [first])
        self.assertEqual(chat.call_count, 2)


class SingleReadRequestPolicyTests(SimpleTestCase):
    def test_single_context_disables_hedge_but_preserves_original_timeout_and_cancel(self):
        response = mock.Mock(status_code=200)
        with readers.cancellable_request(lambda: False), readers.selected_services_only(), \
                readers.without_speculative_duplicates(), \
                mock.patch.object(readers.requests, "post", return_value=response) as post, \
                mock.patch.object(readers, "_chat_once", return_value="one answer") as once, \
                mock.patch.object(readers._HEDGE_EXECUTOR, "submit") as duplicate:
            self.assertIs(readers._post("https://example.invalid", "isolated", {}), response)
            self.assertEqual(post.call_args.kwargs["timeout"], (10, 150))
            self.assertEqual(readers.chat(PRIMARY, "copy", ["image"]), "one answer")
        once.assert_called_once()
        duplicate.assert_not_called()

    def test_single_context_releases_its_policy_and_rejects_cancelled_calls(self):
        with readers.without_speculative_duplicates():
            self.assertFalse(readers._SPECULATIVE_DUPLICATES.get())
            with self.assertRaises(readers.ReaderRequestStopped), readers.cancellable_request(lambda: True):
                readers.chat(PRIMARY, "copy", ["image"])
        self.assertTrue(readers._SPECULATIVE_DUPLICATES.get())
