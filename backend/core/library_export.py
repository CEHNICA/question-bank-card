"""Offline, editable DOCX export from exact published versions.

The browser supplies KaTeX MathML and source offsets, never replacement prose
or image URLs. Every field is checked against its stored publication before
conversion. A formula Word's own equation model cannot express is rendered to a
picture and reported by position; when that rendering is unavailable the whole
download is still refused rather than shipping a paper the reader cannot follow.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import tempfile
import threading
import time
import unicodedata
import uuid
import warnings
import xml.sax
import zipfile
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from . import features, library, tables
from .library_browse import BrowseError, normalize_ids
from .library_drafts import DraftError, _print_options, _title
from .models import PublishedQuestion

MAX_BODY = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_IMAGES = 96 * 1024 * 1024
MAX_FIELD_UNITS = 100_000
MAX_MATHML = 80_000
MATH_NS = "http://www.w3.org/1998/Math/MathML"
OMML_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
SEGMENT_KEYS = {"type", "start", "end", "latex", "mathml", "display", "displayGroup"}
GROUPS = (
    ("single_choice", "选择题"),
    ("multiple_choice", "多选题"),
    ("fill_blank", "填空题"),
    ("true_false", "判断题"),
    ("free_response", "解答题"),
)
FIELDS = {"stem", "answer", "analysis", "origin", *(f"options.{key}" for key in "ABCDE")}
# KaTeX 把重音写成 mover 的第二个 <mo>。只有这张表里的字形能转成 OMML 的原生
# <m:acc>；落在表外的（\overline 的 U+203E、\acute 的 U+02CA、\grave 的 U+02CB）
# 会让整份 Word 导不出来 —— 高考卷里 \overline 极常见，缺一个就废掉一整卷。
ACCENTS = {
    "⃗": "⃗",
    "→": "⃗",
    "←": "⃖",
    "↔": "⃡",
    "^": "̂",
    "~": "̃",
    "ˉ": "̅",
    "¯": "̅",
    "‾": "̅",
    "˙": "̇",
    "¨": "̈",
    "ˇ": "̌",
    "˘": "̆",
    "´": "́",
    "ˊ": "́",
    "`": "̀",
    "ˋ": "̀",
}
MATHML_ELEMENTS = {
    "math",
    "semantics",
    "annotation",
    "mi",
    "mn",
    "mo",
    "mrow",
    "mtext",
    "mspace",
    "ms",
    "mstyle",
    "mpadded",
    "mphantom",
    "mfrac",
    "msqrt",
    "mroot",
    "mfenced",
    "menclose",
    "msub",
    "msup",
    "msubsup",
    "munder",
    "mover",
    "munderover",
    "mmultiscripts",
    "mprescripts",
    "none",
    "mtable",
    "mtr",
    "mtd",
    "mlabeledtr",
    "maction",
}
LINE_SPACING = 1.35
PARAGRAPH_GAP_PT = 3
QUESTION_GAP_PT = 8
# 有效排版、但 Word 的公式模型表达不了的公式。不再让一条这样的公式废掉整卷：
# 交上去让本机浏览器渲染成图片，警告里点名第几题哪个位置。
_UNSUPPORTED = object()
_FALLBACK_REFUSAL = "这段公式暂不能准确转换为可编辑 Word 公式，请在预览核对并改用打印 / 保存 PDF"
_FALLBACK_ADVICE = "这条公式在 Word 里只能以图片呈现"
_FIELD_LABELS = {"stem": "题干", "answer": "答案", "analysis": "解析", "origin": "题源"}
MAX_FALLBACK_FORMULAS = 32
MAX_FALLBACK_BYTES = 16 * 1024 * 1024
MAX_FORMULA_BOX = (1200, 300)
FORMULA_CAPTURE_SCALE = 3
CSS_PIXELS_PER_INCH = 96
_picture_lock = threading.Lock()


class ExportError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


logger = logging.getLogger("core.export")


class _Unrepresentable(Exception):
    """Valid KaTeX rendering that Word's own equation model cannot express."""


def _fail(where, message, status=400):
    raise ExportError(f"{where}：{message}", status)


def unexpected_export_error(kind: str, error: BaseException) -> str:
    """记下一个没接住的导出错，给老师一句能转述的话和一个查得到的号。

    导出这条路上，已知的失败都带原因（配图损坏、文件缺失、浏览器排不了版、请求太大）。
    没被接住的那种错以前只留下一张 HTML 的 500 页：网页上只能显示「Word 未能导出，
    请稍后重试」，日志里连堆栈都没有 —— 用户重试一万次也没用，开发者事后也查不出
    到底为什么。这里给一个诊断号并把堆栈记进日志；老师把这个号报过来就能查到。
    """
    diagnostic = f"EX-{uuid.uuid4().hex[:12].upper()}"
    logger.exception("export %s failed unexpectedly (%s): %s", kind, diagnostic, error)
    return diagnostic


def export_failed_message(kind: str, diagnostic: str) -> str:
    return f"{kind}导出没完成（诊断号 {diagnostic}）：你的题和原卷都没动。可以再试一次；还是这样就把这个号发给开发者。"


def _utf16_map(source: str, where: str) -> dict[int, int]:
    """Only complete Unicode scalar boundaries are accepted as JS offsets."""
    if not isinstance(source, str):
        _fail(where, "字段原文应为文字")
    offsets, total = {0: 0}, 0
    for index, character in enumerate(source):
        code = ord(character)
        if 0xD800 <= code <= 0xDFFF or code in {0xFFFE, 0xFFFF} or (code < 32 and character not in "\t\n\r"):
            _fail(where, "文字含不能写入 Word 的控制字符")
        total += 2 if code > 0xFFFF else 1
        offsets[total] = index + 1
    if total > MAX_FIELD_UNITS:
        _fail(where, "文字过长，请减少本次选题")
    return offsets


def _bounds(item, offsets, start, end, where):
    a, b = item.get("start"), item.get("end")
    if (
        type(a) is not int
        or type(b) is not int
        or a not in offsets
        or b not in offsets
        or a != start
        or not a < b <= end
    ):
        _fail(where, "排版片段的位置不连续或切开了一个字符，请重新打开组卷")
    return a, b


def _convert_math_root(root):
    """Native Word accents instead of upstream overscript limit glyphs.

    Standard KaTeX distinguishes mover[accent=true] from genuine overscripts.
    Only that explicit shape is adapted; overset and large-operator limits keep
    their normal meaning. Bases recurse to preserve nested accents and roots.
    """
    import mathml2omml
    from lxml import etree

    root = deepcopy(root)
    replacements = {}
    prefix = "QBEXPORTACCENT"
    while prefix in "".join(root.itertext()):
        prefix += "X"
    for node in list(root.iter(f"{{{MATH_NS}}}mover")):
        if root not in node.iterancestors() or node.get("accent") != "true":
            continue
        if len(node) != 2 or node[1].tag != f"{{{MATH_NS}}}mo" or len(node[1]):
            raise ValueError("unsupported accent")
        glyph = node[1].text or ""
        if glyph not in ACCENTS:
            raise ValueError("unsupported accent")
        base = etree.Element(f"{{{MATH_NS}}}math", nsmap={None: MATH_NS})
        base.append(deepcopy(node[0]))
        inner = _convert_math_root(base)
        accent = etree.Element(f"{{{OMML_NS}}}acc")
        props = etree.SubElement(accent, f"{{{OMML_NS}}}accPr")
        character = etree.SubElement(props, f"{{{OMML_NS}}}chr")
        character.set(f"{{{OMML_NS}}}val", ACCENTS[glyph])
        expression = etree.SubElement(accent, f"{{{OMML_NS}}}e")
        for child in inner:
            expression.append(child)
        marker = prefix + str(len(replacements))
        replacements[marker] = accent
        placeholder = etree.Element(f"{{{MATH_NS}}}mi")
        placeholder.text, placeholder.tail = marker, node.tail
        node.getparent().replace(node, placeholder)
    converted = mathml2omml.convert(etree.tostring(root, encoding="unicode"))
    converted = re.sub(
        r'(<m:groupChrPr><m:chr m:val="[^"]*"/><m:pos m:val="(?:top|bot)"/>)</m:groupChr>',
        r"\1</m:groupChrPr>",
        converted,
    )
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)
    wrapper = etree.fromstring((f'<root xmlns:m="{OMML_NS}">' + converted + "</root>").encode("utf-8"), parser)
    if len(wrapper) != 1:
        raise ValueError("root")
    omml = wrapper[0]
    if omml.tag != f"{{{OMML_NS}}}oMath" or not list(omml):
        raise ValueError("empty")
    seen = set()
    for run in list(omml.iter(f"{{{OMML_NS}}}r")):
        text = run.find(f"{{{OMML_NS}}}t")
        if text is not None and text.text in replacements:
            seen.add(text.text)
            run.getparent().replace(run, replacements[text.text])
    if seen != set(replacements):
        raise ValueError("accent placeholder")
    for radical in omml.findall(f".//{{{OMML_NS}}}rad"):
        if radical.find(f"{{{OMML_NS}}}deg") is None:
            props = etree.Element(f"{{{OMML_NS}}}radPr")
            hidden = etree.SubElement(props, f"{{{OMML_NS}}}degHide")
            hidden.set(f"{{{OMML_NS}}}val", "1")
            radical.insert(0, props)
            radical.insert(1, etree.Element(f"{{{OMML_NS}}}deg"))
        if radical.find(f"{{{OMML_NS}}}e") is None:
            raise ValueError("radical")
    _mark_functions(omml)
    return omml


# ``\sin x`` 这类写法，mathml2omml 会拆成「函数名、一个空 run、参数」。
# 那个空 run 就是它给函数名和参数留的位置，可 Word 不会给空 run 补空格，
# 纸上就印成 ``sinx``——学生会当成一个变量名（高考卷第 6 题的
# ``(e^x+2\sin x)/(1+x^2)``、``\sin x`` 等 5 处最典型）。PDF 路线由 KaTeX 自己
# 加空格，两条路线于是印得不一样。
#
# 这里只认「紧跟在函数名后面的空 run」这个标记——那是上游自己的意图，不去猜
# LaTeX；没有这个标记的一律不动。
_FUNCTION_NAMES = {
    "sin",
    "cos",
    "tan",
    "cot",
    "sec",
    "csc",
    "sinh",
    "cosh",
    "tanh",
    "arcsin",
    "arccos",
    "arctan",
    "log",
    "ln",
    "lg",
    "exp",
    "det",
    "gcd",
    "lcm",
}
# 函数名取参数到这些普通文字算符为止：``2\sin x+1`` 的参数是 ``x``，不是 ``x+1``。
_ARGUMENT_STOP = set("+-−*/^=<>,;:")


def _base_runs(script):
    """上下标的底座里那几段 run（上游常多包一层 ``m:box``）。底座不是「函数名 + 空串」就返回 None。"""
    base = script.find(f"{{{OMML_NS}}}e")
    if base is None:
        return None
    holder = base
    if len(base) == 1 and base[0].tag == f"{{{OMML_NS}}}box":
        holder = base[0].find(f"{{{OMML_NS}}}e")
    if holder is None:
        return None
    runs = [child for child in holder if child.tag == f"{{{OMML_NS}}}r"]
    if len(runs) != 2 or len(list(holder)) != 2:
        return None
    name, gap = (child.find(f"{{{OMML_NS}}}t") for child in runs)
    if name is None or gap is None or (gap.text or "") != "":
        return None
    if (name.text or "").strip().lower() not in _FUNCTION_NAMES:
        return None
    return runs


def _mark_scripted_functions(omml):
    r"""``\log_{2}8`` / ``\sin^{2}x``：下标或上标挂在函数名上。

    底座正好是「函数名 + 空串」，空串是上游给参数留的位置——参数却跟在**整个
    上下标**后面（``\log_2`` 8），所以 :func:`_mark_functions` 看不到它。Word 里
    这种要写成 ``fName`` 里带 ``m:sSub``，真库里有 28 处。
    """
    from lxml import etree

    for script in list(omml.iter(f"{{{OMML_NS}}}sSub", f"{{{OMML_NS}}}sSup")):
        runs = _base_runs(script)
        if not runs:
            continue
        parent = script.getparent()
        if parent is None:
            continue
        siblings = list(parent)
        position = siblings.index(script)
        argument = []
        for node in siblings[position + 1 :]:
            if node.tag == f"{{{OMML_NS}}}r":
                value = node.find(f"{{{OMML_NS}}}t")
                if value is not None and (value.text or "") in _ARGUMENT_STOP:
                    break
            argument.append(node)
            if len(argument) >= 12:
                break
        if not argument:
            continue
        function = etree.Element(f"{{{OMML_NS}}}func")
        name = etree.SubElement(function, f"{{{OMML_NS}}}fName")
        body = etree.SubElement(function, f"{{{OMML_NS}}}e")
        for node in argument:
            body.append(node)  # 参数都在 script 之后
        runs[1].getparent().remove(runs[1])  # 空串已经被 fName 取代
        # name.append(script) 会把 script 从 parent 摘走，replace() 就找不到它了；
        # 只能按原下标摘掉再插回去。
        parent.remove(script)
        name.append(script)
        parent.insert(position, function)


