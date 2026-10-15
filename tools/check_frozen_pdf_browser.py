"""Verify an installed/built Windows executable's PDF export with private data.

--run creates a new checkout/tmp child folder, migrates an empty database with
the supplied QuestionBankCard.exe, and starts only its internal loopback web
server. A short browser check creates only the shipped fictional demo, selects
four of its questions, downloads a real PDF and compares it with the preview.
No installed service is used
or stopped, and no worker, real credentials or formal question bank is used.

Example:
    python tools/check_frozen_pdf_browser.py --run --bundle C:/path/to/bundle

The frozen server cannot accept Python's socket-guard injection. Its environment
contains no service credentials and its worker is never started; the browser
retains the existing check's local-only request guard. Reports state this limit.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.request
from urllib.parse import urlparse
import uuid

import check_practice_browser as practice


ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def executable(bundle: str) -> Path:
    candidate = Path(bundle).expanduser().resolve()
    if candidate.is_dir():
        candidate /= "QuestionBankCard.exe"
    if candidate.name.lower() != "questionbankcard.exe" or not candidate.is_file():
        raise ValueError("--bundle must name QuestionBankCard.exe or its containing folder")
    return candidate


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def stop_owned(process: subprocess.Popen | None) -> bool:
    """Stop only a process created by this runner and its own descendants."""
    if process is None:
        return True
    if process.poll() is None:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=FLAGS, timeout=20, check=False)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=20)
    return process.poll() is not None


def wait_owned(process: subprocess.Popen, seconds: float) -> int:
    """Use short bounded waits so an interrupted caller can clean up its tree."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, seconds)
        try:
            return process.wait(timeout=min(1, remaining))
        except subprocess.TimeoutExpired:
            continue


def windows_arguments(command_line: str) -> list[str]:
    """Use Windows' own quoting rules for a browser's observed command line."""
    parser = ctypes.WinDLL("shell32", use_last_error=True).CommandLineToArgvW
    parser.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    parser.restype = ctypes.POINTER(ctypes.c_wchar_p)
    release = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    release.argtypes = [ctypes.c_void_p]
    release.restype = ctypes.c_void_p
    count = ctypes.c_int()
    arguments = parser(command_line, ctypes.byref(count))
    if not arguments:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return [arguments[index] for index in range(count.value)]
    finally:
        release(arguments)


def private_profile_processes(temp_root: Path) -> list[dict]:
    """Only return exact --user-data-dir paths belonging to this run's renderer."""
    script = r"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$root = [IO.Path]::GetFullPath($env:QB_FROZEN_CHECK_TMP)
$rows = @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe' OR Name='chrome.exe'" |
    Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0 } |
    ForEach-Object { @{ pid = [int]$_.ProcessId; name = $_.Name; command_line = $_.CommandLine;
        created = $_.CreationDate.ToUniversalTime().ToString('o') } })
ConvertTo-Json -InputObject $rows -Compress
"""
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, QB_FROZEN_CHECK_TMP=str(temp_root)), capture_output=True,
        encoding="utf-8", errors="replace", creationflags=FLAGS, timeout=20)
    if result.returncode:
        raise RuntimeError("Private renderer process inventory failed; cleanup could not be verified")
    records = json.loads(result.stdout.strip() or "null")
    if not isinstance(records, list):
        raise RuntimeError("Private renderer process inventory returned an invalid response")
    owned = []
    for record in records:
        if record.get("name", "").lower() not in ("msedge.exe", "chrome.exe"):
            continue
        arguments = windows_arguments(record["command_line"])
        paths = [argument.partition("=")[2] for argument in arguments if argument.startswith("--user-data-dir=")]
        if len(paths) != 1:
            continue
        profile = Path(paths[0]).resolve()
        if (profile.name == "profile" and profile.parent.parent == temp_root
                and profile.parent.name.startswith("tiyouju-pdf-")):
            owned.append({**record, "profile": str(profile)})
    return owned


def cleanup_private_profiles(temp_root: Path) -> dict:
    """Revalidate PID, creation time and exact command before killing its tree."""
    temp_root = temp_root.resolve()
    practice.confined_output(temp_root)
    if temp_root.name != "process-tmp":
        raise ValueError("Private renderer cleanup requires this run's process-tmp directory")
    terminated = []
    script = r"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$entries = $env:QB_FROZEN_CHECK_TARGETS | ConvertFrom-Json
foreach ($entry in $entries) {
    $row = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$entry.pid)
    if ($row -and $row.Name -in @('msedge.exe', 'chrome.exe') -and
        $row.CreationDate.ToUniversalTime().ToString('o') -ceq $entry.created -and
        $row.CommandLine -ceq $entry.command_line) {
        & taskkill.exe /PID ([int]$entry.pid) /T /F | Out-Null
    }
}
"""
    for _ in range(3):
        owned = private_profile_processes(temp_root)
        if not owned:
            break
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=dict(os.environ, QB_FROZEN_CHECK_TARGETS=json.dumps(owned)),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=FLAGS, timeout=20)
        if result.returncode:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError("Private renderer termination failed; cleanup could not be verified: " + detail[:1500])
        terminated.extend({"pid": item["pid"], "profile": item["profile"]} for item in owned)
    remaining = private_profile_processes(temp_root)
    return {"verified": True, "targeted": terminated,
        "remaining": [{"pid": item["pid"], "profile": item["profile"]} for item in remaining]}


