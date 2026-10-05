"""全屏改字里点「下一题」：应该直接切到下一道题，左栏试卷列表保持隐藏。

用法： python tools/check_fullscreen_nav.py --url http://127.0.0.1:8803 --paper <uuid>
"""

import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8803")
    parser.add_argument("--paper", required=True)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local server is allowed")

    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1'); localStorage.setItem('qb-lens','0')")
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                      ("127.0.0.1", "localhost") else route.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")

        page.locator(".card:not(.compact)").first.get_by_role("button", name="改字", exact=True).click()
        expect(page.locator(".card.editing")).to_be_visible()
        first = page.text_content(".editor-bar-count")
        bar = page.locator(".card.editing .editor-bar")
        next_enabled = not bar.get_by_role("button", name="下一题 ›").is_disabled()

        bar.get_by_role("button", name="下一题 ›").click()
        page.wait_for_timeout(700)
        second = page.text_content(".editor-bar-count")
        still_fullscreen = page.locator(".card.editing").count() == 1 and page.locator(".sidebar").is_hidden()
        prev_now_enabled = not bar.get_by_role("button", name="‹ 上一题").is_disabled()

        # 一路走到最后，「下一题」应该变灰（中途可能弹确认，答“丢弃改动”继续）
        def step(label, times=40):
            for _ in range(times):
                target = bar.get_by_role("button", name=label)
                if target.count() == 0 or target.is_disabled():
                    return True
                target.click()
                page.wait_for_timeout(260)
                if page.locator("#confirmDialog[open]").count():
                    page.locator("#confirmOk").click()
                    page.wait_for_timeout(320)
                if page.locator(".card.editing").count() == 0:
                    return False
            return False

        reached_end = step("下一题 ›")
        last = page.text_content(".editor-bar-count")
        next_at_end_disabled = bar.get_by_role("button", name="下一题 ›").is_disabled()
        reached_start = step("‹ 上一题")
        back_at_start = page.text_content(".editor-bar-count")
        prev_at_start_disabled = bar.get_by_role("button", name="‹ 上一题").is_disabled()

        # 改了点东西再切题，应该先问一句；答「继续编辑」就留在原地
        before_edit = page.text_content(".editor-bar-count")
        page.locator(".card.editing .stem-input").fill("临时改字（不该被保存）")
        page.wait_for_timeout(200)
        bar.get_by_role("button", name="下一题 ›").click()
        page.wait_for_timeout(500)
        asked = page.locator("#confirmDialog[open]").count() == 1
        if asked:
            page.locator("#confirmDialog .confirm-actions button").first.click()
            page.wait_for_timeout(400)
        stayed = page.text_content(".editor-bar-count") == before_edit and page.locator(".card.editing").count() == 1
        kept_text = page.input_value(".card.editing .stem-input") == "临时改字（不该被保存）"

        # Esc 退回审核页
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        if page.locator("#confirmDialog[open]").count():
            page.locator("#confirmOk").click()
            page.wait_for_timeout(400)
        back = page.locator(".card.editing").count() == 0 and page.locator(".sidebar").is_visible()

        for name, good, detail in [
            ("打开后是全屏（左栏隐藏）", page.locator(".sidebar").is_hidden() or still_fullscreen, ""),
            ("第一题的「下一题」可点", next_enabled, first),
            ("点下一题切到了别的题", first != second, f"{first} -> {second}"),
            ("切题后仍然是全屏", still_fullscreen, ""),
            ("切到第二题后「上一题」可点了", prev_now_enabled, ""),
            ("走到最后一题时「下一题」变灰", next_at_end_disabled, f"{last} reached={reached_end}"),
            ("走回第一题时「上一题」变灰", prev_at_start_disabled, f"{first} -> {back_at_start} reached={reached_start}"),
            ("有未保存改动时切题会先问", asked, ""),
            ("答「继续编辑」就留在原来这题", stayed, f"{before_edit} -> {page.text_content('.editor-bar-count') if page.locator('.card.editing').count() else '已退出全屏'}"),
            ("改的字一个字没丢", kept_text, ""),
            ("Esc 退回审核页，左栏回来了", back, ""),
        ]:
            print(f"  {'PASS' if good else 'FAIL'}  {name}{('  ' + detail) if detail and not good else ''}")
        print("  errors:", errors)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
