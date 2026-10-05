"""组卷草稿存下来再打开：选题、排版设置、导出的文件是不是原样。

组卷是备课里跨度最长的一步：周一挑好题、排好版存成草稿，周五再打开接着改。
中间隔着几天、可能还换过浏览器。这一条按真人操作走一遍：

挑几种题型的题 → 把排版改成一眼能认出来的样子（选项竖排、作答空间加大、印题源、14 磅）
→ 存草稿 → 清空试题篮 → **刷新页面**（内存里什么都不剩）→ 从抽屉里的「组卷草稿」重新打开
→ 逐项对设置 → 导出 Word，把文件解开看排版设置是不是真的落到文件里

只看界面上的下拉框没被改回去是不够的：预览是一个样���，导出来的文件是另一个样子，
用户是到打印那一刻才发现的。

收尾删掉自己建的草稿。
"""

from __future__ import annotations

import io
import json
import os
import re
import sys
import zipfile
from xml.sax.saxutils import unescape

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/export"
TITLE = "草稿往返验收卷"
OPTIONS = {
    "printTitle": TITLE,
    "printDocument": "combined",
    # 「逐题紧跟」时解答题不留白，作答空间是灰的；要看它就得让答案放卷末。
    "printAnswerLayout": "appendix",
    "printOptionLayout": "vertical",
    "printAnswerSpace": "large",
    "printFontSize": "14",
}
# 存盘走的是 normalizePrintOptions 之后的键名，和界面控件的 id 不是一套。
SETTINGS = {
    "document": "combined",
    "answer_layout": "appendix",
    "option_layout": "vertical",
    "answer_space": "large",
    "font_size": "14",
}
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def wait_layout(page) -> None:
    try:
        page.wait_for_function(
            "() => { const s = document.getElementById('printPageStatus'); return s && !s.textContent.includes('正在排版'); }",
            timeout=90000)
    except Exception:
        check(False, "排版报了完成（这轮一直卡在「正在排版…」，下面的量都不可信）")


def read_options(page) -> dict:
    return {key: page.locator(f"#{key}").input_value() for key in OPTIONS}


