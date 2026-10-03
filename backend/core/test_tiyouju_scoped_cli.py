"""Explicit one-question handoff carries identity/results without global changes."""
import json
from pathlib import Path
import tempfile
from unittest import mock

from django.test import SimpleTestCase

from .test_tiyouju_cli import cli, run_json
from .test_tiyouju_enrichment_cli import prepared, PUBLICATION, JOB, FINGERPRINT, PNG


class ScopedEnrichmentCliTests(SimpleTestCase):
    def setUp(self):
        self.local = mock.Mock(spec=cli.Client)
        self.local.url = "http://127.0.0.1:8790"; self.local.agent = "Scoped audit assistant"
        self.local.get_bytes.return_value = PNG

    def command(self, *args):
        with mock.patch.object(cli, "Client", return_value=self.local):
            return run_json(*args)

    def test_prepare_uses_exact_job_kind_and_agent_without_enabling_features(self):
        self.local.post.return_value = prepared()
        code, result = self.command("enrich", "prepare", PUBLICATION, "--job-id", JOB, "--kinds", "answer", "--no-images", "--agent", "Scoped assistant")
        self.assertEqual(code, 0, result)
        self.local.post.assert_called_once_with("/api/library/assistant/prepare", {
            "publication_id": PUBLICATION, "job_id": JOB, "kinds": ["answer"], "agent": "Scoped assistant"})
        self.local.get.assert_not_called(); self.local.get_bytes.assert_not_called()

    def test_invalid_bound_job_identity_fails_before_contacting_app(self):
        code, result = self.command("enrich", "prepare", PUBLICATION, "--job-id", "cancelled-latest", "--kinds", "answer", "--no-images")
        self.assertEqual(code, 1, result)
        self.local.post.assert_not_called(); self.local.get.assert_not_called()

    def test_rejected_cancelled_job_has_no_replacement_or_global_fallback(self):
        self.local.post.side_effect = cli.CliError("指定任务已取消；未新建其他任务")
        code, result = self.command("enrich", "prepare", PUBLICATION, "--job-id", JOB, "--kinds", "answer", "--no-images")
        self.assertEqual(code, 1); self.assertIn("已取消", result["error"])
        self.assertEqual(self.local.post.call_count, 1)
        self.local.get.assert_not_called(); self.local.get_bytes.assert_not_called()

    def test_mcp_carries_scoped_job_id_and_every_local_figure(self):
        payload = prepared()
        payload["jobs"][0].update(solution_scope=True, status="running")
        payload["publication"]["content"]["stem"] = "(1) 证明命题。\n(2) 求范围。"
        payload["images"]["figures"] = [{"slot": slot, "url": f"/api/library/{PUBLICATION}/figures/figure-{index}.png"}
                                        for index, slot in enumerate("ABCD", 1)]
        self.local.post.return_value = payload
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(cli.tempfile, "mkdtemp", return_value=folder):
            result, paths = cli.mcp_call(self.local, "prepare_enrichment", {
                "publication_id": PUBLICATION, "kinds": ["answer"], "job_id": JOB})
            self.assertEqual(len(paths), 5, "Original crop and all four option figures must be shown, not one selected image")
            self.assertTrue(all(Path(path).read_bytes() == PNG for path in paths))
        self.assertEqual(self.local.post.call_args.args[1]["job_id"], JOB)
        self.assertEqual(self.local.post.call_args.args[1]["agent"], self.local.agent)
        self.assertEqual(self.local.post.call_count, 1); self.local.get.assert_not_called()
        self.assertEqual(result["publication"]["content"]["stem"], payload["publication"]["content"]["stem"])
        self.assertTrue(result["jobs"][0]["solution_scope"])

    def test_all_subquestion_result_and_long_detailed_steps_are_preserved(self):
        answer = "(1) $3$；(2) $7$"
        analysis = "(1) $1+2=3$。\n\n(2) $3+4=7$。\n\n" + "逐步说明推导并核对条件。"*40
        self.local.post.return_value = {"job": {"id": JOB, "status": "done", "result": {"answer": answer, "analysis": analysis}}}
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder)/"two-parts.json"
            filename.write_text(json.dumps({"answer": answer, "analysis": analysis}, ensure_ascii=False), encoding="utf-8")
            code, result = self.command("enrich", "submit", JOB, "--fingerprint", FINGERPRINT,
                                        "--result-file", str(filename), "--agent", "Scoped assistant")
        self.assertEqual(code, 0, result)
        self.local.post.assert_called_once_with("/api/library/assistant/complete", {
            "job_id": JOB, "fingerprint": FINGERPRINT, "agent": "Scoped assistant", "answer": answer, "analysis": analysis})
        self.assertGreater(len(self.local.post.call_args.args[1]["analysis"]), 300)
        self.local.get.assert_not_called()

    def test_late_submission_rejection_does_not_retry_prepare_or_report_done(self):
        self.local.post.side_effect = cli.CliError("任务已取消，不能重复提交")
        code, result = self.command("enrich", "submit", JOB, "--fingerprint", FINGERPRINT, "--answer", "3")
        self.assertEqual(code, 1); self.assertIn("取消", result["error"])
        self.assertEqual(self.local.post.call_count, 1)
        self.assertEqual(self.local.post.call_args.args[0], "/api/library/assistant/complete")
        self.assertNotIn("saved", result); self.local.get.assert_not_called()
