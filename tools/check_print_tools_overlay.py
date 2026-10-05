"""组卷预览：「调整」菜单不能被后面的 A4 纸盖住、也不能点不到。

背景（1.13.3）：预览为了把 210mm 宽的纸缩到窗口宽度，给每张 .exam-page 套了
transform: scale()（exam-layout.js 的 scale()）。transform 会开一个独立的层叠上下文，
所以 .print-question-tools 自己写的 z-index 出不来这张纸。菜单又只往下展开，题排在
纸的下部时菜单伸出纸外，落到后面那张纸上被整块盖住。实测同一场景下去掉缩放就能点到
（49/49 个探测点），带着缩放一个都点不到。

这个脚本跑两轮：
  1. 真实排版 —— 30 道题排成多页，逐个展开菜单，铺 49 点网格做命中检测；
  2. 受控场景 —— 把一道题硬顶到纸底之外（用户那份组卷就是这种排法），
     再展开菜单，确认补上的层级让菜单仍然完全可点。

用法：先起一个开发服务器（QB_DATABASE / QB_DATA_ROOT 指到有题的库），
再 .\\backend\\.venv\\Scripts\\python.exe tools\\check_print_tools_overlay.py --base http://127.0.0.1:8803
"""

import argparse
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# The preview scales an A4 sheet to the window, so the sheet is only ~794 px tall
# at this width. A fixed 1500x1000 viewport keeps the run reproducible.
VIEWPORT = {"width": 1500, "height": 1000}
QUESTIONS = 30

ADD_TO_BASKET = "\u52a0\u5165\u8bd5\u9898\u7bee"  # 加入试题篮

# One menu row: where it is, whether it escaped the sheet, and what covers it.
PROBE = """() => {
  const label = (e) => e ? e.tagName + '.' + (typeof e.className === 'string' ? e.className : '') : 'null';
  const box = (el) => { const r = el.getBoundingClientRect();
    return { t: Math.round(r.top), b: Math.round(r.bottom), l: Math.round(r.left), r: Math.round(r.right) }; };
  const tools = document.querySelector('.print-question-tools[open]');
  if (!tools) return { err: 'no open menu' };
  const actions = tools.querySelector('.print-question-actions');
  const page = tools.closest('.exam-page');
  if (!actions || !page) return { err: 'menu not rendered' };
  const pr = page.getBoundingClientRect();
  const ar = actions.getBoundingClientRect();
  const points = [];
  for (let i = 0; i <= 6; i++) {
    for (let j = 0; j <= 6; j++) {
      points.push([Math.round(ar.left + 4 + (ar.width - 8) * i / 6),
                   Math.round(ar.top + 4 + (ar.height - 8) * j / 6)]);
    }
  }
  const covered = {};
  let inside = 0, offscreen = 0;
  for (const [x, y] of points) {
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) { offscreen++; continue; }
    const el = document.elementFromPoint(x, y);
    if (!el) { offscreen++; continue; }
    if (actions.contains(el)) { inside++; continue; }
    const key = label(el);
    covered[key] = (covered[key] || 0) + 1;
  }
  return {
    sheet: page.getAttribute('aria-label'),
    sheetLifted: page.classList.contains('tools-open'),
    flippedUp: tools.classList.contains('opens-up'),
    belowSheet: Math.round(ar.bottom - pr.bottom),
    rightOfSheet: Math.round(ar.right - pr.right),
    inside, offscreen, total: points.length, covered,
  };
}"""

OPEN_MENU = """(index) => {
  document.querySelectorAll('.print-question-tools[open]').forEach((d) => { d.open = false; });
  const all = [...document.querySelectorAll('.print-question-tools')];
  const tools = all[index];
  if (!tools) return false;
  tools.scrollIntoView({ block: 'center' });
  tools.open = true;
  return true;
}"""


