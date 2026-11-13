"""测试「复制题目图片」的体验升级：
1. combined 完整带图模式（发微信防漏图）；
2. 题目指纹缓存秒级直出；
3. 四周内边距（Padding）视觉留白。
"""

import io
import os
from copy import deepcopy
from pathlib import Path
from unittest import mock

from django.test import TestCase
from PIL import Image

from . import library_clipboard
from .models import Paper, PublishedQuestion, Question


class ClipboardEnhancementTests(TestCase):
    def setUp(self):
        self.paper = Paper.objects.create(filename="缓存测试卷.pdf", kind="pdf", sha256="d" * 64)
        self.question = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem="测试题", state="green"
        )
        self.pub = PublishedQuestion.objects.create(
            paper=self.paper,
            question=self.question,
            source_filename=self.paper.filename,
            number=1,
            question_type="single_choice",
            content={
                "stem": "计算三角形面积，如图所示。",
                "options": {"A": "1", "B": "2"},
                "figures": [{"slot": "stem", "file": "figure-1.png"}],
                "origin": "2026·期末",
            },
            content_hash="c" * 64,
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
        )
        # 写一张测试配图到题库目录
        from django.conf import settings

        folder = Path(settings.DATA_ROOT) / "library" / str(self.pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        img = Image.new("RGB", (20, 20), (100, 150, 200))
        img.save(folder / "figure-1.png", format="PNG")

    def test_combined_mode_embeds_figures_in_payload(self):
        """combined 模式：配图直接打包进 payload['figures']，不返回分离图。"""
        payload, figures = library_clipboard._collect(self.pub, mode="combined")
        self.assertEqual(figures, [], "combined 模式下配图不单独分离")
        self.assertEqual(len(payload["figures"]), 1, "配图应包含在题面排版 payload 中")
        self.assertTrue(payload["figures"][0]["file"].startswith("data:image/png;base64,"))
        self.assertEqual(payload["figures"][0]["slot"], "stem")

    def test_separated_mode_keeps_figures_separate(self):
        """separated 模式：保持原有题面与配图分离逻辑。"""
        payload, figures = library_clipboard._collect(self.pub, mode="separated")
        self.assertEqual(len(figures), 1, "separated 模式下应返回独立配图")
        self.assertEqual(payload["figures"], [], "题面 payload 中不含配图")

    def test_cache_hits_on_second_call_without_rendering(self):
        """首次排版落盘缓存，二次调用直接命中缓存，无需调用 _render_question_png。"""
        with (
            mock.patch.object(library_clipboard, "_render_question_png", return_value=b"fake-rendered-png") as render,
            mock.patch.object(library_clipboard, "_write_clipboard") as write,
        ):
            # 第一遍：未命中缓存，调用渲染
            res1 = library_clipboard.copy_question(self.pub, mode="combined")
            self.assertEqual(render.call_count, 1)
            self.assertFalse(res1.get("cached"))
            self.assertIn("题目 1 张", res1["message"])

            # 第二遍：直接命中缓存
            res2 = library_clipboard.copy_question(self.pub, mode="combined")
            self.assertEqual(render.call_count, 1, "命中缓存时不应再次调用 _render_question_png")
            self.assertTrue(res2.get("cached"))
            self.assertIn("已秒级复用", res2["message"])

    def test_crop_to_content_adds_padding(self):
        """_crop_to_content 应在裁掉多余白边后加上四周 20px padding。"""
        # 制作一张中心有黑色矩形、四周全白的图片
        raw_image = Image.new("RGB", (100, 100), (255, 255, 255))
        # 在 (30, 30) 到 (50, 50) 绘制内容（宽 20，高 20）
        for x in range(30, 50):
            for y in range(30, 50):
                raw_image.putpixel((x, y), (0, 0, 0))
        buffer = io.BytesIO()
        raw_image.save(buffer, format="PNG")
        raw_bytes = buffer.getvalue()

        # box 涵盖整张图（假定 scale=2，box 坐标对应原图）
        box = {"x": 0, "y": 0, "w": 50, "h": 50}
        cropped_bytes = library_clipboard._crop_to_content(raw_bytes, box)

        with Image.open(io.BytesIO(cropped_bytes)) as cropped:
            # 黑色区域尺寸为 20x20，左右加 padding_x=56，上下加 padding_y=42，因此总尺寸应为 (132, 104)
            self.assertEqual(cropped.size, (132, 104), "加上舒展页边距后总尺寸应为 132x104")
