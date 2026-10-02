"""Exercise upgraded teaching through real UI actions on an isolated demo server.

--run confines its database, papers, settings and output to checkout/tmp,
starts Django without a worker on an unoccupied loopback port, and allows
browser writes only to /api/demo or existing demo question actions. No installed
application, user database, credentials, cloud OCR or formal library is used.
"""

import argparse
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "teaching-upgrade-20261002"


def configure():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, filename in (("QB_DATABASE", "db.sqlite3"), ("QB_DATA_ROOT", "data"),
                           ("QB_CREDENTIAL_FILE", "absent-credentials.bin"),
                           ("QB_MODEL_PREFERENCES_FILE", "model-preferences.json"),
                           ("QB_FEATURES_FILE", "features.json")):
        os.environ[name] = str((OUTPUT / filename).resolve())
        assert Path(os.environ[name]).is_absolute()
        assert Path(os.environ[name]).resolve().is_relative_to(ROOT / "tmp")
    os.environ["QB_CREDENTIAL_HOT_RELOAD"] = "0"
    for service in ("MINERU", "MINIMAX", "SILICONFLOW", "MODELSCOPE"):
        os.environ[f"QB_{service}_CONFIGURED"] = "0"
        os.environ[f"QB_{service}_POOL_SIZE"] = "0"
    os.environ["DJANGO_SETTINGS_MODULE"] = "qb_server.settings"
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.core.management import call_command
    from django.conf import settings
    assert Path(settings.DATABASES["default"]["NAME"]).resolve() == OUTPUT / "db.sqlite3"
    assert Path(settings.DATA_ROOT).resolve() == OUTPUT / "data"
    call_command("migrate", verbosity=0)
    with db() as connection:
        assert all(json.loads(row[0]).get("demo") for row in connection.execute("SELECT structure FROM core_paper"))
        assert connection.execute("SELECT count(*) FROM core_publishedquestion").fetchone()[0] == 0


def db():
    return sqlite3.connect((OUTPUT / "db.sqlite3").as_uri() + "?mode=ro", uri=True)


def demo_only():
    with db() as connection:
        for structure, source, rendered in connection.execute("SELECT structure, source_path, render_path FROM core_paper"):
            assert json.loads(structure).get("demo") is True
            assert Path(source).resolve().is_relative_to(OUTPUT / "data")
            assert Path(rendered).resolve().is_relative_to(OUTPUT / "data")
        assert connection.execute("SELECT count(*) FROM core_publishedquestion").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM core_question WHERE reread_requested=1").fetchone()[0] == 0


def question(number):
    with db() as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT q.* FROM core_question q JOIN core_paper p ON p.id=q.paper_id WHERE q.number=? AND p.archived=0 ORDER BY q.id DESC LIMIT 1", (number,)).fetchone()
        return dict(row) if row else None


def question_rows():
    with db() as connection:
        return connection.execute("SELECT * FROM core_question ORDER BY id").fetchall()


def allow_demo_write(path):
    if path == "/api/demo":
        return True
    match = re.fullmatch(r"/api/questions/(\d+)/(approve|text|figures)", path)
    if match:
        with db() as connection:
            row = connection.execute("SELECT p.structure FROM core_question q JOIN core_paper p ON p.id=q.paper_id WHERE q.id=?", (int(match[1]),)).fetchone()
        return bool(row and json.loads(row[0]).get("demo") is True)
    match = re.fullmatch(r"/api/papers/([0-9a-f-]+)/(approve-green|publish)", path)
    if match:
        with db() as connection:
            row = connection.execute("SELECT structure FROM core_paper WHERE id=?", (match[1].replace("-", ""),)).fetchone()
        return bool(row and json.loads(row[0]).get("demo") is True)
    return False


