"""1.26.48：把一道题做成图片放进剪贴板。

老师要的是：点「复制」，题目一张图；题里带的配图另外单独一张；
粘到 OneNote 里两个是独立对象，能分别拖位置。

这几条测试盯的都是真东西：
- 接口返回的话说得对不对；
- 剪贴板上真的有「HTML（两个 img）」+「PNG」+「DIB」三种格式；
- HTML 里 img 的个数 = 1（题目）+ 配图张数 —— 这是「两张独立对象」的根据；
- 题目图里不画配图（老师选的 A 方案）；
- 配图原图单独给的那张，和库里存的是同一份字节。
"""

import base64
import io
import json
import os
import re
from copy import deepcopy
from unittest import mock

from django.test import Client, TestCase

from . import library_clipboard
from .models import Paper, PublishedQuestion, Question


class ClipboardFigureTests(TestCase):
    """剪贴板格式与张数。"""

    def setUp(self):
        self.serial = 0
        self.paper = Paper.objects.create(filename="剪贴板测试.pdf", kind="pdf", sha256="c" * 64)
        self.question = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem="虚构题", state="green"
        )

    def publication(self, *, figures=(), content=None):
        self.serial += 1
        content = (
            deepcopy(content)
            if content is not None
            else {
                "question_type": "single_choice",
                "stem": "若 $x+1=3$，求 $x$。",
                "options": {"A": "$1$", "B": "$2$"},
                "answer": "",
                "analysis": "",
                "figures": [],
                "origin": "",
            }
        )
        content["figures"] = [
            {"slot": slot, "file": "figure-%d.png" % (index + 1)} for index, slot in enumerate(figures)
        ]
        content.update(document_id=str(self.paper.id), source_filename=self.paper.filename)
        return PublishedQuestion.objects.create(
            paper=self.paper,
            question=self.question,
            source_filename=self.paper.filename,
            number=self.serial,
            question_type=content["question_type"],
            content=content,
            content_hash="h" * 64,
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
        )

    def write_figures(self, publication, count):
        """在库里存几张真的 PNG，并让 content 指向它们。"""
        from PIL import Image

        folder = self.paper_content_folder(publication)
        folder.mkdir(parents=True, exist_ok=True)
        names = []
        for index in range(count):
            buffer = io.BytesIO()
            Image.new("RGB", (12, 9), (index * 40 + 10, 20, 30)).save(buffer, format="PNG")
            data = buffer.getvalue()
            name = "figure-%d.png" % (index + 1)
            (folder / name).write_bytes(data)
            names.append(name)
        content = deepcopy(publication.content)
        content["figures"] = [{"slot": "stem", "file": name} for name in names]
        publication.content = content
        publication.save(update_fields=["content"])
        return names

    def paper_content_folder(self, publication):
        from pathlib import Path

        from django.conf import settings

        return Path(settings.DATA_ROOT) / "library" / str(publication.id)

    def test_clipboard_has_two_images_for_one_figure(self):
        """一道带配图的题：题面一张 + 配图一张，HTML 里两个 img。"""
        publication = self.publication(figures=("stem",))
        self.write_figures(publication, 1)
        payload, figures = library_clipboard._collect(publication)
        self.assertEqual(len(figures), 1)
        # A 方案：题面图里不放配图。
        self.assertEqual(payload["figures"], [])

        html = library_clipboard._html_clipboard_bytes([b"question-png"] + figures).decode("utf-8")
        self.assertEqual(html.count("<img"), 2)
        # HTML Format 的偏移量头必须齐全，否则 Word 贴不出来。
        for key in ("StartHTML:", "EndHTML:", "StartFragment:", "EndFragment:"):
            self.assertIn(key, html)

    def test_write_clipboard_puts_every_image_on_the_clipboard(self):
        """题目图和配图都要真的写上剪贴板 —— 少一张，OneNote 里就少一个可拖的对象。"""
        import win32clipboard
        from PIL import Image

        def real_png(color):
            buffer = io.BytesIO()
            Image.new("RGB", (6, 4), color).save(buffer, format="PNG")
            return buffer.getvalue()

        images = [real_png((200, 30, 30)), real_png((30, 30, 200))]
        with (
            mock.patch.object(win32clipboard, "OpenClipboard"),
            mock.patch.object(win32clipboard, "EmptyClipboard"),
            mock.patch.object(win32clipboard, "CloseClipboard"),
            mock.patch.object(win32clipboard, "SetClipboardData") as set_data,
            mock.patch.object(win32clipboard, "RegisterClipboardFormat", side_effect=[101, 102]),
        ):
            library_clipboard._write_clipboard(images)

        formats = {call.args[0]: call.args[1] for call in set_data.call_args_list}
        self.assertEqual(len(formats), 3, "剪贴板上要写三种格式：HTML、PNG、DIB")
        # HTML 里两张图都要在。
        html = next(
            value for value in formats.values() if isinstance(value, bytes) and b"<!--StartFragment-->" in value
        )
        self.assertEqual(html.count(b"<img"), 2)

    def test_copy_question_gives_the_figure_to_the_clipboard(self):
        """整条路走通：题目一张 + 配图一张，一起进剪贴板。"""
        publication = self.publication(figures=("stem",))
        self.write_figures(publication, 1)
        with (
            mock.patch.object(library_clipboard, "_render_question_png", return_value=b"q"),
            mock.patch.object(library_clipboard, "_write_clipboard") as write,
        ):
            result = library_clipboard.copy_question(publication)
        self.assertEqual(len(write.call_args.args), 2)
        self.assertEqual(len(write.call_args.args[0]), 2)
        self.assertEqual(write.call_args.args[0][0], b"q")
        # 两个图都要写成了文件（HTML 要用本地文件引用）。
        self.assertEqual(len(write.call_args.args[1]), 2)
        for path in write.call_args.args[1]:
            self.assertTrue(os.path.exists(path), "图要先写成文件：%s" % path)
        self.assertEqual(result["images"], 2)
        self.assertIn("配图 1 张", result["message"])

    def test_html_uses_local_files_not_data_uri(self):
        """图必须用本地文件引用，不能用 data URI。

        实测：data URI 里的图是异步解码的，Office 粘贴时经常只来得及贴出
        第一张（同一份剪贴板连粘 5 次，有 1 次只出来 1 个对象）。
        换成 file:// 引用本机文件，连粘 5 次都是 2 个对象。
        """
        import tempfile

        from PIL import Image

        folder = tempfile.mkdtemp(prefix="qb-test-clip-")
        paths = []
        images = []
        for index, color in enumerate(((200, 30, 30), (30, 30, 200))):
            buffer = io.BytesIO()
            Image.new("RGB", (40, 30), color).save(buffer, format="PNG")
            data = buffer.getvalue()
            images.append(data)
            target = os.path.join(folder, "image-%d.png" % index)
            with open(target, "wb") as fh:
                fh.write(data)
            paths.append(target)

        html = library_clipboard._html_clipboard_bytes(images, paths).decode("utf-8")
        self.assertNotIn("data:image/png;base64", html, "图不能塞成 data URI")
        self.assertEqual(html.count("file:///"), 2, "两张图都要用本地文件引用")
        for path in paths:
            self.assertIn(path.replace("\\", "/"), html)

        # 没有路径时才退回 data URI（那张图仍然是完整的）。
        fallback = library_clipboard._html_clipboard_bytes(images).decode("utf-8")
        self.assertEqual(fallback.count("data:image/png;base64"), 2)

    def test_clipboard_without_figure_has_one_image(self):
        publication = self.publication()
        payload, figures = library_clipboard._collect(publication)
        self.assertEqual(figures, [])
        html = library_clipboard._html_clipboard_bytes([b"question-png"]).decode("utf-8")
        self.assertEqual(html.count("<img"), 1)

    def test_html_offset_numbers_point_at_the_right_places(self):
        """偏移量头要真的指对位置，不能只是「有这几个字」。"""
        images = [b"one", b"two"]
        raw = library_clipboard._html_clipboard_bytes(images)
        text = raw.decode("utf-8")
        values = {}
        for key in ("StartHTML", "EndHTML", "StartFragment", "EndFragment"):
            match = re.search(rf"{key}:(\d{{10}})", text)
            self.assertIsNotNone(match, key)
            values[key] = int(match.group(1))
        self.assertEqual(text[values["StartHTML"] : values["StartHTML"] + 6], "<html>")
        self.assertEqual(text[values["EndHTML"] - 7 : values["EndHTML"]], "</html>")
        self.assertEqual(text[values["StartFragment"] : values["StartFragment"] + 9], "<p><img s")
        self.assertEqual(text[values["EndFragment"] - 4 : values["EndFragment"]], "</p>")

    def test_figure_bytes_match_the_stored_file(self):
        """单独给的那张配图，要跟库里存的是同一份字节。"""
        publication = self.publication(figures=("stem",))
        names = self.write_figures(publication, 1)
        payload, figures = library_clipboard._collect(publication)
        stored = (self.paper_content_folder(publication) / names[0]).read_bytes()
        self.assertEqual(figures[0], stored)

    def test_dib_is_a_real_bitmap(self):
        """CF_DIB 要的是 BITMAPINFOHEADER + BGR 像素，不是 PNG。"""
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (3, 2), (10, 20, 30)).save(buffer, format="PNG")
        dib = library_clipboard._dib_bytes(buffer.getvalue())
        self.assertEqual(int.from_bytes(dib[0:4], "little"), 40)
        self.assertEqual(int.from_bytes(dib[4:8], "little"), 3)
        self.assertEqual(int.from_bytes(dib[8:12], "little"), 2)
        self.assertEqual(int.from_bytes(dib[14:16], "little"), 24)
        # 行距按 4 字节对齐：3 像素 * 3 字节 = 9 -> 12。
        self.assertEqual(int.from_bytes(dib[20:24], "little"), 12 * 2)
        # 像素是 BGR 顺序：蓝、绿、红。
        self.assertEqual(tuple(dib[40:43]), (30, 20, 10))


