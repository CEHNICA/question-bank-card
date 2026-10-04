"""MinerU 云端解析：上传原卷，取回内容块（题号、标题、候选配图的位置）。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

import requests
from django.utils import timezone

from .account_pool import AccountPoolCancelled, AccountPoolError, account_pool, secrets_from_environment
from .models import Paper

API_ROOT = "https://mineru.net/api/v4"
MAX_ZIP_BYTES = 250 * 1024 * 1024
MAX_SOURCE_BYTES = 200_000_000
# 当前 API 管理页对精准解析接口标明的单文件上限：200 页、200 MB。
# https://mineru.net/apiManage/docs
# 旧文档仍列有更高上限；提交请求遵循当前管理页的保守边界。
# 集中在这里，便于官方调整后更新。
MAX_PDF_PAGES = 200
logger = logging.getLogger(__name__)

# 1.10.6: what MinerU says about one submitted file (its own words: pending,
# running with pages done, converting…), written by the worker next to the
# paper so the page can show whether MinerU is queueing or reading.
MINERU_STATE_FILE = "mineru_state.json"
STATE_MAX_AGE = 120  # seconds; an older note is from a run that has stopped
QUEUE_STATES = {"submitted", "waiting-file", "pending"}
ALIVE_STATES = {"pending", "running", "converting"}
WAIT_SILENT = 20 * 60
WAIT_ALIVE = 60 * 60
# 1.10.7: the page asks to send the file to MinerU again (重新解析): the web
# process leaves this file beside the paper and the worker, polling MinerU,
# stops waiting for the old task and uploads again.
RESTART_FILE = "mineru_restart"


class MineruRestart(Exception):
    """A person asked to send the file again; not a MinerU error."""


# 1.10.8: 停止处理 — a person wants this paper to stop waiting (to delete it).
CANCEL_FILE = "mineru_cancel"
STOPPED_MESSAGE = "已停止本机处理，原文件和已有成果已保留。远端任务可能仍在结束，本机不再采用本轮结果。"


class MineruCancelled(RuntimeError):
    """A person stopped the parse; the paper is marked stopped, not broken."""

    def __init__(self, message: str = STOPPED_MESSAGE):
        super().__init__(message)


def _phase(state) -> str:
    return "queue" if state in QUEUE_STATES else str(state)


def record_state(path: Path, info: dict) -> None:
    """Write MinerU's latest state for one paper (worker only).  Never raises.

    ``since`` is when this phase began; submitted / waiting-file / pending are
    one phase (waiting in MinerU's queue)."""
    try:
        now = time.time()
        previous = read_state(path, raw=True) or {}
        same = _phase(previous.get("state")) == _phase(info.get("state"))
        since = previous.get("since") if same else now
        note = {**info, "since": since if isinstance(since, (int, float)) else now, "at": now}
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(note), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def read_state(path: Path, *, raw: bool = False) -> dict | None:
    """MinerU's state for the page: state, pages done (when it says), and how long in it."""
    try:
        note = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(note, dict) or not isinstance(note.get("state"), str):
        return None
    if raw:
        return note
    now = time.time()
    if not isinstance(note.get("at"), (int, float)) or now - note["at"] > STATE_MAX_AGE:
        return None
    shown = {"state": note["state"][:20], "for_seconds": max(0, int(now - float(note.get("since") or now)))}
    if isinstance(note.get("pages"), int) and isinstance(note.get("total_pages"), int) and note["total_pages"] > 0:
        shown.update(pages=max(0, min(note["pages"], note["total_pages"])), total_pages=note["total_pages"])
    return shown
ERROR_HINTS = {
    "A0202": "Token 不正确，请在 MinerU API 管理页核对或更换 Token",
    "A0211": "Token 已过期：请到 mineru.net 的 API 管理页生成新 Token，在“设置 → 服务与密钥”里换上；有效期以管理页为准",
    "-500": "请求参数不正确，请检查文件类型与接口设置",
    "-10001": "MinerU 服务暂时异常，请稍后重试",
    "-10002": "请求参数格式不正确，请检查接口设置",
    "-60001": "生成上传地址失败，请稍后重试",
    "-60002": "文件格式识别失败，请检查文件类型",
    "-60003": "云端读取文件失败，请检查文件是否损坏后重试",
    "-60004": "文件为空，请重新选择有效文件",
    "-60005": "文件超过 MinerU 的大小限制",
    "-60006": f"MinerU 返回页数超限；当前精准解析 API 管理页标明单个文件最多 {MAX_PDF_PAGES} 页，请拆分成较小的 PDF 后重试",
    "-60007": "MinerU 模型服务暂时不可用，请稍后重试",
    "-60008": "MinerU 读取文件超时，请稍后重试",
    "-60009": "MinerU 任务队列已满，请稍后重试",
    "-60010": "MinerU 解析失败，请稍后重试",
    "-60011": "MinerU 未获取到有效文件，请重新上传后重试",
    "-60012": "MinerU 找不到解析任务，请重新提交",
    "-60013": "当前 Token 无权访问该解析任务，请核对账号后重新提交",
    "-60014": "解析任务仍在运行，暂时不能删除",
    "-60015": "文件转换失败，请先转换为 PDF 后重试",
    "-60016": "文件转换为指定格式失败，请换一种格式导出后重试",
    "-60017": "MinerU 重试次数已达上限，请稍后再试",
    "-60018": "今日 MinerU 解析任务数量已达上限，请查看 API 管理页的当前额度后重试",
    "-60019": "今日 MinerU HTML 解析额度已用完，请明日重试",
    "-60020": "MinerU 文件拆分失败，请稍后重试",
    "-60021": "MinerU 无法读取文件页数，请检查文件后重试",
    "-60022": "MinerU 读取网页失败，请稍后重试",
}

# MinerU 的批量结果文档只保证 ``err_msg``，没有保证每一项都带
# ``err_code``。这里只匹配官方文档中的固定短语；原始远端文本绝不进入
# 异常、数据库或日志。顺序从具体到一般，避免“网页读取失败”之类的文案
# 被较宽泛的规则提前命中。
REMOTE_ERROR_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("A0211", ("token expired", "token has expired", "token 过期", "token已过期")),
    ("A0202", ("invalid token", "incorrect token", "token 错误", "token错误", "token 无效", "token无效")),
    ("-60019", ("html file extract quota exhausted", "html文件解析额度不足", "html 文件解析额度不足")),
    ("-60018", ("daily extract task limit reached", "每日解析任务数量已达上限")),
    ("-60022", ("web page read failure", "网页读取失败")),
    ("-60021", ("failed to read page count", "读取文件页数失败")),
    ("-60020", ("file splitting failed", "文件拆分失败")),
    ("-60017", ("retry limit reached", "重试次数达到上限", "重试次数已达上限")),
    ("-60015", ("file conversion failed", "文件转换失败")),
    ("-60014", ("deleting a running task", "删除运行中的任务")),
    ("-60013", ("no permission to access this task", "没有权限访问该任务")),
    ("-60012", ("task not found", "找不到任务")),
    ("-60011", ("failed to get valid file", "获取有效文件失败")),
    ("-60009", ("task submission queue is full", "任务提交队列已满")),
    ("-60008", ("file read timeout", "文件读取超时")),
    ("-60007", ("model service temporarily unavailable", "模型服务暂时不可用")),
    ("-60006", (
        "page count exceeds", "page count exceeded", "too many pages",
        "文件页数超过限制", "页数超过限制", "页数超出限制",
    )),
    ("-60005", ("file size exceeds limit", "file size exceeded", "file is too large", "文件大小超出限制")),
    ("-60004", ("empty file", "空文件")),
    ("-60003", ("file read failure", "failed to read file", "文件读取失败")),
    ("-60002", (
        "failed to match file format", "file format not supported", "unsupported file format",
        "unsupported file type", "获取匹配的文件格式失败", "文件格式不支持",
    )),
    ("-60001", ("failed to generate upload url", "生成上传 url 失败", "生成上传地址失败")),
    ("-10002", ("request parameter error", "请求参数错误")),
    ("-10001", ("service error", "服务异常")),
    ("-500", ("parameter error", "传参错误")),
    ("-60010", ("extract failed", "parsing failed", "解析失败")),
)

