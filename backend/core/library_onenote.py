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
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt

from . import library, qtypes
from .models import PublishedQuestion

logger = logging.getLogger("core.onenote")

PS_EXE = "powershell.exe"

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
    if BUNDLED_BRIDGE_PATH.is_file():
        return BUNDLED_BRIDGE_PATH
    if LEGACY_BRIDGE_PATH.is_file():
        return LEGACY_BRIDGE_PATH
    raise OneNoteError("未找到 OneNote PowerShell 桥接脚本，请检查配置文件。")


def shell_open_onenote(url: str) -> bool:
    """用系统协议打开 onenote: 链接。优先 os.startfile（少起一个 cmd）。"""
    href = (url or "").strip()
    if not href:
        return False
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
                [PS_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path), req_file, resp_file],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
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
_SECTION_CACHE_TTL = 300.0
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


def render_question_image(publication: PublishedQuestion) -> bytes | None:
    """复用「复制题目图片」的无头浏览器管线，把题面（含公式与配图）渲染成 PNG。

    渲染失败只记日志并返回 None，由调用方退回文字版，不让建页整体失败。
    """
    from . import library_clipboard as clip

    cached = cached_question_image(publication)
    if cached is not None:
        return cached

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


def build_page_xml(publication: PublishedQuestion, title: str, png: bytes | None) -> str:
    """生成 OneNote 答题页的完整页面 XML（页面 ID 用占位符，由桥接脚本建页后替换）。

    题号和出处放在 OneNote 自己的大标题里。正文只留回跳链接和题面，手写写在下面的空白处。
    """
    content = publication.content or {}
    link = html.escape(backlink_url(publication), quote=True)
    rows = [_text_oe(f'<a href="{link}">{_span("在题有据中打开此题", size="10.0pt", color="#4B5563")}</a>')]
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

    return (
        '<?xml version="1.0"?>'
        f'<one:Page xmlns:one="{ONE_NS}" ID="{PAGE_ID_PLACEHOLDER}">'
        f"<one:Title><one:OE><one:T>{_cdata(html.escape(title))}</one:T></one:OE></one:Title>"
        f"<one:Outline><one:OEChildren>{''.join(rows)}</one:OEChildren></one:Outline>"
        "</one:Page>"
    )


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

    threading.Thread(target=_ensure_protocol_background, name="onenote-protocol-register", daemon=True).start()

    # 只吃缓存，绝不在建页热路径里启动无头浏览器。
    png = cached_question_image(publication)
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

    onenote_record = {
        "url": link,
        "page_id": page_id,
        "title": title,
        "notebook": nb_name,
        "section": sec_name,
        "updated_at": datetime.now(UTC).isoformat(),
    }

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


def _refine_open(page_id: str | None, url: str | None) -> None:
    """后台精修：COM 切页 + 置前。不挡前端点击返回。"""
    navigated = False
    focused = False
    if page_id:
        try:
            data = call_bridge("navigate", id=str(page_id), timeout_seconds=12)
            navigated = True
            if isinstance(data, dict):
                focused = bool(data.get("focused"))
        except Exception as exc:
            logger.warning("Failed to navigate by page_id=%s: %s", page_id, exc)
    if url and (not navigated or not focused):
        shell_open_onenote(str(url))


def open_page(publication: PublishedQuestion, *, wait: bool = False) -> dict[str, Any]:
    """打开已关联解答页。

    默认最快路径：立刻返回，COM 置前丢后台线程。前端应先用协议链接唤起。
    wait=True 时同步执行（测试用）。
    """
    extras = publication.extras or {}
    record = extras.get("onenote")
    if not record or not isinstance(record, dict):
        raise OneNoteError("该题目尚未关联 OneNote 笔记。")

    page_id = record.get("page_id")
    url = record.get("url")
    if not page_id and not url:
        raise OneNoteError("无法定位到该页面，链接或页面标识已丢失。")

    if wait:
        _refine_open(page_id, url)
        return {"ok": True, "opened": True, "focused": True, "onenote": record, "deferred": False}

    threading.Thread(
        target=_refine_open, args=(page_id, url), name=f"onenote-open-{publication.pk}", daemon=True
    ).start()
    return {"ok": True, "opened": True, "focused": False, "onenote": record, "deferred": True}


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
def onenote_open_view(request: HttpRequest, publication_id: str) -> JsonResponse:
    """POST /api/library/<publication_id>/onenote/open: 在 OneNote 中调起并定位到本题解答页。"""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    publication = get_object_or_404(PublishedQuestion, pk=publication_id)
    try:
        result = open_page(publication)
        return JsonResponse(result)
    except OneNoteError as err:
        return JsonResponse({"ok": False, "error": str(err)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected error opening OneNote page for %s", publication_id)
        return JsonResponse({"ok": False, "error": f"打开 OneNote 页面异常: {exc}"}, status=500)


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
    """GET /api/deep-link: 消费一次 tiyouju:// 回跳留下的定位请求。"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    from . import tiyouju_protocol

    pending = tiyouju_protocol.consume_deep_link()
    if not pending:
        return JsonResponse({"ok": True, "q": None})
    return JsonResponse({"ok": True, "q": pending.get("q"), "ts": pending.get("ts")})
