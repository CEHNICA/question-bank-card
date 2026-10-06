"""Check short fractions do not become scroll containers in print preview.

Only synthetic frontend responses on localhost are used; no real data, worker,
cloud or printer. Run --run; screenshots and a PDF go under checkout/tmp.
"""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect
from check_library_preview_browser import FrontendHandler, fixture_item

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tmp" / "print-scroll-browser"


def metrics(field):
    return field.evaluate("""e => ({width:e.clientWidth,scrollWidth:e.scrollWidth,height:e.clientHeight,
        scrollHeight:e.scrollHeight,overflowX:getComputedStyle(e).overflowX,overflowY:getComputedStyle(e).overflowY,
        tabindex:e.getAttribute('tabindex'),marker:e.dataset.printOverflow || null,
        math:[...e.querySelectorAll('.qb-math')].map(m => ({
            size:parseFloat(getComputedStyle(m.querySelector('.katex')).fontSize),
            fitted:m.querySelector('.katex').style.getPropertyValue('--print-math-size') || null,
            overflow:m.querySelector('.katex').dataset.examMathOverflow || null,
            base:[...m.querySelectorAll('.katex-html')].map(h => h.getBoundingClientRect().width)})),
        katex:[...e.querySelectorAll('.katex-html')].map(m => {const a=m.getBoundingClientRect(),b=e.getBoundingClientRect();
          return {top:a.top-b.top,bottom:a.bottom-b.top,height:a.height};})})""")


