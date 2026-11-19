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
from .library_drafts import DraftError, _answer_space_millimetres, _print_options, _title
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
# 答案文本的口径必须和预览、PDF 逐字一致。预览走 frontend/library-solutions.js，
# PDF 走 backend/core/library_pdf.py 里的同一份模板，两边本来就对齐；Word 是唯一
# 偏离的一方（标题多一个「 · 答案解析」、占位符少一半、答案没有「答案：」标签、
# inline 多一个「【答案解析】」前缀、AI 标记用的是「·」）。老师对着预览和导出的
# 卷子找不同，一眼就能看出来，所以这里一律跟预览走。
EMPTY_SOLUTION_TEXT = "（原卷未提供答案解析）"
AI_REFERENCE_NOTE = "（AI 参考，未核对）"
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
# LINE_SPACING 是「em 倍数」，跟预览的 CSS line-height 一个意思：1.35em = 1.35 倍字号。
#
# Word 的「倍数行距」不是这个意思 —— 它是「字体自然行高」的倍数（宋体 12pt 约
# 1.29em）。同一个 1.35，浏览器给 1.35 倍字号，Word 给 1.74 倍字号：同一段话，
# Word 每行比预览高 0.39em，实测 1.67 毫米/行（2026-11-17）。答案卷一屏下来能差出
# 小半厘米，纸和屏幕就对不上了。
#
# 所以 Word 这边一律用「至少 N 磅」落行高，不用「倍数」，也不用「固定」——
# 固定行高会把行里的大分数和行内图裁掉，「至少」跟浏览器一样：内容高就把行撑开。
LINE_SPACING = 1.35
PARAGRAPH_GAP_PT = 3
QUESTION_GAP_PT = 8
# 预览打印流里另外几处行高（frontend/library.css、frontend/styles.css）：
#     .qb-table      line-height: 1.6   （表格）
#     .print-section line-height: 1.5   （16px 字号 → 24px 行盒 = 18 磅）
#     .print-title   继承 body 的 1.5  （24px 字号 → 36px 行盒 = 27 磅）
PREVIEW_TABLE_LINE_HEIGHT_EM = 1.6
# .qb-table td, .qb-table th { height: 2.1em; padding: .2em .7em; }
#
# Chrome 实测（2026-11-17，12pt 字号）：单元格 height 33.59px = 2.1em，
# 上下内边距各 3.2px = .2em，行与行之间 33.59px（= 25.2 磅）。
# Word 这边原来只写 1.6em 的行距、上下一点白都不留，每一行比预览矮 5.4 磅
# （实测 19.8 vs 25.2），一张四行的表就差出 7.5 毫米 —— 纸上那几行挤成一团，
# 屏幕上明明是松开的。
PREVIEW_TABLE_CELL_HEIGHT_EM = 2.1
PREVIEW_TABLE_CELL_PADDING_Y_EM = 0.2
PREVIEW_SECTION_LINE_HEIGHT_PT = 18
PREVIEW_TITLE_LINE_HEIGHT_PT = 27
# .print-answer-row 的上下外边距：6px = 4.5 磅（每个答案块上下各一圈）。
PREVIEW_ANSWER_ROW_MARGIN_PT = 4.5
# .solution-paragraph（解析正文）的上下外边距：3pt。
PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT = 3
#
# 上面两个数是**照抄预览**的，但预览和 Word 的**行盒模型**不一样，
# 照抄完还得补一段距离，不然纸上还是对不上。
#
# Chrome（预览，基准）把多出来的行距**一半放上、一半放下** —— 字顶在行盒里
# 居中（实测：标题行盒 18 磅 → 字顶偏移 3.0 磅；答案行盒 16.2 磅 → 1.5 磅）。
# Word 把多出来的空间**全放在基线上方**，字顶几乎贴着行盒顶。
#
# 于是两段挨着时，「上一段的字顶 → 下一段的字顶」这段距离，Word 比预览短。
# 真 Word 实测（Chrome DOM 见 _probe100.py，真 Word 见 _probe103.py）：
#
#     预览  标题字顶 → 答案字顶    24.84 磅
#     Word  同上                    20.46 磅
#     差                            4.38 磅
#
# 这 4.38 磅里有 0.885 磅是**标题自己**的字顶残差（SimHei 在 Chrome 是
# Type3、在 Word 是 CID，ascent 度量不同），补不了；真正要补的是净差
# 3.5 磅。实测：补 3.0 → 答案偏 −0.190 毫米，补 3.5 → 偏 +0.018 毫米，
# 补 4.0 → 偏 +0.233 毫米 —— 3.5 最准。
#
# **只有行盒高度不同的接缝要补**：答案 → 解析这两行盒一样高（都是 16.2 磅），
# 实测只差 0.24 磅，不用补。
WORD_LINE_BOX_LEADING_COMPENSATION_PT = 3.5
#
# 解析正文那一段还要单独补 1.5 磅：预览里解析正文所在的那一行，行盒被行内
# 公式**撑高**了（行盒顶离上一行底 3 磅，字顶却比行盒顶还高 1.52 磅 ——
# Chrome 把撑出来的高度放在了字顶上面）。Word 的公式是行内对象，行盒不会
# 撑成那个形状，实测差 1.5 磅（0.53 毫米）。
PREVIEW_SOLUTION_LINE_BOX_COMPENSATION_PT = 1.5
# 答题留白切成多段时的单段高度上限（twips）。1140 twips = 57 磅 = 20.1 毫米：
# 一段一行，Word 没法在段内断开，只能整段挪到下一页。
#
# 这个上限**不是随便取的**：预览里留白块往下切的时候有一条阈值
# （frontend/exam-layout.js 的 writingSpace：MIN_WRITABLE = 76px = 20.1 毫米）——
# 页底剩下的空间不足 20.1 毫米，就**整块**把留白推到下一页，不留碎片
# （碎片在纸上看不见，却会逼着下一页顶上一行「第 N 题答题区（续）」）。
# Word 的单行段是「放不下就整段走」，阈值就等于段高，所以段高取 20.1 毫米，
# Word 的「整段走」阈值就跟预览的 MIN_WRITABLE 对上了。
ANSWER_SPACE_SLICE_TWIPS = 1140
# 预览续页顶部的「第 N 题答题区（续）」标题（.exam-continuation-label，
# frontend/library.css:951）：font-size 10px、line-height 1.35、color #666、
# margin 0 0 4px。预览正文 16px = 12 磅，所以 1px = 0.75 磅：
#   字号 10px = 7.5 磅；行盒 13.5px = 10.125 磅；下外边距 4px = 3 磅。
# 三样加起来 4.63 毫米 —— 预览里这个续块（10.01 / 12.27 / 34.63 毫米高）
# 就是这么来的，Word 必须一模一样。
PREVIEW_CONTINUATION_LABEL_SIZE_PT = 7.5
PREVIEW_CONTINUATION_LABEL_LINE_PT = 10.125
PREVIEW_CONTINUATION_LABEL_AFTER_PT = 3.0
PREVIEW_CONTINUATION_LABEL_COLOR = "666666"
# .solution-figure（解析配图）的上下外边距：6px = 4.5pt。
PREVIEW_SOLUTION_FIGURE_MARGIN_PT = 4.5
# 字体规格（字号 / 字重 / 字体族）也照抄预览，Word 这边不许自己另定一套：
#     .print-title    { font-size: 24px; font-family: var(--content-font) }
#         → 24px = 18 磅；--content-font 的西文首选是 Times New Roman
#     .print-section  { font-size: 16px; font-family: "SimHei","Microsoft YaHei",sans-serif }
#         → 16px = 12 磅；中文走黑体
#     .qb-number      { font-weight: 700 }    → 题号是粗的
#     .solution-label { font-weight: 600 }    → 答案/解析标签是半粗
#
# 两个标题的字号在预览里是写死的 px，不跟 --exam-font-size 走；Word 这边原来
# 用 size + 6 / size + 1（字号选项一变就跟预览分家：预览 18 磅，Word 19.2 磅），
# 所以改成定值。字重上 Word 只有「粗 / 不粗」两档，半粗的 600 只能落到粗（700）。
PREVIEW_TITLE_FONT_SIZE_PT = 18.0
PREVIEW_SECTION_FONT_SIZE_PT = 12.0
PREVIEW_SECTION_EAST_ASIA = "黑体"
PREVIEW_LATIN_FONT = "Times New Roman"
# .print-title { letter-spacing: .12em } —— 预览的标题是「散开」排的，Word 这边
# 原来一点字距都没有，同一个标题在纸上窄了一大截（实测 167.02 磅 vs 179.28 磅，
# 差 4.3 毫米）。w:spacing 的单位是 1/20 磅，而且 Word 把它量化到 0.5 磅一档
# （实测 25/26/27/28 都落在同一档），30 是最接近预览的那个（181.42 磅）。
PREVIEW_TITLE_LETTER_SPACING = 30
# .print-info（frontend/library.css:929）：居中；margin 12px 0 22px；14px；#333。
# 字体：**卷面字体**（--content-font），不是界面字体。
# 第六十轮在这里写的是「界面字体（Microsoft YaHei UI）」，理由是
# 「.print-info 不在 .qb-question 里，拿不到 --content-font」—— **那条理由是错的**：
# .print-info 挂在 .print-paper / .exam-page-body 底下，这两层都写了
# font-family: var(--content-font)（frontend/library.css:891、892）。
# Chrome 实测（_probe117b.py）：.print-info 的 computed font-family 就是
# "Times New Roman", "Source Han Serif SC", …, "宋体", serif；
# 预览 PDF 里这一行也是「中文 STSong（宋体）+ 下划线 TimesNewRomanPSMT」。
# 写成雅黑的后果：下划线段宽度、整行居中后的起止 x 全跟预览对不上
# （预览 x 167.7→415.0，Word x 173.6→421.2）。
# Word 这边原来写的是「姓名：____________    班级：____________    得分：____________」：
# 多了三个冒号、分隔符是四个半角空格、得分后是 12 个下划线（预览是 8 个），
# 字号也用了正文 12 磅（16px，预览是 14px），颜色纯黑（预览是 #333）。
# 更要命的是上下那 12px / 22px 的外边距没写：预览里姓名行下面才轮到第一题，
# Word 里贴紧标题，整页从这一行往下**整体上移 7.3 毫米**（实测差 7.31）。
PREVIEW_INFO_FONT_SIZE_PT = 10.5
PREVIEW_INFO_COLOR = "333333"
PREVIEW_INFO_EAST_ASIA = "宋体"
PREVIEW_INFO_LATIN = "Times New Roman"
PREVIEW_INFO_SPACE_BEFORE_PT = 9.0  # 12px
PREVIEW_INFO_SPACE_AFTER_PT = 16.5  # 22px
PREVIEW_INFO_LINE_HEIGHT_PT = 15.75  # 21px 行盒（14px × 1.5）
PREVIEW_STUDENT_INFO_TEXT = "姓名 ____________　班级 ____________　得分 ________"

# 预览的页脚（frontend/exam-layout.js:463）：
#     `第 ${index + 1} 页 / 共 ${pages.length} 页${countText ? ' · ' + countText : ''}`
# countText 来自 `.print-footer`（library.js:5986 的 `共 ${number} 题`）。
# 样式 frontend/library.css:901：
#     .exam-page-number { position:absolute; bottom:8mm; left:16mm; right:16mm;
#                         text-align:center; color:#666; font:10px/1.4 "Microsoft YaHei" }
# 真实预览 PDF 实测：字号 7.5 磅、颜色 #666666、字体 MicrosoftYaHei、文字底距页底 8.187mm。
#
# Word 侧原来写的是「共 N 题 · 第 {PAGE} 页」：页码和题数**顺序反了**、
# 分隔符反了、**没有总页数**、字号 9 磅（预览 7.5）、纯黑、右对齐（预览居中）。
PREVIEW_FOOTER_FONT_SIZE_PT = 7.5
PREVIEW_FOOTER_COLOR = "666666"
PREVIEW_FOOTER_EAST_ASIA = "Microsoft YaHei"
PREVIEW_FOOTER_LATIN = "Microsoft YaHei"
PREVIEW_FOOTER_LINE_HEIGHT_PT = 10.5  # 14px 行盒（10px × 1.4）

# 页首那一段外边距（第六十二轮标定）。
#
# 预览的版心是 .exam-page { padding: 18mm 16mm }，页里第一个元素是 .print-section
# （margin: 12px 0 6px）。.exam-page-body 是 flex 容器，外边距不塌陷，
# 所以**页首那 12px 是照算的**：18 + 3.175 = 21.175mm 才是标题行盒的上沿
# （Chrome 实测：标题元素上沿离版心顶正好 3.175mm，见 _probe97.py）。
#
# Word 这边同一段写了 space_before = Pt(9)（就是那 12px），但 **Word 不吃页首的
# 段前距**：硬分页（w:br type=page）、段前分页（w:pageBreakBefore）都不算，
# 连兼容开关 w:suppressSpBfAfterPgBrk=0（顺序放对以后）也实测无效 —— 五种写法
# 真 Word 渲染对照见 _probe97.py / _probe98.py：
#     现状 19.317mm   段前分页 19.741mm   兼容开关 19.317/19.741mm
#     预览 22.180mm（差 2.863mm）
#
# 补法：页首垫一个「固定行距 9 磅」的空行，把那 12px 补回来
# （实测 22.492mm，与预览差 0.312mm —— 那是 SimHei 在 Chrome 与 Word 里
#  ascent 度量不同带来的行内残差，跟页脚那 0.227mm 一个量级）。
PREVIEW_PAGE_TOP_SPACE_PT = 9.0  # 12px
PREVIEW_PAGE_TOP_LINE_TWIPS = 180  # 9 磅 = 180 twips（固定行距）

# .print-section 的 margin-top 12px（= 9 磅）。大题标题、答案区标题的段前距都是它。
PREVIEW_SECTION_SPACE_BEFORE_PT = 9.0

# .print-question { margin: 0 0 8pt }（frontend/library.css:931）——
# 预览里一个题块的下外边距，**写死的是 8 磅**（不是 px）。
#
# 这个数在「Word 取大 vs 预览相加」的模型里是块间距的一半：
# 预览的 .exam-page-body 是 flex 容器，相邻两块的外边距**相加**
# （题块的下外边距 8 磅 + 下一个大题标题的上外边距 9 磅 = 17 磅）；
# Word 是 max(前段段后距, 后段段前距)，只写 9 磅的话那 8 磅会被吃掉。
PREVIEW_QUESTION_MARGIN_BOTTOM_PT = 8.0

# .qb-options { row-gap: .1em }（frontend/styles.css:1642）—— 选项区里
# **行与行之间**的间距，按字号算（.1em = 字号的十分之一）。
#
# Word 的表格行之间没有间距这回事，行间距只能靠段落段后距顶出来
# （Word 表格行高 = 单元格段落行高 + 该段段后距）。所以：
#     单栏（选项写在正文里）：每段段后距 = .1em
#     多栏（选项在表格里）：非末行的段落段后距 = .1em，末行 = 0
#                          （末行给了，表格底就多出来，后面全被顶下去）
PREVIEW_OPTION_ROW_GAP_EM = 0.1

# 大题标题落在**页首**时的垫行高度（不是 9 磅，见下面）。
#
# 预览（Chrome DOM 实测 _probe106.py，纸面真值 _probe105.py）：
#     版心顶 → 大题标题字顶 = 12px（9 磅 margin-top）+ 行内字顶偏移
# Word：
#     版心顶 → 大题标题字顶 = 只剩行内字顶偏移（页首的段前距被吃掉）
#
# 差在两处：
#   1. 页首不吃段前距 → 预览那 9 磅（3.175 毫米）整段丢失；
#   2. SimHei 在 Chrome 是 Type3、在 Word 是 CID，ascent 度量不同，
#      Word 的字顶比 Chrome 低一点点。
#
# 垫行高度是实测标定出来的（真 Word 六档对照 _probe108/_probe109.py）：
#     128 / 134 twips → 21.646mm（残差 −0.386mm）
#     140 / 146 twips → 21.857mm（残差 −0.175mm）
#     150 / 152 twips → 22.069mm（残差 +0.037mm）  ← 预览 22.032mm
#
# Word 把「固定行距」量化到 0.6 磅一档（同一档里写多少都一样），
# 所以取最好那一档的**中间值**：7.8 磅 = 156 twips，落在 [150, 162] 这一档正中，
# 以后字体度量再动一点也不会掉出档。
PREVIEW_SECTION_PAGE_TOP_LINE_TWIPS = 156

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
            # mathml2omml 0.0.2 里 Math 继承的 InferredMRowElement 没有
            # is_space_like，遇到嵌套 <math> 时它会抛 AttributeError。
            # 那是第三方库对输入结构的意见，不是这份卷子的内容坏了 ——
            # 漏掉它就等于让一个公式把整份卷子带走。
            AttributeError,
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
        AttributeError,
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


