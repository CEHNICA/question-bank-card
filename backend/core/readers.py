"""视觉模型读题：誊录、第二次独立识读、分歧裁决、缺号定位。

模型输出一律用【标签】分段的纯文本，不用 JSON——LaTeX 的反斜杠放进 JSON 字符串
经常被模型写坏（\\f、\\b 会被当成转义），纯文本就没有这个问题。
"""

from __future__ import annotations

import os
import random
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import requests

from .textnorm import clean_option, clean_stem, fix_symbols

MINIMAX_MODEL = os.environ.get("QB_MINIMAX_MODEL", "MiniMax-M3")
MINIMAX_DEFAULT_URL = "https://api.minimax.cn/v1/chat/completions"
MINIMAX_HOSTS = frozenset({"api.minimax.cn", "api.minimax.io", "api.minimaxi.com"})
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_MODEL = os.environ.get("QB_SILICONFLOW_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
RETRYABLE = frozenset({429, 500, 502, 503, 504})
BACKOFF = (2.0, 5.0, 12.0)
MAX_RESPONSE_BYTES = 200_000
OPTION_KEYS = ("A", "B", "C", "D")
TAG = re.compile(r"【\s*(题号|题型|题干|A|B|C|D|配图|其他题号|刻度)\s*】")
_IN_FLIGHT = threading.BoundedSemaphore(int(os.environ.get("QB_PARALLEL", "4")))


class ReaderError(RuntimeError):
    """可以直接给用户看的失败原因（不含密钥、不含原始返回）。"""


@dataclass(frozen=True)
class Engine:
    provider: str   # minimax | siliconflow
    model: str

    @property
    def label(self) -> str:
        return self.model.split("/")[-1]


def configured(service: str) -> bool:
    """网页进程拿不到密钥，只拿到"已配置"的标记；工作者进程拿到真正的密钥。"""
    key = {"minimax": "MINIMAX_API_KEY", "siliconflow": "SILICONFLOW_API_KEY", "mineru": "MINERU_TOKEN"}[service]
    return bool(os.environ.get(key, "").strip()) or os.environ.get(f"QB_{service.upper()}_CONFIGURED") == "1"


def primary_engine() -> Engine | None:
    return Engine("minimax", MINIMAX_MODEL) if configured("minimax") else None


def checker_engine() -> Engine | None:
    """第二位读者：优先用另一家（硅基流动 Qwen-VL），没有就用 MiniMax 再独立读一遍。"""
    if configured("siliconflow"):
        return Engine("siliconflow", SILICONFLOW_MODEL)
    return primary_engine()


# ---------------------------------------------------------------- 提示词

TRANSCRIBE_RULES = """你是数学试卷誊录员。图片是从一张学生做过的试卷上裁下的一道题；若由几段拼成，灰色横线是拼接处，按从上到下的顺序阅读。
只誊录印刷体内容：
- 学生的手写字、批改符号、圈画、划线、草稿一律忽略；括号或横线里手写填的答案不要写，保留空括号（ ）或横线 ____。
- 数学式用 LaTeX，行内公式用 $...$ 包住；中文和中文标点照原卷。
- 数轴、函数图象、平面/立体几何图（包括棱柱）、统计图、表格、流程图等视觉内容不得改写成“[图：……]”“图片中……”或其他文字说明，也不要重排成 Markdown 表格、字符图或项目列表。只誊录图外真正印刷的题干文字。
- 某个选项只有图时，对应的【A】【B】【C】【D】留空；图内的数字、字母、刻度和表格单元格仍属于配图，不要另抄成选项文字。
- 平行四边形符号写成 ▱（例如 ▱ABCD），不要写成 \\square、\\Box 或 □。
- 题号不要写进题干；分值（如"（15分）"）不要写。
- 小问 (1)(2)… 各起一行。
- 选择题把选项分别写在【A】【B】【C】【D】后面；不是选择题就不要写这四个标记。
- 看不清、无法确定的字写成 [?]，不要猜。
- 若图里还露出了别的题目的印刷内容（例如上一题的末尾或下一题的开头），不要誊录它；若看到了别的题号，写在【其他题号】里。"""

FIGURE_RULES = """图中蓝色框和编号标出的是候选配图。请在【配图】里逐个判断：
编号=题干（属于本题题干的印刷图）、编号=A/B/C/D（某个选项的印刷图）、
编号=第N题（印刷的图，但属于别的题，例如图下印着"第14题图"）、编号=无关（手写、草图、涂画）。
例如：1=题干, 2=第14题, 3=无关。没有蓝框就写"无"。
数轴、几何图、立体图、统计图、表格等只在【配图】里标为题干或 A/B/C/D；纯图片选项的文字标签必须留空，不得描述或重排图片内容。
如果原卷本题有印刷的图，却没有被任何蓝框框住，在【配图】末尾加上"缺图"。"""

OUTPUT_FORMAT = """只按下面的格式输出，不要输出别的内容：
【题号】印刷题号
【题型】单选题/多选题/填空题/解答题
【题干】
题干文字
【A】…
【B】…
【C】…
【D】…
【配图】…
【其他题号】没有就写"无\""""


def transcribe_prompt(number: int, with_figures: bool) -> str:
    parts = [TRANSCRIBE_RULES]
    if with_figures:
        parts.append(FIGURE_RULES)
    parts.append(OUTPUT_FORMAT if with_figures else OUTPUT_FORMAT.replace("【配图】…\n", ""))
    parts.append(f"这道题应当是第 {number} 题。")
    return "\n\n".join(parts)


def arbiter_prompt(number: int, first: dict, second: dict) -> str:
    def show(reading: dict) -> str:
        lines = [reading.get("stem", "")]
        for key in OPTION_KEYS:
            if (reading.get("options") or {}).get(key):
                lines.append(f"【{key}】{reading['options'][key]}")
        return "\n".join(lines)

    return "\n\n".join([
        TRANSCRIBE_RULES,
        f"这道题（第 {number} 题）已经被独立誊录了两次，两次有出入。请对照原图逐字核对，给出正确的誊录。"
        "两次都对的地方照抄；有出入的地方以原图印刷体为准。",
        f"【读法甲】\n{show(first)}",
        f"【读法乙】\n{show(second)}",
        "只按下面的格式输出正确结果，不要解释：\n【题干】\n…\n【A】…\n【B】…\n【C】…\n【D】…（不是选择题就不写选项）",
    ])


def locate_prompt(number: int) -> str:
    return (
        "图片左侧有红绿相间的编号刻度（01、02、…），每个编号对应它右侧的一条横带。"
        f"这张图里有几道印刷题目。请找出第 {number} 题的印刷题号（\"{number}.\"）所在的横带编号。"
        "手写的数字不算。\n只输出一行：【刻度】编号。找不到就输出：【刻度】无"
    )


# ---------------------------------------------------------------- 解析

def split_tags(text: str) -> dict[str, str]:
    text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S)
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    parts = TAG.split(text)
    result: dict[str, str] = {}
    for index in range(1, len(parts) - 1, 2):
        key = parts[index]
        value = parts[index + 1].strip().strip("*").strip()
        value = re.sub(r"^[：:]\s*", "", value).strip()
        if key not in result:
            result[key] = value
    return result


