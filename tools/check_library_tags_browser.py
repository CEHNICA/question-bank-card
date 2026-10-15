"""Offline browser check for formal tags and human-reviewed AI candidates.

Only run against an isolated database and data directory under this checkout's
tmp folder. It creates synthetic math questions and never contacts a provider.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import unicodedata
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "tag-ui-browser"
TAG_NAME = "二次函数的最值"
NEW_TAG_NAME = "合成中点关系（浏览器验收）"
CUSTOM_GROUP_NAME = "我的课堂专项分组123（浏览器验收）"
CUSTOM_TAG_NAME = "函数与几何综合复习自定义标签123（浏览器验收）"
CUSTOM_TAG_DESCRIPTION = "中文说明保留：供本机隔离浏览器验收，包含数字123。"
RETRY_TAG_NAME = "响应丢失后安全重试标签456（浏览器验收）"
EDITED_TAG_NAME = "课堂综合复习标签789（修改验收）"
EDITED_GROUP_NAME = "课堂复习分类456（修改验收）"
EDITED_ALIASES = ["课堂综合456", "综合复习789"]


def _assert_isolated():
    for name in ("QB_DATABASE", "QB_DATA_ROOT", "QB_FEATURES_FILE", "QB_LIBRARY_AI_SETTINGS_FILE",
                 "QB_LIBRARY_AI_CREDENTIAL_FILE", "QB_CREDENTIAL_FILE"):
        value = os.environ.get(name)
        if not value or not Path(value).resolve().is_relative_to(ROOT / "tmp"):
            raise SystemExit(f"{name} must point inside this checkout's tmp directory")


def seed():
    _assert_isolated()
    Path(os.environ["QB_DATABASE"]).resolve().parent.mkdir(parents=True, exist_ok=True)
    Path(os.environ["QB_DATA_ROOT"]).resolve().mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.core.management import call_command
    from django.utils import timezone
    from core import features, library, library_tagging
    from core.tag_catalogue_seed import seed_catalogue
    from core.models import (LibraryTag, LibraryTagAssignment, LibraryTagSuggestion,
                             Paper, PublishedQuestion, Question)

    call_command("migrate", verbosity=0)
    features.save({"knowledge_tags": True, "ai_answer": False})
    # The fixture explicitly starts with the reviewed template directory;
    # production catalogue reads remain side-effect free.
    seed_catalogue()
    paper = Paper.objects.create(filename="仅用于标签界面验收.pdf", kind="pdf", sha256="tag-browser-synthetic",
                                 pages=1, status="ready")
    ids = []
    for number, stem in enumerate((
        "虚构题一：已知二次函数 y=x²−4x+3，求最小值。",
        "虚构题二：三角形两边中点连线，说明它与第三边的关系。",
    ), 1):
        question = Question.objects.create(paper=paper, number=number, question_type="free_response", stem=stem)
        content = {"stem": stem, "options": {}, "answer": "", "analysis": "", "figures": [],
                   "sources": [], "origin": "合成浏览器验收数据", "source_filename": paper.filename,
                   "document_id": str(paper.pk), "question_type": "free_response"}
        publication = PublishedQuestion.objects.create(
            question=question, paper=paper, source_filename=paper.filename, number=number,
            question_type="free_response", version=1, status=PublishedQuestion.Status.PUBLISHED,
            review_source="human", content=content, content_hash=library.content_hash(content),
            search_text=library.search_key(stem), extras={})
        ids.append(str(publication.pk))

    knowledge_tag = LibraryTag.objects.get(kind=LibraryTag.Kind.KNOWLEDGE, name=TAG_NAME)
    first = PublishedQuestion.objects.get(pk=ids[0])
    LibraryTagAssignment.objects.create(publication=first, tag=knowledge_tag,
        source=LibraryTagAssignment.Source.HUMAN)
    first.tag_revision = 1
    first.extras = {"tags": [TAG_NAME], "tags_source": "human"}
    first.tags_text = library.tags_text([TAG_NAME])
    first.save(update_fields=["tag_revision", "extras", "tags_text"])

    second = PublishedQuestion.objects.get(pk=ids[1])
    method = LibraryTag.objects.get(kind=LibraryTag.Kind.METHOD, name="构造中位线")
    catalogue = library_tagging.catalogue_state()
    LibraryTagSuggestion.objects.create(
        publication=second, question_fingerprint=library.generation_fingerprint(second.content, second.pk),
        taxonomy_revision=catalogue.revision, assignment_revision=second.tag_revision, method_source={},
        suggestions=[{"kind": "method", "tag_id": str(method.pk), "name": method.name,
                      "description": method.description, "rationale": "连接两边中点后得到平行关系。",
                      "evidence": "题干明确给出两个中点。", "scope": {"subquestions": ["1"]}},
                     {"kind": "knowledge", "new_name": NEW_TAG_NAME,
                      "levels": ["初中"],
                      "description": "仅用于验证新标签编辑栏，不是正式目录内容。",
                      "rationale": "合成题面给出了三角形两边中点。",
                      "evidence": "仅用于浏览器交互验收。", "scope": {"whole": True}}],
        status=LibraryTagSuggestion.Status.PENDING, message="来自合成测试的候选，须人工逐条核对。",
        generation_source="隔离测试模型")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "fixture.json").write_text(json.dumps({"ids": ids}, ensure_ascii=False), encoding="utf-8")
    print("Synthetic tag browser fixture ready")


def check(base: str):
    from playwright.sync_api import sync_playwright, expect

    _assert_isolated()
    if urlparse(base).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("The tag browser check only accepts a local server")
    fixture = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    errors, external = [], []
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
                external.append(route.request.url)
                route.abort()

        context.route("**/*", local_only)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base.rstrip("/") + "/library")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".library-card")).to_have_count(2)
        for publication_id in fixture["ids"]:
            expect(page.locator("#q-" + publication_id)).to_have_count(1)
        first = page.locator("#q-" + fixture["ids"][0])
        second = page.locator("#q-" + fixture["ids"][1])
        inspector = page.locator("#questionInspector")
        first.locator(".paper").click()
        expect(inspector).to_contain_text("第 1 题")
        second.locator(".paper").click()
        expect(inspector).to_contain_text("第 2 题")
        expect(inspector).to_contain_text("仅用于标签界面验收.pdf")
        expect(second).to_have_attribute("aria-current", "true")
        expect(first).not_to_have_attribute("aria-current", "true")
        expect(second.locator(".library-card-body aside.tag-suggestion-card")).to_have_count(0)
        proposal = inspector.locator("aside.tag-suggestion-card")
        expect(proposal).to_contain_text("AI 标签建议")
        expect(proposal).to_contain_text("待核对")

        def check_picker_layout():
            """Check the painted scroll panes and footer, including short screens."""
            picker = page.locator("#tagPickerDialog")
            panes = (picker.locator(".tag-picker-body"),
                     picker.locator("#tagPickerGroups"), picker.locator("#tagPickerList"))
            assert panes[1].evaluate("element => element.parentElement.classList.contains('tag-picker-body')"), (
                "Group navigation must remain inside the picker body")
            for width, height in ((1440, 960), (1440, 680), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                for pane in panes[1:]:
                    pane.evaluate("element => { element.scrollTop = element.scrollHeight; element.scrollLeft = element.scrollWidth; }")
                page.wait_for_function("""() => {
                    const dialog = document.querySelector('#tagPickerDialog');
                    return dialog && dialog.open && dialog.getBoundingClientRect().height > 0;
                }""")
                dialog_bounds = picker.bounding_box()
                footer_bounds = picker.locator(".tag-picker-foot").bounding_box()
                body_bounds, group_bounds, list_bounds = [pane.bounding_box() for pane in panes]
                assert all((dialog_bounds, footer_bounds, body_bounds, group_bounds, list_bounds))
                assert dialog_bounds["y"] >= -1 and dialog_bounds["y"] + dialog_bounds["height"] <= height + 1, (
                    "The tag window must fit within the visible screen", width, height, dialog_bounds)
                for pane_bounds in (body_bounds, group_bounds, list_bounds):
                    assert pane_bounds["y"] + pane_bounds["height"] <= footer_bounds["y"] + 1, (
                        "Scrolled tag content must not paint over the footer", width, height,
                        pane_bounds, footer_bounds)
                assert group_bounds["y"] >= body_bounds["y"] - 1, (body_bounds, group_bounds)
                assert group_bounds["y"] + group_bounds["height"] <= body_bounds["y"] + body_bounds["height"] + 1, (
                    "The groups pane must be constrained by the picker body", body_bounds, group_bounds)
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
                    "The tag picker must not add horizontal page overflow", width, height)
                # Layout boxes alone cannot detect text drawn outside a scroll
                # pane. Hit-testing the actual footer catches a pane overlay.
                assert picker.locator("#tagPickerStatus").evaluate("""element => {
                    const bounds = element.getBoundingClientRect();
                    const hit = document.elementFromPoint(bounds.x + bounds.width / 2,
                                                          bounds.y + bounds.height / 2);
                    return Boolean(hit && element.closest('footer').contains(hit));
                }"""), "The footer must remain on top and usable after scrolling"
                page.screenshot(path=str(OUTPUT / f"tag-picker-scroll-{width}x{height}.png"), full_page=False)
            page.set_viewport_size({"width": 1440, "height": 960})

        # Tag management shows the formal directory and its use counts.
        page.locator("#libraryTagManager").click()
        manager = page.locator("#tagManagerWorkspace")
        expect(manager).to_be_visible()
        expect(page.locator("#tagManagerDialog")).to_have_count(0)
        expect(manager).to_contain_text(TAG_NAME)
        expect(manager).to_contain_text("高中")
        page.screenshot(path=str(OUTPUT / "tag-manager.png"), full_page=False)
        manager.locator("#tagManagerBack").click()
        second.locator(".paper").click()
        if not inspector.is_visible():
            second.locator(".library-card-details").click()

        # AI results appear in the selected question's inspector, separate from its paper card.
        expect(proposal).to_contain_text("AI 标签建议")
        expect(proposal).to_contain_text("待核对")
        candidates = proposal.locator("input[data-tag-suggestion]")
        expect(candidates).to_have_count(2)
        expect(candidates.nth(0)).not_to_be_checked()
        expect(candidates.nth(1)).not_to_be_checked()
        expect(inspector.locator(".library-tags")).not_to_contain_text("构造中位线")
        expect(inspector.locator(".library-tags")).not_to_contain_text(NEW_TAG_NAME)
        accept = proposal.get_by_role("button", name="采纳所选建议", exact=True)
        reject = proposal.get_by_role("button", name="拒绝所选建议", exact=True)
        expect(accept).to_be_disabled()
        expect(reject).to_be_disabled()
        candidates.nth(1).check()
        expect(accept).to_be_enabled()
        expect(reject).to_be_enabled()
        candidates.nth(1).uncheck()
        expect(accept).to_be_disabled()
        expect(reject).to_be_disabled()

        # The suggestion belongs in the question inspector. New-tag fields stay
        # collapsed until explicitly requested.
        edit = proposal.locator("details.tag-suggestion-edit")
        expect(edit).to_have_count(1)
        expect(edit).not_to_have_attribute("open", "")
        name = edit.locator("input[data-new-tag-name]")
        expect(name).not_to_be_visible()
        expect(edit.locator("input[data-new-tag-group]")).to_have_count(0)
        proposal_bounds = proposal.bounding_box()
        inspector_bounds = inspector.bounding_box()
        assert inspector_bounds and proposal_bounds
        assert proposal_bounds["x"] >= inspector_bounds["x"] - 1 and proposal_bounds["x"] + proposal_bounds["width"] <= inspector_bounds["x"] + inspector_bounds["width"] + 1, (
            "The suggestion must stay inside the selected question's inspector", inspector_bounds, proposal_bounds)
        page.screenshot(path=str(OUTPUT / "tag-suggestion-pending.png"), full_page=False)
        edit.get_by_text("修改名称或分组", exact=True).click()
        expect(name).to_be_visible()
        expect(name).to_have_value(NEW_TAG_NAME)
        name.fill(NEW_TAG_NAME + "已编辑")
        expect(name).to_have_value(NEW_TAG_NAME + "已编辑")
        expect(edit.locator("select[data-new-tag-kind]")).to_have_value("knowledge")
        groups = edit.locator("input[data-new-tag-group]")
        expect(groups.first).to_be_visible()
        assert groups.count() > 0, "Opening the editor must lazily populate group choices"
        groups.first.check()
        expect(groups.first).to_be_checked()
        groups.first.uncheck()
        page.screenshot(path=str(OUTPUT / "tag-suggestion-edit.png"), full_page=False)

        # Reading on a narrow screen only selects the question. The explicit
        # details entry opens the same editor inside the viewport.
        page.set_viewport_size({"width": 390, "height": 844})
        second.locator(".paper").click()
        expect(page.locator("#inspectorToggle")).to_have_attribute("aria-expanded", "false")
        second.locator(".library-card-details").click()
        expect(inspector).to_be_visible()
        expect(proposal).to_be_visible()
        second.scroll_into_view_if_needed()
        card_bounds = second.bounding_box()
        assert card_bounds
        assert card_bounds["width"] >= 350, ("Narrow card must use the available page width", card_bounds)
        assert 0 <= card_bounds["x"] < 25, (
            "Narrow card must start near the page edge rather than an unused grid column", card_bounds)
        proposal_bounds = proposal.bounding_box()
        inspector_bounds = inspector.bounding_box()
        assert inspector_bounds and proposal_bounds
        assert proposal_bounds["x"] >= inspector_bounds["x"] - 1 and proposal_bounds["x"] + proposal_bounds["width"] <= inspector_bounds["x"] + inspector_bounds["width"] + 1
        assert page.locator("#inspectorToggle").get_attribute("aria-expanded") == "true"
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), (
            "Narrow tag review must not introduce horizontal page overflow")
        page.screenshot(path=str(OUTPUT / "tag-suggestion-mobile.png"), full_page=False)
        page.set_viewport_size({"width": 1440, "height": 960})

        # Accept only the synthetic knowledge proposal. Its edited name and
        # checkbox group selection must reach the backend and formal tag row.
        groups.first.check()
        selected_group_id = groups.first.input_value()
        candidates.nth(1).check()
        with page.expect_response(lambda response: response.url.endswith("/resolve")
                                  and response.request.method == "POST") as acceptance:
            accept.click()
        response = acceptance.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["action"] == "accept" and submitted["indices"] == [1], submitted
        assert submitted["new_tags"]["1"] == {
            "name": NEW_TAG_NAME + "已编辑", "kind": "knowledge", "group_ids": [selected_group_id]}, submitted
        saved_tag_name = unicodedata.normalize("NFKC", NEW_TAG_NAME + "已编辑")
        expect(inspector.locator(".library-tags")).to_contain_text(saved_tag_name)
        expect(inspector.locator(".library-tags")).not_to_contain_text("构造中位线")
        # A decided candidate exits the review panel immediately; the other
        # candidate keeps its original index and remains independently reviewable.
        expect(proposal.locator(".tag-suggestion-status")).to_have_text("待核对")
        expect(proposal).not_to_contain_text(saved_tag_name)
        remaining = proposal.locator("input[data-tag-suggestion]")
        expect(remaining).to_have_count(1)
        expect(remaining.first).to_have_value("0")
        expect(proposal.locator("details.tag-suggestion-edit")).to_have_count(0)

        # The method has no adopted analysis in this fixture. Reject it rather
        # than accepting fake method evidence; the accepted knowledge stays.
        candidates.nth(0).check()
        with page.expect_response(lambda response: response.url.endswith("/resolve")
                                  and response.request.method == "POST") as rejection:
            reject.click()
        response = rejection.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["action"] == "reject" and submitted["indices"] == [0], submitted
        expect(second.locator(".library-card-body")).not_to_have_class("has-tag-review")
        expect(inspector.locator("aside.tag-suggestion-card")).to_have_count(0)
        expect(inspector.locator(".tag-suggestion-compact")).to_have_count(0)
        expect(inspector.locator(".library-tags")).to_contain_text(saved_tag_name)
        expect(inspector.locator(".library-tags")).not_to_contain_text("构造中位线")
        history_link = inspector.get_by_role("button", name="AI 标签记录", exact=True)
        expect(history_link).to_be_visible()
        history_link.click()
        history_dialog = page.locator(".tag-history-dialog")
        expect(history_dialog).to_be_visible()
        expect(history_dialog).to_contain_text("已采纳")
        expect(history_dialog).to_contain_text("已拒绝")
        page.screenshot(path=str(OUTPUT / "tag-suggestion-reviewed.png"), full_page=False)
        history_dialog.get_by_role("button", name="关闭", exact=True).click()

        # Single-card editing keeps the same immediate save and undo contract.
        first.locator(".paper").click()
        inspector.locator(".library-tags-edit").click()
        picker = page.locator("#tagPickerDialog")
        expect(picker).to_be_visible()
        check_picker_layout()
        page.locator("#tagPickerSearch").fill(TAG_NAME)
        row = page.locator(".tag-picker-row").filter(has_text=TAG_NAME).first
        expect(row.locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        row.click()
        expect(row.locator(".tag-picker-row-state")).to_have_text("添加")
        expect(page.locator("#tagPickerStatus")).to_contain_text("移除")
        page.locator("#tagPickerUndo").click()
        expect(row.locator(".tag-picker-row-state")).to_have_text("✓ 已添加")

        # The contextual picker creates tags. Directory organization belongs
        # to the standalone manager, using the same editor form.
        expect(picker.locator("#tagPickerCreateTag")).to_be_visible()
        expect(picker.locator("#tagPickerCreateGroup")).to_have_count(0)
        expect(picker.locator("#tagPickerManage")).to_have_count(0)
        picker.get_by_role("button", name="关闭", exact=True).click()
        page.locator("#libraryTagManager").click()
        expect(manager).to_be_visible()
        manager.locator("#tagManagerCreateGroup").click()
        editor = page.locator("#tagCatalogueEditorDialog")
        expect(editor).to_be_visible()
        name_field = editor.locator("#tagCatalogueName")
        name_field.press_sequentially(CUSTOM_GROUP_NAME, delay=3)
        expect(name_field).to_have_value(CUSTOM_GROUP_NAME)
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                                  and response.request.method == "POST") as group_creation:
            editor.locator("#tagCatalogueSave").click()
        response = group_creation.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["action"] == "create_group" and submitted["name"] == CUSTOM_GROUP_NAME, submitted
        saved_group_name = unicodedata.normalize("NFKC", CUSTOM_GROUP_NAME)
        group = next(group for group in response.json()["catalogue"]["groups"]
                     if group["name"] == saved_group_name)
        expect(editor).not_to_be_visible()
        expect(manager).to_be_visible()
        manager.locator('#tagManagerGroups button[data-group-id="' + group["id"] + '"]').click()
        expect(manager.locator("#tagManagerGroupActions")).to_contain_text(saved_group_name)
        manager.locator("#tagManagerBack").click()
        first.locator(".paper").click()
        inspector.locator(".library-tags-edit").click()
        expect(picker).to_be_visible()
        picker.locator("#tagPickerGroups").get_by_role("button", name=saved_group_name, exact=True).click()
        expect(picker.locator("#tagPickerGroups .tag-group-button.active")).to_have_text(saved_group_name)

        picker.locator("#tagPickerSearch").fill("")
        picker.locator("#tagPickerCreateTag").click()
        expect(editor).to_be_visible()
        expect(editor.locator("#tagCatalogueKind")).to_have_value("custom")
        selected_group = editor.locator(f'#tagCatalogueGroups input[value="{group["id"]}"]')
        expect(selected_group).to_be_checked()
        name_field.press_sequentially(CUSTOM_TAG_NAME, delay=3)
        expect(name_field).to_have_value(CUSTOM_TAG_NAME)
        editor.locator("#tagCatalogueDescription").fill(CUSTOM_TAG_DESCRIPTION)
        color_field = editor.locator("#tagCatalogueColor")
        selected_color = color_field.locator("option").nth(1).get_attribute("value")
        assert selected_color, "The tag form should offer an optional visible color"
        color_field.select_option(selected_color)
        page.screenshot(path=str(OUTPUT / "tag-create-form.png"), full_page=False)

        # One deliberately failed local save must leave all user input intact.
        injected_error = "本机验收注入：目录保存暂时失败，草稿应保留。"
        failures = []
        catalogue_url = base.rstrip("/") + "/api/library/tags"

        def fail_first_tag_creation(route):
            request = route.request
            if request.method == "POST" and request.post_data_json.get("action") == "create_tag" and not failures:
                failures.append(request.post_data_json)
                route.fulfill(status=503, content_type="application/json",
                              body=json.dumps({"error": injected_error}, ensure_ascii=False))
            else:
                route.continue_()

        page.route(catalogue_url, fail_first_tag_creation)
        with page.expect_response(lambda response: response.url == catalogue_url
                                  and response.request.method == "POST") as failed_creation:
            editor.locator("#tagCatalogueCreateOnly").click()
        assert failed_creation.value.status == 503 and len(failures) == 1
        expect(editor.locator("#tagCatalogueStatus")).to_contain_text(injected_error)
        expect(editor).to_be_visible()
        expect(name_field).to_have_value(CUSTOM_TAG_NAME)
        expect(editor.locator("#tagCatalogueKind")).to_have_value("custom")
        expect(editor.locator("#tagCatalogueDescription")).to_have_value(CUSTOM_TAG_DESCRIPTION)
        expect(color_field).to_have_value(selected_color)
        expect(selected_group).to_be_checked()
        expect(editor.locator("#tagCatalogueSave")).to_be_enabled()
        page.screenshot(path=str(OUTPUT / "tag-create-draft-retained.png"), full_page=False)
        page.unroute(catalogue_url, fail_first_tag_creation)

        with page.expect_response(lambda response: response.url == catalogue_url
                                  and response.request.method == "POST") as tag_creation, \
             page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags/assignments/batch"
                                  and response.request.method == "POST") as assignment_creation:
            editor.locator("#tagCatalogueSave").click()
        response = tag_creation.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["action"] == "create_tag" and submitted["name"] == CUSTOM_TAG_NAME, submitted
        assert submitted["kind"] == "custom" and submitted["group_ids"] == [group["id"]], submitted
        assert submitted["description"] == CUSTOM_TAG_DESCRIPTION and submitted["color"] == selected_color, submitted
        saved_custom_name = unicodedata.normalize("NFKC", CUSTOM_TAG_NAME)
        saved_tag = next(tag for tag in response.json()["catalogue"]["tags"]
                         if tag["name"] == saved_custom_name)
        assignment = assignment_creation.value
        assert assignment.ok, assignment.text()
        submitted = assignment.request.post_data_json
        assert submitted["publication_ids"] == [fixture["ids"][0]], submitted
        assert submitted["add"] == [saved_tag["id"]] and submitted["remove"] == [], submitted
        expect(editor).not_to_be_visible()
        expect(picker).to_be_visible()
        new_row = picker.locator(".tag-picker-row").filter(has_text=saved_custom_name)
        expect(new_row).to_have_count(1)
        expect(new_row.locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        expect(inspector.locator(".library-tags")).to_contain_text(saved_custom_name)
        page.screenshot(path=str(OUTPUT / "tag-create-and-add.png"), full_page=False)

        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags/undo"
                                  and response.request.method == "POST") as creation_undo:
            picker.locator("#tagPickerUndo").click()
        assert creation_undo.value.ok, creation_undo.value.text()
        expect(new_row.locator(".tag-picker-row-state")).to_have_text("添加")
        expect(inspector.locator(".library-tags")).not_to_contain_text(saved_custom_name)
        expect(inspector.locator(".library-tags")).to_contain_text(TAG_NAME)
        expect(picker.locator("#tagPickerUndo")).to_be_hidden()
        page.screenshot(path=str(OUTPUT / "tag-create-add-undone.png"), full_page=False)

        # A server can commit successfully while its reply is lost. Retry
        # must replay the same assignment rather than creating another tag or
        # advancing the question revision twice.
        def read_api(path):
            result = page.request.get(base.rstrip("/") + path, headers={"X-QB-Request": "1"})
            assert result.ok, result.text()
            return result.json()

        before_operations = {operation["id"] for operation in read_api("/api/library/tags/operations")["operations"]}
        picker.locator("#tagPickerGroups").get_by_role("button", name=saved_group_name, exact=True).click()
        picker.locator("#tagPickerSearch").fill("")
        picker.locator("#tagPickerCreateTag").click()
        expect(editor).to_be_visible()
        name_field.press_sequentially(RETRY_TAG_NAME, delay=3)
        expect(name_field).to_have_value(RETRY_TAG_NAME)
        assignment_url = base.rstrip("/") + "/api/library/tags/assignments/batch"
        assignment_requests, committed_assignments = [], []
        lost_reply_error = "本机验收注入：服务器已保存，返回结果暂时丢失。"

        def commit_then_lose_reply(route):
            if route.request.method != "POST":
                route.continue_()
                return
            assignment_requests.append(route.request.post_data_json)
            if not committed_assignments:
                # This request really reaches the isolated server and commits.
                # Only the response visible to the page is replaced.
                committed = route.fetch()
                assert committed.ok, committed.text()
                committed_assignments.append(committed.json())
                route.fulfill(status=503, content_type="application/json",
                              body=json.dumps({"error": lost_reply_error}, ensure_ascii=False))
            else:
                route.continue_()

        page.route(assignment_url, commit_then_lose_reply)
        with page.expect_response(lambda response: response.url == catalogue_url
                                  and response.request.method == "POST") as retry_tag_creation, \
             page.expect_response(lambda response: response.url == assignment_url
                                  and response.request.method == "POST") as lost_assignment:
            editor.locator("#tagCatalogueSave").click()
        assert retry_tag_creation.value.ok, retry_tag_creation.value.text()
        assert lost_assignment.value.status == 503
        saved_retry_name = unicodedata.normalize("NFKC", RETRY_TAG_NAME)
        retry_tag = next(tag for tag in retry_tag_creation.value.json()["catalogue"]["tags"]
                         if tag["name"] == saved_retry_name)
        assert len(assignment_requests) == 1 and len(committed_assignments) == 1
        committed_assignment = committed_assignments[0]
        persisted = read_api("/api/library/tags/assignments/batch?ids=" + fixture["ids"][0])["items"][fixture["ids"][0]]
        assert persisted["revision"] == committed_assignment["revisions"][fixture["ids"][0]], persisted
        assert sum(assignment["tag"]["id"] == retry_tag["id"] for assignment in persisted["assignments"]) == 1, persisted
        expect(editor).to_be_visible()
        expect(editor.locator("#tagCatalogueStatus")).to_contain_text(lost_reply_error)
        expect(name_field).to_have_value(RETRY_TAG_NAME)
        expect(editor.locator("#tagCatalogueSave")).to_have_text("重试添加")
        expect(editor.locator("#tagCatalogueSave")).to_be_enabled()
        page.screenshot(path=str(OUTPUT / "tag-created-reply-lost.png"), full_page=False)
        with page.expect_response(lambda response: response.url == assignment_url
                                  and response.request.method == "POST") as assignment_retry:
            editor.locator("#tagCatalogueSave").click()
        response = assignment_retry.value
        assert response.ok, response.text()
        assert len(assignment_requests) == 2 and assignment_requests[1] == assignment_requests[0], assignment_requests
        replay = response.json()
        assert replay.get("replayed") is True, replay
        assert replay["operation_id"] == committed_assignment["operation_id"], (replay, committed_assignment)
        assert replay["revisions"] == committed_assignment["revisions"], (replay, committed_assignment)
        page.unroute(assignment_url, commit_then_lose_reply)
        expect(editor).not_to_be_visible()
        expect(picker.locator(".tag-picker-row").filter(has_text=saved_retry_name)
               .locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        expect(inspector.locator(".library-tags")).to_contain_text(saved_retry_name)
        after_operations = read_api("/api/library/tags/operations")["operations"]
        added_operations = [operation for operation in after_operations if operation["id"] not in before_operations]
        assert sorted(operation["action"] for operation in added_operations) == ["assign", "catalogue:create_tag"], added_operations
        assert [operation["id"] for operation in added_operations if operation["action"] == "assign"] == [committed_assignment["operation_id"]], added_operations
        assert sum(tag["name"] == saved_retry_name for tag in read_api("/api/library/tags")["tags"]) == 1
        page.screenshot(path=str(OUTPUT / "tag-created-reply-retried.png"), full_page=False)

        # Editing an already assigned tag changes this question's revision.
        # Closing management must refresh the open picker so its next save
        # uses that revision and the revised directory without reopening it.
        picker.get_by_role("button", name="关闭", exact=True).click()
        page.locator("#libraryTagManager").click()
        expect(manager).to_be_visible()
        manager.locator('#tagManagerGroups button[data-group-id="all"]').click()
        managed_tag = manager.locator(".tag-directory-row").filter(
            has=page.get_by_text(saved_retry_name, exact=True))
        managed_tag.get_by_role("button", name=saved_retry_name, exact=True).click()
        manager.locator("#tagManagerEditTag").click()
        expect(editor).to_be_visible()
        expect(editor.locator("#tagCatalogueEditorForm")).to_have_count(1)
        expect(name_field).to_have_value(saved_retry_name)
        expect(editor.locator("#tagCatalogueKind")).to_have_value("custom")
        expect(editor.locator("#tagCatalogueKind")).to_be_disabled()
        name_field.fill(EDITED_TAG_NAME)
        editor.get_by_text("别名、学段与适用条件", exact=True).click()
        aliases_field = editor.get_by_label("别名（用逗号分隔）", exact=True)
        aliases_field.fill("，".join(EDITED_ALIASES))
        with page.expect_response(lambda response: response.url == catalogue_url
                                  and response.request.method == "POST") as tag_edit:
            editor.locator("#tagCatalogueSave").click()
        response = tag_edit.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["action"] == "update_tag" and submitted["tag_id"] == retry_tag["id"], submitted
        assert submitted["name"] == EDITED_TAG_NAME, submitted
        assert submitted["aliases"] == [*EDITED_ALIASES, saved_retry_name], submitted
        expect(editor).not_to_be_visible()
        edited_tag_name = unicodedata.normalize("NFKC", EDITED_TAG_NAME)
        expect(manager.locator(".tag-directory-row").filter(
            has=page.get_by_text(edited_tag_name, exact=True))).to_have_count(1)

        manager.locator('#tagManagerGroups button[data-group-id="' + group["id"] + '"]').click()
        manager.locator("#tagManagerGroupActions").get_by_role("button", name="编辑分组", exact=True).click()
        expect(editor).to_be_visible()
        expect(name_field).to_have_value(saved_group_name)
        name_field.fill(EDITED_GROUP_NAME)
        with page.expect_response(lambda response: response.url == catalogue_url
                                  and response.request.method == "POST") as group_edit:
            editor.locator("#tagCatalogueSave").click()
        response = group_edit.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["action"] == "update_group" and submitted["group_id"] == group["id"], submitted
        assert submitted["name"] == EDITED_GROUP_NAME, submitted
        expect(editor).not_to_be_visible()
        edited_group_name = unicodedata.normalize("NFKC", EDITED_GROUP_NAME)
        expect(manager.locator("#tagManagerGroupActions")).to_contain_text(edited_group_name)
        page.screenshot(path=str(OUTPUT / "tag-manager-edited-directory.png"), full_page=False)
        manager.locator("#tagManagerBack").click()
        first.locator(".paper").click()
        if not inspector.is_visible():
            first.locator(".library-card-details").click()
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags/assignments/batch"
                                  and response.request.method == "GET") as picker_refresh:
            inspector.locator(".library-tags-edit").click()
        response = picker_refresh.value
        assert response.ok, response.text()
        refreshed_revision = response.json()["items"][fixture["ids"][0]]["revision"]
        assert refreshed_revision == committed_assignment["revisions"][fixture["ids"][0]] + 1
        expect(picker).to_be_visible()
        expect(picker.locator("#tagPickerGroups").get_by_role("button", name=edited_group_name, exact=True)).to_be_visible()
        picker.locator("#tagPickerSearch").fill("")
        picker.locator("#tagPickerGroups").get_by_role("button", name=edited_group_name, exact=True).click()
        expect(picker.locator(".tag-picker-row").filter(has_text=edited_tag_name)
               .locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        new_row = picker.locator(".tag-picker-row").filter(has_text=saved_custom_name)
        expect(new_row.locator(".tag-picker-row-state")).to_have_text("添加")
        with page.expect_response(lambda response: response.url == assignment_url
                                  and response.request.method == "POST") as refreshed_save:
            new_row.click()
        response = refreshed_save.value
        assert response.ok, response.text()
        submitted = response.request.post_data_json
        assert submitted["expected_revisions"] == {fixture["ids"][0]: refreshed_revision}, submitted
        assert submitted["add"] == [saved_tag["id"]], submitted
        expect(new_row.locator(".tag-picker-row-state")).to_have_text("✓ 已添加")
        picker.locator("#tagPickerUndo").click()
        expect(new_row.locator(".tag-picker-row-state")).to_have_text("添加")
        renamed_row = picker.locator(".tag-picker-row").filter(has_text=edited_tag_name)
        with page.expect_response(lambda response: response.url == assignment_url
                                  and response.request.method == "POST") as retry_cleanup:
            renamed_row.click()
        assert retry_cleanup.value.ok, retry_cleanup.value.text()
        expect(renamed_row.locator(".tag-picker-row-state")).to_have_text("添加")
        expect(inspector.locator(".library-tags")).not_to_contain_text(edited_tag_name)
        expect(inspector.locator(".library-tags")).to_contain_text(TAG_NAME)
        page.screenshot(path=str(OUTPUT / "tag-picker-after-manager-edit.png"), full_page=False)
        picker.get_by_role("button", name="关闭", exact=True).click()

        # Select both cards: the same label is visibly partial, applies in one
        # atomic batch, and the just-saved action can be undone.
        page.locator("#selectVisible").check()
        page.locator("#editSelectedTags").click()
        picker = page.locator("#tagPickerDialog")
        expect(picker).to_be_visible()
        page.locator("#tagPickerSearch").fill(TAG_NAME)
        row = page.locator(".tag-picker-row").filter(has_text=TAG_NAME).first
        expect(row.locator(".tag-picker-row-state")).to_contain_text("部分有 1/2")
        row.click()
        expect(row.locator(".tag-picker-row-state")).to_have_text("全部都有")
        expect(page.locator("#tagPickerStatus")).to_contain_text("已添加")
        page.locator("#tagPickerUndo").click()
        expect(row.locator(".tag-picker-row-state")).to_contain_text("部分有 1/2")
        expect(page.locator("#tagPickerUndo")).to_be_hidden()
        page.screenshot(path=str(OUTPUT / "tag-picker-bulk-partial.png"), full_page=False)
        picker.get_by_role("button", name="关闭", exact=True).click()

        # System templates are an optional blueprint: preview has no checked
        # groups and does not modify the catalogue until explicit import.
        filters = page.locator("#advancedFilters")
        if not filters.evaluate("element => element.open"):
            filters.locator("summary").click()
        page.locator("#libraryTagManager").click()
        expect(manager).to_be_visible()
        manager.locator("#tagManagerTemplates").click()
        template_dialog = page.locator(".tag-template-dialog")
        page.wait_for_timeout(500)
        if template_dialog.count() == 0:
            toast_text = page.locator("#toast").inner_text() if page.locator("#toast").count() else ""
            raise AssertionError(f"Template preview was not created; toast={toast_text!r}; pageerrors={errors!r}")
        expect(template_dialog).to_be_visible()
        template_checks = template_dialog.locator(".tag-template-group > summary input[type=checkbox]")
        assert template_checks.count() > 0
        expect(template_dialog.locator("input[type=checkbox]:checked")).to_have_count(0)
        page.screenshot(path=str(OUTPUT / "tag-system-template-preview.png"), full_page=False)
        template_checks.first.check()
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                                  and response.request.method == "POST") as template_import:
            template_dialog.get_by_role("button", name="导入所选分组", exact=True).click()
        response = template_import.value
        assert response.ok, response.text()
        assert response.request.post_data_json["action"] == "apply_template"
        expect(template_dialog).to_have_count(0)
        expect(manager).to_be_visible()

        # Clearing the directory is an explicit advanced, reversible operation. It removes
        # assignments and groups, then the operation log restores the snapshot.
        page.once("dialog", lambda dialog: dialog.accept())
        manager.locator("details.tag-directory-advanced > summary").click()
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags"
                                  and response.request.method == "POST") as reset:
            manager.locator("#tagManagerReset").click()
        response = reset.value
        assert response.ok, response.text()
        assert response.request.post_data_json["action"] == "reset_empty"
        expect(manager.locator(".tag-directory-row")).to_have_count(0)
        history_section = manager.locator("details.tag-directory-history")
        if not history_section.get_attribute("open"):
            history_section.locator("summary").click()
        reset_row = manager.locator(".tag-directory-history-row").filter(has_text="清空标签目录")
        expect(reset_row).to_have_count(1)
        with page.expect_response(lambda response: urlparse(response.url).path == "/api/library/tags/undo"
                                  and response.request.method == "POST") as reset_undo:
            reset_row.get_by_role("button", name="撤销", exact=True).click()
        assert reset_undo.value.ok, reset_undo.value.text()
        expect(manager.locator(".tag-directory-row")).not_to_have_count(0)
        page.screenshot(path=str(OUTPUT / "tag-directory-reset-undo.png"), full_page=False)
        manager.locator("#tagManagerBack").click()

        assert not external, external
        assert not errors, errors
        browser.close()
    print("Formal tag browser checks passed: directory, responsive pending-only AI review, "
          "new-tag group acceptance, partial review and rejection, scroll/footer containment, "
          "picker directory creation, Chinese draft retention after failed save, "
          "create-and-add undo, committed assignment reply-loss replay, "
          "shared-form directory edits and refreshed picker revisions, single/bulk save and undo")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8891")
    parser.add_argument("--output", type=Path, default=OUTPUT,
                        help="Fixture and screenshot directory, restricted to this checkout's tmp folder")
    options = parser.parse_args()
    OUTPUT = options.output.resolve()
    if not OUTPUT.is_relative_to(ROOT / "tmp"):
        raise SystemExit("--output must point inside this checkout's tmp directory")
    seed() if options.seed else check(options.url)
