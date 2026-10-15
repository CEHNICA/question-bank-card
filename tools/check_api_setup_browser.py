"""Exercise the real API setup UI against synthetic, local browser fixtures.

--run serves frontend files from an owned temporary loopback HTTP server. Every
API response is a Playwright fixture; no backend, database, credential store,
worker, service test, AI generation or external HTTP request is used.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread
import traceback
from urllib.parse import urlparse
import uuid

from playwright.async_api import async_playwright, expect


ROOT = Path(__file__).resolve().parents[1]
READING_PROVIDERS = ("modelscope", "minimax", "siliconflow")
READING_INPUTS = {
    "mineru": "credentialMineruInput", "modelscope": "credentialModelscopeInput",
    "minimax": "credentialMinimaxInput", "siliconflow": "credentialSiliconflowInput",
}
PROFILES = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "Offline-DeepSeek", "supports_images": False, "thinking": True},
    "minimax": {"base_url": "https://api.minimax.cn/v1", "model": "Offline-MiniMax", "supports_images": True, "thinking": True},
    "modelscope": {"base_url": "https://api-inference.modelscope.cn/v1", "model": "Offline-ModelScope", "supports_images": True, "thinking": True},
    "siliconflow": {"base_url": "https://api.siliconflow.cn/v1", "model": "Offline-SiliconFlow", "supports_images": True, "thinking": False},
    "doubao": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "Offline-Doubao", "supports_images": False, "thinking": True},
    "custom": {"base_url": "https://example.invalid/v1", "model": "Offline-Custom", "supports_images": False, "thinking": True},
}
READING_CARD_IDS = {
    "modelscope": "credentialModelscopeSection", "minimax": "credentialMinimaxSection",
    "siliconflow": "credentialSiliconflowSection",
}


class FrontendHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/settings", "/settings/"):
            self.path = "/index.html"
        elif urlparse(self.path).path.startswith("/static/"):
            self.path = self.path[len("/static"):]
        super().do_GET()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def redact(value):
    if isinstance(value, dict):
        return {key: ("****************" if key in ("value", "accounts", "key_value") else redact(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


class Fixture:
    def __init__(self):
        self.services = {provider: {"configured": provider != "mineru", "count": int(provider != "mineru")}
                         for provider in READING_INPUTS}
        self.models = {"primary": "modelscope_fake", "checker": "auto", "arbiter": "primary",
                       "models": {provider: PROFILES[provider]["model"] for provider in READING_PROVIDERS},
                       "plans": {"minimax": "auto"}}
        self.library = {
            "mode": "api", "provider": "deepseek", **deepcopy(PROFILES["deepseek"]),
            "features": {"knowledge_tags": False, "ai_answer": False},
            "on_intake": {"tags": False, "answer": False}, "verified": True,
        }
        self.profiles = deepcopy(PROFILES)
        self.keys = {provider: {"configured": provider == "deepseek", "count": int(provider == "deepseek"),
                                "shared_with_reading": False} for provider in PROFILES}
        self.posts = []
        self.api_gets = []
        self.forbidden = []
        self.fail_credentials = False
        self.fail_library = False
        self.share_requests = []

    def status(self):
        configured = {provider: meta["configured"] for provider, meta in self.services.items()}
        primary = self.models["primary"]
        names = {"modelscope": "魔搭", "minimax": "MiniMax", "siliconflow": "硅基流动"}
        return {"app_version": "isolated-api-setup-test", "mineru": configured["mineru"],
                "upload_enabled": configured["mineru"], "assistant_mode": False, "configured": configured,
                "reader": self.models["models"][primary.rsplit("_fake", 1)[0]],
                "m3_available": False, "engines": {
                    "saved": deepcopy(self.models), "selected": {key: self.models[key] for key in ("primary", "checker", "arbiter")},
                    "primary": primary, "checker": self.models["checker"], "arbiter": self.models["arbiter"],
                    "models": deepcopy(self.models["models"]), "plans": deepcopy(self.models["plans"]),
                    "configured": configured, "choices": [
                        {"key": provider + "_fake", "provider_key": provider, "provider": names[provider],
                         "model": self.models["models"][provider], "available": configured[provider]}
                        for provider in READING_PROVIDERS]}}

    def library_status(self):
        body = deepcopy(self.library)
        configured = self.keys[body["provider"]]["configured"]
        verified = bool(configured and body["verified"])
        body.update({
            "configured": configured, "ready": verified, "api_ready": verified,
            "key_configured": configured, "key_count": int(configured), "keys": deepcopy(self.keys),
            "shareable_from_reading": ["minimax", "modelscope", "siliconflow"],
            "provider_profiles": deepcopy(self.profiles), "reasoning_effort": "high",
            "knowledge": {"total": 0, "chapters": 0, "file": ""},
            "backlog": {"tags": 0, "answer": 0, "total": 0},
            "verified_at": "fictional-offline-fixture" if verified else "", "resolved_model": "",
            "status": "verified" if verified else "unverified" if configured else "missing",
            "message": "虚构离线状态；没有调用服务。",
        })
        return body

    @staticmethod
    def validate_fake_key(operation):
        if operation.get("action") != "replace":
            return
        values = operation.get("accounts") or [operation.get("value", "")]
        assert values and all(isinstance(value, str) and value.startswith("fake-offline-") for value in values), "Only fabricated keys are permitted"

    async def route(self, route, base):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.scheme != "http" or parsed.netloc != urlparse(base).netloc:
            self.forbidden.append({"kind": "external", "method": request.method, "path": parsed.path})
            await route.abort()
            return
        path, method = parsed.path, request.method
        if method in ("GET", "HEAD"):
            if path.startswith("/api/"):
                self.api_gets.append(path)
            values = {
                "/api/status": self.status(), "/api/papers": {"papers": []},
                "/api/settings/features": {"features": []},
                "/api/settings/credentials": {"services": deepcopy(self.services)},
                "/api/settings/library-ai": self.library_status(),
                "/api/export-preferences": {"directory": "", "desktop_capable": False},
            }
            if path in values:
                await route.fulfill(json=values[path])
            elif path.startswith("/api/"):
                self.forbidden.append({"kind": "unexpected-api", "method": method, "path": path})
                await route.abort()
            else:
                await route.continue_()
            return
        permitted = {"/api/settings/models", "/api/settings/credentials", "/api/settings/library-ai",
                     "/api/settings/library-ai/share-reading-key"}
        if method != "POST" or path not in permitted:
            self.forbidden.append({"kind": "unexpected-write", "method": method, "path": path})
            await route.abort()
            return
        payload = deepcopy(request.post_data_json)
        self.posts.append({"path": path, "payload": payload})
        if path == "/api/settings/models":
            assert payload["primary"] in {provider + "_fake" for provider in READING_PROVIDERS}
            self.models.update(payload)
            await route.fulfill(json={"saved": deepcopy(self.models)})
        elif path == "/api/settings/credentials":
            for provider, operation in payload["services"].items():
                assert provider in self.services
                self.validate_fake_key(operation)
            if self.fail_credentials:
                self.fail_credentials = False
                await route.fulfill(status=503, json={"error": "离线模拟保存失败，未保存内容应保留"})
                return
            for provider, operation in payload["services"].items():
                if operation["action"] == "replace":
                    self.services[provider] = {"configured": True, "count": len(operation["accounts"])}
                elif operation["action"] == "clear":
                    self.services[provider] = {"configured": False, "count": 0}
            await route.fulfill(json={"services": deepcopy(self.services), "message": "虚构 API 配置已保存；没有网络测试。"})
        elif path.endswith("/share-reading-key"):
            assert set(payload) == {"provider"} and payload["provider"] in READING_PROVIDERS
            provider = payload["provider"]
            self.share_requests.append(provider)
            self.library.update(provider=provider, **deepcopy(PROFILES[provider]), verified=False)
            self.library["model"] = self.models["models"][provider]
            self.profiles[provider] = {field: self.library[field] for field in ("base_url", "model", "supports_images", "thinking")}
            self.keys[provider] = {"configured": True, "count": 1, "shared_with_reading": True}
            await route.fulfill(json=self.library_status())
        else:
            for operation in [payload.get("key", {}), *payload.get("keys", {}).values()]:
                self.validate_fake_key(operation)
            if self.fail_library:
                self.fail_library = False
                await route.fulfill(status=503, json={"error": "离线模拟答案配置保存失败"})
                return
            for field in ("provider", "base_url", "model", "supports_images", "thinking", "features", "on_intake", "mode"):
                if field in payload:
                    self.library[field] = deepcopy(payload[field])
            for provider, profile in payload.get("provider_profiles", {}).items():
                assert provider in self.profiles and provider != self.library["provider"]
                self.profiles[provider] = deepcopy(profile)
            self.profiles[self.library["provider"]] = {field: self.library[field] for field in ("base_url", "model", "supports_images", "thinking")}
            operations = deepcopy(payload.get("keys", {}))
            operations[self.library["provider"]] = payload.get("key", {"action": "keep"})
            for provider, operation in operations.items():
                if operation.get("action") == "replace":
                    self.keys[provider] = {"configured": True, "count": 1, "shared_with_reading": False}
                elif operation.get("action") == "clear":
                    self.keys[provider] = {"configured": False, "count": 0, "shared_with_reading": False}
            self.library["verified"] = False
            await route.fulfill(json=self.library_status())


async def browser_check(output, port, report):
    fixture = Fixture()
    base = f"http://127.0.0.1:{port}"
    errors = report["page_errors"]
    async with async_playwright() as pw:
        binary = next((path for path in (Path(pw.chromium.executable_path),
                      Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                      Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
        browser = await pw.chromium.launch(headless=True, **({"executable_path": str(binary)} if binary else {}))
        context = await browser.new_context(viewport={"width": 1366, "height": 768}, service_workers="block")
        await context.add_init_script("localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0')")
        await context.route("**/*", lambda route: fixture.route(route, base))
        page = await context.new_page()
        page.set_default_timeout(8000)
        page.on("pageerror", lambda error: errors.append({"message": str(error), "stack": error.stack}))
        page.on("dialog", lambda dialog: asyncio.create_task(dialog.dismiss()))

        async def screenshot(name, *, overview=False, clip_selector=None):
            # Real and fabricated saved keys remain masked; no reveal is used.
            for input_id in READING_INPUTS.values():
                assert await page.locator("#" + input_id).get_attribute("type") == "password"
            assert await page.locator('input[id^="libraryAIKey-"][type="text"]').count() == 0
            # Transient success/error popovers are visible above modal content.
            # Let their real timer finish so acceptance screenshots show the
            # permanent controls instead of a temporary notification overlay.
            await expect(page.locator("#toast")).not_to_be_visible(timeout=8000)
            if overview:
                await page.evaluate("() => {window.scrollTo(0,0); document.body.scrollTop=0; document.querySelectorAll('#credentialDialog .credential-body,#credentialDialog .library-ai-body').forEach(node=>node.scrollTop=0);}")
            if clip_selector:
                await page.locator(clip_selector).screenshot(path=str(output / name))
            else:
                await page.screenshot(path=str(output / name))
            report["screenshots"].append({"name": name, "path": str(output / name)})

        async def reading():
            await page.locator("#credentialReadingTab").click()
            await expect(page.locator("#credentialReadingPanel")).to_be_visible()

        async def answers():
            await page.locator("#credentialAnswerTab").click()
            await expect(page.locator("#credentialAnswerPanel")).to_be_visible()
            await expect(page.locator("#libraryAIProvider")).to_be_enabled()
            if not await page.locator("#libraryAIProvider").is_visible():
                await page.locator("#libraryAIAdvanced > summary").click()
            await expect(page.locator("#libraryAIProvider")).to_be_visible()

        async def selected_reading(provider):
            await page.locator("#credentialVisionProvider").select_option(provider)
            await page.wait_for_load_state("networkidle")
            await expect(page.locator("#" + READING_CARD_IDS[provider])).to_be_visible()
            for other, card_id in READING_CARD_IDS.items():
                if other != provider:
                    await expect(page.locator("#" + card_id)).not_to_be_visible()

        async def selected_library(provider):
            await page.locator("#libraryAIProvider").select_option(provider)
            await expect(page.locator("#libraryAIKeyBlock-" + provider)).to_be_visible()
            await expect(page.locator('[id^="libraryAIKeyBlock-"]:visible')).to_have_count(1)

        async def save_bar_in_view(button_id, label):
            await expect(page.locator("#" + button_id)).to_be_visible()
            position = await page.locator("#" + button_id).evaluate("node => {const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:innerHeight,width:innerWidth};}")
            assert position["top"] >= 0 and position["bottom"] <= position["height"] + 1, (label, position)
            assert position["left"] >= 0 and position["right"] <= position["width"] + 1, (label, position)
            report["save_bar_positions"].append({"label": label, **position})

        try:
            await page.goto(base + "/settings", wait_until="networkidle")
            await expect(page.locator("#settingsCredentialOpen")).to_be_visible()
            await expect(page.locator("#settingsGeneral .api-status-list")).to_have_count(0)
            await page.locator("#settingsCredentialOpen").click()
            await expect(page.locator("#credentialDialog")).to_be_visible()
            await expect(page.locator('#credentialReadingPanel [data-service="mineru"]')).to_contain_text("云端自动切题必填")
            await expect(page.locator("#credentialVisionTitle")).to_be_visible()
            await expect(page.locator("#credentialVisionProvider")).to_have_value("modelscope")
            await selected_reading("modelscope")
            await screenshot("api-reading-two-steps-desktop.png", overview=True)
            await page.locator("#credentialModelscopeSection").scroll_into_view_if_needed()
            await screenshot("api-reading-selected-key-desktop.png", clip_selector="#credentialModelscopeSection")
            report["passed"].append("settings has one API entry and no four-provider status list; the real credential window presents the cloud-cut and selected vision-service steps")

            reading_key = "fake-offline-reading-modelscope-draft"
            mineru_key = "fake-offline-mineru-draft"
            await page.locator("#credentialModelscopeInput").fill(reading_key)
            await page.locator("#credentialMineruInput").fill(mineru_key)
            await selected_reading("minimax")
            await selected_reading("siliconflow")
            await selected_reading("modelscope")
            await expect(page.locator("#credentialModelscopeInput")).to_have_value(reading_key)
            assert not [post for post in fixture.posts if post["path"] == "/api/settings/credentials"]
            report["passed"].append("only the selected reading provider key card is visible; changing providers keeps every unsaved password without saving or revealing it")

            before_answer_open = len(fixture.posts)
            await answers()
            await expect(page.locator("#libraryAIProvider")).to_have_value("deepseek")
            await expect(page.locator("#libraryAIKeyBlock-deepseek")).to_be_visible()
            await expect(page.locator('[id^="libraryAIKeyBlock-"]:visible')).to_have_count(1)
            assert len(fixture.posts) == before_answer_open
            assert fixture.library["provider"] == "deepseek" and fixture.keys["deepseek"]["configured"]
            await screenshot("api-independent-deepseek-desktop.png", overview=True)
            for width, height in ((1366, 768), (390, 844)):
                await page.set_viewport_size({"width": width, "height": height})
                await page.evaluate("() => document.querySelector('#credentialDialog .library-ai-body').scrollTop=0")
                await expect(page.locator("#libraryAIUseReadingService")).to_be_visible()
                reuse = await page.locator("#libraryAIUseReadingService").evaluate("node => {const r=node.getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:innerHeight,width:innerWidth};}")
                assert reuse["top"] >= 0 and reuse["bottom"] <= height, reuse
                assert reuse["left"] >= 0 and reuse["right"] <= width, reuse
                assert await page.locator("#libraryAIReadingReuse").evaluate("node => {const state=document.getElementById('libraryAIState'), features=document.querySelector('[aria-label=\"分别开启功能\"]'); return !!(state.compareDocumentPosition(node)&Node.DOCUMENT_POSITION_FOLLOWING) && !!(node.compareDocumentPosition(features)&Node.DOCUMENT_POSITION_FOLLOWING);}"), "Reuse action belongs between status and feature switches"
                report["reuse_action_positions"].append({"label": f"independent answers {width}x{height}", **reuse})
                if width == 390:
                    await screenshot("api-independent-deepseek-390x844.png", overview=True)
            await page.set_viewport_size({"width": 1366, "height": 768})
            library_key = "fake-offline-library-deepseek-draft"
            await page.locator("#libraryAIKey-deepseek").fill(library_key)
            library_model_draft = "Offline-DeepSeek-Draft"
            library_url_draft = "https://example.invalid/deepseek-draft"
            minimax_model_draft = "Offline-MiniMax-Draft"
            minimax_url_draft = "https://example.invalid/minimax-draft"
            if not await page.locator("#libraryAIModel").is_visible():
                await page.locator("#libraryAIAdvanced > summary").click()
            await page.locator("#libraryAIModel").fill(library_model_draft)
            await page.locator("#libraryAIBaseURL").fill(library_url_draft)
            await selected_library("minimax")
            await expect(page.locator("#libraryAIActiveState")).to_contain_text("未保存选择")
            await page.locator("#libraryAIModel").fill(minimax_model_draft)
            await page.locator("#libraryAIBaseURL").fill(minimax_url_draft)
            await selected_library("deepseek")
            await expect(page.locator("#libraryAIKey-deepseek")).to_have_value(library_key)
            await expect(page.locator("#libraryAIModel")).to_have_value(library_model_draft)
            await expect(page.locator("#libraryAIBaseURL")).to_have_value(library_url_draft)
            await selected_library("minimax")
            await expect(page.locator("#libraryAIModel")).to_have_value(minimax_model_draft)
            await expect(page.locator("#libraryAIBaseURL")).to_have_value(minimax_url_draft)
            await selected_library("deepseek")
            await reading()
            await expect(page.locator("#credentialMineruInput")).to_have_value(mineru_key)
            await expect(page.locator("#credentialModelscopeInput")).to_have_value(reading_key)
            await answers()
            await expect(page.locator("#libraryAIKey-deepseek")).to_have_value(library_key)
            await screenshot("api-cross-tab-draft-retained.png")
            report["passed"].append("opening optional answers preserves independent DeepSeek; only the selected key card is shown, both tabs retain drafts and each provider keeps its own unsaved model/address")

            await reading()
            fixture.fail_credentials = True
            async with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/settings/credentials") as failed:
                await page.locator("#credentialSave").click()
            assert (await failed.value).status == 503
            await expect(page.locator("#credentialResult")).to_contain_text("离线模拟保存失败")
            await expect(page.locator("#credentialMineruInput")).to_have_value(mineru_key)
            await expect(page.locator("#credentialModelscopeInput")).to_have_value(reading_key)
            await expect(page.locator("#credentialSave")).to_be_enabled()
            attempted = len([post for post in fixture.posts if post["path"] == "/api/settings/credentials"])
            await page.wait_for_load_state("networkidle")
            assert len([post for post in fixture.posts if post["path"] == "/api/settings/credentials"]) == attempted
            await screenshot("api-reading-failure-retains-draft.png")
            async with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/settings/credentials") as saved:
                await page.locator("#credentialSave").click()
            assert (await saved.value).ok
            await expect(page.locator("#credentialResult")).to_contain_text("已保存")
            await expect(page.locator("#credentialMineruInput")).to_have_value("")
            await expect(page.locator("#credentialModelscopeInput")).to_have_value("")
            report["passed"].append("reading-key save failure preserves the draft without auto-retry; an explicit retry saves and clears only the now-saved input")

            await answers()
            await expect(page.locator("#libraryAIKey-deepseek")).to_have_value(library_key)
            if not await page.locator("#libraryAIModel").is_visible():
                await page.locator("#libraryAIAdvanced > summary").click()
            await expect(page.locator("#libraryAIModel")).to_have_value(library_model_draft)
            await expect(page.locator("#libraryAIBaseURL")).to_have_value(library_url_draft)
            await page.locator("#libraryAIAnswer").check()
            fixture.fail_library = True
            async with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/settings/library-ai") as failed:
                await page.locator("#libraryAISave").click()
            assert (await failed.value).status == 503
            await expect(page.locator("#libraryAIResult")).to_contain_text("离线模拟答案配置保存失败")
            await expect(page.locator("#libraryAIResult")).to_contain_text("重新填写")
            await expect(page.locator("#libraryAIKey-deepseek")).to_have_value("")
            await expect(page.locator("#libraryAIKey-deepseek")).to_be_enabled()
            await expect(page.locator("#libraryAISave")).to_be_disabled()
            await expect(page.locator("#libraryAIProvider")).to_have_value("deepseek")
            await expect(page.locator("#libraryAIModel")).to_have_value(library_model_draft)
            await expect(page.locator("#libraryAIAnswer")).to_be_checked()
            await screenshot("api-answers-failure-retains-draft.png")
            await page.locator("#libraryAIKey-deepseek").fill(library_key)
            await expect(page.locator("#libraryAISave")).to_be_enabled()
            async with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/settings/library-ai") as saved:
                await page.locator("#libraryAISave").click()
            assert (await saved.value).ok
            await expect(page.locator("#libraryAIKey-deepseek")).to_have_value("")
            saved_payload = next(post["payload"] for post in reversed(fixture.posts) if post["path"] == "/api/settings/library-ai")
            assert saved_payload["provider"] == "deepseek" and saved_payload["model"] == library_model_draft
            assert saved_payload["base_url"] == library_url_draft
            assert saved_payload["provider_profiles"]["minimax"]["model"] == minimax_model_draft
            assert saved_payload["provider_profiles"]["minimax"]["base_url"] == minimax_url_draft
            assert fixture.profiles["minimax"]["model"] == minimax_model_draft
            report["passed"].append("optional-answer failure preserves nonsecret drafts and requires re-entry of the cleared key; retry saves active settings plus the other provider's cached profile in one payload")

            reading_metadata = deepcopy(fixture.services)
            for provider in READING_PROVIDERS:
                await reading()
                await selected_reading(provider)
                await answers()
                before_share = len(fixture.share_requests)
                await expect(page.locator("#libraryAIUseReadingService")).to_be_enabled()
                async with page.expect_response(lambda response: response.request.method == "POST" and urlparse(response.url).path == "/api/settings/library-ai/share-reading-key") as shared:
                    await page.locator("#libraryAIUseReadingService").click()
                    if await page.locator("#confirmDialog").is_visible():
                        await page.locator("#confirmOk").click()
                assert (await shared.value).ok
                assert fixture.share_requests[before_share:] == [provider]
                await expect(page.locator("#libraryAIProvider")).to_have_value(provider)
                await expect(page.locator("#libraryAIKeyBlock-" + provider)).to_be_visible()
                await expect(page.locator('[id^="libraryAIKeyBlock-"]:visible')).to_have_count(1)
                assert fixture.keys[provider]["shared_with_reading"] is True
                assert fixture.services == reading_metadata
                assert fixture.library["features"] == {"knowledge_tags": False, "ai_answer": True}
                assert fixture.library["on_intake"] == {"tags": False, "answer": False}
                await screenshot(f"api-explicit-use-reading-{provider}.png")
            assert fixture.share_requests == list(READING_PROVIDERS)
            report["passed"].append("the explicit Use reading service action posts exactly once for ModelScope, MiniMax and SiliconFlow, selecting the matching service without changing reading keys or starting generation")

            for width, height in ((1366, 768), (390, 844)):
                await page.set_viewport_size({"width": width, "height": height})
                await reading()
                await save_bar_in_view("credentialSave", f"reading {width}x{height}")
                await screenshot(f"api-reading-{width}x{height}.png", overview=True)
                await answers()
                await save_bar_in_view("libraryAISave", f"answers {width}x{height}")
                await screenshot(f"api-answers-{width}x{height}.png", overview=True)
                if width == 390:
                    bounds = await page.locator("#credentialDialog").bounding_box()
                    assert bounds and bounds["x"] <= 1 and bounds["x"] + bounds["width"] >= width - 1, bounds
            report["passed"].append("reading and optional-answer save controls are visible inside the viewport at 1366x768 and 390x844")

            await page.set_viewport_size({"width": 1366, "height": 768})
            await page.locator("#credentialDialog [data-close]").first.click()
            await expect(page.locator("#credentialDialog")).not_to_be_visible()
            await page.goto(base + "/settings#services", wait_until="networkidle")
            await expect(page.locator("#settingsGeneral")).to_be_visible()
            if not await page.locator("#credentialDialog").is_visible():
                await page.locator("#settingsCredentialOpen").click()
            await expect(page.locator("#credentialReadingPanel")).to_be_visible()
            await screenshot("api-legacy-services-link.png")
            # Reset only this in-memory fixture before reloading: an existing
            # independent choice must survive the legacy answers entry too.
            fixture.library.update(provider="deepseek", **deepcopy(PROFILES["deepseek"]), verified=True)
            before_legacy = len(fixture.posts)
            await page.goto(base + "/settings#api", wait_until="networkidle")
            await expect(page.locator("#credentialAnswerPanel")).to_be_visible()
            await expect(page.locator("#libraryAIProvider")).to_have_value("deepseek")
            assert len(fixture.posts) == before_legacy
            await screenshot("api-legacy-api-link.png")
            report["passed"].append("legacy /settings#services and /settings#api remain valid entries in the same credential window and never auto-replace independent DeepSeek")

            assert not errors, errors
            assert not fixture.forbidden, fixture.forbidden
            assert not any("reveal" in post["path"] or post["path"].endswith("/test") for post in fixture.posts)
            assert all(path.startswith("/api/settings/") for path in (post["path"] for post in fixture.posts))
            report["passed"].append("all credentials are fictional and masked; no key reveal, API connection test, AI generation, real database, worker or outside request occurs")
            report["success"] = True
        except Exception as error:
            report.update(error=str(error) or type(error).__name__, traceback=traceback.format_exc())
            try:
                await page.screenshot(path=str(output / "failure.png"))
            except Exception:
                pass
        finally:
            report.update({"fixture_posts": redact(fixture.posts), "fixture_api_gets": fixture.api_gets,
                           "forbidden_requests": fixture.forbidden, "shared_reading_providers": fixture.share_requests})
            await context.close()
            await browser.close()
            report["browser_stopped"] = True


def run(args):
    frontend = Path(args.frontend or ROOT / "frontend").resolve()
    required = ("index.html", "app.js", "library-ai-settings.js", "styles.css")
    for name in required:
        if not (frontend / name).is_file():
            raise ValueError(f"Frontend directory is missing {name}: {frontend}")
    output = Path(args.output or ROOT / "tmp" / ("api-setup-browser-" + uuid.uuid4().hex[:10])).resolve()
    if not output.is_relative_to((ROOT / "tmp").resolve()) or output.exists():
        raise ValueError("Choose a new evidence folder under checkout/tmp; existing results are never overwritten")
    output.mkdir(parents=True)
    report = {"success": False, "passed": [], "page_errors": [], "forbidden_requests": [], "save_bar_positions": [], "reuse_action_positions": [], "screenshots": [],
              "frontend_directory": str(frontend),
              "frontend_sha256": {name: hashlib.sha256((frontend / name).read_bytes()).hexdigest() for name in required},
              "real_user_data_used": False, "real_credentials_used": False, "worker_started": False,
              "cloud_called": False, "api_responses_are_browser_fixtures": True, "server_stopped": False}
    server = None
    thread = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(FrontendHandler, directory=str(frontend)))
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        report["port"] = server.server_address[1]
        asyncio.run(browser_check(output, report["port"], report))
    except Exception as error:
        report.update(success=False, error=str(error) or type(error).__name__, traceback=traceback.format_exc())
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        report["server_stopped"] = thread is None or not thread.is_alive()
        write_json(output / "report.json", report)
    print(json.dumps({"success": report["success"], "passed_groups": len(report["passed"]),
                      "output": str(output), "error": report.get("error"),
                      "server_stopped": report["server_stopped"]}, ensure_ascii=False), flush=True)
    return 0 if report["success"] and report["server_stopped"] else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", help="New evidence directory under checkout/tmp")
    parser.add_argument("--frontend", help="Frontend directory to serve; supports the built _internal/frontend too")
    parser.add_argument("--port", type=int, default=0, help="Unused loopback port; 0 lets the OS choose")
    args = parser.parse_args()
    if not args.run:
        parser.error("Choose --run")
    raise SystemExit(run(args))
