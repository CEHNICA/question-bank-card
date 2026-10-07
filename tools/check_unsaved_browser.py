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

        def expect_warning():
            expect(page.locator("#confirmDialog")).to_be_visible()
            expect(page.locator("#confirmTitle")).to_have_text("改字还没保存")
            # 破坏性的默认焦点不能落在「丢弃改动」上。
            expect(page.locator("#confirmDialog [value=cancel]")).to_be_focused()

        def watch_dialog():
            # 关掉对话框是同步的，但 close 事件是另派的一个任务，守卫的「正在询问」
            # 标记在那之后才清掉。实测这段窗口 6.5–11.6ms：真人两个动作差一百毫秒
            # 以上，按不到；Playwright 每个动作是独立任务，机器一忙就跨过去了。
            # 所以监听必须在关掉之前挂好 —— 关完再去看 open 已经是 false，
            # 那样量到的只是「已经关了」，不是「事件派发完了」。
            page.evaluate("""() => {
              const dialog = document.getElementById('confirmDialog');
              window.__qbDialogSettled = false;
              dialog.addEventListener('close', () => requestAnimationFrame(
                () => requestAnimationFrame(() => { window.__qbDialogSettled = true; })),
                {once: true});
            }""")

        def after_dialog():
            # 等 close 事件真的派发完，再让下一次动作发生。不猜时长。
            page.wait_for_function("() => window.__qbDialogSettled === true", timeout=10000)
            page.evaluate("() => { window.__qbDialogSettled = false; }")

        def keep_editing():
            watch_dialog()
            page.locator("#confirmDialog").get_by_role("button", name="继续编辑", exact=True).click()
            expect(page.locator("#confirmDialog")).not_to_be_visible()
            expect(page.locator(".card.editing")).to_have_count(1)
            after_dialog()

        def discard():
            watch_dialog()
            page.locator("#confirmDialog").get_by_role("button", name="丢弃改动", exact=True).click()
            expect(page.locator("#confirmDialog")).not_to_be_visible()
            expect(page.locator(".card.editing")).to_have_count(0)
            after_dialog()

        def dismiss_with_escape():
            # 原生对话框按 Esc 关掉走的是同一条路，但没人调 confirmDialog，
            # 守卫的标记同样要等 close 事件派发完才清。
            watch_dialog()
            page.keyboard.press("Escape")
            expect(page.locator("#confirmDialog")).not_to_be_visible()
            after_dialog()

        def ask_by(action, where):
            # 守卫清掉「正在询问」的那一刻在页面里看不到，只能看见对话框会不会弹。
            # 所以不死等一次动作：像真人那样把同一个动作再做一遍，直到警告框真的
            # 出现，但有次数上限 —— 守卫要是真的卡死，这里会红，不会被磨过去。
            # 同一个按钮连点两下、真人两下之间隔几十毫秒，落在窗口里就是这一下。
            for _ in range(20):
                action()
                try:
                    expect(page.locator("#confirmDialog")).to_be_visible(timeout=250)
                    break
                except AssertionError:
                    continue
            else:
                raise AssertionError(f"{where}：重复了 20 次也没有弹出改字警告（守卫可能卡住）")
            expect_warning()

        def ask_to_leave(target, where):
            ask_by(lambda: target.press("Escape"), where)

        def ask_by_back(card, where):
            ask_by(lambda: card.get_by_role("button", name="← 返回", exact=True).click(), where)

        def leave_editor_keeping():
            # Esc 的监听挂在改字面板上，焦点必须在里面才收得到（真人手就停在
            # 输入框里）；blur 到 body 之后按 Esc 什么也不会发生。
            ask_to_leave(page.locator(".card.editing .stem-input"), "改字面板里按 Esc")
            keep_editing()

        def choose_paper(item, dirty=True):
            # 改字时顶栏和试卷列表都收着，界面上点不到另一份卷 —— 先退出来。
            # 改过字的退出去一定要被问一句，没改过的直接就退了。
            if page.locator(".card.editing").count():
                if dirty:
                    ask_to_leave(page.locator(".card.editing .stem-input"), "改字中切卷")
                    discard()
                else:
                    page.locator(".card.editing .stem-input").press("Escape")
                expect(page.locator(".card.editing")).to_have_count(0, timeout=8000)
            page.locator("#paperList .paper-link").filter(has_text=item["name"]).click()

        # 1.12.7 起点「改字」就占满整屏：顶栏和左边的试卷列表都收起来了
        # （body.qb-editing 把 .topbar 和 .sidebar 一起 display:none），
        # 「改字中直接点另一份卷」这个动作在界面上已经不存在了。真正要守的
        # 是另一件事：没保存的字不会自己没了。出路只剩 Esc / 取消 / 返回，
        # 三条都要先问一句；选「继续编辑」一个字都不能少。
        editor = open_editor("切卷时应该保留的临时输入")
        expect(page.locator("#paperList")).not_to_be_visible()
        leave_editor_keeping()
        expect(editor.locator(".stem-input")).to_have_value("切卷时应该保留的临时输入")
        page.screenshot(path=str(OUTPUT / "unsaved-warning.png"))
        # 放弃改动才真的退得出去；退干净之后换卷不该再多问一次。
        choose_paper(second)
        expect(page.locator("#paperName")).to_have_text(second["name"])
        expect(page.locator("#confirmDialog")).not_to_be_visible()
        expect(page.locator(".editor")).to_have_count(0)
        choose_paper(first)
        expect(page.locator("#paperName")).to_have_text(first["name"])

        editor = open_editor("取消前的临时输入")
        # 面板上原来那个「取消」已经改名成「← 返回」；能主动离开改字的按钮
        # 就是它和 Esc（面板下方写着「Ctrl+Enter 保存 · Esc 取消」）。
        # 工具栏不是改字表单的一部分，要从题卡上找。
        card = page.locator(".card.editing")
        ask_by_back(card, "改字面板里点「← 返回」")
        keep_editing()
        expect(editor.locator(".stem-input")).to_have_value("取消前的临时输入")
        ask_by_back(card, "选过「继续编辑」之后再点「← 返回」")
        discard()
        expect(page.locator(".editor")).to_have_count(0)

        editor = open_editor("Esc 前的临时输入")
        editor.locator(".stem-input").press("Escape")
        expect_warning()
        dismiss_with_escape()
        expect(editor.locator(".stem-input")).to_have_value("Esc 前的临时输入")
        ask_to_leave(editor.locator(".stem-input"), "原生 Esc 关掉对话框之后")
        discard()
        expect(page.locator(".editor")).to_have_count(0)

        editor = open_editor("跳转题库前的临时输入")
        # 改字时顶栏也一起收起来了（body.qb-editing .topbar display:none），
        # 所以「改字中点导航去题库」这个动作在界面上根本不存在。出路只剩
        # 改字面板自己那几个：Esc、「← 返回」、保存。
        expect(page.locator("#drawerTrigger")).not_to_be_visible()
        leave_editor_keeping()
        expect(editor.locator(".stem-input")).to_have_value("跳转题库前的临时输入")
        assert urlparse(page.url).path == "/"
        # 放弃改动之后导航恢复正常，而且不会再多问一句。
        ask_to_leave(page.locator(".card.editing .stem-input"), "放弃改动之后")
        discard()
        page.locator("#drawerTrigger").click()
        page.locator("#siteDrawer").wait_for(state="visible", timeout=8000)
        page.locator('.topnav a[href="/library"]').click()
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

        # Editing back to the original value should leave the editor without any
        # question, and a paper switch then needs no confirmation either.
        editor = open_editor("稍后会改回去")
        editor.locator(".stem-input").fill(first["stem"])
        choose_paper(second, dirty=False)
        expect(page.locator("#paperName")).to_have_text(second["name"])
        expect(page.locator("#confirmDialog")).not_to_be_visible()
        choose_paper(first)
        expect(page.locator("#paperName")).to_have_text(first["name"])
        editor = open_editor(first["stem"])
        editor.locator(".stem-input").press("Escape")
        expect(page.locator(".editor")).to_have_count(0)
        expect(page.locator("#confirmDialog")).not_to_be_visible()
        page.screenshot(path=str(OUTPUT / "restored.png"))
        assert len(dialogs) == 2, dialogs
        assert not errors, errors
        assert not forbidden, forbidden
        context.close()
        browser.close()
    assert question_snapshot() == before, "UI tests must not save or change any fixture questions"
    print("Browser checks passed: unsaved edits are unreachable from outside the editor (chrome is folded away), Esc and 返回 ask before letting go, 继续编辑 keeps every character, discard then paper switch and navigation are clean, native refresh still warns; no mutating requests or question changes", flush=True)


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
