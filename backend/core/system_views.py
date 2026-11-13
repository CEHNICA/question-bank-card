"""Static assets and system pages views."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse

FRONTEND = settings.FRONTEND_ROOT
_ASSET_SUFFIXES = {".js", ".css", ".png", ".woff2", ".woff", ".ttf"}
_ASSET_VERSION_TTL = 60.0
_asset_version_cache: dict[str, tuple[float, str]] = {}
_ASSET_URL_RE = re.compile(r'((?:src|href)=")(/[A-Za-z0-9._/-]+\.(?:js|css))(\?v=[^"]*)?(")')


def _file(path: Path, content_type: str):
    if not path.is_file():
        raise Http404
    return FileResponse(path.open("rb"), content_type=content_type)


def _asset_version() -> str:
    now = time.monotonic()
    cached = _asset_version_cache.get("value")
    if cached is not None and now - cached[0] < _ASSET_VERSION_TTL:
        return cached[1]
    digest = hashlib.sha1()
    try:
        for path in sorted(FRONTEND.rglob("*")):
            if path.suffix.lower() not in _ASSET_SUFFIXES or not path.is_file():
                continue
            stat = path.stat()
            relative = path.relative_to(FRONTEND).as_posix()
            digest.update(f"{relative}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    except OSError:
        pass
    value = digest.hexdigest()[:12]
    _asset_version_cache["value"] = (now, value)
    return value


def _etag_for(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"'


def _page(name: str):
    def view(request):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        version = _asset_version()
        try:
            text = (FRONTEND / name).read_text(encoding="utf-8")
        except OSError:
            raise Http404 from None
        text = _ASSET_URL_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}?v={version}{match.group(4)}", text)
        response = HttpResponse(text, content_type="text/html; charset=utf-8")
        response["Cache-Control"] = "no-cache"
        return response

    return view


def _frontend(name: str, content_type: str):
    def view(request):
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        response = _file(FRONTEND / name, content_type)
        response["Cache-Control"] = "public, max-age=604800"
        etag = _etag_for(FRONTEND / name)
        if etag:
            response["ETag"] = etag
        return response

    return view


index_page = _page("index.html")
library_page = _page("library.html")
app_script = _frontend("app.js", "application/javascript; charset=utf-8")
render_script = _frontend("qb-render.js", "application/javascript; charset=utf-8")
library_script = _frontend("library.js", "application/javascript; charset=utf-8")
library_tag_manager_script = _frontend("library-tag-manager.js", "application/javascript; charset=utf-8")
library_tag_manager_styles = _frontend("library-tag-manager.css", "text/css; charset=utf-8")
library_tag_map_script = _frontend("library-tag-map.js", "application/javascript; charset=utf-8")
library_tag_map_styles = _frontend("library-tag-map.css", "text/css; charset=utf-8")
library_workspace_script = _frontend("library-workspace.js", "application/javascript; charset=utf-8")
library_toc_script = _frontend("library-toc.js", "application/javascript; charset=utf-8")
library_solutions_script = _frontend("library-solutions.js", "application/javascript; charset=utf-8")
library_question_editor_script = _frontend("library-question-editor.js", "application/javascript; charset=utf-8")
library_answer_editor_script = _frontend("library-answer-editor.js", "application/javascript; charset=utf-8")
library_ai_script = _frontend("library-ai-settings.js", "application/javascript; charset=utf-8")
export_settings_script = _frontend("export-settings.js", "application/javascript; charset=utf-8")
library_transfer_script = _frontend("library-transfer.js", "application/javascript; charset=utf-8")
library_question_viewer_script = _frontend("library-question-viewer.js", "application/javascript; charset=utf-8")
browser_interactions_script = _frontend("browser-interactions.js", "application/javascript; charset=utf-8")
site_drawer_script = _frontend("site-drawer.js", "application/javascript; charset=utf-8")
exam_export_script = _frontend("exam-export.js", "application/javascript; charset=utf-8")
exam_layout_script = _frontend("exam-layout.js", "application/javascript; charset=utf-8")
styles = _frontend("styles.css", "text/css; charset=utf-8")
api_redesign_styles = _frontend("api-redesign.css", "text/css; charset=utf-8")
library_styles = _frontend("library.css", "text/css; charset=utf-8")
library_reading_styles = _frontend("library-reading.css", "text/css; charset=utf-8")
favicon = _frontend("favicon.png", "image/png")
startup_hint_script = _frontend("startup-hint.js", "application/javascript; charset=utf-8")
startup_hint_styles = _frontend("startup-hint.css", "text/css; charset=utf-8")


def katex_asset(request, asset: str):
    if asset in {"katex.min.js", "contrib/auto-render.min.js"}:
        content_type = "application/javascript; charset=utf-8"
    elif asset == "katex.min.css":
        content_type = "text/css; charset=utf-8"
    elif re.fullmatch(r"fonts/KaTeX_[A-Za-z0-9_-]+\.(?:woff2?|ttf)", asset):
        content_type = {"woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf"}[asset.rsplit(".", 1)[1]]
    else:
        raise Http404
    response = _file(FRONTEND / "vendor" / "katex" / asset, content_type)
    response["Cache-Control"] = "public, max-age=604800"
    etag = _etag_for(FRONTEND / "vendor" / "katex" / asset)
    if etag:
        response["ETag"] = etag
    return response


def health(request):
    return JsonResponse({"ok": True, "app": "question-bank-card"})
