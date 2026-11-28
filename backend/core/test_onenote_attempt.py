"""仅使用虚构页面，回归加号新增与普通打开的差异。"""

import io
from unittest.mock import patch

from django.test import TestCase
from PIL import Image

from . import library_onenote as note
from .models import PublishedQuestion


class OneNoteAttemptTests(TestCase):
    def setUp(self):
        image = io.BytesIO()
        Image.new("RGB", (10, 10), "white").save(image, format="PNG")
        self.png = image.getvalue()
        self.item = PublishedQuestion.objects.create(
            source_filename="虚构测试",
            number=1,
            question_type="free_response",
            version=1,
            content={"stem": "1+1=?"},
            content_hash="b" * 64,
            extras={"onenote": {"page_id": "{page}", "url": "onenote:fake-page"}},
        )

    @staticmethod
    def page(year=2013):
        return (
            f'<n:Page xmlns:n="http://schemas.microsoft.com/office/onenote/{year}/onenote" ID="{{page}}">'
            '<n:Outline objectID="{old}"><n:Position x="36" y="86"/><n:Size width="540"/></n:Outline>'
            '<n:InkDrawing objectID="{ink}"><n:Position x="700" y="100"/><n:Size width="90"/></n:InkDrawing>'
            "</n:Page>"
        )

    def bridge(self, op, **params):
        if op == "page":
            return {"xml": self.page()}
        if op == "append_outline":
            self.assertIn(params["marker"], params["outlineXml"])
            return {"outlineId": "{new}", "focusObjectId": "{oe}", "objectUrl": "onenote:fake-object"}
        if op == "navigate":
            # The DB association must be durable before navigation is attempted.
            self.item.refresh_from_db()
            self.assertEqual(self.item.extras["onenote"]["attempts"][-1]["outline_id"], "{new}")
            self.assertEqual(params["objectId"], "{oe}")
            return {"navigated": True, "object_navigated": True, "focused": True}
        raise AssertionError(op)

    def test_old_schema_uses_actual_right_edge(self):
        for year in (2007, 2010, 2013):
            with self.subTest(year=year):
                self.assertEqual(note.compute_page_right_edge(self.page(year)), 790)

    @patch.object(note, "cached_question_image", return_value=b"fake-image")
    @patch.object(note, "call_bridge")
    def test_legacy_link_without_attempts_still_appends_then_opens_new_object(self, bridge, _cache):
        _cache.return_value = self.png
        bridge.side_effect = self.bridge
        response = self.client.post(f"/api/library/{self.item.pk}/onenote/attempt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["warning"], "")
        calls = [c.args[0] for c in bridge.call_args_list]
        self.assertEqual(calls, ["page", "append_outline", "navigate"])
        self.item.refresh_from_db()
        self.assertGreater(self.item.extras["onenote"]["attempts"][-1]["x"], 790)

    @patch.object(note, "cached_question_image", return_value=b"fake-image")
    @patch.object(note, "call_bridge")
    def test_navigation_failure_keeps_saved_attempt_and_warns_without_reappending(self, bridge, _cache):
        _cache.return_value = self.png
        bridge.side_effect = self.bridge
        with patch.object(note, "open_page", side_effect=note.OneNoteError("fake failure")):
            response = self.client.post(f"/api/library/{self.item.pk}/onenote/attempt")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("无需再次点击", response.json()["warning"])
        self.item.refresh_from_db()
        self.assertEqual(len(self.item.extras["onenote"]["attempts"]), 1)
        self.assertEqual(sum(c.args[0] == "append_outline" for c in bridge.call_args_list), 1)

    @patch.object(note, "cached_question_image", return_value=b"fake-image")
    @patch.object(note, "call_bridge")
    def test_missing_new_object_is_not_success_or_fake_timeline(self, bridge, _cache):
        _cache.return_value = self.png
        bridge.side_effect = lambda op, **kw: {"xml": self.page()} if op == "page" else {"navigated": True}
        response = self.client.post(f"/api/library/{self.item.pk}/onenote/attempt")
        self.assertEqual(response.status_code, 400)
        self.assertIn("未确认新增区域", response.json()["error"])
        self.item.refresh_from_db()
        self.assertNotIn("attempts", self.item.extras["onenote"])

    @patch.object(note, "create_or_sync_page")
    def test_url_only_binding_does_not_degrade_plus_to_open(self, create):
        self.item.extras = {"onenote": {"url": "onenote:fake-page"}}
        with self.assertRaisesMessage(note.OneNoteError, "缺少页面标识"):
            note.start_attempt(self.item)
        create.assert_not_called()

    @patch.object(note, "call_bridge", return_value={"xml": "<broken>"})
    def test_invalid_page_is_not_overlaid_at_default_position(self, bridge):
        with self.assertRaisesMessage(note.OneNoteError, "内容无效"):
            note.start_attempt(self.item)
        self.assertEqual(bridge.call_count, 1)

    @patch.object(note, "shell_open_onenote", return_value=False)
    @patch.object(note, "call_bridge", return_value={"navigated": True, "object_navigated": False, "focused": True})
    def test_visible_page_does_not_prove_object_navigation(self, bridge, shell):
        self.assertFalse(note._refine_open("{page}", None, object_id="{new}"))
        shell.assert_not_called()

    @patch.object(note, "_refine_open", side_effect=[False, True])
    def test_failed_text_object_jump_retries_outline_without_page_url(self, opened):
        self.item.extras["onenote"]["attempts"] = [{"outline_id": "{new}", "focus_object_id": "{oe}"}]
        self.assertTrue(note.open_page(self.item, wait=True, object_id="{oe}")["ok"])
        self.assertEqual(opened.call_count, 2)
        self.assertIsNone(opened.call_args_list[0].args[1])
        self.assertEqual(opened.call_args_list[1].kwargs["object_id"], "{new}")

    @patch.object(note, "call_bridge", return_value={"navigated": True, "focused": True})
    def test_plain_open_does_not_append(self, bridge):
        note.open_page(self.item, wait=True)
        self.assertEqual([c.args[0] for c in bridge.call_args_list], ["navigate"])
