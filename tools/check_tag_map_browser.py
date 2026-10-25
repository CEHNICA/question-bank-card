"""Real, isolated browser acceptance for the tag-map and its question handoff.

Every fixture is fictional. Storage is confined to checkout/tmp, no AI worker
starts, and all external browser requests are rejected. Use --run with the
packaging build Python. An optional --bundle tests the frozen desktop artifact.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import check_tag_redesign_browser as harness

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "tag-map-browser"
BUNDLE = None


def isolated_environment():
    harness.OUTPUT = OUTPUT
    harness.regression.OUTPUT = OUTPUT
    harness.isolated_environment()


def seed():
    isolated_environment()
    if Path(os.environ["QB_DATABASE"]).exists():
        raise SystemExit("Use a fresh --output directory; fixture data will not be replaced")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django

    django.setup()
    from django.core.management import call_command

    from core import features, library, library_tagging
    from core.models import (
        LibraryTag,
        LibraryTagAssignment,
        LibraryTagCatalogue,
        LibraryTagRelation,
        Paper,
        PublishedQuestion,
        Question,
    )

    call_command("migrate", verbosity=0)
    features.save({"knowledge_tags": True, "ai_answer": False})
    LibraryTagCatalogue.objects.update_or_create(pk=1, defaults={"initialized": True, "revision": 1})
    tags = {}
    definitions = (
        ("quadrilaterals", "四边形", "knowledge", "有四条边的平面多边形。"),
        ("parallelogram", "平行四边形", "knowledge", "两组对边分别平行的四边形。"),
        ("rectangle", "矩形", "knowledge", "有一个角是直角的平行四边形。"),
        ("rhombus", "菱形", "knowledge", "一组邻边相等的平行四边形。"),
        ("square", "正方形", "knowledge", "同时具有矩形与菱形的性质。"),
        ("diagonal", "正方形的对角线性质", "knowledge", "对角线相等、垂直且互相平分。"),
        ("triangle", "三角形", "knowledge", "三个顶点与三条边组成的图形。"),
        ("midline", "三角形中位线", "knowledge", "连接两边中点的线段。"),
        ("functions", "函数", "knowledge", "变量之间的对应关系。"),
        ("quadratic", "二次函数", "knowledge", "二次项系数非零的多项式函数。"),
        ("minimum", "二次函数的最值", "knowledge", "研究顶点与取值范围。"),
        ("proofs", "代数方法", "method", "使用代数变形研究数学问题。"),
        ("completing", "配方法", "method", "适用于将二次式改写为平方与常数。"),
        ("geometric", "几何方法", "method", "借助图形关系证明。"),
        ("construction", "构造中位线", "method", "连接边的中点，并使用中位线性质。"),
        ("custom", "课堂例题", "custom", "用户个人教学用途，不参与知识层级。"),
    )
    for key, name, kind, description in definitions:
        tags[key] = LibraryTag.objects.create(
            name=name, kind=kind, description=description, aliases=["正四边形"] if key == "square" else []
        )
    for parent, child in (
        ("quadrilaterals", "parallelogram"),
        ("parallelogram", "rectangle"),
        ("parallelogram", "rhombus"),
        ("rectangle", "square"),
        ("rhombus", "square"),
        ("square", "diagonal"),
        ("triangle", "midline"),
        ("functions", "quadratic"),
        ("quadratic", "minimum"),
        ("proofs", "completing"),
        ("geometric", "construction"),
    ):
        LibraryTagRelation.objects.create(parent=tags[parent], child=tags[child])
    for parent, child in (("quadratic", "completing"), ("midline", "construction")):
        LibraryTagRelation.objects.create(parent=tags[parent], child=tags[child], relation_type="related_method")
    paper = Paper.objects.create(
        filename="标签地图隔离虚构试卷.pdf", kind="pdf", sha256="tag-map-offline-synthetic", pages=1, status="ready"
    )
    publication_ids = []
    assignments = [
        ["quadrilaterals"],
        ["rectangle"],
        ["rhombus"],
        ["square"],
        ["rectangle", "square"],
        ["triangle"],
        ["quadratic"],
        ["minimum"],
        ["square"],
        ["diagonal"],
        ["custom"],
        ["midline"],
        ["minimum", "completing"],
        ["square"],
    ]
    for number, tag_keys in enumerate(assignments, 1):
        stem = (
            f"虚构第 {number} 题：请研究题目中给出的几何或函数条件。"
            "此内容只用于标签地图、找题、阅读位置及保存验收。已知四边形的边长与角度，"
            "分别判断对应的性质，并说明依据；同一个知识标签有多个父级时仍然只有一个身份。"
        )
        question = Question.objects.create(paper=paper, number=number, question_type="free_response", stem=stem)
        content = {
            "stem": stem,
            "options": {},
            "answer": "",
            "analysis": "",
            "figures": [],
            "sources": [],
            "origin": "离线合成验收",
            "source_filename": paper.filename,
            "document_id": str(paper.pk),
            "question_type": "free_response",
        }
        publication = PublishedQuestion.objects.create(
            question=question,
            paper=paper,
            source_filename=paper.filename,
            number=number,
            question_type="free_response",
            version=1,
            status="published",
            review_source="human",
            content=content,
            content_hash=library.content_hash(content),
            search_text=library.search_key(stem),
            extras={},
            tag_revision=1,
        )
        for key in tag_keys:
            LibraryTagAssignment.objects.create(publication=publication, tag=tags[key], source="human")
        library_tagging._sync_projection(publication.pk)
        publication_ids.append(str(publication.pk))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "fixture.json").write_text(
        json.dumps(
            {"ids": publication_ids, "tags": {key: str(tag.pk) for key, tag in tags.items()}}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    print("Synthetic tag-map fixture ready", flush=True)


def check(base):
    from playwright.sync_api import expect, sync_playwright

    harness.regression._assert_isolated()
    if urlparse(base).hostname not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Only a local isolated server is accepted")
    fixture = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    tags, ids = fixture["tags"], fixture["ids"]
    version = next(
        node.value
        for node in ast.parse((ROOT / "backend/core/version.py").read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets)
    )
    report = {
        "checks": [],
        "errors": [],
        "external_requests": [],
        "screenshots": [],
        "tested_artifact": str(BUNDLE) if BUNDLE else "current source",
        "real_backend": True,
        "version": ast.literal_eval(version),
    }
    if BUNDLE:
        report["frozen_executable_sha256"] = hashlib.sha256(BUNDLE.read_bytes()).hexdigest()
    with sync_playwright() as playwright:
        executable = next(
            (
                str(path)
                for path in (
                    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
                    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
                    Path(playwright.chromium.executable_path),
                )
                if path.is_file()
            ),
            None,
        )
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 960})

        def local_only(route):
            if urlparse(route.request.url).hostname in {"127.0.0.1", "localhost"}:
                route.continue_()
            else:
                report["external_requests"].append(route.request.url)
                route.abort()

        context.route("**/*", local_only)
        page = context.new_page()
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        requests = []
        page.on("request", lambda request: requests.append((request.method, urlparse(request.url).path)))

        def read(path):
            response = page.request.get(base + path, headers={"X-QB-Request": "1"})
            assert response.ok, response.text()
            return response.json()

        def post(path, payload):
            return page.request.post(base + path, headers={"X-QB-Request": "1"}, data=payload)

        def capture(name):
            output = OUTPUT / name
            page.screenshot(path=str(output), full_page=False)
            report["screenshots"].append(str(output))

        def node(key):
            return page.locator(f'#tagManagerMap .tag-map-node[data-tag-id="{tags[key]}"]')

        def choose(key):
            target = node(key).locator(".tag-map-node-select")
            target.click()
            expect(page.locator("#tagManagerDetail")).to_contain_text(key_to_name[key])

        def expand(key):
            node(key).locator(".tag-map-expand").click()

        key_to_name = {
            "quadrilaterals": "四边形",
            "parallelogram": "平行四边形",
            "rectangle": "矩形",
            "rhombus": "菱形",
            "square": "正方形",
            "diagonal": "正方形的对角线性质",
            "triangle": "三角形",
            "midline": "三角形中位线",
            "quadratic": "二次函数",
            "completing": "配方法",
            "proofs": "代数方法",
        }
        page.goto(base + "/library")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".library-card")).to_have_count(14)
        initial_catalogue = read("/api/library/tags")
        writes_before = len([request for request in requests if request[0] == "POST"])
        page.locator("#libraryTagManager").click()
        expect(page.locator("#tagManagerWorkspace")).to_be_visible()
        page.locator("#tagManagerMapView").click()
        expect(page.locator("#tagManagerMap .tag-map-viewport")).to_be_visible()

        expect(node("quadrilaterals")).to_be_visible()
        expand("quadrilaterals")
        expect(node("parallelogram")).to_be_visible()
        expand("parallelogram")
        expect(node("rectangle")).to_be_visible()
        expect(node("rhombus")).to_be_visible()
        expand("rectangle")
        expand("rhombus")
        expect(node("square")).to_have_count(1)
        expect(
            page.locator(f'#tagManagerMap path[data-parent-id="{tags["rectangle"]}"][data-child-id="{tags["square"]}"]')
        ).to_have_count(1)
        expect(
            page.locator(f'#tagManagerMap path[data-parent-id="{tags["rhombus"]}"][data-child-id="{tags["square"]}"]')
        ).to_have_count(1)
        expand("rectangle")
        expect(node("square")).to_have_count(1)
        expand("rhombus")
        expect(node("square")).to_have_count(0)
        expand("rectangle")
        expand("rhombus")
        choose("square")
        expect(page.locator("#tagManagerDetail .tag-directory-detail-usage")).to_contain_text("本级 4 题 · 含下级 5 题")
        capture("tag-map-desktop.png")
        viewport = page.locator("#tagManagerMap .tag-map-viewport")
        world = page.locator("#tagManagerMap .tag-map-world")
        transform_before = world.get_attribute("style")
        page.locator('#tagManagerMap [data-map-action="zoom-in"]').click()
        assert world.get_attribute("style") != transform_before
        viewport.focus()
        transform_before = world.get_attribute("style")
        page.keyboard.press("ArrowRight")
        assert world.get_attribute("style") != transform_before
        page.locator('#tagManagerMap [data-map-action="fit"]').click()
        bounds = viewport.bounding_box()
        transform_before = world.get_attribute("style")
        page.mouse.move(bounds["x"] + 5, bounds["y"] + 60)
        page.mouse.down()
        page.mouse.move(bounds["x"] + 45, bounds["y"] + 78, steps=5)
        page.mouse.up()
        assert world.get_attribute("style") != transform_before
        page.locator('#tagManagerMap [data-map-action="fit"]').click()
        original_position = node("square").get_attribute("style")
        bounds = node("square").locator(".tag-map-node-select").bounding_box()
        page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
        page.mouse.down()
        page.mouse.move(bounds["x"] + bounds["width"] / 2 + 22, bounds["y"] + bounds["height"] / 2 + 15, steps=5)
        viewport.dispatch_event("pointercancel", {"pointerId": 1})
        page.mouse.up()
        assert node("square").get_attribute("style") == original_position
        assert read("/api/library/tags")["revision"] == initial_catalogue["revision"]
        assert len([request for request in requests if request[0] == "POST"]) == writes_before
        report["checks"].append(
            "Map expands real hierarchy with one square and both parent edges; zoom, keyboard/mouse pan and cancelled node drag stay read-only"
        )

        # Composition must retain the old graph until the IME commits its input.
        search = page.locator("#tagManagerSearch")
        nodes_before = page.locator("#tagManagerMap .tag-map-node").count()
        search.evaluate(
            "el=>{el.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true})); el.value='正四边形'; el.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true}));}"
        )
        assert page.locator("#tagManagerMap .tag-map-node").count() == nodes_before
        search.evaluate("el=>el.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'正四边形'}))")
        expect(node("square")).to_have_count(1)
        expect(node("rectangle")).to_have_count(1)
        expect(node("rhombus")).to_have_count(1)
        expect(node("quadrilaterals")).to_have_count(1)
        expect(node("triangle")).to_have_count(0)
        search.fill("不存在的虚构标签")
        expect(page.locator("#tagManagerMap")).to_contain_text("没有")
        search.fill("")
        page.locator("#tagManagerListView").click()
        expect(page.locator("#tagManagerList")).to_be_visible()
        expect(page.locator("#tagManagerMap")).to_be_hidden()
        page.locator("#tagManagerMapView").click()
        expect(page.locator("#tagManagerMap .tag-map-viewport")).to_be_visible()
        assert len([request for request in requests if request[0] == "POST"]) == writes_before
        report["checks"].append(
            "Alias search displays both ancestry paths, IME waits for commit, no-result search and map/list switching remain read-only"
        )

        # Selecting a knowledge node never assigns its related method; dashed
        # relation is a navigation bridge and does not increase inherited count.
        search.fill("二次函数")
        choose("quadratic")
        expect(page.locator("#tagManagerDetail")).to_contain_text("配方法")
        expect(page.locator("#tagManagerDetail .tag-directory-detail-usage")).to_contain_text("本级 1 题 · 含下级 3 题")
        related_toggle = page.locator('#tagManagerMap [data-map-action="related"]')
        related_toggle.click()
        expect(
            page.locator(
                f'#tagManagerMap path[data-parent-id="{tags["quadratic"]}"][data-child-id="{tags["completing"]}"][data-relation-type="related_method"]'
            )
        ).to_have_count(1)
        expect(node("completing")).to_have_count(1)
        related_toggle.click()
        page.locator("#tagManagerMapKind").select_option("method")
        search.fill("")
        expect(node("proofs")).to_have_count(1)
        expect(node("quadrilaterals")).to_have_count(0)
        page.locator("#tagManagerMapKind").select_option("knowledge")
        report["checks"].append(
            "Related method is explicit, separate from knowledge inheritance and formal assignments"
        )

        # A current node's question handoff includes descendants and deduplicates
        # questions that explicitly contain both the parent and child labels.
        search.fill("矩形")
        choose("rectangle")
        page.locator("#tagManagerDetail .tag-directory-detail-usage").click()
        expect(page.locator("#tagManagerWorkspace")).to_be_hidden()
        page.wait_for_load_state("networkidle")
        expect(page.locator(".library-card")).to_have_count(6)
        assert read("/api/library?tag_any=" + tags["rectangle"] + "&tag_mode=any")["total"] == 6
        expect(page.locator("#q-" + ids[0])).to_have_count(0)
        expect(page.locator("#q-" + ids[9])).to_have_count(1)
        report["checks"].append(
            "Node-to-question navigation uses existing descendant-aware filtering and counts each question once"
        )

        # Open a map from a reading tag, browse locally, correct one association
        # explicitly, then return without resetting reading or checkbox selection.
        page.goto(base + "/library")
        page.wait_for_load_state("networkidle")
        card = page.locator("#q-" + ids[3])
        card.locator('input[type="checkbox"]').first.check()
        chip = card.locator(f'[data-tag-hover][data-tag-id="{tags["square"]}"]').first
        chip.scroll_into_view_if_needed()
        reading_before = page.evaluate("window.scrollY")
        rect_before = card.evaluate("el=>el.getBoundingClientRect().top")
        chip.click()
        expect(page.locator("#libraryTagMapEntry")).to_be_visible()
        page.locator("#libraryTagMapEntry").click()
        expect(page.locator("#tagManagerWorkspace")).to_be_visible()
        expect(page.locator("#tagManagerQuestionContext")).to_contain_text("第 4 题")
        expect(node("square")).to_have_count(1)
        expect(node("rectangle")).to_have_count(1)
        expect(node("rhombus")).to_have_count(1)
        expect(node("diagonal")).to_have_count(1)
        expect(page.locator("#tagManagerReplaceQuestionTag")).to_be_disabled()
        choose("rectangle")
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == "/api/library/tags/assignments/batch"
                and response.request.method == "POST"
            )
        ) as replacement:
            page.locator("#tagManagerReplaceQuestionTag").click()
        assert replacement.value.ok, replacement.value.text()
        expect(page.locator("#tagManagerQuestionContext")).to_contain_text("本题当前标签：矩形")
        assignments_after = read("/api/library/tags/assignments/batch?ids=" + ids[3])["items"][ids[3]]["assignments"]
        assert [assignment["tag"]["id"] for assignment in assignments_after] == [tags["rectangle"]]
        page.locator("#tagManagerBack").click()
        page.wait_for_load_state("networkidle")
        assert abs(page.evaluate("window.scrollY") - reading_before) <= 3
        assert abs(card.evaluate("el=>el.getBoundingClientRect().top") - rect_before) <= 3
        expect(card.locator('input[type="checkbox"]').first).to_be_checked()
        expect(card.locator(f'[data-tag-hover][data-tag-id="{tags["rectangle"]}"]').first).to_be_visible()
        report["checks"].append(
            "Question-tag map opens a local neighborhood; explicit same-kind replacement saves atomically and preserves the reading position and checkbox"
        )

        page.locator("#libraryTagManager").click()
        page.locator(".tag-directory-history > summary").click()
        assignment_row = (
            page.locator("#tagManagerHistoryList .tag-directory-history-row").filter(has_text="校正题目标签").first
        )
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == "/api/library/tags/undo" and response.request.method == "POST"
            )
        ) as undo_response:
            assignment_row.get_by_role("button", name="撤销", exact=True).click()
        assert undo_response.value.ok, undo_response.value.text()
        reverted = read("/api/library/tags/assignments/batch?ids=" + ids[3])["items"][ids[3]]["assignments"]
        assert [assignment["tag"]["id"] for assignment in reverted] == [tags["square"]]
        page.locator("#tagManagerBack").click()
        page.wait_for_load_state("networkidle")
        expect(card.locator(f'[data-tag-hover][data-tag-id="{tags["square"]}"]').first).to_be_visible()
        expect(card.locator('input[type="checkbox"]').first).to_be_checked()
        report["checks"].append(
            "Contextual map correction is visible in persistent history and its UI undo restores the original stable tag identity"
        )

        # Existing history and editor perform child and parent changes; map
        # browsing never invents a second persistence service.
        page.locator("#libraryTagManager").click()
        page.locator("#tagManagerMapView").click()
        search.fill("正方形")
        choose("square")
        page.locator("#tagManagerCreateChild").click()
        editor = page.locator("#tagCatalogueEditorDialog")
        expect(editor).to_be_visible()
        expect(page.locator("#tagCatalogueKind")).to_have_value("knowledge")
        assert page.locator("#tagCatalogueParents").evaluate("el=>Array.from(el.selectedOptions).map(x=>x.value)") == [
            tags["square"]
        ]
        page.locator("#tagCatalogueName").fill("正方形轴对称性质（隔离验收）")
        page.locator("#tagCatalogueSave").click()
        expect(editor).to_have_count(0)
        directory = read("/api/library/tags")
        created = next(tag for tag in directory["tags"] if tag["name"] == "正方形轴对称性质(隔离验收)")
        assert created["parent_ids"] == [tags["square"]]
        page.locator("#tagManagerAddParent").click()
        page.locator(f'.tag-directory-relation-options input[value="{tags["triangle"]}"]').check()
        page.locator("#tagManagerRelationSave").click()
        expect(page.locator(".tag-directory-relation-dialog")).to_have_count(0)
        assert tags["triangle"] in read("/api/library/tags/" + tags["square"] + "/relations")["tag"]["parent_ids"]
        if not page.locator(".tag-directory-history").evaluate("el=>el.open"):
            page.locator(".tag-directory-history > summary").click()
        history_row = (
            page.locator("#tagManagerHistoryList .tag-directory-history-row").filter(has_text="增加标签父级").first
        )
        history_row.get_by_role("button", name="撤销", exact=True).click()
        expect(page.locator(f'#tagManagerRelations [data-parent-id="{tags["triangle"]}"]')).to_have_count(0)
        assert tags["triangle"] not in read("/api/library/tags/" + tags["square"] + "/relations")["tag"]["parent_ids"]
        directory = read("/api/library/tags")
        cycle = post(
            "/api/library/tags",
            {
                "action": "add_parent",
                "tag_id": tags["square"],
                "parent_id": created["id"],
                "revision": directory["revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        assert cycle.status == 409 and "环" in cycle.json().get("error", ""), (cycle.status, cycle.text())
        assert read("/api/library/tags")["revision"] == directory["revision"]
        original = next(tag for tag in read("/api/library/tags")["tags"] if tag["id"] == tags["square"])
        assert "groups" not in original and set(original["parent_ids"]) == {tags["rectangle"], tags["rhombus"]}
        report["checks"].append(
            "Map child/parent edits reuse existing atomic services and history; undo removes only the new edge and cycles are rejected without touching the rest of the tree"
        )

        search.fill("正方形")
        choose("square")
        page.evaluate("window.scrollTo(0,0)")
        capture("tag-map-context-desktop.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.scrollTo(0,0)")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        capture("tag-map-mobile.png")
        page.locator("#tagManagerMap .tag-map-viewport").scroll_into_view_if_needed()
        capture("tag-map-mobile-canvas.png")
        report["checks"].append(
            "Desktop and 390px mobile map fit the viewport with accessible controls and no page overflow"
        )

        # A separately mounted empty map remains usable without seeding anything
        # or calling the catalogue API. This is the same production module.
        empty_state = page.evaluate("""() => {
            const host = document.createElement('div'); host.id = 'emptyMapAcceptance';
            document.body.append(host);
            const instance = QBLibraryTagMap.create({host});
            instance.update({catalogue:{revision:1,tags:[]},kind:'knowledge'});
            const result = {nodes:host.querySelectorAll('.tag-map-node').length,text:host.textContent};
            instance.destroy(); host.remove(); return result;
        }""")
        assert empty_state["nodes"] == 0 and any(word in empty_state["text"] for word in ("暂无", "没有", "空")), (
            empty_state
        )
        report["checks"].append(
            "Empty production map displays guidance without silently importing templates or creating tags"
        )

        assert not any(path == "/api/library/jobs" and method == "POST" for method, path in requests), requests
        assert not report["errors"], report["errors"]
        assert not report["external_requests"], report["external_requests"]
        context.close()
        browser.close()
    report["success"] = True
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Tag-map browser checks passed: " + "; ".join(report["checks"]), flush=True)


def run():
    seed()
    for _ in range(80):
        port = 20000 + uuid.uuid4().int % 40000
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                break
            except OSError:
                continue
    else:
        raise RuntimeError("No isolated local browser port was available")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = (
        [str(BUNDLE), "--internal-role", "runserver", f"127.0.0.1:{port}", "--noreload"]
        if BUNDLE
        else [sys.executable, str(ROOT / "backend/manage.py"), "runserver", f"127.0.0.1:{port}", "--noreload"]
    )
    os.environ["QB_INTERNAL_LOG"] = str(OUTPUT / "internal-server.log")
    with (OUTPUT / "server.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            command, cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT, creationflags=flags
        )
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
            check(f"http://127.0.0.1:{port}")
        finally:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(server.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                    check=False,
                )
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
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--url")
    parser.add_argument("--bundle", type=Path, help="With --run, test a frozen package using only isolated fixtures")
    parser.add_argument(
        "--output", type=Path, default=OUTPUT / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    )
    args = parser.parse_args()
    if args.bundle:
        if not args.run:
            parser.error("--bundle requires --run")
        BUNDLE = args.bundle.resolve()
        if BUNDLE.is_dir():
            BUNDLE = BUNDLE / "QuestionBankCard.exe"
        if BUNDLE.name != "QuestionBankCard.exe" or not BUNDLE.is_file():
            parser.error("--bundle must name an existing QuestionBankCard.exe or its package folder")
    OUTPUT = args.output.resolve()
    if not OUTPUT.is_relative_to(ROOT / "tmp"):
        raise SystemExit("--output must be inside this checkout's tmp directory")
    isolated_environment()
    if args.run:
        run()
    elif args.seed:
        seed()
    elif args.url:
        check(args.url.rstrip("/"))
    else:
        parser.error("Choose --run, --seed or --url")
