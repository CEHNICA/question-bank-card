"""视觉模型读题：誊录、第二次独立识读、分歧裁决、缺号定位。

模型输出一律用【标签】分段的纯文本，不用 JSON——LaTeX 的反斜杠放进 JSON 字符串
经常被模型写坏（\\f、\\b 会被当成转义），纯文本就没有这个问题。
"""

from __future__ import annotations

import contextvars
import os
import random
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from contextlib import contextmanager
from urllib.parse import urlsplit

import requests

from . import preferences, provider_catalog
from .account_pool import (
    MINIMAX_PLANS, AccountPoolCancelled, AccountPoolError, account_pool, minimax_plan, provider_answered, provider_resting, rest_provider,
    secrets_from_environment,
)
from . import qtypes
from .textnorm import clean_option, clean_stem, fix_symbols, strip_type_label

MINIMAX_MODEL = preferences.DEFAULT_MODELS["minimax"]  # legacy public constant
MINIMAX_DEFAULT_URL = "https://api.minimax.cn/v1/chat/completions"
MINIMAX_HOSTS = frozenset({"api.minimax.cn", "api.minimax.io", "api.minimaxi.com"})
SILICONFLOW_URL = "https://api.siliconflow.cn/v1/chat/completions"
SILICONFLOW_MODEL = preferences.DEFAULT_MODELS["siliconflow"]  # legacy public constant
MODELSCOPE_URL = "https://api-inference.modelscope.cn/v1/chat/completions"
# 529 is MiniMax's "overloaded" answer (seen live during benchmarking); 520-524
# are CDN-edge failures.  All are transient server-side conditions.
SERVER_RETRYABLE = frozenset({500, 502, 503, 504, 520, 521, 522, 523, 524, 529})
BACKOFF = (2.0, 5.0, 12.0)
RATE_LIMIT_ROUNDS = 3
# With no other service to hand the question to, a busy free model is waited
# out longer before the card is given up on.
PATIENT_RATE_LIMIT_ROUNDS = 8
MAX_RESPONSE_BYTES = 200_000
OPTION_KEYS = ("A", "B", "C", "D", "E")
TAG = re.compile(r"【\s*(内容类型|题号|题型|题干|A|B|C|D|E|配图|其他题号|刻度)\s*】")


MAX_PARALLEL_CARDS = 16


def _parallel_limit() -> int:
    """How many cards are read at the same time (each card makes 1–3 calls)."""
    try:
        value = int(os.environ.get("QB_PARALLEL", "4"))
    except (TypeError, ValueError):
        value = 4
    return max(1, min(MAX_PARALLEL_CARDS, value))


# A process-wide safety cap on simultaneous HTTP requests.  Per-provider
# limits are enforced by the account pools; this only stops a runaway fan-out.
_IN_FLIGHT = threading.BoundedSemaphore(min(2 * MAX_PARALLEL_CARDS, 2 * _parallel_limit()))


class ReaderError(RuntimeError):
    """可以直接给用户看的失败原因（不含密钥、不含原始返回）。"""


class ReaderUnavailable(ReaderError):
    """The service could not answer now (rate limit, outage, no working key).

    Another configured service may still answer: ``chat`` then tries it.
    """


class ReaderQuotaExhausted(ReaderUnavailable):
    """The provider confirmed a non-transient plan quota exhaustion.

    This is deliberately separate from an ordinary HTTP 429.  Callers use it
    to pause a whole paper instead of turning every queued card red.  The
    exception message is fixed and never contains the provider response body.
    """


class ReaderRequestStopped(ReaderError):
    """A bounded interactive read expired or was locally cancelled."""


_REQUEST_LIMITS = contextvars.ContextVar("qb_reader_request_limits", default=None)
_SELECTED_SERVICES_ONLY = contextvars.ContextVar("qb_selected_services_only", default=False)
_SPECULATIVE_DUPLICATES = contextvars.ContextVar("qb_speculative_duplicates", default=True)


@contextmanager
def without_speculative_duplicates():
    """Single-reading tasks keep normal timeouts/recovery, without a hedge."""
    token = _SPECULATIVE_DUPLICATES.set(False)
    try:
        yield
    finally:
        _SPECULATIVE_DUPLICATES.reset(token)


@contextmanager
def selected_services_only(enabled: bool = True):
    """Auto imports may use their configured reader roles, never new fallbacks."""
    token = _SELECTED_SERVICES_ONLY.set(_SELECTED_SERVICES_ONLY.get() or enabled)
    try:
        yield
    finally:
        _SELECTED_SERVICES_ONLY.reset(token)


@contextmanager
def cancellable_request(cancel):
    """Add local cancellation without changing a whole-card request policy."""
    token = _REQUEST_LIMITS.set({"deadline": float("inf"), "cancel": cancel, "cancel_only": True})
    try:
        _check_request()
        yield
    finally:
        _REQUEST_LIMITS.reset(token)


@contextmanager
def bounded_request(seconds: float, *, cancel=None):
    """Interactive reads use the chosen service only, with one total budget."""
    token = _REQUEST_LIMITS.set({"deadline": time.monotonic() + max(0, seconds), "cancel": cancel})
    try:
        _check_request()
        yield
    finally:
        _REQUEST_LIMITS.reset(token)


def _request_cancelled() -> bool:
    limits = _REQUEST_LIMITS.get()
    return limits is not None and (time.monotonic() >= limits["deadline"]
        or (limits["cancel"] is not None and limits["cancel"]()))


def _check_request() -> None:
    if _request_cancelled():
        raise ReaderRequestStopped("本次框选识读已取消或超过等待时限；原题未修改，可重新框选或手动改字")


def _reader_pause(seconds: float) -> None:
    if _REQUEST_LIMITS.get() is None:
        time.sleep(seconds)
        return
    remaining = seconds
    while remaining > 0:
        _check_request()
        duration = min(remaining, 0.25)
        time.sleep(duration)
        remaining -= duration
    _check_request()


