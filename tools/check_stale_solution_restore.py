"""题面改过之后，原来填的那份答案解析还拿不拿得回来。

老师先花时间填了解析，后来发现题面有个错别字，顺手改掉。按设计，改完题面之后
旧解析要「待核对」—— 它不会自动套到新题上（题都变了，答案未必还对），这一步没
错。问题出在**拿走了之后还拿不拿得回来**：

旧解析挂在**旧版**题目上，而旧版本身不在题库里。改前改后，编辑器里能点的东西
一模一样，那份花时间填的东西就等于白填了。

这一条量的是真人点出来的那条路：
- 改完题面再打开解析编辑器，旧内容是不是真的摆在眼前（不是只写一句「待核对」）
- 那个块说没说清它是哪一版题面时填的
- 「填回编辑区」按下去，答案和过程是不是真的进了编辑框
- 老师已经对着新题面写了半段，再按一次会不会被悄悄盖掉（要问一句）
- 盖掉之后保存，这份解析是不是真的挂到**新**题上；再打开还在不在
- 配图能不能跟着搬过来（旧版的图在旧目录里，存图只认当前版本目录）

跑在数据库副本上：开跑前把库刷回干净状态，跑完不还原 —— 副本本来就是一次性的。
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
MARK = "旧解析标记"
# 解析配图另存一份：图跟着旧解析待在旧版本的目录里，填回来时得搬过来。
# 这一段是最容易「点一下就红」的地方，文字过了不算这条路过了。
FIG = r"tmp\export\stale-restore-figure.png"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def fail_hard(label: str) -> int:
    check(False, label)
    return 1


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
    """挑一道在库、没存过解析的题，取题面里几个连续汉字当搜索词。"""
    c = sqlite3.connect(WORK_DB)
    try:
        rows = c.execute(
            "select id, extras, content from core_publishedquestion"
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


def card_for(page, pub_id: str):
    return page.locator(f"article#q-{dashed(pub_id)}")


def images_loaded(scope) -> bool:
    """图 <img> 挂在页面上不等于图真出来了。逐张量 complete/naturalWidth。"""
    return bool(scope.locator("img").count()) and scope.evaluate(
        "n => [...n.querySelectorAll('img')].every(i => i.complete && i.naturalWidth > 0)")


def open_from_card(page, card, label: str):
    """按文字点题卡菜单里那一个按钮。必须正好命中一个。"""
    card.locator("details.library-card-more > summary").click()
    entries = card.locator("button").filter(has_text=label)
    if entries.count() != 1:
        raise AssertionError(f"题卡里「{label}」命中 {entries.count()} 个")
    entries.click()


def service_is_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE}/api/library?limit=1", timeout=4) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def make_figure() -> None:
    from PIL import Image, ImageDraw
    os.makedirs(os.path.dirname(FIG), exist_ok=True)
    image = Image.new("RGB", (420, 150), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle([8, 8, 411, 141], outline="#1f6b5f", width=4)
    draw.line([40, 110, 160, 40, 280, 90, 380, 30], fill="#b03a2e", width=5)
    draw.text((30, 118), "figure for the stale-solution check", fill="#333333")
    image.save(FIG)


def main() -> int:
    if not service_is_up():
        print(f"{BASE} 没在跑。这条要在指向 {WORK_DB} 的服务上跑。")
        return 2

    # 每次从干净库开始：这道题要新建一版题目、新建解析行，反复跑会越堆越乱。
    os.makedirs(os.path.dirname(WORK_DB), exist_ok=True)
    shutil.copyfile(SOURCE_DB, WORK_DB)
    make_figure()
    print(f"库已刷回干净副本：{WORK_DB}")

    pub, needle = pick_question()
    if not pub:
        print("库里没有可用的题，这一步没量到")
        return 2
    print(f"题目：{pub}（按「{needle}」找它）")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.goto(f"{BASE}/library", wait_until="domcontentloaded")
        page.wait_for_selector("#typeFilters button")

        found = None
        for candidate in (needle, needle[:6], needle[:4], needle[:3]):
            page.locator("#searchInput").fill(candidate)
            page.wait_for_timeout(900)
            if card_for(page, pub).count() == 1:
                found = candidate
                break
        if not found:
            print(f"按 {needle!r} 搜不到这张题卡，量不到")
            context.close()
            browser.close()
            return 2

        # 改题面会生成新版本、新题号。记下「第几题」，改完照这个找新那张卡。
        meta = re.sub(r"\s+", " ", card_for(page, pub).locator(".library-card-meta").inner_text()).strip()
        number_token = re.search(r"第\s*\d+\s*题", meta)
        number_token = number_token.group(0) if number_token else ""

        # 一、填一段答案，存进题库
        print("一、填一段答案存进题库")
        open_from_card(page, card_for(page, pub), "编辑答案解析")
        page.wait_for_selector("#answerEditorDialog[open]")
        page.wait_for_timeout(800)
        page.locator("#answerEditorResult").fill(MARK + "答案")
        page.locator("#answerEditorAnalysis").fill(MARK + "解析：从题面条件出发，两边同号才能相加。")
        page.locator("#answerEditorDialog input[type='file']").set_input_files(FIG)
        page.wait_for_function(
            "() => document.querySelectorAll('#answerEditorDialog .answer-image-row').length > 0", timeout=30000)
        print(f"   解析图挂上 {page.locator('#answerEditorDialog .answer-image-row').count()} 张")
        page.locator("#answerEditorSave").click()
        page.wait_for_function(
            "() => document.querySelector('.answer-editor-status')?.textContent.includes('已保存')", timeout=30000)
        print(f"   {page.locator('.answer-editor-status').inner_text().strip()}")
        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")

        # 二、再打开：自己刚存的那份在不在（这一步是基线，改之前就该是好的）
        print("二、重新打开，自己刚存的那份还在")
        open_from_card(page, card_for(page, pub), "编辑答案解析")
        page.wait_for_selector("#answerEditorDialog[open]")
        page.wait_for_timeout(900)
        baseline_answer = page.locator("#answerEditorResult").input_value()
        if not check(MARK in baseline_answer, "改题面之前，刚存的解析本来就读得出来"):
            context.close()
            browser.close()
            return 1
        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")

        # 三、改题面
        print("三、顺手把题面改掉")
        MARK_EDIT = "此处更正一个错别字"
        open_from_card(page, card_for(page, pub), "修改题目")
        page.wait_for_selector("#libraryQuestionEditor[open]")
        page.wait_for_timeout(1000)
        stem_box = page.locator("#libraryQuestionEditor textarea").first
        stem_box.fill(stem_box.input_value() + f"（{MARK_EDIT}）")
        page.locator("#libraryQuestionEditor button", has_text="保存题目").click()
        page.wait_for_selector("#libraryQuestionEditor", state="hidden", timeout=30000)
        page.wait_for_timeout(1500)
        # 改完题面就是新版本、新题号了。整个题库里每份卷子都有「第 1 题」，按题号找
        # 会撞车；按刚写进去的那句话找，才只有改完的这一张。
        page.locator("#searchInput").fill("错别字")
        page.wait_for_timeout(1200)
        cards = page.locator("article.library-card")
        if cards.count() != 1:
            print(f"  搜到 {cards.count()} 张题卡，找不到改完那一张，后面的量做不了")
            context.close()
            browser.close()
            return 2
        fresh = cards.first
        new_pub = (fresh.get_attribute("id") or "").removeprefix("q-")
        print(f"   新版本题目：{new_pub}（{number_token}）")

        # 四、改完之后：旧解析摆不摆在眼前
        print("四、改完题面再打开解析编辑器")
        open_from_card(page, fresh, "编辑答案解析")
        page.wait_for_selector("#answerEditorDialog[open]")
        page.wait_for_timeout(1200)

        aside = page.locator("#answerEditorDialog .answer-set-aside")
        visible = aside.count() == 1 and aside.evaluate(
            "n => n.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})")
        if not check(visible, "改完之后，编辑器里多出一块「原来那份解析」"):
            page.screenshot(path="tmp/export/stale-restore-fail.png")
            context.close()
            browser.close()
            return 1
        # 题卡上题面是截断显示的，比不出改没改。编辑器左上角那块「原卷本题」是全的。
        source_text = page.locator("#answerEditorDialog [aria-label='原卷本题']").inner_text()
        check(MARK_EDIT in source_text, "编辑器里看到的是改过之后的那份题面")
        aside_text = aside.inner_text()
        print(f"   那块上写着：{re.sub(chr(10), ' | ', aside_text)[:150]!r}")
        check(MARK in aside_text, "那块里就是原来填的那份解析（答案和过程都在）")
        check(aside.locator("img").count() >= 1 and images_loaded(aside),
              "连原来那张解析图也在，而且真加载出来了（不是裂图）")
        check(bool(re.search(r"第\s*\d+\s*版", aside_text)), "那块说清了它是哪一版题面时填的")
        check(page.locator("#answerEditorResult").input_value().strip() == "",
              "编辑框本身是空的 —— 旧解析没有被悄悄塞进来顶替新题面")
        status_empty = page.locator(".answer-editor-status").inner_text()
        check("已载入" in status_empty, "这时候状态栏说的是「已载入」（还没动过）")
        page.screenshot(path="tmp/export/stale-restore-aside.png")

        # 五、填回编辑区
        print("五、按「填回编辑区」")
        restore = aside.locator("button").filter(has_text="填回编辑区")
        if restore.count() != 1:
            print(f"  「填回编辑区」命中 {restore.count()} 个，量不到")
            context.close()
            browser.close()
            return 1
        restore.click()
        page.wait_for_timeout(600)
        answer_now = page.locator("#answerEditorResult").input_value()
        analysis_now = page.locator("#answerEditorAnalysis").input_value()
        check(MARK in answer_now, "按一下，答案就进了编辑框")
        check(MARK in analysis_now, "按一下，过程也进了编辑框")
        check(page.locator("#answerEditorDialog .answer-image-row").count() == 1,
              "按一下，原来那张解析图也跟着进了编辑区")
        check(images_loaded(page.locator("#answerEditorDialog .answer-image-list")),
              "搬过来的那张图真能显示，不是空框")
        check("未保存" in page.locator(".answer-editor-status").inner_text(),
              "填回来之后状态栏说的是「未保存」，不是当成已经存好了")
        page.screenshot(path="tmp/export/stale-restore-filled.png")

        # 六、老师已经对着新题面写了半段，再按一次会不会被悄悄盖掉
        print("六、已经改过字了，再按一次")
        page.locator("#answerEditorAnalysis").fill("对着改过的题面重写的过程，先写着。")
        page.wait_for_timeout(200)
        restore.click()
        page.wait_for_selector("#confirmDialog[open]", timeout=5000)
        prompt = page.locator("#confirmTitle").inner_text() + " / " + page.locator("#confirmText").inner_text()
        print(f"   问的是：{prompt!r}")
        check("盖掉" in prompt or "填回" in prompt, "按之前问了一句，不是直接盖")
        page.locator("#confirmDialog [value='cancel']").click()
        page.wait_for_timeout(500)
        check(page.locator("#answerEditorAnalysis").input_value() == "对着改过的题面重写的过程，先写着。",
              "选择不盖之后，刚才写的那段一个字都没丢")
        check(page.locator("#answerEditorResult").input_value().strip() != "", "答案也没被顺带清掉")

        # 七、同意盖，然后保存
        print("七、同意盖掉，保存")
        restore.click()
        page.wait_for_selector("#confirmDialog[open]", timeout=5000)
        page.locator("#confirmOk").click()
        page.wait_for_timeout(500)
        check(MARK in page.locator("#answerEditorAnalysis").input_value(), "同意之后确实换成原来那份")
        page.locator("#answerEditorSave").click()
        page.wait_for_function(
            "() => document.querySelector('.answer-editor-status')?.textContent.includes('已保存')", timeout=30000)
        print(f"   {page.locator('.answer-editor-status').inner_text().strip()}")
        check(aside.evaluate(
            "n => !n.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})"),
            "保存之后，那块「原来那份解析」自己收起来了")
        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")

        # 八、关掉再打开：这份解析是不是真的挂到新题上了
        print("八、关掉再打开一次")
        page.locator("#searchInput").fill("错别字")
        page.wait_for_timeout(1200)
        again = page.locator("article.library-card")
        if not check(again.count() == 1, "还能找到改完之后那一张题卡"):
            context.close()
            browser.close()
            return 1
        open_from_card(page, again.first, "编辑答案解析")
        page.wait_for_selector("#answerEditorDialog[open]")
        page.wait_for_timeout(1200)
        check(MARK in page.locator("#answerEditorResult").input_value(), "重新打开，答案就在编辑框里（这次是挂上了）")
        check(page.locator("#answerEditorDialog .answer-image-row").count() == 1
              and images_loaded(page.locator("#answerEditorDialog .answer-image-list")),
              "重新打开，那张解析图也还在（图片真的搬到了新版本名下）")
        aside_again = page.locator("#answerEditorDialog .answer-set-aside")
        check(aside_again.count() == 1 and not aside_again.evaluate(
            "n => n.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})"),
            "不再提示「题面改过」—— 它已经是这道题的解析了")
        page.screenshot(path="tmp/export/stale-restore-done.png")
        page.keyboard.press("Escape")
        page.wait_for_selector("#answerEditorDialog", state="hidden")

        context.close()
        browser.close()

    # 九、库里对不对得上
    print("九、库里")
    c = sqlite3.connect(WORK_DB)
    try:
        rows_new = c.execute("select id, answer, analysis, figures from core_librarysolution where publication_id = ?",
                             (key(new_pub),)).fetchall()
        attached = row_extras(c.execute("select extras from core_publishedquestion where id = ?",
                                       (key(new_pub),)).fetchone()[0]).get("solution_id")
        needs = row_extras(c.execute("select extras from core_publishedquestion where id = ?",
                                     (key(new_pub),)).fetchone()[0]).get("solution_needs_review_id")
    finally:
        c.close()
    check(len(rows_new) == 1, f"新版本下只多出一行解析（实际 {len(rows_new)} 行）")
    if rows_new:
        check(MARK in (rows_new[0][1] or "") and MARK in (rows_new[0][2] or ""), "那一行里就是填回来的内容")
        # extras 里存的是带横杠的 UUID，sqlite 里的 id 是不带横杠的 32 位十六进制。
        check((attached or "").replace("-", "") == rows_new[0][0], "题库挂着的是新版本这一行")
    else:
        check(False, "题库挂着的是新版本这一行")
    check(not needs, "「待核对」那个记号已经清掉了")
    # 图存在新版本自己的目录里，才算真的搬过来了（不是还指着旧目录的路径）。
    # 目录名用的是 str(publication.pk)，也就是**带横杠**的 UUID；sqlite 里的 id 才是不带横杠的。
    carried = json.loads(rows_new[0][3]) if rows_new and rows_new[0][3] else []
    folder = os.path.join("tmp", "accept-review", "data", "library-solutions", new_pub)
    on_disk = [f for f in carried
               if os.path.isfile(os.path.join(folder, f.get("id", "") + ".png"))
               and os.path.isfile(os.path.join(folder, f.get("id", "") + ".json"))]
    check(len(carried) == 1 and len(on_disk) == 1,
          f"新版本目录下真的躺着这张图（记录 {len(carried)} 张，磁盘上 {len(on_disk)} 张）")

    print()
    passed = sum(1 for ok, _ in results if ok)
    print(f"{passed}/{len(results)} 过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
