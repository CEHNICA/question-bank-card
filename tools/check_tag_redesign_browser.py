"""Isolated browser acceptance for contextual tags and the standalone manager.

The fixture is synthetic. All application data and configuration must be under
checkout/tmp; the browser blocks external requests and no AI worker is started.
Use the repository's packaging build Python (Django + Playwright) with --run.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import check_library_tags_browser as regression


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "tag-redesign-browser"
DRAFT_NAME = "未提交的中点专项标签123（隔离验收）"


def isolated_environment():
    """Never inherit real application storage, credentials or AI settings."""
    user = OUTPUT / "user"
    user.mkdir(parents=True, exist_ok=True)
    paths = {
        "QB_USER_ROOT": user,
        "QB_DATABASE": user / "db.sqlite3",
        "QB_DATA_ROOT": user / "data",
        "QB_FEATURES_FILE": user / "features.json",
        "QB_LIBRARY_AI_SETTINGS_FILE": user / "library-ai.json",
        "QB_LIBRARY_AI_CREDENTIAL_FILE": user / "library-ai-credentials.json",
        "QB_CREDENTIAL_FILE": user / "credentials.json",
    }
    for key, value in paths.items():
        os.environ[key] = str(value)
    os.environ["QB_DISABLE_WORKER"] = "1"
    regression.OUTPUT = OUTPUT
    regression._assert_isolated()


def seed():
    isolated_environment()
    if Path(os.environ["QB_DATABASE"]).exists():
        raise SystemExit("Use a fresh --output directory; existing fixture data will not be replaced")
    regression.seed()
    from core import library, library_tagging
    from core.models import LibraryTag, LibraryTagSuggestion, PublishedQuestion, Question

    fixture = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    first = PublishedQuestion.objects.get(pk=fixture["ids"][0])
    stem = "虚构题三：仅用于验证失败标签建议的重试入口，不发送模型请求。"
    question = Question.objects.create(paper=first.paper, number=3,
        question_type="free_response", stem=stem)
    content = {**first.content, "stem": stem}
    third = PublishedQuestion.objects.create(question=question, paper=first.paper,
        source_filename=first.source_filename, number=3, question_type="free_response",
        version=1, status=PublishedQuestion.Status.PUBLISHED, review_source="human",
        content=content, content_hash=library.content_hash(content),
        search_text=library.search_key(stem), extras={})
    LibraryTagSuggestion.objects.create(publication=third,
        question_fingerprint=library.generation_fingerprint(third.content, third.pk),
        taxonomy_revision=library_tagging.catalogue_state().revision,
        assignment_revision=third.tag_revision, suggestions=[],
        status=LibraryTagSuggestion.Status.FAILED,
        message="隔离合成失败：可重试，无真实服务请求。", generation_source="离线验收")
    fixture["failure_id"] = str(third.pk)
    stem = "虚构题四：二次函数的最值，用于真实提交后返回丢失的验收。"
    question = Question.objects.create(paper=first.paper, number=4,
        question_type="free_response", stem=stem)
    content = {**first.content, "stem": stem}
    fourth = PublishedQuestion.objects.create(question=question, paper=first.paper,
        source_filename=first.source_filename, number=4, question_type="free_response",
        version=1, status=PublishedQuestion.Status.PUBLISHED, review_source="human",
        content=content, content_hash=library.content_hash(content),
        search_text=library.search_key(stem), extras={})
    knowledge = LibraryTag.objects.get(kind=LibraryTag.Kind.KNOWLEDGE, name=regression.TAG_NAME)
    LibraryTagSuggestion.objects.create(publication=fourth,
        question_fingerprint=library.generation_fingerprint(fourth.content, fourth.pk),
        taxonomy_revision=library_tagging.catalogue_state().revision,
        assignment_revision=fourth.tag_revision,
        suggestions=[{"kind": "knowledge", "tag_id": str(knowledge.pk), "name": knowledge.name,
            "rationale": "合成题面直接要求求二次函数的最值。", "scope": {"whole": True}}],
        status=LibraryTagSuggestion.Status.PENDING,
        message="仅用于提交丢响应及重试去重验收。", generation_source="离线验收")
    fixture["lost_reply_id"] = str(fourth.pk)
    (OUTPUT / "fixture.json").write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")


def check(base):
    """Real DOM, network contracts and persisted assignments are checked here."""
    from playwright.sync_api import sync_playwright, expect

    regression._assert_isolated()
    if urlparse(base).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Only a local isolated server is accepted")
    fixture = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    version_node = next(node for node in ast.parse((ROOT / "backend/core/version.py").read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets))
    report = {"checks": [], "errors": [], "external_requests": [], "screenshots": [],
        "version": ast.literal_eval(version_node.value), "tested_artifact": "current source"}
    with sync_playwright() as playwright:
        executable = next((str(path) for path in (
            Path(playwright.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
        browser = playwright.chromium.launch(headless=True,
            **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 960}, device_scale_factor=1)

        def local_only(route):
            if urlparse(route.request.url).hostname in {"127.0.0.1", "localhost"}:
                route.continue_()
            else:
                report["external_requests"].append(route.request.url)
                route.abort()

        context.route("**/*", local_only)
        page = context.new_page()
        page.on("pageerror", lambda error: report["errors"].append(str(error)))

        # Readiness is a local UI fixture only. Every queued AI request is
        # intercepted below and no provider or worker is contacted.
        fixture_api_ready = {"value": True}

        def fixture_ai_ready(route):
            response = route.fetch()
            body = response.json()
            body["ai"] = {"mode": "api", "api_ready": fixture_api_ready["value"], "provider": "offline-test"}
            route.fulfill(response=response, json=body)

        page.route("**/api/library?*", fixture_ai_ready)
        page.route("**/api/settings/library-ai", lambda route: route.fulfill(status=200,
            content_type="application/json", body=json.dumps({"mode": "api", "api_ready": True})))
        queued_requests = []

        def fixture_queue(route):
            queued_requests.append(route.request.post_data_json)
            route.fulfill(status=200, content_type="application/json", body='{"queued":1,"skipped":0}')

        page.route("**/api/library/jobs", fixture_queue)
        page.goto(base.rstrip("/") + "/library")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".library-card")).to_have_count(4)
        filterbar = page.locator("#libraryFilterBar")
        expect(filterbar).to_be_visible()
        for control in ("#sourceSelect", "#typeFilters", "#sortSelect", "#advancedFilters"):
            expect(page.locator(control)).to_be_visible()
            assert page.locator(control).evaluate("node => !!node.closest('#libraryFilterBar')"), control
        for control in ("#allQuestionsButton", "#basketViewButton"):
            expect(page.locator(control)).to_be_visible()
            assert page.locator(control).evaluate("node => !!node.closest('#libraryRail')"), control
        for control in ("#sourceSelect", "#typeFilters", "#sortSelect", "#advancedFilters"):
            assert not page.locator(control).evaluate("node => !!node.closest('#libraryRail')"), control
        assert not page.locator("#allQuestionsButton").evaluate("node => !!node.closest('#libraryFilterBar')")
        filter_height = page.locator(".library-bulk").evaluate("node => node.offsetHeight")
        page.locator("#advancedFilters > summary").click()
        expect(page.locator("#advancedFilters")).to_have_attribute("open", "")
        expect(page.locator("#advancedFilters > .library-filters")).to_be_visible()
        assert page.locator(".library-bulk").evaluate("node => node.offsetHeight") == filter_height, "More Filters must float without pushing question cards down"
        page.locator("#advancedFilters > summary").click()
        page.screenshot(path=str(OUTPUT / "question-filterbar-desktop.png"), full_page=False)
        mobile = context.new_page()
        mobile.set_viewport_size({"width": 390, "height": 844})
        mobile.goto(base.rstrip("/") + "/library")
        mobile.wait_for_load_state("networkidle")
        mobile_filterbar = mobile.locator("#libraryFilterBar")
        expect(mobile_filterbar).to_be_visible()
        assert mobile_filterbar.evaluate("node => node.scrollWidth <= node.clientWidth"), "mobile filter bar must not clip controls horizontally"
        assert mobile.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "mobile page must not gain horizontal scrolling"
        assert not mobile.locator("body").evaluate("node => node.classList.contains('rail-open')"), "mobile sidebar should remain closed while top filters are available"
        mobile.screenshot(path=str(OUTPUT / "question-filterbar-mobile.png"), full_page=False)
        mobile.close()
        report["screenshots"].extend([str(OUTPUT / "question-filterbar-desktop.png"), str(OUTPUT / "question-filterbar-mobile.png")])
        report["checks"].append("Source, question type, sort, and advanced filters live above the list; mobile controls wrap without horizontal overflow and More Filters does not shift cards")
        extra_tools = page.locator("#tagQuickTools")
        expect(extra_tools).to_be_visible()
        expect(page.locator("#libraryTagManager")).to_be_visible()
        expect(extra_tools.get_by_role("button", name="标签管理")).to_have_count(0)
        batch_container = page.locator("#aiBatchTools")
        expect(batch_container).to_be_visible()
        batch_tools = batch_container.locator("details.library-batch-ai")
        expect(batch_tools).to_have_count(1)
        expect(batch_tools).not_to_have_attribute("open", "")
        expect(extra_tools.get_by_role("button", name=re.compile("AI 建议待核对"))).to_be_visible()
        expect(extra_tools.get_by_role("button", name=re.compile("未标知识点"))).to_be_visible()
        inspector = page.locator("#questionInspector")
        first = page.locator("#q-" + fixture["ids"][0])
        second = page.locator("#q-" + fixture["ids"][1])

        def capture(name):
            if page.locator("#toast").count():
                expect(page.locator("#toast")).to_be_hidden(timeout=8000)
            destination = OUTPUT / name
            page.screenshot(path=str(destination), full_page=False)
            report["screenshots"].append(str(destination))

        def read_api(path):
            response = page.request.get(base.rstrip("/") + path, headers={"X-QB-Request": "1"})
            assert response.ok, response.text()
            return response.json()

        def assignment_ids(publication_id):
            result = read_api("/api/library/tags/assignments/batch?ids=" + publication_id)
            return [entry["tag"]["id"] for entry in result["items"][publication_id]["assignments"]]

        # Expanding the disclosure is informational only. It exposes the
        # API-consuming tasks without starting one or hiding quick filters.
        batch_tools.locator("summary").click()
        expect(batch_tools).to_have_attribute("open", "")
        expect(batch_tools.get_by_role("button", name=re.compile("全库缺少知识点"))).to_be_visible()
        expect(batch_tools.get_by_role("button", name=re.compile("当前筛选全部"))).to_be_visible()
        assert queued_requests == [], queued_requests
        capture("tag-batch-tools.png")
        batch_tools.locator("summary").click()
        expect(batch_tools).not_to_have_attribute("open", "")
        fixture_api_ready["value"] = False
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#tagQuickTools .library-tool-quick")).to_be_visible()
        expect(page.locator("#aiBatchTools .library-batch-ai")).to_be_visible()
        expect(page.locator("#aiBatchTools .library-batch-ai button")).to_have_count(2)
        expect(page.locator("#aiBatchTools .library-batch-ai button").nth(0)).to_be_disabled()
        expect(page.locator("#aiBatchTools .library-batch-ai button").nth(1)).to_be_disabled()
        assert queued_requests == [], queued_requests
        fixture_api_ready["value"] = True
        page.reload()
        page.wait_for_load_state("networkidle")
        report["checks"].append("Quick tag filters stay visible outside More Filters; API-consuming batch actions are folded and expanding them makes no request")
        report["checks"].append("API batch actions stay disabled when local readiness is false, while quick filters remain available")

        # These tests deliberately retain all four checked questions. The
        # inspector must still act on only the context question.
        first.locator(".paper").click()
        page.locator("#selectVisible").check()
        before_second = assignment_ids(fixture["ids"][1])
        inspector.locator(".library-tags-edit").click()
        picker = page.locator("#tagPickerDialog")
        expect(picker).to_be_visible()
        expect(picker.locator("#tagPickerStatus")).to_have_text("单题")
        expect(picker.locator("#tagPickerTemplates")).to_have_count(0)
        expect(picker.locator("#tagPickerStartEmpty")).to_have_count(0)
        picker.locator("#tagPickerSearch").fill(regression.TAG_NAME)
        row = picker.locator(".tag-picker-row").filter(has_text=regression.TAG_NAME).first
        expect(row.locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        with page.expect_response(lambda response: urlparse(response.url).path ==
                "/api/library/tags/assignments/batch" and response.request.method == "POST") as single:
            row.click()
        assert single.value.ok, single.value.text()
        assert single.value.request.post_data_json["publication_ids"] == [fixture["ids"][0]]
        assert assignment_ids(fixture["ids"][1]) == before_second
        picker.locator("#tagPickerUndo").click()
        expect(row.locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        picker.get_by_role("button", name="关闭", exact=True).click()
        report["checks"].append("Contextual tag picker remains single-question with multiple checkboxes selected")

        # Direct removal has one explicit target, never re-filters the list,
        # and exposes the same persistent-operation undo as picker changes.
        remove = inspector.locator(".library-tags-remove").first
        expect(remove).to_be_visible()
        with page.expect_response(lambda response: urlparse(response.url).path ==
                "/api/library/tags/assignments/batch" and response.request.method == "POST") as removed:
            remove.click()
        assert removed.value.ok, removed.value.text()
        assert removed.value.request.post_data_json["publication_ids"] == [fixture["ids"][0]]
        expect(page.locator(".library-card")).to_have_count(4)
        expect(first).to_have_attribute("aria-current", "true")
        expect(inspector.locator(".library-tags")).not_to_contain_text(regression.TAG_NAME)
        expect(inspector.locator(".library-tags-undo")).to_be_visible()
        inspector.locator(".library-tags-undo").click()
        expect(inspector.locator(".library-tags")).to_contain_text(regression.TAG_NAME)
        assert assignment_ids(fixture["ids"][1]) == before_second
        report["checks"].append("Direct remove and undo affect only the context question without changing filters")

        # This request genuinely commits on the isolated server; only its
        # browser-visible response is lost. Recovery after reload must replay
        # the same operation and revision even though no pending panel remains.
        fourth = page.locator("#q-" + fixture["lost_reply_id"])
        fourth.locator(".paper").click()
        lost_panel = inspector.locator("aside.tag-suggestion-card")
        prior_operations = {operation["id"] for operation in read_api("/api/library/tags/operations")["operations"]}
        committed, replay_requests = [], []

        def commit_then_lose_reply(route):
            replay_requests.append(route.request.post_data_json)
            result = route.fetch()
            assert result.ok, result.text()
            committed.append(result.json())
            route.fulfill(status=503, content_type="application/json",
                body=json.dumps({"error": "隔离验收：服务器已采纳，返回结果丢失。"}, ensure_ascii=False))

        resolve_pattern = "**/api/library/tag-suggestions/*/resolve"
        page.route(resolve_pattern, commit_then_lose_reply)
        lost_panel.locator(".tag-suggestion-accept").click()
        expect(inspector.locator(".tag-suggestion-retry")).to_be_visible()
        assert len(committed) == 1 and len(replay_requests) == 1
        stored_revision = read_api("/api/library/tags/assignments/batch?ids=" + fixture["lost_reply_id"])["items"][fixture["lost_reply_id"]]["revision"]
        page.unroute(resolve_pattern, commit_then_lose_reply)
        page.reload()
        page.wait_for_load_state("networkidle")
        fourth.locator(".paper").click()
        expect(inspector.locator("aside.tag-suggestion-card")).to_have_count(0)
        expect(inspector.locator(".library-tags")).to_contain_text(regression.TAG_NAME)
        expect(inspector.locator(".tag-suggestion-retry")).to_be_visible()
        with page.expect_response(lambda response: urlparse(response.url).path.endswith("/resolve")
                and response.request.method == "POST") as replayed:
            inspector.locator(".tag-suggestion-retry").click()
        assert replayed.value.ok, replayed.value.text()
        assert replayed.value.request.post_data_json == replay_requests[0]
        assert replayed.value.json().get("replayed") is True
        if replayed.value.json().get("operation_id"):
            assert replayed.value.json()["operation_id"] == committed[0]["operation_id"]
        expect(inspector.locator(".tag-suggestion-retry")).to_have_count(0)
        current_revision = read_api("/api/library/tags/assignments/batch?ids=" + fixture["lost_reply_id"])["items"][fixture["lost_reply_id"]]["revision"]
        assert current_revision == stored_revision
        new_operations = [operation for operation in read_api("/api/library/tags/operations")["operations"]
            if operation["id"] not in prior_operations]
        assert len(new_operations) == 1 and new_operations[0]["action"] == "suggestion:accept"
        assert new_operations[0]["id"] == committed[0]["operation_id"]
        report["checks"].append("Real committed AI acceptance with lost reply recovers after reload without duplicate operation or revision")

        # Saved AI candidates remain drafts while users inspect other cards.
        # The same tab's reload must preserve names, group choice and selection.
        second.locator(".paper").click()
        proposal = inspector.locator("aside.tag-suggestion-card")
        entry = proposal.locator('.tag-suggestion-entry[data-suggestion-index="1"]')
        editor = entry.locator("details.tag-suggestion-edit")
        editor.locator("summary").click()
        name = editor.locator("input[data-new-tag-name]")
        name.fill(DRAFT_NAME)
        groups = editor.locator("input[data-new-tag-group]")
        expect(groups.first).to_be_visible()
        groups.first.check()
        draft_group_id = groups.first.input_value()
        entry.locator("input[data-tag-suggestion]").check()
        first.locator(".paper").click()
        second.locator(".paper").click()
        expect(name).to_have_value(DRAFT_NAME)
        expect(groups.first).to_be_checked()
        expect(entry.locator("input[data-tag-suggestion]")).to_be_checked()
        page.reload()
        page.wait_for_load_state("networkidle")
        second.locator(".paper").click()
        expect(name).to_have_value(DRAFT_NAME)
        expect(groups.first).to_be_checked()
        expect(entry.locator("input[data-tag-suggestion]")).to_be_checked()
        report["checks"].append("AI candidate draft name, groups and checkboxes survive question switching and reload")

        # Hold a local request in-flight to check locking, then inject a failed
        # reply. Retrying must use the exact same request, not a second action.
        held, resolve_payloads = [], []

        def hold_resolution(route):
            held.append(route)
            resolve_payloads.append(route.request.post_data_json)

        resolve_pattern = "**/api/library/tag-suggestions/*/resolve"
        page.route(resolve_pattern, hold_resolution)
        entry.locator(".tag-suggestion-accept").click()
        page.wait_for_function("document.querySelector('.tag-suggestion-accept:disabled') !== null")
        assert len(held) == 1 and len(resolve_payloads) == 1
        expect(entry.locator(".tag-suggestion-accept")).to_be_disabled()
        expect(entry.locator(".tag-suggestion-reject")).to_be_disabled()
        expect(name).to_be_disabled()
        held[0].fulfill(status=503, content_type="application/json",
            body=json.dumps({"error": "本机验收：采纳返回暂时失败，保留草稿。"}, ensure_ascii=False))
        expect(entry.locator(".tag-suggestion-accept")).to_be_enabled()
        expect(name).to_have_value(DRAFT_NAME)
        expect(groups.first).to_be_checked()
        page.unroute(resolve_pattern, hold_resolution)
        page.set_viewport_size({"width": 1440, "height": 640})
        page.evaluate("window.scrollTo(0, Math.max(1, document.documentElement.scrollHeight / 3))")
        second.scroll_into_view_if_needed()
        before_accept_position = second.evaluate("element => ({top: element.getBoundingClientRect().top, scrollY: window.scrollY})")
        assert before_accept_position["scrollY"] > 0, before_accept_position
        with page.expect_response(lambda response: urlparse(response.url).path.endswith("/resolve")
                and response.request.method == "POST") as accepted:
            entry.locator(".tag-suggestion-accept").click()
        assert accepted.value.ok, accepted.value.text()
        after_accept_position = second.evaluate("element => ({top: element.getBoundingClientRect().top, scrollY: window.scrollY})")
        assert abs(after_accept_position["top"] - before_accept_position["top"]) < 12, (before_accept_position, after_accept_position)
        assert after_accept_position["scrollY"] > 0, after_accept_position
        assert "inspector-active" in (second.get_attribute("class") or "")
        expect(inspector.locator(".question-inspector-target")).to_contain_text("右侧信息对应绿色高亮题卡")
        page.evaluate("window.scrollTo(0, 0)")
        inspector.locator(".question-inspector-locate").click()
        expect(second).to_have_class(re.compile("inspector-focus-pulse"))
        assert "inspector-active" in (second.get_attribute("class") or "")
        report["checks"].append("AI tag acceptance keeps the target question in place and highlights it; inspector locator focuses the matching card")
        page.set_viewport_size({"width": 1440, "height": 960})
        accepted_payload = accepted.value.request.post_data_json
        assert accepted_payload == resolve_payloads[0], (accepted_payload, resolve_payloads)
        assert accepted_payload["indices"] == [1]
        assert accepted_payload["new_tags"]["1"]["name"] == DRAFT_NAME
        assert accepted_payload["new_tags"]["1"]["group_ids"] == [draft_group_id]
        expect(proposal.locator('.tag-suggestion-entry[data-suggestion-index="1"]')).to_have_count(0)
        expect(inspector.locator(".library-tags")).to_contain_text("未提交的中点专项标签123")
        remaining = proposal.locator('.tag-suggestion-entry[data-suggestion-index="0"]')
        with page.expect_response(lambda response: urlparse(response.url).path.endswith("/resolve")
                and response.request.method == "POST") as rejected:
            remaining.locator(".tag-suggestion-reject").click()
        assert rejected.value.ok, rejected.value.text()
        assert rejected.value.request.post_data_json["indices"] == [0]
        expect(inspector.locator("aside.tag-suggestion-card")).to_have_count(0)
        inspector.get_by_role("button", name="AI 标签记录", exact=True).click()
        history = page.locator(".tag-history-dialog")
        expect(history).to_contain_text("已采纳")
        expect(history).to_contain_text("已拒绝")
        history.get_by_role("button", name="关闭", exact=True).click()
        report["checks"].append("AI per-entry accept/reject locks in-flight edits, retries identically and removes decided entries")

        # Batch scope belongs to the visible batch toolbar, not the inspector.
        page.locator("#selectVisible").check()
        page.locator("#editSelectedTags").click()
        expect(picker).to_be_visible()
        expect(picker.locator("#tagPickerStatus")).to_have_text("已选 4 题")
        picker.locator("#tagPickerSearch").fill(regression.TAG_NAME)
        expect(row.locator(".tag-picker-row-state")).to_contain_text("部分有 2/4")
        with page.expect_response(lambda response: urlparse(response.url).path ==
                "/api/library/tags/assignments/batch" and response.request.method == "POST") as batch:
            row.click()
        assert batch.value.ok, batch.value.text()
        assert set(batch.value.request.post_data_json["publication_ids"]) == {
            *fixture["ids"], fixture["failure_id"], fixture["lost_reply_id"]}
        picker.locator("#tagPickerUndo").click()
        expect(row.locator(".tag-picker-row-state")).to_contain_text("部分有 2/4")
        picker.get_by_role("button", name="关闭", exact=True).click()
        page.locator("#clearSelection").click()
        report["checks"].append("Explicit batch toolbar retains atomic mixed-state add and undo")

        failed = page.locator("#q-" + fixture["failure_id"])
        failed.locator(".paper").click()
        expect(inspector).to_contain_text("生成失败")
        failed.locator(".library-card-more > summary").click()
        failed.get_by_role("button", name="重新建议", exact=True).click()
        page.wait_for_function("document.querySelector('#toast')?.textContent.includes('已排队')")
        assert queued_requests == [{"kind": "tags", "ids": [fixture["failure_id"]],
            "retry": True, "api_only": True}], queued_requests
        report["checks"].append("Failure state exposes explicit retry with only the selected question; provider request is blocked")

        first.locator(".paper").click()
        assert inspector.locator(".question-inspector-tags").evaluate("""element => {
            const info = document.querySelector('.question-inspector-info');
            return info && Boolean(element.compareDocumentPosition(info) & Node.DOCUMENT_POSITION_FOLLOWING);
        }""")
        expect(inspector.locator("details.question-inspector-info")).not_to_have_attribute("open", "")
        capture("context-desktop.png")

        # Reading a question must not open an obscuring drawer on phones.
        page.set_viewport_size({"width": 390, "height": 844})
        first.locator(".paper").click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "false")
        second.locator(".paper").click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "false")
        expect(second).to_have_attribute("aria-current", "true")
        capture("context-mobile-reading.png")
        second.locator(".library-card-details").click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "true")
        expect(inspector).to_be_visible()
        expect(inspector).to_contain_text("第 2 题")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        capture("context-mobile-details.png")
        page.locator("#closeInspector").click()
        report["checks"].append("390px reading remains unobscured until explicit details entry is clicked")

        # The standalone manager's interactions are verified after the common
        # single/batch scope and AI checks, using a restored desktop viewport.
        page.set_viewport_size({"width": 1440, "height": 960})
        page.locator("#libraryTagManager").click()
        manager = page.locator("#tagManagerWorkspace")
        expect(manager).to_be_visible()
        expect(page.locator("#tagManagerDialog")).to_have_count(0)
        assert manager.evaluate("element => element.tagName !== 'DIALOG'")
        expect(page.locator(".library-workspace")).to_be_hidden()
        expect(manager.locator("#tagManagerReset")).not_to_be_visible()
        catalogue = read_api("/api/library/tags")
        target_tag = next(tag for tag in catalogue["tags"] if tag["name"] == regression.TAG_NAME)
        source_tag = next(tag for tag in catalogue["tags"] if "未提交的中点专项标签123" in tag["name"])
        target_id, source_id = target_tag["id"], source_tag["id"]
        target_row = manager.locator(f'.tag-directory-row[data-tag-id="{target_id}"]')
        source_row = manager.locator(f'.tag-directory-row[data-tag-id="{source_id}"]')
        target_row.locator(".tag-directory-name").click()
        expect(manager.locator("#tagManagerDetail")).to_contain_text(regression.TAG_NAME)
        expect(manager.locator("#tagManagerDetail")).to_contain_text("2 道题")
        capture("tag-manager-desktop.png")
        target_row.locator(".tag-directory-usage").click()
        expect(manager).to_be_hidden()
        expect(page.locator(".library-card")).to_have_count(2)
        expect(first).to_be_visible()
        page.get_by_role("button", name="清除筛选", exact=True).click()
        expect(page.locator(".library-card")).to_have_count(4)
        report["checks"].append("Tag manager is a standalone page; selected-tag details and usage counts open exact matching questions")

        # Two explicit checkboxes establish multi-tag scope. Adding a group
        # preserves all existing memberships; removing it preserves tags and
        # question assignments. Real server replies are inspected each time.
        page.locator("#libraryTagManager").click()
        manager.locator("#tagManagerCreateGroup").click()
        catalogue_editor = page.locator("#tagCatalogueEditorDialog")
        expect(catalogue_editor).to_be_visible()
        group_name = "专项验收临时分组ABC123"
        catalogue_editor.locator("#tagCatalogueName").press_sequentially(group_name, delay=3)
        expect(catalogue_editor.locator("#tagCatalogueName")).to_have_value(group_name)
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                and response.request.method == "POST") as group_created:
            catalogue_editor.locator("#tagCatalogueSave").click()
        assert group_created.value.ok, group_created.value.text()
        created_group = next(group for group in group_created.value.json()["catalogue"]["groups"]
            if group["name"] == group_name)
        manager.locator('#tagManagerGroups button[data-group-id="all"]').click()
        target_row.locator('input[type="checkbox"]').check()
        source_row.locator('input[type="checkbox"]').check()
        expect(manager.locator("#tagManagerBulk")).to_contain_text("已选 2 个")
        manager.locator("#tagManagerBulkGroup").select_option(created_group["id"])
        capture("tag-manager-bulk.png")
        before_memberships = {tag["id"]: {group["id"] for group in tag["groups"]}
            for tag in read_api("/api/library/tags")["tags"] if tag["id"] in {target_id, source_id}}
        publication_ids = [*fixture["ids"], fixture["lost_reply_id"]]
        before_assignments = {publication_id: assignment_ids(publication_id) for publication_id in publication_ids}
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                and response.request.method == "POST") as grouped:
            manager.locator("#tagManagerBulkAdd").click()
        assert grouped.value.ok, grouped.value.text()
        payload = grouped.value.request.post_data_json
        assert payload["action"] == "group_membership_batch" and payload["mode"] == "add"
        assert set(payload["tag_ids"]) == {target_id, source_id}
        for tag in grouped.value.json()["catalogue"]["tags"]:
            if tag["id"] in before_memberships:
                assert {group["id"] for group in tag["groups"]} == before_memberships[tag["id"]] | {created_group["id"]}
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                and response.request.method == "POST") as ungrouped:
            manager.locator("#tagManagerBulkRemove").click()
        assert ungrouped.value.ok, ungrouped.value.text()
        assert ungrouped.value.request.post_data_json["mode"] == "remove"
        for tag in ungrouped.value.json()["catalogue"]["tags"]:
            if tag["id"] in before_memberships:
                assert {group["id"] for group in tag["groups"]} == before_memberships[tag["id"]]
        assert {publication_id: assignment_ids(publication_id) for publication_id in publication_ids} == before_assignments
        report["checks"].append("Multi-tag add/remove group is atomic and retains original groups and question associations")

        history_section = manager.locator("details.tag-directory-history")

        def undo_history(label):
            if not history_section.evaluate("element => element.open"):
                history_section.locator("summary").click()
            operation = manager.locator(".tag-directory-history-row").filter(has_text=label).first
            with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags/undo"
                    and response.request.method == "POST") as undone:
                operation.get_by_role("button", name="撤销", exact=True).click()
            assert undone.value.ok, undone.value.text()

        # Used tags merge only after one explicit scope confirmation, and undo
        # restores the stable identities and assignments of both source tags.
        manager.locator("#tagManagerBulkMerge").click()
        merge_dialog = page.locator(".tag-directory-merge-dialog")
        expect(merge_dialog).to_be_visible()
        merge_dialog.locator("#tagManagerMergeTarget").select_option(target_id)
        confirms = []

        def accept_confirm(dialog):
            confirms.append(dialog.message)
            dialog.accept()

        page.once("dialog", accept_confirm)
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                and response.request.method == "POST") as merged:
            merge_dialog.locator("#tagManagerMergeSubmit").click()
        assert merged.value.ok, merged.value.text()
        assert len(confirms) == 1 and "影响" in confirms[0]
        assert merged.value.request.post_data_json["action"] == "merge_tags_batch"
        assert merged.value.request.post_data_json["source_ids"] == [source_id]
        assert merged.value.request.post_data_json["target_id"] == target_id
        expect(source_row).to_have_count(0)
        assert assignment_ids(fixture["ids"][1]) == [target_id]
        undo_history("批量合并标签")
        expect(source_row).to_have_count(1)
        assert {publication_id: assignment_ids(publication_id) for publication_id in publication_ids} == before_assignments
        report["checks"].append("Merge has one impact confirmation and undo restores source identities and assignments")

        # Deleting used tags must cover the full library, then one persisted
        # undo brings back all associations. The directory stays visible.
        target_row.locator('input[type="checkbox"]').check()
        source_row.locator('input[type="checkbox"]').check()
        page.once("dialog", accept_confirm)
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                and response.request.method == "POST") as deleted:
            manager.locator("#tagManagerBulkDelete").click()
        assert deleted.value.ok, deleted.value.text()
        assert len(confirms) == 2 and "3 道当前题" in confirms[-1]
        assert deleted.value.request.post_data_json["action"] == "delete_tags"
        expect(target_row).to_have_count(0)
        expect(source_row).to_have_count(0)
        assert all(not assignment_ids(publication_id) for publication_id in publication_ids)
        undo_history("批量删除标签")
        expect(target_row).to_have_count(1)
        expect(source_row).to_have_count(1)
        assert {publication_id: assignment_ids(publication_id) for publication_id in publication_ids} == before_assignments
        report["checks"].append("Batch delete previews full-library impact; persistent undo restores every association")

        # Delete the now-unused group separately: no tag is deleted or hidden.
        manager.locator('#tagManagerGroups button[data-group-id="' + created_group["id"] + '"]').click()
        page.once("dialog", accept_confirm)
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                and response.request.method == "POST") as group_deleted:
            manager.locator("#tagManagerGroupActions").get_by_role("button", name="删除分组（保留标签）", exact=True).click()
        assert group_deleted.value.ok, group_deleted.value.text()
        assert group_deleted.value.request.post_data_json["action"] == "delete_group"
        expect(target_row).to_have_count(1)
        expect(source_row).to_have_count(1)
        capture("tag-manager-after-operations.png")
        page.set_viewport_size({"width": 390, "height": 844})
        expect(manager).to_be_visible()
        search = manager.locator("#tagManagerSearch")
        search.fill("二次函数")
        expect(search).to_have_value("二次函数")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        capture("tag-manager-mobile.png")
        manager.locator("#tagManagerBack").click()
        expect(page.locator(".library-workspace")).to_be_visible()
        expect(page.locator(".library-card")).to_have_count(4)
        report["checks"].append("Group delete preserves labels; standalone manager fits 390px and returns to unchanged question list")

        page.wait_for_load_state("networkidle")
        page.unroute_all(behavior="wait")
        context.unroute_all(behavior="wait")
        assert not report["errors"], report["errors"]
        assert not report["external_requests"], report["external_requests"]
        context.close()
        browser.close()
    report["success"] = True
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Tag redesign browser checks passed: " + "; ".join(report["checks"]), flush=True)


def run(regression_only=False):
    if regression_only:
        isolated_environment()
        if Path(os.environ["QB_DATABASE"]).exists():
            raise SystemExit("Use a fresh --output directory; existing fixture data will not be replaced")
        regression.seed()
    else:
        seed()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (OUTPUT / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, str(ROOT / "backend/manage.py"), "runserver",
            f"127.0.0.1:{port}", "--noreload"], cwd=ROOT, env=os.environ.copy(), stdout=log,
            stderr=subprocess.STDOUT, creationflags=flags)
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError("Isolated server exited before becoming ready")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", port)) == 0:
                        break
                time.sleep(0.1)
            else:
                raise TimeoutError("Isolated server did not become ready")
            (regression.check if regression_only else check)(f"http://127.0.0.1:{port}")
        finally:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, check=False)
            else:
                server.terminate()
            server.wait(timeout=10)
    receipt = OUTPUT / "report.json"
    if receipt.exists():
        report = json.loads(receipt.read_text(encoding="utf-8"))
        report.update(server_stopped=True, worker_started=False, isolated_root=str(OUTPUT / "user"))
        receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--full-regression", action="store_true",
        help="With --run, execute the existing complete formal-tag browser regression on its own fresh fixture")
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--output", type=Path,
        default=OUTPUT / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]))
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    if not OUTPUT.is_relative_to(ROOT / "tmp"):
        raise SystemExit("--output must be under this checkout's tmp directory")
    regression.OUTPUT = OUTPUT
    if args.run:
        run(args.full_regression)
    elif args.seed:
        seed()
    elif args.url:
        check(args.url.rstrip("/"))
    else:
        parser.error("Choose --run, --seed or --url")
