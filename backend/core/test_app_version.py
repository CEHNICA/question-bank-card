"""The About panel shows the version from one place instead of a stale literal."""

from django.test import TestCase

from .version import APP_VERSION


class AppVersionTests(TestCase):
    def test_status_reports_the_app_version(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["app_version"], APP_VERSION)
        self.assertRegex(APP_VERSION, r"^\d+\.\d+(?:\.\d+){0,2}$")