def run(port):
    if port == 8768:
        raise SystemExit("The installed application's port is forbidden")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"Port {port} is occupied; no existing service will be used or stopped")
    configure()
    report = {"passed": [], "failures": [], "page_errors": [], "forbidden_requests": [],
              "writes": [], "worker_started": False, "user_data_used": False,
              "database": os.environ["QB_DATABASE"], "data_root": os.environ["QB_DATA_ROOT"], "port": port}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (OUTPUT / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, str(ROOT / "backend/manage.py"), "runserver", f"127.0.0.1:{port}", "--noreload"],
                                  cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Owned isolated server exited before startup")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(.1)
            else:
                raise TimeoutError("Owned isolated server did not become ready")
            check(f"http://127.0.0.1:{port}", report)
            demo_only()
            report["only_demo_records"] = True
        except Exception as error:
            report["failures"].append({"error": str(error), "traceback": traceback.format_exc()})
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=flags, check=False)
            else:
                server.terminate()
            server.wait(timeout=10)
            report["server_stopped"] = True
            (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    assert not report["failures"], report["failures"]
    assert not report["page_errors"] and not report["forbidden_requests"], report
    print(f"Teaching browser checks passed ({len(report['passed'])} groups); demo-only server stopped", flush=True)


def check(url, report):
    from playwright.sync_api import expect, sync_playwright

    with sync_playwright() as pw:
        executable = next((str(path) for path in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        contexts = []
        try:
            def create_page(width=1440, legacy=None):
                context = browser.new_context(viewport={"width": width, "height": 1000})
                contexts.append(context)
                init = "localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0');"
                if legacy is not None:
                    init += f"if (!localStorage.getItem('qa-teach-initialized')) {{ localStorage.setItem('qb-teach', {json.dumps(json.dumps(legacy))}); localStorage.setItem('qa-teach-initialized','1'); }}"
                context.add_init_script(init)

                def allowed(route):
                    request = route.request
                    parsed = urlparse(request.url)
                    permitted = parsed.scheme == "http" and parsed.netloc == urlparse(url).netloc
                    if permitted and request.method not in ("GET", "HEAD"):
                        permitted = request.method == "POST" and allow_demo_write(parsed.path)
                    if not permitted:
                        report["forbidden_requests"].append(f"{request.method} {request.url}")
                        route.abort()
                    else:
                        if request.method not in ("GET", "HEAD"):
                            report["writes"].append({"method": request.method, "path": parsed.path})
                        route.continue_()

                context.route("**/*", allowed)
                page = context.new_page()
                page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
                page.on("dialog", lambda dialog: dialog.dismiss())
                page.goto(url)
                page.wait_for_load_state("networkidle")
                return page

            def storage(page):
                return page.evaluate("JSON.parse(localStorage.getItem('qb-teach') || 'null')")

            def lesson(page, key):
                page.wait_for_function("key => JSON.parse(localStorage.getItem('qb-teach') || 'null')?.lesson === key", arg=key)
                expect(page.locator("#teachPanel")).to_be_visible()

            def card(page, number):
                row = question(number)
                assert row
                return page.locator(f'.card[data-id="{row["id"]}"]')

            def help_entry(page, selector):
                page.locator("#settingsButton").click()
                page.get_by_role("tab", name="帮助", exact=True).click()
                page.locator(selector).click()
                expect(page.locator("#settingsDialog")).not_to_be_visible()

            def visible_guide(page, dialog=None):
                panel = page.locator("#teachPanel")
                expect(panel).to_be_visible()
                if dialog:
                    assert panel.evaluate("n=>n.parentElement.id") == dialog
                box = panel.bounding_box()
                bounds = page.viewport_size
                assert box and box["x"] >= -1 and box["y"] >= -1
                assert box["x"] + box["width"] <= bounds["width"] + 1
                assert box["y"] + box["height"] <= bounds["height"] + 1, box
                close = page.locator("#teachClose")
                assert close.evaluate("n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}"), "Guide close button is covered"

            def open_editor(page, number):
                target = card(page, number)
                if target.locator(".compact").count() or "compact" in (target.get_attribute("class") or ""):
                    target.get_by_role("button", name="展开", exact=True).click()
                target.get_by_role("button", name="改字", exact=True).click()
                editor = target.locator(".editor")
                expect(editor).to_be_visible()
                return editor

            def settle_dialog(page, selector):
                expect(page.locator(selector)).to_be_visible()
                page.locator(selector).evaluate("n=>Promise.all(n.getAnimations().map(a=>a.finished))")

            def discard_editor(page, editor):
                editor.get_by_role("button", name="取消", exact=True).click()
                expect(page.locator("#confirmDialog")).to_be_visible()
                page.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
                expect(editor).to_have_count(0)

            page = create_page()
            page.screenshot(path=str(OUTPUT / "initial.png"))
            help_entry(page, "#settingsLearn")
            lesson(page, "card")
            assert question(1)["approved"] == 0
            visible_guide(page)
            page.locator("#teachNext").click()
            lesson(page, "viewer")
            card(page, 1).locator(".source-note").click()
            settle_dialog(page, "#viewerDialog")
            visible_guide(page, "viewerDialog")
            page.wait_for_function("[...document.querySelectorAll('#viewerCrop img')].every(i=>i.complete&&i.naturalWidth>0)")
            stage = page.locator("#viewerSource")
            point = stage.bounding_box()
            page.mouse.move(point["x"] + point["width"] / 2, point["y"] + point["height"] / 2)
            initial_zoom = page.locator("#zoomLevel").inner_text()
            page.keyboard.down("Control"); page.mouse.wheel(0, -220); page.keyboard.up("Control")
            assert page.locator("#zoomLevel").inner_text() != initial_zoom
            page.mouse.down(); page.mouse.move(point["x"] + point["width"] / 2 - 30, point["y"] + point["height"] / 2 - 30, steps=3); page.mouse.up()
            assert storage(page)["lesson"] == "viewer", "Opening a viewer should not prematurely finish the manual zoom lesson"
            page.locator("#teachNext").click()
            lesson(page, "tick")
            expect(page.locator("#viewerDialog")).not_to_be_visible()
            card(page, 3).locator(".card-tick").click()
            page.wait_for_timeout(1250)
            assert question(3)["approved"] == 1 and storage(page)["lesson"] == "tick"
            card(page, 1).locator(".card-tick").click()
            lesson(page, "fix")
            assert question(1)["approved"] == 1
            report["passed"].append("Basic tutorial opens without keys/OCR, real viewer zoom/pan remains a manual step, only question 1 approval advances")

            original = question(9)["stem"]
            assert "3 个单位" in original
            editor = open_editor(page, 9)
            editor.locator(".stem-input").fill(original.replace("3 个单位", "4 个单位"))
            writes_before = len(report["writes"])
            discard_editor(page, editor)
            assert question(9)["stem"] == original and len(report["writes"]) == writes_before
            assert storage(page)["lesson"] == "fix"
            editor = open_editor(page, 9)
            editor.locator(".stem-input").fill(original.replace("3 个单位", "4 个单位"))
            editor.get_by_role("button", name="保存", exact=True).click()
            expect(page.locator("#teachHint")).to_be_visible()
            assert "4 个单位" in question(9)["stem"] and storage(page)["lesson"] == "fix"
            editor = open_editor(page, 9)
            editor.locator(".stem-input").fill(original.replace("3 个单位", "5 个单位"))
            editor.get_by_role("button", name="保存", exact=True).click()
            lesson(page, "figure")
            assert "5 个单位" in question(9)["stem"]
            report["passed"].append("Cancelling a real dirty edit preserves demo text and lesson; saving a wrong answer gives a hint; correct saved text advances")

            card(page, 2).get_by_role("button", name="补选配图", exact=True).click()
            settle_dialog(page, "#pageDialog")
            visible_guide(page, "pageDialog")
            candidate = page.locator("#pageStage .candidate").first
            expect(candidate).to_be_visible()
            candidate.click()
            page.locator('[data-figure-slot="stem"]').click()
            assert json.loads(question(2)["figures"]) == []
            page.locator("#pageDialogClose").click()
            assert json.loads(question(2)["figures"]) == [] and storage(page)["lesson"] == "figure"
            card(page, 2).get_by_role("button", name="补选配图", exact=True).click()
            settle_dialog(page, "#pageDialog")
            page.locator("#pageStage .candidate").first.click()
            page.locator('[data-figure-slot="stem"]').click()
            page.locator("#pageDialogSave").click()
            lesson(page, "publish")
            assert len(json.loads(question(2)["figures"])) == 1
            page.locator("#publishButton").click()
            settle_dialog(page, "#confirmDialog")
            expect(page.locator("#confirmTitle")).to_have_text("示例试卷不会入库")
            visible_guide(page, "confirmDialog")
            page.locator("#teachFold").click()
            expect(page.locator("#teachDetails")).not_to_be_visible()
            # Real readers spend more than the auto-advance delay reading this
            # explanation. Its confirmation must still finish the lesson.
            page.wait_for_timeout(1250)
            page.locator("#confirmDialog").get_by_role("button", name="知道了", exact=True).click()
            lesson(page, "basics")
            expect(page.locator("#teachDetails")).to_be_visible()
            demo_only()
            report["passed"].append("Picking a figure and cancelling does not save; saving the candidate persists and advances; demo publish explains without creating a formal question")

            page.screenshot(path=str(OUTPUT / "basics.png"))
            paper_id = storage(page)["paper"]
            report["demo_paper_id"] = paper_id
            page.locator("#teachSkip").click()
            expect(page.locator("#teachPanel")).not_to_be_visible()
            assert storage(page) is None
            reviewed_before = question_rows()
            help_entry(page, "#settingsNewFeatures")
            lesson(page, "original")
            assert storage(page)["paper"] == paper_id and question_rows() == reviewed_before
            expect(page.locator("#teachSection")).to_have_text("新版功能")
            page.locator("#teachShow").click()
            settle_dialog(page, "#pageDialog")
            visible_guide(page, "pageDialog")
            expect(page.locator("#pageDialogTitle")).to_have_text("查看整份原卷")
            expect(page.locator("#pageDialogSave")).not_to_be_visible()
            page.locator("#teachNext").click()
            lesson(page, "preview")
            expect(page.locator("#pageDialog")).not_to_be_visible()
            page.locator("#teachShow").click()
            editor = card(page, 9).locator(".editor")
            expect(editor).to_be_visible()
            editor.locator(".stem-input").fill(question(9)["stem"] + " 临时练习改动")
            page.locator("#teachNext").click()
            settle_dialog(page, "#confirmDialog")
            visible_guide(page, "confirmDialog")
            page.locator("#confirmDialog").get_by_role("button", name="继续编辑", exact=True).click()
            assert storage(page)["lesson"] == "preview"
            assert editor.locator(".stem-input").input_value().endswith(" 临时练习改动")
            page.locator("#teachNext").click()
            page.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
            lesson(page, "region")
            expect(editor).to_have_count(0)
            assert question_rows() == reviewed_before
            page.locator("#teachShow").click()
            settle_dialog(page, "#pageDialog")
            visible_guide(page, "pageDialog")
            expect(page.locator("#readTargetSelect")).to_have_value("auto")
            expect(page.locator("#pageDialogSave")).to_be_disabled()
            page.wait_for_function("document.querySelector('#pageStage img')?.complete&&document.querySelector('#pageStage img')?.naturalWidth>0")
            page.locator("#teachFold").click()
            expect(page.locator("#teachDetails")).not_to_be_visible()
            visible_guide(page, "pageDialog")
            image = page.locator("#pageStage .stage-surface").bounding_box()
            stage_box = page.locator("#pageStage").bounding_box()
            start_x = max(image["x"], stage_box["x"]) + 25
            start_y = max(image["y"], stage_box["y"]) + 25
            page.mouse.move(start_x, start_y); page.mouse.down()
            page.mouse.move(start_x + 60, start_y + 35, steps=4); page.mouse.up()
            expect(page.locator("#pageStage .edit-box:not(.ghost)")).to_have_count(1)
            page.screenshot(path=str(OUTPUT / "region-practice.png"))
            page.locator("#teachNext").click()
            lesson(page, "library")
            expect(page.locator("#pageDialog")).not_to_be_visible()
            page.locator("#teachShow").click()
            expect(page.locator("#teachText")).to_contain_text("版本历史")
            page.locator("#teachNext").click()
            lesson(page, "recovery")
            page.locator("#teachShow").click()
            settle_dialog(page, "#settingsDialog")
            visible_guide(page, "settingsDialog")
            expect(page.locator(".teaching-help")).to_have_attribute("open", "")
            page.locator("#teachNext").click()
            lesson(page, "finish")
            expect(page.locator("#settingsDialog")).not_to_be_visible()
            page.locator("#teachNext").click()
            expect(page.locator("#teachPanel")).not_to_be_visible()
            assert storage(page) is None and question_rows() == reviewed_before
            report["passed"].append("Base course can end early; optional features reuse the same demo, show real controls, protect dirty text before advancing, allow offline region drawing, and close learning windows on each transition")

            for index, expected in ((0, "card"), (4, "fix"), (10, "basics"), (-1, "card"), (99, "card")):
                migrated = create_page(650, {"paper": paper_id, "index": index})
                lesson(migrated, expected)
                saved = storage(migrated)
                assert saved == {"paper": paper_id, "version": 2, "lesson": expected, "completed": False}, saved
                visible_guide(migrated)
                migrated.reload(wait_until="networkidle")
                lesson(migrated, expected)
                assert storage(migrated) == saved
                migrated.locator("#teachClose").click()
                expect(migrated.locator("#teachPanel")).not_to_be_visible()
                assert storage(migrated) is None
            assert question_rows() == reviewed_before
            report["passed"].append("Legacy numeric progress migrates by lesson content (including finished/invalid values), persists after reload, and closes without touching demo records")

            for key, modal in (("viewer", "viewerDialog"), ("original", "pageDialog"), ("region", "pageDialog")):
                narrow = create_page(390, {"paper": paper_id, "version": 2, "lesson": key})
                lesson(narrow, key)
                visible_guide(narrow)
                narrow.locator("#teachShow").click()
                settle_dialog(narrow, "#" + modal)
                narrow.wait_for_timeout(750)
                visible_guide(narrow, modal)
                narrow.screenshot(path=str(OUTPUT / f"{key}-390.png"))
                narrow.locator("#teachFold").click()
                expect(narrow.locator("#teachDetails")).not_to_be_visible()
                visible_guide(narrow, modal)
                narrow.locator("#teachFold").click()
                expect(narrow.locator("#teachDetails")).to_be_visible()
                narrow.locator("#teachNext").click()
                expect(narrow.locator("#" + modal)).not_to_be_visible()
                narrow.locator("#teachClose").click()
                expect(narrow.locator("#teachPanel")).not_to_be_visible()
                assert storage(narrow) is None
            assert question_rows() == reviewed_before
            report["passed"].append("390px viewer, full original and region practice keep the guide/close control visible; collapse/expand and next/close work in the real modal top layer")

            exit_in_confirm = create_page(650, {"paper": paper_id, "version": 2, "lesson": "publish"})
            lesson(exit_in_confirm, "publish")
            exit_in_confirm.locator("#publishButton").click()
            settle_dialog(exit_in_confirm, "#confirmDialog")
            visible_guide(exit_in_confirm, "confirmDialog")
            exit_in_confirm.locator("#teachFold").click()
            expect(exit_in_confirm.locator("#teachDetails")).not_to_be_visible()
            exit_in_confirm.locator("#teachClose").click()
            expect(exit_in_confirm.locator("#teachPanel")).not_to_be_visible()
            exit_in_confirm.wait_for_timeout(1250)
            exit_in_confirm.locator("#confirmDialog").get_by_role("button", name="知道了", exact=True).click()
            exit_in_confirm.wait_for_timeout(1250)
            assert storage(exit_in_confirm) is None
            expect(exit_in_confirm.locator("#teachPanel")).not_to_be_visible()
            assert question_rows() == reviewed_before
            report["passed"].append("A slow explanation can be collapsed and teaching can be exited inside its confirmation; later closing the dialog cannot restart or advance the exited course")

            blocked = create_page(650, {"paper": paper_id, "version": 2, "lesson": "fix"})
            lesson(blocked, "fix")
            untouched_four = question(4)["stem"]
            editor_four = open_editor(blocked, 4)
            editor_four.locator(".stem-input").fill(untouched_four + " 尚未保存的练习文字")
            saved_nine = question(9)["stem"]
            editor_nine = open_editor(blocked, 9)
            editor_nine.locator(".stem-input").fill(saved_nine + " （另一题尚未保存）")
            editor_nine.get_by_role("button", name="保存", exact=True).click()
            expect(blocked.locator("#teachDone")).to_be_visible()
            expect(blocked.locator("#confirmDialog")).to_be_visible(timeout=5000)
            blocked.locator("#confirmDialog").get_by_role("button", name="继续编辑", exact=True).click()
            lesson(blocked, "fix")
            expect(blocked.locator("#teachNext")).to_have_text("继续下一步")
            completed_saved = storage(blocked)
            assert completed_saved["completed"] is True
            assert editor_four.locator(".stem-input").input_value().endswith(" 尚未保存的练习文字")
            assert question(4)["stem"] == untouched_four

            resumed = create_page(650, completed_saved)
            lesson(resumed, "fix")
            expect(resumed.locator("#teachNext")).to_have_text("继续下一步")
            resumed.locator("#teachNext").click()
            lesson(resumed, "figure")
            resumed.locator("#teachClose").click()

            blocked.locator("#teachNext").click()
            blocked.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
            lesson(blocked, "figure")
            expect(editor_four).to_have_count(0)
            assert question(4)["stem"] == untouched_four
            blocked.locator("#teachClose").click()
            restore_nine = open_editor(blocked, 9)
            restore_nine.locator(".stem-input").fill(saved_nine)
            restore_nine.get_by_role("button", name="保存", exact=True).click()
            blocked.wait_for_function("document.querySelector('.editor') === null")
            assert question(9)["stem"] == saved_nine
            report["passed"].append("An accepted fix blocked by another dirty editor retains completed progress and a Continue button; both keeping edits and restoring saved completion can safely continue without re-saving the task")

            pending_page = create_page(650, {"paper": paper_id, "version": 2, "lesson": "fix"})
            lesson(pending_page, "fix")
            pending_original = question(9)["stem"]
            pending_editor = open_editor(pending_page, 9)
            pending_editor.locator(".stem-input").fill(pending_original + " （延迟回归练习）")
            pending_editor.get_by_role("button", name="保存", exact=True).click()
            expect(pending_page.locator("#teachDone")).to_be_visible()
            pending_page.locator("#teachClose").click()
            expect(pending_page.locator("#teachPanel")).not_to_be_visible()
            pending_page.wait_for_timeout(1300)
            assert storage(pending_page) is None
            help_entry(pending_page, "#settingsNewFeatures")
            lesson(pending_page, "original")
            pending_page.wait_for_timeout(1300)
            assert storage(pending_page)["lesson"] == "original", "A stale completion advanced the restarted tutorial"
            pending_page.locator("#teachClose").click()
            pending_editor = open_editor(pending_page, 9)
            pending_editor.locator(".stem-input").fill(pending_original)
            pending_editor.get_by_role("button", name="保存", exact=True).click()
            pending_page.wait_for_function("document.querySelector('.editor') === null")
            assert question(9)["stem"] == pending_original
            demo_only()
            report["passed"].append("Closing during the real 1100ms completion delay cancels the pending transition; reopening only-new-features is not advanced by a stale callback")
        except Exception:
            if contexts:
                contexts[-1].pages[-1].screenshot(path=str(OUTPUT / "failure.png"))
            raise
        finally:
            for context in contexts:
                context.close()
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--port", type=int, default=8991)
    args = parser.parse_args()
    if not args.run:
        parser.error("Choose --run")
    run(args.port)