def _font(run, size, bold=False, east_asia="宋体", latin=None, letter_spacing=None, color=None):
    """把这个 run 的字体规格写成预览里的那一套。

    预览的 --content-font 是 "Times New Roman", …, "SimSun", "宋体", serif：
    西文走 Times New Roman，中文走宋体。Word 这边原来西文写的是 Cambria ——
    数字和字母的宽度跟屏幕上对不上（导出来的卷子里同一个 "1." 比预览窄）。
    ``letter_spacing`` 是字符间距（1/20 磅），对应 CSS 的 letter-spacing。
    ``color`` 是十六进制颜色（不带 #），对应 CSS 的 color。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    if latin is None:
        latin = PREVIEW_LATIN_FONT
    run.font.name = latin
    run.font.size = Pt(size)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), east_asia)
    if color:
        # 交给 python-docx 写 w:color —— 它知道 OOXML 里 w:color 的确切次序
        # （排在 w:spacing 之前），自己插容易插错位置，Word 打开就报文件损坏。
        run.font.color.rgb = RGBColor.from_string(color)
    if letter_spacing:
        node = OxmlElement("w:spacing")
        node.set(qn("w:val"), str(int(letter_spacing)))
        # w:spacing 在 w:rPr 里必须排在 w:sz 前面（OOXML 的次序是硬要求）。
        size_node = rpr.find(qn("w:sz"))
        if size_node is None:
            rpr.append(node)
        else:
            size_node.addprevious(node)
    return run


def _write_prefix(paragraph, prefix, size):
    """写一串「前缀 run」。

    prefix 可以是一整段文字，也可以是 [(文字, 是否加粗), …]。预览里题号是粗的
    （.qb-number { font-weight: 700 }），跟在题号后面的说明文字不是粗的 ——
    一股脑写成一个 run 就只能全粗或全不粗，只能拆开写。
    """
    if isinstance(prefix, str):
        prefix = [(prefix, False)]
    for text, bold in prefix:
        if text:
            _font(paragraph.add_run(text), size, bold=bold)
    return paragraph


def _line_spacing(paragraph, points):
    """把行高（磅）写进 Word 的行距。

    用「至少 N 磅」：行里有大分数或行内图时，预览的行会被撑高，固定行高会把内容裁掉。
    """
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    fmt = paragraph.paragraph_format
    fmt.line_spacing = Pt(points)
    fmt.line_spacing_rule = WD_LINE_SPACING.AT_LEAST
    return paragraph


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


# 预览（frontend/qb-render.js 的 tidyText）把「汉字旁边的空格」删掉：
# 「一周计划生产 700 辆自行车」在屏幕上印成「一周计划生产700辆自行车」。
# Word 这边原来一个字不动地照抄原文，纸上就比屏幕宽一截 —— 真 Word 实测
# 同一行预览 96.0 磅、Word 100.91 磅，其中 2.98 磅就是那个空格。
# 真题库里这样的空格有 1000 多处（题干 673、解析 333、题源 3）。
#
# 这里照预览的规矩来，但只管纯文字：公式、填空线旁边的空格留着 ——
# 预览那边是 .qb-math { margin: 0 .12em } 留的缝，Word 没有这个外边距，
# 那个空格就是缝。删了公式就贴到汉字上了。
def _is_cjk(char):
    """跟预览的 tidyText 用同一套范围：\u3000-\u303f、\uff00-\uffef、\u4e00-\u9fff。"""
    if not char:
        return False
    code = ord(char)
    return 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF or 0x4E00 <= code <= 0x9FFF


def _word_text(raw, before="", after=""):
    """按预览的规矩排一段纯文字。

    预览的 tidyText 先把连续空格并成一个、去掉换行前后的空格，再删掉紧挨
    汉字的空格。Word 原来照抄原文，于是纸上多出一截空格。

    ``before`` / ``after`` 是紧挨着的上一段 / 下一段的**文字**（公式、填空线、
    图片不算文字）。只有两边都是文字时，空格才夹在文字中间、才轮到汉字
    那一侧说话；有一边不是文字时，空格留着。
    """
    if not raw:
        return raw
    value = re.sub(r"[ \t\u00a0]+", " ", raw)
    value = re.sub(r" ?\n ?", "\n", value)
    out = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char != " ":
            out.append(char)
            index += 1
            continue
        end = index
        while end < length and value[end] == " ":
            end += 1
        left = value[index - 1] if index > 0 else before
        right = value[end] if end < length else after
        left_is_text = index > 0 or bool(before)
        right_is_text = end < length or bool(after)
        if left_is_text and right_is_text and (_is_cjk(left) or _is_cjk(right)):
            index = end
            continue
        out.append(value[index:end])
        index = end
    return "".join(out)


def _neighbour_text(segments, index, step):
    """紧挨着的上一段 / 下一段的文字；不是文字（公式、填空线、图片）就空。"""
    position = index + step
    while 0 <= position < len(segments):
        neighbour = segments[position]
        kind = neighbour.get("type")
        if kind == "text":
            raw = neighbour.get("raw") or ""
            if not raw or raw[-1].isspace() or raw[0].isspace():
                return ""
            return raw[-1] if step < 0 else raw[0]
        if kind == "delimiter":
            position += step
            continue
        return ""
    return ""


# pPr 里 w:autoSpaceDN 之后的兄弟（OOXML 规定的顺序）。python-docx 的
# CT_PPr._tag_seq 在类体末尾被 del 掉了，只能自己写一份。
_PPR_TAGS_AFTER_AUTO_SPACE_DN = (
    "w:bidi",
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)


def _disable_punctuation_overflow(document):
    """关掉 Word 的「标点溢出边界」。

    Word 默认允许行末的标点（。，；：等）悬挂到版心右缘之外；Chrome 没有
    这条规矩 —— 放不下的标点会被推到下一行。

    实测同一道题：预览第 1 行末是「驶向乙」，Word 第 1 行末是「驶向乙地。」。
    Word 每行多塞一个字，整行的内容就错开一行，块里的公式和图片跟着一起
    错位；挂出去的标点还会越过版心右缘 194.09 毫米。

    关掉它要写 <w:overflowPunct w:val="0"/>，而且在 pPr 里的位置必须排在
    w:topLinePunct / w:autoSpaceDE 之前 —— 放错位置 Word 直接忽略。
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    successors = ("w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN") + _PPR_TAGS_AFTER_AUTO_SPACE_DN
    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

    def apply(paragraph_element):
        properties = paragraph_element.get_or_add_pPr()
        for old in properties.findall(qn("w:overflowPunct")):
            properties.remove(old)
        properties.insert_element_before(parse_xml(f'<w:overflowPunct {namespace} w:val="0"/>'), *successors)

    for paragraph in document.element.body.iter(qn("w:p")):
        apply(paragraph)
    # 页脚页眉里也有行末标点（「第 1 页，共 2 页」），一样要关。
    for part in document.part.package.iter_parts():
        element = getattr(part, "element", None)
        if element is None or element.tag not in (qn("w:hdr"), qn("w:ftr")):
            continue
        for paragraph in element.iter(qn("w:p")):
            apply(paragraph)


def _disable_cjk_auto_spacing(document):
    """关掉 Word 的「自动调整中文与西文的间距」。

    Word 默认在汉字和西文/数字之间塞 1/4 汉字宽（12 磅时实测 2.98 磅）的
    空隙，预览那边（Chrome）没有这条规矩 —— 实测同一行「答案：答案是42。」，
    预览 96.0 磅，Word 103.91 磅，差的就是这 2.98 磅。

    顺带一提：Word 里就算原文里写了空格，这 2.98 磅的自动间距会让位给空格
    （实测「答案是 42。」和「答案是42。」在 Word 里一样宽），所以只删空格
    是改不动行宽的 —— 得连这条自动间距一起关。

    这两个属性在 pPr 里有固定位置：排在 w:topLinePunct 之后、w:spacing 之前。
    位置放错 Word 直接忽略（曾经就因为这个以为关不掉）。
    """
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn

    # pPr 里这两个属性后面的兄弟（OOXML 规定的顺序）。
    after_dn = _PPR_TAGS_AFTER_AUTO_SPACE_DN
    successors = {"w:autoSpaceDE": ("w:autoSpaceDN",) + after_dn, "w:autoSpaceDN": after_dn}
    namespace = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

    def apply(paragraph_element):
        properties = paragraph_element.get_or_add_pPr()
        for tag, after in successors.items():
            for old in properties.findall(qn(tag)):
                properties.remove(old)
            properties.insert_element_before(parse_xml(f'<{tag} {namespace} w:val="0"/>'), *after)

    for paragraph in document.element.body.iter(qn("w:p")):
        apply(paragraph)
    # 页脚页眉里也有汉字和数字挨着（「第 1 页 共 2 页」），一样要关。
    for part in document.part.package.iter_parts():
        element = getattr(part, "element", None)
        if element is None or element.tag not in (qn("w:hdr"), qn("w:ftr")):
            continue
        for paragraph in element.iter(qn("w:p")):
            apply(paragraph)


def _write_segments(paragraph, segments, size, bold=False):
    from docx.shared import Mm

    for index, segment in enumerate(segments):
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
            if kind == "text":
                # 汉字旁边的空格按预览的规矩删掉（公式、填空线旁边的留着）。
                shown = _word_text(
                    segment["raw"], _neighbour_text(segments, index, -1), _neighbour_text(segments, index, 1)
                )
            _font(paragraph.add_run(shown), size, bold)


# 预览里 .qb-table 的规矩（frontend/styles.css，1690 行）：
#   .qb-table td { min-width: 2.6em; padding: .2em .7em; border: 1px solid }
#   全局 box-sizing: border-box
# 所以一列的**边框盒**宽度 = max(2.6em, 文字宽 + 1.4em 内边距 + 1 像素边框)。
#
# 这三个数是照抄预览的，不是估的：12 磅正文里 2.6em = 11.007 毫米，
# Chrome 实测两列的 | x | y | 表每列正好 11.005 毫米（整表 22.275）。
PREVIEW_TABLE_MIN_COLUMN_EM = 2.6
PREVIEW_TABLE_CELL_PADDING_X_EM = 0.7
PREVIEW_TABLE_BORDER_MM = 25.4 / 96.0  # 1 像素

# 列数达到这个数，预览就给表格挂 ``qb-table-dense``（qb-render.js 的
# ``DENSE_TABLE_COLUMNS = 13``）：最小列宽放开、内边距收成 ``.15em .45em``。
# 导出这边必须跟着换模型 —— 不然十三列的表每列还占 11 毫米，
# 整张表比预览宽 54 毫米（预览实测 111.32 毫米，模型算出来 165.8 毫米）。
PREVIEW_TABLE_DENSE_COLUMNS = 13
PREVIEW_TABLE_DENSE_CELL_PADDING_X_EM = 0.45

# 表格的边框照抄预览：``border: 1px solid var(--paper-ink)``，
# ``--paper-ink: #161816``（frontend/styles.css:17 与 1690）。
# 1 CSS 像素 = 0.75 磅，w:sz 的单位是 1/8 磅 → 6。
PREVIEW_TABLE_BORDER_COLOR = "161816"
PREVIEW_TABLE_BORDER_SZ = 6

# 预览正文字体（Times New Roman）里那些**窄**字符的字宽。
# 全角汉字和全角标点都是 1 个字宽；ASCII 的标点符号多数不到半个字宽，
# 按 0.5 算会高估：「零售收入/万元」实际 6.278 个字宽，
# 按「斜杠 0.5」算成了 6.5，dense 表的表头列因此宽了 0.94 毫米。
# 数值是 Chrome 实测（_probe69.py，在真实 .qb-stem-body 里量 20 个重复字符）。
PREVIEW_NARROW_CHAR_EM = {
    "/": 0.2778,
    "\\": 0.2778,
    "(": 0.3330,
    ")": 0.3330,
    "[": 0.3330,
    "]": 0.3330,
    "{": 0.4800,
    "}": 0.4800,
    "<": 0.5640,
    ">": 0.5640,
    "-": 0.3330,
    "=": 0.5640,
    "+": 0.5640,
    "\u00d7": 0.5640,
    "\u00f7": 0.5488,
    "%": 0.8330,
    "\u00b7": 0.3330,
    "\u201c": 0.4438,
    "\u201d": 0.4438,
    "\u2018": 0.2625,
    "\u2019": 0.2625,
}


def _WIDE_CHAR(ch):
    """中日韩字符和全角标点按一个字宽算，其余按半个字宽。"""
    return "\u2e80" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef"


def _preview_char_em(ch):
    """预览里这个字符占多少 em。

    预览的正文字体是 ``Times New Roman, Source Han Serif SC``：
    汉字 1 个字宽，数字 0.5 个（实测「2023」四个字 31.96 像素，字号 16 像素），
    大写字母约 0.72 个，小写约 0.5 个，空格 0.25 个。
    """
    if _WIDE_CHAR(ch):
        return 1.0
    if ch == " ":
        return 0.25
    narrow = PREVIEW_NARROW_CHAR_EM.get(ch)
    if narrow is not None:
        return narrow
    if ch.isupper():
        return 0.72
    return 0.5


def _field_max_text_width_mm(blocks, size, extra_segments=None):
    """这一段正文「不换行」时最多占多宽（毫米）。

    预览里 ``.qb-stem-body`` 是 flex item，宽度 = 正文内容宽度，但不超过
    题干正文列的可用宽度；正文比表窄时，整个正文列会缩到表那么宽，
    表于是**贴左**（实测离版心左缘 4.66 毫米），而不是在 173 毫米里居中。

    要算准这张表的位置，就得知道正文有多宽。这里按硬换行（源码里的换行符）
    分段，取最宽的那一段 —— 跟浏览器 max-content 的算法一致：软换行的机会
    在 max-content 下不算换行点，只有源码里的真换行才算。
    """
    em = 0.0
    line = 0.0

    def feed(segments):
        nonlocal em, line
        for segment in segments or []:
            if segment.get("type") == "math":
                stripped = re.sub(r"\\[a-zA-Z]+", "ab", segment.get("latex") or "")
                line += max(1.0, len(stripped) * 0.6)
                continue
            parts = (segment.get("raw") or "").split("\n")
            for index, part in enumerate(parts):
                if index:
                    em = max(em, line)
                    line = 0.0
                line += sum(_preview_char_em(ch) for ch in part)
        em = max(em, line)
        line = 0.0

    for block in blocks or []:
        if block.get("type") == "text":
            feed(block.get("segments"))
    if extra_segments:
        feed(extra_segments)
    return em * size * 25.4 / 72.0


def _cell_content_mm(cell, size):
    """这一格里的**文字**有多宽（毫米），不含单元格内边距。

    这是**预览**里的文字宽度：字宽系数是照预览量的（汉字 1 个、数字 0.5、
    大写字母 0.72、小写 0.5、空格 0.25），不含单元格内边距。
    预览里（``styles.css`` 全局 ``box-sizing: border-box``）单元格的内边距是
    ``.7em`` 两侧，最小宽度 ``2.6em`` 是一起算的，所以这里单独留一份。
    """
    em = 0.0
    for segment in cell["segments"]:
        if segment.get("type") == "math":
            stripped = re.sub(r"\\[a-zA-Z]+", "ab", segment.get("latex") or "")
            em += max(1.0, len(stripped) * 0.6)
        else:
            em += sum(_preview_char_em(ch) for ch in segment.get("raw") or "")
    return em * size * 25.4 / 72.0


def _preview_column_widths(placements, width, size):
    """预览里**每一列**的边框盒宽度（毫米）。

    预览的 ``.qb-table`` 是 ``table-layout: auto``，列宽按内容分，
    每格还有 ``min-width: 2.6em`` 和 ``.7em`` 的左右内边距
    （``styles.css`` 全局 ``box-sizing: border-box``，最小宽度含内边距）。

    真实宽度是这么来的（Chrome 实测，12pt / 16 像素字号）：
      · 单元格边框盒 = max(2.6em, 文字宽 + 1.4em 内边距 + 1 像素边框)；
      · 表宽 = 各列边框盒之和 + 左右外边框（各半像素，合起来 1 像素）。

    拿「年份 | 产量（辆） | 备注」这张三列表验：56.92 毫米（实测 56.9185）。
    拿「甲 | 乙」那张两列表验：每列 11.005 毫米、整表 22.275 毫米（实测）。

    列数 >= 13 的表在预览里挂 ``qb-table-dense``（qb-render.js 的
    ``DENSE_TABLE_COLUMNS = 13``）：最小列宽放开、内边距收成 ``.15em .45em``，
    所以那种表的每列 = 文字宽 + 0.9em + 1 像素，**没有 2.6em 下限**。
    实测那张十三列表：表头列 30.646、数字列 6.185、整表 111.319 毫米。

    Word 这边画出来的列距就等于写进去的 ``w:gridCol``（真 Word 16 实测：
    写 8.008 毫米，纸上是 8.052；写 11.005，纸上是 11.016），
    所以这一列宽可以直接当 Word 的列宽用，不需要补偿。
    """
    em_mm = size * 25.4 / 72.0
    need = [0.0] * width
    for _, column, cell in placements:
        span = min(max(1, cell["colspan"]), width - column)
        share = _cell_content_mm(cell, size) / span
        for offset in range(span):
            need[column + offset] = max(need[column + offset], share)
    if width >= PREVIEW_TABLE_DENSE_COLUMNS:
        # dense 表（列数 >= 13）：没有最小列宽，内边距也收窄。
        floor = 0.0
        padding = 2 * PREVIEW_TABLE_DENSE_CELL_PADDING_X_EM * em_mm
    else:
        floor = PREVIEW_TABLE_MIN_COLUMN_EM * em_mm
        padding = 2 * PREVIEW_TABLE_CELL_PADDING_X_EM * em_mm
    return [max(floor, need[index] + padding + PREVIEW_TABLE_BORDER_MM) for index in range(width)]


