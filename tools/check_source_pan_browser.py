"""Offline real-pointer regression for the library's original-paper canvas.

Serves checkout frontend only, with two synthetic SVG pages. Rejects every
write and non-local request; never touches the installed app, OCR or user data.
Use --help first, then --run. This server owns only the supplied free port.
"""

import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
from threading import Thread
import traceback
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from check_library_preview_browser import FrontendHandler, fixture_item

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "tmp" / "source-pan-browser"


def synthetic_page(index):
    lines = []
    for x in range(50, 601, 50):
        lines.append(f'<path d="M{x} 0V900" stroke="#d4dedd"/><text x="{x+3}" y="30">x{x}</text>')
    for y in range(50, 901, 50):
        lines.append(f'<path d="M0 {y}H650" stroke="#d4dedd"/><text x="8" y="{y-4}">y{y}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="650" height="900" viewBox="0 0 650 900">'
        f'<rect width="650" height="900" fill="white"/><g font-family="sans-serif" font-size="12">'
        + "".join(lines) + f'<text x="180" y="420" font-size="34" fill="#00635d">OFFLINE PAGE {index+1}</text>'
        '<circle cx="325" cy="500" r="35" fill="#ffe3a4" stroke="#bb7200"/></g></svg>').encode()


def geometry(viewport):
    return viewport.evaluate("""v => ({left:v.scrollLeft, top:v.scrollTop, width:v.clientWidth,
        height:v.clientHeight, maxX:v.scrollWidth-v.clientWidth, maxY:v.scrollHeight-v.clientHeight,
        panning:v.classList.contains('panning')})""")


def pointer_point(viewport, target):
    # Measure the element the caller is actually holding. Re-typing it as a CSS
    # selector is how ":nth-child(3)" ended up asking for a node that is not the
    # second crop segment, and the failure read as a missing element.
    return viewport.evaluate("""(v, el) => {
        const a=v.getBoundingClientRect(), b=el.getBoundingClientRect();
        const left=Math.max(a.left+20,b.left+10), right=Math.min(a.left+v.clientWidth-20,b.right-10);
        const top=Math.max(a.top+20,b.top+10), bottom=Math.min(a.top+v.clientHeight-20,b.bottom-10);
        if (right<=left || bottom<=top) throw Error('Target not visible in canvas');
        return {x:left+(right-left)*.43,y:top+(bottom-top)*.42};
    }""", target.element_handle())


def relative_point(target, point):
    return target.evaluate("""(t,p) => {const r=t.getBoundingClientRect();return {
        x:(p.x-r.left)/r.width,y:(p.y-r.top)/r.height,width:r.width,height:r.height};}""", point)


def wheel_anchor(page, viewport, target, zoom, delta, allow_boundary_limit=False):
    point = pointer_point(viewport, target)
    before = relative_point(target, point)
    old_zoom = zoom.inner_text()
    page.mouse.move(point["x"], point["y"])
    page.keyboard.down("Control")
    page.mouse.wheel(0, delta)
    page.keyboard.up("Control")
    expect(zoom).not_to_have_text(old_zoom)
    page.wait_for_timeout(60)
    after = relative_point(target, point)
    # Express the displaced paper point in screen pixels, allowing subpixel rounding.
    signed = {"x": (before["x"]-after["x"])*after["width"],
              "y": (before["y"]-after["y"])*after["height"]}
    error = {axis:abs(value) for axis,value in signed.items()}
    limits=geometry(viewport); limited=[]
    for axis,position,maximum in (("x","left","maxX"),("y","top","maxY")):
        if error[axis] <= 3:continue
        at_bound=(signed[axis]>0 and abs(limits[position]-limits[maximum])<1) or (signed[axis]<0 and limits[position]<1)
        if allow_boundary_limit and at_bound:limited.append(axis)
        else:raise AssertionError({"anchor_error_px":error,"before":before,"after":after,"scroll_limits":limits})
    return {"from": old_zoom, "to": zoom.inner_text(), "anchor_error_px": error,
            "boundary_limited_axes":limited,"scroll_limits":limits}


def pan_and_release(page, viewport):
    viewport.evaluate("v=>{v.scrollLeft=(v.scrollWidth-v.clientWidth)/2;v.scrollTop=(v.scrollHeight-v.clientHeight)/3}")
    before = geometry(viewport)
    assert before["maxX"] >= 100 and before["maxY"] >= 100, before
    rect = viewport.bounding_box()
    point = {"x":rect["x"]+rect["width"]*.55, "y":rect["y"]+rect["height"]*.48}
    page.mouse.move(**point)
    page.mouse.down()
    page.mouse.move(point["x"]+55, point["y"]+45, steps=8)
    moved = geometry(viewport)
    page.mouse.up()
    assert abs(moved["left"]-(before["left"]-55)) <= 2, {"before":before,"after":moved}
    assert abs(moved["top"]-(before["top"]-45)) <= 2, {"before":before,"after":moved}
    released = geometry(viewport)
    page.mouse.move(point["x"]+5, point["y"]+5, steps=5)
    assert geometry(viewport) == released, {"released":released,"after_move":geometry(viewport)}
    assert not released["panning"], released
    return {"before":before,"dragged":moved,"released":released}


