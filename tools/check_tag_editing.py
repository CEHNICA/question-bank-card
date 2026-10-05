"""亲手点一遍：题库里的知识点标签能不能改、能不能删、删完能不能重打。

这不是「接口通了吗」的检查。断言全部落在外部可见的地方——卡片上显示的标签、
对话框列出的目录、保存后从 API 读回的标签、以及清空后自己回来的
「打知识点标签」按钮。跑在数据库副本上，不碰真库。
"""

from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def api(path: str, payload=None):
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-QB-Request": "1"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def seed():
    """拿一道原本没标签的题做实验，跑完恢复原样。

    脚本本身要改标签，所以不能依赖「库里有几道带标签的题」这种既有状态：
    连跑两次就会把自己的实验对象清空。改之前把原标签记下来。
    """
    items = api("/api/library?limit=100")["items"]
    target = next((item for item in items if not item.get("tags")), None)
    if not target:
        raise SystemExit("题库里找不到没标签的题，先导入几道再跑这个脚本")
    original = api(f"/api/library/{target['id']}/tags")["tags"]
    api(f"/api/library/{target['id']}/tags", {"tags": ["导数的运算", "导数在研究函数中的应用"]})
    return target["id"], original


def run(page, publication, original) -> None:
    page.goto(f"{BASE}/library", wait_until="networkidle")
    page.wait_for_selector("#libraryList .library-card", timeout=20000)
    first_card = page.locator("#libraryList .library-card").filter(has=page.locator(".library-tags")).first
    check(first_card.count() == 1, "题库首页就能看到带知识点标签的题")
    if not first_card.count():
        return
    # 一旦清空标签，按「有标签」筛出来的卡片就换成别的题了。后面每一步都必须
    # 按题目 id 钉住同一张卡，否则量的是另一道题。
    card = page.locator(f"#q-{publication}")
    before = card.locator(".library-tag").all_text_contents()
    served_before = api(f"/api/library/{publication}/tags")
    check(served_before["tags"] == before, f"卡片和服务端说的标签一致：{'、'.join(before)}")
    version_before = api(f"/api/library/{publication}")["publication"]["version"]

    card.locator(".library-tags-edit").click()
    page.wait_for_selector("#tagEditorDialog[open]", timeout=8000)
    check(True, "点「改」能打开标签对话框")
    place = page.locator("#tagEditorDialog .tag-editor-place").inner_text()
    check("第" in place and "题" in place, f"对话框写明是哪道题：{place}")
    # 来源要么写着人工改过，要么写着是哪个引擎自动打的，要和存的一致。
    said_human = "人工改过" in place
    said_engine = "自动生成" in place and served_before["source"] in place
    check(said_human == (served_before["source"] == "human") or said_engine,
          f"对话框说得出这组标签是谁打的（{served_before['source'] or '没打过'}）")
    points = page.locator("#tagEditorList .tag-editor-point").count()
    catalogue_size = len(api(f"/api/library/{publication}/tags")["catalogue"])
    check(points == catalogue_size, f"目录列出全部 {points} 个知识点")
    check(page.locator("#tagEditorPicked .library-tag").all_text_contents() == before,
          "对话框原样带出当前标签")
    page.screenshot(path=f"{SHOTS}/tag_editor_open.png")

    # 上限三个：满了再点要被挡住，而且要说清为什么
    if before and len(before) >= 3:
        page.locator("#tagEditorList .tag-editor-point:not(.active)").first.click()
        page.wait_for_timeout(300)
        check(page.locator("#tagEditorPicked .library-tag").count() == 3, "已经有 3 个时点第 4 个没有加上")
        check("最多" in page.locator("#toast").inner_text(),
              f"并且说明了原因：{page.locator('#toast').inner_text()}")
        page.locator("#tagEditorSearch").fill("这一串目录里不可能有")
        page.wait_for_timeout(250)
        check(page.locator("#tagEditorList .library-empty").count() == 1, "搜不到时给一句话，不留空白")
        page.locator("#tagEditorSearch").fill("")

    # 去掉一个、换一个
    page.locator("#tagEditorPicked .library-tag").first.click()
    page.wait_for_timeout(150)
    check(page.locator("#tagEditorPicked .library-tag").count() == len(before) - 1, "点已选的标签能去掉")
    page.locator("#tagEditorSearch").fill("圆锥")
    page.wait_for_timeout(250)
    fresh = page.locator("#tagEditorList .tag-editor-point:not(.active)")
    check(fresh.count() > 0, "搜索能筛出目录里的知识点")
    addition = fresh.first.inner_text()
    fresh.first.click()
    page.wait_for_timeout(150)
    page.locator("#tagEditorSave").click()
    page.wait_for_selector("#tagEditorDialog[open]", state="detached", timeout=8000)
    page.wait_for_timeout(1200)
    shown = card.locator(".library-tag").all_text_contents()
    check(addition in shown and before[0] not in shown,
          f"保存后卡片上换成了：{'、'.join(shown)}")
    check(api(f"/api/library/{publication}/tags")["tags"] == shown, "保存后服务端也是同一组标签")
    check(api(f"/api/library/{publication}/tags")["source"] == "human", "来源改成了人工改过")
    page.screenshot(path=f"{SHOTS}/tag_editor_saved.png")

    # 题面没被动过
    check(api(f"/api/library/{publication}")["publication"]["version"] == version_before,
          f"题面版本号没有变（第 {version_before} 版）")

    # 清除：清空后「打知识点标签」按钮要自己回来
    card.locator(".library-tags-edit").click()
    page.wait_for_selector("#tagEditorDialog[open]", timeout=8000)
    page.locator("#tagEditorClear").click()
    page.wait_for_timeout(200)
    check(page.locator("#tagEditorPicked .library-tag").count() == 0, "「清除全部标签」把选择清空")
    page.locator("#tagEditorSave").click()
    page.wait_for_selector("#tagEditorDialog[open]", state="detached", timeout=8000)
    page.wait_for_timeout(1500)
    check(card.locator(".library-tags").count() == 0, "清空后卡片上不再有标签行")
    check(api(f"/api/library/{publication}/tags")["tags"] == [], "服务端也确认标签已空")
    # 「打知识点标签」在卡片的「更多」菜单里。数得到不等于看得见，
    # 必须把菜单点开，再确认这个按钮真的能看见、点得动。
    card.locator(".library-card-more > summary").click()
    page.wait_for_timeout(300)
    regen = card.locator(".library-card-menu button", has_text="打知识点标签")
    check(regen.count() == 1 and regen.is_visible(), "清空后「更多」菜单里的「打知识点标签」回来了")
    card.locator(".library-card-more > summary").click()
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    page.screenshot(path=f"{SHOTS}/tag_editor_cleared.png")

    # 取消不该留下改动
    tagged = page.locator("#libraryList .library-card").filter(has=page.locator(".library-tags")).count()
    if tagged:
        again = page.locator("#libraryList .library-card").filter(has=page.locator(".library-tags")).first
        keep = again.locator(".library-tag").all_text_contents()
        again.locator(".library-tags-edit").click()
        page.wait_for_selector("#tagEditorDialog[open]", timeout=8000)
        page.locator("#tagEditorPicked .library-tag").first.click()
        page.locator("#tagEditorCancel").click()
        page.wait_for_timeout(700)
        check(again.locator(".library-tag").all_text_contents() == keep, "点「取消」不留下任何改动")


def main() -> int:
    publication, original = seed()
    print(f"实验对象：{publication}（跑完恢复成 {original or '无标签'}）")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            run(page, publication, original)
            browser.close()
    finally:
        api(f"/api/library/{publication}/tags", {"tags": original})
        print(f"已恢复 {publication} 的标签：{original or '（无）'}")
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

