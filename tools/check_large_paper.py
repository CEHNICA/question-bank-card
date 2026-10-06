"""组一份大卷子：篮里塞很多题，预览、导出、按钮状态会怎样。

老师一周攒一份卷子，二三十题很正常；期中考卷一上百题也不稀奇。
导出请求有个 2 MiB 的上限（超出直接 413「请减少选题、分批导出」），那——
篮子里到底能放多少道题？到顶之前界面上有没有一个字提醒过？

这一条量的是「大」这个维度以前没量过：预览会不会卡、按钮是不是灰的、
状态栏说了什么、有没有那条 2 MiB 的预警。
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SIZES = [1, 20, 60, 120]

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> bool:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)
    return bool(ok)


def all_ids() -> list[str]:
    ids, offset = [], 0
    while True:
        with urllib.request.urlopen(f"{BASE}/api/library?limit=100&offset={offset}") as response:
            body = json.load(response)
        ids += [item["id"] for item in body["items"]]
        offset += 100
        if len(ids) >= body["total"] or not body["items"]:
            return ids


STATE = """() => {
  // 带小问的题会拆成好几块，同一个 id 出现好几次 —— 数块会把「20 题」量成 22。
  const blocks = [...document.querySelectorAll('#printPaper .print-question')];
  const ids = new Set(blocks.map(b => b.dataset.questionId).filter(Boolean));
  const shown = (id) => { const n = document.getElementById(id); return n && !n.hidden; };
  return {
    blocks: blocks.length,
    unique: ids.size,
    basket: document.querySelector('#basketCount')?.textContent?.trim() || '',
    missing: document.querySelector('#printMissing')?.hidden === false,
    status: (document.querySelector('#printStatus')?.textContent || '').replace(/\\s+/g, ' ').trim(),
    exportStatus: (document.querySelector('#printExportStatus')?.textContent || '').replace(/\\s+/g, ' ').trim(),
    // 导出 PDF 被灰掉时，老师该在哪儿看到原因。这几块就是候选。
    layoutNoticeShown: shown('printLayoutNotice'),
    layoutNotice: (document.querySelector('#printLayoutNotice')?.textContent || '').replace(/\\s+/g, ' ').trim(),
    layoutWarningsShown: shown('printLayoutWarnings'),
    layoutWarnings: (document.querySelector('#printLayoutWarnings')?.textContent || '').replace(/\\s+/g, ' ').trim(),
    pageStatus: (document.querySelector('#printPageStatus')?.textContent || '').replace(/\\s+/g, ' ').trim(),
    buttons: Object.fromEntries(['printButton', 'exportPdf', 'exportWord', 'exportSplit', 'managePrintAnswers']
      .map(id => { const n = document.getElementById(id); return [id, n ? n.disabled : null]; })),
    basketCount: (document.querySelector('#basketSummary')?.textContent || '').replace(/\\s+/g, ' ').trim(),
    paperChars: (document.querySelector('#printPaper')?.textContent || '').length
  };
}"""


def open_print(page) -> None:
    if not page.locator("#basketButton").is_visible():
        page.locator("#basketHandle").click()
        page.wait_for_timeout(600)
    page.locator("#basketButton").click()
    page.wait_for_selector("#printSheet[open]", timeout=20000)


def main() -> int:
    ids = all_ids()
    print(f"题库一共 {len(ids)} 道题，大的那一档只能拿这么多去试")
    if len(ids) < 60:
        print("题库太小，这一条量不到")
        return 2

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        page = browser.new_context(viewport={"width": 1600, "height": 1000}).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/library", wait_until="domcontentloaded")
        page.wait_for_selector("#typeFilters button")
        page.wait_for_timeout(1200)

        for size in SIZES:
            take = ids[:size]
            page.evaluate("""(list) => { localStorage.setItem('qb-basket', JSON.stringify(list));
                localStorage.setItem('qb-basket-seen', '1'); }""", take)
            started = time.time()
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#typeFilters button")
            page.wait_for_timeout(900)
            open_print(page)
            # 预览是异步的：等题真的铺开，或者明确说「载入不全」。
            try:
                page.wait_for_function(
                    "n => { const b = document.querySelectorAll('#printPaper .print-question');"
                    " const ids = new Set([...b].map(x => x.dataset.questionId).filter(Boolean));"
                    " const miss = document.querySelector('#printMissing');"
                    " return ids.size === n || (miss && !miss.hidden); }", arg=len(take), timeout=60000)
            except Exception:
                pass
            elapsed = round(time.time() - started, 1)
            # 排版是异步的：大卷子要点一下才铺开，不等它落定就读状态，读到的是
            # 「正在排版……那个中间态 —— 看起来像按钮坏了，其实只是还没排完。
            # 这一步量的是**要等多久**以及等完之后到底成不成立。
            try:
                page.wait_for_function(
                    "() => { const n = document.querySelector('#printPageStatus');"
                    " const t = n ? n.textContent : '';"
                    " return t && !t.includes('正在排版'); }", timeout=120000)
            except Exception:
                print(f"  ⚠ 排版 120 秒还没结束")
            settle = round(time.time() - started, 1)
            state = page.evaluate(STATE)
            print(f"\n篮里 {size} 题：铺开用了 {elapsed}s，排版落定到 {settle}s，纸上 {state['unique']} 道（{state['blocks']} 块，小问会拆块）")
            print(f"  状态栏：{state['status'][:130]}")
            print(f"  页码状态：{state['pageStatus'][:120]}")
            print(f"  篮摘要：{state['basketCount'][:110]}")
            print(f"  按钮：{json.dumps(state['buttons'], ensure_ascii=False)}")
            check(not state["missing"], f"{size} 题：没有「尚未载入」的题")
            check(state["unique"] == size, f"{size} 题：纸上真的铺了 {state['unique']} 道")
            # 「分别导出」没有答案时本来就该灰，那不是缺陷。题卷/打印/补答案必须随时可点。
            for name in ("printButton", "exportWord", "managePrintAnswers"):
                check(state["buttons"][name] is False, f"{size} 题：「{name}」点得动")
            if state["buttons"]["exportPdf"]:
                # PDF 灰了就得让老师知道为什么，以及还能走哪条路。
                # 注意 buttons 里存的是 n.disabled：true 才是「灰了」。
                explained = (state["layoutNoticeShown"] and state["layoutNotice"]) or \
                            (state["layoutWarningsShown"] and state["layoutWarnings"]) or \
                            state["status"] or state["pageStatus"]
                check(bool(explained), f"{size} 题：导出 PDF 灰了，界面上找得到原因")
                print(f"  版式提示块：{'显示' if state['layoutNoticeShown'] else '不显示'} | {state['layoutNotice'][:120]}")
                print(f"  版式警告块：{'显示' if state['layoutWarningsShown'] else '不显示'} | {state['layoutWarnings'][:160]}")
                print(f"  状态栏：{state['status'][:120]}")
                check(state["buttons"]["exportWord"] is False, f"{size} 题：灰掉 PDF 之后 Word 还能走")
            check(state["paperChars"] > size * 20, f"{size} 题：纸上确实有内容（{state['paperChars']} 字）")
            if size >= 120 and not state["buttons"]["exportPdf"]:
                # 真正导一次。导出请求有 2 MiB 上限，超了直接 413「请减少选题、分批
                # 导出」—— 大卷子到底导不导得出来、扛不扛得到，这是「组一份大卷子」
                # 这个问题里唯一还没量过的一环。
                posts: list[dict] = []
                page.on("request", lambda r: posts.append({"route": r.url, "body": r.post_data})
                        if "/export-docx" in r.url else None)
                before = len(posts)
                page.locator("#exportWord").click()
                try:
                    page.wait_for_function(
                        "() => { const n = document.querySelector('#printExportStatus');"
                        " const t = n ? n.textContent : '';"
                        " return t && (t.includes('已导出') || t.includes('失败') || t.includes('未能')"
                        " || t.includes('超过') || t.includes('请')); }", timeout=180000)
                except Exception:
                    print("  ⚠ 导出 180 秒没有给出结果")
                got = posts[before:]
                print(f"  导出请求：{len(got)} 次，载荷 {len(got[0]['body'] or '') if got else 0} 字符"
                      f"（上限 2097152）" if got else "  导出请求：0 次")
                line = (page.locator("#printExportStatus").inner_text() or "").strip()
                print(f"  导出结果：{line[:150]}")
                check(bool(got), f"{size} 题：点「导出 Word」真的发出了请求")
                if got:
                    body = got[0]["body"] or ""
                    check(len(body) <= 2 * 1024 * 1024,
                          f"{size} 题：请求体 {len(body)} 字符，没撞上 2 MiB 上限")
                # 报错里那个「第 N 题」是**篮子里第 N 个**，卷面是按题型重排过的。
                # 这两个不是同一道题的话，老师按 N 去卷子上找会找错 —— 那是唯一一条
                # 告诉他哪道题坏了的信息。
                named = re.search(r"(?:选题)?第\s*(\d+)\s*题", line)
                if named:
                    number = int(named.group(1))
                    # 报错说的 N，卷面上第 N 道题印的号就该是 N。
                    # 之前先按篮子顺序去取第 N 个再找它在哪 —— 那是另一个顺序，
                    # 量出来「对不上」其实一半是自己量错的。
                    truth = page.evaluate("""(n) => {
                      const seen = new Set(); const order = [];
                      for (const b of document.querySelectorAll('#printPaper .print-question')) {
                        const id = b.dataset.questionId;
                        if (id && !seen.has(id)) { seen.add(id); order.push(b); }
                      }
                      const block = order[n - 1];
                      return {total: order.length, shown: block ? (block.querySelector('.qb-number')?.textContent || '').trim() : null};
                    }""", number)
                    print(f"  报错说的「第 {number} 题」，卷面上第 {number} 道印的是：{truth}")
                    check(truth["shown"] == f"{number}.",
                          f"报错说的题号 = 卷面上那道题印的号（{truth['shown']!r}）")
                else:
                    print("  （这次没报出题号，这条量不到）")
                # 契约不是「一定导得出来」：一道题的公式转不成可编辑 Word 公式时，
                # 整卷拒绝导出是**故意的**（悄悄印一份公式坏掉的卷子更糟）。真正的
                # 契约是：要么导出来，要么失败时说清是哪道题、为什么、还能走哪条路。
                done = "已导出" in line or "已下载" in line
                # 拒绝导出有好几种说法：公式转不成可编辑公式是一种，
                # 配图文件在库里却已经不在是另一种。两种都算数 ——
                # 只要说清了是哪道题、为什么，就不是「点了没反应」。
                refused = bool(named) and any(word in line for word in
                                              ("未能导出", "暂不能准确转换", "文件缺失", "配图"))
                check(done or refused,
                      f"{size} 题：要么导出来、要么失败时说清是哪道题（{line[:60]}）")
                if refused:
                    # 「还能走哪条路」不等于一定要提打印：公式转不动时可以改用打印或
                    # PDF，配图文件不在时唯一能走的是把图补上/重切。
                    # 只要说得出下一步该做什么，就算数。
                    check(any(word in line for word in ("打印", "PDF", "请检查", "重新")),
                          f"{size} 题：失败时说了下一步怎么办（{line[-40:]}）")
            page.keyboard.press("Escape")
            page.wait_for_timeout(600)
            page.evaluate("() => localStorage.removeItem('qb-basket')")

        check(not errors, f"全程没有脚本报错（{errors[:2]}）")
        browser.close()

    print()
    passed = sum(1 for ok, _ in results if ok)
    print(f"{passed}/{len(results)} 过")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
