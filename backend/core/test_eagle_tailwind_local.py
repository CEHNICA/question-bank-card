"""Eagle 工作台的 Tailwind 必须本地化：不再依赖外网 CDN，样式随前端走。"""

from pathlib import Path

from django.test import SimpleTestCase

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def _text(response) -> str:
    """_frontend 走 FileResponse（流式），测试里要自己拼内容。"""
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content).decode("utf-8")
    return response.content.decode("utf-8")


class EagleTailwindLocalTests(SimpleTestCase):
    def test_eagle_page_does_not_use_cdn_tailwind(self):
        """Eagle 页不能再挂外网 Tailwind CDN：断网时整页会没样式。"""
        response = self.client.get("/eagle")
        self.assertEqual(response.status_code, 200)
        body = _text(response)
        self.assertNotIn("cdn.tailwindcss.com", body)

    def test_eagle_page_links_local_tailwind(self):
        response = self.client.get("/eagle")
        body = _text(response)
        self.assertIn('href="/eagle/tailwind.css', body)

    def test_local_tailwind_asset_is_served(self):
        """本地预编译产物要能从 /eagle/tailwind.css 取到，且是真正的 CSS。"""
        response = self.client.get("/eagle/tailwind.css")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/css", response["Content-Type"])
        body = _text(response)
        self.assertIn("--tw-", body)
        self.assertNotIn("cdn.tailwindcss.com", body)

    def test_tailwind_artifact_ships_with_frontend(self):
        """产物必须跟着前端目录走（安装版打包会按扩展名白名单收 .css）。"""
        artifact = FRONTEND / "eagle" / "tailwind.css"
        self.assertTrue(artifact.is_file(), "frontend/eagle/tailwind.css 缺失")
        self.assertGreater(artifact.stat().st_size, 10000)
