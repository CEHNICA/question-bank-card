"""把所有页面走一遍，抓控制台报错和失败请求；再压一个没测过的交互。

两件事：

一、报错清扫。没人系统地扫过「每个页面打开有没有 console error、有没有 404」。
   一条 JS 报错不会让页面变白，但会让某个按钮永远不响应——用户只会说「点��没反应」。

二、按知识点筛选之后，直接在那张卡上改标签。改完那道题可能已经不符合筛选条件了：
   卡片该消失还是留下？筛选下拉里的计数有没有跟着变？这条路没人走过。
"""

from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"
SEED = ["导数在研究函数中的应用", "导数的运算"]
OTHER = ["随机抽样"]

PAGES = [
    ("/", "录入终审"),
    ("/library", "题库"),
    ("/settings", "设置"),
]

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
    items = api("/api/library?limit=100")["items"]
    target = next((item for item in items if not item.get("tags")), None)
    if not target:
        raise SystemExit("题库里找不到没标签的题")
    original = api(f"/api/library/{target['id']}/tags")["tags"]
    api(f"/api/library/{target['id']}/tags", {"tags": SEED})
    return target["id"], original


def sweep(page) -> None:
    for path, name in PAGES:
        errors: list[str] = []
        failed: list[str] = []
        page.on("console", lambda message, bag=errors: bag.append(f"{message.type}: {message.text}")
                if message.type == "error" else None)
        page.on("response", lambda response, bag=failed: bag.append(f"{response.status} {response.url}")
                if response.status >= 400 else None)
        page.goto("about:blank")
        page.goto(f"{BASE}{path}", wait_until="networkidle")
        page.wait_for_timeout(1500)
        check(not errors, f"{name}（{path}）打开没有控制台报错" + (f"：{errors[:2]}" if errors else ""))
        real_failed = [line for line in failed if "favicon" not in line]
        check(not real_failed, f"{name}（{path}）没有失败的请求" + (f"：{real_failed[:3]}" if real_failed else ""))
        page.remove_listener("console", page.listeners("console")[-1]) if hasattr(page, "listeners") else None


def filtered_edit(page, publication: str) -> None:
    page.goto("about:blank")
    page.goto(f"{BASE}/library?tag={urllib.parse.quote(SEED[0])}", wait_until="networkidle")
    page.wait_for_selector("#libraryList .library-card", timeout=20000)
    page.wait_for_timeout(900)
    total = page.locator("#libraryList .library-card").count()
    check(total == 1, f"按「{SEED[0]}」筛出来正好是那一道（{total} 张卡）")
    card = page.locator(f"#q-{publication}")
    check(card.count() == 1, "筛选结果里能找到它")

    # <option> 没有渲染盒，inner_text() 恒为空；下拉里长什么样要用 text_content()。
    option = page.locator("#tagSelect option", has_text=SEED[0])
    count_text = option.text_content() if option.count() else ""
    check("（1）" in count_text, f"筛选下拉里写着这个标签有几道：{count_text}")

    # 把它身上的筛选条件那个标签去掉，再保存——保存完它就不该符合筛选条件了。
    card.locator(".library-tags-edit").click()
    page.wait_for_selector("#tagEditorDialog[open]", timeout=8000)
    picked = page.locator(f"#tagEditorPicked .library-tag", has_text=SEED[0])
    picked.click()
    page.locator("#tagEditorSearch").fill("抽样")
    page.wait_for_timeout(300)
    page.locator("#tagEditorList .tag-editor-point:not(.active)").first.click()
    page.locator("#tagEditorSave").click()
    page.wait_for_selector("#tagEditorDialog[open]", state="detached", timeout=8000)
    page.wait_for_timeout(1500)

    gone = page.locator(f"#q-{publication}").count() == 0
    check(gone, "去掉筛选用的那个标签后，这道题从筛选结果里消失了（而不是留在那儿骗人）")
    toast = page.locator("#toast").inner_text() if page.locator("#toast").count() else ""
    check("随机抽样" in toast, f"保存结果说清楚了现在是什么标签：{toast}")
    # 种子题本来就带两个标签，这里只去掉筛选用的那个，另一个应当留着。
    still = page.request.get(f"{BASE}/api/library/{publication}/tags").json()["tags"]
    check(still == [SEED[1]] + OTHER, f"服务端存的也是同一组：{'、'.join(still)}")

    # 筛选下拉的计数要跟着变，不能还写着 1。
    page.goto("about:blank")
    page.goto(f"{BASE}/library?tag={urllib.parse.quote(SEED[0])}", wait_until="networkidle")
    page.wait_for_timeout(1200)
    left = page.locator("#libraryList .library-card").count()
    check(left == 0, f"重新进来，这个标签底下已经一道题都没有了（{left} 张卡）")
    page.screenshot(path=f"{SHOTS}/tag_filter_after_edit.png")


def main() -> int:
    import urllib.parse
    publication, original = seed()
    print(f"样本题：{publication}（跑完恢复成 {original or '无标签'}）")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                sweep(page)
                filtered_edit(page, publication)
            finally:
                browser.close()
    finally:
        api(f"/api/library/{publication}/tags", {"tags": original})
        print(f"已恢复 {publication} 的标签：{original or '（无）'}")
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
