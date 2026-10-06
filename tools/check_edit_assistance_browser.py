"""Offline browser regression for edit-position preview and region-read location.

--run uses a separate checkout/tmp/edit-assistance-browser database and port
8982, starts Django without a worker, and stops that exact server tree. The
two-page original and completed recognition are synthetic: no OCR, models,
cloud API, credentials or real user records are used. UI writes are forbidden.
"""

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "edit-assistance-browser"
PORT = 8982


def isolated_paths():
    for name in ("QB_DATABASE", "QB_DATA_ROOT"):
        value = os.environ.get(name)
        if not value or not Path(value).resolve().is_relative_to(ROOT / "tmp"):
            raise SystemExit(f"{name} must point inside this checkout's tmp directory")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    Path(os.environ["QB_DATA_ROOT"]).mkdir(parents=True, exist_ok=True)


def seed():
    isolated_paths()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    import pymupdf as fitz
    from core.models import Block, Paper, Question, RegionRead
    from core import region_reads

    call_command("migrate", verbosity=0)
    if not hasattr(region_reads, "recommendation_for"):
        raise SystemExit("The region recommendation backend must be present before seeding this test")
    pages = [{"page_idx": index, "width": 595, "height": 842} for index in (0, 1)]
    paper, _ = Paper.objects.get_or_create(sha256="edit-assistance-browser",
        defaults={"filename": "编辑辅助离线演示.pdf", "kind": "pdf", "status": "ready", "pages": pages, "total": 2})
    folder = settings.DATA_ROOT / str(paper.id)
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / "source.pdf"
    if not original.exists():
        document = fitz.open()
        first = document.new_page(width=595, height=842)
        for y, text in ((70, "Offline original: edit assistance"), (130, "1. Given f(x)=x^2+1, find f(2)."),
                        (180, "A. 7    B. 8    C. 9    D. 10")):
            first.insert_text((60, y), text, fontsize=16)
        second = document.new_page(width=595, height=842)
        for y, text in ((70, "Offline original: tables and formulas"), (130, "2. Compare 1/2 and sqrt(2)."),
                        (180, "x    y"), (220, "1    2"), (260, "2    4")):
            second.insert_text((60, y), text, fontsize=16)
        document.save(original)
        document.close()
    paper.source_path = str(original)
    paper.save(update_fields=["source_path"])
    stem = "已知函数 $f(x)=x^{3}+1$，求 $f(2)$。"
    old_formula, new_formula = "$f(x)=x^{3}+1$", "$f(x)=x^{2}+1$"
    digit = stem.index("3")
    spot = {"n": 1, "reading": "3", "mineru": "2", "page_idx": 0, "bbox": [90, 135, 880, 185],
            "field": "stem", "start": digit, "end": digit + 1, "text": "3"}
    first, _ = Question.objects.get_or_create(paper=paper, number=1, defaults={
        "stem": stem, "options": {"A": "$7$", "B": "$8$", "C": "$9$", "D": "$10$"},
        "question_type": "single_choice", "state": "yellow", "approved": False,
        "regions": [{"page_idx": 0, "bbox": [80, 100, 930, 340]}],
        "regions_auto": [{"page_idx": 0, "bbox": [80, 100, 930, 340]}],
        "read_a": {"engine": "Offline fixture", "stem": stem}, "read_b": {"engine": "Offline fixture", "stem": stem},
        "read_c": {"doubtful": [spot]},
        "flags": ["两次识读一致，但 MinerU 在这里读法不同，再看一次也不能确定：…函数【3】…（MinerU：2），请对照原卷"]})
    second, _ = Question.objects.get_or_create(paper=paper, number=2, defaults={
        "stem": "🙂 比较 $\\frac{1}{2}$ 与 $\\sqrt{2}$。\n|x|y|\n|---|---|\n|1|2|\n|2|4|",
        "question_type": "free_response", "state": "green", "approved": False,
        "regions": [{"page_idx": 1, "bbox": [80, 100, 930, 400]}]})
    Block.objects.get_or_create(paper=paper, seq=1, defaults={"type": "text", "page_idx": 0,
        "bbox": spot["bbox"], "text": stem.replace(old_formula, new_formula)})
    base = region_reads.question_context(first)
    recommendation = region_reads.recommendation_for(base, new_formula, {
        "target": "stem", "before": old_formula, "confidence": "high",
        "why": "识读区域是函数表达式，完整表达式在题干中只有一个位置。"})
    assert recommendation["status"] == "recommended", recommendation
    read, _ = RegionRead.objects.update_or_create(question=first, defaults={"page_idx": 0,
        "bbox": [90, 135, 880, 185], "target": "auto", "status": "done", "text": new_formula,
        "engine": "Offline deterministic fixture", "recommendation": recommendation})
    (OUTPUT / "fixture.json").write_text(json.dumps({"paper": str(paper.id), "first": first.id,
        "second": second.id, "stem": stem, "old_formula": old_formula, "new_formula": new_formula,
        "read": read.id}, ensure_ascii=False), encoding="utf-8")
    print("Isolated edit-assistance fixtures ready", flush=True)


