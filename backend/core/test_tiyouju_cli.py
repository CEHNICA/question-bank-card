"""tiyouju (the command line and MCP server for AI assistants) against a live app."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pymupdf as fitz
from django.test import LiveServerTestCase, SimpleTestCase, override_settings
from PIL import Image, ImageDraw

from . import provider_catalog
from .models import Paper, PublishedQuestion, Question
from .pipeline import paper_dir

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("tiyouju_cli", ROOT / "tiyouju_cli.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("tiyouju_cli", cli)
_spec.loader.exec_module(cli)


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
            mock.patch.object(cli, "configure_streams"):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def run_json(*argv: str):
    code, out, err = run(*argv, "--json")
    return code, json.loads(out) if out.strip() else None


# The app has no static files; Django's live server still wants a STATIC_URL.
@override_settings(STATIC_URL="/static/")
class TiyoujuCliTests(LiveServerTestCase):
    # Match the app's IPv4-only listener. On Windows, localhost may try ::1
    # for a full connection timeout before falling back on every CLI request.
    host = "127.0.0.1"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        override = override_settings(DATA_ROOT=self.temp / "data")
        override.enable()
        self.addCleanup(override.disable)
        environment = mock.patch.dict(os.environ, {
            "TIYOUJU_URL": self.live_server_url, "TIYOUJU_AGENT": "",
            "QB_MINERU_CONFIGURED": "1", "QB_MINIMAX_CONFIGURED": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.paper = Paper.objects.create(
            filename="期中卷.pdf", kind="pdf", sha256="b" * 64,
            source_path=str(self.temp / "s.pdf"), render_path=str(self.temp / "s.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}], status=Paper.Status.READY,
        )
        pages = paper_dir(self.paper) / "pages"
        pages.mkdir(parents=True)
        image = Image.new("RGB", (1000, 1000), "white")
        ImageDraw.Draw(image).rectangle((600, 300, 900, 600), outline="black", width=4)
        image.save(pages / "page_0.png")
        candidate = {"page_idx": 0, "bbox": [600, 300, 900, 600]}
        self.q1 = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", state=Question.State.GREEN,
            stem="计算 $1+1$。", options={"A": "1", "B": "2"},
            regions=[{"page_idx": 0, "bbox": [50, 50, 950, 250]}],
        )
        self.q2 = Question.objects.create(
            paper=self.paper, number=2, question_type="free_response", state=Question.State.YELLOW,
            stem="如图，求 $x$。", flags=["两次识读不一致，请核对题面中标黄的位置"],
            regions=[{"page_idx": 0, "bbox": [50, 260, 950, 700]}], figure_candidates=[candidate],
            read_a={"stem": "如图，求 $x$。"}, read_b={"stem": "如图，求 $y$。"},
        )
        self.q3 = Question.objects.create(
            paper=self.paper, number=3, question_type="fill_blank", state=Question.State.GREEN,
            stem="$2+2=$____。", regions=[{"page_idx": 0, "bbox": [50, 710, 950, 900]}],
        )

    def test_status_papers_and_card_lists(self):
        code, status = run_json("status")
        self.assertEqual(code, 0)
        self.assertTrue(status["running"])
        self.assertTrue(status["keys"]["mineru"])
        code, items = run_json("papers")
        self.assertEqual([item["name"] for item in items], ["期中卷.pdf"])
        for ref in (str(self.paper.pk)[:8], "期中", "latest"):
            code, cards = run_json("cards", ref, "--filter", "todo")
            self.assertEqual(code, 0, cards)
            self.assertEqual([card["number"] for card in cards["cards"]], [2])
        self.assertTrue(any("两次识读不一致" in issue for issue in cards["cards"][0]["issues"]))
        self.assertTrue(any(issue.startswith("可能漏图") for issue in cards["cards"][0]["issues"]))
        code, text, _err = run("cards", "期中")
        self.assertIn("第 2 题", text)
        code, missing = run_json("cards", "没有这份")
        self.assertEqual(code, 1)
        self.assertIn("找不到试卷", missing["error"])

    def test_show_saves_the_crop_and_numbered_candidates(self):
        out = self.temp / "images"
        code, card = run_json("show", "期中", "2", "--out", str(out))
        self.assertEqual(code, 0, card)
        self.assertEqual(card["candidates"][0]["number"], 1)
        self.assertEqual(card["candidates"][0]["key"], "0:600,300,900,600")
        self.assertEqual(Image.open(card["images"]["crop"]).size, (900, 440))
        self.assertTrue(Path(card["images"]["candidates"]).is_file())
        self.assertEqual(set(card["readings"]), {"a", "b"})
        code, text, _err = run("show", "期中", "2", "--out", str(out))
        self.assertIn("候选图编号", text)
        self.assertIn("几次识读", text)

    def test_fix_figures_approve_and_publish_as_an_assistant(self):
        stem = self.temp / "2.txt"
        stem.write_text("如图，求 $y$ 的值。", encoding="utf-8")
        code, fixed = run_json("fix", "期中", "2", "--stem-file", str(stem), "--agent", "豆包")
        self.assertEqual(code, 0, fixed)
        self.q2.refresh_from_db()
        self.assertEqual((self.q2.stem, self.q2.text_source), ("如图，求 $y$ 的值。", "assistant"))

        code, figures = run_json("figures", "期中", "2", "--use", "1", "--agent", "豆包")
        self.assertEqual(code, 0, figures)
        self.q2.refresh_from_db()
        self.assertEqual(self.q2.figures[0]["candidate_key"], "0:600,300,900,600")
        self.assertEqual((self.q2.figure_review["decided_by"], self.q2.figure_review["agent"]), ("ai", "豆包"))
        code, none = run_json("figures", "期中", "3", "--none")
        self.assertEqual(code, 0, none)

        code, approved = run_json("approve", "期中", "1", "2", "9", "--agent", "豆包")
        self.assertEqual(code, 0)
        self.assertEqual(approved["approved"], 2)
        self.assertIn("没有第 9 题", approved["results"][2]["error"])
        self.q2.refresh_from_db()
        self.assertEqual((self.q2.approval_source, self.q2.approval_agent), ("ai", "豆包"))
        code, green = run_json("approve", "期中", "--green")
        self.assertEqual(green["approved"], 1)

        code, published = run_json("publish", "期中")
        self.assertEqual((code, published["created"], published["not_approved"]), (0, 3, []))
        self.assertEqual(set(PublishedQuestion.objects.values_list("review_source", flat=True)), {"ai"})
        code, found = run_json("library", "--review", "ai")
        self.assertEqual(found["total"], 3)

    def test_an_undecided_type_is_chosen_before_approving_and_origin_is_shown(self):
        Question.objects.filter(pk=self.q1.pk).update(question_type="unknown", origin="2025·北京海淀·期中")
        code, cards = run_json("cards", "期中", "--filter", "todo")
        card = next(item for item in cards["cards"] if item["number"] == 1)
        self.assertTrue(card["type_blocked"])
        self.assertTrue(card["issues"][0].startswith("题型还没定"))
        code, approved = run_json("approve", "期中", "1")
        self.assertEqual(approved["approved"], 0)
        self.assertIn("题型还没定", approved["results"][0]["error"])
        code, text, _err = run("show", "期中", "1", "--no-images")
        self.assertIn("题源：2025·北京海淀·期中", text)
        # Only --type: the type action, nothing else touched.
        code, fixed = run_json("fix", "期中", "1", "--type", "single_choice", "--agent", "豆包")
        self.assertEqual(code, 0, fixed)
        self.q1.refresh_from_db()
        self.assertEqual((self.q1.question_type, self.q1.edited, self.q1.text_source), ("single_choice", False, ""))
        code, approved = run_json("approve", "期中", "1")
        self.assertEqual(approved["approved"], 1)
        code, fixed = run_json("fix", "期中", "2", "--origin", "【2026·滕州二中月考】")
        self.q2.refresh_from_db()
        self.assertEqual(self.q2.origin, "2026·滕州二中月考")

    def test_a_persons_approval_is_left_alone(self):
        self.client.post(f"/api/questions/{self.q1.pk}/approve", data=json.dumps({"approved": True}),
                         content_type="application/json", HTTP_X_QB_REQUEST="1")
        code, result = run_json("fix", "期中", "1", "--stem", "改掉")
        self.assertEqual(code, cli.EXIT_NEEDS_USER)
        self.assertIn("人工通过", result["error"])
        code, result = run_json("unapprove", "期中", "1")
        self.assertEqual(code, 1)
        self.assertIn("人工通过", result["error"])
        code, approved = run_json("approve", "期中", "1")
        self.assertEqual(approved["results"][0]["approved_by"], "human")
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.approval_source, "human")

    def test_upload_sends_the_file_and_spots_a_duplicate(self):
        pdf = self.temp / "新卷.pdf"
        document = fitz.open()
        document.new_page().insert_text((72, 72), "1. 1+1=?")
        document.save(pdf)
        code, first = run_json("upload", str(pdf))
        self.assertEqual(code, 0, first)
        self.assertEqual(first["paper"]["name"], "新卷.pdf")
        self.assertFalse(first["duplicate"])
        code, again = run_json("upload", str(pdf))
        self.assertTrue(again["duplicate"])
        code, missing = run_json("upload", str(self.temp / "nope.pdf"))
        self.assertIn("找不到文件", missing["error"])

    def test_mcp_lists_tools_and_returns_the_crop_as_an_image(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "cherry-studio"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "show_card", "arguments": {"paper": "期中", "card": "2"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "approve_cards", "arguments": {"paper": "期中", "cards": ["1"]}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "list_cards", "arguments": {"paper": "不存在"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "nope"},
        ]
        stdin = io.StringIO("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n")
        stdout = io.StringIO()
        client = cli.Client(agent=cli.DEFAULT_AGENT)
        self.assertEqual(cli.run_mcp(client, stdin, stdout), 0)
        replies = {reply["id"]: reply for reply in map(json.loads, stdout.getvalue().splitlines())}
        self.assertEqual(sorted(replies), [1, 2, 3, 4, 5, 6])
        self.assertEqual(replies[1]["result"]["serverInfo"]["name"], "tiyouju")
        names = {tool["name"] for tool in replies[2]["result"]["tools"]}
        self.assertTrue({"upload_paper", "show_card", "fix_card", "set_figures", "approve_cards", "publish_paper"} <= names)
        content = replies[3]["result"]["content"]
        self.assertEqual([item["type"] for item in content], ["text", "image"])
        self.assertEqual(content[1]["mimeType"], "image/png")
        self.assertFalse(replies[4]["result"]["isError"])
        self.q1.refresh_from_db()
        self.assertEqual(self.q1.approval_agent, "cherry-studio")
        self.assertTrue(replies[5]["result"]["isError"])
        self.assertEqual(replies[6]["error"]["code"], -32601)


    def test_status_names_the_free_keys_still_missing(self):
        nothing = {"QB_MODEL_PREFERENCES_FILE": str(self.temp / "prefs.json")}
        for service, (pool, single) in provider_catalog.environment_names().items():
            nothing.update({pool: "[]", single: "", f"QB_{service.upper()}_CONFIGURED": "0"})
        with mock.patch.dict(os.environ, nothing):
            code, status = run_json("status")
            self.assertEqual(code, 0)
            self.assertFalse(status["upload_enabled"])
            self.assertEqual([item["what"] for item in status["missing"]], ["mineru", "vision"])
            self.assertIn("mineru.net", status["missing"][0]["signup"])
            self.assertEqual([item["service"] for item in status["missing"][1]["options"]], ["modelscope"])
            code, text, _err = run("status")
            self.assertIn("modelscope.cn", text)
            self.assertNotIn("bigmodel", text)
            self.assertIn("tiyouju config --reader assistant", text)
            # AI 助手读题 needs MinerU only.
            run_json("config", "--reader", "assistant")
            code, status = run_json("status")
            self.assertEqual([item["what"] for item in status["missing"]], ["mineru"])

    def test_config_switches_reading_without_touching_keys(self):
        preferences_file = self.temp / "prefs.json"
        with mock.patch.dict(os.environ, {"QB_MODEL_PREFERENCES_FILE": str(preferences_file)}):
            code, result = run_json("config")
            self.assertEqual(code, 0)
            self.assertFalse(result["changed"])
            self.assertFalse(result["assistant_mode"])
            self.assertEqual(result["reader"], "MiniMax MiniMax-M3")
            code, result = run_json("config", "--reader", "assistant", "--agent", "豆包")
            self.assertTrue(result["changed"])
            self.assertTrue(result["assistant_mode"])
            self.assertIsNone(result["reader"])
            saved = json.loads(preferences_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["roles"]["primary_engine"], "assistant")
            code, text, _err = run("config", "--reader", "modelscope", "--minimax-plan", "max")
            self.assertEqual(code, 0)
            self.assertIn("已保存", text)
            self.assertIn("modelscope", text)
            saved = json.loads(preferences_file.read_text(encoding="utf-8"))
            self.assertEqual((saved["roles"]["primary_engine"], saved["plans"]["minimax"]), ("modelscope_qwen", "max"))
            self.assertNotIn("key", json.dumps(saved).lower().replace("minimax", ""))


class TiyoujuOfflineTests(SimpleTestCase):
    def test_a_closed_app_is_reported_with_its_own_exit_code(self):
        with mock.patch.dict(os.environ, {"TIYOUJU_URL": "http://127.0.0.1:9"}):
            code, result = run_json("status")
        self.assertEqual(code, cli.EXIT_NOT_RUNNING)
        self.assertIn("tiyouju start", result["error"])

    def test_the_port_comes_from_the_launchers_instance_file(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "QuestionBankCard" / "runtime"
            runtime.mkdir(parents=True)
            (runtime / "instance.json").write_text(json.dumps({"pid": 1, "port": 8790}), encoding="utf-8")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": folder, "TIYOUJU_URL": ""}):
                self.assertEqual(cli.base_url(), "http://127.0.0.1:8790")
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(Path(folder) / "none"), "TIYOUJU_URL": ""}):
                self.assertEqual(cli.base_url(), "http://127.0.0.1:8768")

    def test_candidate_keys_match_the_app(self):
        from .figure_policy import candidate_key
        for item in ({"page_idx": 0, "bbox": [600, 300, 900, 600]},
                     {"page_idx": 2, "bbox": [142.04, 233.0, 843.25, 294.96]},
                     {"page_idx": 1, "bbox": [-5, 0, 1004, 2]}):
            self.assertEqual(cli.candidate_key(item), candidate_key(item))

    def test_figure_choices_are_parsed(self):
        self.assertEqual(cli.parse_uses(["1", "2:a，图3:stem"]), [(1, "stem"), (2, "A"), (3, "stem")])
        with self.assertRaises(cli.CliError):
            cli.parse_uses(["1:F"])

    def test_help_explains_the_workflow(self):
        code, out, _err = run()
        self.assertEqual(code, 0)
        self.assertIn("处理一份试卷的标准步骤", out)
