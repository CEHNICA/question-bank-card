"""量题库页的纵向占用 —— 这次版面重排唯一要证明的事。

一屏 768px 里，题目拿到的必须比所有附属横条加起来还多。
改版前是题目 334 / 附属 434（题目输了）。这个脚本把两条数各算一遍，
再顺手确认抽屉确实折进去了（关着时导航在视口外）。

Browser 面板只有 639-901px，量不出宽屏，所以这里显式给 viewport。
只读，不改数据。

用法： python tools/probe_library_vertical.py --url http://127.0.0.1:8802
"""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "layout"

MEASURE = """() => {
  const box = (sel) => { const n = document.querySelector(sel); if (!n) return null;
    const r = n.getBoundingClientRect();
    return {top: Math.round(r.top), bottom: Math.round(r.bottom), left: Math.round(r.left),
      right: Math.round(r.right), width: Math.round(r.width), height: Math.round(r.height)}; };
  // 「附属横条」= 压在题目上方的条。侧栏是并排的一列，不算横条。
  const chromeBoxes = ['.topbar', '.library-toolbar', '.library-bulk']
    .map(box).filter((b) => b && b.width > 0 && b.height > 0);
  const chromeTop = Math.min(...chromeBoxes.map(b => b.top));
  const chromeBottom = Math.max(...chromeBoxes.map(b => b.bottom));
  const chrome = chromeBottom - chromeTop;
  const card = document.querySelector('.library-card');
  const cr = card ? card.getBoundingClientRect() : null;
  const cardTop = cr ? Math.round(cr.top) : null;
  const laterChrome = chromeBoxes.filter(b => b.top >= (cardTop ?? 0) + 1)
    .reduce((sum, b) => sum + b.height, 0);
  const question = cr ? (window.innerHeight - cardTop) - laterChrome : null;
  const cards = [...document.querySelectorAll('.library-card')].map(n => n.getBoundingClientRect())
    .filter(r => r.height > 0);
  const navLink = document.querySelector('.topnav a');
  const trigger = document.querySelector('.drawer-trigger');
  const tr = trigger ? trigger.getBoundingClientRect() : null;
  const cs = trigger ? getComputedStyle(trigger) : null;
  // 题面那几块本来就有 overflow-x: auto：出现横向滚动条 = 侧栏把题目挤窄了。
  const overflowing = [...document.querySelectorAll('.library-card .qb-stem-body, .library-card .qb-option-body, .library-card .qb-analysis')]
    .filter(n => n.scrollWidth > n.clientWidth + 1).length;
  return {
    viewport: {w: window.innerWidth, h: window.innerHeight},
    topbar: box('.topbar'), bulk: box('.library-bulk'), rail: box('.library-rail'),
    railPosition: (() => { const n = document.querySelector('.library-rail'); return n ? getComputedStyle(n).position : null; })(),
    resultsHeadExists: Boolean(document.querySelector('.library-results-head')),
    cardTop,
    cardWidth: cr ? Math.round(cr.width) : null,
    stemWidth: (() => { const n = document.querySelector('.library-card .qb-stem-body, .library-card .paper'); return n ? Math.round(n.getBoundingClientRect().width) : null; })(),
    firstCardHeight: cr ? Math.round(cr.height) : null,
    fullyVisibleCards: cards.filter(r => r.top >= (cardTop ?? 0) - 1 && r.bottom <= window.innerHeight).length,
    verdict: question === null ? null : {question, chrome, ok: question > chrome},
    overflowingTextBlocks: overflowing,
    navInViewport: navLink ? (() => { const r = navLink.getBoundingClientRect();
      return r.right > 0 && r.left < window.innerWidth && r.bottom > 0 && r.top < window.innerHeight; })() : null,
    drawerOpen: Boolean(window.QBSiteDrawer?.isOpen?.()),
    trigger: tr ? {w: Math.round(tr.width), h: Math.round(tr.height), border: cs.borderTopWidth, bg: cs.backgroundColor} : null,
    topbarToolsVisible: (() => { const n = document.querySelector('.topbar-tools'); if (!n) return null;
      return getComputedStyle(n).display !== 'none'; })(),
    scrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth
  };
}"""