def _mark_functions(omml):
    """把「函数名 + 空 run + 参数」改写成 Word 认识的 ``m:func``。

    改写之后细空格由 Word 自己补，和 KaTeX 印出来的一致；老师在 Word 里改这条
    公式时看到的也是「函数」而不是三个挤在一起的字符。
    """
    from lxml import etree

    _mark_scripted_functions(omml)
    for run in list(omml.iter(f"{{{OMML_NS}}}r")):
        text = run.find(f"{{{OMML_NS}}}t")
        if text is None or (text.text or "").strip().lower() not in _FUNCTION_NAMES:
            continue
        parent = run.getparent()
        if parent is None or parent.tag == f"{{{OMML_NS}}}func":
            continue
        siblings = list(parent)
        position = siblings.index(run)
        if position + 2 >= len(siblings):
            continue
        gap = siblings[position + 1]
        gap_text = gap.find(f"{{{OMML_NS}}}t") if gap.tag == f"{{{OMML_NS}}}r" else None
        if gap_text is None or (gap_text.text or "") != "":
            continue
        argument = []
        for node in siblings[position + 2 :]:
            if node.tag == f"{{{OMML_NS}}}r":
                value = node.find(f"{{{OMML_NS}}}t")
                if value is not None and (value.text or "") in _ARGUMENT_STOP:
                    break
            argument.append(node)
            if len(argument) >= 12:
                break
        if not argument:
            continue
        function = etree.Element(f"{{{OMML_NS}}}func")
        name = etree.SubElement(function, f"{{{OMML_NS}}}fName")
        name.append(run)
        body = etree.SubElement(function, f"{{{OMML_NS}}}e")
        for node in argument:
            body.append(node)
        parent.replace(gap, function)


def _math(segment, where, *, word_math=True):
    """Adapt two narrowly identified upstream OMML bugs, then validate structure.

    mathml2omml 0.0.2 closes groupChrPr with the wrong tag, and omits deg for
    square roots. The latter loses the radicand in a real LibreOffice render.
    These schema repairs are bounded; arbitrary malformed XML is never repaired.

    Two outcomes, and they are not interchangeable. Everything that says the
    client's rendering cannot be trusted (missing annotation, foreign nodes,
    source mismatch) is a hard failure. Everything that only says Word's own
    equation model cannot express a valid rendering returns _UNSUPPORTED, so the
    caller can hand it to the picture renderer instead of losing the whole paper.
    """
    from lxml import etree

    latex, raw = segment.get("latex"), segment.get("mathml")
    if (
        not isinstance(latex, str)
        or not latex.strip()
        or len(latex) > 12_000
        or not isinstance(raw, str)
        or len(raw) > MAX_MATHML
    ):
        _fail(where, "公式缺少完整的本地排版结果，请重新打开组卷")
    if re.search(r"<!|<\?", raw):
        _fail(where, "公式 XML 不能含实体声明或处理指令")
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False)
    try:
        root = etree.fromstring(raw.encode("utf-8"), parser)
        if root.tag != f"{{{MATH_NS}}}math":
            raise ValueError("root")
        nodes = list(root.iter())
        if len(nodes) > 3000 or any(
            not isinstance(node.tag, str) or not node.tag.startswith(f"{{{MATH_NS}}}") for node in nodes
        ):
            raise ValueError("nodes")
        unsupported = None
        for node in nodes:
            if (
                etree.QName(node).localname not in MATHML_ELEMENTS
                or len(list(node.iterancestors())) > 60
                or any(
                    "href" in key.lower() or "src" in key.lower() or key.lower().startswith("on") for key in node.attrib
                )
            ):
                raise ValueError("attributes")
            # Upstream turns every menclose into a box, silently losing strikes.
            # A boxed formula is safe; cancellation/radical/arrow enclosures aren't.
            if word_math and etree.QName(node).localname == "menclose" and node.get("notation", "longdiv") != "box":
                unsupported = "unsupported enclosure"
        annotations = root.findall(f".//{{{MATH_NS}}}annotation")
        if (
            len(annotations) != 1
            or annotations[0].get("encoding") != "application/x-tex"
            or len(annotations[0])
            or "".join(annotations[0].itertext()) != latex
            or annotations[0].getparent().tag != f"{{{MATH_NS}}}semantics"
            or len(annotations[0].getparent()) < 2
        ):
            raise ValueError("annotation")
        annotations[0].getparent().remove(annotations[0])
        if unsupported:
            # Integrity is settled first: only then does "Word can't express it"
            # become a reason to degrade instead of refusing the download.
            raise _Unrepresentable(unsupported)
        if not word_math:
            # PDF renders canonical source again through KaTeX; validated metadata
            # stays in the segment, without imposing Word's OMML support limits.
            return None
        try:
            # Upstream SAX handler would print annotation characters as a side effect.
            omml = _convert_math_root(root)
            # Detect silent token loss in the converter, excluding intentional phantom.
            tokens = []
            for node in root.iter():
                if etree.QName(node).localname not in {"mi", "mn", "mo", "mtext", "ms"} or any(
                    etree.QName(parent).localname == "mphantom" for parent in node.iterancestors()
                ):
                    continue
                token = "".join(node.itertext())
                parent = node.getparent()
                if (
                    parent is not None
                    and parent.tag == f"{{{MATH_NS}}}mover"
                    and parent.get("accent") == "true"
                    and len(parent) == 2
                    and node is parent[1]
                ):
                    token = ACCENTS.get(token, token)
                tokens.append(token)
            output = "".join(omml.itertext()) + "".join(node.get(f"{{{OMML_NS}}}val", "") for node in omml.iter())
            for token in tokens:
                token = re.sub(r"[\s\u2061-\u2064]", "", token)
                if token and token not in re.sub(r"\s", "", output):
                    raise ValueError("lost token")
            return omml
        except (
            ValueError,
            TypeError,
            RuntimeError,
            NotImplementedError,
            AssertionError,
            IndexError,
            KeyError,
            etree.XMLSyntaxError,
            xml.sax.SAXException,
        ):
            # The rendering itself is valid and matches the stored source; only
            # Word cannot express it. The reason is for the log, never the paper.
            raise _Unrepresentable(unsupported or "omml conversion") from None
    except _Unrepresentable:
        return _UNSUPPORTED
    except (
        ValueError,
        TypeError,
        RuntimeError,
        NotImplementedError,
        AssertionError,
        IndexError,
        KeyError,
        etree.XMLSyntaxError,
        xml.sax.SAXException,
    ):
        _fail(where, _FALLBACK_REFUSAL if word_math else "公式排版结果无效，请重新打开组卷预览后再导出 PDF")


def _segments(source, segments, start, end, where, offsets=None, *, word_math=True):
    offsets = offsets or _utf16_map(source, where)
    if not isinstance(segments, list) or len(segments) > 4000:
        _fail(where, "排版片段格式不正确")
    converted, cursor = [], start
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) - SEGMENT_KEYS:
            _fail(where, "排版片段含不支持的字段")
        a, b = _bounds(segment, offsets, cursor, end, where)
        raw = source[offsets[a] : offsets[b]]
        kind = segment.get("type")
        if kind not in {"text", "math", "blank", "bracket", "parallelogram", "delimiter"}:
            _fail(where, "排版片段类型不正确")
        if "display" in segment and type(segment["display"]) is not bool:
            _fail(where, "公式展示方式不正确")
        if "displayGroup" in segment and (
            not isinstance(segment["displayGroup"], str) or len(segment["displayGroup"]) > 100
        ):
            _fail(where, "公式分组不正确")
        if kind != "math" and ({"latex", "mathml"} & set(segment)):
            _fail(where, "普通文字不能携带替换公式")
        if kind == "delimiter" and raw not in {"$", "$$", "\\(", "\\)", "\\[", "\\]"}:
            _fail(where, "只能省略公式定界符，不能省略题目文字")
        if kind == "parallelogram" and not re.fullmatch(r"▱|\\(?:text|mathrm|mathbf)\s*\{\s*▱\s*\}", raw):
            _fail(where, "平行四边形符号与原文不符")
        if kind == "blank" and not re.fullmatch(r"(?:\\_){2,}|_{3,}", raw):
            _fail(where, "填空线与原文不符")
        if kind == "bracket" and not re.fullmatch(r"（[ \u3000]*）|\([ \u3000]+\)", raw):
            _fail(where, "答题括号与原文不符")
        if kind == "text" and re.search(r"\$[^$\n]+\$|\\[([]|\\[A-Za-z]{2,}", raw):
            _fail(where, "公式尚未转换，不能把 LaTeX 源码当作成功结果")
        if kind == "text" and re.search(r"<(?:table|tr|td|th)\b", raw, re.I):
            _fail(where, "表格尚未转换，不能把表格源码当作成功结果")
        if kind == "text" and tables.markdown_tables(raw):
            # 管道表和 HTML 表一样：送回来时必须已经是排好版的表格块。混在普通
            # 文字里就意味着成品卷上会印出一排竖线，而表格源码是识读提示词
            # 规定的写法，不会被上面那条 HTML 检查挡住。
            _fail(where, "表格尚未转换，不能把表格源码当作成功结果")
        omml = _math(segment, where, word_math=word_math) if kind == "math" else None
        entry = {**segment, "raw": raw, "omml": None if omml is _UNSUPPORTED else omml}
        if omml is _UNSUPPORTED:
            # Remember where it is; the picture is filled in once for the whole
            # paper, after every question has been checked.
            entry["fallback"] = where
        converted.append(entry)
        cursor = b
    if cursor != end:
        _fail(where, "排版结果没有完整覆盖原文，请重新打开组卷")
    return converted


def _decoded(value):
    # Match QB's deliberately small entity decoder rather than reinterpret HTML.
    def decode(match):
        code = match.group(1).lower()
        named = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " "}
        if code in named:
            return named[code]
        number = int(code[2:], 16) if code.startswith("#x") else int(code[1:])
        return chr(number) if 0 < number < 0x110000 else match.group(0)

    return re.sub(r"&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos|nbsp);", decode, value, flags=re.I)


def _table_source(raw, where):
    """Reconstruct authoritative cells; client rows cannot hide or alter cells."""
    if re.match(r"\s*<table\b", raw, re.I):
        if re.search(r"<(?:img|svg|math|script|iframe|object)\b", raw, re.I):
            _fail(where, "表格含嵌入图像或公式标记，暂不能保证完整 Word 导出")
        rows = []
        for row in re.finditer(r"<tr\b[^>]*>([\s\S]*?)</tr\s*>", raw, re.I):
            cells = []
            for cell in re.finditer(r"<(td|th)\b([^>]*)>([\s\S]*?)</\1\s*>", row[1], re.I):

                def span(name, source):
                    match = re.search(rf"\b{name}\s*=\s*[\"']?(\d{{1,2}})", source, re.I)
                    return max(1, min(20, int(match[1]))) if match else 1

                value = re.sub(r"<br\s*/?>", " ", cell[3], flags=re.I)
                value = re.sub(r"<[^>]+>", "", value)
                cells.append(
                    {
                        "source": re.sub(r"\s+", " ", _decoded(value)).strip(),
                        "header": cell[1].lower() == "th",
                        "colspan": span("colspan", cell[2]),
                        "rowspan": span("rowspan", cell[2]),
                    }
                )
            if cells:
                rows.append(cells)
        if not rows or len(rows) > 60 or any(len(row) > 20 for row in rows):
            _fail(where, "表格结构不完整或超出 60 行、20 列，请分批整理")
        return rows
    lines = raw.split("\n")
    if len(lines) < 2 or any(not line.strip().startswith("|") for line in lines):
        _fail(where, "Markdown 表格结构不完整")
    rows = []
    for line in lines:
        line = line.strip()[1:]
        if line.endswith("|") and not line.endswith("\\|"):
            line = line[:-1]
        cells, start, in_math, index = [], 0, False, 0
        while index < len(line):
            char = line[index]
            if char == "\\" and index + 1 < len(line) and line[index + 1] == "|" and not in_math:
                index += 2
                continue
            if char == "$":
                in_math = not in_math
            if char == "|" and not in_math:
                cells.append(line[start:index].strip().replace("\\|", "|"))
                start = index + 1
            index += 1
        cells.append(line[start:].strip().replace("\\|", "|"))
        rows.append(cells)
    header = bool(rows[1] and any(rows[1]) and all(not cell or re.fullmatch(r":?-{3,}:?", cell) for cell in rows[1]))
    if header:
        rows = [rows[0], *rows[2:]]
    if len(rows) > 60 or any(len(row) > 20 for row in rows):
        _fail(where, "表格超出 60 行、20 列")
    return [
        [{"source": cell, "header": header and i == 0, "colspan": 1, "rowspan": 1} for cell in row]
        for i, row in enumerate(rows)
    ]


