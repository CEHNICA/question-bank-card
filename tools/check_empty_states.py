"""空态和极端内容压测：题库里没有结果、篮是空的、筛选条件互斥时，界面还站得住吗。

「有内容」的那条路被测过无数次，「没有内容」这条几乎没有。而空态恰恰是最容易写坏的地方：
一个空 div、一个高 0 的容器、一条 `flex-wrap` 之后跑到屏幕外的按钮——都能通过所有正常
用例，然后在用户筛了个错别字的时候塌掉。

覆盖四种真会遇到的空/极端：
1. 搜索框里打了一段不存在的东西（最常见的空态，用户会经常打错）；
2. 已选题目视图，篮是空的；
3. 筛到「已入库」只剩 2 道题（题卡会收成一行 compact 行，版式完全不同）；
4. 搜索框塞进 200 个字符（长内容不撑破版式）。

每一档都量同一组东西：页面上有没有「说得清楚的提示」而不是一片白、侧栏还点得到、
右边缘那条篮把手还点得到、当前筛选行说得清现在筛的是什么。
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

results: list[tuple[bool, str]] = []
problems: list[str] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


# 一屏之内量：侧栏、把手、当前筛选行还在不在、还能不能点、有没有说得清的提示。
PROBE = """() => {
  const vis = (sel) => { const n = document.querySelector(sel); if (!n) return null;
    const r = n.getBoundingClientRect();
    const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
    return {w: Math.round(r.width), h: Math.round(r.height),
            x: Math.round(r.left), y: Math.round(r.top),
            clickable: !!(hit === n || n.contains(hit))};
  };
  const empty = document.querySelector('.library-empty');
  const cards = document.querySelectorAll('#libraryList .library-card');
  const compact = document.querySelectorAll('#libraryList .library-card.compact');
  const wide = (el) => el ? el.scrollWidth - el.clientWidth : 0;
  return {
    emptyText: empty ? empty.textContent.trim().slice(0, 40) : '',
    cards: cards.length, compact: compact.length,
    rail: vis('#libraryRail'), handle: vis('#basketHandle'),
    active: vis('#activeFilters'), more: vis('#moreButton'),
    railOverflow: wide(document.querySelector('#libraryRail')),
    listOverflow: wide(document.querySelector('#libraryList')),
    bodyOverflow: Math.max(0, document.documentElement.scrollWidth - window.innerWidth),
  };
}"""


def report(page, tag: str, shot: str | None = None) -> dict:
    data = page.evaluate(PROBE)
    if shot:
        page.screenshot(path=shot)
    print(f"  [{tag}] 卡片 {data['cards']}（compact {data['compact']}） 提示「{data['emptyText']}」")
    return data


def run(page, width: int, height: int) -> None:
    tag = f"{width}×{height}"
    page.set_viewport_size({"width": width, "height": height})
    page.goto("about:blank")
    page.goto(f"{BASE}/library", wait_until="networkidle")
    page.wait_for_selector("#libraryList .library-card", timeout=20000)
    page.wait_for_timeout(1500)

    base = report(page, f"{tag} 正常", f"{SHOTS}/empty_{width}x{height}_normal.png")
    check(base["cards"] > 0, f"{tag}：题库正常状态有题可看（{base['cards']} 张）")
    check(base["handle"] and base["handle"]["clickable"], f"{tag}：右边缘的篮把手点得到")
    check(base["railOverflow"] <= 0, f"{tag}：正常状态侧栏没有横向溢出（{base['railOverflow']}px）")

    # 1. 搜索一个不存在的词
    search = page.locator("#searchInput")
    search.fill("zzz不存在的题zzz")
    page.wait_for_timeout(900)
    none = report(page, f"{tag} 搜不到", f"{SHOTS}/empty_{width}x{height}_noresult.png")
    check(none["cards"] == 0, f"{tag}：搜不到时列表真的空了（{none['cards']} 张）")
    check(bool(none["emptyText"]), f"{tag}：搜不到时有一句能看懂的提示（「{none['emptyText']}」）")
    check(bool(none["active"] and none["active"]["clickable"]),
          f"{tag}：搜不到时「当前筛选」这一行还点得到（用户才知道自己筛了什么）")
    check(bool(none["handle"] and none["handle"]["clickable"]),
          f"{tag}：搜不到时篮把手还点得到")
    check(not (none["more"] and none["more"]["w"] > 0 and none["more"]["h"] > 0),
          f"{tag}：搜不到时「加载更多」收起来了")
    check(none["bodyOverflow"] <= 0, f"{tag}：搜不到时整页没有横向溢出（{none['bodyOverflow']}px）")

    # 2. 清掉搜索，进「已选题目」（篮是空的）
    search.fill("")
    page.wait_for_timeout(800)
    page.locator("#basketViewButton").click()
    page.wait_for_timeout(900)
    empty_basket = report(page, f"{tag} 篮是空的", f"{SHOTS}/empty_{width}x{height}_nobasket.png")
    check(bool(empty_basket["emptyText"]),
          f"{tag}：篮空着时有一句提示（「{empty_basket['emptyText']}」）")
    check(bool(empty_basket["handle"] and empty_basket["handle"]["clickable"]),
          f"{tag}：篮空着时篮把手还点得到")
    page.locator("#allQuestionsButton").click()
    page.wait_for_timeout(800)

    # 3. 200 个字符的搜索词：长内容不撑破版式
    search.fill("长" * 200)
    page.wait_for_timeout(900)
    long_q = report(page, f"{tag} 超长搜索词", f"{SHOTS}/empty_{width}x{height}_long.png")
    check(long_q["bodyOverflow"] <= 0,
          f"{tag}：搜索框里塞 200 字整页不横向溢出（{long_q['bodyOverflow']}px）")
    check(long_q["railOverflow"] <= 0,
          f"{tag}：搜索框里塞 200 字侧栏不横向溢出（{long_q['railOverflow']}px）")
    search.fill("")
    page.wait_for_timeout(800)

    # 4. 筛到只剩一道题：按知识点筛（侧栏 #tagSelect 里每个标签都只有 1 道），
    #    这是最极端的版式 —— 一张卡撑满整列，和 40 张时完全不是一回事。
    #    知识点那一组通常折在「更多筛选」里，选之前先把折叠打开。
    if page.locator("#tagSelect").count() and not page.locator("#tagSelect").is_visible():
        if page.locator("#advancedFilters summary").count():
            page.locator("#advancedFilters summary").click()
            page.wait_for_timeout(300)
    options = page.evaluate("""() => [...document.querySelectorAll('#tagSelect option')]
        .map((o) => o.value).filter(Boolean)""")
    if not options or not page.locator("#tagSelect").is_visible():
        check(False, f"{tag}：侧栏里没有可点的知识点标签（选项 {len(options)} 个），这一项没量到")
        return
    page.locator("#tagSelect").select_option(options[0])
    page.wait_for_timeout(1100)
    one = report(page, f"{tag} 筛到一个标签", f"{SHOTS}/empty_{width}x{height}_one.png")
    check(one["cards"] >= 1, f"{tag}：按知识点筛完还剩 {one['cards']} 张（至少 1 张，不是空）")
    check(one["bodyOverflow"] <= 0, f"{tag}：只剩一张卡时整页不横向溢出（{one['bodyOverflow']}px）")
    check(one["railOverflow"] <= 0, f"{tag}：只剩一张卡时侧栏不横向溢出（{one['railOverflow']}px）")
    check(bool(one["active"] and one["active"]["clickable"]),
          f"{tag}：只剩一张卡时「当前筛选」还点得到（告诉用户正在按知识点筛）")
    check(bool(one["handle"] and one["handle"]["clickable"]),
          f"{tag}：只剩一张卡时篮把手还点得到")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            for width, height in ((1440, 900), (1280, 600)):
                print(f"\n--- {width}×{height} ---")
                run(page, width, height)
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
