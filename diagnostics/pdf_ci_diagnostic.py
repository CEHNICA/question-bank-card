"""Diagnostic-only runner: no renderer/test edits, retries, or extra browser calls.

Copy to diagnostics/pdf_ci_diagnostic.py on an independent CI branch only.
Replace backend's test command with:
  python ../diagnostics/pdf_ci_diagnostic.py core
Use --self-test to check instrumentation without Django, browsers, or services.
"""
from __future__ import annotations

import argparse
import ctypes
import functools
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace


EXPECTED_SOURCE_SHA256 = "7172c7939b89934714c12a7953e94aa75fa4cfcbeebfbdc96634579e6ffb897e"
PREFIX = "QB_PDF_DIAGNOSTIC "


def _emit(record):
    # Never emit exception messages, arguments, HTML, URLs, env, or local paths.
    try:
        print(PREFIX + json.dumps(record, ensure_ascii=True, sort_keys=True), file=sys.stderr, flush=True)
    except Exception:
        pass  # Diagnostics must not change the renderer's exception behavior.


def _file_version(path):
    result = {"name": "Edge" if path.name.lower() == "msedge.exe" else "Chrome" if path.name.lower() == "chrome.exe" else "unknown"}
    if os.name != "nt":
        return {**result, "product_version": "unavailable"}
    try:
        from ctypes import wintypes
        dll = ctypes.WinDLL("version", use_last_error=True)
        dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        dll.GetFileVersionInfoW.restype = wintypes.BOOL
        dll.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.UINT)]
        dll.VerQueryValueW.restype = wintypes.BOOL
        ignored = wintypes.DWORD()
        size = dll.GetFileVersionInfoSizeW(str(path), ctypes.byref(ignored))
        if not size or size > 16 * 1024 * 1024:
            return {**result, "product_version": "unavailable"}
        buffer = ctypes.create_string_buffer(size)
        if not dll.GetFileVersionInfoW(str(path), 0, size, buffer):
            return {**result, "product_version": "unavailable"}
        pointer, length = wintypes.LPVOID(), wintypes.UINT()
        if not dll.VerQueryValueW(buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
            return {**result, "product_version": "unavailable"}
        fields = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD * 13)).contents
        if fields[0] != 0xFEEF04BD:
            return {**result, "product_version": "unavailable"}
        version = lambda hi, lo: ".".join(map(str, (hi >> 16, hi & 65535, lo >> 16, lo & 65535)))
        return {**result, "product_version": version(fields[4], fields[5]), "file_version": version(fields[2], fields[3])}
    except Exception as error:
        return {**result, "product_version": "unavailable", "version_error_type": type(error).__name__}


def install_diagnostics(pdf):
    original = pdf._render
    lines, first_line = inspect.getsourcelines(original)
    stages = {}
    for offset, line in enumerate(lines):
        code = line.strip()
        stage = None
        for marker, name in (
            ("executable = _browser_path", "resolve_browser"), ("with tempfile.TemporaryDirectory", "allocate_tempdir"),
            ("process = subprocess.Popen", "launch_browser"), ("port_file =", "wait_debug_port"),
            ("lines = port_file.read_text", "read_debug_port"), ("with opener.open", "get_debug_targets"),
            ("target = next", "select_blank_page"), ("client = _CDP", "connect_websocket"),
            ("if status.get(\"error\")", "inspect_layout_status"), ("data = base64.b64decode", "decode_pdf"),
            ("_validate_pdf(", "validate_pdf"), ("return data,", "return_pdf"),
            ("client.close()", "cleanup_socket"), ("process.wait(", "cleanup_wait_browser"),
            ("subprocess.run([\"taskkill\"", "cleanup_owned_process_tree"), ("process.kill()", "cleanup_owned_process"),
        ):
            if marker in code:
                stage = name
                break
        call = re.search(r'client\.call\("([A-Za-z]+\.[A-Za-z]+)"', code)
        if call:
            stage = "cdp:" + call[1]
        if stage:
            stages[first_line + offset] = stage
    watched = {original.__code__, pdf._remaining.__code__}
    for name in ("__init__", "_read", "_send", "_message", "call"):
        watched.add(getattr(pdf._CDP, name).__code__)
    counter = 0

    @functools.wraps(original)
    def wrapped(document):
        nonlocal counter
        counter += 1
        number = counter
        state = {"stage": "enter", "browser": None, "seen": set(), "render_frame": None}
        started = time.monotonic()
        old_trace = sys.gettrace()

        def trace(frame, event, arg):
            if frame.f_code not in watched:
                return None
            if frame.f_code is original.__code__:
                state["render_frame"] = frame
            else:
                frame.f_trace_lines = False  # Avoid tracing the large WebSocket mask loop.
            if event == "call" and frame.f_code is pdf._CDP.call.__code__:
                method = frame.f_locals.get("method", "")
                if isinstance(method, str) and re.fullmatch(r"[A-Za-z]+\.[A-Za-z]+", method):
                    state["stage"] = "cdp:" + method
            if event == "line" and frame.f_code is original.__code__:
                if frame.f_lineno in stages:
                    state["stage"] = stages[frame.f_lineno]
                executable = frame.f_locals.get("executable")
                if executable and state["browser"] is None:
                    state["browser"] = _file_version(Path(executable))
                    _emit({"event": "browser", "render": number, "browser": state["browser"]})
            if event == "exception":
                exception_type, error, tb = arg
                if id(error) in state["seen"]:
                    return trace
                state["seen"].add(id(error))
                active = state["render_frame"]
                local = active.f_locals if active else {}
                process = local.get("process")
                stack = []
                while tb:
                    stack.append({"file": Path(tb.tb_frame.f_code.co_filename).name, "line": tb.tb_lineno, "function": tb.tb_frame.f_code.co_name})
                    tb = tb.tb_next
                record = {"event": "exception", "render": number, "stage": state["stage"],
                          "exception_type": exception_type.__module__ + "." + exception_type.__qualname__,
                          "errno": getattr(error, "errno", None), "winerror": getattr(error, "winerror", None),
                          "export_status": getattr(error, "status", None), "origin": stack,
                          "elapsed_seconds": round(time.monotonic() - started, 3), "browser": state["browser"],
                          "client_created": local.get("client") is not None,
                          "process_exit_code": process.poll() if process is not None else None}
                targets = local.get("targets")
                if isinstance(targets, list):
                    record["debug_target_count"] = len(targets)
                    record["blank_page_count"] = sum(isinstance(t, dict) and t.get("type") == "page" and t.get("url") == "about:blank" for t in targets)
                if isinstance(local.get("lines"), list):
                    record["port_file_line_count"] = len(local["lines"])
                if hasattr(error, "lineno"):
                    record["json_line"] = error.lineno
                    record["json_column"] = getattr(error, "colno", None)
                _emit(record)
            return trace

        _emit({"event": "start", "render": number})
        try:
            sys.settrace(trace)
            result = original(document)
            _emit({"event": "success", "render": number, "pages": result[1], "elapsed_seconds": round(time.monotonic() - started, 3), "browser": state["browser"]})
            return result
        finally:
            sys.settrace(old_trace)
            state["render_frame"] = None

    pdf._render = wrapped
    return original