TYPE_NAMES = {"单选": "single_choice", "多选": "multiple_choice", "选择": "single_choice",
              "填空": "fill_blank", "解答": "free_response"}

# 视觉模型偶尔会把纯图片选项改写成无障碍式说明。这里只处理完整包裹、带冒号的
# 明确占位说明；“如图……”“图 1”或数学区间 [a,b] 等真实印刷文字不会命中。
FIGURE_DESCRIPTION = re.compile(
    r"^\s*(?:"
    r"\[\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*\S[\s\S]*\]|"
    r"【\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*\S[\s\S]*】|"
    r"[（(]\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*\S[\s\S]*[）)]"
    r")\s*[。.]?\s*$"
)

BRACKETED_FIGURE_DESCRIPTION = re.compile(
    r"(?:"
    # 同一行取到最后一个 ]，允许说明内部出现模型的不确定标记 [?]。
    r"\[\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：][^\r\n]*\]|"
    r"【\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*[^】]+】|"
    r"[（(]\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*[^）)]+[）)]"
    r")"
)
# 旧模型在数轴图片选项上还会省略“[图：]”，但使用非常固定的说明句式。
# 只接受包含至少三个数值/未知标记的完整“标号依次为”句，避免把“数轴上点 A
# 表示……”这类真正的印刷选项误删。
NUMBER_LINE_DESCRIPTION = re.compile(
    r"^\s*(?:一条)?数轴上(?:从左到右)?标有(?:若干个?)?点\s*[，,]?\s*标号依次为\s*"
    r"(?:\$?\s*[−-]?\s*(?:\d+(?:\.\d+)?|\[\?\])\s*\$?\s*[、，,]\s*){2,}"
    r"\$?\s*[−-]?\s*(?:\d+(?:\.\d+)?|\[\?\])\s*\$?\s*[。.]?\s*$"
)
MARKDOWN_TABLE_SEPARATOR = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$")


