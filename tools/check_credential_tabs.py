"""同一扇「API 配置」窗口里的两个页签，都要在一屏内够得着保存。

上一轮发现「标签与答案」那一页把保存按钮顶到了视口外面（1366×768 时底边 941px）。
那一页已经修好。同一扇窗口里还有一个页签「读题与切题」——从来没量过，
里面是读题服务的密钥配置，内容更长。两个页签现在是同一种结构，
所以这里把两个一起量：各自的滚动容器、自己的保存栏、以及在四档屏幕上的位置。
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

SIZES = [(1920, 1080), (1536, 864), (1366, 768), (1280, 720)]

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


PROBE = """(tab) => {
  const panel = document.querySelector(tab);
  const pick = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, height: r.height,
            scrolls: el.scrollHeight > el.clientHeight + 1}; };
  const actions = panel.querySelector('.credential-actions, .credential-form .actions, .credential-footer');
  return {
    viewport: window.innerHeight,
    panel: pick(panel),
    actions: pick(actions),
    actionsText: actions ? actions.innerText.replace(/\\s+/g, ' ').trim().slice(0, 60) : '',
    lastButton: pick(panel.querySelector('button[type=submit]') || panel.querySelector('.actions button:last-of-type')),
  };
}"""


def run(page) -> None:
    for width, height in SIZES:
        page.set_viewport_size({"width": width, "height": height})
        page.goto("about:blank")
        page.goto(f"{BASE}/settings#api", wait_until="networkidle")
        page.wait_for_selector("#credentialDialog[open]", timeout=15000)
        page.wait_for_timeout(500)

        for tab, panel, label in (("#credentialReadingTab", "#credentialReadingPanel", "读题与切题"),
                                  ("#credentialAnswerTab", "#credentialAnswerPanel", "标签与答案")):
            page.locator(tab).click()
            page.wait_for_timeout(900)
            data = page.evaluate(PROBE, panel)
            # 「读题与切题」没有独立底栏，它的主按钮就是最下面那个提交/保存。
            bottom = (data["actions"] or data["lastButton"] or {}).get("bottom", 0)
            fits = 0 < bottom <= height + 1
            check(fits, f"{label} @ {width}×{height}：主按钮底边 {bottom:.0f}px 在视口内（视口 {height}）")
            if not fits:
                page.screenshot(path=f"{SHOTS}/tab_{width}x{height}_{label}.png")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            run(page)
        finally:
            browser.close()
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
