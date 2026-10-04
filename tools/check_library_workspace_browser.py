"""Click-test the new workspace against the existing isolated UX fixtures.

Only localhost is allowed; draft writes require the exact four-question fixture.
No workers, OCR, cloud probes, publication changes or real credentials are used.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "library-v111" / "browser"


def check(base):
    from playwright.sync_api import sync_playwright, expect
    assert urlparse(base).hostname in ("127.0.0.1", "localhost")
    fixture = json.loads((ROOT / "tmp/library-ux-browser/fixture.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    errors, outgoing = [], []
    with sync_playwright() as pw:
        executable = next(str(p) for p in (
            Path(pw.chromium.executable_path), Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file())
        browser = pw.chromium.launch(headless=True, executable_path=executable)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        def local_only(route):
            if urlparse(route.request.url).hostname in ("127.0.0.1", "localhost"):
                route.continue_()
            else:
                outgoing.append(route.request.url)
                route.abort()
        context.route("**/*", local_only)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/library")
        expect(page.locator(".library-card")).to_have_count(4)
        served = page.request.get(base + "/api/library").json()
        assert served["total"] == 4 and set(fixture["ids"]).issubset({i["id"] for i in served["items"]}), "Not the isolated fixture; refusing writes"
        expect(page.locator("#advancedFilters")).not_to_have_attribute("open", "")
        page.locator("#selectVisible").check()
        page.locator("#addSelected").click()
        expect(page.locator("#basketPanelCount")).to_have_text("4")
        page.locator("#searchInput").fill("距离")
        expect(page.locator(".library-card")).to_have_count(1)
        page.locator("#searchInput").fill("没有这样的关键词-v111")
        expect(page.locator(".library-card")).to_have_count(0)
        expect(page.locator("#basketPanelCount")).to_have_text("4")
        page.locator("#basketViewButton").click()
        expect(page.locator(".library-card")).to_have_count(4)
        expect(page.locator("#searchInput")).to_be_disabled()
        expect(page.locator("#basketToggle")).to_be_enabled()
        page.locator("#basketToggle").click()
        expect(page.locator("#basketPanel")).to_be_hidden()
        page.locator("#basketToggle").click()
        expect(page.locator("#basketPanel")).to_be_visible()
        page.locator("#allQuestionsButton").click()
        expect(page.locator("#searchInput")).to_be_enabled()
        expect(page.locator(".library-card")).to_have_count(0)
        page.get_by_role("button", name="清除搜索与筛选", exact=True).click()
        expect(page.locator(".library-card")).to_have_count(4)
        page.locator("#sortSelect").select_option("source")
        expect(page).to_have_url(base + "/library?sort=source")
        card = page.locator("#q-" + fixture["ids"][0])
        card.get_by_role("button", name="完整题目", exact=True).click()
        expect(page.locator("#questionContent .qb-options")).to_be_visible()
        page.locator("#questionActions").get_by_role("button", name="查看出处", exact=True).click()
        expect(page.locator("#sourceDialog")).to_be_visible()
        page.wait_for_function("[...document.querySelectorAll('#sourcePages img')].length && [...document.querySelectorAll('#sourcePages img')].every(i=>i.complete&&i.naturalWidth)")
        page.locator("#sourceZoomIn").click()
        expect(page.locator("#sourceZoom")).to_have_text("150%")
        canvas = page.locator("#sourcePages")
        rect = canvas.bounding_box()
        page.mouse.move(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
        page.keyboard.down("Control")
        page.mouse.wheel(0, -120)
        page.keyboard.up("Control")
        expect(page.locator("#sourceZoom")).not_to_have_text("150%")
        before = canvas.evaluate("e=>[e.scrollLeft,e.scrollTop]")
        page.mouse.down()
        page.mouse.move(rect["x"] + 20, rect["y"] + 20, steps=10)
        page.mouse.up()
        after = canvas.evaluate("e=>[e.scrollLeft,e.scrollTop]")
        assert before != after, "Original did not pan"
        page.locator("#sourceDialog").get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator("#questionDialog")).to_be_visible()
        page.locator("#questionActions").get_by_role("button", name="移出试题篮", exact=True).click()
        page.locator("#questionActions").get_by_role("button", name="加入试题篮", exact=True).click()
        page.locator("#closeQuestion").click()
        expect(card.get_by_role("button", name="完整题目", exact=True)).to_be_focused()
        page.locator("#basketButton").click()
        expect(page.locator(".print-question")).to_have_count(4)
        page.locator("#printTitle").fill("离线验收草稿-v111")
        page.locator(".print-controls label").filter(has=page.locator("#printOrigin")).click()
        expect(page.locator("#printOrigin")).to_be_checked()
        page.locator("#saveDraft").click()
        expect(page.locator("#draftSaveStatus")).to_contain_text("已保存")
        drafts = page.request.get(base + "/api/library/drafts").json()["drafts"]
        saved = next(d for d in drafts if d["title"] == "离线验收草稿-v111")
        assert len(saved["ids"]) == 4 and saved["print_options"]["origin"] is True
        page.locator(".print-question-tools [data-action='down']:not([disabled])").first.click()
        expect(page.locator("#draftSaveStatus")).to_contain_text("未保存")
        page.locator("#saveDraft").click()
        expect(page.locator("#draftSaveStatus")).to_contain_text("已保存")
        reloaded = page.request.get(base + "/api/library/drafts/" + saved["id"]).json()["draft"]
        assert reloaded["ids"] != saved["ids"] and reloaded["revision"] > saved["revision"]
        page.locator("#closePrint").click()
        page.reload()
        expect(page.locator(".library-card")).to_have_count(4)
        page.locator("#openDrafts").click()
        row = page.locator(".draft-row").filter(has_text="离线验收草稿-v111").first
        row.get_by_role("button", name="继续组卷", exact=True).click()
        if page.locator("#confirmDialog").is_visible():
            page.locator("#confirmOk").click()
        expect(page.locator("#printTitle")).to_have_value("离线验收草稿-v111")
        expect(page.locator("#printOrigin")).to_be_checked()
        expect(page.locator(".print-question")).to_have_count(4)
        kinds = ["single_choice", "multiple_choice", "fill_blank", "true_false", "free_response"]
        item_types = {item["id"]: item["question_type"] for item in served["items"]}
        expected_order = sorted(reloaded["ids"], key=lambda key: kinds.index(item_types[key]) if item_types[key] in kinds else len(kinds))
        assert page.locator(".print-question").evaluate_all("rows=>rows.map(e=>e.dataset.questionId)") == expected_order
        # The saved request contains a snapshot; later edits must stay dirty.
        held = []
        def delay_save(route):
            if route.request.method == "PUT":
                held.append(route)
            else:
                route.continue_()
        page.route("**/api/library/drafts/*", delay_save)
        page.locator("#saveDraft").click()
        page.wait_for_timeout(100)
        assert len(held) == 1
        page.locator("#printTitle").fill("保存期间继续修改")
        held.pop().continue_()
        expect(page.locator("#draftSaveStatus")).to_contain_text("后续改动尚未保存")
        page.unroute("**/api/library/drafts/*", delay_save)
        page.locator("#closePrint").click()
        if page.locator("#draftsDialog").is_visible():
            page.locator("#closeDrafts").click()
        # Same IDs still need confirmation when only the title was edited.
        page.locator("#openDrafts").click()
        page.locator(".draft-row").filter(has_text="离线验收草稿-v111").first.get_by_role("button", name="继续组卷", exact=True).click()
        expect(page.locator("#confirmDialog")).to_be_visible()
        page.locator("#confirmOk").click()
        expect(page.locator("#printTitle")).to_have_value("离线验收草稿-v111")
        page.locator("#closePrint").click()
        page.get_by_role("link", name="设置", exact=True).click()
        expect(page).to_have_url(base + "/settings")
        expect(page.locator(".layout")).to_be_hidden()
        page.locator("[data-library-ai-settings]").click()
        expect(page.locator("#libraryAITags")).not_to_be_checked()
        expect(page.locator("#libraryAIAnswer")).not_to_be_checked()
        expect(page.locator("#libraryAITest")).to_be_disabled()
        page.locator("#libraryAICancel").click()
        page.locator("#settingsClose").click()
        page.locator("#settingsReturn").click()
        expect(page.locator(".library-card")).to_have_count(4)
        for width in (1280, 980, 650, 390, 320):
            page.set_viewport_size({"width": width, "height": 900})
            page.wait_for_timeout(100)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), f"Horizontal overflow at {width}"
            menu = page.locator(".library-card-more").first
            menu.locator("summary").click()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth+1"), f"Menu overflow at {width}"
            menu.locator("summary").click()
            page.screenshot(path=str(OUT / f"workspace-{width}.png"), full_page=True)
        assert not errors, errors
        assert not outgoing, outgoing
        page.set_viewport_size({"width": 650, "height": 574})
        page.locator(".library-card").first.scroll_into_view_if_needed()
        assert page.locator(".library-toolbar").evaluate("e=>getComputedStyle(e).position") == "static"
        assert page.locator(".library-card").first.bounding_box()["y"] < 300, "Short window keeps controls over the question"
        browser.close()
    print("Offline workspace browser passed: filters/selected basket/full detail/source wheel+pan/history/draft save+reorder+reopen/AI default off/5 viewport widths; no external requests")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8776")
    args = parser.parse_args()
    check(args.url.rstrip("/"))