@contextmanager
def _http_slot():
    if _REQUEST_LIMITS.get() is None:
        with _IN_FLIGHT:
            yield
        return
    while True:
        _check_request()
        if _IN_FLIGHT.acquire(timeout=0.25):
            break
    try:
        _check_request()
        yield
    finally:
        _IN_FLIGHT.release()


@dataclass(frozen=True)
class Engine:
    provider: str   # a provider_catalog.VISION key: minimax | modelscope | siliconflow
    model: str

    @property
    def label(self) -> str:
        return self.model.split("/")[-1]

    @property
    def key(self) -> str:
        # Engine keys are stable provider slots.  The concrete model ID is a
        # separate preference and may change without breaking stored roles.
        spec = provider_catalog.VISION.get(self.provider)
        return spec["engine"] if spec else f"{self.provider}:{self.model}"

    @property
    def vendor(self) -> str:
        spec = provider_catalog.VISION.get(self.provider)
        return spec["label"] if spec else self.provider


def configured(service: str) -> bool:
    """网页进程拿不到密钥，只拿到"已配置"的标记；工作者进程拿到真正的密钥。"""
    if os.environ.get(f"QB_{service.upper()}_CONFIGURED") == "1":
        return True
    try:
        return bool(secrets_from_environment(service))
    except AccountPoolError:
        return False


def _reported_pool_size(service: str) -> int:
    try:
        return max(0, min(8, int(os.environ.get(f"QB_{service.upper()}_POOL_SIZE", "0"))))
    except (TypeError, ValueError):
        return 0


ENGINE_CHOICES = dict(provider_catalog.ENGINES)
ASSISTANT = provider_catalog.ASSISTANT_ENGINE


def provider_model(provider: str, configuration: dict | None = None) -> str:
    """Read the current task snapshot from the environment with safe fallback."""
    environment_key = provider_catalog.model_environment(provider)
    if configuration is not None:
        models = configuration.get("models") if isinstance(configuration, dict) else None
        value = models.get(provider) if isinstance(models, dict) else None
        value = value or preferences.DEFAULT_MODELS[provider]
    else:
        value = os.environ.get(environment_key, preferences.DEFAULT_MODELS[provider])
    normalized = preferences.normalize_models({provider: value})
    return normalized[provider] if normalized is not None else preferences.DEFAULT_MODELS[provider]


def _selected(name: str, default: str, allowed: set[str]) -> str:
    value = os.environ.get(name, default).strip().lower()
    return value if value in allowed else default


def _configured_selection(
    configuration: dict | None, role: str, environment_name: str, default: str, allowed: set[str],
) -> str:
    if configuration is None:
        return _selected(environment_name, default, allowed)
    roles = configuration.get("roles") if isinstance(configuration, dict) else None
    value = roles.get(role) if isinstance(roles, dict) else None
    return value if value in allowed else default


def engine_by_key(key: str, configuration: dict | None = None) -> Engine | None:
    provider = ENGINE_CHOICES.get(key)
    if provider is None:
        return None
    return Engine(provider, provider_model(provider, configuration)) if configured(provider) else None


def _primary_selection(configuration: dict | None = None) -> str:
    return _configured_selection(
        configuration, "primary_engine", "QB_PRIMARY_ENGINE", "minimax_m3", {ASSISTANT, *ENGINE_CHOICES},
    )


def assistant_mode(configuration: dict | None = None) -> bool:
    """No vision model: cards start as MinerU's text and an AI assistant checks them."""
    return _primary_selection(configuration) == ASSISTANT


def _first_configured(order, configuration: dict | None = None, *, skip: str = "") -> Engine | None:
    for provider in order:
        if provider != skip and configured(provider):
            return Engine(provider, provider_model(provider, configuration))
    return None


def primary_readiness(configuration: dict | None = None) -> dict:
    """Why the primary reader can or cannot be used, in one place.

    The entry points and the worker used to answer this question separately:
    the entry point fell back to any configured service while the worker, under
    ``selected_services_only``, refused. That is how a photo could be reported
    as readable and then fail with “没有配置所选主读模型的 API Key”. Both now ask
    here, and the answer says which service was actually chosen and whether a
    usable fallback exists.

    ``selected_services_only`` guards *the request*, not the *choice*. A
    configured service that is already answering must not be swapped out when
    its call fails — that is the case that costs a second fee and can leave a
    card half-read.  A service that was never usable at all is a different
    problem: the teacher filled 魔搭 and asked for a photo to be read, and
    refusing to read it because the *selected* service has no key helps nobody.
    So a missing credential falls back to a configured service even in a
    selected-only round, and ``used`` names who will actually read.
    """
    selected = _primary_selection(configuration)
    label = _service_label(selected)
    if selected == ASSISTANT:
        return {"ready": False, "reason": "assistant_mode", "selected": selected, "label": label,
                "engine": None, "used": "", "fallback_available": False}
    chosen = engine_by_key(selected, configuration)
    if chosen is not None:
        return {"ready": True, "reason": "", "selected": selected, "label": chosen.label,
                "engine": chosen, "used": chosen.provider, "fallback_available": False}
    fallback = _first_configured(provider_catalog.PRIMARY_ORDER, configuration)
    return {"ready": fallback is not None,
            "reason": "" if fallback is not None else "none_configured",
            "selected": selected, "label": label, "engine": fallback,
            "used": fallback.provider if fallback is not None else "",
            # The page shows the service that will really read, so a switch the
            # user did not type is visible rather than silent.
            "fallback_available": fallback is not None and fallback.provider != selected}


def _service_label(key: str) -> str:
    """The provider name a teacher would recognise, for the message they read.

    “所选主读模型” told them something was wrong but not which key to paste, so
    they had to open the settings and compare lists themselves.
    """
    if key == ASSISTANT:
        return "AI 助手读题"
    provider = ENGINE_CHOICES.get(key)
    spec = provider_catalog.VISION.get(provider) if provider else None
    return str(spec["label"]) if spec else (provider or key)