class ClipboardViewTests(TestCase):
    """接口本身。"""

    def setUp(self):
        self.client = Client()
        self.paper = Paper.objects.create(filename="剪贴板接口.pdf", kind="pdf", sha256="d" * 64)
        self.question = Question.objects.create(
            paper=self.paper, number=1, question_type="single_choice", stem="虚构题", state="green"
        )
        self.publication = PublishedQuestion.objects.create(
            paper=self.paper,
            question=self.question,
            source_filename=self.paper.filename,
            number=1,
            question_type="single_choice",
            content={
                "question_type": "single_choice",
                "stem": "若 $x+1=3$，求 $x$。",
                "options": {"A": "$1$", "B": "$2$"},
                "answer": "",
                "analysis": "",
                "figures": [],
                "origin": "",
                "document_id": str(self.paper.id),
                "source_filename": self.paper.filename,
            },
            content_hash="e" * 64,
            version=1,
            status=PublishedQuestion.Status.PUBLISHED,
        )

    def post(self, payload):
        return self.client.post(
            "/api/library/copy-image",
            data=json.dumps(payload),
            content_type="application/json",
            headers={"x-qb-request": "1"},
        )

    def test_needs_the_local_request_header(self):
        response = self.client.post(
            "/api/library/copy-image",
            data=json.dumps({"publication_id": str(self.publication.id)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_rejects_a_broken_publication_id(self):
        response = self.post({"publication_id": "not-a-uuid"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

    def test_unknown_publication_says_so(self):
        response = self.post({"publication_id": "00000000-0000-4000-8000-000000000000"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("不在题库里", response.json()["error"])

    def test_successful_copy_reports_the_image_count(self):
        with (
            mock.patch.object(library_clipboard, "_render_question_png", return_value=b"png-bytes"),
            mock.patch.object(library_clipboard, "_write_clipboard") as write,
        ):
            response = self.post({"publication_id": str(self.publication.id)})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["images"], 1)
        self.assertIn("题目 1 张", body["message"])
        # 写剪贴板时会多传一个「图存成了哪些文件」的参数。
        self.assertEqual(len(write.call_args.args), 2)
        self.assertEqual(write.call_args.args[0], [b"png-bytes"])
        self.assertEqual(len(write.call_args.args[1]), 1)

    def test_withdrawn_question_cannot_be_copied(self):
        self.publication.status = PublishedQuestion.Status.WITHDRAWN
        self.publication.save(update_fields=["status"])
        response = self.post({"publication_id": str(self.publication.id)})
        self.assertEqual(response.status_code, 409)
        self.assertIn("撤回", response.json()["error"])

    def test_a_second_copy_is_refused_while_one_is_running(self):
        """两道题不能同时排版 —— 浏览器会打架。"""
        held = library_clipboard._render_lock.acquire(blocking=False)
        self.assertTrue(held)
        try:
            response = self.post({"publication_id": str(self.publication.id)})
            self.assertEqual(response.status_code, 409)
            self.assertIn("另一道题", response.json()["error"])
        finally:
            library_clipboard._render_lock.release()

    def test_other_machines_cannot_reach_this_endpoint(self):
        """别的电脑上的网页不能借这个接口把题库的内容写进本机剪贴板。"""
        response = self.client.post(
            "/api/library/copy-image",
            data=json.dumps({"publication_id": str(self.publication.id)}),
            content_type="application/json",
            headers={"x-qb-request": "1"},
            REMOTE_ADDR="203.0.113.7",
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("本机", response.json()["error"])

    def test_get_is_not_allowed(self):
        response = self.client.get("/api/library/copy-image", headers={"x-qb-request": "1"})
        self.assertEqual(response.status_code, 405)


class ClipboardDocumentTests(TestCase):
    """渲染用的页面：样式与内容都得对。"""

    def test_document_hides_nothing_and_keeps_fonts_inline(self):
        """library.css 把排版区设成 visibility:hidden（离屏测量用），截图前必须改回来。"""
        from pathlib import Path

        from django.conf import settings

        payload = {"stem": "题干", "options": {}, "origin": "", "figures": []}
        document = library_clipboard._build_document(payload)
        self.assertIn("visibility:visible", document)
        # 样式表靠这个 class 定位；位置和可见性由后面的覆盖规则改回来。
        self.assertIn('id="shotSource" class="print-flow exam-source"', document)
        self.assertIn("#shotSource{position:static", document)
        # KaTeX 的字体要内嵌成 data URI，不能让页面对外去连网。
        self.assertIn("data:font/woff2;base64,", document)
        self.assertIn("data:application/javascript;base64,", document)

    def test_document_escapes_the_json_payload(self):
        payload = {"stem": "题干 </script> <b>", "options": {}, "origin": "", "figures": []}
        document = library_clipboard._build_document(payload)
        body = document.split('id="examData"')[1]
        self.assertNotIn("</script>", body.split("</script>")[0])

    def test_driver_passes_content_directly(self):
        """渲染函数的第二个参数是 content，不是 item.content。"""
        driver = library_clipboard._DRIVER
        self.assertIn("QBRender.renderQuestion(block,item,", driver)
        self.assertNotIn("renderQuestion(block,item.content,", driver)