def question_snapshot():
    with sqlite3.connect(Path(os.environ["QB_DATABASE"]).resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return {table: connection.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
                for table in ("core_question", "core_publishedquestion", "core_regionread")}


def check(url):
    isolated_paths()
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local isolated server is allowed")
    from playwright.sync_api import sync_playwright, expect

    fixture = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    before = question_snapshot()
    errors, forbidden, requests, passed = [], [], [], []
    with sync_playwright() as pw:
        executable = next((str(path) for path in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1050},
            permissions=["clipboard-read", "clipboard-write"])
        context.add_init_script("localStorage.setItem('qb-welcome-seen', '1'); localStorage.setItem('qb-lens', '0')")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: requests.append({"method": request.method, "url": request.url}))

        def read_only(route):
            request = route.request
            if urlparse(request.url).hostname not in ("127.0.0.1", "localhost") or request.method not in ("GET", "HEAD"):
                forbidden.append(f"{request.method} {request.url}")
                route.abort()
            else:
                route.continue_()

        context.route("**/*", read_only)
        native_dialogs = []
        page.on("dialog", lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
        paper_url = url + "/api/papers/" + fixture["paper"]
        original = page.request.get(paper_url).json()
        question = next(item for item in original["questions"] if item["id"] == fixture["first"])
        assert question["region_read"]["recommendation"]["status"] == "recommended"
        active_response = deepcopy(original)
        def fixture_response(route):
            if route.request.method != "GET":
                forbidden.append(f"{route.request.method} {route.request.url}")
                route.abort()
            else:
                route.fulfill(json=active_response)

        page.route("**/api/papers/" + fixture["paper"], fixture_response)

        def load_variant(status="recommended"):
            nonlocal active_response
            close_editor()
            active_response = deepcopy(original)
            item = next(row for row in active_response["questions"] if row["id"] == fixture["first"])
            if status != "recommended":
                item["region_read"]["recommendation"]["status"] = status
                item["region_read"]["recommendation"]["why"] = "离线回归：未找到唯一位置，请手动选择。"
            page.goto(url + "/?paper=" + fixture["paper"])
            page.wait_for_load_state("networkidle")
            expect(card()).to_be_visible()

        def card(second=False):
            return page.locator(f'.card[data-id="{fixture["second" if second else "first"]}"]')

        def close_editor():
            editors = page.locator(".editor")
            if editors.count():
                # 面板上原来那个「取消」已经改名成「← 返回」；它是题卡的直接子元素，
                # 不在改字表单里，所以要从题卡上找，不能在 .editor 里找。
                page.locator(".card.editing").get_by_role("button", name="← 返回", exact=True).click()
                if page.locator("#confirmDialog").is_visible():
                    page.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
                expect(page.locator(".editor")).to_have_count(0)

        def open_editor(second=False):
            card(second).get_by_role("button", name="改字", exact=True).click()
            editor = card(second).locator(".editor")
            expect(editor).to_be_visible()
            return editor

        def position(input_, find, length=0, last=False):
            return input_.evaluate("""async (el, args) => {
                const index = args.last ? el.value.lastIndexOf(args.find) : el.value.indexOf(args.find);
                if (index < 0) throw new Error('Missing fixture selection');
                el.focus(); el.setSelectionRange(index, index + args.length);
                el.dispatchEvent(new Event('select', {bubbles:true}));
                el.dispatchEvent(new KeyboardEvent('keyup', {key:'ArrowRight',bubbles:true}));
                return await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(index))));
            }""", {"find": find, "length": length, "last": last})

        def text_overlay(text, second=False):
            preview = card(second).locator(".editor-preview")
            expect(preview.locator(".qb-preview-selection")).to_have_count(1)
            # The selection is a display-only rectangle, so compare it with
            # the native DOM Range around the intended visible characters.
            measured = preview.evaluate("""(el, text) => {
                const node = [...el.querySelectorAll('[data-qb-field=stem] .qb-source-text')]
                    .find(node => node.textContent.includes(text));
                if (!node) throw new Error('Missing selected preview text');
                const offset = node.textContent.indexOf(text);
                const range = document.createRange();
                range.setStart(node.firstChild,offset); range.setEnd(node.firstChild,offset+text.length);
                const expected = range.getBoundingClientRect();
                const actual = el.querySelector('.qb-preview-selection').getBoundingClientRect();
                return {left:Math.abs(expected.left-actual.left),top:Math.abs(expected.top-actual.top),
                    width:Math.abs(expected.width-actual.width),actualWidth:actual.width};
            }""", text)
            assert measured["actualWidth"] > 0 and max(measured[key] for key in ("left", "top", "width")) < 2, measured

        def remember_preview_nodes(second=False):
            return card(second).evaluate("""el => {
                window.__editTestNodes = [...el.querySelectorAll('.editor-preview .qb-source-text, .crop-seg img')];
                return {count:window.__editTestNodes.length,scrollY:window.scrollY};
            }""")

        def assert_preview_nodes(remembered, second=False):
            preserved = card(second).evaluate("""el => {
                const current = [...el.querySelectorAll('.editor-preview .qb-source-text, .crop-seg img')];
                return current.length === window.__editTestNodes.length &&
                    current.every((node,index) => node === window.__editTestNodes[index]);
            }""")
            assert remembered["count"] > 0 and preserved, "Selecting a position rebuilt preview text or original images"
            assert abs(page.evaluate("window.scrollY") - remembered["scrollY"]) < 2, "Selecting a position moved the page"

        def symbol_visible(second=False):
            preview = card(second).locator(".editor-preview")
            expect(preview.locator(".qb-preview-active-symbol")).to_have_count(1)
            return preview.evaluate("""el => {
                const symbol = [...el.querySelectorAll('.qb-preview-active-symbol [style]')]
                    .find(node => node.style.color === 'rgb(31, 107, 95)' || node.style.color === '#1f6b5f');
                if (!symbol) throw new Error('No safe green formula symbol');
                const rect = symbol.getBoundingClientRect(), box = el.getBoundingClientRect();
                return {visible:rect.left >= box.left && rect.right <= box.right,
                    scrollLeft:el.scrollLeft, scrollWidth:el.scrollWidth, clientWidth:el.clientWidth};
            }""")

        load_variant()
        page.wait_for_function("[...document.querySelectorAll('.crop-seg img')].every(i => i.complete && i.naturalWidth)")
        expect(card().locator(".crop-spot")).to_have_count(1)
        expect(card().locator("mark.qb-mark.spot.qb-mark-math")).to_have_count(1)
        passed.append("Original source bbox and disputed rendered formula are both marked")
        card().get_by_text("查看完整替换位置", exact=True).click()
        # Keyboard navigation holds the current card while the screenshot is
        # framed, matching a reader explicitly jumping to this question.
        page.keyboard.press("k")
        expect(card()).to_have_class(re.compile(r"\bis-current\b"))
        page.wait_for_function("id => getComputedStyle(document.querySelector(`.card[data-id='${id}']`)).opacity === '1'", arg=fixture["first"])
        card().screenshot(path=str(OUTPUT / "original-red-box-and-recommendation.png"))
        card().locator(".region-recommendation").screenshot(path=str(OUTPUT / "recommended-location-detail.png"))
        editor = open_editor()
        stem = editor.locator(".stem-input")
        position(stem, "已知", length=2)
        text_overlay("已知")
        remembered = remember_preview_nodes()
        position(stem, "函数")
        expect(card().locator(".editor-preview .qb-preview-caret")).to_have_count(1)
        assert_preview_nodes(remembered)
        position(stem, "3", length=1)
        expect(card().locator(".editor-preview .qb-preview-active-formula")).to_have_count(1)
        assert symbol_visible()["visible"]
        assert_preview_nodes(remembered)
        expect(card().locator(".editor-position-hint")).to_contain_text("公式")
        page.screenshot(path=str(OUTPUT / "safe-exponent-position.png"))
        position(stem, "f(2)", length=1)
        expect(card().locator(".editor-preview .qb-preview-active-formula")).to_have_attribute("data-raw", "$f(2)$")
        first_formula = card().locator('.editor-preview .qb-math[data-raw="$f(x)=x^{3}+1$"]')
        assert first_formula.evaluate("""el => ![...el.querySelectorAll('[style]')].some(node =>
            node.style.color === 'rgb(31, 107, 95)' || node.style.color === '#1f6b5f')"""), "Prior formula keeps temporary green color"
        assert_preview_nodes(remembered)
        passed.append("Plain text caret/selection and safe formula symbol position; selection preserves text/image DOM and page scroll")
        passed.append("Moving between formulas restores prior formula color and marks only the current formula")
        page.screenshot(path=str(OUTPUT / "formula-position.png"))
        close_editor()

        editor = open_editor(second=True)
        stem = editor.locator(".stem-input")
        # JS indexOf and selectionStart use UTF16, including the emoji prefix.
        index = position(stem, "比较", length=2)
        assert index == 3
        text_overlay("比较", second=True)
        position(stem, "frac", length=2)
        expect(card(second=True).locator(".editor-preview .qb-preview-active-formula")).to_have_count(1)
        expect(card(second=True).locator(".editor-preview .qb-preview-active-symbol")).to_have_count(0)
        expect(stem).to_have_value(original["questions"][1]["stem"])
        position(stem, "---", length=1)
        expect(card(second=True).locator(".editor-preview .qb-preview-active-region")).to_have_count(1)
        passed.append("Emoji uses UTF16 offsets; LaTeX command and Markdown separator use honest whole-region fallback")

        # Simulated browser composition events must not move the text caret or
        # replace its intermediate text. This checks integration with IME
        # event ordering, not a particular installed input-method engine.
        composition = stem.evaluate("""el => {
            const at = el.value.indexOf('比较');
            el.focus(); el.setSelectionRange(at, at);
            el.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true,data:''}));
            el.value = el.value.slice(0,at) + '中' + el.value.slice(at);
            el.setSelectionRange(at+1,at+1);
            el.dispatchEvent(new InputEvent('input',{bubbles:true,data:'中',inputType:'insertCompositionText',isComposing:true}));
            return {value:el.value, at:at+1};
        }""")
        expect(stem).to_have_value(composition["value"])
        assert stem.evaluate("el => el.selectionStart") == composition["at"]
        passed.append("Composition start/input/end preserve intermediate Chinese text and textarea caret")
        stem.evaluate("el => el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'中'}))")
        expect(stem).to_have_value(composition["value"])
        assert stem.evaluate("el => el.selectionStart") == composition["at"]
        page.set_viewport_size({"width": 390, "height": 844})
        position(stem, "比较", length=2)
        text_overlay("比较", second=True)
        assert editor.evaluate("el => el.scrollWidth <= el.clientWidth + 2"), "Narrow editor overflows"
        card(second=True).locator(".editor-preview-box").screenshot(path=str(OUTPUT / "mobile-preview-position.png"))
        passed.append("390px viewport selection rectangle remains aligned and editor does not overflow")

        # A deliberately wide fraction must reveal the selected last term in
        # the preview's own horizontal scroll area, keeping the page still.
        # 8 段十位数：390px 下预览宽约 352px，5 段刚好塞得下、压根不溢出，
        # 量到的 scrollLeft 永远是 0 —— 那不是「预览不滚动」，是这条样本不够宽。
        # 再长（12 段）预览里就不排这个公式了，量不到东西。
        long_formula = "长式 $\\frac{" + "+".join("1234567890" for _ in range(8)) + "}{1}$"
        stem.fill(long_formula)
        expect(card(second=True).locator(".editor-preview .qb-math")).to_have_attribute("data-raw", long_formula[3:])
        position(stem, "9", length=1, last=True)
        first_scroll = symbol_visible(second=True)
        assert first_scroll["visible"] and first_scroll["scrollLeft"] > 0, first_scroll
        remembered = remember_preview_nodes(second=True)
        position(stem, "9", length=1, last=True)
        second_scroll = symbol_visible(second=True)
        assert abs(second_scroll["scrollLeft"] - first_scroll["scrollLeft"]) < 2, (first_scroll, second_scroll)
        assert_preview_nodes(remembered, second=True)
        stem.evaluate("""el => {
            const at = el.selectionStart;
            el.value += '。'; el.setSelectionRange(at,at+1);
            el.dispatchEvent(new InputEvent('input',{bubbles:true,data:'。',inputType:'insertText'}));
        }""")
        expect(stem).to_have_value(long_formula + "。")
        expect(card(second=True).locator(".editor-preview .qb-source-text").last).to_contain_text("。")
        third_scroll = symbol_visible(second=True)
        assert third_scroll["visible"] and third_scroll["scrollLeft"] > 0, third_scroll
        assert abs(page.evaluate("window.scrollY") - remembered["scrollY"]) < 2, "Input moved the page while updating long-formula preview"
        card(second=True).locator(".editor-preview-box").screenshot(path=str(OUTPUT / "long-formula-position.png"))
        passed.append("Wide fraction reveals selected term inside preview; repeated selection and input keep page stable and relevant internal scroll")
        close_editor()
        page.set_viewport_size({"width": 1440, "height": 1050})

        load_variant()
        expect(card().locator(".region-location-before .qb-math")).to_have_attribute("data-raw", fixture["old_formula"])
        expect(card().locator(".region-location-after .qb-math")).to_have_attribute("data-raw", fixture["new_formula"])
        card().get_by_role("button", name="确认位置并填入改字", exact=True).click()
        editor = card().locator(".editor")
        expect(editor.locator(".stem-input")).to_have_value(fixture["stem"].replace(fixture["old_formula"], fixture["new_formula"]))
        # 「保存」在 .editor-bar 上，不是改字表单的一部分（和「← 返回」一样），
        # 所以要从题卡上找，不能在 .editor 里找。
        expect(card().get_by_role("button", name="保存", exact=True)).to_be_enabled()
        assert question_snapshot() == before, "Confirming placement must only fill the editor"
        passed.append("Recommended exact replacement changes only editor, with recognizable before/after formulas and enabled manual save")
        close_editor()

        # An already edited field cannot be silently replaced with the saved
        # baseline when a recommendation was calculated before those edits.
        load_variant()
        editor = open_editor()
        editor.locator(".stem-input").fill("这是还没有保存、不能被识读结果覆盖的新题干。")
        card().get_by_role("button", name="确认位置并填入改字", exact=True).click()
        expect(page.locator(".editor")).to_have_count(1)
        expect(editor.locator(".stem-input")).to_have_value("这是还没有保存、不能被识读结果覆盖的新题干。")
        passed.append("Recommendation cannot overwrite existing unsaved stem; only one editor remains")
        close_editor()

        # 手动兜底不再是「手动选择替换位置」那个下拉框了，现在是几个直接点的按钮
        # （放到题干 / 填到选项 B）。选哪一块就是点哪个按钮。
        load_variant("manual")
        card().get_by_role("button", name="填到选项 B", exact=True).click()
        editor = card().locator(".editor")
        expect(editor.locator(".stem-input")).to_have_value(fixture["stem"])
        expect(editor.locator(".option-inputs input").nth(1)).to_have_value(fixture["new_formula"])
        passed.append("Manual option B fallback fills only option B in editor")
        close_editor()

        load_variant("stale")
        # 位置建议过期后不给任何「一键填入」：面板上只剩手动改字 / 重新框选 / 关闭。
        # 原来这条还要求「复制并打开改字」把识读原文复制进剪贴板 —— 那条路现在没有了，
        # 改成断言现在真实存在的保证：没有一键填入，手动改字打开的仍是原题。
        expect(card().get_by_role("button", name="确认位置并填入改字", exact=True)).to_have_count(0)
        assert card().locator(".region-manual-target").count() == 0, \
            {"位置建议已经过期，却还留着「放到题干 / 填到选项」这些一键填入": card().inner_text()[:300]}
        card().get_by_role("button", name="手动改字", exact=True).click()
        editor = card().locator(".editor")
        expect(editor.locator(".stem-input")).to_have_value(fixture["stem"])
        assert question_snapshot() == before, "过期建议不该改动题目"
        passed.append("Stale recommendation offers no one-click apply; manual edit still opens the original stem")
        close_editor()

        assert not errors, errors
        assert not forbidden, forbidden
        assert not native_dialogs, native_dialogs
        context.close()
        browser.close()
    assert question_snapshot() == before, "Browser checks must not change fixture questions"
    snapshot_hash = hashlib.sha256(json.dumps(before, ensure_ascii=False).encode("utf-8")).hexdigest()
    (OUTPUT / "results.json").write_text(json.dumps({"passed": passed,
        "question_counts": {table: len(rows) for table, rows in before.items()},
        "saved_records_identical": True, "saved_record_sha256_before_and_after": snapshot_hash,
        "page_errors": errors, "forbidden_requests": forbidden, "native_dialogs": native_dialogs,
        "observed_requests": requests, "database": os.environ["QB_DATABASE"], "data_root": os.environ["QB_DATA_ROOT"],
        "worker_started": False, "server_port": PORT}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Edit-assistance browser checks passed; no question saves, cloud calls or user data", flush=True)


def run():
    os.environ["QB_DATABASE"] = str(OUTPUT / "db.sqlite3")
    os.environ["QB_DATA_ROOT"] = str(OUTPUT / "data")
    isolated_paths()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit(f"Port {PORT} is already occupied; no existing service will be stopped")
    seed()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (OUTPUT / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, str(ROOT / "backend/manage.py"), "runserver",
            f"127.0.0.1:{PORT}", "--noreload"], cwd=ROOT, env=os.environ.copy(), stdout=log,
            stderr=subprocess.STDOUT, creationflags=flags)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Isolated server exited before becoming ready")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", PORT)) == 0:
                        break
                time.sleep(0.1)
            else:
                raise TimeoutError("Isolated server did not become ready")
            check(f"http://127.0.0.1:{PORT}")
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=False)
            else:
                server.terminate()
            server.wait(timeout=10)
    print("Isolated edit-assistance server stopped", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--url")
    args = parser.parse_args()
    if args.run:
        run()
    elif args.seed:
        seed()
    elif args.url:
        check(args.url.rstrip("/"))
    else:
        parser.error("Choose --run, --seed or --url")
