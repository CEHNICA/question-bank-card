"""把一道题做成图片放进剪贴板。

老师的需求：点一下「复制」，这道题变成一张图片；题里带的配图另外单独一张。
粘到 OneNote 里，题目和配图是两个独立对象，能各自拖到想要的位置。

做法分两段：

1. **题面渲染成 PNG** —— 复用组卷导出 PDF 那条无头浏览器管线（Edge/Chrome + KaTeX
   字体内嵌），只是把 ``Page.printToPDF`` 换成 ``Page.captureScreenshot``。
   按老师选的 A 方案，**配图不画进题面图里**，配图原图另外单独给一张。

2. **写剪贴板** —— 一次写三种格式：
   - ``HTML Format``：HTML 里放两张 ``<img>``（题面一张、配图一张）。Word / OneNote
     优先认这个，粘出来是**两个独立对象**，能分别拖位置 —— 这是老师要的。
   - ``PNG``：题面图本身（保留透明通道的程序认这个）。
   - ``CF_DIB``：题面图的位图（画图、聊天软件认这个）。

只放一种格式不行：只放位图就只有一张图（配图丢了），只放 HTML 有些程序不认。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import library_export as word
from .library_pdf import (
    _CDP,
    _assets,
    _browser_path,
    _cleanup_browser,
    _launch_browser,
    _wait_debug_port,
    _wait_page_target,
)

logger = logging.getLogger("core.clipboard")

# 一道题最多带多少张配图。超过这个数不是正常题目，挡住比默默截断好。
MAX_FIGURES = 16
# 题面图的宽度（CSS 像素）。和组卷预览一个量级，公式才不会挤成一团。
RENDER_WIDTH = 760
# 截图倍率。2 倍在 OneNote 里放大看公式仍然清楚。
RENDER_SCALE = 2
# 无头浏览器最多给多少秒。一道题排一页，60 秒绰绰有余。
DEADLINE_SECONDS = 60
# 剪贴板上一道题的总量上限。太大 Office 会拒绝粘贴。
MAX_TOTAL_BYTES = 32 * 1024 * 1024

_render_lock = threading.Lock()


class ClipboardError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _fail(message, status=400):
    raise ClipboardError(message, status)


# ---------------------------------------------------------------------------
# 1. 题面渲染
# ---------------------------------------------------------------------------

_DRIVER = r"""
window.__qbShotStatus = {ready:false};
(async () => {
  const input = JSON.parse(document.getElementById('examData').textContent);
  const source = document.getElementById('shotSource');
  const node=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  for (const item of input.items) {
    const block=node('div','print-question');
    // 第二个参数直接是 content —— 传 item.content 会拿到 undefined。
    QBRender.renderQuestion(block,item,{showNumber:false,showAnswer:'none',resolveUrl:x=>x.file,resolveQuestionImageUrl:x=>x.file});
    source.append(block);
  }
  const images=[...source.querySelectorAll('img')];
  await Promise.all(images.map(async i=>{i.loading='eager';await i.decode();if(!i.naturalWidth)throw new Error('配图未完整载入');}));
  await document.fonts.ready;
  window.__qbShotStatus={ready:true};
})().catch(e=>{window.__qbShotStatus={ready:false,error:e.message||'题目排版未完成'};});
"""

# library.css 里 `.print-flow.exam-source` 是 visibility:hidden —— 那是给组卷导出
# 离屏测量用的。截图前必须把它变回可见，否则截出来全白（DOM 里却是有内容的）。
_FIXED_CSS = (
    "html,body{margin:0;background:#fff;padding:0}"
    "#shotSource{position:static;left:auto;top:auto;visibility:visible;"
    "display:block;width:__WIDTH__px;max-width:none;padding:24px;background:#fff;box-sizing:content-box}"
)


def _build_document(payload):
    """拼一个只放一道题、字体全内嵌的页面。"""
    css, scripts = _assets(Path(settings.FRONTEND_ROOT))
    data = json.dumps({"items": [payload]}, ensure_ascii=False)
    # 这几个字符不转义就会提前结束 <script> 里的 JSON。
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    nonce = secrets.token_urlsafe(24)
    script_tags = "".join(
        f'<script nonce="{nonce}" src="data:application/javascript;base64,'
        + base64.b64encode(script.encode()).decode("ascii")
        + '"></script>'
        for script in scripts
    )
    fixed = _FIXED_CSS.replace("__WIDTH__", str(RENDER_WIDTH))
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>题目图片</title><style>'
        + css
        + fixed
        + "</style></head><body>"
        + '<div id="shotSource" class="print-flow exam-source"></div>'
        + f'<script nonce="{nonce}" id="examData" type="application/json">{data}</script>'
        + script_tags
        + f'<script nonce="{nonce}">{_DRIVER}</script></body></html>'
    )


def _element_box(client):
    """量出题面那一块的屏幕位置和大小。"""
    value = client.call(
        "Runtime.evaluate",
        {
            "expression": (
                "(()=>{const e=document.getElementById('shotSource');"
                "if(!e)return null;const r=e.getBoundingClientRect();"
                "return {x:r.x+window.scrollX,y:r.y+window.scrollY,w:r.width,h:r.height};})()"
            ),
            "returnByValue": True,
        },
    )
    box = value.get("result", {}).get("value")
    if not isinstance(box, dict):
        return None
    if not isinstance(box.get("w"), (int, float)) or not isinstance(box.get("h"), (int, float)):
        return None
    if box["w"] <= 0 or box["h"] <= 0:
        return None
    return box


def _render_question_png(payload):
    """把一道题排好版截成 PNG，返回图片字节。"""
    from urllib.request import ProxyHandler, build_opener

    document = _build_document(payload)
    try:
        executable = _browser_path()
    except word.ExportError as error:
        _fail(str(error), 503)
    deadline = time.monotonic() + DEADLINE_SECONDS
    profile = Path(tempfile.mkdtemp(prefix="tiyouju-shot-")) / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    process = _launch_browser(executable, profile)
    client = None
    box = None
    try:
        port = _wait_debug_port(profile, process, deadline)
        opener = build_opener(ProxyHandler({}))
        target = _wait_page_target(opener, port, process, deadline)
        url = target.get("webSocketDebuggerUrl") or ""
        match = re.fullmatch(rf"ws://(?:127\.0\.0\.1|localhost):{port}(/devtools/page/[A-Za-z0-9_-]+)", url)
        if not match:
            _fail("本机排版连接失败，请重试", 503)
        client = _CDP(port, match.group(1), deadline)
        client.call("Page.enable")
        client.call("Runtime.enable")
        client.call("Network.enable")
        # 只准用页面里内嵌的资源，不许它出去连网。
        client.call(
            "Network.setBlockedURLs", {"urls": ["http://*", "https://*", "ws://*", "wss://*", "ftp://*", "file://*"]}
        )
        frame = client.call("Page.getFrameTree")["frameTree"]["frame"]["id"]
        client.call("Page.setDocumentContent", {"frameId": frame, "html": document})
        while True:
            value = client.call(
                "Runtime.evaluate", {"expression": "window.__qbShotStatus || {ready:false}", "returnByValue": True}
            )
            status = value.get("result", {}).get("value", {})
            if isinstance(status, dict) and status.get("error"):
                _fail("题目排版没完成：" + str(status["error"])[:80], 422)
            if isinstance(status, dict) and status.get("ready"):
                break
            if time.monotonic() >= deadline:
                _fail("题目排版超时，请重试", 504)
            time.sleep(0.05)

        box = _element_box(client)
        if box is None:
            _fail("题目排版后没有内容，无法出图", 422)
        # 视口先设成内容大小 + 一点余量，然后整屏截图，最后按倍数裁。
        # captureScreenshot 的 clip/scale 会和 deviceScaleFactor 再乘一遍，
        # 尺寸会不对，所以这里不传 clip。
        client.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": max(640, int(box["w"]) + 80),
                "height": max(480, int(box["h"]) + 80),
                "deviceScaleFactor": RENDER_SCALE,
                "mobile": False,
            },
        )
        time.sleep(0.35)
        box = _element_box(client)
        if box is None:
            _fail("题目排版后没有内容，无法出图", 422)
        shot = client.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        encoded = shot.get("data")
        if not isinstance(encoded, str) or not encoded:
            _fail("题目图片没有生成，请重试", 422)
        raw = base64.b64decode(encoded)
    except ClipboardError:
        raise
    except word.ExportError as error:
        _fail(str(error), error.status)
    except Exception as error:  # noqa: BLE001 - 意外错也要说人话
        logger.exception("render question image failed: %s", type(error).__name__)
        _fail("题目图片没有生成，请重试", 500)
    finally:
        _cleanup_browser(client, process)

    return _crop_to_content(raw, box)


def _crop_to_content(raw, box):
    """按测出来的位置裁出题面，再裁掉四周白边。"""
    from PIL import Image, ImageChops

    try:
        with Image.open(io.BytesIO(raw)) as image:
            rgb = image.convert("RGB")
    except Exception:  # noqa: BLE001
        _fail("题目图片没有生成，请重试", 422)
    scale = RENDER_SCALE
    left = max(0, int(box["x"] * scale))
    top = max(0, int(box["y"] * scale))
    right = min(rgb.width, int((box["x"] + box["w"]) * scale))
    bottom = min(rgb.height, int((box["y"] + box["h"]) * scale))
    if right <= left or bottom <= top:
        _fail("题目图片没有生成，请重试", 422)
    region = rgb.crop((left, top, right, bottom))
    # 裁掉白边：题面本身是白底，边缘不该留一圈空白。
    background = Image.new("RGB", region.size, (255, 255, 255))
    bounds = ImageChops.difference(region, background).getbbox()
    if bounds:
        region = region.crop(bounds)
    if region.width < 1 or region.height < 1:
        _fail("题目图片没有生成，请重试", 422)
    buffer = io.BytesIO()
    region.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 2. 剪贴板
# ---------------------------------------------------------------------------


def _dib_bytes(data):
    """PNG -> CF_DIB（BITMAPINFOHEADER + BGR 像素，行距 4 字节对齐）。

    **行序按 DIB 规范来：正 biHeight = 像素自下而上排。**
    这是老师报「粘出来上下颠倒」的根因：原来写的是「正 biHeight + 自上而下」，
    一种自相矛盾的组合 —— 任何按规范读的程序都会把图翻过来。

    事实基准是 Word 自己：它写进剪贴板的 CF_DIB 同样是正 biHeight + 自下而上
    （32bpp BI_BITFIELDS；24bpp 同样合法）。实测把行序改成自下而上后，
    直接读 CF_DIB 的程序和 Windows 合成出来的 CF_BITMAP 两边朝向都正确。
    """
    from PIL import Image

    with Image.open(io.BytesIO(data)) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        raw = rgb.tobytes("raw", "BGR")
    stride = (width * 3 + 3) // 4 * 4
    # 补白要【逐行】补，不能加在整块缓冲末尾 —— 加在末尾的话行边界就全错了。
    rows = []
    for index in range(height):
        row = raw[index * width * 3 : (index + 1) * width * 3]
        rows.append(row + b"\x00" * (stride - len(row)))
    # 再按 DIB 规范把行倒过来：正 biHeight = 像素自下而上排。
    padded = b"".join(reversed(rows))
    header = (
        (40).to_bytes(4, "little")
        + width.to_bytes(4, "little")
        + height.to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (24).to_bytes(2, "little")
        + (0).to_bytes(4, "little")
        + len(padded).to_bytes(4, "little")
        + (2835).to_bytes(4, "little")
        + (2835).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + (0).to_bytes(4, "little")
    )
    return header + padded


def _display_width(data):
    """图在剪贴板里显示多宽（CSS 像素）。

    写死宽度有两个用处：老师看到的大小可控；Office 粘贴时也有个明确的显示尺寸。
    """
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception:  # noqa: BLE001
        return 480
    if width < 1 or height < 1:
        return 480
    # 最宽 480 像素（约 12.7 厘米，纸面上够看），最窄 120 像素。
    return max(120, min(480, width))


def _html_clipboard_bytes(images, paths=None):
    """HTML Format：几张图就放几个 <img>，粘出来是几个独立对象。

    偏移量头要自己算（StartHTML/EndHTML/StartFragment/EndFragment 四个 10 位数），
    按 UTF-8 字节算 —— 算错了 Word 就贴不出来东西。

    **图用本地文件引用，不用 data URI。** 这是实测出来的：
    data URI 里的图是异步解码的，Office 粘贴时经常只来得及贴出第一张
    （同一份剪贴板连续粘 5 次，有 1 次只出来 1 个对象）。
    换成 file:// 引用本机文件，同一份剪贴板连粘 5 次都是 2 个对象。
    浏览器复制图片时走的也是这条 —— Office 本来就认本地文件引用。
    """
    from urllib.parse import quote

    parts = []
    for index, data in enumerate(images):
        if paths and index < len(paths):
            source = "file:///" + quote(paths[index].replace("\\", "/"), safe="/:")
        else:
            source = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
        parts.append(
            '<p><img src="'
            + source
            + '" style="width:'
            + str(_display_width(data))
            + 'px;max-width:100%;height:auto"></p>'
        )
    fragment = "".join(parts)
    head = "<html><body><!--StartFragment-->" + fragment + "<!--EndFragment--></body></html>"
    prefix = (
        "Version:1.0\r\n"
        "StartHTML:{start:>010}\r\n"
        "EndHTML:{end:>010}\r\n"
        "StartFragment:{frag:>010}\r\n"
        "EndFragment:{fend:>010}\r\n"
    )

    def render(start, end, frag, fend):
        return prefix.format(start=start, end=end, frag=frag, fend=fend) + head

    body = head.encode("utf-8")
    template = render(0, 0, 0, 0).encode("utf-8")
    frag_start = template.index(b"<!--StartFragment-->") + len(b"<!--StartFragment-->")
    frag_end = template.index(b"<!--EndFragment-->")
    start_html = template.index(b"<html>")
    end_html = start_html + len(body)
    return render(start_html, end_html, frag_start, frag_end).encode("utf-8")


def _write_clipboard(images, paths=None):
    """把几张图写进剪贴板（HTML + PNG + DIB 三种格式）。"""
    import pythoncom
    import win32clipboard

    html_format = win32clipboard.RegisterClipboardFormat("HTML Format")
    png_format = win32clipboard.RegisterClipboardFormat("PNG")
    pythoncom.CoInitialize()
    opened = False
    for _ in range(20):
        try:
            win32clipboard.OpenClipboard()
            opened = True
            break
        except Exception:  # noqa: BLE001 - 别的程序短暂占着剪贴板
            time.sleep(0.1)
    if not opened:
        _fail("剪贴板正被别的程序占用，请稍后再试一次", 503)
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(html_format, _html_clipboard_bytes(images, paths))
        win32clipboard.SetClipboardData(png_format, images[0])
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, _dib_bytes(images[0]))
    except ClipboardError:
        raise
    except Exception as error:  # noqa: BLE001
        logger.exception("write clipboard failed: %s", type(error).__name__)
        _fail("剪贴板没有写入成功，请重试", 500)
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 3. 对外
# ---------------------------------------------------------------------------


def _collect(publication):
    """把入库版拆成「题面要渲染成什么」和「配图原图是哪几张」。

    按老师选的 A 方案：题面图里不放配图，配图原图另外单独给。
    原图题例外 —— 它的题干本身就是截图，没有独立的配图可分，
    截图要照原样画进题面图里。
    """
    content = publication.content
    if not isinstance(content, dict) or not isinstance(content.get("stem"), str):
        _fail("这道题的题目快照不完整，无法出图", 409)
    body_mode = content.get("body_mode", "text")
    if body_mode not in {"text", "source_image"}:
        _fail("这道题的正文类型无法识别，无法出图", 409)
    options = content.get("options") or {}
    if not isinstance(options, dict) or set(options) - set("ABCDE"):
        _fail("这道题的选项快照不完整，无法出图", 409)
    for text in options.values():
        if not isinstance(text, str):
            _fail("这道题的选项快照不完整，无法出图", 409)

    try:
        images = word._images(publication, "这道题")
    except word.ExportError as error:
        _fail(str(error), error.status)

    if body_mode == "source_image":
        # 原图题：题干就是这些截图，没有单独的配图。
        payload = {
            "body_mode": "source_image",
            "question_images": [
                {
                    "file": "data:image/png;base64," + base64.b64encode(image["bytes"]).decode("ascii"),
                    "width": image["size"][0],
                    "height": image["size"][1],
                    "order": order,
                }
                for order, image in enumerate(images)
            ],
            "figures": [],
        }
        return payload, []

    payload = {
        "stem": content.get("stem") or "",
        "options": {key: str(value) for key, value in options.items()},
        "origin": content.get("origin") or "",
        "figures": [],
    }
    return payload, [image["bytes"] for image in images]


def copy_question(publication):
    """把一道题做成图片放进剪贴板。返回给界面的一句话说明。"""
    payload, figures = _collect(publication)

    if not _render_lock.acquire(blocking=False):
        _fail("另一道题正在出图，请稍等一下再试", 409)
    try:
        question_png = _render_question_png(payload)
    finally:
        _render_lock.release()

    images = [question_png] + figures
    total = sum(len(image) for image in images)
    if total > MAX_TOTAL_BYTES:
        _fail("这道题的图片太大，放不进剪贴板", 413)
    # 图要先落成文件：剪贴板里的 HTML 用本地文件引用，Office 才粘得稳。
    folder = Path(tempfile.mkdtemp(prefix="tiyouju-clip-"))
    paths = []
    for index, image in enumerate(images):
        target = folder / ("image-%d.png" % index)
        target.write_bytes(image)
        paths.append(str(target))
    _write_clipboard(images, paths)
    if figures:
        return {
            "images": len(images),
            "message": "已复制：题目 1 张、配图 %d 张，粘到 OneNote 里能分开拖动。" % len(figures),
        }
    return {"images": 1, "message": "已复制：题目 1 张，粘到 OneNote 里就能用。"}


@csrf_exempt
def copy_question_view(request):
    """POST /api/library/copy-image"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .models import PublishedQuestion
    from .views import _body, _guard

    rejected = _guard(request)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "复制图片需从本机题库页面发起"}, status=403)
    payload = _body(request, limit=4096)
    if payload is None:
        return JsonResponse({"error": "请求内容格式不正确，请刷新题库后重试"}, status=400)
    publication_id = payload.get("publication_id")
    if not isinstance(publication_id, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", publication_id):
        return JsonResponse({"error": "这道题的编号无法识别，请刷新题库后重试"}, status=400)
    try:
        publication = PublishedQuestion.objects.filter(pk=publication_id).first()
        if publication is None:
            _fail("这道题已经不在题库里了，请刷新题库", 409)
        if publication.status != PublishedQuestion.Status.PUBLISHED:
            _fail("这道题已撤回或被替代，无法出图", 409)
        result = copy_question(publication)
    except RequestDataTooBig:
        return JsonResponse({"error": "请求内容太大，请重试"}, status=413)
    except ClipboardError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except Exception as error:  # noqa: BLE001 - 意外错也要说人话
        diagnostic = word.unexpected_export_error("题目图片", error)
        return JsonResponse({"error": word.export_failed_message("题目图片", diagnostic)}, status=500)
    response = JsonResponse(result)
    response["Cache-Control"] = "no-store"
    return response
