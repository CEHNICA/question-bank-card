"""在题卡上填了答案，回头组卷时这份答案还在不在。

组卷预览每次打开都会给每道题记一条「这次用原卷的答案」（内存里的一张表）。
在题卡上填答案走的是另一条路，只更新题库那份缓存，不碰这张表。
于是在题卡上明明存好了、界面也写着「已保存到题库」，回到组卷预览却还是
「这些题没有答案或解析」，「分别导出题目卷与答案卷」一直灰着；
刷新一下页面又好了 —— 看着就像没存上，用户多半会再填一遍。

所以这条量三处：填之前、填之后不刷新、填之后刷新。三处看到的应该一致。
1.13.5 之前第二处和第三处对不上。

借一段答案进去，收尾还回去（借之前那道题本来是没有答案的）。
"""
import json
import sqlite3
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DB = r"tmp\accept-1133\db.sqlite3"
MARK = "界面复现标记"
LOAN = None      # (publication_id, 借之前的 extras, 借出来的 solution id)


def db_key(publication_id: str) -> str:
    return publication_id.replace("-", "")


def restore(pub_id: str, original_extras: str, solution_id: str) -> None:
    connection = sqlite3.connect(DB)
    try:
        deleted = connection.execute("DELETE FROM core_librarysolution WHERE id = ?",
                                    (db_key(solution_id),)).rowcount
        written = connection.execute("UPDATE core_publishedquestion SET extras = ? WHERE id = ?",
                                    (original_extras, db_key(pub_id))).rowcount
        connection.commit()
    finally:
        connection.close()
    print(f"  还原：删了 {deleted} 行、extras 改了 {written} 行"
          + ("" if deleted == 1 and written == 1 else "  ← 没还原干净"))


def main() -> int:
    global LOAN
    LOAN = None
    try:
        return walk()
    finally:
        # 中途崩了也必须还回去：借答案是往题库里真写的。
        if LOAN and LOAN[2]:
            restore(*LOAN)
        LOAN = None


def walk() -> int:
    global LOAN
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()

        connection = sqlite3.connect(DB)
        page.goto(f"{BASE}/library", wait_until="domcontentloaded")
        # 只在开场清一次。用 add_init_script 的话每次导航都会清，
        # 后面「刷新页面再看」那一步篮子是空的，量不到对照。
        page.evaluate("() => localStorage.clear()")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#typeFilters button")
        # 挑一道**本来就没有答案**的题：题库里已经有三道带着真答案，
        # 挑到它们的话一开始就写着「1 题有答案」，这次改动看不出来。
        card_index, pub_id, original_extras = None, None, None
        for index, node in enumerate(page.query_selector_all("article.library-card")[:12]):
            candidate = (node.get_attribute("id") or "").removeprefix("q-")
            if not candidate:
                continue
            row = connection.execute(
                "select extras from core_publishedquestion where id = ?", (db_key(candidate),)).fetchone()
            if row and "solution_id" not in (json.loads(row[0] or "{}") or {}):
                card_index, pub_id = index, candidate
                original_extras = row[0] or "{}"
                break
        connection.close()
        if pub_id is None:
            print("前 12 张题卡里找不到没答案的题，这一步没量到")
            context.close()
            browser.close()
            return 2
        card = page.locator("article.library-card").nth(card_index)
        card.locator(".library-card-select").check()
        page.wait_for_timeout(200)
        page.locator("#addSelected").click()
        page.wait_for_timeout(300)
        if not page.locator("#basketPanel").is_visible():
            page.locator("#basketHandle").click()
            page.wait_for_selector("#basketPanel", state="visible")

        print("题目：", pub_id)
        print("一、第一次打开组卷预览")
        page.locator("#basketButton").click()
        page.wait_for_selector("#printSheet[open]")
        page.wait_for_function("() => document.querySelectorAll('#printPaper .print-question').length >= 1", timeout=40000)
        first = page.locator("#printAnswerStatus").inner_text().strip()
        print("   ", first)
        page.locator("#closePrint").click()
        page.wait_for_selector("#printSheet", state="hidden")

        print("二、回到题库，在题卡上填答案（「更多 → 编辑答案解析」）")
        card.locator("details.library-card-more > summary").click()
        entry = card.locator("button").filter(has_text="编辑答案解析")
        if entry.count() != 1:
            print(f"   题卡里「编辑答案解析」命中 {entry.count()} 个，这一步没量到")
            context.close()
            browser.close()
            return 2
        entry.click()
        page.wait_for_selector("#answerEditorDialog[open]")
        page.locator("#answerEditorResult").fill(MARK)
        page.locator("#answerEditorAnalysis").fill(MARK + "：这是复现用的解析。")
        page.locator("#answerEditorSave").click()
        page.wait_for_function(
            "mark => { const s = document.getElementById('answerEditorStatus') || document.querySelector('.answer-editor-status');"
            " return s && s.textContent.includes('已保存'); }", arg=MARK, timeout=30000)
        saved_text = page.locator(".answer-editor-status").inner_text().strip()
        print("   保存后状态：", saved_text[:60])

        # 把刚存的那一版解析记下来，收尾要还回去
        connection = sqlite3.connect(DB)
        linked = (json.loads(connection.execute(
            "select extras from core_publishedquestion where id = ?", (db_key(pub_id),)).fetchone()[0] or "{}")
            or {}).get("solution_id")
        connection.close()
        LOAN = (pub_id, original_extras, linked or "")
        print("   题库已挂上解析：", linked)

        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")
        page.wait_for_timeout(800)

        print("三、同一次使用里再打开组卷预览")
        if not page.locator("#basketPanel").is_visible():
            page.locator("#basketHandle").click()
            page.wait_for_selector("#basketPanel", state="visible")
        page.locator("#basketButton").click()
        page.wait_for_selector("#printSheet[open]")
        page.wait_for_function("() => document.querySelectorAll('#printPaper .print-question').length >= 1", timeout=40000)
        again = page.locator("#printAnswerStatus").inner_text().strip()
        print("   ", again)
        split_disabled = page.evaluate("() => document.getElementById('exportSplit').disabled")
        print("    「分别导出题目卷与答案卷」是灰的：", split_disabled)
        page.screenshot(path="tmp/export/saved-answer-kept.png")
        page.locator("#closePrint").click()
        page.wait_for_selector("#printSheet", state="hidden")

        print("四、刷新页面之后再打开")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#typeFilters button")
        if not page.locator("#basketPanel").is_visible():
            page.locator("#basketHandle").click()
            page.wait_for_selector("#basketPanel", state="visible")
        page.locator("#basketButton").click()
        page.wait_for_selector("#printSheet[open]")
        page.wait_for_function("() => document.querySelectorAll('#printPaper .print-question').length >= 1", timeout=40000)
        after_reload = page.locator("#printAnswerStatus").inner_text().strip()
        print("   ", after_reload)
        print("    「分别导出题目卷与答案卷」是灰的：",
              page.evaluate("() => document.getElementById('exportSplit').disabled"))

        # 三、四两处看到的应该一样。刷新能好、同一次使用里不好，
        # 说明答案在题库里、只是这次组卷没认它 —— 正是这条要盯的毛病。
        broken = "没有答案" in again and "没有答案" not in after_reload
        print()
        print("FAIL  填过的答案在这次组卷里没出现，刷新一下才认" if broken
              else "PASS  填过的答案，同一次使用里立刻就在试卷上")
        print("      " + ("（刷新后：" + after_reload + "）" if broken else ""))

        context.close()
        browser.close()

    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
