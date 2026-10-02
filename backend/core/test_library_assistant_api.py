"""Local-only mutation boundaries for assistant generation and writeback."""

import json
from unittest import mock

from django.test import Client, TestCase

from . import library_assistant
from .models import LibraryJob


class AssistantAPIBoundaryTests(TestCase):
    def setUp(self):
        self.client = Client()

    def post(self, endpoint, data="{}", **extra):
        return self.client.post("/api/library/assistant/" + endpoint, data=data,
                                content_type="application/json", **extra)

    def test_remote_requests_and_browser_forms_cannot_create_or_submit_tasks(self):
        for endpoint in ("prepare", "complete"):
            with self.subTest(endpoint=endpoint), mock.patch.object(library_assistant, endpoint) as action:
                self.assertEqual(self.post(endpoint, HTTP_X_QB_REQUEST="1", REMOTE_ADDR="192.0.2.1").status_code, 403)
                self.assertEqual(self.post(endpoint).status_code, 403)
                self.assertEqual(self.client.post("/api/library/assistant/" + endpoint,
                                                 {"job_id": "ignored"}, HTTP_X_QB_REQUEST="1").status_code, 415)
                self.assertEqual(self.client.get("/api/library/assistant/" + endpoint).status_code, 405)
                action.assert_not_called()
        self.assertFalse(LibraryJob.objects.exists())

    def test_invalid_json_and_oversized_writeback_never_reach_task_handler(self):
        for endpoint in ("prepare", "complete"):
            with self.subTest(endpoint=endpoint), mock.patch.object(library_assistant, endpoint) as action:
                for body in ("[]", "null", "{", json.dumps({"answer": "x" * 200_001})):
                    self.assertEqual(self.post(endpoint, body, HTTP_X_QB_REQUEST="1").status_code, 400)
                action.assert_not_called()

    def test_inbox_is_read_only_local_and_rejects_bad_limits_or_ids(self):
        url = "/api/library/assistant/tasks"
        self.assertEqual(self.client.get(url, REMOTE_ADDR="192.0.2.1").status_code, 403)
        self.assertEqual(self.post("tasks", HTTP_X_QB_REQUEST="1").status_code, 405)
        for query in ({"limit": "oops"}, {"limit": 0}, {"limit": 51}, {"ids": "invalid"}):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(url, query).status_code, 400)
        self.assertFalse(LibraryJob.objects.exists())
