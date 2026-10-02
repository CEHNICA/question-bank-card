"""The assistant CLI/MCP handoff uses only the local app, with synthetic data."""

from __future__ import annotations

import base64
import json
import os
import tempfile
import urllib.parse
from copy import deepcopy
from pathlib import Path
from unittest import mock

from django.test import LiveServerTestCase, SimpleTestCase, override_settings
from django.utils import timezone

from . import features, library, library_ai_settings
from .test_v110_types_origin import TempDataMixin
from .test_tiyouju_cli import cli, run_json


PUBLICATION = "12345678-1234-5678-1234-567812345678"
OTHER = "22345678-1234-5678-1234-567812345678"
JOB = "32345678-1234-5678-1234-567812345678"
FINGERPRINT = "a" * 64
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6S8AAAAASUVORK5CYII=")


def prepared():
    return {
        "publication": {"id": PUBLICATION, "version": 2, "content": {"stem": "如图，求 $x$。"}},
        "jobs": [{"id": JOB, "kind": "answer", "fingerprint": FINGERPRINT,
                  "executor": "assistant", "status": "queued", "prompt": "看图并自己解题"}],
        "images": {"crop": f"/api/library/{PUBLICATION}/crop",
                   "figures": [{"index": 0, "slot": "stem", "url": f"/api/library/{PUBLICATION}/figures/figure-1.png"}],
                   "originals": [{"page_idx": 0, "url": f"/api/library/{PUBLICATION}/pages/0"}]},
        "knowledge": {"points": [{"point": "一元一次方程", "chapter": "方程"}]},
    }


