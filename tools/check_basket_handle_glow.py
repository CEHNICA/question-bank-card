# -*- coding: utf-8 -*-
"""验收试题篮把手的荧光呼吸灯。

把手的状态全在 localStorage 里，所以这里用 add_init_script 播种试题篮，不写后端数据。
断言全部落在可观测的计算样式、几何和对比度上：只看 animationName 会被「写了动画
但没真跑」骗过去，所以要跨一个周期采样；只看颜色又会漏掉「2/255 那种等于没有」，
所以要拿波谷/波峰的底色和白比、和数字的对比度一起算。
"""
import argparse
import asyncio
import json
import pathlib
import re
import sys

from playwright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SEEN_PREF = "qb-basket-handle-seen"
WIDTH, HEIGHT = 1400, 950
PERIOD_MS = 2800

READ_AFTER = """
() => {
  const handle = document.getElementById("basketHandle");
  const countEl = document.getElementById("basketHandleCount");
  const glow = getComputedStyle(handle, "::after");
  const rect = handle.getBoundingClientRect();
  return {
    animationName: glow.animationName,
    backgroundColor: glow.backgroundColor,
    boxShadow: glow.boxShadow,
    color: getComputedStyle(countEl).color,
    text: countEl.textContent,
    right: rect.right, width: rect.width, height: rect.height,
    bodyClasses: document.body.className,
  };
}
"""

RGB = re.compile(r"[\d.]+")
failures = []
notes = []


def check(ok, message):
    print(("  OK  " if ok else "  FAIL ") + message)
    if not ok:
        failures.append(message)
    return ok


def parse_rgba(value):
    parts = [float(x) for x in RGB.findall(value)[:4]]
    if len(parts) == 4:
        return tuple(parts)
    return (tuple(parts[:3]) + (1.0,)) if len(parts) == 3 else (0.0, 0.0, 0.0, 0.0)


def parse_rgb(value):
    return parse_rgba(value)[:3]


def over_white(rgba):
    """Composite onto the handle's own white surface; a fully transparent fill is just white."""
    r, g, b, a = rgba
    return tuple(channel * a + 255 * (1 - a) for channel in (r, g, b))


def relative_luminance(rgb):
    channels = []
    for raw in rgb:
        value = raw / 255
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(first, second):
    a, b = relative_luminance(first), relative_luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def distance_from_white(value):
    """How far the glow's own fill sits from the page white. No fill means zero, not 255:
    a fully transparent rgba(0,0,0,0) is not 'very bright', it is 'not there'."""
    r, g, b, a = parse_rgba(value)
    if a < 0.5:
        return 0.0
    return max(255 - channel for channel in (r, g, b))


async def sample_cycle(page, steps=9):
    """Read the pseudo-element across more than one full period of the animation."""
    reads = []
    for index in range(steps):
        reads.append(await page.evaluate(READ_AFTER))
        await page.wait_for_timeout(PERIOD_MS * 1.4 / steps)
    return reads


def spread_of(box_shadow):
    """The 'Npx 0 Ypx' spill layers as (offset, blur).

    The computed value is `rgba(...) -8px 0px 15px 0px` — colour first and four
    lengths, not the three the stylesheet is written with.
    """
    layers = []
    for match in re.finditer(
            r"(-?[\d.]+)px\s+(-?[\d.]+)px\s+(-?[\d.]+)px(?:\s+(-?[\d.]+)px)?", box_shadow):
        offset, blur = float(match.group(1)), float(match.group(3))
        spread = float(match.group(4) or 0)
        if abs(offset) >= 1 or blur >= 1:
            layers.append((offset, blur, spread))
    return layers


def extremes(values, key):
    """Phase-independent way to compare two runs: the animation's own bounds.
    Comparing raw samples taken at different moments is meaningless — the same
    fixed-strength animation yields different values purely because of phase."""
    picked = sorted(values, key=key)
    return key(picked[0]), key(picked[-1])


def widest(box_shadow):
    """How far the spill reaches: the widest layer's offset plus blur."""
    return max((abs(offset) + blur for offset, blur, _ in spread_of(box_shadow)), default=0)


