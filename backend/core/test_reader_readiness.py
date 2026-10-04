"""One readiness answer for the reading entry points and the reading worker.

An automatic import that fell back to manual keeps ``auto_fallback``, and the
worker then keeps that scope: a service that already answered may not be swapped
for another when its call fails.  The page used to ask the question without that
context, answer “可以读”, and the same failure came back from the worker as
“没有配置所选主读模型的 API Key” — written to two fields and therefore shown
twice.  These tests pin the one answer, the single place the sentence is stored,
the entry that takes the teacher to the setting, and the one thing the scope is
still allowed to stop: moving a request that was already under way.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image

from . import intake, pipeline, provider_catalog, readers, views
from . import test_manual_intake_review as manual_review
from .models import Question


CONFIGURED = readers.Engine("modelscope", "isolated-ready")
MISSING = "minimax_m3"
MISSING_PROVIDER = readers.ENGINE_CHOICES[MISSING]
MISSING_LABEL = provider_catalog.VISION[MISSING_PROVIDER]["label"]
NOT_CONFIGURED = {
    "ready": False, "reason": "none_configured", "selected": MISSING,
    "label": MISSING_LABEL, "engine": None, "used": "", "fallback_available": False,
}


class IsolatedData:
    def isolate(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        setting = override_settings(DATA_ROOT=self.root)
        setting.enable()
        self.addCleanup(setting.disable)
        system_environment = {
            key: value for key, value in os.environ.items()
            if key.casefold() in {"path", "systemroot", "windir", "temp", "tmp", "tmpdir"}
        }
        environment = mock.patch.dict(os.environ, {
            **system_environment,
            "QB_FEATURES_FILE": str(self.root / "features.json"),
            "QB_MODEL_PREFERENCES_FILE": str(self.root / "models.json"),
        }, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        network = mock.patch("requests.sessions.Session.request",
            side_effect=AssertionError("unexpected real cloud request"))
        network.start()
        self.addCleanup(network.stop)


class PrimaryReadinessTests(IsolatedData, SimpleTestCase):
    """``primary_engine`` and the page now ask the same question."""

    def test_selected_service_with_a_key_is_ready_and_is_the_one_reported(self):
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=CONFIGURED):
            readiness = readers.primary_readiness()
            engine = readers.primary_engine()
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["selected"], MISSING)
        self.assertEqual(readiness["label"], CONFIGURED.label)
        self.assertIs(engine, CONFIGURED)

    def test_missing_key_falls_back_when_the_round_allows_it_and_says_so(self):
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "_first_configured", return_value=CONFIGURED) as fallback:
            readiness = readers.primary_readiness()
        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["fallback_available"])
        # The teacher still has to know which service their choice named.
        self.assertEqual((readiness["selected"], readiness["label"]), (MISSING, MISSING_LABEL))
        fallback.assert_called_once()

    def test_selected_only_round_uses_a_configured_service_but_says_which(self):
        # The scope stops a *failing request* from moving to another service.
        # A service that never had a key is not a failing request, so a
        # selected-only round still reads — and the answer names the service
        # that will read, so the switch is never the silent kind.
        with readers.selected_services_only(), \
                mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "_first_configured", return_value=CONFIGURED) as fallback:
            readiness = readers.primary_readiness()
            engine = readers.primary_engine()
        self.assertTrue(readiness["ready"])
        self.assertEqual((readiness["selected"], readiness["label"]), (MISSING, MISSING_LABEL))
        self.assertEqual(readiness["used"], CONFIGURED.provider)
        self.assertTrue(readiness["fallback_available"])
        self.assertIs(engine, CONFIGURED)
        fallback.assert_called()

    def test_selected_only_round_with_nothing_configured_still_refuses(self):
        with readers.selected_services_only(), \
                mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "_first_configured", return_value=None):
            readiness = readers.primary_readiness()
        self.assertEqual((readiness["ready"], readiness["reason"]), (False, "none_configured"))
        self.assertEqual(readiness["label"], MISSING_LABEL)
        self.assertEqual(readiness["used"], "")
        self.assertFalse(readiness["fallback_available"])

    def test_auto_scope_still_does_not_switch_a_service_that_is_answering(self):
        # The other half of the rule, so narrowing the credential case did not
        # also allow swapping a running reader: _fallback_enabled() is what the
        # request path consults, and the scope still turns it off.
        with mock.patch.dict("os.environ", {"QB_PROVIDER_FALLBACK": "1"}), \
                readers.selected_services_only():
            self.assertFalse(readers._fallback_enabled())

    def test_the_service_the_teacher_chose_is_not_reported_as_a_fallback(self):
        # “auto” resolves to a real provider before it reaches here, so the
        # only way to be ready without a fallback is: the chosen service is the
        # one that reads.  Saying otherwise would tell the teacher their
        # reading changed hands when it did not.
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=CONFIGURED), \
                mock.patch.object(readers, "_first_configured") as alternative:
            readiness = readers.primary_readiness()
        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["used"], CONFIGURED.provider)
        self.assertFalse(readiness["fallback_available"])
        alternative.assert_not_called()

    def test_nothing_configured_and_assistant_mode_are_told_apart(self):
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "_first_configured", return_value=None):
            nothing = readers.primary_readiness()
        self.assertEqual((nothing["ready"], nothing["reason"]), (False, "none_configured"))
        with mock.patch.object(readers, "_primary_selection", return_value=readers.ASSISTANT):
            assistant = readers.primary_readiness()
        self.assertEqual((assistant["ready"], assistant["reason"]), (False, "assistant_mode"))
        self.assertEqual(assistant["label"], "AI 助手读题")


class ReadCardConfigurationTests(IsolatedData, SimpleTestCase):
    def setUp(self):
        self.isolate()
        self.snapshot = {"id": 1, "number": 1, "question_type": "free_response",
            "regions": [{"page_idx": 0, "bbox": [20, 30, 800, 400]}], "candidates": []}

    def run_card(self):
        primary = mock.patch.object(readers, "primary_engine", return_value=None)
        readiness = mock.patch.object(readers, "primary_readiness", return_value=dict(NOT_CONFIGURED))
        checker = mock.patch.object(readers, "checker_engine", return_value=None)
        chat = mock.patch.object(readers, "chat")
        stack = mock.patch.object(pipeline.imaging, "stack_regions", return_value=(Image.new("RGB", (120, 100), "white"), []))
        with primary, readiness, checker, chat as model, stack:
            return pipeline.read_card(self.snapshot, mock.Mock()), model

    def test_missing_key_is_a_named_configuration_state_and_sends_no_request(self):
        result, model = self.run_card()
        self.assertEqual(result["state"], Question.State.RED)
        self.assertEqual(result["error_kind"], "reader_not_configured")
        self.assertEqual(result["error_service"], MISSING)
        self.assertEqual(result["error_service_label"], MISSING_LABEL)
        self.assertTrue(result["error_recoverable"])
        # Reaching here means nothing at all is configured, so the sentence says
        # that instead of blaming the one service they picked and promising not
        # to use any other.
        self.assertIn("没有配置任何看图读题服务", result["error"])
        self.assertIn(MISSING_LABEL, result["error"])
        model.assert_not_called()

    def test_a_real_service_failure_is_not_disguised_as_a_missing_key(self):
        with mock.patch.object(readers, "primary_engine", return_value=CONFIGURED), \
                mock.patch.object(readers, "checker_engine", return_value=None), \
                mock.patch.object(readers, "chat", side_effect=readers.ReaderError("mock outage")), \
                mock.patch.object(pipeline.imaging, "stack_regions",
                    return_value=(Image.new("RGB", (120, 100), "white"), [])):
            result = pipeline.read_card(self.snapshot, mock.Mock())
        self.assertNotIn("error_kind", result)
        self.assertEqual(result["error"], "mock outage")


class StatusReportsReadinessTests(IsolatedData, TestCase):
    def setUp(self):
        self.isolate()

    def test_status_exposes_the_reason_without_any_secret(self):
        with mock.patch.object(views.readers, "primary_readiness",
            return_value=dict(NOT_CONFIGURED)) as readiness:
            body = self.client.get("/api/status").json()
        readiness.assert_called()
        reported = body["reader_readiness"]
        self.assertEqual(set(reported), {"ready", "reason", "selected", "label", "used", "fallback_available"})
        self.assertEqual((reported["ready"], reported["reason"]), (False, "none_configured"))
        self.assertEqual(reported["label"], MISSING_LABEL)
        # Which service will really read travels with the reason, so the button
        # can say “这次用 魔搭 读” instead of implying the chosen one is used.
        self.assertEqual(reported["used"], "")
        self.assertFalse(reported["fallback_available"])
        # Only the reason travels; the engine object and any credential stay put.
        self.assertNotIn("engine", json.dumps(reported, ensure_ascii=False))

    def test_status_names_the_service_that_will_read_when_it_is_not_the_one_chosen(self):
        with mock.patch.object(views.readers, "primary_readiness", return_value={
            "ready": True, "reason": "", "selected": MISSING, "label": MISSING_LABEL,
            "engine": None, "used": "modelscope", "fallback_available": True}):
            reported = self.client.get("/api/status").json()["reader_readiness"]
        self.assertTrue(reported["ready"])
        self.assertEqual(reported["used"], "modelscope")
        self.assertTrue(reported["fallback_available"])


class RereadEntryAsksTheSameQuestionTests(IsolatedData, TestCase):
    paper = manual_review.ManualIntakeReviewTests.paper

    def setUp(self):
        self.isolate()
        self.original = self.paper()
        self.original.processing_plan = {**self.original.processing_plan, "auto_fallback": True}
        self.original.save()
        self.question = Question.objects.create(paper=self.original, number=1, body_mode="source_image",
            processing_mode="manual", question_type="free_response", state="yellow",
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}])

    def post(self):
        return self.client.post(f"/api/questions/{self.question.pk}/reread", json.dumps({"revision": self.question.content_revision}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_auto_fallback_paper_reads_with_the_configured_service_instead_of_refusing(self):
        # The teacher asked for this paper to be read and another service does
        # have a key.  409 here is what produced “可以读” on one screen and
        # “没有配置所选主读模型的 API Key” on the next; the read is queued and the
        # entry point says which service will really do it.
        before = deepcopy(Question.objects.values().get(pk=self.question.pk))
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "configured", return_value=False), \
                mock.patch.object(readers, "_first_configured", return_value=CONFIGURED) as fallback:
            result = self.post()
        self.assertEqual(result.status_code, 200, result.content)
        self.assertNotIn("API 配置", result.json().get("error", ""))
        fallback.assert_called_once()
        after = Question.objects.values().get(pk=self.question.pk)
        self.assertNotEqual(after["reread_requested"], before["reread_requested"])
        self.assertTrue(after["ocr_pending"])

    def test_auto_fallback_paper_with_no_service_at_all_refuses_and_names_what_is_missing(self):
        before = deepcopy(Question.objects.values().get(pk=self.question.pk))
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "configured", return_value=False), \
                mock.patch.object(readers, "_first_configured", return_value=None):
            result = self.post()
        self.assertEqual(result.status_code, 409, result.content)
        error = result.json()["error"]
        self.assertIn(MISSING_LABEL, error)
        self.assertIn("API 配置", error)
        after = Question.objects.values().get(pk=self.question.pk)
        self.assertEqual(after["reread_requested"], before["reread_requested"])
        self.assertEqual(after["ocr_pending"], before["ocr_pending"])

    def test_same_paper_without_auto_fallback_is_allowed_to_fall_back(self):
        self.original.processing_plan = {**self.original.processing_plan, "auto_fallback": False}
        self.original.save(update_fields=["processing_plan"])
        self.question.refresh_from_db()
        with mock.patch.object(readers, "_primary_selection", return_value=MISSING), \
                mock.patch.object(readers, "engine_by_key", return_value=None), \
                mock.patch.object(readers, "configured", return_value=False), \
                mock.patch.object(readers, "_first_configured", return_value=CONFIGURED):
            result = self.post()
        self.assertEqual(result.status_code, 200, result.content)
        self.question.refresh_from_db()
        self.assertTrue(self.question.reread_requested and self.question.ocr_pending)


class ManualSwitchClearsTheStaleSentenceTests(IsolatedData, TestCase):
    paper = manual_review.ManualIntakeReviewTests.paper

    def setUp(self):
        self.isolate()
        self.original = self.paper()

    def test_a_card_no_longer_waiting_for_a_read_does_not_keep_saying_it_could_not_read(self):
        queued = Question.objects.create(paper=self.original, number=1, body_mode="source_image",
            processing_mode="manual", question_type="free_response", state="reading", ocr_pending=True,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}],
            error="所选主读模型“MiniMax”还没有 API Key，无法读题。",
            ocr_suggestion={"revision": 0, "error": "所选主读模型“MiniMax”还没有 API Key，无法读题。",
                "error_kind": "reader_not_configured", "error_service": MISSING,
                "error_service_label": MISSING_LABEL, "state": Question.State.RED})
        finished = Question.objects.create(paper=self.original, number=2, body_mode="source_image",
            processing_mode="manual", question_type="free_response", state="yellow",
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 420]}],
            error="原卷文件或裁片已变化，本轮识读未写入，请重新确认原卷范围。")
        intake.select_manual(self.original, [0])
        queued.refresh_from_db()
        finished.refresh_from_db()
        # The record stays, so 识读记录 and the 原图审核 flow still have the reason.
        self.assertEqual(queued.ocr_suggestion["error_kind"], "reader_not_configured")
        self.assertEqual(queued.error, "")
        self.assertEqual((queued.state, queued.body_mode), (Question.State.YELLOW, "source_image"))
        # Nothing was queued, so a past failure is left alone.
        self.assertIn("原卷文件或裁片已变化", finished.error)