def primary_engine(configuration: dict | None = None) -> Engine | None:
    """The first reader.  When the chosen service has no key, the first one in
    provider_catalog.PRIMARY_ORDER that has a key reads instead, so a teacher
    who only filled the free 魔搭 key never meets “没有配置主读模型”."""
    return primary_readiness(configuration)["engine"]


def checker_engine(configuration: dict | None = None) -> Engine | None:
    """第二位读者：优先用另一家（独立复核），没有就用主读的模型再独立读一遍。"""
    if assistant_mode(configuration):
        return None
    selected = _configured_selection(
        configuration, "checker_engine", "QB_CHECKER_ENGINE", "auto", {"auto", *ENGINE_CHOICES},
    )
    if selected != "auto":
        return engine_by_key(selected, configuration)
    primary = primary_engine(configuration)
    # “自动”按提供商选择另一家，而不是写死某一家。
    other = _first_configured(provider_catalog.CHECKER_ORDER, configuration,
                              skip=primary.provider if primary is not None else "")
    return other or (None if _SELECTED_SERVICES_ONLY.get() else primary)


def arbiter_engine(
    primary: Engine | None = None, checker: Engine | None = None, configuration: dict | None = None,
) -> Engine | None:
    """分歧裁决模型；默认沿用主读，也可显式选择复核或某一已配置引擎。"""
    selected = _configured_selection(
        configuration, "arbiter_engine", "QB_ARBITER_ENGINE", "primary",
        {"primary", "checker", *ENGINE_CHOICES},
    )
    if selected == "primary":
        return primary if primary is not None else primary_engine(configuration)
    if selected == "checker":
        return checker if checker is not None else checker_engine(configuration)
    return engine_by_key(selected, configuration)


def engine_settings(configuration: dict | None = None) -> dict:
    """给本机设置页的非秘密模型信息。"""
    selected = {
        "primary": _primary_selection(configuration),
        "checker": _configured_selection(
            configuration, "checker_engine", "QB_CHECKER_ENGINE", "auto", {"auto", *ENGINE_CHOICES},
        ),
        "arbiter": _configured_selection(
            configuration, "arbiter_engine", "QB_ARBITER_ENGINE", "primary",
            {"primary", "checker", *ENGINE_CHOICES},
        ),
    }
    primary = primary_engine(configuration)
    checker = checker_engine(configuration)
    arbiter = arbiter_engine(primary, checker, configuration)
    models = {provider: provider_model(provider, configuration) for provider in preferences.DEFAULT_MODELS}
    if isinstance(configuration, dict):
        plans = preferences.normalize_plans(configuration.get("plans")) or dict(preferences.DEFAULT_PLANS)
    else:
        plans = {"minimax": minimax_plan()}
    return {
        "selected": selected,
        "models": models,
        "plans": plans,
        # (start, ceiling) of simultaneous requests per MiniMax key for each plan.
        "plan_concurrency": {key: list(value) for key, value in MINIMAX_PLANS.items()},
        "primary": primary.key if primary else None,
        "checker": checker.key if checker else None,
        "arbiter": arbiter.key if arbiter else None,
        "assistant": assistant_mode(configuration),
        "configured": {provider: configured(provider) for provider in provider_catalog.VISION},
        "pool_sizes": {provider: _reported_pool_size(provider) for provider in provider_catalog.VISION},
        "choices": [
            {"key": spec["engine"], "provider": spec["label"], "provider_key": provider,
             "model": models[provider], "available": configured(provider),
             "free": models[provider] in spec["free_models"], "note": spec["note"]}
            for provider, spec in provider_catalog.VISION.items()
        ],
        "suggested_models": preferences.SUGGESTED_MODELS,
        "free_models": preferences.FREE_MODELS,
        "signup": {"mineru": provider_catalog.MINERU["signup"],
                   **{provider: spec["signup"] for provider, spec in provider_catalog.VISION.items()}},
    }


# ---------------------------------------------------------------- 提示词

TRANSCRIBE_RULES = """你是数学资料誊录员。图片是从数学试卷或教材中裁下的一段候选内容；若由几段拼成，灰色横线是拼接处，按从上到下的顺序阅读。
只誊录印刷体内容：
- 学生的手写字、批改符号、圈画、划线、草稿一律忽略；括号或横线里手写填的答案不要写，保留空括号（ ）或横线 ____。
- 数学式用 LaTeX，行内公式用 $...$ 包住；中文和中文标点照原卷。
- 数轴、函数图象、平面/立体几何图（包括棱柱）、统计图、流程图等视觉内容不得改写成“[图：……]”“图片中……”或其他文字说明，也不要重排成字符图或项目列表。只誊录图外真正印刷的题干文字。
- 格子里只有文字、数字和公式的表格（数据表、列联表、分布列、填空用的空表等）是题目文字：在题干里它印的位置，按 Markdown 表格逐格照抄。每行一行，格子用 | 隔开，第一行后面加一行 |---|---|；空格子就空着，不要填；格子里的公式照样用 $...$。被分页或拼接处切成两截的同一张表，接起来写成一张表。有合并单元格的表写成 HTML：<table><tr><td rowspan="2">…</td>…</tr></table>。表格里画着图形的，仍当作配图。
- 某个选项只有图时，对应的【A】【B】【C】【D】（或【E】）留空；图内的数字、字母和刻度仍属于配图，不要另抄成选项文字。
- 逐字照抄印刷内容：原卷有错字、漏字、语句不通、字母顺序或大小写特别（如 FE、边长为 C）、人名书名与常识不符时也原样照抄，不要改正、补字、删字或调换顺序。
- 原卷印的是平行四边形符号时写成 ▱（例如 ▱ABCD），不要写成 \\square、\\Box 或 □；原卷印的是汉字“平行四边形”就照写汉字。
- 题号不要写进题干，教材里“例1”“例题2”这类例题标号也不要写进题干；分值（如"（15分）"）不要写。
- 小问 (1)(2)… 各起一行。
- 选择题把选项分别写在【A】【B】【C】【D】后面，原卷印了 E 选项就再写【E】；不是选择题就不要写这些标记。
- 题干开头印的“（多项选择题）”“（多选）”“（单选题）”这类题型标注不要写进题干，写在【题型】里。
- 看不清、无法确定的字写成 [?]，不要猜。
- 若图里还露出了别的题目的印刷内容（例如上一题的末尾或下一题的开头），不要誊录它；若看到了别的题号，写在【其他题号】里。
- 独立判断候选内容的性质，不要因为程序提供了候选编号就把教材小标题或讲解正文硬说成题目：
  “例1/例题2”开头的是例题；练习、习题中的作答任务是练习题；概念说明、性质讲解等是教材正文；只有章节或小节名称的是标题；确实无法确定才写不确定。"""

