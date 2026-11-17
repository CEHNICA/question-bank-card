"""导出到 Word 的图，不许在链路里被降质或被放大。

实测（第四十轮尺子 _audit40.py）：真实产物里 14 张图，
docx 内位图像素 == PDF 内像素 == 源图尺寸，Word 没有二次重采样；
有效 DPI = 原图像素宽 / 纸上显示宽，全部 >= 150dpi（导出的基准）。

上游也查过了：data/<paper>/pages/ 的整页渲染 dpi 是 256~363，
切题是从整页图上裁的，所以源图本身就比 150dpi 宽裕。

这一组测试锁的是那条链路上最容易退化的两环：
  1. 写进 docx 的位图是原图字节，一个字节都没动
  2. 没有人工指定宽度时，纸面宽度就是 150dpi 等效宽，不多不少
"""

import io
import zipfile

from django.test import SimpleTestCase

from . import library_export

QUESTION = "11111111-1111-4111-8111-111111111111"


def source_png(width=600, height=400):
    """一张有细节的 PNG：纯白图会被 PNG 压成几 KB，看不出有没有被重编码。"""
    from PIL import Image

    image = Image.new("RGB", (width, height), "white")
    pixels = image.load()
    for x in range(0, width, 7):
        for y in range(0, height, 5):
            pixels[x, y] = ((x * 7) % 256, (y * 11) % 256, (x * y) % 256)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_document_with_body_image(image_bytes, size=(600, 400)):
    from docx import Document

    item = {"id": QUESTION, "images": [{"slot": "body", "bytes": image_bytes, "size": size}], "fields": {}}
    document = Document()
    library_export._write_images(document, item, "body")
    return document


def media_blobs(document):
    """docx 里实际存下来的每一个 media 文件（名字 + 字节）。"""
    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    with zipfile.ZipFile(buffer) as archive:
        return {name: archive.read(name) for name in archive.namelist() if name.startswith("word/media/")}


class ImageFidelityTests(SimpleTestCase):
    def test_the_picture_reaches_the_document_byte_for_byte(self):
        """导出不能重新编码图片：写进 docx 的必须是原图字节。"""
        blob = source_png()
        document = build_document_with_body_image(blob)
        blobs = media_blobs(document)
        self.assertEqual(len(blobs), 1, f"docx 里应有 1 张图，实际 {len(blobs)}")
        written = next(iter(blobs.values()))
        self.assertEqual(written, blob, f"图片被重新编码了：源 {len(blob)} 字节，写进文档 {len(written)} 字节")

    def test_the_picture_keeps_its_pixel_size(self):
        """重新编码最常见的症状就是掉像素，尺寸必须原样。"""
        from PIL import Image

        blob = source_png(600, 400)
        document = build_document_with_body_image(blob)
        written = next(iter(media_blobs(document).values()))
        with Image.open(io.BytesIO(written)) as image:
            self.assertEqual((image.width, image.height), (600, 400))

    def test_the_natural_width_is_the_150_dpi_width(self):
        """没人指定宽度时，纸面宽度就是 150dpi 等效宽 —— 有效 DPI 恰好 150。"""
        width, height = library_export._figure_paper_size((600, 400))
        self.assertAlmostEqual(width, 600 * 25.4 / library_export.PREVIEW_FIGURE_DPI, places=2)
        self.assertAlmostEqual(height, 400 * 25.4 / library_export.PREVIEW_FIGURE_DPI, places=2)
        effective_dpi = 600 / (width / 25.4)
        self.assertGreaterEqual(effective_dpi, 150.0)

    def test_a_wide_picture_is_capped_at_the_text_width_not_stretched(self):
        """超宽的图按正文宽收口，收口只会更小，有效 DPI 只会更高。"""
        width, _ = library_export._figure_paper_size((6000, 400))
        self.assertLessEqual(width, library_export.PREVIEW_TEXT_WIDTH_MM + 0.01)
        effective_dpi = 6000 / (width / 25.4)
        self.assertGreaterEqual(effective_dpi, 150.0)
