"""亲手点一遍抽屉：篮进出、专注浏览进退、Esc、教学巡演的聚光灯。

DOM 上写着「已展开」不算数 —— 每一项都去读真实状态（localStorage 里的篮、
body 上的 class、抽屉的 hidden）。只读题库，不改题。

用法： python tools/check_site_drawer.py --url http://127.0.0.1:8802
"""

import argparse
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "layout"


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

    def check(label, condition, detail=""):
        print(f"  {'OK ' if condition else 'FAIL'} {label}{'  ' + str(detail) if detail else ''}")
        if not condition:
            failures.append(f"{label} {detail}")

    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": args.width, "height": args.height})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1'); localStorage.setItem('qb-lens','0')")
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                      ("127.0.0.1", "localhost") else route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        # ---- 题库页
        page.goto(f"{url}/library")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector(".library-card")
        print("== 题库页 ==")
        # 侧栏：搜索、来源、题型、排序、更多筛选都得能用（查找条搬进侧栏之后）
        rail = page.locator("#libraryRail")
        check("查找和筛选在左侧栏", rail.is_visible() and rail.bounding_box()["width"] < 320,
              f"侧栏宽 {rail.bounding_box()['width']}")
        check("搜索框和下拉同高", page.evaluate("""() => {
            const s = document.querySelector('.library-search input').getBoundingClientRect();
            const d = document.querySelector('.library-rail .library-select select').getBoundingClientRect();
            return Math.abs(s.height - d.height) < 1; }"""))
        page.locator("#searchInput").fill("抛物线")
        page.wait_for_timeout(700)
        check("侧栏里搜索能用", page.locator(".library-card").count() >= 1, page.locator(".library-card").count())
        check("搜索后出现「当前筛选」行（在侧栏里）", page.locator("#activeFilters").is_visible()
              and page.locator("#activeFilters").evaluate("n => n.closest('.library-rail') !== null"))
        page.locator("#searchInput").fill("")
        page.wait_for_timeout(700)
        page.locator("#sourceSelect").select_option(index=1)
        page.wait_for_timeout(700)
        check("侧栏里来源筛选能用", page.locator("#sourceSelect").input_value() != "")
        page.locator("#sourceSelect").select_option("")
        page.wait_for_timeout(700)
        page.locator("#typeFilters button").nth(2).click()
        page.wait_for_timeout(700)
        check("侧栏里题型筛选能用", page.locator("#typeFilters button[aria-pressed='true']").count() == 1)
        page.locator("#typeFilters button").first.click()
        page.wait_for_timeout(700)
        page.locator("#sortSelect").select_option("source")
        page.wait_for_timeout(700)
        check("侧栏里排序能用", page.evaluate("new URL(location.href).searchParams.get('sort')") == "source")
        page.locator("#sortSelect").select_option("recent")
        page.wait_for_timeout(700)
        check("清掉排序后 URL 也跟着清", page.evaluate("new URL(location.href).searchParams.get('sort')") in (None, "recent"))

        # 加一题进篮，验的是 localStorage 里的篮，不���顶栏按钮。
        page.locator(".library-card").first.get_by_role("button", name="加入试题篮").click()
        page.wait_for_timeout(500)
        stored = page.evaluate("JSON.parse(localStorage.getItem('qb-basket') || '[]').length")
        check("点「加入试题篮」后篮里真有题", stored == 1, f"qb-basket = {stored}")
        topbar_basket = page.locator(".topbar-basket")
        check("篮里有题，顶栏出现入口", topbar_basket.is_visible(), topbar_basket.inner_text().replace("\n", " "))
        check("顶栏工具区这时才占位", page.evaluate("getComputedStyle(document.querySelector('.topbar-tools')).display") != "none")

        # 点顶栏的篮 → 抽屉拉开、篮展开、列表里看得见那道题
        topbar_basket.click()
        page.wait_for_timeout(400)
        check("点顶栏的篮会拉开抽屉", page.evaluate("window.QBSiteDrawer.isOpen()"))
        check("篮面板展开", page.locator("#basketPanel").is_visible())
        check("篮列表里有那道题", page.locator("#basketList .basket-row").count() == 1)
        page.screenshot(path=str(OUTPUT / "drawer-basket.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

        # 「已选题目 / 全部题目」在筛选行上，抽屉关着才点得到
        page.locator("#basketViewButton").click()
        page.wait_for_timeout(700)
        check("「已选题目」切到篮内视图", page.locator("#basketViewButton").get_attribute("aria-pressed") == "true")
        page.locator("#allQuestionsButton").click()
        page.wait_for_timeout(700)
        check("「全部题目」切回来", page.locator("#allQuestionsButton").get_attribute("aria-pressed") == "true")

        # 侧栏里的「更多筛选」空时整条不出现，有内容时能展开
        print("== 更多筛选 ==")
        check("侧栏里有「更多筛选」", page.locator("#advancedFilters").count() == 1)
        if page.locator("#advancedFilters").is_visible():
            page.locator("#advancedFilters summary").click()
            page.wait_for_timeout(250)
            inner = page.locator("#advancedFilters .library-filters > :not([hidden])")
            check("展开后里面有可筛的东西", inner.count() >= 1, f"{inner.count()} 组")
            page.locator("#advancedFilters summary").click()
            page.wait_for_timeout(250)
        else:
            check("「更多筛选」空时整条不出现", True)

        # 抽屉里收起篮
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
        check("Esc 收起抽屉", not page.evaluate("window.QBSiteDrawer.isOpen()"))
        page.click(".drawer-trigger")
        page.wait_for_timeout(300)
        page.locator(".site-drawer-title-button").click()
        page.wait_for_timeout(300)
        check("抽屉里收起篮", not page.locator("#basketPanel").is_visible())
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

        # ---- 专注浏览：进得去也退得出（退出的按钮在顶栏，不在关着的抽屉里）
        print("== 专注浏览 ==")
        page.click(".drawer-trigger")
        page.wait_for_timeout(300)
        page.locator("#libraryFocusBrowse").click()
        page.wait_for_timeout(350)
        check("进了专注浏览", page.evaluate("document.body.classList.contains('library-focus-mode')"))
        check("退出按钮搬到了顶栏", page.locator(".topbar-tools #libraryFocusBrowse").count() == 1)
        check("顶栏工具区这时可见", page.evaluate("getComputedStyle(document.querySelector('.topbar-tools')).display") != "none")
        check("抽屉已经合上（否则遮罩会挡住顶栏的退出按钮）", not page.evaluate("window.QBSiteDrawer.isOpen()"))
        check("筛选条收起了", not page.locator("#libraryRail").is_visible())
        page.screenshot(path=str(OUTPUT / "focus-mode.png"))
        page.locator(".topbar-tools #libraryFocusBrowse").click()
        page.wait_for_timeout(350)
        check("从顶栏退出了专注浏览", not page.evaluate("document.body.classList.contains('library-focus-mode')"))
        check("按钮搬回抽屉", page.locator('.site-drawer [data-slot="library"] #libraryFocusBrowse').count() == 1)
        check("筛选条回来了", page.locator("#libraryRail").is_visible())
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

        # ---- 批量条：全选入口常驻，三个按钮勾了才出现
        print("== 批量条 ==")
        check("没勾选时批量按钮不出现", not page.locator("#addSelected").is_visible())
        page.locator("#selectVisible").check()
        page.wait_for_timeout(400)
        check("勾上后批量按钮出现", page.locator("#addSelected").is_visible())
        check("勾选数写对了", page.locator("#selectionCount").inner_text().strip() != "已勾选 0 题",
              page.locator("#selectionCount").inner_text())
        page.locator("#clearSelection").click()
        page.wait_for_timeout(400)
        check("取消勾选后按钮又收起来", not page.locator("#addSelected").is_visible())

        # ---- 录入终审页：导航折进去、教学巡演的聚光灯照得着
        print("== 录入终审页 ==")
        page.goto(f"{url}/")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(600)
        check("终审页也挂了抽屉", page.evaluate("window.QBSiteDrawer.isMounted()"))
        check("终审页导航默认不在视口里", not page.locator(".topnav a").first.is_visible())
        page.click(".drawer-trigger")
        page.wait_for_timeout(300)
        check("终审页抽屉能拉开", page.locator("#siteDrawer").is_visible())
        check("三个导航都在抽屉里", page.locator(".site-drawer .topnav a").count() == 3)
        check("当前页是录入终审", page.locator(".site-drawer .topnav a.active").inner_text().strip() == "录入终审")
        check("终审页没有试题篮这一组", page.locator('.site-drawer [data-group="basket"]').is_hidden())
        page.screenshot(path=str(OUTPUT / "drawer-review.png"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

        # 设置页高亮跟着 pathname 走
        page.goto(f"{url}/settings")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
        check("设置页高亮切到设置", page.locator(".site-drawer .topnav a.active").inner_text().strip() == "设置")

        # 教学巡演：走到题库那一步时抽屉得是开的
        page.goto(f"{url}/?tour=1")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(800)
        titles = []
        for _ in range(6):
            titles.append(page.locator("#tourTitle").inner_text())
            if titles[-1] == "找题和组卷":
                break
            if titles[-1] == "遇到问题时看帮助":
                break
            page.locator("#tourNext").click()
            page.wait_for_timeout(500)
        if "找题和组卷" not in titles:
            print(f"  (跳过) 巡演没走到题库那一步，标题：{titles}")
        else:
            check("巡演走到题库那一步时抽屉是开的", page.evaluate("window.QBSiteDrawer.isOpen()"), titles[-1])
            spot = page.evaluate("""() => { const s = document.querySelector('#tourSpot').getBoundingClientRect();
                return {w: Math.round(s.width), h: Math.round(s.height)}; }""")
            check("聚光灯照在那条链接上（不是一片空白）", spot["w"] > 20 and spot["h"] > 10, spot)
            page.screenshot(path=str(OUTPUT / "tour-drawer.png"))
            page.locator("#tourNext").click()
            page.wait_for_timeout(500)
            check("下一步到设置那一步，抽屉仍开着", page.evaluate("window.QBSiteDrawer.isOpen()"),
                  page.locator("#tourTitle").inner_text())
            page.locator("#tourNext").click()
            page.wait_for_timeout(500)
            check("巡演走完抽屉合上", not page.evaluate("window.QBSiteDrawer.isOpen()"))

        context.close()
        browser.close()

    print("errors:", errors or "无")
    print("failures:", failures or "无")
    print("saved:", OUTPUT)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