def _field(value, official, where, *, word_math=True):
    if not isinstance(value, dict) or set(value) != {"source", "blocks"} or value.get("source") != official:
        _fail(where, "原文与当前入库版不一致，请重新打开组卷", 409)
    offsets = _utf16_map(official, where)
    end = max(offsets)
    blocks = value.get("blocks")
    if not isinstance(blocks, list) or len(blocks) > 1000:
        _fail(where, "排版块格式不正确")
    cursor, result = 0, []
    for block in blocks:
        if not isinstance(block, dict):
            _fail(where, "排版块格式不正确")
        a, b = _bounds(block, offsets, cursor, end, where)
        if block.get("type") == "text" and set(block) == {"type", "start", "end", "segments"}:
            result.append(
                {
                    "type": "text",
                    "segments": _segments(official, block["segments"], a, b, where, offsets, word_math=word_math),
                }
            )
        elif block.get("type") == "table" and set(block) == {"type", "start", "end", "rows"}:
            expected = _table_source(official[offsets[a] : offsets[b]], where)
            rows = block["rows"]
            if not isinstance(rows, list) or len(rows) != len(expected):
                _fail(where, "表格行数与原文不符")
            converted = []
            for given, actual in zip(rows, expected):
                if not isinstance(given, list) or len(given) != len(actual):
                    _fail(where, "表格格数与原文不符")
                cells = []
                for cell, reference in zip(given, actual):
                    if (
                        not isinstance(cell, dict)
                        or set(cell) != {"source", "header", "colspan", "rowspan", "segments"}
                        or any(
                            cell.get(key) != reference[key] or type(cell.get(key)) is not type(reference[key])
                            for key in reference
                        )
                    ):
                        _fail(where, "表格内容或合并结构与原文不符")
                    cell_offsets = _utf16_map(cell["source"], where)
                    cells.append(
                        {
                            **reference,
                            "segments": _segments(
                                cell["source"],
                                cell["segments"],
                                0,
                                max(cell_offsets),
                                where,
                                cell_offsets,
                                word_math=word_math,
                            ),
                        }
                    )
                converted.append(cells)
            result.append({"type": "table", "rows": converted})
        else:
            _fail(where, "排版块含不支持的字段或类型")
        cursor = b
    if cursor != end:
        _fail(where, "排版结果没有完整覆盖原文，请重新打开组卷")
    return result


def _selected(content, extras, use_ai):
    if str(content.get("answer") or "").strip():
        return content, False
    ai = extras.get("ai_answer") if use_ai and isinstance(extras, dict) else None
    if isinstance(ai, dict) and (str(ai.get("answer") or "").strip() or str(ai.get("analysis") or "").strip()):
        return ai, True
    return (content, False) if str(content.get("analysis") or "").strip() else ({}, False)


def _has_selected_answers(captured):
    return any(
        str(item["selected"].get("answer") or "").strip()
        or str(item["selected"].get("analysis") or "").strip()
        or item.get("solution_images")
        for item in captured
    )


def _solution_selection(publication, use_ai, revision=None):
    from . import library_solutions

    try:
        solution = library_solutions.selected(publication, revision)
    except library_solutions.SolutionError as error:
        raise ExportError(str(error), error.status) from None
    if solution is not None:
        return {"answer": solution.answer, "analysis": solution.analysis}, False, solution
    selected, is_ai = _selected(publication.content, publication.extras, use_ai)
    return selected, is_ai, None


def _effective_document(captured, mode):
    # Match the preview: an optional answer appendix with no real content
    # must not produce a page of missing-answer placeholders.
    return "questions" if mode == "combined" and not _has_selected_answers(captured) else mode


def _images(publication, where):
    from PIL import Image

    images, total = [], 0
    image_body = publication.content.get("body_mode", "text") == "source_image"
    figures = publication.content.get("question_images" if image_body else "figures", [])
    if not isinstance(figures, list) or len(figures) > 64 or (image_body and not figures):
        _fail(where, "配图快照不完整", 409)
    for index, figure in enumerate(figures):
        label = f"{where} · {'题目片段' if image_body else '配图'} {index + 1}"
        if not isinstance(figure, dict) or (
            not image_body and figure.get("slot") not in {"stem", *"ABCDE", *(f"option_{key}" for key in "ABCDE")}
        ):
            _fail(label, "配图位置无法识别，不能省略", 409)
        name = figure.get("file")
        pattern = r"question-\d{1,3}\.png" if image_body else r"figure-\d{1,3}\.png"
        if not isinstance(name, str) or not re.fullmatch(pattern, name):
            _fail(label, "缺少入库配图文件", 409)
        folder = Path(settings.DATA_ROOT) / "library" / str(publication.id)
        target = folder / name
        try:
            if target.is_symlink() or folder.is_symlink() or target.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError
            data = target.read_bytes()
            total += len(data)
            if len(data) > MAX_IMAGE_BYTES or total > MAX_TOTAL_IMAGES:
                raise ValueError
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    size = image.size
                    if image.format != "PNG" or image.width < 1 or image.height < 1:
                        raise ValueError
                    image.verify()
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
            digest = hashlib.sha256(data).hexdigest()
            if image_body and (
                figure.get("image_sha256") != digest
                or type(figure.get("width")) is not int
                or type(figure.get("height")) is not int
                or (figure["width"], figure["height"]) != size
            ):
                _fail(label, "正文截图与已核对的入库版不一致，请检查原图题", 409)
            images.append(
                {
                    "slot": "body" if image_body else figure["slot"].removeprefix("option_"),
                    "bytes": data,
                    "size": size,
                    "name": name,
                    "sha256": digest,
                }
            )
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            _fail(label, "文件缺失、损坏或过大，请检查这道题的入库配图", 409)
    return images


def _capture(ids, rendered, options, output_format, *, word_math=None, solutions=None):
    from . import library_solutions

    try:
        solutions = library_solutions.normalize_map({} if solutions is None else solutions, ids)
    except library_solutions.SolutionError as error:
        raise ExportError(str(error), error.status) from None
    word_math = output_format != "pdf" if word_math is None else word_math
    if not isinstance(rendered, dict) or set(rendered) != set(ids):
        raise ExportError("每道选题都需提供完整排版结果，请重新打开组卷")
    found = {str(item.id): item for item in PublishedQuestion.objects.filter(pk__in=ids)}
    use_ai = options["ai_answers"] and features.enabled("ai_answer")
    question_fields = output_format == "split" or options["document"] != "answers"
    answer_fields = output_format == "split" or options["document"] != "questions"
    captured, total_bytes = [], 0
    for position, key in enumerate(ids, 1):
        where = f"第 {position} 题"
        publication = found.get(key)
        if publication is None or publication.status != PublishedQuestion.Status.PUBLISHED:
            _fail(where, "这条入库版已撤回、被替代或不存在；请返回组卷处理后再导出", 409)
        # 报错要能让人直接在这一卷上找到那道题，还要说得出它是哪份原卷的第几题 ——
        # 只给一个「本卷第 N 题」的话，想回原卷核对时还是得自己认。
        where = f"第 {position} 题（{publication.source_filename or '原卷'}，原卷第 {publication.number} 题）"
        if not isinstance(publication.content, dict) or not isinstance(publication.content.get("stem"), str):
            _fail(where, "题目快照不完整", 409)
        content, extras = deepcopy(publication.content), deepcopy(publication.extras)
        image_body = content.get("body_mode", "text") == "source_image"
        if content.get("body_mode", "text") not in {"text", "source_image"}:
            _fail(where, "题目正文类型无法识别", 409)
        if any(
            content.get(name) is not None and not isinstance(content[name], str)
            for name in ("answer", "analysis", "origin")
        ):
            _fail(where, "答案或题源快照格式不完整", 409)
        selected, is_ai, solution = _solution_selection(publication, use_ai, solutions.get(key))
        if any(
            selected.get(name) is not None and not isinstance(selected[name], str) for name in ("answer", "analysis")
        ):
            _fail(where, "所选参考答案格式不完整", 409)
        if is_ai and (
            (selected.get("publication_id") and str(selected["publication_id"]) != key)
            or (
                selected.get("fingerprint")
                and selected["fingerprint"] != library.generation_fingerprint(content, publication.id)
            )
        ):
            _fail(where, "AI 参考与这条入库版不匹配，请先核对或重新生成；也可关闭 AI 参考后导出", 409)
        required = {}
        if question_fields:
            required["stem"] = content["stem"]
            if not image_body and not required["stem"].strip():
                _fail(where, "题干为空，不能导出空题", 409)
            option_values = content.get("options") or {}
            if not isinstance(option_values, dict) or set(option_values) - set("ABCDE"):
                _fail(where, "选项快照不完整", 409)
            for letter, text in option_values.items():
                if not isinstance(text, str):
                    _fail(where, "选项快照不完整", 409)
                if text.strip() and not image_body:
                    required[f"options.{letter}"] = text
            if options["origin"] and str(content.get("origin") or "").strip():
                required["origin"] = content["origin"]
        if answer_fields:
            required.update({name: str(selected.get(name) or "") for name in ("answer", "analysis")})
        given = rendered[key]
        if not isinstance(given, dict) or set(given) - FIELDS or not set(required) <= set(given):
            _fail(where, "缺少题干、选项或所选答案的排版字段")
        converted = {
            name: _field(given[name], text, f"{where} · {name}", word_math=word_math) for name, text in required.items()
        }
        # Optional empty fields sent by older clients must still match the official value.
        for name in set(given) - set(required):
            if name in {"answer", "analysis"}:
                official = str(selected.get(name) or "")
            elif name.startswith("options."):
                official = str((content.get("options") or {}).get(name[-1]) or "")
            else:
                official = str(content.get(name) or "")
            _field(given[name], official, f"{where} · {name}", word_math=word_math)
        images = _images(publication, where) if question_fields else []
        try:
            solution_images = (
                library_solutions.capture_images(solution) if solution is not None and answer_fields else []
            )
        except library_solutions.SolutionError as error:
            _fail(where, str(error), error.status)
        total_bytes += sum(len(image["bytes"]) for image in images + solution_images)
        if total_bytes > MAX_TOTAL_IMAGES:
            _fail(where, "本次配图总量过大，请减少选题")
        captured.append(
            {
                "id": key,
                "where": where,
                "content": content,
                "extras": extras,
                "selected": deepcopy(selected),
                "ai": is_ai,
                "hash": publication.content_hash,
                "type": publication.question_type,
                "fields": converted,
                "images": images,
                "solution_images": solution_images,
                "solution_id": str(solution.pk) if solution is not None else None,
                "selection_revision": solutions.get(key),
                "solution_explicit": key in solutions,
            }
        )
    if (answer_fields and options["document"] == "answers") or output_format == "split":
        if not _has_selected_answers(captured):
            raise ExportError("这些题没有原卷答案或所选 AI 参考，请改选题目卷，或先补齐答案再分卷导出")
    return captured, use_ai