def run(port):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "Port already occupied; existing server is not ours"
    item = fixture_item("source-two-pages", 1, stem="合成原卷，检查鼠标缩放与拖动。")
    item["content"]["sources"] = [{"page_idx":i,"bbox":[80,70,920,930],"type":"text"} for i in (0,1)]
    payload = {"items":[item],"total":1,"features":{},"facets":{"sources":[],"types":{"single_choice":1},"answers":{},"reviews":{},"tags":[]}}
    server = ThreadingHTTPServer(("127.0.0.1",port), partial(FrontendHandler,directory=str(ROOT / "frontend")))
    thread = Thread(target=server.serve_forever,daemon=True)
    thread.start()
    report = {"passed":[],"failures":[],"widths":{},"page_errors":[],"forbidden_requests":[],"server_port":port,
              "synthetic_original":True,"user_data_touched":False,"worker_started":False}
    fail_pages = set()
    try:
        with sync_playwright() as pw:
            executable = next((str(p) for p in (Path(pw.chromium.executable_path),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()),None)
            browser = pw.chromium.launch(headless=True,**({"executable_path":executable} if executable else {}))
            context = browser.new_context(viewport={"width":1440,"height":950})
            def route_request(route):
                request=route.request; parsed=urlparse(request.url)
                if parsed.hostname != "127.0.0.1" or request.method != "GET":
                    report["forbidden_requests"].append({"method":request.method,"url":request.url})
                    route.abort(); return
                if parsed.path == "/api/library": route.fulfill(json=payload)
                elif parsed.path == "/api/library/source-two-pages":
                    route.fulfill(json={"publication":item,"history":[item],"related_sources":[],"possible_sources":[]})
                elif "/pages/" in parsed.path and parsed.path.endswith("/preview"):
                    index=int(parsed.path.split("/")[-2])
                    if index in fail_pages: route.fulfill(status=404,body="Synthetic page unavailable")
                    else: route.fulfill(body=synthetic_page(index),content_type="image/svg+xml")
                elif parsed.path.startswith("/api/"):
                    report["forbidden_requests"].append({"method":request.method,"url":request.url})
                    route.abort()
                else: route.continue_()
            context.route("**/*",route_request)
            page=context.new_page()
            page.on("pageerror",lambda e:report["page_errors"].append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/library")
            expect(page.locator(".library-card")).to_have_count(1)
            def open_source():
                page.locator(".library-card").get_by_role("button",name="查看出处",exact=True).click()
                expect(page.locator("#sourceDialog")).to_be_visible()
                page.locator("#sourceDialog").evaluate("d=>Promise.all(d.getAnimations().map(a=>a.finished))")
            def wait_images(count=2):
                expect(page.locator(".source-surface img")).to_have_count(count)
                page.wait_for_function("[...document.querySelectorAll('.source-surface img')].every(i=>i.complete&&i.naturalWidth>0)")
            for width in (1440,650,390):
                result={}; report["widths"][str(width)]=result
                try:
                    page.set_viewport_size({"width":width,"height":950}); open_source(); wait_images()
                    viewport=page.locator("#sourcePages"); zoom=page.locator("#sourceZoom")
                    expect(zoom).to_have_text("100%")
                    expect(page.locator("#sourceGestureHint")).to_be_visible()
                    first=page.locator(".source-surface").nth(0)
                    # Tag the actual DOM image so resizing must preserve it and its loaded state.
                    page.locator(".source-surface img").first.evaluate("i=>i.dataset.qaIdentity='preserved'")
                    result["zoom_in_anchor"]=wheel_anchor(page,viewport,first,zoom,-150)
                    expect(page.locator(".source-surface img").first).to_have_attribute("data-qa-identity","preserved")
                    page.locator("#sourceZoomIn").click(); page.locator("#sourceZoomIn").click()
                    result["pan_and_release"]=pan_and_release(page,viewport)
                    result["zoom_out_anchor"]=wheel_anchor(page,viewport,first,zoom,80)
                    assert page.locator(".source-surface img").evaluate_all("imgs=>imgs.every(i=>i.draggable===false)"), "Native image drag still enabled"
                    # A held drag must end on close, even when release arrives after the dialog closes.
                    rect=viewport.bounding_box(); page.mouse.move(rect["x"]+70,rect["y"]+70); page.mouse.down()
                    page.mouse.move(rect["x"]+60,rect["y"]+60,steps=3); page.keyboard.press("Escape"); page.mouse.up()
                    expect(page.locator("#sourceDialog")).not_to_be_visible(); open_source(); wait_images()
                    stable=geometry(viewport); page.mouse.move(rect["x"]+90,rect["y"]+90,steps=4)
                    assert geometry(viewport)==stable, "A closed gesture leaked into the reopened dialog"
                    # Switching modes through real keyboard while the pointer is held cancels capture.
                    page.mouse.move(rect["x"]+60,rect["y"]+60); page.mouse.down()
                    page.locator("#sourceWholePage").focus(); page.keyboard.press("Enter"); page.mouse.up(); wait_images()
                    expect(zoom).to_have_text("100%"); assert not geometry(viewport)["panning"]
                    # Use page 2 so cross-page mouse anchoring cannot incorrectly measure page 1.
                    second=page.locator(".source-surface").nth(1)
                    second.evaluate("s=>s.scrollIntoView({block:'start'})")
                    result["page_2_anchor"]=wheel_anchor(page,viewport,second,zoom,-100)
                    page.locator("#sourceFit").click()
                    fitted=geometry(viewport); result["fit"]=fitted
                    assert fitted["left"]==0 and fitted["top"]==0, fitted
                    frame=first.bounding_box()
                    assert frame["width"] <= fitted["width"]+1 and frame["height"]+26 <= fitted["height"]+2, {"frame":frame,"viewport":fitted}
                    # Repeated wheel events must clamp instead of overflowing zoom or browser zoom.
                    rect=viewport.bounding_box(); page.mouse.move(rect["x"]+50,rect["y"]+50)
                    page.keyboard.down("Control")
                    for _ in range(12): page.mouse.wheel(0,-800)
                    page.keyboard.up("Control"); expect(zoom).to_have_text("400%")
                    expect(page.locator("#sourceZoomIn")).to_be_disabled()
                    page.keyboard.down("Control")
                    for _ in range(12): page.mouse.wheel(0,800)
                    page.keyboard.up("Control"); expect(zoom).to_have_text("25%")
                    expect(page.locator("#sourceZoomOut")).to_be_disabled()
                    assert page.evaluate("window.visualViewport.scale") == 1
                    page.locator("#sourceFit").click()
                    page.screenshot(path=str(OUTPUT/f"source-fit-{width}.png"))
                    page.keyboard.press("Escape")
                    report["passed"].append(f"{width}px: Ctrl wheel anchors both pages, left drag, release/close/mode cleanup, native drag prevention, fit and zoom bounds")
                except Exception as e:
                    report["failures"].append({"width":width,"error":str(e),"traceback":traceback.format_exc()})
                    page.screenshot(path=str(OUTPUT/f"failure-{width}.png")); page.mouse.up(); page.keyboard.up("Control"); page.keyboard.press("Escape")
            try:
                # A distinct original URL avoids Chromium reusing a decoded image
                # within one document before a new request reaches interception.
                page.set_viewport_size({"width":650,"height":950}); fail_pages.add(1)
                item["document_id"]="offline-partly-unavailable"
                page.reload(); expect(page.locator(".library-card")).to_have_count(1); open_source()
                expect(page.locator(".source-unavailable")).to_have_count(1); wait_images(1)
                expect(page.locator("#sourceZoomIn")).to_be_enabled()
                page.keyboard.press("Escape"); fail_pages.add(0)
                item["document_id"]="offline-fully-unavailable"
                page.reload(); expect(page.locator(".library-card")).to_have_count(1); open_source()
                expect(page.locator(".source-unavailable")).to_have_count(2)
                expect(page.locator("#sourceZoomIn")).to_be_disabled(); expect(page.locator("#sourceZoomOut")).to_be_disabled()
                expect(page.locator("#sourceFit")).to_be_disabled()
                viewport=page.locator("#sourcePages"); rect=viewport.bounding_box(); page.mouse.move(rect["x"]+60,rect["y"]+60)
                page.keyboard.down("Control"); page.mouse.wheel(0,-300); page.keyboard.up("Control")
                expect(page.locator("#sourceZoom")).to_have_text("100%")
                page.mouse.down(); page.mouse.move(rect["x"]+40,rect["y"]+40,steps=3); page.mouse.up()
                assert not geometry(viewport)["panning"]
                page.screenshot(path=str(OUTPUT/"source-unavailable-650.png"))
                report["passed"].append("One failed page preserves the usable page; all failed pages disable canvas gestures and provide an explanation")
            except Exception as e:
                report["failures"].append({"case":"load_failure","error":str(e),"traceback":traceback.format_exc()})
                page.screenshot(path=str(OUTPUT/"failure-load.png"))
            context.close(); browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        report["server_stopped"]=True
        (OUTPUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    assert not report["failures"], report["failures"]
    assert not report["page_errors"] and not report["forbidden_requests"], report
    print("Original-paper pointer checks passed; isolated server stopped",flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--port",type=int,default=8985)
    args=parser.parse_args()
    if not args.run: parser.error("Choose --run")
    run(args.port)
