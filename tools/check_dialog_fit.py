"""所有弹窗的「矮窗口还能不能用」压测。

这个项目里弹窗有 20 多个（两个页面加起来）。之前只单独验过一个「标签与答案」面板，
发现它会被外层容器按内容长高、掉到视口外面 941px 处——**保存按钮在屏幕上根本找不到**。
那不是个例，是一类：弹窗自己限了高（92vh 之类），里面再套一层能滚的容器，
容器没设 `min-height: 0` 就撑开了，于是底部的按钮被推到屏幕外，而用户看不到任何提示。

做法是像用户那样**把弹窗真的打开**（点触发它的那个按钮，不是 `showModal()`），
然后对弹窗里每个能点的控件问同一句话：*用户滚到它面前，还点不点得到？*

这里有个我踩过的坑，别再踩：别图省事改成「把所有容器一次性滚到底，然后要求所有
控件都在屏内」。列表滚到底之后上半截的条目当然在屏幕外（中心 y 是负数），也会被
吸顶的标题和搜索框盖住 —— 那全是正常滚动行为。这么量会把 79 个控件里的 40 个报成
「用户按不到」，结论直接错。真正的失败是**滚到它面前了还是点不到**。

窗口尺寸特意挑了矮的：600px 和 560px 高是真实存在的（笔记本半屏、缩放 125%、
任务栏占地方）。只在 1080 上量过就下结论「放得下」是不算的。
"""

from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

# 1080 是舒服的；768 是老本；600/560 是真实存在的矮窗口。
SIZES = [(1920, 1080), (1366, 768), (1280, 600), (1000, 560)]

results: list[tuple[bool, str]] = []
problems: list[str] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def api(path: str, payload=None):
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", "X-QB-Request": "1"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


# 逐项探测：先把控件滚到滚动区中间（这一步才是「用户滚到那儿点它」），
# 再打 elementFromPoint。打回来的不是它自己，就是滚过去了也点不到。
PROBE = """(dialog) => {
  const outside = [], covered = [];
  let total = 0;
  const items = [...dialog.querySelectorAll('button, a[href], input, select, textarea, summary, [role=menuitem], [role=button]')];
  for (const item of items) {
    const ir = item.getBoundingClientRect();
    if (ir.width < 1 || ir.height < 1) continue;
    if (getComputedStyle(item).visibility === 'hidden') continue;
    const label = (item.textContent || item.getAttribute('aria-label') || item.id || '?').trim().slice(0, 16);
    total++;
    item.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = item.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cy < 0 || cy > window.innerHeight || cx < 0 || cx > window.innerWidth) {
      outside.push({label, y: Math.round(cy)});
      continue;
    }
    const hit = document.elementFromPoint(cx, cy);
    if (hit !== item && !item.contains(hit)) {
      covered.push({label, by: hit ? (hit.tagName + (hit.id ? '#' + hit.id : '') +
                 (hit.className ? '.' + String(hit.className).trim().split(/\\s+/).slice(0,2).join('.') : '')) : '（打不到）'});
    }
  }
  const r = dialog.getBoundingClientRect();
  return {total, outside, covered,
          box: {left: Math.round(r.left), right: Math.round(r.right)},
          vw: window.innerWidth, vh: window.innerHeight,
          title: (dialog.querySelector('h2, h3, .dialog-title, header')?.textContent || dialog.id || '').trim().slice(0, 18)};
}"""


def run_dialog(page, selector: str, tag: str, shot: str | None = None) -> None:
    page.wait_for_timeout(250)
    if not page.locator(f"{selector}[open]").count():
        check(False, f"{tag}：{selector} 没打开（这一项没量到）")
        return
    data = page.evaluate(PROBE, page.locator(selector).element_handle())
    title = data["title"] or selector
    if data["total"] == 0:
        check(False, f"{tag}：{selector} 里一个可点控件都没有（多半是脚本找错了）")
        return

    if data["box"]["left"] < -1 or data["box"]["right"] > data["vw"] + 1:
        check(False, f"{tag}：{title} 横向出屏（{data['box']['left']}–{data['box']['right']}，视口宽 {data['vw']}）")
    else:
        check(True, f"{tag}：{title} 横向在屏内（{data['box']['left']}–{data['box']['right']}）")

    if data["outside"]:
        for item in data["outside"]:
            problems.append(f"{tag} · {selector}「{title}」的「{item['label']}」滚到它面前还在屏幕外"
                            f"（中心 y={item['y']}，视口高 {data['vh']}）")
        check(False, f"{tag}：{title} 有 {len(data['outside'])}/{data['total']} 个控件滚到面前还在屏外")
    else:
        check(True, f"{tag}：{title} 的 {data['total']} 个控件滚过去都露得出来")

    if data["covered"]:
        for item in data["covered"]:
            problems.append(f"{tag} · {selector}「{title}」的「{item['label']}」滚到面前还被 {item['by']} 压住")
        check(False, f"{tag}：{title} 有 {len(data['covered'])}/{data['total']} 个控件滚过去了也点不到")
    else:
        check(True, f"{tag}：{title} 的 {data['total']} 个控件全都点得到")

    if shot:
        page.screenshot(path=shot)


def close_all(page) -> None:
    page.evaluate("""() => {
      document.querySelectorAll('dialog[open]').forEach((d) => { try { d.close(); } catch (e) {} });
      document.querySelectorAll('details[open]').forEach((d) => { d.open = false; });
    }""")
    page.wait_for_timeout(200)


