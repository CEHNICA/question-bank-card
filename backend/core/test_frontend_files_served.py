"""首页引用的每一个前端文件，都要真的取得到。

前端文件不是「一个目录一起发」——而是在后台**一个一个登记路径**。
漏登记的后果很隐蔽：

  * 文件确实在安装目录里（打包检查过、有文件）；
  * 首页确实引用了它（页面上写着 <script src="/startup-hint.js">）；
  * 浏览器去要的时候拿到的是 404 —— 那个功能**从来没生效过**。

而代码在、测试也在，看着一切正常。1.26.50 就这样丢过一次：
「打开时那一栏提示正在准备」写完了、测过了、打包了，
实机一看，那句话从来没出现在屏幕上。

所以这里加一条兜底的检查：把首页引用的每一个本地文件都真的要一遍，
不许 404。
"""

from __future__ import annotations

import re

from django.test import Client, SimpleTestCase

SCRIPT_SRC = re.compile(r'<script[^>]+src="([^"]+)"')
STYLE_HREF = re.compile(r'<link[^>]+href="([^"]+)"')


class FrontendFilesAreServedTests(SimpleTestCase):
    def referenced_files(self, html: str) -> list[str]:
        found = []
        for match in SCRIPT_SRC.findall(html):
            found.append(match)
        for match in STYLE_HREF.findall(html):
            found.append(match)
        # 只管本机的文件；外链（katex 字体等）不管。
        return [item for item in found if item.startswith("/") and not item.startswith("//")]

    def test_every_file_the_page_mentions_can_be_fetched(self):
        html = Client().get("/").content.decode("utf-8")
        referenced = self.referenced_files(html)
        self.assertTrue(referenced, "首页总得引用点什么")

        missing = []
        for item in referenced:
            # 去掉版本戳（?v=…）再去要。
            path = item.split("?")[0]
            response = Client().get(path)
            if response.status_code != 200:
                missing.append(f"{item} → {response.status_code}")
        self.assertEqual(
            missing, [], "首页引用了这些文件，但取不到（前端文件要一个一个登记，漏登记就是 404）：" + "、".join(missing)
        )

    def test_the_startup_hint_files_are_served(self):
        for path, content_type in (("/startup-hint.js", "javascript"), ("/startup-hint.css", "css")):
            response = Client().get(path)
            self.assertEqual(response.status_code, 200, f"{path} 取不到")
            self.assertIn(content_type, response["Content-Type"], f"{path} 的类型不对，浏览器可能不认")

    def test_the_library_page_files_are_served_too(self):
        html = Client().get("/library").content.decode("utf-8")
        missing = []
        for item in self.referenced_files(html):
            path = item.split("?")[0]
            if Client().get(path).status_code != 200:
                missing.append(item)
        self.assertEqual(missing, [], "题库页引用的文件取不到：" + "、".join(missing))
