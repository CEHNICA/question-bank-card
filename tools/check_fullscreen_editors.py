"""全屏改字 / 全屏答案解析：点保存 → 读 API → 改回去。

界面看着对不算数。这里真的点保存，再回头读后端确认文字落库了，
最后把原文改回去。只对开发服务器（真库副本）跑。

用法： python tools/check_fullscreen_editors.py --url http://127.0.0.1:8802 --paper <uuid>
"""

import argparse
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
MARK = "（全屏保存往返验证）"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8802")
    parser.add_argument("--paper", required=True)
    args = parser.parse_args()
    url = args.url.rstrip("/")
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local server is allowed")

    from playwright.sync_api import sync_playwright, expect

    failures, checks = [], []

    def ok(name, condition, detail=""):
        checks.append(f"{'PASS' if condition else 'FAIL'}  {name}{'' if condition else '  ' + detail}")
        if not condition:
            failures.append(name)

    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1'); localStorage.setItem('qb-lens','0')")
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                      ("127.0.0.1", "localhost") else route.abort())
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        # ---------------------------------------------------------- 改字
        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")
        # 卡片上的 data-id 就是后端那道题的 id。
        card = page.locator(".card:not(.compact)").first
        question_id = card.get_attribute("data-id")
        before = page.request.get(f"{url}/api/papers/{args.paper}").json()
        record = next(q for q in before["questions"] if str(q["id"]) == str(question_id))
        original_stem = record["stem"]

        card = page.locator(f'.card[data-id="{question_id}"]')
        card.get_by_role("button", name="改字", exact=True).click()
        expect(page.locator(".card.editing .editor")).to_be_visible()
        stem = page.locator(".card.editing .stem-input")
        stem.fill(f"{original_stem}{MARK}")
        page.wait_for_timeout(250)
        ok("改字：保存条在顶栏", page.locator(".card.editing .editor-bar").get_by_role("button", name="保存", exact=True).count() == 1)
        ok("改字：左栏试卷列表已隐藏", page.locator(".sidebar").is_hidden())
        page.locator(".card.editing .editor-bar").get_by_role("button", name="保存", exact=True).click()
        page.wait_for_timeout(1500)
        ok("改字：保存后退出全屏", page.locator(".card.editing").count() == 0)
        ok("改字：左栏试卷列表回来了", page.locator(".sidebar").is_visible())
        saved = page.request.get(f"{url}/api/papers/{args.paper}").json()
        now_stem = next(q["stem"] for q in saved["questions"] if str(q["id"]) == str(question_id))
        ok("改字：题干真的写进了后端", now_stem == f"{original_stem}{MARK}", f"got {now_stem[-40:]!r}")

        # 改回去（保存后题卡会重画，重新按 id 找一次）
        page.goto(f"{url}/?paper={args.paper}")
        page.wait_for_load_state("networkidle")
        page.locator(f'.card[data-id="{question_id}"]').get_by_role("button", name="改字", exact=True).click()
        expect(page.locator(".card.editing .stem-input")).to_be_visible()
        page.locator(".card.editing .stem-input").fill(original_stem)
        page.locator(".card.editing .editor-bar").get_by_role("button", name="保存", exact=True).click()
        page.wait_for_timeout(1500)
        restored = page.request.get(f"{url}/api/papers/{args.paper}").json()
        ok("改字：原文改回去了",
           next(q["stem"] for q in restored["questions"] if str(q["id"]) == str(question_id)) == original_stem)

        # ---------------------------------------------------------- 答案解析
        # 挑一道已经有答案解析的题：往返之后能原样存回去，不会给这份库留验证垃圾
        page.goto(f"{url}/library")
        page.wait_for_load_state("networkidle")
        pick = page.evaluate("""() => {
            const card = [...document.querySelectorAll('.library-card')]
              .find(c => c.textContent.includes('已保存答案解析'));
            return card ? card.querySelector('.library-card-select') : null; }""")
        if not pick:
            raise SystemExit("题库里没有已保存答案解析的题，跳过答案解析这一段")
        pick.check(force=True)
        page.wait_for_timeout(120)
        page.locator("#addSelected").click()
        page.wait_for_timeout(250)
        page.locator("#basketButton").click()
        page.wait_for_load_state("networkidle")
        page.locator("#managePrintAnswers").click()
        expect(page.locator(".answer-editor-dialog[open]")).to_be_visible()
        page.wait_for_timeout(700)

        publication = page.evaluate("""() => {
            const row = document.querySelector('.answer-list-row.active');
            return row ? (row.querySelector('input') || {}).getAttribute('aria-label') : null; }""")
        original_answer = page.input_value("#answerEditorResult")
        ok("答案解析：保存条在顶栏",
           page.evaluate("""() => { const bar = document.querySelector('.answer-editor-bar');
             return Boolean(bar.querySelector('#answerEditorSave') && bar.querySelector('#answerEditorSync')); }"""))
        ok("答案解析：底部没有悬浮保存条", page.locator(".answer-editor-footer").count() == 0)
        ok("答案解析：题号在顶栏的横排带里",
           page.evaluate("""() => { const strip = document.querySelector('.answer-editor-strip');
             return Boolean(strip && strip.querySelector('.answer-editor-list')); }"""))

        page.fill("#answerEditorResult", f"{original_answer}{MARK}" if original_answer else MARK)
        page.wait_for_timeout(300)
        page.locator("#answerEditorSave").click()
        page.wait_for_timeout(1600)
        status = page.text_content(".answer-editor-status") or ""
        ok("答案解析：保存后对话框还在（可以继续改下一题）", page.locator(".answer-editor-dialog[open]").count() == 1)
        ok("答案解析：状态行说已保存", "已保存" in status, f"状态是 {status!r}")

        # 关掉再打开：读回来的必须是刚才存进去的那一份
        page.locator(".answer-editor-bar .button-quiet").click()
        page.wait_for_timeout(600)
        if page.locator(".answer-editor-dialog[open]").count():
            page.locator("#confirmOk").click()
            page.wait_for_timeout(500)
        page.locator("#managePrintAnswers").click()
        expect(page.locator(".answer-editor-dialog[open]")).to_be_visible()
        page.wait_for_timeout(800)
        reloaded = page.input_value("#answerEditorResult")
        ok("答案解析：重开后答案还在（真的落了库）", MARK in reloaded, f"读到 {reloaded[-40:]!r}")
        print(f"  当前题 {page.text_content('.answer-editor-place')}；状态 {status!r}")

        # 改回去，别把验证标记留在草稿里
        page.fill("#answerEditorResult", original_answer)
        page.wait_for_timeout(250)
        page.locator("#answerEditorSave").click()
        page.wait_for_timeout(1400)
        restored_status = page.text_content(".answer-editor-status") or ""
        ok("答案解析：原文改回去了", "已保存" in restored_status, f"状态是 {restored_status!r}")
        toasts = page.evaluate("""() => [...document.querySelectorAll('.toast')].map(n => n.textContent)""")
        if toasts:
            print("  toast:", toasts)

        ok("没有脚本报错", not errors, str(errors))
        browser.close()

    for line in checks:
        print(" ", line)
    print(f"\n{len(checks) - len(failures)}/{len(checks)} 通过")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
