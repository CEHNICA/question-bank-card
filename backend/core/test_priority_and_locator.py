"""Tests for question locator IDs, prioritized/parallel library tag jobs, and background backfill."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, TransactionTestCase

from . import features, library, library_ai_settings, library_job_control, library_jobs
from .models import LibraryJob, LibraryTag, LibraryTagSuggestion, Paper, PublishedQuestion, Question


class PriorityQueueAndLocatorTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = mock.patch.dict(
            os.environ,
            {
                "QB_FEATURES_FILE": str(self.root / "features.json"),
                "QB_LIBRARY_AI_SETTINGS_FILE": str(self.root / "ai-settings.json"),
                "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.root / "ai-key.dat"),
                "QB_CREDENTIALS_FILE": str(self.root / "credentials.dat"),
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.data_root = self.root / "data"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings_patch = self.settings(DATA_ROOT=self.data_root)
        self.settings_patch.__enter__()
        self.addCleanup(self.settings_patch.__exit__, None, None, None)

        self.paper = Paper.objects.create(filename="mock-math.pdf")
        self.pubs = []
        for idx in range(1, 5):
            q = Question.objects.create(paper=self.paper, number=idx, stem=f"测试题干 {idx}")
            pub = PublishedQuestion.objects.create(
                question=q,
                paper=self.paper,
                source_filename="2026模拟测试卷",
                number=idx,
                question_type="single_choice",
                version=1,
                content={"stem": f"测试题干 {idx}", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}},
                content_hash=f"hash-{idx}",
            )
            self.pubs.append(pub)

        transform = mock.patch.object(
            library_ai_settings.credential_settings.store, "_transform", side_effect=lambda raw, *, protect: raw
        )
        transform.start()
        self.addCleanup(transform.stop)

        self.client = Client(HTTP_X_QB_REQUEST="1", HTTP_REFERER="http://testserver/")
        library_ai_settings.save(
            {
                "mode": "api",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "base_url": "https://api.deepseek.com",
                "supports_images": False,
                "features": {"knowledge_tags": True},
                "key": {"action": "replace", "value": "sk-test-123456789"},
            }
        )
        cfg = library_ai_settings._load()
        cfg.update(verified=True, verified_at="2026-11-22T00:00:00Z")
        library_ai_settings._write_settings(cfg)
        LibraryTag.objects.create(name="集合的运算", kind=LibraryTag.Kind.KNOWLEDGE)

    def test_locator_round_trips_short_id_copy_text_and_api(self):
        pub = self.pubs[0]
        locator = library.question_locator(pub)
        self.assertTrue(locator["short_id"].startswith("Q-"))
        self.assertEqual(len(locator["short_id"]), 10)
        self.assertIn(locator["short_id"], locator["copy_text"])

        for raw in (str(pub.pk), locator["short_id"], locator["copy_text"]):
            with self.subTest(raw=raw):
                self.assertEqual(library.resolve_publication_ref(raw).pk, pub.pk)

        resp = self.client.get("/api/library/locate", {"ref": locator["copy_text"]})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["publication"]["id"], str(pub.pk))
        self.assertEqual(body["publication"]["short_id"], locator["short_id"])
        self.assertEqual(body["locator"]["short_id"], locator["short_id"])

    def test_high_priority_promotes_queued_tag_job_and_reports_queue_ahead(self):
        # 后台低优先级入队第1、2题
        low_resp = self.client.post(
            "/api/library/jobs",
            data=json.dumps(
                {
                    "kind": "tags",
                    "ids": [str(self.pubs[0].pk), str(self.pubs[1].pk)],
                    "api_only": True,
                    "priority": "normal",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(low_resp.status_code, 200)
        self.assertEqual(low_resp.json()["queued"], 2)

        # 单题点击第2题触发高优先级插队（不重复新建，而是原地提升为 priority=10）
        promote_resp = self.client.post(
            "/api/library/jobs",
            data=json.dumps({"kind": "tags", "ids": [str(self.pubs[1].pk)], "api_only": True}),
            content_type="application/json",
        )
        self.assertEqual(promote_resp.status_code, 200)
        promoted_jobs = promote_resp.json()["jobs"]
        self.assertEqual(len(promoted_jobs), 1)
        self.assertEqual(promoted_jobs[0]["priority"], library_jobs.PRIORITY_HIGH)
        self.assertEqual(promoted_jobs[0]["queue_ahead"], 0)
        self.assertEqual(LibraryJob.objects.filter(publication=self.pubs[1], kind="tags").count(), 1)

        # 核对第1题排在后面（queue_ahead == 1）
        q0_status = self.client.get(f"/api/library/jobs?ids={self.pubs[0].pk}&tag_scope=true").json()
        self.assertEqual(q0_status["jobs"][0]["queue_ahead"], 1)

        # Worker 抢占下一条必定先拿到第2题（高优先级）
        claimed = library_jobs.claim_next_job()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.publication_id, self.pubs[1].pk)

    def test_cancel_tags_job_also_marks_suggestion_failed(self):
        resp = self.client.post(
            "/api/library/jobs",
            data=json.dumps({"kind": "tags", "ids": [str(self.pubs[0].pk)], "api_only": True}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        job_id = resp.json()["jobs"][0]["id"]

        cancel_resp = self.client.post(
            "/api/library/jobs/cancel",
            data=json.dumps({"ids": [job_id], "solution_scope": False}),
            content_type="application/json",
        )
        self.assertEqual(cancel_resp.status_code, 200)
        self.assertEqual(cancel_resp.json()["cancelled"], 1)
        suggestion = LibraryTagSuggestion.objects.get(job_id=job_id)
        self.assertEqual(suggestion.status, LibraryTagSuggestion.Status.FAILED)
        self.assertEqual(suggestion.message, library_job_control.CANCEL_MESSAGE)

    def test_background_untagged_enqueue_respects_feature_flag(self):
        self.assertEqual(library_jobs.enqueue_untagged_background(), 0)
        features.save({"auto_tag_background": True})
        queued = library_jobs.enqueue_untagged_background(batch_size=2, max_queued_floor=4)
        self.assertEqual(queued, 2)
        self.assertEqual(LibraryJob.objects.filter(kind="tags", status=LibraryJob.Status.QUEUED, priority=0).count(), 2)


class ParallelJobWorkerTests(TransactionTestCase):
    setUp = PriorityQueueAndLocatorTests.setUp

    def test_process_parallel_executes_without_duplicate_claims(self):
        for pub in self.pubs[:3]:
            library_jobs.enqueue(pub, LibraryJob.Kind.TAGS, api_only=True, tag_scope=True, priority=0)

        seen_ids: list[str] = []
        lock = threading.Lock()

        def fake_proposal(publication, _suggestion):
            time.sleep(0.05)
            with lock:
                seen_ids.append(str(publication.pk))
            return {"status": "no_match", "suggestions": [], "message": "ok"}, "deepseek"

        with mock.patch.object(library_jobs, "run_tag_proposal", side_effect=fake_proposal):
            handled = library_jobs.process_parallel(max_workers=3)

        self.assertEqual(handled, 3)
        self.assertEqual(len(seen_ids), 3)
        self.assertEqual(len(set(seen_ids)), 3)
        self.assertEqual(LibraryJob.objects.filter(status=LibraryJob.Status.DONE).count(), 3)

    def test_default_concurrency_throttles_free_rate_limited_providers(self):
        with mock.patch.object(
            library_jobs.library_ai_settings,
            "public_status",
            return_value={"provider": "nvidia", "keys": {"nvidia": {"configured": True, "count": 1}}},
        ):
            self.assertEqual(library_jobs.default_concurrency(), 1)
        with mock.patch.object(
            library_jobs.library_ai_settings,
            "public_status",
            return_value={"provider": "nvidia", "keys": {"nvidia": {"configured": True, "count": 2}}},
        ):
            self.assertEqual(library_jobs.default_concurrency(), 2)
        with mock.patch.object(
            library_jobs.library_ai_settings,
            "public_status",
            return_value={"provider": "zhipu", "keys": {"zhipu": {"configured": True, "count": 4}}},
        ):
            self.assertEqual(library_jobs.default_concurrency(), 4)
        features.save({"ai_auto_route": True})
        with mock.patch.object(
            library_jobs.library_ai_settings,
            "public_status",
            return_value={
                "provider": "nvidia",
                "keys": {
                    "nvidia": {"configured": True, "count": 1},
                    "zhipu": {"configured": True, "count": 1},
                    "modelscope": {"configured": True, "count": 1},
                },
            },
        ):
            self.assertEqual(library_jobs.default_concurrency(), 3)
        with mock.patch.object(
            library_jobs.library_ai_settings,
            "public_status",
            return_value={"provider": "deepseek", "keys": {"deepseek": {"configured": True, "count": 1}}},
        ):
            self.assertEqual(library_jobs.default_concurrency(), library_jobs.DEFAULT_JOB_CONCURRENCY)