def run(port, probe=False):
    OUT.mkdir(parents=True, exist_ok=True)
    short = fixture_item("short", 1, stem="经过点 $(3,1)$，斜率为 $\\dfrac{1}{2}$ 的直线方程是____。")
    tall = fixture_item("tall", 2, stem="短分数：$\\dfrac{1+x^2}{1+x^2}$。")
    # 纸面版心宽 178mm ≈ 655px，排版时公式只缩到 12px 为止，再宽才让它横着滚。
    # 三档分别落在三个分支上：放得下、缩一缩还看得清、再也缩不下去。
    tight_stem = "\\dfrac{1}{" + "+".join(["x^2"] * 24) + "}"
    long_stem = "\\dfrac{1}{" + "+".join(["x^2"] * 40) + "}=24681"
    tight = fixture_item("tight", 3, stem="刚好多一点：$" + tight_stem + "$。")
    long = fixture_item("long", 4, stem="长分数：$" + long_stem + "$。")
    items = [short, tall, tight, long]
    body = {"items": items, "total": 4, "features": {}, "facets": {"sources": [], "types": {"single_choice":4}}}
    by_id = {item["id"]: item for item in items}
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(FrontendHandler, directory=str(ROOT / "frontend")))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    report = {"fields":{},"errors":[],"forbidden":[]}
    try:
        with sync_playwright() as p:
            executable = next((str(v) for v in (Path(p.chromium.executable_path),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if v.is_file()), None)
            browser = p.chromium.launch(headless=True, **({"executable_path":executable} if executable else {}))
            context = browser.new_context(viewport={"width":650,"height":1050})
            def route_request(route):
                request = route.request
                parsed = urlparse(request.url)
                # 打开卷子会先核对一遍选题快照（POST）。不答它，openPrint 会把
                # 全部选题算成「未载入」，卷面上一道题都没有。
                if parsed.path == "/api/library/batch" and request.method == "POST":
                    ids = (request.post_data_json or {}).get("ids", [])
                    route.fulfill(json={"items": [by_id[i] for i in ids if i in by_id],
                                        "missing": [{"id": i, "reason": "not_found"} for i in ids if i not in by_id]})
                    return
                if parsed.hostname != "127.0.0.1" or request.method != "GET":
                    report["forbidden"].append(f"{request.method} {request.url}")
                    route.abort()
                elif parsed.path == "/api/library":
                    route.fulfill(json=body)
                elif parsed.path.startswith("/api/"):
                    raise AssertionError(request.url)
                else:
                    route.continue_()
            context.route("**/*", route_request)
            page = context.new_page()
            page.on("pageerror", lambda e: report["errors"].append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/library")
            page.wait_for_load_state("networkidle")
            for item in items:
                page.locator(f"#q-{item['id']}").get_by_role("button",name="加入试题篮",exact=True).click()
            # 「组卷预览」在试题篮抽屉里，篮没拉开就点不到（真人也是先拉把手）。
            page.locator("#basketHandle").click()
            expect(page.locator("#basketPanel")).to_be_visible()
            # 选题是异步读的：角标还没跳到 3 就点「组卷预览」，打开的是空卷。
            expect(page.locator("#basketHandleCount")).to_have_text(str(len(items)), timeout=10000)
            page.locator("#basketButton").click()
            # 打开卷子要先核对一遍选题快照，这一步是网络往返；5 秒不够就是还没排完。
            expect(page.locator(".print-question")).to_have_count(4, timeout=20000)
            for width in (650,390,1440):
                page.set_viewport_size({"width":width,"height":1050})
                page.wait_for_timeout(100)
                fields = {}
                for key in ("short","tall","tight","long"):
                    field = page.locator(f'.print-question[data-question-id="{key}"] .qb-stem-body')
                    fields[key] = metrics(field)
                report["fields"][str(width)] = fields
                page.screenshot(path=str(OUT / f"fractions-{width}-{'probe' if probe else 'fixed'}.png"))
                if probe:
                    continue
                for key in ("short","tall"):
                    value = fields[key]
                    assert value["width"] == value["scrollWidth"], value
                    assert value["overflowX"] == value["overflowY"] == "visible", value
                    assert value["tabindex"] is None and value["marker"] is None, value
                # 放得下就不动，放不下就缩，但缩完还得看得清（≥12px）。
                tight = fields["tight"]
                assert tight["math"] and all(m["fitted"] is not None for m in tight["math"]), tight
                assert all(m["overflow"] is None for m in tight["math"]), tight
                assert all(m["size"] >= 12 for m in tight["math"]), ("A formula shrunk to fit must stay readable", tight)
                assert tight["width"] == tight["scrollWidth"] and tight["marker"] is None, tight
                long_field = page.locator('.print-question[data-question-id="long"] .qb-stem-body')
                assert fields["long"]["scrollWidth"] > fields["long"]["width"], fields["long"]
                assert fields["long"]["math"][0]["overflow"] == "1", fields["long"]
                expect(long_field).to_have_attribute("tabindex","0")
                expect(long_field).to_have_attribute("data-print-overflow","1")
                expect(page.locator(".print-overflow-hint")).to_have_count(1)
                assert fields["long"]["scrollHeight"] <= fields["long"]["height"] + 1, fields["long"]
                long_field.focus()
                for _ in range(6):
                    page.keyboard.press("ArrowRight")
                page.wait_for_timeout(400)
                keyboard_scroll = long_field.evaluate("e=>e.scrollLeft")
                assert keyboard_scroll > 0, keyboard_scroll
                long_field.hover()
                bounds = long_field.bounding_box()
                page.mouse.move(bounds["x"] + bounds["width"] / 2, bounds["y"] + bounds["height"] / 2)
                page.mouse.wheel(3000,0)
                page.wait_for_function("e=>Math.abs(e.scrollWidth-e.clientWidth-e.scrollLeft)<=2",arg=long_field.element_handle(),timeout=2000)
                page.screenshot(path=str(OUT / f"fraction-tail-{width}.png"))
                report["fields"][str(width)]["long"]["keyboard_scroll"] = keyboard_scroll
            if not probe:
                page.emulate_media(media="print")
                assert long_field.evaluate("e=>getComputedStyle(e).overflowX==='visible' && getComputedStyle(e).outlineStyle==='none'")
                path = OUT / "fractions-print.pdf"
                page.pdf(path=str(path),format="A4",print_background=True,prefer_css_page_size=True)
                import pymupdf
                document = pymupdf.open(path)
                text = "".join(p.get_text() for p in document).replace("\n","")
                assert "24681" in text, text
                for index, pdf_page in enumerate(document):
                    pdf_page.get_pixmap(matrix=pymupdf.Matrix(1.6,1.6),alpha=False).save(OUT / f"fractions-print-page-{index+1}.png")
                report["pdf"] = {"pages":len(document),"tail_present":True,"screen_focus_outline_hidden":True}
                document.close()
                page.emulate_media(media="screen")
                page.locator("#printTitle").focus()
                short_focus = []
                for _ in range(18):
                    page.keyboard.press("Tab")
                    short_focus.append(page.evaluate("!!document.activeElement.closest('[data-question-id=short], [data-question-id=tall]') && document.activeElement.classList.contains('qb-stem-body')"))
                assert not any(short_focus), short_focus
                report["short_fields_in_tab_order"] = False
            browser.close()
            assert not report["errors"] and not report["forbidden"], report
            report["result"] = "PROBE" if probe else "PASS"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        (OUT / ("probe.json" if probe else "report.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--probe",action="store_true",help="capture current behavior without passing assertions")
    parser.add_argument("--port",type=int,default=8985)
    args = parser.parse_args()
    run(args.port,args.probe) if args.run else parser.print_help()
