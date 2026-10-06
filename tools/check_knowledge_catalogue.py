"""知识点目录的入口搬到了「标签与答案」面板里，还能直接看内容。

以前这个入口在「显示与导出」页最底下、归在「题面整理」板块里——和题面整理毫无
关系，而且只能拿记事本改。这里断言：面板里有一行说清目录有多少、点「查看目录」
能看到全部知识点、能搜、并且说清楚文件在哪。
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"
VIEWPORT = {"width": 1440, "height": 1000}

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def open_panel(page):
    page.goto("about:blank")
    page.goto(f"{BASE}/settings#api", wait_until="networkidle")
    page.wait_for_selector("#credentialDialog[open]", timeout=15000)
    page.locator("#credentialAnswerTab").click()
    page.wait_for_selector("#libraryAISettingsDialog", state="visible", timeout=8000)
    # 目录这一段跟着「生成知识点标签」走：本机默认没开，整段不渲染，
    # 后面点「查看目录」会直接找不到按钮。先把它打开（不保存，走完就丢）。
    tags = page.locator("#libraryAITags")
    if tags.count() and not tags.is_checked():
        tags.check()
        page.wait_for_timeout(1200)
    page.wait_for_timeout(900)


def run(page) -> None:
    # 「显示与导出」那一页不该再有知识点目录的入口了。
    page.goto("about:blank")
    page.goto(f"{BASE}/settings", wait_until="networkidle")
    page.wait_for_timeout(800)
    check(page.locator("#knowledgeDetails").count() == 0, "「显示与导出」页不再挂知识点目录的入口")
    check(page.locator("#featureNote").count() == 0, "连那条只剩一个文件路径的说明一起搬走了")

    open_panel(page)
    section = page.locator("#libraryAIKnowledge")
    # 目录的规模是从服务端读回来的：功能关着时服务端不读那个文件，前端在面板里
    # 勾开开关也补不上（它只管显示，不去重新拉）。这时候量到的「正在读取…」不是
    # 界面坏了，是这台机器压根没开着这个功能 —— 如实说，别当成缺陷。
    served = page.request.get(f"{BASE}/api/settings/library-ai", headers={"X-QB-Request": "1"}).json()
    if not served.get("features", {}).get("knowledge_tags"):
        print("     SKIP 本机没开「生成知识点标签」，服务端不读知识点目录，"
              "这一节量不到（先把功能打开再跑）")
        return
    check(section.count() == 1 and section.is_visible(), "「标签与答案」面板里有知识点目录这一段")
    count_text = page.locator("#libraryAIKnowledgeCount").inner_text()
    check("个知识点" in count_text, f"说清了目录有多大：{count_text}")
    file_text = page.locator("#libraryAIKnowledgeFile").inner_text()
    check("knowledge-points.txt" in file_text, f"给出了文件位置：{file_text}")
    page.screenshot(path=f"{SHOTS}/catalogue_in_panel.png")

    # 这一段必须排在「独立模型配置」前面：目录跟打标签有关，跟模型无关。
    order = page.evaluate("""() => {
      const y = el => el.getBoundingClientRect().top;
      return {knowledge: y(document.querySelector('#libraryAIKnowledge')),
              advanced: y(document.querySelector('#libraryAIAdvanced'))};
    }""")
    check(order["knowledge"] < order["advanced"], "目录排在模型配置前面")

    page.locator("#libraryAIKnowledgeOpen").click()
    page.wait_for_selector("#knowledgeCatalogueDialog[open]", timeout=8000)
    page.wait_for_timeout(400)
    points = page.locator("#catalogueList .catalogue-point").count()
    total = page.request.get(f"{BASE}/api/settings/knowledge", headers={"X-QB-Request": "1"}).json()["total"]
    check(points == total, f"预览列出了全部 {points} 个知识点（接口说 {total} 个）")
    chapters = page.locator("#catalogueList .catalogue-chapter").count()
    check(chapters > 0, f"按 {chapters} 个章分组")
    # 目录自己滚动，底栏那句「要增删就用记事本」任何时候都得看得见。
    foot = page.locator(".catalogue-foot")
    check(foot.is_visible() and foot.bounding_box()["y"] + foot.bounding_box()["height"] <= 1000,
          "底栏那句改目录的说明在窗口内")
    check(page.evaluate("() => document.querySelector('#catalogueList').scrollHeight > "
                        "document.querySelector('#catalogueList').clientHeight"),
          "73 个知识点放不下，目录自己在滚")
    page.screenshot(path=f"{SHOTS}/catalogue_dialog.png")

    # 搜章名也要搜得到：椭圆、双曲线、抛物线三个词都不含「圆锥」。
    page.locator("#libraryAIKnowledgeSearch").fill("圆锥")
    page.wait_for_timeout(300)
    found = page.locator("#catalogueList .catalogue-point").all_text_contents()
    check("椭圆" in found and "双曲线" in found, f"搜章名「圆锥」找到了 {found}")
    page.locator("#libraryAIKnowledgeSearch").fill("这一串目录里不可能有")
    page.wait_for_timeout(300)
    check(page.locator("#catalogueList .catalogue-empty").count() == 1, "搜不到时给一句话")
    page.locator("#catalogueClose").click()
    page.wait_for_timeout(400)
    check(page.locator("#knowledgeCatalogueDialog").evaluate("el => el.open") is False, "点「关闭」关得掉")

    # 关掉「生成知识点标签」，目录这一段就该跟着藏起来。
    page.locator("#libraryAITags").uncheck()
    page.wait_for_timeout(1200)
    check(page.locator("#libraryAIKnowledge").is_hidden(), "关掉标签功能，目录这一段也一起藏起来")
    page.locator("#libraryAITags").check()
    page.wait_for_timeout(1200)
    check(page.locator("#libraryAIKnowledge").is_visible(), "再打开，目录又回来")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        try:
            run(page)
        finally:
            browser.close()
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
