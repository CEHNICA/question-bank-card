"""只用键盘走一遍录入终审页：焦点落在哪、看不看得见。

「专注」模式（默认开着）把非当前题压到 34% 不透明度。鼠标点一下别的卡，那张卡会
被点亮，所以看不出来；但**只用键盘时没人点亮它**——1.13.5 之前 Tab 会一路走过别的卡的
「改字」「配图」「更多」「标记通过」，焦点圈跟着一起只剩 34%，屏幕上几乎看不出焦点
在哪（实测 Tab 60 次有 27 次落在这种地方）。这等于「不用鼠标」这个用法在默认状态下
基本没法用。

顺带查两件同类的事：
- 焦点元素有没有被浮层压住（elementFromPoint 打不回它自己）。
- 焦点落点是不是「看不见」：1×1 的隐藏 file 输入框是有意藏起来的，但它上面的
  拖放区必须有可见的焦点提示，否则 Tab 过去等于凭空消失。
"""

from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8803"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
SHOTS = "tmp/tagsys"

STEPS = 60

results: list[tuple[bool, str]] = []
problems: list[str] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))
    print(("PASS  " if ok else "FAIL  ") + label)


DESCRIBE = """() => {
  const el = document.activeElement;
  if (!el || el === document.body) return {tag: 'BODY', nowhere: true};
  const r = el.getBoundingClientRect();
  // 有效不透明度 = 自己到根之间所有 opacity 的乘积。焦点圈是画在元素自己的
  // 外框上，所以这个乘积才是用户真正看到的浓度。
  let op = 1, n = el;
  while (n && n.nodeType === 1) { op *= parseFloat(getComputedStyle(n).opacity) || 1; n = n.parentElement; }
  const cs = getComputedStyle(el);
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const inView = r.width > 0 && r.height > 0 && cy >= 0 && cy <= window.innerHeight;
  const hit = inView ? document.elementFromPoint(cx, cy) : null;

  // 焦点提示不一定画在 focused 元素自己身上，有两处是**故意**画在别处的：
  // - 上传入口是个 1×1 的隐藏 file 框，提示由祖先 .drop-zone 的 :focus-within 画；
  // - 「一份试卷 / 一本书讲义」那两个是真 input，只是 opacity: 0，提示由
  //   `label:has(input:focus-visible)` 画在整段标签上。
  // 两种情况打回来的都是「自己的 label 里的东西」，不是被别的东西压住。
  // 所以统一问一句：**自己的 label（或 label 的祖先）有没有画出可见的焦点提示**。
  const drawnElsewhere = (node) => {
    for (let a = node; a && a !== document.body; a = a.parentElement) {
      const acs = getComputedStyle(a);
      const ring = (acs.outlineStyle !== 'none' && parseFloat(acs.outlineWidth) > 0)
                || (acs.boxShadow && acs.boxShadow !== 'none');
      if (ring && (a.tagName === 'LABEL' || a.id === 'dropZone')) return a.className || a.tagName;
    }
    return '';
  };
  const ownLabel = el.closest('label') || (hit && hit.closest ? hit.closest('label') : null);
  const coveredByOwn = !!(ownLabel && hit && ownLabel.contains(hit));
  const shownBy = coveredByOwn ? drawnElsewhere(ownLabel) : '';
  if (!shownBy && el.classList.contains('visually-hidden')) shownBy = drawnElsewhere(el.parentElement);

  return {
    tag: el.tagName + (el.id ? '#' + el.id : ''),
    cls: String(el.className || '').split(/\\s+/).slice(0, 2).join('.'),
    text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 14),
    w: Math.round(r.width), h: Math.round(r.height),
    op: Math.round(op * 100) / 100,
    hiddenInput: el.classList.contains('visually-hidden'),
    shown: !!shownBy, shownBy,
    covered: inView && !(hit === el || el.contains(hit)) && !shownBy,
    coveredBy: inView && hit ? (hit.tagName + (hit.id ? '#' + hit.id : '') +
                 (hit.className ? '.' + String(hit.className).trim().split(/\\s+/)[0] : '')) : '',
  };
}"""


def run(page) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto("about:blank")
    page.goto(f"{BASE}/", wait_until="networkidle")
    page.wait_for_selector("details.more", timeout=20000)
    page.wait_for_timeout(1800)
    if page.locator("#welcomeDialog[open]").count():
        page.locator("#welcomeSkip").first.click()
        page.wait_for_timeout(400)

    check(page.locator("#focusToggle").get_attribute("aria-pressed") == "true",
          "专注模式默认开着（这是最坏情况，也是默认情况）")

    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Escape")
    page.wait_for_timeout(250)

    stops = []
    for _ in range(STEPS):
        page.keyboard.press("Tab")
        # 卡片透明度有 .22s 过渡，量早了会读到中间值——这不是真问题，是动画还没走完。
        page.wait_for_timeout(280)
        stops.append(page.evaluate(DESCRIBE))

    check(all(not s.get("nowhere") for s in stops), f"Tab {STEPS} 次，每一步都有落点")

    dim = [s for s in stops if 0 < s["op"] < 0.5]
    for s in dim:
        problems.append(f"焦点落在被压暗的地方（不透明度 {s['op']}）：{s['tag']}.{s['cls']}「{s['text']}」")
    check(not dim,
          f"Tab {STEPS} 次，没有一步落在被压暗的卡片上（之前有 {len(dim)} 步）"
          if not dim else
          f"Tab {STEPS} 次，有 {len(dim)} 步落在被压暗的卡片上（不透明度 0.34）")

    invisible = [s for s in stops if s["w"] < 2 or s["h"] < 2 or s["op"] == 0]
    bad_hidden = [s for s in invisible if not s["shown"]]
    for s in bad_hidden:
        problems.append(f"焦点落在一个看不见的东西上：{s['tag']}.{s['cls']}「{s['text']}」"
                        f" {s['w']}×{s['h']}")
    check(not bad_hidden,
          "看不见的落点都把焦点提示画在了看得见的 label 上" if not bad_hidden
          else f"有 {len(bad_hidden)} 个落点既看不见、也没有任何可见的焦点提示")

    # 焦点提示画在自己 label 上、或画在可见祖先上的，不算被浮层压住（见 DESCRIBE）。
    covered = [s for s in stops if s.get("covered")]
    for s in covered:
        problems.append(f"焦点被浮层压住：{s['tag']}.{s['cls']}「{s['text']}」被 {s['coveredBy']} 盖住")
    check(not covered,
          f"Tab {STEPS} 次，没有一步的焦点被浮层压住" if not covered
          else f"有 {len(covered)} 步的焦点被浮层压住")

    # 截一张：Tab 停在别的卡的「配图」上时，焦点圈必须看得见。
    for _ in range(STEPS):
        d = page.evaluate(DESCRIBE)
        if d.get("cls", "").startswith("button") and d.get("text") in ("改字", "配图", "更多"):
            page.screenshot(path=f"{SHOTS}/keyboard_focus_{d['text']}.png")
            print(f"截图：焦点在「{d['text']}」上")
            break
        page.keyboard.press("Tab")
        page.wait_for_timeout(280)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=CHROME, headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        try:
            run(page)
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
