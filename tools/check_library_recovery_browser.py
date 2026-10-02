"""Offline human-flow regression for library error recovery and printing.

Run --run to serve only the checkout frontend on port 8985, intercept API GETs
with synthetic data, exercise browser interactions and capture print PDFs under
checkout/tmp/library-recovery-browser. No Django, worker, cloud, real data or
physical printer is used. Existing QA failure evidence is left untouched.
"""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
from threading import Thread
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright, expect

from check_library_preview_browser import FrontendHandler, fixture_item

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "library-recovery-browser"


def question(key, number, kind="single_choice", stem=None):
    item = fixture_item(key, number, stem=stem)
    item["question_type"] = item["content"]["question_type"] = kind
    if kind not in ("single_choice", "multiple_choice"):
        item["content"]["options"] = {}
    return item


def payload(items):
    types = {}
    for item in items:
        types[item["question_type"]] = types.get(item["question_type"], 0) + 1
    return {"items": items, "total": len(items), "features": {"ai_answer": False},
        "facets": {"sources": [], "types": types, "answers": {"yes": 0, "no": len(items)},
                   "reviews": {"human": len(items)}, "tags": []}}


def fixtures():
    long = "长公式：$" + "+".join(["x^2"] * 28) + "=0$。"
    fraction = "长分数：$\\dfrac{1}{" + "+".join(["x^2"] * 24) + "}=24681$。"
    proof = "证明：$\\dfrac{x^2}{(2-y)(2-z)}+\\dfrac{y^2}{(2-z)(2-x)}+\\dfrac{z^2}{(2-x)(2-y)}\\geq3$。"
    items = [question("choice-a", 1), question("choice-long", 2, stem=long),
        question("choice-fraction", 3, stem=fraction), question("fill-a", 4, "fill_blank", "填空测试：$1+1=$____。"),
        question("proof-a", 5, "free_response", proof)]
    for n in range(6, 10):
        body = f"分页测试第 {n} 题 START{n}。\n\n" + "\n\n".join(
            f"第 {line:02d} 小问：计算 $x^2+{line}$，请写出解题过程。" for line in range(1, 11)) + f"\n\nEND{n}"
        items.append(question(f"proof-{n}", n, "free_response", body))
    giant = "超长单题 START10。\n\n" + "\n\n".join(
        f"第 {line:03d} 小问：说明依据，验证 $a+b\\geq2\\sqrt{{ab}}$。" for line in range(1, 221)) + "\n\nEND10"
    items.append(question("proof-10", 10, "free_response", giant))
    extreme_latex = "\\dfrac{1}{" + "+".join(["x^2"] * 80) + "}=987654321"
    extreme = question("extreme-fraction", 11, "free_response", f"不可分超宽公式：${extreme_latex}$。")
    missing = question("missing-fixture", 12, stem="恢复出的缺题：$2+2$。")
    return items, extreme, missing, extreme_latex


def pdf_info(path):
    import pymupdf
    document = pymupdf.open(path)
    pages = []
    for index, page in enumerate(document):
        raw = page.get_text("rawdict")
        spans = [span for block in raw["blocks"] if "lines" in block for line in block["lines"] for span in line["spans"]]
        chars = [char for span in spans for char in span["chars"]]
        pages.append({"page": index + 1, "text": page.get_text(),
            "width": page.rect.width, "height": page.rect.height,
            "min_x_font_pt": min((span["size"] for span in spans if any(c["c"] == "x" for c in span["chars"])), default=None),
            "outside_page": [c for c in chars if c["bbox"][0] < -1 or c["bbox"][2] > page.rect.width + 1
                or c["bbox"][1] < -1 or c["bbox"][3] > page.rect.height + 1]})
        # A visual QA copy accompanies every page; this is PDF rendering, not product mutation.
        page.get_pixmap(matrix=pymupdf.Matrix(1.6, 1.6), alpha=False).save(OUT / f"{path.stem}-page-{index + 1}.png")
    document.close()
    return {"path": str(path), "pages": pages}


