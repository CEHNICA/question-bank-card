"""开着浮层时把窗口拖小：菜单、弹窗、改字操作栏还点得到吗。

题有据是打包成桌面应用的，用户**会一边开着浮层一边拖窗口边角**——把菜单拉出来比一比、
弹窗开着嫌宽随手收窄。这条路径和平常不一样：浮层是在**旧尺寸下算好位置**的，窗口一变
就得跟着重排。

覆盖四种「开着的东西」：题卡上的「更多」菜单、试卷操作菜单、知识点标签弹窗、
改字的全屏操作栏。每种都是：先在宽窗口下打开，再一路拖到 1000×560，然后逐项问
「滚到它面前 / 就地打一下 elementFromPoint，控件还点不点得到」。

只量「当前尺寸下重新打开一遍」是不够的——那量的是另一个东西，浮层从来没经历过缩放。
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

WIDE = (1600, 1000)
NARROW = [(1400, 900), (1200, 700), (1000, 560)]

results: list[tuple[bool, str]] = []
problems: list[str] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


# 同一个探针，改字/菜单/弹窗都能用：把 root 里所有可见可点的控件滚到面前再打一下。
PROBE = """(root) => {
  const bad = [];
  let total = 0, skipped = 0;
  for (const item of root.querySelectorAll('button, a[href], input, select, textarea, summary, [role=menuitem]')) {
    if (!item.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})) { skipped++; continue; }
    const r0 = item.getBoundingClientRect();
    if (r0.width < 1 || r0.height < 1) { skipped++; continue; }
    const label = (item.textContent || item.getAttribute('aria-label') || item.placeholder || item.tagName).trim().slice(0, 12);
    total++;
    item.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = item.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cy < 0 || cy > window.innerHeight || cx < 0 || cx > window.innerWidth) {
      bad.push({label, why: `拖小之后还在屏外（中心 ${Math.round(cx)},${Math.round(cy)}，视口 ${window.innerWidth}×${window.innerHeight}）`});
      continue;
    }
    const hit = document.elementFromPoint(cx, cy);
    if (hit !== item && !item.contains(hit)) {
      bad.push({label, why: `拖小之后被 ${hit ? hit.tagName + '.' + String(hit.className || '').split(' ')[0] : '（打不到）'} 压住`});
    }
  }
  return {total, bad, skipped};
}"""


def probe_open(page, root_selector: str, name: str, tag: str) -> None:
    if not page.locator(f"{root_selector}").count():
        check(False, f"{tag}：{name} 没打开，这一项没量到")
        return
    data = page.evaluate(PROBE, page.locator(root_selector).first.element_handle())
    for item in data["bad"]:
        problems.append(f"{tag} · {name} 的「{item['label']}」{item['why']}")
    check(not data["bad"],
          f"{tag}：{name} 的 {data['total']} 个控件拖小之后还点得到"
          if not data["bad"]
          else f"{tag}：{name} 有 {len(data['bad'])}/{data['total']} 个控件拖小之后点不到")


def run(page) -> None:
    # ---- 1. 题卡上的「更多」菜单
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    page.goto("about:blank")
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector("details.more", timeout=20000)
    page.wait_for_timeout(1500)
    if page.locator("#welcomeDialog[open]").count():
        page.locator("#welcomeSkip").first.click()
        page.wait_for_timeout(400)

    last = page.locator("details.more > summary").last
    last.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    last.click()
    page.wait_for_timeout(300)
    for width, height in NARROW:
        tag = f"{width}×{height}"
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(350)
        probe_open(page, "details.more[open]", "题卡「更多」菜单", tag)
        page.screenshot(path=f"{SHOTS}/resize_more_{width}x{height}.png")
    page.keyboard.press("Escape")
    page.evaluate("document.querySelectorAll('details[open]').forEach(d => d.open = false)")

    # ---- 2. 试卷操作菜单
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    page.wait_for_timeout(300)
    page.locator("#paperMenu > summary").first.click()
    page.wait_for_timeout(300)
    for width, height in NARROW:
        tag = f"{width}×{height}"
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(350)
        probe_open(page, "#paperMenu[open]", "试卷操作菜单", tag)
    page.keyboard.press("Escape")
    page.evaluate("document.querySelectorAll('details[open]').forEach(d => d.open = false)")

    # ---- 3. 改字的全屏操作栏（桌面应用里最容易被拖窄的那个）
    page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
    page.wait_for_timeout(300)
    card = page.locator(".card:not(.compact)").first
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    card.locator("button", has_text="改字").first.click()
    page.wait_for_timeout(700)
    if not page.locator(".card.editing .editor-bar").count():
        check(False, "改字没打开，操作栏那一项没量到")
    else:
        for width, height in NARROW:
            tag = f"{width}×{height}"
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(400)
            probe_open(page, ".card.editing .editor-bar", "改字操作栏", tag)
            page.screenshot(path=f"{SHOTS}/resize_editor_{width}x{height}.png")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        if page.locator("#confirmDialog[open]").count():
            page.locator("#confirmDialog button", has_text="丢弃改动").first.click()
        page.wait_for_timeout(500)

    # ---- 4. 题库的知识点标签弹窗（先借一道题的标签，量完还回去）
    import json
    import urllib.request

    def api(path: str, payload=None):
        request = urllib.request.Request(
            f"{BASE}{path}", data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json", "X-QB-Request": "1"},
            method="POST" if payload is not None else "GET")
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read())

    items = api("/api/library?limit=100")["items"]
    target = next((item for item in items if not item.get("tags")), None)
    if not target:
        check(False, "题库里找不到没标签的题，标签弹窗那一项没量到")
        return
    original = api(f"/api/library/{target['id']}/tags")["tags"]
    api(f"/api/library/{target['id']}/tags", {"tags": ["导数的运算"]})
    try:
        page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})
        page.goto("about:blank")
        page.goto(f"{BASE}/library", wait_until="networkidle")
        page.wait_for_selector("#libraryTagEdit", timeout=20000)
        page.wait_for_timeout(1200)
        page.locator("#libraryTagEdit").first.click()
        page.wait_for_timeout(500)
        for width, height in NARROW:
            tag = f"{width}×{height}"
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(400)
            probe_open(page, "dialog.tag-editor-dialog", "知识点标签弹窗", tag)
            page.screenshot(path=f"{SHOTS}/resize_tag_editor_{width}x{height}.png")
    finally:
        api(f"/api/library/{target['id']}/tags", {"tags": original})
        print(f"已把 {target['id']} 的标签还回 {original or '（空）'}")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": WIDE[0], "height": WIDE[1]})
        try:
            run(page)
        finally:
            browser.close()
    if problems:
        print("\n问题明细：")
        for line in problems:
            print("  · " + line)
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