def launch(pw, url):
    executable = next((str(p) for p in (Path(pw.chromium.executable_path),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
    return pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--width", type=int, default=1366)
    parser.add_argument("--height", type=int, default=768)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local server is allowed")

    from playwright.sync_api import sync_playwright

    OUTPUT.mkdir(parents=True, exist_ok=True)
    errors, failures = [], []
    with sync_playwright() as pw:
        browser = launch(pw, url)
        for width, height in ((args.width, args.height), (1100, 768), (1001, 768), (979, 768), (820, 768), (390, 780)):
            context = browser.new_context(viewport={"width": width, "height": height})
            context.add_init_script("localStorage.setItem('qb-welcome-seen','1'); localStorage.setItem('qb-lens','0')")
            context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                          ("127.0.0.1", "localhost") else route.abort())
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{url}/library")
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".library-card", timeout=15000)
            page.wait_for_timeout(400)
            data = page.evaluate(MEASURE)
            print(f"=== {width}x{height} ===")
            print(json.dumps(data, ensure_ascii=False, indent=2))
            page.screenshot(path=str(OUTPUT / f"library-{width}.png"))

            if width == args.width:
                if data["resultsHeadExists"]:
                    failures.append("范围切换那一行还在")
                if data["navInViewport"]:
                    failures.append("抽屉关着时导航仍在视口内 —— 没收进去")
                if data["cardTop"] is None or data["cardTop"] > 110:
                    failures.append(f"第一道题 top = {data['cardTop']}，目标 ≤ 110")
                if (data["verdict"] or {}).get("ok") is not True:
                    failures.append(f"一屏里题目 {data['verdict']} 没比附属横条多")
                if (data["rail"] or {}).get("width") != 268:
                    failures.append(f"侧栏宽 {(data['rail'] or {}).get('width')}，目标 268")
                if (data["stemWidth"] or 0) < 900:
                    failures.append(f"题面宽 {data['stemWidth']}，目标 ≥ 900")
                if data["overflowingTextBlocks"]:
                    failures.append(f"{data['overflowingTextBlocks']} 块题面出现横向滚动条 = 侧栏挤窄了题目")
                if data["trigger"] and (data["trigger"]["w"] > 34 or data["trigger"]["h"] > 34
                                        or data["trigger"]["border"] != "0px"):
                    failures.append(f"☰ 触发器 {data['trigger']}，要 ≤34px 见方、无边框")
                if data["scrollX"] > 0:
                    failures.append(f"横向溢出 {data['scrollX']}px")

                # 抽屉打开后：三个导航都在、当前页有高亮
                page.click(".drawer-trigger")
                page.wait_for_timeout(350)
                opened = page.evaluate("""() => ({
                  open: window.QBSiteDrawer.isOpen(),
                  links: [...document.querySelectorAll('.site-drawer .topnav a')].map(a => ({
                    text: a.textContent.trim(), active: a.classList.contains('active') })),
                  tools: [...document.querySelectorAll('.site-drawer [data-slot="tools"] > *')].map(n => n.textContent.trim()),
                  library: [...document.querySelectorAll('.site-drawer [data-slot="library"] > *')].map(n => n.textContent.trim()),
                  hint: Boolean(document.querySelector('.site-drawer [data-slot="hint"] #libraryShortcutHint')),
                  panelVisible: !document.querySelector('#siteDrawer').hidden
                })""")
                print("  抽屉打开后:", json.dumps(opened, ensure_ascii=False))
                page.screenshot(path=str(OUTPUT / f"library-{width}-drawer.png"))
                if not opened["open"] or len(opened["links"]) != 3:
                    failures.append(f"抽屉里的导航不对：{opened['links']}")
                if sum(1 for link in opened["links"] if link["active"]) != 1:
                    failures.append("抽屉里当前页没有唯一高亮")
                if not opened["hint"]:
                    failures.append("操作说明没进抽屉")
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
                if page.evaluate("window.QBSiteDrawer.isOpen()"):
                    failures.append("Esc 没收起抽屉")
            else:
                if data["scrollX"] > 0:
                    failures.append(f"{width}px 横向溢出 {data['scrollX']}px")
                # 1001 侧栏常驻，1000 以下变右侧浮层：中间不留半吊子状态
                if width == 1001 and (data["rail"] or {}).get("width") != 268:
                    failures.append(f"1001px 侧栏应为常驻 268，实际 {(data['rail'] or {}).get('width')}")
                if width <= 979:
                    if data["railPosition"] != "fixed":
                        failures.append(f"{width}px 侧栏应变浮层（position: fixed），实际 {data['railPosition']}")
                    toggle = page.locator("#libraryFilterToggle")
                    if not toggle.is_visible():
                        failures.append(f"{width}px 顶栏右端应有「筛选」按钮")
                    else:
                        toggle.click()
                        page.wait_for_timeout(350)
                        opened = page.evaluate("""() => {
                            const r = document.querySelector('.library-rail').getBoundingClientRect();
                            return {open: document.body.classList.contains('rail-open'),
                              onScreen: r.left < window.innerWidth && r.right > 0,
                              fromRight: Math.round(r.right),
                              drawerShut: !window.QBSiteDrawer.isOpen()};
                        }""")
                        if not opened["open"] or not opened["onScreen"] or not opened["drawerShut"]:
                            failures.append(f"{width}px 侧栏浮层没拉开或导航没让位：{opened}")
                        page.screenshot(path=str(OUTPUT / f"library-{width}-rail.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(250)
                        if page.evaluate("document.body.classList.contains('rail-open')"):
                            failures.append(f"{width}px Esc 没收起侧栏")

                        # 抽屉开着时遮罩会盖住顶栏（模态该有的样子），所以 force 点：
                        # 这里验的是「强行点筛选时，导航抽屉会自己让位」这条守卫。
                        page.click(".drawer-trigger")
                        page.wait_for_timeout(300)
                        if not page.evaluate("window.QBSiteDrawer.isOpen()"):
                            failures.append(f"{width}px 导航抽屉没拉开")
                        # 抽屉开着时遮罩会盖住顶栏，真人点不到（模态该有的样子）。
                        # 用脚本派发点击绕过命中测试，验「强行点筛选时导航自己让位」这条守卫。
                        page.evaluate("document.getElementById('libraryFilterToggle').click()")
                        page.wait_for_timeout(350)
                        both = page.evaluate("({rail: document.body.classList.contains('rail-open'), drawer: window.QBSiteDrawer.isOpen()})")
                        if not both["rail"]:
                            failures.append(f"{width}px 点「筛选」没拉开侧栏：{both}")
                        if both["drawer"]:
                            failures.append(f"{width}px 两个面板同时开着：{both}")
                        page.screenshot(path=str(OUTPUT / f"library-{width}-rail.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(250)
            context.close()
        browser.close()

    print("errors:", errors or "无")
    print("failures:", failures or "无")
    print("saved:", OUTPUT)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
