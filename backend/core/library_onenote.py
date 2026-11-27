"""OneNote 知识库联动模块：用于题目在 OneNote 中的答题创建、链接绑定与快速调起。"""

from __future__ import annotations

import base64
import html
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from . import library, qtypes
from .models import PublishedQuestion

logger = logging.getLogger("core.onenote")

PS_EXE = "powershell.exe"
# 避免无控制台桌面应用(QuestionBankCard.exe)拉起 powershell.exe 时弹出可见的蓝色控制台窗口。
CREATE_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 安装包 / 源码自带的桥接脚本优先；可用环境变量或破甲目录 on.ps1 覆盖。
BUNDLED_BRIDGE_PATH = Path(__file__).resolve().parent / "onenote_bridge.ps1"
LEGACY_BRIDGE_PATH = Path(r"C:\Users\17321\Documents\破甲\onenote\on.ps1")


class OneNoteError(Exception):
    """OneNote 操作异常。"""


def get_bridge_script() -> Path:
    """获取 PowerShell COM 桥接脚本路径。"""
    override = (os.environ.get("TIYOUJU_ONENOTE_BRIDGE") or "").strip()
    if override:
        path = Path(override)
        if path.is_file():
            return path
    candidates = [BUNDLED_BRIDGE_PATH]
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidates.append(Path(frozen) / "backend" / "core" / "onenote_bridge.ps1")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if LEGACY_BRIDGE_PATH.is_file():
        return LEGACY_BRIDGE_PATH
    raise OneNoteError("未找到 OneNote PowerShell 桥接脚本，请检查配置文件。")


def _ensure_desktop_window() -> bool:
    """在 Windows 上清理卡死的无头 /hyperlink 进程并确保桌面版 OneNote 主窗口可见。"""
    if os.name != "nt":
        return True
    try:
        data = call_bridge("ensure_window", timeout_seconds=12)
        return bool(data.get("ready")) if isinstance(data, dict) else False
    except Exception as exc:
        logger.warning("ensure OneNote desktop window failed: %s", exc)
        return False


