"""题面改过之后，试题篮里那道题还能不能用、老师知不知道下一步该干什么。

老师组卷到一半，发现题面有个错别字，回题库改掉，再回来接着组卷 —— 篮里那道题
已经作废了。这条路以前没人量过，量出来三处：

- 预览上写的是「第 1 题: superseded」。后端明明带了一句中文，界面却印了状态枚举。
- 后端算好了该换成哪一版（replacement_id），界面上一个字都没用，只剩「移出试题篮」。
- 「答案解析」按钮还是可点的，按下去打开一个空编辑器，屏幕上什么也不发生。

改完之后还要接着量：换进来的是不是新那一版、从预览里能不能把搁置的那份解析拿回来。

跑在数据库副本上，开跑前把库刷回干净状态。副本是一次性的，跑完不还原。
需要 8804 端口上的服务指向 tmp\\accept-review。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8804"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SOURCE_DB = r"tmp\accept-1133\db.sqlite3"
WORK_DB = r"tmp\accept-review\db.sqlite3"
MARK = "篮口标记"
EDIT_MARK = "此处更正一个错别字"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def key(publication_id: str) -> str:
    return publication_id.replace("-", "")


def dashed(publication_id: str) -> str:
    k = key(publication_id)
    return f"{k[:8]}-{k[8:12]}-{k[12:16]}-{k[16:20]}-{k[20:]}"


def row_extras(raw) -> dict:
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}


def pick_question():
    c = sqlite3.connect(WORK_DB)
    try:
        rows = c.execute("select id, extras, content from core_publishedquestion"
                         " where status = 'published' and withdrawn_at is null").fetchall()
    finally:
        c.close()
    for pub, extras, content in rows:
        if "solution_id" in row_extras(extras):
            continue
        stem = str((json.loads(content) or {}).get("stem") or "")
        plain = re.sub(r"\$[^$]*\$", " ", stem)
        plain = re.sub(r"\\[a-zA-Z]+|[{}\[\]^_]", " ", plain)
        for length in (10, 8, 6, 4, 3):
            words = [w for w in re.findall(r"[一-鿿]{%d,}" % length, plain) if len(w) >= length]
            if words:
                return pub, max(words, key=len)[:length]
    return None, None


def service_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/library?limit=1", timeout=4) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def open_menu_item(card, label: str) -> None:
    card.locator("details.library-card-more > summary").click()
    entries = card.locator("button").filter(has_text=label)
    if entries.count() != 1:
        raise AssertionError(f"题卡里「{label}」命中 {entries.count()} 个")
    entries.click()


def open_print(page) -> None:
    if not page.locator("#basketButton").is_visible():
        page.locator("#basketHandle").click()
        page.wait_for_timeout(700)
    page.locator("#basketButton").click()
    page.wait_for_selector("#printSheet[open]", timeout=15000)
    page.wait_for_timeout(1800)


def visible_button(scope, label: str):
    for index in range(scope.locator("button").count()):
        item = scope.locator("button").nth(index)
        if label in (item.inner_text() or "") and item.is_visible():
            return item
    return None


def main() -> int:
    if not service_is_up():
        print(f"{BASE} 没在跑。这条要在指向 {WORK_DB} 的服务上跑。")
        return 2
    os.makedirs(os.path.dirname(WORK_DB), exist_ok=True)
    shutil.copyfile(SOURCE_DB, WORK_DB)
    print(f"库已刷回干净副本：{WORK_DB}")

    pub, needle = pick_question()
    if not pub:
        print("库里没有可用的题，这一步没量到")
        return 2
    print(f"题目：{pub}（按「{needle}」找）")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        page.goto(f"{BASE}/library", wait_until="domcontentloaded")
        page.wait_for_selector("#typeFilters button")

        found = None
        for candidate in (needle, needle[:6], needle[:4], needle[:3]):
            page.locator("#searchInput").fill(candidate)
            page.wait_for_timeout(900)
            if page.locator(f"article#q-{dashed(pub)}").count() == 1:
                found = candidate
                break
        if not found:
            print(f"按 {needle!r} 搜不到这张题卡，量不到")
            browser.close()
            return 2

        # 一、填一段答案，存进题库（第二段要拿回来，先把这份造出来）
        print("一、填一段答案存进题库")
        card = page.locator(f"article#q-{dashed(pub)}")
        open_menu_item(card, "编辑答案解析")
        page.wait_for_selector("#answerEditorDialog[open]")
        page.wait_for_timeout(800)
        page.locator("#answerEditorResult").fill(MARK + "答案")
        page.locator("#answerEditorAnalysis").fill(MARK + "解析：从题面条件出发分两种情况讨论。")
        page.locator("#answerEditorSave").click()
        page.wait_for_function(
            "() => document.querySelector('.answer-editor-status')?.textContent.includes('已保存')", timeout=30000)
        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")

        # 二、加入试题篮
        print("二、加入试题篮")
        page.locator("#searchInput").fill(found)
        page.wait_for_timeout(1000)
        add = page.locator(f"article#q-{dashed(pub)} button").filter(has_text="加入试题篮")
        if add.count() != 1:
            print(f"「加入试题篮」命中 {add.count()} 个，量不到")
            browser.close()
            return 2
        add.click()
        page.wait_for_timeout(1200)
        if not check(page.locator("#basketList li.basket-row").count() == 1, "篮里那一行在"):
            browser.close()
            return 1

        # 三、回题库改题面
        print("三、发现错别字，回题库改掉")
        card = page.locator(f"article#q-{dashed(pub)}")
        open_menu_item(card, "修改题目")
        page.wait_for_selector("#libraryQuestionEditor[open]")
        page.wait_for_timeout(1000)
        box = page.locator("#libraryQuestionEditor textarea").first
        box.fill(box.input_value() + f"（{EDIT_MARK}）")
        page.locator("#libraryQuestionEditor button", has_text="保存题目").click()
        page.wait_for_selector("#libraryQuestionEditor", state="hidden", timeout=30000)
        page.wait_for_timeout(1500)
        page.locator("#searchInput").fill("错别字")
        page.wait_for_timeout(1200)
        fresh = page.locator("article.library-card")
        if not check(fresh.count() == 1, "改完之后题库上是新那一版"):
            browser.close()
            return 1
        new_pub = (fresh.first.get_attribute("id") or "").removeprefix("q-")
        print(f"   新版本：{new_pub}")

        # 四、回到组卷预览
        print("四、回到组卷预览")
        open_print(page)
        check(page.locator("#printPaper .print-question").count() == 0, "篮里那道旧版本确实载入不了")
        box = page.locator("#printMissing")
        if not check(box.is_visible(), "预览上有一块专门说「尚未载入」"):
            page.screenshot(path="tmp/export/paper-stale-fail.png")
            browser.close()
            return 1
        print(f"   提示：{re.sub(chr(10), ' | ', box.inner_text())[:170]!r}")
        row = page.locator(".print-missing-row").first
        row_text = row.inner_text()
        check(not re.search(r"\b(superseded|withdrawn|not_found)\b", row_text),
              f"界面上不出现英文状态枚举（现在写的是 {row_text.strip()[:60]!r}）")
        check("新版本" in row_text, "说的是「已经有新版本」，老师一眼知道自己该怎么办")

        swap = visible_button(row, "换成当前版本")
        if not check(swap is not None, "有一个「换成当前版本」的动作，不用自己移出去重搜"):
            page.screenshot(path="tmp/export/paper-stale-noswap.png")
            browser.close()
            return 1

        manage = page.locator("#managePrintAnswers")
        check(manage.is_disabled(), "一道题都没载入时「答案解析」是灰的")
        hint = manage.get_attribute("title") or ""
        print(f"   灰掉时的提示：{hint!r}")
        check(bool(hint), "灰掉时说清了为什么")

        drawer_text = ""
        page.locator("#closePrint").click()
        page.wait_for_timeout(700)
        if page.locator("#basketList li.basket-row").count() == 1:
            drawer_text = page.locator("#basketList li.basket-row").first.inner_text()
            print(f"   试题篮抽屉里写着：{re.sub(chr(10), ' | ', drawer_text)[:120]!r}")
            check(not re.search(r"\b(superseded|withdrawn|not_found)\b", drawer_text),
                  "试题篮抽屉里也不出现英文状态枚举")
            check(visible_button(page.locator("#basketList li.basket-row").first, "换新版本") is not None,
                  "抽屉里也有「换新版本」")
            open_print(page)
        else:
            check(False, "试题篮抽屉里还能看到那一行")
            browser.close()
            return 1

        # 五、换成当前版本
        print("五、按「换成当前版本」")
        swap = visible_button(page.locator(".print-missing-row").first, "换成当前版本")
        swap.click()
        page.wait_for_function(
            "() => !document.querySelector('#printMissing') || document.querySelector('#printMissing').hidden",
            timeout=20000)
        page.wait_for_timeout(1500)
        if not check(page.locator("#printPaper .print-question").count() == 1, "换完就载入了"):
            browser.close()
            return 1
        paper_text = re.sub(r"\s+", " ", page.locator("#printPaper").inner_text())
        check(EDIT_MARK in paper_text, "换进来的是改过之后的那一版题面")
        check(not page.locator("#managePrintAnswers").is_disabled(), "题进来了，「答案解析」就又能点了")
        page.screenshot(path="tmp/export/paper-stale-swapped.png")

        # 六、从预览里把搁置的那份解析拿回来（scope=paper 这一条路）
        print("六、从预览里补答案解析")
        page.locator("#managePrintAnswers").click()
        page.wait_for_selector("#answerEditorDialog[open]", timeout=15000)
        page.wait_for_timeout(1500)
        aside = page.locator("#answerEditorDialog .answer-set-aside")
        visible = aside.count() == 1 and aside.evaluate(
            "n => n.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})")
        check(visible, "从组卷预览进去，也能看到「题面改过，原来那份解析还留着」")
        if visible:
            aside_text = aside.inner_text()
            check(MARK in aside_text, "搁置的那份就在里面")
            check(bool(re.search(r"第\s*\d+\s*版", aside_text)), "说清了是哪一版题面时填的")
            rows = page.locator("#answerEditorDialog .answer-list-row")
            if check(rows.count() == 1, f"编辑器列出 {rows.count()} 道题"):
                # 题号带只有一格宽，完整状态按设计放在 title 里、格子上只留一个色点。
                # 这里量的就是 title，别拿 inner_text —— 那一格本来就是空的。
                row_text = re.sub(r"\s+", " ", rows.first.locator("button.answer-list-question").get_attribute("title") or "")
                print(f"   列表行写着：{row_text[:140]!r}")
                check("原解析还在" in row_text, "列表行也说了原解析还在、可以填回")
                state = rows.first.get_attribute("data-state")
                check(state == "review", f"那一行的状态是「待核对」（实际 {state!r}）")
        page.screenshot(path="tmp/export/paper-stale-editor.png")
        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")
        browser.close()

    c = sqlite3.connect(WORK_DB)
    try:
        superseded = c.execute("select status from core_publishedquestion where id = ?", (key(pub),)).fetchone()
    finally:
        c.close()
    check(superseded and superseded[0] == "superseded", "旧那一版留在库里是「已被新版替代」，没有消失")

    print()
    passed = sum(1 for ok, _ in results if ok)
    print(f"{passed}/{len(results)} 过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
