"""Versioned model preferences and worker task-boundary reloads."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import Client, SimpleTestCase

from . import preferences
from .management.commands import run_worker


class ModelPreferenceTests(SimpleTestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.path = self.temp / "model-preferences.json"
        # The worker loop also checks the library job queue (1.10); these
        # database-free tests must not reach it, or each run logs an error
        # and sleeps five seconds.
        for queue in (run_worker.library_jobs, run_worker.region_reads):
            patcher = mock.patch.object(queue, "pending", return_value=False)
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self):
        return mock.patch.dict(os.environ, {
            "QB_MODEL_PREFERENCES_FILE": str(self.path),
            "QB_MINIMAX_CONFIGURED": "1",
            "QB_SILICONFLOW_CONFIGURED": "1",
        }, clear=True)

    def test_version_one_roles_load_with_legacy_model_defaults(self):
        self.path.write_text(json.dumps({
            "version": 1,
            "roles": {
                "primary_engine": "siliconflow_qwen3",
                "checker_engine": "auto",
                "arbiter_engine": "primary",
            },
        }), encoding="utf-8")
        with self.env():
            loaded = preferences.load_configuration()
        self.assertEqual(loaded["roles"]["primary_engine"], "siliconflow_qwen3")
        self.assertEqual(loaded["models"], preferences.DEFAULT_MODELS)

    def test_worker_keeps_active_paper_stable_and_applies_save_to_next_paper(self):
        old_models = {
            "minimax": "MiniMax-old", "siliconflow": "Qwen/old",
        }
        new_models = {
            "minimax": "MiniMax-new", "siliconflow": "Qwen/new",
        }
        roles = dict(preferences.DEFAULTS)
        with self.env():
            preferences.save_configuration(roles, old_models)

            first = SimpleNamespace(display_name="甲", get_status_display=lambda: "等待")
            second = SimpleNamespace(display_name="乙", get_status_display=lambda: "等待")
            first_query = mock.Mock()
            first_query.exclude.return_value = first_query
            first_query.order_by.return_value = [first, second]
            empty_query = mock.Mock()
            empty_query.exclude.return_value = empty_query
            empty_query.order_by.return_value = []
            observations: list[tuple[str, str]] = []
            active_statuses: list[dict] = []

            def process(paper):
                observations.append((paper.display_name + "-开始", os.environ["QB_MINIMAX_MODEL"]))
                if paper is first:
                    preferences.save_configuration(roles, new_models)
                    observations.append((paper.display_name + "-处理中", os.environ["QB_MINIMAX_MODEL"]))
                    active_statuses.append(Client().get("/api/status").json()["engines"])

            with mock.patch.object(run_worker, "SingleInstance"), \
                    mock.patch.object(run_worker.Paper.objects, "filter", side_effect=[first_query, empty_query]), \
                    mock.patch.object(run_worker, "process_paper", side_effect=process), \
                    mock.patch.object(run_worker, "rereads_pending", return_value=False), \
                    mock.patch.object(run_worker, "process_rereads", return_value=0):
                command = run_worker.Command()
                command.stdout = mock.Mock()
                command.handle(once=True)
            final_status = Client().get("/api/status").json()["engines"]

        self.assertEqual(observations, [
            ("甲-开始", "MiniMax-old"),
            ("甲-处理中", "MiniMax-old"),
            ("乙-开始", "MiniMax-new"),
        ])
        self.assertEqual(active_statuses[0]["models"], {**preferences.DEFAULT_MODELS, **old_models})
        self.assertTrue(active_statuses[0]["pending_change"])
        self.assertEqual(final_status["models"], {**preferences.DEFAULT_MODELS, **new_models})
        self.assertFalse(final_status["pending_change"])

    def test_worker_startup_replaces_stale_snapshot_even_without_queued_work(self):
        roles = dict(preferences.DEFAULTS)
        old = {"minimax": "MiniMax-old", "siliconflow": "Qwen/old"}
        new = {"minimax": "MiniMax-new", "siliconflow": "Qwen/new"}
        with self.env():
            preferences.save_configuration(roles, new)
            preferences.save_applied_configuration({"roles": roles, "models": old})
            empty_query = mock.Mock()
            empty_query.exclude.return_value = empty_query
            empty_query.order_by.return_value = []
            with mock.patch.object(run_worker, "SingleInstance"), \
                    mock.patch.object(run_worker, "clean_saved_example_labels"), \
                    mock.patch.object(run_worker.library_jobs, "recover_interrupted"), \
                    mock.patch.object(run_worker.region_reads, "recover_interrupted"), \
                    mock.patch.object(run_worker.library_jobs, "pending", return_value=False), \
                    mock.patch.object(run_worker.region_reads, "pending", return_value=False), \
                    mock.patch.object(run_worker.Paper.objects, "filter", return_value=empty_query), \
                    mock.patch.object(run_worker, "rereads_pending", return_value=False), \
                    mock.patch.object(run_worker, "process_paper"), \
                    mock.patch.object(run_worker, "process_rereads"):
                command = run_worker.Command()
                command.stdout = mock.Mock()
                command.handle(once=True)
            status = Client().get("/api/status").json()["engines"]
            applied = preferences.load_applied_configuration()

        self.assertEqual(applied["models"], {**preferences.DEFAULT_MODELS, **new})
        self.assertEqual(status["models"], {**preferences.DEFAULT_MODELS, **new})
        self.assertFalse(status["pending_change"])

    def test_failed_snapshot_write_restores_old_applied_not_new_startup_environment(self):
        old = {
            "roles": dict(preferences.DEFAULTS),
            "models": {"minimax": "MiniMax-old", "siliconflow": "Qwen/old"},
        }
        new = {
            "roles": {
                "primary_engine": "siliconflow_qwen3",
                "checker_engine": "minimax_m3",
                "arbiter_engine": "checker",
            },
            "models": {"minimax": "MiniMax-new", "siliconflow": "Qwen/new"},
        }
        with self.env():
            preferences.save_configuration(new["roles"], new["models"])
            preferences.save_applied_configuration(old)
            # Simulate a restarted process whose launcher environment already
            # contains the newly saved values while the durable applied state
            # still correctly describes the last task.
            preferences.apply_to_environment(new)
            with mock.patch.object(
                preferences, "save_applied_configuration",
                side_effect=preferences.PreferenceError("disk full"),
            ), self.assertRaises(preferences.PreferenceError):
                preferences.apply_and_record(new)

            restored = {
                "roles": {
                    "primary_engine": os.environ["QB_PRIMARY_ENGINE"],
                    "checker_engine": os.environ["QB_CHECKER_ENGINE"],
                    "arbiter_engine": os.environ["QB_ARBITER_ENGINE"],
                },
                "models": {
                    "minimax": os.environ["QB_MINIMAX_MODEL"],
                    "siliconflow": os.environ["QB_SILICONFLOW_MODEL"],
                },
            }
            durable = preferences.load_applied_configuration()

        self.assertEqual(restored, old)
        self.assertEqual(durable, {**old, "models": {**preferences.DEFAULT_MODELS, **old["models"]},
                                   "plans": preferences.DEFAULT_PLANS})