def _recheck(captured, options, use_ai):
    found = {str(item.id): item for item in PublishedQuestion.objects.filter(pk__in=[item["id"] for item in captured])}
    current_ai = options["ai_answers"] and features.enabled("ai_answer")
    for item in captured:
        live = found.get(item["id"])
        if (
            live is None
            or live.status != PublishedQuestion.Status.PUBLISHED
            or live.content_hash != item["hash"]
            or live.content != item["content"]
            or live.question_type != item["type"]
        ):
            _fail(item["where"], "导出期间入库版本发生变化，请重新打开组卷", 409)
        selected, is_ai, solution = _solution_selection(
            live, current_ai, item.get("selection_revision") if item.get("solution_explicit") else None
        )
        if selected != item["selected"] or is_ai != item["ai"] or use_ai != current_ai:
            _fail(item["where"], "导出期间答案或 AI 设置发生变化，请重新打开组卷", 409)
        current_images = _images(live, item["where"]) if item["images"] else []
        if len(current_images) != len(item["images"]):
            _fail(item["where"], "导出期间图片数量发生变化，请重新打开组卷", 409)
        for before, after in zip(item["images"], current_images):
            if before["sha256"] != after["sha256"]:
                _fail(item["where"], "导出期间配图发生变化，请重新打开组卷", 409)
        if (str(solution.pk) if solution is not None else None) != item.get("solution_id"):
            _fail(item["where"], "导出期间解析版本发生变化，请重新打开组卷", 409)
        if item.get("solution_images"):
            from . import library_solutions

            try:
                current = library_solutions.capture_images(solution)
            except library_solutions.SolutionError as error:
                _fail(item["where"], str(error), error.status)
            if len(current) != len(item["solution_images"]) or any(
                before != after for before, after in zip(item["solution_images"], current)
            ):
                _fail(item["where"], "导出期间解析图片发生变化，请重新打开组卷", 409)


def _font(run, size, bold=False):
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = "Cambria"
    run.font.size = Pt(size)
    run.bold = bold
    run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    return run


_FORMULA_DRIVER = r"""
window.__qbPictureStatus = {ready:false};
(async () => {
  try {
    const data = JSON.parse(document.getElementById('formulaData').textContent);
    const host = document.getElementById('qbFormulas');
    host.replaceChildren(...data.map(item => {
      const node = document.createElement('span');
      node.className = 'qb-math';
      katex.render(item.latex, node, {
        displayMode: false, throwOnError: true, strict: 'ignore', trust: false,
        maxSize: 10, maxExpand: 1000,
        macros: {"\\parallel":"\\mathrel{/\\mkern-6mu/}","\\nparallel":"\\mathrel{/\\mkern-6mu/\\mkern-11mu\\backslash}"}
      });
      return node;
    }));
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const boxes = [];
    for (const node of host.children) {
      const r = node.getBoundingClientRect();
      const padding = 2;
      boxes.push({x: Math.max(0, r.x + scrollX - padding), y: Math.max(0, r.y + scrollY - padding),
        width: r.width + padding * 2, height: r.height + padding * 2});
    }
    window.__qbPictureStatus = {ready:true, boxes};
  } catch (error) {
    window.__qbPictureStatus = {ready:true, error: String(error && error.message || error).slice(0, 200)};
  }
})();
"""


