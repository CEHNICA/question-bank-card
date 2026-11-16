"""题库工作台的资料清单不需要胖 JSON，也不该逐卡算判定。

``/api/papers`` 每份资料都跑一遍 ``card_verdicts``：把该资料所有题卡的 JSON
字段拉出来、算指纹、逐张判状态。Eagle 题库工作台只要 id / 名称 / 已入库数 /
总题数四样东西，却为这四样付了整套判定的钱——13 份资料里有一份 509 题的教材，
光这一项就吃掉首屏近 0.4 秒。

``?slim=1`` 给只读侧栏用：口径必须和完整模式一模一样（total/published 逐份相等），
但不算判定、不带 pages/structure/processing_plan 这些胖字段。
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase, override_settings

from . import views
from .models import Paper, PublishedQuestion, Question


class SlimPapersTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="slim.pdf",
            kind="pdf",
            sha256="b" * 64,
            source_path=str(Path(temp.name) / "slim.pdf"),
            render_path=str(Path(temp.name) / "slim.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}],
            status=Paper.Status.READY,
            # 一份胖资料：结构、页表、处理计划都是几百 KB 的 JSON
            structure={"sections": [{"title": f"第{i}章", "pages": [0, 1]} for i in range(400)]},
            processing_plan={"steps": [{"name": f"步骤{i}", "detail": "x" * 200} for i in range(300)]},
            photos={"files": [{"name": f"page_{i}.png"} for i in range(50)], "order": list(range(50))},
        )
        self.cards = [
            Question.objects.create(
                paper=self.paper,
                number=index,
                question_type="free_response",
                state=Question.State.GREEN,
                stem=f"第{index}题 $x+{index}=2$，求 $x$。",
                flags=[],
                regions=[{"page_idx": 0, "bbox": [50, 50 + index * 100, 950, 140 + index * 100]}],
            )
            for index in range(1, 4)
        ]
        self.published = PublishedQuestion.objects.create(
            id=uuid.uuid4(),
            question=self.cards[0],
            paper=self.paper,
            source_filename="slim.pdf",
            number=1,
            question_type="free_response",
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
            content={"stem": self.cards[0].stem},
            content_hash="d" * 64,
        )
        self.client = Client()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_slim_counts_are_identical_to_the_full_payload(self):
        full = {p["id"]: p for p in self.get("/api/papers")["papers"]}[str(self.paper.id)]
        slim = {p["id"]: p for p in self.get("/api/papers?slim=1")["papers"]}[str(self.paper.id)]
        self.assertEqual(slim["counts"]["total"], full["counts"]["total"])
        self.assertEqual(slim["counts"]["published"], full["counts"]["published"])
        self.assertEqual(slim["name"], full["name"])
        self.assertEqual(slim["material_type"], full["material_type"])
        self.assertEqual(slim["archived"], full["archived"])
        self.assertEqual(slim["created_at"], full["created_at"])

    def test_slim_payload_drops_the_fat_fields_but_keeps_what_eagle_reads(self):
        slim = {p["id"]: p for p in self.get("/api/papers?slim=1")["papers"]}[str(self.paper.id)]
        for heavy in ("pages", "structure", "processing_plan", "question_groups", "photos", "notes"):
            self.assertNotIn(heavy, slim, f"slim 不该带 {heavy}")
        for needed in ("id", "name", "filename", "material_type", "archived", "created_at", "counts"):
            self.assertIn(needed, slim)
        full = {p["id"]: p for p in self.get("/api/papers")["papers"]}[str(self.paper.id)]
        for heavy in ("pages", "structure", "processing_plan", "question_groups"):
            self.assertIn(heavy, full)

    def test_slim_never_computes_the_per_card_verdicts(self):
        with mock.patch.object(views, "card_verdicts", side_effect=AssertionError("不该算逐卡判定")):
            payload = self.get("/api/papers?slim=1")
            self.assertEqual(len(payload["papers"]), 1)
            with self.assertRaises(AssertionError):
                self.get("/api/papers")

    def test_slim_response_is_far_smaller_than_the_full_one(self):
        full_len = len(json.dumps(self.get("/api/papers"), ensure_ascii=False))
        slim_len = len(json.dumps(self.get("/api/papers?slim=1"), ensure_ascii=False))
        self.assertLess(slim_len, full_len / 2, "slim 模式的意义就是别把胖 JSON 传出去")
