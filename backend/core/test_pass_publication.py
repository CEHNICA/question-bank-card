"""Passing saves a bank snapshot atomically; old publish stays idempotent."""
from copy import deepcopy
import json
import os
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from . import features, library, library_ai_settings, library_jobs
from .models import LibraryJob, PublishedQuestion
from .test_v110_types_origin import TempDataMixin


class PassPublicationTests(TempDataMixin, TestCase):
    def setUp(self):
        self.use_temp_data()
        environment = mock.patch.dict(os.environ, {
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "ai-settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "ai-key.dat"),
            "QB_FEATURES_FILE": str(self.temp / "features.json"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        network = mock.patch("requests.sessions.Session.request", side_effect=AssertionError("No cloud calls"))
        network.start()
        self.addCleanup(network.stop)
        self.paper = self.make_paper()
        self.question = self.card(self.paper, number=1, question_type="free_response", stem="计算 $1+2$。")

    def post(self, endpoint, payload=None):
        return self.client.post(endpoint, json.dumps(payload or {}), content_type="application/json", HTTP_X_QB_REQUEST="1")

    def approve(self, question=None, **extra):
        question = question or self.question
        return self.post(f"/api/questions/{question.pk}/approve", {"approved": True, **extra})

    def test_pass_saves_immediately_and_old_publish_and_repeated_pass_are_idempotent(self):
        response = self.approve()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["paper"]["counts"]["published"], 1)
        publication = PublishedQuestion.objects.get(question=self.question, version=1)
        snapshot = deepcopy(publication.content)
        self.assertEqual(publication.content["stem"], self.question.stem)
        self.assertEqual(publication.review_source, "human")
        self.assertEqual(self.client.get("/api/library").json()["total"], 1)
        self.assertEqual(self.approve().status_code, 200)
        repeated = self.post(f"/api/papers/{self.paper.pk}/publish").json()
        self.assertEqual((repeated["created"], repeated["unchanged"], repeated["problems"]), (0, 1, []))
        publication.refresh_from_db()
        self.assertEqual(publication.content, snapshot)
        self.assertEqual(self.question.publications.count(), 1)
        self.assertFalse(LibraryJob.objects.exists())

    def test_single_pass_failure_after_snapshot_insert_rolls_back_approval_and_files(self):
        before = {field: deepcopy(getattr(self.question, field)) for field in (
            "approved", "approved_at", "approved_content_hash", "approval_source", "approval_agent", "figure_review", "state")}
        with mock.patch.object(library_jobs, "queue_on_intake", side_effect=OSError("synthetic save failure")):
            response = self.approve(by="ai", agent="离线助手")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("通过未保存", response.json()["error"])
        self.question.refresh_from_db()
        self.assertEqual({field: getattr(self.question, field) for field in before}, before)
        self.assertFalse(PublishedQuestion.objects.exists())
        self.assertFalse(LibraryJob.objects.exists())
        self.assertEqual(list((self.temp / "library").iterdir()), [])
        self.assertEqual(self.approve().status_code, 200)
        self.assertEqual(self.question.publications.get().version, 1)

    def test_failed_human_confirmation_does_not_relabel_an_existing_ai_publication(self):
        self.assertEqual(self.approve(by="ai", agent="离线助手").status_code, 200)
        publication = self.question.publications.get()
        snapshot = deepcopy(publication.content)
        with mock.patch.object(library, "publish", side_effect=ValueError("synthetic confirmation failure")):
            response = self.approve()
        self.assertEqual(response.status_code, 409, response.content)
        self.question.refresh_from_db()
        publication.refresh_from_db()
        self.assertEqual((self.question.approval_source, self.question.approval_agent), ("ai", "离线助手"))
        self.assertEqual((publication.review_source, publication.review_agent), ("ai", "离线助手"))
        self.assertEqual(publication.content, snapshot)
        self.assertEqual(self.question.publications.count(), 1)

    def test_batch_save_failure_keeps_successes_and_failed_card_is_not_passed(self):
        failed = self.card(self.paper, number=2, question_type="free_response", stem="计算 $3+4$。")
        original_queue = library_jobs.queue_on_intake
        def partially_fail(publication):
            if publication.question_id == failed.pk:
                raise OSError("synthetic second-card save failure")
            return original_queue(publication)
        with mock.patch.object(library_jobs, "queue_on_intake", side_effect=partially_fail):
            response = self.post(f"/api/papers/{self.paper.pk}/approve-green", {"by": "ai", "agent": "离线助手"})
        self.assertEqual(response.status_code, 200, response.content)
        result = response.json()
        self.assertEqual(result["approved"], 1)
        self.assertEqual(len(result["problems"]), 1)
        self.assertIn("第 2 题", result["problems"][0])
        self.assertEqual((result["paper"]["counts"]["approved"], result["paper"]["counts"]["published"]), (1, 1))
        failed.refresh_from_db()
        self.assertEqual((failed.approved, failed.approved_at, failed.approved_content_hash,
                          failed.approval_source, failed.approval_agent), (False, None, "", "", ""))
        self.assertEqual((failed.state, failed.stem), ("green", "计算 $3+4$。"))
        self.assertFalse(failed.publications.exists())
        saved = self.question.publications.get()
        self.assertEqual(saved.review_source, "ai")
        self.assertEqual(len(list((self.temp / "library").iterdir())), 1)
        retry = self.post(f"/api/papers/{self.paper.pk}/approve-green", {"by": "ai", "agent": "离线助手"}).json()
        self.assertEqual((retry["approved"], retry["problems"]), (1, []))
        self.assertEqual(failed.publications.get().version, 1)
        self.assertEqual(self.question.publications.get().pk, saved.pk)
        self.assertEqual(PublishedQuestion.objects.count(), 2)

    def test_upgrade_publish_explicitly_disables_enrichment_without_touching_global_preferences(self):
        features.save({"ai_answer": True, "knowledge_tags": True})
        library_ai_settings.save({"mode": "assistant", "on_intake": {"tags": True, "answer": True}})
        settings = {path: path.read_bytes() for path in (self.temp / "features.json", self.temp / "ai-settings.json")}
        library.approve(self.question, now=timezone.now())
        self.question.save()
        with mock.patch.object(library_jobs, "queue_on_intake", side_effect=AssertionError("Upgrade must not create generation work")) as queue:
            publication, created = library.publish(self.question, queue_enrichment=False)
        queue.assert_not_called()
        self.assertTrue(created)
        self.assertEqual(publication.version, 1)
        self.assertFalse(LibraryJob.objects.exists())
        self.assertEqual({path: path.read_bytes() for path in settings}, settings)
