"""审核页题卡：配图判断面板滚动时不能被常驻条压住。

1.13.3：这块面板没有定位也没有层级。卡片自己底部那条吸底操作条（白底、z-index 4）
会从下面滑上来盖住它 —— 19 题里有 8 题的面板和它相交，最狠一次 24 个探测点一个都
点不到；顶栏和筛选条（都是吸顶）也会把往上滚的面板埋进去。修法是让面板吸在常驻
工具条下面。

这个脚本整页每 200px 扫一遍，对每个露出视野的面板铺 24 点网格跑命中检测，断言没有
任何一个点落在面板外的元素上。「DOM 上写着没被盖」不算数 —— elementFromPoint 才是
眼睛。

用法：先起一个开发服务器（QB_DATABASE / QB_DATA_ROOT 指到一份副本），再
.\\backend\\.venv\\Scripts\\python.exe tools\\check_figure_panel_overlay.py --base http://127.0.0.1:8803
"""

import argparse
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# The card is two columns above 1100 px and the sticky chrome only stacks at the top;
# the in-app Browser panel is 901 px, so this has to go through Playwright.
VIEWPORT = {"width": 1400, "height": 950}
STEP = 200

# 6 x 4 probe grid, inset 4 px on every side: a point on the very edge can land just
# outside the panel and report its own parent as the "coverer", which is a rounding
# artifact, not an occlusion.
SWEEP = """() => {
  const label = (e) => e ? e.tagName + '.' + (typeof e.className === 'string' ? e.className : '') : 'null';
  const out = [];
  document.querySelectorAll('.card .figure-review').forEach((panel) => {
    const r = panel.getBoundingClientRect();
    if (r.height < 8 || r.bottom < 0 || r.top > innerHeight) return;
    const card = panel.closest('.card');
    const points = [];
    for (let i = 0; i <= 5; i++) {
      for (let j = 0; j <= 3; j++) {
        points.push([Math.round(r.left + 4 + (r.width - 8) * i / 5),
                     Math.round(r.top + 4 + (r.height - 8) * j / 3)]);
      }
    }
    // Only another part of the SAME card covering the panel is a bug: that is the
    // card's own sticky action bar sliding over its own yellow panel. The top bar,
    // the sticky filter bar and the back-to-top button are page-level chrome, and
    // content scrolling under them is how sticky chrome is supposed to behave.
    const blocked = {};
    const chrome = {};
    let inside = 0, offscreen = 0;
    for (const [x, y] of points) {
      if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) { offscreen++; continue; }
      const el = document.elementFromPoint(x, y);
      if (!el) { offscreen++; continue; }
      if (panel.contains(el)) { inside++; continue; }
      const key = label(el);
      const bucket = card.contains(el) ? blocked : chrome;
      bucket[key] = (bucket[key] || 0) + 1;
    }
    out.push({
      question: (card.querySelector('.qnum') || {}).textContent || '',
      status: (panel.className.match(/figure-review-([a-z_]+)/) || [])[1] || '',
      top: Math.round(r.top), bottom: Math.round(r.bottom),
      inside, offscreen, blocked, chrome,
    });
  });
  return out;
}"""


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8803")
    parser.add_argument("--viewport", default="1400x950")
    args = parser.parse_args()
    width, height = (int(v) for v in args.viewport.split("x"))

    failures, scanned, worst = 0, 0, []
    chrome_hits = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport={"width": width, "height": height})
        try:
            await page.goto(f"{args.base}/", wait_until="domcontentloaded")
            await page.wait_for_selector(".paper-link", timeout=30000)
            await page.wait_for_timeout(2500)
            titles = await page.evaluate(
                "() => [...document.querySelectorAll('.paper-link')].map((b) => b.title)")
            print(f"试卷 {len(titles)} 份，viewport {width}x{height}")

            for index, title in enumerate(titles):
                await page.evaluate("(i) => document.querySelectorAll('.paper-link')[i].click()", index)
                await page.wait_for_selector(".card", timeout=30000)
                await page.wait_for_timeout(2500)
                for dialog in await page.evaluate(
                    "() => [...document.querySelectorAll('dialog[open]')].map((d) => d.id)"
                ):
                    await page.evaluate("(id) => document.getElementById(id)?.close()", dialog)

                has_panel = await page.evaluate(
                    "() => document.querySelectorAll('.card .figure-review').length")
                if not has_panel:
                    print(f"  {title[:30]}: 没有配图面板，跳过")
                    continue

                # Cards are content-visibility:auto, so walk the whole page once to
                # get everything laid out before sampling.
                total = await page.evaluate("() => document.body.scrollHeight")
                for _ in range(0, total, 500):
                    await page.mouse.wheel(0, 500)
                    await page.wait_for_timeout(90)
                await page.wait_for_timeout(400)

                print(f"  {title[:30]}: {has_panel} 个面板，页高 {total}")
                for y in range(0, total, STEP):
                    await page.evaluate("(v) => window.scrollTo(0, v)", y)
                    await page.wait_for_timeout(120)
                    for result in await page.evaluate(SWEEP):
                        scanned += 1
                        for key, hits in result["chrome"].items():
                            chrome_hits[key] = chrome_hits.get(key, 0) + hits
                        if result["blocked"]:
                            failures += 1
                            worst.append((title, y, result))
                await page.evaluate("() => window.scrollTo(0, 0)")
        finally:
            await browser.close()
    print(f"采样 {scanned} 次")
    for title, y, result in worst[:12]:
        print(f"  FAIL {title[:24]} scrollY={y} {result['question']} "
              f"inside={result['inside']} blocked={json.dumps(result['blocked'], ensure_ascii=False)}")
    if len(worst) > 12:
        print(f"  ... 另有 {len(worst) - 12} 次")
    if chrome_hits:
        print("  页面级吸顶件压到的点（正常滚动，不算失败）："
              + json.dumps(chrome_hits, ensure_ascii=False))
    print("FAIL" if failures else "PASS", f"- {failures} 次被同一张卡片内的元素盖住")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
