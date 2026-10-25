"""切图时框边压在字上，那行字不能只剩半截。

识读给的框是照着「图形」画的，可它常常正好落在图注那一行上：八下 #1048、#1034
印出来都是「（第 N 题）」被切成上半行，九上第 205 题那张反比例函数图底部一整行
坐标数字被切掉。切图不留余量就一定会这样。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase, TransactionTestCase, override_settings
from PIL import Image, ImageDraw

from . import imaging, library
from .models import Paper, Question
from .pipeline import paper_dir


def page_with_bars(size=(1000, 1000), bars=((150, 200, 250, 215),)):
    """白底 + 若干条黑条当作一行行字。图注是居中的一小段，比图窄，所以左右两边
    旁边是白的——只有下面那条边会被切到。"""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for left, top, right, bottom in bars:
        draw.rectangle([left, top, right, bottom], fill="black")
    return image


def ink_pixels(image):
    return sum(1 for value in image.convert("L").tobytes() if value < 128)


def border_ink(image):
    """最外面一整圈里有没有墨——只让到边上干净为止，所以这条边上不该再有墨。"""
    gray = image.convert("L")
    strips = [gray.crop((0, 0, gray.width, 1)), gray.crop((0, gray.height - 1, gray.width, gray.height)),
              gray.crop((0, 0, 1, gray.height)), gray.crop((gray.width - 1, 0, gray.width, gray.height))]
    return min(strip.getextrema()[0] for strip in strips)


class CropFigureTests(SimpleTestCase):
    def test_a_box_that_cuts_a_line_in_half_grows_until_the_whole_line_is_inside(self):
        page = page_with_bars()
        box = (100, 100, 300, 201)            # 下沿正好压在 y=200..215 这条黑条中间
        cropped = imaging.crop_figure(page, box)

        self.assertGreater(cropped.height, box[3] - box[1])
        # 切下来的图边上不能再有墨：那条黑条整个进来了，边上留的是白。
        self.assertGreaterEqual(border_ink(cropped), imaging.INK_MAX)
        # 而且不能只进来一半——黑条 101x16 个像素必须一个不少。
        self.assertEqual(ink_pixels(cropped), 101 * 16)

    def test_a_box_already_clear_of_ink_is_left_exactly_where_it_is(self):
        page = page_with_bars()
        box = (100, 400, 300, 600)            # 框和黑条之间有一截白
        cropped = imaging.crop_figure(page, box)
        self.assertEqual(cropped.size, (box[2] - box[0], box[3] - box[1]))

    def test_only_the_edge_that_cuts_grows(self):
        page = page_with_bars()
        box = (100, 100, 300, 201)
        cropped = imaging.crop_figure(page, box)
        self.assertEqual(cropped.size, (200, cropped.size[1]))
        # 左边没墨，就一列都不该让出去。
        self.assertEqual(cropped.convert("L").getpixel((0, 0)), 255)

    def test_growth_stops_at_the_slack_instead_of_running_to_the_page_edge(self):
        page = page_with_bars(bars=((100, 300, 900, 800),))
        box = (100, 100, 900, 301)
        slack = 56
        cropped = imaging.crop_figure(page, box, slack=slack)
        self.assertEqual(cropped.height - (box[3] - box[1]), slack)

    def test_a_box_on_the_page_edge_does_not_grow_past_the_page(self):
        page = page_with_bars()
        box = (0, 100, 300, 201)               # 左边已经在纸上
        cropped = imaging.crop_figure(page, box)
        self.assertEqual(cropped.width, 300)


class FigureFileUsesTheRelaxedCropTests(TransactionTestCase):
    """入库时的切图要走同一条路，不然卡上看图正常、印出来还是切了字的。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temp.cleanup)
        override = override_settings(DATA_ROOT=Path(temp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.paper = Paper.objects.create(
            filename="k3.pdf", kind="pdf", sha256="d" * 64,
            source_path=str(Path(temp.name) / "k3.pdf"), render_path=str(Path(temp.name) / "k3.pdf"),
            pages=[{"page_idx": 0, "width": 1000, "height": 1000}], status=Paper.Status.READY,
        )
        pages = paper_dir(self.paper) / "pages"
        pages.mkdir(parents=True)
        page_with_bars().save(pages / "page_0.png")
        self.question = Question.objects.create(
            paper=self.paper, number=7, question_type="free_response",
            figures=[{"page_idx": 0, "bbox": [100, 100, 300, 201], "slot": "stem"}],
            regions=[{"page_idx": 0, "bbox": [50, 50, 500, 500]}],
            stem="如图", state=Question.State.GREEN,
        )

    def test_the_cropped_figure_file_keeps_the_line_the_box_was_sitting_on(self):
        with Image.open(library.figure_file(self.question, 0)) as cropped:
            self.assertGreater(cropped.size[1], 101)
            self.assertGreaterEqual(border_ink(cropped), imaging.INK_MAX)
            self.assertEqual(sum(1 for v in cropped.convert("L").tobytes() if v < 128), 101 * 16)

    def test_the_served_figure_is_the_relaxed_crop(self):
        self.question.figures = [
            {"page_idx": 0, "bbox": [100, 100, 300, 201], "slot": "stem", "source": "manual"}]
        self.question.save()
        response = self.client.post(
            f"/api/questions/{self.question.pk}/figures",
            data=json.dumps({"figures": [self.question.figures[0]], "ignored_candidates": []}),
            content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200, response.content)
        url = response.json()["question"]["figures"][0]["url"]
        served = self.client.get(url)
        with Image.open(__import__("io").BytesIO(b"".join(served.streaming_content))) as image:
            self.assertGreater(image.size[1], 101)
