"""1.26.13：看图读题这一段的连接测试。

只发软件自己画的合成图，不发老师的试卷；测的是已保存的密钥。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from . import credential_settings, preferences, reader_test


class ReaderConnectionTestTests(TestCase):
    def setUp(self) -> None:
        self.client = Client()

    def _post(self, payload, remote=None):
        extra = {"HTTP_X_QB_REQUEST": "1"}
        if remote:
            extra["REMOTE_ADDR"] = remote
        return self.client.post(
            "/api/settings/credentials/test", data=json.dumps(payload), content_type="application/json", **extra
        )

    def test_requires_local_origin(self):
        response = self._post({"service": "minimax", "confirm": True}, remote="192.0.2.10")
        self.assertEqual(response.status_code, 403)

    def test_requires_explicit_consent(self):
        response = self._post({"service": "minimax"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("同意", response.json()["error"])

    def test_rejects_a_service_without_a_test_path(self):
        response = self._post({"service": "mineru", "confirm": True})
        self.assertEqual(response.status_code, 400)

    def test_reports_a_missing_saved_key(self):
        with mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value=""):
            with self.assertRaises(reader_test.ReadingTestError) as raised:
                reader_test.test_reading_connection("minimax")
        self.assertIn("保存密钥", str(raised.exception))

    def test_the_view_turns_a_missing_key_into_a_clear_400(self):
        with mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value=""):
            response = self._post({"service": "minimax", "confirm": True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("保存密钥", response.json()["error"])

    def test_the_view_never_reaches_a_service_on_its_own(self):
        """The endpoint only reports what the tester returns.

        Development must never spend the teacher's own quota: the service
        call is replaced here, so this test proves the view adds no request
        of its own and never touches the real credential store.
        """

        with mock.patch.object(
            reader_test,
            "test_reading_connection",
            return_value={"ok": True, "model": "offline-model", "message": "连接成功"},
        ) as call:
            response = self._post({"service": "minimax", "confirm": True})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(call.call_args.args[0], "minimax")
        self.assertEqual(response.json()["message"], "连接成功")


class ReaderConnectionCallTests(TestCase):
    """The synthetic request itself: one picture, one question, no paper."""

    def _response(self, status_code=200, body=None):
        return mock.Mock(status_code=status_code, json=lambda: body if body is not None else {})

    def test_a_correct_answer_passes(self):
        body = {"choices": [{"message": {"content": "【答案】2"}}]}
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="MiniMax-M3"),
            mock.patch.object(reader_test.requests, "post", return_value=self._response(200, body)) as post,
        ):
            result = reader_test.test_reading_connection("minimax")
        self.assertTrue(result["ok"])
        self.assertIn("连接成功", result["message"])
        # One request, carrying one synthetic image and the fixed question.
        self.assertEqual(post.call_count, 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "MiniMax-M3")
        content = payload["messages"][0]["content"]
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("不是用户试卷", content[0]["text"])

    def test_a_miscount_is_reported_as_a_model_that_cannot_see(self):
        body = {"choices": [{"message": {"content": "【答案】5"}}]}
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="MiniMax-M3"),
            mock.patch.object(reader_test.requests, "post", return_value=self._response(200, body)),
        ):
            result = reader_test.test_reading_connection("minimax")
        self.assertFalse(result["ok"])
        self.assertIn("看不懂图片", result["message"])

    def test_a_rejected_key_says_so(self):
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="MiniMax-M3"),
            mock.patch.object(reader_test.requests, "post", return_value=self._response(401)),
        ):
            result = reader_test.test_reading_connection("minimax")
        self.assertFalse(result["ok"])
        self.assertIn("密钥", result["message"])

    def test_a_used_up_quota_says_so(self):
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="MiniMax-M3"),
            mock.patch.object(reader_test.requests, "post", return_value=self._response(429)),
        ):
            result = reader_test.test_reading_connection("minimax")
        self.assertFalse(result["ok"])
        self.assertIn("额度", result["message"])

    def test_the_saved_key_is_never_written_into_the_message(self):
        body = {"choices": [{"message": {"content": "【答案】2"}}]}
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="offline-secret-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="MiniMax-M3"),
            mock.patch.object(reader_test.requests, "post", return_value=self._response(200, body)),
        ):
            result = reader_test.test_reading_connection("minimax")
        self.assertNotIn("offline-secret-key", result["message"])
        self.assertNotIn("offline-secret-key", json.dumps(result, ensure_ascii=False))

    def test_modelscope_disables_thinking_and_retries_400_without_optional_flag(self):
        err_400 = self._response(400, {"error": {"message": "enable_thinking is not supported"}})
        ok_200 = self._response(200, {"choices": [{"message": {"content": "【答案】2"}}]})
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="ms-offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="Qwen/Qwen3.5-35B-A3B"),
            mock.patch.object(reader_test.requests, "post", side_effect=[err_400, ok_200]) as post,
        ):
            result = reader_test.test_reading_connection("modelscope")
        self.assertTrue(result["ok"])
        self.assertEqual(post.call_count, 2)
        self.assertIs(post.call_args_list[0].kwargs["json"]["enable_thinking"], False)
        self.assertNotIn("enable_thinking", post.call_args_list[1].kwargs["json"])

    def test_thinking_exhaustion_is_reported_clearly_and_upstream_detail_is_sanitized(self):
        thinking_body = {
            "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning_content": "let me think..."}}]
        }
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="ms-offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="Qwen/Qwen3.5-35B-A3B"),
            mock.patch.object(reader_test.requests, "post", return_value=self._response(200, thinking_body)),
        ):
            result = reader_test.test_reading_connection("modelscope")
        self.assertFalse(result["ok"])
        self.assertIn("思考过程中未输出正文", result["message"])

        err_400 = self._response(400, {"error": {"message": "Model Qwen/Bad not found with sk-secret-123"}})
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="ms-offline-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="Qwen/Bad"),
            mock.patch.object(reader_test.requests, "post", return_value=err_400),
        ):
            bad_result = reader_test.test_reading_connection("modelscope")
        self.assertFalse(bad_result["ok"])
        self.assertIn("HTTP 400", bad_result["message"])
        self.assertIn("not found", bad_result["message"])
        self.assertNotIn("sk-secret-123", bad_result["message"])

    def test_saved_model_reads_configured_preferences_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            pref_file = Path(tmp) / "prefs.json"
            with mock.patch.dict(os.environ, {"QB_MODEL_PREFERENCES_FILE": str(pref_file)}):
                preferences.save_configuration(
                    dict(preferences.DEFAULTS), {**preferences.DEFAULT_MODELS, "modelscope": "Qwen/Qwen3.5-122B-A10B"}
                )
                self.assertEqual(reader_test._saved_model("modelscope"), "Qwen/Qwen3.5-122B-A10B")

    def test_modelscope_email_verification_error_returns_clear_chinese_guidance(self):
        err_401 = self._response(
            401,
            {
                "error": {
                    "message": "For account security, please complete your profile via email verification. ModelScope Magi"
                }
            },
        )
        with (
            mock.patch.object(reader_test.credential_settings, "reveal_saved_key", return_value="ms-secret-key"),
            mock.patch.object(reader_test, "_saved_model", return_value="Qwen/Qwen3.5-35B-A3B"),
            mock.patch.object(reader_test.requests, "post", return_value=err_401),
        ):
            result = reader_test.test_reading_connection("modelscope")
        self.assertFalse(result["ok"])
        self.assertIn("尚未完成邮箱验证", result["message"])
        self.assertNotIn("ms-secret-key", result["message"])
