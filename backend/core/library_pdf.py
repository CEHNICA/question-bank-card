"""Local PDF exports from stored publications and bundled layout resources.

The caller never supplies HTML, CSS, a URL, executable paths or browser flags.
A fresh system-browser profile renders a fixed document with no network assets.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import html
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import tempfile
import threading
import time
from urllib.parse import quote
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import library_export as word
from .library_browse import BrowseError, normalize_ids
from .library_drafts import DraftError, _print_options, _title

DEADLINE_SECONDS = 60
MAX_PDF_BYTES = 64 * 1024 * 1024
MAX_MESSAGE_BYTES = 192 * 1024 * 1024
MAX_PAGES = 200
_render_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def _browser_path():
    candidates = []
    for key in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
        root = os.environ.get(key)
        if root:
            candidates.append(Path(root) / "Microsoft/Edge/Application/msedge.exe")
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = os.environ.get(key)
        if root:
            candidates.append(Path(root) / "Google/Chrome/Application/chrome.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise word.ExportError("导出 PDF 需要本机 Microsoft Edge 或 Google Chrome；仍可导出 Word。", 503)


def _launch_browser(executable, profile):
    """Keep ownership of the headless process used by PDF and Word pictures."""
    flags = [str(executable), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-extensions",
             "--no-proxy-server", "--host-resolver-rules=MAP * ~NOTFOUND", "--remote-debugging-port=0",
             "--remote-debugging-address=127.0.0.1", "--window-size=1280,1000", "--user-data-dir=" + str(profile)]
    if os.name == "nt" and executable.name.lower() == "msedge.exe":
        # Edge's compatibility-layer relaunch exits this Popen with code 0 and
        # leaves another browser using the profile. Waiting and cleanup must own
        # the real browser, not that launcher. Playwright uses the same switch:
        # https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/server/chromium/chromiumSwitches.ts
        flags.append("--edge-skip-compat-layer-relaunch")
    flags.append("about:blank")
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "shell": False}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(flags, **kwargs)


def _remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise word.ExportError("PDF 排版超时，请减少题目后重试；也可先导出 Word。", 504)
    return seconds


def _wait_debug_port(profile, process, deadline):
    # Chromium creates this file before it has finished writing it. On Windows
    # its writer can also briefly deny reads, so creation alone is not readiness.
    port_file = profile / "DevToolsActivePort"
    while True:
        _remaining(deadline)
        if process.poll() is not None:
            raise word.ExportError("本机浏览器未能启动 PDF 排版，请重试或先导出 Word。", 503)
        try:
            lines = port_file.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, PermissionError):
            lines = []
        if len(lines) >= 2 and lines[0] and lines[1]:
            port = int(lines[0])
            if not 1 <= port <= 65535:
                raise ValueError("debug port")
            return port
        time.sleep(min(.05, _remaining(deadline)))


def _wait_page_target(opener, port, process, deadline):
    # The debugging listener may precede the initial blank page's registration.
    # Wait for that owned target, without restarting or rendering a document twice.
    while True:
        _remaining(deadline)
        if process.poll() is not None:
            raise word.ExportError("本机浏览器未能启动 PDF 排版，请重试或先导出 Word。", 503)
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=_remaining(deadline)) as reply:
                targets = json.loads(reply.read(100_000))
        except (ConnectionRefusedError, URLError) as error:
            if isinstance(error, URLError) and not isinstance(error.reason, ConnectionRefusedError):
                raise
            targets = []
        if not isinstance(targets, list) or any(not isinstance(target, dict) for target in targets):
            raise ValueError("debug catalog")
        target = next((item for item in targets if item.get("type") == "page" and item.get("url") == "about:blank"), None)
        if target is not None:
            return target
        time.sleep(min(.05, _remaining(deadline)))


def _cleanup_warning(stage, error):
    # Do not include documents, profile paths, or exception messages in this log.
    try:
        _logger.warning("PDF cleanup step failed: stage=%s error_type=%s", stage, type(error).__name__)
    except Exception:
        pass


@contextmanager
def _temporary_directory():
    directory = tempfile.TemporaryDirectory(prefix="tiyouju-pdf-")
    try:
        yield directory.name
    finally:
        try:
            directory.cleanup()
        except Exception as error:
            _cleanup_warning("temporary_directory", error)


def _cleanup_browser(client, process):
    if client:
        try:
            client.call("Browser.close")
        except (word.ExportError, ConnectionError):
            # Shutdown can close CDP before its reply, or follow the main deadline.
            pass
        except Exception as error:
            _cleanup_warning("browser_close", error)
        try:
            client.close()
        except Exception as error:
            _cleanup_warning("socket_close", error)
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
    except Exception as error:
        _cleanup_warning("process_poll", error)
    try:
        process.wait(timeout=2)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception as error:
        _cleanup_warning("process_wait", error)
    try:
        # Recheck this Popen before force-stopping its PID and child tree.
        if process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False,
                    timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                process.kill()
    except Exception as error:
        _cleanup_warning("process_terminate", error)
    try:
        process.wait(timeout=5)
    except Exception as error:
        _cleanup_warning("process_final_wait", error)


class _CDP:
    """Bounded RFC 6455 transport, only to this fresh browser's loopback port."""

    def __init__(self, port, path, deadline):
        if type(port) is not int or not 1 <= port <= 65535 or not re.fullmatch(r"/devtools/(?:page|browser)/[A-Za-z0-9_-]+", path):
            raise word.ExportError("本机 PDF 排版进程未能连接，请重试。", 503)
        self.deadline, self.serial, self.buffer = deadline, 0, bytearray()
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline))
        self.events = []
        try:
            key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
            request = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
            self.socket.sendall(request.encode("ascii"))
            while b"\r\n\r\n" not in self.buffer:
                self.socket.settimeout(_remaining(deadline))
                chunk = self.socket.recv(4096)
                if not chunk or len(self.buffer) + len(chunk) > 32_768:
                    raise ValueError("invalid handshake")
                self.buffer.extend(chunk)
            header, remainder = bytes(self.buffer).split(b"\r\n\r\n", 1)
            self.buffer = bytearray(remainder)
            lines = header.decode("ascii").split("\r\n")
            fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            fields = {key.lower(): value.strip() for key, value in fields.items()}
            expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
            if not re.match(r"HTTP/1\.[01] 101(?: |$)", lines[0]) or fields.get("sec-websocket-accept") != expected:
                raise ValueError("invalid handshake")
        except BaseException:
            try:
                self.socket.close()
            except Exception as error:
                _cleanup_warning("handshake_socket_close", error)
            raise

    def close(self):
        self.socket.close()

    def _read(self, count):
        while len(self.buffer) < count:
            self.socket.settimeout(_remaining(self.deadline))
            data = self.socket.recv(min(1_048_576, count - len(self.buffer)))
            if not data:
                raise ConnectionError("PDF renderer closed")
            self.buffer.extend(data)
        result = bytes(self.buffer[:count])
        del self.buffer[:count]
        return result

    def _send(self, data, opcode=1):
        if len(data) > MAX_MESSAGE_BYTES:
            raise word.ExportError("本次 PDF 内容过多，请减少选题后重试。", 413)
        length = len(data)
        header = bytes([0x80 | opcode, 0x80 | (length if length < 126 else 126 if length < 65536 else 127)])
        if length >= 126:
            header += struct.pack("!H" if length < 65536 else "!Q", length)
        mask = secrets.token_bytes(4)
        masked = bytearray(data)
        for index in range(length):
            masked[index] ^= mask[index % 4]
        self.socket.settimeout(_remaining(self.deadline))
        self.socket.sendall(header + mask + masked)

    def _message(self):
        message, started = bytearray(), False
        while True:
            first, second = self._read(2)
            opcode, final = first & 15, bool(first & 128)
            if first & 112 or second & 128:
                raise ValueError("invalid websocket frame")
            size = second & 127
            if size == 126:
                size = struct.unpack("!H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read(8))[0]
            if size > MAX_MESSAGE_BYTES or len(message) + size > MAX_MESSAGE_BYTES:
                raise word.ExportError("本次 PDF 内容过多，请减少选题后重试。", 413)
            if opcode >= 8 and (not final or size > 125):
                raise ValueError("invalid control frame")
            data = self._read(size)
            if opcode == 8:
                raise ConnectionError("PDF renderer closed")
            if opcode == 9:
                self._send(data, 10)
                continue
            if opcode == 10:
                continue
            if opcode == 1 and not started:
                started = True
            elif opcode != 0 or not started:
                raise ValueError("invalid fragmented message")
            message.extend(data)
            if final:
                return json.loads(message.decode("utf-8"))

    def call(self, method, params=None):
        self.serial += 1
        serial = self.serial
        self._send(json.dumps({"id": serial, "method": method, "params": params or {}}, ensure_ascii=False).encode("utf-8"))
        while True:
            response = self._message()
            if not isinstance(response, dict):
                raise ValueError("invalid CDP reply")
            if response.get("id") == serial:
                if "error" in response:
                    raise word.ExportError("本机 PDF 排版未完成，请重试；也可先导出 Word。", 503)
                return response.get("result", {})
            if len(self.events) < 1000 and response.get("method") in {"Runtime.exceptionThrown", "Network.requestWillBeSent"}:
                self.events.append(response)


def _assets(frontend):
    """Read only fixed public program files, embedding fonts in the document."""
    frontend = frontend.resolve()
    def read(name):
        path = (frontend / name).resolve()
        if not path.is_relative_to(frontend) or not path.is_file():
            raise word.ExportError("PDF 排版文件缺失，请修复或重新安装题有据。", 503)
        return path.read_text(encoding="utf-8")
    katex_css = read("vendor/katex/katex.min.css")
    def font(match):
        name = match.group(1).strip("\"'")
        path = (frontend / "vendor/katex" / name).resolve()
        allowed = (frontend / "vendor/katex/fonts").resolve()
        if not path.is_relative_to(allowed) or path.suffix not in {".woff2", ".woff", ".ttf"} or not path.is_file():
            raise word.ExportError("PDF 公式字体不完整，请修复安装。", 503)
        mime = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}[path.suffix]
        return 'url("data:' + mime + ';base64,' + base64.b64encode(path.read_bytes()).decode("ascii") + '")'
    katex_css = re.sub(r"url\(([^)]+)\)", font, katex_css)
    css = "\n".join([read("styles.css"), katex_css, read("library.css")])
    scripts = [read(name) for name in ("vendor/katex/katex.min.js", "qb-render.js", "library-solutions.js", "exam-layout.js")]
    return css, scripts


