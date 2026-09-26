"""MinerU 云端解析：上传原卷，取回内容块（题号、标题、候选配图的位置）。"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import zipfile
from pathlib import Path

import requests
from django.utils import timezone

from .models import Paper

API_ROOT = "https://mineru.net/api/v4"
MAX_ZIP_BYTES = 250 * 1024 * 1024
# 当前产品内 API 管理文档对精准解析 API 标明的上限：
# https://mineru.net/apiManage/docs?openApplyModal=true
# 集中在这里，便于官方调整后更新。
MAX_PDF_PAGES = 200
logger = logging.getLogger(__name__)
ERROR_HINTS = {
    "A0202": "Token 不正确，请在 MinerU API 管理页核对或更换 Token",
    "A0211": "Token 已过期，请在 MinerU API 管理页更换 Token",
    "-500": "请求参数不正确，请检查文件类型与接口设置",
    "-10001": "MinerU 服务暂时异常，请稍后重试",
    "-10002": "请求参数格式不正确，请检查接口设置",
    "-60001": "生成上传地址失败，请稍后重试",
    "-60002": "文件格式识别失败，请检查文件类型",
    "-60003": "云端读取文件失败，请检查文件是否损坏后重试",
    "-60005": "文件超过 MinerU 的大小限制",
    "-60006": "MinerU 返回页数超限；当前精准解析 API 文档标明最多 200 页，请拆分成较小的 PDF 后重试",
    "-60007": "MinerU 模型服务暂时不可用，请稍后重试",
    "-60009": "MinerU 任务队列已满，请稍后重试",
    "-60010": "MinerU 解析失败，请稍后重试",
    "-60018": "今日 MinerU 解析额度已用完，请明日重试",
}


class MineruError(RuntimeError):
    pass


def page_limit_message(page_count: int, subject: str = "这份文件") -> str:
    return (
        f"{subject}共 {page_count} 页，超过 MinerU 精准解析 API 当前最多 {MAX_PDF_PAGES} 页的限制。"
        f"请先拆成每份不超过 {MAX_PDF_PAGES} 页的 PDF，再分别上传。"
    )


def validate_page_count(page_count: int, subject: str = "这份文件") -> None:
    if page_count > MAX_PDF_PAGES:
        raise MineruError(page_limit_message(page_count, subject))


def _safe_error_code(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    code = str(value)
    return code if re.fullmatch(r"(?:A[0-9]{4}|-[0-9]{1,6})", code) else ""


def _safe_trace_id(value: object) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{32}", value) else ""


def _is_page_limit_error(value: object) -> bool:
    """只对白名单特征分类，绝不把远端 err_msg 原样显示给用户。"""
    if not isinstance(value, str) or len(value) > 1000:
        return False
    message = value.casefold()
    english = "page" in message and any(word in message for word in ("limit", "exceed", "too many"))
    chinese = "页" in message and any(word in message for word in ("限制", "超过", "超出", "过多"))
    return english or chinese


def _error_message(stage: str, result: dict | None, status: int | None = None) -> str:
    # Never include response text, remote error messages, signed URLs or tokens.
    result = result if isinstance(result, dict) else {}
    code = _safe_error_code(result.get("code"))
    trace_id = _safe_trace_id(result.get("trace_id"))
    if code == "-60006":
        # 云端限额可能先于本文档更新，不能把本地页数与旧上限拼成逻辑矛盾的句子。
        hint = ERROR_HINTS["-60006"]
    elif code in ERROR_HINTS:
        hint = ERROR_HINTS[code]
    elif _is_page_limit_error(result.get("err_msg")):
        hint = ERROR_HINTS["-60006"]
    elif code:
        hint = "MinerU 拒绝了本次请求，请核对 API 配置"
    elif status in {401, 403}:
        hint = "Token 无效、过期或没有接口权限，请在 MinerU API 管理页核对"
    elif status == 429:
        hint = "MinerU 请求过于频繁，请稍后重试"
    elif status is not None and status >= 500:
        hint = "MinerU 服务暂时异常，请稍后重试"
    else:
        hint = "请检查 MinerU API 配置后重试"
    details = [f"HTTP {status}" if status is not None else "接口返回错误"]
    if code:
        details.append(f"错误码 {code}")
    if trace_id:
        details.append(f"追踪号 {trace_id}")
    return f"MinerU {stage}失败（{'，'.join(details)}）：{hint}"


def _api_json(session: requests.Session, token: str, endpoint: str, payload: dict | None = None) -> dict:
    try:
        response = session.request(
            "POST" if payload is not None else "GET",
            f"{API_ROOT}/{endpoint.lstrip('/')}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            json=payload,
            timeout=(10, 45),
        )
    except requests.RequestException:
        raise MineruError("MinerU 接口连接失败，请检查网络后重试") from None
    try:
        result = response.json()
    except ValueError:
        result = None
    stage = "申请上传地址" if endpoint == "file-urls/batch" else "查询解析结果"
    if not response.ok:
        raise MineruError(_error_message(stage, result, response.status_code))
    if not isinstance(result, dict):
        raise MineruError("MinerU 接口返回内容格式不正确")
    if result.get("code") != 0:
        raise MineruError(_error_message(stage, result))
    data = result.get("data")
    if not isinstance(data, dict):
        raise MineruError("MinerU 接口没有返回预期数据")
    return data


def _download_zip(session: requests.Session, url: str, target: Path) -> None:
    if not url.startswith("https://"):
        raise MineruError("MinerU 下载地址不是 HTTPS")
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        with session.get(url, stream=True, timeout=(10, 120)) as response:
            if not response.ok:
                raise MineruError(f"MinerU 解析包下载失败（HTTP {response.status_code}）")
            with partial.open("wb") as output:
                size = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > MAX_ZIP_BYTES:
                        raise MineruError("MinerU 解析包超过 250 MB 上限")
                    output.write(chunk)
        if not zipfile.is_zipfile(partial):
            raise MineruError("MinerU 返回的解析包不是有效 ZIP，请稍后重试")
        os.replace(partial, target)
    except requests.RequestException as exc:
        partial.unlink(missing_ok=True)
        raise MineruError(f"MinerU 解析包下载失败（{type(exc).__name__}）") from None
    except (MineruError, OSError):
        partial.unlink(missing_ok=True)
        raise


def request_extract(paper: Paper, token: str, source: Path) -> Path:
    """调用 MinerU 云端接口。不保存上传地址、下载地址和授权头。"""
    validate_page_count(len(paper.pages))
    target = Path(paper.source_path).parent / "mineru_result.zip"
    with requests.Session() as session:
        batch = _api_json(session, token, "file-urls/batch",
                          {"files": [{"name": source.name}], "model_version": "vlm"})
        batch_id = batch.get("batch_id")
        upload_urls = batch.get("file_urls") or []
        if not batch_id or len(upload_urls) != 1:
            raise MineruError("MinerU 未返回上传地址")
        try:
            with source.open("rb") as stream:
                response = session.put(upload_urls[0], data=stream, timeout=(10, 180))
                if not response.ok:
                    raise MineruError(f"MinerU 文件上传失败（HTTP {response.status_code}）")
        except requests.RequestException as exc:
            raise MineruError(f"MinerU 文件上传失败（{type(exc).__name__}）") from None
        deadline = time.monotonic() + 20 * 60
        while time.monotonic() < deadline:
            data = _api_json(session, token, f"extract-results/batch/{batch_id}")
            Paper.objects.filter(pk=paper.pk).update(updated_at=timezone.now())
            rows = data.get("extract_result") or []
            task = rows[0] if rows else None
            state = task.get("state") if isinstance(task, dict) else None
            if state == "done":
                url = task.get("full_zip_url")
                if not url:
                    raise MineruError("MinerU 完成任务但没有解析包")
                _download_zip(session, url, target)
                return target
            if state == "failed":
                raise MineruError(_error_message("解析", {
                    "code": task.get("err_code"),
                    "err_msg": task.get("err_msg"),
                }))
            time.sleep(5)
    raise MineruError("MinerU 解析超时")


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
        blocks.append({"seq": seq, "type": str(raw.get("type") or "unknown")[:40], "page_idx": page,
                       "bbox": [float(v) for v in bbox] if bbox else None, "text": text[:4000]})
    if not blocks:
        raise MineruError("MinerU 没有返回可用的内容块")
    return blocks
