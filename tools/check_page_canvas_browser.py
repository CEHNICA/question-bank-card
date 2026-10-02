"""Offline pointer regression for original-paper view/edit and comparison viewer.

--run creates only checkout/tmp/page-canvas-browser records and two synthetic
pages, starts Django without any worker, blocks all browser writes and non-local
requests, and stops the exact owned server. No OCR, installed app or user data.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import traceback
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
import check_edit_assistance_browser as seed_helper
from check_source_pan_browser import geometry, pan_and_release, wheel_anchor

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/"tmp"/"page-canvas-browser"


def settle_modal(page, selector):
    expect(page.locator(selector)).to_be_visible()
    page.locator(selector).evaluate("d=>Promise.all(d.getAnimations().map(a=>a.finished))")
    page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")


def boxes(page):
    return page.locator("#pageStage .edit-box:not(.drawing)").evaluate_all("""ns=>ns.map(n=>({index:n.dataset.boxIndex,
        left:n.style.left,top:n.style.top,width:n.style.width,height:n.style.height}))""")


def seed():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    os.environ["QB_DATABASE"]=str(OUTPUT/"db.sqlite3")
    os.environ["QB_DATA_ROOT"]=str(OUTPUT/"data")
    seed_helper.OUTPUT=OUTPUT
    seed_helper.seed()
    fixture=json.loads((OUTPUT/"fixture.json").read_text(encoding="utf-8"))
    from core.models import Question
    first=Question.objects.get(pk=fixture["first"])
    # Tall, two-page crop exercises vertical panning and anchoring on page 2.
    first.regions=[{"page_idx":i,"bbox":[80,70,920,930]} for i in (0,1)]
    first.regions_auto=first.regions
    first.save(update_fields=["regions","regions_auto"])
    return fixture


def check(url, fixture):
    before=seed_helper.question_snapshot()
    failed_pages=set()
    report={"passed":[],"failures":[],"widths":{},"page_errors":[],"forbidden_requests":[],"native_dialogs":[],
        "synthetic_original":True,"user_data_touched":False,"worker_started":False}
    with sync_playwright() as pw:
        executable=next((str(p) for p in (Path(pw.chromium.executable_path),
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if p.is_file()),None)
        browser=pw.chromium.launch(headless=True,**({"executable_path":executable} if executable else {}))
        context=browser.new_context(viewport={"width":1440,"height":1050})
        context.add_init_script("localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0')")
        def readonly(route):
            req=route.request
            if urlparse(req.url).hostname not in ("127.0.0.1","localhost") or req.method not in ("GET","HEAD"):
                report["forbidden_requests"].append(f"{req.method} {req.url}");route.abort()
            elif "/pages/" in urlparse(req.url).path and urlparse(req.url).path.endswith("/preview") and int(urlparse(req.url).path.split("/")[-2]) in failed_pages:
                route.fulfill(status=500,body="Offline original unavailable")
            else: route.continue_()
        context.route("**/*",readonly)
        page=context.new_page()
        page.on("pageerror",lambda e:report["page_errors"].append(str(e)))
        page.on("dialog",lambda d:(report["native_dialogs"].append(d.type),d.dismiss()))
        def load():
            page.goto(url+"/?paper="+fixture["paper"])
            page.wait_for_load_state("networkidle")
            expect(page.locator(f'.card[data-id="{fixture["first"]}"]')).to_be_visible()
        def first_card():return page.locator(f'.card[data-id="{fixture["first"]}"]')
        def open_paper():
            page.locator("#paperMenu>summary").click();page.locator("#viewOriginalPaper").click()
            settle_modal(page,"#pageDialog")
            page.wait_for_function("(()=>{const i=document.querySelector('#pageStage .stage-surface img');return i&&i.complete&&i.naturalWidth>0})()")
        def open_regions():
            first_card().get_by_role("button",name="调整范围",exact=True).click()
            settle_modal(page,"#pageDialog")
            page.wait_for_function("(()=>{const i=document.querySelector('#pageStage .stage-surface img');return i&&i.complete&&i.naturalWidth>0})()")
        def open_viewer():
            first_card().locator(".source-note").click()
            settle_modal(page,"#viewerDialog")
            page.wait_for_function("[...document.querySelectorAll('#viewerCrop img')].every(i=>i.complete&&i.naturalWidth>0)")
        for width in (1440,650,390):
            result={};report["widths"][str(width)]=result
            try:
                page.set_viewport_size({"width":width,"height":1050});load();open_paper()
                viewport=page.locator("#pageStage");zoom=page.locator("#pageZoomLevel");surface=page.locator("#pageStage .stage-surface")
                expect(page.locator("#pageDialogSave")).not_to_be_visible();assert boxes(page)==[]
                expect(page.locator("#pageCanvasHint")).to_contain_text("鼠标左键")
                page.locator("#pageZoomWidth").click();expect(zoom).to_have_text("100%")
                result["view_zoom_in_anchor"]=wheel_anchor(page,viewport,surface,"#pageStage .stage-surface",zoom,-120,allow_boundary_limit=True)
                for _ in range(3):page.locator("#pageZoomIn").click()
                result["view_pan_and_release"]=pan_and_release(page,viewport)
                result["view_zoom_out_anchor"]=wheel_anchor(page,viewport,surface,"#pageStage .stage-surface",zoom,120,allow_boundary_limit=True)
                assert boxes(page)==[],"Read-only drag unexpectedly created a region"
                assert surface.locator("img").evaluate("i=>!i.draggable"),"Native image drag enabled"
                # Hold left drag while navigating by the real focused button.
                rect=viewport.bounding_box();page.mouse.move(rect["x"]+80,rect["y"]+80);page.mouse.down()
                page.mouse.move(rect["x"]+65,rect["y"]+65,steps=4)
                page.locator("#pageNext").focus();page.keyboard.press("Enter");page.mouse.up()
                expect(page.locator("#pageNumberInput")).to_have_value("2")
                page.wait_for_function("document.querySelector('#pageStage img').complete")
                assert not geometry(viewport)["panning"]
                stable=geometry(viewport);page.mouse.move(rect["x"]+95,rect["y"]+95,steps=4)
                assert geometry(viewport)==stable,"Page switching retained a previous drag"
                result["view_page_2_anchor"]=wheel_anchor(page,viewport,surface,"#pageStage .stage-surface",zoom,-120,allow_boundary_limit=True)
                # Closing with mouse held clears capture and all pending gestures.
                page.mouse.move(rect["x"]+70,rect["y"]+70);page.mouse.down();page.keyboard.press("Escape");page.mouse.up()
                expect(page.locator("#pageDialog")).not_to_be_visible();open_paper()
                stable=geometry(viewport);page.mouse.move(rect["x"]+90,rect["y"]+90,steps=4)
                assert geometry(viewport)==stable,"Reopening retained a previous drag"
                assert boxes(page)==[]
                page.locator("#pageZoomFit").click();page.wait_for_timeout(70)
                fitted=geometry(viewport);frame=surface.bounding_box();result["view_fit"]={"viewport":fitted,"surface":frame}
                assert frame["width"]<=fitted["width"]+1 and frame["height"]<=fitted["height"]+1, result["view_fit"]
                page.screenshot(path=str(OUTPUT/f"original-fit-{width}.png"));page.locator("#pageDialogClose").click()
                report["passed"].append(f"{width}px: read-only original zoom anchors, XY left-pan, fit, next-page and close cleanup, no edits")

                open_regions();expect(page.locator("#pageDialogSave")).to_be_visible()
                page.locator("#pageZoomWidth").click();expect(zoom).to_have_text("100%")
                for _ in range(3):page.locator("#pageZoomIn").click()
                original_boxes=boxes(page)
                viewport.focus();page.keyboard.down("Space")
                result["edit_space_pan"]=pan_and_release(page,viewport);page.keyboard.up("Space")
                assert boxes(page)==original_boxes,"Space+left pan changed original boxes"
                viewport.evaluate("v=>{v.scrollLeft=(v.scrollWidth-v.clientWidth)/2;v.scrollTop=(v.scrollHeight-v.clientHeight)/3}")
                start=geometry(viewport);rect=viewport.bounding_box();x=rect["x"]+rect["width"]*.6;y=rect["y"]+rect["height"]*.5
                page.mouse.move(x,y);page.mouse.down(button="middle");page.mouse.move(x+40,y+35,steps=5);page.mouse.up(button="middle")
                moved=geometry(viewport)
                assert abs(moved["left"]-(start["left"]-40))<=2 and abs(moved["top"]-(start["top"]-35))<=2,{"before":start,"after":moved}
                assert boxes(page)==original_boxes,"Middle-pan changed boxes"
                page.locator("#pageZoomWidth").click();expect(zoom).to_have_text("100%");viewport.evaluate("v=>v.scrollTo(0,0)")
                # Empty strip to the left of the existing region: ordinary left drag
                # still creates a correctly scaled region rather than panning.
                rect=surface.bounding_box()
                a={"x":rect["x"]+rect["width"]*.02,"y":rect["y"]+rect["height"]*.04}
                b={"x":rect["x"]+rect["width"]*.075,"y":rect["y"]+rect["height"]*.11}
                page.mouse.move(**a);page.mouse.down();page.mouse.move(b["x"],b["y"],steps=7)
                held_dimensions=surface.bounding_box();held_zoom=zoom.inner_text()
                page.keyboard.press("0");page.keyboard.press("w")
                page.evaluate("()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
                assert surface.bounding_box()==held_dimensions and zoom.inner_text()==held_zoom,"0/W resized the canvas during a draw"
                page.mouse.up()
                expect(page.locator("#pageStage .edit-box:not(.drawing)")).to_have_count(len(original_boxes)+1)
                drawn=boxes(page)[-1];result["drawn_region"]=drawn
                for key,target in (("left",2),("top",4),("width",5.5),("height",7)):
                    assert abs(float(drawn[key].rstrip("%"))-target)<.15,{"drawn":drawn,"target":target,"key":key}
                after_draw=boxes(page)
                # Cancel during an in-progress rectangle removes only that rectangle.
                page.mouse.move(a["x"],a["y"]+rect["height"]*.15);page.mouse.down()
                page.mouse.move(b["x"],b["y"]+rect["height"]*.15,steps=4)
                viewport.dispatch_event("pointercancel",{"pointerId":1,"pointerType":"mouse","bubbles":True});page.mouse.up()
                expect(page.locator("#pageStage .drawing")).to_have_count(0)
                assert boxes(page)==after_draw,"Cancelled draw left a region behind"
                page.screenshot(path=str(OUTPUT/f"edit-left-draw-{width}.png"))
                page.locator("#pageDialogClose").click();open_regions()
                assert boxes(page)==original_boxes,"Canceling editor persisted unsaved boxes"
                page.locator("#pageDialogClose").click()
                report["passed"].append(f"{width}px: Space+left and middle pan preserve all regions; ordinary left draws correctly; 0/W cannot resize active draw; pointercancel and cancel discard new region")

                open_viewer();viewport=page.locator("#viewerSource");zoom=page.locator("#zoomLevel")
                page.locator("#zoomWidth").click()
                first=page.locator("#viewerCrop .crop-seg").first
                result["comparison_zoom_in_anchor"]=wheel_anchor(page,viewport,first,"#viewerCrop .crop-seg",zoom,-120)
                for _ in range(3):page.locator("#zoomIn").click()
                result["comparison_pan_and_release"]=pan_and_release(page,viewport)
                result["comparison_zoom_out_anchor"]=wheel_anchor(page,viewport,first,"#viewerCrop .crop-seg",zoom,120)
                second=page.locator("#viewerCrop .crop-seg").nth(1);second.evaluate("s=>s.scrollIntoView({block:'start'})")
                result["comparison_page_2_anchor"]=wheel_anchor(page,viewport,second,"#viewerCrop .crop-seg:nth-child(3)",zoom,-120)
                rect=viewport.bounding_box();page.mouse.move(rect["x"]+65,rect["y"]+65);page.mouse.down()
                page.mouse.move(rect["x"]+50,rect["y"]+50,steps=3)
                page.locator("#viewerNext").focus();page.keyboard.press("ArrowRight");page.mouse.up()
                expect(page.locator("#viewerTitle")).to_contain_text("2")
                assert not geometry(viewport)["panning"]
                stable=geometry(viewport);page.mouse.move(rect["x"]+100,rect["y"]+100,steps=4)
                assert geometry(viewport)==stable,"Comparison question switch retained a drag"
                page.keyboard.press("Escape");open_viewer()
                page.locator("#zoomFit").click();page.wait_for_timeout(70)
                page.screenshot(path=str(OUTPUT/f"comparison-fit-{width}.png"));page.keyboard.press("Escape")
                report["passed"].append(f"{width}px: comparison zoom anchors both crop segments, XY left-pan, release and question-switch cleanup")
            except Exception as e:
                report["failures"].append({"width":width,"error":str(e),"traceback":traceback.format_exc()})
                page.screenshot(path=str(OUTPUT/f"failure-{width}.png"))
                page.mouse.up();page.mouse.up(button="middle");page.keyboard.up("Control");page.keyboard.up("Space")
                page.keyboard.press("Escape")
        try:
            page.set_viewport_size({"width":650,"height":1050});failed_pages.add(1);load();open_paper()
            page.locator("#pageNext").click();expect(page.locator("#pageNumberInput")).to_have_value("2")
            expect(page.locator("#pageImageState")).to_contain_text("没能加载")
            expect(page.locator("#pageImageRetry")).to_be_visible()
            for selector in ("#pageZoomFit","#pageZoomWidth","#pageZoomOut","#pageZoomIn"):
                expect(page.locator(selector)).to_be_disabled()
            assert boxes(page)==[]
            page.screenshot(path=str(OUTPUT/"original-image-failed-650.png"))
            failed_pages.clear();page.locator("#pageImageRetry").click()
            page.wait_for_function("document.querySelector('#pageStage img').complete&&document.querySelector('#pageStage img').naturalWidth>0")
            expect(page.locator("#pageImageState")).not_to_be_visible();expect(page.locator("#pageZoomIn")).to_be_enabled()
            page.locator("#pagePrevious").click();expect(page.locator("#pageNumberInput")).to_have_value("1")
            report["passed"].append("Original image 500 provides explanation and retry, disables zoom, recovers on retry and allows returning to prior page without edits")
        except Exception as e:
            report["failures"].append({"case":"image_failure_retry","error":str(e),"traceback":traceback.format_exc()})
            page.screenshot(path=str(OUTPUT/"failure-image-retry.png"))
        context.close();browser.close()
    report["saved_records_identical"]=(before==seed_helper.question_snapshot())
    report["saved_record_sha256_before_and_after"]=hashlib.sha256(json.dumps(before,ensure_ascii=False).encode()).hexdigest()
    (OUTPUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    assert report["saved_records_identical"] and not report["failures"],report["failures"]
    assert not report["page_errors"] and not report["forbidden_requests"] and not report["native_dialogs"],report
    print("Original-paper and comparison pointer checks passed; no saved records changed",flush=True)


def run(port):
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1",port))!=0,"Port occupied; existing server not stopped"
    fixture=seed()
    flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
    with (OUTPUT/"server.log").open("w",encoding="utf-8") as log:
        server=subprocess.Popen([sys.executable,str(ROOT/"backend/manage.py"),"runserver",f"127.0.0.1:{port}","--noreload"],
            cwd=ROOT,env=os.environ.copy(),stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if server.poll() is not None:raise RuntimeError("Owned test server exited")
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1",port))==0:break
                time.sleep(.1)
            else:raise TimeoutError("Owned test server did not become ready")
            check(f"http://127.0.0.1:{port}",fixture)
        finally:
            if os.name=="nt":subprocess.run(["taskkill","/PID",str(server.pid),"/T","/F"],stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,creationflags=flags,check=False)
            else:server.terminate()
            server.wait(timeout=10)
            path=OUTPUT/"report.json"
            if path.exists():
                report=json.loads(path.read_text(encoding="utf-8"));report["server_port"]=port;report["server_stopped"]=True
                path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Owned isolated original-paper server stopped",flush=True)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run",action="store_true");parser.add_argument("--port",type=int,default=8985)
    args=parser.parse_args()
    if not args.run:parser.error("Choose --run")
    run(args.port)