SAFE_STAGES = {"申请上传地址", "查询解析结果", "解析", "文件上传", "解析包下载", "接口"}


def page_limit_message(page_count: int, subject: str = "这份文件") -> str:
    return (
        f"{subject}共 {page_count} 页，超过 MinerU 精准解析 API 当前最多 {MAX_PDF_PAGES} 页的限制。"
        f"请先拆成每份不超过 {MAX_PDF_PAGES} 页的 PDF，再分别上传。"
    )


def validate_page_count(page_count: int, subject: str = "这份文件") -> None:
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count <= 0:
        raise MineruError("待解析文件页数不正确，未提交给 MinerU")
    if page_count > MAX_PDF_PAGES:
        raise MineruError(page_limit_message(page_count, subject))


def _safe_error_code(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    code = str(value)
    return code if re.fullmatch(r"(?:A[0-9]{4}|-[0-9]{1,6})", code) else ""


def _safe_trace_id(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{32}", value) else ""


def _classify_remote_error(value: object) -> str:
    """把远端错误归入官方固定类别，但绝不返回或保存远端原文。"""
    if not isinstance(value, str) or len(value) > 1000:
        return ""
    normalized = re.sub(r"\s+", " ", value.casefold()).strip()
    for code, phrases in REMOTE_ERROR_PHRASES:
        if any(phrase in normalized for phrase in phrases):
            return code
    return ""


def _safe_http_status(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 100 <= value <= 599 else None


class MineruError(RuntimeError):
    """只携带可公开的诊断字段；远端原文、Token 和 URL 不得传入。"""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "接口",
        code: object = "",
        category: object = "",
        trace_id: object = "",
        http_status: object = None,
        diagnostic_id: str | None = None,
    ) -> None:
        self.stage = stage if stage in SAFE_STAGES else "接口"
        self.code = _safe_error_code(code)
        self.category = _safe_error_code(category)
        self.trace_id = _safe_trace_id(trace_id)
        self.http_status = _safe_http_status(http_status)
        self.diagnostic_id = (
            diagnostic_id
            if isinstance(diagnostic_id, str) and re.fullmatch(r"MU-[0-9A-F]{12}", diagnostic_id)
            else f"MU-{uuid.uuid4().hex[:12].upper()}"
        )
        self.metadata = {
            "stage": self.stage,
            "code": self.code,
            "category": self.category,
            "trace_id": self.trace_id,
            "http_status": self.http_status,
            "diagnostic_id": self.diagnostic_id,
        }
        safe_message = str(message)[:420]
        super().__init__(f"{safe_message}（本机诊断号 {self.diagnostic_id}）")

    @property
    def effective_code(self) -> str:
        return self.category or self.code

    @property
    def account_unusable(self) -> bool:
        """Whether only this account should be removed from the current run."""

        return self.http_status in {401, 403} or self.effective_code in {
            "A0202", "A0211", "-60013", "-60018", "-60019",
        }

    @property
    def rate_limited(self) -> bool:
        return self.http_status == 429


def _error_details(stage: str, result: dict | None, status: int | None = None) -> tuple[str, dict]:
    """生成用户可见文案和严格白名单元数据，不保留远端原文。"""
    stage = stage if stage in SAFE_STAGES else "接口"
    safe_status = _safe_http_status(status)
    result = result if isinstance(result, dict) else {}
    code = _safe_error_code(result.get("code"))
    trace_id = _safe_trace_id(result.get("trace_id"))
    inferred_category = _classify_remote_error(result.get("err_msg"))
    # Preserve the exact safe code for diagnostics, but let a recognised code
    # or official fixed phrase independently drive account failover.
    category = code if code in ERROR_HINTS else inferred_category
    if category in ERROR_HINTS:
        hint = ERROR_HINTS[category]
    elif code:
        hint = "MinerU 拒绝了本次请求，请核对 API 配置"
    elif safe_status in {401, 403}:
        hint = "Token 无效、过期或没有接口权限，请在 MinerU API 管理页核对"
    elif safe_status == 429:
        hint = "MinerU 请求过于频繁，请稍后重试"
    elif safe_status is not None and safe_status >= 500:
        hint = "MinerU 服务暂时异常，请稍后重试"
    else:
        hint = "请检查 MinerU API 配置后重试"
    details = [f"HTTP {safe_status}" if safe_status is not None else "接口返回错误"]
    if code:
        details.append(f"错误码 {code}")
    elif category:
        # 这是由固定短语白名单推断出的本地类别，不冒充 MinerU 实际返回的错误码。
        details.append(f"诊断分类 {category}")
    if trace_id:
        details.append(f"追踪号 {trace_id}")
    message = f"MinerU {stage}失败（{'，'.join(details)}）：{hint}"
    metadata = {
        "stage": stage,
        "code": code,
        "category": category,
        "trace_id": trace_id,
        "http_status": safe_status,
    }
    return message, metadata


def _error_message(stage: str, result: dict | None, status: int | None = None) -> str:
    return _error_details(stage, result, status)[0]


def _mineru_error(stage: str, result: dict | None, status: int | None = None) -> MineruError:
    message, metadata = _error_details(stage, result, status)
    return MineruError(message, **metadata)


# Transient network trouble (a dropped connection, a stalled upload to the
# storage bucket) is retried a few times before the task is marked failed.
NETWORK_RETRY_DELAYS = (2.0, 5.0)
TRANSIENT_HTTP = frozenset({500, 502, 503, 504})


class _TransientDownloadError(MineruError):
    """A download failure worth retrying (network error or HTTP 5xx)."""


def _check_cancel(cancel: Callable[[], bool] | None) -> None:
    if cancel is not None and cancel():
        raise MineruCancelled()


def _pause(seconds: float, cancel: Callable[[], bool] | None = None) -> None:
    if cancel is None:
        time.sleep(seconds)
        return
    # Bounded checks also make an account retry or poll delay cancellable.
    remaining = seconds
    while remaining > 0:
        _check_cancel(cancel)
        duration = min(0.25, remaining)
        time.sleep(duration)
        remaining -= duration
    _check_cancel(cancel)


def _with_network_retries(action: Callable[[], requests.Response], *, cancel=None) -> requests.Response:
    for attempt in range(len(NETWORK_RETRY_DELAYS) + 1):
        _check_cancel(cancel)
        try:
            response = action()
        except requests.RequestException:
            if attempt == len(NETWORK_RETRY_DELAYS):
                raise
        else:
            _check_cancel(cancel)
            if response.status_code not in TRANSIENT_HTTP or attempt == len(NETWORK_RETRY_DELAYS):
                return response
        _pause(NETWORK_RETRY_DELAYS[attempt], cancel)
    raise AssertionError("unreachable")


def _api_json(
    session: requests.Session,
    token: str,
    endpoint: str,
    payload: dict | None = None,
    *, cancel=None,
) -> tuple[dict, str]:
    try:
        response = _with_network_retries(lambda: session.request(
            "POST" if payload is not None else "GET",
            f"{API_ROOT}/{endpoint.lstrip('/')}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json=payload,
            timeout=(10, 45),
        ), cancel=cancel)
    except requests.RequestException:
        raise MineruError("MinerU 接口连接失败，请检查网络后重试") from None
    try:
        result = response.json()
    except ValueError:
        result = None
    stage = "申请上传地址" if endpoint == "file-urls/batch" else "查询解析结果"
    if not response.ok:
        raise _mineru_error(stage, result, response.status_code)
    if not isinstance(result, dict):
        raise MineruError(f"MinerU {stage}返回内容格式不正确", stage=stage)
    if type(result.get("code")) is not int or result["code"] != 0:
        raise _mineru_error(stage, result)
    data = result.get("data")
    if not isinstance(data, dict):
        raise MineruError(f"MinerU {stage}没有返回预期数据", stage=stage,
                          trace_id=_safe_trace_id(result.get("trace_id")))
    return data, _safe_trace_id(result.get("trace_id"))


def _https_url(value: object, stage: str, *, trace_id: str = "") -> str:
    """Validate returned storage URLs without exposing signed URLs in errors."""
    valid = (isinstance(value, str) and bool(value)
             and not any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value))
    if valid:
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme == "https" and bool(parsed.hostname)
                     and parsed.username is None and parsed.password is None and not parsed.fragment
                     and (parsed.port is None or 0 < parsed.port <= 65535))
        except ValueError:
            valid = False
    if not valid:
        raise MineruError(f"MinerU {stage}返回的地址格式不正确，需要有效的 HTTPS 地址", stage=stage,
                          trace_id=trace_id)
    return value


