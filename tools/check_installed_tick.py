"""在**正式安装的应用**上验 1.12.6 第 1 条：对号可撤销。

跑在真库（127.0.0.1:8768）上，只做一次完整的可逆往返：
  已入库 → 取消勾 → 题库少一道、两个计数同步 → 再打勾 → 全部回到原样
跑完数据必须回到原样。找不到可逆的题就明确报出来，不硬来。
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
    parser.add_argument("--url", default="http://127.0.0.1:8768")
    parser.add_argument("--paper", required=True)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only the local app is allowed")
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
        page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)

        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")
        print("app version     :", page.evaluate("() => document.title"), page.url)
        page.get_by_role("tab", name="已入库", exact=False).first.click()
        page.wait_for_timeout(500)

        card = page.locator("#cards .card").first
        expect(card).to_be_visible()
        question = card.get_attribute("data-id")
        tick = card.locator(".card-tick")
        cls = tick.get_attribute("class") or ""
        pressed = tick.get_attribute("aria-pressed")
        title = tick.get_attribute("title")
        print("tick            :", cls, "pressed=", pressed)
        print("title           :", title)
        # aria-pressed 是「这个勾打上了吗」的唯一依据；published 只是「入库了但
        # 没手动打勾」那个形状的视觉标记，人工打勾过的题不带它。
        assert pressed == "true", f"已入库的题对号不是实心的：aria-pressed={pressed}"
        assert cls.startswith("card-tick")
        assert "取消勾会同时从题库撤回" in (title or "")

        before_counters, before_total = counters(page), library_total(page, url)
        print("before          :", before_counters, "library:", before_total)

        posts = []

        def watch(request):
            if request.method == "POST" and "/api/" in request.url:
                posts.append(f"{request.method} {urlparse(request.url).path}")

        page.on("request", watch)
        tick.click()
        page.wait_for_timeout(1800)
        print("POSTs           :", posts)
        toast = page.locator(".toast").first.inner_text() if page.locator(".toast").count() else "(none)"
        print("toast           :", toast.replace("\n", " / "))
        after_counters, after_total = counters(page), library_total(page, url)
        print("untick          :", after_counters, "library:", after_total)
        assert after_total == before_total - 1, f"题库没少一道：{after_total} vs {before_total}"
        assert after_counters["approved"] == before_counters["approved"] - 1
        assert after_counters["todo"] == before_counters["todo"] + 1
        assert after_counters["all"] == before_counters["all"], "撤回不该让题卡消失"

        page.get_by_role("tab", name="需要核查", exact=False).first.click()
        page.wait_for_timeout(600)
        back = page.locator(f'#cards .card[data-id="{question}"]')
        expect(back).to_be_visible()
        back.locator(".card-tick").click()
        page.wait_for_timeout(1800)
        assert library_total(page, url) == before_total, library_total(page, url)
        page.get_by_role("tab", name="已入库", exact=False).first.click()
        page.wait_for_timeout(600)
        final = counters(page)
        assert final == before_counters, (final, before_counters)
        expect(page.locator(f'#cards .card[data-id="{question}"] .card-tick')).to_have_attribute("aria-pressed", "true")
        print("re-tick         :", final, "library:", library_total(page, url))
        assert not errors, errors
        browser.close()
    print("Installed app: an in-library tick is solid and reversible; the library count and both review counters move together and come back: OK")


if __name__ == "__main__":
    main()