def _preview_table_width_mm(placements, width, size):
    """这张表在**预览**里有多宽（毫米）。

    表宽决定它在一列里居中时落在哪儿，所以兜底落位必须按预览的宽算，
    不能拿 Word 自己的列宽（那边内边距是 1.9 毫米）来算。
    """
    return sum(_preview_column_widths(placements, width, size)) + PREVIEW_TABLE_BORDER_MM


def _fair_widths(need, budget):
    """版心放不下时，先让每一列都够用，实在放不下的几列再一起挤。

    按比例全体缩小是错的：九上「根据图象，填写下表」那张五列表（性质/二次函数、
    开口方向、对称轴、顶点坐标、取哪些值时……）按预览的列宽模型算，需求合计
    180.2mm，超版心 2.2mm。按比例缩到 0.988 之后，「开口方向」本该 23.12mm
    只剩 22.85mm，扣掉 Word 单元格左右各 1.9mm 只余 19.0mm，而四个字实测
    16.99mm——虽然这一列还塞得下，别的表就会把表头顶成「开口方 / 向」两行。
    同一张表里需求本来就放得下的几列，全被陪着缩窄了。

    这里按水位线分：需求不超过水位的列照需求给，剩下的列一律给水位线，水位由
    「未分到水位的列的需求之和」摊平得出。对上面那张五列表算出来是
    ``[32.65, 23.12, 18.89, 23.12, 80.22]``——需求放得下的四列全都拿到完整需求，
    只有最后那列（需求 82.39mm，本来就超出版心）被挤；按比例缩放则是四列一起缩。

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
    """按预览的列宽模型分列宽（`.qb-table` 是 table-layout:auto，不写死列宽）。

    早先每列都 `Mm(178 / width)`：13 列的表每列只剩 13.7mm，「零售收入/万元」
    七个字被挤成五行，整张表凭空长高一倍——同一份内容在 PDF 路线里是一行。
    真库 27 个表格里 11 个会这样，九上第 1 题（#1282）最典型。

    接着换成了 ``max(8.0, 内容 + 5.0)`` 这套 Word 味的估法：窄表被 8 毫米的
    下限卡住，而预览里同样的表每列是 11.005 毫米（2.6em 下限）——两边每列
    差 3 毫米，纸上一眼就看出表比预览窄。

    现在直接用预览那套（``_preview_column_widths``）：每列
    ``max(2.6em, 文字宽 + 1.4em 内边距 + 1 像素边框)``，Word 画出来的列距
    就等于 ``w:gridCol``，不需要任何补偿。
    """
    need = _preview_column_widths(placements, width, size)
    total = sum(need)
    if total <= text_width_mm:
        # 内容窄就照内容的宽度印，别硬撑满——预览里那张表也是这么收着的。
        return need
    return _fair_widths(need, text_width_mm)


def _set_table_widths(table, widths):
    """把列宽写实：w:tblW（表格总宽）+ 每个单元格的 w:tcW。

    为什么非要写这两样：python-docx 的 ``column.width`` **只**改 ``w:gridCol``，
    每个单元格原有的 ``w:tcW`` 原地不动 —— 两者对不上的时候，Word 不按你写的
    排，而是按自己的规矩重排成等宽。

    实测（Word 16）：三列表，gridCol 写 17.69 毫米、tcW 还留着建表时的
    59.33 毫米（建表时按正文宽平均分的默认值），Word 打开后认成
    50.8×3（等宽，总宽 6 英寸 152.4 毫米），预览里按内容分配的列宽全丢了。

    三变体对照（_probe42.py）：
      只改 gridCol           -> Word 认 50.8×3（152.4 毫米）
      再加 w:tblW            -> 总宽对了，但仍均分 21.68/21.66/21.66
      再把每格 w:tcW 改成一样 -> Word 认 30.0 / 20.0 / 14.99，一字不差

    元素顺序是硬要求：``w:tblW`` 必须排在 ``w:jc`` 前面，``w:tcW`` 必须排在
    ``w:gridSpan`` 前面，否则 Word 打开时会自己重排 pPr/tcPr，那两条顺序
    也就跟着丢了（和 _paragraph_flag 里说的是同一件事）。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def twips(mm):
        return str(int(round(mm * 1440 / 25.4)))

    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblW"))
    if existing is not None:
        tbl_pr.remove(existing)
    tbl_w = OxmlElement("w:tblW")
    tbl_pr.insert_element_before(
        tbl_w,
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
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), twips(sum(widths)))

    # 逐行按网格推进：合并单元格的 tc 跨了若干列（w:gridSpan），
    # 它的宽度是那几列宽度之和；纵向合并（w:vMerge）的行沿用同一列宽。
    for row in table._tbl.tr_lst:
        column = 0
        for cell in row.tc_lst:
            tc_pr = cell.find(qn("w:tcPr"))
            span = 1
            if tc_pr is not None:
                grid_span = tc_pr.find(qn("w:gridSpan"))
                if grid_span is not None:
                    try:
                        span = max(1, int(grid_span.get(qn("w:val"))))
                    except (TypeError, ValueError):
                        span = 1
            value = sum(widths[column : column + span])
            column += span
            if tc_pr is None:
                tc_pr = OxmlElement("w:tcPr")
                cell.insert(0, tc_pr)
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.insert_element_before(
                    tc_w,
                    "w:gridSpan",
                    "w:hMerge",
                    "w:vMerge",
                    "w:tcBorders",
                    "w:shd",
                    "w:noWrap",
                    "w:tcMar",
                    "w:textDirection",
                    "w:tcFitText",
                    "w:vAlign",
                    "w:hideMark",
                )
            tc_w.set(qn("w:type"), "dxa")
            tc_w.set(qn("w:w"), twips(value))


def _mark_repeat_header(trpr):
    """把这一行标成「表头行」：表格跨页时 Word 会把它在每页顶端重复一遍。

    不写 w:tblHeader 的话，续页只剩一串没有列名的数字 —— 跟 PDF 路线
    （exam-layout.js 补表头行）必须是一个效果。
    """
    from docx.oxml import OxmlElement

    header = OxmlElement("w:tblHeader")
    # w:trPr 里的元素有固定顺序，tblHeader 排在 cantSplit / trHeight 之后。
    trpr.insert_element_before(header, "w:tblCellSpacing", "w:jc", "w:hidden", "w:ins", "w:del", "w:trPrChange")


def _cell_vertical_padding(cell, twips_value, horizontal_twips=None):
    """给单元格留出上下白，并把文字垂直居中（照抄 .qb-table td 的 padding）。

    预览里单元格上下各 .2em 的白，文字在这块白里居中；Word 默认上下不留白、
    文字贴顶。写 w:tcMar（上下）+ w:vAlign（居中）才跟预览一样。

    左右内边距（``horizontal_twips``）也写进同一个 w:tcMar：Word 对「只写了
    上下的 tcMar」不一定去继承表级的 w:tblCellMar，左右也写死最保险 ——
    真 Word 实测（_probe76.py）：同一张表里把 w:tblCellMar 的左右从 0 改到
    216 twips，格子里三段探针（居中/右对齐）的位置跟着变，唯独**左对齐那一段
    纹丝不动**（x0 恒为 45.650pt），说明内容盒的左缘是钉在 tblInd 上的，
    边距只决定右边往回收多少。
    """
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    margin = tc_pr.find(qn("w:tcMar"))
    if margin is None:
        margin = OxmlElement("w:tcMar")
        tc_pr.insert_element_before(margin, "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark")
    # 旧的四边先收起来：w:tcMar 里的顺序是 上、左、下、右，重排才合法。
    previous = {}
    for side in list(margin):
        name = side.tag.split("}")[-1]
        if name in ("top", "left", "bottom", "right"):
            previous[name] = side.get(qn("w:w"))
            margin.remove(side)
    values = {"top": twips_value, "bottom": twips_value}
    for name in ("left", "right"):
        values[name] = horizontal_twips if horizontal_twips is not None else previous.get(name)
    for name in ("top", "left", "bottom", "right"):
        if values[name] is None:
            continue
        element = OxmlElement("w:" + name)
        element.set(qn("w:w"), str(values[name]))
        element.set(qn("w:type"), "dxa")
        margin.append(element)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _table_cell_margin_twips(size, dense):
    """预览里单元格的**左右内边距**，换算成 twips。

    预览（frontend/styles.css）：``.qb-table td { padding: .2em .7em }`` →
    左右各 .7em；列数 >= 13 的 dense 表（frontend/library.css 的
    ``.print-flow .qb-table-dense td``）收成 ``.15em .45em`` → 左右各 .45em。

    12 磅正文里（Chrome 实测，_probe72.py）：.7em = 2.963 毫米 = 168 twips，
    .45em = 1.905 毫米 = 108 twips。

    Word 的 Table Grid 默认每格留 108 twips —— 普通表每侧少 60 twips
    （1.058 毫米），格子里的内容盒比预览**宽 2.1 毫米**：11.005 毫米那一列，
    预览的内容盒 4.82 毫米，Word 却有 7.09 毫米，格子里的字折行位置直接对不上。
    """
    em = PREVIEW_TABLE_DENSE_CELL_PADDING_X_EM if dense else PREVIEW_TABLE_CELL_PADDING_X_EM
    return int(round(size * em * 20))


def _set_table_cell_margins(table, horizontal_twips):
    """写 w:tblCellMar：单元格的左右内边距照抄打印预览。

    上下留白是每格的 w:tcMar（.2em，见 _cell_vertical_padding），这里只管左右；
    表级的 w:tblCellMar 是「这张表每格默认留多少白」的声明，写上它，
    没被单独指定过的格子才不会退回 Word 默认的 108 twips。

    w:tblCellMar 在 w:tblPr 里必须排在 w:tblLook 之前，顺序错了 Word 会自己
    重排整块 tblPr，那几个值也就跟着丢了。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblCellMar"))
    if existing is not None:
        tbl_pr.remove(existing)
    margins = OxmlElement("w:tblCellMar")
    # w:tblCellMar 里的顺序是 上、左、下、右。
    for side, value in (("top", 0), ("left", horizontal_twips), ("bottom", 0), ("right", horizontal_twips)):
        node = OxmlElement("w:" + side)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")
        margins.append(node)
    tbl_pr.insert_element_before(margins, "w:tblLook", "w:tblCaption", "w:tblDescription")


def _set_table_borders(table):
    """把表格的边框照抄打印预览：0.75 磅、#161816 的实线。

    预览（frontend/styles.css:1690）：``.qb-table td, .qb-table th
    { border: 1px solid var(--paper-ink) }``，``--paper-ink: #161816``
    （styles.css:17）。Chrome 实测（_probe82.py）每一格四边都是
    ``1px solid rgb(22, 24, 22)``。

    1 CSS 像素 = 1/96 英寸 = 0.75 磅，而 ``w:sz`` 的单位是 1/8 磅 → 6。

    不写的话就走 Table Grid 样式的默认：0.5 磅（sz=4）纯黑 auto —— 细一档
    的黑框，跟预览那张纸不是一回事。

    **纸上的实际线宽是 0.6 磅，不是 0.75**：真 Word 一次排四张表（sz=4/6/8/12），
    导 PDF 量竖边框矩形（_probe84.py）—— sz=4、6、8 在纸上**全都是** 0.600 磅，
    sz=12 才是 1.2 磅。也就是 Word 在 0.25~1 磅之间只给 0.6 磅这一档，
    sz=6 已经是能拿到的最近值（差 0.15 磅 = 0.05 毫米，肉眼看不出来）。

    六边都要写：外框四条（top/left/bottom/right）加内部两条
    （insideH/insideV），少一条就少一道线。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblBorders"))
    if existing is not None:
        tbl_pr.remove(existing)
    borders = OxmlElement("w:tblBorders")
    # w:tblBorders 里的顺序是 上、左、下、右、内部横线、内部竖线。
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = OxmlElement("w:" + side)
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), str(PREVIEW_TABLE_BORDER_SZ))
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), PREVIEW_TABLE_BORDER_COLOR)
        borders.append(node)
    # w:tblBorders 在 w:tblPr 里排在 w:tblInd 之后、w:shd 之前。
    tbl_pr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"
    )


def _set_table_indent(table, left_mm, nested=False, cell_margin_twips=0):
    """把表格的左边缘挪到离版心左缘 ``left_mm`` 毫米的位置。

    ``w:tblInd`` 是表格相对左边距的缩进，``w:jc`` 必须写成 left ——
    jc=center 是「在整栏里居中」，那正是 Word 这边一直排错的地方：
    预览里题干里那张表贴在题号右边（实测离版心左缘 4.66 毫米），
    Word 却把它摆在 178 毫米版心的正中间（差 56.9 毫米）。

    但 ``w:tblInd`` 量到的是**第一格文字的左缘**，不是表格边框的左缘 ——
    边框还要再往左退一个单元格左边距。真 Word 16 实测（_probe76.py：
    八张 40 毫米的表，w:tblCellMar 的左右从 0 一路排到 216 twips，
    每张格里放左对齐/居中/右对齐三段探针，导 PDF 量原始 pt）：

      · 第一格**文字左缘** = 版心左缘 + tblInd + 0.3pt，**与单元格边距无关**
        （tblInd=0 时八张表的左对齐那一段 x0 恒为 45.650pt，一动不动）；
      · 表格**边框中心** = 版心左缘 + tblInd − 单元格左边距；
      · **内容盒宽度** = 列宽 − 2 × 单元格左边距 − 0.3pt。

    所以要让边框落在 ``left_mm``，tblInd 必须写成「目标位置 + 这张表实际写进去的
    单元格左边距」（普通表 168 twips、dense 表 108 twips），而不是拍一个常数 ——
    补错了表就整体偏出去：第一格文字会离开它该在的位置。

    **嵌套在格子里的表是另一条规矩**：真 Word 实测（2026-11-17，_probe57.py，
    同一格里摆 5 张 tblInd = 0/200/400/848/1200 twips 的嵌套表，导 PDF 量边框）：
    缩进 0 时嵌套表的边框正好落在**格子左缘**（53.075 毫米 = B 栏格子左缘），
    缩进 200 twips 时边框落在 56.684（差 3.609 毫米 ≈ 3.528 + 0.08）。
    也就是说嵌套表的 ``w:tblInd`` 直接就是**边框**的位置，没有那 1.905 毫米的
    补偿。补偿加错方向，选项里的表就会偏右 2 毫米（实测 70.026 vs 预览 67.924）。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblInd"))
    if existing is not None:
        tbl_pr.remove(existing)
    indent = OxmlElement("w:tblInd")
    indent.set(qn("w:type"), "dxa")
    # 主文档里的表：tblInd 量到第一格文字的左缘，边框还要再往左退一个单元格
    # 左边距，所以把「这张表实际写进去的那个边距」加回去。
    # 嵌套表：tblInd 就是边框位置，一个 twips 都不用补。
    twips = int(round(float(left_mm) * 1440 / 25.4))
    if not nested:
        twips += cell_margin_twips
    indent.set(qn("w:w"), str(twips))
    # 元素顺序是硬要求：w:tblInd 排在 w:jc 之后、w:tblBorders 之前。
    tbl_pr.insert_element_before(
        indent, "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"
    )


def _set_table_text_indent(table, text_left_mm):
    """把表格里**第一格文字**的左缘挪到离版心左缘 ``text_left_mm`` 毫米。

    ``w:tblInd`` 量到的就是第一格文字的左缘（真 Word 16 实测：写 480 twips
    = 8.4667 毫米，文字就落在离版心左缘 8.4667 毫米处）。这一点与单元格
    左边距无关 —— 默认的 108 twips 和写成 0，量出来都是同一个位置；
    变的只是表格**边框**落在哪儿（边框还要再往左退一个单元格左边距）。

    ``_set_table_indent`` 管的是边框位置（题干里那些表要的就是它）；
    选项块要对齐的是文字，所以这里另写一个。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tbl_pr = table._tbl.tblPr
    existing = tbl_pr.find(qn("w:tblInd"))
    if existing is not None:
        tbl_pr.remove(existing)
    indent = OxmlElement("w:tblInd")
    indent.set(qn("w:type"), "dxa")
    indent.set(qn("w:w"), str(int(round(float(text_left_mm) * 1440 / 25.4))))
    # 元素顺序是硬要求：w:tblInd 排在 w:jc 之后、w:tblBorders 之前。
    tbl_pr.insert_element_before(
        indent, "w:tblBorders", "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"
    )


