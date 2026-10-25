"""改名为 .pdf 的网页/图片不是卷子。

PyMuPDF 不只认 PDF：HTML、PNG、SVG 改个后缀就能打开，还会被赋一个**假的页面
尺寸**（HTML 恒为 400×600）。以前上传只判「能不能打开」，于是下载链接失效存下的
404 页面会被当成一份正常卷子收下——库里记着 1 页，预览渲染出来只有一个灰阶
（一片空白），失败原因还说「本地文字层没有可靠题卡」。老师看到的是「切题坏了」，
真实原因是文件根本不是 PDF。
"""
import io
import tempfile
from pathlib import Path

import pymupdf as fitz
from django.conf import settings
from django.test import TransactionTestCase
from PIL import Image

from . import imaging, views
from .models import Paper

NOT_A_PDF_HTML = b"<!DOCTYPE html><html><body>404</body></html>"
NOT_A_PDF_SVG = (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" '
                 b'width="100" height="100"><rect width="100" height="100" fill="red"/></svg>')


def png_bytes(width: int = 600, height: int = 400) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 90, 200)).save(buffer, "PNG")
    return buffer.getvalue()


def real_pdf_bytes() -> bytes:
    with fitz.open() as document:
        page = document.new_page(width=595, height=842)
        page.insert_text((72, 120), "1. 已知 a = 2，求 a 的平方。")
        return document.tobytes()


class RenamedNonPdfUploadTests(TransactionTestCase):
    """上传入口按内容判断，而不是按后缀判断。"""

    def upload(self, name: str, data: bytes, **extra):
        upload = io.BytesIO(data)
        upload.name = name
        return self.client.post("/api/papers",
                                {"file": upload, "parse_mode": "auto", **extra},
                                HTTP_X_QB_REQUEST="1")

    def test_dead_link_404_page_saved_as_pdf_is_refused_and_named_as_a_webpage(self):
        response = self.upload("月考卷.pdf", NOT_A_PDF_HTML)
        self.assertEqual(response.status_code, 400, response.content)
        # 必须说清「这是网页」，而不是说 PDF 打不开——后者老师只会原样再传一次。
        self.assertIn("网页", response.json()["error"])
        self.assertIn(".pdf", response.json()["error"])
        self.assertFalse(Paper.objects.exists())

    def test_png_saved_as_pdf_is_refused_and_named_as_an_image(self):
        response = self.upload("试卷.pdf", png_bytes())
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("图片", response.json()["error"])
        self.assertFalse(Paper.objects.exists())

    def test_svg_saved_as_pdf_is_refused(self):
        response = self.upload("图形.pdf", NOT_A_PDF_SVG)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("矢量图", response.json()["error"])
        self.assertFalse(Paper.objects.exists())

    def test_a_real_pdf_is_still_accepted_with_its_own_page_size(self):
        response = self.upload("真卷.pdf", real_pdf_bytes())
        self.assertEqual(response.status_code, 201, response.content)
        paper = Paper.objects.get()
        self.assertEqual([(page["width"], page["height"]) for page in paper.pages], [(595.0, 842.0)])

    def test_a_photo_upload_still_goes_through_the_photo_path(self):
        # 图片按后缀走照片分支，压根不经过 PDF 校验；这条不能被上面的守卫误伤。
        upload = io.BytesIO(png_bytes())
        upload.name = "photo.png"
        response = self.client.post("/api/papers",
                                    {"file": upload, "parse_mode": "manual", "enhance": "0"},
                                    HTTP_X_QB_REQUEST="1")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Paper.objects.get().kind, "image")

    def test_refused_file_leaves_nothing_behind_on_disk(self):
        before = {path.name for path in Path(settings.DATA_ROOT).iterdir()}
        self.assertEqual(self.upload("月考卷.pdf", NOT_A_PDF_HTML).status_code, 400)
        after = {path.name for path in Path(settings.DATA_ROOT).iterdir()}
        self.assertEqual(before, after, "被拒绝的上传留下了临时目录")

    def test_a_damaged_real_pdf_still_says_it_cannot_be_opened(self):
        # 真正的 PDF 只是坏了（下载中断、只剩文件头）：还是「无法打开」，
        # 不能说成「这不是 PDF」——那会让老师去换文件，而不是重下。
        broken = b"%PDF-1.4\n" + b"garbage without any xref table " * 8
        response = self.upload("坏卷.pdf", broken)
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("无法打开", response.json()["error"])


class PageSizesRejectsRenamedFilesTests(TransactionTestCase):
    """``imaging.page_sizes`` 这一层自己也要拒绝，否则别的入口照样收得下。"""

    def write(self, data: bytes) -> Path:
        path = Path(tempfile.mkdtemp()) / "source.pdf"
        path.write_bytes(data)
        return path

    def test_non_pdf_content_raises_instead_of_inventing_a_page_size(self):
        for label, data in (("html", NOT_A_PDF_HTML), ("png", png_bytes()), ("svg", NOT_A_PDF_SVG)):
            with self.subTest(label=label):
                with self.assertRaises(imaging.NotAPdfFile):
                    imaging.page_sizes(self.write(data), "pdf")

    def test_a_real_pdf_still_reports_its_page_sizes(self):
        self.assertEqual(imaging.page_sizes(self.write(real_pdf_bytes()), "pdf"),
                         [{"page_idx": 0, "width": 595.0, "height": 842.0}])

    def test_image_kind_still_goes_through_pillow(self):
        path = self.write(png_bytes(12, 7))
        self.assertEqual(imaging.page_sizes(path, "image"),
                         [{"page_idx": 0, "width": 12, "height": 7}])

    def test_the_error_names_what_the_file_really_is(self):
        with self.assertRaises(imaging.NotAPdfFile) as caught:
            imaging.page_sizes(self.write(NOT_A_PDF_HTML), "pdf")
        message = str(caught.exception)
        self.assertIn("网页", message)
        self.assertIn("照片", message, "该告诉老师可以直接传照片")