class EnrichmentCliProtocolTests(SimpleTestCase):
    def setUp(self):
        self.local = mock.Mock(spec=cli.Client)
        self.local.url = "http://127.0.0.1:8790"
        self.local.agent = "当前豆包"
        self.local.get_bytes.return_value = PNG

    def command(self, *args):
        with mock.patch.object(cli, "Client", return_value=self.local):
            return run_json(*args)

    def test_feature_inspection_never_changes_switches(self):
        self.local.get.return_value = {"features": [{"key": "ai_answer", "enabled": False}]}
        code, result = self.command("features")
        self.assertEqual(code, 0)
        self.assertFalse(result["features"][0]["enabled"])
        self.local.get.assert_called_once_with("/api/settings/features")
        self.local.post.assert_not_called()


    def test_only_explicit_feature_choices_are_sent(self):
        self.local.post.return_value = {"features": []}
        code, _ = self.command("features", "--enable", "knowledge_tags", "--disable", "ai_answer")
        self.assertEqual(code, 0)
        self.local.post.assert_called_once_with("/api/settings/features", {
            "features": {"knowledge_tags": True, "ai_answer": False}})
        self.local.post.reset_mock()
        code, result = self.command("features", "--enable", "ai_answer", "--disable", "ai_answer")
        self.assertEqual(code, 1)
        self.assertIn("同时", result["error"])
        self.local.post.assert_not_called()

    def test_auto_is_read_only_by_default_and_partial_changes_keep_other_settings(self):
        self.local.get.return_value = {"on_intake": {"tags": False, "answer": False}, "mode": "assistant"}
        code, result = self.command("enrich", "auto")
        self.assertEqual(code, 0)
        self.assertFalse(result["on_intake"]["tags"])
        self.local.get.assert_called_once_with("/api/settings/library-ai")
        self.local.post.assert_not_called()
        self.local.post.return_value = {"on_intake": {"tags": True, "answer": False}, "mode": "assistant"}
        code, _ = self.command("enrich", "auto", "--tags", "on")
        self.assertEqual(code, 0)
        self.local.post.assert_called_once_with("/api/settings/library-ai", {"on_intake": {"tags": True}})

    def test_mcp_auto_preserves_false_and_does_not_enable_features(self):
        self.local.post.return_value = {"on_intake": {"tags": False, "answer": True}}
        cli.mcp_call(self.local, "configure_enrichment_auto", {"tags": False, "answer": True})
        self.local.post.assert_called_once_with("/api/settings/library-ai", {"on_intake": {"tags": False, "answer": True}})

    def test_listing_preserves_paused_tasks_and_filters_without_preparing(self):
        self.local.get.return_value = {"tasks": [{"id": JOB, "enabled": False, "stale": True}],
                                       "total": 1, "mode": "assistant", "message": "功能已关闭"}
        code, result = self.command("enrich", "tasks", "--ids", PUBLICATION, OTHER, PUBLICATION, "--limit", "7")
        self.assertEqual(code, 0)
        self.assertFalse(result["tasks"][0]["enabled"])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.local.get.call_args.args[0]).query)
        self.assertEqual(query, {"limit": ["7"], "ids": [f"{PUBLICATION},{OTHER}"]})
        self.local.post.assert_not_called()

    def test_invalid_limits_or_identity_fail_before_contacting_the_app(self):
        for extra in (("--limit", "0"), ("--limit", "51"), ("--ids", "latest")):
            code, result = self.command("enrich", "tasks", *extra)
            self.assertEqual(code, 1, result)
        self.local.get.assert_not_called()
        self.local.post.assert_not_called()

    def test_prepare_downloads_only_question_images_and_keeps_original_urls(self):
        self.local.post.return_value = prepared()
        with tempfile.TemporaryDirectory() as folder:
            code, result = self.command("enrich", "prepare", PUBLICATION, "--kinds", "answer",
                                        "--agent", "豆包工作版", "--out", folder)
            self.assertEqual(code, 0, result)
            self.assertEqual(Path(result["local_images"]["crop"]).read_bytes(), PNG)
            self.assertEqual(Path(result["local_images"]["figures"][0]).read_bytes(), PNG)
        self.local.post.assert_called_once_with("/api/library/assistant/prepare", {
            "publication_id": PUBLICATION, "kinds": ["answer"], "agent": "豆包工作版"})
        self.assertEqual(self.local.get_bytes.call_args_list, [mock.call(f"/api/library/{PUBLICATION}/crop"),
                                                              mock.call(f"/api/library/{PUBLICATION}/figures/figure-1.png")])
        self.assertEqual(result["images"]["originals"][0]["url"], f"{self.local.url}/api/library/{PUBLICATION}/pages/0")
        self.assertEqual(result["jobs"][0]["fingerprint"], FINGERPRINT)

    def test_prepare_can_return_urls_without_fetching_images_or_opening_features(self):
        self.local.post.return_value = prepared()
        code, result = self.command("enrich", "prepare", PUBLICATION, "--no-images")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["local_images"], {"crop": None, "figures": []})
        self.local.get_bytes.assert_not_called()
        self.assertEqual(self.local.post.call_count, 1)

    def test_prepare_rejects_remote_or_wrong_publication_images(self):
        for url in ("https://example.com/image.png", f"/api/library/{OTHER}/crop", "/api/settings/credentials"):
            result = prepared()
            result["images"]["crop"] = url
            self.local.post.return_value = result
            code, response = self.command("enrich", "prepare", PUBLICATION)
            self.assertEqual(code, 1, response)
        self.local.get_bytes.assert_not_called()

    def test_submit_preserves_job_fingerprint_agent_and_latex_without_changing_source(self):
        self.local.post.return_value = {"job": {"id": JOB, "status": "done"}, "publication": {"id": PUBLICATION}}
        content = {"answer": "$x=2$", "analysis": "由 $\\frac{x}{2}=1$ 得 $x=2$。"}
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "结果.json"
            file.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8-sig")
            code, result = self.command("enrich", "submit", JOB, "--fingerprint", FINGERPRINT,
                                        "--result-file", str(file), "--agent", "豆包工作版")
        self.assertEqual(code, 0, result)
        self.local.post.assert_called_once_with("/api/library/assistant/complete", {
            **content, "job_id": JOB, "fingerprint": FINGERPRINT, "agent": "豆包工作版"})

    def test_submit_rejects_mixed_results_and_attempts_to_override_task_or_source(self):
        invalid = [{"tags": ["一元一次方程"], "answer": "2"}, {"tags": []}, {"tags": [1]},
                   {"answer": "2", "agent": "伪造来源"}, {"answer": "2", "stem": "改原题"},
                   {"answer": "2", "job_id": OTHER}, {"analysis": "只有解析"}, []]
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "结果.json"
            for value in invalid:
                file.write_text(json.dumps(value), encoding="utf-8")
                code, result = self.command("enrich", "submit", JOB, "--fingerprint", FINGERPRINT, "--result-file", str(file))
                self.assertEqual(code, 1, result)
        self.local.post.assert_not_called()

    def test_submit_does_not_report_success_when_the_app_rejects_a_stale_task(self):
        self.local.post.side_effect = cli.CliError("题目或配图已变化，请重新准备")
        code, result = self.command("enrich", "submit", JOB, "--fingerprint", FINGERPRINT, "--tags", "一元一次方程")
        self.assertEqual(code, 1)
        self.assertIn("重新准备", result["error"])
        self.assertNotIn("saved", result)
        self.assertEqual(self.local.post.call_count, 1, "stale results must not be retried blindly")

    def test_mcp_preparation_attaches_local_images_and_uses_client_identity(self):
        self.local.post.return_value = prepared()
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(cli.tempfile, "mkdtemp", return_value=folder):
            reply = cli.mcp_handle(self.local, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                               "params": {"name": "prepare_enrichment", "arguments": {"publication_id": PUBLICATION, "kinds": ["answer"]}}})
        result = reply["result"]
        self.assertFalse(result["isError"])
        self.assertEqual([block["type"] for block in result["content"]], ["text", "image", "image"])
        self.assertEqual(base64.b64decode(result["content"][1]["data"]), PNG)
        self.assertEqual(self.local.post.call_args.args[1]["agent"], "当前豆包")

    def test_mcp_submit_does_not_mix_kinds_or_call_a_cloud_api(self):
        self.local.post.return_value = {"job": {"status": "done"}}
        result, images = cli.mcp_call(self.local, "submit_enrichment", {
            "job_id": JOB, "fingerprint": FINGERPRINT, "tags": ["一元一次方程"]})
        self.assertEqual(result["job"]["status"], "done")
        self.assertEqual(images, [])
        self.assertEqual(self.local.post.call_args.args[0], "/api/library/assistant/complete")
        self.local.post.reset_mock()
        result = cli.mcp_handle(self.local, {"id": 2, "method": "tools/call", "params": {
            "name": "submit_enrichment", "arguments": {"job_id": JOB, "fingerprint": FINGERPRINT,
                                                        "tags": ["一元一次方程"], "answer": "2"}}})
        self.assertTrue(result["result"]["isError"])
        self.local.post.assert_not_called()


