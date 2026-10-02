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

            def intercept(route):
                request = route.request
                parsed = urlparse(request.url)
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

            def preview():
                page.locator("#basketButton").click()
                expect(page.locator(".print-question").first).to_be_visible()

            def close():
                page.locator("#closePrint").click()

            def no_answers():
                expect(page.locator("#printAnswers")).to_be_disabled()
                expect(page.locator("#printAnswers")).not_to_be_checked()
                expect(page.locator(".print-answers")).to_have_count(0)

            def set_option(selector, checked):
                control = page.locator(selector)
                if control.is_checked() != checked:
                    page.locator("label").filter(has=control).click()

            toggle("no-answer-1")
            toggle("no-answer-2")
            preview()
            no_answers()
            expect(page.locator("#printAnswerStatus")).to_have_text("所选题目没有答案或解析，卷末不附参考答案。")
            # Controls must stay visible without hover, even beside a very long formula.
            for width in (1440, 650, 390, 320):
                page.set_viewport_size({"width": width, "height": 950})
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
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "第 2 题上移"
            page.get_by_role("button", name="第 2 题上移", exact=True).press("Space")
            assert page.locator(".print-question").evaluate_all("nodes => nodes.map(node => node.dataset.questionId)") == ["no-answer-1", "no-answer-2"]
            page.get_by_role("button", name="第 1 题移出试题篮", exact=True).press("Enter")
            expect(page.locator(".print-question")).to_have_count(1)
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "第 1 题移出试题篮"
            close()
            toggle("no-answer-2", selected=True)

            # Default answer preference recovers when the basket has an answer.
            toggle("with-answer")
            preview()
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator("#printAnswers")).to_be_checked()
            expect(page.locator(".print-answers")).to_contain_text("将")
            set_option("#printAnswers", False)
            expect(page.locator(".print-answers")).to_have_count(0)
            close()
            toggle("with-answer", selected=True)
            toggle("analysis-only")
            preview()
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator("#printAnswers")).not_to_be_checked()
            set_option("#printAnswers", True)
            expect(page.locator(".print-answers")).to_contain_text("只有解析的原卷内容")
            expect(page.locator(".print-answers")).not_to_contain_text("未提供答案")
            close()
            toggle("analysis-only", selected=True)
            toggle("no-answer-1")
            preview()
            no_answers()
            close()
            toggle("no-answer-1", selected=True)
            toggle("with-answer")
            preview()
            expect(page.locator("#printAnswers")).to_be_checked()
            close()
            toggle("with-answer", selected=True)

            # An AI-only basket should explain the optional reference, not say it has no content.
            toggle("ai-only")
            preview()
            no_answers()
            expect(page.locator("#printAiBox")).to_be_visible()
            expect(page.locator("#printAnswerStatus")).to_contain_text("未核对的参考内容")
            set_option("#printAiAnswers", True)
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator("#printAnswers")).to_be_checked()
            expect(page.locator(".print-answers")).to_contain_text("AI 参考，未核对")
            set_option("#printAiAnswers", False)
            no_answers()
            set_option("#printAiAnswers", True)
            page.set_viewport_size({"width": 390, "height": 950})
            page.screenshot(path=str(OUTPUT / "preview-ai-390.png"))
            close()
            toggle("with-answer")
            preview()
            expect(page.locator(".print-answer-row")).to_have_count(2)
            expect(page.locator(".print-answers")).to_contain_text("AI 参考，未核对")
            expect(page.locator(".print-answers")).to_contain_text("将")
            set_option("#printAiAnswers", False)
            expect(page.locator("#printAnswers")).to_be_enabled()
            expect(page.locator(".print-answers")).to_contain_text("原卷未提供答案")
            expect(page.locator(".print-answers")).not_to_contain_text("AI 参考，未核对")
            set_option("#printAiAnswers", True)
            # Screen controls and availability notices do not enter the printed paper.
            page.emulate_media(media="print")
            for tool in page.locator(".print-question-tools").all():
                expect(tool).to_be_hidden()
            expect(page.locator("#printAnswerStatus")).to_be_hidden()
            expect(page.locator(".print-answers")).to_be_visible()
            assert not errors, errors
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    print(json.dumps({"result": "PASS", "viewports": [1440, 650, 390, 320],
        "checks": ["no empty answer section", "answer preference restored", "analysis-only content",
                   "AI-only hint and opt-in", "original/AI mixed answers", "no horizontal page overflow", "visible controls",
                   "keyboard move/remove and focus", "print controls hidden"],
        "screenshots": str(OUTPUT)}, ensure_ascii=False))


if __name__ == "__main__":
    check()
