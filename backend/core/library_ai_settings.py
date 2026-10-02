"""Independent, opt-in Doubao Pro configuration for library enrichment.

This module never consults OCR credentials, desktop assistants or fallback
engines. Saving performs local validation only; an explicit synthetic probe
must succeed before generation can start. Keys stay in a separate DPAPI file.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

import requests
from django.utils import timezone
from PIL import Image, ImageDraw

from . import credential_settings, features

ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
ENDPOINT_ID = re.compile(r"ep-[A-Za-z0-9][A-Za-z0-9_-]{3,150}\Z")
FEATURE_KEYS = {"knowledge_tags", "ai_answer"}
UNAVAILABLE = "豆包 API 尚未配置并通过显式测试，已暂停生成；请打开“标签与参考答案设置”。可考虑 DeepSeek Pro；软件不会自动切换到它、OCR 读题服务或其他 AI 助手。"
_lock = threading.RLock()


class SettingsError(ValueError):
    pass


class ServiceError(SettingsError):
    pass


class ConnectionError(ServiceError):
    """A credential/transport/model failure invalidates the former probe."""


def path() -> Path:
    explicit = (os.environ.get("QB_LIBRARY_AI_SETTINGS_FILE") or os.environ.get("QB_LIBRARY_AI_FILE") or "").strip()
    return Path(explicit).resolve() if explicit else credential_settings.store.credential_path().with_name("library-ai-settings.json")


def key_path() -> Path:
    explicit = os.environ.get("QB_LIBRARY_AI_CREDENTIAL_FILE", "").strip()
    return Path(explicit).resolve() if explicit else path().with_name("library-ai.dat")


def _load() -> dict:
    try:
        value = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get("version") == 1 else {}


def _write(target: Path, value: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".library-ai-", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_settings(value: dict) -> None:
    _write(path(), json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"))


def public_status() -> dict:
    """Read non-secret metadata only. Never decrypt a key during a web GET."""
    config = _load()
    endpoint = str(config.get("endpoint_id") or "")
    configured = bool(config.get("key_configured") is True and key_path().is_file()
                      and ENDPOINT_ID.fullmatch(endpoint) and config.get("thinking") is True)
    verified = configured and config.get("verified") is True
    switches = features.load()
    return {
        "provider": "doubao", "model_profile": "pro", "endpoint_id": endpoint,
        "thinking": True, "configured": configured, "verified": verified,
        "ready": verified, "status": "verified" if verified else "unverified" if configured else "missing",
        "verified_at": str(config.get("verified_at") or "") if verified else "",
        "resolved_model": str(config.get("resolved_model") or "") if verified else "",
        "features": {key: switches[key] for key in sorted(FEATURE_KEYS)},
        "message": "豆包 Pro API 已通过图文与思考测试；生成结果仍需核对。" if verified else UNAVAILABLE,
    }


def save(payload: dict) -> dict:
    """Validate and save without a network call or an OCR credential change."""
    if not isinstance(payload, dict) or set(payload) - {"features", "endpoint_id", "thinking", "key"}:
        raise SettingsError("标签与参考答案设置格式不正确。")
    changes = payload.get("features", {})
    if not isinstance(changes, dict) or set(changes) - FEATURE_KEYS or not all(type(v) is bool for v in changes.values()):
        raise SettingsError("标签与参考答案开关只能分别设为 true 或 false。")
    if payload.get("thinking", True) is not True:
        raise SettingsError("数学标签与答案使用 Pro 思考策略，请保持思考开启。")
    operation = payload.get("key", {"action": "keep"})
    if not isinstance(operation, dict) or set(operation) - {"action", "value"}:
        raise SettingsError("请按保持、替换或清除保存豆包 API Key。")
    action = operation.get("action")
    if action not in {"keep", "clear", "replace"} or (action != "replace" and "value" in operation):
        raise SettingsError("请按保持、替换或清除保存豆包 API Key。")
    key = ""
    if action == "replace":
        raw = operation.get("value")
        if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 4096:
            raise SettingsError("请填写完整的豆包 API Key。")
        key = raw.strip()
        if any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
            raise SettingsError("豆包 API Key 不能包含空格、换行或非英文字符。")
    with _lock:
        previous = _load()
        endpoint = payload.get("endpoint_id", previous.get("endpoint_id", ""))
        if not isinstance(endpoint, str):
            raise SettingsError("请填写火山方舟控制台的模型 Endpoint ID。")
        endpoint = endpoint.strip()
        if endpoint and ENDPOINT_ID.fullmatch(endpoint) is None:
            raise SettingsError("模型 Endpoint ID 应以 ep- 开头，请从火山方舟控制台完整复制。")
        if action == "replace" and not endpoint:
            raise SettingsError("保存豆包 API Key 前，请填写 Pro 思考模型的 Endpoint ID。")
        changed = action != "keep" or endpoint != previous.get("endpoint_id", "")
        revision = uuid.uuid4().hex if changed else previous.get("revision", uuid.uuid4().hex)
        config = {"version": 1, "revision": revision, "endpoint_id": endpoint, "thinking": True,
                  "key_revision": previous.get("key_revision", ""),
                  "key_configured": previous.get("key_configured") is True,
                  "verified": previous.get("verified") is True and not changed,
                  "verified_at": previous.get("verified_at", "") if not changed else "",
                  "resolved_model": previous.get("resolved_model", "") if not changed else ""}
        if changed:
            # Invalidate a former probe before any credential mutation. A failed
            # write cannot leave a changed key marked usable in another process.
            _write_settings({**config, "verified": False})
        try:
            if action == "replace":
                config["key_revision"] = uuid.uuid4().hex
                encrypted = credential_settings.store._transform(
                    json.dumps({"version": 1, "revision": config["key_revision"], "key": key}).encode("utf-8"), protect=True)
                _write(key_path(), encrypted)
                config["key_configured"] = True
            elif action == "clear":
                key_path().unlink(missing_ok=True)
                config["key_configured"] = False
                config["key_revision"] = ""
            _write_settings(config)
        except (OSError, credential_settings.CredentialStoreError):
            raise SettingsError("豆包 API 设置加密保存失败；生成保持暂停，请重新保存。") from None
        if changes:
            features.save(changes)
    return public_status()


def ensure_ready(kind: str | None = None) -> dict:
    if kind and not features.enabled({"answer": "ai_answer", "tags": "knowledge_tags"}.get(kind, "")):
        raise ServiceError("这个功能已在“标签与参考答案设置”里关掉，已暂停生成。")
    result = public_status()
    if not result["ready"]:
        raise ServiceError(UNAVAILABLE)
    return result


def _key(config: dict) -> str:
    try:
        value = json.loads(credential_settings.store._transform(key_path().read_bytes(), protect=False).decode("utf-8"))
        key = value.get("key", "") if isinstance(value, dict) and value.get("version") == 1 else ""
        if (not isinstance(key, str) or not key or any(c.isspace() for c in key)
                or value.get("revision") != config.get("key_revision")):
            raise ValueError
        return key
    except (OSError, ValueError, credential_settings.CredentialStoreError):
        raise ConnectionError("豆包 API Key 无法读取，已暂停生成；请在独立设置中重新保存。") from None


def _request(config: dict, prompt: str, image_urls: list[str], max_tokens: int, *, kind=None) -> tuple[str, dict]:
    key = _key(config)
    if not _same_configuration(config, _load()):
        raise ServiceError("请求前豆包 API 设置已变化，旧任务未发起，请按新设置重试。")
    if kind:
        ensure_ready(kind)
    content = [{"type": "text", "text": prompt}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls)
    payload = {"model": config["endpoint_id"], "messages": [{"role": "user", "content": content}],
               "thinking": {"type": "enabled"}, "max_tokens": max_tokens, "stream": False}
    try:
        response = requests.post(ARK_URL, json=payload, headers={"Authorization": f"Bearer {key}"},
                                 timeout=(15, 180), allow_redirects=False)
        if response.status_code != 200:
            if response.status_code in {401, 403}:
                raise ConnectionError("豆包 API 鉴权或权限未通过，已暂停生成；请核对 API Key 与 Endpoint ID。")
            raise ConnectionError(f"豆包 API 未完成请求（HTTP {int(response.status_code)}），已暂停生成，请稍后显式重试。")
        body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ConnectionError("豆包思考或答案超过本次长度限制，未保存不完整结果。")
        message = choice["message"]
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise ValueError
        return text, {"model": str(body.get("model") or ""),
                      "thinking": bool(str(message.get("reasoning_content") or "").strip())}
    except ServiceError:
        raise
    except requests.RequestException:
        raise ConnectionError("豆包 API 网络请求未完成，已暂停生成，请稍后显式重试。") from None
    except (ValueError, TypeError, KeyError, IndexError):
        raise ConnectionError("豆包 API 没有返回完整可用的答案，未保存结果。") from None


def _same_configuration(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in ("revision", "key_revision", "endpoint_id", "thinking"))


def _mark_unverified(config: dict) -> None:
    with _lock:
        current = _load()
        if _same_configuration(config, current):
            _write_settings({**current, "verified": False, "verified_at": "", "resolved_model": ""})


def chat(prompt: str, image_urls: list[str], *, kind: str, max_tokens: int = 12000) -> tuple[str, str]:
    """Exactly one Doubao request; no engine discovery, fallback or retries."""
    ensure_ready(kind)
    config = _load()
    try:
        text, evidence = _request(config, prompt, image_urls, max_tokens, kind=kind)
        if "doubao" not in evidence["model"].lower() or "pro" not in evidence["model"].lower() or not evidence["thinking"]:
            raise ConnectionError("本次响应未确认豆包 Pro 思考能力，结果未保存，生成保持暂停。")
    except ConnectionError:
        _mark_unverified(config)
        raise
    if not _same_configuration(config, _load()):
        raise ServiceError("生成期间豆包 API 设置已变化，旧结果未保存，请按新设置重试。")
    ensure_ready(kind)
    return text, f"豆包 Pro · {evidence['model'] or config['endpoint_id']}"


def test_connection(payload: dict) -> dict:
    """Explicit paid-capable synthetic image/math probe, never a user paper."""
    if not isinstance(payload, dict) or payload != {"confirm": True}:
        raise SettingsError("请明确确认运行一次豆包图文与思考测试；这可能产生 API 费用。")
    config = _load()
    if not public_status()["configured"]:
        raise ServiceError(UNAVAILABLE)
    image = Image.new("RGB", (120, 70), "white")
    drawing = ImageDraw.Draw(image)
    drawing.ellipse((15, 20, 35, 40), fill="black")
    drawing.ellipse((70, 20, 90, 40), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    image_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    prompt = "这是连接测试用的合成图，不是用户试卷。开启思考，计算 1+1，并数图中的黑色圆点。只输出【答案】数值【图示】圆点个数。"
    try:
        text, evidence = _request(config, prompt, [image_url], 12000)
        if not re.search(r"【答案】\s*\$?2\$?\s*【图示】\s*\$?2\$?", text):
            raise ServiceError("豆包 API 未通过合成数学题与图像核对，生成保持暂停。")
        if "doubao" not in evidence["model"].lower() or "pro" not in evidence["model"].lower() or not evidence["thinking"]:
            raise ServiceError("服务响应未确认豆包 Pro 模型及思考能力；请核对 Endpoint ID，生成保持暂停。")
    except ServiceError:
        _mark_unverified(config)
        raise
    with _lock:
        current = _load()
        if not _same_configuration(config, current):
            raise ServiceError("测试期间设置已变化，测试结果未沿用，请重新测试。")
        _write_settings({**current, "verified": True, "verified_at": timezone.now().isoformat(),
                         "resolved_model": evidence["model"]})
    return public_status()
