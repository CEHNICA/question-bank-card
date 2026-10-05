"""生成要花钱：面板上得看得见「每道新题几次调用」和「题库里还差几道」。

以前那两行只写「会用到服务额度」，既没写几次，也没写还差几道，用户点下去才知道。
这里把数字和真库对一遍：面板上写的和 /api/library 数出来的必须是同一个数，
不能各数一遍又互相矛盾。
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
    page.wait_for_timeout(900)


def run(page) -> None:
    open_panel(page)
    served = page.request.get(f"{BASE}/api/settings/library-ai").json()
    backlog = served["backlog"]

    # 服务端自己报的数和题库里数出来的一样吗。接口一页只给 100 条，要翻页数全。
    first = page.request.get(f"{BASE}/api/library?limit=100").json()
    items = list(first["items"])
    offset = first["next_offset"]
    while offset is not None:
        following = page.request.get(f"{BASE}/api/library?limit=100", params={"offset": offset}).json()
        items.extend(following["items"])
        offset = following["next_offset"]
    tagged = sum(1 for item in items if item["tags"])
    check(backlog["total"] == len(items), f"题库共 {len(items)} 道，面板这边也说是 {backlog['total']} 道")
    check(backlog["tags"] == len(items) - tagged,
          f"还差标签的 {backlog['tags']} 道，和题库里一页页数出来的一致")

    cost = page.locator("#libraryAICost").inner_text()
    check("调用" in cost and "次服务" in cost, f"费用那句话写明了调用次数：{cost}")
    check(str(len(items)) in cost, "费用那句话带着题库的总题数")
    check("入库时生成" in cost or "都开" in cost, "说的是入库时自动生成这件事")

    tags_backlog = page.locator("#libraryAITagsBacklog").inner_text()
    answer_backlog = page.locator("#libraryAIAnswerBacklog").inner_text()
    check(f"还差 {backlog['tags']} 道" in tags_backlog, f"标签那一行：{tags_backlog}")
    check(f"还差 {backlog['answer']} 道" in answer_backlog, f"答案那一行：{answer_backlog}")
    page.screenshot(path=f"{SHOTS}/cost_line.png")

    # 两个「入库时生成」都开着的时候，才说每道新题几次调用。
    both = "2 次" in cost
    check(both == (served["on_intake"]["tags"] and served["on_intake"]["answer"]),
          f"写着「2 次」和两个开关的实际状态一致（本机 tags={served['on_intake']['tags']} answer={served['on_intake']['answer']}）")

    # 拨开关，费用那句话要当场跟着变，不能等保存后重读。
    intake_answer = page.locator("#libraryAIAnswerIntake")
    if intake_answer.is_visible():
        intake_answer.uncheck()
        page.wait_for_timeout(400)
        after = page.locator("#libraryAICost").inner_text()
        check(after != cost and "1 次" in after, f"关掉「入库时生成参考答案」，立刻变成：{after[:40]}…")
        intake_answer.check()
        page.wait_for_timeout(400)
        check(page.locator("#libraryAICost").inner_text() == cost, "再打开，费用那句话跟着回来")

    # 关掉整个功能，那句话退回最基本的说法。
    page.locator("#libraryAIAnswer").uncheck()
    page.wait_for_timeout(400)
    off = page.locator("#libraryAICost").inner_text()
    check("1 次" in off or "两项默认关闭" in off, f"只开一项时说的是：{off[:40]}…")
    page.locator("#libraryAIAnswer").check()
    page.wait_for_timeout(300)


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