FIGURE_RULES = """图中蓝色框和编号标出的是候选配图。请在【配图】里逐个判断：
编号=题干（属于本题题干的印刷图）、编号=A/B/C/D/E（某个选项的印刷图）、
编号=第N题（印刷的图，但属于别的题，例如图下印着"第14题图"）、编号=无关（手写、草图、涂画）、
编号=表格（这是一张只有文字和数字的表格，已经按上面的规则写进题干）。
例如：1=题干, 2=第14题, 3=无关, 4=表格。没有蓝框就写"无"。
数轴、几何图、立体图、统计图等只在【配图】里标为题干或 A/B/C/D/E；纯图片选项的文字标签必须留空，不得描述或重排图片内容。
如果原卷本题有印刷的图，却没有被任何蓝框框住，在【配图】末尾加上"缺图"。
几张图并排时，逐张核对图中的字母、数字标注是否与本题题干提到的点、线、数据一致；对不上的图属于别的题，写“无关”。"""

OUTPUT_FORMAT = """只按下面的格式输出，不要输出别的内容：
【内容类型】例题/练习题/教材正文/标题/不确定
【题号】印刷题号
【题型】单选题/多选题/填空题/判断题/解答题
【题干】
题干文字
【A】…
【B】…
【C】…
【D】…
【E】…（原卷没有 E 选项就不写这一行）
【配图】…
【其他题号】没有就写"无\""""


def transcribe_prompt(number: int, with_figures: bool, source_kind: str = "unknown") -> str:
    parts = [TRANSCRIBE_RULES]
    if with_figures:
        parts.append(FIGURE_RULES)
    parts.append(OUTPUT_FORMAT if with_figures else OUTPUT_FORMAT.replace("【配图】…\n", ""))
    expected = {
        "example": "本地版面规则检测到“例N/例题N”起点；请核对它是否确为例题。",
        "exercise": "本地版面规则检测到练习或习题中的题目起点；请核对它是否确为练习题。",
        "manual": "这段范围由人手工框出；请按原图独立判断内容类型。",
    }.get(source_kind, "这是程序切出的候选范围；它也可能是教材正文或标题，请独立判断。")
    parts.append(f"候选显示编号为 {number}。{expected}不要为了迎合候选编号而虚构题目性质或印刷题号。")
    return "\n\n".join(parts)


def arbiter_prompt(number: int, first: dict, second: dict, witness: str = "") -> str:
    def show(reading: dict) -> str:
        lines = [reading.get("stem", "")]
        for key in OPTION_KEYS:
            if (reading.get("options") or {}).get(key):
                lines.append(f"【{key}】{reading['options'][key]}")
        return "\n".join(lines)

    return "\n\n".join([
        TRANSCRIBE_RULES,
        f"这段候选内容（显示编号 {number}）已经被独立誊录了两次，两次有出入。请对照原图逐字核对，给出正确的誊录和内容类型。"
        "两次都对的地方照抄；有出入的地方以原图印刷体为准。",
        f"【读法甲】\n{show(first)}",
        f"【读法乙】\n{show(second)}",
        *([
            "【另一识别引擎的文字】（MinerU 按版面识别，可能混入手写、漏字或把公式写乱，只作参考；"
            "有出入处以原图印刷体为准，但它常能帮你发现两种读法里被“改正”过的字）\n"
            + witness.strip()[:1500]
        ] if witness and witness.strip() else []),
        "只按下面的格式输出正确结果，不要解释：\n【内容类型】例题/练习题/教材正文/标题/不确定"
        "\n【题干】\n…\n【A】…\n【B】…\n【C】…\n【D】…\n【E】…（原卷有 E 选项才写；不是选择题就不写选项）",
    ])


def spot_check_prompt(spots: list[dict]) -> tuple[str, list[dict]]:
    """An either/or question per disputed spot, neither side marked as ours.

    Presented as “the first transcription” versus “another engine”, the model
    kept its own reading every time in testing; asked neutrally which of two
    spellings is printed, it found the misread 需用/需要 and x²/x³.
    """
    order = []
    lines = []
    for index, spot in enumerate(spots, 1):
        # A stable, content-dependent side so neither engine is always 甲.
        reading_first = sum(map(ord, spot["before"] + spot["after"] + str(index))) % 2 == 0
        first, second = (spot["reading"], spot["mineru"]) if reading_first else (spot["mineru"], spot["reading"])
        order.append({"甲": "reading" if reading_first else "mineru", "乙": "mineru" if reading_first else "reading"})
        lines.append(f"第{index}处：…{spot['before']}＿{spot['after']}…　甲：{first}　乙：{second}")
    prompt = (
        "请只看图片中的印刷体（忽略手写和涂画），判断下面每一处空位上印的是甲还是乙。"
        "文字已去掉空格、标点和 LaTeX 写法，只比较字符本身。两种写法都可能是对的，请放大看清，"
        "不要根据常识、上下文或哪种更通顺来猜。\n"
        + "\n".join(lines)
        + "\n\n每处一行，只写序号和甲或乙，例如“1=乙”。看不清就写“1=不确定”。"
    )
    return prompt, order