def _download_zip(session: requests.Session, url: str, target: Path, *, cancel=None) -> None:
    for attempt in range(len(NETWORK_RETRY_DELAYS) + 1):
        _check_cancel(cancel)
        try:
            return _download_zip_once(session, url, target, cancel=cancel)
        except _TransientDownloadError:
            if attempt == len(NETWORK_RETRY_DELAYS):
                raise
        _pause(NETWORK_RETRY_DELAYS[attempt], cancel)


def _download_zip_once(session: requests.Session, url: str, target: Path, *, cancel=None) -> None:
    _check_cancel(cancel)
    url = _https_url(url, "解析包下载")
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        with session.get(url, stream=True, timeout=(10, 120)) as response:
            if not response.ok:
                error_type = _TransientDownloadError if response.status_code in TRANSIENT_HTTP else MineruError
                raise error_type(f"MinerU 解析包下载失败（HTTP {response.status_code}）")
            with partial.open("wb") as output:
                size = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    _check_cancel(cancel)
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise MineruError("MinerU 解析包超过 250 MB 上限")
                    output.write(chunk)
        if not zipfile.is_zipfile(partial):
            raise MineruError("MinerU 返回的解析包不是有效 ZIP，请稍后重试")
        _check_cancel(cancel)
        os.replace(partial, target)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise _TransientDownloadError(f"MinerU 解析包下载失败（{type(exc).__name__}）") from None
    except (MineruError, MineruCancelled, OSError):
        partial.unlink(missing_ok=True)
        raise


