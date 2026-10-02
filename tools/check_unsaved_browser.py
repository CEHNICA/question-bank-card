"""Check unsaved edit protection against two isolated, offline demo papers.

Run --run to seed checkout/tmp/unsaved-browser, launch Django on port 8981
without a worker, test a headless browser, and stop the complete server tree.
Alternatively set QB_DATABASE/QB_DATA_ROOT under checkout/tmp, run --seed,
start your own server without a worker, and use --url http://127.0.0.1:8981.
Browser requests outside loopback and all mutating requests are forbidden.
"""

import argparse
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "unsaved-browser"
PORT = 8981


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
    from django.core.management import call_command
    from core.models import Paper, Question

    call_command("migrate", verbosity=0)
    fixtures = []
    for name, stem in (("未保存保护演示甲.pdf", "求 $1+1$。"), ("未保存保护演示乙.pdf", "求 $2+2$。")):
        paper, _ = Paper.objects.get_or_create(sha256="unsaved-browser-" + name,
            defaults={"filename": name, "kind": "pdf", "status": "ready", "pages": [], "total": 1})
        question, _ = Question.objects.get_or_create(paper=paper, number=1,
            defaults={"stem": stem, "question_type": "free_response", "state": "green", "approved": False})
        fixtures.append({"paper": str(paper.id), "question": question.id, "name": name, "stem": stem})
    (OUTPUT / "fixture.json").write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
    print("Isolated unsaved-edit fixtures ready", flush=True)


def question_snapshot():
    uri = Path(os.environ["QB_DATABASE"]).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return connection.execute("SELECT * FROM core_question ORDER BY id").fetchall()


