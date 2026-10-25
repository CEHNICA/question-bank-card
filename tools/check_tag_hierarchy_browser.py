"""Windows browser acceptance against a real, isolated hierarchy database.

Synthetic questions only. The server has no worker, all storage is under tmp,
and browsers reject every external request. No installed user data is read.
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
OUTPUT = ROOT / "tmp" / "tag-hierarchy-browser"
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
    chapter = LibraryTag.objects.create(
        name="验收 · 高中函数", kind="knowledge", levels=["高中"], description="教材章节，用来给标签目录分层。"
    )
    tags = {}
    for key, name, kind in (
        ("functions", "函数", "knowledge"),
        ("equations", "代数", "knowledge"),
        ("geometry", "几何", "knowledge"),
        ("quadratic", "二次函数", "knowledge"),
        ("minimum", "二次函数的最值", "knowledge"),
        ("proofs", "代数方法", "method"),
        ("completing", "配方法", "method"),
    ):
        tags[key] = LibraryTag.objects.create(name=name, kind=kind, description="仅用于本机分级标签验收")
    LibraryTagRelation.objects.create(parent=chapter, child=tags["functions"])
    LibraryTagRelation.objects.create(parent=chapter, child=tags["equations"])
    for parent, child in (
        ("functions", "quadratic"),
        ("equations", "quadratic"),
        ("quadratic", "minimum"),
        ("proofs", "completing"),
    ):
        LibraryTagRelation.objects.create(parent=tags[parent], child=tags[child])
    LibraryTagRelation.objects.create(
        parent=tags["quadratic"], child=tags["completing"], relation_type="related_method"
    )
    paper = Paper.objects.create(
        filename="分级标签隔离虚构试卷.pdf", kind="pdf", sha256="hierarchy-offline-synthetic", pages=1, status="ready"
    )
    publication_ids = []
    for number in range(1, 15):
        stem = f"虚构第 {number} 题：已知二次函数 y=x²−4x+3，研究它的图像与最小值。这道题仅用于标签层级、鼠标和键盘操作验收。"
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
        for key in ["quadratic", "minimum"] if number == 3 else ["quadratic"] if number == 1 else ["minimum"]:
            LibraryTagAssignment.objects.create(publication=publication, tag=tags[key], source="human")
        library_tagging._sync_projection(publication.pk)
        publication_ids.append(str(publication.pk))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "fixture.json").write_text(
        json.dumps(
            {
                "ids": publication_ids,
                "tags": {key: str(tag.pk) for key, tag in tags.items()},
                "chapter": str(chapter.pk),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("Synthetic hierarchy browser fixture ready", flush=True)


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
        page.on("request", lambda request: requests.append((request.method, urlparse(request.url).path, request.url)))

        def read(path):
            response = page.request.get(base + path, headers={"X-QB-Request": "1"})
            assert response.ok, response.text()
            return response.json()

        def post(path, payload):
            return page.request.post(base + path, headers={"X-QB-Request": "1"}, data=payload)

        def assignment_ids(publication_id):
            assignments = read("/api/library/tags/assignments/batch?ids=" + publication_id)
            return [entry["tag"]["id"] for entry in assignments["items"][publication_id]["assignments"]]

        def capture(name):
            output = OUTPUT / name
            page.screenshot(path=str(output), full_page=False)
            report["screenshots"].append(str(output))

        def open_chip(publication_id, tag_id, keyboard=False):
            card = page.locator("#q-" + publication_id)
            chip = card.locator(f'[data-tag-hover][data-tag-id="{tag_id}"]').first
            chip.scroll_into_view_if_needed()
            if keyboard:
                chip.focus()
            else:
                chip.click()
            expect(page.locator("#libraryTagHover")).to_be_visible()
            expect(page.locator("#libraryTagHover .library-tag-hover-section").first).to_be_visible()
            return page.locator("#libraryTagHover")

        page.goto(base + "/library")
        page.wait_for_load_state("networkidle")
        expect(page.locator(".library-card")).to_have_count(14)
        # Keyboard focus opens exactly the same contextual hierarchy as hover/click.
        popup = open_chip(ids[0], tags["quadratic"], keyboard=True)
        expect(popup.locator(".library-tag-hover-path")).to_have_count(2)
        expect(popup).to_contain_text("相关方法 · 添加到本题")
        before_revision = read("/api/library/tags/assignments/batch?ids=" + ids[0])["items"][ids[0]]["revision"]
        before_writes = len([request for request in requests if request[0] == "POST"])
        # Expand a parent's ancestry and view paths without making a write.
        assert popup.locator(".library-tag-hover-name.current").evaluate_all(
            "buttons=>buttons.length>0&&buttons.every(button=>button.disabled)"
        )
        page.keyboard.press("Escape")
        expect(popup).to_be_hidden()
        popup = open_chip(ids[1], tags["minimum"])
        arrow = popup.get_by_role("button", name="展开二次函数的上级", exact=True)
        arrow.focus()
        page.keyboard.press("Enter")
        expect(popup.locator(f'.library-tag-hover-name[data-replace-tag-id="{tags["functions"]}"]')).to_be_visible()
        assert len([request for request in requests if request[0] == "POST"]) == before_writes
        assert read("/api/library/tags/assignments/batch?ids=" + ids[0])["items"][ids[0]]["revision"] == before_revision
        page.keyboard.press("Escape")
        report["checks"].append(
            "Keyboard/click opens one contextual popup, multiple parent paths and expandable ancestors are read-only; current label is inert"
        )

        # A downward correction modifies only the source relation and preserves the question's reading position.
        popup = open_chip(ids[0], tags["quadratic"])
        page.locator("#q-" + ids[0] + " .library-card-check").check() if page.locator(
            "#q-" + ids[0] + " .library-card-check"
        ).count() else None
        scroll_before = page.evaluate("window.scrollY")
        with page.expect_response(
            lambda response: (
                urlparse(response.url).path == "/api/library/tags/assignments/batch"
                and response.request.method == "POST"
            )
        ) as corrected:
            popup.locator(f'[data-replace-tag-id="{tags["minimum"]}"]').click()
        assert corrected.value.ok, corrected.value.text()
        expect(
            page.locator("#q-" + ids[0]).locator(f'[data-tag-hover][data-tag-id="{tags["minimum"]}"]')
        ).to_be_visible()
        expect(popup).to_contain_text("已将")
        page.wait_for_load_state("networkidle")
        scroll_after = page.evaluate("window.scrollY")
        if abs(scroll_after - scroll_before) > 3:
            report["errors"].append(f"Tag replacement moved scroll from {scroll_before} to {scroll_after}")
        assert assignment_ids(ids[0]) == [tags["minimum"]]
        popup.get_by_role("button", name="撤销", exact=True).click()
        expect(page.locator("#q-" + ids[0]).locator(f'[data-tag-id="{tags["quadratic"]}"]')).to_be_visible()
        expect(popup.locator(".library-tag-hover-head")).to_contain_text("二次函数")
        expect(popup.locator(".library-tag-hover-head")).not_to_contain_text("二次函数的最值")
        assert assignment_ids(ids[0]) == [tags["quadratic"]]
        page.keyboard.press("Escape")
        report["checks"].append(
            "Downward replace uses one atomic saved operation, preserves scroll and supports persistent undo"
        )

        # Parent and child already assigned: replacement deduplicates the target without losing other relations.
        popup = open_chip(ids[2], tags["quadratic"])
        popup.locator(f'[data-replace-tag-id="{tags["minimum"]}"]').click()
        expect(page.locator("#q-" + ids[2]).locator(f'[data-tag-id="{tags["quadratic"]}"]')).to_have_count(0)
        assert assignment_ids(ids[2]) == [tags["minimum"]]
        page.keyboard.press("Escape")
        report["checks"].append("Replacing a parent with an already assigned child deduplicates one target")

        # Send a real write, deliberately lose only its response, then recover by request ID.
        dropped = []

        def lose_response(route):
            if route.request.method != "POST":
                route.continue_()
                return
            if dropped:
                route.continue_()
                return
            response = route.fetch()
            assert response.ok, response.text()
            dropped.append(route.request.post_data_json)
            route.fulfill(status=503, content_type="application/json", body='{"error":"隔离验收：保存成功但响应丢失"}')

        page.route("**/api/library/tags/assignments/batch", lose_response)
        popup = open_chip(ids[0], tags["quadratic"])
        popup.locator(f'[data-replace-tag-id="{tags["minimum"]}"]').click()
        expect(popup.get_by_role("button", name="核实上次操作", exact=True)).to_be_visible()
        expect(popup.get_by_role("button", name="核实上次操作", exact=True)).to_be_enabled()
        assert assignment_ids(ids[0]) == [tags["minimum"]]
        request_id = dropped[0]["request_id"]
        popup.get_by_role("button", name="核实上次操作", exact=True).click()
        expect(popup.get_by_role("button", name="核实上次操作", exact=True)).to_have_count(0)
        expect(page.locator("#q-" + ids[0]).locator(f'[data-tag-id="{tags["minimum"]}"]')).to_be_visible()
        operations = read("/api/library/tags/operations?request_id=" + request_id)["operations"]
        assert len(operations) == 1
        assert any("request_id=" + request_id in request[2] for request in requests), requests
        page.unroute("**/api/library/tags/assignments/batch", lose_response)
        page.keyboard.press("Escape")
        report["checks"].append(
            "Real saved response loss recovers through the original request ID, with one persisted operation"
        )

        # Moving upward out of a leaf filter retains that card for the current reading session.
        page.goto(base + "/library?tag_any=" + tags["minimum"] + "&tag_mode=any")
        page.wait_for_load_state("networkidle")
        total_before = read("/api/library?tag_any=" + tags["minimum"] + "&tag_mode=any")["total"]
        popup = open_chip(ids[0], tags["minimum"])
        popup.locator(f'[data-replace-tag-id="{tags["quadratic"]}"]').click()
        expect(page.locator("#q-" + ids[0] + " .library-tag-retained")).to_be_visible()
        assert read("/api/library?tag_any=" + tags["minimum"] + "&tag_mode=any")["total"] == total_before - 1
        capture("hierarchy-question-popup-desktop.png")
        page.keyboard.press("Escape")
        page.reload()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#q-" + ids[0])).to_have_count(0)
        report["checks"].append(
            "Upward replace retains a now-unmatched card and updates match count; explicit refresh removes it"
        )

        # Manage real relation edges; grouping no longer exists, so the parent
        # tree is the only way to place a tag.
        page.goto(base + "/library")
        page.wait_for_load_state("networkidle")
        page.locator("#libraryTagManager").click()
        manager = page.locator("#tagManagerWorkspace")
        expect(manager).to_be_visible()
        page.locator('#tagManagerHierarchyNav [data-nav-id="knowledge"]').click()
        # 按类型浏览时先看到的是顶层标签，章节要先展开才看得到它下面的标签。
        page.locator(f'[data-expand-tag-id="{fixture["chapter"]}"]').click()
        page.locator(f'[data-expand-tag-id="{tags["functions"]}"]').click()
        page.locator(f'[data-tag-id="{tags["quadratic"]}"] .tag-directory-name').click()
        expect(page.locator("#tagManagerRelations [data-parent-id]")).to_have_count(2)
        page.locator("#tagManagerCreateChild").click()
        editor = page.locator("#tagCatalogueEditorDialog")
        expect(editor).to_be_visible()
        expect(page.locator("#tagCatalogueKind")).to_have_value("knowledge")
        assert page.locator("#tagCatalogueParents").evaluate("el=>Array.from(el.selectedOptions).map(x=>x.value)") == [
            tags["quadratic"]
        ]
        page.locator("#tagCatalogueName").fill("二次函数顶点坐标（隔离验收）")
        page.locator("#tagCatalogueSave").click()
        expect(editor).to_have_count(0)
        directory = read("/api/library/tags")
        created = next(tag for tag in directory["tags"] if tag["name"] == "二次函数顶点坐标(隔离验收)")
        assert created["parent_ids"] == [tags["quadratic"]]
        assert tags["quadratic"] in assignment_ids(ids[0])
        page.locator("#tagManagerAddParent").click()
        page.locator(f'.tag-directory-relation-options input[value="{tags["geometry"]}"]').check()
        page.locator("#tagManagerRelationSave").click()
        expect(page.locator(".tag-directory-relation-dialog")).to_have_count(0)
        assert tags["geometry"] in read("/api/library/tags/" + tags["quadratic"] + "/relations")["tag"]["parent_ids"]
        page.locator(".tag-directory-history > summary").click()
        history_row = (
            page.locator("#tagManagerHistoryList .tag-directory-history-row").filter(has_text="增加标签父级").first
        )
        history_row.get_by_role("button", name="撤销", exact=True).click()
        expect(page.locator(f'[data-parent-id="{tags["geometry"]}"]')).to_have_count(0)
        assert (
            tags["geometry"] not in read("/api/library/tags/" + tags["quadratic"] + "/relations")["tag"]["parent_ids"]
        )
        # UI omits cyclic candidates; the service rejects the same attempt sent directly.
        page.locator("#tagManagerAddParent").click()
        expect(page.locator(f'.tag-directory-relation-options input[value="{created["id"]}"]')).to_have_count(0)
        page.keyboard.press("Escape")
        expect(page.locator(".tag-directory-relation-dialog")).to_have_count(0)
        directory = read("/api/library/tags")
        cycle = post(
            "/api/library/tags",
            {
                "action": "add_parent",
                "tag_id": tags["quadratic"],
                "parent_id": created["id"],
                "revision": directory["revision"],
                "request_id": str(uuid.uuid4()),
            },
        )
        assert cycle.status == 409 and "环" in cycle.json().get("error", ""), (cycle.status, cycle.text())
        assert read("/api/library/tags")["revision"] == directory["revision"]
        unchanged = next(tag for tag in directory["tags"] if tag["id"] == tags["quadratic"])
        assert "groups" not in unchanged
        assert set(unchanged["parent_ids"]) == {tags["functions"], tags["equations"]}
        page.evaluate("window.scrollTo(0,0)")
        capture("hierarchy-manager-desktop.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.evaluate("window.scrollTo(0,0)")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        capture("hierarchy-manager-mobile.png")
        manager.locator("#tagManagerDetail").scroll_into_view_if_needed()
        capture("hierarchy-manager-mobile-details.png")
        report["checks"].append(
            "Real manager creates a same-kind child, retains old parent assignments, edits multiple parents, undoes one edge, leaves the rest of the tree alone and rejects cycles atomically"
        )
        page.locator("#tagManagerBack").click()

        # A touch-only client can open and dismiss the same hierarchy without hovering.
        touch = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
        touch.route("**/*", local_only)
        mobile = touch.new_page()
        mobile.on("pageerror", lambda error: report["errors"].append(str(error)))
        mobile.goto(base + "/library")
        mobile.wait_for_load_state("networkidle")
        chip = mobile.locator("#q-" + ids[1]).locator(f'[data-tag-id="{tags["minimum"]}"]').first
        chip.scroll_into_view_if_needed()
        chip.tap()
        expect(mobile.locator("#libraryTagHover")).to_be_visible()
        expect(mobile.locator("#libraryTagHover .library-tag-hover-section").first).to_be_visible()
        assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        destination = OUTPUT / "hierarchy-popup-touch.png"
        mobile.screenshot(path=str(destination), full_page=False)
        report["screenshots"].append(str(destination))
        mobile.get_by_role("button", name="关闭标签层级", exact=True).tap()
        expect(mobile.locator("#libraryTagHover")).to_be_hidden()
        report["checks"].append("390px touch-only popup opens, fits the viewport and closes with an explicit control")
        assert not any(request[1] == "/api/library/jobs" and request[0] == "POST" for request in requests), requests
        assert not report["errors"], report["errors"]
        assert not report["external_requests"], report["external_requests"]
        touch.close()
        context.close()
        browser.close()
    report["success"] = True
    (OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Hierarchy browser checks passed: " + "; ".join(report["checks"]), flush=True)


def run():
    seed()
    # Some Windows hosts assign low ephemeral ports forbidden by browsers.
    # Reserve a free high port instead of inheriting that platform range.
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
    parser.add_argument(
        "--bundle",
        type=Path,
        help="With --run, test a frozen package folder or QuestionBankCard.exe using only isolated fixture data",
    )
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
