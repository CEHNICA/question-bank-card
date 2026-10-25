"""A figure box that cannot be cropped answers 409, not 500.

三张图接口伺服的都是「从原卷裁一张图」这件事，但只有 ``question_image`` 兜住了
裁不出来的情况。``question_figure`` 用题卡上存的坐标，``question_ocr_figure`` 用的
还是模型给的坐标（没过 _valid_bbox）——两者都没有 try/except，于是坐标一坏，
用户看到的是一张 500 白页，而不是隔壁那个接口给的「原卷裁片无法生成」。

坐标为什么真的会坏：模型给的框可能小于一个像素，页面换过序之后旧坐标可能指向
不存在的页，缓存也可能被清掉。这三样都不是假设。
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import fitz
from django.test import TestCase, override_settings

from .models import Paper, Question


def original_pdf(pages=2):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)
        page.draw_rect(fitz.Rect(50, 90, 470, 330), color=(1, 0, 0), fill=(index / 3, .3, .7))
    data = document.tobytes()
    document.close()
    return data


class FigureEndpointTests(TestCase):
    def setUp(self):
        # FileResponse keeps the PNG open for streaming, so Windows refuses to
        # remove the temp tree until the response is closed.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        setting = override_settings(DATA_ROOT=Path(self.tmp.name))
        setting.enable()
        self.addCleanup(setting.disable)
        data = original_pdf()
        self.paper = Paper.objects.create(
            filename="crops.pdf", kind="pdf", sha256=hashlib.sha256(data).hexdigest(),
            status=Paper.Status.READY, processing_plan={"schema": 1, "revision": 0})
        folder = Path(self.tmp.name) / str(self.paper.pk)
        folder.mkdir(parents=True)
        source = folder / "source.pdf"
        source.write_bytes(data)
        self.paper.source_path = str(source)
        self.paper.pages = [{"page_idx": index, "width": 595, "height": 842} for index in range(2)]
        self.paper.save(update_fields=["source_path", "pages"])

    def card(self, **extra):
        values = {"paper": self.paper, "number": 1, "body_mode": "text", "processing_mode": "auto",
                      "question_type": "single_choice", "state": "yellow",
                      "regions": [{"page_idx": 0, "bbox": [50, 50, 900, 420]}],
                      "figures": [{"page_idx": 0, "bbox": [100, 200, 450, 500],
                                "slot": "stem", "source": "auto"}], "flags": []}
        values.update(extra)
        return Question.objects.create(**values)

    def test_a_box_that_collapses_to_nothing_answers_409(self):
        # 宽和高都不足一个像素：裁图函数抛 ValueError。
        question = self.card(figures=[{"page_idx": 0, "bbox": [100, 200, 100, 200],
                                       "slot": "stem", "source": "auto"}])
        response = self.client.get(f"/api/questions/{question.pk}/figures/0")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("原卷裁片无法生成", response.json()["error"])

    def test_a_box_written_wrongly_answers_409(self):
        # 模型给的坐标没过 _valid_bbox：坐标是 null、维度不够、坐标是字符串。
        for bbox in (None, [0, 0, 100], "0,0,100,100", [0, 0, 100, None]):
            with self.subTest(bbox=bbox):
                question = self.card(figures=[{"page_idx": 0, "bbox": bbox,
                                               "slot": "stem", "source": "auto"}])
                response = self.client.get(f"/api/questions/{question.pk}/figures/0")
                self.assertEqual(response.status_code, 409, response.content)

    def test_a_page_that_no_longer_exists_answers_409(self):
        # 页面换过序之后，旧坐标指向的页可能已经不存在了。
        question = self.card(figures=[{"page_idx": 99, "bbox": [100, 200, 450, 500],
                                       "slot": "stem", "source": "auto"}])
        response = self.client.get(f"/api/questions/{question.pk}/figures/0")
        self.assertEqual(response.status_code, 409, response.content)

    def test_an_uncroppable_ocr_suggestion_answers_409(self):
        # ocr-figures 用的是模型给的坐标，从来没有校验过，所以这条更要兜住。
        question = self.card(
            ocr_pending=True,
            ocr_suggestion={"revision": 0, "figures": [
                {"page_idx": 0, "bbox": [100, 200, 100, 200], "slot": "stem", "source": "auto"}]})
        response = self.client.get(f"/api/questions/{question.pk}/ocr-figures/0")
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn("原卷裁片无法生成", response.json()["error"])

    def test_a_good_box_still_serves_the_picture(self):
        question = self.card()
        response = self.client.get(f"/api/questions/{question.pk}/figures/0")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))

    def test_a_good_ocr_suggestion_still_serves_the_picture(self):
        question = self.card(
            ocr_pending=True,
            ocr_suggestion={"revision": 0, "figures": [
                {"page_idx": 0, "bbox": [100, 200, 450, 500], "slot": "stem", "source": "auto"}]})
        response = self.client.get(f"/api/questions/{question.pk}/ocr-figures/0")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))
