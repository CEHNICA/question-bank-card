"""量「标签与答案」面板到底有多高：改完要一屏放得下四个功能开关。

这不是「代码里删了一行 open = true」就算数。断言落在实测像素上：
视口 1000px 高的屏幕上，面板和四个开关都要落在首屏里，
功能开关那一段不再被 1669px 高的模型配置顶到屏幕外。
"""

from __future__ import annotations

import json
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"
VIEWPORT = {"width": 1440, "height": 1000}

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


MEASURE = """() => {
  const box = el => { const r = el.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, height: r.height}; };
  const dialog = document.querySelector('#libraryAISettingsDialog');
  const advanced = document.querySelector('#libraryAIAdvanced');
  const save = document.querySelector('#libraryAISave');
  return {
    viewport: window.innerHeight,
    dialog: box(dialog),
    advancedOpen: advanced.open,
    advanced: box(advanced),
    tags: box(document.querySelector('#libraryAITags')),
    answer: box(document.querySelector('#libraryAIAnswer')),
    saveBottom: save ? box(save).bottom : 0,
  };
}"""


def measure(page):
    # /settings#api 会直接打开「API 配置」窗口的「标签与答案」页。
    # 先跳到 about:blank：连着两次 goto 同一个带 # 的地址是同文档跳转，不会重新加载，
    # 面板的折叠状态会被上一轮留下来，量出来的就不是「打开时的样子」了。
    page.goto("about:blank")
    page.goto(f"{BASE}/settings#api", wait_until="networkidle")
    page.wait_for_selector("#credentialDialog[open]", timeout=15000)
    page.locator("#credentialAnswerTab").click()
    page.wait_for_selector("#libraryAISettingsDialog", state="visible", timeout=8000)
    page.wait_for_timeout(900)
    return page.evaluate(MEASURE)


def measure_open(page):
    return page.evaluate(MEASURE)


def run(page) -> None:
    # 交付行为：打开设置，模型配置就是收着的。
    folded = measure(page)
    page.screenshot(path=f"{SHOTS}/panel_after.png")
    check(not folded["advancedOpen"], "打开「标签与答案」时模型配置默认是收起的")
    check(folded["dialog"]["height"] <= folded["viewport"],
          f"面板高 {folded['dialog']['height']:.0f}px，放得进 {folded['viewport']}px 的视口")
    check(folded["answer"]["bottom"] <= folded["viewport"],
          f"两个开关都在首屏里（第二个底边 {folded['answer']['bottom']:.0f}px）")

    # 展开是什么样、代价多大：手动撑开再量一次，这就是改动前每次打开的样子。
    page.evaluate("document.querySelector('#libraryAIAdvanced').open = true")
    page.wait_for_timeout(500)
    expanded = measure_open(page)
    page.screenshot(path=f"{SHOTS}/panel_before.png")
    check(expanded["advanced"]["height"] > 1000,
          f"展开后模型配置自己就 {expanded['advanced']['height']:.0f}px 高")
    check(expanded["dialog"]["height"] > folded["dialog"]["height"] + 500,
          f"整个面板从 {folded['dialog']['height']:.0f}px 涨到 {expanded['dialog']['height']:.0f}px")
    check(expanded["dialog"]["height"] > expanded["viewport"] or expanded["saveBottom"] > expanded["viewport"],
          f"展开后保存按钮底边 {expanded['saveBottom']:.0f}px，一屏 {expanded['viewport']}px 放不下")

    # API 没配好的时候，那一块要自动展开——用户本来就得进去改。
    ready = json.loads(page.request.get(f"{BASE}/api/settings/library-ai").text())
    fake = {"ready": False}
    page.route("**/api/settings/library-ai", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({**ready, "api_ready": fake["ready"], "message": "没有可用密钥"})))
    unready = measure(page)
    check(unready["advancedOpen"], "API 没配好时，模型配置自动展开，不用用户自己找")
    fake["ready"] = True
    check(not measure(page)["advancedOpen"], "配好之后重新打开就收回去了")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        try:
            run(page)
        finally:
            browser.close()
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