_DRIVER = r"""
window.__qbPdfStatus = {ready:false};
(async () => {
  const input = JSON.parse(document.getElementById('examData').textContent);
  const source = document.getElementById('printSource'), host = document.getElementById('printPaper');
  const o = input.options;
  source.style.setProperty('--exam-font-size',o.font_size+'pt');
  source.dataset.answerSpace=o.answer_space;source.dataset.document=o.document;
  source.dataset.answerLayout=o.answer_layout;
  const node=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  source.append(node('h2','print-title',input.title));
  if(o.student_info && o.document!=='answers')source.append(node('p','print-info','姓名 ____________　班级 ____________　得分 ________'));
  let number=0;const numbered=[];
  const inline=o.document==='combined' && o.answer_layout==='inline';
  const solutionRow=(n,item,isInline)=>{
    const row=node('div','print-answer-row'+(isInline?' print-answer-inline':'')),body=node('div','');
    row.dataset.questionId=item.id;row.append(node('strong','',isInline?'':n+'.'));
    LibrarySolutions.render(body,{...(item.selected||{}),figures:(item.solution_images||[]).map(f=>({...f,url:f.file}))},
      {node,QB:QBRender,empty:'（原卷未提供答案解析）'});
    if(item.ai)body.prepend(node('span','print-ai-note','（AI 参考，未核对）'));
    row.append(body);return row;
  };
  const types=[['single_choice','选择题'],['multiple_choice','多选题'],['fill_blank','填空题'],['true_false','判断题'],['free_response','解答题']];
  const known=new Set(types.map(x=>x[0]));types.push(['other','其他']);
  let section=0;
  for(const [key,name] of types){
    const items=input.items.filter(x=>key==='other'?!known.has(x.type):x.type===key);if(!items.length)continue;
    if(o.document!=='answers')source.append(node('h3','print-section','一二三四五六'[section]+'、'+name));
    section++;
    for(const item of items){
      number++;numbered.push([number,item]);if(o.document==='answers')continue;
      const block=node('div','print-question');
      QBRender.renderQuestion(block,item.content,{number,showAnswer:'none',resolveUrl:x=>x.file,resolveQuestionImageUrl:x=>x.file});
      block.dataset.questionId=item.id;
      if(o.origin && item.content.origin)(block.querySelector('.qb-stem-body')||block.querySelector('.qb-stem')).prepend(node('span','print-origin','（'+item.content.origin+'）'));
      const space=(o.answer_space_overrides||{})[item.id]||o.answer_space;
      if(item.type==='free_response' && space!=='none'&&!inline){
        const blank=node('div','print-answer-space');blank.dataset.answerSpace=space;
        blank.style.height=({small:12,medium:30,large:60}[space]||0)+'mm';block.append(blank);
      }
      source.append(block);
      if(inline && (String(item.selected?.answer||'').trim() || String(item.selected?.analysis||'').trim() || (item.solution_images||[]).length))source.append(solutionRow(number,item,true));
    }
  }
  if(o.document!=='questions'&&!inline){
    const key=node('section','print-answers');if(o.document==='answers')key.classList.add('print-answers-only');
    key.append(node('h3','print-section','参考答案与解析'));
    for(const [n,item] of numbered){
      key.append(solutionRow(n,item,false));
    }
    source.append(key);
  }
  source.append(node('p','print-footer','共 '+number+' 题'));
  const images=[...source.querySelectorAll('img')];
  await Promise.all(images.map(async i=>{i.loading='eager';await i.decode();if(!i.naturalWidth)throw new Error('试卷配图未完整载入');}));
  await document.fonts.ready;
  const result=await ExamLayout.paginate(source,{...o,host});
  if(!result.page_count || result.page_count>200)throw new Error('试卷页数过多，请减少选题后重试');
  if(result.formula_overflow)throw new Error('有公式超出 A4 正文，请调整字号或导出 Word 继续排版');
  await document.fonts.ready;
  window.__qbPdfStatus={ready:true,page_count:result.page_count,warnings:result.warnings||[]};
})().catch(e=>{window.__qbPdfStatus={ready:false,error:e.message||'试卷排版未完成'};});
"""


