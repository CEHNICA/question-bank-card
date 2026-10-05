"""答案解析编辑器：一次填好几道题，切换和保存会不会把没保存的内容弄丢。

老师补答案是最费时间的活，也是最容易一肚子火的活：一次把整张卷子的题都放进编辑器，
一道一道往下填。中途保存一道、切到下一道、再切回来 —— **前面那些没保存的字还在不在**，
是这条唯一要盯的事。丢一个字符，用户就以为编辑器有毛病，接下来一小时都不敢放心用。

两种保存都量到：
- 从组卷预览里进来，「同时保存到题库」默认不勾 —— 存的是这份卷子，题库里不动
- 勾上之后才进题库

还量：同一道题改两遍，版本是不是真的多了一版（「已保存」只说存上了，不说存了几版）；
存完关掉再打开，看到的是不是刚存的那一版。

借答案进去，收尾原样还回去。挑的都是题库里本来没有解析的题，
免得把「它本来就有的答案」当成「这次填的」。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/export"
DB = r"tmp\accept-1133\db.sqlite3"
MARK = "批量填写标记"
COUNT = 6
UNSAVED = ("有未保存的编辑", "未保存")

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def db_key(publication_id: str) -> str:
    return publication_id.replace("-", "")


def dashed(publication_id: str) -> str:
    key = db_key(publication_id)
    return f"{key[:8]}-{key[8:12]}-{key[12:16]}-{key[16:20]}-{key[20:]}"


def restore(baseline: dict) -> None:
    """按「跑之前就有的行」收尾。

    保存解析时**每一版都会建一行**，不勾同步只是不把题库指向它 —— 所以行会多出来，
    靠 extras 里的 solution_id 找不全。基线之外的、属于这轮挑的题的行，全删掉。
    """
    if not baseline:
        return
    connection = sqlite3.connect(DB)
    missed = []
    try:
        for pub_id, (original, before) in baseline.items():
            key = db_key(pub_id)
            extra = connection.execute(
                "select id from core_librarysolution where publication_id = ?", (key,)).fetchall()
            fresh = [row[0] for row in extra if row[0] not in before]
            for solution_id in fresh:
                connection.execute("DELETE FROM core_librarysolution WHERE id = ?", (solution_id,))
            left = connection.execute(
                "select count(*) from core_librarysolution where publication_id = ?", (key,)).fetchone()[0]
            if left != len(before):
                missed.append((pub_id, f"删了 {len(fresh)} 行，还剩 {left}，跑之前是 {len(before)}"))
            written = connection.execute("UPDATE core_publishedquestion SET extras = ? WHERE id = ?",
                                         (baseline[pub_id][0], key)).rowcount
            if written != 1:
                missed.append((pub_id, f"还原 extras 改了 {written} 行"))
        connection.commit()
    finally:
        connection.close()
    print(f"  （{'已还回' if not missed else '没能还干净'} {len(baseline)} 道题：{missed or '都复原了'}）")


def extras_of(pub_id: str) -> tuple[str, str]:
    connection = sqlite3.connect(DB)
    try:
        raw = connection.execute("SELECT extras FROM core_publishedquestion WHERE id = ?",
                                 (db_key(pub_id),)).fetchone()[0]
    finally:
        connection.close()
    raw = raw or "{}"
    return raw, ((json.loads(raw) or {}).get("solution_id") or "")


def solution_count(pub_id: str) -> int:
    connection = sqlite3.connect(DB)
    try:
        return connection.execute("SELECT count(*) FROM core_librarysolution WHERE publication_id = ?",
                                 (db_key(pub_id),)).fetchone()[0]
    finally:
        connection.close()


def latest_answer(pub_id: str) -> str | None:
    connection = sqlite3.connect(DB)
    try:
        row = connection.execute("SELECT answer FROM core_librarysolution WHERE publication_id = ?"
                                 " ORDER BY created_at DESC, id DESC LIMIT 1", (db_key(pub_id),)).fetchone()
    finally:
        connection.close()
    return row[0] if row else None


def pick_without_answers(page, connection) -> list[str]:
    picked: list[str] = []
    for node in page.query_selector_all("article.library-card")[:30]:
        candidate = (node.get_attribute("id") or "").removeprefix("q-")
        if not candidate:
            continue
        row = connection.execute("SELECT extras FROM core_publishedquestion WHERE id = ?",
                                 (db_key(candidate),)).fetchone()
        if row and "solution_id" not in (json.loads(row[0] or "{}") or {}):
            picked.append(candidate)
        if len(picked) == COUNT:
            break
    return picked


def open_question(page, number: int) -> None:
    for index, row in enumerate(page.query_selector_all("#answerEditorDialog .answer-list-row")):
        head = row.query_selector("strong")
        if head and head.text_content().strip().startswith(f"第 {number} 题"):
            page.locator("#answerEditorDialog .answer-list-row").nth(index) \
                .locator("button.answer-list-question").click()
            page.wait_for_function("n => { const p = document.querySelector('.answer-editor-place');"
                                   " return p && p.textContent.startsWith('第 ' + n + ' 题'); }",
                                   arg=number, timeout=20000)
            return
    raise AssertionError(f"题号带里找不到「第 {number} 题」这一行")


def status_text(page) -> str:
    return page.locator(".answer-editor-status").inner_text().strip()


def wait_saved(page) -> str:
    page.wait_for_function("() => { const s = document.querySelector('.answer-editor-status');"
                           " return s && s.textContent.includes('已保存'); }", timeout=30000)
    return status_text(page)


def snapshot() -> dict:
    """全库解析行分布。键统一成带横杠的，和界面给的编号对得上。"""
    connection = sqlite3.connect(DB)
    try:
        rows = connection.execute(
            "select publication_id, count(*) from core_librarysolution group by 1").fetchall()
    finally:
        connection.close()
    return {dashed(pub): count for pub, count in rows}


def solution_ids(pub_id: str) -> set:
    connection = sqlite3.connect(DB)
    try:
        return {row[0] for row in connection.execute(
            "select id from core_librarysolution where publication_id = ?", (db_key(pub_id),))}
    finally:
        connection.close()


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    baseline: dict = {}
    try:
        baseline = walk(baseline)
    finally:
        restore(baseline)
    failures = len([1 for ok, _ in results if not ok])
    print()
    print(f"共 {len(results)} 条，FAIL {failures} 条")
    return 1 if failures else 0


def walk(baseline: dict) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        connection = sqlite3.connect(DB)
        try:
            page.goto(f"{BASE}/library", wait_until="domcontentloaded")
            page.evaluate("() => localStorage.clear()")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#typeFilters button")

            picked = pick_without_answers(page, connection)
            originals = {pub: (connection.execute(
                "SELECT extras FROM core_publishedquestion WHERE id = ?", (db_key(pub),)).fetchone()[0] or "{}")
                for pub in picked}
            connection.close()
            if not check(len(picked) == COUNT, f"挑到 {len(picked)} 道本来没有解析的题"):
                return {}
            # 基线：这几道题跑之前各有哪些解析行、extras 原文是什么。收尾按它还原。
            baseline.update({pub: (originals[pub], solution_ids(pub)) for pub in picked})
            order = {pub: index + 1 for index, pub in enumerate(picked)}

            for pub_id in picked:
                page.locator(f"article#q-{pub_id} .library-card-select").check()
                page.locator("#addSelected").click()
                page.wait_for_timeout(120)
            check(page.locator("#basketCount").inner_text().strip() == str(COUNT),
                  f"{COUNT} 道题都进了篮（篮里写着 {page.locator('#basketCount').inner_text().strip()}）")

            if not page.locator("#basketPanel").is_visible():
                page.locator("#basketHandle").click()
                page.wait_for_selector("#basketPanel", state="visible")
            page.locator("#basketButton").click()
            page.wait_for_selector("#printSheet[open]")
            page.wait_for_function(f"() => document.querySelectorAll('#printPaper .print-question').length >= {COUNT}",
                                   timeout=60000)
            blocks = page.eval_on_selector_all(
                "#printPaper .print-question", "nodes => nodes.map(n => n.dataset.questionId)")
            repeated = sorted({i for i in blocks if blocks.count(i) > 1})
            check(len(set(blocks)) == COUNT,
                  f"预览里有 {len(blocks)} 个题块、{len(set(blocks))} 道题，和篮里 {COUNT} 道对得上"
                  + (f"（同一道题的小问拆成了多块：{repeated}）" if repeated else ""))
            if len(set(blocks)) != COUNT:
                return baseline
            # 卷面是按题型重排过的，编辑器里的「第 N 题」跟的是卷面顺序，不是篮里的顺序。
            # 拿错就等于在量另一道题 —— 这里先换算好。
            paper_order = list(dict.fromkeys(blocks))
            check(sorted(paper_order) == sorted(picked),
                  f"卷面顺序和篮里是同一批题（卷面第 1 题 = 篮里第 {picked.index(paper_order[0]) + 1} 题）")
            check(not page.locator("#printMissing").is_visible(), "没有「尚未载入」的提示")
            try:
                page.wait_for_function(
                    "() => { const s = document.getElementById('printPageStatus');"
                    " return s && !s.textContent.includes('正在排版'); }", timeout=90000)
            except Exception:
                check(False, "排版报了完成（一直卡在「正在排版…」，下面的量都不可信）")

            # ── 一、每道题都先写点什么，一道都不保存就来回切 ──────
            page.locator("#managePrintAnswers").click()
            page.wait_for_selector("#answerEditorDialog[open]")
            open_question(page, 1)
            page.locator("#answerEditorResult").fill(MARK + "1")
            page.locator("#answerEditorAnalysis").fill(MARK + "解析1")
            for number in range(2, COUNT + 1):
                open_question(page, number)
                page.locator("#answerEditorResult").fill(MARK + str(number))
                page.locator("#answerEditorAnalysis").fill(MARK + "解析" + str(number))
            page.screenshot(path=f"{SHOTS}/answer-editor-all-typed.png")

            lost = []
            for number in range(1, COUNT + 1):
                open_question(page, number)
                got = page.locator("#answerEditorResult").input_value()
                said = status_text(page)
                if got != MARK + str(number) or not any(word in said for word in UNSAVED):
                    lost.append((number, got, said[:30]))
            check(not lost, f"{COUNT} 道题一道没保存就来回切了一遍，字一个没丢（丢的：{lost}）")

            # ── 二、默认保存只进这份卷子：多一版行，但题库不指向它 ──
            one, two = paper_order[0], paper_order[1]
            open_question(page, 1)
            check(not page.locator("#answerEditorSync").is_checked(),
                  "「同时保存到题库」默认没勾")
            base_rows = snapshot()
            base_link = extras_of(one)[1]
            page.locator("#answerEditorSave").click()
            check("已保存" in wait_saved(page), f"卷面第 1 题存下来了：{status_text(page)[:40]}")
            grew = [pub for pub, count in snapshot().items() if count > base_rows.get(pub, 0)]
            check(grew == [one], f"保存确实落在卷面第 1 题上（多出行的：{grew}）")
            check(extras_of(one)[1] == base_link,
                  f"没勾同步时题库不指向新存的那一版（仍挂着 {base_link or '没有'}）")

            # ── 三、勾上同步再存一道：题库和卷子都得有 ──────────────
            survivors = []
            for number in range(2, COUNT + 1):
                open_question(page, number)
                if page.locator("#answerEditorResult").input_value() != MARK + str(number):
                    survivors.append((number, page.locator("#answerEditorResult").input_value()))
            check(not survivors, f"保存第 1 题之后，另外 {COUNT - 1} 道没保存的字还在（丢的：{survivors}）")
            check(page.locator(".answer-editor-place").inner_text().strip().endswith(f"共 {COUNT} 题"),
                  f"题号带还是 {COUNT} 道：{page.locator('.answer-editor-place').inner_text().strip()}")

            open_question(page, 2)
            page.locator("label:has(#answerEditorSync)").click()
            check(page.locator("#answerEditorSync").is_checked(), "同步开关点得动、也勾上了")
            base_rows = snapshot()
            base_link = extras_of(two)[1]
            page.locator("#answerEditorSave").click()
            said = wait_saved(page)
            check("已保存到题库" in said, f"卷面第 2 题存下来了：{said[:40]}")
            after_rows = snapshot()
            check(after_rows.get(two, 0) == base_rows.get(two, 0) + 1,
                  f"勾了同步，卷面第 2 题在题库里多了一版（{base_rows.get(two, 0)} → {after_rows.get(two, 0)}）")
            linked = extras_of(two)[1]
            check(bool(linked) and linked != base_link, f"题库把新存的那一版挂上了：{linked or '没有'}")
            if not linked:
                return baseline

            # ── 四、同一道改两遍，应该真的多一版 ──────────────────
            open_question(page, 2)
            page.locator("#answerEditorResult").fill(MARK + "2-改")
            base_rows = snapshot()
            page.locator("#answerEditorSave").click()
            wait_saved(page)
            after_rows = snapshot()
            check(after_rows.get(two, 0) == base_rows.get(two, 0) + 1,
                  f"同一道题存了两次，题库里是 {after_rows.get(two, 0)} 版"
                  f"（{base_rows.get(two, 0)} → {after_rows.get(two, 0)}）")
            check(latest_answer(two) == MARK + "2-改", f"生效的是最后存的那一版：{latest_answer(two)}")

            # ── 五、关掉再打开，看到的是刚存的那一版 ──────────────
            # 还有没保存的编辑时按 Esc 会先问一句 —— 闷头关掉才是真的丢字。
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            if page.locator("#confirmDialog").is_visible():
                asked = page.locator("#confirmText").inner_text().strip()
                check("未保存" in asked, f"有没保存的内容时，先问一句才让走：{asked[:44]}")
                page.locator("#confirmOk").click()
            page.wait_for_selector("#answerEditorDialog", state="hidden", timeout=20000)
            page.wait_for_timeout(800)
            page.locator("#managePrintAnswers").click()
            page.wait_for_selector("#answerEditorDialog[open]")
            open_question(page, 2)
            check(page.locator("#answerEditorResult").input_value() == MARK + "2-改",
                  f"重新打开还是刚才那版：{page.locator('#answerEditorResult').input_value()!r}")
            check(not any(word in status_text(page) for word in UNSAVED),
                  f"状态栏没把它说成还有没保存的东西：{status_text(page)[:40]}")
            open_question(page, 1)
            check(page.locator("#answerEditorResult").input_value() == MARK + "1",
                  f"只存进这份卷子的第 1 题也还在：{page.locator('#answerEditorResult').input_value()!r}")
            open_question(page, 3)
            check(page.locator("#answerEditorResult").input_value() == "",
                  f"确认返回后，没保存的内容真的没被带回来（{page.locator('#answerEditorResult').input_value()!r}）")
            page.screenshot(path=f"{SHOTS}/answer-editor-reopened.png")
            check(not errors, f"全程没有脚本报错（{errors[:2]}）")
        finally:
            context.close()
            browser.close()

    return baseline


if __name__ == "__main__":
    sys.exit(main())