def verify_empty_database(user: Path) -> None:
    database = user / "db.sqlite3"
    if not database.is_file():
        raise RuntimeError("Frozen migration did not create the private database")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        for table in ("core_paper", "core_question", "core_publishedquestion", "core_libraryjob"):
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]:
                raise RuntimeError("Private migration did not produce an empty " + table)
    for name in ("credentials.bin", "library-ai-credentials.bin"):
        if (user / name).exists():
            raise RuntimeError("Private migration unexpectedly created credentials")


def browser_check(output: Path, port: int) -> int:
    """Exercise only selection/preview/export; unrelated lessons cannot skip PDF."""
    from playwright.sync_api import expect, sync_playwright
    practice.OUTPUT = output = practice.confined_output(output)
    practice.USER = user = output / "user"
    practice.BASE = base = f"http://127.0.0.1:{port}"
    receipt = json.loads((output / "server.json").read_text(encoding="utf-8"))
    assert Path(receipt["isolated_root"]).resolve() == user
    assert receipt["url"] == base
    practice.forbid_remote_connections()
    practice.REPORT = report = {"success": False, "passed": [], "page_errors": [],
        "forbidden_requests": [], "writes": [], "real_user_data_used": False,
        "worker_started": False, "isolated_root": str(user), "mode": "frozen-practice-pdf"}
    try:
        with sync_playwright() as playwright:
            chrome = next((str(path) for path in (Path(playwright.chromium.executable_path),
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")) if path.is_file()), None)
            browser = playwright.chromium.launch(headless=True,
                **({"executable_path": chrome} if chrome else {}))
            context = browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
            context.add_init_script("localStorage.setItem('qb-welcome-seen','1');localStorage.setItem('qb-lens','0');")

            def route_guard(route):
                request = route.request
                parsed = urlparse(request.url)
                allowed = parsed.scheme in ("data", "blob", "about") or (
                    parsed.scheme == "http" and parsed.netloc == urlparse(base).netloc)
                if allowed and request.method not in ("GET", "HEAD"):
                    allowed = request.method == "POST" and practice.allowed_write(parsed.path)
                if not allowed:
                    report["forbidden_requests"].append(f"{request.method} {request.url}")
                    route.abort()
                else:
                    if request.method not in ("GET", "HEAD"):
                        report["writes"].append({"method": request.method, "path": parsed.path})
                    route.continue_()

            context.route("**/*", route_guard)
            page = context.new_page()
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("dialog", lambda dialog: dialog.dismiss())
            downloads = []
            page.on("download", lambda download: downloads.append(download))
            try:
                page.goto(base + "/settings#help")
                page.wait_for_load_state("networkidle")
                created = page.evaluate("""async () => {
                  const response = await fetch('/api/demo', {method:'POST',
                    headers:{'Content-Type':'application/json','X-QB-Request':'1'},
                    body:JSON.stringify({course:'full'})});
                  if(!response.ok) throw Error('Private demo creation failed: '+await response.text());
                  return await response.json();
                }""")
                paper_id = created["paper"]["id"]
                with practice.db() as connection:
                    chosen = [dict(row) for row in connection.execute(
                        "SELECT id, number, stem FROM core_question WHERE paper_id=? "
                        "AND number IN (1,3,5,12) ORDER BY number", (paper_id.replace("-", ""),))]
                assert len(chosen) == 4, "Private fictional demo did not contain all four selected questions"
                for question in chosen:
                    page.evaluate("""async question => {
                      const response = await fetch('/api/questions/'+question.id+'/approve', {
                        method:'POST', headers:{'Content-Type':'application/json','X-QB-Request':'1'},
                        body:JSON.stringify({approved:true,by:'ai',agent:'offline frozen PDF check'})});
                      if(!response.ok) throw Error('Private demo approval failed: '+await response.text());
                    }""", question)
                report.update(demo_paper=paper_id, selected_demo_numbers=[question["number"] for question in chosen])
                report["passed"].append("four fictional demo questions prepared in the new private database")
                page.goto(base + "/practice/" + paper_id)
                page.wait_for_load_state("networkidle")
                expect(page.locator("#practiceList input[type=checkbox]")).to_have_count(4)
                for checkbox in page.locator("#practiceList input[type=checkbox]").all():
                    checkbox.check()
                page.locator("#practicePreview").click()
                page.wait_for_function("""() => {
                  const frame=document.querySelector('#practiceFrame');
                  return frame?.contentWindow?.__qbPdfStatus?.ready ||
                    !document.querySelector('#practiceError').hidden;
                }""", timeout=60000)
                if page.locator("#practiceError").is_visible():
                    raise RuntimeError(page.locator("#practiceError").inner_text())
                preview_pages = page.locator("#practiceFrame").evaluate("n=>n.contentWindow.__qbPdfStatus.page_count")
                page.screenshot(path=str(output / "practice-preview.png"), full_page=True)
                report["preview_pages"] = preview_pages
                report["passed"].append("real preview completed with formulas, a table and an illustration")
                page.locator("#practiceExport").click()
                page.wait_for_function("""() => !document.querySelector('#practiceDone').hidden ||
                    !document.querySelector('#practiceError').hidden""", timeout=90000)
                if page.locator("#practiceError").is_visible():
                    raise RuntimeError(page.locator("#practiceError").inner_text())
                assert len(downloads) == 1, "PDF export must produce exactly one real browser download"
                destination = output / "practice-actual.pdf"
                downloads[0].save_as(destination)
                expect(page.locator("#practiceDone")).to_be_visible()
                import pymupdf
                import unicodedata
                with pymupdf.open(destination) as document:
                    assert document.page_count == preview_pages, "Downloaded PDF page count differs from the preview"
                    assert all(abs(page.rect.width - 210 / 25.4 * 72) < 1 and
                        abs(page.rect.height - 297 / 25.4 * 72) < 1 for page in document), "PDF pages must be A4"
                    text = unicodedata.normalize("NFKC", "".join(page.get_text() for page in document))
                    expected = ("已知集合", "平均阅读时长", "Which equation", "并说明所使用的恒等式")
                    assert all(marker in text for marker in expected), "Downloaded PDF is missing a selected question"
                    report["exported_pdf_pages"] = document.page_count
                    report["pdf_images"] = sum(len(page.get_images()) for page in document)
                    assert report["pdf_images"] > 0, "Downloaded PDF is missing the demo illustration"
                    document[0].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2)).save(output / "pdf-first-page.png")
                report.update(exported_pdf=str(destination), pdf_bytes=destination.stat().st_size,
                    downloads=[download.suggested_filename for download in downloads])
                report["passed"].append("actual downloaded A4 PDF has all four questions, an illustration and preview-matching pages")
                page.screenshot(path=str(output / "practice-complete.png"), full_page=True)
            finally:
                try:
                    page.screenshot(path=str(output / "browser-last.png"), full_page=True)
                finally:
                    context.close()
                    browser.close()
        practice.verify_private_data()
        report["passed"].append("zero formal publications, jobs, reread requests, credentials or forbidden requests")
        report["success"] = True
    except Exception as error:
        report.update(error=str(error) or type(error).__name__, traceback=traceback.format_exc())
        try:
            practice.verify_private_data()
        except Exception as verification_error:
            report["private_data_verification_error"] = str(verification_error) or type(verification_error).__name__
    write_json(output / "report.json", report)
    print(json.dumps({"success": report["success"], "error": report.get("error"),
        "passed_groups": len(report["passed"])}, ensure_ascii=False), flush=True)
    return 0 if report["success"] else 1


def run(args) -> int:
    if os.name != "nt":
        raise RuntimeError("The frozen executable check requires Windows")
    if args.port == 8768 or not 1024 <= args.port <= 65535:
        raise ValueError("Use an unoccupied test port other than the installed application's 8768")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", args.port)) == 0:
            raise RuntimeError("Test port occupied: no existing service will be reused or stopped")
    application = executable(args.bundle)
    browser_python = practice.runtime(args.browser_python, ("playwright.sync_api", "pymupdf"))
    pdf_python = practice.runtime(args.pdf_python, ("pymupdf",))
    output = practice.confined_output(args.output or ROOT / "tmp" /
        ("frozen-pdf-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]))
    if output.exists():
        raise RuntimeError("Evidence folder already exists; choose a new folder instead of reusing its database")
    output.mkdir(parents=True)
    user = output / "user"
    user.mkdir()
    (user / "data").mkdir()
    process_tmp = output / "process-tmp"
    process_tmp.mkdir()
    url = f"http://127.0.0.1:{args.port}"
    environment = practice.private_environment(user, url)
    environment.update(TEMP=str(process_tmp), TMP=str(process_tmp), __COMPAT_LAYER="DetectorsAppHealth")
    environment["QB_INTERNAL_LOG"] = str(output / "migration.log")
    receipt = {
        "url": url, "isolated_root": str(user), "backend_python": pdf_python,
        "browser_python": browser_python, "frozen_executable": str(application),
        "frozen_executable_sha256": hashlib.sha256(application.read_bytes()).hexdigest(),
        "worker_started": False, "cloud_called": False, "real_credentials_available": False,
        "browser_remote_socket_connections_blocked": True,
        "frozen_backend_remote_socket_hook": False, "preseeded_questions": 0,
        "private_process_tmp": str(process_tmp), "compatibility_layer_regression_value": "DetectorsAppHealth",
    }
    migration = server = browser = None
    result = 1
    runner_error = None
    try:
        with (output / "migration-process.log").open("wb") as log:
            migration = subprocess.Popen([str(application), "--internal-role", "migrate", "--noinput"],
                cwd=application.parent, env=environment, stdout=log, stderr=subprocess.STDOUT,
                creationflags=FLAGS)
            receipt["migration_pid"] = migration.pid
            migration_code = wait_owned(migration, 90)
        if migration_code:
            raise RuntimeError(f"Frozen private migration exited {migration_code}; inspect migration.log")
        verify_empty_database(user)
        receipt["empty_private_database_verified"] = True
        server_environment = dict(environment, QB_INTERNAL_LOG=str(output / "server.log"))
        with (output / "server-process.log").open("wb") as log:
            server = subprocess.Popen([str(application), "--internal-role", "runserver",
                f"127.0.0.1:{args.port}", "--noreload"], cwd=application.parent,
                env=server_environment, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        receipt["pid"] = server.pid
        write_json(output / "server.json", receipt)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("Owned frozen server exited during startup; inspect server.log")
            try:
                with opener.open(url + "/api/status", timeout=1) as response:
                    status = json.load(response)
                version = status.get("app_version")
                if not isinstance(version, str) or not version:
                    raise RuntimeError("Frozen server did not report its application version")
                receipt["app_version"] = version
                break
            except OSError:
                time.sleep(.1)
        else:
            raise TimeoutError("Owned frozen server did not become ready; inspect server.log")
        write_json(output / "server.json", receipt)
        command = [browser_python, str(Path(__file__).resolve()),
            "--internal-mode", "browser", "--output", str(output), "--port", str(args.port)]
        with (output / "browser.log").open("wb") as log:
            browser = subprocess.Popen(command, cwd=ROOT, env=environment,
                stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
            receipt["browser_pid"] = browser.pid
            result = wait_owned(browser, args.timeout)
        if not (output / "report.json").is_file():
            raise RuntimeError("Browser check did not produce its report; inspect browser.log")
        if result == 0:
            report = json.loads((output / "report.json").read_text(encoding="utf-8"))
            if report.get("success") is not True or not report.get("exported_pdf"):
                raise RuntimeError("Browser check did not confirm an actual downloaded PDF")
    except (Exception, KeyboardInterrupt) as error:
        result = 1
        runner_error = {"error": str(error) or type(error).__name__, "traceback": traceback.format_exc()}
        write_json(output / "runner-error.json", runner_error)
    finally:
        cleanup_errors = []
        for name, process in (("browser", browser), ("server", server), ("migration", migration)):
            try:
                receipt[name + "_stopped"] = stop_owned(process)
            except Exception as error:
                receipt[name + "_stopped"] = False
                cleanup_errors.append({"stage": name, "error": str(error), "type": type(error).__name__})
        try:
            receipt["renderer_profile_cleanup"] = cleanup_private_profiles(process_tmp)
            if receipt["renderer_profile_cleanup"]["remaining"]:
                cleanup_errors.append({"stage": "private_renderer_profiles", "error": "Owned renderer processes remain"})
        except Exception as error:
            receipt["renderer_profile_cleanup"] = {"verified": False}
            cleanup_errors.append({"stage": "private_renderer_profiles", "error": str(error), "type": type(error).__name__})
        if cleanup_errors:
            result = 1
            receipt["cleanup_errors"] = cleanup_errors
        write_json(output / "server.json", receipt)
        report_path = output / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {
            "success": False, "passed": [], "error": "Browser check did not complete"}
        report.update({key: value for key, value in receipt.items() if key != "backend_python"})
        if runner_error:
            report.update(success=False, runner_error=runner_error, error=runner_error["error"])
        if cleanup_errors:
            report.update(success=False, cleanup_errors=cleanup_errors)
        if result == practice.SKIP_EXIT:
            report.update(success=False, skipped=True)
        write_json(report_path, report)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    success = result == 0 and report.get("success") is True
    summary = {"success": success, "skipped": report.get("skipped", False),
        "app_version": receipt.get("app_version"), "output": str(output),
        "report": str(output / "report.json"), "browser_log": str(output / "browser.log"),
        "server_log": str(output / "server.log"), "migration_log": str(output / "migration.log"),
        "error": report.get("error") or report.get("skip_reason"),
        "passed_groups": len(report.get("passed", [])),
        "server_stopped": receipt["server_stopped"], "browser_stopped": receipt["browser_stopped"]}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not success:
        print("FAIL " + (summary["error"] or "Frozen PDF export check failed; inspect report.json"), flush=True)
    return 0 if success else practice.SKIP_EXIT if report.get("skipped") else 1


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Run against a newly isolated private database")
    parser.add_argument("--bundle", help="Installed/built bundle folder or QuestionBankCard.exe")
    parser.add_argument("--port", type=int, default=8992, help="Unoccupied loopback port other than 8768")
    parser.add_argument("--output", help="New evidence directory under checkout/tmp")
    parser.add_argument("--browser-python", help="Python with Playwright; automatically detected")
    parser.add_argument("--pdf-python", help="Python with PyMuPDF for checking the downloaded PDF")
    parser.add_argument("--timeout", type=int, default=420, help="Browser-check deadline in seconds")
    parser.add_argument("--internal-mode", choices=("browser",), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.internal_mode:
        if not args.output:
            parser.error("Internal browser mode requires --output")
        return browser_check(Path(args.output), args.port)
    if not args.run:
        parser.print_help()
        return 0
    if not args.bundle:
        parser.error("--run requires --bundle")
    if not 30 <= args.timeout <= 1200:
        parser.error("--timeout must be between 30 and 1200 seconds")
    try:
        return run(args)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