def _html_document(captured, title, options):
    mode = word._effective_document(captured, options["document"])
    options = {**options, "document": mode, "answers": mode != "questions"}
    css, scripts = _assets(Path(settings.FRONTEND_ROOT))
    nonce = secrets.token_urlsafe(24)
    items = []
    for item in captured:
        content = {name: item["content"].get(name) for name in ("stem", "options", "origin")}
        if item["content"].get("body_mode", "text") == "source_image":
            content["body_mode"] = "source_image"
            content["question_images"] = [{"file": "data:image/png;base64," + base64.b64encode(image["bytes"]).decode("ascii"),
                                            "width": image["size"][0], "height": image["size"][1], "order": order}
                                           for order, image in enumerate(item["images"])]
            content["figures"] = []
        else:
            content["figures"] = [{"slot": image["slot"], "file": "data:image/png;base64," + base64.b64encode(image["bytes"]).decode("ascii")}
                                  for image in item["images"]]
        items.append({"id": item["id"], "type": item["type"], "content": content,
                      "selected": item["selected"] if options["document"] != "questions" else {},
                      "solution_images": [{**{key: image[key] for key in ("position", "paragraph", "display_width")},
                          "file": "data:image/png;base64," + base64.b64encode(image["bytes"]).decode("ascii")}
                          for image in item.get("solution_images", [])] if options["document"] != "questions" else [],
                      "ai": item["ai"] if options["document"] != "questions" else False})
    data = json.dumps({"items": items, "title": title, "options": options}, ensure_ascii=False)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    policy = f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'"
    script_tags = "".join(f'<script nonce="{nonce}" src="data:application/javascript;base64,{base64.b64encode(script.encode()).decode()}"></script>' for script in scripts)
    if re.search(r"</style", css, re.I):
        raise word.ExportError("PDF 排版资源不完整，请修复安装。", 503)
    # Measurements use fixed print width, independent of the headless viewport.
    fixed = "html,body{margin:0;background:#fff}#printSource{position:absolute;left:-20000px;top:0;width:178mm;max-width:none}#printPaper{width:210mm;max-width:none;padding:0;box-shadow:none}"
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'
            + html.escape(policy, quote=True) + '"><title>' + html.escape(title) + '</title><style>' + css + fixed + '</style></head>'
            + '<body><section class="print-sheet"><div id="printSource" class="print-flow exam-source"></div><article id="printPaper" class="print-paper"></article></section>'
            + f'<script nonce="{nonce}" id="examData" type="application/json">' + data + '</script>' + script_tags
            + f'<script nonce="{nonce}">' + _DRIVER + '</script></body></html>')


