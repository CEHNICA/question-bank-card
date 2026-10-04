"""照片卷：初步顺序、按题号排页序、拉正、扫描件效果、多图上传与调整页序（离线，不调用任何模型）。"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

from django.test import Client, SimpleTestCase, TestCase, override_settings
from PIL import Image, ImageDraw, ImageStat

from . import photos, pipeline
from .models import Paper, Question, QuestionGroup


def jpeg_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def marked_page(bars: int, size=(1400, 1000)) -> Image.Image:
    """白纸上画 bars 条黑杠：用来认出合成 PDF 里的每一页是哪张照片。"""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for index in range(bars):
        draw.rectangle((100, 100 + index * 120, 1300, 160 + index * 120), fill="black")
    return image


def dark_bars(page: Image.Image) -> int:
    """数页面左侧一列里有几段黑杠。"""
    column = page.convert("L").resize((50, 200)).crop((10, 0, 11, 200))
    values = list(column.getdata())
    return sum(1 for a, b in zip([255] + values, values) if a >= 128 > b)


def text_page() -> Image.Image:
    image = Image.new("RGB", (1200, 1700), "white")
    draw = ImageDraw.Draw(image)
    for row in range(22):
        draw.rectangle((120, 120 + row * 65, 1080, 140 + row * 65), fill=(20, 20, 20))
    return image


def on_desk(page: Image.Image, quad, size=(1800, 2400)) -> Image.Image:
    """把纸按 quad（左上、右上、右下、左下）斜着放到深色桌面上。"""
    w, h = page.size
    rows, values = [], []
    for (x, y), (u, v) in zip(quad, [(0, 0), (w, 0), (w, h), (0, h)]):
        rows += [[x, y, 1, 0, 0, 0, -x * u, -y * u], [0, 0, 0, x, y, 1, -x * v, -y * v]]
        values += [u, v]
    coefficients = photos._solve(rows, values)
    warped = page.transform(size, Image.Transform.PERSPECTIVE, coefficients, Image.Resampling.BICUBIC)
    mask = Image.new("L", page.size, 255).transform(size, Image.Transform.PERSPECTIVE, coefficients)
    desk = Image.new("RGB", size, (110, 80, 55))
    desk.paste(warped, (0, 0), mask)
    return desk


class OrderingTests(SimpleTestCase):
    def test_initial_order_prefers_capture_time_then_filename(self):
        files = [{"name": "b.jpg", "taken": "2025:10:28 07:02:00"}, {"name": "a.jpg", "taken": "2025:10:28 07:01:00"}]
        self.assertEqual(photos.initial_order(files), ([1, 0], "拍摄时间"))
        files = [{"name": "IMG_10.jpg", "taken": ""}, {"name": "IMG_9.jpg", "taken": ""}]
        self.assertEqual(photos.initial_order(files), ([1, 0], "文件名"))
        files = [{"name": "第二页.jpg", "taken": ""}, {"name": "首页.jpg", "taken": ""}]
        self.assertEqual(photos.initial_order(files), ([0, 1], "选择顺序"))

    def test_arrange_by_question_numbers(self):
        ranges = {0: (14, 20), 1: (1, 13), 2: (21, 25)}
        self.assertEqual(photos.arrange(ranges, [0, 1, 2]), ([1, 0, 2], ""))

    def test_page_without_numbers_stays_after_its_neighbour_and_asks(self):
        # 初步顺序：第 0 页（1–10 题）、第 2 页（无题号，长解答题的后半截）、第 1 页（11–15 题）
        ranges = {0: (11, 15), 1: (1, 10), 2: None}
        order, check = photos.arrange(ranges, [1, 2, 0])
        self.assertEqual(order, [1, 2, 0])
        self.assertIn("没找到题号", check)

    def test_repeated_numbers_keep_initial_order_and_ask(self):
        ranges = {0: (1, 12), 1: (1, 10)}
        order, check = photos.arrange(ranges, [0, 1])
        self.assertEqual(order, [0, 1])
        self.assertIn("重复", check)

    def test_page_ranges_read_each_page_on_its_own(self):
        blocks = [
            {"seq": 0, "type": "text", "page_idx": 0, "bbox": [60, 100, 900, 140], "text": "14. 如图，已知 AB=CD"},
            {"seq": 1, "type": "text", "page_idx": 0, "bbox": [60, 400, 900, 440], "text": "15. 已知函数 f(x)"},
            {"seq": 2, "type": "text", "page_idx": 1, "bbox": [60, 100, 900, 140], "text": "一、选择题"},
            {"seq": 3, "type": "text", "page_idx": 1, "bbox": [60, 200, 900, 240], "text": "1. 下列各数中"},
            {"seq": 4, "type": "text", "page_idx": 1, "bbox": [60, 500, 900, 540], "text": "2. 如图，在三角形中"},
            {"seq": 5, "type": "text", "page_idx": 2, "bbox": [60, 100, 900, 140], "text": "(2) 求证：四边形是菱形"},
        ]
        pages = [{"page_idx": i, "width": 595, "height": 842} for i in range(3)]
        self.assertEqual(photos.page_ranges(pages, blocks), {0: (14, 15), 1: (1, 2), 2: None})


class ImageTests(SimpleTestCase):
    def test_tilted_sheet_on_desk_is_found_and_straightened(self):
        quad = [(300, 380), (1480, 300), (1600, 2050), (220, 2120)]
        photo = on_desk(text_page(), quad)
        found = photos.find_page_quad(photo)
        self.assertIsNotNone(found)
        for (x, y), (fx, fy) in zip(quad, found):
            self.assertLess(abs(x - fx), 60)
            self.assertLess(abs(y - fy), 60)
        flat = photos.straighten(photo, found)
        self.assertAlmostEqual(flat.height / flat.width, 2 ** 0.5, places=2)   # 试卷纸按 √2 还原
        corner = flat.convert("L").crop((0, 0, flat.width // 12, flat.height // 12))
        self.assertGreater(ImageStat.Stat(corner).mean[0], 200)                # 角上是纸，不是桌面

    def test_not_straightened_when_sheet_fills_frame_or_is_cut_off(self):
        self.assertIsNone(photos.find_page_quad(text_page()))
        cut = on_desk(text_page(), [(200, -200), (1600, -150), (1650, 2100), (150, 2150)])
        self.assertIsNone(photos.find_page_quad(cut))

    def test_enhance_brightens_dark_uneven_paper_and_keeps_text(self):
        page = text_page().convert("L")
        shade = Image.linear_gradient("L").resize(page.size).point(lambda v: 90 + v // 3)   # 发暗且一边更暗
        dark = Image.composite(page, Image.new("L", page.size, 0), shade)
        clean = photos.enhance(dark)
        paper = clean.crop((20, 20, 100, 1650))              # 左边空白处（原来很暗）
        text = clean.crop((300, 125, 900, 135))              # 一行"字"
        self.assertGreater(ImageStat.Stat(paper).mean[0], 240)
        self.assertLess(ImageStat.Stat(text).mean[0], 60)


def mineru_zip(path: Path, blocks: list[dict]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("x_content_list.json", json.dumps(blocks, ensure_ascii=False))


class PhotoPaperTests(TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        override = override_settings(DATA_ROOT=self.temp)
        override.enable()
        self.addCleanup(override.disable)
        env = mock.patch.dict("os.environ", {"MINERU_TOKEN": "t", "MINIMAX_API_KEY": "k"})
        env.start()
        self.addCleanup(env.stop)
        self.client = Client()

    def upload(self, files: dict[str, bytes], **extra):
        uploads = []
        for name, data in files.items():
            item = io.BytesIO(data)
            item.name = name
            uploads.append(item)
        return self.client.post("/api/papers", {"file": uploads, **extra}, HTTP_X_QB_REQUEST="1")

    def test_several_photos_become_one_paper(self):
        response = self.upload({"IMG_2.jpg": jpeg_bytes(marked_page(2)), "IMG_1.jpg": jpeg_bytes(marked_page(1))})
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get()
        self.assertEqual(paper.kind, "image")
        self.assertEqual(paper.filename, "IMG_2 等 2 张照片")
        self.assertEqual([f["name"] for f in paper.photos["files"]], ["IMG_2.jpg", "IMG_1.jpg"])
        self.assertEqual(paper.photos["order"], [1, 0])          # 文件名粗排
        self.assertTrue(paper.photos["enhance"])
        self.assertEqual(response.json()["paper"]["photos"]["names"], ["IMG_1.jpg", "IMG_2.jpg"])
        # 同一组照片换个顺序再传：认出是同一份卷
        again = self.upload({"IMG_1.jpg": jpeg_bytes(marked_page(1)), "IMG_2.jpg": jpeg_bytes(marked_page(2))})
        self.assertTrue(again.json().get("duplicate"))
        # 不做扫描件效果：算另一份卷
        plain = self.upload({"IMG_1.jpg": jpeg_bytes(marked_page(1)), "IMG_2.jpg": jpeg_bytes(marked_page(2))}, enhance="0")
        self.assertEqual(plain.status_code, 201)
        self.assertFalse(Paper.objects.get(pk=plain.json()["paper"]["id"]).photos["enhance"])

    def test_archiving_photo_pages_does_not_license_a_second_task_for_the_same_photos(self):
        # 1.12.6：归档过的照片卷再传一次同样这组照片，不该多出一份同名任务。
        batch = {"IMG_1.jpg": jpeg_bytes(marked_page(1)), "IMG_2.jpg": jpeg_bytes(marked_page(2))}
        first = self.upload(batch)
        self.assertEqual(first.status_code, 201, first.content)
        archived = Paper.objects.get()
        archived.archived = True
        archived.save(update_fields=["archived", "updated_at"])
        again = self.upload(batch)
        self.assertTrue(again.json().get("duplicate"))
        self.assertTrue(again.json().get("archived"))
        self.assertEqual(Paper.objects.count(), 1)
        forced = self.upload(batch, force="1")
        self.assertEqual(forced.status_code, 201, forced.content)
        self.assertEqual(Paper.objects.count(), 2)
        self.assertNotEqual(Paper.objects.get(pk=forced.json()["paper"]["id"]).display_name,
            archived.display_name, "两份来源必须能在题库里分开")

    def test_mixing_pdf_and_photos_or_bad_image_is_rejected(self):
        response = self.upload({"a.jpg": jpeg_bytes(marked_page(1)), "b.pdf": b"%PDF-1.4"})
        self.assertEqual(response.status_code, 400)
        response = self.upload({"a.jpg": b"not an image"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Paper.objects.exists())

    def test_parse_orders_pages_by_question_numbers_then_manual_reorder(self):
        # 三张照片按文件名粗排成 A、B、C，但卷面题号说明正确顺序是 B（1–5）、C（6–9）、A（10–12）。
        pictures = {"A.jpg": marked_page(1), "B.jpg": marked_page(2), "C.jpg": marked_page(3)}
        response = self.upload({name: jpeg_bytes(image) for name, image in pictures.items()},
            enhance="0", parse_mode="mineru")
        paper = Paper.objects.get(pk=response.json()["paper"]["id"])
        numbers = {"A.jpg": (10, 12), "B.jpg": (1, 5), "C.jpg": (6, 9)}
        blocks, seq = [], 0
        for page, index in enumerate(paper.photos["order"]):          # MinerU 看到的页序 = 粗排顺序
            low, high = numbers[paper.photos["files"][index]["name"]]
            for offset, number in enumerate(range(low, high + 1)):
                blocks.append({"type": "text", "page_idx": page, "bbox": [60, 80 + offset * 150, 900, 110 + offset * 150],
                               "text": f"{number}. 已知函数 f(x) 的值"})
                seq += 1
        folder = self.temp / str(paper.id)
        mineru_zip(folder / "mineru_result.zip", blocks)
        pipeline.parse(paper)
        paper.refresh_from_db()
        names = [paper.photos["files"][i]["name"] for i in paper.photos["order"]]
        self.assertEqual(names, ["B.jpg", "C.jpg", "A.jpg"])
        self.assertEqual(paper.photos["check"], "")
        self.assertTrue(paper.notes == [] and paper.photos["notes"][0].startswith("页序：已按卷面题号排好"))
        shown = self.client.get(f"/api/papers/{paper.id}").json()["paper"]["photos"]
        self.assertEqual(shown["names"], ["B.jpg", "C.jpg", "A.jpg"])
        self.assertEqual(shown["ranges"], [[1, 5], [6, 9], [10, 12]])
        store = pipeline.PageStore(paper)
        self.assertEqual([dark_bars(store.load(p)) for p in range(3)], [2, 3, 1])     # PDF 也按新页序重拼
        first_page = {b.text[:2] for b in paper.blocks.filter(page_idx=0)}
        self.assertEqual(first_page, {"1.", "2.", "3.", "4.", "5."})

        # 人工把第 3 页调到最前：内容块、题卡范围、页面都跟着换
        paper.status = Paper.Status.READY
        paper.save()
        first_group = QuestionGroup.objects.create(
            paper=paper, title="前两页", sequence=0, page_start=1, page_end=2,
            metadata={"pages": [0, 1]},
        )
        second_group = QuestionGroup.objects.create(
            paper=paper, title="最后一页", sequence=1, page_start=3, page_end=3,
            metadata={"pages": [2]},
        )
        question = Question.objects.create(paper=paper, group=second_group, number=10, regions=[{"page_idx": 2, "bbox": [50, 50, 950, 500]}],
                                            regions_auto=[{"page_idx": 2, "bbox": [50, 50, 950, 500]}], state="green")
        response = self.client.post(f"/api/papers/{paper.id}/page-order", data=json.dumps({"order": [2, 0, 1]}),
                                    content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 200, response.content)
        paper.refresh_from_db()
        # This manual order restarts from 10–12 back to 1–5.  The new no-loss
        # safeguard therefore pauses before cards can overwrite one another.
        self.assertEqual(paper.status, Paper.Status.NEEDS_GROUPING)
        self.assertTrue(paper.photos["manual"])
        self.assertEqual([paper.photos["files"][i]["name"] for i in paper.photos["order"]], ["A.jpg", "B.jpg", "C.jpg"])
        question.refresh_from_db()
        self.assertEqual(question.regions[0]["page_idx"], 0)
        first_group.refresh_from_db()
        second_group.refresh_from_db()
        self.assertEqual(first_group.metadata["pages"], [1, 2])
        self.assertEqual((first_group.page_start, first_group.page_end), (2, 3))
        self.assertEqual(second_group.metadata["pages"], [0])
        self.assertEqual((second_group.page_start, second_group.page_end), (1, 1))
        self.assertEqual({b.text[:3] for b in paper.blocks.filter(page_idx=0)}, {"10.", "11.", "12."})
        self.assertEqual([dark_bars(pipeline.PageStore(paper).load(p)) for p in range(3)], [1, 2, 3])
        self.assertNotEqual(response.json()["paper"]["pages_version"], "")
        self.assertEqual(response.json()["paper"]["photos"]["ranges"], [[10, 12], [1, 5], [6, 9]])  # 范围跟着照片走
        bad = self.client.post(f"/api/papers/{paper.id}/page-order", data=json.dumps({"order": [0, 0, 1]}),
                               content_type="application/json", HTTP_X_QB_REQUEST="1")
        self.assertEqual(bad.status_code, 400)

        # Confirming the new physical order must apply its new scopes, not keep
        # the old group sequence. The existing q10 card follows its page/group
        # and retains its stable source identity.
        source_key = question.source_key
        confirmed = self.client.post(
            f"/api/papers/{paper.id}/confirm-structure", data=json.dumps({}),
            content_type="application/json", HTTP_X_QB_REQUEST="1",
        )
        self.assertEqual(confirmed.status_code, 200, confirmed.content)
        paper.refresh_from_db()
        with mock.patch.object(pipeline.imaging, "trim_regions", side_effect=lambda regions, _load: regions), \
                mock.patch.object(pipeline, "locate_missing", return_value=[]):
            pipeline.segment_paper(paper)
        question.refresh_from_db()
        self.assertEqual(question.source_key, source_key)
        self.assertEqual(question.group.sequence, 0)
        self.assertEqual(question.group.metadata["pages"], [0])
        self.assertEqual(paper.questions.count(), 12)
