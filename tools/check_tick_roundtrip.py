"""1.12.6 第 1 条：已入库题的对号必须可撤销，而且两下都真的有效。

在真库副本上跑一个完整闭环：
  已入库 → 取消勾 → 题库少一道、审核页两个数字同步各动一格 → 再打勾 → 全部回到原样
闭环跑完数据库必须回到原样。中途任何一边没动，这条就不过。
"""

import argparse
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def counters(page):
    return page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('#filters .filter')]
        .map((node) => [node.dataset.filter, Number(node.querySelector('.filter-count').textContent)]))""")


def library_total(page, base):
    return page.request.get(f"{base}/api/library?limit=1").json()["total"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8803")
    parser.add_argument("--paper", required=True)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local isolated server is allowed")
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1500, "height": 950})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1')")
        context.route("**/*", lambda r: r.continue_() if urlparse(r.request.url).hostname in ("127.0.0.1", "localhost") else r.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        posts = []

        def watch(request):
            if request.method == "POST" and "/api/" in request.url:
                posts.append(f"{request.method} {urlparse(request.url).path}")

        page.on("request", watch)

        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")
        page.get_by_role("tab", name="已入库", exact=False).first.click()
        page.wait_for_timeout(400)

        card = page.locator("#cards .card").first
        question = card.get_attribute("data-id")
        tick = card.locator(".card-tick")
        # 入库但没打过勾的题，对号以前渲染成空的 —— 点了没反应就是这个原因。
        assert "published" in (tick.get_attribute("class") or ""), tick.get_attribute("class")
        assert tick.get_attribute("aria-pressed") == "true"
        expect(tick).to_have_attribute("aria-label", "撤销第 1 题的通过")
        before_counters, before_total = counters(page), library_total(page, url)
        print("before  :", before_counters, "library:", before_total)

        # 取消勾 → 撤销通过 + 从题库撤回
        posts.clear()
        tick.click()
        page.wait_for_timeout(1500)
        print("POSTs           :", posts)
        print("toast           :", page.locator(".toast").first.inner_text() if page.locator(".toast").count() else "(none)")
        after_counters, after_total = counters(page), library_total(page, url)
        print("untick  :", after_counters, "library:", after_total)
        assert after_total == before_total - 1, f"题库没少一道：{after_total} vs {before_total}"
        assert after_counters["approved"] == before_counters["approved"] - 1, (after_counters, before_counters)
        assert after_counters["todo"] == before_counters["todo"] + 1, (after_counters, before_counters)
        assert after_counters["all"] == before_counters["all"], "撤回不该让题卡消失"
        assert "撤回" in page.locator(".toast").first.inner_text()

        # 再打勾 → 重新入库，数字全部回到原样
        page.get_by_role("tab", name="需要核查", exact=False).first.click()
        page.wait_for_timeout(500)
        back = page.locator(f'#cards .card[data-id="{question}"]')
        expect(back).to_be_visible()
        back.locator(".card-tick").click()
        page.wait_for_timeout(1500)
        assert library_total(page, url) == before_total, library_total(page, url)
        page.get_by_role("tab", name="已入库", exact=False).first.click()
        page.wait_for_timeout(500)
        final = counters(page)
        assert final == before_counters, (final, before_counters)
        expect(page.locator(f'#cards .card[data-id="{question}"] .card-tick')).to_have_attribute("aria-pressed", "true")
        print("re-tick :", final, "library:", library_total(page, url))
        assert not errors, errors
        browser.close()
    print("Tick round trip: an in-library tick is solid and reversible; the library count and both review counters move together and come back: OK")


if __name__ == "__main__":
    main()