def _zero_cell_margins(cell):
    """把一个单元格的左右内边距清零。

    预览里选项那一栏两侧都没有留白（``.qb-option`` 没有 padding），
    Word 默认每格留 108 twips（1.905 毫米），不清掉的话正文那一列会比
    预览窄 3.81 毫米 —— 折行位置就不一样了。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()
    existing = tc_pr.find(qn("w:tcMar"))
    if existing is not None:
        tc_pr.remove(existing)
    mar = OxmlElement("w:tcMar")
    for side in ("top", "left", "bottom", "right"):
        node = OxmlElement("w:" + side)
        node.set(qn("w:w"), "0")
        node.set(qn("w:type"), "dxa")
        mar.append(node)
    # w:tcPr 里的顺序也是硬要求：w:tcMar 排在 w:textDirection 之前。
    for tag in ("w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"):
        found = tc_pr.find(qn(tag))
        if found is not None:
            found.addprevious(mar)
            break
    else:
        tc_pr.append(mar)
    return cell


def _write_table(
    container,
    rows,
    size,
    where,
    text_width_mm=None,
    left_mm=None,
    column=None,
    column_shrinks=False,
    field_text_width_mm=None,
):
    """写一张表。

    ``container`` 是正文（Document）或一个 Word 单元格（_Cell）——
    两种都有 ``add_table``，选项里的表要嵌在自己的格子里。

    ``left_mm`` 是预览量出来的表格左边缘位置（离版心左缘多少毫米）；
    ``column`` 是这张表所在那一列的 (左缘, 宽度)，没有实测值时退回到按预览的排版规则算。

    ``column_shrinks`` 说那一列会不会「缩到内容那么宽」：预览里题干正文列是
    flex item，正文比表窄时整列缩到表宽，表于是**贴左**；答案正文列是
    ``minmax(0, 1fr)``，宽度恒定，表永远在列里居中。两条规则不一样，
    搞混了窄表就会差 77 毫米（实测：贴左 4.66 毫米 vs 居中 82.10 毫米）。

    ``field_text_width_mm`` 是这一段正文不换行时的宽度（估算），只在
    ``column_shrinks`` 时用得上：正文列宽 = min(列可用宽, max(正文宽, 表宽))。
    """
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt

    # 嵌套在格子里的表，w:tblInd 的解释跟主文档里的表不一样（见 _set_table_indent）：
    # 嵌套表的 tblInd 直接就是边框位置，没有那 1.905 毫米的单元格边距补偿。
    nested = getattr(container, "_tc", None) is not None

    # 下面两个循环都要用 "column" 这个名字（网格列号、python-docx 的列对象），
    # 会把形参 column 覆盖掉 —— 形参先另存一份，落位时用它。
    column_box = column

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
    table = container.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    table.autofit = False
    # 横向位置：预览量出来的位置说了算，实在量不到才退回到「在这一列里居中」。
    # 什么都不给的时候才居中整栏 —— 那正是 Word 这边一直排错的地方：
    # 预览里题干里那张表贴在题号右边（离版心左缘 4.66 毫米），
    # Word 却把它摆在 178 毫米版心的正中间（差 56.9 毫米）。
    if left_mm is not None or column_box is not None:
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
    else:
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 先合并，再写宽度：合并之后 w:gridSpan 才在，_set_table_widths 才能把
    # 跨列的格子写成「那几列宽度之和」。反过来（先写宽度再合并）的话，
    # 合并格的宽度就轮到 python-docx 自己去加 —— 那是它的内部行为，
    # 我们既没写、也没测，Word 打开后是什么样全看它心情。
    for r, c, cell in placements:
        if cell["rowspan"] > 1 or cell["colspan"] > 1:
            table.cell(r, c).merge(table.cell(r + cell["rowspan"] - 1, c + cell["colspan"] - 1))
    widths = _table_column_widths(placements, width, size, 178.0 if text_width_mm is None else text_width_mm)
    for column, value in zip(table.columns, widths):
        column.width = Mm(value)
    # 光写 w:gridCol 不够，Word 会按自己的规矩把表重排成等宽。
    _set_table_widths(table, widths)
    # 单元格的左右内边距照抄预览：普通表 .7em、dense 表 .45em。
    # Word 默认 108 twips，普通表每侧少 60 twips，内容盒比预览宽 2.1 毫米 ——
    # 格子里的字折行位置就跟着错（真 Word 实测：11.005 毫米那一列，
    # 预览内容盒 4.82 毫米，Word 7.09 毫米）。
    cell_margin_twips = _table_cell_margin_twips(size, width >= PREVIEW_TABLE_DENSE_COLUMNS)
    _set_table_cell_margins(table, cell_margin_twips)
    # 边框照抄预览：0.75 磅、#161816 的实线（不是 Table Grid 的 0.5 磅纯黑）。
    _set_table_borders(table)
    # 表格的左边缘该落在哪儿：优先用预览量出来的实测位置；
    # 量不到（没渲染过预览、或题被拆到跨页续片里）就按预览的排版规则算。
    #
    # 预览里表是在**自己的那一列**里居中（.qb-table { margin: 0 auto }），
    # 但「那一列」有多宽，两种情况不一样：
    #   - 题干正文列是 flex item（.qb-stem-body），宽度 = min(列可用宽, max(正文, 表)）。
    #     正文比表窄时整列缩到表宽 → 表**贴左**（实测离版心左缘 4.66 毫米）；
    #     正文撑满时列宽 173.34 毫米 → 表居中（实测 80.19 毫米）。
    #   - 答案正文列是 grid 的 minmax(0, 1fr)，宽度恒定 166.78 毫米 → 表永远居中
    #     （实测 83.47 毫米，答案长短不影响）。
    # 两种规则差 77 毫米，不能混。
    offset = left_mm
    if offset is None and column_box is not None:
        column_left, column_width = column_box
        preview_width = _preview_table_width_mm(placements, width, size)
        if column_shrinks:
            body_width = min(column_width, max(field_text_width_mm or 0.0, preview_width))
        else:
            body_width = column_width
        offset = column_left + max(0.0, (body_width - preview_width) / 2)
    if offset is not None:
        _set_table_indent(table, offset, nested=nested, cell_margin_twips=cell_margin_twips)
    for r, c, cell in placements:
        target = table.cell(r, c)
        target.text = ""
        # 预览里 th 和 td 是同一条规则（font-weight: 400），表头**不加粗**。
        _write_segments(target.paragraphs[0], cell["segments"], size, False)
        # 预览里表格是 .qb-table { line-height: 1.6 }，单元格没有外边距。
        # Word 这边跟着走：行高用 1.6em，段后距清零（Normal 样式默认留 3 磅，
        # 不清的话每一行都白胖 3 磅）。
        cell_paragraph = target.paragraphs[0]
        _line_spacing(cell_paragraph, size * PREVIEW_TABLE_LINE_HEIGHT_EM)
        cell_paragraph.paragraph_format.space_after = Pt(0)
        # 预览里格子里的字是**居中**的：``.qb-table td, .qb-table th
        # { text-align: center }``（frontend/styles.css:1690）。Chrome 实测
        # （_probe80.py）：表头格和数据格都居中，dense 表也一样。
        #
        # Word 这边默认左对齐 —— 窄表那一列的内容盒只有 4.97 毫米，
        # 居中时左边空 1.30 毫米，左对齐只空 0.13 毫米，纸上差 1.17 毫米。
        #
        # 注意这跟**表格整体**的位置是两回事：w:tblPr/w:jc 管的是表格框在页面
        # 上的位置（题干表靠左、答案表在正文列里居中），w:pPr/w:jc 管的才是
        # 格子里的文字。
        cell_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        # 预览里表头**没有底色**：``.qb-table td, .qb-table th`` 只写边框和
        # 内边距，底色是透明的（Chrome 实测 rgba(0, 0, 0, 0)，_probe82.py）。
        # Word 这边原来给表头涂了 F1F5F3 浅绿底 —— 导出后表头一块绿，跟预览
        # 那张白纸完全不是一回事，所以这里不涂。
        # 预览里表头**没有底色**：``.qb-table td, .qb-table th`` 只写边框和
        # 内边距，底色是透明的（Chrome 实测 rgba(0, 0, 0, 0)，_probe82.py）。
        # Word 这边原来给表头涂了 F1F5F3 浅绿底 —— 导出后表头一块绿，跟预览
        # 那张白纸完全不是一回事，所以这里不涂。
    # 首行整行都是表头格时，把它标成「表头行」：表格跨页时 Word 会把这一行
    # 在每一页的顶端重复一遍。
    head_row = bool(rows) and all(cell["header"] for cell in rows[0])
    # 预览里每一行都是 2.1em 高、上下各留 .2em 的白（Chrome 实测 25.2 磅/行）。
    # Word 这边照抄：文字垂直居中，上下边距 .2em。
    #
    # 行高那个数要减掉边距：Word 的 w:trHeight 量的是「内容区」最低高度，单元格
    # 边距算在它外面（实测：trHeight 25.2 磅 + 上下 2.4 磅 = 行高 30.6 磅）；
    # Chrome 的 height 2.1em 是「整行」的高度（实测 25.5 磅）。所以这里写
    # 2.1em − 2×.2em = 1.7em，行高才跟预览一样是 25.2 磅。
    row_height = int(round(size * (PREVIEW_TABLE_CELL_HEIGHT_EM - 2 * PREVIEW_TABLE_CELL_PADDING_Y_EM) * 20))
    padding_y = int(round(size * PREVIEW_TABLE_CELL_PADDING_Y_EM * 20))
    for index, row in enumerate(table.rows):
        trpr = row._tr.get_or_add_trPr()
        avoid = OxmlElement("w:cantSplit")
        trpr.append(avoid)
        height = OxmlElement("w:trHeight")
        height.set(qn("w:hRule"), "atLeast")
        height.set(qn("w:val"), str(row_height))
        # w:trPr 里 trHeight 排在 cantSplit 之后、tblHeader 之前。
        trpr.insert_element_before(
            height, "w:tblHeader", "w:tblCellSpacing", "w:jc", "w:hidden", "w:ins", "w:del", "w:trPrChange"
        )
        for cell in row.cells:
            _cell_vertical_padding(cell, padding_y, cell_margin_twips)
        if index == 0 and head_row:
            _mark_repeat_header(trpr)


def _write_field(
    container,
    blocks,
    size,
    prefix="",
    where="",
    lead_segments=None,
    indent_mm=None,
    hanging=False,
    text_width_mm=None,
    space_before_pt=None,
    space_after_pt=None,
    table_lefts=None,
    table_column=None,
    table_column_shrinks=False,
    first_paragraph=None,
    first_paragraph_tab_mm=None,
    first_paragraph_keep=False,
    right_indent_mm=None,
    list_indent_mm=None,
):
    """Write one field (题干预览里的一个字段块) into the document.

    ``container`` 是正文（Document）或一个 Word 单元格（_Cell）——
    两种都有 ``add_paragraph``。选项里的内容要排进选项格子里，
    用的就是这一套（多栏排版时一个选项可能不止一段、还可能带一张表）。

    ``first_paragraph`` 是已经建好的第一个段落：调用方先把「A. 」这样的题号
    写进去了，正文要接着往下写，不能另起一段（预览里题号和选项正文在同一格里）。

    ``first_paragraph_tab_mm`` 是「标签 → 正文」的距离（毫米）：复用那个段落时，
    先插一个 tab 把正文推到标签右边，并把段落设成悬挂缩进 —— 折行时折回正文
    那一列，跟预览里 ``.qb-option`` 的两列网格一致。

    ``right_indent_mm`` 把这一段的右边让出来（毫米）：Word 的表格没有列间距，
    预览里栏与栏之间那 2em 的间隙就是这么让出来的。

    ``indent_mm`` 把这一段挪到答案正文列里（预览里那一列缩在题号右边）；
    ``hanging`` 让首行顶格放题号、后续行缩进，跟预览的两栏网格一致；
    ``text_width_mm`` 是这一列的净宽，里面的表格不能比它宽；
    ``space_before_pt`` / ``space_after_pt`` 覆盖预览里那一段的外边距
    （.solution-result 没有外边距、.solution-paragraph 上下各 3pt）。
    ``table_lefts`` 是预览量出来的每张表的位置（离版心左缘多少毫米），
    ``table_column`` 是这些表所在那一列的 (左缘, 宽度) —— 量不到时按这一列居中。
    ``table_column_shrinks`` 说这一列会不会缩到内容那么宽（题干正文列会，
    答案正文列不会）。

    ``list_indent_mm`` 是这一段所属**列表**的左缩进（毫米）—— 预览里
    ``.qb-options`` 那个 2em 的 padding。多栏路径里选项被塞进表格格子，
    格子的左缘已经是栏左缘，所以不用给（None）；单栏路径里选项直接写在
    正文里，就要把这 2em 补上，否则整块选项比预览靠左 8.47 毫米
    （第五十五轮实测：Word 顶格 0.11 毫米，预览 13.85 毫米）。
    """
    from docx.shared import Mm, Pt

    written = []

    # 一张表对一个位置。数量对不上就整份丢掉：一张表配错位置，
    # 表就摆在错的地方 —— 比位置差一点更糟。
    tables = [block for block in blocks if block["type"] == "table"]
    offsets = list(table_lefts) if table_lefts is not None and len(table_lefts) == len(tables) else None
    table_index = 0
    # 这一段正文不换行时有多宽（估算）。题干正文列是 flex item，正文比表窄时
    # 整列缩到表宽，表于是贴左 —— 要算准位置就得知道正文有多宽。
    field_text_width = _field_max_text_width_mm(blocks, size, extra_segments=lead_segments)

    # 调用方先写好的那个段落（选项里的「A.」）只有在这一段是**普通行内文字**时
    # 才能接着写：
    #   · 行间公式要自己起一段 —— 预览里公式另起一行，单栏排版也是另起一段；
    #     抢来写会让多栏和单栏排出来的样子不一样。
    #   · 那个段落在格子里物理上排在最前面，抢来写后面的内容会打乱顺序。
    # 第一块是表的时候同理：表是独立的一块，题号自己占一段（单栏排版就是这样）。
    if first_paragraph is not None:
        head = blocks[0] if blocks else None
        reusable = False
        if head is not None and head.get("type") == "text":
            segments = head.get("segments") or []
            if segments:
                first_segment = segments[0]
                group = first_segment.get("displayGroup") or ("display" if first_segment.get("display") else None)
                reusable = group is None
        if not reusable:
            # ``first_paragraph_keep`` 是题干用的：那个段落里已经写着题号，
            # 丢不得。题干只有三种情况会走到这里：
            #   · 没有正文块（题干是空的，或者题干在题源那一段里就写完了）——
            #     题号段落自己留着，「6.」独占一行；
            #   · 正文第一块是表 —— 题号和题源在正文列的第一行，表是独立的一块，
            #     自己另起。题号不该因为后面是表就自己独占一行。
            #   · 题源在题干前面（lead_segments）—— 「（题源）」是行内文字，
            #     跟在题号同一行（预览里它在正文列的第一行），后面的行间公式
            #     自己另起就是了。
            # 选项那边（keep=False）保持原样：行间公式开头时标签自己占一段，
            # 免得多栏排出来的样子跟单栏不一样。
            keep = first_paragraph_keep and (head is None or head.get("type") == "table" or bool(lead_segments))
            if not keep:
                first_paragraph = None

    # 标签和正文之间留出预览里那 0.3em：正文从悬挂缩进的位置开始，
    # 折行也折回这一列。tab stop 必须自己写 —— Word 默认的制表位是
    # 12.7 毫米，我们要的是 5.4 毫米。
    if first_paragraph is not None:
        fmt = first_paragraph.paragraph_format
        if first_paragraph_tab_mm:
            # 悬挂缩进的第一行落在**标签**的位置上（预览的标签列），
            # 正文落在标签 + 间隙那一列。列表自己的缩进（预览 .qb-options 的
            # padding-left）加在左边，两边一起才是正文列的左缘。
            base = float(list_indent_mm or 0.0)
            fmt.left_indent = Mm(base + first_paragraph_tab_mm)
            fmt.first_line_indent = Mm(-first_paragraph_tab_mm)
            fmt.tab_stops.add_tab_stop(Mm(base + first_paragraph_tab_mm))
            _font(first_paragraph.add_run("\t"), size)
        # 右缩进和悬挂缩进是两回事：栏间间隙要一直让出来，
        # 不能因为这一格没有 tab（标签栏没复用）就跟着丢掉。
        if right_indent_mm:
            fmt.right_indent = Mm(right_indent_mm)

    def _new_paragraph():
        nonlocal first_paragraph
        if first_paragraph is not None:
            paragraph = first_paragraph
            first_paragraph = None
        else:
            paragraph = container.add_paragraph()
        fmt = paragraph.paragraph_format
        if indent_mm:
            fmt.left_indent = Mm(indent_mm)
            if hanging:
                fmt.first_line_indent = Mm(-indent_mm)
                fmt.tab_stops.add_tab_stop(Mm(indent_mm))
        if right_indent_mm:
            fmt.right_indent = Mm(right_indent_mm)
        written.append(paragraph)
        return paragraph

    paragraph = None
    if prefix:
        paragraph = _new_paragraph()
        _write_prefix(paragraph, prefix, size)
    if lead_segments:
        paragraph = paragraph or _new_paragraph()
        _write_segments(paragraph, lead_segments, size)
    for block in blocks:
        if block["type"] == "table":
            left = offsets[table_index] if offsets is not None else None
            _write_table(
                container,
                block["rows"],
                size,
                where,
                text_width_mm=text_width_mm,
                left_mm=left,
                column=table_column,
                column_shrinks=table_column_shrinks,
                field_text_width_mm=field_text_width,
            )
            table_index += 1
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
                        paragraph = paragraph or _new_paragraph()
                        if group is not None:
                            paragraph.alignment = 1
                        _write_segments(paragraph, [{**segment, "raw": part} if part is not None else segment], size)
                        paragraph.paragraph_format.space_after = Pt(PARAGRAPH_GAP_PT)
                        _line_spacing(paragraph, size * LINE_SPACING)
                    if index < len(parts) - 1:
                        paragraph = None
                display_group = group
            paragraph = None
    if written:
        if space_before_pt is not None:
            written[0].paragraph_format.space_before = Pt(space_before_pt)
        if space_after_pt is not None:
            for item in written:
                item.paragraph_format.space_after = Pt(space_after_pt)
    return written


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
# 预览里答案块是「题号列 + 正文列」两栏网格：
#     .print-answer-row { display: grid; grid-template-columns: 2.4em minmax(0, 1fr); gap: 4px; }
# 老师核对的是屏幕：题干顶格，答案正文和解析配图缩在题号右边（实测缩进 11.22mm）。
# Word 当年把「1. 答案：……」整句顶格写，解析配图也从版心左缘开始 —— 纸上和屏幕上
# 对不上（2026-11-17 实测：预览缩进 11.22mm，Word 0mm；解析图预览宽 166.78mm，
# Word 168mm）。
#
# 下面两个常量把那条两栏网格抄过来：题号列宽 2.4em，列间隙 4 个 CSS 像素。
# em 相对答案行的字号（12pt = 16px），所以缩进随字号变，跟预览一致。
PREVIEW_ANSWER_NUMBER_COLUMN_EM = 2.4
PREVIEW_ANSWER_COLUMN_GAP_PX = 4
PREVIEW_PX_TO_MM = 25.4 / 96
# 题干正文列的左缘：预览里题干也是「题号列 + 正文列」两栏
# （.qb-stem { display: flex; gap: .35em }），正文列紧跟在题号列右边。
# 题号列多宽取决于题号有几位：Times New Roman 粗体下数字宽 0.5em、句点 0.25em，
# 再加 0.35em 的列间隙 —— 1 位数时正文列左缘 = 1.1em
# （实测 17.59 像素 @12pt，与 1.1em 一字不差）。
PREVIEW_NUMBER_DIGIT_EM = 0.5
PREVIEW_NUMBER_DOT_EM = 0.25
PREVIEW_STEM_COLUMN_GAP_EM = 0.35
# Word 的 Table Grid 样式给每个单元格留 108 twips（1.905 毫米）的左右内边距，
# 而 ``w:tblInd`` 量的是**第一格文字的左缘**，不是表格边框的左缘 ——
# 边框还要再往左退这一个单元格左边距。实测（Word 16.0 转 PDF）：
# 题干表预览在离版心左缘 4.66 毫米处，写 tblInd=264（4.66 毫米）后
# Word 的边框却落在 2.65 毫米处，差的正是这 1.905 毫米；答案表同样差 1.905。
# 写 tblInd 时必须把它加回去，否则「看着贴左了，其实还差 2 毫米」。
#
# 但补多少要看这张表**实际写进去**的单元格左边距：第五十七轮起，Word 这边
# 不再用 Word 默认的 108，而是照抄预览（普通表 .7em = 168 twips、dense 表
# .45em = 108 twips，见 _table_cell_margin_twips），所以补偿也跟着变 ——
# 补的是那张表的边距，不是某个写死的常数。
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


def _answer_block_indent_mm(size):
    """答案正文列相对版心左边缘的缩进（毫米）。

    与 ``.print-answer-row`` 的两栏网格逐字对应：2.4em 的题号列 + 4px 的列间隙。
    字号是磅，1pt = 96/72 个 CSS 像素。
    """
    font_px = size * 96 / 72
    return (PREVIEW_ANSWER_NUMBER_COLUMN_EM * font_px + PREVIEW_ANSWER_COLUMN_GAP_PX) * PREVIEW_PX_TO_MM


def _answer_block_text_width_mm(size):
    """答案正文列的净宽（毫米）：版心宽度减去题号列和列间隙。"""
    return PREVIEW_TEXT_WIDTH_MM - _answer_block_indent_mm(size)


def _stem_column_left_mm(size, digits):
    """题干正文列相对版心左边缘的缩进（毫米）。

    与 ``.qb-stem`` 的两栏结构逐字对应：题号列的宽度随题号位数变，
    再加 0.35em 的列间隙，就是正文列的左缘。
    """
    font_mm = size * 25.4 / 72
    return (PREVIEW_NUMBER_DIGIT_EM * digits + PREVIEW_NUMBER_DOT_EM + PREVIEW_STEM_COLUMN_GAP_EM) * font_mm


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
    default_align = (
        options.get("figure_align") if options.get("figure_align") in ("left", "center", "right") else "right"
    )
    if not isinstance(settings, dict):
        return {"width": {}, "breaks": [], "align": default_align}
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
    # 预览量出来的每张图在纸面上的实际宽度（毫米）：位置 → 宽度列表。
    # 选项里的图在屏幕上会被选项栏宽收口，Word 自己量不到那个栏宽 ——
    # 没有这份实测值，Word 就照 150dpi 自然尺寸排，纸上比屏幕上大了一圈。
    measured = (options.get("measured_figure_widths") or {}).get(item["id"]) or {}
    measured = {
        slot: [
            float(value)
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
        ]
        for slot, values in measured.items()
        if isinstance(values, list)
    }
    return {
        "width": widths,
        "breaks": breaks,
        "align": align if align in ("left", "center", "right") else default_align,
        "measured": measured,
    }


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


def _figure_row_lines(images, text_width_mm=PREVIEW_TEXT_WIDTH_MM, settings=None, widths=None):
    """How many figures the preview puts on each line, e.g. ``[2, 1]``.

    The line lengths are kept, not just the widest one. The preview centres
    *every* line on its own (``justify-content: center`` applies per flex line),
    and one uniform Word grid cannot do that: a 3+2+1 set comes out right, but
    2+1 puts the lone third picture in the first column, hard against the left
    of a table that is itself centred. Three questions in the bank wrap that way,
    so each line gets its own table.

    ``widths`` passes in the widths already resolved (teacher's millimetres or
    the preview's measured ones) so the packing matches what the screen did.
    """
    resolved = list(widths) if widths is not None else _figure_widths(images, text_width_mm, settings)
    return _pack_lines(resolved, text_width_mm, (settings or {}).get("breaks"))


def _figure_widths_for_slot(pictures, slot, settings=None, measured=None):
    """这个位置上的每张图，纸面上最终有多宽（毫米）。

    两层：先是老师自己在预览里改的宽度（只对题干配图有意义，``settings``
    里那个索引空间是题干配图的序号），再是预览量出来的实测宽度 ——
    选项里的图在屏幕上被选项栏宽收口过，Word 自己量不到那个栏宽，
    不按实测值收口，纸上就比屏幕上大一圈。
    """
    widths = _figure_widths(pictures, PREVIEW_TEXT_WIDTH_MM, settings if slot != "body" else None)
    measured = list(measured or [])
    if not measured or slot == "body":
        return widths
    return [min(width, measured[index]) if index < len(measured) else width for index, width in enumerate(widths)]


# ---- 选项块的水平排版规矩（照抄打印预览）--------------------------
#
# 预览里选项是两层 grid：``.qb-options`` 排栏，``.qb-option`` 排标签和正文。
#   · ``.qb-options`` 的 padding-left 是 2em —— 选项区整体离版心左缘 2em；
#   · 栏与栏之间 column-gap 是 2em；
#   · 标签（「A.」）和正文之间 column-gap 是 .3em。
# 预览的字号是写死的 16px，也就是 12 磅，所以 1em = 4.2333 毫米。
#
# 以上三个数都是 Chrome 实测（2026-11-17，_probe53.py）：
#   选项区左缘 8.47 毫米；栏 1 左缘 8.47 宽 80.53；栏 2 左缘 97.47。
#
# Word 这边原来一张表平均分栏（每栏 89 毫米），既没有那 2em 缩进，也没有栏间
# 2em 间隙 —— 纸上的选项整体比预览靠左 8.47 毫米，第二栏还整整偏左 8.47 毫米。
# 现在照抄预览的规矩：左边缩进用 tblInd，栏间间隙算进左栏宽度 +
# 段落右缩进（Word 的表格没有列间距这一说）。
PREVIEW_EM_MM = 12 * 25.4 / 72.0  # 16px = 12 磅 = 4.2333 毫米
OPTION_LIST_INDENT_MM = 2 * PREVIEW_EM_MM  # 8.4667
OPTION_COLUMN_GAP_MM = 2 * PREVIEW_EM_MM  # 8.4667
OPTION_LABEL_GAP_MM = 0.3 * PREVIEW_EM_MM  # 1.27
# Times New Roman 的大写字母宽度（em）。预览的西文首选就是这个字体：
# Chrome 实测「A.」4.11 毫米、「B.」3.88 毫米，与 (advance + 0.25em) 逐个对得上。
OPTION_LABEL_EM = {"A": 0.722, "B": 0.667, "C": 0.667, "D": 0.722, "E": 0.611}
OPTION_PERIOD_EM = 0.25


def _option_column_width_mm(columns, text_width_mm=PREVIEW_TEXT_WIDTH_MM):
    """选项表里一栏有多宽（毫米）—— 照抄预览的 ``.qb-options``。

    预览里选项区左边留 2em，栏与栏之间还有 2em 的间隙，所以一栏比
    「版心平均分」要窄：两栏时 80.53 毫米（平均分是 89）。
    """
    columns = max(1, int(columns or 1))
    gaps = (columns - 1) * OPTION_COLUMN_GAP_MM
    return (text_width_mm - OPTION_LIST_INDENT_MM - gaps) / columns


def _option_label_width_mm(letter, size=12):
    """标签（「A.」）有多宽（毫米）。"""
    em = size * 25.4 / 72.0
    return (OPTION_LABEL_EM.get(letter, 0.722) + OPTION_PERIOD_EM) * em


def _option_label_indent_mm(letter, size=12):
    """标签加标签与正文之间的间隙 —— 正文从这儿开始（毫米）。

    Chrome 实测：正文左缘离栏左缘 5.386 毫米（A）/ 5.151 毫米（B），
    与这里的 5.3848 / 5.1520 逐个对得上。
    """
    return _option_label_width_mm(letter, size) + OPTION_LABEL_GAP_MM


def _option_cell_usable_mm(columns, text_width_mm=PREVIEW_TEXT_WIDTH_MM, size=12, letter="A"):
    """选项表格里，一格最多能放多宽的东西（毫米）。

    Word 不会自动缩图：图比格子宽，就直接压到隔壁列上。
    预览里选项里的图排在正文那一列（``.qb-option .qb-figure { grid-column: 2 }``），
    所以上限就是正文列的宽：栏宽减去标签和它后面的间隙。

    ``letter`` 是这一格是哪个选项：预览里标签列的宽是 ``auto``，各行自己算 ——
    「A.」比「B.」宽，所以 A 那一行的正文列就比 B 窄（Chrome 实测 2026-11-17：
    4 栏时 A 的正文列 30.646 毫米、B 的 30.882 毫米）。一律按最宽的 A 算，
    B、C、D 的图就会被多裁掉两三毫米。
    """
    return _option_column_width_mm(columns, text_width_mm) - _option_label_indent_mm(letter, size)


def _write_images(
    document,
    item,
    slot,
    lines=None,
    settings=None,
    measured=None,
    max_width_mm=None,
    indent_mm=None,
    align_override=None,
):
    """写这个位置上的图。

    ``indent_mm`` 是图所在段落的左缩进（毫米）。选项里的图在预览里被钉在
    **正文列**（``.qb-option .qb-figure { grid-column: 2 }``），所以要往右挪出
    「标签宽 + 标签与正文之间的间隙」，让图落在正文列的左缘上。

    ``align_override`` 强制对齐方式。题干配图默认靠右（``.qb-figures`` 是
    ``justify-content: flex-end``），但选项里的图是正文列里的一个网格格子，
    贴着列的**左**缘 —— 沿用「靠右」会把图推到整格的右边，偏 8.46 毫米。
    """
    from docx.shared import Mm

    pictures = [image for image in item["images"] if image["slot"] == slot]
    if not pictures:
        return
    align = align_override or (settings or {}).get("align") or "right"
    if align not in ("left", "center", "right"):
        align = "right"
    widths = _figure_widths_for_slot(pictures, slot, settings, measured)
    # 格子有上限：图比格子宽就压到隔壁列上（选项表就是这种情况）。
    if max_width_mm is not None:
        widths = [min(width, max_width_mm) for width in widths]
    if slot != "body" and lines is None:
        lines = _figure_row_lines(
            pictures,
            text_width_mm=PREVIEW_TEXT_WIDTH_MM if max_width_mm is None else max_width_mm,
            settings=settings,
            widths=widths,
        )
    # Only a line that actually holds two or more diagrams needs a table. When
    # every line holds one, they are lone figures and a single aligned
    # paragraph says so with less machinery than a one-column table.
    if lines and max(lines) > 1:
        _write_figure_rows(
            document,
            pictures,
            lines,
            align,
            widths,
            text_width_mm=PREVIEW_TEXT_WIDTH_MM if max_width_mm is None else max_width_mm,
        )
        return
    for index, image in enumerate(pictures):
        width, height = image["size"]
        # One figure per line keeps the same 150 dpi natural size the preview
        # uses. A scanned page ("body") gets no special allowance: the preview
        # caps every picture at one page height, and the reason those pictures
        # stack vertically instead of sitting in a figure row is a layout
        # choice, not a licence to print them larger than the screen shows.
        paper = widths[index]
        paragraph = document.add_paragraph()
        # 图所在的段落要落在正文列上：往右挪出「标签 + 间隙」那么远。
        if indent_mm is not None:
            paragraph.paragraph_format.left_indent = Mm(indent_mm)
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


def _write_figure_rows(document, pictures, lines, align="center", widths=None, text_width_mm=PREVIEW_TEXT_WIDTH_MM):
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
        _write_figure_line(
            document, chunk, text_width_mm=text_width_mm, align=align, widths=widths[start - count : start]
        )
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


def _write_solution_image(document, image, indent_mm=0.0, text_width_mm=None, space_before_pt=None):
    from docx.shared import Mm, Pt

    width, height = image["size"]
    px_per_mm = 96 / 25.4
    column_mm = PREVIEW_TEXT_WIDTH_MM if text_width_mm is None else text_width_mm
    # 预览那边是两步：exam-layout.js 的 imageScale 先按 150dpi 自然尺寸等比缩，
    # 宽高各自取整到 CSS 像素；再由 .solution-figure img 的 max-width:100% 把宽度
    # 收进所在的那一列 —— 高度不动。逐字抄过来，Word 上的图才跟屏幕上那张
    # 一样大、一样宽（预览实测 166.69 × 210.08 毫米，Word 以前是 168 × 210）。
    scale = min(
        float(image["display_width"]) * px_per_mm / width,
        PREVIEW_TEXT_WIDTH_MM * px_per_mm / width,
        PREVIEW_PAGE_HEIGHT_MM * px_per_mm / height,
    )
    box_width_px = round(width * scale)
    box_height_px = round(height * scale)
    box_width_px = min(box_width_px, round(column_mm * px_per_mm))
    paragraph = document.add_paragraph()
    if indent_mm:
        paragraph.paragraph_format.left_indent = Mm(indent_mm)
    paragraph.add_run().add_picture(
        io.BytesIO(image["bytes"]), width=Mm(box_width_px * 25.4 / 96), height=Mm(box_height_px * 25.4 / 96)
    )
    paragraph.paragraph_format.keep_together = True
    if space_before_pt is not None:
        paragraph.paragraph_format.space_before = Pt(space_before_pt)
    # .solution-figure { margin: 6px 0 } 的下外边距。
    paragraph.paragraph_format.space_after = Pt(PREVIEW_SOLUTION_FIGURE_MARGIN_PT)
    return paragraph


def _write_solution(document, item, size, label, lead_space_pt=0.0, table_lefts=None):
    """把一道题的答案块写进 Word。

    结构照抄预览（frontend/library-solutions.js 的 render()）：

        div.print-answer-row（题号列 + 正文列）
          div.solution-result    → strong「答案：」+ 正文（同一行）
          figure.solution-figure  → position=before 的配图
          strong.solution-label「解析：」  ← 独占一行
          div.qb-analysis.solution-paragraph × N

    竖向节奏也照抄：行高 1.35em（Word 的「倍数行距」是字体自然行高的倍数，
    跟 CSS 的 em 不是一回事），每一段的外边距按 CSS 折叠后的结果给。

    ``table_lefts`` 是预览量出来的表格位置：``{"answer": [...], "analysis": [...]}``，
    每个数是那张表离版心左缘多少毫米（答案正文列里的表是居中的，实测 79.80 毫米）。
    """
    from docx.shared import Mm, Pt

    selected, images = item["selected"], item.get("solution_images", [])
    # 预览里答案块是「题号列 + 正文列」两栏网格：题号顶格，答案正文和解析配图
    # 缩在右边那一列（实测缩进 11.22mm、净宽 166.78mm）。Word 当年把
    # 「1. 答案：……」整句顶格写 —— 纸上和屏幕上对不上。
    # 这里照抄那两栏：题号用悬挂缩进顶格，正文用制表符接到右边那一列。
    indent = _answer_block_indent_mm(size)
    column = _answer_block_text_width_mm(size)
    number = label.strip()
    note = AI_REFERENCE_NOTE if item.get("ai") else ""
    # 题号只出现一次：要么顶格单独占一段，要么跟在正文前面（用制表符接到右列）。
    # 预览里题号是 <strong>（粗），AI 备注是 .print-ai-note（常规），所以拆成
    # 几个 run 写，不能一股脑塞进一个 run。
    head_runs = [(number, True), ("\t", True), (note, False)] if number else [(note, False)]
    lead = lead_space_pt or 0.0
    # 预览里的外边距会折叠（相邻两块取大的那个），Word 不会 —— Word 是段后距加段前距。
    # 所以这里记着上一块留下的下外边距，下一段只补差值：折叠后该有的空白，
    # 一分不多一分不少。
    pending = [0.0]
    last = [None]

    def _space_before(top_margin, first=False):
        gap = max(0.0, top_margin - pending[0])
        pending[0] = 0.0
        return (lead if first else 0.0) + gap

    def _close_block():
        """给这个答案块补上「块外面那一圈空白」。

        预览里两个答案块之间是 .print-answer-row 的下外边距（6px）+ 上一个块
        的上外边距（6px）= 12px = 9 磅。Word 没有「外边距」这个东西，只有段后距，
        所以把这两个 4.5 磅一起记在这一段的段后距上。
        """
        if last[0] is None:
            return
        after = last[0].paragraph_format.space_after
        extra = PREVIEW_ANSWER_ROW_MARGIN_PT * 2
        last[0].paragraph_format.space_after = Pt((after.pt if after is not None else 0.0) + extra)

    def _paragraph(text, hanging=False, top=0.0, bottom=0.0, first=False):
        paragraph = document.add_paragraph()
        if indent:
            fmt = paragraph.paragraph_format
            fmt.left_indent = Mm(indent)
            if hanging:
                fmt.first_line_indent = Mm(-indent)
                fmt.tab_stops.add_tab_stop(Mm(indent))
        _write_prefix(paragraph, text, size)
        _line_spacing(paragraph, size * LINE_SPACING)
        paragraph.paragraph_format.space_before = Pt(_space_before(top, first))
        paragraph.paragraph_format.space_after = Pt(bottom)
        pending[0] = bottom
        last[0] = paragraph
        return paragraph

    def _image(image):
        last[0] = _write_solution_image(
            document,
            image,
            indent_mm=indent,
            text_width_mm=column,
            space_before_pt=_space_before(PREVIEW_SOLUTION_FIGURE_MARGIN_PT),
        )
        pending[0] = PREVIEW_SOLUTION_FIGURE_MARGIN_PT

    # 题号和 AI 标记只出现一次：要么跟在答案前面，要么（没有答案正文时）自己占一段，
    # 跟预览里 .print-answer-row 的结构一一对应。
    has_answer = bool(str(selected.get("answer") or "").strip())
    has_analysis = bool(str(selected.get("analysis") or "").strip())
    if not has_answer and not has_analysis and not images:
        _paragraph(head_runs + [(EMPTY_SOLUTION_TEXT, False)], hanging=bool(number), first=True)
        _close_block()
        return
    parts = _analysis_parts(item["fields"].get("analysis", []))
    # 答案正文列里的表：预览把它放在这一列里居中（实测离版心左缘 79.80 毫米）。
    answer_lefts = (table_lefts or {}).get("answer")
    # 解析里的表按段落顺序排，这里按同样的顺序一段一段切给它们。
    part_tables = [sum(1 for block in blocks if block["type"] == "table") for blocks in parts]
    analysis_lefts = (table_lefts or {}).get("analysis")
    analysis_offsets = (
        list(analysis_lefts) if analysis_lefts is not None and len(analysis_lefts) == sum(part_tables) else None
    )
    if has_answer:
        written = _write_field(
            document,
            item["fields"]["answer"],
            size,
            # 「答案：」标签在预览里是 .solution-label（font-weight: 600，半粗）。
            prefix=head_runs + [("答案：", True)],
            where=item["where"],
            indent_mm=indent,
            hanging=bool(number),
            text_width_mm=column,
            space_before_pt=_space_before(0.0, first=True),
            space_after_pt=0.0,
            table_lefts=answer_lefts,
            table_column=(indent, column),
        )
        if written:
            last[0] = written[-1]
        pending[0] = 0.0
    elif not parts:
        # 只有答案配图、没有解析正文时，题号不能丢；预览里这一行只有题号和图。
        _paragraph(head_runs, hanging=bool(number), first=True)
    for image in images:
        if image["position"] == "before":
            _image(image)
    for index, blocks in enumerate(parts):
        # 「解析：」标签在预览里独占一行：library-solutions.js 把 strong 直接挂在
        # 容器上（不是塞进正文段里）。Word 当年把它写进正文段的前缀，纸上就挤成
        # 一行了 —— 结构就不一样，段前段后也没法照抄。
        if index == 0:
            if has_answer:
                _paragraph([("解析：", True)])
            else:
                _paragraph(head_runs + [("解析：", True)], hanging=bool(number))
        written = _write_field(
            document,
            blocks,
            size,
            where=item["where"],
            indent_mm=indent,
            text_width_mm=column,
            # 紧跟「解析：」标签的那一段要补 1.5 磅（Chrome 的行盒被行内公式
            # 撑高，字顶比 Word 高）。后面的解析段不补 —— 预览里那些段之间
            # 的间距是折叠后的 3 磅，补了就多一截。
            space_before_pt=_space_before(
                PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT
                + (PREVIEW_SOLUTION_LINE_BOX_COMPENSATION_PT if index == 0 else 0.0)
            ),
            space_after_pt=PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT,
            table_lefts=(
                analysis_offsets[sum(part_tables[:index]) : sum(part_tables[: index + 1])]
                if analysis_offsets is not None
                else None
            ),
            table_column=(indent, column),
        )
        if written:
            last[0] = written[-1]
        pending[0] = PREVIEW_SOLUTION_PARAGRAPH_MARGIN_PT
        for image in images:
            if image["position"] == "paragraph" and image["paragraph"] == index:
                _image(image)
    for image in images:
        if image["position"] == "after" or (image["position"] == "paragraph" and image["paragraph"] >= len(parts)):
            _image(image)
    _close_block()


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
    # 屏幕预览已经把这道题最后排成几栏量出来了（前端量的是 A4 正文宽，
    # 和 Word 的 178 毫米正文宽一样宽）。照它排 —— 两边各算各的，迟早对不上：
    # 老师核对的是屏幕，拿到手的是文件。宽度仍由 Word 自己兜底：
    # 下面的估算只在没有实测值时才用。
    measured = (options.get("option_columns") or {}).get(item["id"])
    if type(measured) is int and 1 <= measured <= len(letters):
        return measured
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


def _write_compact_options(document, item, size, columns, *, text_width_mm=178, figures=None):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm

    letters = [
        key
        for key in "ABCDE"
        if f"options.{key}" in item["fields"] or any(image["slot"] == key for image in item["images"])
    ]
    columns = max(1, int(columns or 1))
    # 预览里栏与栏之间有 2em 的间隙，Word 的表格没有列间距这一说。
    # 把那一段间隙算进左边那一栏的宽度，再用段落的右缩进把它让出来：
    # 这样每栏的文字区宽度还是预览里的栏宽（80.53 毫米），折行位置才对得上，
    # 而第二栏的左缘正好落在 97.47 毫米 —— 跟预览一模一样。
    column_mm = _option_column_width_mm(columns, text_width_mm)
    widths = [column_mm + OPTION_COLUMN_GAP_MM] * (columns - 1) + [column_mm]
    table = document.add_table(rows=(len(letters) + columns - 1) // columns, cols=columns)
    table.autofit = False
    for column, width in zip(table.columns, widths):
        column.width = Mm(width)
    # 只改 w:gridCol 是不够的：每个格子原来的 w:tcW 原地不动，Word 打开时
    # 会自己重排成等宽（第四十二轮实测：写成 30/20/15 毫米的表，Word 认成
    # 50.8×3）。tblW 和 tcW 都要写，Word 才认我们写的列宽。
    _set_table_widths(table, widths)
    # 选项区整体离版心左缘 2em（预览的 .qb-options padding-left）。
    _set_table_text_indent(table, OPTION_LIST_INDENT_MM)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + side)
        border.set(qn("w:val"), "nil")
        borders.append(border)
    # w:tblPr is a fixed sequence: borders belong between w:jc and w:tblLayout.
    # Appending them at the end puts them after w:tblLook, which is not a valid
    # document — the same trap _write_figure_line already avoids.
    table._tbl.tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"
    )
    for index, letter in enumerate(letters):
        row_index, column_index = divmod(index, columns)
        cell = table.cell(row_index, column_index)
        # 预览里选项那一栏两侧不留白，清掉 Word 默认的 1.905 毫米。
        _zero_cell_margins(cell)
        paragraph = cell.paragraphs[0]
        _font(paragraph.add_run(letter + "."), size)
        blocks = item["fields"].get(f"options.{letter}", [])
        _line_spacing(paragraph, size * LINE_SPACING)
        # 选项里的图最多只能占满正文那一列：Word 不会缩图，画超了就压到隔壁列上。
        # 正文列的宽按这一格的标签算（预览里标签列是 auto，各行自己算宽：
        # 「A.」比「B.」宽，A 那一行的正文列就比 B 窄）。
        usable_mm = _option_cell_usable_mm(columns, text_width_mm, size, letter)
        # 正文列离栏左缘多远 = 标签宽 + 标签与正文之间的间隙（预览的 .qb-option
        # 两列网格）。选项里的图和表都要落在这条线上。
        label_offset_mm = _option_label_indent_mm(letter, size)
        # 一个选项的内容可能不止一段，还可能带着一张表（预览里选项里也能排表）。
        #
        # 原来这里只写 blocks[0]：选项以表开头时直接 KeyError('segments') 崩掉，
        # 整份 Word 导出失败；表排在第二块时更阴险 —— 整张表被静默丢掉，
        # 纸上的选项少了一截，而 PDF 路线那边好好地排着表。
        # 这条路不是理论上的：一张窄表（| x | / | 1 |）的前端实测栏数就是 2，
        # auto 布局下老师什么都不用选，Word 就崩（实测 2026-11-17）。
        #
        # 现在多栏和单栏走同一套块处理（_write_field），内容不会少。
        _write_field(
            cell,
            blocks,
            size,
            where=item.get("where", ""),
            # 表格不能比这一格还宽：Word 不会缩表，超了就压到隔壁列上。
            text_width_mm=usable_mm,
            first_paragraph=paragraph,
            # 标签和正文之间留出预览里那 0.3em，正文从这儿开始，
            # 折行也折回这一列（预览里正文是一个独立的列）。
            first_paragraph_tab_mm=label_offset_mm,
            # 最后一栏右边没有下一栏了，不用让出间隙。
            right_indent_mm=0 if column_index == columns - 1 else OPTION_COLUMN_GAP_MM,
            # 选项里的表在预览里是在**正文列**里居中（.qb-table { margin: 0 auto }），
            # 不是在整格里居中 —— 整格比正文列宽 13.6 毫米（栏宽 + 栏间间隙），
            # 居中的话表会偏右 3.4 毫米（Chrome 实测 71.296 vs 67.924）。
            # 这一列的左缘是相对**格子**说的：格子的左缘就是栏左缘。
            table_column=(label_offset_mm, usable_mm),
            table_column_shrinks=False,
        )
        # 选项里的图在预览里被选项栏宽收口过，Word 照实测宽度排；
        # 就算没有实测值（老前端、预览没量到），也不能超过正文那一列的宽。
        #
        # 位置：预览里 ``.qb-option .qb-figure { grid-column: 2 }`` 把图钉在正文列，
        # 所以图要往右挪出「标签 + 间隙」，且是**左**对齐（不是题干配图那种靠右）。
        # 预览里选项里的几张图是网格里各自一行的格子 —— 一张一行，不是并排。
        option_pictures = [image for image in item["images"] if image["slot"] == letter]
        _write_images(
            cell,
            item,
            letter,
            lines=[1] * len(option_pictures) if option_pictures else None,
            measured=(figures or {}).get("measured", {}).get(letter),
            max_width_mm=usable_mm,
            indent_mm=label_offset_mm,
            align_override="left",
        )
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement("w:cantSplit"))
    _set_option_row_spacing(table, size, columns, len(letters))


def _set_option_row_spacing(table, size, columns, letter_count):
    """把选项表格里单元格的段后距写死。

    Word 表格的行高 = 单元格内段落行高 **+ 该段的段后距**。选项表格的
    单元格段落原来没写段后距，全都继承 Normal 样式的 3 磅 —— 每一行白胖
    3 磅，整张表比预览高 3 磅，后面的内容全被顶下去（真 Word 实测
    _exp116.py：选项表①末行 → 下一题题干这一处边界，Word 比预览宽
    1.062 毫米；把表格内的段后距清零后只剩 0.003 毫米）。

    预览侧的真值（frontend/styles.css:1642）：
        .qb-options { row-gap: .1em }        行与行之间 0.1em
        .qb-option-body { white-space: pre-wrap }  一个选项就是一个块，
                                                  块内没有段后距这回事

    所以：
        多行（末行以外）：段后距 = .1em —— 顶出预览那个行间距
        末行：段后距 = 0 —— 表格底不能多出来
        单行：段后距 = 0 —— 预览里只有一行，没有行间距

    嵌套表（选项里带的表）不在这里动 —— 它的行高由 _write_table 自己管。
    """
    from docx.shared import Pt

    count = max(1, int(columns or 1))
    row_count = (letter_count + count - 1) // count
    gap = Pt(size * PREVIEW_OPTION_ROW_GAP_EM)
    for row_index, row in enumerate(table.rows):
        # 只有「不是末行」的行才顶出预览的行间距。
        after = gap if row_count > 1 and row_index < row_count - 1 else Pt(0)
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = after


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


def _paragraph_flag(props, name, value):
    """写 w:keepNext / w:keepLines / w:widowControl，按 w:pPr 的固定顺序插入。

    w:pPr 是一条固定 sequence：keepNext、keepLines 排在最前，widowControl 紧随，
    spacing 和 jc 都在它们后面。直接 append 会把这些开关塞到 pPr 末尾，
    产出的就不再是合法文档 —— Word 打开时会自己重排（它宽容），但别的排版
    工具未必，可能把这些开关连同「整题同页」一起丢掉。

    python-docx 自己知道这条顺序（它的 ZeroOrOne 会按 successors 插入），
    所以这里用它，而不是手写一张后继元素表。
    """
    from docx.oxml.ns import qn

    existing = props.find(qn(name))
    if existing is not None:
        # 已经存在的那个位置可能是错的，先摘掉再重新按顺序放回去。
        props.remove(existing)
    node = getattr(props, "get_or_add_" + name.split(":")[1])()
    node.set(qn("w:val"), "1" if value else "0")
    return node


def _keep_unit(document, start):
    """Keep a question together when it fits; Word may split an over-page item.

    Cell paragraphs also carry keepNext so a short table and its following
    diagram/options stay with the stem. The final paragraph releases the chain.
    """
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
            value = not (name == "w:keepNext" and index == len(paragraphs) - 1)
            _paragraph_flag(props, name, value)
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
    exact = None
    if spacing is not None and spacing.get(qn("w:lineRule")) == "exact" and spacing.get(qn("w:line")):
        exact = int(spacing.get(qn("w:line"))) / 20 * 25.4 / 72
    if exact is not None and not text and not metrics:
        # 空段：行高由显式行距决定。留白块是**固定行高**的空段
        # （w:lineRule="exact"），拿默认行距去估会把留白少算将近一整行，
        # 分页就会提前。
        return exact + gap
    width = (_text_em(text) + sum(metric[0] for metric in metrics)) * size * 25.4 / 72
    if exact is not None and width <= width_mm:
        # 单行固定行高的段落（续页标题就是这种）：行盒高度就是写死的那行高，
        # 拿正文行距去估会把它算高一大截（10.125 磅被当成 16.2 磅）。
        return exact + gap
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
            # 表格的行高有下限：预览里每行 2.1em（12pt 字号 = 25.2 磅），
            # 算分页的时候不能只按文字高度算，否则 Word 会以为表格比实际矮。
            floor = size * PREVIEW_TABLE_CELL_HEIGHT_EM * 25.4 / 72
            for row in element.findall(qn("w:tr")):
                content = max(
                    (
                        sum(
                            _paragraph_measure(paragraph, size, 178 / columns - 4)
                            for paragraph in cell.findall(qn("w:p"))
                        )
                        for cell in row.findall(qn("w:tc"))
                    ),
                    default=0,
                )
                total += max(content, floor)
    return total


def _pagination(document, start, mode, size):
    """Compact flows at paragraph/row boundaries; short keep-mode items bind."""
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
                _paragraph_flag(props, name, value)
    # An isolated number/origin lead must stay with the start of its actual stem.
    # Only a one-line caption stays with the immediately following diagram/table.
    # A normal multi-line stem must remain breakable in compact mode; plain-text
    # length alone misses the width of its native formulas.
    for index, element in enumerate(elements[:-1]):
        if element.tag != qn("w:p"):
            continue
        plain = "".join(node.text or "" for node in element.findall(".//" + qn("w:t"))).strip()
        next_element = elements[index + 1]
        lead_only = bool(re.fullmatch(r"\d+\.(?:（AI 参考，未核对）)?\s*", plain))
        next_visual = next_element.tag == qn("w:tbl") or bool(next_element.findall(".//" + qn("w:drawing")))
        formula_width = sum(_math_extent(node)[0] for node in element.findall(".//" + qn("m:oMath")))
        width_mm = (_text_em(plain) + formula_width) * size * 25.4 / 72
        short_caption = bool(plain and len(plain) <= 40 and width_mm <= 178)
        if lead_only or (short_caption and next_visual):
            element.get_or_add_pPr().find(qn("w:keepNext")).set(qn("w:val"), "1")


def _answer_space_groups(millimetres, plan):
    """把预览量出来的留白分段整理成 Word 要写的一组组留白。

    plan 是预览量出来的：题目编号 → [{"mm": 毫米, "continuation": 是否续页}, …]。
    预览的留白是**可切**的（frontend/exam-layout.js 的 writingSpace）：页底放不下的
    那几毫米挪到下一页，续页顶部还打一行「第 N 题答题区（续）」。Word 排版是静态的，
    自己不知道该在哪儿切、也不知道该打这行标题，只能照这里量好的来。

    量来的分段总高必须和这次要的留白一致；对不上（老草稿、预览改过、别的导出器
    量的是另一份卷子）就当没有量过，退回「整块留白、不打标题」的老行为。
    """
    fallback = [{"mm": float(millimetres), "continuation": False}]
    if not isinstance(plan, list) or not plan:
        return fallback
    groups = []
    for piece in plan:
        if not isinstance(piece, dict):
            return fallback
        value = piece.get("mm")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return fallback
        groups.append({"mm": float(value), "continuation": bool(piece.get("continuation"))})
    if abs(sum(group["mm"] for group in groups) - millimetres) > 0.6:
        return fallback
    return groups


def _answer_space_slices(millimetres):
    """把一段留白切成若干**单行**固定行高段（高度守恒）。

    单行段 Word 没法在段内断开，只能整段挪到下一页 —— 这正是预览要的
    「页底放得下几毫米就留几毫米、放不下就整段走」。
    """
    remaining = int(round(millimetres * 1440 / 25.4))
    slices = []
    while remaining > 0:
        take = min(remaining, ANSWER_SPACE_SLICE_TWIPS)
        remaining -= take
        slices.append(take)
    return slices


def _write_continuation_label(document, number):
    """写出续页顶部的「第 N 题答题区（续）」标题。

    预览里这是 .exam-continuation-label：10px 字号、line-height 1.35、#666、
    下外边距 4px（合起来 4.63 毫米）。这段标题必须跟住下面那块续页留白
    （keep_with_next）—— 不然 Word 把它留在上一页页底，学生在下一页看到的是
    一块没有主人的答题区。
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(PREVIEW_CONTINUATION_LABEL_AFTER_PT)
    fmt.keep_with_next = True
    spacing = paragraph._p.get_or_add_pPr().get_or_add_spacing()
    spacing.set(qn("w:line"), str(int(round(PREVIEW_CONTINUATION_LABEL_LINE_PT * 20))))
    spacing.set(qn("w:lineRule"), "exact")
    _font(
        paragraph.add_run(f"第 {number} 题答题区（续）"),
        PREVIEW_CONTINUATION_LABEL_SIZE_PT,
        color=PREVIEW_CONTINUATION_LABEL_COLOR,
    )
    return paragraph


def _write_answer_space(document, millimetres, plan=None, number=None):
    """写出答题留白块（**可跨页断开**，跨页时补「第 N 题答题区（续）」标题）。

    预览里留白是紧贴上一块、上下没有外边距的一个固定高度块
    （frontend/library.css 的 .print-answer-space），题目块的下外边距
    （8 磅）由 _question_gap 补在留白之后。

    Word 里**不能**拿「段后距」当留白高度：段后距不吃行盒，一段 8 毫米的
    段后距实际排出来是「一个空行盒 + 8 毫米间距」，比预览多出整个行盒的高度。
    第六十九轮实测：这样每道解答题比预览高 3.95 毫米（11.2 磅）。

    这里改成**固定行高**（w:lineRule="exact"）的空段，行盒高度就是留白高度；
    同时把上一段的段后距归零，让留白真正贴住上一块（预览里 .print-answer-space
    的上下外边距都是 0）。

    第七十轮：**留白必须能断开**。预览的留白是可切的
    （frontend/exam-layout.js 的 writingSpace：页底放不下的那几毫米留白
    挪到下一页，续页顶部还打一行「第 N 题答题区（续）」）。
    Word 原来把 30 毫米留白写成**一整块**固定行高空段 —— 固定行高的段落
    不可断开，放不下就整题推到下一页，于是 Word 每页比预览少装一题，
    分页点逐页漂：40 题对照卷实测，预览每页首题 1,7,14,20,27,33,40，
    Word 是 1,7,13,19,25,31,37，7 页里 5 页对不上。

    修法：把留白切成若干个**单行**固定行高段。单行段落 Word 无法在段内断开，
    只能整段挪到下一页 —— 于是「页底放得下几毫米就留几毫米、放不下就整段走」
    的行为跟预览一致了。

    第七十一轮：**照着预览的分段写，并补上续页标题**。预览的留白在页底被切成
    「首页那一份 + 续页那一份」（40 题对照卷实测：题 6 是 24.6 + 5.4 毫米，
    题 13 整块 30 毫米挪到续页），续页上还要打一行「第 N 题答题区（续）」。
    Word 原来把 30 毫米一律切成 20.1 + 9.9 —— 切点跟预览不是一回事，
    续页顶部那行标题更是压根没有。预览的切法只有浏览器知道，所以前端把
    量好的分段（measured_answer_splits）送过来，Word 照着写：
      · 首页那份按 20.1 毫米的粒度切（保住「放不下就整段走」的阈值）
      · 续页那份单独成段，前面加标题
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt
    from docx.text.paragraph import Paragraph

    elements = list(document._element.body)
    previous = elements[-2] if len(elements) >= 2 else None
    if previous is not None and previous.tag == qn("w:p"):
        Paragraph(previous, document).paragraph_format.space_after = Pt(0)

    labels = []
    for group in _answer_space_groups(millimetres, plan):
        if group["mm"] <= 0:
            continue
        if group["continuation"]:
            labels.append(_write_continuation_label(document, number))
        for take in _answer_space_slices(group["mm"]):
            paragraph = document.add_paragraph()
            fmt = paragraph.paragraph_format
            fmt.space_before = Pt(0)
            fmt.space_after = Pt(0)
            spacing = paragraph._p.get_or_add_pPr().get_or_add_spacing()
            spacing.set(qn("w:line"), str(take))
            spacing.set(qn("w:lineRule"), "exact")
    return labels


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


def _page_top_space(document, points=PREVIEW_PAGE_TOP_SPACE_PT):
    """在页首补回预览那一段外边距（.print-section 的 margin-top 12px）。

    Word 不吃页首的段前距 —— 硬分页、段前分页都不算，兼容开关
    ``w:suppressSpBfAfterPgBrk=0`` 也实测无效（见 PREVIEW_PAGE_TOP_SPACE_PT
    上面的对照表）。唯一靠得住的办法是在页首垫一个**固定行距**的空行：
    行盒高度就是那 9 磅（180 twips），跟字号无关（实测 1 磅字号和 12 磅字号
    落点完全一样），纸上只多 3.175 毫米白，跟预览里那 12px 一模一样。

    这个空行是**正文里的一个空段落**：正文里多一行空白，但纸上多出来的
    就是预览里页首那 12px —— 位置对得上就说明排版对上了。
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt

    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    # 字号压到 1 磅：万一固定行距被忽略，也不至于撑出一个 12 磅的行盒。
    paragraph.add_run("").font.size = Pt(1)
    spacing = paragraph._p.get_or_add_pPr().get_or_add_spacing()
    spacing.set(qn("w:line"), str(PREVIEW_PAGE_TOP_LINE_TWIPS))
    spacing.set(qn("w:lineRule"), "exact")
    return paragraph


def _section_page_top_space(document, twips=None):
    """大题标题落在页首时：分页 + 垫一行，把预览页首那 12px 补回来。

    原来这里是把 ``w:pageBreakBefore`` 写在标题自己身上 —— 页首的段前距
    Word 照样不吃（见 PREVIEW_PAGE_TOP_SPACE_PT 上面的对照表），标题比预览
    高 2.291 毫米。

    现在改成：分页写在**垫行**身上，标题跟着垫行走，标题自己的段前距归零
    （它在页首，写了也是白写；不归零就跟垫行叠加）。

    垫行的高度不是预览那 9 磅，而是 7.8 磅（156 twips）—— Word 把「固定行距」
    量化到 0.6 磅一档，实测六档里 150/152 落在最好的一档，取那档的中间值，
    见 PREVIEW_SECTION_PAGE_TOP_LINE_TWIPS 的说明。
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt

    if twips is None:
        twips = PREVIEW_SECTION_PAGE_TOP_LINE_TWIPS
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    # 字号压到 1 磅：万一固定行距被忽略，也不至于撑出一个 12 磅的行盒。
    paragraph.add_run("").font.size = Pt(1)
    spacing = paragraph._p.get_or_add_pPr().get_or_add_spacing()
    spacing.set(qn("w:line"), str(twips))
    spacing.set(qn("w:lineRule"), "exact")
    paragraph.paragraph_format.page_break_before = True
    return paragraph


def _footer_style(document):
    """把页脚样式改成预览的那一套。

    **域结果的格式取自段落样式，不是域里的 run。** 实测（真 Word 渲染，
    _probe96.py 五个变体对照）：

      - fldSimple 里放带格式的 run      → 无效，域结果还是正文 12 磅黑
      - 段落标记 pPr/rPr 带格式          → 无效
      - 三段式域（fldChar）+ 带格式结果  → 无效
      - 改 Footer 样式                    → **有效**，整行连域结果都对了

    所以域里那个 run 带不带格式都无所谓，但样式必须改 —— 不改的话，
    页码和总页数这两个数字会比预览大一圈、还是纯黑。
    """
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor

    style = document.styles["Footer"]
    style.font.size = Pt(PREVIEW_FOOTER_FONT_SIZE_PT)
    style.font.name = PREVIEW_FOOTER_LATIN
    style.font.color.rgb = RGBColor.from_string(PREVIEW_FOOTER_COLOR)
    rpr = style.element.get_or_add_rPr()
    rpr.get_or_add_rFonts().set(qn("w:eastAsia"), PREVIEW_FOOTER_EAST_ASIA)
    return style


def _footer_field(footer, instr):
    """页脚里的域（PAGE / NUMPAGES）。

    域自带一个带格式的 run —— Word 更新域时按域里 run 的格式重排，
    域里没有 run 就退回段落默认格式（正文 12 磅纯黑），字号和颜色全丢。

    run 是先用 python-docx 造好再搬进域里的：w:color 之类由 python-docx
    写才不会插错 OOXML 次序（自己插容易插错位置，Word 打开就报文件损坏）。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instr)
    run = footer.add_run("1")  # 域的缓存结果；Word 打开/打印时按 PAGE/NUMPAGES 重算
    _font(
        run,
        PREVIEW_FOOTER_FONT_SIZE_PT,
        east_asia=PREVIEW_FOOTER_EAST_ASIA,
        latin=PREVIEW_FOOTER_LATIN,
        color=PREVIEW_FOOTER_COLOR,
    )
    field.append(run._r)  # lxml 的 append 是移动：run 从段落里进到域里
    footer._p.append(field)
    return field


