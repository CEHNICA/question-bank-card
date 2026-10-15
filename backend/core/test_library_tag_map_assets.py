"""Tag-map assets must be served by the installed application's real routes."""

from pathlib import Path

from django.test import SimpleTestCase


FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


class LibraryTagMapAssetTests(SimpleTestCase):
    def asset(self, filename, content_type):
        response = self.client.get("/" + filename)
        try:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], content_type + "; charset=utf-8")
            actual = b"".join(response.streaming_content)
            self.assertEqual(actual, (FRONTEND / filename).read_bytes())
            self.assertTrue(actual.strip(), "A successful asset response must contain the current application file")
        finally:
            response.close()

    def test_javascript_route_serves_current_asset(self):
        self.asset("library-tag-map.js", "application/javascript")

    def test_stylesheet_route_serves_current_asset(self):
        self.asset("library-tag-map.css", "text/css")

    def test_asset_routes_reject_writes(self):
        for filename in ("library-tag-map.js", "library-tag-map.css"):
            for method in ("post", "put", "delete"):
                with self.subTest(filename=filename, method=method):
                    response = getattr(self.client, method)("/" + filename, data="{}", content_type="application/json")
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "GET")

    def test_library_page_loads_tag_map_assets(self):
        response = self.client.get("/library")
        try:
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content).decode("utf-8")
            self.assertIn('href="/library-tag-map.css', content)
            self.assertIn('src="/library-tag-map.js', content)
            self.assertLess(content.index('src="/library-tag-map.js'), content.index('src="/library-tag-manager.js'))
        finally:
            response.close()
