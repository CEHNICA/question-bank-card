"""Real local library UI checks with synthetic maths questions and no workers.

Reuses the isolated hierarchy harness, never the installed app or user data.
The before screenshot serves the delivered 1.21.0 frontend from its source ZIP.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import uuid
import zipfile
from urllib.parse import urlparse

import check_tag_hierarchy_browser as harness

ROOT = harness.ROOT
original_seed = harness.seed


def seed():
    original_seed()
    from core import library, library_tagging
    from core.models import LibraryTag, LibraryTagAssignment, LibraryTagRelation, PublishedQuestion
    from PIL import Image, ImageDraw
    fixtures = json.loads((harness.OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    questions = [PublishedQuestion.objects.get(pk=key) for key in fixtures["ids"]]
    geometry = LibraryTag.objects.get(pk=fixtures["tags"]["geometry"])
    triangle = LibraryTag.objects.create(name="直角三角形", kind="knowledge")
    LibraryTagRelation.objects.create(parent=geometry, child=triangle)
    period = LibraryTag.objects.create(name="三角函数的周期", kind="knowledge")
    LibraryTagRelation.objects.create(parent_id=fixtures["tags"]["functions"], child=period)
    properties = LibraryTag.objects.create(name="二次函数的性质", kind="knowledge")
    LibraryTagRelation.objects.create(parent_id=fixtures["tags"]["quadratic"], child=properties)
    specs = [
        ("single_choice", r"如图，在直角三角形 $ABC$ 中，$\angle A=90^\circ$，$AB=4$，$AC=3$。则 $\sin B$ 的值为（　）。", {"A": r"$\frac35$", "B": r"$\frac45$", "C": r"$\frac34$", "D": r"$\frac43$"}),
        ("free_response", r"已知函数 $f(x)=2\sin(\omega x+\varphi)$ 的最小正周期为 $\pi$。\n（1）求 $\omega$ 的值；\n（2）若 $f(0)=1$，且 $0<\varphi<\frac\pi2$，求 $\varphi$。", {}),
        ("fill_blank", r"已知二次函数 $y=x^2-4x+3$，当 $x\in[0,3]$ 时，函数的最小值为________。", {}),
        ("multiple_choice", r"对于函数 $f(x)=x^2$，下列说法正确的是（　）。", {"A": "图像关于 y 轴对称", "B": "值域为所有实数", "C": "在正半轴上单调递增", "D": "图像经过原点"}),
    ]
    for index, publication in enumerate(questions):
        kind, stem, options = specs[index % len(specs)]
        content = dict(publication.content)
        content.update(stem=stem.replace(r"\n", "\n"), options=options, origin="", question_type=kind,
                       source_filename="数学专项练习 · 界面验收示例.pdf")
        LibraryTagAssignment.objects.filter(publication=publication).delete()
        tag = [triangle, period, LibraryTag.objects.get(pk=fixtures["tags"]["minimum"]), properties][index % 4]
        LibraryTagAssignment.objects.create(publication=publication, tag=tag, source="human")
        if index == 0:
            folder = Path(os.environ["QB_DATA_ROOT"]) / "library" / str(publication.pk)
            folder.mkdir(parents=True, exist_ok=True)
            image = Image.new("RGB", (360, 220), "white")
            draw = ImageDraw.Draw(image)
            draw.line([(50, 175), (275, 175), (50, 25), (50, 175)], fill="#27362f", width=3)
            draw.line([(50, 157), (68, 157), (68, 175)], fill="#27362f", width=2)
            for point, label in [((34, 183), "A"), ((280, 174), "B"), ((35, 8), "C"), ((145, 181), "4"), ((26, 91), "3")]:
                draw.text(point, label, fill="#27362f", font_size=19)
            image.save(folder / "figure-1.png")
            content["figures"] = [{"slot": "stem", "file": "figure-1.png", "url": f"/api/library/{publication.pk}/figures/figure-1.png"}]
        publication.content = content
        publication.content_hash = library.content_hash(content)
        publication.search_text = library.search_key(content["stem"])
        publication.question_type = kind
        publication.source_filename = content["source_filename"]
        publication.save()
        library_tagging._sync_projection(publication.pk)


def check(base):
    from playwright.sync_api import sync_playwright, expect
    harness.harness.regression._assert_isolated()
    report = {"checks": [], "errors": [], "external_requests": [], "screenshots": [], "real_backend": True}
    fixtures = json.loads((harness.OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    with sync_playwright() as playwright:
        executable = next((str(path) for path in [Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"), Path(playwright.chromium.executable_path)] if path.is_file()), None)
        browser = playwright.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1536, "height": 960}, device_scale_factor=1)
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in {"localhost", "127.0.0.1"} else (report["external_requests"].append(route.request.url), route.abort()))
        page = context.new_page()
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        requests = []
        page.on("request", lambda request: requests.append((request.method, urlparse(request.url).path)))
        status_response = page.request.get(base + "/api/status", headers={"X-QB-Request": "1"})
        assert status_response.ok, status_response.text()
        report["app_version"] = status_response.json()["app_version"]
        report["frozen_bundle"] = str(harness.BUNDLE) if harness.BUNDLE else None

        def shot(name):
            page.evaluate("() => Promise.allSettled(document.getAnimations().filter(animation => animation.effect.getTiming().iterations !== Infinity).map(animation => animation.finished))")
            destination = harness.OUTPUT / name
            page.screenshot(path=str(destination), full_page=False)
            report["screenshots"].append(str(destination))

        def overflow():
            if not page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"):
                shot("overflow-diagnostic.png")
                details = page.evaluate("() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth, nodes: [...document.querySelectorAll('header,nav,main,section,input,button')].filter(node => {let r=node.getBoundingClientRect(); return r.width && (r.right>innerWidth+1 || r.left< -1)}).map(node => ({node:node.id||node.className, left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right})).slice(0,15)})")
                raise AssertionError("page overflows horizontally: " + json.dumps(details))

        # Use a fixed order, so the same example question appears in both views.
        url = base + "/library?sort=source"
        old_zip = ROOT / "packaging/dist/local-acceptance-1.21.0/question-bank-card-1.21.0-source.zip"
        if old_zip.is_file():
            with zipfile.ZipFile(old_zip) as archive:
                old = {name: archive.read("question-bank-card-1.21.0/frontend/" + name) for name in ["library.html", "library.css", "library.js"]}
            def before(route):
                path = urlparse(route.request.url).path
                key = "library.html" if path == "/library" else path.lstrip("/")
                if key in old:
                    route.fulfill(body=old[key], content_type="text/html" if key.endswith("html") else "text/css" if key.endswith("css") else "text/javascript")
                else:
                    route.fallback()
            page.route("**/*", before)
            page.goto(url)
            expect(page.locator(".library-card")).to_have_count(14)
            page.locator(".qb-figures img").first.wait_for()
            page.evaluate("document.fonts.ready")
            shot("before-desktop.png")
            page.unroute("**/*", before)
        page.goto(url)
        expect(page.locator(".library-card")).to_have_count(14)
        page.evaluate("document.fonts.ready")
        assert page.locator(".library-card .qb-figures img").first.evaluate("node => node.complete && node.naturalWidth > 0")
        overflow()
        first = page.locator(".library-card").first
        expect(first.locator(".library-number")).to_have_text("第 1 题")
        expect(page.locator("#libraryRail")).to_be_hidden()
        header_bottom = page.locator(".topbar").bounding_box()["height"]
        for key in ["searchInput", "allQuestionsButton", "basketViewButton", "libraryTagManager"]:
            expect(page.locator("#" + key)).to_be_visible()
            assert page.locator("#" + key).count() == 1
            assert page.locator("#" + key).bounding_box()["y"] < header_bottom
        assert page.locator("#questionInspector").bounding_box()["height"] < 600
        types = page.locator("#typeFilters .draft-filter").evaluate_all("nodes => nodes.map(node => Math.round(node.getBoundingClientRect().top))")
        assert len(set(types)) == 1, types
        shot("after-desktop.png")
        report["checks"].append("1536px: intentional filter rows, visible question titles, loaded figure, compact inspector and no overflow")

        page.locator("#advancedFilters > summary").click()
        expect(page.locator("#advancedFilters > .library-filters")).to_be_visible()
        overflow()
        page.locator("#advancedFilters > summary").click()
        first.locator(".library-card-select").check()
        expect(page.locator("#selectionCount")).to_contain_text("1")
        expect(page.locator("#addSelected")).to_be_enabled()
        page.locator("#clearSelection").click()
        first.get_by_role("button", name="加入试题篮", exact=True).click()
        expect(page.locator("#selectedViewCount")).to_have_text("1")
        page.locator("#basketHandle").click()
        expect(page.locator("#basketPanel")).to_be_visible()
        overflow()
        page.locator("#basketHandle").click()
        page.locator("#inspectorToggle").click()
        first.get_by_role("button", name="全屏看题", exact=True).click()
        expect(page.locator("#libraryQuestionViewer")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#libraryQuestionViewer")).to_be_hidden()
        first.locator(".library-card-tag-glimpse").first.hover()
        expect(page.locator("#libraryTagHover")).to_be_visible()
        page.keyboard.press("Escape")
        report["checks"].append("Advanced filters, batch selection, basket, full-screen viewing and hierarchy hover remain usable")

        page.locator("#closeInspector").click()
        width = page.locator(".library-results").bounding_box()["width"]
        assert width > 1400, width
        overflow()
        shot("both-sidebars-collapsed.png")
        page.locator("#inspectorToggle").click()
        first.locator(".library-number").click()
        page.keyboard.press("/")
        expect(page.locator("#searchInput")).to_be_focused()
        page.locator("#searchInput").fill("直角三角形")
        expect(page.locator(".library-card")).to_have_count(4)
        page.locator("#searchInput").fill("")
        expect(page.locator(".library-card")).to_have_count(14)
        report["checks"].append("The left rail stays hidden; right inspector collapse gives full list width; header search and / shortcut return the correct subset")

        page.set_viewport_size({"width": 1366, "height": 768})
        overflow()
        shot("after-laptop.png")
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#searchInput").evaluate("node => node.blur()")
        overflow()
        shot("after-mobile.png")
        expect(page.locator("#searchInput")).to_be_visible()
        expect(page.locator("#libraryRail")).to_be_hidden()
        first.get_by_role("button", name="标签与详情", exact=True).click()
        expect(page.locator("#questionInspector")).to_be_visible()
        overflow()
        shot("after-mobile-inspector.png")
        page.locator("#closeInspector").click()
        page.locator("#libraryTagManager").click()
        expect(page.get_by_role("region", name="标签管理", exact=True)).to_be_visible()
        page.locator("#tagManagerBack").click()
        expect(page.get_by_role("region", name="标签管理", exact=True)).to_be_hidden()
        assert not report["errors"], report["errors"]
        assert not report["external_requests"], report["external_requests"]
        ai_requests = [request for request in requests if request[0] == "POST" and request[1] == "/api/library/jobs"]
        assert not ai_requests, ai_requests
        report["ai_requests"] = ai_requests
        report["checks"].append("1366px and 390px: no horizontal overflow; explicit mobile drawers still work")
        context.close()
        browser.close()
    report["success"] = True
    (harness.OUTPUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="Test a frozen package folder or QuestionBankCard.exe using only isolated fixture data")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp/library-reading-design" / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]))
    args = parser.parse_args()
    if args.bundle:
        harness.BUNDLE = args.bundle.resolve()
        if harness.BUNDLE.is_dir():
            harness.BUNDLE = harness.BUNDLE / "QuestionBankCard.exe"
        if harness.BUNDLE.name != "QuestionBankCard.exe" or not harness.BUNDLE.is_file():
            parser.error("--bundle must name an existing QuestionBankCard.exe or its package folder")
    harness.OUTPUT = args.output.resolve()
    if not harness.OUTPUT.is_relative_to(ROOT / "tmp"):
        raise SystemExit("--output must be inside the checkout's tmp directory")
    harness.seed, harness.check = seed, check
    harness.run()
