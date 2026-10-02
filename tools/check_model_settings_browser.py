"""Check queued model-setting saves in the real UI with deterministic local APIs.

--run serves only frontend files at 127.0.0.1:8986. All APIs are browser fixtures;
no database, credentials, worker, model service or other external request is used.
The temporary server closes even if a check fails.
"""

import argparse
import asyncio
from copy import deepcopy
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "qa-faults-20261002" / "model-save-fixed"
PORT = 8986


class FrontendHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def check():
    saved = {"primary": "minimax_test", "checker": "auto", "arbiter": "primary",
             "models": {"minimax": "Offline-MiniMax", "modelscope": "Offline-Qwen", "siliconflow": "Offline-Silicon"},
             "plans": {"minimax": "auto"}}
    posts, forbidden, errors, checks = [], [], [], []
    control = {"hold_save": False, "fail_save": False, "hold_status": False,
               "save_started": asyncio.Event(), "save_release": asyncio.Event(),
               "status_started": asyncio.Event(), "status_release": asyncio.Event(), "configured": False}
    url = f"http://127.0.0.1:{PORT}"

    def status():
        return {"app_version": "isolated-browser-test", "mineru": control["configured"],
            "upload_enabled": control["configured"], "assistant_mode": saved["primary"] == "assistant",
            "configured": {"mineru": control["configured"], "minimax": control["configured"]}, "m3_available": False,
            "engines": {"saved": deepcopy(saved), "selected": {k: saved[k] for k in ("primary", "checker", "arbiter")},
                "primary": saved["primary"], "checker": saved["checker"], "arbiter": saved["arbiter"],
                "models": deepcopy(saved["models"]), "plans": deepcopy(saved["plans"]),
                "configured": {"minimax": control["configured"]},
                "choices": [{"key": key, "provider_key": provider, "provider": label, "model": model, "available": True}
                    for key, provider, label, model in (("minimax_test", "minimax", "MiniMax", "Offline-MiniMax"),
                    ("modelscope_test", "modelscope", "魔搭", "Offline-Qwen"),
                    ("siliconflow_test", "siliconflow", "硅基流动", "Offline-Silicon"))]}}

    async def route_all(route):
        request = route.request
        parsed = urlparse(request.url)
        if parsed.hostname != "127.0.0.1" or parsed.port != PORT:
            forbidden.append(f"External request: {request.method} {request.url}")
            await route.abort()
        elif parsed.path == "/api/status":
            payload = status()
            if control["hold_status"]:
                control["hold_status"] = False
                control["status_started"].set()
                await control["status_release"].wait()
            await route.fulfill(json=payload)
        elif parsed.path == "/api/settings/models" and request.method == "POST":
            body = deepcopy(request.post_data_json)
            posts.append(body)
            fail = control["fail_save"]
            control["fail_save"] = False
            if control["hold_save"]:
                control["hold_save"] = False
                control["save_started"].set()
                await control["save_release"].wait()
            if fail:
                await route.fulfill(status=503, json={"error": "离线模拟保存失败"})
            else:
                saved.update(body)
                await route.fulfill(json={"saved": deepcopy(saved)})
        elif request.method not in ("GET", "HEAD"):
            forbidden.append(f"Unexpected write: {request.method} {request.url}")
            await route.abort()
        elif parsed.path == "/api/papers":
            await route.fulfill(json={"papers": []})
        elif parsed.path == "/api/settings/features":
            await route.fulfill(json={"features": []})
        elif parsed.path.startswith("/api/"):
            forbidden.append(f"Unexpected API: {request.method} {request.url}")
            await route.abort()
        else:
            await route.continue_()

    async with async_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = await pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        await context.add_init_script("localStorage.setItem('qb-welcome-seen','1')")
        await context.route("**/*", route_all)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def open_settings():
            await page.locator("#settingsButton").click()
            await page.locator('[data-settings-tab="settingsModels"]').click()
            await page.wait_for_load_state("networkidle")

        async def advanced():
            if not await page.locator("#settingsModelscopeModel").is_visible():
                await page.locator(".model-id-details summary").click()

        async def screenshot(name):
            await page.screenshot(path=str(OUTPUT / name))

        def hold_next_save(fail=False):
            control.update(hold_save=True, fail_save=fail, save_started=asyncio.Event(), save_release=asyncio.Event())

        await page.goto(url + "/")
        await page.wait_for_load_state("networkidle")
        await open_settings()
        hold_next_save()
        offset = len(posts)
        await page.locator("#settingsPrimaryModel").select_option("modelscope_test")
        await asyncio.wait_for(control["save_started"].wait(), 5)
        await page.locator("#settingsCheckerModel").select_option("siliconflow_test")
        await page.locator("#settingsArbiterModel").select_option("checker")
        await screenshot("01-latest-model-intent-waiting.jpg")
        control["save_release"].set()
        await expect(page.locator("#settingsModelResult")).to_contain_text("已保存")
        await page.wait_for_load_state("networkidle")
        await expect(page.locator("#settingsCheckerModel")).to_have_value("siliconflow_test")
        await expect(page.locator("#settingsArbiterModel")).to_have_value("checker")
        assert saved["checker"] == "siliconflow_test" and saved["arbiter"] == "checker"
        assert posts[offset]["checker"] == "auto"
        assert posts[offset + 1]["checker"] == "siliconflow_test" and posts[offset + 1]["arbiter"] == "primary"
        assert posts[offset + 2]["arbiter"] == "checker"
        await screenshot("02-latest-model-intent-saved.jpg")
        checks.append({"case": "Delayed first save; three fast changes keep exact snapshots and final intent", "outcome": "PASS",
                       "requests": posts[offset:], "evidence": "02-latest-model-intent-saved.jpg"})

        # A currently typed model ID must also survive an unrelated save/status
        # response, before native change/blur submits the new text.
        hold_next_save()
        await page.locator('[data-settings-tab="settingsGeneral"]').click()
        await page.locator("#settingsMinimaxPlan").select_option("plus")
        await asyncio.wait_for(control["save_started"].wait(), 5)
        await page.locator('[data-settings-tab="settingsModels"]').click()
        await advanced()
        await page.locator("#settingsModelscopeModel").fill("Offline-Typed-While-Saving")
        control["save_release"].set()
        await expect(page.locator("#settingsModelResult")).to_contain_text("有新改动待保存")
        await expect(page.locator("#settingsModelscopeModel")).to_have_value("Offline-Typed-While-Saving")
        await screenshot("03-uncommitted-model-id-preserved.jpg")
        await page.locator("#settingsModelscopeModel").press("Tab")
        await expect(page.locator("#settingsModelResult")).to_contain_text("已保存")
        await page.wait_for_load_state("networkidle")
        assert saved["models"]["modelscope"] == "Offline-Typed-While-Saving"
        checks.append({"case": "New typed model ID survives an older response and saves on blur", "outcome": "PASS"})

        control["fail_save"] = True
        await page.locator("#settingsArbiterModel").select_option("siliconflow_test")
        await expect(page.locator("#settingsModelRetry")).to_be_visible()
        await expect(page.locator("#settingsArbiterModel")).to_have_value("siliconflow_test")
        await screenshot("04-failed-save-preserves-choice-and-retry.jpg")
        offset = len(posts)
        await page.locator("#settingsClose").click()
        await page.wait_for_load_state("networkidle")
        assert len(posts) == offset, "Closing an unchanged failed form must not silently retry"
        await open_settings()
        await expect(page.locator("#settingsModelRetry")).to_be_visible()
        await expect(page.locator("#settingsArbiterModel")).to_have_value("siliconflow_test")
        await page.locator("#settingsModelRetry").click()
        await expect(page.locator("#settingsModelResult")).to_contain_text("已保存")
        await page.wait_for_load_state("networkidle")
        await expect(page.locator("#settingsModelRetry")).not_to_be_visible()
        assert saved["arbiter"] == "siliconflow_test"
        await screenshot("05-retry-saves-retained-choice.jpg")
        checks.append({"case": "Failed latest save preserves form through close/reopen and explicit retry succeeds", "outcome": "PASS",
                       "evidence": ["04-failed-save-preserves-choice-and-retry.jpg", "05-retry-saves-retained-choice.jpg"]})

        hold_next_save(fail=True)
        await page.locator('[data-settings-tab="settingsGeneral"]').click()
        await page.locator("#settingsMinimaxPlan").select_option("max")
        await asyncio.wait_for(control["save_started"].wait(), 5)
        await page.locator('[data-settings-tab="settingsModels"]').click()
        await page.locator("#settingsArbiterModel").select_option("primary")
        control["save_release"].set()
        await expect(page.locator("#settingsModelResult")).to_contain_text("已保存")
        await page.wait_for_load_state("networkidle")
        assert saved["arbiter"] == "primary" and saved["plans"]["minimax"] == "max"
        await expect(page.locator("#settingsModelRetry")).not_to_be_visible()
        checks.append({"case": "A failed older save cannot stop the newer queued change", "outcome": "PASS"})

        for mode in ("Escape", "Close"):
            await advanced()
            offset = len(posts)
            value = "Offline-Exit-" + mode
            await page.locator("#settingsModelscopeModel").fill(value)
            if mode == "Escape":
                await page.locator("#settingsModelscopeModel").press("Escape")
            else:
                await page.locator("#settingsClose").click()
            await expect(page.locator("#settingsDialog")).not_to_be_visible()
            await expect(page.locator("#settingsModelResult")).to_contain_text("已保存")
            await page.wait_for_load_state("networkidle")
            assert saved["models"]["modelscope"] == value
            assert len(posts) == offset + 1, "Closing must submit one captured snapshot, not duplicate it"
            await open_settings()
            await advanced()
            await expect(page.locator("#settingsModelscopeModel")).to_have_value(value)
            checks.append({"case": f"Typed model ID saves exactly once on {mode} and persists after reopen", "outcome": "PASS"})
        await screenshot("06-model-id-escape-and-close-saved.jpg")

        # Hold an older status response from opening settings, finish a newer
        # model save/status refresh, then deliver the stale status last.
        await page.locator("#settingsClose").click()
        control.update(hold_status=True, status_started=asyncio.Event(), status_release=asyncio.Event())
        await page.locator("#settingsButton").click()
        await asyncio.wait_for(control["status_started"].wait(), 5)
        await page.locator('[data-settings-tab="settingsModels"]').click()
        await page.locator("#settingsCheckerModel").select_option("modelscope_test")
        await expect(page.locator("#settingsModelResult")).to_contain_text("已保存")
        await page.wait_for_timeout(150)
        control["status_release"].set()
        await page.wait_for_load_state("networkidle")
        await expect(page.locator("#settingsCheckerModel")).to_have_value("modelscope_test")
        assert saved["checker"] == "modelscope_test"
        await screenshot("07-stale-status-cannot-replace-latest-model.jpg")
        checks.append({"case": "Late status from opening settings cannot replace latest saved choice", "outcome": "PASS"})

        await page.locator("#settingsClose").click()
        control["configured"] = True
        await page.reload(wait_until="networkidle")
        await page.locator("#settingsButton").click()
        await expect(page.locator("#settingsReady")).to_contain_text("所需密钥已配置")
        await expect(page.locator("#settingsReady")).to_contain_text("服务当前是否可用，以任务返回为准")
        await screenshot("08-configured-is-not-a-service-availability-promise.jpg")
        checks.append({"case": "Configured credentials wording explicitly distinguishes actual service availability", "outcome": "PASS"})
        await browser.close()

    assert not errors, errors
    assert not forbidden, forbidden
    report = {"result": "PASS", "checks": checks, "mocked_settings_posts": posts,
        "final_mock_settings": saved, "page_errors": errors, "external_or_unplanned_requests": forbidden,
        "real_user_data_accessed": False, "worker_started": False, "server_stopped": False}
    (OUTPUT / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": "PASS", "checks": len(checks), "report": str(OUTPUT / "results.json")}, ensure_ascii=False))


def run():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(FrontendHandler, directory=str(ROOT / "frontend")))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        asyncio.run(check())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    report_path = OUTPUT / "results.json"
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["server_stopped"] = True
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Isolated model-settings server stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.run:
        run()
    else:
        parser.error("Choose --run")
