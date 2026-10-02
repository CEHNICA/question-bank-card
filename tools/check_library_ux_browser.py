"""Offline browser regression for library filters, source zoom and related sources.

Both QB_DATABASE and QB_DATA_ROOT must point under this checkout's tmp directory.
Run --seed then start only Django (no worker) and run --url against that server.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "library-ux-browser"


def seed():
    for name in ("QB_DATABASE", "QB_DATA_ROOT"):
        value = os.environ.get(name)
        if not value or not Path(value).resolve().is_relative_to(ROOT / "tmp"):
            raise SystemExit(f"{name} must be inside this checkout's tmp directory")
    from check_library_history_browser import seed as seed_history
    seed_history()
    from django.conf import settings
    from core import library
    from core.models import Paper, Question, PublishedQuestion
    import shutil

    OUTPUT.mkdir(parents=True, exist_ok=True)
    base = Question.objects.get(number=12)
    base.stem = "设集合 $A=\\{x|x>1\\}$，求 $\\complement_U A$。"
    base.question_type = "single_choice"
    base.options = {"A": "空集", "B": "$\\{x|x\\leq 1\\}$"}
    base.regions = [{"page_idx": 0, "bbox": [90, 125, 480, 220]}]
    base.save()
    content = library.final_content(base)
    first = PublishedQuestion.objects.get(question=base)
    first.content = content
    first.question_type = base.question_type
    first.content_hash = library.content_hash(content)
    first.search_text = library._search_text(content)
    first.save()
    ids = [str(first.id)]
    for index, near in enumerate((False, True)):
        paper = Paper.objects.create(filename=f"另一份资料{index + 1}.pdf", kind="pdf", sha256=f"ux-other-{index}",
                                     pages=base.paper.pages, status="ready")
        folder = settings.DATA_ROOT / str(paper.id)
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(base.paper.source_path, folder / "source.pdf")
        paper.source_path = str(folder / "source.pdf")
        paper.save()
        saved = deepcopy(content)
        saved.update(document_id=str(paper.id), source_filename=paper.filename)
        if near:
            saved["stem"] = saved["stem"].replace("x|", "x\\mid ").replace("_U ", "_{U}")
        draft = Question.objects.create(paper=paper, number=1, question_type="single_choice", stem=saved["stem"],
                                        options=saved["options"], regions=base.regions)
        row = PublishedQuestion.objects.create(question=draft, paper=paper, source_filename=paper.filename,
            number=1, question_type="single_choice", version=1, status="published", content=saved,
            content_hash=library.content_hash(saved), search_text=library._search_text(saved))
        ids.append(str(row.id))
    (OUTPUT / "fixture.json").write_text(json.dumps({"ids": ids}), encoding="utf-8")
    print("Offline UX fixtures ready")


def check(url):
    from playwright.sync_api import sync_playwright, expect
    ids = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))["ids"]
    errors = []
    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 650, "height": 920})
        context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).hostname in
                      ("127.0.0.1", "localhost") else route.abort())
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(url + "/library")
        page.wait_for_load_state("networkidle")

        # All filters, including source and review, clear in one action.
        page.get_by_label("来源试卷", exact=True).select_option(label="另一份资料1.pdf（1）")
        page.get_by_label("搜索题目", exact=True).fill("不存在的关键词-回归")
        expect(page.locator(".library-empty")).to_contain_text("其他关键词")
        expect(page.locator(".library-empty")).not_to_contain_text("录入终审")
        page.get_by_role("button", name="清除搜索与筛选", exact=True).click()
        expect(page.locator(".library-card")).to_have_count(4)
        expect(page.get_by_label("来源试卷", exact=True)).to_have_value("")
        expect(page.get_by_label("搜索题目", exact=True)).to_have_value("")
        assert not urlparse(page.url).query

        card = page.locator(f"#q-{ids[0]}")
        card.get_by_role("button", name="查看出处", exact=True).click()
        dialog = page.locator("#sourceDialog")
        expect(page.locator("#sourceQuestion")).to_have_attribute("aria-pressed", "true")
        image = dialog.locator("img").first
        expect(image).to_be_visible()
        page.wait_for_function("[...document.querySelectorAll('#sourcePages img')].every(i => i.complete && i.naturalWidth > 0)")
        crop_width = image.evaluate("e => e.getBoundingClientRect().width")
        box = dialog.locator(".source-box").first
        assert box.evaluate("e => { const a=e.getBoundingClientRect(),b=e.parentElement.getBoundingClientRect(); return a.left >= b.left-1 && a.right <= b.right+1 && a.top>=b.top-1 && a.bottom<=b.bottom+1; }")
        page.locator("#sourceWholePage").click()
        expect(page.locator("#sourceQuestion")).to_have_attribute("aria-pressed", "false")
        expect(dialog.locator(".source-cropped")).to_have_count(0)
        full_width = image.evaluate("e => e.getBoundingClientRect().width")
        assert crop_width > full_width * 2
        page.locator("#sourceZoomIn").click()
        expect(page.locator("#sourceZoom")).to_have_text("150%")
        assert dialog.locator(".source-content").evaluate("e => e.getBoundingClientRect().width") > full_width * 1.4
        page.locator("#sourceFit").click()
        # Fit includes height: a whole portrait page must be visible, rather than only fitting its width.
        fitted = page.locator("#sourcePages .source-surface").first.evaluate("""surface => {
            const canvas = surface.closest('#sourcePages');
            const style = getComputedStyle(canvas);
            const rect = surface.getBoundingClientRect();
            return { width: rect.width, height: rect.height,
                roomWidth: canvas.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
                roomHeight: canvas.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom) - 26 };
        }""")
        assert fitted["width"] <= fitted["roomWidth"] + 1, fitted
        assert fitted["height"] <= fitted["roomHeight"] + 1, fitted
        page.locator("#sourceQuestion").click()
        page.screenshot(path=str(OUTPUT / "source-question.png"))
        dialog.get_by_role("button", name="关闭", exact=True).click()

        card.get_by_role("button", name="版本历史", exact=True).click()
        expect(page.locator("#historyRelated")).to_be_visible()
        page.locator("#historyRelated summary").click()
        expect(page.locator("#historyRelated")).to_contain_text("题面相同的其他资料")
        expect(page.locator("#historyRelated")).to_contain_text("题面相近的其他资料（需核对）")
        expect(page.locator("#historyVersions button")).to_have_count(1)
        page.locator("#historyRelated").get_by_role("button", name="查看这份资料的历史", exact=True).first.click()
        expect(page.locator("#historyTitle")).to_contain_text("另一份资料1.pdf")
        expect(page.locator("#historyVersions button")).to_have_count(1)
        page.locator("#historyRelated summary").click()
        page.locator("#historyRelated").get_by_role("button", name="查看原卷", exact=True).first.click()
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator("#historyDialog")).to_be_visible()
        page.screenshot(path=str(OUTPUT / "related-sources.png"))
        page.locator("#historyDialog").get_by_role("button", name="关闭", exact=True).click()

        # A failed original still leaves a useful fallback in cropped mode.
        page.route("**/api/documents/*/pages/*/preview", lambda route: route.fulfill(status=404, body="missing"))
        page.locator(f"#q-{ids[1]}").get_by_role("button", name="查看出处", exact=True).click()
        expect(page.locator(".source-unavailable")).to_be_visible()
        page.unroute("**/api/documents/*/pages/*/preview")
        dialog.get_by_role("button", name="关闭", exact=True).click()

        page.set_viewport_size({"width": 390, "height": 844})
        card.get_by_role("button", name="版本历史", exact=True).click()
        expect(page.locator("#historyStatus")).to_contain_text("只有这一版")
        assert page.locator("#historyDialog").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
        assert not errors, errors
        browser.close()
    print("Library UX browser checks passed: clear filters, crop/position/zoom, separate related histories, missing source, narrow viewport")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8980")
    args = parser.parse_args()
    seed() if args.seed else check(args.url.rstrip("/"))
