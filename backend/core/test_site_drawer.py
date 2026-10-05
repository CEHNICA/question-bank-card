"""1.12.7 抽屉导航：脚本要能真的发出来，且两个页面都加载了它。

这是顶到第一道题之前所有附属横条的来源，抽屉挂不上就等于版面重排没做。
"""

from pathlib import Path

from django.test import SimpleTestCase

FRONTEND = Path(__file__).resolve().parent.parent.parent / "frontend"


class SiteDrawerRouteTests(SimpleTestCase):
    def test_script_is_served_as_javascript(self):
        response = self.client.get("/site-drawer.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/javascript", response["Content-Type"])
        body = b"".join(response.streaming_content)
        self.assertIn(b"QBSiteDrawer", body)
        self.assertIn(b"drawer-open", body)
        self.assertEqual(self.client.post("/site-drawer.js").status_code, 405)

    def test_both_pages_load_the_drawer(self):
        for page in ("index.html", "library.html"):
            html = (FRONTEND / page).read_text(encoding="utf-8")
            self.assertIn('<script src="/site-drawer.js" defer></script>', html, f"{page} 少加载了抽屉脚本")

    def test_index_navigation_is_marked_for_the_drawer(self):
        html = (FRONTEND / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="topnav" aria-label="页面" data-drawer="nav"', html)
        # 1.12.7：源码链接从抽屉里删了。AGPL 义务由「设置 → 关于」那一栏
        # （查看源代码按钮 + AGPL-3.0）和发布页承担，菜单里不再摆一份。
        self.assertNotIn('class="source-link"', html)
        drawer = (FRONTEND / "site-drawer.js").read_text(encoding="utf-8")
        self.assertNotIn('name: "hint"', drawer, "抽屉里不再有「操作说明」组")
        self.assertIn('name: "tools"', drawer)