def check(url):
    isolated_paths()
    if urlparse(url).hostname not in ("127.0.0.1", "localhost"):
        raise SystemExit("Only a local isolated server is allowed")
    from playwright.sync_api import sync_playwright, expect, Error

    fixtures = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    first, second = fixtures
    before = question_snapshot()
    errors, forbidden, dialogs = [], [], []
    native_action = "dismiss"
    with sync_playwright() as pw:
        executable = next((str(path) for path in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        context.add_init_script("localStorage.setItem('qb-welcome-seen', '1')")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def local_read_only(route):
            request = route.request
            if urlparse(request.url).hostname not in ("127.0.0.1", "localhost") or request.method not in ("GET", "HEAD"):
                forbidden.append(f"{request.method} {request.url}")
                route.abort()
            else:
                route.continue_()

        context.route("**/*", local_read_only)

        def handle_native(dialog):
            dialogs.append(dialog.type)
            if native_action == "accept":
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", handle_native)
        page.goto(url + "/?paper=" + first["paper"])
        page.wait_for_load_state("networkidle")
        expect(page.locator("#paperName")).to_have_text(first["name"])
        # Inspect the rendered page before selecting its controls.
        page.screenshot(path=str(OUTPUT / "initial.png"))
        assert page.get_by_role("button", name="改字", exact=True).count() == 1

        def open_editor(marker):
            page.locator(f'.card[data-id="{first["question"]}"]').get_by_role("button", name="改字", exact=True).click()
            editor = page.locator(".editor")
            expect(editor).to_be_visible()
            editor.locator(".stem-input").fill(marker)
            return editor

        def choose_paper(item):
            page.locator("#paperList .paper-link").filter(has_text=item["name"]).click()

        def expect_warning():
            expect(page.locator("#confirmDialog")).to_be_visible()
            expect(page.locator("#confirmTitle")).to_have_text("改字还没保存")

        def keep_editing():
            page.locator("#confirmDialog").get_by_role("button", name="继续编辑", exact=True).click()
            expect(page.locator("#confirmDialog")).not_to_be_visible()

        def discard():
            page.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
            expect(page.locator("#confirmDialog")).not_to_be_visible()

        editor = open_editor("切卷时应该保留的临时输入")
        choose_paper(second)
        expect_warning()
        expect(page.locator("#confirmDialog [value=cancel]")).to_be_focused()
        page.screenshot(path=str(OUTPUT / "unsaved-warning.png"))
        keep_editing()
        expect(page.locator("#paperName")).to_have_text(first["name"])
        expect(editor.locator(".stem-input")).to_have_value("切卷时应该保留的临时输入")
        choose_paper(second)
        expect_warning()
        discard()
        expect(page.locator("#paperName")).to_have_text(second["name"])
        expect(page.locator(".editor")).to_have_count(0)
        choose_paper(first)
        expect(page.locator("#paperName")).to_have_text(first["name"])

        editor = open_editor("取消前的临时输入")
        editor.get_by_role("button", name="取消", exact=True).click()
        expect_warning()
        keep_editing()
        expect(editor.locator(".stem-input")).to_have_value("取消前的临时输入")
        editor.get_by_role("button", name="取消", exact=True).click()
        expect_warning()
        discard()
        expect(page.locator(".editor")).to_have_count(0)

        editor = open_editor("Esc 前的临时输入")
        editor.locator(".stem-input").press("Escape")
        expect_warning()
        page.keyboard.press("Escape")
        expect(page.locator("#confirmDialog")).not_to_be_visible()
        expect(editor.locator(".stem-input")).to_have_value("Esc 前的临时输入")
        editor.locator(".stem-input").press("Escape")
        expect_warning()
        discard()
        expect(page.locator(".editor")).to_have_count(0)

        editor = open_editor("跳转题库前的临时输入")
        page.locator('.topnav a[href="/library"]').click()
        expect_warning()
        keep_editing()
        expect(editor.locator(".stem-input")).to_have_value("跳转题库前的临时输入")
        assert urlparse(page.url).path == "/"
        page.locator('.topnav a[href="/library"]').click()
        expect_warning()
        discard()
        page.wait_for_url("**/library")
        page.wait_for_load_state("networkidle")
        assert not dialogs, "Custom navigation must not cause a second native warning"

        page.goto(url + "/?paper=" + first["paper"])
        page.wait_for_load_state("networkidle")
        editor = open_editor("刷新前的临时输入")
        try:
            page.reload(wait_until="domcontentloaded", timeout=5000)
        except Error:
            # Dismissing beforeunload aborts navigation in Chromium.
            pass
        assert dialogs == ["beforeunload"], dialogs
        expect(editor.locator(".stem-input")).to_have_value("刷新前的临时输入")
        native_action = "accept"
        page.reload(wait_until="networkidle")
        assert dialogs == ["beforeunload", "beforeunload"], dialogs
        expect(page.locator(".editor")).to_have_count(0)
        expect(page.locator("#paperName")).to_have_text(first["name"])
        native_action = "dismiss"

        # Editing back to the original value should allow a paper switch and
        # a later clean cancel, with no custom or native confirmation.
        editor = open_editor("稍后会改回去")
        editor.locator(".stem-input").fill(first["stem"])
        choose_paper(second)
        expect(page.locator("#paperName")).to_have_text(second["name"])
        expect(page.locator("#confirmDialog")).not_to_be_visible()
        choose_paper(first)
        expect(page.locator("#paperName")).to_have_text(first["name"])
        editor = open_editor(first["stem"])
        editor.get_by_role("button", name="取消", exact=True).click()
        expect(page.locator(".editor")).to_have_count(0)
        expect(page.locator("#confirmDialog")).not_to_be_visible()
        page.screenshot(path=str(OUTPUT / "restored.png"))
        assert len(dialogs) == 2, dialogs
        assert not errors, errors
        assert not forbidden, forbidden
        context.close()
        browser.close()
    assert question_snapshot() == before, "UI tests must not save or change any fixture questions"
    print("Browser checks passed: paper switch keep/discard, cancel, Esc, library navigation, native refresh, original-value recovery; no mutating requests or question changes", flush=True)


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
            # The Windows venv launcher creates a child Python process, so
            # terminate this exact process tree rather than the launcher only.
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=False)
            else:
                server.terminate()
            server.wait(timeout=10)
    print("Isolated server stopped", flush=True)


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
