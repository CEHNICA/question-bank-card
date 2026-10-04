"""截下改字面板在宽屏下的样子，供人工看布局（只读，不改数据）。

用法： QB_DATABASE/QB_DATA_ROOT 指到真库副本，先起开发服务器，再
       python tools/shot_edit_panel.py --url http://127.0.0.1:8802 --paper <uuid>
"""

import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "edit-panel"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--paper", required=True)
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1000)
    parser.add_argument("--tag", default="wide")
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local server is allowed")

    from playwright.sync_api import sync_playwright, expect

    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": args.width, "height": args.height})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1'); localStorage.setItem('qb-lens','0')")
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                      ("127.0.0.1", "localhost") else route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")
        # 专注模式会把左栏原卷收起来；这里要量的是默认的两栏布局。
        focus = page.get_by_role("button", name="专注", exact=True)
        if focus.count() and focus.first.get_attribute("aria-pressed") == "true":
            focus.first.click()
            page.wait_for_timeout(300)

        card = page.locator(".card").first
        expect(card).to_be_visible()
        card.get_by_role("button", name="改字", exact=True).click()
        editor = card.locator(".editor")
        expect(editor).to_be_visible()
        page.wait_for_timeout(600)

        geometry = card.evaluate("""el => {
            const box = (sel) => { const n = el.querySelector(sel); if (!n) return null;
                const r = n.getBoundingClientRect(); return {top: Math.round(r.top), left: Math.round(r.left),
                    width: Math.round(r.width), height: Math.round(r.height)}; };
            return {head: box('.editor-head'), stem: box('.stem-input'), preview: box('.editor-preview'),
                previewBox: box('.editor-preview-box'), inLeftColumn: Boolean(el.querySelector('.card-source .editor-preview-box')),
                actions: box('.editor-actions')};
        }""")
        for name, value in geometry.items():
            print(f"  {name}: {value}")
        print("errors:", errors)
        page.screenshot(path=str(OUTPUT / f"edit-panel-{args.tag}.png"))
        card.screenshot(path=str(OUTPUT / f"edit-card-{args.tag}.png"))
        print("saved:", OUTPUT / f"edit-panel-{args.tag}.png")
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
