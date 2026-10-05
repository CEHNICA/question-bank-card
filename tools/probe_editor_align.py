"""量一道长题干题的左右两栏实际高度，找出"预览和题干对不齐"的原因。

1.12.6 用「把预览往下推 margin-top」来对齐题干顶端，但 margin 只能往下推。
左栏原卷截图高过一定高度，预览已经在题干下面了，就再也推不动。
这个脚本把相关数字都打出来。
"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "edit-panel"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8768")
    parser.add_argument("--paper", required=True)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("local only")
    from playwright.sync_api import sync_playwright

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1')")
        context.route("**/*", lambda r: r.continue_() if urlparse(r.request.url).hostname in ("127.0.0.1", "localhost") else r.abort())
        page = context.new_page()
        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")

        rows = []
        for index in range(8):
            cards = page.locator("#cards .card")
            if index >= cards.count():
                break
            card = cards.nth(index)
            label = card.locator(".qnum").inner_text() if card.locator(".qnum").count() else f"#{index}"
            tick = card.get_by_role("button", name="改字", exact=True)
            if not tick.count():
                continue
            tick.click()
            editor = card.locator(".editor")
            editor.wait_for(state="visible", timeout=5000)
            page.wait_for_timeout(350)
            m = card.evaluate("""el => {
                const box = (sel) => { const n = el.querySelector(sel); if (!n) return null;
                    const r = n.getBoundingClientRect(); return {top: Math.round(r.top), height: Math.round(r.height)}; };
                const source = el.querySelector('.source-sticky');
                const shot = el.querySelector('.crop, .crop-missing');
                return {head: box('.editor-head'), stem: box('.stem-input'),
                    preview: box('.editor-preview'), shot: box('.crop'),
                    marginTop: el.querySelector('.editor-preview-box')?.style.marginTop || '0px',
                    sourceH: source ? Math.round(source.getBoundingClientRect().height) : null,
                    stemScroll: el.querySelector('.stem-input')?.scrollHeight ?? null,
                    stemMax: getComputedStyle(el.querySelector('.stem-input')).maxHeight};
            }""")
            stem, prev = m["stem"], m["preview"]
            gap = (stem["top"] - prev["top"]) if stem and prev else None
            rows.append((label, m, gap))
            print(f"{label:>8}  题干顶={stem['top']} 高={stem['height']} (内容{m['stemScroll']})  "
                  f"预览顶={prev['top']} 高={prev['height']}  截图高={m['shot']['height'] if m['shot'] else '-'}  "
                  f"左栏总高={m['sourceH']}  margin={m['marginTop']}  差={gap}")
            page.screenshot(path=str(OUTPUT / f"align-{index}.png"), full_page=False)
            card.get_by_role("button", name="取消", exact=True).click()
            if page.locator("#confirmDialog").is_visible():
                page.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
            page.wait_for_timeout(250)
        browser.close()
        bad = [r for r in rows if r[2] is None or abs(r[2]) > 8]
        print(f"\n对齐: {len(rows) - len(bad)}/{len(rows)}  在 8px 内；不齐的: {[r[0] for r in bad]}")


if __name__ == "__main__":
    main()