def parse_spot_answers(raw: str, order: list[dict]) -> list[str | None]:
    text = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S)
    answers: list[str | None] = [None] * len(order)
    for match in re.finditer(r"(?:第\s*)?(\d{1,2})\s*处?\s*[=＝:：]\s*(甲|乙)", text):
        index = int(match.group(1)) - 1
        if 0 <= index < len(order) and answers[index] is None:
            answers[index] = order[index][match.group(2)]
    return answers


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


# 读题模型写的【题型】按共用词表认（见 qtypes.TYPE_WORDS）：“计算题”“证明题”也是解答题。
TYPE_NAMES = dict(qtypes.TYPE_WORDS)
CONTENT_KIND_NAMES = {
    "例题": "example",
    "练习题": "exercise",
    "习题": "exercise",
    "题目": "exercise",
    "教材正文": "prose",
    "正文": "prose",
    "标题": "heading",
    "不确定": "unknown",
}

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
    r"[（(]\s*(?:图|图片|图形|图示|示意图|表|表格)\s*[:：]\s*[^）)]+[）)]|"
    # 不带冒号的占位说明：“（原卷此处为配图，无印刷文字）”“（此处为图片）”“（无印刷文字）”。
    # 第 11 题 A 选项被手写的 × 盖住，裁决把它写成了这样一句，看着像读出了选项。
    r"[（(\[【]\s*(?:原卷|原题|该选项|本选项)?\s*(?:此处|这里)?\s*(?:为|是|只有|仅有|仅为)\s*"
    r"(?:配图|图片|图形|图像|示意图)\s*(?:[，,；;]\s*[^（()）\[\]【】\r\n]{0,12})?[）)\]】]|"
    r"[（(\[【]\s*(?:此处|这里)?\s*无(?:印刷)?文字\s*[）)\]】]"
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
    kind = qtypes.from_words(tags.get("题型", ""))
    content_kind = "unknown"
    content_kind_text = tags.get("内容类型", "")
    for word, value in CONTENT_KIND_NAMES.items():
        if word in content_kind_text:
            content_kind = value
            break
    figures: dict[str, str] = {}
    figure_text = tags.get("配图", "")
    for label, role in re.findall(r"(\d{1,2})\s*[=＝:：]\s*(题干|无关|表格|第\s*\d{1,3}\s*题|[A-EＡ-Ｅ])", figure_text):
        if role.startswith("第"):
            other = int(re.search(r"\d+", role).group(0))
            figures[label] = "stem" if other == number else f"q{other}"
        else:
            figures[label] = {"题干": "stem", "无关": "none", "表格": "table"}.get(role, role.translate(str.maketrans("ＡＢＣＤＥ", "ABCDE")))
    others = [int(v) for v in re.findall(r"\d{1,2}", tags.get("其他题号", "")) if int(v) != number]
    seen = re.findall(r"\d{1,2}", tags.get("题号", ""))
    stem = fix_symbols(clean_stem(tags["题干"], number))
    stem, labelled_kind = strip_type_label(stem)
    stem = stem.strip()
    if labelled_kind:
        # The paper's own “（多项选择题）” outranks the model's guess.
        kind = labelled_kind
    stem, stem_described = strip_bracketed_figure_descriptions(stem)
    if stem_described:
        figure_descriptions.append("stem")
    if options:
        # 模型偶尔把选项也写进题干末尾：从独占一行的"A."起截掉。
        cut = re.search(r"\n\s*A\s*[.．、:：]", stem)
        if cut:
            stem = stem[:cut.start()].rstrip()
    elif kind in {"single_choice", "multiple_choice"}:
        # 另一种写法：选项全写在【题干】里（“…（ ）A. √23 B. √5 C. √26 D. √11”），
        # 【A】–【D】为空。拆出来，否则两次识读只因写法不同而“不一致”。
        stem, options = split_inline_options(stem)
    stem = drop_filled_choice(stem)
    return {
        "stem": stem,
        "options": options,
        "type": kind,
        "content_kind": content_kind,
        "figures": figures,
        "missing_figure": "缺图" in figure_text,
        "others": sorted(set(others)),
        "number_seen": int(seen[0]) if seen else None,
        "figure_descriptions": figure_descriptions,
        "unclear": "[?]" in stem or any("[?]" in v for v in options.values()),
    }


_INLINE_OPTIONS = re.compile(
    r"(?:(?<=[\s)）　])|^)A\s*[.．、]\s*(?P<A>.+?)\s+B\s*[.．、]\s*(?P<B>.+?)\s+"
    r"C\s*[.．、]\s*(?P<C>.+?)\s+D\s*[.．、]\s*(?P<D>.+?)"
    r"(?:\s+E\s*[.．、]\s*(?P<E>.+?))?\s*$",
    re.S,
)


def split_inline_options(stem: str) -> tuple[str, dict[str, str]]:
    """“…（ ）A. 2 B. 3 C. 4 D. 5” → (“…（ ）”, {A: 2, …}); unchanged when not all four are there."""
    match = _INLINE_OPTIONS.search(stem)
    if not match or not match.start():
        return stem, {}
    options = {key: fix_symbols(match.group(key).strip()) for key in ("A", "B", "C", "D")}
    if not all(options.values()):
        return stem, {}
    if match.group("E"):
        options["E"] = fix_symbols(match.group("E").strip())
    return stem[:match.start()].rstrip(), options


