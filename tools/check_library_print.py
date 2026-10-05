"""打印预览里不许出现侧栏和它的遮罩 —— 打出来的组卷只有题目。

用法： python tools/check_library_print.py --url http://127.0.0.1:8803
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "layout"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8803")
    parser.add_argument("--width", type=int, default=1366)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local server is allowed")

    from playwright.sync_api import sync_playwright

    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors, failures = [], []
    with sync_playwright() as pw:
        exe = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": exe} if exe else {}))
        for width in (1366, 900):
            ctx = browser.new_context(viewport={"width": width, "height": 768})
            ctx.add_init_script("localStorage.setItem('qb-welcome-seen','1')")
            ctx.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                      ("127.0.0.1", "localhost") else route.abort())
            page = ctx.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{url}/library")
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".library-card")
            if width <= 979:
                page.click("#libraryFilterToggle")
                page.wait_for_timeout(300)
            page.emulate_media(media="print")
            page.wait_for_timeout(300)
            state = page.evaluate("""() => {
                const shown = (sel) => { const n = document.querySelector(sel); if (!n) return null;
                  const s = getComputedStyle(n); return s.display !== 'none' && s.visibility !== 'hidden'; };
                return {rail: shown('.library-rail'), scrim: shown('.rail-scrim'),
                        topbar: shown('.topbar'), drawer: shown('.site-drawer'),
                        cards: document.querySelectorAll('.library-card').length};
            }""")
            print(f"{width}px 打印媒体下：{state}")
            for key in ("rail", "scrim", "topbar", "drawer"):
                if state[key]:
                    failures.append(f"{width}px 打印时 {key} 仍然可见")
            if not state["cards"]:
                failures.append(f"{width}px 打印时题目也没了")
            page.pdf(path=str(OUTPUT / f"library-print-{width}.pdf"), format="A4",
                     print_background=True, margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
            ctx.close()
        browser.close()
    print("errors:", errors or "无")
    print("failures:", failures or "无")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