async def fill_basket(page, base):
    await page.goto(f"{base}/library", wait_until="domcontentloaded")
    await page.wait_for_selector(".library-card", timeout=30000)
    await page.wait_for_timeout(2500)
    for dialog in await page.evaluate(
        "() => [...document.querySelectorAll('dialog[open]')].map((d) => d.id)"
    ):
        await page.evaluate("(id) => document.getElementById(id)?.close()", dialog)
    added = await page.evaluate(
        """(label) => {
            const buttons = [...document.querySelectorAll('.library-card button')]
              .filter((b) => (b.textContent || '').trim() === label);
            buttons.slice(0, %d).forEach((b) => b.click());
            return buttons.length;
        }"""
        % QUESTIONS,
        ADD_TO_BASKET,
    )
    await page.wait_for_timeout(1200)
    if not added:
        raise SystemExit("题库里没有可加入试题篮的题，先导入资料再跑这个脚本。")
    hidden = await page.evaluate("() => document.getElementById('basketButton')?.hidden")
    if hidden:
        raise SystemExit("加入试题篮后「组卷预览」按钮仍然是隐藏的，加题没有生效。")
    await page.evaluate("() => document.getElementById('basketButton')?.click()")
    await page.wait_for_timeout(6000)
    return added


async def check_menus(page):
    """Every menu must be fully clickable, wherever the question sits on the sheet."""
    count = await page.evaluate("() => document.querySelectorAll('.print-question-tools').length")
    sheets = await page.evaluate("() => document.querySelectorAll('.exam-page').length")
    print(f"排版结果：{count} 道题 / {sheets} 张 A4")
    failures, flipped, escaped = [], 0, 0
    for index in range(count):
        if not await page.evaluate(OPEN_MENU, index):
            continue
        await page.wait_for_timeout(220)
        result = await page.evaluate(PROBE)
        if result.get("err"):
            print(f"  SKIP 第 {index + 1} 个：{result['err']}")
            continue
        if result["flippedUp"]:
            flipped += 1
        if result["belowSheet"] > 0:
            escaped += 1
        # A probe point that lands on another element is a real occlusion, not a
        # boundary rounding artifact: the grid is inset by 4px on every side.
        if result["covered"]:
            failures.append((index + 1, result))
    print(f"向上翻的：{flipped} 个；伸到纸外的：{escaped} 个")
    return count, failures


async def check_pushed_past_sheet(page):
    """The regression itself: a question pushed past the bottom of its sheet.

    Real baskets hit this whenever a long question lands low on the page. The
    menu escapes onto the next sheet; before the fix the whole menu was dead.
    """
    info = await page.evaluate(
        """() => {
            document.querySelectorAll('.print-question-tools[open]').forEach((d) => { d.open = false; });
            const sheet = document.querySelector('.exam-page');
            const questions = sheet.querySelectorAll('.print-question');
            const question = questions[questions.length - 1];
            question.style.marginTop = '820px';
            const tools = question.querySelector('.print-question-tools');
            tools.open = true;
            tools.scrollIntoView({ block: 'center' });
            return { sheet: sheet.getAttribute('aria-label'), marginTop: question.style.marginTop };
        }"""
    )
    await page.wait_for_timeout(400)
    result = await page.evaluate(PROBE)
    print(f"受控场景 {info['sheet']}：{info}")
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8803")
    args = parser.parse_args()

    failures = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME)
        page = await browser.new_page(viewport=VIEWPORT)
        try:
            added = await fill_basket(page, args.base)
            print(f"已加入试题篮 {added} 道题")

            count, bad = await check_menus(page)
            for number, result in bad:
                failures += 1
                print(
                    f"  FAIL 第 {number} 个菜单被盖：inside={result['inside']}/{result['total']} "
                    f"covered={json.dumps(result['covered'], ensure_ascii=False)}"
                )
            if not bad and count:
                print(f"  OK {count} 个菜单全部可点")

            pushed = await check_pushed_past_sheet(page)
            if pushed.get("err"):
                print(f"  SKIP 受控场景：{pushed['err']}")
            elif pushed["belowSheet"] <= 0:
                print(f"  SKIP 受控场景：菜单没能伸出纸（belowSheet={pushed['belowSheet']}），"
                      "这一轮没有真正复现要防的情形")
            elif pushed["covered"]:
                failures += 1
                print(
                    f"  FAIL 伸出纸 {pushed['belowSheet']}px 仍被盖："
                    f"inside={pushed['inside']}/{pushed['total']} "
                    f"covered={json.dumps(pushed['covered'], ensure_ascii=False)}"
                )
            else:
                print(f"  OK 伸出纸 {pushed['belowSheet']}px 仍完全可点"
                      f"（{pushed['inside']}/{pushed['total']} 个点，向上翻={pushed['flippedUp']}，"
                      f"纸已抬起={pushed['sheetLifted']}）")
        finally:
            await browser.close()

    print("FAIL" if failures else "PASS", f"- {failures} 个问题")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
