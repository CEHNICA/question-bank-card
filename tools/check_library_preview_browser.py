"""Offline browser regression for the real preview UI, without Django or a database.

Serves the checkout's frontend on an ephemeral localhost port and supplies fixture
library responses. All non-local requests and every write request are rejected.
Requires Python Playwright and an installed Chrome/Edge or Playwright Chromium.
"""

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "preview-browser"


class FrontendHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path == "/library":
            self.path = "/library.html"
        super().do_GET()

    def log_message(self, *args):
        pass


def fixture_item(key, number, answer="", analysis="", ai=None, stem=None):
    return {
        "id": key, "document_id": "offline-preview", "draft_id": key,
        "source_filename": "组卷离线测试.pdf", "number": number,
        "question_type": "single_choice", "version": 1, "status": "published",
        "published_at": "2026-10-02T01:00:00Z", "has_answer": bool(answer or analysis),
        "review": {"source": "human"}, "ai_answer": ai, "jobs": [],
        "content": {"number": number, "question_type": "single_choice",
            "stem": stem or f"离线题 {number}：求 $1+1$。",
            "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
            "answer": answer, "analysis": analysis, "figures": [], "sources": []},
    }


def check():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    fixtures = [
        fixture_item("no-answer-1", 1),
        fixture_item("no-answer-2", 2, stem="长公式：$" + "+".join(["x^2"] * 28) + "=0$。"),
        fixture_item("with-answer", 3, answer="B", analysis="将 $1+1$ 相加，得到 $2$。"),
        fixture_item("analysis-only", 4, analysis="只有解析的原卷内容也应保留。"),
        fixture_item("ai-only", 5, ai={"answer": "B", "analysis": "测试用 AI 参考解析。"}),
    ]
    payload = {"items": fixtures, "total": len(fixtures), "features": {"ai_answer": True},
        "facets": {"sources": [], "types": {"single_choice": len(fixtures)},
                   "answers": {"yes": 2, "no": 3}, "reviews": {"human": len(fixtures)}, "tags": []}}
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(FrontendHandler, directory=str(ROOT / "frontend")))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    errors = []
    try:
        with sync_playwright() as pw:
            executable = next((str(path) for path in (
                Path(pw.chromium.executable_path),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ) if path.is_file()), None)
            browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            context = browser.new_context(viewport={"width": 650, "height": 950})
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))

            by_id = {item["id"]: item for item in fixtures}

            def intercept(route):
                request = route.request
                parsed = urlparse(request.url)
                # 1.13 起每次打开卷子都会先核对一遍选题快照，这里得答得上来，
                # 否则「非 GET 一律拒绝」会把这条正常的读请求当成越界。
                if parsed.path == "/api/library/batch" and request.method == "POST":
                    ids = (request.post_data_json or {}).get("ids", [])
                    route.fulfill(json={"items": [by_id[i] for i in ids if i in by_id],
                                        "missing": [{"id": i, "reason": "not_found"} for i in ids if i not in by_id]})
                    return
                if parsed.hostname != "127.0.0.1" or request.method != "GET":
                    raise AssertionError(f"Unexpected request: {request.method} {request.url}")
                if parsed.path == "/api/library":
                    route.fulfill(json=payload)
                elif parsed.path.startswith("/api/"):
                    raise AssertionError(f"Unexpected API access: {request.url}")
                else:
                    route.continue_()

            context.route("**/*", intercept)
            page.goto(f"http://127.0.0.1:{server.server_port}/library")
            expect(page.locator(".library-card")).to_have_count(len(fixtures))

            def toggle(key, selected=False):
                page.locator(f"#q-{key}").get_by_role("button", name="已在试题篮" if selected else "加入试题篮", exact=True).click()

            # 「组卷预览」在试题篮抽屉里，篮不是打开的就点不到它。
            def open_basket():
                if not page.locator("#basketPanel").is_visible():
                    page.locator("#basketHandle").click()
                    expect(page.locator("#basketPanel")).to_be_visible()

            def close_basket():
                if page.locator("#basketPanel").is_visible():
                    page.locator("#basketHandle").click()

            def preview():
                open_basket()
                page.locator("#basketButton").click()
                expect(page.locator(".print-question").first).to_be_visible()

            def close():
                page.locator("#closePrint").click()
                close_basket()

            def no_answers():
                expect(page.locator("#printAnswers")).to_be_disabled()
                expect(page.locator("#printAnswers")).not_to_be_checked()
                expect(page.locator(".print-answer-row")).to_have_count(0)

            def set_option(selector, checked):
                # 「排版与内容」是折叠的，里面那些控件收起时点不到 —— 真人得先展开。
                # 它里面还嵌了两层 details，直接找 summary 会命中 3 个。
                settings = page.locator("#printSettings")
                if not settings.evaluate("e => e.open"):
                    settings.locator("> summary").click()
                control = page.locator(selector)
                if control.is_checked() != checked:
                    page.locator("label").filter(has=control).click()

            def set_document(value):
                settings = page.locator("#printSettings")
                if not settings.evaluate("e => e.open"):
                    settings.locator("> summary").click()
                page.locator("#printDocument").select_option(value)

            toggle("no-answer-1")
            toggle("no-answer-2")
            preview()
            no_answers()
            expect(page.locator("#printAnswerStatus")).to_have_text("这些题没有答案或解析，仅输出题目卷。")
            # Controls must stay visible without hover, even beside a very long formula.
            # 每题的排序/排版按钮现在收在「调整」这个折叠里，先点开才谈得上「按钮可见」。
            first_tools = page.locator(".print-question-tools").first
            first_tools.locator("summary").click()
            expect(first_tools).to_have_attribute("open", "")
            # 改窗口之后纸面要重新缩放，读早了量到的是缩放前的宽度（650 时会读到 802）。
            # 等它稳下来再断言，稳不下来才算真出事。
            for width in (1440, 650, 390, 320):
                page.set_viewport_size({"width": width, "height": 950})
                try:
                    page.wait_for_function("""() => {
                        const sheet = document.querySelector('#printSheet');
                        return sheet.scrollWidth <= sheet.clientWidth + 1;
                    }""", timeout=6000)
                except Exception:
                    pass
                layout = page.locator("#printSheet").evaluate("""sheet => ({
                    width: sheet.clientWidth, scrollWidth: sheet.scrollWidth,
                    buttons: [...sheet.querySelectorAll('.print-question-tools button')].map(button => {
                        const rect = button.getBoundingClientRect();
                        return {left: rect.left, right: rect.right, opacity: getComputedStyle(button.parentElement).opacity};
                    })
                })""")
                assert layout["scrollWidth"] <= layout["width"] + 1, layout
                assert all(button["left"] >= 0 and button["right"] <= width + 1 and button["opacity"] == "1"
                           for button in layout["buttons"]), layout
            page.set_viewport_size({"width": 650, "height": 950})
            page.screenshot(path=str(OUTPUT / "preview-no-answers-650.png"))
            down = page.get_by_role("button", name="第 1 题下移", exact=True)
            down.focus()
            down.press("Enter")
            assert page.locator(".print-question").evaluate_all("nodes => nodes.map(node => node.dataset.questionId)") == ["no-answer-2", "no-answer-1"]
            # 按完回车焦点要跟着这道题走（分页会把整卷重排，焦点得等排完再给）。
            # 只有两道题，移到末位后「下移」本来就禁用，焦点退到这道题的「调整」。
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "第 2 题排版操作", \
                page.evaluate("() => ({label: document.activeElement.getAttribute('aria-label'), tag: document.activeElement.tagName})")
            page.get_by_role("button", name="第 2 题上移", exact=True).press("Space")
            assert page.locator(".print-question").evaluate_all("nodes => nodes.map(node => node.dataset.questionId)") == ["no-answer-1", "no-answer-2"]
            # 回到第一位，「上移」禁用，焦点同样退到「调整」。
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "第 1 题排版操作"
            page.get_by_role("button", name="第 1 题移出试题篮", exact=True).press("Enter")
            expect(page.locator(".print-question")).to_have_count(1)
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "第 1 题排版操作"
            close()
            toggle("no-answer-2", selected=True)

            # 「输出内容」默认是「题目」：篮里有了答案也不会自己附上答案卷。
            toggle("with-answer")
            preview()
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator("#printAnswers")).not_to_be_checked()
            set_document("answers")
            expect(page.locator("#printAnswers")).to_be_checked()
            # 「参考答案与解析」那一节只是排版的分组：分页时被拆开铺进卷面，
            # 卷上留下的是每题一行的 .print-answer-row，断言要落在它身上。
            expect(page.locator(".print-answer-row")).to_contain_text("将")
            set_document("questions")
            expect(page.locator(".print-answer-row")).to_have_count(0)
            close()
            toggle("with-answer", selected=True)
            toggle("analysis-only")
            preview()
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator("#printAnswers")).not_to_be_checked()
            set_document("combined")
            expect(page.locator(".print-answer-row")).to_contain_text("只有解析的原卷内容")
            expect(page.locator(".print-answer-row")).not_to_contain_text("未提供答案")
            close()
            toggle("analysis-only", selected=True)
            toggle("no-answer-1")
            preview()
            no_answers()
            set_document("questions")
            close()
            toggle("no-answer-1", selected=True)
            toggle("with-answer")
            preview()
            # 答案开关跟着「输出内容」那一栏走：自己关掉之后重开卷，答案不该自己回来。
            expect(page.locator("#printAnswers")).not_to_be_checked()
            set_document("answers")
            expect(page.locator("#printAnswers")).to_be_checked()
            expect(page.locator(".print-answer-row")).to_contain_text("将")
            # 纯答案卷上没有题面（.print-question 一个都不渲染），后面的流程要题面，回到合并卷。
            set_document("combined")
            close()
            toggle("with-answer", selected=True)

            # An AI-only basket should explain the optional reference, not say it has no content.
            toggle("ai-only")
            preview()
            no_answers()
            expect(page.locator("#printAiBox")).to_be_visible()
            expect(page.locator("#printAnswerStatus")).to_contain_text("AI 参考（未核对）")
            set_option("#printAiAnswers", True)
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator("#printAnswers")).to_be_checked()
            expect(page.locator(".print-answer-row")).to_contain_text("AI 参考，未核对")
            set_option("#printAiAnswers", False)
            no_answers()
            set_option("#printAiAnswers", True)
            page.set_viewport_size({"width": 390, "height": 950})
            page.screenshot(path=str(OUTPUT / "preview-ai-390.png"))
            close()
            toggle("with-answer")
            preview()
            # 「逐题紧跟」时答案是每题一行的，不是卷末一节。
            set_document("combined")
            expect(page.locator(".print-answer-row")).to_have_count(2)
            # 两行答案：一行原卷给的，一行只有 AI 参考 —— 断言要落在具体那一行上。
            expect(page.locator(".print-answer-row").filter(has_text="AI 参考，未核对")).to_have_count(1)
            expect(page.locator(".print-answer-row").filter(has_text="将")).to_have_count(1)
            set_option("#printAiAnswers", False)
            expect(page.locator("#printAnswers")).to_be_enabled()
            # 关掉 AI 参考后那道题没有答案可印，逐题紧跟的卷上就不给它留位置了。
            expect(page.locator(".print-answer-row")).to_have_count(1)
            expect(page.locator(".print-answer-row").filter(has_text="AI 参考，未核对")).to_have_count(0)
            set_option("#printAiAnswers", True)
            # Screen controls and availability notices do not enter the printed paper.
            page.emulate_media(media="print")
            for tool in page.locator(".print-question-tools").all():
                expect(tool).to_be_hidden()
            expect(page.locator("#printAnswerStatus")).to_be_hidden()
            expect(page.locator(".print-answer-row").first).to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    print(json.dumps({"result": "PASS", "viewports": [1440, 650, 390, 320],
        "checks": ["no empty answer section", "answers follow the output selector", "analysis-only content",
                   "AI-only hint and opt-in", "original/AI mixed answers", "no horizontal page overflow", "visible controls",
                   "keyboard move/remove and focus", "print controls hidden"],
        "screenshots": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    check()
