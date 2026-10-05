"""对照原卷看题：这道题的出处打不打得开、看不看得清、点得动点不动。

老师核题就靠这一步：题库里的题和原卷对不上时，打开出处看原卷那一页长什么样。
这一条是「对着原卷核对」这条主路的入口，已有压测没有一条走完过它。

量的是打开之后真的会怎样：
- 那一页图到底加载出来没有（打出「原卷暂时打不开」的占位也算一种结果，但得是明说的）
- 画在图上的题目框，位置有没有跑到图外面去
- 「本题范围 / 整页」切过去，画面真的换了
- 放大缩小：读数变了**而且画面真的变大变小**（只变读数是最容易漏的一种假动作）
- 放到头就不再放大了，「适应窗口」能把整页收进窗口
- 关掉再打开另一道题，看到的是不是新那道（上一道的内容留在屏幕上最难发现）
- 矮窗口下这一排控件还点不点得到

只读：只打开、缩放、切换，不改题、不删东西。
"""

from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/export"
WIDE = (1600, 1000)
NARROW = [(1280, 800), (1000, 560)]

results: list[tuple[bool, str]] = []
skipped: list[str] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def skip(label: str) -> None:
    skipped.append(label)
    print("SKIP  " + label)


# 画面尺寸：读 .source-content 的实际宽度，和读数一起看。只看读数会漏掉「数字动了、画没动」。
GEOMETRY = """() => {
  const content = document.querySelector('#sourcePages .source-content');
  const surface = document.querySelector('#sourcePages .source-surface');
  const pages = document.getElementById('sourcePages');
  const image = surface ? surface.querySelector('img') : null;
  return {
    zoom: document.getElementById('sourceZoom')?.textContent || '',
    contentWidth: content ? Math.round(content.getBoundingClientRect().width) : 0,
    surfaceWidth: surface ? Math.round(surface.getBoundingClientRect().width) : 0,
    surfaceHeight: surface ? Math.round(surface.getBoundingClientRect().height) : 0,
    viewportWidth: pages ? pages.clientWidth : 0,
    viewportHeight: pages ? pages.clientHeight : 0,
    imageLoaded: Boolean(image && image.complete && image.naturalWidth > 0),
    imageWidth: image ? image.naturalWidth : 0,
    placeholder: document.querySelector('#sourcePages .source-unavailable')?.textContent || '',
    noCoords: document.querySelector('#sourcePages .helper')?.textContent || '',
    boxCount: document.querySelectorAll('#sourcePages .source-box').length,
    boxesOutOfPage: Array.from(document.querySelectorAll('#sourcePages .source-box')).filter((b) => {
      const r = b.getBoundingClientRect(), s = surface.getBoundingClientRect();
      return r.left < s.left - 1 || r.top < s.top - 1 || r.right > s.right + 1 || r.bottom > s.bottom + 1;
    }).length
  };
}"""


