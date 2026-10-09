"""Real pointer and persisted-layout acceptance, with synthetic private data.

--run creates an unused child of checkout/tmp, builds a fictional old-schema
database, upgrades it with the selected source/frozen program, and exercises
the real loopback UI. No worker, installed service, credentials or cloud API
is used. Writes are allowed only to this fixture's layout, undo, question
deletion and recycle-bin restore endpoints.
"""
from __future__ import annotations

import argparse
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
        stem = ("人工保存的虚构第 1 题：求 sin x 的值。" if i == 0 else
                "人工保存的虚构第 2 题：求 \\sin x 的值。" if i == 1 else
                "人工保存的虚构第 3 题：求 SIN x 的值。" if i == 2 else "")
        rows.append(Question.objects.create(paper=paper, group=groups[0 if i < 3 else 1],
            number=i + 1 if i < 3 else 1, regions=parts, regions_auto=parts,
            question_type="free_response", state="yellow", stem=stem,
            processing_mode="auto" if i == 0 else "manual", body_mode="source_image" if i == 3 else "text", edited=i != 3,
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
                paper_path = f"/api/papers/{fixture['paper']}"
                allowed = request.method == "POST" and (
                    parsed.path.startswith(paper_path + "/question-layout")
                    or parsed.path == paper_path + "/questions/delete"
                    or re.fullmatch(re.escape(paper_path) + r"/question-trash/[0-9a-fA-F-]{36}/restore", parsed.path) is not None
                )
            if not allowed:
                report["forbidden_requests"].append({"method": request.method, "path": parsed.path})
                route.abort()
            else:
                if request.method == "POST":
                    report["writes"].append({"method": "POST", "path": parsed.path, "payload": request.post_data_json})
                route.continue_()
        context.route("**/*", guard)
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append({"message": str(error), "stack": error.stack}))
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
            if page.locator("#pageNumberInput").input_value() != "1":
                page.locator("#pageNumberInput").fill("1")
                page.locator("#pageNumberInput").press("Enter")
                page.wait_for_load_state("networkidle")

        try:
            # Search is an overlay over the loaded paper, not a card sort or
            # recognition request. The fixture includes equivalent sin forms,
            # repeated question numbers across groups and a blank original-image question.
            page.goto(base + "/?paper=" + fixture["paper"])
            page.wait_for_load_state("networkidle")
            card_order = page.locator(".card").evaluate_all("nodes => nodes.map(node => node.dataset.id)")
            page.keyboard.press("Control+f")
            expect(page.locator("#reviewFinderPanel")).to_be_visible()
            expect(page.locator("#reviewFindInput")).to_be_focused()
            finder = page.locator("#reviewFindInput")
            finder.fill("sin")
            expect(page.locator("#reviewFindResults [data-question-id]")).to_have_count(3)
            assert page.locator(".card").evaluate_all("nodes => nodes.map(node => node.dataset.id)") == card_order
            finder.fill("SIN")
            expect(page.locator("#reviewFindResults [data-question-id]")).to_have_count(3)
            finder.fill("\\sin")
            expect(page.locator("#reviewFindResults [data-question-id]")).to_have_count(3)
            finder.fill("第 １ 题")
            expect(page.locator("#reviewFindResults [data-question-id]")).to_have_count(2)
            assert page.locator("#reviewFindResults").inner_text().count("虚构试卷") == 2
            finder.fill("第 2 页")
            page.wait_for_function("document.querySelectorAll('#reviewFindResults [data-question-id]').length === 2")
            finder.fill("虚构试卷乙")
            expect(page.locator("#reviewFindResults [data-question-id]")).to_have_count(1)
            expect(page.locator("#reviewFindResults")).to_contain_text("原图题")
            page.locator('#filters [data-filter="todo"]').click()
            finder.fill("2")
            expect(page.locator("#reviewFindResults [data-question-id]")).to_have_count(1)
            page.locator('#reviewFindResults [data-question-id]').click()
            expect(page.locator('#filters [data-filter="all"]')).to_have_attribute("aria-selected", "true")
            expect(page.locator(f'.card[data-id="{fixture["nested"]}"]')).to_be_visible()
            report["passed"].append("review finder handles sin/SIN/\\sin, repeated numbers, page and original-image lookup without reordering cards; hidden results open under All")
            # Reload restores this paper's finder query, filter and reading context.
            page.locator('#filters [data-filter="todo"]').click()
            finder = page.locator("#reviewFindInput")
            finder.fill("1")
            page.reload()
            page.wait_for_load_state("networkidle")
            expect(page.locator('#filters [data-filter="todo"]')).to_have_attribute("aria-selected", "true")
            expect(page.locator("#reviewFindInput")).to_have_value("1")
            page.screenshot(path=str(output / "review-finder-desktop.png"))
            open_paper()
            original = paper_data()
            # The side panel no longer mirrors every question. The canvas shows
            # only regions on the active page, while the complete question set
            # remains available to the finder and the layout service.
            expect(page.locator("[data-layout-coverage-id]:not([data-layout-coverage-id^='-'])")).to_have_count(3)
            assert len(original["questions"]) == 4
            expect(page.locator("#pageLayoutPanel .layout-question-list")).to_have_count(0)
            page.locator("#pageNext").click()
            expect(page.locator("[data-layout-coverage-id]:not([data-layout-coverage-id^='-'])")).to_have_count(2)
            expect(page.locator(f'[data-layout-coverage-id="{fixture["crosspage"]}"]')).to_have_count(1)
            page.locator("#pagePrevious").click()
            assert original["paper"]["layout_revision"] == 0
            assert len({q["color_index"] for q in original["questions"]}) >= 3
            page.screenshot(path=str(output / "overview-desktop.png"))
            report["passed"].append("old-schema upgrade, four distinct question identities, persistent palette and two question groups")
            run_ui_cases(page, expect, output, fixture, report, paper_data)
            if page.locator("#pageDialog").is_visible():
                page.locator("#pageDialogClose").click()
            open_paper(fixture["stress"])
            expect(page.locator("[data-layout-coverage-id]:not([data-layout-coverage-id^='-'])")).to_have_count(100)
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
        regions = question(key)["regions"]
        active_page = int(page.locator("#pageNumberInput").input_value()) - 1
        region = next((item for item in regions if item["page_idx"] == active_page), regions[0])
        bbox = region["bbox"]
        # Click near the frame's upper-left interior. Clicking the locator's
        # center can land on a selected neighbour's resize grip when frames
        # overlap, which tests a resize gesture instead of choosing a frame.
        offset = min(12, (bbox[2] - bbox[0]) / 8, (bbox[3] - bbox[1]) / 8)
        page.mouse.click(*point(bbox[0] + offset, bbox[1] + offset))
        if page.locator("#pageLayoutOverlap").is_visible():
            label = f"第 {question(key)['number']} 题"
            candidates = page.locator("#pageLayoutOverlap [data-layout-hit]")
            for i in range(candidates.count()):
                if label in candidates.nth(i).inner_text():
                    candidates.nth(i).click()
                    break

    def fit():
        page.locator("#pageZoomFit").click()
        page.wait_for_function("document.querySelector('#pageStage img')?.complete && document.querySelector('#pageStage img')?.naturalWidth > 0")
        page.locator("#pageStage .stage-surface").scroll_into_view_if_needed()
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
        needs_confirmation = page.locator("#pageDialogSave").inner_text().startswith("预览并确认")
        if needs_confirmation:
            page.locator("#pageDialogSave").click()
            expect(page.locator("#pageLayoutConfirmSave")).to_be_visible()
            report["before_last_save"] = paper_data()
            with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
                page.locator("#pageLayoutConfirmSave").click()
        else:
            with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
                page.locator("#pageDialogSave").click()
        response = waiting.value
        report["last_save_response"] = {"status": response.status, "body": response.json()}
        if not needs_confirmation:
            expect(page.locator("#pageLayoutConfirmSave")).to_have_count(0)
        expect(page.locator("#pageLayoutPanel")).to_be_visible()
        assert paper_data()["paper"]["layout_revision"] > revision

    def cancel_draft():
        page.locator("#pageLayoutCancelDraft").click()
        if page.locator(".layout-leave-dialog").is_visible():
            page.locator(".layout-leave-dialog").get_by_role("button", name="放弃修改").click()
        expect(page.locator("#pageLayoutPanel")).to_be_visible()

    def reopen_workspace():
        page.locator("#pageDialogClose").click()
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#paperMenu > summary").click()
        page.locator("#viewOriginalPaper").click()
        expect(page.locator("#pageLayoutPanel")).to_be_visible()
        page.wait_for_function("document.querySelector('#pageStage img')?.complete && document.querySelector('#pageStage img')?.naturalWidth > 0")

    def undo(reopen=False):
        if reopen:
            reopen_workspace()
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
    # Selecting a saved frame immediately creates a clean editable draft,
    # represented separately from the persisted frame as id -1.
    expect(page.locator('[data-layout-coverage-id="-1"]').first).to_have_class(re.compile("selected"))
    assert paper_data() == original and not report["writes"]
    report["passed"].append("overlapping nested boxes choose the smaller question first without editing or OCR")

    # This still-published fixture is deliberately checked before split/undo
    # withdraws it. Removing its last range only changes the local draft;
    # the existing publication protection must also apply inside this workspace.
    publication_before = database_rows()["core_publishedquestion"]
    page.locator('[data-layout-part-index="0"]').get_by_role("button", name="移除", exact=True).click()
    expect(page.locator("#pageLayoutEmptyRange")).to_contain_text("这道题已经没有范围框")
    expect(page.locator("#pageLayoutDeleteQuestion")).to_be_disabled()
    expect(page.locator("#pageLayoutEmptyRange")).to_contain_text("已经入库")
    expect(page.locator("#pageDialogSave")).to_be_disabled()
    expect(page.locator("#pageLayoutParts")).to_have_count(0)
    expect(page.locator("#pageLayoutTargetPreview")).to_have_count(0)
    expect(page.locator("[data-layout-target-index]")).to_have_count(0)
    page.locator("#pageLayoutRestoreFrame").click()
    expect(page.locator("#pageLayoutEmptyRange")).to_have_count(0)
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    assert paper_data() == original and not report["writes"]
    assert database_rows()["core_publishedquestion"] == publication_before
    report["passed"].append("removing the last frame offers a direct restore without saving or OCR; published-question delete protection remains intact")

    select("first")
    expect(page.locator('[data-layout-box-index="0"] [data-layout-handle="e"]')).to_be_visible()
    expect(page.locator("#pageDialogSave")).to_have_text("保存")
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
    page.locator('[data-layout-part-index="1"] button').first.click()
    page.wait_for_function("document.querySelector('#pageStage img')?.complete && document.querySelector('#pageStage img')?.naturalWidth > 0")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    expect(page.locator('[data-layout-box-index="1"]')).to_be_visible()
    frame = page.locator('[data-layout-box-index="1"]').bounding_box()
    stage = page.locator("#pageStage").bounding_box()
    assert abs(frame["x"] + frame["width"] / 2 - (stage["x"] + stage["width"] / 2)) < stage["width"] * .28
    assert abs(frame["y"] + frame["height"] / 2 - (stage["y"] + stage["height"] / 2)) < stage["height"] * .28
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
    if page.locator("#pageNumberInput").input_value() != "1":
        page.locator("#pageNumberInput").fill("1")
        page.locator("#pageNumberInput").press("Enter")
    select("first")
    page.locator("#pageLayoutAdd").click()
    assert page.locator("#pageStage .stage-surface").evaluate("node => getComputedStyle(node).cursor") == "crosshair"
    draw([70, 730, 930, 840])
    expect(page.locator("#pageLayoutDraw")).to_have_attribute("aria-pressed", "false")
    page.mouse.dblclick(*point(500, 785))
    expect(page.locator("#pageDialog")).to_be_visible()
    page.locator("#pageLayoutNumber").fill("4")
    page.locator("#pageLayoutNumber").press("Tab")
    before_add = paper_data()["paper"]["layout_revision"]
    with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
        page.locator("#pageDialogSave").click()
    response = waiting.value
    report["last_add_response"] = {"status": response.status, "body": response.json()}
    expect(page.locator("#pageLayoutPanel")).to_contain_text("第 4 题")
    assert paper_data()["paper"]["layout_revision"] > before_add
    added = next(q for q in paper_data()["questions"] if q["id"] not in {r["id"] for r in original["questions"]})
    assert added["body_mode"] == "source_image" and added["processing_mode"] == "manual"
    assert not added["approved"] and not added["ocr_pending"]
    assert len(paper_data()["questions"]) == 5
    page.locator("#pageLayoutCancelDraft").click()
    expect(page.locator("#pageLayoutPanel")).to_be_visible()
    page.screenshot(path=str(output / "supplement-saved.png"))
    undo(reopen=True)
    assert len(paper_data()["questions"]) == 4
    report["passed"].append("supplement an uncovered question as a manual original-image question, then persistent undo")

    select("nested")
    old_nested = question("nested")
    assert old_nested["approved"]
    page.locator("#pageLayoutPanel .layout-more-actions > summary").click()
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
    with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as split_waiting:
        page.locator("#pageLayoutConfirmSave").click()
    split_response = split_waiting.value
    report["split_response"] = {"status": split_response.status, "body": split_response.json()}
    assert split_response.ok, report["split_response"]
    expect(page.locator("#pageLayoutPanel")).to_be_visible()
    split_data = paper_data()
    report["split_response_snapshot"] = {
        "count": len(split_data.get("questions", [])),
        "ids": [q.get("id") for q in split_data.get("questions", [])],
        "paper_id": split_data.get("id"),
    }
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
    page.locator("#pageLayoutPanel .layout-more-actions > summary").click()
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
    with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
        page.locator("#pageDialogSave").click()
    assert waiting.value.status == 409
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
    page.locator("#pageLayoutPanel .layout-more-actions > summary").click()
    page.locator("#pageLayoutMerge").click()
    page.locator(f'[data-layout-merge-id="{fixture["crosspage"]}"]').check()
    page.locator("#pageLayoutMergeBegin").click()
    # begin() awaits source readiness before rendering the merge draft.
    # Wait for that async transition before pressing Save or installing the
    # lost-response interceptor, otherwise the click can hit the prior draft.
    expect(page.locator('[data-layout-part-index]')).to_have_count(3)
    expect(page.locator("#pageLayoutPanel")).to_contain_text("合题 · 未保存")
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
        expect(page.locator("#pageLayoutConfirmSave")).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "GET" and "client_request_id=" in response.url) as receipt_waiting:
            page.locator("#pageLayoutConfirmSave").click()
            # The panel stays visible during saves, so wait for the impact
            # confirmation to disappear after receipt recovery instead.
            expect(page.locator("#pageLayoutConfirmSave")).to_have_count(0, timeout=15000)
        assert receipt_waiting.value.ok
    finally:
        page.unroute(endpoint, drop_committed_response)
    assert len(lost) == 1 and len(paper_data()["questions"]) == 3
    operation = lost[0]["operation"]
    result = page.request.get(endpoint + "?client_request_id=" + lost[0]["payload"]["client_request_id"]).json()
    assert result["operation"]["id"] == operation["id"] and len(operation["target_ids"]) == 1
    undo(reopen=True)
    assert len(paper_data()["questions"]) == 4
    report["passed"].append("lost successful merge response recovers the same durable receipt without a duplicate write or duplicate question")

    # Correct the number of one existing question, preserving its identity and
    # source material. This is deliberately a distinct workflow from geometry.
    select("first")
    fit()
    page.mouse.click(*point(500, 140))
    expect(page.locator(f'[data-layout-source-question-id="{fixture["first"]}"]').first).to_have_class(re.compile("selected"))
    before_renumber = question("first")
    before_data = paper_data()
    before_writes = len(report["writes"])
    page.wait_for_load_state("networkidle")
    delayed_history = []

    def hold_history_response(route):
        if route.request.method == "GET":
            delayed_history.append(route)
        else:
            route.fallback()

    page.route(endpoint, hold_history_response)
    history = page.locator("#pageLayoutHistory")
    if history.get_attribute("open") is None:
        history.locator("summary").first.click()
    history.get_by_role("button", name="刷新记录", exact=True).click()
    assert len(delayed_history) == 1
    page.locator("#pageLayoutRenumber").click()
    expect(page.locator("#pageLayoutNumber")).to_have_value(str(before_renumber["number"]))
    expect(page.locator("#pageDialogSave")).to_have_text("保存")
    assert page.locator("[data-layout-handle]").count() == 0
    assert page.locator("[data-layout-drag-label]").count() == 0
    expect(page.locator("#pageLayoutDraw")).to_have_count(0)
    expect(page.locator("#pageLayoutGroup")).to_have_count(0)
    expect(page.locator("#pageLayoutType")).to_have_count(0)

    # Merely entering this editor and saving the unchanged value cannot create
    # a layout version or a durable history record.
    if page.locator("#pageDialogSave").is_enabled():
        page.locator("#pageDialogSave").click()
    assert len(report["writes"]) == before_writes
    assert paper_data() == before_data
    expect(page.locator("#pageLayoutNumber")).to_have_value(str(before_renumber["number"]))

    page.locator("#pageLayoutNumber").fill("")
    number_input = page.locator("#pageLayoutNumber").element_handle()
    page.locator("#pageLayoutNumber").press_sequentially("1")
    assert number_input.evaluate("node => node.isConnected && node === document.activeElement")
    delayed_history[0].fulfill(response=delayed_history[0].fetch())
    page.unroute(endpoint, hold_history_response)
    page.wait_for_load_state("networkidle")
    assert number_input.evaluate("node => node.isConnected && node === document.activeElement")
    expect(page.locator("#pageLayoutNumber")).to_have_value("1")
    page.locator("#pageLayoutNumber").press_sequentially("9")
    assert number_input.evaluate("node => node.isConnected && node === document.activeElement")
    expect(page.locator("#pageLayoutNumber")).to_have_value("19")
    page.locator("#pageLayoutNumber").press("Tab")
    expect(page.locator("#pageDialogSave")).to_be_enabled()
    expect(page.locator("#toast")).not_to_be_visible(timeout=8000)
    page.screenshot(path=str(output / "renumber-draft.png"))
    with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
        page.locator("#pageDialogSave").click()
    response = waiting.value
    assert response.ok, response.text()
    saved = response.json()
    assert saved["operation"]["kind"] == "renumber"
    assert saved["operation"]["source_ids"] == [fixture["first"]]
    assert saved["operation"]["target_ids"] == [fixture["first"]]
    expect(page.locator("#pageLayoutPanel")).to_be_visible()
    after_renumber = question("first")
    assert after_renumber["number"] == 19
    assert after_renumber["content_revision"] > before_renumber["content_revision"]
    assert len(paper_data()["questions"]) == len(before_data["questions"])
    for key in ("id", "regions", "regions_auto", "stem", "edited_stem", "ai_stem", "options", "edited_options",
                "answer", "edited_answer", "analysis", "edited_analysis", "body_mode", "processing_mode", "question_type",
                "color_index", "group", "figures", "manual_figures"):
        assert after_renumber.get(key) == before_renumber.get(key), key
    box = page.locator(f'[data-layout-source-question-id="{fixture["first"]}"]').first
    expect(box).to_contain_text("19")
    expect(box).to_have_class(re.compile("selected"))
    assert len(report["writes"]) == before_writes + 1
    page.screenshot(path=str(output / "renumber-saved.png"))

    # Reopening must display the persisted number before performing the saved
    # undo; a refresh cannot turn this into a second question or lose colour.
    reopen_workspace()
    select("first")
    expect(page.locator(f'[data-layout-source-question-id="{fixture["first"]}"]').first).to_contain_text("19")
    assert question("first")["color_index"] == before_renumber["color_index"]
    undo()
    restored = question("first")
    assert restored["number"] == before_renumber["number"]
    assert restored["regions"] == before_renumber["regions"]
    assert restored["stem"] == before_renumber["stem"]
    assert restored["content_revision"] > after_renumber["content_revision"]
    expect(page.locator(f'[data-layout-source-question-id="{fixture["first"]}"]').first).to_contain_text("1")
    report["renumber_verification"] = {"question_id": fixture["first"], "operation_id": saved["operation"]["id"],
        "old_number": before_renumber["number"], "saved_number": after_renumber["number"], "undone_number": restored["number"],
        "source_button": "pageDialogSave", "question_identity_preserved": True,
        "regions_preserved": True, "manual_content_preserved": True, "colour_preserved": True,
        "single_mutation": True, "reopened_before_undo": True, "multi_digit_typing_preserves_focus": True,
        "late_history_response_preserves_input": True}
    report["passed"].append("existing question renumber is one direct save, persists across reopen, preserves identity/content/ranges/colour, and supports saved undo")

    # A duplicate in the same group and an invalid number are rejected while
    # preserving the user's draft. No mutation is sent for either case.
    select("first")
    page.locator("#pageLayoutRenumber").click()
    rejection_data = paper_data()
    rejection_writes = len(report["writes"])
    for value in ("2", "0"):
        page.locator("#pageLayoutNumber").fill(value)
        page.locator("#pageLayoutNumber").press("Tab")
        if page.locator("#pageDialogSave").is_enabled():
            page.locator("#pageDialogSave").click()
        expect(page.locator("#pageLayoutNumber")).to_have_value(value)
        assert len(report["writes"]) == rejection_writes
        assert paper_data() == rejection_data
    page.screenshot(path=str(output / "renumber-invalid-retains-draft.png"))
    cancel_draft()
    report["passed"].append("renumber unchanged value, same-group duplicate and invalid number leave coordinates and versions unchanged; rejected drafts are retained")

    for value, shortcut in (("8", "Enter"), ("9", "Control+s")):
        select("first")
        page.locator("#pageLayoutRenumber").click()
        page.locator("#pageLayoutNumber").fill(value)
        with page.expect_response(lambda response: response.request.method == "POST" and "/question-layout" in response.url) as waiting:
            page.locator("#pageLayoutNumber").press(shortcut)
        assert waiting.value.ok, waiting.value.text()
        expect(page.locator("#pageLayoutPanel")).to_be_visible()
        assert question("first")["number"] == int(value)
        assert question("first")["regions"] == before_renumber["regions"]
        undo()
        assert question("first")["number"] == before_renumber["number"]
    report["passed"].append("renumber Enter and Ctrl+S each save exactly the selected question without a preview step and remain undoable")

    # Last-frame removal is an explicit, recoverable local state. It is not a
    # zero-range save, an implicit question deletion or a recognition request.
    select("first")
    fit()
    empty_before = paper_data()
    empty_writes = len(report["writes"])
    page.locator('[data-layout-part-index="0"]').get_by_role("button", name="移除", exact=True).click()
    expect(page.locator("#pageLayoutEmptyRange")).to_contain_text("题目尚未删除")
    expect(page.locator("#pageLayoutRestoreFrame")).to_have_text("恢复刚删的框")
    expect(page.locator("#pageLayoutDraw")).to_have_text("重新画框")
    expect(page.locator("#pageLayoutDeleteQuestion")).to_have_text("删除这题")
    expect(page.locator("#pageLayoutDeleteQuestion")).to_be_enabled()
    expect(page.locator("#pageDialogSave")).to_be_disabled()
    expect(page.locator("#pageLayoutParts")).to_have_count(0)
    expect(page.locator("#pageLayoutTargetPreview")).to_have_count(0)
    expect(page.locator("[data-layout-target-index]")).to_have_count(0)
    assert "结果 1" not in page.locator("#pageLayoutPanel").inner_text()
    assert "1 段" not in page.locator("#pageLayoutPanel").inner_text()
    assert paper_data() == empty_before and len(report["writes"]) == empty_writes
    page.screenshot(path=str(output / "last-frame-clear-actions.png"))
    page.locator("#pageLayoutRestoreFrame").click()
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    expect(page.locator("#pageUndo")).to_be_disabled()
    assert paper_data() == empty_before and len(report["writes"]) == empty_writes

    # Cancelling an unfinished redraw does not erase the removed-frame undo.
    # Completing a redraw participates in ordinary draft undo/redo, and the
    # explicit restore button can still return to the original saved range.
    page.locator('[data-layout-part-index="0"]').get_by_role("button", name="移除", exact=True).click()
    page.locator("#pageLayoutDraw").click()
    expect(page.locator("#pageLayoutDraw")).to_have_attribute("aria-pressed", "true")
    assert page.locator("#pageStage .stage-surface").evaluate("node => getComputedStyle(node).cursor") == "crosshair"
    fit()
    page.mouse.click(*point(90, 95))
    page.keyboard.press("Escape")
    expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
    expect(page.locator("#pageDialogSave")).to_be_disabled()
    assert paper_data() == empty_before and len(report["writes"]) == empty_writes
    draw([90, 95, 900, 400])
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    expect(page.locator("#pageDialogSave")).to_be_enabled()
    page.locator("#pageUndo").click()
    expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
    page.locator("#pageRedo").click()
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    page.locator("#pageUndo").click()
    expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
    page.locator("#pageLayoutRestoreFrame").click()
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    assert paper_data() == empty_before and len(report["writes"]) == empty_writes
    report["passed"].append("last-frame empty state has clear restore/redraw/delete actions; cancelled redraw and draft undo/redo keep saved content and versions unchanged")

    # A multi-frame question remains valid after one piece is removed. Its
    # sidebar must describe the draft's current range rather than stale data.
    select("crosspage")
    multi_before = question("crosspage")
    expect(page.locator("[data-layout-part-index]")).to_have_count(2)
    page.locator('[data-layout-part-index="1"]').get_by_role("button", name="移除", exact=True).click()
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    expect(page.locator("#pageLayoutPanel .layout-current")).to_contain_text("1 个框")
    expect(page.locator("#pageLayoutEmptyRange")).to_have_count(0)
    expect(page.locator("#pageDialogSave")).to_be_enabled()
    assert question("crosspage") == multi_before
    page.locator("#pageUndo").click()
    expect(page.locator("[data-layout-part-index]")).to_have_count(2)
    page.locator("#pageRedo").click()
    save()
    multi_saved = question("crosspage")
    assert multi_saved["regions"] == multi_before["regions"][:1]
    assert multi_saved["stem"] == multi_before["stem"] and not multi_saved["ocr_pending"]
    undo()
    assert question("crosspage")["regions"] == multi_before["regions"]
    report["passed"].append("removing one of two pieces updates the draft frame count, remains directly saveable and supports draft plus persisted undo without OCR")

    select("first")
    fit()
    delete_before = paper_data()
    delete_question_before = question("first", delete_before)
    delete_writes = len(report["writes"])
    page.locator('[data-layout-part-index="0"]').get_by_role("button", name="移除", exact=True).click()
    page.locator("#pageLayoutDeleteQuestion").click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    expect(page.locator("#confirmTitle")).to_contain_text("回收站")
    page.locator('#confirmDialog button[value="cancel"]').click()
    expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
    expect(page.locator("#pageLayoutDeleteQuestion")).to_be_enabled()
    expect(page.locator("#pageDialogSave")).to_be_disabled()
    assert paper_data() == delete_before and len(report["writes"]) == delete_writes

    # Inject failures before reaching the real fixture server. Both the 400
    # response and a failed connection must keep the empty-range draft and its
    # restore/redraw choices, then allow the user to retry one real deletion.
    delete_endpoint = f"{base}/api/papers/{fixture['paper']}/questions/delete"
    injected_deletes = []

    def reject_delete(route):
        assert route.request.method == "POST"
        assert route.request.post_data_json == {"question_ids": [fixture["first"]]}
        injected_deletes.append("400")
        route.fulfill(status=400, content_type="application/json", body=json.dumps({"error": "虚构删除失败：草稿应保留"}))

    page.route(delete_endpoint, reject_delete)
    try:
        page.locator("#pageLayoutDeleteQuestion").click()
        expect(page.locator("#confirmDialog")).to_be_visible()
        with page.expect_response(lambda response: response.request.method == "POST" and response.url == delete_endpoint) as failed_delete:
            page.locator("#confirmOk").click()
        assert failed_delete.value.status == 400
        expect(page.locator("#toast")).to_contain_text("虚构删除失败")
        expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
        expect(page.locator("#pageLayoutDeleteQuestion")).to_be_enabled()
        expect(page.locator("#pageLayoutRestoreFrame")).to_be_enabled()
    finally:
        page.unroute(delete_endpoint, reject_delete)
    assert paper_data() == delete_before and len(report["writes"]) == delete_writes

    def disconnect_delete(route):
        assert route.request.method == "POST"
        assert route.request.post_data_json == {"question_ids": [fixture["first"]]}
        injected_deletes.append("network")
        route.abort("connectionreset")

    page.route(delete_endpoint, disconnect_delete)
    try:
        page.locator("#pageLayoutDeleteQuestion").click()
        expect(page.locator("#confirmDialog")).to_be_visible()
        with page.expect_event("requestfailed", predicate=lambda request: request.method == "POST" and request.url == delete_endpoint):
            page.locator("#confirmOk").click()
        expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
        expect(page.locator("#pageLayoutDeleteQuestion")).to_be_enabled()
        expect(page.locator("#pageLayoutRestoreFrame")).to_be_enabled()
    finally:
        page.unroute(delete_endpoint, disconnect_delete)
    assert injected_deletes == ["400", "network"]
    assert paper_data() == delete_before and len(report["writes"]) == delete_writes
    report["passed"].append("whole-question delete cancellation, server rejection and network failure retain the local empty-frame draft and retry/restore choices")

    zoom_before_delete = page.locator("#pageZoomLevel").inner_text()
    page_before_delete = page.locator("#pageNumberInput").input_value()
    scroll_before_delete = page.locator("#pageStage").evaluate("node => [node.scrollLeft, node.scrollTop]")
    page.locator("#pageLayoutDeleteQuestion").click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "POST" and response.url == delete_endpoint) as successful_delete:
        page.locator("#confirmOk").click()
    assert successful_delete.value.ok, successful_delete.value.text()
    deleted_receipt = successful_delete.value.json()
    expect(page.locator("#pageLayoutEmptyRange")).to_have_count(0)
    expect(page.locator("#pageDialog")).to_be_visible()
    expect(page.locator("#pageDialogSave")).not_to_be_visible()
    assert fixture["first"] not in {q["id"] for q in paper_data()["questions"]}
    assert len(report["writes"]) == delete_writes + 1
    assert page.locator("#pageZoomLevel").inner_text() == zoom_before_delete
    assert page.locator("#pageNumberInput").input_value() == page_before_delete
    assert page.locator("#pageStage").evaluate("node => [node.scrollLeft, node.scrollTop]") == scroll_before_delete
    expect(page.locator("#pageLayoutRestoreDeleted")).to_have_text("恢复这题")
    expect(page.locator("#pageLayoutRestoreDeleted")).to_be_enabled()
    expect(page.locator("#toast .toast-action")).to_have_count(0)
    page.screenshot(path=str(output / "question-deleted-overview.png"))
    direct_restore_endpoint = f"{base}/api/papers/{fixture['paper']}/question-trash/{deleted_receipt['undo_batch']['id']}/restore"
    with page.expect_response(lambda response: response.request.method == "POST" and response.url == direct_restore_endpoint) as direct_restore:
        page.locator("#pageLayoutRestoreDeleted").click()
    assert direct_restore.value.ok, direct_restore.value.text()
    expect(page.locator("#pageLayoutRestoreDeleted")).to_have_count(0)
    expect(page.locator("[data-layout-part-index]")).to_have_count(1)
    expect(page.locator(f'[data-layout-source-question-id="{fixture["first"]}"]').first).to_be_visible()
    direct_restored_question = question("first")
    for field in ("id", "number", "regions", "stem", "edited_stem", "edited_options", "body_mode", "color_index", "group", "manual_figures"):
        assert direct_restored_question.get(field) == delete_question_before.get(field), field
    assert not direct_restored_question["ocr_pending"]
    assert len(report["writes"]) == delete_writes + 2
    assert page.locator("#pageZoomLevel").inner_text() == zoom_before_delete
    assert page.locator("#pageNumberInput").input_value() == page_before_delete
    assert page.locator("#pageStage").evaluate("node => [node.scrollLeft, node.scrollTop]") == scroll_before_delete
    page.screenshot(path=str(output / "question-restored-sidebar.png"))
    report["passed"].append("deleting a question exposes an actionable in-workspace Restore this question button; one real restore returns its saved frame/content without moving the canvas")

    # Exercise the older recycle-bin route as well, using a second actual
    # delete batch rather than restoring an already-restored batch again.
    page.locator('[data-layout-part-index="0"]').get_by_role("button", name="移除", exact=True).click()
    expect(page.locator("#pageLayoutEmptyRange")).to_be_visible()
    page.locator("#pageLayoutDeleteQuestion").click()
    expect(page.locator("#confirmDialog")).to_be_visible()
    with page.expect_response(lambda response: response.request.method == "POST" and response.url == delete_endpoint) as second_delete:
        page.locator("#confirmOk").click()
    assert second_delete.value.ok, second_delete.value.text()
    deleted_receipt = second_delete.value.json()
    expect(page.locator("#pageLayoutRestoreDeleted")).to_be_enabled()
    assert fixture["first"] not in {q["id"] for q in paper_data()["questions"]}
    assert len(report["writes"]) == delete_writes + 3
    page.locator("#pageDialogClose").click()
    page.locator("#toolsMenu > summary").click()
    page.locator("#questionTrash").click()
    expect(page.locator("#trashDialog")).to_be_visible()
    batch = page.locator("#trashList .trash-batch:not(.restored)").filter(has_text=delete_question_before["stem"])
    expect(batch).to_have_count(1)
    restore_endpoint = f"{base}/api/papers/{fixture['paper']}/question-trash/{deleted_receipt['undo_batch']['id']}/restore"
    with page.expect_response(lambda response: response.request.method == "POST" and response.url == restore_endpoint) as restore_response:
        batch.get_by_role("button", name="恢复这一批", exact=True).click()
    assert restore_response.value.ok, restore_response.value.text()
    # The ordinary recycle-bin endpoint lists only un-restored batches.
    # Successful restoration removes both batches from this live list.
    expect(page.locator("#trashList .trash-batch")).to_have_count(0)
    expect(page.locator("#trashList")).to_contain_text("回收站是空的")
    assert paper_data()["paper"]["trash_count"] == 0
    restored_question = question("first")
    for field in ("id", "number", "regions", "stem", "edited_stem", "edited_options", "body_mode", "color_index", "group", "manual_figures"):
        assert restored_question.get(field) == delete_question_before.get(field), field
    assert not restored_question["ocr_pending"]
    assert len(report["writes"]) == delete_writes + 4
    page.screenshot(path=str(output / "question-restored-recycle-bin.png"))
    page.locator("#trashDialog [data-close]").click()
    page.locator("#paperMenu > summary").click()
    page.locator("#viewOriginalPaper").click()
    expect(page.locator("#pageDialog")).to_be_visible()
    page.wait_for_function("document.querySelector('#pageStage img')?.complete && document.querySelector('#pageStage img')?.naturalWidth > 0")
    if page.locator("#pageNumberInput").input_value() != "1":
        page.locator("#pageNumberInput").fill("1")
        page.locator("#pageNumberInput").press("Enter")
    fit()
    report["passed"].append("explicit whole-question deletion makes one recoverable soft-delete, preserves canvas position and restores original identity/content/ranges through the real recycle-bin UI")

    assert all(not q["ocr_pending"] for q in paper_data()["questions"])
    db = database_rows()
    assert not db["core_regionread"] and not db["core_libraryjob"]
    assert not report["forbidden_requests"], report["forbidden_requests"]
    report["passed"].append("all saves, renumber, supplement, split, merge and undo run without an implicit AI request or background job")
    # A saved box returns to its own review card; a normal return from the
    # workspace restores the opening context instead of guessing by number.
    select("first")
    # The first region contains another question's smaller frame. Click a
    # visible part of the larger range so Playwright doesn't target that box.
    page.mouse.dblclick(*point(500, 140))
    expect(page.locator("#pageDialog")).not_to_be_visible()
    expect(page.locator(f'.card[data-id="{fixture["first"]}"]')).to_be_visible()
    page.screenshot(path=str(output / "review-question-jump.png"))
    report["passed"].append("double-clicking a saved range returns to the matching review card")


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