FILLED_CHOICE = re.compile(r"(?<=[\u4e00-\u9fff\s，,。：:$=＝])([（(])\s*[A-EＡ-Ｅ]{1,5}\s*([)）])(?=\s*[。．.]?\s*$)")


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
        _check_request()
        try:
            with _http_slot():
                limits = _REQUEST_LIMITS.get()
                actual_timeout = timeout
                if limits is not None and not limits.get("cancel_only"):
                    remaining = max(0.1, limits["deadline"] - time.monotonic())
                    actual_timeout = (min(timeout[0], remaining), min(timeout[1], remaining, 30))
                response = requests.post(url, json=payload, timeout=actual_timeout, allow_redirects=False,
                                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        except (requests.Timeout, requests.ConnectionError):
            if attempt == len(BACKOFF):
                raise ReaderUnavailable("连接模型服务超时或中断") from None
            _reader_pause(BACKOFF[attempt] * (0.8 + 0.4 * random.random()))
            continue
        # 429 belongs to one account, not the whole provider.  Return it at
        # once so ``chat`` can cool down that account and lease another one.
        _check_request()
        if response.status_code not in SERVER_RETRYABLE or attempt == len(BACKOFF):
            return response
        _reader_pause(BACKOFF[attempt] * (0.8 + 0.4 * random.random()))
    return response


def _retry_after_seconds(value: object, *, fallback: float | None = None) -> float:
    """Return a bounded rate-limit delay without ever reflecting header text.

    MiniMax currently sends delta seconds.  Invalid, missing, or deliberately
    huge values fall back to the caller's bounded exponential delay.
    """

    text = value.strip() if isinstance(value, str) else ""
    if not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,2})?", text):
        return BACKOFF[-1] if fallback is None else min(60.0, max(0.0, float(fallback)))
    return min(60.0, float(text))


TOKEN_PLAN_EXHAUSTED_CODE = re.compile(r"\(2056\)\s*$")
TOKEN_PLAN_EXHAUSTED_MESSAGE = "MiniMax Token Plan 额度已用尽；补充额度后点“重试”即可续跑"


def _minimax_token_plan_exhausted(response: requests.Response) -> bool:
    """Recognise MiniMax's structured, non-transient Token Plan exhaustion.

    MiniMax currently returns HTTP 429 with ``type=error`` and a nested
    ``rate_limit_error``.  Its numeric code is present only as the final token
    in the message.  Match that code only after validating the surrounding
    structure, and never return or log the provider message itself.
    """

    if response.status_code != 429 or len(response.content) > 64_000:
        return False
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict) or payload.get("type") != "error":
        return False
    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    if str(error.get("http_code", "")).strip() != "429" or error.get("type") != "rate_limit_error":
        return False
    message = error.get("message")
    return isinstance(message, str) and TOKEN_PLAN_EXHAUSTED_CODE.search(message) is not None


def _hedge_after() -> float:
    """Seconds before a straggling request gets a duplicate (0 disables)."""
    try:
        value = float(os.environ.get("QB_HEDGE_AFTER", "18"))
    except (TypeError, ValueError):
        value = 18.0
    return value if value > 0 else 0.0


_HEDGE_EXECUTOR = ThreadPoolExecutor(max_workers=64, thread_name_prefix="qb-reader")


# The engine that actually answered the last ``chat`` in this context (a
# fallback service may have stood in for the chosen one).
_ANSWERED_BY: contextvars.ContextVar[Engine | None] = contextvars.ContextVar("qb_answered_by", default=None)


def answered_by(default: Engine) -> Engine:
    return _ANSWERED_BY.get() or default


def _fallback_enabled() -> bool:
    return not _SELECTED_SERVICES_ONLY.get() and os.environ.get("QB_PROVIDER_FALLBACK", "1").strip() != "0"


def fallback_engines(engine: Engine) -> list[Engine]:
    """Other services with a key, in provider_catalog.CHECKER_ORDER."""
    return [Engine(provider, provider_model(provider)) for provider in provider_catalog.CHECKER_ORDER
            if provider != engine.provider and configured(provider)]


def chat(engine: Engine, prompt: str, image_urls: list[str], max_tokens: int = 3000) -> str:
    """One model answer.  When the service is unavailable (persistent rate
    limits, an outage, a used-up free quota, no working key), the other
    configured services answer instead, so a free tier running dry does not
    turn the rest of the paper red.  A content error (HTTP 400…) is not retried
    elsewhere.  ``QB_PROVIDER_FALLBACK=0`` turns this off."""
    _ANSWERED_BY.set(None)
    limits = _REQUEST_LIMITS.get()
    if limits is not None and not limits.get("cancel_only"):
        # A person chose this reader for one small crop. Neither duplicate
        # hedges nor another configured/paid service are implicitly authorised.
        _check_request()
        text = _chat_once(engine, prompt, image_urls, max_tokens)
        _check_request()
        _ANSWERED_BY.set(engine)
        return text
    if not _fallback_enabled():
        text = _chat_hedged(engine, prompt, image_urls, max_tokens)
        _ANSWERED_BY.set(engine)
        return text
    # A service that just failed rests a few minutes (account_pool.rest_provider)
    # and is not asked meanwhile: the rest of the paper goes to another service,
    # or, when there is none (only 魔搭, its free quota used up), fails at once
    # so the card starts as MinerU's draft instead of sitting through retries.
    candidates = [engine, *fallback_engines(engine)]
    errors: dict[str, ReaderUnavailable] = {}
    for candidate in candidates:
        if provider_resting(candidate.provider) and candidate.provider in _REST_ERRORS:
            last = _REST_ERRORS[candidate.provider]
            errors[candidate.provider] = type(last)(str(last))
            continue
        try:
            text = _chat_hedged(candidate, prompt, image_urls, max_tokens)
        except ReaderUnavailable as error:
            errors[candidate.provider] = error
            _REST_ERRORS[candidate.provider] = error
            rest_provider(candidate.provider)
            continue
        provider_answered(candidate.provider)
        _ANSWERED_BY.set(candidate)
        return text
    # Nobody could answer: report the asked service's reason (a used-up
    # MiniMax plan pauses the paper, see pipeline).
    raise errors.get(engine.provider) or next(iter(errors.values()))


# Why each resting service last failed, so a skipped call reports the same.
_REST_ERRORS: dict[str, ReaderUnavailable] = {}


