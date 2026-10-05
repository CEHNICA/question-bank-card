"""量全屏改字的三块区域：左上原卷 / 左下编辑 / 右边预览。

Browser 面板只有 639-901px，量不出宽屏，所以这里显式给 viewport。
只读，不改数据。

用法： python tools/probe_editor_fullscreen.py --url http://127.0.0.1:8802 --paper <uuid>
"""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "edit-panel"

MEASURE = """() => {
  const box = (sel) => { const n = document.querySelector(sel); if (!n) return null;
    const r = n.getBoundingClientRect();
    return {top: Math.round(r.top), left: Math.round(r.left),
      right: Math.round(r.right), bottom: Math.round(r.bottom),
      width: Math.round(r.width), height: Math.round(r.height)}; };
  // Anything that would sit on top of the lower half of the screen.
  const blockers = [...document.querySelectorAll("body *")].filter((n) => {
    const s = getComputedStyle(n);
    if (s.position !== "fixed" && s.position !== "sticky") return false;
    if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
    const r = n.getBoundingClientRect();
    if (r.height < 8 || r.width < 8) return false;
    if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) return false;
    // Ignore the fullscreen card itself and its own top bar.
    if (n.classList.contains("editing") || n.classList.contains("editor-bar")) return false;
    return r.bottom > window.innerHeight * 0.6;
  }).map((n) => ({cls: n.className.toString().slice(0, 60), pos: getComputedStyle(n).position,
    top: Math.round(n.getBoundingClientRect().top), h: Math.round(n.getBoundingClientRect().height)}));
  return {
    viewport: {w: window.innerWidth, h: window.innerHeight},
    topbarVisible: getComputedStyle(document.querySelector(".topbar")).display !== "none",
    sidebarVisible: getComputedStyle(document.querySelector(".sidebar")).display !== "none",
    bodyClass: document.body.className,
    bar: box(".editor-bar"),
    source: box(".card.editing > .card-source"),
    body: box(".card.editing > .card-body"),
    previewBox: box(".card.editing > .editor-preview-box"),
    stem: box(".stem-input"),
    preview: box(".card.editing .editor-preview"),
    count: (document.querySelector(".editor-bar-count") || {}).textContent || null,
    blockers
  };
}"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--paper", required=True)
    parser.add_argument("--questions", default="1,2,3,9")
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
        focus = page.get_by_role("button", name="专注", exact=True)
        if focus.count() and focus.first.get_attribute("aria-pressed") == "true":
            focus.first.click()
            page.wait_for_timeout(300)

        for number in [int(value) for value in args.questions.split(",") if value.strip()]:
            index = page.evaluate("""(n) => [...document.querySelectorAll('.card:not(.compact)')]
                .findIndex(c => ((c.querySelector('.qnum') || {}).textContent || '').includes(`第 ${n} 题`))""", number)
            if index < 0:
                print(f"第 {number} 题: not found")
                continue
            card = page.locator(".card:not(.compact)").nth(index)
            card.get_by_role("button", name="改字", exact=True).click()
            expect(page.locator(".card.editing .editor")).to_be_visible()
            page.wait_for_timeout(500)
            data = page.evaluate(MEASURE)
            print(f"--- 第 {number} 题 ---")
            print(json.dumps(data, ensure_ascii=False, indent=2))
            if data["source"] and data["previewBox"] and data["bar"]:
                print(f"  预览顶 - 原卷顶 = {data['previewBox']['top'] - data['source']['top']}  (应为 0)")
            print(f"  顶栏底 = {data['bar']['bottom']}  原卷底 = {data['source']['bottom']}  "
                  f"编辑底 = {data['body']['bottom']}  视口高 = {data['viewport']['h']}")
            print(f"  底部挡视线的悬浮/固定元素: {data['blockers'] or '无'}")
            page.screenshot(path=str(OUTPUT / f"fullscreen-{args.tag}-{number}.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
            if page.locator(".card.editing").count():
                page.get_by_role("button", name="继续编辑").click()
                page.wait_for_timeout(200)
        print("errors:", errors)
        print("saved:", OUTPUT)
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
