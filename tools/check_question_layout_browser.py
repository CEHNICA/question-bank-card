"""Real pointer and persisted-layout acceptance, with synthetic private data.

--run creates an unused child of checkout/tmp, builds a fictional old-schema
database, upgrades it with the selected source/frozen program, and exercises
the real loopback UI. No worker, installed service, credentials or cloud API
is used. Writes are allowed only to this fixture's layout and undo endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request
from urllib.parse import urlparse
import uuid

import check_practice_browser as private
from check_frozen_pdf_browser import stop_owned, wait_owned

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def database_rows():
    db = Path(os.environ["QB_DATABASE"]).resolve()
    assert db.is_relative_to((ROOT / "tmp").resolve())
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return {table: [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
                for table in ("core_paper", "core_question", "core_publishedquestion", "core_regionread", "core_libraryjob")}


def bootstrap():
    user = Path(os.environ["QB_USER_ROOT"]).resolve()
    assert user.is_relative_to((ROOT / "tmp").resolve())
    assert Path(os.environ["QB_DATABASE"]).resolve() == user / "db.sqlite3"
    assert Path(os.environ["QB_DATA_ROOT"]).resolve() == user / "data"
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()


def seed_old(output):
    bootstrap()
    import pymupdf as fitz
    from django.conf import settings
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    target = [("core", "0020_alter_question_state")]
    executor.migrate(target)
    apps = executor.loader.project_state(target).apps
    Paper = apps.get_model("core", "Paper")
    Question = apps.get_model("core", "Question")
    Group = apps.get_model("core", "QuestionGroup")
    paper = Paper.objects.create(filename="虚构题框校正验收.pdf", kind="pdf", sha256="layout-synthetic-main",
        status="ready", pages=[{"page_idx": i, "width": 600, "height": 900} for i in range(2)],
        processing_plan={"schema": 1, "mode": "manual", "revision": 0}, total=4, progress=4)
    groups = [Group.objects.create(paper=paper, title=title, sequence=i, kind="exam",
              page_start=1, page_end=2, metadata={"pages": [0, 1]})
              for i, title in enumerate(("虚构试卷甲", "虚构试卷乙"))]
    regions = [
        [{"page_idx": 0, "bbox": [70, 85, 930, 410]}],
        [{"page_idx": 0, "bbox": [70, 255, 930, 360]}],
        [{"page_idx": 0, "bbox": [70, 500, 930, 680]}, {"page_idx": 1, "bbox": [70, 65, 930, 180]}],
        [{"page_idx": 1, "bbox": [70, 300, 930, 450]}],
    ]
    rows = []
    for i, parts in enumerate(regions):
        rows.append(Question.objects.create(paper=paper, group=groups[0 if i < 3 else 1],
            number=i + 1 if i < 3 else 1, regions=parts, regions_auto=parts,
            question_type="free_response", state="yellow", stem=f"人工保存的虚构第 {i + 1} 题内容。",
            processing_mode="auto" if i == 0 else "manual", body_mode="text", edited=True,
            type_locked=True, text_source="human", start_source="manual"))
    folder = settings.DATA_ROOT / str(paper.pk)
    folder.mkdir(parents=True)
    source = folder / "source.pdf"
    document = fitz.open()
    for index in range(2):
        page = document.new_page(width=600, height=900)
        lines = ((65, "虚构试卷甲：所有内容仅用于界面验收"), (120, "1. 已知函数 f(x)=x²+1，求 f(2)。"),
                 (255, "2. 计算 2+3。此题在第一题的大框内部。"),
                 (465, "3. 求图中三角形的面积，本题续在下一页。"),
                 (690, "4. 这是一道尚未添加题框的漏题。")) if index == 0 else (
                 (70, "3. 续题：三角形底为 4，高为 3。"),
                 (290, "虚构试卷乙：题号再次从 1 开始"), (335, "1. 比较二分之一与三分之一。"))
        for y, text in lines:
            page.insert_text((45, y), text, fontname="china-s", fontsize=15)
        if index == 0:
            page.draw_polyline([(110, 580), (260, 580), (200, 500), (110, 580)], color=(0, 0, .8), width=2)
    document.save(source)
    document.close()
    paper.source_path = str(source)
    paper.save(update_fields=["source_path"])
    stress = Paper.objects.create(filename="虚构一百题.pdf", kind="pdf", sha256="layout-synthetic-stress",
        status="ready", pages=[{"page_idx": 0, "width": 1000, "height": 1000}], total=100, progress=100,
        processing_plan={"schema": 1, "mode": "manual", "revision": 0})
    folder = settings.DATA_ROOT / str(stress.pk)
    folder.mkdir()
    document = fitz.open()
    page = document.new_page(width=1000, height=1000)
    for i in range(100):
        x, y = 5 + (i % 10) * 98, 5 + (i // 10) * 98
        parts = [{"page_idx": 0, "bbox": [x, y, x + 90, y + 87]}]
        page.insert_text((x + 5, y + 20), f"{i+1}. fixture", fontsize=9)
        Question.objects.create(paper=stress, number=i + 1, regions=parts, regions_auto=parts,
            question_type="free_response", state="yellow", stem=f"虚构第 {i+1} 题。",
            processing_mode="manual", start_source="manual")
    source = folder / "source.pdf"
    document.save(source)
    document.close()
    stress.source_path = str(source)
    stress.save(update_fields=["source_path"])
    write_json(output / "old-schema-snapshot.json", database_rows())
    write_json(output / "fixture.json", {"paper": str(paper.pk), "stress": str(stress.pk),
        "first": rows[0].pk, "nested": rows[1].pk, "crosspage": rows[2].pk, "duplicate": rows[3].pk,
        "group": groups[0].pk, "other_group": groups[1].pk})
    print("Fictional schema-0020 fixture created", flush=True)


def finish_seed(output):
    bootstrap()
    from django.utils import timezone
    from core import library
    from core.models import Question
    before = json.loads((output / "old-schema-snapshot.json").read_text(encoding="utf-8"))
    after = database_rows()
    for table, rows in before.items():
        assert len(rows) == len(after[table]), f"Migration changed row count: {table}"
        for old, new in zip(rows, after[table]):
            assert all(new[key] == value for key, value in old.items()), f"Migration changed original values: {table} id={old['id']}"
    assert all(0 <= q.color_index <= 5 for q in Question.objects.all())
    fixture = json.loads((output / "fixture.json").read_text(encoding="utf-8"))
    question = Question.objects.get(pk=fixture["nested"])
    library.approve(question, now=timezone.now(), source="human", agent="")
    question.save()
    publication, _created = library.publish(question, queue_enrichment=False)
    fixture["publication"] = str(publication.pk)
    write_json(output / "fixture.json", fixture)
    write_json(output / "migration-verification.json", {"success": True, "old_schema": "0020",
        "question_rows_preserved": len(before["core_question"]), "original_values_preserved": True,
        "colour_backfill_passed": True, "fixture_publication_created_after_migration": True})
    print("Old-schema upgrade preserves all original fields; colour backfill PASS", flush=True)


def browser_check(output, port):
    from playwright.sync_api import sync_playwright, expect
    base = f"http://127.0.0.1:{port}"
    fixture = json.loads((output / "fixture.json").read_text(encoding="utf-8"))
    report = {"success": False, "passed": [], "page_errors": [], "forbidden_requests": [], "writes": [],
              "real_user_data_used": False, "worker_started": False, "cloud_called": False}
    with sync_playwright() as pw:
        binary = next((p for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": str(binary)} if binary else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0')")

        def guard(route):
            request = route.request
            parsed = urlparse(request.url)
            allowed = parsed.scheme == "http" and parsed.netloc == urlparse(base).netloc
            if allowed and request.method not in ("GET", "HEAD"):
                allowed = request.method == "POST" and parsed.path.startswith(f"/api/papers/{fixture['paper']}/question-layout")
            if not allowed:
                report["forbidden_requests"].append({"method": request.method, "path": parsed.path})
                route.abort()
            else:
                if request.method == "POST":
                    report["writes"].append({"method": "POST", "path": parsed.path, "payload": request.post_data_json})
                route.continue_()
        context.route("**/*", guard)
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("dialog", lambda dialog: dialog.dismiss())

        def paper_data(paper_id=fixture["paper"]):
            result = page.request.get(base + "/api/papers/" + paper_id)
            assert result.ok, result.text()
            return result.json()

        def open_paper(paper_id=fixture["paper"]):
            page.goto(base + "/?paper=" + paper_id)
            page.wait_for_load_state("networkidle")
            page.locator("#paperMenu > summary").click()
            page.locator("#viewOriginalPaper").click()
            expect(page.locator("#pageDialog")).to_be_visible()
            page.wait_for_function("document.querySelector('#pageStage img')?.complete && document.querySelector('#pageStage img')?.naturalWidth > 0")
            expect(page.locator("#pageLayoutPanel")).to_be_visible()

        try:
            open_paper()
            original = paper_data()
            expect(page.locator("[data-layout-question-id]")).to_have_count(4)
            assert original["paper"]["layout_revision"] == 0
            assert len({q["color_index"] for q in original["questions"]}) >= 3
            page.screenshot(path=str(output / "overview-desktop.png"))
            report["passed"].append("old-schema upgrade, four distinct question identities, persistent palette and two question groups")
            run_ui_cases(page, expect, output, fixture, report, paper_data)
            expect(page.locator("#pageDialogClose")).to_be_enabled()
            page.locator("#pageDialogClose").click()
            open_paper(fixture["stress"])
            expect(page.locator("[data-layout-question-id]")).to_have_count(100)
            page.screenshot(path=str(output / "one-hundred-questions.png"))
            report["passed"].append("100-question overview and palette")
            page.set_viewport_size({"width": 650, "height": 1050})
            page.screenshot(path=str(output / "overview-narrow.png"))
            assert not report["page_errors"] and not report["forbidden_requests"], report
            report["success"] = True
        except Exception as error:
            report.update(error=str(error) or type(error).__name__, traceback=traceback.format_exc())
            try:
                page.screenshot(path=str(output / "failure.png"))
            except Exception:
                pass
        finally:
            context.close()
            browser.close()
    write_json(output / "report.json", report)
    print(json.dumps({"success": report["success"], "error": report.get("error"),
                      "passed_groups": len(report["passed"])}, ensure_ascii=False), flush=True)
    return 0 if report["success"] else 1


def run_ui_cases(page, expect, output, fixture, report, paper_data):
    def question(key, data=None):
        return next(q for q in (data or paper_data())["questions"] if q["id"] == fixture[key])

    def select(key):
        page.locator(f'[data-layout-question-id="{fixture[key]}"]').click()

    def fit():
        page.locator("#pageZoomFit").click()
        page.wait_for_function("document.querySelector('#pageStage img')?.complete && document.querySelector('#pageStage img')?.naturalWidth > 0")
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")

    def point(x, y):
        rect = page.locator("#pageStage .stage-surface").bounding_box()
        assert rect
        return rect["x"] + x * rect["width"] / 1000, rect["y"] + y * rect["height"] / 1000

    def draw(bbox):
        expect(page.locator("#pageLayoutDraw")).to_be_visible()
        if page.locator("#pageLayoutDraw").get_attribute("aria-pressed") != "true":
            page.locator("#pageLayoutDraw").click()
        fit()
        page.mouse.click(*point(*bbox[:2]))
        page.mouse.move(*point(*bbox[2:]))
        page.mouse.click(*point(*bbox[2:]))

    def drag(locator, dx, dy, cancel=False):
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        rect = locator.bounding_box()
        assert rect
        x, y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
        report["last_pointer_target"] = page.evaluate("([x,y]) => document.elementFromPoint(x,y)?.outerHTML", [x, y])
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + dx, y + dy, steps=3)
        if cancel:
            page.keyboard.press("Escape")
        page.mouse.up()

    def geometry():
        return page.locator('[data-layout-box-index="0"]').get_attribute("style")

    def save():
        revision = paper_data()["paper"]["layout_revision"]
        page.locator("#pageDialogSave").click()
        expect(page.locator("#pageLayoutConfirmSave")).to_be_visible()
        report["before_last_save"] = paper_data()
        with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
            page.locator("#pageLayoutConfirmSave").click()
        response = waiting.value
        report["last_save_response"] = {"status": response.status, "body": response.json()}
        expect(page.locator("#pageLayoutEdit")).to_be_visible()
        assert paper_data()["paper"]["layout_revision"] > revision

    def cancel_draft():
        page.locator("#pageLayoutCancelDraft").click()
        if page.locator("#confirmDialog").is_visible():
            page.locator("#confirmOk").click()
        expect(page.locator("#pageLayoutEdit")).to_be_visible()

    def undo(reopen=False):
        if reopen:
            page.locator("#pageDialogClose").click()
            page.reload()
            page.wait_for_load_state("networkidle")
            page.locator("#paperMenu > summary").click()
            page.locator("#viewOriginalPaper").click()
            expect(page.locator("#pageLayoutPanel")).to_be_visible()
        history = page.locator("#pageLayoutHistory")
        if history.get_attribute("open") is None:
            history.locator("summary").first.click()
        expect(page.locator("#pageLayoutUndoSaved")).to_be_enabled()
        before = paper_data()["paper"]["layout_revision"]
        page.locator("#pageLayoutUndoSaved").click()
        expect(page.locator("#confirmDialog")).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url.endswith("/undo")) as waiting:
            page.locator("#confirmOk").click()
        assert waiting.value.ok, waiting.value.text()
        expect(page.locator("#pageLayoutUndoSaved")).to_be_disabled()
        assert paper_data()["paper"]["layout_revision"] > before

    original = paper_data()
    fit()
    page.mouse.click(*point(500, 290))
    expect(page.locator("#pageLayoutOverlap")).to_be_visible()
    assert "第 2 题" in page.locator('[data-layout-hit="0"]').inner_text()
    page.locator('[data-layout-hit="0"]').click()
    expect(page.locator(f'[data-layout-question-id="{fixture["nested"]}"]')).to_have_attribute("aria-pressed", "true")
    assert paper_data() == original and not report["writes"]
    report["passed"].append("overlapping nested boxes choose the smaller question first without editing or OCR")

    select("first")
    page.locator("#pageLayoutEdit").click()
    fit()
    before = geometry()
    # The interior is never a drag handle, even for a large movement.
    x, y = point(500, 140)
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 30, y + 10)
    page.mouse.up()
    assert geometry() == before
    label = page.locator('[data-layout-drag-label="0"]')
    drag(label, 3, 0)
    assert geometry() == before
    expect(page.locator("#pageUndo")).to_be_disabled()
    page.locator("#pageZoomIn").click()
    drag(page.locator('[data-layout-drag-label="0"]'), 0, 3)
    assert geometry() == before
    expect(page.locator("#pageUndo")).to_be_disabled()
    fit()
    before = geometry()
    drag(page.locator('[data-layout-drag-label="0"]'), 15, 10, cancel=True)
    assert geometry() == before
    expect(page.locator("#pageUndo")).to_be_disabled()
    page.locator("#pageZoomIn").click()
    center = point(500, 400)
    page.mouse.move(*center)
    page.mouse.down(button="right")
    page.mouse.move(center[0] - 25, center[1] - 35)
    page.mouse.up(button="right")
    assert geometry() == before
    assert paper_data() == original and not report["writes"]
    fit()
    # One selected edge, one geometry change; content and processing mode survive.
    drag(page.locator('[data-layout-box-index="0"] [data-layout-handle="e"]'), -12, 0)
    expect(page.locator("#pageUndo")).to_be_enabled()
    changed = geometry()
    page.locator("#pageUndo").click()
    assert geometry() == before
    page.locator("#pageRedo").click()
    assert geometry() == changed
    zoom = page.locator("#pageZoomLevel").inner_text()
    save()
    after = question("first")
    old = question("first", original)
    assert after["regions"] != old["regions"]
    for key in ("stem", "edited_stem", "edited_options", "body_mode", "processing_mode", "question_type", "color_index"):
        assert after.get(key) == old.get(key), key
    assert not after["approved"] and not after["ocr_pending"]
    assert page.locator("#pageZoomLevel").inner_text() == zoom
    page.screenshot(path=str(output / "range-saved.png"))
    report["passed"].append("interior selection, 3-pixel jitter at two zooms, Escape rollback, right-pan, draft undo/redo and in-place save preserve manual content")
    undo(reopen=True)
    restored = question("first")
    assert restored["regions"] == old["regions"] and restored["content_revision"] > old["content_revision"]
    report["passed"].append("latest saved operation survives reload and undo increases versions")

    select("crosspage")
    page.locator("#pageLayoutEdit").click()
    page.locator('[data-layout-part-index="1"] button').first.click()
    fit()
    expect(page.locator('[data-layout-box-index="1"]')).to_be_visible()
    assert page.locator('[data-layout-box-index="0"] [data-layout-handle]').count() == 0
    page.locator('[data-layout-box-index="1"]').focus()
    page.keyboard.press("ArrowDown")
    page.locator('[data-layout-part-index="0"] button').first.click()
    page.locator('[data-layout-part-index="1"] button').first.click()
    expect(page.locator("#pageUndo")).to_be_enabled()
    page.locator("#pageUndo").click()
    expect(page.locator("#pageUndo")).to_be_disabled()
    cancel_draft()
    assert question("crosspage")["regions"] == question("crosspage", original)["regions"]
    report["passed"].append("cross-page draft and keyboard fine adjustment retain ordered pieces without saving")

    # Return to page one before supplementing the uncovered fictional question.
    select("first")
    page.locator("#pageLayoutPanel").get_by_role("button", name="定位当前题", exact=True).click()
    page.locator("#pageLayoutAdd").click()
    draw([70, 730, 930, 840])
    page.locator("#pageLayoutNumber").fill("4")
    page.locator("#pageLayoutNumber").press("Tab")
    save()
    added = next(q for q in paper_data()["questions"] if q["id"] not in {r["id"] for r in original["questions"]})
    assert added["body_mode"] == "source_image" and added["processing_mode"] == "manual"
    assert not added["approved"] and not added["ocr_pending"]
    assert len(paper_data()["questions"]) == 5
    page.screenshot(path=str(output / "supplement-saved.png"))
    undo(reopen=True)
    assert len(paper_data()["questions"]) == 4
    report["passed"].append("supplement an uncovered question as a manual original-image question, then persistent undo")

    select("nested")
    old_nested = question("nested")
    assert old_nested["approved"]
    page.locator("#pageLayoutSplit").click()
    expect(page.locator('[data-layout-target-index]')).to_have_count(2)
    fit()
    rect = page.locator('[data-layout-box-index="0"]').bounding_box()
    page.mouse.click(rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)
    # Overlap may include the containing first question: select the split target explicitly.
    if page.locator("#pageLayoutOverlap").is_visible():
        candidates = page.locator("#pageLayoutOverlap [data-layout-hit]")
        for i in range(candidates.count()):
            if "第 2 题" in candidates.nth(i).inner_text():
                candidates.nth(i).click()
                break
    surface = page.locator("#pageStage .stage-surface").bounding_box()
    drag(page.locator('[data-layout-box-index="0"] [data-layout-handle="s"]'), 0, -50 * surface["height"] / 1000)
    page.locator('[data-layout-target-index="1"]').click()
    draw([70, 308, 930, 360])
    page.locator("#pageDialogSave").click()
    expect(page.locator("#pageLayoutConfirmSave")).to_be_visible()
    page.screenshot(path=str(output / "split-confirmation.png"))
    page.locator("#pageLayoutConfirmSave").click()
    expect(page.locator("#pageLayoutEdit")).to_be_visible()
    split_data = paper_data()
    assert len(split_data["questions"]) == 5
    assert fixture["nested"] not in {q["id"] for q in split_data["questions"]}
    new = [q for q in split_data["questions"] if q["id"] not in {r["id"] for r in original["questions"]}]
    assert len(new) == 2 and all(q["body_mode"] == "source_image" and not q["approved"] and not q["ocr_pending"] for q in new)
    # Read the actual private DB to verify publication status and preserved content.
    database = database_rows()
    publication = next(r for r in database["core_publishedquestion"] if uuid.UUID(str(r["id"])) == uuid.UUID(fixture["publication"]))
    assert publication["status"] == "withdrawn"
    undo(reopen=True)
    restored = question("nested")
    assert restored["regions"] == old_nested["regions"] and restored["stem"] == old_nested["stem"]
    assert not restored["approved"]
    assert next(r for r in database_rows()["core_publishedquestion"] if uuid.UUID(str(r["id"])) == uuid.UUID(fixture["publication"]))["status"] == "withdrawn"
    report["passed"].append("true two-question split withdraws the current publication; undo restores content without approval or publication")

    select("first")
    page.locator("#pageLayoutMerge").click()
    page.locator(f'[data-layout-merge-id="{fixture["crosspage"]}"]').check()
    page.locator("#pageLayoutMergeBegin").click()
    expect(page.locator('[data-layout-part-index]')).to_have_count(3)
    save()
    merged = paper_data()
    assert len(merged["questions"]) == 3
    assert fixture["first"] not in {q["id"] for q in merged["questions"]}
    assert fixture["crosspage"] not in {q["id"] for q in merged["questions"]}
    new = next(q for q in merged["questions"] if q["id"] not in {r["id"] for r in original["questions"]})
    assert len(new["regions"]) == 3 and new["body_mode"] == "source_image" and not new["ocr_pending"]
    undo(reopen=True)
    assert {q["id"] for q in paper_data()["questions"]} == {q["id"] for q in original["questions"]}
    report["passed"].append("true same-group merge retains cross-page piece order; undo restores original visible identities")

    # A second window changes the server state while this window holds its draft.
    select("first")
    page.locator("#pageLayoutEdit").click()
    fit()
    drag(page.locator('[data-layout-box-index="0"] [data-layout-handle="e"]'), -10, 0)
    current = paper_data()
    concurrent = question("crosspage", current)
    changed_regions = json.loads(json.dumps(concurrent["regions"]))
    changed_regions[0]["bbox"][2] -= 5
    base = f"http://127.0.0.1:{urlparse(page.url).port}"
    payload = {"kind": "regions", "layout_revision": current["paper"]["layout_revision"],
        "client_request_id": str(uuid.uuid4()), "sources": [{"id": concurrent["id"], "revision": concurrent["content_revision"], "fingerprint": concurrent["layout_fingerprint"]}],
        "targets": [{"regions": changed_regions}]}
    result = page.request.post(f"{base}/api/papers/{fixture['paper']}/question-layout", data=payload,
                               headers={"X-QB-Request": "1"})
    assert result.ok, result.text()
    page.locator("#pageDialogSave").click()
    page.locator("#pageLayoutConfirmSave").click()
    expect(page.locator("#pageLayoutConflict")).to_be_visible()
    expect(page.locator('[data-layout-box-index="0"]')).to_be_visible()
    page.screenshot(path=str(output / "conflict-retains-draft.png"))
    assert question("first")["regions"] == old["regions"]
    cancel_draft()
    undo(reopen=True)
    report["passed"].append("two-window stale layout is rejected while the local draft remains available")

    # The real server commits a merge; the browser loses its response, then
    # recovers the same receipt through GET without making another POST.
    select("first")
    page.locator("#pageLayoutMerge").click()
    page.locator(f'[data-layout-merge-id="{fixture["crosspage"]}"]').check()
    page.locator("#pageLayoutMergeBegin").click()
    endpoint = f"{base}/api/papers/{fixture['paper']}/question-layout"
    lost = []

    def drop_committed_response(route):
        if route.request.method != "POST":
            route.fallback()
            return
        assert not lost, "Receipt recovery must not repeat the mutation"
        body = route.request.post_data_json
        response = route.fetch()
        assert response.ok, response.text()
        lost.append({"payload": body, "operation": response.json()["operation"]})
        report["writes"].append({"method": "POST", "path": urlparse(endpoint).path,
                                "response_intentionally_lost": True, "payload": body})
        route.abort("connectionreset")

    page.route(endpoint, drop_committed_response)
    try:
        page.locator("#pageDialogSave").click()
        page.locator("#pageLayoutConfirmSave").click()
        expect(page.locator("#pageLayoutEdit")).to_be_visible(timeout=15000)
    finally:
        page.unroute(endpoint, drop_committed_response)
    assert len(lost) == 1 and len(paper_data()["questions"]) == 3
    operation = lost[0]["operation"]
    result = page.request.get(endpoint + "?client_request_id=" + lost[0]["payload"]["client_request_id"]).json()
    assert result["operation"]["id"] == operation["id"] and len(operation["target_ids"]) == 1
    undo(reopen=True)
    assert len(paper_data()["questions"]) == 4
    report["passed"].append("lost successful merge response recovers the same durable receipt without a duplicate write or duplicate question")
    assert all(not q["ocr_pending"] for q in paper_data()["questions"])
    db = database_rows()
    assert not db["core_regionread"] and not db["core_libraryjob"]
    assert not report["forbidden_requests"], report["forbidden_requests"]
    report["passed"].append("all saves, supplement, split, merge and undo run without an implicit AI request or background job")


def run(args):
    output = private.confined_output(args.output or ROOT / "tmp" / ("question-layout-" + uuid.uuid4().hex[:10]))
    if output.exists():
        raise ValueError("Choose an unused output directory; existing evidence is never overwritten")
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", args.port)) != 0, "Port occupied; existing service was not touched"
    output.mkdir(parents=True)
    user = output / "user"
    (user / "data").mkdir(parents=True)
    process_tmp = output / "process-tmp"
    process_tmp.mkdir()
    base = f"http://127.0.0.1:{args.port}"
    environment = private.private_environment(user, base)
    environment.update(TEMP=str(process_tmp), TMP=str(process_tmp), __COMPAT_LAYER="DetectorsAppHealth")
    backend_python = Path(args.backend_python or ROOT / "packaging/.build/venv/Scripts/python.exe").resolve()
    application = Path(args.bundle).resolve() if args.bundle else None
    if application and application.is_dir():
        application /= "QuestionBankCard.exe"
    assert backend_python.is_file()
    if application:
        assert application.is_file() and application.name.lower() == "questionbankcard.exe"
    receipt = {"isolated_root": str(user), "port": args.port, "worker_started": False,
        "real_credentials_available": False, "frozen_executable": str(application) if application else None}
    if application:
        receipt["frozen_executable_sha256"] = hashlib.sha256(application.read_bytes()).hexdigest()
    owned = server = None

    def child(command, log_name, seconds=120):
        nonlocal owned
        with (output / log_name).open("wb") as log:
            owned = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
            result = wait_owned(owned, seconds)
        if result:
            raise RuntimeError(f"{log_name}: process exited {result}; inspect this private log")
    try:
        script = str(Path(__file__).resolve())
        child([str(backend_python), script, "--internal-mode", "seed-old", "--output", str(output)], "seed-old.log")
        if application:
            environment["QB_INTERNAL_LOG"] = str(output / "migration-internal.log")
            command = [str(application), "--internal-role", "migrate", "--noinput"]
        else:
            command = [str(backend_python), str(ROOT / "backend/manage.py"), "migrate", "--noinput"]
        child(command, "migration.log")
        child([str(backend_python), script, "--internal-mode", "finish-seed", "--output", str(output)], "finish-seed.log")
        if application:
            environment["QB_INTERNAL_LOG"] = str(output / "server-internal.log")
            command = [str(application), "--internal-role", "runserver", f"127.0.0.1:{args.port}", "--noreload"]
        else:
            command = [str(backend_python), str(ROOT / "backend/manage.py"), "runserver", f"127.0.0.1:{args.port}", "--noreload"]
        with (output / "server.log").open("wb") as log:
            server = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
            receipt["server_pid"] = server.pid
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Owned server exited during startup")
                try:
                    with opener.open(base + "/api/status", timeout=1) as response:
                        receipt["app_version"] = json.load(response).get("app_version")
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise TimeoutError("Owned test server did not become ready")
            child([sys.executable, script, "--internal-mode", "browser", "--output", str(output),
                   "--port", str(args.port)], "browser.log", 240)
    finally:
        receipt["last_child_stopped"] = stop_owned(owned)
        receipt["server_stopped"] = stop_owned(server)
        write_json(output / "receipt.json", receipt)
    print(f"Layout acceptance PASS: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--port", type=int, default=18926)
    parser.add_argument("--bundle")
    parser.add_argument("--backend-python")
    parser.add_argument("--internal-mode", choices=("seed-old", "finish-seed", "browser"))
    args = parser.parse_args()
    if args.internal_mode:
        output = private.confined_output(args.output)
        if args.internal_mode == "seed-old":
            seed_old(output)
        elif args.internal_mode == "finish-seed":
            finish_seed(output)
        else:
            return browser_check(output, args.port)
        return 0
    if not args.run:
        parser.error("Choose --run")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
