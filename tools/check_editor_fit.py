"""「改字」编辑器压测：矮窗口下还能不能改完存下来，预览是不是真的在更新。

改字是用得最多的一个操作（一道题的字错了就点它），但它也是唯一一个**占满整屏**的界面
——顶栏收起来、页面不滚、左右两栏各自滚。这类全屏布局最容易在矮窗口上出事：保存按钮
被挤到屏幕外，用户改了半天发现存不了，或者以为是应用卡死了。

所以量四件事：
1. 保存按钮和顶栏那几个控件在视口里、点得到（不是"DOM 里有"就算过）。
2. 每个输入框滚到它面前都点得到 —— 只能改一半的编辑器等于不能用。
3. **实时预览是真在更新**：在题干里打一个标记，预览里必须出现同一个标记。
   「预览 · 随输入实时更新」这行字写在界面上，得有人验它不是骗人的。
4. Esc 取消之后题面一个字都没变（比对接口，不靠肉眼）。
   这一步只取消不保存，不动数据。
"""

from __future__ import annotations

import json
import sys
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"
MARK = "零伍柒标记"

SIZES = [(1920, 1080), (1366, 768), (1280, 600), (1000, 560)]

results: list[tuple[bool, str]] = []
problems: list[str] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


def api(path: str):
    request = urllib.request.Request(f"{BASE}{path}", headers={"X-QB-Request": "1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


# 顶栏控件 + 每个输入框：滚到面前，再问点不点得到。
#
# 跳过不可见的元素要用 `checkVisibility()`，**不能靠 getBoundingClientRect 的宽高**：
# 折叠着的 `<details>`（没答案的题，「答案与解析」就是折着的）里那些字段，
# Chrome 用 `content-visibility: hidden` 跳过渲染 —— 它们**有布局盒**（宽高都不为零），
# 但不绘制、也打不到。我一开始就是照宽高量的，结果把「答案」输入框和「解析」文本域
# 报成两个「点不到」，白查半天。
PROBE = """(root) => {
  const bad = [];
  let total = 0, skipped = 0;
  const items = [...root.querySelectorAll('button, input, select, textarea, summary')];
  for (const item of items) {
    if (!item.checkVisibility({checkVisibilityCSS: true, contentVisibilityAuto: true})) { skipped++; continue; }
    const r0 = item.getBoundingClientRect();
    if (r0.width < 1 || r0.height < 1) { skipped++; continue; }
    const label = (item.textContent || item.getAttribute('aria-label') || item.placeholder || item.tagName).trim().slice(0, 12);
    total++;
    item.scrollIntoView({block: 'center', inline: 'nearest'});
    const r = item.getBoundingClientRect();
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    if (cy < 0 || cy > window.innerHeight || cx < 0 || cx > window.innerWidth) {
      bad.push({label, why: `滚到面前还在屏外（y=${Math.round(cy)}，视口高 ${window.innerHeight}）`});
      continue;
    }
    const hit = document.elementFromPoint(cx, cy);
    if (hit !== item && !item.contains(hit)) {
      bad.push({label, why: `被 ${hit ? hit.tagName + '.' + String(hit.className || '').split(' ')[0] : '（打不到）'} 压住`});
    }
  }
  return {total, bad, skipped};
}"""


def open_editor(page) -> str | None:
    """点第一张能改的卡的「改字」，返回那道题的题号。"""
    card = page.locator(".card:not(.compact)").first
    card.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    card.locator("button", has_text="改字").first.click()
    page.wait_for_timeout(600)
    if not page.locator("form.editor").count():
        return None
    return card.locator(".qnum").first.inner_text().strip()


def run(page, width: int, height: int) -> None:
    tag = f"{width}×{height}"
    page.set_viewport_size({"width": width, "height": height})
    page.goto("about:blank")
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector(".card:not(.compact) button", timeout=20000)
    page.wait_for_timeout(1500)
    if page.locator("#welcomeDialog[open]").count():
        page.locator("#welcomeSkip").first.click()
        page.wait_for_timeout(400)

    number = open_editor(page)
    if not number:
        check(False, f"{tag}：点「改字」没打开编辑器，这一项没量到")
        return
    print(f"--- {tag}：改的是第 {number} 题 ---")

    # 1 + 2：控件和输入框点不点得到。改字的操作栏是 .editor-bar（顶栏那条），
    # 卡片自己的 .card-head 在改字时是 display:none，拿它量只会量到一堆看不见的东西。
    data = page.evaluate(PROBE, page.locator("form.editor").element_handle())
    top = page.evaluate("""() => {
      const bar = document.querySelector('.card.editing .editor-bar');
      if (!bar) return null;
      return [...bar.querySelectorAll('button, select')].map((b) => {
        const r = b.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
        return {label: (b.textContent || b.tagName).trim().slice(0, 8),
                y: Math.round(r.top),
                ok: hit === b || b.contains(hit)};
      });
    }""")
    check(bool(top) and any(t["label"] == "保存" and t["ok"] for t in (top or [])),
          f"{tag}：保存按钮在操作栏里、点得到（y={next((t['y'] for t in (top or []) if t['label'] == '保存'), '?')}，视口高 {height}）"
          if top else f"{tag}：改字的操作栏（.editor-bar）没找到，这一项没量到")
    if top:
        stuck = [t for t in top if not t["ok"]]
        for t in stuck:
            problems.append(f"{tag} · 改字操作栏的「{t['label']}」点不到")
        check(not stuck,
              f"{tag}：改字操作栏 {len(top)} 个控件全都点得到" if not stuck
              else f"{tag}：改字操作栏有 {len(stuck)} 个控件点不到")
    for item in data["bad"]:
        problems.append(f"{tag} · 改字里的「{item['label']}」{item['why']}")
    check(not data["bad"],
          f"{tag}：编辑器里 {data['total']} 个可见控件滚到面前都点得到（折叠起来的 {data['skipped']} 个不算）"
          if not data["bad"]
          else f"{tag}：编辑器里有 {len(data['bad'])}/{data['total']} 个控件点不到")

    # 3：实时预览是不是真在更新
    stem = page.locator("form.editor textarea").first
    before = page.locator(".editor-preview").inner_text()
    stem.click()
    stem.type(MARK, delay=12)
    page.wait_for_timeout(500)
    after = page.locator(".editor-preview").inner_text()
    check(MARK in after and MARK not in before,
          f"{tag}：在题干里打「{MARK}」，预览里出现了同一段字"
          if MARK in after else f"{tag}：预览没有跟着更新（打进去的是「{MARK}」，预览里没有）")
    page.screenshot(path=f"{SHOTS}/editor_{width}x{height}.png")

    # 4：改了字之后按 Esc —— 不能直接把编辑器关掉丢改动，要先问一句。
    #    这一步只取消不保存，不动数据。判据是「问没问」和「问完之后字还在不在」，
    #    不是「编辑器关没关」——没保存的改动本来就不该被一声不吭地丢掉。
    qid = page.evaluate("() => document.querySelector('.card.editing')?.dataset.id")
    card_text_before = page.evaluate(
        "(id) => document.querySelector(`.card[data-id=\"${id}\"] .rendered`)?.textContent || ''", qid)
    page.keyboard.press("Escape")
    page.wait_for_timeout(700)
    check(page.locator("#confirmDialog[open]").count() == 1,
          f"{tag}：改了字按 Esc，先问一句要不要丢")
    focused = page.evaluate("() => { const d = document.querySelector('#confirmDialog[open]'); "
                            "if (!d) return ''; return (document.activeElement?.textContent || '').trim(); }")
    check("继续编辑" in focused,
          f"{tag}：确认框默认落在「继续编辑」上（现在落在「{focused}」）")
    # 按文字精确点。`.get_by_role("button").last` 是取对话框里最后一个按钮，
    # 而焦点落在「继续编辑」上、它未必是最后一个 —— 我这么写过一回，
    # 结果点到了「丢弃改动」，把编辑器关了，后面全错。
    page.locator("#confirmDialog button", has_text="继续编辑").first.click()
    page.wait_for_timeout(500)
    check(page.locator("form.editor").count() == 1, f"{tag}：选「继续编辑」后编辑器还开着")
    check(MARK in page.locator("form.editor textarea").first.input_value(),
          f"{tag}：选「继续编辑」后刚才打的字还在")
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    if page.locator("#confirmDialog[open]").count():
        page.locator("#confirmDialog button", has_text="丢弃改动").first.click()
    page.wait_for_timeout(800)
    check(page.locator("form.editor").count() == 0, f"{tag}：选「丢弃改动」之后编辑器关掉了")
    card_text_after = page.evaluate(
        "(id) => document.querySelector(`.card[data-id=\"${id}\"] .rendered`)?.textContent || ''", qid)
    check(MARK not in card_text_after and card_text_after == card_text_before,
          f"{tag}：丢弃之后题面一个字都没变"
          if card_text_after == card_text_before
          else f"{tag}：丢弃之后题面变了（标记还在：{MARK in card_text_after}）")


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            for width, height in SIZES:
                run(page, width, height)
        finally:
            browser.close()
    if problems:
        print("\n问题明细：")
        for line in problems:
            print("  · " + line)
    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