def _chat_hedged(engine: Engine, prompt: str, image_urls: list[str], max_tokens: int = 3000) -> str:
    """One model answer, with a duplicate request for rare stragglers.

    Measured on MiniMax: median 4.3 s, p90 ~8 s, but about one call in thirty
    stalls for 40–65 s while producing ~100 tokens, and a single straggler
    holds up the whole paper.  After ``QB_HEDGE_AFTER`` seconds a second,
    identical request is sent and whichever answers first is used.  At
    temperature 0 both answers are equivalent, so this changes latency only.
    """
    delay = _hedge_after() if _SPECULATIVE_DUPLICATES.get() else 0
    if not delay:
        return _chat_once(engine, prompt, image_urls, max_tokens)
    started = threading.Event()
    first = _HEDGE_EXECUTOR.submit(contextvars.copy_context().run, _chat_once, engine, prompt,
                                   image_urls, max_tokens, started)
    # The clock starts when the request is on the wire.  Counting the time it
    # waited for a free account slot duplicated requests that were merely
    # queued; the duplicates took the slots, and a 30-page book ran with 20
    # requests waiting behind one.
    while not started.wait(0.5):
        if first.done():
            return first.result()
    try:
        return first.result(timeout=delay)
    except FutureTimeout:
        pass
    if not _has_spare_slot(engine):
        return first.result()
    second = _HEDGE_EXECUTOR.submit(contextvars.copy_context().run, _chat_once, engine, prompt,
                                    image_urls, max_tokens)
    pending = {first, second}
    failure: BaseException | None = None
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            error = future.exception()
            if error is None:
                return future.result()
            failure = failure or error
    assert failure is not None
    raise failure


def _request(engine: Engine, messages: list[dict], max_tokens: int) -> tuple[str, dict, tuple[str, ...]]:
    """(URL, payload, optional payload keys) for one service.

    Every service is asked for plain, deterministic answers without a
    reasoning phase: thinking roughly triples the time and the free quota a
    transcription takes, and it does not make the copy more faithful.
    """
    base = {"model": engine.model, "messages": messages, "stream": False}
    if engine.provider == "minimax":
        return _minimax_url(), {**base, "temperature": 0, "thinking": {"type": "disabled"}, "reasoning_split": True,
                                "max_completion_tokens": max_tokens}, ()
    if engine.provider == "modelscope":
        return MODELSCOPE_URL, {**base, "temperature": 0, "max_tokens": max_tokens,
                                "enable_thinking": False}, ("enable_thinking",)
    return SILICONFLOW_URL, {**base, "temperature": 0, "max_tokens": max_tokens}, ()


_OUTPUT_LIMITS: dict[tuple[str, str], int] = {}
_OUTPUT_LIMIT = re.compile(r"max_(?:completion_)?tokens[^\[\]]{0,40}\[\s*\d+\s*,\s*(\d+)\s*\]", re.I)


def _output_limit(response) -> int | None:
    """The largest max_tokens a service accepts, when its 400 says so
    (e.g. “max_tokens参数非法：限制数值范围[1,1024]”)."""
    try:
        text = response.text[:2000]
    except (AttributeError, TypeError, ValueError):
        return None
    match = _OUTPUT_LIMIT.search(text or "")
    return int(match.group(1)) if match and int(match.group(1)) > 0 else None


def _max_tokens(payload: dict) -> int:
    return int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 0)


def _with_max_tokens(payload: dict, limit: int) -> dict:
    key = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
    return {**payload, key: min(_max_tokens(payload) or limit, limit)}


def _has_spare_slot(engine: Engine) -> bool:
    try:
        return account_pool(engine.provider).spare > 0
    except AccountPoolError:
        return False