def _validate_pdf(data, pages):
    import pymupdf
    if not data.startswith(b"%PDF-") or b"%%EOF" not in data[-1024:] or len(data) > MAX_PDF_BYTES:
        raise word.ExportError("PDF 文件未完整生成，请减少选题后重试。", 422)
    try:
        with pymupdf.open(stream=data, filetype="pdf") as result:
            if result.page_count != pages or not 1 <= pages <= MAX_PAGES or result.is_encrypted:
                raise ValueError("page count")
            for page in result:
                if abs(page.rect.width - 210 / 25.4 * 72) > 1 or abs(page.rect.height - 297 / 25.4 * 72) > 1:
                    raise ValueError("page size")
    except (ValueError, RuntimeError, pymupdf.FileDataError):
        raise word.ExportError("PDF 页数或 A4 页面不完整，请重新打开组卷后导出。", 422) from None


def _render(document):
    if not _render_lock.acquire(blocking=False):
        raise word.ExportError("另一份 PDF 正在排版，请稍后重试。", 409)
    process = client = None
    try:
        executable = _browser_path()
        deadline = time.monotonic() + DEADLINE_SECONDS
        with _temporary_directory() as directory:
            root = Path(directory).resolve()
            if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("tiyouju-pdf-"):
                raise word.ExportError("PDF 临时目录未能建立，请重试。", 503)
            profile = root / "profile"
            process = _launch_browser(executable, profile)
            try:
                port = _wait_debug_port(profile, process, deadline)
                opener = build_opener(ProxyHandler({}))
                target = _wait_page_target(opener, port, process, deadline)
                url = target["webSocketDebuggerUrl"]
                match = re.fullmatch(rf"ws://(?:127\.0\.0\.1|localhost):{port}(/devtools/page/[A-Za-z0-9_-]+)", url)
                if not match:
                    raise ValueError("debug target")
                client = _CDP(port, match[1], deadline)
                client.call("Page.enable")
                client.call("Runtime.enable")
                client.call("Network.enable")
                client.call("Network.setBlockedURLs", {"urls": ["http://*", "https://*", "ws://*", "wss://*", "ftp://*", "file://*"]})
                frame = client.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
                client.call("Page.setDocumentContent", {"frameId": frame, "html": document})
                while True:
                    value = client.call("Runtime.evaluate", {"expression": "window.__qbPdfStatus || {ready:false}", "returnByValue": True})
                    status = value.get("result", {}).get("value", {})
                    if status.get("error"):
                        raise word.ExportError("PDF 未能完整排版：" + str(status["error"])[:200], 422)
                    if status.get("ready"):
                        break
                    _remaining(deadline)
                    time.sleep(.05)
                if type(status.get("page_count")) is not int or not 1 <= status["page_count"] <= MAX_PAGES:
                    raise word.ExportError("PDF 页数不完整，请重新打开组卷。", 422)
                result = client.call("Page.printToPDF", {"printBackground": True, "displayHeaderFooter": False,
                    "preferCSSPageSize": True, "paperWidth": 210 / 25.4, "paperHeight": 297 / 25.4,
                    "marginTop": 0, "marginBottom": 0, "marginLeft": 0, "marginRight": 0})
                data = base64.b64decode(result.get("data", ""), validate=True)
                _validate_pdf(data, status["page_count"])
                if any(event.get("method") == "Runtime.exceptionThrown" for event in client.events):
                    raise word.ExportError("PDF 排版发生错误，未下载不完整的试卷。", 422)
                return data, status["page_count"]
            finally:
                # Cleanup failures must not replace validated bytes or the main error.
                _cleanup_browser(client, process)
                client = process = None
    except word.ExportError:
        raise
    except (OSError, ValueError, ConnectionError, TimeoutError, KeyError, StopIteration, IndexError, subprocess.SubprocessError):
        raise word.ExportError("本机 PDF 排版连接失败，请重试；也可先导出 Word。", 503) from None
    finally:
        _render_lock.release()


