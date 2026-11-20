"""统一 API 配置接口、凭据池迁移与安全契约的离线单元测试。"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import Client, TestCase

from . import api_config, credential_settings, library_ai_settings, provider_catalog, reader_test


def _fake_dpapi(data: bytes, *, protect: bool) -> bytes:
    """跨平台离线测试用的可逆伪 DPAPI 加密边界。"""

    return b"dpapi:" + base64.b64encode(data) if protect else base64.b64decode(data.removeprefix(b"dpapi:"))


class UnifiedApiConfigTests(TestCase):
    """覆盖 /api/settings/api-config 全流程及历史密钥无损迁移。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env_patch = mock.patch.dict(
            os.environ,
            {
                "QB_CREDENTIAL_FILE": str(self.root / "credentials.dat"),
                "QB_MODEL_PREFERENCES_FILE": str(self.root / "model-preferences.json"),
                "QB_LIBRARY_AI_SETTINGS_FILE": str(self.root / "library-ai-settings.json"),
                "QB_FEATURES_FILE": str(self.root / "features.json"),
                # 统一模式下清除独立 legacy credential 覆盖，触发与 credentials.dat 的合一路径
                "QB_LIBRARY_AI_CREDENTIAL_FILE": "",
                **{
                    name: val
                    for service in provider_catalog.ALL_SERVICES
                    for name, val in (
                        (f"QB_{service.upper()}_CONFIGURED", "0"),
                        (f"QB_{service.upper()}_POOL_SIZE", "0"),
                    )
                },
            },
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)
        transform_patch = mock.patch.object(credential_settings.store, "_transform", side_effect=_fake_dpapi)
        self.transform_mock = transform_patch.start()
        self.addCleanup(transform_patch.stop)
        self.client = Client()

    def _same_origin_headers(self) -> dict[str, str]:
        return {
            "HTTP_X_QB_REQUEST": "1",
            "HTTP_HOST": "127.0.0.1:8768",
            "HTTP_ORIGIN": "http://127.0.0.1:8768",
            "HTTP_SEC_FETCH_SITE": "same-origin",
        }

    def test_get_never_decrypts_credentials_and_exposes_all_seven_services(self) -> None:
        credential_settings.save_all_actions(
            {
                "mineru": {"action": "replace", "accounts": ["secret-mineru-token-abc"]},
                "deepseek": {"action": "replace", "accounts": ["sk-deepseek-secret-xyz"]},
            }
        )
        credential_settings.refresh_public_environment(include_all=True)

        with mock.patch.object(
            credential_settings.store, "load_credentials", side_effect=AssertionError("GET 不得解密凭据")
        ):
            response = self.client.get("/api/settings/api-config")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body["credentials"]), set(provider_catalog.ALL_SERVICES))
        self.assertTrue(body["credentials"]["mineru"]["configured"])
        self.assertTrue(body["credentials"]["deepseek"]["configured"])
        self.assertFalse(body["credentials"]["minimax"]["configured"])
        raw_text = response.content.decode("utf-8")
        self.assertNotIn("secret-mineru", raw_text)
        self.assertNotIn("sk-deepseek", raw_text)

    def test_single_save_persists_credentials_reading_and_answering(self) -> None:
        response = self.client.post(
            "/api/settings/api-config",
            data=json.dumps(
                {
                    "credentials": {
                        "mineru": {"action": "replace", "accounts": ["mineru-tok-1"]},
                        "modelscope": {"action": "replace", "accounts": ["ms-key-1", "ms-key-2"]},
                        "deepseek": {"action": "replace", "accounts": ["ds-key-1"]},
                    },
                    "reading": {
                        "primary": "modelscope_qwen",
                        "models": {"modelscope": "Qwen/Qwen3.5-35B-A3B"},
                        "plans": {"minimax": "plus"},
                    },
                    "answering": {
                        "mode": "api",
                        "provider": "deepseek",
                        "model": "deepseek-v4-pro",
                        "base_url": "https://api.deepseek.com",
                        "supports_images": False,
                        "thinking": True,
                        "features": {"ai_answer": True},
                        "on_intake": {"answer": True},
                    },
                }
            ),
            content_type="application/json",
            **self._same_origin_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["credentials"]["mineru"]["count"], 1)
        self.assertEqual(data["credentials"]["modelscope"]["count"], 2)
        self.assertEqual(data["credentials"]["deepseek"]["count"], 1)
        self.assertEqual(data["reading"]["primary"], "modelscope_qwen")
        self.assertEqual(data["answering"]["mode"], "api")
        self.assertEqual(data["answering"]["provider"], "deepseek")
        self.assertTrue(data["answering"]["key_configured"])
        self.assertTrue(data["answering"]["on_intake"]["answer"])
        self.assertTrue(data["answering"]["features"]["ai_answer"])

    def test_invalid_save_does_not_partially_mutate_credentials(self) -> None:
        response = self.client.post(
            "/api/settings/api-config",
            data=json.dumps(
                {
                    "credentials": {"mineru": {"action": "replace", "accounts": ["mineru-tok-ok"]}},
                    "reading": {"primary": "nonexistent_engine"},
                }
            ),
            content_type="application/json",
            **self._same_origin_headers(),
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.root / "credentials.dat").exists())

    def test_inactive_key_edit_preserves_verified_while_active_key_change_invalidates(self) -> None:
        api_config.save_payload(
            {
                "credentials": {"deepseek": {"action": "replace", "accounts": ["sk-deepseek-v1"]}},
                "answering": {
                    "mode": "api",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "base_url": "https://api.deepseek.com",
                    "supports_images": False,
                    "thinking": True,
                },
            }
        )
        probe_response = mock.Mock(status_code=200)
        probe_response.json.return_value = {
            "model": "deepseek-v4-pro",
            "choices": [{"finish_reason": "stop", "message": {"content": "【答案】2", "reasoning_content": "1+1=2"}}],
        }
        with mock.patch.object(library_ai_settings.requests, "post", return_value=probe_response):
            test_res = self.client.post(
                "/api/settings/api-config/test",
                data=json.dumps({"target": "answering", "confirm": True}),
                content_type="application/json",
                **self._same_origin_headers(),
            )
        self.assertEqual(test_res.status_code, 200)
        self.assertTrue(test_res.json()["ok"])

        # 修改未激活的服务（doubao / mineru），不应清掉当前 deepseek 的 verified 状态
        update_other = api_config.save_payload(
            {
                "credentials": {
                    "mineru": {"action": "replace", "accounts": ["mineru-new"]},
                    "doubao": {"action": "replace", "accounts": ["doubao-new"]},
                }
            }
        )
        self.assertTrue(update_other["answering"]["verified"])

        # 修改当前激活的服务（deepseek），必须重置 verified 为 False
        update_active = api_config.save_payload(
            {"credentials": {"deepseek": {"action": "replace", "accounts": ["sk-deepseek-v2"]}}}
        )
        self.assertFalse(update_active["answering"]["verified"])

    def test_unified_reveal_enforces_local_same_origin_and_returns_saved_key(self) -> None:
        api_config.save_payload(
            {
                "credentials": {
                    "deepseek": {"action": "replace", "accounts": ["sk-reveal-deepseek-99"]},
                    "doubao": {"action": "replace", "accounts": ["sk-reveal-doubao-11"]},
                }
            }
        )
        # 跨站来源拒绝
        forbidden = self.client.post(
            "/api/settings/api-config/key/reveal",
            data=json.dumps({"service": "deepseek", "index": 0}),
            content_type="application/json",
            HTTP_HOST="127.0.0.1:8768",
            HTTP_ORIGIN="https://evil.example",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        self.assertEqual(forbidden.status_code, 403)

        ok = self.client.post(
            "/api/settings/api-config/key/reveal",
            data=json.dumps({"service": "deepseek", "index": 0}),
            content_type="application/json",
            **self._same_origin_headers(),
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["key"], "sk-reveal-deepseek-99")

    def test_lossless_migration_from_legacy_library_ai_dat_files(self) -> None:
        # 模拟历史遗留的两份独立 DPAPI 密钥文件（doubao 放在 library-ai.dat，deepseek 放在 library-ai-deepseek.dat）
        doubao_dat = self.root / "library-ai.dat"
        deepseek_dat = self.root / "library-ai-deepseek.dat"
        doubao_dat.write_bytes(
            _fake_dpapi(json.dumps({"version": 1, "revision": "r1", "key": "legacy-doubao-k"}).encode(), protect=True)
        )
        deepseek_dat.write_bytes(
            _fake_dpapi(json.dumps({"version": 1, "revision": "r2", "key": "legacy-ds-k"}).encode(), protect=True)
        )

        # 统一池里已有另一把 minimax 密钥，迁移绝不能覆盖已有服务
        credential_settings.save_all_actions({"minimax": {"action": "replace", "accounts": ["existing-minimax"]}})

        first = self.client.get("/api/settings/api-config")
        self.assertEqual(first.status_code, 200)
        body = first.json()
        self.assertTrue(body["credentials"]["doubao"]["configured"])
        self.assertTrue(body["credentials"]["deepseek"]["configured"])
        self.assertTrue(body["credentials"]["minimax"]["configured"])
        self.assertEqual(credential_settings.reveal_saved_key("doubao", 0), "legacy-doubao-k")
        self.assertEqual(credential_settings.reveal_saved_key("deepseek", 0), "legacy-ds-k")
        # 原文件保留作为只读备份
        self.assertTrue(doubao_dat.exists())
        self.assertTrue(deepseek_dat.exists())

        # 用户后续从统一池清除了 deepseek 后，再次 GET 不得从旧备份复活
        api_config.save_payload({"credentials": {"deepseek": {"action": "clear"}}})
        second = self.client.get("/api/settings/api-config")
        self.assertFalse(second.json()["credentials"]["deepseek"]["configured"])

    def test_reading_connection_probe_requires_explicit_confirm(self) -> None:
        no_confirm = self.client.post(
            "/api/settings/api-config/test",
            data=json.dumps({"target": "reading", "service": "modelscope"}),
            content_type="application/json",
            **self._same_origin_headers(),
        )
        self.assertEqual(no_confirm.status_code, 400)

        with mock.patch.object(
            reader_test, "test_reading_connection", return_value={"ok": True, "service": "modelscope", "message": "ok"}
        ) as probe:
            ok = self.client.post(
                "/api/settings/api-config/test",
                data=json.dumps({"target": "reading", "service": "modelscope", "confirm": True}),
                content_type="application/json",
                **self._same_origin_headers(),
            )
        self.assertEqual(ok.status_code, 200)
        probe.assert_called_once_with("modelscope")