def request_extract_file(
    token: str,
    source: Path,
    target: Path,
    page_count: int,
    *,
    heartbeat: Callable[[], None] | None = None,
    on_state: Callable[[dict], None] | None = None,
    restart: Callable[[], bool] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """解析一个已经满足页数限制的文件。

    ``on_state`` hears what MinerU reports (uploading, pending, running with
    pages done, converting, downloading) so the page can say what is slow.

    ``source`` 可以是原始 PDF，也可以是本机从一本书切出的临时分片；
    ``target`` 始终由调用方指定，因此多个分片不会互相覆盖。上传地址、
    下载地址和授权头均不落盘。
    """
    validate_page_count(page_count, source.name)
    _check_cancel(cancel)
    try:
        source_size = source.stat().st_size
    except OSError:
        raise MineruError("待解析文件无法读取，请检查原文件后重试") from None
    if source_size == 0:
        raise MineruError("待解析文件为空，未提交给 MinerU")
    if source_size > MAX_SOURCE_BYTES:
        raise MineruError("待解析文件超过 MinerU 当前 200 MB 单文件上限，请压缩或拆分后重试")
    target.parent.mkdir(parents=True, exist_ok=True)

    def report(state: str, **extra) -> None:
        if on_state is not None:
            try:
                on_state({"state": state, **extra})
            except Exception:  # showing progress must never break parsing
                logger.debug("mineru state callback failed", exc_info=True)

    with requests.Session() as session:
        report("uploading")
        batch, submit_trace_id = _api_json(session, token, "file-urls/batch",
                                            {"files": [{"name": source.name}], "model_version": "vlm"},
                                            **({"cancel": cancel} if cancel is not None else {}))
        _check_cancel(cancel)
        batch_id = batch.get("batch_id")
        if (not isinstance(batch_id, str) or not batch_id.strip()
                or any(character.isspace() or ord(character) < 32 for character in batch_id)):
            raise MineruError("MinerU 申请上传地址返回的批次编号格式不正确，未上传文件",
                              stage="申请上传地址", trace_id=submit_trace_id)
        upload_urls = batch.get("file_urls")
        if not isinstance(upload_urls, list) or len(upload_urls) != 1:
            raise MineruError("MinerU 申请上传地址没有返回单个文件所需的上传地址列表，未上传文件",
                              stage="申请上传地址", trace_id=submit_trace_id)
        upload_url = _https_url(upload_urls[0], "申请上传地址", trace_id=submit_trace_id)
        def upload() -> requests.Response:
            # Reopen the file for every attempt: a retried PUT must send the
            # whole body again, not the remainder of a half-read stream.
            with source.open("rb") as stream:
                return session.put(upload_url, data=stream, timeout=(10, 180))

        try:
            response = _with_network_retries(upload, cancel=cancel)
            if not response.ok:
                raise MineruError(f"MinerU 文件上传失败（HTTP {response.status_code}）")
        except requests.RequestException as exc:
            raise MineruError(f"MinerU 文件上传失败（{type(exc).__name__}）") from None
        report("submitted")
        started = time.monotonic()
        state = None
        # 20 minutes, or an hour while MinerU says the file is queued or being
        # read: giving up then and sending again would only start the queue over (1.10.8).
        while time.monotonic() - started < (WAIT_ALIVE if state in ALIVE_STATES else WAIT_SILENT):
            _check_cancel(cancel)
            data, trace_id = _api_json(session, token, f"extract-results/batch/{quote(batch_id, safe='')}",
                                       **({"cancel": cancel} if cancel is not None else {}))
            _check_cancel(cancel)
            if heartbeat is not None:
                heartbeat()
            rows = data.get("extract_result")
            if not isinstance(rows, list) or len(rows) > 1 or (rows and not isinstance(rows[0], dict)):
                raise MineruError("MinerU 查询解析结果返回的单文件任务列表格式不正确",
                                  stage="查询解析结果", trace_id=trace_id)
            task = rows[0] if rows else None
            state = task.get("state") if isinstance(task, dict) else None
            if task is not None and (not isinstance(state, str) or not state.strip()):
                raise MineruError("MinerU 查询解析结果没有返回有效的任务状态",
                                  stage="查询解析结果", trace_id=trace_id)
            if state == "done":
                url = task.get("full_zip_url")
                url = _https_url(url, "解析包下载", trace_id=trace_id)
                report("downloading")
                _download_zip(session, url, target, **({"cancel": cancel} if cancel is not None else {}))
                _check_cancel(cancel)
                return target
            if isinstance(state, str) and state != "failed":
                # “running” comes with {extracted_pages, total_pages}.
                pages = task.get("extract_progress") if isinstance(task.get("extract_progress"), dict) else {}
                done, total = pages.get("extracted_pages"), pages.get("total_pages")
                report(state, **({"pages": done, "total_pages": total}
                                 if isinstance(done, int) and isinstance(total, int) and total > 0 else {}))
            if state == "failed":
                raise _mineru_error("解析", {
                    "code": task.get("err_code"),
                    "err_msg": task.get("err_msg"),
                    "trace_id": trace_id,
                })
            if cancel is not None and cancel():
                raise MineruCancelled()
            if restart is not None and restart():
                raise MineruRestart()
            # A short exam is usually done within 10–20 s; poll briskly at first
            # and back off for long books so the API is not hammered.
            _pause(2 if time.monotonic() - started < 60 else 5, cancel)
    if state == "pending":
        raise MineruError("MinerU 排了一个小时还没开始识别，多半是 MinerU 那边太忙；请过一会儿点“重试”")
    raise MineruError("MinerU 解析超时")


def mineru_tokens_from_environment() -> tuple[str, ...]:
    """Return the worker-only Token pool; values must never be logged."""

    return secrets_from_environment("mineru")


def request_extract_file_from_pool(
    source: Path,
    target: Path,
    page_count: int,
    *,
    heartbeat: Callable[[], None] | None = None,
    on_state: Callable[[dict], None] | None = None,
    restart: Callable[[], bool] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> Path:
    """Run one complete MinerU task with one leased account.

    Invalid, expired, or exhausted accounts are isolated for the rest of this
    worker run.  A rate-limited account cools down while another account gets a
    chance.  Document failures are not retried against every account.
    """

    try:
        _check_cancel(cancel)
        pool = account_pool("mineru")
    except AccountPoolError as exc:
        raise MineruError(str(exc)) from None
    attempted: set[int] = set()
    last_error: MineruError | None = None
    while len(attempted) < max(1, pool.size):
        try:
            _check_cancel(cancel)
            with pool.lease(exclude=attempted, **({"cancel": cancel} if cancel is not None else {})) as lease:
                try:
                    return request_extract_file(
                        lease.secret, source, target, page_count, heartbeat=heartbeat,
                        **({"on_state": on_state} if on_state is not None else {}),
                        **({"restart": restart} if restart is not None else {}),
                        **({"cancel": cancel} if cancel is not None else {}),
                    )
                except MineruError as error:
                    last_error = error
                    if error.account_unusable:
                        attempted.add(lease.slot)
                        lease.disable()
                        continue
                    if error.rate_limited:
                        attempted.add(lease.slot)
                        lease.cooldown(12)
                        continue
                    raise
        except AccountPoolCancelled:
            raise MineruCancelled() from None
        except AccountPoolError:
            break
    if last_error is not None:
        raise last_error
    raise MineruError("MinerU 账号池中没有可用账号")


def request_extract(paper: Paper, token: str, source: Path) -> Path:
    """兼容单文件试卷的旧入口。"""
    target = Path(paper.source_path).parent / "mineru_result.zip"

    def heartbeat() -> None:
        Paper.objects.filter(pk=paper.pk).update(updated_at=timezone.now())

    return request_extract_file(token, source, target, len(paper.pages), heartbeat=heartbeat)


def write_pdf_slice(source: Path, target: Path, page_start: int, page_end: int) -> None:
    """把 ``[page_start, page_end)`` 原样复制为独立 PDF，供 MinerU 分片解析。

    这是纯本机文件操作。原始 PDF 不会被修改，保存成功前也不会覆盖既有分片。
    """
    import pymupdf as fitz

    temporary = target.with_name(f"{target.stem}.tmp{target.suffix}")
    temporary.unlink(missing_ok=True)
    with fitz.open(source) as original:
        if not 0 <= page_start < page_end <= len(original):
            raise ValueError("PDF 分片页码范围不正确")
        sliced = fitz.open()
        try:
            sliced.insert_pdf(original, from_page=page_start, to_page=page_end - 1)
            sliced.save(temporary, garbage=3, deflate=True)
        finally:
            sliced.close()
    temporary.replace(target)


def load_blocks(archive_path: Path, page_count: int) -> list[dict]:
    """从解析包读出内容块（坐标 0–1000）。"""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = [n for n in archive.namelist()
                     if n.endswith("_content_list.json") and not n.endswith("_content_list_v2.json")]
            if len(names) != 1:
                raise MineruError("MinerU 解析包缺少唯一的 content_list.json")
            info = archive.getinfo(names[0])
            if info.file_size > 50 * 1024 * 1024:
                raise MineruError("MinerU 内容文件过大")
            content = json.loads(archive.read(info))
    except MineruError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError):
        raise MineruError("MinerU 解析包已损坏或内容格式不正确，请重试解析") from None
    if not isinstance(content, list):
        raise MineruError("MinerU 内容块格式不正确")
    blocks = []
    for seq, raw in enumerate(content):
        if not isinstance(raw, dict):
            continue
        page = raw.get("page_idx")
        if isinstance(page, bool) or not isinstance(page, int) or not 0 <= page < page_count:
            continue
        bbox = raw.get("bbox")
        if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(v, (int, float)) for v in bbox)
                and 0 <= bbox[0] < bbox[2] <= 1000 and 0 <= bbox[1] < bbox[3] <= 1000):
            bbox = None
        text = raw.get("text") or raw.get("content") or ""
        if not isinstance(text, str):
            text = ""
        block = {"seq": seq, "type": str(raw.get("type") or "unknown")[:40], "page_idx": page,
                 "bbox": [float(v) for v in bbox] if bbox else None, "text": text[:4000]}
        # A recognised table keeps its cells: it can become a text table.
        body = raw.get("table_body")
        if block["type"] == "table" and isinstance(body, str) and body.strip():
            block["html"] = body[:20000]
        blocks.append(block)
    if not blocks:
        raise MineruError("MinerU 没有返回可用的内容块")
    return blocks
