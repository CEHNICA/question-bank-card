"""量全屏「答案解析」编辑器的三块区域：左上原卷本题 / 左下编辑区 / 右边预览。

Browser 面板只有 639-901px，量不出宽屏，所以这里显式给 viewport。
只读：脚本只打开界面和截图，不点保存。

用法： python tools/probe_answer_editor.py --url http://127.0.0.1:8802
"""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "answer-editor"

MEASURE = """() => {
  const box = (sel) => { const n = document.querySelector(sel); if (!n) return null;
    const r = n.getBoundingClientRect();
    return {top: Math.round(r.top), left: Math.round(r.left), right: Math.round(r.right),
      bottom: Math.round(r.bottom), width: Math.round(r.width), height: Math.round(r.height)}; };
  const blockers = [...document.querySelectorAll('body *')].filter((n) => {
    const s = getComputedStyle(n);
    if (s.position !== 'fixed' && s.position !== 'sticky') return false;
    if (s.display === 'none' || s.visibility === 'hidden' || Number(s.opacity) === 0) return false;
    const r = n.getBoundingClientRect();
    if (r.height < 8 || r.width < 8) return false;
    if (r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth) return false;
    if (n.closest('.answer-editor-dialog')) return false;
    return r.bottom > window.innerHeight * 0.6;
  }).map((n) => ({cls: n.className.toString().slice(0, 50), pos: getComputedStyle(n).position,
    top: Math.round(n.getBoundingClientRect().top), h: Math.round(n.getBoundingClientRect().height)}));
  return {
    viewport: {w: window.innerWidth, h: window.innerHeight},
    bar: box('.answer-editor-bar'),
    strip: box('.answer-editor-strip'),
    source: box('.answer-editor-source'),
    input: box('.answer-editor-input-column'),
    edit: box('.answer-editor-edit'),
    previewColumn: box('.answer-editor-preview-column'),
    chips: document.querySelectorAll('.answer-list-row').length,
    place: (document.querySelector('.answer-editor-place') || {}).textContent || null,
    footerLeft: document.querySelectorAll('.answer-editor-footer').length,
    navLeft: document.querySelectorAll('.answer-editor-nav').length,
    blockers
  };
}"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--pick", type=int, default=6)
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

        page.goto(f"{url}/library")
        page.wait_for_load_state("networkidle")
        # 勾题卡 → 加入试题篮 → 组卷预览 → 答案解析
        boxes = page.locator(".library-card-select")
        for index in range(min(args.pick, boxes.count())):
            boxes.nth(index).check(force=True)
            page.wait_for_timeout(80)
        page.locator("#addSelected").click()
        page.wait_for_timeout(300)
        expect(page.locator("#basketButton")).to_be_visible()
        page.locator("#basketButton").click()
        page.wait_for_load_state("networkidle")
        page.locator("#managePrintAnswers").click()
        expect(page.locator(".answer-editor-dialog[open]")).to_be_visible()
        page.wait_for_timeout(900)

        data = page.evaluate(MEASURE)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        if data["source"] and data["previewColumn"] and data["bar"]:
            print(f"  预览顶 - 原卷顶 = {data['previewColumn']['top'] - data['source']['top']}  (应为 0)")
        print(f"  顶栏底 = {data['bar']['bottom']}  题号带底 = {data['strip']['bottom']}  "
              f"编辑区底 = {data['input']['bottom']}  视口高 = {data['viewport']['h']}")
        print(f"  底部挡视线的悬浮/固定元素: {data['blockers'] or '无'}")
        page.screenshot(path=str(OUTPUT / f"answer-{args.tag}.png"))
        print("errors:", errors)
        print("saved:", OUTPUT / f"answer-{args.tag}.png")
        browser.close()


if __name__ == "__main__":
    sys.exit(main())