def is_figure_description(value: str) -> bool:
    """是否为证据充分的模型图片说明，而不是原卷上的选项文字。"""
    text = str(value or "")
    return bool(FIGURE_DESCRIPTION.fullmatch(text) or NUMBER_LINE_DESCRIPTION.fullmatch(text))


def strip_bracketed_figure_descriptions(value: str) -> tuple[str, bool]:
    """移除 AI 插入的 [图：……] 片段，保留同一字段里的真实文字。"""
    text = str(value or "")
    cleaned, count = BRACKETED_FIGURE_DESCRIPTION.subn("", text)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, bool(count)


def strip_markdown_tables(value: str) -> tuple[str, bool]:
    """移除视觉模型从原卷表格重排出的 Markdown 表；真实表格由裁图保留。"""
    lines = str(value or "").splitlines()
    kept: list[str] = []
    removed = False
    index = 0
    while index < len(lines):
        if (index + 1 < len(lines) and "|" in lines[index]
                and MARKDOWN_TABLE_SEPARATOR.fullmatch(lines[index + 1])):
            removed = True
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                index += 1
            continue
        kept.append(lines[index])
        index += 1
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return cleaned, removed


def parse_reading(text: str, number: int) -> dict:
    tags = split_tags(text)
    if "题干" not in tags:
        raise ValueError("缺少【题干】")
    options = {}
    figure_descriptions = []
    for key in OPTION_KEYS:
        value = fix_symbols(clean_option(tags.get(key, ""), key))
        value, described = strip_bracketed_figure_descriptions(value)
        if described:
            figure_descriptions.append(key)
        if value and value not in {"无", "…", "..."}:
            options[key] = value
    kind = "unknown"
    for word, value in TYPE_NAMES.items():
        if word in tags.get("题型", ""):
            kind = value
            break
    figures: dict[str, str] = {}
    figure_text = tags.get("配图", "")
    for label, role in re.findall(r"(\d{1,2})\s*[=＝:：]\s*(题干|无关|第\s*\d{1,3}\s*题|[A-DＡ-Ｄ])", figure_text):
        if role.startswith("第"):
            other = int(re.search(r"\d+", role).group(0))
            figures[label] = "stem" if other == number else f"q{other}"
        else:
            figures[label] = {"题干": "stem", "无关": "none"}.get(role, role.translate(str.maketrans("ＡＢＣＤ", "ABCD")))
    others = [int(v) for v in re.findall(r"\d{1,2}", tags.get("其他题号", "")) if int(v) != number]
    seen = re.findall(r"\d{1,2}", tags.get("题号", ""))
    stem = fix_symbols(clean_stem(tags["题干"], number))
    stem, stem_described = strip_bracketed_figure_descriptions(stem)
    if stem_described:
        figure_descriptions.append("stem")
    if options:
        # 模型偶尔把选项也写进题干末尾：从独占一行的"A."起截掉。
        cut = re.search(r"\n\s*A\s*[.．、:：]", stem)
        if cut:
            stem = stem[:cut.start()].rstrip()
    stem = drop_filled_choice(stem)
    return {
        "stem": stem,
        "options": options,
        "type": kind,
        "figures": figures,
        "missing_figure": "缺图" in figure_text,
        "others": sorted(set(others)),
        "number_seen": int(seen[0]) if seen else None,
        "figure_descriptions": figure_descriptions,
        "unclear": "[?]" in stem or any("[?]" in v for v in options.values()),
    }


FILLED_CHOICE = re.compile(r"(?<=[\u4e00-\u9fff\s，,。：:$=＝])([（(])\s*[A-DＡ-Ｄ]{1,4}\s*([)）])(?=\s*[。．.]?\s*$)")


