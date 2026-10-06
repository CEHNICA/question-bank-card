"""1.12.6 第 1 条：已入库题的对号必须可撤销，而且两下都真的有效。

在真库副本上跑一个完整闭环：
  已入库 → 取消勾 → 题库少一道、审核页两个数字同步各动一格 → 再打勾 → 全部回到原样
闭环跑完数据库必须回到原样。中途任何一边没动，这条就不过。
"""

import argparse
import re
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

        # 「已入库」这一页里，人工打过勾的和「题库里本来就有、还没人亲自打过」的混在一起。
        # 主闭环对两种都成立，所以拿第一张卡就行。
        cards = page.locator("#cards .card")
        assert cards.count() > 0, "这份卷子的「已入库」页里一题都没有，闭环无从下手"
        card = cards.first
        question = card.get_attribute("data-id")
        tick = card.locator(".card-tick")
        # 题库里已经有的题，对号必须是按下的，不管当初是谁按的。
        assert tick.get_attribute("aria-pressed") == "true", tick.get_attribute("aria-pressed")
        expect(tick).to_have_attribute("aria-label", re.compile(r"^撤销第 \d+ 题的通过$"))

        # 1.12.6 那条：入库但没人工打过勾的题，对号以前渲染成空的，点了没反应。
        # 这种题不是每份卷子都有：有就量一下，没有就说清楚，别拿别的卡顶替
        # —— 断言挂在不合适的卡上，报出来的 class="card-tick" 看着像产品坏了。
        published_cards = page.locator("#cards .card", has=page.locator(".card-tick.published"))
        if published_cards.count():
            published_tick = published_cards.first.locator(".card-tick")
            assert published_tick.get_attribute("aria-pressed") == "true", "题库里已有、还没人亲自打过勾的题，对号也要是实心的"
            print("note   : 这份卷子里有『已入库但没人工打过勾』的题，对号同样是实心的")
        else:
            print("note   : 这份卷子里入库的题都人工打过勾，「published 对号渲染」这一段没测到")
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