def _write_footer(document, count):
    """页脚：第 <PAGE> 页 / 共 <NUMPAGES> 页 · 共 N 题。

    文字照抄预览（frontend/exam-layout.js:463）。

    页码必须是域（fldSimple PAGE），不能是写死的数字 —— 域的结果是 Word
    打开时才算出来的，写死的话整份卷子都印「第 1 页」。
    总页数同理用 NUMPAGES 域：预览分完页才知道有几页，写死的话分页一变就错。
    单独抽出来，是为了能单独盯住它（见 test_library_export_footer.py）。
    """
    _footer_style(document)
    footer = document.sections[0].footer.paragraphs[0]
    footer.alignment = 1  # 预览 .exam-page-number 是 text-align:center
    _line_spacing(footer, PREVIEW_FOOTER_LINE_HEIGHT_PT)

    def text(value):
        _font(
            footer.add_run(value),
            PREVIEW_FOOTER_FONT_SIZE_PT,
            east_asia=PREVIEW_FOOTER_EAST_ASIA,
            latin=PREVIEW_FOOTER_LATIN,
            color=PREVIEW_FOOTER_COLOR,
        )

    text("第 ")
    _footer_field(footer, "PAGE")
    text(" 页 / 共 ")
    _footer_field(footer, "NUMPAGES")
    text(f" 页 · 共 {count} 题")


