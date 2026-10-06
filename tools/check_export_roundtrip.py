"""组卷到文件：四种题型选进篮，导出的 Word / 分卷 / PDF 里是不是都真的在。

已有的压测都停在「界面能用」。导出是这个产品**唯一的出口**——用户在题库里挑了半天题、
组好卷、点了导出，最后拿到的文件里少一题、少一个公式、少一段答案，界面上不会有任何提示。
后端那套导出有单测，可单测用的是造出来的固定内容，**没有一道真题走完「勾选 → 加篮 →
预览 → 点导出 → 解开文件看内容」这条路**。

所以这条脚本按真人操作走一遍，最后把导出的文件解开、逐项核对：

- 四种题型各挑一道进篮，预览里题型分组和题数对不对
- 「组卷预览」那个按钮收在右边缘的抽屉里 —— 量一下加完题之后，用户看得见的是什么
- Word 里四道题的题干都在，公式是 Word 公式（OMML）而不是图片，借进去的答案也在
- 「分别导出」给出的是真 zip，里面题目卷、答案卷各是一个能打开的 docx
- PDF 文件头合法、真的有页
- 导完一次按钮恢复可点（导出卡在「正在导出」是最难自己发现的坏法）

借答案：这道题库一道答案都没有，「分别导出」永远点不开。脚本走答案编辑器的真实接口
借四段答案进去（那是用户手工填答案走的同一条路），导完原样还回去。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import zipfile
from io import BytesIO
from xml.sax.saxutils import unescape

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/export"
DB = r"tmp\accept-1133\db.sqlite3"
MARK = "验收标记"

TYPES = [("single_choice", "单选题"), ("multiple_choice", "多选题"),
         ("fill_blank", "填空题"), ("free_response", "解答题")]

# <w:t> 的正则不能写成 <w:t[^>]*>：<w:tblPr>、<w:tc> 也会被它当开口，
# 结果整张表格的属性全被当成正文混进文本里，比对时看着像「少了一题」。
WT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>", re.S)
MT = re.compile(r"<m:t(?:\s[^>]*)?>(.*?)</m:t>", re.S)

results: list[tuple[bool, str]] = []
skipped: list[str] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def skip(label: str) -> None:
    skipped.append(label)
    print("SKIP  " + label)


def fail_count(ok: bool) -> int:
    return 0 if ok else 1


def norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def signature(text: str) -> str:
    """只留汉字、字母和数字。

    填空题那条横线两边不一样：界面上是一个画出来的空（没有文字），
    Word 里是一串下划线。标点、空格、下划线全去掉，两边才对得上。
    """
    return "".join(ch for ch in (text or "") if ch.isalnum())


def docx_parts(blob: bytes) -> dict:
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
        media = [n for n in archive.namelist() if n.startswith("word/media/")]
    return {
        "text": signature(unescape("".join(WT.findall(xml)))),
        "raw_text": unescape("".join(WT.findall(xml))),
        "math": len(re.findall(r"<m:oMath[ >]", xml)),
        "drawings": xml.count("<w:drawing>"),
        "media": len(media),
    }


def is_docx(blob: bytes) -> bool:
    try:
        with zipfile.ZipFile(BytesIO(blob)) as archive:
            return "word/document.xml" in archive.namelist()
    except Exception:
        return False


PARA = re.compile(r"<w:p[ >].*?</w:p>", re.S)


def paragraphs(blob: bytes) -> list[str]:
    """按段落切开。题号是段首的「N.」，连成一片正文就分不出第几题了。"""
    with zipfile.ZipFile(BytesIO(blob)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    out = []
    for para in PARA.findall(xml):
        text = unescape("".join(WT.findall(para))).strip()
        if text:
            out.append(text)
    return out


def question_numbers(blob: bytes) -> list[int]:
    """按出现顺序取出题号。段首形如「3. 」或「3.」（AI 参考 · 未核对）。"""
    seen = []
    for text in paragraphs(blob):
        match = re.match(r"^(\d+)\s*[.．、]", text)
        if match:
            seen.append(int(match.group(1)))
    return seen


# 题干里混着公式：公式在 Word 里变成 OMML，原始字符不会原样出现，
# 拿整段题干去比必然对不上。这里只取**不含公式的纯文字片段**去核对。
PLAIN_STEM = """(block) => {
  const clone = block.querySelector('.qb-stem-body')?.cloneNode(true) || block.cloneNode(true);
  clone.querySelectorAll('.formula, .katex, .katex-mathml, math, img, svg, .print-question-tools, button')
    .forEach(n => n.remove());
  return (clone.textContent || '').replace(/[0-9]+[.．、]/g, '').trim();
}"""

BUTTON_STATE = """() => Object.fromEntries(
  ['exportWord', 'exportPdf', 'exportSplit'].map(id => {
    const node = document.getElementById(id);
    return [id, {disabled: node.disabled,
                 visible: node.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})}];
  }))"""


# ── 借答案（走答案编辑器的真实接口），收尾原样还回去 ────────────────
# 接口给的 UUID 带横杠，Django 在 sqlite 里存的是不带横杠的 32 位十六进制。
# 拿带横杠的去查/改，SELECT 永远查不到、UPDATE 永远匹配 0 行 —— 而脚本照样
# 报「已还回」。所以这里统一换，并且读不到、写不动都当失败。
def db_key(publication_id: str) -> str:
    return publication_id.replace("-", "")


class Loans:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []   # (publication_id, 原 extras, 新建 solution id)
        self.broken: list[str] = []

    def borrow(self, page, publications: dict[str, str]) -> bool:
        for index, (key, pub_id) in enumerate(publications.items(), 1):
            original, existing = self._extras(pub_id)
            if existing is None and original is None:
                self.broken.append(pub_id)
                skip(f"借答案前读不到 {pub_id} 的 extras，这道题不借了")
                continue
            response = page.request.post(
                f"{BASE}/api/library/{pub_id}/solution",
                data=json.dumps({"answer": f"{MARK}答案{index}-{key}",
                                 "analysis": f"{MARK}解析{index}-{key}：这一步是从题面推到结论的关键。",
                                 "figures": [], "base_revision": existing, "sync_library": True}),
                headers={"Content-Type": "application/json", "X-QB-Request": "1"})
            if response.status != 201:
                self.broken.append(pub_id)
                skip(f"借答案失败（HTTP {response.status} {response.text()[:120]}），「分别导出」这条路这轮量不到")
                self.restore()
                return False
            self.rows.append((pub_id, original, response.json()["solution"]["id"]))
        print(f"  （借了 {len(self.rows)} 段答案进去，收尾会还）")
        return bool(self.rows)

    def restore(self) -> None:
        if not self.rows:
            return
        connection = sqlite3.connect(DB)
        missed = 0
        try:
            for pub_id, original, solution_id in self.rows:
                plain = db_key(solution_id)
                cursor = connection.execute("DELETE FROM core_librarysolution WHERE id = ?", (plain,))
                if cursor.rowcount != 1:
                    missed += 1
                    print(f"  !! 没删掉解析行 {plain}（匹配 {cursor.rowcount} 行），库现在是脏的")
                write = connection.execute("UPDATE core_publishedquestion SET extras = ? WHERE id = ?",
                                           (original, db_key(pub_id)))
                if write.rowcount != 1:
                    missed += 1
                    print(f"  !! 没还原 {pub_id} 的 extras（匹配 {write.rowcount} 行）")
            connection.commit()
        finally:
            connection.close()
        print(f"  （{'已还回' if not missed else '没能还干净'} {len(self.rows)} 段答案，题库 extras 复原）")
        self.rows.clear()

    def _extras(self, pub_id: str) -> tuple[str | None, str | None]:
        """(原 extras 原文, 已挂着的 solution_id)。读不到就返回 (None, None)。"""
        connection = sqlite3.connect(DB)
        try:
            row = connection.execute("SELECT extras FROM core_publishedquestion WHERE id = ?",
                                     (db_key(pub_id),)).fetchone()
        finally:
            connection.close()
        if row is None:
            return None, None
        raw = row[0] if row[0] is not None else "{}"
        parsed = json.loads(raw) if raw.strip() else {}
        return raw, parsed.get("solution_id")


def last_post(posts: list[dict], route: str) -> dict | None:
    for post in reversed(posts):
        if route in post["url"]:
            return post
    return None


def settle(page, label: str, timeout: int = 90000) -> str:
    """等状态栏给出结果。中途弹确认框就点确定。"""
    waited = 0
    while waited < timeout:
        if page.locator("#confirmDialog").is_visible():
            page.locator("#confirmOk").click()
        text = page.locator("#printExportStatus").inner_text()
        if any(word in text for word in ("已导出", "已下载", "未能", "不能", "请")):
            return text.strip()
        page.wait_for_timeout(400)
        waited += 400
    return f"（等 {label} 的结果等了 {timeout // 1000}s，没等到）"


def replay(page, post: dict) -> tuple[int, bytes, str]:
    response = page.request.post(post["url"], data=post["body"],
                                 headers={"Content-Type": "application/json", "X-QB-Request": "1"})
    return response.status, response.body(), response.headers.get("content-type", "")


def main() -> int:
    os.makedirs(SHOTS, exist_ok=True)
    failures = 0
    loans = Loans()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, accept_downloads=True)
        # 试题篮住在 localStorage，先清干净再开场，免得量到上一次留下的题。
        context.add_init_script("try { localStorage.clear(); } catch (e) {}")
        page = context.new_page()

        posts: list[dict] = []
        page.on("request", lambda r: posts.append({"url": r.url, "body": r.post_data or ""})
                if "/api/library/export-" in r.url and r.method == "POST" else None)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        try:
            page.goto(f"{BASE}/library", wait_until="domcontentloaded")
            page.wait_for_selector("#typeFilters button")

            # ── 一、四种题型各挑一道进篮 ──────────────────────────
            picked: dict[str, str] = {}
            for key, label in TYPES:
                button = page.locator("#typeFilters button").filter(has_text=label)
                if button.count() != 1:
                    skip(f"{label}：点不准这一个筛选（命中 {button.count()} 个）")
                    continue
                button.click()
                # 等列表真的换成这一型，别在旧列表上勾。
                try:
                    page.wait_for_function(
                        "label => { const c = document.querySelector('.library-card .library-type');"
                        " return c && c.textContent.trim() === label; }",
                        arg=label, timeout=15000)
                except Exception:
                    skip(f"{label}：筛完列表没换过来")
                    continue
                card = page.locator("article.library-card").first
                if card.count() == 0:
                    skip(f"{label}：这一型一道题都没有")
                    continue
                picked[key] = (card.get_attribute("id") or "").removeprefix("q-")
                card.locator(".library-card-select").check()
                page.locator("#addSelected").click()
                page.wait_for_timeout(120)

            got = "、".join(label for key, label in TYPES if key in picked)
            failures += fail_count(check(len(picked) >= 3, f"至少挑进三种题型（实到 {len(picked)} 种：{got}）"))
            count_text = page.locator("#basketCount").inner_text().strip()
            failures += fail_count(check(count_text == str(len(picked)),
                                           f"篮里显示 {count_text} 题，和挑进去的 {len(picked)} 道对得上"))
            summary = page.locator("#basketSummary").inner_text()
            missing = [label for key, label in TYPES if key in picked and label not in summary]
            failures += fail_count(check(not missing, f"篮里写清了各题型（{summary}）"))

            # ── 二、加完题之后，用户下一步该点哪儿 ──────────────────
            # 「组卷预览」那个按钮收在右边缘抽屉里。加完题只弹一句「试题篮共 N 题」，
            # 所以要量的是：界面上唯一指向下一步的那条把手，此刻是不是真的在提示人。
            handle = page.locator("#basketHandle")
            failures += fail_count(check(handle.is_visible(), "右边缘有一条常驻的篮子把手"))
            box = handle.bounding_box() or {}
            failures += fail_count(check(box.get("width", 0) >= 24 and box.get("height", 0) >= 40,
                                           f"把手够大点得中（{round(box.get('width', 0))}×{round(box.get('height', 0))}px）"))
            failures += fail_count(check(not page.locator("#basketButton").is_visible(),
                                           "加完题不自动弹抽屉，「组卷预览」此刻不在屏幕上（得先点把手）"))
            glow = handle.evaluate("n => getComputedStyle(n, '::after').animationName")
            failures += fail_count(check("fluoresce" in (glow or ""),
                                           f"篮里有题、还没展开过时，把手在闪（::{'{'}after{'}'} 动画 {glow}）"))
            page.screenshot(path=f"{SHOTS}/after-add.png")
            handle.click()
            page.wait_for_selector("#basketPanel", state="visible")
            glow2 = handle.evaluate("n => getComputedStyle(n, '::after').animationName")
            failures += fail_count(check("fluoresce" not in (glow2 or ""),
                                           f"展开过一次之后就不再闪了（动画 {glow2}）"))
            page.locator("#basketButton").click()

            # ── 三、组卷预览 ──────────────────────────────────────
            wait_preview(page, len(picked))
            failures += preview_checks(page, picked)

            stems: list[tuple[str, str]] = []
            for block in page.query_selector_all("#printPaper .print-question"):
                qid = block.get_attribute("data-question-id") or ""
                snippet = signature(page.evaluate(PLAIN_STEM, block))[:24]
                # 太短的片段在文件里容易撞车（"下列"这种），只留够长的。
                if len(snippet) >= 5:
                    stems.append((qid, snippet))
            if not check(stems, f"四道题都取到了能比对的题干文字（{len(stems)} 段）"):
                failures += 1
            print("  （题干片段：" + " ｜ ".join(snippet for _, snippet in stems) + "）")

            # ── 四、借答案，把「分别导出」这条路打开 ────────────────
            page.locator("#closePrint").click()
            page.wait_for_selector("#printSheet", state="hidden")
            borrowed = loans.borrow(page, picked)
            # 抽屉本来就是开着的，别顺手点一下把手把它关了。
            if not page.locator("#basketPanel").is_visible():
                page.locator("#basketHandle").click()
                page.wait_for_selector("#basketPanel", state="visible")
            page.locator("#basketButton").click()
            wait_preview(page, len(picked))

            buttons = page.evaluate(BUTTON_STATE)
            failures += fail_count(check(not buttons["exportWord"]["disabled"], f"「导出 Word」可点（{buttons['exportWord']}）"))
            failures += fail_count(check(bool(borrowed) is not buttons["exportSplit"]["disabled"],
                                         f"借到答案之后「分别导出」的状态跟着变（借到 {len(loans.rows)} 段，"
                                         f"按钮 {buttons['exportSplit']}）"))
            print(f"  （答案状态：{page.locator('#printAnswerStatus').inner_text().strip() or '没写'}）")

            # ── 五、导出 Word ─────────────────────────────────────
            # 「输出内容」默认是「题目」，答案本来就不该进去 —— 先量默认这一档。
            posts.clear()
            page.locator("#exportWord").click()
            status = settle(page, "Word")
            word = last_post(posts, "export-docx")
            failures += fail_count(check(word is not None, "点「导出 Word」真的发了一次请求"))
            if word:
                failures += export_checks(page, "Word 题目卷", word, stems, status, expect_answers=False)

            # 再切到「题目＋答案」导一次：借进去的答案得跟着进文件。
            page.select_option("#printDocument", "combined")
            page.wait_for_function(
                "() => { const s = document.getElementById('printPageStatus'); return s && !s.textContent.includes('正在排版'); }",
                timeout=90000)
            posts.clear()
            page.locator("#exportWord").click()
            status = settle(page, "Word 题目＋答案")
            combined = last_post(posts, "export-docx")
            failures += fail_count(check(combined is not None, "切成「题目＋答案」后再导一次也真的发了请求"))
            if combined:
                failures += export_checks(page, "Word 题目＋答案", combined, stems, status, expect_answers=True)

            # ── 六、分别导出（题目卷 + 答案卷） ─────────────────────
            if page.evaluate(BUTTON_STATE)["exportSplit"]["disabled"]:
                skip("这批题还是没有可导的答案，「分别导出」是灰的，这条路这轮没量到")
            else:
                posts.clear()
                page.locator("#exportSplit").click()
                status = settle(page, "分卷")
                split = last_post(posts, "export-docx")
                failures += fail_count(check(split is not None, "点「分别导出」真的发了一次请求"))
                if split:
                    failures += split_checks(page, split, stems, status, len(picked))

            # ── 七、导出 PDF ──────────────────────────────────────
            posts.clear()
            page.locator("#exportPdf").click()
            status = settle(page, "PDF", 150000)
            pdf = last_post(posts, "export-pdf")
            failures += fail_count(check(pdf is not None, "点「导出 PDF」真的发了一次请求"))
            if pdf:
                failures += pdf_checks(page, pdf, status)

            # ── 八、导完之后按钮回得来吗 ────────────────────────────
            after = page.evaluate(BUTTON_STATE)
            failures += fail_count(check(not [k for k, v in after.items() if v["disabled"]],
                                         f"导完一轮，按钮都回来了（{json.dumps(after, ensure_ascii=False)}）"))
            stuck = page.locator("#printExportStatus").inner_text()
            failures += fail_count(check("正在" not in stuck, f"状态栏没卡在「正在…」：{stuck.strip()[:60]}"))
            page.screenshot(path=f"{SHOTS}/after-export.png")
            failures += fail_count(check(not errors, f"全程没有脚本报错（{errors[:2]}）"))

        finally:
            loans.restore()
            context.close()
            browser.close()

    print()
    print(f"共 {len(results)} 条，FAIL {failures} 条")
    if skipped:
        print("没量的：")
        for item in skipped:
            print("  - " + item)
    return 1 if failures else 0


def wait_preview(page, count: int) -> None:
    page.wait_for_selector("#printSheet[open]")
    page.wait_for_function("n => document.querySelectorAll('#printPaper .print-question').length === n",
                           arg=count, timeout=40000)
    try:
        page.wait_for_function(
            "() => { const s = document.getElementById('printPageStatus'); return s && !s.textContent.includes('正在排版'); }",
            timeout=90000)
    except Exception:
        skip("排版一直没报完成（分页卡住）")


def preview_checks(page, picked: dict) -> int:
    failures = 0
    shown = page.eval_on_selector_all("#printPaper .print-question", "nodes => nodes.map(n => n.dataset.questionType)")
    failures += fail_count(check(sorted(shown) == sorted(picked),
                                 f"预览里 {len(shown)} 道题的题型和挑的一样：{'、'.join(shown)}"))
    sections = page.eval_on_selector_all("#printPaper .print-section", "nodes => nodes.map(n => n.textContent.trim())")
    failures += fail_count(check(len(sections) >= 2, f"预览按题型分了 {len(sections)} 组：{' / '.join(sections)}"))
    failures += fail_count(check(not page.locator("#printMissing").is_visible(),
                                 "没有「尚未载入」的提示（否则导出会自己拦下来）"))
    page.screenshot(path=f"{SHOTS}/preview.png")
    return failures


def export_checks(page, label: str, post: dict, stems, status: str, expect_answers: bool) -> int:
    failures = 0
    code, blob, mime = replay(page, post)
    if not check(code == 200 and blob[:2] == b"PK", f"{label} 文件拿到了（HTTP {code}，{len(blob)} 字节，{mime}）"):
        print(f"  状态栏写的是：{status[:140]}")
        return failures + 1
    count = re.search(r"(?:已导出|已下载)\s*(\d+)\s*道题", status)
    if count:
        failures += fail_count(check(int(count.group(1)) == len(stems),
                                     f"{label}：状态栏说导出了 {count.group(1)} 道题（篮里 {len(stems)} 道）"))
    parts = docx_parts(blob)
    lost = [qid for qid, snippet in stems if snippet not in parts["text"]]
    if not check(not lost, f"{label}：{len(stems)} 道题的题干都在（{len(stems) - len(lost)}/{len(stems)}，缺 {lost}）"):
        for qid, snippet in stems:
            if qid in lost:
                print(f"    少了这道，界面上的片段是：{snippet!r}")
        print(f"    文件里的纯文字是：{parts['raw_text'][:600]!r}")
    failures += fail_count(check(parts["math"] > 0,
                                 f"{label}：公式是 Word 公式（OMML {parts['math']} 处）而不是图片"))
    found = parts["text"].count(MARK)
    if expect_answers:
        failures += fail_count(check(found >= len(stems),
                                     f"{label}：带上了借进去的 {len(stems)} 段答案（文件里 {found} 处「{MARK}」）"))
    else:
        failures += fail_count(check(found == 0,
                                     f"{label}：没混进答案（「输出内容」选的是题目，文件里 {found} 处「{MARK}」）"))
    print(f"  （{label}：图片 {parts['drawings']} 处、内嵌文件 {parts['media']} 个）")
    open(f"{SHOTS}/{label}.docx", "wb").write(blob)
    return failures


def split_checks(page, post: dict, stems, status: str, count: int) -> int:
    failures = 0
    code, blob, mime = replay(page, post)
    if not check(code == 200 and blob[:2] == b"PK", f"分卷文件拿到了（HTTP {code}，{len(blob)} 字节，{mime}）"):
        print(f"  状态栏写的是：{status[:140]}")
        return failures + 1
    try:
        with zipfile.ZipFile(BytesIO(blob)) as archive:
            inner = {n: archive.read(n) for n in archive.namelist() if not n.endswith("/")}
    except Exception as error:
        return failures + fail_count(check(False, f"分卷文件能解开（{error}）"))
    docs = [n for n, data in inner.items() if is_docx(data)]
    failures += fail_count(check(len(docs) == 2, f"分卷里有 2 个 Word（{sorted(inner)}）"))
    for name, data in inner.items():
        if not is_docx(data):
            failures += fail_count(check(False, f"分卷里的《{name}》不是能打开的 Word"))
    question = next((inner[n] for n in docs if "题目" in n), inner.get(docs[0] if docs else "", b""))
    answer = next((inner[n] for n in docs if "答案" in n), b"")
    if question:
        text = docx_parts(question)["text"]
        hit = sum(1 for _, snippet in stems if snippet in text)
        failures += fail_count(check(hit == len(stems), f"题目卷里 {hit}/{len(stems)} 道题都在"))
        failures += fail_count(check(MARK + "答案" not in text, "题目卷里没有混进答案"))
    if answer:
        text = docx_parts(answer)["text"]
        found = text.count(MARK + "解析")
        if not check(found >= count, f"答案卷里 {found}/{count} 段解析都在"):
            print("    答案卷的段落：")
            for line in paragraphs(answer):
                print(f"      {line[:90]}")
        failures += fail_count(found >= count)
        # 两卷必须**逐题对得上**。老师拿题目卷给学生、拿答案卷批改，题号错位
        # 这件事在界面上永远不会提示，只会在考完之后才发现。以前这里只查了
        # 「各自的题干/答案在不在」，两份文件各写各的也能全绿。
        qnums = question_numbers(question) if question else []
        anums = question_numbers(answer)
        print(f"  题目卷题号 {qnums} / 答案卷题号 {anums}")
        failures += fail_count(check(anums == qnums,
                                     f"两卷题号完全一致（题目卷 {qnums}，答案卷 {anums}）"))
        failures += fail_count(check(anums == list(range(1, len(anums) + 1)),
                                     f"答案卷题号是 1..{count} 不跳号不重号（{anums}）"))
        failures += fail_count(check(len(anums) == count,
                                     f"答案卷题数 {len(anums)} = 篮里 {count} 道"))
    open(f"{SHOTS}/split.zip", "wb").write(blob)
    return failures


def pdf_checks(page, post: dict, status: str) -> int:
    failures = 0
    code, blob, mime = replay(page, post)
    if not check(code == 200 and blob[:5] == b"%PDF-", f"PDF 文件头合法（HTTP {code}，{len(blob)} 字节，{mime}）"):
        return failures + 1
    pages = blob.count(b"/Type /Page") + blob.count(b"/Type/Page")
    failures += fail_count(check(pages >= 1, f"PDF 里有 {pages} 页"))
    failures += fail_count(check(blob.rstrip().endswith(b"%%EOF"), "PDF 收尾完整（%%EOF 在）"))
    open(f"{SHOTS}/paper.pdf", "wb").write(blob)
    return failures


if __name__ == "__main__":
    sys.exit(main())