def shell_open_onenote(url: str) -> bool:
    """用系统协议打开 onenote: 链接。先确保桌面主窗口存在，避免直接触发 /hyperlink 卡成无头进程。"""
    href = (url or "").strip()
    if not href:
        return False
    _ensure_desktop_window()
    try:
        os.startfile(href)
        return True
    except OSError as exc:
        logger.warning("shell open onenote failed: %s", exc)
        if os.name == "nt":
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(
                    ["cmd", "/c", "start", "", href],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
                return True
            except OSError as nested:
                logger.warning("cmd start onenote also failed: %s", nested)
        return False


def call_bridge(op: str, timeout_seconds: int = 15, **params: Any) -> Any:
    """通过 PowerShell COM 桥接脚本执行 OneNote 操作。

    Args:
        op: 操作名称，如 'list', 'create', 'navigate', 'hyperlink'
        timeout_seconds: 超时时间（秒）
        **params: 传递给操作的命名参数

    Returns:
        OneNote 操作返回的数据内容

    Raises:
        OneNoteError: 当 COM 操作失败或超时时抛出
    """
    script_path = get_bridge_script()
    payload = {"op": op, "params": params}

    with tempfile.TemporaryDirectory() as temp_dir:
        req_file = os.path.join(temp_dir, "req.json")
        resp_file = os.path.join(temp_dir, "resp.json")

        with open(req_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        try:
            proc = subprocess.run(
                [
                    PS_EXE,
                    "-NoProfile",
                    "-NonInteractive",
                    "-WindowStyle",
                    "Hidden",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                    req_file,
                    resp_file,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            logger.error("OneNote bridge call timed out on op=%s", op)
            raise OneNoteError("OneNote 响应超时，请确认 OneNote 客户端是否正常运行。") from exc
        except Exception as exc:
            logger.error("Failed to run PowerShell process: %s", exc)
            raise OneNoteError(f"启动 PowerShell 桥接失败: {exc}") from exc

        if not os.path.exists(resp_file):
            logger.error(
                "OneNote bridge produced no response (exit %s)\nstdout: %s\nstderr: %s",
                proc.returncode,
                proc.stdout[-1000:],
                proc.stderr[-1000:],
            )
            raise OneNoteError("OneNote COM 桥接未生成响应，请检查 OneNote 是否已正常安装。")

        try:
            with open(resp_file, encoding="utf-8") as f:
                resp = json.load(f)
        except Exception as exc:
            raise OneNoteError(f"解析 OneNote 响应数据失败: {exc}") from exc

    if not resp.get("ok"):
        err_msg = resp.get("error", "未知 OneNote COM 错误")
        logger.warning("OneNote bridge returned error for op=%s: %s", op, err_msg)
        raise OneNoteError(err_msg)

    return resp.get("data")


# 建页时 list/hierarchy 很贵；短时缓存目标分区，连续建页少等一整轮 COM。
_SECTION_CACHE: tuple[str, str, str, str] | None = None
_SECTION_CACHE_AT = 0.0
_SECTION_CACHE_TTL = 3600.0
_SECTION_CACHE_LOCK = threading.Lock()


def clear_section_cache() -> None:
    """测试或笔记本变更后丢掉分区缓存。"""
    global _SECTION_CACHE, _SECTION_CACHE_AT
    with _SECTION_CACHE_LOCK:
        _SECTION_CACHE = None
        _SECTION_CACHE_AT = 0.0


def resolve_target_section() -> tuple[str, str, str, str]:
    """寻找适合存放做题记录的笔记本与分区。

    优先级：
    1. 查找包含 '题有据' 的笔记本
    2. 查找 'math' 或 '高中数学思想导引' 笔记本
    3. 取第一个可用普通笔记本

    Returns:
        (notebook_id, section_id, notebook_name, section_name)
    """
    global _SECTION_CACHE, _SECTION_CACHE_AT
    now = time.monotonic()
    with _SECTION_CACHE_LOCK:
        if _SECTION_CACHE is not None and (now - _SECTION_CACHE_AT) < _SECTION_CACHE_TTL:
            return _SECTION_CACHE

    data = call_bridge("list")
    notebooks = data.get("notebooks") or []
    if not notebooks:
        raise OneNoteError("未检测到已打开的 OneNote 笔记本，请先在 OneNote 中打开或创建一个笔记本。")

    target_nb = None
    # 策略 1: 专用笔记本
    for nb in notebooks:
        name = nb.get("name", "")
        if "题有据" in name:
            target_nb = nb
            break

    # 策略 2: 数学或学科笔记本
    if target_nb is None:
        for nb in notebooks:
            name = nb.get("name", "").lower()
            if name in {"math", "高中数学思想导引", "数学"}:
                target_nb = nb
                break

    # 策略 3: 第一个有效笔记本
    if target_nb is None:
        target_nb = notebooks[0]

    nb_id = target_nb["id"]
    nb_name = target_nb["name"]

    # 读取该笔记本的分区列表（必须过滤掉回收站、只读、加密等不可写入的分区）
    hier = call_bridge("hierarchy", id=nb_id, scope=3).get("xml", "")
    valid_sections: list[tuple[str, str]] = []
    for match in re.finditer(r"<(?:one:)?Section\b([^>]*)>", hier):
        attr_str = match.group(1)
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', attr_str))
        sec_name = attrs.get("name", "")
        sec_id = attrs.get("ID", "")
        if not sec_id:
            continue

        sec_path = attrs.get("path", "").lower()
        # 跳过回收站及只读/锁定分区，避免 CreateNewPage 抛出 0x8004202E (hrInsertingIntoRecycleBin)
        if (
            attrs.get("isInRecycleBin", "").lower() == "true"
            or attrs.get("isDeletedPages", "").lower() == "true"
            or attrs.get("readOnly", "").lower() == "true"
            or attrs.get("locked", "").lower() == "true"
            or "onenote_recyclebin" in sec_path
            or "deletedpages" in sec_path
            or sec_name in {"删除的页面", "Deleted Pages"}
        ):
            continue

        valid_sections.append((sec_name, sec_id))

    if not valid_sections:
        raise OneNoteError(f"笔记本「{nb_name}」中没有可写入的普通分区，请先在其中新建一个分区。")

    # 优先匹配适合做题记录的分区名称，其次匹配包含“题”字的分区，最后回退到第一个普通分区
    target_sec_id = None
    target_sec_name = None
    preferred_names = ("题有据", "做题解答", "题库答题", "数学试题", "试题", "草稿过程", "错题解答")
    for preferred in preferred_names:
        for sec_name, sec_id in valid_sections:
            if sec_name == preferred:
                target_sec_name = sec_name
                target_sec_id = sec_id
                break
        if target_sec_id is not None:
            break

    if target_sec_id is None:
        for sec_name, sec_id in valid_sections:
            if "题" in sec_name:
                target_sec_name = sec_name
                target_sec_id = sec_id
                break

    if target_sec_id is None or target_sec_name is None:
        target_sec_name, target_sec_id = valid_sections[0]

    result = (str(nb_id), str(target_sec_id), str(nb_name), str(target_sec_name))
    with _SECTION_CACHE_LOCK:
        _SECTION_CACHE = result
        _SECTION_CACHE_AT = time.monotonic()
    return result


def _display_source_name(name: str) -> str:
    """页面上展示的来源名：去掉文件后缀，笔记本列表更好读。"""
    text = str(name or "").strip() or "未命名试卷"
    cleaned = re.sub(r"\.(?:pdf|docx?|pptx?)$", "", text, flags=re.IGNORECASE).strip()
    return cleaned or text


def _cite_source(name: str) -> str:
    """能写进标题的出处。微信图片、整夹照片这类文件名不写。"""
    raw = str(name or "").strip()
    if not raw:
        return ""
    text = _display_source_name(raw)
    if text in {"未命名试卷", "未命名"}:
        return ""
    if re.search(r"等\s*\d+\s*张(?:照片|图片)\s*$", text):
        return ""
    if re.match(r"^(?:微信图片|微信图像|IMG|DSC|PXL|Screenshot|屏幕截图)(?:[_\-\s\d]|$)", text, re.IGNORECASE):
        return ""
    return text


def _page_title(_publication: PublishedQuestion | None = None) -> str:
    """OneNote 页标题：按创建时间命名，列表里最好认、最好排。"""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")


def format_question_body(publication: PublishedQuestion) -> tuple[str, str]:
    """将题目信息格式化为适合在 OneNote 中做题的页面标题与正文模板。

    Returns:
        (page_title, page_text)
    """
    content = publication.content or {}
    source_name = _display_source_name(publication.source_filename or "未命名试卷")
    number = publication.number
    title = _page_title(publication)

    qtype_label = qtypes.label(publication.question_type)
    origin = content.get("origin") or ""

    meta_lines = [
        f"【来源】：{source_name}",
        f"【题号】：第 {number} 题" if number else "【题号】：未标号",
        f"【题型】：{qtype_label}",
    ]
    if origin:
        meta_lines.append(f"【题源】：{origin}")

    meta_block = "\n".join(meta_lines)

    stem = content.get("stem") or "(无文字题干)"
    options = content.get("options") or {}

    opt_lines = []
    if isinstance(options, dict) and options:
        for key in sorted(options.keys()):
            opt_lines.append(f"{key}. {options[key]}")

    options_block = "\n".join(opt_lines) if opt_lines else ""

    text_parts = ["==== 题有据 · 题目信息 ====", meta_block, "", "==== 题面 ====", stem]
    if options_block:
        text_parts.extend(["", options_block])

    text_parts.extend(
        [
            "",
            "==================================================",
            "【我的解答过程与思考草稿】",
            "（请在此处书写推导步骤、绘制图示或记录解题要点）",
            "",
        ]
    )

    page_text = "\n".join(text_parts)
    return title, page_text


# ---------------- 题面渲染与页面排版 ----------------

ONE_NS = "http://schemas.microsoft.com/office/onenote/2013/onenote"
PAGE_ID_PLACEHOLDER = "__QB_PAGE_ID__"
BACKLINK_SCHEME = "tiyouju"
# OneNote 页面里题面图的最大显示宽度（磅）。再宽在常规窗口里就要横向滚动了。
MAX_IMAGE_WIDTH_PT = 520.0
DEFAULT_OUTLINE_X_PT = 36.0
DEFAULT_OUTLINE_Y_PT = 86.0
DEFAULT_OUTLINE_WIDTH_PT = 540.0
FALLBACK_OUTLINE_WIDTH_PT = 420.0
FALLBACK_INK_WIDTH_PT = 240.0
# 新一刷插在当前整页最右侧内容再往右留白 240pt 处，聚焦时左侧旧解法落在视野外，天然防剧透。
SAFE_HORIZONTAL_GAP_PT = 240.0

# NOTE: 只在题面图渲染失败时才走文字兜底，所以这里只覆盖中学数学最常见的命令，
# 目标是「读得懂」，不是完整的 LaTeX 排版。
_LATEX_SYMBOLS = {
    r"\triangle": "△",
    r"\angle": "∠",
    r"\perp": "⊥",
    r"\parallel": "∥",
    r"\cdot": "·",
    r"\times": "×",
    r"\div": "÷",
    r"\pm": "±",
    r"\leqslant": "≤",
    r"\geqslant": "≥",
    r"\leq": "≤",
    r"\geq": "≥",
    r"\le": "≤",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\ne": "≠",
    r"\approx": "≈",
    r"\infty": "∞",
    r"\pi": "π",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\theta": "θ",
    r"\lambda": "λ",
    r"\omega": "ω",
    r"\in": "∈",
    r"\cup": "∪",
    r"\cap": "∩",
    r"\subseteq": "⊆",
    r"\subset": "⊂",
    r"\varnothing": "∅",
    r"\emptyset": "∅",
    r"\circ": "°",
    r"\Rightarrow": "⇒",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\cong": "≅",
    r"\sim": "∽",
    r"\because": "∵",
    r"\therefore": "∴",
}


def latex_to_plain(text: str) -> str:
    """把题干里的 LaTeX 粗略转成可读的 Unicode 文本（仅用于渲染失败时的兜底）。"""
    if not text:
        return ""
    result = text
    # 先处理带参数的结构，再处理单个符号，避免 \sqrt 被符号表误伤
    for _ in range(3):
        result = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", result)
        result = re.sub(r"\\sqrt\s*\[([^{}\]]*)\]\s*\{([^{}]*)\}", r"\1√(\2)", result)
        result = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"√\1", result)
    result = re.sub(r"\\(?:overline|vec|overrightarrow)\s*\{([^{}]*)\}", r"\1", result)
    result = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\s*\{([^{}]*)\}", r"\1", result)
    for command in sorted(_LATEX_SYMBOLS, key=len, reverse=True):
        result = re.sub(re.escape(command) + r"(?![A-Za-z])", _LATEX_SYMBOLS[command], result)
    result = result.replace(r"\left", "").replace(r"\right", "")
    result = re.sub(r"\^\{\\circ\}|\^\\circ", "°", result)
    result = re.sub(r"\^\{([^{}]*)\}", r"^\1", result)
    result = re.sub(r"_\{([^{}]*)\}", r"_\1", result)
    result = result.replace(r"\{", "{").replace(r"\}", "}").replace(r"\,", " ").replace(r"\;", " ")
    result = result.replace("$", "")
    return result


def cached_question_image(publication: PublishedQuestion) -> bytes | None:
    """只读已有题面图缓存，绝不启动无头浏览器。"""
    from . import library_clipboard as clip

    cache_file = clip._cache_path(publication, mode="combined")
    try:
        if cache_file.is_file() and cache_file.stat().st_size > 0:
            return cache_file.read_bytes()
    except OSError:
        return None
    return None


def _source_image_png(publication: PublishedQuestion) -> bytes | None:
    """原图题直接读取本机已入库的原卷裁片（多段时纵向拼接），无需启动无头浏览器。"""
    content = publication.content or {}
    if content.get("body_mode") != "source_image":
        return None
    items = content.get("question_images") or []
    if not isinstance(items, list) or not items:
        return None
    from django.conf import settings
    from PIL import Image

    folder = Path(settings.DATA_ROOT) / "library" / str(publication.pk)
    paths: list[Path] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or "")
        if not name or Path(name).name != name:
            continue
        candidate = folder / name
        if candidate.is_file():
            paths.append(candidate)
    if not paths:
        return None
    if len(paths) == 1:
        try:
            return paths[0].read_bytes()
        except OSError:
            return None
    images: list[Image.Image] = []
    try:
        for path in paths:
            with Image.open(path) as opened:
                images.append(opened.convert("RGB"))
        gap = 12
        width = max(item.width for item in images)
        height = sum(item.height for item in images) + gap * (len(images) - 1)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        y = 0
        for item in images:
            canvas.paste(item, (0, y))
            y += item.height + gap
        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except OSError:
        return None
    finally:
        for item in images:
            item.close()


def render_question_image(publication: PublishedQuestion) -> bytes | None:
    """复用「复制题目图片」的无头浏览器管线，把题面（含公式与配图）渲染成 PNG。

    渲染失败只记日志并返回 None，由调用方退回文字版，不让建页整体失败。
    """
    from . import library_clipboard as clip

    cached = cached_question_image(publication)
    if cached is not None:
        return cached
    source_png = _source_image_png(publication)
    if source_png is not None:
        return source_png

    cache_file = clip._cache_path(publication, mode="combined")
    try:
        payload, _figures = clip._collect(publication, mode="combined")
    except clip.ClipboardError as err:
        logger.warning("OneNote 题面图收集失败 %s: %s", publication.pk, err)
        return None

    # NOTE: 与「复制题目图片」共用同一把锁，避免两个无头浏览器同时抢资源；最多等 30 秒
    if not clip._render_lock.acquire(timeout=30):
        logger.warning("OneNote 题面图渲染排队超时 %s", publication.pk)
        return None
    try:
        png = clip._render_question_png(payload)
    except clip.ClipboardError as err:
        logger.warning("OneNote 题面图渲染失败 %s: %s", publication.pk, err)
        return None
    finally:
        clip._render_lock.release()

    try:
        cache_file.write_bytes(png)
    except OSError:
        pass
    return png


def _ensure_protocol_background() -> None:
    try:
        from . import tiyouju_protocol

        tiyouju_protocol.ensure_registered()
    except Exception as exc:  # noqa: BLE001
        logger.warning("tiyouju:// 协议注册失败: %s", exc)


def _backfill_page_image(publication_id: str, page_id: str, section_id: str, title: str) -> None:
    """建页已经打开后，后台再补题面图，避免无头渲染拖住首次打开。"""
    try:
        publication = PublishedQuestion.objects.get(pk=publication_id)
    except PublishedQuestion.DoesNotExist:
        return
    png = render_question_image(publication)
    if not png:
        return
    try:
        page_xml = build_page_xml(publication, title, png)
        call_bridge("create_xml", timeout_seconds=40, sectionId=section_id, pageXml=page_xml, pageId=page_id)
    except Exception:
        logger.exception("OneNote 后台补题面图失败 publication=%s page=%s", publication_id, page_id)


def _cdata(html_text: str) -> str:
    """OneNote 文本节点内容是 CDATA 包裹的 HTML。"""
    return "<![CDATA[" + html_text.replace("]]>", "]]&gt;") + "]]>"


def _text_oe(html_text: str) -> str:
    return f"<one:OE><one:T>{_cdata(html_text)}</one:T></one:OE>"


def _image_oe(png: bytes) -> str:
    """把 PNG 作为行内图片放进大纲，保证和上下文字按顺序排列、不会叠在一起。"""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        pixel_width, pixel_height = image.size
    # 渲染倍率为 2，CSS 像素再换算成磅（×0.75）
    width_pt = pixel_width / 2 * 0.75
    height_pt = pixel_height / 2 * 0.75
    if width_pt > MAX_IMAGE_WIDTH_PT:
        height_pt = height_pt * MAX_IMAGE_WIDTH_PT / width_pt
        width_pt = MAX_IMAGE_WIDTH_PT
    data = base64.b64encode(png).decode("ascii")
    return (
        '<one:OE><one:Image format="png">'
        f'<one:Size width="{width_pt:.1f}" height="{height_pt:.1f}" isSetByUser="true"/>'
        f"<one:Data>{data}</one:Data>"
        "</one:Image></one:OE>"
    )


def backlink_url(publication: PublishedQuestion) -> str:
    """OneNote 页面里「回到题有据」的链接。

    题有据每次启动端口可能不同，所以不写死 http 地址，而是走本机注册的自定义协议，
    由协议处理脚本临时查找当前运行端口。
    """
    return f"{BACKLINK_SCHEME}://question/{publication.pk}"


def _span(text: str, *, size: str = "11.0pt", color: str = "#1E293B", bold: bool = False) -> str:
    """OneNote 文本节点里实际生效的是这一小段 HTML。"""
    weight = "font-weight:bold;" if bold else ""
    return f'<span style="font-family:微软雅黑;font-size:{size};color:{color};{weight}">{html.escape(text)}</span>'


def _question_rows(publication: PublishedQuestion, png: bytes | None, *, header_label: str = "") -> list[str]:
    """构造一组 OneNote Outline 内部的行（回跳链接、可选刷次标头与题面图/文字兜底）。"""
    content = publication.content or {}
    link = html.escape(backlink_url(publication), quote=True)
    rows: list[str] = []
    if header_label:
        rows.append(
            _text_oe(
                f"{_span(header_label, size='11.0pt', color='#047857', bold=True)}"
                f'&nbsp;&nbsp;<a href="{link}">{_span("在题有据中打开此题", size="10.0pt", color="#4B5563")}</a>'
            )
        )
    else:
        rows.append(_text_oe(f'<a href="{link}">{_span("在题有据中打开此题", size="10.0pt", color="#4B5563")}</a>'))

    if png:
        rows.append(_image_oe(png))
    else:
        # 渲染失败的兜底：去掉 $ 并把常见 LaTeX 命令转成 Unicode，至少能读
        rows.append(_text_oe(_span("题面图生成失败，下面是文字版。", size="10.0pt", color="#B91C1C")))
        stem = latex_to_plain(content.get("stem") or "(无文字题干)")
        for line in stem.splitlines():
            if line.strip():
                rows.append(_text_oe(_span(line, size="12.0pt", color="#1E293B")))
        options = content.get("options") or {}
        if isinstance(options, dict):
            for key in sorted(options):
                rows.append(
                    _text_oe(_span(f"{key}. {latex_to_plain(str(options[key]))}", size="12.0pt", color="#1E293B"))
                )
    return rows


def build_page_xml(publication: PublishedQuestion, title: str, png: bytes | None, *, header_label: str = "") -> str:
    """生成 OneNote 答题页的完整页面 XML（页面 ID 用占位符，由桥接脚本建页后替换）。

    题号和出处放在 OneNote 自己的大标题里。正文只留回跳链接和题面，手写写在下面的空白处。
    """
    rows = _question_rows(publication, png, header_label=header_label)
    return (
        '<?xml version="1.0"?>'
        f'<one:Page xmlns:one="{ONE_NS}" ID="{PAGE_ID_PLACEHOLDER}">'
        f"<one:Title><one:OE><one:T>{_cdata(html.escape(title))}</one:T></one:OE></one:Title>"
        f'<one:Outline><one:Position x="{DEFAULT_OUTLINE_X_PT:.1f}" y="{DEFAULT_OUTLINE_Y_PT:.1f}" z="0"/>'
        f'<one:Size width="{DEFAULT_OUTLINE_WIDTH_PT:.1f}" height="240.0" isSetByUser="true"/>'
        f"<one:OEChildren>{''.join(rows)}</one:OEChildren></one:Outline>"
        "</one:Page>"
    )


def build_attempt_outline_xml(
    publication: PublishedQuestion, png: bytes | None, *, x: float, y: float, header_label: str
) -> str:
    """构造可追加到现有 OneNote 页面的独立 <one:Outline> 片段（带定位坐标与刷次标头）。"""
    rows = _question_rows(publication, png, header_label=header_label)
    return (
        f'<one:Outline xmlns:one="{ONE_NS}">'
        f'<one:Position x="{x:.1f}" y="{y:.1f}" z="0"/>'
        f'<one:Size width="{DEFAULT_OUTLINE_WIDTH_PT:.1f}" height="240.0" isSetByUser="true"/>'
        f"<one:OEChildren>{''.join(rows)}</one:OEChildren>"
        "</one:Outline>"
    )


def compute_page_right_edge(page_xml: str) -> float:
    """扫描 OneNote 页面 XML 中所有 Outline / InkDrawing / Image 等对象，计算最右侧横坐标 max_x。"""
    raw = str(page_xml or "").strip()
    if not raw:
        return 0.0
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return 0.0

    container_tags = {
        f"{{{ONE_NS}}}Outline",
        f"{{{ONE_NS}}}InkDrawing",
        f"{{{ONE_NS}}}Image",
        f"{{{ONE_NS}}}InsertedFile",
        f"{{{ONE_NS}}}MediaFile",
    }
    pos_tag = f"{{{ONE_NS}}}Position"
    size_tag = f"{{{ONE_NS}}}Size"
    ink_tag = f"{{{ONE_NS}}}InkDrawing"

    max_x = 0.0
    for elem in root.iter():
        if elem.tag not in container_tags:
            continue
        pos = elem.find(pos_tag)
        if pos is None:
            continue
        try:
            x = float(pos.attrib.get("x", "0"))
        except (TypeError, ValueError):
            continue
        if x < 0:
            x = 0.0
        size = elem.find(size_tag)
        width = 0.0
        if size is not None:
            try:
                width = float(size.attrib.get("width", "0"))
            except (TypeError, ValueError):
                width = 0.0
        if width <= 0:
            width = FALLBACK_INK_WIDTH_PT if elem.tag == ink_tag else FALLBACK_OUTLINE_WIDTH_PT
        right = x + width
        if right > max_x:
            max_x = right
    return round(max_x, 1)


def _format_attempt_stamp(dt: datetime | None = None) -> tuple[str, str, str]:
    local_dt = (dt or datetime.now()).astimezone()
    return local_dt.isoformat(), local_dt.strftime("%Y-%m-%d"), local_dt.strftime("%Y-%m-%d %H:%M")


def _save_onenote_record(publication: PublishedQuestion, onenote_record: dict[str, Any]) -> None:
    content = dict(publication.content or {})
    if not str(content.get("source_filename") or "").strip():
        content["source_filename"] = publication.source_filename or ""
        publication.content = content
        publication.save(update_fields=["content"])

    extras = dict(publication.extras or {})
    extras["onenote"] = onenote_record
    try:
        library.save_extras(publication, extras)
    except Exception:
        logger.exception("OneNote 页面已建好，但写入题库关联失败 publication=%s", publication.pk)
        publication.extras = extras
        publication.save(update_fields=["extras"])


def create_or_sync_page(publication: PublishedQuestion) -> dict[str, Any]:
    """在 OneNote 中为该题目创建答题页并尽快打开。

    已有关联则只打开，绝不 create_xml 覆盖原页（手写会没）。
    首次建页：有缓存才同步塞图；否则先文字页打开，题面图后台补。
    """
    existing_onenote = (publication.extras or {}).get("onenote") or {}
    if isinstance(existing_onenote, dict):
        existing_page_id = str(existing_onenote.get("page_id") or "").strip()
        existing_url = str(existing_onenote.get("url") or "").strip()
        if existing_page_id or existing_url:
            # 之后再点「去 OneNote」= 打开，不是重建。
            if existing_url:
                shell_open_onenote(existing_url)
            open_page(publication)
            return dict(existing_onenote)

    _nb_id, sec_id, nb_name, sec_name = resolve_target_section()
    title = _page_title(publication)
    created_iso, created_date, created_hm = _format_attempt_stamp()

    threading.Thread(target=_ensure_protocol_background, name="onenote-protocol-register", daemon=True).start()

    # 只吃缓存，绝不在建页热路径里启动无头浏览器；原图题直接读取本地裁片秒传。
    png = cached_question_image(publication) or _source_image_png(publication)
    need_backfill = png is None
    page_xml = build_page_xml(publication, title, png)

    bridge_params: dict[str, Any] = {"sectionId": sec_id, "pageXml": page_xml}

    data = call_bridge("create_xml", timeout_seconds=40, **bridge_params)
    page_id = data.get("id")
    if not page_id:
        raise OneNoteError("OneNote 未能返回新建页面的 ID。")

    link = data.get("link")
    if not link:
        link_data = call_bridge("hyperlink", id=page_id)
        link = link_data.get("link", "")

    outline_id = str(data.get("outlineId") or "").strip()
    focus_object_id = str(data.get("focusObjectId") or outline_id).strip()
    object_url = str(data.get("objectUrl") or link or "").strip()

    first_attempt = {
        "attempt": 1,
        "created_at": created_iso,
        "date": created_date,
        "label": f"{created_date} 笔记",
        "outline_id": outline_id,
        "focus_object_id": focus_object_id,
        "object_url": object_url,
        "x": DEFAULT_OUTLINE_X_PT,
        "y": DEFAULT_OUTLINE_Y_PT,
    }

    onenote_record = {
        "url": link,
        "page_id": page_id,
        "title": title,
        "notebook": nb_name,
        "section": sec_name,
        "updated_at": datetime.now(UTC).isoformat(),
        "attempts": [first_attempt],
    }

    _save_onenote_record(publication, onenote_record)

    if link:
        shell_open_onenote(link)
    threading.Thread(
        target=_refine_open, args=(page_id, None), name=f"onenote-create-open-{publication.pk}", daemon=True
    ).start()
    if need_backfill:
        threading.Thread(
            target=_backfill_page_image,
            args=(str(publication.pk), str(page_id), str(sec_id), title),
            name=f"onenote-backfill-{publication.pk}",
            daemon=True,
        ).start()

    return onenote_record


def start_attempt(publication: PublishedQuestion) -> dict[str, Any]:
    """发起新的一刷（首次则建页记第 1 刷；已有页则在当前最右侧安全区插入并对象级聚焦）。"""
    existing = (publication.extras or {}).get("onenote") or {}
    if not isinstance(existing, dict) or not str(existing.get("page_id") or "").strip():
        return create_or_sync_page(publication)

    page_id = str(existing["page_id"]).strip()
    page_data = call_bridge("page", id=page_id, timeout_seconds=20)
    page_xml = str((page_data or {}).get("xml") or "")
    if not page_xml:
        raise OneNoteError("未在 OneNote 中找到该题原页面；如需新建，请先取消当前关联后重试。")

    max_x = compute_page_right_edge(page_xml)
    insert_x = round(max_x + SAFE_HORIZONTAL_GAP_PT, 1) if max_x > 0 else DEFAULT_OUTLINE_X_PT
    insert_y = DEFAULT_OUTLINE_Y_PT

    raw_attempts = existing.get("attempts")
    prev_attempts: list[Any] = raw_attempts if isinstance(raw_attempts, list) else []
    max_num = max((int(a.get("attempt") or 0) for a in prev_attempts if isinstance(a, dict)), default=1)
    next_num = max(2, max_num + 1) if prev_attempts or existing.get("page_id") else 1

    created_iso, created_date, created_hm = _format_attempt_stamp()
    attempt_label = f"{created_date} 重做"

    png = cached_question_image(publication) or _source_image_png(publication) or render_question_image(publication)
    outline_xml = build_attempt_outline_xml(publication, png, x=insert_x, y=insert_y, header_label=attempt_label)

    append_res = call_bridge(
        "append_outline", timeout_seconds=40, pageId=page_id, outlineXml=outline_xml, marker=created_hm
    )

    outline_id = str((append_res or {}).get("outlineId") or "").strip()
    focus_object_id = str((append_res or {}).get("focusObjectId") or outline_id).strip()
    object_url = str((append_res or {}).get("objectUrl") or existing.get("url") or "").strip()

    attempt_entry = {
        "attempt": next_num,
        "created_at": created_iso,
        "date": created_date,
        "label": attempt_label,
        "outline_id": outline_id,
        "focus_object_id": focus_object_id,
        "object_url": object_url,
        "x": insert_x,
        "y": insert_y,
    }

    updated_attempts = [a for a in prev_attempts if isinstance(a, dict)] + [attempt_entry]
    updated_record = dict(existing)
    updated_record["updated_at"] = datetime.now(UTC).isoformat()
    updated_record["attempts"] = updated_attempts
    _save_onenote_record(publication, updated_record)
    return updated_record


def _refine_open(page_id: str | None, url: str | None, *, object_id: str | None = None) -> bool:
    """COM 切页并置前；若 COM 失败或未置前，再通过系统协议补开一次，返回是否至少有一种方式生效。"""
    navigated = False
    focused = False
    has_window = False
    if page_id:
        try:
            nav_kwargs: dict[str, Any] = {"id": str(page_id), "timeout_seconds": 12}
            if object_id:
                nav_kwargs["objectId"] = str(object_id)
            data = call_bridge("navigate", **nav_kwargs)
            navigated = bool(data.get("navigated", True)) if isinstance(data, dict) else True
            if isinstance(data, dict):
                focused = bool(data.get("focused"))
                has_window = bool(data.get("has_window", focused))
            else:
                has_window = focused
        except Exception as exc:
            logger.warning("Failed to navigate by page_id=%s object_id=%s: %s", page_id, object_id, exc)
    opened_by_shell = False
    if url and (not navigated or not focused):
        opened_by_shell = bool(shell_open_onenote(str(url)))
    return (navigated and (focused or has_window)) or opened_by_shell


def open_page(publication: PublishedQuestion, *, wait: bool = False, object_id: str | None = None) -> dict[str, Any]:
    """打开已关联解答页（支持传入 object_id 精确跳转到某一次刷题区域）。

    默认兼容立刻返回的异步路径；前端传入 wait=True 时同步完成 COM/协议唤起，
    让打开过程中的等待条持续显示到实际唤起结束。
    """
    extras = publication.extras or {}
    record = extras.get("onenote")
    if not record or not isinstance(record, dict):
        raise OneNoteError("该题目尚未关联 OneNote 笔记。")

    page_id = record.get("page_id")
    url = record.get("url")
    target_object_id = str(object_id or "").strip() or None
    if target_object_id:
        for attempt in record.get("attempts") or []:
            if isinstance(attempt, dict) and (
                attempt.get("focus_object_id") == target_object_id or attempt.get("outline_id") == target_object_id
            ):
                if attempt.get("object_url"):
                    url = attempt.get("object_url")
                break
    if not page_id and not url:
        raise OneNoteError("无法定位到该页面，链接或页面标识已丢失。")

    if wait:
        ok = _refine_open(page_id, url, object_id=target_object_id) if target_object_id else _refine_open(page_id, url)
        if not ok:
            raise OneNoteError("未能唤起 OneNote，请确认桌面版 OneNote 已启动并打开对应笔记本。")
        return {"ok": True, "opened": True, "focused": True, "onenote": record, "deferred": False}

    refine_kwargs = {"object_id": target_object_id} if target_object_id else {}
    threading.Thread(
        target=_refine_open,
        args=(page_id, url),
        kwargs=refine_kwargs,
        name=f"onenote-open-{publication.pk}",
        daemon=True,
    ).start()
    return {"ok": True, "opened": True, "focused": False, "onenote": record, "deferred": True}


def _crop_and_compress_onenote_page(img, *, max_long_side: int = 1280, pad_px: int = 24) -> bytes:
    """裁掉 OneNote 无限画布四周多余空白，保留舒适边距并压成紧凑 PNG。"""
    from PIL import Image, ImageOps

    rgb = ImageOps.exif_transpose(img).convert("RGB")
    gray = rgb.convert("L")
    # Treat near-white (>= 248) pixels as background so infinite-canvas margins crop cleanly.
    mask = gray.point(lambda p: 255 if p < 248 else 0, mode="1")
    bbox = mask.getbbox()
    if bbox:
        left = max(0, bbox[0] - pad_px)
        top = max(0, bbox[1] - pad_px)
        right = min(rgb.width, bbox[2] + pad_px)
        bottom = min(rgb.height, bbox[3] + pad_px)
        rgb = rgb.crop((left, top, right, bottom))

    long_side = max(rgb.width, rgb.height)
    if long_side > max_long_side > 0:
        scale = max_long_side / float(long_side)
        rgb = rgb.resize(
            (max(1, int(round(rgb.width * scale))), max(1, int(round(rgb.height * scale)))), Image.Resampling.LANCZOS
        )

    quantized = rgb.quantize(colors=64, method=Image.Quantize.FASTOCTREE)
    buf = io.BytesIO()
    quantized.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def export_page_step_images(
    page_id: str, dest_dir: Path, *, max_pages: int = 3, max_long_side: int = 1280
) -> list[Path]:
    """将关联的 OneNote 页面导出为裁边压缩后的步骤 PNG 列表（必要时自动打开 OneNote）。"""
    clean_page_id = str(page_id or "").strip()
    if not clean_page_id:
        raise OneNoteError("题目未关联有效的 OneNote 页面。")

    import pymupdf
    from PIL import Image

    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tyj-onenote-pdf-") as tmp_dir:
        pdf_path = Path(tmp_dir) / "onenote-page.pdf"
        _ensure_desktop_window()
        result = call_bridge(
            "publish", timeout_seconds=45, id=clean_page_id, dest=str(pdf_path), format=3, ensureWindow=True
        )
        if (
            not isinstance(result, dict)
            or not result.get("exists")
            or not pdf_path.is_file()
            or pdf_path.stat().st_size <= 0
        ):
            raise OneNoteError("未能从 OneNote 导出该笔记页 PDF。")

        out_paths: list[Path] = []
        with pymupdf.open(str(pdf_path)) as doc:
            total = min(len(doc), max(1, int(max_pages)))
            matrix = pymupdf.Matrix(150 / 72.0, 150 / 72.0)
            for idx in range(total):
                pix = doc[idx].get_pixmap(matrix=matrix, alpha=False)
                pil_img = Image.frombytes("RGB", (int(pix.width), int(pix.height)), pix.samples)
                png_bytes = _crop_and_compress_onenote_page(pil_img, max_long_side=max_long_side)
                target = dest_dir / f"onenote-step-{idx + 1}.png"
                target.write_bytes(png_bytes)
                out_paths.append(target)
        return out_paths


def build_onenote_timeline() -> dict[str, Any]:
    """聚合全库所有刷题记录，按日期倒序生成每日刷题时间轴。"""
    rows = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).only(
        "id", "source_filename", "number", "question_type", "content", "extras", "published_at"
    )
    events: list[dict[str, Any]] = []
    questions_seen: set[str] = set()

    for pub in rows:
        extras = pub.extras if isinstance(pub.extras, dict) else {}
        onenote = extras.get("onenote")
        if not isinstance(onenote, dict) or (not onenote.get("page_id") and not onenote.get("url")):
            continue
        pid = str(pub.pk)
        questions_seen.add(pid)
        content = pub.content if isinstance(pub.content, dict) else {}
        stem = str(content.get("stem") or "").strip()
        stem_preview = re.sub(r"\s+", " ", stem)[:120]
        source_name = _display_source_name(pub.source_filename or content.get("source_filename") or "")
        section = str(content.get("section") or "").strip()
        attempts = onenote.get("attempts") if isinstance(onenote.get("attempts"), list) else []

        if attempts:
            for item in attempts:
                if not isinstance(item, dict):
                    continue
                created_at = str(item.get("created_at") or onenote.get("updated_at") or "")
                date_str = str(item.get("date") or created_at[:10] or "").strip()
                if not date_str:
                    date_str = (onenote.get("title") or "")[:10] or "未记录日期"
                events.append(
                    {
                        "publication_id": pid,
                        "number": pub.number,
                        "question_type": pub.question_type,
                        "source_filename": source_name,
                        "section": section,
                        "stem_preview": stem_preview,
                        "attempt": int(item.get("attempt") or 1),
                        "label": str(item.get("label") or f"第 {item.get('attempt') or 1} 刷"),
                        "created_at": created_at,
                        "date": date_str,
                        "object_id": str(item.get("focus_object_id") or item.get("outline_id") or ""),
                        "object_url": str(item.get("object_url") or onenote.get("url") or ""),
                        "page_id": str(onenote.get("page_id") or ""),
                    }
                )
        else:
            raw_date = str(onenote.get("updated_at") or onenote.get("title") or "")
            date_str = raw_date[:10] if len(raw_date) >= 10 and raw_date[4] == "-" else "早期记录"
            events.append(
                {
                    "publication_id": pid,
                    "number": pub.number,
                    "question_type": pub.question_type,
                    "source_filename": source_name,
                    "section": section,
                    "stem_preview": stem_preview,
                    "attempt": 1,
                    "label": str(onenote.get("title") or "第 1 刷"),
                    "created_at": raw_date,
                    "date": date_str,
                    "object_id": "",
                    "object_url": str(onenote.get("url") or ""),
                    "page_id": str(onenote.get("page_id") or ""),
                }
            )

    events.sort(key=lambda item: (item["date"], item["created_at"], item["attempt"]), reverse=True)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ev in events:
        grouped.setdefault(ev["date"], []).append(ev)

    days = [
        {"date": day, "count": len(items), "items": items}
        for day, items in sorted(grouped.items(), key=lambda pair: pair[0], reverse=True)
    ]
    return {"ok": True, "days": days, "total_attempts": len(events), "total_questions": len(questions_seen)}


def _parse_optional_uuid(raw: str | None) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except (ValueError, TypeError):
        return None


def list_linked_notes(*, limit: int = 50, paper_id: str | None = None, query: str = "") -> dict[str, Any]:
    """只读列出已关联 OneNote 笔记的正式入库题目，供 AI Agent 浏览与按需阅读。"""
    clamped_limit = max(1, min(100, int(limit)))
    qs = PublishedQuestion.objects.filter(status=PublishedQuestion.Status.PUBLISHED).only(
        "id", "paper_id", "source_filename", "number", "question_type", "content", "extras", "published_at"
    )
    clean_paper_id = _parse_optional_uuid(paper_id)
    if paper_id and not clean_paper_id:
        raise OneNoteError("指定的试卷/教材编号格式不正确。")
    if clean_paper_id:
        qs = qs.filter(paper_id=clean_paper_id)

    keyword = str(query or "").strip().lower()
    matched: list[tuple[str, dict[str, Any]]] = []
    for pub in qs:
        extras = pub.extras if isinstance(pub.extras, dict) else {}
        onenote = extras.get("onenote")
        if not isinstance(onenote, dict) or (not onenote.get("page_id") and not onenote.get("url")):
            continue
        pid = str(pub.pk)
        short_id = f"Q-{pid.replace('-', '')[:8].lower()}"
        content = pub.content if isinstance(pub.content, dict) else {}
        stem = str(content.get("stem") or "").strip()
        stem_preview = re.sub(r"\s+", " ", stem)[:120]
        source_name = _display_source_name(pub.source_filename or content.get("source_filename") or "")
        section = str(content.get("section") or "").strip()
        page_title = str(onenote.get("title") or "").strip()
        notebook_name = str(onenote.get("notebook_name") or "").strip()
        section_name = str(onenote.get("section_name") or "").strip()

        if keyword:
            haystack = "\n".join((short_id, source_name, stem, section, page_title, notebook_name, section_name)).lower()
            if keyword not in haystack:
                continue

        updated_at = str(onenote.get("updated_at") or "")
        published_iso = pub.published_at.isoformat() if pub.published_at else ""
        sort_key = updated_at or published_iso
        attempts = onenote.get("attempts") if isinstance(onenote.get("attempts"), list) else []
        matched.append(
            (
                sort_key,
                {
                    "publication_id": pid,
                    "short_id": short_id,
                    "paper_id": str(pub.paper_id) if pub.paper_id else "",
                    "source_filename": source_name,
                    "number": pub.number,
                    "question_type": pub.question_type,
                    "section": section,
                    "stem_preview": stem_preview,
                    "attempt_count": len(attempts),
                    "onenote": {
                        "page_id": str(onenote.get("page_id") or ""),
                        "title": page_title,
                        "url": str(onenote.get("url") or ""),
                        "notebook_name": str(onenote.get("notebook_name") or ""),
                        "section_name": str(onenote.get("section_name") or ""),
                        "updated_at": updated_at,
                    },
                },
            )
        )

    matched.sort(key=lambda pair: pair[0], reverse=True)
    items = [entry for _, entry in matched[:clamped_limit]]
    return {"ok": True, "total": len(matched), "items": items}


def read_page_note(publication: PublishedQuestion, *, include_images: bool = True, max_pages: int = 3) -> dict[str, Any]:
    """严格只读提取题目关联的 OneNote 页面文字块与可选手写墨迹截图，绝不改动原笔记。"""
    extras = publication.extras if isinstance(publication.extras, dict) else {}
    record = extras.get("onenote")
    if not isinstance(record, dict) or not str(record.get("page_id") or "").strip():
        raise OneNoteError("该题目尚未关联 OneNote 笔记。")

    page_id = str(record["page_id"]).strip()
    page_data = call_bridge("page", id=page_id, timeout_seconds=20)
    if not isinstance(page_data, dict):
        raise OneNoteError("未能读取到有效的 OneNote 页面内容。")

    blocks = [str(b) for b in (page_data.get("blocks") or []) if str(b).strip()]
    title = str(page_data.get("title") or record.get("title") or "").strip()
    text = str(page_data.get("text") or "").strip()

    image_paths: list[str] = []
    image_warning = ""
    if include_images:
        clamped_pages = max(1, min(5, int(max_pages)))
        cache_dir = Path(settings.DATA_ROOT) / "cache" / "onenote_read" / str(publication.pk)
        try:
            exported = export_page_step_images(page_id, cache_dir, max_pages=clamped_pages)
            image_paths = [str(p) for p in exported if p.is_file()]
        except Exception as exc:
            logger.warning("Failed to export OneNote read images for %s: %s", publication.pk, exc)
            image_warning = str(exc)

    pid = str(publication.pk)
    content = publication.content if isinstance(publication.content, dict) else {}
    stem = str(content.get("stem") or "").strip()
    return {
        "ok": True,
        "publication_id": pid,
        "short_id": f"Q-{pid.replace('-', '')[:8].lower()}",
        "source_filename": _display_source_name(publication.source_filename or ""),
        "number": publication.number,
        "stem_preview": re.sub(r"\s+", " ", stem)[:160],
        "onenote": record,
        "title": title,
        "text": text,
        "blocks": blocks,
        "image_paths": image_paths,
        "image_warning": image_warning,
    }


# ---------------- API Views ----------------


@csrf_exempt
def onenote_create_view(request: HttpRequest, publication_id: str) -> JsonResponse:
    """POST /api/library/<publication_id>/onenote/create: 为题目在 OneNote 创建答题页并关联。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        record = create_or_sync_page(publication)
        return JsonResponse({"ok": True, "onenote": record})
    except OneNoteError as err:
        return JsonResponse({"ok": False, "error": str(err)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected error creating OneNote page for %s", publication_id)
        return JsonResponse({"ok": False, "error": f"创建 OneNote 页面异常: {exc}"}, status=500)


@csrf_exempt
def onenote_attempt_view(request: HttpRequest, publication_id: str) -> JsonResponse:
    """POST /api/library/<publication_id>/onenote/attempt: 在同页右侧安全区发起新的一刷。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        record = start_attempt(publication)
        return JsonResponse({"ok": True, "onenote": record})
    except OneNoteError as err:
        return JsonResponse({"ok": False, "error": str(err)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected error starting OneNote attempt for %s", publication_id)
        return JsonResponse({"ok": False, "error": f"创建 OneNote 刷题区域异常: {exc}"}, status=500)


@csrf_exempt
def onenote_open_view(request: HttpRequest, publication_id: str) -> JsonResponse:
    """POST /api/library/<publication_id>/onenote/open: 在 OneNote 中调起并定位到本题解答页或指定刷次对象。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        wait = request.GET.get("wait") in {"1", "true", "yes"}
        object_id = str(request.GET.get("object_id") or "").strip() or None
        if not object_id and request.body:
            try:
                body = json.loads(request.body)
                if isinstance(body, dict):
                    object_id = str(body.get("object_id") or "").strip() or None
            except (ValueError, TypeError):
                object_id = None
        if object_id:
            result = open_page(publication, wait=wait, object_id=object_id)
        else:
            result = open_page(publication, wait=wait)
        return JsonResponse(result)
    except OneNoteError as err:
        return JsonResponse({"ok": False, "error": str(err)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected error opening OneNote page for %s", publication_id)
        return JsonResponse({"ok": False, "error": f"打开 OneNote 页面异常: {exc}"}, status=500)


@csrf_exempt
def onenote_timeline_view(request: HttpRequest) -> JsonResponse:
    """GET /api/library/onenote/timeline: 返回按天分组的个人刷题时间轴。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return JsonResponse(build_onenote_timeline())


@csrf_exempt
def onenote_notes_list_view(request: HttpRequest) -> JsonResponse:
    """GET /api/library/onenote/notes: 只读列出已关联 OneNote 的入库题目，便于 Agent 检索。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    raw_limit = request.GET.get("limit") or "50"
    try:
        limit = int(raw_limit)
    except ValueError:
        return JsonResponse({"ok": False, "error": "limit 参数必须为整数"}, status=400)
    paper_id = str(request.GET.get("document") or "").strip() or None
    query = str(request.GET.get("q") or "").strip()
    try:
        return JsonResponse(list_linked_notes(limit=limit, paper_id=paper_id, query=query))
    except OneNoteError as err:
        return JsonResponse({"ok": False, "error": str(err)}, status=400)


@csrf_exempt
def onenote_read_view(request: HttpRequest, publication_id: str) -> JsonResponse:
    """GET /api/library/<publication_id>/onenote/read: 只读提取关联 OneNote 页的文字与手写墨迹图。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    raw_images = str(request.GET.get("images") or "1").strip().lower()
    include_images = raw_images not in {"0", "false", "no", "off"}
    raw_max_pages = request.GET.get("max_pages") or "3"
    try:
        max_pages = int(raw_max_pages)
    except ValueError:
        return JsonResponse({"ok": False, "error": "max_pages 参数必须为整数"}, status=400)
    try:
        return JsonResponse(read_page_note(publication, include_images=include_images, max_pages=max_pages))
    except OneNoteError as err:
        return JsonResponse({"ok": False, "error": str(err)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected error reading OneNote page for %s", publication_id)
        return JsonResponse({"ok": False, "error": f"读取 OneNote 页面异常: {exc}"}, status=500)


@csrf_exempt
def onenote_unbind_view(request: HttpRequest, publication_id: str) -> JsonResponse:
    """POST /api/library/<publication_id>/onenote/unbind: 解除本题与 OneNote 笔记的关联。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    extras = dict(publication.extras or {})
    if "onenote" in extras:
        del extras["onenote"]
        library.save_extras(publication, extras)

    return JsonResponse({"ok": True, "unbound": True})


@csrf_exempt
def deep_link_view(request: HttpRequest) -> JsonResponse:
    """GET /api/deep-link: 消费一次 tiyouju:// 回跳留下的定位或社区动作请求。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from . import tiyouju_protocol

    pending = tiyouju_protocol.consume_deep_link()
    if not pending:
        return JsonResponse({"ok": True, "q": None, "action": None})
    return JsonResponse(
        {
            "ok": True,
            "q": pending.get("q"),
            "action": pending.get("action"),
            "payload": pending,
            "ts": pending.get("ts"),
        }
    )


@csrf_exempt
def open_external_view(request: HttpRequest) -> JsonResponse:
    """POST /api/open-external：用系统默认浏览器打开一个 http(s) 地址。

    资源社区走这里。WebView2 里的 window.open 会留在题有据窗口里，不是用户的默认浏览器。
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if request.META.get("REMOTE_ADDR", "") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "只能在本机打开"}, status=403)
    if request.headers.get("X-QB-Request") != "1":
        return JsonResponse({"error": "请求需从题库页面发起"}, status=403)
    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse({"error": "请求格式不正确"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "请求格式不正确"}, status=400)
    url = str(payload.get("url") or "").strip()
    if len(url) > 2000:
        return JsonResponse({"error": "地址太长"}, status=400)
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return JsonResponse({"error": "只能打开网页地址"}, status=400)
    try:
        if os.name == "nt":
            os.startfile(url)  # noqa: S606 - 只接受上面校验过的 http(s)
        else:
            import webbrowser

            webbrowser.open(url)
    except OSError:
        return JsonResponse({"error": "系统没有打开这个地址"}, status=500)
    return JsonResponse({"ok": True})