def _chat_once(engine: Engine, prompt: str, image_urls: list[str], max_tokens: int = 3000,
               started: threading.Event | None = None) -> str:
    content = [{"type": "text", "text": prompt}] + [
        {"type": "image_url", "image_url": {"url": url}} for url in image_urls
    ]
    messages = [{"role": "user", "content": content}]
    name = engine.vendor
    url, payload, optional = _request(engine, messages, max_tokens)
    if (known := _OUTPUT_LIMITS.get((engine.provider, engine.model))) is not None:
        payload = _with_max_tokens(payload, known)
    # Free services change their parameters often.  When one answers HTTP 400,
    # retry without the optional switches.
    variants = [payload]
    if optional:
        variants.append({key: value for key, value in payload.items() if key not in optional})
    variant = 0
    rounds = RATE_LIMIT_ROUNDS if _fallback_enabled() and fallback_engines(engine) else PATIENT_RATE_LIMIT_ROUNDS
    try:
        pool = account_pool(engine.provider)
    except AccountPoolError as exc:
        raise ReaderUnavailable(str(exc)) from None
    response = None
    attempted: set[int] = set()
    plan_exhausted_slots: set[int] = set()
    rate_limit_round = 0
    round_had_rate_limit = False
    rate_limit_exhausted = False
    while True:
        try:
            _check_request()
            with pool.lease(exclude=attempted, **({"cancel": _request_cancelled} if _REQUEST_LIMITS.get() is not None else {})) as lease:
                if started is not None:
                    started.set()
                response = _post(url, lease.secret, variants[variant])
                if response.status_code == 400 and (limit := _output_limit(response)) is not None \
                        and limit < _max_tokens(variants[variant]):
                    # Some free models answer at most 1024 tokens.  Remembered,
                    # so the next question does not pay for the same refusal.
                    _OUTPUT_LIMITS[(engine.provider, engine.model)] = limit
                    variants = [_with_max_tokens(item, limit) for item in variants]
                    continue
                if response.status_code == 400 and variant < len(variants) - 1:
                    variant += 1
                    continue
                if response.status_code in (401, 403):
                    attempted.add(lease.slot)
                    lease.disable()
                    continue
                if response.status_code == 429:
                    if engine.provider == "minimax" and _minimax_token_plan_exhausted(response):
                        # This account's plan cannot recover after a short
                        # cooldown.  Disable it, but still try every other pool
                        # account before escalating to a task-wide pause.
                        attempted.add(lease.slot)
                        plan_exhausted_slots.add(lease.slot)
                        lease.disable("quota")
                        continue
                    attempted.add(lease.slot)
                    retry_after = response.headers.get("Retry-After", "")
                    fallback = BACKOFF[min(rate_limit_round, len(BACKOFF) - 1)]
                    lease.cooldown(_retry_after_seconds(retry_after, fallback=fallback))
                    round_had_rate_limit = True
                    continue
                break
        except AccountPoolCancelled:
            raise ReaderRequestStopped("本次框选识读等待超时或已取消；原题未修改") from None
        except AccountPoolError:
            if pool.quota_exhausted or (
                plan_exhausted_slots and pool.enabled_size == 0
            ) or len(plan_exhausted_slots) >= pool.size:
                raise ReaderQuotaExhausted(
                    TOKEN_PLAN_EXHAUSTED_MESSAGE
                ) from None
            # A round tries every enabled account once, so a second account is
            # still an immediate failover.  Only after the whole pool reports
            # 429 do we clear the exclusions and let AccountPool wait for the
            # earliest account cooldown.  The lease is already released by the
            # context manager, and AccountPool's exclusive in_use flag means a
            # single-key pool cannot wake a herd of concurrent HTTP requests.
            if round_had_rate_limit and pool.enabled_size > 0:
                rate_limit_round += 1
                if rate_limit_round >= rounds:
                    rate_limit_exhausted = True
                    break
                attempted.clear()
                round_had_rate_limit = False
                continue
            break
    if response is None or response.status_code in (401, 403):
        raise ReaderUnavailable(f"{name} 账号池中没有可用密钥")
    if rate_limit_exhausted:
        raise ReaderUnavailable(f"{name} 接口持续限流，已自动等待并重试")
    if response.status_code >= 500:
        raise ReaderUnavailable(f"{name} 接口返回 HTTP {response.status_code}")
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
    if isinstance(text, list):  # some services answer in content parts
        text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    if not isinstance(text, str) or not text.strip():
        raise ReaderError(f"{name} 没有返回内容")
    if choice.get("finish_reason") == "length":
        raise ReaderError(f"{name} 返回内容被截断")
    return text


def read_question(
    engine: Engine,
    image_url: str,
    number: int,
    with_figures: bool,
    source_kind: str = "unknown",
) -> dict:
    prompt = transcribe_prompt(number, with_figures, source_kind)
    last_error = ""
    for _ in range(2):
        raw = chat(engine, prompt if not last_error else prompt + f"\n\n（上次输出不合格式：{last_error}。请严格按格式重写。）",
                   [image_url])
        try:
            reading = parse_reading(raw, number)
        except ValueError as error:
            last_error = str(error)
            continue
        reading["engine"] = answered_by(engine).label
        reading["raw"] = raw[:6000]
        return reading
    raise ReaderError(f"{engine.label} 两次输出都不合格式")


def arbitrate(
    engine: Engine, image_url: str, number: int, first: dict, second: dict, witness: str = "",
) -> dict:
    raw = chat(engine, arbiter_prompt(number, first, second, witness), [image_url])
    try:
        reading = parse_reading(raw, number)
    except ValueError as error:
        # read_card treats ReaderError as a recoverable arbitration failure:
        # keep the primary reading and leave the card yellow for review.  A
        # raw parser ValueError would otherwise escape the per-card policy and
        # incorrectly turn the whole card red in the worker's outer guard.
        raise ReaderError(f"{engine.label} 裁决输出不合格式：{error}") from None
    reading["engine"] = answered_by(engine).label
    reading["raw"] = raw[:6000]
    return reading


def spot_check(engine: Engine, image_url: str, spots: list[dict]) -> list[str | None]:
    """For each spot: ``"reading"``, ``"mineru"`` or ``None`` (unclear)."""
    prompt, order = spot_check_prompt(spots)
    return parse_spot_answers(chat(engine, prompt, [image_url], max_tokens=600), order)


def classify_figures_prompt(number: int, labels: list[str]) -> str:
    listed = "、".join(labels)
    return (
        f"图中用蓝色框和编号标出了候选配图。这是第 {number} 题的截图。上次没有判断编号 {listed} 的框，"
        "请只判断这几个框：\n"
        "编号=题干（属于本题题干的印刷图）、编号=A/B/C/D/E（某个选项的印刷图）、"
        "编号=第N题（印刷的图，但属于别的题）、编号=无关（手写、草图、涂画、背面透过来的字、装饰）。\n"
        "每个编号一行，例如：2=无关。不要输出别的内容。"
    )


def classify_figures(engine: Engine, image_url: str, number: int, labels: list[str]) -> dict[str, str]:
    """Roles for boxes a reading left unjudged; boxes it still skips stay unjudged."""
    raw = chat(engine, classify_figures_prompt(number, labels), [image_url], max_tokens=300)
    raw = re.sub(r"<think>.*?</think>", "", str(raw or ""), flags=re.S)
    wanted = set(labels)
    result: dict[str, str] = {}
    for label, role in re.findall(r"(\d{1,2})\s*[=＝:：]\s*(题干|无关|表格|第\s*\d{1,3}\s*题|[A-EＡ-Ｅ])", raw):
        if label not in wanted or label in result:
            continue
        if role.startswith("第"):
            other = int(re.search(r"\d+", role).group(0))
            result[label] = "stem" if other == number else f"q{other}"
        else:
            result[label] = {"题干": "stem", "无关": "none", "表格": "table"}.get(role, role.translate(str.maketrans("ＡＢＣＤＥ", "ABCDE")))
    return result


def locate_band(engine: Engine, image_url: str, number: int) -> int | None:
    return parse_band(chat(engine, locate_prompt(number), [image_url], max_tokens=200))
