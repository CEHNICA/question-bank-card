"""1.12.7 第三批：把碍事的东西收起来。模拟真人点击，不看测试文件的脸色。

五条：
  1. 篮子把手改成悬浮，题目右边那 26px 还给题目
  2. 抽屉里删掉源码链接和整个「操作说明」组
  3. 进了一道题不用再勾它 —— 勾选框默认是勾着的
  4. 题库页常驻的两条快捷键提示条消失，「恢复操作提示」按钮也没了
  5. 答案解析里的「历史版本」下拉删掉
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8803"
EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOT = Path(r"C:\Users\Administrator\question-bank-card\tmp\accept-1127c")
SHOT.mkdir(parents=True, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -> " + str(detail)) if detail and not ok else ""))


GEO = """() => {
  const b = s => { const n = document.querySelector(s); if (!n) return null;
    const r = n.getBoundingClientRect();
    return {t:Math.round(r.top), l:Math.round(r.left), r:Math.round(r.right), b:Math.round(r.bottom),
      w:Math.round(r.width), h:Math.round(r.height), hidden:n.hidden, disp:getComputedStyle(n).position}; };
  const card = document.querySelector('.library-card');
  return { vw: innerWidth, card: b('.library-card'), cardW: card ? Math.round(card.getBoundingClientRect().width) : null,
    cardRight: card ? Math.round(innerWidth - card.getBoundingClientRect().right) : null,
    handle: b('.basket-handle'), panel: b('.basket-panel'),
    open: document.body.classList.contains('library-basket-open'),
    rail: b('.library-rail'),
    // 题库页顶到第一道题之前所有还亮着的横条
    bars: [...document.querySelectorAll('.library-workspace > *, .topbar, .site-header')]
      .filter(n => getComputedStyle(n).display !== 'none' && n.getBoundingClientRect().height > 0)
      .map(n => ({ cls: n.className || n.tagName, h: Math.round(n.getBoundingClientRect().height) })),
    hintNodes: [...document.querySelectorAll('#libraryShortcutHint, #printShortcutHint, #libraryRestoreHints, .source-link')]
      .map(n => n.id || n.className),
    scrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
}"""

DRAWER = """() => ({
  groups: [...document.querySelectorAll('.site-drawer-group')].filter(s => !s.hidden).map(s => s.dataset.group),
  text: [...document.querySelectorAll('.site-drawer')].map(n => n.innerText.replace(/\\s+/g,' ')).join(' | '),
  body: document.body.innerText.replace(/\\s+/g,' ')
})"""

ANSWER = """() => {
  const d = document.querySelector('.answer-editor-dialog[open]');
  if (!d) return null;
  const rows = [...d.querySelectorAll('.answer-list-row')];
  const tools = d.querySelector('.question-viewer-tools');
  return {
    open: true,
    checked: rows.filter(r => r.querySelector('input')?.checked).length,
    total: rows.length,
    active: rows.findIndex(r => r.className.includes('active')),
    checkedIdx: rows.findIndex(r => r.querySelector('input')?.checked),
    historySelect: Boolean(d.querySelector('select[aria-label="已保存的答案解析历史"]')),
    originTitle: d.querySelector('.answer-origin summary')?.textContent || '',
    originSelects: d.querySelectorAll('.answer-origin select').length,
    aiDisabled: d.querySelector('#answerEditorAi')?.disabled,
    topBars: [...d.children].filter(n => getComputedStyle(n).display !== 'none').map(n => n.className),
  };
}"""

VIEWER = """() => {
  const d = document.querySelector('.question-viewer-dialog[open]');
  if (!d) return null;
  return { tools: [...d.querySelectorAll('.question-viewer-tools > *')].map(n => n.textContent.replace(/\\s+/g,' ').trim()),
           shortcutNodes: d.querySelectorAll('.question-viewer-shortcuts, .question-viewer-tip').length };
}"""

# 筛选栏：横向滚动条就是 scrollWidth 大于可见宽；「越过右缘」的元素直接点名是谁顶的。
RAIL = """() => { const r = document.querySelector('.library-rail'); const rr = r.getBoundingClientRect();
  const btns = [...document.querySelectorAll('#extraTools .button')];
  const btn = btns[btns.length - 1];
  return { cw: r.clientWidth, sw: r.scrollWidth, railW: Math.round(rr.width),
    past: [...r.querySelectorAll('*')].filter(n => n.getBoundingClientRect().right > rr.right + 1)
      .map(n => ({ cls: (n.id || n.className || n.tagName).toString().slice(0, 30),
                   over: Math.round(n.getBoundingClientRect().right - rr.right) })),
    btnW: btn ? Math.round(btn.getBoundingClientRect().width) : 0,
    btnH: btn ? Math.round(btn.getBoundingClientRect().height) : 0,
    btnRight: btn ? Math.round(btn.getBoundingClientRect().right) : 0,
    railRight: Math.round(rr.right) }; }"""


def run(pg):
    print("\n=== 1. 收起状态：题目宽度")
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.goto(URL + "/library?c=1", wait_until="load"); pg.wait_for_timeout(2600)
    g = pg.evaluate(GEO)
    closed_w = g["cardW"]
    print("     收起：题面宽 %s，右边留白 %s，把手 %s×%s (%s)" % (
        g["cardW"], g["cardRight"], g["handle"]["w"], g["handle"]["h"], g["handle"]["disp"]))
    check("题面右缘离屏幕右边只剩 20px 左右", g["cardRight"] is not None and g["cardRight"] <= 24, g["cardRight"])
    check("把手是悬浮的（fixed），不占栅格", g["handle"]["disp"] == "fixed", g["handle"])
    # 把手 26px 宽，右边留白只有 19px，所以它必然往题卡的边框上压进去几个像素。
    # 这是故意的：为了不压到内容去把右边留白加宽，代价是从题目身上再扣像素。
    # 题卡自己的内边距远大于这几像素，压到的只是边框。
    overlap = g["card"]["r"] - g["handle"]["l"]
    check("把手只压在题卡边框上（不碰内容）", overlap <= 8, (overlap, g["handle"]["l"], g["card"]["r"]))
    check("收起时篮面板不占位", not g["open"] and g["panel"]["hidden"], g["panel"])
    check("没有横向溢出", g["scrollX"] == 0, g["scrollX"])
    print("     亮着的横条：", g["bars"])
    pg.screenshot(path=str(SHOT / "01-library-collapsed.png"))

    print("\n=== 2. 展开篮：只让出第三列，收起后原样还回来")
    pg.click(".basket-handle"); pg.wait_for_timeout(600)
    g = pg.evaluate(GEO)
    check("展开后题面仍在第二列（没被挤走）", g["card"]["l"] > g["rail"]["r"], (g["card"]["l"], g["rail"]["r"]))
    check("展开后正好让出 300px 给面板", abs((closed_w - g["cardW"]) - 316) <= 2, (closed_w, g["cardW"]))
    pg.screenshot(path=str(SHOT / "02-library-basket-open.png"))
    pg.click(".basket-handle"); pg.wait_for_timeout(600)
    g = pg.evaluate(GEO)
    check("收起后题面宽度原样回来", g["cardW"] == closed_w, (closed_w, g["cardW"]))

    print("\n=== 3. 抽屉：源码和操作说明都没了")
    pg.click(".drawer-trigger"); pg.wait_for_timeout(600)
    d = pg.evaluate(DRAWER)
    check("抽屉只剩导航/题库/工具三组", d["groups"] == ["nav", "library", "tools"], d["groups"])
    check("抽屉里没有「操作说明」", "操作说明" not in d["text"], d["text"][:160])
    check("抽屉里没有源码链接", "源码" not in d["text"], d["text"][:160])
    pg.screenshot(path=str(SHOT / "03-drawer.png"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)

    print("\n=== 4. 提示条：题库页一处都不挂")
    g = pg.evaluate(GEO)
    check("题库页没有常驻提示条/恢复按钮/源码链接", g["hintNodes"] == [], g["hintNodes"])
    body = pg.evaluate("() => document.body.innerText")
    for word in ["恢复操作提示", "查看已保存的解析版本"]:
        check("页面文字里没有「%s」" % word, word not in body)
    # 设置里那份还在：提示不许真的消失，只是别挡着题
    pg.goto(URL + "/settings?s=1", wait_until="load"); pg.wait_for_timeout(1800)
    has = pg.evaluate("() => Boolean(document.querySelector('#settingsHelpKeys, .shortcut-details, [data-drawer=\"tools\"]'))")
    check("设置里仍然查得到快捷键说明", has)

    print("\n=== 5. 答案解析：进来就勾上正在看的那道题")
    pg.goto(URL + "/library?a=1", wait_until="load"); pg.wait_for_timeout(2600)
    # 「编辑答案解析」在题卡的「更多」折叠菜单里。点 summary 要幂等：已经开着就别点，
    # 再点一下反而把菜单关上了，然后后面所有断言都会说「按钮不可见」。
    more = pg.locator(".library-card-more").first
    if not more.evaluate("n => n.open"):
        more.locator("summary").click(); pg.wait_for_timeout(350)
    edit = pg.locator(".library-card-more[open] button:has-text('编辑答案解析')").first
    check("题卡「更多」里能打开答案解析", edit.count() > 0 and edit.is_visible())
    edit.click(); pg.wait_for_timeout(2400)
    a = pg.evaluate(ANSWER)
    check("能从题卡打开答案解析", bool(a))
    if a:
        print("     ", a)
        check("正在看的那道题已经勾上了", a["checked"] >= 1, a)
        check("勾上的就是当前激活的那道", a["checkedIdx"] == a["active"], (a["checkedIdx"], a["active"]))
        check("没有「已保存的答案解析历史」下拉", a["historySelect"] is False and a["originSelects"] == 0, a)
        check("折叠面板标题不再写「与历史」", a["originTitle"] == "原卷答案解析", a["originTitle"])
        pg.screenshot(path=str(SHOT / "04-answer-editor.png"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(900)

    print("\n=== 6. 组卷预览里补解析：进来就勾上要补的那道")
    # 组卷预览右栏的「补解析」把整份卷子一次丢进编辑器，不传 focus 也不传 selected ——
    # 这条路以前最吃亏：它先打开一道最缺答案的题，勾选框却是空的。
    ids = pg.evaluate("() => fetch('/api/library?limit=6').then(r=>r.json()).then(j=>j.items.map(i=>i.id))")
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", ids)
    pg.reload(wait_until="load"); pg.wait_for_timeout(2600)
    pg.click(".basket-handle"); pg.wait_for_timeout(500)
    pg.click("#basketButton"); pg.wait_for_timeout(2400)
    check("组卷预览能打开", pg.evaluate("() => Boolean(document.querySelector('#printSheet[open]'))"))
    pg.click("#managePrintAnswers"); pg.wait_for_timeout(2600)
    a = pg.evaluate(ANSWER)
    check("能打开答案解析", bool(a))
    if a:
        print("     共 %d 道题，勾了 %d 个，激活第 %d 个" % (a["total"], a["checked"], a["active"]))
        check("打开的是缺解析的那道，而且已经勾上", a["checked"] == 1 and a["checkedIdx"] == a["active"], a)
        check("没有历史版本下拉", a["historySelect"] is False, a)
        pg.screenshot(path=str(SHOT / "05-print-answer.png"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(900)
    pg.evaluate("() => document.querySelector('#printSheet[open]')?.close()"); pg.wait_for_timeout(400)

    print("\n=== 7. 全屏看题：工具栏只剩缩放")
    pg.goto(URL + "/library?v=1", wait_until="load"); pg.wait_for_timeout(2400)
    if pg.locator("button:has-text('全屏看题')").count():
        pg.locator("button:has-text('全屏看题')").first.click(force=True); pg.wait_for_timeout(2000)
        v = pg.evaluate(VIEWER)
        if v:
            print("     工具栏：", v["tools"])
            check("全屏看题里没有快捷键提示条", v["shortcutNodes"] == 0, v)
            check("工具栏只剩缩放那几件", v["tools"] == ["适合宽度", "图片原尺寸", "−", "100%", "＋"], v["tools"])
            pg.screenshot(path=str(SHOT / "05-fullscreen-viewer.png"))
            pg.keyboard.press("Escape"); pg.wait_for_timeout(600)
    else:
        check("找到「全屏看题」入口", False)

    print("\n=== 8. 七个宽度都不溢出（把手改悬浮之后最容易破的是窄屏）")
    for w in [1536, 1366, 1100, 1001, 979, 820, 560, 390]:
        pg.set_viewport_size({"width": w, "height": 900})
        pg.goto(URL + "/library?bw=" + str(w), wait_until="load"); pg.wait_for_timeout(1700)
        s = pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        check("%d 宽无横向溢出" % w, s == 0, s)
    pg.set_viewport_size({"width": 1366, "height": 900})

    # ---- 1.12.9 ----
    print("\n=== 9. 篮子把手：有题就亮，题目越多越浓")
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.goto(URL + "/library?gl=1", wait_until="load"); pg.wait_for_timeout(2500)
    every = pg.evaluate("() => fetch('/api/library?limit=50').then(r=>r.json()).then(j=>j.items.map(i=>i.id))")
    check("题库里有足够多的题可以试浓度", len(every) >= 40, len(every))

    def fill(n):
        ids = every[:n]
        pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", ids)
        pg.reload(wait_until="load"); pg.wait_for_timeout(2100)
        return pg.evaluate("""() => { const h = document.querySelector('.basket-handle');
          const cs = getComputedStyle(h);
          const before = getComputedStyle(h, '::before');
          return { n: document.querySelector('#basketHandleCount').textContent,
            fill: parseFloat(cs.getPropertyValue('--basket-fill')),
            overlay: parseFloat(before.opacity),
            shadow: cs.boxShadow }; }""")

    for n, want in [(0, 0.0), (1, 0.05), (5, 0.25), (20, 1.0), (40, 1.0)]:
        s = fill(n)
        print("     篮里 %2d 题 -> --basket-fill=%s 底色不透明度=%s" % (n, s["fill"], s["overlay"]))
        check("%d 题时浓度是 %g" % (n, want), abs(s["fill"] - want) < 0.001, s)
        check("%d 题时底色跟着浓度走" % n, abs(s["overlay"] - s["fill"]) < 0.01, s)
    one, many = fill(1), fill(20)
    check("1 题明显比 20 题淡", many["overlay"] - one["overlay"] >= 0.4, (one["overlay"], many["overlay"]))
    check("辉光跟着浓度变（阴影串不同）", one["shadow"] != many["shadow"], (one["shadow"][:40], many["shadow"][:40]))
    pg.screenshot(path=str(SHOT / "06-handle-glow.png"))

    print("\n=== 10. 专注 + 篮展开：题面必须还是整屏宽")
    pg.set_viewport_size({"width": 1650, "height": 900})
    pg.goto(URL + "/library?fo=1", wait_until="load"); pg.wait_for_timeout(2500)
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", every[:5])
    pg.reload(wait_until="load"); pg.wait_for_timeout(2400)
    pg.click(".rail-collapse"); pg.wait_for_timeout(500)          # 进专注
    check("专注已开", pg.evaluate("() => document.body.classList.contains('library-focus-mode')"))
    pg.click(".basket-handle"); pg.wait_for_timeout(700)
    geo = pg.evaluate("""() => { const ws = document.querySelector('.library-workspace');
      const r = document.querySelector('.library-results').getBoundingClientRect();
      return { cols: getComputedStyle(ws).gridTemplateColumns, resultsW: Math.round(r.width),
        wsW: Math.round(ws.getBoundingClientRect().width) }; }""")
    # 1650 − 左右各 20px 内边距 = 1610 的工作区；篮子 300 + 列间距 16 之外全归题目。
    want = 1650 - 40 - 300 - 16
    print("     列：", geo["cols"], " 题面宽：", geo["resultsW"], "（应为 %d）" % want)
    check("专注+篮展开时列不是 212px 开头", not geo["cols"].startswith("212px"), geo)
    check("专注+篮展开时题目拿走除篮子外的全部宽度", geo["resultsW"] == want, (geo, want))
    check("篮子只占 300px，中间没有空列", geo["cols"].split() == ["%dpx" % want, "300px"], geo)
    check("没有横向溢出", pg.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth") == 0)
    pg.screenshot(path=str(SHOT / "07-focus-plus-basket.png"))
    pg.click(".basket-handle"); pg.wait_for_timeout(400)
    pg.click(".rail-collapse"); pg.wait_for_timeout(400)

    print("\n=== 11. 组卷默认只出题目")
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.goto(URL + "/library?pd=1", wait_until="load"); pg.wait_for_timeout(2500)
    pg.click(".basket-handle"); pg.wait_for_timeout(500)
    pg.click("#basketButton"); pg.wait_for_timeout(2600)
    pd = pg.evaluate("""() => ({ doc: document.querySelector('#printDocument').value,
      label: document.querySelector('#printDocument').selectedOptions[0].textContent,
      layoutHidden: document.querySelector('#printAnswerLayoutBox').hidden,
      open: Boolean(document.querySelector('#printSheet[open]')) })""")
    print("     ", pd)
    check("组卷预览能打开", pd["open"])
    check("输出内容默认是「题目」", pd["doc"] == "questions" and pd["label"] == "题目", pd)
    check("答案解析位置那一行自动藏起来了", pd["layoutHidden"] is True, pd)
    # 切到「题目＋答案」时那一行要回来
    pg.select_option("#printDocument", "combined"); pg.wait_for_timeout(800)
    check("切到题目＋答案后答案解析位置那行回来",
          pg.evaluate("() => document.querySelector('#printAnswerLayoutBox').hidden") is False)
    pg.screenshot(path=str(SHOT / "08-print-default.png"))

    print("\n=== 12. 设置里那行本机切题提示没了")
    pg.goto(URL + "/settings?x=1", wait_until="load"); pg.wait_for_timeout(1800)
    gone = pg.evaluate("() => ({ el: document.querySelectorAll('#settingsReady').length, text: document.body.innerText })")
    check("设置页不再有那个提示元素", gone["el"] == 0, gone)
    check("页面文字里没有「导入时先在本机切题」", "导入时先在本机切题" not in gone["text"])
    check("服务状态列表还在（没被误删）", pg.evaluate("() => document.querySelectorAll('.api-status-list .api-state').length") >= 4)

    print("\n=== 13. 批量条吸顶")
    BULK = """() => { const b = document.querySelector('.library-bulk');
      const t = document.querySelector('.topbar');
      const r = b.getBoundingClientRect();
      return { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height),
        topbarH: Math.round(t.getBoundingClientRect().height),
        stuck: b.classList.contains('stuck'),
        varH: getComputedStyle(document.documentElement).getPropertyValue('--library-bulk-h').trim() }; }"""
    for w in [1650, 1366, 1100, 979, 820, 560, 390]:
        pg.set_viewport_size({"width": w, "height": 900})
        pg.goto(URL + "/library?bk=" + str(w), wait_until="load"); pg.wait_for_timeout(2400)
        if w <= 979:
            pg.click("#libraryFilterToggle"); pg.wait_for_timeout(600)
        at_top = pg.evaluate(BULK)
        pg.evaluate("() => window.scrollTo(0, 1200)"); pg.wait_for_timeout(700)
        far = pg.evaluate(BULK)
        # 正好等于，不是 <=：小于说明被顶栏或别的东西压住了
        check("%d 宽滚 1200 后条正好吸在顶栏下（%d）" % (w, far["topbarH"]), far["top"] == far["topbarH"], far)
        check("%d 宽滚下去时条有 .stuck" % w, far["stuck"] is True, far)
        check("%d 宽 --library-bulk-h 等于条高" % w, far["varH"] == "%dpx" % far["h"], far)
        pg.evaluate("() => window.scrollTo(0, 0)"); pg.wait_for_timeout(700)
        check("%d 宽滚回顶部后 .stuck 撤掉" % w, pg.evaluate(BULK)["stuck"] is False)
        if w <= 979:
            pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.screenshot(path=str(SHOT / "10-bulk-stuck.png"), full_page=False)

    print("\n=== 14. 跳到某道题：题顶不被吸顶的条压住")
    for w in [1650, 1366, 560, 390]:
        pg.set_viewport_size({"width": w, "height": 900})
        pg.goto(URL + "/library?jmp=" + str(w), wait_until="load"); pg.wait_for_timeout(2400)
        if w <= 979:
            pg.click("#libraryFilterToggle"); pg.wait_for_timeout(600)
        g = pg.evaluate("""() => { document.querySelectorAll('.library-card')[6]
            .scrollIntoView({ block: 'start', behavior: 'instant' });
          return null; }""")
        pg.wait_for_timeout(700)
        j = pg.evaluate("""() => { const c = document.querySelectorAll('.library-card')[6].getBoundingClientRect();
          const b = document.querySelector('.library-bulk').getBoundingClientRect();
          return { cardTop: Math.round(c.top), barBottom: Math.round(b.bottom) }; }""")
        print("     %d 宽：条底 %d，题顶 %d" % (w, j["barBottom"], j["cardTop"]))
        check("%d 宽跳过去的题没被压在条下面" % w, j["cardTop"] >= j["barBottom"], j)
        if w <= 979:
            pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("\n=== 15. 窄屏上批量条收成一行，功能没丢")
    pg.set_viewport_size({"width": 390, "height": 900})
    pg.goto(URL + "/library?nb=1", wait_until="load"); pg.wait_for_timeout(2600)
    idle = pg.evaluate(BULK)
    check("390 宽没勾选时条只有一行（%dpx）" % idle["h"], idle["h"] <= 40, idle)
    check("窄屏上状态文字收起来了",
          pg.evaluate("() => getComputedStyle(document.querySelector('#libraryStatus')).display") == "none")
    pg.evaluate("""() => { document.querySelectorAll('.library-card-select').forEach((b, i) => {
        if (i < 3) { b.checked = true; b.dispatchEvent(new Event('change', { bubbles: true })); } }); }""")
    pg.wait_for_timeout(800)
    sel = pg.evaluate("""() => ({ h: Math.round(document.querySelector('.library-bulk').getBoundingClientRect().height),
      add: !document.querySelector('#addSelected').closest('.bulk-actions').hidden
        || getComputedStyle(document.querySelector('.bulk-actions')).display !== 'none',
      n: document.querySelector('#selectionCount').textContent })""")
    print("     勾了 3 题：%dpx，%s" % (sel["h"], sel["n"]))
    check("390 宽勾了 3 题时条不超过 75px（%d）" % sel["h"], sel["h"] <= 75, sel)
    check("勾选之后批量按钮还在（不能因为收窄就把功能收没了）", sel["add"] is True, sel)
    check("勾选计数跟着变", "3" in sel["n"], sel)
    pg.screenshot(path=str(SHOT / "11-bulk-narrow.png"))
    pg.click("#clearSelection"); pg.wait_for_timeout(700)
    check("取消勾选后复原", "0" in pg.evaluate("() => document.querySelector('#selectionCount').textContent"))

    print("\n=== 16. 导出结果卡上的「打开文件 / 打开文件夹」真点一次")
    # 这两个按钮以前调的是 library.js 里根本不存在的 api()，点一下抛 ReferenceError，
    # 弹一句英文 "api is not defined"，看着就是「没反应」。98 个前端测试、1383 个后端
    # 测试全绿也没拦住 —— 没有任何一个测试点过它。
    # 断言的是「请求确实发出去了」，不是「文件夹真的打开了」：后者要么在桌面留下
    # 一个测试文件，要么弹出资源管理器，都会污染这台机器。
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.goto(URL + "/library?op=1", wait_until="load"); pg.wait_for_timeout(2600)
    ids = pg.evaluate("() => fetch('/api/library?limit=8').then(r=>r.json()).then(j=>j.items.map(i=>i.id))")
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", ids)
    pg.reload(wait_until="load"); pg.wait_for_timeout(2600)
    # 假导出：不真的生成文件，也不往磁盘写东西
    pg.evaluate("""() => { window.ExamExport = Object.assign({}, window.ExamExport, { download: async () => ({
        saved: true, filename: '练习.pdf', path: 'C:\\\\Users\\\\Test\\\\练习.pdf',
        file_token: '假凭据', question_count: 8 }) }); }""")
    opened = []
    def _route(route):
        opened.append(route.request.post_data)
        route.fulfill(status=200, content_type="application/json", body='{"opened": true}')
    pg.route("**/api/export-preferences/open", _route)
    pg.click(".basket-handle"); pg.wait_for_timeout(600)
    pg.click("#basketButton"); pg.wait_for_timeout(2600)
    pg.click("#exportPdf"); pg.wait_for_timeout(3000)
    card = pg.evaluate("""() => { const s = document.querySelector('#printExportStatus');
      return { text: s.innerText.replace(/\\s+/g,' '), buttons: [...s.querySelectorAll('button')].map(b=>b.textContent) }; }""")
    print("     导出结果卡：", card["text"][:70], "| 按钮", card["buttons"])
    check("导出结果卡上有那两个按钮", card["buttons"] == ["打开文件", "打开文件夹"], card["buttons"])
    before_errs = len(errs)
    for label, want_target, want_token in [("打开文件", "file", True), ("打开文件夹", "directory", False)]:
        opened.clear()
        pg.locator("#printExportStatus button", has_text=label).first.click()
        pg.wait_for_timeout(1200)
        body = opened[0] if opened else {}
        parsed = json.loads(body) if body else {}
        check("点「%s」发出了请求" % label, len(opened) == 1, opened)
        check("「%s」target=%s" % (label, want_target), parsed.get("target") == want_target, parsed)
        check("「%s」%s凭据" % (label, "带" if want_token else "不带"),
              ("file_token" in parsed) == want_token, parsed)
    toast = pg.evaluate("() => document.querySelector('#toast')?.innerText || ''")
    check("没有弹出报错提示", "not defined" not in toast and "错误" not in toast, toast)
    check("点这两个按钮没有抛 JS 异常", len(errs) == before_errs, errs[before_errs:])
    pg.unroute("**/api/export-preferences/open")
    pg.screenshot(path=str(SHOT / "12-export-actions.png"))

    print("\n=== 17. 筛选栏「更多筛选」展开后不出现横向滚动条")
    for w in [1650, 1366, 1100, 979, 820, 560, 390]:
        pg.set_viewport_size({"width": w, "height": 900})
        pg.goto(URL + "/library?adv=" + str(w), wait_until="load"); pg.wait_for_timeout(2400)
        if w <= 979:
            pg.click("#libraryFilterToggle"); pg.wait_for_timeout(600)
        before = pg.evaluate(RAIL)
        if pg.locator(".library-advanced summary").count():
            pg.click(".library-advanced summary"); pg.wait_for_timeout(700)
        g = pg.evaluate(RAIL)
        past = [x for x in g["past"] if "svg" not in x["cls"]]
        print("     %4d 宽 侧栏 %d：关 %d/%d  开 %d/%d  越界 %s" % (
            w, g["cw"], before["sw"], before["cw"], g["sw"], g["cw"], past or "无"))
        check("%4d 宽「更多筛选」关着时无横向溢出" % w, before["sw"] <= before["cw"], before)
        check("%4d 宽展开后无横向溢出" % w, g["sw"] <= g["cw"], g)
        check("%4d 宽没有元素越过侧栏右缘" % w, not past, past[:3])
        check("%4d 宽按钮没被切掉" % w, g["btnRight"] <= g["railRight"] and g["btnW"] <= g["cw"], g)
        if w > 979:
            check("%4d 宽按钮标签换成了两行（不是被削掉）" % w, g["btnH"] >= 36, g)
        if w <= 979:
            pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.set_viewport_size({"width": 1366, "height": 900})
    pg.screenshot(path=str(SHOT / "13-rail-advanced.png"))



with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=EXE)
    pg = b.new_context(viewport={"width": 1366, "height": 900}).new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append("console:" + m.text) if m.type == "error" else None)
    run(pg)
    b.close()

print("\n" + "=" * 60)
print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for name in FAIL:
        print("  - " + name)
print("页面报错：", errs[:5] if errs else "无")
sys.exit(1 if FAIL or errs else 0)