def _formula_document(requests, font_size):
    """A fixed document holding every degraded formula, and nothing else."""
    from . import library_pdf as pdf

    if type(font_size) is not int or font_size not in (12, 14, 16):
        _fail(requests[0]["where"], "公式字号不正确")
    css, scripts = pdf._assets(Path(settings.FRONTEND_ROOT))
    if re.search(r"</style", css, re.I):
        _fail(requests[0]["where"], "公式排版资源不完整，请修复或重新安装题有据", 503)
    import html as _html
    import secrets

    nonce = secrets.token_urlsafe(24)
    data = json.dumps([{"latex": request["latex"]} for request in requests], ensure_ascii=False)
    data = (
        data.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    policy = (
        "default-src 'none'; script-src 'nonce-" + nonce + "'; style-src 'unsafe-inline'; "
        "img-src data:; font-src data:; connect-src 'none'; object-src 'none'; base-uri 'none'; "
        "form-action 'none'; frame-src 'none'"
    )
    # Inline on one line, in reading order: the screenshot rect of each child
    # is the formula, so nothing here can shift what a later formula looks like.
    # The app stylesheet paints the paper colour on html/body; that would print a
    # beige rectangle behind every formula in Word, and it would also make the
    # blank-image check below impossible to trigger.
    fixed = (
        "html,body{margin:0;background:transparent!important}"
        "#qbFormulas{position:absolute;left:0;top:0;width:auto;padding:2px;"
        f"font-size:{font_size}pt;color:#000;line-height:1.4;background:transparent}}"
        "#qbFormulas>span{display:inline-block;vertical-align:middle;"
        "background:transparent;padding:0}"
    )
    katex = (
        '<script nonce="'
        + nonce
        + '" src="data:application/javascript;base64,'
        + base64.b64encode(scripts[0].encode("utf-8")).decode("ascii")
        + '"></script>'
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="' + _html.escape(policy, quote=True) + '">'
        "<title>公式</title><style>" + css + fixed + "</style></head><body>"
        '<div id="qbFormulas"></div>'
        '<script id="formulaData" type="application/json">'
        + data
        + "</script>"
        + katex
        + '<script nonce="'
        + nonce
        + '">'
        + _FORMULA_DRIVER
        + "</script></body></html>"
    )


def _formula_pictures(requests, font_size):
    """Render every degraded formula to a PNG in one browser session.

    One launch, one document, one screenshot per formula — starting a browser
    per formula would make a long paper unusable. If the browser cannot be used
    at all the caller still refuses the download: a paper the reader cannot
    follow is worse than no paper.
    """
    if not _picture_lock.acquire(blocking=False):
        raise ExportError("另一份试卷正在排版公式，请稍后重试。", 409)
    try:
        return _render_formula_pictures(requests, font_size)
    finally:
        _picture_lock.release()


def _render_formula_pictures(requests, font_size):
    """One browser session; every way it can fail ends in the same user advice."""
    from . import library_pdf as pdf

    where = requests[0]["where"]
    process = client = None
    try:
        try:
            executable = pdf._browser_path()
            deadline = time.monotonic() + pdf.DEADLINE_SECONDS
            with pdf._temporary_directory() as directory:
                root = Path(directory).resolve()
                if root.parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith("tiyouju-pdf-"):
                    raise ValueError("temporary directory")
                profile = root / "profile"
                process = pdf._launch_browser(executable, profile)
                port = pdf._wait_debug_port(profile, process, deadline)
                opener = pdf.build_opener(pdf.ProxyHandler({}))
                target = pdf._wait_page_target(opener, port, process, deadline)
                match = re.fullmatch(
                    rf"ws://(?:127\.0\.0\.1|localhost):{port}(/devtools/page/[A-Za-z0-9_-]+)",
                    target["webSocketDebuggerUrl"],
                )
                if not match:
                    raise ValueError("debug target")
                client = pdf._CDP(port, match[1], deadline)
                client.call("Page.enable")
                client.call("Runtime.enable")
                client.call("Network.enable")
                client.call(
                    "Network.setBlockedURLs",
                    {"urls": ["http://*", "https://*", "ws://*", "wss://*", "ftp://*", "file://*"]},
                )
                # omitBackground alone still paints an opaque backdrop once the
                # screenshot is clipped; this is what actually yields transparency.
                client.call("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
                client.call(
                    "Page.setDocumentContent",
                    {
                        "frameId": client.call("Page.getFrameTree")["frameTree"]["frame"]["id"],
                        "html": _formula_document(requests, font_size),
                    },
                )
                status = {}
                while True:
                    value = client.call(
                        "Runtime.evaluate",
                        {"expression": "window.__qbPictureStatus || {ready:false}", "returnByValue": True},
                    )
                    status = value.get("result", {}).get("value", {})
                    if status.get("ready"):
                        break
                    pdf._remaining(deadline)
                    time.sleep(0.05)
                boxes = status.get("boxes")
                if status.get("error") or not isinstance(boxes, list) or len(boxes) != len(requests):
                    raise ValueError("formula layout")
                return [_formula_png(client, box) for box in boxes]
        finally:
            # Cleanup failures must not replace validated bytes or the main error.
            pdf._cleanup_browser(client, process)
            client = process = None
    except Exception:
        # No browser, no time, nothing drawn: every one of those ends the same way,
        # and none of them may be turned into a blank box on the paper.
        raise ExportError(f"{where}：{_FALLBACK_REFUSAL}", 400) from None


def _formula_png(client, box):
    """One clipped screenshot, refused unless it actually drew something."""
    from PIL import Image

    if not isinstance(box, dict) or any(
        type(box.get(key)) not in (int, float) for key in ("x", "y", "width", "height")
    ):
        raise ValueError("formula box")
    width, height = box["width"], box["height"]
    if not (0 < width <= MAX_FORMULA_BOX[0] and 0 < height <= MAX_FORMULA_BOX[1]):
        raise ValueError("formula box")
    clip = {"x": box["x"], "y": box["y"], "width": width, "height": height, "scale": FORMULA_CAPTURE_SCALE}
    for attempt in range(4):
        result = client.call(
            "Page.captureScreenshot",
            {"format": "png", "omitBackground": True, "captureBeyondViewport": True, "clip": clip},
        )
        try:
            data = base64.b64decode(result.get("data", ""), validate=True)
        except (ValueError, TypeError):
            raise ValueError("formula png") from None
        if not data.startswith(b"\x89PNG"):
            raise ValueError("formula png")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                rgba = image.convert("RGBA")
                white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                flat = Image.alpha_composite(white, rgba).convert("RGB")
                size = image.size
                # Composite over the Word page's white background before checking
                # colour. Dropping alpha would turn black glyphs on transparency black.
                colours = flat.getcolors(maxcolors=2)
                visible = colours is None or len(colours) > 1
        except Exception:
            raise ValueError("unreadable formula png") from None
        if visible:
            return {"bytes": data, "size": size}
        if attempt < 3:
            # A just-inserted KaTeX SVG can report its final box one paint before
            # Chromium composites its strokes. Give it two frames, then retry.
            client.call(
                "Runtime.evaluate",
                {
                    "expression": "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
                    "awaitPromise": True,
                    "returnByValue": True,
                },
            )
    raise ValueError("blank formula")


def _fill_fallback_pictures(captured, font_size):
    """Render every degraded formula in the paper at once; report each by position."""
    pending = [
        segment
        for item in captured
        for blocks in item["fields"].values()
        for block in blocks
        for segment in block.get("segments", ())
        if segment.get("fallback")
    ]
    if not pending:
        return []
    if len(pending) > MAX_FALLBACK_FORMULAS:
        _fail(
            pending[0]["fallback"], f"本卷有 {len(pending)} 条公式无法转为 Word 公式，请减少选题或改用打印 / 保存 PDF"
        )
    pictures = _formula_pictures(
        [{"latex": segment["latex"], "where": segment["fallback"]} for segment in pending], font_size
    )
    if len(pictures) != len(pending) or sum(len(picture["bytes"]) for picture in pictures) > MAX_FALLBACK_BYTES:
        _fail(pending[0]["fallback"], _FALLBACK_REFUSAL)
    for segment, picture in zip(pending, pictures):
        segment["picture"] = picture
    return [
        f"{segment['fallback'].rsplit(' · ', 1)[0]} · "
        f"{_field_label(segment['fallback'].rsplit(' · ', 1)[-1])}：{_FALLBACK_ADVICE}。"
        for segment in pending
    ]


def _field_label(name):
    return f"选项 {name[-1]}" if name.startswith("options.") else _FIELD_LABELS.get(name, "正文")


def _write_segments(paragraph, segments, size, bold=False):
    from docx.shared import Mm

    for segment in segments:
        kind = segment["type"]
        if kind == "math":
            picture = segment.get("picture")
            if picture:
                # Word cannot express this one. Keep every character of it, as a
                # picture sized like the text it sits between.
                width, height = picture["size"]
                # CDP captured at 3 px per CSS px. Convert using CSS's 96 dpi,
                # or inline formulas become almost twice as large in Word.
                scale = min(25.4 / (CSS_PIXELS_PER_INCH * FORMULA_CAPTURE_SCALE), 90 / width, 10 / height)
                run = paragraph.add_run()
                _font(run, size, bold)
                run.add_picture(io.BytesIO(picture["bytes"]), width=Mm(width * scale), height=Mm(height * scale))
            else:
                # Native oMath, not screenshots. Display grouping is kept in one paragraph.
                paragraph._p.append(deepcopy(segment["omml"]))
        elif kind != "delimiter":
            shown = (
                "________"
                if kind == "blank"
                else "（\u3000\u3000）"
                if kind == "bracket"
                else "▱"
                if kind == "parallelogram"
                else segment["raw"]
            )
            _font(paragraph.add_run(shown), size, bold)


# Word 标称单元格左右各留 0.19cm，合起来 3.8mm。实测按 3.8 算仍然会差一点：
# 「二次项系数」五个字要 21.17mm，列宽算出来 24.9mm，减去 3.8 只剩 21.1mm，
# 就差 0.07mm 被顶成两行。留到 5mm，一列多 1.2mm，十三列的表也还有富余。
CELL_PADDING_MM = 5.0
# 一列最少给多少。跟预览里 `.qb-table td { min-width: 2.6em }` 一个量级，
# 免得内容窄的时候挤成一条看不见的缝。
COLUMN_FLOOR_MM = 8.0


def _WIDE_CHAR(ch):
    """中日韩字符和全角标点按一个字宽算，其余按半个字宽。"""
    return "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _cell_width_mm(cell, size):
    """这一格大概要占多宽（毫米）。

    系数是量出来的，不是猜的：12pt 正文里一个汉字 4.23mm、数字 2.51mm，
    也就是 1 em = 4.23mm、数字 0.6 em。取 0.6 是宁可高估一点——
    差 0.3mm 就够把「12」顶到换行（实测 8.3mm 的格子放不下 5.02mm 的「12」）。

    公式段按 LaTeX 去命令后估：`\\tan\\theta` 当 4 个字符算，量出来 8.26mm。
    """
    em = 0.0
    for segment in cell["segments"]:
        if segment.get("type") == "math":
            stripped = re.sub(r"\\[a-zA-Z]+", "ab", segment.get("latex") or "")
            em += max(1.0, len(stripped) * 0.6)
        else:
            em += sum(1.0 if _WIDE_CHAR(ch) else 0.6 for ch in segment.get("raw") or "")
    return em * size * 25.4 / 72.0 + CELL_PADDING_MM


def _fair_widths(need, budget):
    """版心放不下时，先让每一列都够用，实在放不下的几列再一起挤。

    按比例全体缩小是错的：九上「根据图象，填写下表」那张五列表（性质/二次函数、
    开口方向、对称轴、顶点坐标、取哪些值时……）需求合计 195.2mm，超版心 17.2mm。
    按比例缩到 0.912 之后，「开口方向」本该 21.93mm 只剩 20.00mm，扣掉 Word 单元格
    左右各 1.9mm 只余 16.2mm，而四个字实测 16.99mm——差 0.79mm，表头就被顶成
    「开口方 / 向」两行。同一张表里需求本来就放得下的三列，全被陪着缩窄了。

    这里按水位线分：需求不超过水位的列照需求给，剩下的列一律给水位线，水位由
    「未分到水位的列的需求之和」摊平得出。对五列表算出来是
    ``[48.18, 21.93, 17.70, 21.93, 68.26]``——需求放得下的四列全都拿到完整需求，
    只有最后那列（需求 85.43mm，本来就超出版心）被挤；按比例缩放则是四列一起折行。

    水位线取的是「能让总和凑齐版心的最低水位」，所以能让多少列保持完整需求就让
    多少列保持——**折行的格子数最少**，这才是换掉按比例的理由（按比例并不逐列
    都不更窄：上面那列按比例给 77.91mm，比水位线的 68.26mm 还宽）。
    """
    capped = list(range(len(need)))
    free = 0.0
    level = budget / len(need) if need else 0.0
    while capped:
        level = (budget - free) / len(capped)
        small = [index for index in capped if need[index] <= level]
        if not small:
            break
        for index in small:
            free += need[index]
        capped = [index for index in capped if need[index] > level]
    capped_set = set(capped)
    return [level if index in capped_set else need[index] for index in range(len(need))]


def _table_column_widths(placements, width, size, text_width_mm=178.0):
    """按内容分列宽，跟预览里那张表一样（`.qb-table` 是 table-layout:auto，不写死列宽）。

    早先每列都 `Mm(178 / width)`：13 列的表每列只剩 13.7mm，「零售收入/万元」
    七个字被挤成五行，整张表凭空长高一倍——同一份内容在 PDF 路线里是一行。
    真库 27 个表格里 11 个会这样，九上第 1 题（#1282）最典型。
    """
    need = [COLUMN_FLOOR_MM] * width
    for _, column, cell in placements:
        span = min(max(1, cell["colspan"]), width - column)
        share = _cell_width_mm(cell, size) / span
        for offset in range(span):
            need[column + offset] = max(need[column + offset], share)
    total = sum(need)
    if total <= text_width_mm:
        # 内容窄就照内容的宽度印，别硬撑满——预览里那张表也是这么收着的。
        return need
    return _fair_widths(need, text_width_mm)


def _write_table(document, rows, size, where):
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm

    # Place rowspan cells into a complete grid; overlapping/out-of-bounds spans abort.
    grid, placements, width = {}, [], 0
    for r, row in enumerate(rows):
        column = 0
        for cell in row:
            while (r, column) in grid:
                column += 1
            h, w = cell["rowspan"], cell["colspan"]
            if r + h > len(rows) or column + w > 40:
                _fail(where, "表格合并单元格超出边界")
            for y in range(r, r + h):
                for x in range(column, column + w):
                    if (y, x) in grid:
                        _fail(where, "表格合并单元格重叠")
                    grid[y, x] = True
            placements.append((r, column, cell))
            column += w
            width = max(width, column)
    if width < 1:
        _fail(where, "表格没有可导出的单元格")
    table = document.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    table.autofit = False
    # `.qb-table { margin: 0 auto }`：预览里窄表是居中的，Word 这边也要跟着居中。
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    for column, value in zip(table.columns, _table_column_widths(placements, width, size)):
        column.width = Mm(value)
    for r, c, cell in placements:
        target = table.cell(r, c)
        if cell["rowspan"] > 1 or cell["colspan"] > 1:
            target = target.merge(table.cell(r + cell["rowspan"] - 1, c + cell["colspan"] - 1))
        target.text = ""
        _write_segments(target.paragraphs[0], cell["segments"], size, cell["header"])
        if cell["header"]:
            shade = OxmlElement("w:shd")
            shade.set(qn("w:fill"), "F1F5F3")
            target._tc.get_or_add_tcPr().append(shade)
    for row in table.rows:
        avoid = OxmlElement("w:cantSplit")
        row._tr.get_or_add_trPr().append(avoid)


def _write_field(document, blocks, size, prefix="", where="", lead_segments=None):
    from docx.shared import Pt

    paragraph = None
    if prefix:
        paragraph = document.add_paragraph()
        _font(paragraph.add_run(prefix), size)
    if lead_segments:
        paragraph = paragraph or document.add_paragraph()
        _write_segments(paragraph, lead_segments, size)
    for block in blocks:
        if block["type"] == "table":
            _write_table(document, block["rows"], size, where)
            paragraph = None
        else:
            display_group = None
            for segment in block["segments"]:
                if segment["type"] == "delimiter":
                    continue
                group = segment.get("displayGroup") or ("display" if segment.get("display") else None)
                if group != display_group and (group is not None or display_group is not None):
                    paragraph = None
                # Source newlines become real Word paragraphs so compact mode
                # can break between subquestions, rather than keeping a huge
                # paragraph with many manual line breaks on one page. Empty
                # source lines use the normal paragraph gap, not extra full-
                # height empty paragraphs; inline spaces still remain intact.
                parts = re.split(r"\r\n|[\r\n]", segment["raw"]) if segment["type"] == "text" else [None]
                for index, part in enumerate(parts):
                    if part is None or (part and (paragraph is not None or part.strip())):
                        paragraph = paragraph or document.add_paragraph()
                        if group is not None:
                            paragraph.alignment = 1
                        _write_segments(paragraph, [{**segment, "raw": part} if part is not None else segment], size)
                        paragraph.paragraph_format.space_after = Pt(PARAGRAPH_GAP_PT)
                        paragraph.paragraph_format.line_spacing = LINE_SPACING
                    if index < len(parts) - 1:
                        paragraph = None
                display_group = group
            paragraph = None


# 组卷预览里的配图不是交给样式表管的。frontend/exam-layout.js 的 paginate() 会等
# 每张图解码完，然后亲手往 <img> 上写死一行内联宽度。样式表里 .qb-figure img 的
# max-width: min(100%, 320px) 与 max-height: 220px 在排版流程里被 library.css 的
# .print-flow 覆盖成 max-width: 100% / max-height: none，那两条上限根本没生效
# （实测：原图 294×201 的配图在预览里显示 188px 宽，不是 320 也不是 220）。
#
# 它写死的那行宽度是：
#     宽 = 原像素 × min(96 / 150, 178mm / 原像素宽, 210mm / 原像素高)
# 也就是「按 Word 的 150dpi 自然尺寸摆，再保证不超过正文栏、不超过一页高」。
# 96 / 150 = 0.64 正是把一个 96dpi 的屏幕像素折成 150dpi 纸上尺寸的比例；279px
# 的图在预览里是 178.56px，正是 279 × 0.64。
#
# 下面三个常量把这行算式逐字抄过来。导出要对齐的是这一行，不是样式表那两条上限。
PREVIEW_FIGURE_DPI = 150
PREVIEW_TEXT_WIDTH_MM = 178
PREVIEW_PAGE_HEIGHT_MM = 210
# 配图行是一个普通的 flex-wrap 容器（.qb-figures，gap 14px、justify-content: center）：
# 从左往右排，加上间距塞不下正文栏就换下一行。一 CSS 像素是 1/96 英寸，14px 即 3.70mm。
PREVIEW_PX_PER_INCH = 96
PREVIEW_FIGURE_GAP_PX = 14
# 选项列数不再照抄 styles.css 那条 "@container qpaper (max-width: 680px) {
# .qb-options.cols-4 -> two columns }"。
#
# 那条规则在浏览器里从来不生效：``exam-layout.js`` 的 ``fitOptions`` 对每一个
# ``.qb-options`` 都无条件写一行内联 ``grid-template-columns``（那是它逐级降列的实现），
# 内联样式压过样式表，容器查询也压不过。实测（2026-10-11，2024 全国甲卷理，23 题）：
# 老师选「四项一行」，PDF 4 页、选项一行四个；Word 5 页、2×2。而且四个选项只有
# ``10i / 2i / 10 / −2``，宽度上排四列绰绰有余——那条降列从来就没量过宽度，于是
# 给老师的提示「选项较宽，Word 已使用两列」是在冤枉选项。
#
# 现在 Word 只按自己量出来的宽度决定列数（``_option_columns``），和 PDF 一致；
# 真的排不下四列时它才降，并如实说是宽度不够。
#
# Known remaining difference: five options each carrying a diagram. The
# preview keeps five columns and lets the images shrink to ~82px, while Word
# refuses to shrink content to hold a column count (it warns instead), so it
# lays those out in two columns. Left alone on purpose.


def _figure_paper_size(
    size, text_width_mm=PREVIEW_TEXT_WIDTH_MM, page_height_mm=PREVIEW_PAGE_HEIGHT_MM, forced_mm=None
):
    """``(width_mm, height_mm)``: how large one figure lands on paper.

    This is ``exam-layout.js``'s ``imageScale`` transcribed, because that is
    what the print preview really draws: a picture sits at Word's natural
    150 dpi size, capped so it is never wider than the text area and never
    taller than one A4 page. Sizing from the stylesheet's 320px/220px caps
    instead is exactly what made the download disagree with the screen — those
    caps are overridden inside the print flow, so they describe a view no
    teacher looks at before exporting.

    ``forced_mm`` is the teacher's own width in millimetres, and it takes the
    place of the 150 dpi term rather than scaling on top of it: the preview's
    line reads ``min(chosen, 178, 210 * naturalWidth / naturalHeight)``, so a
    number typed into the preview is a millimetre on paper, not a percentage of
    a default the teacher never chose.
    """
    width, height = float(size[0]), float(size[1])
    if width <= 0 or height <= 0:
        return 0.0, 0.0
    paper = min(
        float(forced_mm) if forced_mm else width * 25.4 / PREVIEW_FIGURE_DPI,
        float(text_width_mm),
        page_height_mm * width / height,
    )
    return paper, paper * height / width


def _figure_settings(options, item):
    """One question's saved figure adjustments, cleaned into the shapes below use.

    Draft storage already checked these values, but the export endpoint takes
    them straight off the request, so the keys arrive as the strings a JSON
    object turns them into and anything odd is dropped rather than allowed to
    reach the layout maths.
    """
    settings = (options.get("figure_overrides") or {}).get(item["id"]) or {}
    if not isinstance(settings, dict):
        return {"width": {}, "breaks": [], "align": "center"}
    widths = {}
    for key, value in (settings.get("width") or {}).items():
        try:
            index = int(key)
            millimetres = float(value)
        except (TypeError, ValueError):
            continue
        if index >= 0 and millimetres > 0:
            widths[index] = millimetres
    breaks = sorted({int(value) for value in settings.get("breaks") or [] if str(value).lstrip("-").isdigit()})
    align = settings.get("align")
    return {"width": widths, "breaks": breaks, "align": align if align in ("left", "center", "right") else "center"}


def _figure_widths(images, text_width_mm=PREVIEW_TEXT_WIDTH_MM, settings=None):
    """Every picture's printed width in millimetres, teacher's override applied."""
    overrides = (settings or {}).get("width") or {}
    return [
        _figure_paper_size(image["size"], text_width_mm, forced_mm=overrides.get(index))[0]
        for index, image in enumerate(images)
    ]


def _pack_lines(widths, text_width_mm=PREVIEW_TEXT_WIDTH_MM, forced=()):
    """Greedy line packing against the text area, honouring named line starts.

    ``.qb-figures`` fills a line until the next picture no longer fits and then
    wraps — greedy, left to right, never shrinking a row to make room. A
    ``forced`` index is the teacher naming the figure that starts a new line; the
    preview inserts a full-width spacer there, which is the same break told in
    the language of a table row.
    """
    gap = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH
    forced = set(forced or ())
    lines, current, used = [], 0, 0.0
    for index, width in enumerate(widths):
        if index in forced and current:
            lines.append(current)
            current, used = 0, 0.0
        if current and used + gap + width > text_width_mm + 0.01:
            lines.append(current)
            current, used = 0, 0.0
        used += (gap if current else 0.0) + width
        current += 1
    if current:
        lines.append(current)
    return lines or [1]


def _figure_row_lines(images, text_width_mm=PREVIEW_TEXT_WIDTH_MM, settings=None):
    """How many figures the preview puts on each line, e.g. ``[2, 1]``.

    The line lengths are kept, not just the widest one. The preview centres
    *every* line on its own (``justify-content: center`` applies per flex line),
    and one uniform Word grid cannot do that: a 3+2+1 set comes out right, but
    2+1 puts the lone third picture in the first column, hard against the left
    of a table that is itself centred. Three questions in the bank wrap that way,
    so each line gets its own table.
    """
    return _pack_lines(_figure_widths(images, text_width_mm, settings), text_width_mm, (settings or {}).get("breaks"))


def _write_images(document, item, slot, lines=None, settings=None):
    from docx.shared import Mm

    pictures = [image for image in item["images"] if image["slot"] == slot]
    if not pictures:
        return
    align = (settings or {}).get("align", "center")
    widths = _figure_widths(pictures, PREVIEW_TEXT_WIDTH_MM, settings if slot != "body" else None)
    if slot != "body" and lines is None:
        lines = _figure_row_lines(pictures, settings=settings)
    # Only a line that actually holds two or more diagrams needs a table. When
    # every line holds one, they are lone figures and a single aligned
    # paragraph says so with less machinery than a one-column table.
    if lines and max(lines) > 1:
        _write_figure_rows(document, pictures, lines, align, widths)
        return
    for index, image in enumerate(pictures):
        width, height = image["size"]
        # One figure per line keeps the same 150 dpi natural size the preview
        # uses. A scanned page ("body") may be taller, because the preview
        # stacks those vertically instead of feeding them into the figure row.
        paper = widths[index] if slot != "body" else _figure_paper_size(image["size"], PREVIEW_TEXT_WIDTH_MM, 235)[0]
        paragraph = document.add_paragraph()
        # A lone stem figure still belongs to a .qb-figures row, and that row is
        # centred — measured in the app, a single 188px picture sits with 242px of
        # space on each side. Left alone, Word parks it against the margin, which
        # is the one case where the download reads visibly off-centre. A scanned
        # page is the opposite case: .qb-question-images is a column flex box, so
        # a picture with a set width stays at the left there, and must here too.
        if slot != "body":
            from docx.enum.text import WD_ALIGN_PARAGRAPH

            paragraph.alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "right": WD_ALIGN_PARAGRAPH.RIGHT}.get(
                align, WD_ALIGN_PARAGRAPH.CENTER
            )
        paragraph.add_run().add_picture(io.BytesIO(image["bytes"]), width=Mm(paper), height=Mm(height * paper / width))
        paragraph.paragraph_format.keep_together = True


def _write_figure_rows(document, pictures, lines, align="center", widths=None):
    """One table per preview line, separated so Word keeps them apart."""
    widths = list(widths or _figure_widths(pictures))
    start = 0
    for number, count in enumerate(lines):
        chunk = pictures[start : start + count]
        start += count
        if number:
            # Two w:tbl elements with nothing between them are one table to Word,
            # which would drag the second line's columns back to the first line's
            # width. The paragraph is 1pt tall and carries the preview's row gap.
            _figure_row_separator(document)
        _write_figure_line(document, chunk, align=align, widths=widths[start - count : start])
    return start


def _figure_row_separator(document):
    from docx.shared import Mm, Pt

    paragraph = document.add_paragraph()
    paragraph.add_run().font.size = Pt(1)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = Mm(PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH)
    return paragraph


def _write_figure_line(document, pictures, text_width_mm=PREVIEW_TEXT_WIDTH_MM, align="center", widths=None):
    """One line of stem figures, at the size and the position the preview shows.

    Three things have to match or the download reads differently from the
    screen it was checked on:

    - **Size.** Every picture is placed by ``_figure_paper_size`` — the same
      150 dpi size the preview writes onto the image itself, or the width the
      teacher typed into the preview. Guessing from the stylesheet's 320px/220px
      caps makes the download disagree with the preview, because the print flow
      overrides those caps.
    - **The gap between figures.** Word table columns touch each other, so the
      gap has to live *inside* a column. Dividing the text area between
      columns instead puts the pictures edge to edge, and the labels A–F of
      neighbouring diagrams end up touching.
    - **Where the row sits.** ``.qb-figures`` positions each row with
      ``justify-content``, and the teacher can choose left, centre or right.
      The table follows that choice, and the outer gap is dropped on whichever
      end the row is pushed towards — otherwise a left-aligned row would sit
      1.85mm off the margin the teacher aimed at. Centring only means anything
      once the table's own width is pinned and its cells stop carrying Word's
      default 1.9mm padding, and once every column holds the gap — including the
      last, which is what leaves the same space before the first diagram as
      after the last.
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm

    # Widths arrive already resolved (teacher's millimetres, else 150 dpi natural
    # size); the height follows from the picture's own aspect ratio so a widened
    # diagram grows as tall as it needs and does not come out stretched.
    widths = [float(width) for width in (widths if widths is not None else _figure_widths(pictures, text_width_mm))]
    heights = [width * float(image["size"][1]) / float(image["size"][0]) for width, image in zip(widths, pictures)]
    gap_mm = PREVIEW_FIGURE_GAP_PX * 25.4 / PREVIEW_PX_PER_INCH
    # Fit is judged on the pictures and the gaps between them. The empty space
    # the columns add at each end is allowed to spill into the A4 margins rather
    # than shrink the diagrams by a percent and put them back out of step with
    # the preview.
    row_mm = sum(widths) + gap_mm * max(len(widths) - 1, 0)
    if row_mm > text_width_mm:
        shrink = text_width_mm / row_mm
        widths = [width * shrink for width in widths]
        heights = [height * shrink for height in heights]
        gap_mm *= shrink
    # The gap belongs *between* two diagrams. A centred row therefore carries
    # half of it at each end (flex's `justify-content: center` leaves a gap
    # outside the first and last picture only because both ends are equally
    # spaced), but a row pushed to the left or right has one open end, and a gap
    # there would push the row 1.85mm off the margin the teacher aimed at.
    if align == "left":
        column_widths = [
            width + (gap_mm if position < len(widths) - 1 else 0.0) for position, width in enumerate(widths)
        ]
    elif align == "right":
        column_widths = [width + (gap_mm if position else 0.0) for position, width in enumerate(widths)]
    else:
        column_widths = [width + gap_mm for width in widths]
    table = document.add_table(rows=1, cols=len(pictures))
    table.autofit = False
    table.alignment = {"left": WD_TABLE_ALIGNMENT.LEFT, "right": WD_TABLE_ALIGNMENT.RIGHT}.get(
        align, WD_TABLE_ALIGNMENT.CENTER
    )
    for column, width in zip(table.columns, column_widths):
        column.width = Mm(width)
    properties = table._tbl.tblPr

    # Word pads every table cell by 1.9mm on each side. A column that already
    # carries the gap between two diagrams then ends up 3.8mm wider than its
    # contents, and a picture that exactly fills the last column is pushed off
    # centre by about 4mm — measured in Word 16.0, not estimated. The gap is
    # already part of the column width above, so the cells get no padding.
    cell_margins = OxmlElement("w:tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        edge = OxmlElement("w:" + side)
        edge.set(qn("w:w"), "0")
        edge.set(qn("w:type"), "dxa")
        cell_margins.append(edge)
    properties.insert_element_before(cell_margins, "w:tblLook", "w:tblCaption", "w:tblDescription")

    # State the width instead of leaving "auto" for Word to re-infer from the
    # content, which is how a centred table still ends up hugging the margin.
    table_width = properties.find(qn("w:tblW"))
    if table_width is None:
        table_width = OxmlElement("w:tblW")
        properties.insert_element_before(
            table_width,
            "w:jc",
            "w:tblCellSpacing",
            "w:tblInd",
            "w:tblBorders",
            "w:shd",
            "w:tblLayout",
            "w:tblCellMar",
            "w:tblLook",
            "w:tblCaption",
            "w:tblDescription",
        )
    table_width.set(qn("w:w"), str(int(round(sum(column_widths) * 1440 / 25.4))))
    table_width.set(qn("w:type"), "dxa")

    # w:tblPr is a fixed sequence: borders belong between w:jc and w:tblLayout.
    # Appending them at the end put them after w:tblLook, which is not a valid
    # document and is exactly the kind of thing Word quietly repairs on its own
    # terms. Insert against the elements that must follow instead.
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + side)
        border.set(qn("w:val"), "nil")
        borders.append(border)
    properties.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"
    )

    for index, image in enumerate(pictures):
        width_mm, height_mm = widths[index], heights[index]
        cell_width = column_widths[index]
        fit = min(1.0, cell_width / width_mm, 210 / height_mm)
        cell = table.cell(0, index)
        cell.width = Mm(cell_width)
        paragraph = cell.paragraphs[0]
        # .qb-figures places each row with justify-content; the grid cells do
        # the same job inside the table, and a lone figure in a one-picture
        # column needs it just as much as a row of three.
        paragraph.alignment = {"left": 0, "right": 2}.get(align, 1)
        paragraph.add_run().add_picture(
            io.BytesIO(image["bytes"]), width=Mm(width_mm * fit), height=Mm(height_mm * fit)
        )
        paragraph.paragraph_format.keep_together = True
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def _analysis_parts(blocks):
    """Split rendered analysis at blank source lines without flattening math/tables."""
    parts, current = [], []
    for block in blocks:
        if block["type"] == "table":
            current.append(block)
            continue
        segments = []
        for segment in block["segments"]:
            if segment["type"] != "text":
                segments.append(segment)
                continue
            pieces = re.split(r"\r?\n(?:[ \t]*\r?\n)+", segment["raw"])
            for index, raw in enumerate(pieces):
                if raw:
                    segments.append({**segment, "raw": raw})
                if index < len(pieces) - 1:
                    if segments:
                        current.append({"type": "text", "segments": segments})
                    if current:
                        parts.append(current)
                    current, segments = [], []
        if segments:
            current.append({"type": "text", "segments": segments})
    if current:
        parts.append(current)
    return parts


def _write_solution_image(document, image):
    from docx.shared import Mm

    width, height = image["size"]
    scale = min(float(image["display_width"]) / width, 235 / height)
    paragraph = document.add_paragraph()
    paragraph.add_run().add_picture(io.BytesIO(image["bytes"]), width=Mm(width * scale), height=Mm(height * scale))
    paragraph.paragraph_format.keep_together = True


def _write_solution(document, item, size, label):
    selected, images = item["selected"], item.get("solution_images", [])
    if not str(selected.get("answer") or "").strip() and not str(selected.get("analysis") or "").strip() and not images:
        _font(document.add_paragraph().add_run(label + "（原卷未提供答案）"), size)
        return
    if str(selected.get("answer") or "").strip():
        _write_field(document, item["fields"]["answer"], size, prefix=label + " ", where=item["where"])
    else:
        _font(document.add_paragraph().add_run(label + " 解析："), size)
    for image in images:
        if image["position"] == "before":
            _write_solution_image(document, image)
    parts = _analysis_parts(item["fields"].get("analysis", []))
    for index, blocks in enumerate(parts):
        _write_field(
            document,
            blocks,
            size,
            prefix="解析：" if index == 0 and selected.get("answer") else "",
            where=item["where"],
        )
        for image in images:
            if image["position"] == "paragraph" and image["paragraph"] == index:
                _write_solution_image(document, image)
    for image in images:
        if image["position"] == "after" or (image["position"] == "paragraph" and image["paragraph"] >= len(parts)):
            _write_solution_image(document, image)


def _text_em(value):
    """Conservative Cambria/Chinese width in em, independent of LaTeX spelling."""
    total = 0.0
    for char in value:
        if unicodedata.combining(char):
            continue
        total += 1.0 if unicodedata.east_asian_width(char) in {"W", "F"} else 0.30 if char.isspace() else 0.60
    return total


def _math_extent(element):
    """Approximate native formula width/height for safe column selection.

    Fractions, radicals, scripts and matrices are measured structurally; raw
    command length is a poor proxy. This errs toward fewer columns and never
    changes font size or mathematical content.
    """
    from lxml import etree

    name = etree.QName(element).localname
    if name.endswith("Pr"):
        return 0.0, 0.0
    if name == "t":
        return _text_em(element.text or ""), 1.0
    children = {etree.QName(child).localname: child for child in element}

    def size(key):
        return _math_extent(children[key]) if key in children else (0.0, 0.0)

    if name == "f":
        numerator, denominator = size("num"), size("den")
        return max(numerator[0], denominator[0]) + 0.4, numerator[1] + denominator[1] + 0.3
    if name == "rad":
        base, degree = size("e"), size("deg")
        return base[0] + 1.0 + degree[0] * 0.35, max(1.3, base[1] + 0.25)
    if name in {"sSup", "sSub", "sSubSup"}:
        base, upper, lower = size("e"), size("sup"), size("sub")
        return base[0] + 0.7 * max(upper[0], lower[0]), base[1] + 0.5 * (upper[1] + lower[1])
    if name in {"acc", "bar", "groupChr"}:
        base = size("e")
        return base[0], base[1] + 0.4
    if name == "d":
        base = size("e")
        return base[0] + 1.0, base[1]
    if name in {"limLow", "limUpp"}:
        base, limit = size("e"), size("lim")
        return max(base[0], limit[0] * 0.75), base[1] + limit[1] * 0.6
    if name == "nary":
        base, upper, lower = size("e"), size("sup"), size("sub")
        return 1.0 + max(upper[0], lower[0]) * 0.65 + base[0], max(base[1], 1.5 + (upper[1] + lower[1]) * 0.6)
    if name in {"m", "eqArr"}:
        rows = [child for child in element if etree.QName(child).localname in {"mr", "e"}]
        metrics = [_math_extent(row) for row in rows]
        return max((metric[0] for metric in metrics), default=1.0), sum(metric[1] + 0.2 for metric in metrics)
    metrics = [_math_extent(child) for child in element]
    return sum(metric[0] for metric in metrics), max((metric[1] for metric in metrics), default=0.0)


def _option_columns(item, options, *, text_width_mm=178):
    values = item["content"].get("options") or {}
    requested = options.get("option_overrides", {}).get(item["id"], options.get("option_layout", "auto"))
    letters = [
        key
        for key in "ABCDE"
        if str(values.get(key) or "").strip() or any(image["slot"] == key for image in item["images"])
    ]
    if len(letters) < 2 or requested == "vertical":
        return 1
    widths = []
    for letter in letters:
        blocks = item["fields"].get(f"options.{letter}", [])
        if re.search(r"[\r\n]", values.get(letter, "")) or (
            blocks and (len(blocks) != 1 or blocks[0]["type"] != "text")
        ):
            return 1
        width = 1.8  # letter, period and breathing room
        for segment in blocks[0]["segments"] if blocks else []:
            if segment.get("display") or segment.get("displayGroup"):
                return 1
            if segment["type"] == "math":
                if segment["omml"] is None:
                    return 1
                extent = _math_extent(segment["omml"])
                if extent[1] > 2.6:
                    return 1
                width += extent[0]
            elif segment["type"] != "delimiter":
                width += _text_em(segment["raw"]) if segment["type"] == "text" else 4.5
        image_width = max(
            (
                image["size"][0] * min(178 / image["size"][0], 210 / image["size"][1], 25.4 / 150)
                for image in item["images"]
                if image["slot"] == letter
            ),
            default=0,
        )
        widths.append(max(width * options["font_size"] * 25.4 / 72, image_width))
    candidates = (
        (4, 2) if requested != "two" and (len(letters) == 4 or (requested == "four" and len(letters) <= 4)) else (2,)
    )
    for columns in candidates:
        # Cell margins and a 10% reserve absorb font/rendering variation.
        if max(widths) <= (text_width_mm / columns - 4.0) * 0.90:
            return columns
    return 1


def _write_compact_options(document, item, size, columns, *, text_width_mm=178):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm

    letters = [
        key
        for key in "ABCDE"
        if f"options.{key}" in item["fields"] or any(image["slot"] == key for image in item["images"])
    ]
    table = document.add_table(rows=(len(letters) + columns - 1) // columns, cols=columns)
    table.autofit = False
    for column in table.columns:
        column.width = Mm(text_width_mm / columns)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + side)
        border.set(qn("w:val"), "nil")
        borders.append(border)
    table._tbl.tblPr.append(borders)
    for index, letter in enumerate(letters):
        cell = table.cell(index // columns, index % columns)
        paragraph = cell.paragraphs[0]
        _font(paragraph.add_run(letter + ". "), size)
        blocks = item["fields"].get(f"options.{letter}", [])
        _write_segments(paragraph, blocks[0]["segments"] if blocks else [], size)
        paragraph.paragraph_format.line_spacing = LINE_SPACING
        _write_images(cell, item, letter)
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))


def _ordered(captured):
    known = {key for key, _ in GROUPS}
    result = [(name, [item for item in captured if item["type"] == key]) for key, name in GROUPS]
    result.append(("其他", [item for item in captured if item["type"] not in known]))
    return [(name, group) for name, group in result if group]


def _section_heading(group, index, name) -> str:
    """组卷上印在每一组题前面的大题标题。

    1.26.25：这一行以前印的是**原卷那一整行原文**（「一、选择题：本题共 12 小题，
    每小题 5 分，共 60 分，在每小题给出的四个选项中…」），而屏幕上的组卷预览印的
    是简简单单的「一、选择题」。老师核对的是屏幕、拿到手的是文件 —— 两边对不上，
    等于没核对过（2026-10-24 老师报的问题）。现在两边都印题型名。

    为什么不再印原卷那一行：它带着**原卷**的题数、分值和题号范围
    （「第 17 题~第 21 题为必考题」）。组卷的卷子题数是老师自己挑的、题号从 1 重排，
    那一行对不上就不该印 —— 印上去学生会以为卷子漏了题。

    教材那种**短章节名**（「习题 3.1」「练习」）照旧印：它说的是这些题出自哪一节，
    对新卷子依然成立。长章节名（教材一整章的大题说明）按卡面上那条规矩算噪音，不印。
    短章节名的判定和卡面、搜索用的是同一条（library.section_label）。
    """
    sections = {str((item.get("content") or {}).get("section") or "").strip() for item in group}
    sections.discard("")
    if len(sections) == 1:
        label = library.section_label(next(iter(sections)))
        if label:
            return label
    names = "一二三四五六"
    prefix = names[index] if index < len(names) else str(index + 1)
    return f"{prefix}、{name}"


def _keep_unit(document, start):
    """Keep a question together when it fits; Word may split an over-page item.

    Cell paragraphs also carry keepNext so a short table and its following
    diagram/options stay with the stem. The final paragraph releases the chain.
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    elements = list(document._element.body)[start:-1]  # last child is sectPr
    paragraphs = []
    for element in elements:
        if element.tag == qn("w:p"):
            paragraphs.append(element)
        elif element.tag == qn("w:tbl"):
            paragraphs.extend(element.findall(".//" + qn("w:p")))
    for index, paragraph in enumerate(paragraphs):
        props = paragraph.get_or_add_pPr()
        for name in ("w:keepNext", "w:keepLines"):
            existing = props.find(qn(name))
            if existing is not None:
                props.remove(existing)
            node = OxmlElement(name)
            node.set(qn("w:val"), "0" if name == "w:keepNext" and index == len(paragraphs) - 1 else "1")
            props.append(node)
    # A final table releases every cell in its final physical row, otherwise a
    # neighbouring cell's keepNext can accidentally chain the following question.
    if elements and elements[-1].tag == qn("w:tbl"):
        rows = elements[-1].findall(qn("w:tr"))
        if rows:
            for paragraph in rows[-1].findall(".//" + qn("w:p")):
                keep = paragraph.get_or_add_pPr().find(qn("w:keepNext"))
                if keep is not None:
                    keep.set(qn("w:val"), "0")


def _paragraph_measure(paragraph, size, width_mm=178):
    import math

    from docx.oxml.ns import qn

    spacing = paragraph.find(qn("w:pPr") + "/" + qn("w:spacing"))
    gap = (
        int(spacing.get(qn("w:after"), str(PARAGRAPH_GAP_PT * 20))) * 25.4 / 1440
        if spacing is not None
        else PARAGRAPH_GAP_PT * 25.4 / 72
    )
    drawings = paragraph.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent")
    if drawings:
        return max(int(node.get("cy", "0")) / 36_000 for node in drawings) + gap
    text = "".join(node.text or "" for node in paragraph.findall(".//" + qn("w:t")))
    metrics = [_math_extent(node) for node in paragraph.findall(".//" + qn("m:oMath"))]
    width = (_text_em(text) + sum(metric[0] for metric in metrics)) * size * 25.4 / 72
    height = max(LINE_SPACING, max((metric[1] for metric in metrics), default=1.0)) * size * 25.4 / 72
    return max(1, math.ceil(width / width_mm)) * height + gap


def _unit_height(elements, size):
    from docx.oxml.ns import qn

    total = 0.0
    for element in elements:
        if element.tag == qn("w:p"):
            total += _paragraph_measure(element, size)
        elif element.tag == qn("w:tbl"):
            columns = max(1, len(element.findall(qn("w:tblGrid") + "/" + qn("w:gridCol"))))
            for row in element.findall(qn("w:tr")):
                total += max(
                    (
                        sum(
                            _paragraph_measure(paragraph, size, 178 / columns - 4)
                            for paragraph in cell.findall(qn("w:p"))
                        )
                        for cell in row.findall(qn("w:tc"))
                    ),
                    default=0,
                )
    return total


def _pagination(document, start, mode, size):
    """Compact flows at paragraph/row boundaries; short keep-mode items bind."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    elements = list(document._element.body)[start:-1]
    if mode == "keep" and _unit_height(elements, size) <= 230:
        _keep_unit(document, start)
        return
    for element in elements:
        paragraphs = [element] if element.tag == qn("w:p") else element.findall(".//" + qn("w:p"))
        for paragraph in paragraphs:
            props = paragraph.get_or_add_pPr()
            plain = "".join(node.text or "" for node in paragraph.findall(".//" + qn("w:t"))).strip()
            atomic = (
                element.tag == qn("w:tbl")
                or bool(paragraph.findall(".//" + qn("w:drawing")))
                or (bool(paragraph.findall(".//" + qn("m:oMath"))) and not plain.strip("▱"))
            )
            for name, value in (("w:keepNext", False), ("w:keepLines", atomic), ("w:widowControl", True)):
                existing = props.find(qn(name))
                if existing is not None:
                    props.remove(existing)
                node = OxmlElement(name)
                node.set(qn("w:val"), "1" if value else "0")
                props.append(node)
    # An isolated number/origin lead must stay with the start of its actual stem.
    # Only a one-line caption stays with the immediately following diagram/table.
    # A normal multi-line stem must remain breakable in compact mode; plain-text
    # length alone misses the width of its native formulas.
    for index, element in enumerate(elements[:-1]):
        if element.tag != qn("w:p"):
            continue
        plain = "".join(node.text or "" for node in element.findall(".//" + qn("w:t"))).strip()
        next_element = elements[index + 1]
        lead_only = bool(re.fullmatch(r"\d+\.(?:（AI 参考 · 未核对）)?\s*", plain))
        next_visual = next_element.tag == qn("w:tbl") or bool(next_element.findall(".//" + qn("w:drawing")))
        formula_width = sum(_math_extent(node)[0] for node in element.findall(".//" + qn("m:oMath")))
        width_mm = (_text_em(plain) + formula_width) * size * 25.4 / 72
        short_caption = bool(plain and len(plain) <= 40 and width_mm <= 178)
        if lead_only or (short_caption and next_visual):
            element.get_or_add_pPr().find(qn("w:keepNext")).set(qn("w:val"), "1")


def _question_gap(document):
    from docx.oxml.ns import qn
    from docx.shared import Pt

    last = list(document._element.body)[-2]
    if last.tag == qn("w:tbl"):
        # Word requires a paragraph after a table; use its invisible line for the
        # same external question gap as a normal paragraph, without resizing text.
        if "QB Question Gap" not in document.styles:
            from docx.enum.style import WD_STYLE_TYPE

            style = document.styles.add_style("QB Question Gap", WD_STYLE_TYPE.PARAGRAPH)
            style.font.size = Pt(1)
            style.paragraph_format.line_spacing = Pt(1)
            style.paragraph_format.space_after = Pt(QUESTION_GAP_PT)
        document.add_paragraph(style="QB Question Gap")
    else:
        from docx.text.paragraph import Paragraph

        paragraph = Paragraph(last, document)
        after = paragraph.paragraph_format.space_after
        if after is None or after < Pt(QUESTION_GAP_PT):
            paragraph.paragraph_format.space_after = Pt(QUESTION_GAP_PT)


def _question_page_break(document, start):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    elements = list(document._element.body)[start:-1]
    first = next((element for element in elements if element.tag == qn("w:p")), None)
    if first is not None:
        first.get_or_add_pPr().append(OxmlElement("w:pageBreakBefore"))


def _document(captured, title, options, mode):
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt

    mode = _effective_document(captured, mode)
    inline_answers = mode == "combined" and options.get("answer_layout", "appendix") == "inline"
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(18)
    section.left_margin = section.right_margin = Mm(16)
    section.header_distance = section.footer_distance = Mm(8)
    size = options["font_size"]
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = "Cambria", Pt(size)
    normal.paragraph_format.line_spacing = LINE_SPACING
    normal.paragraph_format.space_after = Pt(PARAGRAPH_GAP_PT)
    normal.paragraph_format.widow_control = True
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    # Word math defaults, including editable equations, follow the selected size.
    default = document.styles["Default Paragraph Font"]
    default.font.name, default.font.size = "Cambria", Pt(size)
    heading = document.add_paragraph()
    heading.alignment = 1
    heading.paragraph_format.keep_with_next = True
    _font(heading.add_run(title + (" · 答案解析" if mode == "answers" else "")), size + 6, True)
    if options["student_info"] and mode != "answers":
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        info = document.add_paragraph()
        # 预览里这一行是居中的（.print-info 的 text-align: center），PDF 也跟着
        # 那条样式走。Word 这里原来没写对齐，落在左边，于是老师在预览里看着居中、
        # 导出来却贴着左页边 —— 整张卷子上只有这一行对不上，一眼就能看出来。
        info.alignment = WD_ALIGN_PARAGRAPH.CENTER
        info.paragraph_format.keep_with_next = True
        _font(info.add_run("姓名：____________    班级：____________    得分：____________"), size)
    numbered, number = [], 0
    for index, (name, group) in enumerate(_ordered(captured)):
        section_title = None
        if mode != "answers":
            section_title = document.add_paragraph()
            section_title.paragraph_format.keep_with_next = True
            _font(section_title.add_run(_section_heading(group, index, name)), size + 1, True)
        for item in group:
            number += 1
            numbered.append((number, item))
            if mode == "answers":
                continue
            unit_start = len(document._element.body) - 1
            prefix = f"{number}. "
            lead_segments = None
            if "origin" in item["fields"]:
                origin_blocks = item["fields"]["origin"]
                if len(origin_blocks) == 1 and origin_blocks[0]["type"] == "text":
                    lead_segments = [
                        {"type": "text", "raw": "（"},
                        *origin_blocks[0]["segments"],
                        {"type": "text", "raw": "）"},
                    ]
                else:
                    _write_field(document, origin_blocks, size, prefix=prefix + "（题源）", where=item["where"])
                    prefix = ""
            image_body = item["content"].get("body_mode", "text") == "source_image"
            _write_field(
                document,
                [] if image_body else item["fields"]["stem"],
                size,
                prefix=prefix,
                where=item["where"],
                lead_segments=lead_segments,
            )
            # 原图题在预览里就是一张一张竖排（.qb-question-images 是 column 方向），
            # 只有排版文字的题干配图跟着 .qb-figures 的换行走。
            if image_body:
                _write_images(document, item, "body")
            else:
                stem_figures = [image for image in item["images"] if image["slot"] == "stem"]
                # 老师在预览里改过的图宽、整行位置和换行点，跟预览走同一套算法，
                # 而不是在这里再猜一次。预览和 PDF 共用 exam-layout.js 的排版，
                # 只有 Word 是这份代码，所以两边最容易分家 —— 数字先统一到这里。
                figures = _figure_settings(options, item)
                _write_images(
                    document, item, "stem", _figure_row_lines(stem_figures, settings=figures), settings=figures
                )
            columns = _option_columns(item, options) if not image_body else 1
            if columns > 1:
                _write_compact_options(document, item, size, columns)
            else:
                for letter in "" if image_body else "ABCDE":
                    if f"options.{letter}" in item["fields"]:
                        _write_field(
                            document,
                            item["fields"][f"options.{letter}"],
                            size,
                            prefix=f"{letter}. ",
                            where=item["where"],
                        )
                    elif any(image["slot"] == letter for image in item["images"]):
                        _font(document.add_paragraph().add_run(f"{letter}."), size)
                    _write_images(document, item, letter)
            answer_space = options.get("answer_space_overrides", {}).get(item["id"], options["answer_space"])
            if item["type"] == "free_response" and answer_space != "none" and not inline_answers:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.space_after = Mm({"small": 12, "medium": 30, "large": 60}[answer_space])
            _question_gap(document)
            _pagination(document, unit_start, options["pagination"], size)
            if inline_answers and _has_selected_answers([item]):
                # A long solution may cross pages; never keep it with the entire
                # question or add student writing space to a teacher copy.
                _write_solution(document, item, size, "【答案解析】" + ("（AI 参考 · 未核对）" if item["ai"] else ""))
                _question_gap(document)
            if number > 1 and item["id"] in options["question_breaks"]:
                if item is group[0] and section_title is not None:
                    section_title.paragraph_format.page_break_before = True
                else:
                    _question_page_break(document, unit_start)
    if mode != "questions" and not inline_answers:
        if mode == "combined":
            document.add_page_break()
        answer_heading = document.add_paragraph()
        answer_heading.paragraph_format.keep_with_next = True
        _font(answer_heading.add_run("参考答案与解析"), size + 1, True)
        for number, item in numbered:
            unit_start = len(document._element.body) - 1
            label = f"{number}." + ("（AI 参考 · 未核对）" if item["ai"] else "")
            _write_solution(document, item, size, label)
            _question_gap(document)
            # Even keep-whole-question mode must allow a long solution to flow.
            _pagination(document, unit_start, "compact", size)
    footer = section.footer.paragraphs[0]
    footer.alignment = 2
    _font(footer.add_run(f"共 {len(captured)} 题 · 第 "), 9)
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    _font(footer.add_run(" 页"), 9)
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def safe_filename(title):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).rstrip(" .") or "练习"
    if re.match(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", value, re.I):
        value = "练习-" + value
    return value[:100]


def export(payload, *, warnings=None):
    if not isinstance(payload, dict) or set(payload) - {
        "ids",
        "title",
        "print_options",
        "rendered_fields",
        "format",
        "solutions",
    }:
        raise ExportError("导出请求包含不支持的字段")
    try:
        ids = normalize_ids(payload.get("ids"))
        title = _title(payload.get("title", "练习"))
        options = _print_options(payload.get("print_options", {}), {"answer_layout": "appendix"}, ids=ids)
    except (BrowseError, DraftError) as error:
        raise ExportError(str(error)) from None
    if not ids:
        raise ExportError("请先把题目加入题篮")
    _utf16_map(title, "卷名")
    if len(ids) != len(payload["ids"]):
        raise ExportError("选题编号重复，请重新打开组卷")
    output_format = payload.get("format", "docx")
    if not isinstance(output_format, str) or output_format not in {"docx", "split"}:
        raise ExportError("导出格式只能选 docx 或 split")
    captured, use_ai = _capture(
        ids, payload.get("rendered_fields"), options, output_format, solutions=payload.get("solutions")
    )
    name = safe_filename(title)
    # Formulas Word cannot express are rendered once for the whole paper, then
    # reported by position so the teacher can find each one in the download.
    fallback_warnings = _fill_fallback_pictures(captured, options["font_size"])
    if warnings is not None:
        warnings.extend(fallback_warnings)
    if warnings is not None and (options["document"] != "answers" or output_format == "split"):
        numbered = [item for _, group in _ordered(captured) for item in group]
        for number, item in enumerate(numbered, 1):
            requested = options.get("option_overrides", {}).get(item["id"], options.get("option_layout", "auto"))
            wanted = 4 if requested == "four" else 2 if requested == "two" else 1
            actual = _option_columns(item, options)
            if wanted > actual and (
                item["content"].get("options") or any(image["slot"] in "ABCDE" for image in item["images"])
            ):
                warnings.append(
                    f"第 {number} 题选项较宽，Word 已使用{'两列' if actual == 2 else '竖排'}，内容和配图完整保留。"
                )
            # ``_write_question`` prints ``f"{letter}. "`` for every slot in
            # ABCDE whatever the question type is, so pictures the reader filed
            # under option slots make those letters appear on the paper even
            # though the source never had them: 数学九上第 1 题 prints (1)(2)(3)(4)
            # and 括号 under four objects, and its 12 pictures came back slotted
            # A~D, so the student sheet invented A. B. C. D. and lost both the
            # numbering and the place to write the answer.
            if item["type"] not in {"single_choice", "multiple_choice"} and any(
                image["slot"] in "ABCDE" for image in item["images"]
            ):
                warnings.append(
                    f"{item['where']}：这不是选择题，但卷面上会出现 A. B. C. 等字母，"
                    f"原卷印的可能是 (1)(2)(3)(4)。请核对配图分组，或把这道题改成选择题。"
                )
    if output_format == "split":
        questions = _document(captured, title, options, "questions")
        answers = _document(captured, title, options, "answers")
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name + "-题目卷.docx", questions)
            archive.writestr(name + "-答案解析卷.docx", answers)
        data, filename, mime = stream.getvalue(), name + "-分卷.zip", "application/zip"
    else:
        data = _document(captured, title, options, options["document"])
        filename, mime = name + ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    _recheck(captured, options, use_ai)
    return data, filename, mime, len(ids)


@csrf_exempt
def export_docx_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    from .views import _body, _guard

    rejected = _guard(request)
    if rejected:
        return rejected
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return JsonResponse({"error": "导出需从本机题库页面发起"}, status=403)
    try:
        payload = _body(request, limit=MAX_BODY)
        if payload is None:
            raise ExportError(
                "导出内容格式不正确或超过 2 MiB，请减少选题、分批导出", 413 if len(request.body) > MAX_BODY else 400
            )
        warnings = []
        data, filename, mime, count = export(payload, warnings=warnings)
    except RequestDataTooBig:
        return JsonResponse({"error": "导出内容超过 2 MiB，请减少选题、分批导出"}, status=413)
    except ExportError as error:
        return JsonResponse({"error": str(error)}, status=error.status)
    except Exception as error:  # noqa: BLE001 - 意外错也要说人话，不能只剩一张 500 网页
        diagnostic = unexpected_export_error("Word", error)
        return JsonResponse({"error": export_failed_message("Word", diagnostic)}, status=500)
    response = HttpResponse(data, content_type=mime)
    response["Content-Disposition"] = (
        "attachment; filename=practice."
        + ("zip" if mime == "application/zip" else "docx")
        + "; filename*=UTF-8''"
        + quote(filename)
    )
    response["X-Question-Count"] = str(count)
    response["Cache-Control"] = "no-store"
    if warnings:
        response["X-QB-Layout-Warning"] = quote(" ".join(warnings), safe="")
    from .export_preferences import deliver

    return deliver(request, response, data, filename, count=count)
