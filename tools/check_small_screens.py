"""同一套界面，换几台常见的屏幕再量一遍。

这一轮的三处改动（面板折叠、目录预览、标签编辑）全部只在 1440×1000 上量过。
1000px 高的视口不是用户最常见的屏幕——1366×768 和 1280×720 的笔记本更常见。
「一屏放得下」这句话换个高度还成不成立，必须重新量，不能假设。

量的是实打实的像素：对话框底边、滚动区、底栏按钮的位置，
以及在每一档高度下有没有元素掉到视口外面。
"""

from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

SEED_TAGS = ["导数在研究函数中的应用", "导数的运算"]


def api(path: str, payload=None):
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-QB-Request": "1"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def seed():
    """借一道没标签的题当样本，跑完还回去。

    题库里带标签的题会被别的脚本清掉，脚本不能靠「正好有」才量得着。
    """
    items = api("/api/library?limit=100")["items"]
    target = next((item for item in items if not item.get("tags")), None)
    if not target:
        raise SystemExit("题库里找不到没标签的题，先导入几道再跑这个脚本")
    original = api(f"/api/library/{target['id']}/tags")["tags"]
    api(f"/api/library/{target['id']}/tags", {"tags": SEED_TAGS})
    return target["id"], original

SIZES = [
    (1920, 1080, "1920×1080 桌面"),
    (1536, 864, "1536×864 笔记本"),
    (1366, 768, "1366×768 老笔记本"),
    (1280, 720, "1280×720 更老的"),
    (1440, 1000, "1440×1000 上一轮量的那台"),
]

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def inside(page, selector: str) -> bool:
    """元素整个落在视口里。看不见的按钮不算「在」。"""
    box = page.locator(selector).first.bounding_box()
    if not box:
        return False
    return box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"] + 1


def open_panel(page):
    page.goto("about:blank")
    page.goto(f"{BASE}/settings#api", wait_until="networkidle")
    page.wait_for_selector("#credentialDialog[open]", timeout=15000)
    page.locator("#credentialAnswerTab").click()
    page.wait_for_selector("#libraryAISettingsDialog", state="visible", timeout=8000)
    page.wait_for_timeout(800)


def measure_panel(page) -> dict:
    return page.evaluate("""() => {
      const box = el => { const r = el.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, height: r.height}; };
      const dialog = document.querySelector('#libraryAISettingsDialog');
      return {
        viewport: window.innerHeight,
        dialog: box(dialog),
        save: box(document.querySelector('#libraryAISave')),
        scrollable: dialog.querySelector('.library-ai-body')?.scrollHeight >
                    dialog.querySelector('.library-ai-body')?.clientHeight,
      };
    }""")


def measure_catalogue(page) -> dict:
    page.locator("#libraryAIKnowledgeOpen").click()
    page.wait_for_selector("#knowledgeCatalogueDialog[open]", timeout=8000)
    page.wait_for_timeout(400)
    return page.evaluate("""() => {
      const box = el => { const r = el.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, height: r.height}; };
      const list = document.querySelector('#catalogueList');
      const foot = document.querySelector('.catalogue-foot');
      const search = document.querySelector('#libraryAIKnowledgeSearch');
      return {
        viewport: window.innerHeight,
        list: box(list), foot: box(foot), search: box(search),
        listScrolls: list.scrollHeight > list.clientHeight,
      };
    }""")


def measure_tag_editor(page) -> dict:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    if page.locator("#knowledgeCatalogueDialog").evaluate("el => el.open"):
        page.locator("#catalogueClose").click()
        page.wait_for_timeout(300)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    page.goto("about:blank")
    page.goto(f"{BASE}/library", wait_until="networkidle")
    page.wait_for_selector("#libraryList .library-card", timeout=20000)
    card = page.locator("#libraryList .library-card").filter(has=page.locator(".library-tags")).first
    if not card.count():
        return None
    card.locator(".library-tags-edit").click()
    page.wait_for_selector("#tagEditorDialog[open]", timeout=8000)
    page.wait_for_timeout(400)
    return page.evaluate("""() => {
      const box = el => { const r = el.getBoundingClientRect(); return {top: r.top, bottom: r.bottom, height: r.height}; };
      return {
        viewport: window.innerHeight,
        dialog: box(document.querySelector('#tagEditorDialog')),
        save: box(document.querySelector('#tagEditorSave')),
        list: box(document.querySelector('#tagEditorList')),
        listScrolls: (() => { const l = document.querySelector('#tagEditorList');
                               return l.scrollHeight > l.clientHeight; })(),
      };
    }""")


def run(page) -> None:
    for width, height, label in SIZES:
        page.set_viewport_size({"width": width, "height": height})
        print(f"\n--- {label} ---")

        open_panel(page)
        panel = measure_panel(page)
        check(panel["dialog"]["height"] <= height or panel["scrollable"],
              f"面板 {panel['dialog']['height']:.0f}px 在 {height}px 高的屏上放得下（放不下就内部滚动）")
        check(inside(page, "#libraryAISave"), "「保存 API 设置」在视口内")
        check(inside(page, "#libraryAIKnowledgeCount"), "知识点目录那行在视口内")

        catalogue = measure_catalogue(page)
        check(inside(page, "#libraryAIKnowledgeSearch"), "目录预览的搜索框在视口内")
        check(inside(page, ".catalogue-foot"), "目录预览的底栏说明在视口内")
        check(catalogue["listScrolls"] or catalogue["list"]["height"] <= height,
              f"73 个知识点在 {height}px 高的屏上能看完或自己滚（列高 {catalogue['list']['height']:.0f}px）")
        if height <= 768:
            page.screenshot(path=f"{SHOTS}/small_{width}x{height}_catalogue.png")

        editor = measure_tag_editor(page)
        if editor is None:
            check(False, f"{label}：题库里没有带标签的题，标签编辑器量不到")
        else:
            check(inside(page, "#tagEditorSave"), "标签对话框的「保存标签」在视口内")
            check(inside(page, "#tagEditorList"), "标签对话框的目录列表在视口内")
            check(editor["listScrolls"] or editor["list"]["height"] <= height,
                  f"73 个知识点在 {height}px 高的屏上能看完或自己滚（列高 {editor['list']['height']:.0f}px）")
            page.keyboard.press("Escape")
            if height <= 768:
                page.screenshot(path=f"{SHOTS}/small_{width}x{height}_tageditor.png")


def main() -> int:
    publication, original = seed()
    print(f"样本题：{publication}（跑完恢复成 {original or '无标签'}）")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                run(page)
            finally:
                browser.close()
    finally:
        api(f"/api/library/{publication}/tags", {"tags": original})
        print(f"已恢复 {publication} 的标签：{original or '（无）'}")
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
