"""录入终审这一页的浮层挡路压测。

1.13.3 修的三个「界面挡路」问题里，有两个在这一类：菜单被别的东西盖住、
   操作条把按钮压在下面。录入终审是用户待得最久的页面，从没做过这个压测。

做法是像用户那样**用鼠标点开**菜单（不是设 menu.open = true——那不算数，
菜单的开合方向、定位都只在真实点击里才发生），再对菜单里每个可点的元素，
在它中心点打一下 document.elementFromPoint：打回来的不是它自己，就是被压住了。
压它的是谁也一并报出来（标签 + 类名 + id），不然只知道「点不到」，不知道被谁挡的。

扫的是**每一张卡**，不是第一张和最后一张：这个 bug 只在「这张卡露在屏幕里的
那一截比菜单还矮」时才出（实测最后一张卡 4 项里第 1 项整个消失），换一份数据
位置就变，写死首尾两张等于没测。

五个容易把结论带歪的地方，脚本一开始就先处理掉：
- 欢迎弹窗。它是模态，会把底下所有东西都算成「被压住」。
- 菜单只开一个。开着一个再开另一个，前一个会被自动收掉。
- 滚动位置。吸顶条、吸底操作条只在某些位置才压得到东西，只量一个位置等于没量。
- 关掉菜单后残留的浮层会挡住下一张卡，开下一张前先全部收掉。
- 菜单面板要**正好命中一个**。工具栏里「处理说明」和「工具」挨着，找宽一层就会
  量到隔壁那个空面板，然后一路报「菜单是空的，跳过」——绿灯，菜单一项没测。
"""

from __future__ import annotations

import sys

from PIL import Image
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

SIZES = [(1920, 1080), (1366, 768)]
SCROLLS = [0.0, 0.5, 1.0]

results: list[tuple[bool, str]] = []
blocked: list[str] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


PROBE = """(menu) => {
  const out = [];
  const items = menu.querySelectorAll('button, a, [role=menuitem]');
  for (const item of items) {
    const r = item.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    if (r.bottom < 0 || r.top > window.innerHeight) continue;   // 本来就在屏外，不算被挡
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    const hit = document.elementFromPoint(cx, cy);
    if (hit !== item && !item.contains(hit)) {
      out.push({label: (item.textContent || item.getAttribute('aria-label') || '?').trim().slice(0, 18),
                coveredBy: hit ? (hit.tagName + (hit.id ? '#' + hit.id : '') +
                                  (hit.className ? '.' + String(hit.className).trim().split(/\\s+/).slice(0,2).join('.') : '')) : '（打不到）',
                itemTop: Math.round(r.top), itemBottom: Math.round(r.bottom)});
    }
  }
  return {items: out, count: items.length};
}"""


def dismiss_modals(page) -> None:
    for selector in ("#welcomeDialog[open]", "#automaticGuideDialog[open]"):
        while page.locator(selector).count():
            page.locator(f"{selector} [data-close]").first.click() if page.locator(f"{selector} [data-close]").count() \
                else page.locator("#welcomeSkip").first.click()
            page.wait_for_timeout(400)


def close_everything(page) -> None:
    page.evaluate("document.querySelectorAll('details[open]').forEach(d => d.open = false)")
    page.wait_for_timeout(120)


def open_and_probe(page, summary_locator, label: str) -> tuple[int, int]:
    """点开一个菜单，逐项探可点性。返回 (被挡项数, 可点项总数)。"""
    summary_locator.scroll_into_view_if_needed()
    page.wait_for_timeout(160)
    summary_locator.click()
    page.wait_for_timeout(180)
    # 只往上找一层，落在 summary 所在的 details 里。用 ../.. 会落到整组工具栏上，
    # 那里面「处理说明」的面板排在「工具」前面，量的是一个空面板还报 PASS ——
    # 工具菜单因此整整一轮没被量过。面板必须正好命中 1 个，命中多个就当脚本坏了。
    panel = summary_locator.locator("xpath=..").locator(".more-menu, .menu-panel")
    if panel.count() != 1:
        check(False, f"{label}：脚本没找准菜单面板（命中 {panel.count()} 个），这一项没量到")
        close_everything(page)
        return 0, 0
    data = page.evaluate(PROBE, panel.element_handle())
    if data["items"]:
        for item in data["items"]:
            blocked.append(f"{label} · 「{item['label']}」被 {item['coveredBy']} 压住（元素在 {item['itemTop']}–{item['itemBottom']}）")
    close_everything(page)
    return len(data["items"]), data["count"]


def check_menu(page, summary_locator, label: str) -> None:
    bad, total = open_and_probe(page, summary_locator, label)
    if total == 0:
        check(False, f"{label}：菜单里一个可点项都没有（多半是脚本找错了面板）")
    elif bad == 0:
        check(True, f"{label}：{total} 个可点项全都点得到")
    else:
        check(False, f"{label}：{bad}/{total} 个可点项被压住")


