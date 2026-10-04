"""查审核页现场是不是真的写进了 sessionStorage（只读）。"""

import argparse
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "edit-panel"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--paper", required=True)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("local only")
    from playwright.sync_api import sync_playwright, expect

    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1400, "height": 950})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1');"
                                "window.__early = () => sessionStorage.getItem('qb-review-state')")
        context.route("**/*", lambda r: r.continue_() if urlparse(r.request.url).hostname in ("127.0.0.1", "localhost") else r.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: print("  console:", m.text) if "QBDEBUG" in m.text else None)

        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")
        print("after load      :", page.evaluate("() => sessionStorage.getItem('qb-review-state')"))
        print("papers in list  :", page.evaluate("() => document.querySelectorAll('#paperList .paper-item, #paperList li, #paperList a').length"))
        print("state.paperId   :", page.evaluate("() => document.querySelectorAll('.card').length"))

        page.get_by_role("tab", name="需要核查", exact=False).first.click()
        page.wait_for_timeout(300)
        print("after filter    :", page.evaluate("() => sessionStorage.getItem('qb-review-state')"))
        card = page.locator("#cards .card").nth(6)
        card.locator(".card-head .qnum").click()
        page.wait_for_timeout(300)
        print("after setCurrent:", page.evaluate("() => sessionStorage.getItem('qb-review-state')"))
        print("current card   :", page.locator("#cards .card.is-current").get_attribute("data-id"))
        print("paper in url   :", page.url)

        page.click(".topnav a[href='/library']")
        page.wait_for_load_state("networkidle")
        print("on library      :", page.evaluate("() => sessionStorage.getItem('qb-review-state')"))
        page.click(".topnav a[href='/']")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        print("back on review  :", page.url)
        print("key at boot     :", page.evaluate("() => window.__early()"))
        print("key now         :", page.evaluate("() => sessionStorage.getItem('qb-review-state')"))
        print("filter active   :", page.evaluate("() => document.querySelector('.filter.active')?.dataset.filter"))
        print("current card    :", page.evaluate("() => document.querySelector('#cards .card.is-current')?.dataset.id"))
        print("errors          :", errors)
        browser.close()


if __name__ == "__main__":
    main()