def _page_setup(document):
    """纸张和页边距：A4，上下 18、左右 16 毫米。

    正文净宽 = 210 - 16 * 2 = 178 毫米，和 PREVIEW_TEXT_WIDTH_MM 必须一致 ——
    表格的列宽就是按这个数分配的。

    抽成函数是因为体检器要用同一份设置（第四十二轮）：尺子自己拿
    python-docx 默认模板造文档的话，那份模板是 Letter 纸加 1 英寸边距，
    正文净宽只有 165 毫米 —— 178 毫米的表在里头当然出血。
    那是尺子的锅，不是实现的锅，尺子得跟生产共用这一份。
    """
    from docx.shared import Mm

    section = document.sections[0]
    section.page_width, section.page_height = Mm(210), Mm(297)
    section.top_margin = section.bottom_margin = Mm(18)
    section.left_margin = section.right_margin = Mm(16)
    section.header_distance = section.footer_distance = Mm(8)
    return section


def _document(captured, title, options, mode):
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt

    mode = _effective_document(captured, mode)
    inline_answers = mode == "combined" and options.get("answer_layout", "appendix") == "inline"
    document = Document()
    _page_setup(document)
    size = options["font_size"]
    normal = document.styles["Normal"]
    normal.font.name, normal.font.size = PREVIEW_LATIN_FONT, Pt(size)
    _line_spacing(normal, size * LINE_SPACING)
    normal.paragraph_format.space_after = Pt(PARAGRAPH_GAP_PT)
    normal.paragraph_format.widow_control = True
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")
    # Word math defaults, including editable equations, follow the selected size.
    default = document.styles["Default Paragraph Font"]
    default.font.name, default.font.size = PREVIEW_LATIN_FONT, Pt(size)
    heading = document.add_paragraph()
    heading.alignment = 1
    heading.paragraph_format.keep_with_next = True
    # .print-title 的字号是写死的 24px（18 磅），不跟字号选项走。
    _font(heading.add_run(title), PREVIEW_TITLE_FONT_SIZE_PT, True, letter_spacing=PREVIEW_TITLE_LETTER_SPACING)
    # .print-title 继承 body 的 line-height: 1.5 → 24px 字号 = 36px 行盒 = 27 磅。
    _line_spacing(heading, PREVIEW_TITLE_LINE_HEIGHT_PT)
    if options["student_info"] and mode != "answers":
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt

        info = document.add_paragraph()
        # 预览里这一行是居中的（.print-info 的 text-align: center），PDF 也跟着
        # 那条样式走。Word 这里原来没写对齐，落在左边，于是老师在预览里看着居中、
        # 导出来却贴着左页边 —— 整张卷子上只有这一行对不上，一眼就能看出来。
        info.alignment = WD_ALIGN_PARAGRAPH.CENTER
        info.paragraph_format.keep_with_next = True
        # 字、字号、颜色、字体、上下外边距、行高全部照抄 .print-info（Chrome 实测
        # 见 _probe93.py）。少了上下那 12px / 22px，卷子从这一行往下整体上移
        # 7.3 毫米 —— 预览和导出的题号根本不在一条横线上。
        info.paragraph_format.space_before = Pt(PREVIEW_INFO_SPACE_BEFORE_PT)
        info.paragraph_format.space_after = Pt(PREVIEW_INFO_SPACE_AFTER_PT)
        _line_spacing(info, PREVIEW_INFO_LINE_HEIGHT_PT)
        _font(
            info.add_run(PREVIEW_STUDENT_INFO_TEXT),
            PREVIEW_INFO_FONT_SIZE_PT,
            east_asia=PREVIEW_INFO_EAST_ASIA,
            latin=PREVIEW_INFO_LATIN,
            color=PREVIEW_INFO_COLOR,
        )
    numbered, number = [], 0
    # 考生信息行在不在（第一组大题标题的前一个块就是它）。
    has_student_info = bool(options["student_info"]) and mode != "answers"
    # 预览量出来的表格位置：题目编号 → 位置 → 离版心左缘多少毫米。
    # 表格在预览里的横向位置不是定值（题干正文越长，表在那一列里越靠中间），
    # Word 排版是静态的，只能照量好的位置摆。
    measured_tables = options.get("measured_table_offsets") or {}
    for index, (name, group) in enumerate(_ordered(captured)):
        section_title = None
        # 这一组的第一题被设成「每题分页」时，预览里**整组的大题标题跟着它**
        # 落到新页页首：大题标题在预览的排版器里是 pending 的，题一分页，
        # 它就跟到新页的第一块（实测 _probe106.py：第 2 页第一个孩子就是它）。
        #
        # Word 这边原来把分页写在标题自己身上（page_break_before），页首的段前距
        # 照样被 Word 吃掉，标题比预览高 2.291 毫米（实测 _probe105.py）。
        # 现在改成：组开头先垫一个带分页的垫行，标题跟着它，段前距归零。
        group_starts_page = mode != "answers" and number > 0 and group[0]["id"] in options["question_breaks"]
        # 前一块在预览里的下外边距 —— 大题标题的段前距要把它加上（见下面）。
        # 第一组的前一块是考生信息行（.print-info 的 margin-bottom 22px），
        # 之后每一组的前一块都是上一组的最后一道题（.print-question 的 8pt）。
        if index == 0:
            previous_margin_bottom = PREVIEW_INFO_SPACE_AFTER_PT if has_student_info else 0.0
        else:
            previous_margin_bottom = PREVIEW_QUESTION_MARGIN_BOTTOM_PT
        if mode != "answers":
            if group_starts_page:
                _section_page_top_space(document)
            section_title = document.add_paragraph()
            section_title.paragraph_format.keep_with_next = True
            _font(
                section_title.add_run(_section_heading(group, index, name)),
                PREVIEW_SECTION_FONT_SIZE_PT,
                True,
                east_asia=PREVIEW_SECTION_EAST_ASIA,
            )
            # .print-section { margin: 12px 0 6px; line-height: 1.5 } → 18 磅行盒。
            _line_spacing(section_title, PREVIEW_SECTION_LINE_HEIGHT_PT)
            # 段前距 = **前一块在预览里的下外边距 + 本标题的上外边距**。
            #
            # 预览的 .exam-page-body 是 flex 容器：相邻两块的外边距**相加**
            # （题块 8 磅 + 标题 9 磅 = 17 磅；考生信息行 16.5 磅 + 标题 9 磅
            # = 25.5 磅）。Word 却是 max(前段段后距, 后段段前距)（真 Word 实测
            # _probe107.py，本轮 _probe113.py 又验了一遍）：只写 9 磅，前一块的
            # 下外边距就被 Word 吃掉 —— 整卷从第一个大题标题起逐段往下漂移
            # （实测三个标题分别偏 −1.798 / −3.463 / −6.091 毫米）。
            #
            # 把和值整个写进段前距，Word 取大时取到的正是预览那个和值。
            # 标题落在页首时另说：那里没有前一块，12px 由垫行补回来（见上面
            # PREVIEW_SECTION_PAGE_TOP_LINE_TWIPS）。
            section_title.paragraph_format.space_before = Pt(
                0 if group_starts_page else previous_margin_bottom + PREVIEW_SECTION_SPACE_BEFORE_PT
            )
            section_title.paragraph_format.space_after = Pt(PREVIEW_ANSWER_ROW_MARGIN_PT)
        for item in group:
            number += 1
            numbered.append((number, item))
            if mode == "answers":
                continue
            unit_start = len(document._element.body) - 1
            # 题号在预览里是粗的（.qb-number { font-weight: 700 }），Word 这边
            # 原来写成一个普通 run，纸上就是常规 —— 一眼能看出粗细不一样。
            #
            # 题号还占预览里**自己那一列**：.qb-stem 是「题号列 + 正文列」两栏
            # （gap .35em），正文列左缘 = (0.5*位数 + 0.25 + 0.35) em。
            # Word 原来把题号写成行内 run「N. 」（1.0 em）：
            #   · 正文列左缘只有 1.0 em，比预览窄 0.1 em = 0.42 毫米 ——
            #     40 个汉字（169.33 毫米）在预览里放不下第 41 个，在 Word 里
            #     放得下，断行点就差一个字（实测 _probe167.py：题 6 差一个字）；
            #   · 折行更是从版心左缘起（预览折回正文列，差 4.66 毫米，
            #     实测 _probe167.py：Word 16.10 毫米 vs 预览 20.65 毫米）。
            # 现在照抄预览：题号写成「N.」+ tab，段落悬挂缩进 = 正文列左缘，
            # 第一行从版心左缘起（题号列），正文从正文列起，折行也折回这一列。
            stem_left = _stem_column_left_mm(size, len(str(number)))
            number_paragraph = document.add_paragraph()
            _font(number_paragraph.add_run(f"{number}."), size, True)
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
                    _write_field(
                        document,
                        origin_blocks,
                        size,
                        prefix=[("（题源）", False)],
                        where=item["where"],
                        first_paragraph=number_paragraph,
                        first_paragraph_tab_mm=stem_left,
                        first_paragraph_keep=True,
                    )
                    # 题号那一段已经被题源用掉了，题干接着从正文列起。
                    number_paragraph = None
            image_body = item["content"].get("body_mode", "text") == "source_image"
            stem_column = (stem_left, PREVIEW_TEXT_WIDTH_MM - stem_left)
            table_lefts = measured_tables.get(item["id"]) or {}
            _write_field(
                document,
                [] if image_body else item["fields"]["stem"],
                size,
                where=item["where"],
                lead_segments=lead_segments,
                table_lefts=table_lefts.get("stem"),
                table_column=stem_column,
                # 题干正文列是 flex item：正文比表窄时整列缩到表宽，表于是贴左。
                table_column_shrinks=True,
                # 题干正文列的左缘（版心左缘起算）——预览的正文列，
                # 折行也折回这一列。
                indent_mm=stem_left,
                # 首行顶上挂着题号：题号落在版心左缘，正文从正文列起。
                first_paragraph=number_paragraph,
                first_paragraph_tab_mm=stem_left,
                first_paragraph_keep=True,
            )
            # 老师在预览里改过的图宽、整行位置和换行点，跟预览走同一套算法，
            # 而不是在这里再猜一次。预览和 PDF 共用 exam-layout.js 的排版，
            # 只有 Word 是这份代码，所以两边最容易分家 —— 数字先统一到这里。
            figures = _figure_settings(options, item)
            # 原图题在预览里就是一张一张竖排（.qb-question-images 是 column 方向），
            # 只有排版文字的题干配图跟着 .qb-figures 的换行走。
            if image_body:
                _write_images(document, item, "body")
            else:
                stem_figures = [image for image in item["images"] if image["slot"] == "stem"]
                stem_measured = figures["measured"].get("stem")
                _write_images(
                    document,
                    item,
                    "stem",
                    _figure_row_lines(
                        stem_figures,
                        settings=figures,
                        widths=_figure_widths_for_slot(stem_figures, "stem", figures, stem_measured),
                    ),
                    settings=figures,
                    measured=stem_measured,
                )
            columns = _option_columns(item, options) if not image_body else 1
            if columns > 1:
                _write_compact_options(document, item, size, columns, figures=figures)
            else:
                # 单栏路径：预览里选项照样是「选项区缩进 2em + 标签列 + 正文列」
                # 两列网格（.qb-options padding-left 2em，.qb-option 两列 grid），
                # 不是顶格的一段话。原来这里直接写 "A. 正文"，整块选项比预览
                # 靠左 8.47 毫米，折行也不回正文列（实测：Word 顶格 0.11 毫米，
                # 预览正文列 13.85 毫米）。
                #
                # 这里照抄预览：段落左缩进 = 选项区缩进 + 标签宽 + 标签与正文的
                # 间隙，首行悬挂缩进 = 负的（标签宽 + 间隙）—— 标签落在选项区
                # 缩进那一列，正文落在正文列，制表位把正文推过去。
                # 选项里的图钉在正文列（.qb-option .qb-figure { grid-column: 2 }），
                # 选项里的表在正文列里居中（.qb-table { margin: 0 auto }），
                # 两样都按正文列算。
                for letter in "" if image_body else "ABCDE":
                    has_field = f"options.{letter}" in item["fields"]
                    has_image = any(image["slot"] == letter for image in item["images"])
                    if not has_field and not has_image:
                        continue
                    # 标签列是 auto 宽，各行自己算：「A.」比「B.」宽，
                    # 所以 A 那一行的正文列比 B 窄（Chrome 实测 13.853 / 13.618）。
                    label_offset_mm = _option_label_indent_mm(letter, size)
                    body_left_mm = OPTION_LIST_INDENT_MM + label_offset_mm
                    body_width_mm = PREVIEW_TEXT_WIDTH_MM - body_left_mm
                    if has_field:
                        paragraph = document.add_paragraph()
                        _font(paragraph.add_run(f"{letter}."), size)
                        # 标签这一段自己也要缩进：选项以表开头时正文用不上
                        # first_paragraph（表是独立的一块），标签就落回顶格了。
                        fmt = paragraph.paragraph_format
                        fmt.left_indent = Mm(body_left_mm)
                        fmt.first_line_indent = Mm(-label_offset_mm)
                        _write_field(
                            document,
                            item["fields"][f"options.{letter}"],
                            size,
                            where=item["where"],
                            # 正文列的左缘（版心左缘起算）。
                            indent_mm=body_left_mm,
                            text_width_mm=body_width_mm,
                            first_paragraph=paragraph,
                            # 标签 → 正文：正文从悬挂缩进那一列开始。
                            first_paragraph_tab_mm=label_offset_mm,
                            # 选项区自己的 2em 缩进。
                            list_indent_mm=OPTION_LIST_INDENT_MM,
                            # 选项里的表在预览里是在**正文列**里居中，不是整栏居中。
                            table_column=(body_left_mm, body_width_mm),
                            table_column_shrinks=False,
                        )
                    else:
                        paragraph = document.add_paragraph()
                        _font(paragraph.add_run(f"{letter}."), size)
                        fmt = paragraph.paragraph_format
                        fmt.left_indent = Mm(body_left_mm)
                        fmt.first_line_indent = Mm(-label_offset_mm)
                    # 单栏路径里选项的段后距也写死：预览 .qb-options 的 row-gap
                    # 是 .1em（frontend/styles.css:1642），而这一段原来继承
                    # Normal 的 3 磅，选项之间凭空多出 1.8 磅（0.63 毫米）。
                    # 最后一个选项的段后距由 _question_gap 抬到 8 磅（题块下外边距）。
                    paragraph.paragraph_format.space_after = Pt(size * PREVIEW_OPTION_ROW_GAP_EM)
                    # 选项里的图在预览里被钉在正文列、左对齐，一张一行。
                    option_pictures = [image for image in item["images"] if image["slot"] == letter]
                    _write_images(
                        document,
                        item,
                        letter,
                        lines=[1] * len(option_pictures) if option_pictures else None,
                        measured=figures["measured"].get(letter),
                        max_width_mm=body_width_mm,
                        indent_mm=body_left_mm,
                        align_override="left",
                    )
            answer_space = options.get("answer_space_overrides", {}).get(item["id"], options["answer_space"])
            # 每道题都能有留白；解答题有 8 毫米下限。口径与预览、PDF 一致。
            millimetres = _answer_space_millimetres(answer_space, item["type"])
            continuation_labels = []
            if millimetres > 0 and not inline_answers:
                # 预览量出来的留白分段（哪一份在首页、哪一份在续页）。
                # 没有就退回整块留白（老草稿、PDF 路线）。
                plan = (options.get("measured_answer_splits") or {}).get(item["id"])
                continuation_labels = _write_answer_space(document, millimetres, plan=plan, number=number)
            _question_gap(document)
            _pagination(document, unit_start, options["pagination"], size)
            # _pagination 会把整段里的 keepNext 归零，续页标题得自己把 keepNext 补回去：
            # 它必须跟住下面那块续页留白，不然会被留在上一页页底。
            for label in continuation_labels:
                label.paragraph_format.keep_with_next = True
            if inline_answers and _has_selected_answers([item]):
                # A long solution may cross pages; never keep it with the entire
                # question or add student writing space to a teacher copy.
                _write_solution(document, item, size, "")
                _question_gap(document)
            if number > 1 and item["id"] in options["question_breaks"]:
                if item is group[0] and section_title is not None:
                    # 这一组的分页已经在组开头处理了（垫行 + 标题），
                    # 这里再写一次就多一个空页。
                    pass
                else:
                    _question_page_break(document, unit_start)
    if mode != "questions" and not inline_answers:
        if mode == "combined":
            document.add_page_break()
            # 答案区从新页页首开始：预览里页首那 12px 是**算进去**的
            # （.exam-page-body 是 flex 容器，外边距不塌陷），Word 不吃页首的
            # 段前距 —— 硬分页、段前分页、兼容开关都试过，一个都不管用。
            # 所以这里垫一个固定行距 9 磅的空行把那 12px 补回来，
            # 标题自己的段前距就得让位（否则补两次，实测会多出 3.175 毫米）。
            _page_top_space(document)
        answer_heading = document.add_paragraph()
        answer_heading.paragraph_format.keep_with_next = True
        _font(
            answer_heading.add_run("参考答案与解析"),
            PREVIEW_SECTION_FONT_SIZE_PT,
            True,
            east_asia=PREVIEW_SECTION_EAST_ASIA,
        )
        # .print-section { margin: 12px 0 6px; line-height: 1.5 } → 18 磅行盒。
        _line_spacing(answer_heading, PREVIEW_SECTION_LINE_HEIGHT_PT)
        # 段前距 12px：合卷模式下这一份已经由页首那个空行补上了（Word 页首不吃
        # 段前距，写在这里只会跟空行叠加）；答案卷里标题跟在卷名后面、不在页首，
        # 预览的 margin-top 照算，这边就照写。
        answer_heading.paragraph_format.space_before = Pt(0 if mode == "combined" else PREVIEW_SECTION_SPACE_BEFORE_PT)
        answer_heading.paragraph_format.space_after = Pt(PREVIEW_ANSWER_ROW_MARGIN_PT)
        for index, (number, item) in enumerate(numbered):
            unit_start = len(document._element.body) - 1
            # 题号后面留一个空格：预览里题号和正文是分开的两栏，Word 是同一段，
            # 挤在一起读起来像「2.答案：」。
            label = f"{number}. "
            # 第一个答案块紧跟在小标题后面：预览里那一行的上外边距（6px = 4.5 磅）
            # 就是这一块自己的。之后的块靠 _question_gap 隔开（上下各 6px = 9 磅）。
            _write_solution(
                document,
                item,
                size,
                label,
                # 预览的 .print-answer-row 上外边距是 6px = 4.5 磅，这边照抄；
                # 再补 3.5 磅 —— Chrome 和 Word 的行盒模型不同，标题行盒
                # 18 磅、答案行盒 16.2 磅，两行挨着时字顶距离 Word 比预览短
                # 3.5 磅（详见 WORD_LINE_BOX_LEADING_COMPENSATION_PT）。
                lead_space_pt=(
                    PREVIEW_ANSWER_ROW_MARGIN_PT + WORD_LINE_BOX_LEADING_COMPENSATION_PT if index == 0 else 0.0
                ),
                table_lefts={
                    "answer": (measured_tables.get(item["id"]) or {}).get("answer"),
                    "analysis": (measured_tables.get(item["id"]) or {}).get("analysis"),
                },
            )
            _question_gap(document)
            # Even keep-whole-question mode must allow a long solution to flow.
            _pagination(document, unit_start, "compact", size)
    _write_footer(document, len(captured))
    _disable_cjk_auto_spacing(document)
    _disable_punctuation_overflow(document)
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
            # 预览量过栏数时，文件照屏幕排，屏幕上已经提示过降级，这里不再重复；
            # 没有实测值（老前端、PDF 路径）才由 Word 自己决定，这时必须说清楚。
            measured = (options.get("option_columns") or {}).get(item["id"])
            if (
                type(measured) is not int
                and wanted > actual
                and (item["content"].get("options") or any(image["slot"] in "ABCDE" for image in item["images"]))
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
