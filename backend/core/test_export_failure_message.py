"""1.26.32：导出失败要说人话，别只剩一句「请稍后重试」。

老师转述的用户反馈：「Word 未能导出，请稍后重试」。
这句话不是后台给的结论 —— 它是网页上的兜底话：后台回了非 2xx、正文又不是带
error 的 JSON 时，前端只能这么说。导出这条路上已知的失败全都带原因，
所以那个用户碰到的是**没被接住的意外错**。

以前这种错的后果被放大了两层：
  1. 网页上只说「请稍后重试」—— 原因若是永久性的，重试一万次也没用；
  2. 日志里连堆栈都没有 —— 事后谁也查不出到底为什么。

这一版量的是真跑出来的行为：
  1. 导出接口撞上意外错 → 回的是 JSON，带一句能转述的话和一个查得到的诊断号；
  2. 诊断号在日志里能查到（堆栈不丢）；
  3. 拿掉兜底 → 退回一张 HTML 网页（前端连原因都读不出来）。
"""

from __future__ import annotations

from unittest import mock

from django.test import Client, SimpleTestCase, override_settings
from django.urls import path

from . import library_export, library_pdf

urlpatterns = [
    path("api/library/export-docx", library_export.export_docx_view),
    path("api/library/export-pdf", library_pdf.export_pdf_view),
]


@override_settings(ROOT_URLCONF=__name__)
class ExportFailureMessageTests(SimpleTestCase):
    def setUp(self):
        self.client = Client()

    def post(self, route, module):
        payload = {"ids": ["00000000-0000-0000-0000-000000000000"], "title": "导出失败测试"}
        with mock.patch.object(module, "export", side_effect=RuntimeError("故意出的意外错")):
            return self.client.post(route, json=payload, content_type="application/json", HTTP_X_QB_REQUEST="1")

    def test_an_unexpected_word_export_error_becomes_a_readable_message(self):
        """Word 导出撞上意外错 → 回 JSON，带诊断号和一句能转述的话。"""
        with self.assertLogs("core.export", level="ERROR") as captured:
            response = self.post("/api/library/export-docx", library_export)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertIn("诊断号 EX-", body["error"])
        self.assertIn("导出没完成", body["error"])
        # 老师要能转述：出了事，题和原卷没被动过。
        self.assertIn("原卷都没动", body["error"])
        # 堆栈得留下来，否则事后查不出原因。
        self.assertTrue(captured.records, "意外错必须记进日志")
        self.assertIn("故意出的意外错", captured.output[0])

    def test_an_unexpected_pdf_export_error_becomes_a_readable_message(self):
        """PDF 导出同样：不能只剩一张 500 网页。"""
        with self.assertLogs("core.export", level="ERROR"):
            response = self.post("/api/library/export-pdf", library_pdf)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response["Content-Type"], "application/json")
        body = response.json()
        self.assertIn("诊断号 EX-", body["error"])
        self.assertIn("PDF导出没完成", body["error"])

    def test_the_diagnostic_id_is_a_usable_shape(self):
        """诊断号要短、要能念出来、还要能在日志里搜到 —— 老师把它报过来就能查。"""
        with self.assertLogs("core.export", level="ERROR") as captured:
            response = self.post("/api/library/export-docx", library_export)
        body = response.json()
        number = body["error"].split("诊断号 ")[1].split("）")[0]
        self.assertRegex(number, r"^EX-[0-9A-F]{12}$")
        # 同一次出错：网页上给用户的号，和日志里记的号得是同一个。
        self.assertIn(number, captured.output[0].upper())

    def test_the_backend_never_leaves_a_bare_html_page(self):
        """拿掉兜底会退回一张 HTML 网页 —— 前端连原因都读不出来，只能说「请稍后重试」。

        这一条是给以后的人看的：谁要是把兜底删了，这条测试会红。
        """
        from django.core.handlers.exception import response_for_exception
        from django.test import RequestFactory

        request = RequestFactory().post(
            "/api/library/export-docx",
            data=b'{"ids": []}',
            content_type="application/json",
            HTTP_X_QB_REQUEST="1",
            REMOTE_ADDR="127.0.0.1",
        )
        # Django 对没接住的错返回的是网页（text/html），不是 JSON。
        try:
            raise RuntimeError("故意出的意外错")
        except Exception as error:
            fallback = response_for_exception(request, error)
        self.assertIn("html", fallback.get("Content-Type", ""))
        self.assertNotIn("json", fallback.get("Content-Type", ""))