def open_basket(page) -> None:
    if not page.locator("#basketPanel").is_visible():
        page.locator("#basketHandle").click()
        page.wait_for_selector("#basketPanel", state="visible")


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    draft_id = None
    failures = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        posts: list[dict] = []
        page.on("request", lambda r: posts.append(r.post_data or "")
                if "/api/library/export-docx" in r.url and r.method == "POST" else None)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        try:
            page.goto(f"{BASE}/library", wait_until="domcontentloaded")
            page.evaluate("() => localStorage.clear()")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#typeFilters button")

            # ── 一、挑三种题型的题进篮 ────────────────────────────
            picked: dict[str, str] = {}
            for label in ("单选题", "填空题", "解答题"):
                button = page.locator("#typeFilters button").filter(has_text=label)
                if button.count() != 1:
                    check(False, f"{label}：点不准这一个筛选（命中 {button.count()} 个）")
                    continue
                button.click()
                try:
                    page.wait_for_function(
                        "l => { const c = document.querySelector('.library-card .library-type');"
                        " return c && c.textContent.trim() === l; }", arg=label, timeout=15000)
                except Exception:
                    check(False, f"{label}：筛完列表没换过来")
                    continue
                card = page.locator("article.library-card").first
                picked[label] = (card.get_attribute("id") or "").removeprefix("q-")
                card.locator(".library-card-select").check()
                page.locator("#addSelected").click()
                page.wait_for_timeout(150)
            if not check(len(picked) == 3, f"三种题型各挑一道进篮（{list(picked)}）"):
                return 1
            order = list(picked.values())

            # ── 二、把排版改成一眼能认出来的样子 ────────────────────
            open_basket(page)
            page.locator("#basketButton").click()
            page.wait_for_selector("#printSheet[open]")
            page.wait_for_function("() => document.querySelectorAll('#printPaper .print-question').length >= 1", timeout=40000)
            wait_layout(page)
            # 字号和留白收在「更多排版」里，不点开根本点不到。
            if not page.locator(".print-advanced").evaluate("n => n.open"):
                page.locator(".print-advanced > summary").click()
            for key, value in OPTIONS.items():
                control = page.locator(f"#{key}")
                if control.evaluate("n => n.tagName") == "SELECT":
                    control.select_option(value)
                else:
                    control.fill(value)
            # 开关本体是藏起来的，真人点的是它那个标签。
            if not page.locator("#printOrigin").is_checked():
                page.locator("label:has(#printOrigin)").click()
            wait_layout(page)
            before = read_options(page)
            origin_before = page.locator("#printOrigin").is_checked()
            check(before == OPTIONS, f"排版设置都设上了（{before}）")
            page.screenshot(path=f"{SHOTS}/draft-before.png")

            # ── 三、存草稿 ────────────────────────────────────────
            # 「保存草稿」收在工具条上那个「草稿」折叠里。
            if not page.locator("#printDraftMenu").evaluate("n => n.open"):
                page.locator("#printDraftMenu > summary").click()
            page.locator("#saveDraft").click()
            page.wait_for_function("t => { const s = document.getElementById('draftSaveStatus');"
                                   " return s && s.textContent.includes(t); }", arg=TITLE, timeout=30000)
            saved_text = page.locator("#draftSaveStatus").inner_text().strip()
            check("已保存" in saved_text, f"存下来了：{saved_text}")
            listing = page.request.get(f"{BASE}/api/library/drafts")
            found = [d for d in listing.json().get("drafts", []) if d.get("title") == TITLE]
            if not check(len(found) == 1, f"草稿列表里正好有一份《{TITLE}》（{len(found)} 份）"):
                return 1
            draft_id = found[0]["id"]
            stored = page.request.get(f"{BASE}/api/library/drafts/{draft_id}").json()["draft"]
            check(stored["ids"] == order, f"存进去的选题顺序和篮里一样（{len(stored['ids'])} 题）")
            # 存盘用的是 normalizePrintOptions 之后的键名，不是界面控件的 id。
            saved_options = stored.get("print_options") or {}
            wrong = {key: (saved_options.get(key), value) for key, value in SETTINGS.items()
                     if str(saved_options.get(key)) != value}
            check(not wrong, f"排版设置原样存进去了（对不上的：{wrong}）")
            check(stored.get("title") == TITLE, f"标题存下来了：{stored.get('title')}")

            # ── 四、清空试题篮 + 刷新，内存里什么都不剩 ──────────────
            page.locator("#closePrint").click()
            page.wait_for_selector("#printSheet", state="hidden")
            page.evaluate("() => localStorage.clear()")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#typeFilters button")
            check(page.locator("#basketCount").inner_text().strip() == "0", "试题篮是空的")

            # ── 五、从抽屉里的「组卷草稿」重新打开 ──────────────────
            page.locator(".drawer-trigger").click()
            page.wait_for_timeout(400)
            page.locator("#openDrafts").click()
            page.wait_for_selector("#draftsDialog[open]")
            row = page.locator("#draftsList .draft-row").filter(has_text=TITLE)
            check(row.count() == 1, f"草稿列表里找得到《{TITLE}》（命中 {row.count()} 行）")
            row.locator("button", has_text="继续组卷").click()
            # 篮里现在是空的，和草稿一致时不该弹确认
            page.wait_for_selector("#printSheet[open]", timeout=40000)
            page.wait_for_function("() => document.querySelectorAll('#printPaper .print-question').length >= 1", timeout=40000)
            wait_layout(page)

            after = read_options(page)
            check(after == before, f"排版设置一样回来了（{after}）")
            check(page.locator("#printOrigin").is_checked() == origin_before, "「印题源」也回来了")
            check(page.locator("#printTitle").input_value() == TITLE, f"标题也回来了：{page.locator('#printTitle').input_value()}")
            ids = page.eval_on_selector_all("#printPaper .print-question", "nodes => nodes.map(n => n.dataset.questionId)")
            check(ids == order, f"选题和顺序都一样（{len(ids)} 题）")
            page.screenshot(path=f"{SHOTS}/draft-after.png")

            # ── 六、导出的文件是不是也照着这份设置来的 ──────────────
            posts.clear()
            page.locator("#exportWord").click()
            # 「输出内容」选了题目＋答案而题都没答案时，导出会先问一句 —— 真人会选「本次跳过」。
            if page.locator("#answerMissingDialog").is_visible():
                check(True, "选了「题目＋答案」而题没答案时，导出前先问了一句（本次跳过并导出）")
                page.locator("#answerMissingDialog button[value='skip']").click()
            status, waited = "", 0
            while waited < 90000:
                status = page.locator("#printExportStatus").inner_text()
                if "已导出" in status or "已下载" in status:
                    break
                page.wait_for_timeout(400)
                waited += 400
            check(status.strip() != "", f"导出后状态栏有话可说：{status.strip()[:70]}")
            body = posts[-1] if posts else ""
            if not check(bool(body), f"重新打开之后导得出 Word（状态栏：{status.strip()[:70]}）"):
                return 1
            blob = page.request.post(f"{BASE}/api/library/export-docx", data=body,
                                     headers={"Content-Type": "application/json", "X-QB-Request": "1"}).body()
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            text = unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", xml, re.S)))
            # 「竖排」是把四个选项各占一段，不是排成多列的表格；
            # 「自动 / 一行四个」才会用表格。所以这里看选项是不是各自成段。
            paragraphs = [unescape("".join(re.findall(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", block, re.S)))
                          for block in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)]
            option_lines = [p for p in paragraphs if re.match(r"^\s*[A-E]\s*[.．、]", p)]
            check(len(option_lines) >= 4 and xml.count("<w:tbl>") == 0,
                  f"选项真的按「竖排」排了（{len(option_lines)} 个选项各占一段，表格 {xml.count('<w:tbl>')} 张）")
            check(TITLE in text, f"标题写进了文件：{TITLE in text}")
            sizes = re.findall(r'<w:sz w:val="(\d+)"/>', xml)
            # Word 里字号是「半磅」为单位的数值：14 磅存成 28。
            check("28" in sizes, f"字号是 14 磅（文件里存的是半磅：{sorted(set(sizes), key=int)}）")
            check(len(paragraphs) > len(option_lines) + 6, f"解答题后面留出了作答的空白段（共 {len(paragraphs)} 段）")
            open(f"{SHOTS}/draft-restored.docx", "wb").write(blob)
            check(not errors, f"全程没有脚本报错（{errors[:2]}）")

        finally:
            if draft_id:
                removed = page.request.delete(f"{BASE}/api/library/drafts/{draft_id}",
                                              data="{}", headers={"Content-Type": "application/json",
                                                                  "X-QB-Request": "1"})
                print(f"  （收尾删掉自己建的草稿：HTTP {removed.status}）")
            context.close()
            browser.close()

    failures = len([1 for ok, _ in results if not ok])
    print()
    print(f"共 {len(results)} 条，FAIL {failures} 条")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
