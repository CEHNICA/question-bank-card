"""Exercise history in a real browser, using an isolated offline demo database.

Set QB_DATABASE and QB_DATA_ROOT to paths below this checkout's tmp directory.
Run --seed, then start Django locally and run this script with --url. No worker,
OCR, credentials or external services are used. Requires Python Playwright and
an installed Chrome/Edge (or Playwright Chromium).
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "history-browser"
OUTPUT.mkdir(parents=True, exist_ok=True)


def seed():
    for name in ("QB_DATABASE", "QB_DATA_ROOT"):
        value = os.environ.get(name)
        if not value or not Path(value).resolve().is_relative_to(ROOT / "tmp"):
            raise SystemExit(f"{name} must point inside this checkout's tmp directory")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "qb_server.settings")
    sys.path.insert(0, str(ROOT / "backend"))
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    from django.utils import timezone
    from PIL import Image, ImageDraw
    from core import library
    from core.models import Paper, Question, PublishedQuestion

    call_command("migrate", verbosity=0)
    fixture = json.loads((ROOT / "backend/core/demo_data/demo-paper.json").read_text(encoding="utf-8"))
    paper = Paper.objects.create(filename="历史功能离线演示.pdf", kind="pdf", sha256="browser-history",
                                 pages=fixture["pages"], status="ready")
    folder = settings.DATA_ROOT / str(paper.id)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "backend/core/demo_data/demo-paper.pdf", folder / "source.pdf")
    paper.source_path = str(folder / "source.pdf")
    paper.save()
    item = deepcopy(next(row for row in fixture["questions"] if row["number"] == 11))
    question = Question.objects.create(paper=paper, **item)
    content = library.final_content(question)
    content.update(stem="如图，已知函数 $f(x)=x^2+4$，求图像与 $x$ 轴交点间的距离。", answer="4", analysis="这是一份用于测试历史窗口的虚构记录。", origin="原创演示卷")
    versions = []
    for number in (1, 2, 3):
        saved = deepcopy(content)
        if number >= 2:
            saved.update(stem="如图，已知函数 $f(x)=x^2-4$，求图像与 $x$ 轴交点间的距离。", answer="$4$", analysis="令 $x^2-4=0$，得 $x=\\pm2$，两点距离为 $4$。")
        if number == 3:
            saved["figures"][0]["bbox"][0] += 10
        version = PublishedQuestion.objects.create(question=question, paper=paper, source_filename=paper.filename,
            number=11, question_type=question.question_type, version=number,
            status="published" if number == 3 else "superseded", content=saved, content_hash=library.content_hash(saved),
            published_at=timezone.now(), review_source="ai" if number == 1 else "human", review_agent="演示助手" if number == 1 else "")
        image_folder = settings.DATA_ROOT / "library" / str(version.id)
        image_folder.mkdir(parents=True, exist_ok=True)
        plot = Image.new("RGB", (300, 155), "#faf7ec")
        draw = ImageDraw.Draw(plot)
        draw.line((20, 112, 280, 112), fill="#26463d", width=2)
        draw.line((150, 10, 150, 150), fill="#26463d", width=2)
        points = [(x, int(132 - ((x - 150) / 12) ** 2)) for x in range(22, 280)]
        draw.line(points, fill="#2c8064", width=3)
        plot.save(image_folder / "figure-1.png")
        version.content["figures"][0].update(file="figure-1.png", url=f"/api/library/{version.id}/figures/figure-1.png")
        version.save()
        versions.append(str(version.id))
    # A single-version card exercises the empty-comparison state.
    another = Question.objects.create(paper=paper, number=12, question_type="free_response", stem="求 $1+1$。")
    single = PublishedQuestion.objects.create(question=another, paper=paper, source_filename=paper.filename,
        number=12, question_type="free_response", version=1, content={"stem": "求 $1+1$。", "figures": [], "sources": []},
        content_hash="single", status="published")
    (OUTPUT / "fixture.json").write_text(json.dumps({"versions": versions, "single": str(single.id)}), encoding="utf-8")
    print("Isolated history fixtures ready")


def check(url):
    from playwright.sync_api import sync_playwright, expect
    fixture = json.loads((OUTPUT / "fixture.json").read_text(encoding="utf-8"))
    first, second, third = fixture["versions"]
    errors = []
    with sync_playwright() as pw:
        executable = next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()), None)
        browser = pw.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
        context = browser.new_context(viewport={"width": 1440, "height": 1050})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        def local_only(route):
            if urlparse(route.request.url).hostname not in ("127.0.0.1", "localhost"):
                raise AssertionError("Unexpected external browser request")
            route.continue_()
        context.route("**/*", local_only)
        page.goto(url + "/library")
        page.wait_for_load_state("networkidle")
        card = page.locator(f"#q-{third}")
        card.get_by_role("button", name="版本历史", exact=True).click()
        expect(page.locator("#historyStatus")).to_contain_text("共 3 个入库版本")
        expect(page.locator(".history-panel")).to_have_count(2)
        expect(page.locator("#historySummary")).to_have_text("改了：配图")
        expect(page.locator("#historyDiff")).to_be_hidden()
        page.locator(f'.history-version[data-id="{second}"]').click()
        expect(page.locator("#historyStatus")).to_contain_text("正在查看第 2 版，与第 1 版比较")
        expect(page.locator("#historySummary")).to_contain_text("题干")
        expect(page.locator(".history-version").last).to_contain_text("演示助手")
        page.locator("#historyDiff summary").click()
        expect(page.locator("#historyChanges del").first).to_have_text("+")
        expect(page.locator("#historyChanges ins").first).to_have_text("-")
        page.screenshot(path=str(OUTPUT / "history-desktop.png"))
        page.locator(".history-panel").first.get_by_role("button", name="查看这版原卷位置").click()
        expect(page.locator("#sourceDialog")).to_be_visible()
        page.wait_for_function("[...document.querySelectorAll('#sourcePages img')].every(i => i.complete && i.naturalWidth > 0)")
        expect(page.locator("#sourcePages .source-box")).not_to_have_count(0)
        page.locator("#sourceDialog").get_by_role("button", name="关闭", exact=True).click()
        expect(page.locator("#historyDialog")).to_be_visible()
        page.locator(f'.history-version[data-id="{third}"]').click()
        expect(page.locator("#historyStatus")).to_contain_text("正在查看第 3 版")
        page.get_by_label("对照版本", exact=True).select_option(first)
        expect(page.locator("#historyStatus")).to_contain_text("与第 1 版比较")

        # A slow response must not overwrite the version selected afterwards.
        old_response = page.request.get(url + f"/api/library/{first}").json()
        held = []
        pattern = "**/api/library/" + first
        page.route(pattern, lambda route: held.append(route))
        page.locator(f'.history-version[data-id="{first}"]').click()
        page.wait_for_function("document.querySelector('#historyStatus').textContent.includes('正在读取')")
        page.locator(f'.history-version[data-id="{third}"]').click()
        expect(page.locator("#historyStatus")).to_contain_text("正在查看第 3 版")
        assert held
        held[0].fulfill(json=old_response)
        page.unroute(pattern)
        expect(page.locator(f'.history-version[data-id="{third}"]')).to_have_attribute("aria-pressed", "true")

        # Missing originals get a useful message without hiding stored text.
        pattern = "**/api/documents/*/pages/*/preview*"
        page.route(pattern, lambda route: route.fulfill(status=404, body="missing"))
        page.locator(".history-panel").last.get_by_role("button", name="查看这版原卷位置").click()
        # Use a distinct URL so a previously decoded page cannot satisfy this
        # deliberately missing-page request from the browser's image cache.
        page.locator("#sourcePages img").first.evaluate("img => { img.src += '?offline-test=missing'; }")
        expect(page.locator("#sourcePages .source-unavailable").first).to_be_visible()
        page.locator("#sourceDialog").get_by_role("button", name="关闭", exact=True).click()
        page.unroute(pattern)

        # Snapshot text is treated as text even in the raw-change panel.
        response = page.request.get(url + f"/api/library/{second}?compare={first}").json()
        marker = '<img src=x onerror="window.historyInjected=1">'
        response["comparison"]["changes"][0]["segments"] = [{"kind": "replace", "before": marker, "after": "safe"}]
        pattern = "**/api/library/" + second + "?compare=*"
        page.route(pattern, lambda route: route.fulfill(json=response))
        page.locator(f'.history-version[data-id="{second}"]').click()
        expect(page.locator("#historyChanges del").first).to_have_text(marker)
        assert page.evaluate("window.historyInjected") is None
        assert page.locator("#historyChanges img").count() == 0
        page.unroute(pattern)

        page.set_viewport_size({"width": 390, "height": 844})
        page.locator(f'.history-version[data-id="{second}"]').click()
        expect(page.locator("#historyStatus")).to_contain_text("正在查看第 2 版")
        assert page.locator("#historyDialog").evaluate("e => e.scrollWidth <= e.clientWidth + 1")
        page.screenshot(path=str(OUTPUT / "history-mobile.png"))
        page.keyboard.press("Escape")
        expect(page.locator("#historyDialog")).not_to_be_visible()
        page.locator(f'#q-{fixture["single"]}').get_by_role("button", name="版本历史", exact=True).click()
        expect(page.locator("#historyStatus")).to_contain_text("只有这一版入库记录")
        expect(page.locator("#historyControls")).to_be_hidden()
        expect(page.locator(".history-panel")).to_have_count(1)
        assert not errors, errors
        browser.close()
    print("Browser checks passed: comparison, math edits, reviewer labels, original positions, stale responses, missing pages, safe text, mobile, single version")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--url", default="http://127.0.0.1:8976")
    args = parser.parse_args()
    seed() if args.seed else check(args.url.rstrip("/"))