def drop_filled_choice(stem: str) -> str:
    """题干末尾括号里的选项字母一定是学生手写的答案（印刷卷只会是空括号），清成空括号。

    只处理位于题干末尾、前面是汉字或标点的括号，所以 P(A) 这类数学记号不受影响。
    """
    return FILLED_CHOICE.sub(lambda m: "（　）" if m.group(1) == "（" else "(　)", stem)


def parse_band(text: str) -> int | None:
    tags = split_tags(text)
    match = re.search(r"\d{1,2}", tags.get("刻度", text))
    return int(match.group(0)) if match else None


# ---------------------------------------------------------------- 接口调用

def _minimax_url() -> str:
    raw = os.environ.get("MINIMAX_BASE_URL", "").strip() or MINIMAX_DEFAULT_URL
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.hostname not in MINIMAX_HOSTS:
        raise ReaderError("MiniMax 接口地址必须是官方 HTTPS 域名")
    return f"https://{parts.hostname}/v1/chat/completions"


def _post(url: str, key: str, payload: dict, timeout=(10, 150)) -> requests.Response:
    response = None
    for attempt in range(len(BACKOFF) + 1):
        try:
            with _IN_FLIGHT:
                response = requests.post(url, json=payload, timeout=timeout, allow_redirects=False,
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except (requests.Timeout, requests.ConnectionError):
            if attempt == len(BACKOFF):
                raise ReaderError("连接模型服务超时或中断") from None
            time.sleep(BACKOFF[attempt] * (0.8 + 0.4 * random.random()))
            continue
        if response.status_code not in RETRYABLE or attempt == len(BACKOFF):
            return response
        time.sleep(BACKOFF[attempt] * (0.8 + 0.4 * random.random()))
    return response


def chat(engine: Engine, prompt: str, image_urls: list[str], max_tokens: int = 3000) -> str:
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": url}} for url in image_urls
    ]
    messages = [{"role": "user", "content": content}]
    if engine.provider == "minimax":
        key = os.environ.get("MINIMAX_API_KEY", "").strip()
        name = "MiniMax"
        url = _minimax_url()
        payload = {"model": engine.model, "messages": messages, "temperature": 0, "stream": False,
                   "thinking": {"type": "disabled"}, "reasoning_split": True, "max_completion_tokens": max_tokens}
    else:
        key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
        name = "硅基流动"
        url = SILICONFLOW_URL
        payload = {"model": engine.model, "messages": messages, "temperature": 0, "stream": False,
                   "max_tokens": max_tokens}
    if not key or "\n" in key or "\r" in key:
        raise ReaderError(f"未配置 {name} 的密钥")
    response = _post(url, key, payload)
    if response.status_code in (401, 403):
        raise ReaderError(f"{name} 密钥无效、过期或没有该模型权限")
    if response.status_code != 200:
        raise ReaderError(f"{name} 接口返回 HTTP {response.status_code}")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ReaderError(f"{name} 返回内容过大")
    try:
        data = response.json()
        choice = data["choices"][0]
        text = choice["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ReaderError(f"{name} 返回格式不正确") from None
    if not isinstance(text, str) or not text.strip():
        raise ReaderError(f"{name} 没有返回内容")
    if choice.get("finish_reason") == "length":
        raise ReaderError(f"{name} 返回内容被截断")
    return text


def read_question(engine: Engine, image_url: str, number: int, with_figures: bool) -> dict:
    prompt = transcribe_prompt(number, with_figures)
    last_error = ""
    for _ in range(2):
        raw = chat(engine, prompt if not last_error else prompt + f"\n\n（上次输出不合格式：{last_error}。请严格按格式重写。）",
                   [image_url])
        try:
            reading = parse_reading(raw, number)
        except ValueError as error:
            last_error = str(error)
            continue
        reading["engine"] = engine.label
        reading["raw"] = raw[:6000]
        return reading
    raise ReaderError(f"{engine.label} 两次输出都不合格式")


def arbitrate(engine: Engine, image_url: str, number: int, first: dict, second: dict) -> dict:
    raw = chat(engine, arbiter_prompt(number, first, second), [image_url])
    reading = parse_reading(raw, number)
    reading["engine"] = engine.label
    reading["raw"] = raw[:6000]
    return reading


def locate_band(engine: Engine, image_url: str, number: int) -> int | None:
    return parse_band(chat(engine, locate_prompt(number), [image_url], max_tokens=200))
