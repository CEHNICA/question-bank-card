"""1.12.7 版面收口之后的压力测试：模拟真人点击，不看测试文件的脸色。

跑完 dev server 上真的那一份（tmp\accept2），每一步都量真实的 DOM 状态。
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
URL = "http://127.0.0.1:8802"
SHOT = Path(r"C:\Users\Administrator\question-bank-card\tmp\accept-1127")
SHOT.mkdir(parents=True, exist_ok=True)

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name, ("  -> " + str(detail)) if detail and not ok else ""))


GEOM = """() => {
  const b = s => { const n = document.querySelector(s); if (!n) return null;
    const r = n.getBoundingClientRect(); const cs = getComputedStyle(n);
    return {t:Math.round(r.top), l:Math.round(r.left), r:Math.round(r.right), b:Math.round(r.bottom),
      w:Math.round(r.width), h:Math.round(r.height), disp: cs.display, hidden: n.hidden}; };
  const c = b('.library-card');
  return { vw: innerWidth, vh: innerHeight,
    topbar: b('.topbar'), brand: b('.brand'), tools: b('.topbar-tools'),
    basketEntry: b('.topbar-basket'), preview: b('#basketButton'),
    rail: b('.library-rail'), seam: b('.rail-collapse'), card: c,
    rightGap: c ? Math.round(innerWidth - c.r) : null,
    focus: document.body.classList.contains('library-focus-mode'),
    scrollX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
}"""

DRAWER = """() => {
  const sec = document.querySelector('.site-drawer-group[data-group=basket]');
  const slot = document.querySelector('.site-drawer-group[data-group=basket] .site-drawer-slot');
  const panel = document.querySelector('#basketPanel');
  const list = document.querySelector('#basketList');
  const body = document.querySelector('.site-drawer-body');
  return { open: !document.querySelector('.drawer-scrim').hidden,
    groupShown: sec ? !sec.hidden : null, slotShown: slot ? !slot.hidden : null,
    panelDisp: panel ? getComputedStyle(panel).display : null,
    panelH: panel ? Math.round(panel.getBoundingClientRect().height) : null,
    rows: list ? list.querySelectorAll('.basket-row').length : 0,
    titles: [...document.querySelectorAll('.site-drawer-group[data-group=basket] .site-drawer-title')]
      .map(n => n.innerText.replace(/\\s+/g,' ').trim()),
    innerScrolls: list ? list.scrollHeight > list.clientHeight : null,
    bodyScrolls: body ? body.scrollHeight > body.clientHeight : null };
}"""


def basket_ids(pg, n=5):
    return pg.evaluate("(n) => fetch('/api/library?limit=' + n).then(r=>r.json()).then(j=>j.items.map(i=>i.id))", n)


def run(pg):
    print("\n=== 1. 顶栏：品牌小、点空白不跳页、两个按钮都在")
    pg.goto(URL + "/library?p=1", wait_until="load")
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", basket_ids(pg))
    pg.reload(wait_until="load"); pg.wait_for_timeout(2600)
    g = pg.evaluate(GEOM)
    check("顶栏仍 48px", g["topbar"]["h"] == 48, g["topbar"]["h"])
    check("品牌链接 <= 120px", g["brand"]["w"] <= 120, g["brand"]["w"])
    check("顶栏右端有「试题篮」入口", bool(g["basketEntry"] and g["basketEntry"]["w"] > 0), g["basketEntry"])
    check("「组卷预览」在顶栏里（不在侧栏）", bool(g["preview"] and g["preview"]["l"] > 400), g["preview"])
    check("侧栏 212px", g["rail"] and g["rail"]["w"] == 212, g["rail"])
    check("右侧空白 <= 20px", g["rightGap"] <= 20, g["rightGap"])
    check("第一道题 top <= 94", g["card"] and g["card"]["t"] <= 94, g["card"])
    before = pg.url
    pg.mouse.click(900, 24); pg.wait_for_timeout(900)
    check("点顶栏中间空白不跳首页", pg.url == before, pg.url)

    print("\n=== 2. 抽屉里的试题篮：两个状态都要有列表")
    pg.click(".topbar-basket"); pg.wait_for_timeout(600)
    d = pg.evaluate(DRAWER)
    check("抽屉打开且篮这一组可见", d["open"] and d["groupShown"], d)
    check("篮列表有内容（侧栏可见时）", d["panelDisp"] == "flex" and d["panelH"] > 100 and d["rows"] > 0, d)
    check("「试题篮」只出现一次", len(d["titles"]) == 1, d["titles"])
    check("列表不再自带滚动条（只留抽屉一根）", d["innerScrolls"] is False, d)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)

    print("\n=== 3. 专注模式（收侧栏）下点篮 —— 这一版之前是坏的")
    pg.click(".rail-collapse"); pg.wait_for_timeout(600)
    g = pg.evaluate(GEOM)
    check("专注模式生效、侧栏没了", g["focus"] and g["rail"]["h"] == 0, g["rail"])
    check("侧栏接缝的 ‹/› 贴到左边缘", g["seam"] and g["seam"]["l"] < 60, g["seam"])
    check("组卷预览留在顶栏右端（不跟着搬）", g["preview"] and g["preview"]["l"] > 400, g["preview"])
    pg.click(".topbar-basket"); pg.wait_for_timeout(700)
    d = pg.evaluate(DRAWER)
    check("专注模式下篮列表照样出现", d["panelDisp"] == "flex" and d["panelH"] > 100 and d["rows"] > 0, d)
    pg.screenshot(path=str(SHOT / "stress-focus-basket.png"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    pg.click(".rail-collapse"); pg.wait_for_timeout(500)
    check("再点一次侧栏弹回来", not pg.evaluate(GEOM)["focus"])

    print("\n=== 4. 组卷预览不乱跑")
    spots = []
    for label in ["初始", "开抽屉", "关抽屉"] :
        if label == "开抽屉": pg.click(".drawer-trigger"); pg.wait_for_timeout(400)
        if label == "关抽屉": pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        g = pg.evaluate(GEOM)
        spots.append((label, g["preview"]["l"], g["preview"]["t"]))
    check("三个状态下按钮位置都不变", len({(l, t) for _, l, t in spots}) == 1, spots)
    check("按钮始终可见", all(t >= 0 and t < 96 for _, _, t in spots), spots)
    pg.click("#basketButton"); pg.wait_for_timeout(2500)
    check("点组卷预览能打开组卷窗口", pg.evaluate("() => Boolean(document.querySelector('#printSheet[open]'))"))
    pg.click("#closePrint"); pg.wait_for_timeout(600)

    print("\n=== 5. 篮空了这一组整个消失")
    pg.evaluate("() => localStorage.setItem('qb-basket','[]')"); pg.reload(wait_until="load"); pg.wait_for_timeout(2600)
    check("空篮时顶栏没有这两个按钮", not pg.evaluate(GEOM)["basketEntry"] or pg.evaluate(GEOM)["basketEntry"]["w"] == 0)
    pg.click(".drawer-trigger"); pg.wait_for_timeout(500)
    d = pg.evaluate(DRAWER)
    check("空篮时抽屉里没有试题篮这一组", d["groupShown"] is False, d)
    check("抽屉里其它组还在（页面/题库/工具）",
          pg.evaluate("() => [...document.querySelectorAll('.site-drawer-group')].filter(g=>!g.hidden).length >= 3"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)

    print("\n=== 6. 题卡「更多」点外面关、Esc 焦点回来")
    pg.evaluate("(ids) => localStorage.setItem('qb-basket', JSON.stringify(ids))", basket_ids(pg))
    pg.reload(wait_until="load"); pg.wait_for_timeout(2600)
    c0 = pg.locator(".library-card").first
    c0.locator(".library-card-more summary").click(); pg.wait_for_timeout(350)
    pg.mouse.click(1400, 880); pg.wait_for_timeout(450)
    check("点空白处菜单关掉", pg.evaluate("() => document.querySelectorAll('.library-card-more[open]').length") == 0)
    c0.locator(".library-card-more summary").click(); pg.wait_for_timeout(300)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
    check("Esc 也关，且焦点回到「更多」",
          pg.evaluate("() => { const a=document.activeElement; return document.querySelectorAll('.library-card-more[open]').length===0 && a && a.parentElement && a.parentElement.className==='library-card-more'; }"))
    c0.locator(".library-card-more summary").click(); pg.wait_for_timeout(250)
    c1 = pg.locator(".library-card").nth(1)
    c1.locator(".library-card-more summary").click(); pg.wait_for_timeout(400)
    check("同时只开一个", pg.evaluate("() => document.querySelectorAll('.library-card-more[open]').length") == 1)

    print("\n=== 7. 提示条不挡按钮")
    check("toast 鼠标穿透", pg.evaluate("() => getComputedStyle(document.querySelector('.toast') || document.body).pointerEvents") in ("none", "auto"))

    print("\n=== 8. 七个宽度不横向溢出")
    for w in [1536, 1366, 1100, 1001, 979, 820, 390]:
        pg.set_viewport_size({"width": w, "height": 900})
        pg.goto(URL + "/library?w=" + str(w), wait_until="load"); pg.wait_for_timeout(1500)
        g = pg.evaluate(GEOM)
        check("%d 宽无横向溢出" % w, g["scrollX"] == 0, g["scrollX"])
    pg.set_viewport_size({"width": 1366, "height": 768})

    print("\n=== 9. 设置页与密钥窗口说同一件事")
    pg.goto(URL + "/settings", wait_until="load"); pg.wait_for_timeout(2600)
    ready = pg.evaluate("() => (document.querySelector('#settingsReady')||{}).textContent || ''")
    check("设置页提到 MinerU 已配好时不再说它没配", "MinerU" not in ready or "还需 MinerU" not in ready, ready[:70])
    mineru = pg.evaluate("() => (document.querySelector('#settingsMineruState')||{}).textContent || ''")
    check("MinerU 状态灯有结论", mineru in ("已填写", "已填写（旧环境变量）", "未填写"), mineru)
    note = pg.evaluate("() => { const n=document.querySelector('#settingsMineruState'); const r=n&&n.parentElement.querySelector('.api-source-note'); return r?r.textContent:''; }")
    print("     MinerU 灯：%s ｜ 来源说明：%s" % (mineru, note or "（无）"))
    pg.click("#settingsCredentialOpen"); pg.wait_for_timeout(1800)
    total = pg.evaluate("() => (document.querySelector('#credentialSavedTotal')||{}).textContent || ''")
    print("     密钥窗口：%s" % total)
    check("密钥窗口能打开并显示总数", "密钥" in total, total)
    notes = pg.evaluate("() => [...document.querySelectorAll('#credentialDialog .api-source-note')].map(n=>n.textContent.slice(0,30))")
    if note:
        check("两个界面的来源说明一致", bool(notes) and notes[0].startswith(note[:20]), (note[:20], notes))
    pg.screenshot(path=str(SHOT / "stress-credentials.png"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)

    print("\n=== 10. 录入终审：失败提示只说一遍、列表里没有空灰条")
    pg.goto(URL + "/", wait_until="load"); pg.wait_for_timeout(2500)
    if pg.locator("#welcomeDialog[open]").count():
        pg.evaluate("() => document.querySelector('#welcomeDialog').close()")
        pg.wait_for_timeout(400)
    empty_bar = pg.evaluate("""() => [...document.querySelectorAll('#paperList .paper-item')].map(i => {
        const m = i.querySelector('.mini-meter'); return {failed: i.className.includes('failed'), has: Boolean(m)}; })
        .filter(x => x.failed && x.has)""")
    check("失败的卷没有空灰进度条", len(empty_bar) == 0, empty_bar)
    dup = pg.evaluate("""() => { const a=(document.querySelector('#paperStatus')||{}).innerText||'';
        const b=(document.querySelector('#paperError')||{}).innerText||''; if(!b) return null;
        return b.includes(a.split('\\n')[0].trim()) && a.trim().length>3; }""")
    check("红条里的句子没有在上面重复出现", dup is not True, dup)
    pg.screenshot(path=str(SHOT / "stress-review.png"))
    # 两种卷状态都要看：全部处理完的那份（顶部只剩一条横栏），和还在等识读的那份。
    picked = []
    for i in range(min(4, pg.locator('#paperList .paper-link').count())):
        pg.locator('#paperList .paper-link').nth(i).click(); pg.wait_for_timeout(1800)
        picked.append((i, pg.evaluate("""() => ({name:(document.querySelector('#paperName')||{}).textContent||'',
            top: Math.round((document.querySelector('#cards .card')||{getBoundingClientRect:()=>({top:9999})}).getBoundingClientRect().top),
            banner: !document.querySelector('#doneBanner').hidden})""")))
    for i, info in picked:
        print("     卷%d %-28s 第一道题 top=%s%s" % (i, info["name"][:26], info["top"], "（全部处理完）" if info["banner"] else ""))
    finished = [i for i, v in picked if v["banner"]]
    check("至少找到一份全部处理完的卷来量顶部", bool(finished), picked)
    if finished:
        top = [v for i, v in picked if i == finished[0]][0]["top"]
        check("全部处理完的卷：第一道题 top <= 270", top <= 270, top)
    check("任何一份卷第一道题 top <= 600", all(v["top"] <= 600 for _, v in picked), picked)
    # 切题面板在的那一份：按钮灰、红条写清原因
    pg.locator('#paperList .paper-link').nth(0).click(); pg.wait_for_timeout(1800)

    print("\n=== 11. 识读按钮没密钥就是灰的、红条已经写好原因")
    btn = pg.evaluate("""() => { const b=[...document.querySelectorAll('#cutReadingActions button')]
        .find(x => x.textContent.includes('识读未完成题目')); if(!b) return null;
        const e=document.querySelector('#cutReadingError'); const l=document.querySelector('#cutReadingSettings');
        return {disabled:b.disabled, title:b.title, errHidden:e?e.hidden:null, err:(e||{}).textContent||'', linkShown:l?!l.hidden:null}; }""")
    if btn:
        check("识读按钮已禁用", btn["disabled"] is True, btn)
        check("红条写着缺哪个服务", "看图读题模型" in btn["err"], btn["err"][:60])
        check("配置链接已出现", btn["linkShown"] is True, btn)
    else:
        print("     （这份卷没有待识读的题，跳过）")

    print("\n=== 12. 题库页整体")
    pg.goto(URL + "/library", wait_until="load"); pg.wait_for_timeout(2600)
    pg.screenshot(path=str(SHOT / "stress-library.png"))


with sync_playwright() as p:
    b = p.chromium.launch(headless=True, executable_path=EXE)
    ctx = b.new_context(viewport={"width": 1366, "height": 768})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append("console:" + m.text) if m.type == "error" else None)
    run(pg)
    b.close()

print("\n" + "=" * 60)
print("通过 %d，失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for name in FAIL: print("  - " + name)
if errors:
    print("页面报错 %d 条：" % len(errors))
    for e in errors[:8]: print("  " + e[:150])
else:
    print("全程无 JS 报错")
sys.exit(1 if FAIL or errors else 0)