def open_source(page, card_index: int) -> None:
    page.locator("article.library-card").nth(card_index).locator("button", has_text="查看出处").click()
    page.wait_for_selector("#sourceDialog[open]")


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": WIDE[0], "height": WIDE[1]})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{BASE}/library", wait_until="domcontentloaded")
        page.wait_for_selector("#typeFilters button")
        entry = page.locator("article.library-card").first.locator("button", has_text="查看出处")
        if entry.count() != 1:
            print(f"题卡上「查看出处」命中 {entry.count()} 个，这一步没量到")
            context.close()
            browser.close()
            return 1

        # ── 一、打开出处：那一页图到底出来没有 ────────────────────
        open_source(page, 0)
        title = page.locator("#sourceTitle").inner_text().strip()
        check("第" in title, f"标题写清了是哪份资料的第几题：{title}")
        page.wait_for_function(
            "() => { const i = document.querySelector('#sourcePages .source-surface img');"
            " return i ? (i.complete || i.dataset.ready === 'x') : false; }", timeout=30000)
        page.wait_for_timeout(700)
        first = page.evaluate(GEOMETRY)
        if not check(first["imageLoaded"], f"原卷那一页真的画出来了（{first['imageWidth']}×{first['imageWidth']} 宽的图）"):
            print(f"    占位上写着：{first['placeholder'] or first['noCoords']}")
            if not (first["placeholder"] or first["noCoords"]):
                skip("原卷没加载出来、界面上也什么都没说 —— 后面几项没量到")
                context.close()
                browser.close()
                return 1
        check(first["boxCount"] > 0, f"题目的位置框画出来了（{first['boxCount']} 个）")
        check(first["boxesOutOfPage"] == 0, f"位置框没有跑到图外面去（跑出去的：{first['boxesOutOfPage']} 个）")
        check(first["viewportWidth"] > 0 and first["surfaceWidth"] > 0, "画面有尺寸，不是空的")
        page.screenshot(path=f"{SHOTS}/source-open.png")

        # ── 二、放大缩小：读数和画面都得动 ──────────────────────
        before = page.evaluate(GEOMETRY)
        page.locator("#sourceZoomIn").click()
        page.wait_for_timeout(250)
        zoomed = page.evaluate(GEOMETRY)
        check(zoomed["zoom"] != before["zoom"], f"点放大读数变了：{before['zoom']} → {zoomed['zoom']}")
        check(zoomed["contentWidth"] > before["contentWidth"],
              f"画面真的变大了（{before['contentWidth']} → {zoomed['contentWidth']}px）")
        for _ in range(10):
            if page.locator("#sourceZoomIn").is_disabled():
                break
            page.locator("#sourceZoomIn").click()
            page.wait_for_timeout(150)
        maxed = page.evaluate(GEOMETRY)
        check(maxed["zoom"] == "400%", f"放到头就停住了（{maxed['zoom']}）")
        check(page.locator("#sourceZoomIn").is_disabled(), "到顶之后「放大」自己变灰，不会再往大了走")
        for _ in range(12):
            if page.locator("#sourceZoomOut").is_disabled():
                break
            page.locator("#sourceZoomOut").click()
            page.wait_for_timeout(150)
        minned = page.evaluate(GEOMETRY)
        check(minned["zoom"] == "25%", f"缩到头就停住了（{minned['zoom']}）")
        check(page.locator("#sourceZoomOut").is_disabled(), "到底之后「缩小」自己变灰")

        # ── 三、适应窗口：整页要收得进来看得全 ──────────────────
        page.locator("#sourceZoomIn").click()
        page.locator("#sourceZoomIn").click()
        page.locator("#sourceZoomIn").click()
        page.wait_for_timeout(300)
        wide_state = page.evaluate(GEOMETRY)
        check(wide_state["surfaceWidth"] > wide_state["viewportWidth"],
              f"先放大到撑出窗口（页宽 {wide_state['surfaceWidth']} > 窗口 {wide_state['viewportWidth']}）")
        page.locator("#sourceFit").click()
        page.wait_for_timeout(400)
        fitted = page.evaluate(GEOMETRY)
        check(fitted["surfaceWidth"] < wide_state["surfaceWidth"],
              f"「适应窗口」确实把画面收窄了（{wide_state['surfaceWidth']} → {fitted['surfaceWidth']}px）")
        check(fitted["surfaceWidth"] <= fitted["viewportWidth"] + 2,
              f"整页宽度收得进窗口（{fitted['surfaceWidth']} / {fitted['viewportWidth']}）")
        check(fitted["surfaceHeight"] <= fitted["viewportHeight"] + 2,
              f"整页高度也收得进（{fitted['surfaceHeight']} / {fitted['viewportHeight']}）")
        page.screenshot(path=f"{SHOTS}/source-fit.png")

        # ── 四、Ctrl+滚轮也能缩放 ────────────────────────────────
        wheel_before = page.evaluate(GEOMETRY)
        page.locator("#sourcePages").hover()
        page.mouse.wheel(0, 0)
        page.keyboard.down("Control")
        page.mouse.wheel(0, -400)
        page.keyboard.up("Control")
        page.wait_for_timeout(300)
        wheel_after = page.evaluate(GEOMETRY)
        check(wheel_after["contentWidth"] != wheel_before["contentWidth"],
              f"Ctrl+滚轮也缩放（{wheel_before['contentWidth']} → {wheel_after['contentWidth']}px）")

        # ── 五、本题范围 / 整页 ─────────────────────────────────
        page.locator("#sourceQuestion").click()
        page.wait_for_timeout(600)
        cropped = page.evaluate(GEOMETRY)
        page.locator("#sourceWholePage").click()
        page.wait_for_timeout(900)
        whole = page.evaluate(GEOMETRY)
        check(cropped["imageWidth"] == whole["imageWidth"], "切到整页还是同一张原卷图")
        check(page.locator("#sourceWholePage").get_attribute("aria-pressed") == "true", "「整页」按下去了")
        check(page.locator("#sourceQuestion").get_attribute("aria-pressed") == "false", "「本题范围」弹起来了")
        page.screenshot(path=f"{SHOTS}/source-whole.png")
        page.locator("#sourceQuestion").click()
        page.wait_for_timeout(500)

        # ── 六、关掉再开另一道题，看的是不是新那道 ──────────────
        first_id = page.locator("article.library-card").first.get_attribute("id")
        page.keyboard.press("Escape")
        page.wait_for_selector("#sourceDialog", state="hidden")
        page.wait_for_timeout(400)
        open_source(page, 1)
        page.wait_for_timeout(1200)
        second_title = page.locator("#sourceTitle").inner_text().strip()
        other = page.locator("article.library-card").nth(1).get_attribute("id")
        check(other not in second_title or first_id != other,
              f"换了一道题看，标题跟着换了：{second_title}")
        second = page.evaluate(GEOMETRY)
        check(second["imageLoaded"], f"换题之后那一页也加载出来了（{second['imageWidth']}px 宽）")
        check(second["boxCount"] > 0, f"换题之后位置框也重画了（{second['boxCount']} 个）")
        page.screenshot(path=f"{SHOTS}/source-second.png")
        page.keyboard.press("Escape")
        page.wait_for_selector("#sourceDialog", state="hidden")

        # ── 七、矮窗口下这一排控件还点不点得到 ──────────────────
        for width, height in NARROW:
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(400)
            open_source(page, 0)
            page.wait_for_timeout(1200)
            blocked = page.evaluate("""(root) => {
              const bad = [];
              for (const item of root.querySelectorAll('button, a[href], summary, [role=menuitem]')) {
                if (!item.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})) continue;
                item.scrollIntoView({block: 'center', inline: 'nearest'});
                const r = item.getBoundingClientRect();
                if (r.width < 1 || r.height < 1) continue;
                const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
                const hit = document.elementFromPoint(cx, cy);
                if (!hit || !root.contains(hit) || !item.contains(hit) && hit !== item) {
                  bad.push((item.textContent || item.tagName).trim().slice(0, 10));
                }
              }
              return bad;
            }""", page.locator("#sourceDialog").element_handle())
            check(not blocked, f"{width}×{height} 下原卷窗里的控件都点得到（被挡的：{blocked}）")
            page.screenshot(path=f"{SHOTS}/source-{width}.png")
            page.keyboard.press("Escape")
            page.wait_for_selector("#sourceDialog", state="hidden")
        page.set_viewport_size({"width": WIDE[0], "height": WIDE[1]})

        check(not errors, f"全程没有脚本报错（{errors[:2]}）")
        context.close()
        browser.close()

    failures = len([1 for ok, _ in results if not ok])
    print()
    print(f"共 {len(results)} 条，FAIL {failures} 条")
    if skipped:
        print("没量的：")
        for item in skipped:
            print("  - " + item)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