def self_test():
    # Expected exceptions contain a sentinel that must never appear in diagnostics.
    def _remaining(deadline):
        return deadline
    class CDP:
        def __init__(self): pass
        def _read(self): pass
        def _send(self): pass
        def _message(self): pass
        def call(self): pass
    def _render(document):
        try:
            raise ConnectionResetError(10054, "SENTINEL_PRIVATE_VALUE")
        finally:
            raise PermissionError(13, "SENTINEL_PRIVATE_VALUE")
    pdf = SimpleNamespace(_render=_render, _remaining=_remaining, _CDP=CDP)
    capture, saved = io.StringIO(), sys.stderr
    sys.stderr = capture
    previous_trace = sys.gettrace()
    try:
        install_diagnostics(pdf)
        try:
            pdf._render("SENTINEL_PRIVATE_HTML")
        except PermissionError:
            pass
        else:
            raise AssertionError("The original cleanup exception must remain unchanged")
    finally:
        sys.stderr = saved
    records = [json.loads(line[len(PREFIX):]) for line in capture.getvalue().splitlines()]
    assert "SENTINEL" not in capture.getvalue()
    assert {r.get("exception_type") for r in records} >= {"builtins.ConnectionResetError", "builtins.PermissionError"}
    assert sys.gettrace() is previous_trace
    print("Diagnostic self-test passed; original and cleanup exceptions retained; secret/HTML sentinel omitted.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="*", default=["core.test_pdf_pagination"])
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        parser.error("Diagnostic execution is CI-only; use --self-test locally")
    project = next((p for p in Path(__file__).resolve().parents if (p / "backend/manage.py").is_file()), None)
    if project is None:
        parser.error("Place the runner under the diagnostic branch repository")
    source = project / "backend/core/library_pdf.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != EXPECTED_SOURCE_SHA256:
        parser.error("Renderer differs from frozen 1.11.18 source; diagnostic run refused")
    for key in list(os.environ):
        if key.upper().startswith(("QB_", "TIYOUJU_", "DJANGO_", "PYTHONPATH")) or any(s in key.upper() for s in ("API_KEY", "TOKEN", "SECRET")):
            del os.environ[key]
    with tempfile.TemporaryDirectory(prefix="qb-ci-pdf-diagnostic-") as private:
        root = Path(private)
        os.environ.update(DJANGO_SETTINGS_MODULE="qb_server.settings", QB_USER_ROOT=private,
                          QB_DATABASE=str(root / "db.sqlite3"), QB_DATA_ROOT=str(root / "data"),
                          QB_CREDENTIAL_FILE=str(root / "credentials.bin"), LOCALAPPDATA=str(root / "local"), APPDATA=str(root / "roaming"))
        sys.path.insert(0, str(project / "backend"))
        import django
        django.setup()
        from django.conf import settings
        from django.test.utils import get_runner
        from core import library_pdf as pdf
        _emit({"event": "source", "sha256": digest, "diagnostic_only": True, "retries_added": False, "browser_flags_changed": False})
        install_diagnostics(pdf)
        failures = get_runner(settings)(verbosity=2, interactive=False).run_tests(args.labels)
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