@override_settings(STATIC_URL="/static/")
class EnrichmentCliLiveTests(TempDataMixin, LiveServerTestCase):
    """Real local HTTP and database, an original synthetic PDF, no cloud calls."""

    host = "127.0.0.1"

    def setUp(self):
        self.use_temp_data()
        environment = mock.patch.dict(os.environ, {
            "TIYOUJU_URL": self.live_server_url, "TIYOUJU_AGENT": "",
            "QB_LIBRARY_AI_SETTINGS_FILE": str(self.temp / "library-ai-settings.json"),
            "QB_LIBRARY_AI_CREDENTIAL_FILE": str(self.temp / "synthetic-key.dat"),
            "QB_FEATURES_FILE": str(self.temp / "features.json"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        transport = mock.patch.object(library_ai_settings.requests, "post", side_effect=AssertionError("no cloud request allowed"))
        self.network = transport.start()
        self.addCleanup(transport.stop)
        self.paper = self.make_paper()
        self.question = self.card(self.paper, question_type="free_response", stem="计算 $1+1$。")
        self.question.approved = True
        self.question.approved_at = timezone.now()
        self.question.approved_content_hash = library.approval_hash(self.question)
        self.question.save()
        self.publication = library.publish(self.question)[0]

    def test_current_assistant_can_prepare_and_submit_two_separate_tasks_over_real_http(self):
        before = deepcopy(self.publication.content)
        code, switches = run_json("features", "--enable", "knowledge_tags", "ai_answer")
        self.assertEqual(code, 0, switches)
        with tempfile.TemporaryDirectory() as folder:
            code, task = run_json("enrich", "prepare", str(self.publication.pk), "--out", folder, "--agent", "豆包工作版")
            self.assertEqual(code, 0, task)
            self.assertTrue(Path(task["local_images"]["crop"]).is_file())
            self.assertEqual(len(task["jobs"]), 2)
            for job in task["jobs"]:
                if job["kind"] == "tags":
                    args = ["--tags", task["knowledge"]["points"][0]["point"]]
                else:
                    args = ["--answer", "$2$", "--analysis", "$1+1=2$，代回检查。"]
                code, result = run_json("enrich", "submit", job["id"], "--fingerprint", job["fingerprint"],
                                        "--agent", "豆包工作版", *args)
                self.assertEqual(code, 0, result)
                self.assertEqual(result["job"]["status"], "done")
        self.publication.refresh_from_db()
        self.question.refresh_from_db()
        self.assertEqual(self.publication.content, before)
        self.assertEqual(self.question.answer, "")
        self.assertTrue(self.question.approved)
        self.assertEqual(self.publication.extras["ai_answer"]["agent"], "豆包工作版")
        self.assertFalse(self.publication.extras["ai_answer"]["checked"])
        self.assertFalse(self.publication.extras["tags_checked"])
        self.network.assert_not_called()

    def test_auto_changes_only_timing_and_disabled_task_cannot_write_back(self):
        code, current = run_json("enrich", "auto")
        self.assertEqual(code, 0, current)
        self.assertEqual(current["on_intake"], {"tags": False, "answer": False})
        code, current = run_json("enrich", "auto", "--tags", "on")
        self.assertEqual(code, 0, current)
        self.assertEqual(current["on_intake"], {"tags": True, "answer": False})
        self.assertFalse(current["features"]["knowledge_tags"])
        run_json("features", "--enable", "ai_answer")
        code, task = run_json("enrich", "prepare", str(self.publication.pk), "--kinds", "answer", "--no-images", "--agent", "豆包工作版")
        self.assertEqual(code, 0, task)
        run_json("features", "--disable", "ai_answer")
        job = task["jobs"][0]
        code, result = run_json("enrich", "submit", job["id"], "--fingerprint", job["fingerprint"], "--answer", "2", "--agent", "豆包工作版")
        self.assertEqual(code, 1)
        self.assertIn("关闭", result["error"])
        self.publication.refresh_from_db()
        self.assertNotIn("ai_answer", self.publication.extras)
        self.network.assert_not_called()