async def seed(page, ids, seen=False):
    await page.evaluate(
        """({ ids, seen, seenKey }) => {
          localStorage.setItem("qb-basket", JSON.stringify(ids));
          if (seen) localStorage.setItem(seenKey, "1"); else localStorage.removeItem(seenKey);
        }""",
        {"ids": ids, "seen": seen, "seenKey": SEEN_PREF},
    )


async def open_library(page, base):
    await page.goto(f"{base}/library", wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)
    await page.evaluate("""() => { for (const id of ["welcomeDialog","pageDialog","viewerDialog"]) {
        const d = document.getElementById(id); if (d && d.open) d.close(); } }""")
    await page.wait_for_timeout(400)
    if not await page.locator("#basketHandle").count():
        await page.goto(f"{base}/", wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8803")
    args = parser.parse_args()
    shots = pathlib.Path(__file__).resolve().parents[1] / "tmp" / "basketglow"
    shots.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as play:
        browser = await play.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
        await open_library(page, args.base)
        if not await page.locator("#basketHandle").count():
            print("题库页上找不到试题篮把手，无法验收")
            await browser.close()
            return 1

        library = await (await page.request.get(f"{args.base}/api/library?limit=500")).json()
        ids = [item["id"] for item in library.get("items", [])][:20]
        if not ids:
            print("题库里没有可用的题目，无法验收")
            await browser.close()
            return 1
        print(f"题库可取 {len(ids)} 道题，viewport {WIDTH}x{HEIGHT}")

        # 1) 空篮：不亮
        await seed(page, [])
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        state = await page.evaluate(READ_AFTER)
        check(state["animationName"] == "none", f"空篮时不亮（animationName={state['animationName']}）")
        notes.append(f"空篮：数字 {state['text']}，类 {state['bodyClasses']}")

        # 2) 有题、未展开、未看过：亮，而且真的在动
        await seed(page, ids[:3])
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        cycle = await sample_cycle(page)
        names = {read["animationName"] for read in cycle}
        check(names == {"basket-handle-fluoresce"}, f"有题未打开未看过时亮（{names}）")
        fills = {read["backgroundColor"] for read in cycle}
        shadows = {read["boxShadow"] for read in cycle}
        check(len(fills) > 1, f"底色在变化（{len(fills)} 个不同取值）")
        check(len(shadows) > 1, f"溢光在变化（{len(shadows)} 个不同取值）")

        # 3) 必须真的亮：拿底色和白比，否掉「2/255 那种等于没有」
        brightness = [distance_from_white(value) for value in fills]
        trough, peak = min(brightness), max(brightness)
        notes.append(f"底色距白：波谷 {trough:.0f}/255，波峰 {peak:.0f}/255")
        check(trough >= 12, f"波谷就明显离白足够远（{trough:.0f} ≥ 12）")
        check(peak >= 40, f"波峰明显亮起来（{peak:.0f} ≥ 40）")

        # 4) 可读性：越亮越清楚，两端都过 AA。底色要压在白底上算，透明才算白。
        text_rgb = parse_rgb(cycle[0]["color"])
        trough_contrast = contrast(text_rgb, over_white(parse_rgba(
            min(fills, key=lambda v: distance_from_white(v)))))
        peak_contrast = contrast(text_rgb, over_white(parse_rgba(
            max(fills, key=lambda v: distance_from_white(v)))))
        notes.append(f"数字 {cycle[0]['text']} 颜色 {cycle[0]['color']}")
        notes.append(f"数字对比度：波谷 {trough_contrast:.2f}:1，波峰 {peak_contrast:.2f}:1")
        check(trough_contrast >= 4.5, f"波谷数字仍可读（{trough_contrast:.2f} ≥ 4.5）")
        check(peak_contrast >= 4.5, f"波峰数字仍可读（{peak_contrast:.2f} ≥ 4.5）")

        # 5) 强度固定，不跟题数走。两边都得真在动，否则「都没动所以一样」是假通过。
        await seed(page, ids[:1])
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        one = await sample_cycle(page, steps=5)
        await seed(page, ids[:20])
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        twenty = await sample_cycle(page, steps=5)
        one_fills = {read["backgroundColor"] for read in one}
        twenty_fills = {read["backgroundColor"] for read in twenty}
        check(len(one_fills) > 1 and len(twenty_fills) > 1,
              f"1 题和 20 题都真的在呼吸（{len(one_fills)} / {len(twenty_fills)} 组取值）")
        one_bounds = extremes(one_fills, distance_from_white)
        twenty_bounds = extremes(twenty_fills, distance_from_white)
        notes.append(f"底色距白 1 题 {one_bounds} / 20 题 {twenty_bounds}")
        check(one_bounds == twenty_bounds,
              f"1 题和 20 题的荧光强弱完全一样（{one_bounds} vs {twenty_bounds}）")
        one_shadows = {read["boxShadow"] for read in one}
        twenty_shadows = {read["boxShadow"] for read in twenty}
        one_shadow_bound = extremes(one_shadows, widest)
        twenty_shadow_bound = extremes(twenty_shadows, widest)
        notes.append(f"溢光最宽 1 题 {one_shadow_bound} / 20 题 {twenty_shadow_bound}")
        # 模糊半径是连续变化的，采样很难正好落在极值上，所以留一点容差。
        check(all(abs(a - b) <= 2 for a, b in zip(one_shadow_bound, twenty_shadow_bound)),
              "1 题和 20 题的溢光强弱一样")

        # 9) 几何：贴边 + 溢光不被视口切。取溢光最宽的那一帧（波峰）来量。
        peak_shadow = max((read["boxShadow"] for read in twenty), key=widest)
        rect = await page.locator("#basketHandle").bounding_box()
        check(WIDTH - (rect["x"] + rect["width"]) < 1,
              f"把手贴在视口右缘（间距 {WIDTH - (rect['x'] + rect['width']):.2f}px）")
        layers = spread_of(peak_shadow)
        notes.append(f"波峰溢光层：{layers}")
        check(bool(layers), f"解析出 {len(layers)} 层溢光")
        for offset, blur, spread in layers:
            check(abs(offset) >= blur / 2,
                  f"溢光层 {offset:g}px/{blur:g}px 的右边界落在视口内，不露硬边")

        # 10) 截图：波谷和波峰，带把手周围的留白
        box = await page.locator("#basketHandle").bounding_box()
        clip = {"x": box["x"] - 58, "y": box["y"] - 16,
                "width": 58 + box["width"] + 10, "height": box["height"] + 32}
        await page.screenshot(path=str(shots / "01-trough.png"), clip=clip)
        for _ in range(int(PERIOD_MS / 2 / 100)):
            await page.wait_for_timeout(100)
        await page.screenshot(path=str(shots / "02-peak.png"), clip=clip)
        wide = {"x": box["x"] - 120, "y": box["y"] - 40, "width": 140, "height": box["height"] + 80}
        await page.screenshot(path=str(shots / "03-context.png"), clip=wide)
        notes.append(f"截图：{shots}")

        # 6) 展开篮子：立刻停
        await seed(page, ids[:3])
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        await page.click("#basketHandle")
        await page.wait_for_timeout(500)
        state = await page.evaluate(READ_AFTER)
        check(state["animationName"] == "none", "展开篮子后立刻停")
        check("library-basket-seen" in state["bodyClasses"], "记住「已经看过」")

        # 7) 收起 + 刷新：仍然不亮
        await page.click("#basketHandle")
        await page.wait_for_timeout(400)
        state = await page.evaluate(READ_AFTER)
        check(state["animationName"] == "none", "收起之后不再亮")
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        state = await page.evaluate(READ_AFTER)
        check(state["animationName"] == "none", "刷新之后仍然不亮（标志真的落盘了）")

        # 8) 减少动态效果
        await page.emulate_media(reduced_motion="reduce")
        await seed(page, ids[:3], seen=False)
        await page.reload(wait_until="domcontentloaded")
        await page.wait_for_timeout(1400)
        state = await page.evaluate(READ_AFTER)
        check(state["animationName"] == "none", "系统开了「减少动态效果」时不亮")
        await page.emulate_media(reduced_motion="no-preference")
        await browser.close()

    print()
    for note in notes:
        print("  " + note)
    print()
    if failures:
        print(f"FAIL - {len(failures)} 个问题")
        for message in failures:
            print("   - " + message)
        return 1
    print("PASS - 0 个问题")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
