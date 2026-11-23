"""外部 AI 助手自主探查与切题全流程测试（Agent-First Intake + scan_only + CLI/MCP）。

全部测试仅使用临时目录中合成的 PDF、图片和 MinerU 解析包，绝不读取真实密钥或用户题库。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from unittest import mock

import pymupdf as fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, LiveServerTestCase, TestCase, override_settings
from PIL import Image, ImageDraw

from . import library, pipeline, question_layout, readers
from .models import Block, Paper, PublishedQuestion, Question, QuestionGroup
from .pipeline import paper_dir

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("tiyouju_cli", ROOT / "tiyouju_cli.py")
cli = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("tiyouju_cli", cli)
_spec.loader.exec_module(cli)


def _run_cli(*argv: str) -> tuple[int, dict | list | None, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), mock.patch.object(cli, "configure_streams"):
        code = cli.main([*argv, "--json"])
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None), err.getvalue()


def _make_pdf_bytes(lines_per_page: list[list[str]]) -> bytes:
    doc = fitz.open()
    for page_lines in lines_per_page:
        page = doc.new_page(width=595, height=842)
        y = 72
        for line in page_lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 28
    return doc.tobytes()


def _make_mineru_zip(target: Path, content_items: list[dict]) -> Path:
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("doc_content_list.json", json.dumps(content_items, ensure_ascii=False))
    return target


class AgentCuttingBackendTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="tiyouju-agent-cut-", ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        override = override_settings(DATA_ROOT=self.root / "data")
        override.enable()
        self.addCleanup(override.disable)
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        self.client = Client()
        self.headers = {"HTTP_X_QB_REQUEST": "1"}

    def _create_ready_paper(self, pages_count: int = 2) -> Paper:
        pdf_bytes = _make_pdf_bytes([[f"第 {i + 1} 页测试文本"] for i in range(pages_count)])
        pdf_path = self.root / "sample.pdf"
        pdf_path.write_bytes(pdf_bytes)
        paper = Paper.objects.create(
            filename="合成教辅.pdf",
            kind="pdf",
            material_type=Paper.MaterialType.BOOK,
            sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            source_path=str(pdf_path),
            render_path=str(pdf_path),
            pages=[{"page_idx": i, "width": 595, "height": 842} for i in range(pages_count)],
            status=Paper.Status.READY,
            processing_plan={"schema": 1, "mode": "mineru", "revision": 1, "scan_only": True},
        )
        pages_folder = paper_dir(paper) / "pages"
        pages_folder.mkdir(parents=True, exist_ok=True)
        for i in range(pages_count):
            img = Image.new("RGB", (1000, 1400), "white")
            ImageDraw.Draw(img).rectangle((100, 200, 400, 420), outline="black", width=4)
            img.save(pages_folder / f"page_{i}.png")
        return paper

    def test_upload_scan_only_mineru_does_not_require_vision_reader_and_stops_at_ready(self):
        pdf_bytes = _make_pdf_bytes([["1. 已知集合 A={1,2}，求 A 的子集个数。", "2. 计算 1+1。"]])
        upload = SimpleUploadedFile("book.pdf", pdf_bytes, content_type="application/pdf")
        with (
            mock.patch.object(readers, "configured", side_effect=lambda name: name == "mineru"),
            mock.patch.object(readers, "primary_engine", return_value=None),
            mock.patch.object(readers, "assistant_mode", return_value=False),
        ):
            resp = self.client.post(
                "/api/papers",
                data={"file": upload, "parse_mode": "mineru", "scan_only": "1", "material_type": "exam"},
                **self.headers,
            )
        self.assertEqual(resp.status_code, 201)
        paper_data = resp.json()["paper"]
        self.assertTrue(paper_data["processing_plan"].get("scan_only"))

        paper = Paper.objects.get(pk=paper_data["id"])
        zip_path = _make_mineru_zip(
            self.root / "mineru_result.zip",
            [
                {"type": "text", "page_idx": 0, "bbox": [70, 60, 900, 150], "text": "1. 第一章习题"},
                {"type": "text", "page_idx": 0, "bbox": [70, 180, 900, 260], "text": "1. 第二章习题"},
                {
                    "type": "table",
                    "page_idx": 0,
                    "bbox": [70, 300, 900, 450],
                    "table_body": "<table><tr><th>x</th><th>y</th></tr><tr><td>1</td><td>2</td></tr></table>",
                },
            ],
        )

        def fake_extract(render, download, page_count, **kwargs):
            Path(download).write_bytes(zip_path.read_bytes())

        with (
            mock.patch.object(pipeline, "request_extract_file_from_pool", side_effect=fake_extract),
            mock.patch.object(pipeline, "segment_paper", side_effect=AssertionError("scan_only 不应自动切题")),
            mock.patch.object(pipeline, "read_questions", side_effect=AssertionError("scan_only 不应触发内置看图识读")),
        ):
            pipeline.process_paper(paper)

        paper.refresh_from_db()
        self.assertEqual(paper.status, Paper.Status.READY)
        self.assertEqual(paper.questions.count(), 0)
        self.assertEqual(paper.blocks.count(), 3)
        self.assertTrue(paper.question_groups.exists())
        self.assertTrue((paper.structure or {}).get("scan_only"))

        # blocks 接口应完整返回 text 与 table_markdown
        blocks_resp = self.client.get(f"/api/papers/{paper.pk}/blocks?pages=0")
        self.assertEqual(blocks_resp.status_code, 200)
        blocks_data = blocks_resp.json()["blocks"]
        self.assertEqual(len(blocks_data), 3)
        self.assertIn("| x | y |", blocks_data[2]["table_markdown"])

    def test_upload_scan_only_native_pdf_extracts_blocks_without_auto_cutting(self):
        pdf_bytes = _make_pdf_bytes([["1. Solve x + 1 = 2.", "2. Solve x + 3 = 5."]])
        upload = SimpleUploadedFile("native.pdf", pdf_bytes, content_type="application/pdf")
        resp = self.client.post(
            "/api/papers", data={"file": upload, "parse_mode": "auto", "scan_only": "1"}, **self.headers
        )
        self.assertEqual(resp.status_code, 201)
        paper = Paper.objects.get(pk=resp.json()["paper"]["id"])
        self.assertEqual(paper.status, Paper.Status.READY)
        self.assertEqual(paper.questions.count(), 0)
        self.assertGreater(paper.blocks.count(), 0)

    def test_page_preview_grid_draws_overlay_in_memory_only(self):
        paper = self._create_ready_paper(pages_count=1)
        plain_resp = self.client.get(f"/api/papers/{paper.pk}/pages/0/preview")
        self.assertEqual(plain_resp.status_code, 200)
        preview_cache = pipeline.PageStore(paper).preview(0)
        before_hash = hashlib.sha256(preview_cache.read_bytes()).hexdigest()

        grid_resp = self.client.get(f"/api/papers/{paper.pk}/pages/0/preview?grid=1")
        self.assertEqual(grid_resp.status_code, 200)
        self.assertEqual(grid_resp["Content-Type"], "image/jpeg")
        grid_bytes = grid_resp.content
        self.assertNotEqual(grid_bytes, preview_cache.read_bytes())
        # 磁盘原页预览缓存文件字节必须纹丝不动
        after_hash = hashlib.sha256(preview_cache.read_bytes()).hexdigest()
        self.assertEqual(before_hash, after_hash)
        with Image.open(io.BytesIO(grid_bytes)) as img:
            self.assertGreater(img.width, 100)

    def test_agent_cut_dual_track_and_read_cut_questions_handoff(self):
        paper = self._create_ready_paper(pages_count=2)
        Block.objects.create(paper=paper, seq=0, type="image", page_idx=0, bbox=[100, 200, 400, 420], text="")

        payload = {
            "client_request_id": str(uuid.uuid4()),
            "agent": "codex-agent",
            "questions": [
                {
                    "number": 1,
                    "group_title": "第1章 · 例题精讲",
                    "section": "第1节 函数性质 · 例题",
                    "source_kind": "example",
                    "regions": [{"page_idx": 0, "bbox": [50, 50, 950, 480]}],
                    "figures": [{"slot": "stem", "page_idx": 0, "bbox": [100, 200, 400, 420]}],
                    "question_type": "single_choice",
                    "stem": "已知函数 $f(x)=x^2$，则 $f(2)=$（ ）。",
                    "options": {"A": "2", "B": "4", "C": "8", "D": "1"},
                    "answer": "B",
                    "analysis": "代入 $x=2$ 得 $f(2)=4$。",
                    "origin": "2026年测试卷",
                },
                {
                    "number": 2,
                    "group_title": "第1章 · 例题精讲",
                    "section": "第1节 函数性质 · 练习",
                    "source_kind": "exercise",
                    "regions": [{"page_idx": 0, "bbox": [50, 520, 950, 900]}],
                    "confirm_no_figure": True,
                },
            ],
        }
        resp = self.client.post(
            f"/api/papers/{paper.pk}/agent-cut",
            data=json.dumps(payload),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["created_ids"]), 2)
        self.assertFalse(data["already_applied"])

        # 幂等重放
        replay = self.client.post(
            f"/api/papers/{paper.pk}/agent-cut",
            data=json.dumps(payload),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["already_applied"])
        self.assertEqual(replay.json()["created_ids"], data["created_ids"])

        q1 = Question.objects.get(pk=data["created_ids"][0])
        q2 = Question.objects.get(pk=data["created_ids"][1])
        self.assertEqual(q1.body_mode, "text")
        self.assertEqual(q1.text_source, "assistant")
        self.assertTrue(q1.edited)
        self.assertFalse(q1.approved)
        self.assertEqual(q1.state, Question.State.YELLOW)
        self.assertEqual(q1.section, "第1节 函数性质 · 例题")
        self.assertEqual(len(q1.figures), 1)
        self.assertEqual(len(q1.figure_candidates), 1)

        self.assertEqual(q2.body_mode, "source_image")
        self.assertFalse(q2.edited)
        self.assertFalse(q2.approved)
        self.assertEqual(q2.figure_review.get("status"), "confirmed_no_figure")

        # Track B 原图题可交回 read-cut-questions；Track A 已有文字题被安全跳过
        with mock.patch("core.views._vision_ready", return_value=True):
            rc_resp = self.client.post(
                f"/api/papers/{paper.pk}/read-cut-questions",
                data=json.dumps({"question_ids": []}),
                content_type="application/json",
                **self.headers,
            )
        self.assertEqual(rc_resp.status_code, 200)
        rc_data = rc_resp.json()
        self.assertEqual(rc_data["queued_ids"], [q2.pk])
        self.assertEqual(rc_data["skipped"], [{"id": q1.pk, "reason": "已有文字正文，已保留"}])
        q2.refresh_from_db()
        self.assertTrue(q2.ocr_pending)
        self.assertTrue(q2.reread_requested)

    def test_replace_scope_protects_approved_edited_published_and_supports_undo(self):
        paper = self._create_ready_paper(pages_count=2)
        group = QuestionGroup.objects.create(
            paper=paper, title="默认组", kind="exam", sequence=0, page_start=1, page_end=2, metadata={"pages": [0, 1]}
        )
        draft_card = Question.objects.create(
            paper=paper,
            group=group,
            number=1,
            body_mode="source_image",
            edited=False,
            approved=False,
            regions=[{"page_idx": 0, "bbox": [50, 50, 900, 200]}],
            state=Question.State.YELLOW,
        )
        edited_card = Question.objects.create(
            paper=paper,
            group=group,
            number=2,
            body_mode="text",
            stem="人工已改字题干",
            edited=True,
            approved=False,
            regions=[{"page_idx": 0, "bbox": [50, 220, 900, 380]}],
            state=Question.State.YELLOW,
        )
        approved_card = Question.objects.create(
            paper=paper,
            group=group,
            number=3,
            body_mode="text",
            stem="人工已通过题干",
            edited=True,
            approved=True,
            regions=[{"page_idx": 0, "bbox": [50, 400, 900, 560]}],
            state=Question.State.GREEN,
        )
        pub_source_card = Question.objects.create(
            paper=paper,
            group=group,
            number=4,
            question_type="single_choice",
            body_mode="text",
            stem="已入库未解锁题干",
            edited=False,
            approved=True,
            regions=[{"page_idx": 0, "bbox": [50, 580, 900, 740]}],
            state=Question.State.GREEN,
        )
        pub_source_card.approved_content_hash = library.approval_hash(pub_source_card)
        pub_source_card.save(update_fields=["approved_content_hash"])
        library.publish(pub_source_card)
        # 将 approved 置回 False，以单独验证 PublishedQuestion 在用入库记录的硬保护分支
        pub_source_card.approved = False
        pub_source_card.save(update_fields=["approved"])

        # replace_scope="question_ids" 遇到受保护题必须返回 409 拒绝
        reject_resp = self.client.post(
            f"/api/papers/{paper.pk}/agent-cut",
            data=json.dumps(
                {
                    "replace_scope": "question_ids",
                    "replace_question_ids": [edited_card.pk],
                    "questions": [{"regions": [{"page_idx": 0, "bbox": [50, 220, 900, 380]}], "stem": "覆盖尝试"}],
                }
            ),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(reject_resp.status_code, 409)

        # replace_scope="page" 仅替换草稿 draft_card，三条受保护题全部保留
        replace_resp = self.client.post(
            f"/api/papers/{paper.pk}/agent-cut",
            data=json.dumps(
                {
                    "replace_scope": "page",
                    "replace_pages": [0],
                    "questions": [
                        {
                            "number": 10,
                            "group_id": group.pk,
                            "regions": [{"page_idx": 0, "bbox": [50, 50, 900, 200]}],
                            "stem": "重切后的新草稿",
                        }
                    ],
                }
            ),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(replace_resp.status_code, 200)
        rdata = replace_resp.json()
        self.assertEqual(rdata["replaced_ids"], [draft_card.pk])
        self.assertEqual(len(rdata["skipped_protected"]), 3)
        self.assertFalse(Question.objects.filter(pk=draft_card.pk).exists())
        self.assertTrue(Question.objects.filter(pk=edited_card.pk).exists())
        self.assertTrue(Question.objects.filter(pk=approved_card.pk).exists())
        self.assertTrue(Question.objects.filter(pk=pub_source_card.pk).exists())

        # 撤销整批切题应恢复 draft_card 并软删除新创建的题卡
        op_id = rdata["operation"]["id"]
        undo_resp = self.client.post(
            f"/api/papers/{paper.pk}/question-layout/{op_id}/undo",
            data=json.dumps({"layout_revision": rdata["layout_revision"], "client_request_id": str(uuid.uuid4())}),
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(undo_resp.status_code, 200)
        self.assertTrue(Question.objects.filter(pk=draft_card.pk).exists())
        self.assertFalse(Question.objects.filter(pk=rdata["created_ids"][0]).exists())


@override_settings(STATIC_URL="/static/")
class AgentCuttingCliAndMcpTests(LiveServerTestCase):
    host = "127.0.0.1"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        override = override_settings(DATA_ROOT=self.temp / "data")
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict(
            os.environ,
            {
                "TIYOUJU_URL": self.live_server_url,
                "TIYOUJU_AGENT": "codex-test",
                "QB_MINERU_CONFIGURED": "1",
                "QB_MINIMAX_CONFIGURED": "1",
            },
        )
        env.start()
        self.addCleanup(env.stop)

    def test_cli_and_mcp_end_to_end_scan_inspect_cut_layout_and_read_cuts(self):
        pdf_path = self.temp / "scan_book.pdf"
        pdf_path.write_bytes(_make_pdf_bytes([["1. Example A", "2. Exercise B"], ["3. Exercise C"]]))

        # 1. upload --parse-mode native --scan-only
        code, uploaded, err = _run_cli("upload", str(pdf_path), "--book", "--parse-mode", "native", "--scan-only")
        self.assertEqual(code, 0, err)
        paper_id = uploaded["paper"]["id"]
        self.assertTrue(uploaded["paper"]["processing_plan"].get("scan_only"))

        # 2. blocks
        code, blocks_out, err = _run_cli("blocks", paper_id, "--pages", "0")
        self.assertEqual(code, 0, err)
        self.assertGreater(blocks_out["count"], 0)
        self.assertTrue(all(b["page_idx"] == 0 for b in blocks_out["blocks"]))

        # 3. pages --grid
        out_dir = self.temp / "exported_pages"
        code, pages_out, err = _run_cli("pages", paper_id, "--pages", "0-1", "--grid", "--out", str(out_dir))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(pages_out["pages"]), 2)
        for p in pages_out["pages"]:
            self.assertTrue(Path(p["path"]).is_file())

        # 4. cut via JSON file (Track A + Track B)
        cut_json = self.temp / "cuts.json"
        cut_json.write_text(
            json.dumps(
                {
                    "questions": [
                        {
                            "number": 1,
                            "group_title": "第1章",
                            "section": "1.1 例题",
                            "regions": [{"page_idx": 0, "bbox": [50, 50, 900, 350]}],
                            "stem": "已知 $x=1$，求 $x+2$。",
                            "question_type": "fill_blank",
                            "answer": "3",
                        },
                        {
                            "number": 2,
                            "group_title": "第1章",
                            "section": "1.1 练习",
                            "regions": [{"page_idx": 0, "bbox": [50, 400, 900, 800]}],
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        code, cut_out, err = _run_cli("cut", paper_id, "--file", str(cut_json))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(cut_out["created_ids"]), 2)
        q2_id = cut_out["created_ids"][1]

        # 5. layout renumber + undo
        code, lay_out, err = _run_cli("layout", "renumber", paper_id, f"#{q2_id}", "9")
        self.assertEqual(code, 0, err)
        self.assertEqual(Question.objects.get(pk=q2_id).number, 9)

        code, undo_out, err = _run_cli("layout", "undo", paper_id)
        self.assertEqual(code, 0, err)
        self.assertEqual(Question.objects.get(pk=q2_id).number, 2)

        # 6. read-cuts & MCP get_paper_pages attachment cap
        with mock.patch("core.views._vision_ready", return_value=True):
            code, rc_out, err = _run_cli("read-cuts", paper_id, f"#{q2_id}")
        self.assertEqual(code, 0, err)
        self.assertEqual(rc_out["queued_ids"], [q2_id])

        # MCP tools check
        client = cli.Client(agent="mcp-agent")
        res_blocks, imgs = cli.mcp_call(client, "get_paper_blocks", {"paper": paper_id, "pages": "0"})
        self.assertEqual(imgs, [])
        self.assertGreater(res_blocks["count"], 0)

        res_pages, page_imgs = cli.mcp_call(client, "get_paper_pages", {"paper": paper_id, "grid": True})
        self.assertEqual(len(page_imgs), 2)
        self.assertFalse(res_pages["truncated"])
