"""原图题的正文截图，导出到 Word 后必须和打印预览一样宽。

预览侧的图宽由 ``exam-layout.js`` 的 ``imageScale`` 决定：150dpi 自然尺寸，
不宽过正文 178mm，不高过 210mm。导出侧 ``_figure_paper_size`` 是它的转写，
但正文截图（``body``）当年被单独给了一条 235mm 的高度上限，理由是「原卷
扫描页更高」。那条理由讲的是排法（竖着排还是并排），不是尺寸上限，预览
并没有因此放宽，于是同一张图在屏幕上是 31.48mm、在 Word 里是 35.23mm。

这些测试把 body 锁回和普通配图同一口径：``_figure_paper_size`` 的默认值。
"""

import hashlib
import io
import zipfile

from django.test import TestCase, override_settings
from django.urls import path
from lxml import etree
from PIL import Image

from . import library, library_export as export, test_library_export as fixtures

urlpatterns = [path("api/library/export-docx", export.export_docx_view)]

WP_NS = {"wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"}


@override_settings(ROOT_URLCONF=__name__)
class SourceImageWidthTests(TestCase):
    setUp = fixtures.LibraryExportTests.setUp
    publication = fixtures.LibraryExportTests.publication
    payload = fixtures.LibraryExportTests.payload
    post = fixtures.LibraryExportTests.post

    def image_publication(self, *sizes):
        """一道正文是原卷截图的题，截图按 ``sizes`` 给的像素尺寸。"""
        pub = self.publication(stem="", kind="free_response")
        folder = self.root / "library" / str(pub.id)
        folder.mkdir(parents=True, exist_ok=True)
        images = []
        for index, size in enumerate(sizes, 1):
            target = folder / f"question-{index}.png"
            Image.new("RGB", size, "red").save(target)
            images.append(
                {
                    "file": target.name,
                    "width": size[0],
                    "height": size[1],
                    "page_idx": index - 1,
                    "bbox": [100, 100, 900, 400],
                    "order": index - 1,
                    "image_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }
            )
        pub.content.update(body_mode="source_image", question_images=images, figures=[])
        pub.content_hash = library.content_hash(pub.content)
        pub.save(update_fields=["content", "content_hash"])
        return pub

    def exported_widths_mm(self, pub):
        """导出件里每张正文截图在纸面上的宽（毫米）。"""
        result = self.post(self.payload([pub]))
        self.assertEqual(result.status_code, 200, result.content[:300])
        with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
            tree = etree.fromstring(archive.read("word/document.xml"))
        extents = tree.findall(".//wp:extent", WP_NS)
        self.assertTrue(extents, "导出件里没有图片")
        return [int(node.get("cx")) / 36000 for node in extents]

    def test_a_tall_scan_keeps_the_same_width_as_the_preview(self):
        # 高窄截图：高度才是最小约束（150dpi 自然宽 50.8mm，正文宽 178mm）。
        pub = self.image_publication((300, 2001))
        expected = export._figure_paper_size((300, 2001), export.PREVIEW_TEXT_WIDTH_MM)[0]
        stale = export._figure_paper_size((300, 2001), export.PREVIEW_TEXT_WIDTH_MM, 235)[0]
        self.assertAlmostEqual(expected, 31.484, places=3)
        self.assertAlmostEqual(stale, 35.232, places=3)
        widths = self.exported_widths_mm(pub)
        self.assertEqual(len(widths), 1)
        self.assertAlmostEqual(widths[0], expected, delta=0.02)

    def test_a_tall_scan_is_not_stretched_to_the_taller_body_allowance(self):
        pub = self.image_publication((300, 2001))
        stale = export._figure_paper_size((300, 2001), export.PREVIEW_TEXT_WIDTH_MM, 235)[0]
        widths = self.exported_widths_mm(pub)
        self.assertNotAlmostEqual(widths[0], stale, delta=0.02)

    def test_a_wide_scan_lands_on_the_text_width_like_any_other_figure(self):
        pub = self.image_publication((2000, 280))
        widths = self.exported_widths_mm(pub)
        self.assertAlmostEqual(widths[0], export.PREVIEW_TEXT_WIDTH_MM, delta=0.02)

    def test_every_scan_of_one_question_uses_the_same_rule(self):
        pub = self.image_publication((300, 2001), (2000, 280), (420, 210))
        widths = self.exported_widths_mm(pub)
        self.assertEqual(len(widths), 3)
        for width, size in zip(widths, ((300, 2001), (2000, 280), (420, 210))):
            self.assertAlmostEqual(width, export._figure_paper_size(size, export.PREVIEW_TEXT_WIDTH_MM)[0], delta=0.02)