def export(payload):
    if not isinstance(payload, dict) or set(payload) - {"ids", "title", "print_options", "rendered_fields", "format", "solutions", "preview_page_count"} or payload.get("format", "pdf") != "pdf":
        raise word.ExportError("PDF 导出内容格式不正确")
    try:
        ids = normalize_ids(payload.get("ids"))
        title = _title(payload.get("title", "练习"))
        options = _print_options(payload.get("print_options", {}), {"answer_layout": "appendix"}, ids=ids)
    except (BrowseError, DraftError) as error:
        raise word.ExportError(str(error)) from None
    if not ids or len(ids) != len(payload["ids"]):
        raise word.ExportError("请检查选题编号后重新打开组卷")
    word._utf16_map(title, "卷名")
    previewed = payload.get("preview_page_count")
    if previewed is not None and (type(previewed) is not int or not 1 <= previewed <= MAX_PAGES):
        raise word.ExportError("请重新打开组卷预览后再导出 PDF")
    captured, use_ai = word._capture(ids, payload.get("rendered_fields"), options, "pdf", solutions=payload.get("solutions"))
    document = _html_document(captured, title, options)
    data, pages = _render(document)
    word._recheck(captured, options, use_ai)
    # The teacher checked the paper on screen and is about to print this file.
    # A different page count means the file is not what was checked, so it is
    # refused rather than downloaded; re-opening the preview is one click.
    if previewed is not None and previewed != pages:
        raise word.ExportError("排版有变化，请重新预览后导出。", 409)
    return data, word.safe_filename(title) + ".pdf", len(ids), pages


@csrf_exempt
def export_pdf_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _guard
    rejected = _guard(request)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "导出需从本机题库页面发起"}, status=403)
    try:
        payload = _body(request, limit=word.MAX_BODY)
        if payload is None:
            raise word.ExportError("PDF 导出内容格式不正确或超过 2 MiB，请减少选题", 413 if len(request.body) > word.MAX_BODY else 400)
        data, filename, count, pages = export(payload)
    except RequestDataTooBig:
        return JsonResponse({"error": "导出内容超过 2 MiB，请减少选题、分批导出"}, status=413)
    except word.ExportError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = "attachment; filename=practice.pdf; filename*=UTF-8''" + quote(filename)
    response["X-Question-Count"], response["X-Page-Count"] = str(count), str(pages)
    response["Cache-Control"] = "no-store"
    from .export_preferences import deliver
    return deliver(request, response, data, filename, count=count, pages=pages)
