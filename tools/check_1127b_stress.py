"""1.12.7 第二批：右侧篮抽屉 + 改字换题方向。模拟真人点击，不看测试文件的脸色。

核心两条：
  1. 连点「下一题」4 次题号必须逐一 +1，连点「上一题」4 次必须逐一 -1
     —— 改之前「上一题」是往前走的。
  2. 篮是右边缘一条常驻把手 + 抽屉面板，顶栏和导航抽屉里都不再有第二份。
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = "http://127.0.0.1:8803"
SHOT = Path(r"C:\Users\Administrator\question-bank-card\tmp\accept-1127b")
SHOT.mkdir(parents=True, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -> " + str(detail)) if detail and not ok else ""))


GEO = """() => {
  const b = s => { const n = document.querySelector(s); if (!n) return null;
    const r = n.getBoundingClientRect(); const cs = getComputedStyle(n);
    return {t:Math.round(r.top), l:Math.round(r.left), r:Math.round(r.right), b:Math.round(r.bottom),
      w:Math.round(r.width), h:Math.round(r.height), disp: cs.display, hidden: n.hidden}; };
  return { vw: innerWidth, vh: innerHeight,
    topbar: b('.topbar'), brand: b('.brand'), tools: b('.topbar-tools'),
    toolButtons: [...document.querySelectorAll('.topbar-tools > *')].filter(n => getComputedStyle(n).display !== 'none').length,
    handle: b('.basket-handle'), panel: b('.basket-panel'),
    basketButton: b('#basketButton'), rail: b('.library-rail'), seam: b('.rail-collapse'),
    card: b('.library-card'),
    rightGap: (() => { const c = document.querySelector('.library-card'); return c ? Math.round(innerWidth - c.r) : null; })(),
    handleCount: (document.querySelector('#basketHandleCount')||{}).textContent,
    rows: document.querySelectorAll('#basketList .basket-row').length,
    open: document.body.classList.contains('library-basket-open'),
    focus: document.body.classList.contains('library-focus-mode'),
    scrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
}"""

DRAWER = """() => ({
  groups: [...document.querySelectorAll('.site-drawer-group')].filter(s => !s.hidden).map(s => s.dataset.group),
  texts: [...document.querySelectorAll('.site-drawer')].map(n => n.innerText.replace(/\\s+/g,' ').slice(0,200))
})"""

EDIT = """() => { const ed = document.querySelector('.card.editing'); if (!ed) return null;
  const fig = ed.querySelector('.editor-preview .qb-figure');
  const fcs = fig ? getComputedStyle(fig, '::after') : null;
  return { title: (ed.querySelector('.editor-bar-count')||{}).textContent||'',
    stem: ((ed.querySelector('.stem-input')||{}).value||'').replace(/\\s+/g,' ').slice(0,40),
    figBorder: fig ? getComputedStyle(fig).borderTopWidth : null,
    figLabel: fcs ? fcs.content : null,
    prevDisabled: [...ed.querySelectorAll('.editor-bar button')].find(b=>b.textContent.includes('上一题'))?.disabled,
    nextDisabled: [...ed.querySelectorAll('.editor-bar button')].find(b=>b.textContent.includes('下一题'))?.disabled }; }"""


def number(text):
    # 「第 12 题 / 共 4 题」只取前一个数：后面那个是总题数，拼起来就成了 124。
    import re
    m = re.search(u'第\s*(\d+)\s*题', text)
    return int(m.group(1)) if m else None


def run(pg):
    ids = None
    print("\n=== 1. 右侧篮子：收起时只留一条把手")
    pg.goto(URL + "/library?b=1", wait_until="load"); pg.wait_for_timeout(2200)
    ids = pg.evaluate("() => fetch('/api/library?limit=5').then(r=>r.json()).then(j=>j.items.map(i=>i.id))")
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", ids)
    pg.reload(wait_until="load"); pg.wait_for_timeout(2600)
    g = pg.evaluate(GEO)
    check("把手可见且贴着右边缘", g["handle"] and g["handle"]["w"] <= 30 and g["handle"]["r"] >= g["vw"] - 24, g["handle"])
    check("收起时篮面板不占位", not g["open"] and g["panel"]["hidden"], g["panel"])
    check("顶栏没有任何篮按钮", g["toolButtons"] == 0, g["toolButtons"])
    check("把手上显示题数", g["handleCount"] == "5", g["handleCount"])
    pg.screenshot(path=str(SHOT / "basket-closed.png"))

    print("\n=== 2. 展开：列表、组卷预览、把手位置不变")
    spots = []
    pg.click(".basket-handle"); pg.wait_for_timeout(500)
    g = pg.evaluate(GEO)
    check("展开后面板出现且有列表", g["open"] and not g["panel"]["hidden"] and g["rows"] > 0, g)
    check("「组卷预览」在篮里面", g["basketButton"] and not g["basketButton"]["hidden"], g["basketButton"])
    check("组卷预览不在顶栏", not g["basketButton"] or g["basketButton"]["t"] > 48, g["basketButton"])
    spots.append((g["handle"]["l"], g["handle"]["t"]))
    pg.screenshot(path=str(SHOT / "basket-open.png"))
    pg.click("#basketButton"); pg.wait_for_timeout(2200)
    check("点组卷预览能打开组卷窗口", pg.evaluate("() => Boolean(document.querySelector('#printSheet[open]'))"))
    pg.click("#closePrint"); pg.wait_for_timeout(600)
    pg.click(".basket-handle"); pg.wait_for_timeout(400)
    pg.click(".basket-handle"); pg.wait_for_timeout(400)
    g = pg.evaluate(GEO); spots.append((g["handle"]["l"], g["handle"]["t"]))
    pg.click(".rail-collapse"); pg.wait_for_timeout(400); _g = pg.evaluate(GEO); spots.append((_g["handle"]["l"], _g["handle"]["t"]))
    check("三个状态下把手位置不变", len({s[0] for s in spots}) == 1, spots)
    pg.click(".rail-collapse"); pg.wait_for_timeout(400)

    print("\n=== 3. 篮空时把手仍在")
    pg.evaluate("() => localStorage.setItem('qb-basket','[]')"); pg.reload(wait_until="load"); pg.wait_for_timeout(2400)
    g = pg.evaluate(GEO)
    check("篮空时把手还在（写着 0）", g["handle"] and g["handle"]["w"] > 0 and g["handleCount"] == "0", g)
    pg.click(".drawer-trigger"); pg.wait_for_timeout(500)
    d = pg.evaluate(DRAWER)
    check("导航抽屉里没有试题篮了", "basket" not in d["groups"], d["groups"])
    check("抽屉里其它组还在", len(d["groups"]) >= 2, d["groups"])
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("\n=== 4. 七个宽度零横向溢出")
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", ids)
    for w in [1536, 1366, 1100, 1001, 979, 820, 390]:
        pg.set_viewport_size({"width": w, "height": 900})
        pg.goto(URL + "/library?bw=" + str(w), wait_until="load"); pg.wait_for_timeout(1600)
        g = pg.evaluate(GEO)
        check("%d 宽无横向溢出" % w, g["scrollX"] == 0, g["scrollX"])
    pg.set_viewport_size({"width": 1366, "height": 900})

    print("\n=== 5. 窄屏：篮抽屉和筛选浮层不同时开")
    pg.set_viewport_size({"width": 820, "height": 900})
    pg.goto(URL + "/library?n=1", wait_until="load"); pg.wait_for_timeout(2400)
    pg.click(".basket-handle"); pg.wait_for_timeout(400)
    check("篮抽屉打开", pg.evaluate("() => document.body.classList.contains('library-basket-open')"))
    pg.click("#libraryFilterToggle"); pg.wait_for_timeout(500)
    both = pg.evaluate("() => [document.body.classList.contains('library-basket-open'), document.body.classList.contains('rail-open')]")
    check("开筛选时篮抽屉自动收起", both == [False, True], both)

    print("\n=== 6. 改字：连点下一题/上一题，方向必须对")
    pg.set_viewport_size({"width": 1536, "height": 900})
    pg.goto(URL + "/", wait_until="load"); pg.wait_for_timeout(2500)
    if pg.locator("#welcomeDialog[open]").count():
        pg.evaluate("() => document.querySelector('#welcomeDialog').close()"); pg.wait_for_timeout(400)
    # 换一份真有配图的卷：示例试卷里没有图，角标那条断言就落空了。
    picked = None
    links = pg.locator("#paperList .paper-link").count()
    for i in range(links):
        pg.locator("#paperList .paper-link").nth(i).click(); pg.wait_for_timeout(1800)
        n = pg.locator("#cards .card:not(.compact)").count()
        if n < 3: continue
        # 卷内也要扫：配图不一定长在第一道题上（几何卷的图常在后面几题）。
        # 只看第一张卡会得出「这份库里没有带配图的题卡」这种错的结论。
        for c in range(min(n, 4)):
            pg.locator("#cards .card:not(.compact) button", has_text="改字").nth(c).click(); pg.wait_for_timeout(1600)
            has_figure = bool(pg.evaluate("() => !!document.querySelector('.editor-preview .qb-figure')"))
            if has_figure:
                picked = (i, n, True)
                break
            pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        if picked: break
    print("     扫了 %d 份卷，选中的卷序号/题卡数/有配图: %s" % (links, picked))
    # 扫完全部卷还是没配图的题卡：这一节测不了，如实说，别让它变成一段 TypeError。
    # 写死「前 8 份卷」选卷子会随卷增删失效 —— 挑到没图的卷时，失败报的是
    # 「TypeError: NoneType」，看着像代码坏了，其实只是这一节没测到。
    if not (picked and picked[2]):
        print("     SKIP 这份库里没有带配图的题卡：第 6 节（改字上下题方向、图上角标）测不了")
        # 扫描时点开的「全屏看题」弹窗还开着的话，它会盖住整页，
        # 下一节点任何按钮都会超时 —— 收尾时顺手关掉，别让跳过变成后面的假故障。
        for dialog_id in ("#viewerDialog", "#pageDialog", "#editorDialog"):
            if pg.locator(dialog_id + "[open]").count():
                pg.evaluate("(id) => document.querySelector(id).close()", dialog_id)
                pg.wait_for_timeout(300)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    else:
        # 点到头为止：按钮自己会灰，那说明走到了最后一道，不是 bug。
        seq = [number(pg.evaluate(EDIT)["title"])]
        for k in range(6):
            st = pg.evaluate(EDIT)
            if st["nextDisabled"]: break
            pg.click("button:has-text('下一题')"); pg.wait_for_timeout(1500)
            seq.append(number(pg.evaluate(EDIT)["title"]))
        print("     连点下一题：", seq)
        check("连点下一题，题号逐一 +1", len(seq) >= 2 and all(b - a == 1 for a, b in zip(seq, seq[1:])), seq)
        back = [seq[-1]]
        for k in range(6):
            st = pg.evaluate(EDIT)
            if st["prevDisabled"]: break
            pg.click("button:has-text('上一题')"); pg.wait_for_timeout(1500)
            back.append(number(pg.evaluate(EDIT)["title"]))
        print("     连点上一题：", back)
        check("连点上一题，题号逐一 -1", len(back) >= 2 and all(b - a == -1 for a, b in zip(back, back[1:])), back)
        check("来回数一遍回到同一题", back[-1] == seq[0], (seq[0], back[-1]))
        # 图的断言要趁还停在那张带图的题卡上做：往后翻到别的题，图就没了。
        e = pg.evaluate(EDIT)
        check("预览里原卷裁片有角标和细边", e["figBorder"] not in (None, "0px") and "原卷" in str(e["figLabel"]), e)
        pg.screenshot(path=str(SHOT / "edit-figure.png"))
        # 「上一题」灰掉的断言要真走到头才有意义。走到头指的是当前这一屏题卡的第一张，
        # 不是题号 1 —— 回收站里的题和折叠起来的题不在这一屏里。
        for k in range(30):
            if pg.evaluate(EDIT)["prevDisabled"]: break
            pg.click("button:has-text('上一题')"); pg.wait_for_timeout(900)
        first = pg.evaluate(EDIT)
        check("走到第一张题卡后「上一题」灰掉", bool(first["prevDisabled"]), first)
        pg.keyboard.press("Escape"); pg.wait_for_timeout(700)
        check("Esc 能退出全屏改字", pg.evaluate("() => !document.querySelector('.card.editing')"))

    # 1.12.7d：点过工具栏开关之后焦点赖在按钮上，Enter 变成「再点一次开关」。
    # 老师报的现象是「按 Enter 竟然全屏了」——全屏那个开关吃掉了本该「通过这道题」的键。
    print("\n=== 7. 审核工具栏：点过开关之后 Enter 仍然是「通过」")
    pg.set_viewport_size({"width": 1440, "height": 900})
    pg.goto(URL + "/?t=1", wait_until="load"); pg.wait_for_timeout(2500)
    if pg.locator("#welcomeDialog[open]").count():
        pg.evaluate("() => document.querySelector('#welcomeDialog').close()"); pg.wait_for_timeout(400)
    for i in range(min(6, pg.locator("#paperList .paper-link").count())):
        pg.locator("#paperList .paper-link").nth(i).click(); pg.wait_for_timeout(2000)
        if pg.evaluate("() => document.querySelectorAll('.card').length") >= 6: break
    pg.click("#cards .card"); pg.wait_for_timeout(800)
    # 点题卡可能弹出「全屏看题」，它盖住整页，之后任何按钮都点不动。
    # 这一节要按的是审核工具栏，所以先把浮层收干净，点不到时才能说是真的坏了。
    for dialog_id in ("#viewerDialog", "#pageDialog", "#editorDialog"):
        if pg.locator(dialog_id + "[open]").count():
            pg.evaluate("(id) => document.querySelector(id).close()", dialog_id)
            pg.wait_for_timeout(300)

    def where():
        return pg.evaluate("""() => ({ fs: document.documentElement.classList.contains('review-fullscreen'),
          card: document.activeElement?.classList?.contains?.('card') || false,
          id: document.activeElement?.dataset?.id || document.activeElement?.tagName })""")

    pg.click("#fullscreenToggle"); pg.wait_for_timeout(700)
    check("进全屏时焦点已经交回题卡", where()["card"] and where()["fs"], where())
    pg.click("#fullscreenToggle"); pg.wait_for_timeout(700)
    before = where()
    pg.keyboard.press("Enter"); pg.wait_for_timeout(1500)
    after = where()
    check("退出全屏后按 Enter 不会又全屏", not after["fs"], after)
    check("按 Enter 换到了下一道题（等于通过）", after["id"] != before["id"], (before, after))
    for tid, name in [("#focusToggle", "专注"), ("#lensToggle", "放大镜"), ("#toolsMenu summary", "工具")]:
        pg.click(tid); pg.wait_for_timeout(500)
        if tid == "#toolsMenu summary":
            pg.click(tid); pg.wait_for_timeout(500)
        mid = where()
        pg.keyboard.press("Enter"); pg.wait_for_timeout(1400)
        done = where()
        check("点过「%s」后 Enter 仍然是换题，不是全屏" % name, not done["fs"] and done["id"] != mid["id"], (mid, done))
    pg.screenshot(path=str(SHOT / "enter-after-toggle.png"))


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
    for name in FAIL: print("  - " + name)
print("页面报错：", errs[:5] if errs else "无")
sys.exit(1 if FAIL or errs else 0)