def run(port):
    OUT.mkdir(parents=True, exist_ok=True)
    items, extreme, missing, extreme_latex = fixtures()
    source_before = json.dumps(items, ensure_ascii=False, sort_keys=True)
    behavior = {"search_failed": True, "missing": "404"}
    report = {"checks": [], "pdfs": [], "errors": [], "forbidden_requests": [], "requests": []}
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(FrontendHandler, directory=str(ROOT / "frontend")))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as p:
            browser_path = next((str(v) for v in (Path(p.chromium.executable_path),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if v.is_file()), None)
            browser = p.chromium.launch(headless=True, **({"executable_path": browser_path} if browser_path else {}))
            context = browser.new_context(viewport={"width": 650, "height": 950})
            context.add_init_script("localStorage.setItem('qb-basket', JSON.stringify(['choice-a','missing-fixture']));")
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            def intercept(route):
                request = route.request
                parsed = urlparse(request.url)
                if parsed.hostname != "127.0.0.1" or request.method != "GET":
                    report["forbidden_requests"].append(f"{request.method} {request.url}")
                    route.abort()
                    return
                if parsed.path.startswith("/api/"):
                    report["requests"].append(parsed.path + "?" + parsed.query)
                if parsed.path == "/api/library":
                    if parse_qs(parsed.query).get("q") == ["服务失败"]:
                        if behavior["search_failed"]:
                            route.fulfill(status=503, json={"error": "测试：读取题库暂时失败"})
                        else:
                            route.fulfill(json=payload([items[0]]))
                    else:
                        route.fulfill(json=payload(items + [extreme]))
                elif parsed.path == "/api/library/missing-fixture":
                    if behavior["missing"] == "ok":
                        route.fulfill(json={"publication": missing})
                    else:
                        route.fulfill(status=int(behavior["missing"]), json={"error": "测试：这道题暂时不能读取"})
                elif parsed.path.startswith("/api/"):
                    raise AssertionError(f"Unexpected API request: {request.url}")
                else:
                    route.continue_()
            context.route("**/*", intercept)
            page.goto(f"http://127.0.0.1:{port}/library")
            page.wait_for_load_state("networkidle")
            expect(page.locator(".library-card")).to_have_count(len(items) + 1)

            # New search failure cannot silently present old results as a successful search.
            page.get_by_label("搜索题目", exact=True).fill("服务失败")
            expect(page.locator("#libraryLoadError")).to_be_visible()
            expect(page.locator("#libraryLoadError")).to_contain_text("测试：读取题库暂时失败")
            expect(page.locator("#libraryStatus")).to_be_visible()
            expect(page.locator("#libraryStatus")).to_contain_text("上一次读取的结果")
            expect(page.locator("#libraryStatus")).to_contain_text("尚未应用这次筛选")
            expect(page.locator(".library-card")).to_have_count(len(items) + 1)
            page.screenshot(path=str(OUT / "search-503-old-results.png"))
            behavior["search_failed"] = False
            page.locator("#retryLibraryLoad").click()
            expect(page.locator(".library-card")).to_have_count(1)
            expect(page.locator("#libraryLoadError")).to_be_hidden()
            expect(page.get_by_label("搜索题目", exact=True)).to_have_value("服务失败")
            report["checks"].append("503 search keeps labelled old results; retry updates the same query")

            # Missing selected card is explicit, retryable, and never silently omitted.
            page.locator("#basketButton").click()
            expect(page.locator("#printSheet")).to_be_visible()
            expect(page.locator("#printMissing")).to_be_visible()
            expect(page.locator(".print-question")).to_have_count(1)
            expect(page.locator("#printButton")).to_be_disabled()
            expect(page.locator("#basketCount")).to_have_text("2")
            page.screenshot(path=str(OUT / "missing-question-404.png"))
            behavior["missing"] = "ok"
            page.locator("#retryPrintMissing").click()
            expect(page.locator(".print-question")).to_have_count(2)
            expect(page.locator("#printMissing")).to_be_hidden()
            expect(page.locator("#printButton")).to_be_enabled()
            expect(page.locator("#basketCount")).to_have_text("2")
            report["checks"].append("missing card blocks printing; retry restores it without changing basket")
            page.keyboard.press("Escape")
            expect(page.locator("#printSheet")).to_be_hidden()
            expect(page.locator("#basketButton")).to_be_focused()

            # A subsequent unavailable result can be explicitly removed from the basket.
            behavior["missing"] = "503"
            page.locator("#basketButton").click()
            expect(page.locator("#printMissing")).to_be_visible()
            missing_remove = page.locator("#printMissing .print-missing-row").get_by_role("button", name=re.compile("^移出"))
            expect(missing_remove).to_have_count(1)
            expect(missing_remove).to_have_text("移出试题篮")
            missing_remove.click()
            expect(page.locator("#printMissing")).to_be_hidden()
            expect(page.locator("#basketCount")).to_have_text("1")
            expect(page.locator("#printButton")).to_be_enabled()
            report["checks"].append("explicitly removing unavailable card restores a consistent basket")
            page.keyboard.press("Escape")

            # Recover the full local library and build a multi-type paper from visible cards.
            page.get_by_label("搜索题目", exact=True).fill("")
            expect(page.locator(".library-card")).to_have_count(len(items) + 1)
            for item in items[1:5]:
                page.locator(f"#q-{item['id']}").get_by_role("button", name="加入试题篮", exact=True).click()
            page.locator("#basketButton").click()
            expect(page.locator(".print-question")).to_have_count(5)
            screen_long_text = page.locator('.print-question[data-question-id="choice-long"] .qb-stem-body').text_content()
            for width in (650, 390):
                page.set_viewport_size({"width": width, "height": 950})
                expect(page.locator(".print-overflow-hint").first).to_be_visible()
                active_steps = []
                report[f"focus-{width}"] = active_steps
                for _ in range(32):
                    page.keyboard.press("Tab")
                    step = page.evaluate("({inside:!!document.activeElement.closest('#printSheet'), tag:document.activeElement.tagName, classes:document.activeElement.className, open:document.querySelector('#printSheet').open, id:document.activeElement.id, label:document.activeElement.getAttribute('aria-label')})")
                    active_steps.append(step)
                    assert step["inside"], step
                report[f"focus-{width}"] = active_steps
                for _ in range(8):
                    page.keyboard.press("Shift+Tab")
                    assert page.evaluate("!!document.activeElement.closest('#printSheet')")
                page.screenshot(path=str(OUT / f"preview-hint-focus-{width}.png"))
            expect(page.get_by_role("button", name="第 3 题下移", exact=True)).to_be_disabled()
            expect(page.get_by_role("button", name="第 4 题上移", exact=True)).to_be_disabled()
            page.get_by_role("button", name="第 1 题下移", exact=True).click()
            assert page.locator(".print-question").evaluate_all("e => e.map(n => n.dataset.questionId)") == [
                "choice-long", "choice-a", "choice-fraction", "fill-a", "proof-a"]
            page.get_by_role("button", name="第 1 题下移", exact=True).click()
            report["checks"].append("modal preview contains Tab/Shift+Tab; same-type sorting boundaries remain correct")

            # Print media gets complete readable formulae; screen/source remain untouched.
            for width in (650, 1440):
                page.set_viewport_size({"width": width, "height": 950})
                page.emulate_media(media="print")
                page.evaluate("document.fonts.ready")
                path = OUT / f"fixed-formulas-{width}.pdf"
                page.pdf(path=str(path), format="A4", print_background=True, prefer_css_page_size=True)
                info = pdf_info(path)
                full_text = "\n".join(v["text"] for v in info["pages"])
                between = full_text.split("长公式：", 1)[1].split("长分数：", 1)[0]
                assert between.count("x") >= 28, between
                assert "0" in between, between
                assert "24681" in full_text.replace("\n", ""), full_text
                assert all(not v["outside_page"] for v in info["pages"]), info
                # Main math x glyphs must remain readable, not silently shrunk to tiny type.
                assert all(v["min_x_font_pt"] is None or v["min_x_font_pt"] >= 8.75 for v in info["pages"]), info
                report["pdfs"].append(info)
                page.emulate_media(media="screen")
            assert page.locator('.print-question[data-question-id="choice-long"] .qb-stem-body').text_content() == screen_long_text
            # PDF hooks may attach fitting metadata, but must preserve equation markup/content.
            assert page.locator('.print-question[data-question-id="choice-long"] .qb-stem-body .katex-html').text_content().endswith("=0")
            report["checks"].append("28-term formula tail and moderate fraction print fully at readable size; screen math content retained")
            page.keyboard.press("Escape")
            expect(page.locator("#basketButton")).to_be_focused()

            # Multi-page and greater-than-page questions preserve all problem statements.
            for item in items[5:]:
                page.locator(f"#q-{item['id']}").get_by_role("button", name="加入试题篮", exact=True).click()
            page.locator("#basketButton").click()
            expect(page.locator(".print-question")).to_have_count(len(items))
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            path = OUT / "fixed-pagination-220.pdf"
            page.pdf(path=str(path), format="A4", print_background=True, prefer_css_page_size=True)
            info = pdf_info(path)
            combined = "\n".join(v["text"] for v in info["pages"])
            assert len(info["pages"]) >= 3, info
            assert combined.count("说明依据") == 220, combined.count("说明依据")
            assert "END10" in combined.replace("\n", ""), combined[-500:]
            assert all(not v["outside_page"] for v in info["pages"]), info
            report["pdfs"].append(info)
            report["checks"].append("220 subquestions and the final marker survive multi-page printing")
            page.emulate_media(media="screen")
            page.locator("#clearBasket").click()
            expect(page.locator("#printSheet")).to_be_hidden()

            # Impossible layout gets a clear block and complete source fallback for shortcut printing.
            page.locator(f"#q-{extreme['id']}").get_by_role("button", name="加入试题篮", exact=True).click()
            page.locator("#basketButton").click()
            expect(page.locator("#printButton")).to_be_disabled()
            assert any(word in page.locator("#printSheet").inner_text() for word in ("太宽", "过宽", "放不下", "无法适配", "调整"))
            page.screenshot(path=str(OUT / "extreme-formula-print-blocked.png"))
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            path = OUT / "extreme-shortcut-fallback.pdf"
            page.pdf(path=str(path), format="A4", print_background=True, prefer_css_page_size=True)
            info = pdf_info(path)
            text = "".join(v["text"] for v in info["pages"]).replace("\n", "").replace(" ", "")
            assert "987654321" in text, text[-500:]
            assert text.count("x^2") >= 80, {"terms": text.count("x^2"), "tail": text[-500:]}
            assert extreme_latex in text, "Shortcut PDF did not preserve the complete LaTeX source"
            assert any(word in text for word in ("太宽", "过宽", "无法", "排版", "LaTeX", "原文")), text[:500]
            report["pdfs"].append(info)
            report["checks"].append("unfittable formula blocks ordinary print; shortcut PDF retains every LaTeX term and end marker")
            page.emulate_media(media="screen")
            page.keyboard.press("Escape")
            expect(page.locator("#basketButton")).to_be_focused()
            assert json.dumps(items, ensure_ascii=False, sort_keys=True) == source_before
            assert not report["errors"], report["errors"]
            assert not report["forbidden_requests"], report["forbidden_requests"]
            browser.close()
            report["result"] = "PASS"
    except Exception as error:
        report["result"] = "FAIL"
        report["failure"] = str(error)
        try:
            page.screenshot(path=str(OUT / "failure.png"))
            (OUT / "failure-dom.html").write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        raise
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": report["checks"], "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="run isolated browser/PDF regression")
    parser.add_argument("--port", type=int, default=8985)
    args = parser.parse_args()
    run(args.port) if args.run else parser.print_help()
