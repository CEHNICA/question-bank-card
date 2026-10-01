"""AI-assistant reading: no vision key, cards start as MinerU's own text."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings

from . import pipeline
from .models import Block, Paper, Question

REGION = [{"page_idx": 0, "bbox": [0, 0, 1000, 600]}]


class MineruDraftTests(TestCase):
    def test_prose_formulas_and_tables_in_reading_order(self):
        blocks = [
            {"seq": 3, "type": "table", "page_idx": 0, "bbox": [100, 300, 900, 400], "text": ""},
            {"seq": 1, "type": "text", "page_idx": 0, "bbox": [100, 100, 900, 150], "text": "9. 计算下列各式："},
            {"seq": 2, "type": "equation", "page_idx": 0, "bbox": [100, 160, 900, 200],
             "text": "$$\n\\frac {1}{2} + x\n$$"},
            {"seq": 4, "type": "image", "page_idx": 0, "bbox": [100, 420, 300, 500], "text": ""},
            {"seq": 5, "type": "header", "page_idx": 0, "bbox": [0, 0, 1000, 30], "text": "期中考试"},
            {"seq": 6, "type": "text", "page_idx": 0, "bbox": [100, 700, 900, 750], "text": "10. 下一题"},
        ]
        html = {3: "<table><tr><td>甲</td><td>乙</td></tr><tr><td>1</td><td>2</td></tr></table>"}
        draft = pipeline.mineru_draft(blocks, html, REGION)
        lines = draft.split("\n")
        self.assertEqual(lines[0], "9. 计算下列各式：")
        self.assertEqual(lines[1], "$\\frac {1}{2} + x$")
        self.assertIn("| 甲 | 乙 |", draft)
        self.assertNotIn("期中考试", draft)
        self.assertNotIn("下一题", draft)

    def test_lines_follow_the_page_not_minerus_order(self):
        # 31. 如图… / (1) … / (2) …, but MinerU numbered (1) first; options side by side.
        blocks = [
            {"seq": 1, "type": "text", "page_idx": 0, "bbox": [100, 160, 600, 190], "text": "(1) 求面积;"},
            {"seq": 2, "type": "text", "page_idx": 0, "bbox": [80, 100, 700, 130], "text": "31. 如图所示，已知 A(0,1)."},
            {"seq": 3, "type": "text", "page_idx": 0, "bbox": [100, 220, 900, 250], "text": "(2) 求点 P."},
            {"seq": 5, "type": "text", "page_idx": 0, "bbox": [500, 271, 700, 300], "text": "B. 2"},
            {"seq": 4, "type": "text", "page_idx": 0, "bbox": [100, 270, 300, 302], "text": "A. 1"},
        ]
        draft = pipeline.mineru_draft(blocks, {}, REGION)
        self.assertEqual(draft.split("\n"),
                         ["31. 如图所示，已知 A(0,1).", "(1) 求面积;", "(2) 求点 P.", "A. 1", "B. 2"])
        # A card spread over two regions reads the first region first.
        two = [{"page_idx": 1, "bbox": [0, 0, 1000, 300]}, {"page_idx": 0, "bbox": [0, 700, 1000, 1000]}]
        pages = [{"seq": 1, "type": "text", "page_idx": 0, "bbox": [100, 800, 900, 830], "text": "第二段"},
                 {"seq": 9, "type": "text", "page_idx": 1, "bbox": [100, 50, 900, 80], "text": "第一段"}]
        self.assertEqual(pipeline.mineru_draft(pages, {}, two), "第一段\n第二段")

    def test_the_draft_card_is_yellow_with_options_split_off(self):
        card = pipeline.assistant_draft({
            "number": 9, "question_type": "unknown", "segmentation_flags": [],
            "candidates": [{"label": "1", "page_idx": 0, "bbox": [600, 300, 900, 500]}],
            "draft": "9．如图，数轴上点 P 表示的数为 -2，向右移动 5 个单位后表示（ ）\nA. -7 B. -3 C. 3 D. 7",
        })
        self.assertTrue(card["stem"].startswith("如图，数轴上点 P"))
        self.assertEqual(card["options"], {"A": "-7", "B": "-3", "C": "3", "D": "7"})
        self.assertEqual((card["question_type"], card["text_source"], card["state"]),
                         ("single_choice", "mineru", Question.State.YELLOW))
        self.assertIn(pipeline.FLAG_MINERU_DRAFT, card["flags"])
        # “如图” and exactly one picture in the crop: it is the stem's figure.
        self.assertEqual([(figure["slot"], figure["source"]) for figure in card["figures"]], [("stem", "auto")])
        self.assertEqual(card["figure_review"]["status"], "ok")
        # Two pictures, or an option printed as a picture: left for the reviewer.
        two = pipeline.assistant_draft({
            "number": 9, "draft": "9. 如图，求 x 的值。",
            "candidates": [{"label": "1", "page_idx": 0, "bbox": [600, 300, 900, 500]},
                           {"label": "2", "page_idx": 0, "bbox": [100, 300, 400, 500]}],
        })
        self.assertEqual(two["figures"], [])
        self.assertEqual(two["figure_review"]["status"], "blocked_missing")
        pictured = pipeline.assistant_draft({
            "number": 9, "draft": "9. 如图，哪个是正方体的展开图（ ）\nA. B. C. D.",
            "candidates": [{"label": "1", "page_idx": 0, "bbox": [600, 300, 900, 500]}],
        })
        self.assertEqual(pictured["figures"], [])
        empty = pipeline.assistant_draft({"number": 3, "draft": "", "candidates": []})
        self.assertEqual(empty["stem"], "")
        self.assertIn(pipeline.FLAG_MINERU_DRAFT_EMPTY, empty["flags"])


class AssistantModeReadTests(TestCase):
    def test_reading_a_paper_calls_no_vision_model(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder, \
                override_settings(DATA_ROOT=Path(folder)), \
                mock.patch.dict("os.environ", {"QB_PRIMARY_ENGINE": "assistant", "QB_MINIMAX_CONFIGURED": "1"}), \
                mock.patch.object(pipeline.readers, "chat", side_effect=AssertionError("no vision call")):
            paper = Paper.objects.create(
                filename="a.pdf", kind="pdf", sha256="d" * 64, source_path=str(Path(folder) / "a.pdf"),
                render_path=str(Path(folder) / "a.pdf"), pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
                status=Paper.Status.READING,
            )
            Block.objects.create(paper=paper, seq=1, type="text", page_idx=0, bbox=[100, 100, 900, 150],
                                 text="1. 计算 $1+1$ 的值。")
            question = Question.objects.create(paper=paper, number=1, regions=REGION, state=Question.State.WAITING)
            pipeline.read_questions(paper, [question])
            question.refresh_from_db()
        self.assertEqual(question.stem, "计算 $1+1$ 的值。")
        self.assertEqual((question.state, question.text_source), (Question.State.YELLOW, "mineru"))
        self.assertTrue(question.read_a.get("draft"))


class DraftEditTests(TestCase):
    def test_saving_the_text_clears_the_draft_reminder_but_not_the_range_one(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder, \
                override_settings(DATA_ROOT=Path(folder)):
            paper = Paper.objects.create(
                filename="d.pdf", kind="pdf", sha256="e" * 64, source_path=str(Path(folder) / "d.pdf"),
                render_path=str(Path(folder) / "d.pdf"), pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
                status=Paper.Status.READY,
            )
            range_flag = "截图里还露出了第 2 题，范围可能需要调整"
            question = Question.objects.create(
                paper=paper, number=1, question_type="free_response", state=Question.State.YELLOW,
                stem="计算 1+1 的值", text_source="mineru", regions=REGION,
                flags=[pipeline.FLAG_MINERU_DRAFT, range_flag],
            )
            card = self.client.post(
                f"/api/questions/{question.pk}/text",
                data=json.dumps({"stem": "计算 $1+1$ 的值。", "options": {}, "question_type": "free_response",
                                 "by": "ai", "agent": "豆包"}),
                content_type="application/json", HTTP_X_QB_REQUEST="1",
            ).json()["question"]
        self.assertEqual(card["flags"], [range_flag])
        self.assertEqual(card["text_source"], "assistant")


class ReadersDownTests(TestCase):
    def test_a_card_no_service_could_read_starts_as_minerus_draft(self):
        from PIL import Image

        from .pipeline import paper_dir
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder, \
                override_settings(DATA_ROOT=Path(folder)), \
                mock.patch.dict("os.environ", {"QB_PRIMARY_ENGINE": "modelscope_qwen", "QB_CHECKER_ENGINE": "auto",
                                               "QB_MODELSCOPE_CONFIGURED": "1", "QB_MINIMAX_CONFIGURED": "0",
                                               "QB_SILICONFLOW_CONFIGURED": "0"}), \
                mock.patch.object(pipeline.readers, "read_question",
                                  side_effect=pipeline.readers.ReaderUnavailable("魔搭 接口持续限流，已自动等待并重试")):
            paper = Paper.objects.create(
                filename="b.pdf", kind="pdf", sha256="f" * 64, source_path=str(Path(folder) / "b.pdf"),
                render_path=str(Path(folder) / "b.pdf"), pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
                status=Paper.Status.READING,
            )
            pages = paper_dir(paper) / "pages"
            pages.mkdir(parents=True)
            Image.new("RGB", (1000, 1000), "white").save(pages / "page_0.png")
            Block.objects.create(paper=paper, seq=1, type="text", page_idx=0, bbox=[100, 100, 900, 150],
                                 text="1. 计算 $1+1$ 的值。")
            question = Question.objects.create(paper=paper, number=1, regions=REGION, state=Question.State.WAITING)
            pipeline.read_questions(paper, [question])
            question.refresh_from_db()
        self.assertEqual((question.state, question.text_source, question.stem),
                         (Question.State.YELLOW, "mineru", "计算 $1+1$ 的值。"))
        self.assertIn(pipeline.FLAG_READERS_DOWN, question.flags)
        self.assertIn("持续限流", question.read_a.get("error", ""))

    def test_a_card_without_a_crop_is_still_red(self):
        snapshot = {"number": 1, "regions": [], "candidates": [], "fallback_draft": "1. 题"}
        with mock.patch.dict("os.environ", {"QB_MODELSCOPE_CONFIGURED": "1", "QB_PRIMARY_ENGINE": "modelscope_qwen"}):
            card = pipeline.read_card(snapshot, store=None)
        self.assertEqual(card["state"], Question.State.RED)      # nothing to read: not a service problem
