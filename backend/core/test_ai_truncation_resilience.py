"""截断不该让整站 AI 停摆：输出超长是内容问题，不是服务不可用。

背景：MiniMax M3.1 的自适应思考会吃掉 token 预算，题目一长就 finish_reason=length。
以前这条被当成 ConnectionError，_mark_unverified 把「已验证」清掉，于是全站
AI 生成暂停、用户还得手动去点一次连接测试才能恢复——一次内容超长，赔上整个功能。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.test import SimpleTestCase

from . import library_ai_settings as service

WINDOWS_DPAPI_TRANSFORM = service.credential_settings.store._transform


def protected_test_bytes(data, *, protect):
    return b"test-dpapi:" + data if protect else data.removeprefix(b"test-dpapi:")


def response(finish_reason="stop", text="【答案】2【图示】2", model="minimax-offline-pro"):
    payload = {
        "model": model,
        "choices": [
            {"finish_reason": finish_reason, "message": {"content": text, "reasoning_content": "offline reasoning"}}
        ],
    }
    item = mock.Mock(status_code=200)
    item.json.return_value = payload
    return item


class TruncationResilienceTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        environment = mock.patch.dict(
            os.environ,
            {
                "QB_LIBRARY_AI_SETTINGS_FILE": str(root / "settings.json"),
                "QB_LIBRARY_AI_CREDENTIAL_FILE": str(root / "library-ai.dat"),
                "QB_FEATURES_FILE": str(root / "features.json"),
                "QB_CREDENTIAL_FILE": str(root / "ocr-credentials.dat"),
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        transform = mock.patch.object(service.credential_settings.store, "_transform", side_effect=protected_test_bytes)
        transform.start()
        self.addCleanup(transform.stop)
        self.network = mock.patch.object(service.requests, "post").start()
        self.addCleanup(mock.patch.stopall)

    def configure_verified(self):
        service.save(
            {"mode": "api", "provider": "minimax", "key": {"action": "replace", "value": "offline-minimax-key"}}
        )
        self.network.side_effect = None
        self.network.return_value = response()
        service.test_connection({"confirm": True})
        self.network.reset_mock()
        self.network.side_effect = None
        return service.public_status()

    def test_truncated_generation_is_retried_once_with_a_larger_budget(self):
        before = self.configure_verified()
        self.assertTrue(before["api_ready"])

        self.network.return_value = response(finish_reason="length")
        self.network.side_effect = [response(finish_reason="length"), response()]

        text, engine = service.chat("题干", [], kind=None, max_tokens=2000)

        self.assertIn("2", text)
        self.assertTrue(engine)
        self.assertEqual(self.network.call_count, 2, "超长必须只重试一次，不能无限烧钱")
        first = self.network.call_args_list[0].kwargs["json"]
        second = self.network.call_args_list[1].kwargs["json"]
        first_budget = first.get("max_completion_tokens") or first.get("max_tokens")
        second_budget = second.get("max_completion_tokens") or second.get("max_tokens")
        self.assertGreater(second_budget, first_budget, "重试必须给更大的 token 预算")
        self.assertTrue(service.public_status()["api_ready"], "超长不许把 API 打成未验证")

    def test_repeated_truncation_raises_an_actionable_error_without_pausing_the_api(self):
        self.configure_verified()
        self.network.side_effect = [response(finish_reason="length")] * 2

        with self.assertRaises(service.ServiceError) as caught:
            service.chat("特别长的题干", [], kind=None, max_tokens=2000)

        message = str(caught.exception)
        self.assertIn("长度", message)
        self.assertTrue(service.public_status()["api_ready"], "内容超长不是服务故障，不该停摆")

    def test_real_transport_failures_still_mark_the_api_unverified(self):
        self.configure_verified()
        self.network.side_effect = requests.RequestException("offline network down")

        with self.assertRaises(service.ConnectionError):
            service.chat("题干", [], kind=None, max_tokens=2000)

        self.assertFalse(service.public_status()["api_ready"], "真的连不上服务时仍必须让用户重新做一次连接测试")

    def test_the_connection_probe_still_rejects_a_truncated_answer(self):
        # 探针本身被截断＝没验成，仍然算未验证（这条不能被上面的修复带偏）。
        service.save(
            {"mode": "api", "provider": "minimax", "key": {"action": "replace", "value": "offline-minimax-key"}}
        )
        self.network.side_effect = None
        self.network.return_value = response(finish_reason="length")
        with self.assertRaises(service.ServiceError):
            service.test_connection({"confirm": True})
        self.assertFalse(service.public_status()["api_ready"])