def open_tools(page) -> None:
    summary = page.locator("#toolsMenu > summary")
    if summary.count() and not page.evaluate("!!document.querySelector('#toolsMenu[open]')"):
        summary.first.click()
        page.wait_for_timeout(200)


# (名字, 怎么点开它)。点不开的路径不放进来——放进来只会变成「没量到」。
def triggers(page) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    # 快捷键说明：侧栏里那个按钮常常在折叠处看不见，用「?」键——真人也是这么按的。
    out.append(("快捷键说明", "KEY:?"))
    if page.locator("#archivedPapersButton").is_visible():
        out.append(("已归档的试卷", "#archivedPapersButton"))
    open_tools(page)
    if page.locator("#questionTrash").is_visible():
        out.append(("题卡回收站", "#questionTrash"))
    close_all(page)
    if page.locator(".card .crop.zoomable").count():
        out.append(("放大对照", ".card .crop.zoomable"))
    return out


MAPPING = {"快捷键说明": "#keysDialog", "已归档的试卷": "#archivedPapersDialog",
           "题卡回收站": "#trashDialog", "放大对照": "#viewerDialog"}


def run_review(page, width: int, height: int) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto("about:blank")
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector("details.more", timeout=20000)
    page.wait_for_timeout(1500)

    tag = f"{width}×{height}"
    # 欢迎弹窗和首次引导自己会弹出来，先量它们再关。
    for did in ("#welcomeDialog", "#automaticGuideDialog"):
        if page.locator(f"{did}[open]").count():
            run_dialog(page, did, tag, shot=f"{SHOTS}/dialog_{did.strip('#')}_{width}x{height}.png")
            close_all(page)
    if page.locator("#welcomeDialog[open], #automaticGuideDialog[open]").count():
        check(False, f"{tag}：关掉欢迎弹窗之后还有模态没关")
    else:
        check(True, f"{tag}：欢迎弹窗和首次引导都量过了，也都关掉了")

    for name, how in triggers(page):
        if how.startswith("KEY:"):
            page.locator("body").click(position={"x": 5, "y": 5})   # 先把焦点还给页面
            page.keyboard.press(how.split(":", 1)[1])
        else:
            open_tools(page)
            target = page.locator(how).first
            target.scroll_into_view_if_needed()
            page.wait_for_timeout(150)
            target.click()
        run_dialog(page, MAPPING[name], tag, shot=f"{SHOTS}/dialog_{MAPPING[name].strip('#')}_{width}x{height}.png")
        close_all(page)


def seed() -> tuple[str, list[str]]:
    """借一道**没标签**的题、给它挂一个标签，跑完恢复原样。

    「改」按钮只在题有标签时才出现（library.js extrasNode 里 `if ((item.tags || []).length)`
    才建），而题库里可能一道带标签的题都没有 —— 那就自己借一道，别把「没量到」
    当成通过。改之前把原标签记下来，量完原样写回去。
    """
    items = api("/api/library?limit=100")["items"]
    target = next((item for item in items if not item.get("tags")), None)
    if not target:
        raise SystemExit("题库里找不到没标签的题，先导入几道再跑这个脚本")
    original = api(f"/api/library/{target['id']}/tags")["tags"]
    api(f"/api/library/{target['id']}/tags", {"tags": ["导数的运算"]})
    return target["id"], original


def run_library(page, width: int, height: int) -> None:
    label = f"{width}×{height} 题库"
    page.set_viewport_size({"width": width, "height": height})
    page.goto("about:blank")
    page.goto(f"{BASE}/library", wait_until="networkidle")
    page.wait_for_selector("#libraryList .library-card", timeout=20000)
    page.wait_for_timeout(1200)

    # 「全屏看题」占的是 calc(100dvh - 16px)，矮窗口下最值得量；先量它，
    # 因为它一开就把整页盖住，后面的「改」按钮就点不到了。
    full = page.locator(".library-full-button")
    if full.count() and full.first.is_visible():
        full.first.click()
        page.wait_for_timeout(500)
        run_dialog(page, "dialog.question-viewer-dialog", label,
                   shot=f"{SHOTS}/dialog_question_viewer_{width}x{height}.png")
        close_all(page)
        page.wait_for_timeout(300)
    else:
        check(False, f"{label}：题库里找不到「全屏看题」按钮，这一项没量到")

    edit = page.locator("#libraryTagEdit")
    if not edit.count() or not edit.first.is_visible():
        check(False, f"{label}：借了标签还是没看到「改」按钮，这一项没量到")
        return
    edit.first.click()
    page.wait_for_timeout(400)
    run_dialog(page, "dialog.tag-editor-dialog", label,
               shot=f"{SHOTS}/dialog_tag_editor_{width}x{height}.png")
    close_all(page)


def main() -> int:
    publication, original = "", []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            for width, height in SIZES:
                run_review(page, width, height)
            publication, original = seed()
            print(f"\n--- 题库：借了一道题的标签（{publication}），量完原样还回去 ---")
            run_library(page, 1366, 768)
            run_library(page, 1280, 600)
            run_library(page, 1000, 560)
        finally:
            browser.close()
            if publication:
                api(f"/api/library/{publication}/tags", {"tags": original})
                print(f"已把 {publication} 的标签还回 {original or '（空）'}")
    if problems:
        print("\n问题明细：")
        for line in problems:
            print("  · " + line)
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
