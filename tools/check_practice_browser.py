"""Verify the practical onboarding through real controls on a fresh offline server.

No arguments or --help only display help. --run creates one private folder under
checkout/tmp, migrates an empty database, starts its own loopback Django server
without a worker, and completes crop/edit/approval/practice PDF export in a fresh
browser. Credentials, formal question banks and real cloud APIs are excluded.
The browser and owned server are stopped in finally, including failed checks.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
from urllib.parse import urlparse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = USER = RECEIPT = BASE = BACKEND_PYTHON = None
REPORT = {}


def private_environment(user: Path, url: str) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items()
        if not key.upper().startswith(("QB_", "TIYOUJU_", "DJANGO_", "PYTHONPATH"))
        and not any(marker in key.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "ACCESS_KEY", "AUTHORIZATION", "PASSWORD"))}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", DJANGO_SETTINGS_MODULE="qb_server.settings",
        QB_USER_ROOT=str(user), QB_DATABASE=str(user / "db.sqlite3"), QB_DATA_ROOT=str(user / "data"),
        QB_CREDENTIAL_FILE=str(user / "credentials.bin"), QB_FEATURES_FILE=str(user / "features.json"),
        QB_LIBRARY_AI_SETTINGS_FILE=str(user / "library-ai.json"),
        QB_LIBRARY_AI_CREDENTIAL_FILE=str(user / "library-ai-credentials.bin"),
        QB_MODEL_PREFERENCES_FILE=str(user / "models.json"), QB_CREDENTIAL_HOT_RELOAD="0",
        LOCALAPPDATA=str(user / "appdata"), APPDATA=str(user / "roaming"),
        QB_DESKTOP_EXPORT="0", TIYOUJU_URL=url)
    for service in ("MINERU", "MINIMAX", "SILICONFLOW", "MODELSCOPE"):
        env[f"QB_{service}_CONFIGURED"] = "0"; env[f"QB_{service}_POOL_SIZE"] = "0"
    return env


def confined_output(value: str | Path) -> Path:
    output = Path(value).resolve()
    if not output.is_relative_to((ROOT / "tmp").resolve()) or output == (ROOT / "tmp").resolve():
        raise ValueError("Evidence and private data must be in a child folder of checkout/tmp")
    return output


def bootstrap(user: Path) -> None:
    assert user.is_relative_to((ROOT / "tmp").resolve())
    assert Path(os.environ["QB_USER_ROOT"]).resolve() == user
    assert Path(os.environ["QB_DATABASE"]).resolve() == user / "db.sqlite3"
    assert Path(os.environ["QB_DATA_ROOT"]).resolve() == user / "data"
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()


def forbid_remote_connections() -> None:
    original = socket.socket.connect
    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise OSError("Private practice check forbids external network connections")
        return original(sock, address)
    socket.socket.connect = local_connect


def runtime(explicit: str | None, modules: tuple[str, ...]) -> str:
    choices = [explicit] if explicit else [sys.executable, str(ROOT / "packaging/.build/venv/Scripts/python.exe"),
        getattr(sys, "_base_executable", None), shutil.which("python")]
    seen = set()
    for candidate in choices:
        if not candidate or candidate in seen or not Path(candidate).is_file(): continue
        seen.add(candidate)
        check = subprocess.run([candidate, "-c", ";".join(f"import {name}" for name in modules)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if check.returncode == 0: return str(Path(candidate).resolve())
    raise RuntimeError("No Python runtime contains " + ", ".join(modules) + "; pass --server-python / --browser-python")


def db():
    conn = sqlite3.connect((USER / "db.sqlite3").as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def question(number):
    with db() as conn:
        row = conn.execute("SELECT * FROM core_question WHERE number=? ORDER BY id DESC LIMIT 1", (number,)).fetchone()
    return dict(row) if row else None


def demo_owner(kind, id):
    with db() as conn:
        if kind == "question":
            row = conn.execute("SELECT p.structure FROM core_paper p JOIN core_question q ON q.paper_id=p.id WHERE q.id=?", (int(id),)).fetchone()
        else:
            row = conn.execute("SELECT structure FROM core_paper WHERE id=?", (id.replace("-", ""),)).fetchone()
    return bool(row and json.loads(row[0]).get("demo"))


def allowed_write(path):
    if path == "/api/demo": return True
    q = re.fullmatch(r"/api/questions/(\d+)/(text|approve|type|figures)", path)
    if q: return demo_owner("question", q[1])
    p = re.fullmatch(r"/api/papers/([0-9a-f-]+)/questions", path)
    if p: return demo_owner("paper", p[1])
    p = re.fullmatch(r"/api/demo/([0-9a-f-]+)/(preview|export-pdf)", path)
    if p: return demo_owner("paper", p[1])
    return False


def browser_check():
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as pw:
        chrome = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": chrome} if chrome else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0');")
        def route_guard(route):
            request = route.request; parsed = urlparse(request.url)
            allowed = parsed.scheme in ("data", "blob", "about") or (parsed.scheme == "http" and parsed.netloc == urlparse(BASE).netloc)
            if allowed and request.method not in ("GET", "HEAD"):
                allowed = request.method == "POST" and allowed_write(parsed.path)
            if not allowed:
                REPORT["forbidden_requests"].append(f"{request.method} {request.url}"); route.abort()
            else:
                if request.method not in ("GET", "HEAD"): REPORT["writes"].append({"method":request.method,"path":parsed.path})
                route.continue_()
        context.route("**/*", route_guard)
        page = context.new_page(); page.on("pageerror", lambda e: REPORT["page_errors"].append(str(e)))
        page.on("dialog", lambda d: d.dismiss())
        def stage(key):
            page.wait_for_function("key=>JSON.parse(localStorage.getItem('qb-teach')||'null')?.lesson===key", arg=key)
            expect(page.locator("#teachPanel")).to_be_visible()
        def card(number):
            return page.locator(f'.card[data-id="{question(number)["id"]}"]')
        try:
            page.goto(BASE + "/settings#help"); page.wait_for_load_state("networkidle")
            expect(page.locator("#settingsLearn")).to_have_text("开始新手练习")
            assert page.locator("#settingsReview .explain-list:visible").count() == 0
            page.locator("#settingsHelpKeys").click()
            expect(page.locator("#keysDialog")).to_be_visible()
            page.locator("#keysDialog select").select_option("crop")
            assert "Ctrl+S" in page.locator("#keysDialog").inner_text()
            page.locator("#keysDialog").get_by_role("button", name="关闭", exact=True).click()
            page.screenshot(path=str(OUTPUT / "help-clean.png"), full_page=True)
            REPORT["passed"].append("help starts with practice, FAQ and actual shortcut content; details collapsed")
            page.locator("#settingsLearn").click(); page.wait_for_load_state("networkidle"); stage("cut")
            page.wait_for_function("!new URL(location.href).searchParams.has('learn')")
            assert question(1) is None and question(2) and question(9)
            page.locator("#teachShow").click()
            expect(page.locator("#pageDialog")).to_be_visible()
            page.wait_for_function("()=>{const i=document.querySelector('#pageStage img');return i?.complete&&i.naturalWidth>0}")
            image = page.locator("#pageStage img").bounding_box(); assert image
            # Demo q1 bounds in the shipped source. A real two-click gesture,
            # with a margin around its stem and all four options.
            x1=image['x']+image['width']*.035; y1=image['y']+image['height']*.145
            x2=image['x']+image['width']*.88; y2=image['y']+image['height']*.255
            page.mouse.click(x1,y1); page.mouse.click(x2,y2)
            page.locator("#pageStage").focus(); page.keyboard.press("s"); stage("cutComplete")
            assert question(1)["body_mode"] == "source_image"
            # Closing the guide pauses it without losing the saved crop.
            page.locator("#teachClose").click()
            expect(page.locator("#teachPanel")).not_to_be_visible()
            paused = page.evaluate("JSON.parse(localStorage.getItem('qb-teach'))")
            assert paused['lesson'] == 'cutComplete' and paused['active'] is False
            page.locator("#pageDialogClose").click()
            page.reload(wait_until="networkidle")
            expect(page.locator("#teachPanel")).not_to_be_visible()
            assert question(1)['body_mode'] == 'source_image'
            page.goto(BASE + "/settings#help"); page.wait_for_load_state("networkidle")
            expect(page.locator("#settingsLearn")).to_have_text("继续新手练习")
            page.locator("#settingsLearn").click(); stage("cutComplete")
            page.locator("#teachShow").click()
            expect(page.locator("#pageDialog")).to_be_visible()
            page.locator("#pageStage").focus(); page.keyboard.press("Control+s"); stage("fix")
            REPORT['passed'].append('closing the guide pauses the saved crop; refresh stays quiet and help resumes without duplicating question1')
            expect(page.locator("#pageDialog")).not_to_be_visible()
            page.screenshot(path=str(OUTPUT / "practice-cut-complete.png"), full_page=True)
            REPORT["passed"].append("actual two-click crop; S creates question1; CtrlS ends crop without OCR")
            page.locator("#teachShow").click()
            editor = card(9).locator(".editor"); expect(editor).to_be_visible()
            field = editor.locator(".stem-input"); original=field.input_value(); assert "3 个单位" in original
            # Another dirty editor must block the delayed automatic move.
            original_two = question(2)['stem']
            card(2).get_by_role('button', name='改字', exact=True).click()
            dirty = card(2).locator('.editor')
            dirty.locator('.stem-input').fill(original_two + ' 尚未保存的练习文字')
            field.fill(original.replace("3 个单位", "5 个单位"))
            editor.get_by_role("button", name="保存", exact=True).click()
            expect(page.locator('#teachDone')).to_be_visible()
            expect(page.locator('#confirmDialog')).to_be_visible(timeout=6000)
            page.locator('#confirmDialog').get_by_role('button', name='继续编辑', exact=True).click()
            stage('fix')
            blocked_progress = page.evaluate("JSON.parse(localStorage.getItem('qb-teach'))")
            assert blocked_progress['completed'] is True
            expect(page.locator('#teachNext')).to_have_text('继续下一步')
            assert dirty.locator('.stem-input').input_value().endswith(' 尚未保存的练习文字')
            assert question(2)['stem'] == original_two
            resumed = context.new_page()
            resumed.goto(BASE + '/?paper=' + blocked_progress['paper']); resumed.wait_for_load_state('networkidle')
            expect(resumed.locator('#teachNext')).to_have_text('继续下一步')
            resumed.locator('#teachNext').click()
            resumed.wait_for_function("JSON.parse(localStorage.getItem('qb-teach')).lesson==='tick9'")
            resumed.close()
            page.locator('#teachNext').click()
            expect(page.locator('#confirmDialog')).to_be_visible()
            page.locator('#confirmDialog').get_by_role('button', name='丢弃改动', exact=True).click()
            stage('tick9')
            assert question(2)['stem'] == original_two
            expect(dirty).to_have_count(0)
            REPORT['passed'].append('another dirty editor blocks completed fix transition; continue/discard and refreshed completed progress work without another save')
            assert "5 个单位" in question(9)["stem"]
            page.locator("#teachShow").click(); card(9).locator(".card-tick").click(); stage("tick")
            page.locator("#teachShow").click(); card(1).locator(".card-tick").click(); stage("library")
            assert question(1)["approved"] and question(9)["approved"]
            with db() as conn: assert conn.execute("SELECT COUNT(*) FROM core_question").fetchone()[0] == 3
            REPORT["passed"].append("wrong 3 fixed to5; both actual questions individually approved; 3 demo cards total")
            page.locator("#teachShow").click(); page.wait_for_url("**/practice/*"); page.wait_for_load_state("networkidle")
            expect(page.locator("#practiceList input[type=checkbox]")).to_have_count(2)
            page.locator("#practiceList input[type=checkbox]").nth(0).check()
            page.locator("#practiceList input[type=checkbox]").nth(1).check()
            page.locator("#practicePreview").click()
            expect(page.locator("#practicePages")).to_contain_text("页", timeout=60000)
            page.wait_for_function("()=>document.querySelector('#practiceFrame').contentWindow.__qbPdfStatus?.ready")
            preview_pages=page.locator("#practiceFrame").evaluate("n=>n.contentWindow.__qbPdfStatus.page_count")
            page.screenshot(path=str(OUTPUT / "practice-preview.png"), full_page=True)
            with page.expect_download(timeout=90000) as pending: page.locator("#practiceExport").click()
            download=pending.value; destination=OUTPUT / "practice-actual.pdf"; download.save_as(destination)
            expect(page.locator("#practiceDone")).to_be_visible()
            check=subprocess.run([BACKEND_PYTHON,"-c","import pymupdf,sys;d=pymupdf.open(sys.argv[1]);print(d.page_count)",str(destination)], capture_output=True, text=True, timeout=20)
            assert check.returncode == 0, check.stderr
            export_pages=int(check.stdout.strip()); assert export_pages == preview_pages
            saved=page.evaluate("JSON.parse(localStorage.getItem('qb-teach'))")
            assert saved['lesson']=='finish' and saved['completed'] is True
            REPORT.update(preview_pages=preview_pages, exported_pdf_pages=export_pages, pdf_bytes=destination.stat().st_size,
                exported_pdf=str(destination), teaching_complete=saved)
            REPORT["passed"].append("actual isolated practice selection, shared-layout preview and downloaded PDF pages match")
            page.screenshot(path=str(OUTPUT / "practice-complete.png"), full_page=True)
            page.goto(BASE+"/settings#help"); page.wait_for_load_state("networkidle")
            expect(page.locator("#settingsLearn")).to_have_text("回看已完成的练习")
            REPORT["passed"].append("help recognises completed practical course after navigation")
            # Legacy course preparation is confined to a demo, never a formal
            # question. It intentionally models old saved progress, not a claim
            # that the new course's missing actions have been done.
            legacy = page.evaluate("""async () => {
              const r=await fetch('/api/demo',{method:'POST',headers:{'Content-Type':'application/json','X-QB-Request':'1'},body:JSON.stringify({reset:true})});
              if(!r.ok)throw Error('legacy example setup failed');return (await r.json()).paper;
            }""")
            legacy_id=legacy['id']
            page.evaluate("old=>localStorage.setItem('qb-teach',JSON.stringify(old))", {"paper":legacy_id,"version":2,"lesson":"library","completed":True})
            with db() as conn: old_count=conn.execute('SELECT COUNT(*) FROM core_question').fetchone()[0]
            assert old_count > 3
            page.goto(BASE+'/settings#help'); page.wait_for_load_state('networkidle')
            page.locator('#settingsLearn').click()
            expect(page.locator('#confirmDialog')).to_be_visible()
            expect(page.locator('#confirmTitle')).to_have_text('重新开始新版示例练习？')
            page.locator('#confirmDialog').get_by_role('button',name='取消',exact=True).click()
            page.wait_for_function("!new URL(location.href).searchParams.has('learn')")
            page.reload(wait_until='networkidle')
            expect(page.locator('#confirmDialog')).not_to_be_visible()
            with db() as conn:
                assert conn.execute('SELECT COUNT(*) FROM core_question').fetchone()[0] == old_count
                assert conn.execute('SELECT id FROM core_paper').fetchone()[0] == legacy_id.replace('-','')
            page.goto(BASE+'/settings#help'); page.wait_for_load_state('networkidle')
            page.locator('#settingsLearn').click()
            expect(page.locator('#confirmDialog')).to_be_visible()
            page.locator('#confirmDialog').get_by_role('button',name='重置示例并开始',exact=True).click()
            page.wait_for_function("old=>{const u=new URL(location.href);return u.searchParams.get('paper')!==old&&!u.searchParams.has('learn')}",arg=legacy_id)
            stage('cut')
            with db() as conn: assert conn.execute('SELECT COUNT(*) FROM core_question').fetchone()[0] == 2
            assert question(1) is None
            REPORT['passed'].append('legacy text-only course never claims new actions complete; cancelling reset keeps old demo and accepting rebuilds only the example')
            # Recheck the modal guide at a narrow viewport with real controls.
            page.set_viewport_size({'width':390,'height':844})
            page.locator('#teachShow').click()
            expect(page.locator('#pageDialog')).to_be_visible()
            page.wait_for_function("()=>{const i=document.querySelector('#pageStage img');return i?.complete&&i.naturalWidth>0}")
            assert page.locator('#teachPanel').evaluate('n=>n.parentElement.id') == 'pageDialog'
            guide=page.locator('#teachPanel').bounding_box(); assert guide
            assert guide['x'] >= -1 and guide['x']+guide['width'] <= 391
            assert page.locator('#teachClose').evaluate("n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}")
            page.locator('#teachFold').click(); expect(page.locator('#teachDetails')).not_to_be_visible()
            page.locator('#teachFold').click(); expect(page.locator('#teachDetails')).to_be_visible()
            page.screenshot(path=str(OUTPUT/'guide-390.png'),full_page=True)
            page.locator('#pageDialogClose').click()
            page.set_viewport_size({'width':1440,'height':1050})
            page.locator('#teachShow').click()
            page.wait_for_function("()=>{const i=document.querySelector('#pageStage img');return i?.complete&&i.naturalWidth>0}")
            image=page.locator('#pageStage img').bounding_box()
            page.mouse.click(image['x']+image['width']*.035,image['y']+image['height']*.145)
            page.mouse.click(image['x']+image['width']*.88,image['y']+image['height']*.255)
            page.locator('#pageStage').focus();page.keyboard.press('s');stage('cutComplete')
            page.locator('#pageStage').focus();page.keyboard.press('Control+s');stage('fix')
            page.locator('#teachShow').click()
            editor=card(9).locator('.editor');field=editor.locator('.stem-input')
            field.fill(field.input_value().replace('3 个单位','5 个单位'))
            editor.get_by_role('button',name='保存',exact=True).click()
            expect(page.locator('#teachDone')).to_be_visible()
            page.locator('#teachClose').click()
            expect(page.locator('#teachPanel')).not_to_be_visible()
            before=page.evaluate("JSON.parse(localStorage.getItem('qb-teach'))")
            assert before['lesson']=='fix' and before['completed'] and before['active'] is False
            # Wait beyond the specific 1100ms pending-transition deadline.
            page.wait_for_timeout(1450)
            assert page.evaluate("JSON.parse(localStorage.getItem('qb-teach'))") == before
            page.reload(wait_until='networkidle');expect(page.locator('#teachPanel')).not_to_be_visible()
            page.goto(BASE+'/settings#help');page.wait_for_load_state('networkidle')
            page.locator('#settingsLearn').click();stage('fix')
            expect(page.locator('#teachNext')).to_have_text('继续下一步')
            page.locator('#teachNext').click();stage('tick9')
            REPORT['passed'].append('390px modal guide remains usable; closing during completion cancels stale timers and resumes accepted action after refresh')
        finally:
            page.screenshot(path=str(OUTPUT / "browser-last.png"), full_page=True)
            context.close(); browser.close()
    with db() as conn:
        REPORT['formal_publications']=conn.execute("SELECT COUNT(*) FROM core_publishedquestion").fetchone()[0]
        REPORT['jobs']=conn.execute("SELECT COUNT(*) FROM core_libraryjob").fetchone()[0]
        REPORT['reread_requested']=conn.execute("SELECT COUNT(*) FROM core_question WHERE reread_requested=1").fetchone()[0]
        assert REPORT['formal_publications']==REPORT['jobs']==REPORT['reread_requested']==0
        assert all(json.loads(row[0]).get('demo') for row in conn.execute("SELECT structure FROM core_paper"))
    assert not (USER/'credentials.bin').exists() and not (USER/'library-ai-credentials.bin').exists()
    assert not REPORT['page_errors'] and not REPORT['forbidden_requests'], REPORT


def internal_mode(mode: str, output: Path, port: int) -> int:
    global OUTPUT, USER, RECEIPT, BASE, BACKEND_PYTHON, REPORT
    OUTPUT = confined_output(output); USER = OUTPUT / "user"
    BASE = f"http://127.0.0.1:{port}"
    forbid_remote_connections()
    if mode in ("migrate", "serve"):
        bootstrap(USER)
        from django.core.management import call_command
        if mode == "migrate":
            call_command("migrate", interactive=False, verbosity=0)
            from core.models import Paper, Question, PublishedQuestion, LibraryJob
            assert not any(model.objects.exists() for model in (Paper, Question, PublishedQuestion, LibraryJob))
        else:
            call_command("runserver", f"127.0.0.1:{port}", use_reloader=False, verbosity=1)
        return 0
    RECEIPT = json.loads((OUTPUT / "server.json").read_text(encoding="utf-8"))
    assert Path(RECEIPT["isolated_root"]).resolve() == USER
    assert RECEIPT["url"] == BASE
    BACKEND_PYTHON = RECEIPT["backend_python"]
    REPORT = {"passed": [], "page_errors": [], "forbidden_requests": [], "writes": [],
        "real_user_data_used": False, "isolated_root": str(USER), "worker_started": False}
    try:
        browser_check(); REPORT["success"] = True
    except Exception as error:
        REPORT.update(success=False, error=str(error), traceback=traceback.format_exc())
    (OUTPUT / "report.json").write_text(json.dumps(REPORT, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": REPORT["success"], "passed_groups": len(REPORT["passed"]), "error": REPORT.get("error")}))
    return 0 if REPORT["success"] else 1


def run(args) -> int:
    if args.port == 8768 or not 1024 <= args.port <= 65535:
        raise ValueError("Use an unoccupied test port other than the installed application's 8768")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("Test port occupied: no existing service will be reused or stopped")
    backend_python = runtime(args.server_python, ("django", "pymupdf"))
    browser_python = runtime(args.browser_python, ("playwright.sync_api",))
    output = confined_output(args.output or ROOT / "tmp" / ("practice-browser-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]))
    if output.exists():
        raise RuntimeError("Evidence folder already exists; choose a new folder instead of reusing its database")
    output.mkdir(parents=True); user = output / "user"; user.mkdir(); (user / "data").mkdir()
    url = f"http://127.0.0.1:{args.port}"; env = private_environment(user, url)
    command = [str(Path(__file__).resolve()), "--output", str(output), "--port", str(args.port)]
    migrated = subprocess.run([backend_python, *command, "--internal-mode", "migrate"],
        cwd=ROOT, env=env, capture_output=True, timeout=90)
    (output / "migration.log").write_bytes(migrated.stdout + migrated.stderr)
    if migrated.returncode:
        raise RuntimeError("Empty private database migration failed; inspect " + str(output / "migration.log"))
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server = browser_process = None; result = 1
    receipt = {"url": url, "isolated_root": str(user), "backend_python": backend_python,
        "browser_python": browser_python, "worker_started": False, "cloud_called": False,
        "real_credentials_available": False, "remote_socket_connections_blocked": True, "preseeded_questions": 0}
    try:
        with (output / "server.log").open("ab") as log:
            server = subprocess.Popen([backend_python, *command, "--internal-mode", "serve"],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        receipt["pid"] = server.pid
        (output / "server.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})); deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if server.poll() is not None: raise RuntimeError("Owned private server exited during startup")
            try:
                with opener.open(url + "/api/status", timeout=1) as response: status = json.load(response)
                receipt["app_version"] = status.get("app_version")
                assert isinstance(receipt["app_version"], str) and receipt["app_version"]
                break
            except OSError: time.sleep(.1)
        else: raise TimeoutError("Owned private server did not become ready")
        (output / "server.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        with (output / "browser.log").open("wb") as log:
            browser_process = subprocess.Popen([browser_python, *command, "--internal-mode", "browser"],
                cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            receipt["browser_pid"] = browser_process.pid
            result = browser_process.wait(timeout=420)
        if not (output / "report.json").exists():
            (output / "report.json").write_text(json.dumps({"success": False, "error": "Browser check did not produce its report; inspect browser.log"}), encoding="utf-8")
    except Exception as error:
        result = 1
        (output / "runner-error.json").write_text(json.dumps({"error": str(error), "traceback": traceback.format_exc()}, indent=2), encoding="utf-8")
    finally:
        # The browser subprocess is owned just like the server; terminate its
        # process tree on timeout/interruption so Chromium cannot be orphaned.
        if browser_process is not None:
            if browser_process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(browser_process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=20)
                else: browser_process.terminate()
            browser_process.wait(timeout=20)
            receipt["browser_stopped"] = browser_process.poll() is not None
        else: receipt["browser_stopped"] = True
        if server is not None:
            if server.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, timeout=20)
                else: server.terminate()
            server.wait(timeout=20)
            receipt["server_stopped"] = server.poll() is not None
        else: receipt["server_stopped"] = True
        (output / "server.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        if (output / "report.json").exists():
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            report["server_stopped"] = receipt["server_stopped"]
            report["browser_stopped"] = receipt["browser_stopped"]
            report["app_version"] = receipt.get("app_version")
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"success": result == 0, "output": str(output), "server_stopped": receipt["server_stopped"], "browser_stopped": receipt["browser_stopped"]}))
    return 0 if result == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run the real course against a newly isolated server")
    parser.add_argument("--port", type=int, default=8991, help="Unoccupied loopback test port (default: 8991)")
    parser.add_argument("--output", help="New evidence folder under checkout/tmp; omitted creates a random folder")
    parser.add_argument("--server-python", help="Python with Django and PyMuPDF; automatically detected by default")
    parser.add_argument("--browser-python", help="Python with Playwright; automatically detected by default")
    parser.add_argument("--internal-mode", choices=("migrate", "serve", "browser"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.internal_mode:
        if not args.output: parser.error("Internal mode requires --output")
        return internal_mode(args.internal_mode, Path(args.output), args.port)
    if not args.run:
        parser.print_help(); return 0
    try: return run(args)
    except Exception as error:
        print(str(error), file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