# 菜单开着时卡片左上角的圆角不能被顶成方角 —— 放开裁剪的代价，得守住。
#
# 判据只能是**像素**：早先用 elementFromPoint 去看「圆角外那几像素打到了谁」，
# 结论和截图互相矛盾（一次说卡片自己顶出来了、一次说图完全一样），因为命中测试
# 和实际绘制不是一回事。现在反过来做：截两��同一块地方 —— 菜单开着（放开裁剪），
# 再注入一条 overflow: clip !important 把裁剪按回去 —— 只让这一个变量变，比像素差。
# 两张图应该只差抗锯齿。阈值取 24：卡片左边缘常常落在小数像素上（比如 307.515625），
# 圆角弧线上的抗锯齿像素本来就会有零点几个通道的抖动，实测最大 10；而真顶出方角时
# 同一块地方差 85、连着 174 个像素变。两个量级差着好几倍，24 卡在中间不会两边都放过。
CORNER_CLIP_IT = ".card:has(.card-actions > details.more[open]) { overflow: clip !important; }"
CORNER_MAX_DELTA = 24


def corner_clip(page) -> dict | None:
    return page.evaluate("""() => {
      const c = document.querySelector('.card:has(.card-actions > details.more[open])');
      if (!c) return null;
      const r = c.getBoundingClientRect();
      if (r.top < 0 || r.bottom > window.innerHeight) return null;
      return {x: Math.max(0, r.left - 5), y: Math.max(0, r.top - 5), width: 70, height: 60};
    }""")


def check_corner(page, tag: str) -> None:
    clip = corner_clip(page)
    if clip is None:
        check(True, f"{tag}：圆角检查跳过（开着菜单的那张卡不在屏幕里）")
        return
    before = f"{SHOTS}/_corner_open.png"
    after = f"{SHOTS}/_corner_clip.png"
    page.screenshot(path=before, clip=clip)
    page.evaluate("(css) => { const s = document.createElement('style'); s.id = 'corner-probe'; s.textContent = css; document.head.appendChild(s); }", CORNER_CLIP_IT)
    page.wait_for_timeout(200)
    page.screenshot(path=after, clip=corner_clip(page) or clip)
    page.evaluate("document.getElementById('corner-probe')?.remove()")
    page.wait_for_timeout(150)

    a = Image.open(before).convert("RGB")
    b = Image.open(after).convert("RGB")
    if a.size != b.size:
        check(False, f"{tag}：圆角比对的两张图尺寸不一样（{a.size} vs {b.size}）")
        return
    worst, changed = 0, 0
    for pa, pb in zip(a.getdata(), b.getdata()):
        d = max(abs(x - y) for x, y in zip(pa, pb))
        if d:
            changed += 1
            worst = max(worst, d)
    check(worst <= CORNER_MAX_DELTA,
          f"{tag}：菜单开着时卡片左上角和裁剪时几乎一样（{changed} 个像素有差，最大通道差 {worst}）"
          if worst <= CORNER_MAX_DELTA else
          f"{tag}：菜单开着时卡片左上角顶出方角了（{changed} 个像素，最大通道差 {worst}）")


def run(page) -> None:
    for width, height in SIZES:
        page.set_viewport_size({"width": width, "height": height})
        page.goto("about:blank")
        page.goto(f"{BASE}/", wait_until="networkidle")
        page.wait_for_selector("details.more", timeout=20000)
        page.wait_for_timeout(1500)
        dismiss_modals(page)
        cards = page.locator("details.more").count()
        still_open = page.locator("dialog[open]").count()
        check(still_open == 0, f"{width}×{height}：关掉欢迎弹窗之后页面上没有别的模态（剩 {still_open} 个）")
        check(cards >= 2, f"{width}×{height}：这一页有 {cards} 张题卡可扫")
        print(f"\n--- {width}×{height}：{cards} 张题卡 ---")

        for fraction in SCROLLS:
            span = page.evaluate("document.documentElement.scrollHeight") - height
            page.evaluate("(y) => window.scrollTo(0, y)", max(0, int(span * fraction)))
            page.wait_for_timeout(300)
            where = "顶部" if fraction == 0 else ("中段" if fraction == 0.5 else "最底")

            selector = "#paperMenu > summary"
            if page.locator(selector).count():
                check_menu(page, page.locator(selector).first, f"试卷操作 @ {where}")

            bad_total = 0
            for index in range(cards):
                bad, _ = open_and_probe(page, page.locator("details.more > summary").nth(index),
                                       f"第 {index + 1} 张卡的「更多」 @ {where}")
                bad_total += bad
            check(bad_total == 0,
                  f"{width}×{height} @ {where}：{cards} 张卡的「更多」菜单（{cards * 4} 项）全都点得到"
                  if bad_total == 0 else
                  f"{width}×{height} @ {where}：{cards} 张卡里有 {bad_total} 项被压住")

        # 圆角特写：菜单开着的时候量这张正在屏幕里的卡
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(300)
        first = page.locator("details.more > summary").first
        first.scroll_into_view_if_needed()
        first.click()
        page.wait_for_timeout(250)
        check_corner(page, f"{width}×{height}")
        page.screenshot(path=f"{SHOTS}/review_overlay_{width}x{height}.png")
        close_everything(page)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            run(page)
        finally:
            browser.close()
    if blocked:
        print("\n被压住的明细：")
        for line in blocked:
            print("  · " + line)
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
