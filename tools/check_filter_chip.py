"""筛选条件那一行：标签名再长，「×」也得跟名字待在同一行。

默认目录里就有二十来个字的知识点名（「分类加法计数原理与分步乘法计数原理」），
筛选栏只有 212px。以前「标签 ×」是一整段文字，断行会断在词中间，
× 被挤到第二行单独待着，看着像界面坏了。

量的是：这一行到底占几行高、名字和 × 的竖直位置有没有对齐、
以及完整的名字有没有留在 title 里（截断了也得让人看得到全名）。
"""

from __future__ import annotations

import sys
import urllib.parse

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

LONG_TAG = "分类加法计数原理与分步乘法计数原理"
SHORT_TAG = "随机抽样"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def measure(page, tag: str) -> dict:
    page.goto("about:blank")
    page.goto(f"{BASE}/library?tag={urllib.parse.quote(tag)}", wait_until="networkidle")
    page.wait_for_selector("#activeFilters .library-filter-chip", timeout=15000)
    page.wait_for_timeout(700)
    return page.evaluate("""() => {
      const chip = document.querySelector('#activeFilters .library-filter-chip');
      const label = chip.querySelector('.label');
      const drop = chip.querySelector('.drop');
      const rail = document.querySelector('.library-rail');
      const box = chip.getBoundingClientRect(), rbox = rail.getBoundingClientRect();
      const style = getComputedStyle(label);
      const padding = parseFloat(getComputedStyle(chip).paddingTop) + parseFloat(getComputedStyle(chip).paddingBottom);
      return {
        // 内容高（去掉内边距）才是「几行」。
        contentHeight: box.height - padding,
        lineHeight: parseFloat(style.lineHeight) || style.fontSize * 1.4,
        labelWidth: label.getBoundingClientRect().width,
        dropTop: drop.getBoundingClientRect().top, labelTop: label.getBoundingClientRect().top,
        labelRight: label.getBoundingClientRect().right, dropLeft: drop.getBoundingClientRect().left,
        truncated: label.scrollWidth > label.clientWidth + 1,
        title: chip.title,
        railRight: rbox.right,
        chipRight: box.right,
      };
    }""")


def run(page) -> None:
    # 先钉住结构：名字和 × 必须是两段。老写法是一整段文字，断行会断在词中间。
    page.goto("about:blank")
    page.goto(f"{BASE}/library?tag={urllib.parse.quote(SHORT_TAG)}", wait_until="networkidle")
    page.wait_for_selector("#activeFilters .library-filter-chip", timeout=15000)
    shape = page.evaluate("""() => {
      const chip = document.querySelector('#activeFilters .library-filter-chip');
      return {labels: chip.querySelectorAll('.label').length, drops: chip.querySelectorAll('.drop').length,
              text: chip.textContent};
    }""")
    check(shape["labels"] == 1 and shape["drops"] == 1,
          f"名字和 × 是两段（label {shape['labels']} 段、× {shape['drops']} 段）")
    check(shape["text"] == f"知识点：{SHORT_TAG}×", f"两段拼起来还是原来那句话：{shape['text']}")

    short = measure(page, SHORT_TAG)
    check(abs(short["dropTop"] - short["labelTop"]) < 2, "短标签：名字和 × 在同一行")

    long = measure(page, LONG_TAG)
    lines = long["contentHeight"] / long["lineHeight"]
    check(lines < 1.4, f"二十字的标签也只占一行高（内容 {long['contentHeight']:.0f}px，约 {lines:.1f} 行）")
    check(abs(long["dropTop"] - long["labelTop"]) < 2, "长标签：× 仍然跟名字同一行，没被挤到下面")
    check(long["dropLeft"] >= long["labelRight"] - 1, "× 在名字右边，没有重叠")
    check(long["truncated"], "名字被截断了（没有硬撑成两行）")
    check(long["title"] == f"知识点：{LONG_TAG}", f"完整名字留在 title 里：{long['title']}")
    # 两边都是视口坐标，可以直接比。
    check(long["chipRight"] <= long["railRight"] + 1,
          f"这一行没有超出筛选栏（到 {long['chipRight']:.0f}，栏右缘 {long['railRight']:.0f}）")
    page.screenshot(path=f"{SHOTS}/filter_chip_long.png")

    # 点 × 还得管用：它现在是两段文字拼的按钮，别把可点区域做没了。
    page.locator("#activeFilters .library-filter-chip .drop").click()
    page.wait_for_timeout(1200)
    check(page.locator("#activeFilters .library-filter-chip").count() == 0, "点 × 能取消这个筛选")


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
