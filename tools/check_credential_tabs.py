"""设置页上平铺的那份 API 配置，保存按钮要在够得着的地方。

1.26.9 之前，这份配置收在一个弹窗里，还分「读题与切题」「标签与答案」两个页签；
那一页曾把保存按钮顶到视口外面（1366×768 时底边 941px）。现在没有页签了 ——
两段内容一直摆在设置页上，同一个滚动条，所以这里改成量**整页**：从页首到
「保存密钥」按钮，一路量下来，并且保证页面上只有一个滚动容器在滚。
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


PROBE = """() => {
  const host = document.querySelector('#settingsCredentialHost');
  const pick = (el) => { if (!el) return null; const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, height: r.height,
            scrolls: el.scrollHeight > el.clientHeight + 1}; };
  const save = document.querySelector('#credentialSave');
  const panels = [...document.querySelectorAll('#credentialUi .credential-panel')]
    .map(pick);
  return {
    viewport: window.innerHeight,
    host: pick(host),
    panels,
    save: pick(save),
    saveText: save ? save.innerText.trim() : '',
    // 页面自己滚，不再有内层滚动容器（不然老师会滚到一半不知道还有内容）。
    innerScroll: [...document.querySelectorAll('#credentialUi .credential-panel, #credentialUi .credential-body')]
      .filter((el) => el.scrollHeight > el.clientHeight + 1).length,
  };
}"""


def run(page) -> None:
    for width, height in SIZES:
        page.set_viewport_size({"width": width, "height": height})
        page.goto("about:blank")
        page.goto(f"{BASE}/settings#api", wait_until="networkidle")
        page.wait_for_selector("#settingsCredentialHost #credentialUi", timeout=15000)
        page.wait_for_timeout(800)

        data = page.evaluate(PROBE)
        # 两段内容都在同一页上：都在页面上，没有被藏起来。
        check(len(data["panels"]) == 2, f"@ {width}×{height}：读题与标签答案两段都在同一页上")
        check(data["innerScroll"] == 0, f"@ {width}×{height}：配置区没有内层滚动条（{data['innerScroll']} 个）")
        # 保存按钮在页面里（整页滚动到底能点到，不是被顶出屏幕外）。
        save = data["save"] or {}
        check(bool(save.get("top")), f"@ {width}×{height}：保存按钮在页面上（{save.get('top', 0):.0f}px）")
        if not save.get("top"):
            page.screenshot(path=f"{SHOTS}/inline_{width}x{height}.png")


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
