"""Optional enrichment by the current assistant or an explicitly configured API.

Saving never contacts a service. API credentials are independent of OCR and
provider-specific DPAPI files; status reads only their non-secret metadata.
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
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import requests
from django.utils import timezone
from PIL import Image, ImageDraw

from . import credential_settings, features, knowledge, provider_catalog, readers

ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
ARK_URL = ARK_BASE + "/chat/completions"
MINIMAX_BASE = "https://api.minimax.cn/v1"
MINIMAX_MODEL = "MiniMax-M3.1-Flash-Preview"
MODELSCOPE_BASE = readers.MODELSCOPE_URL.removesuffix("/chat/completions")
SILICONFLOW_BASE = readers.SILICONFLOW_URL.removesuffix("/chat/completions")
MINIMAX_TEXT_MODELS = {"MiniMax-M2", *(f"MiniMax-M2.{version}{speed}" for version in (1, 5, 7) for speed in ("", "-highspeed"))}
DEFAULTS = {
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-pro", "supports_images": False, "thinking": True},
    "doubao": {"base_url": ARK_BASE, "model": "", "supports_images": False, "thinking": True},
    "minimax": {"base_url": MINIMAX_BASE, "model": MINIMAX_MODEL, "supports_images": True, "thinking": True},
    "custom": {"base_url": "", "model": "", "supports_images": False, "thinking": True},
    "modelscope": {"base_url": MODELSCOPE_BASE, "model": provider_catalog.VISION["modelscope"]["default_model"], "supports_images": True, "thinking": True},
    # The existing reader's default is an Instruct model, so it must not be
    # advertised as a reasoning model merely because the service has others.
    "siliconflow": {"base_url": SILICONFLOW_BASE, "model": provider_catalog.VISION["siliconflow"]["default_model"], "supports_images": True, "thinking": False},
}
ENDPOINT_ID = re.compile(r"ep-[A-Za-z0-9][A-Za-z0-9_-]{3,150}\Z")
FEATURE_KEYS = {"knowledge_tags", "ai_answer"}
# 「入库时生成」的每一项对应哪个功能开关。library_jobs.FEATURE_OF 说的是同一件事
# （那边按任务种类写），两份定义必须一致，test_library_ai_settings 里有一条盯着。
ON_INTAKE_FEATURE = {"tags": "knowledge_tags", "answer": "ai_answer"}
KEY_PROVIDER_ORDER = ("deepseek", "minimax", "doubao", "custom", "modelscope", "siliconflow")
PROVIDER_NAMES = {"deepseek": "DeepSeek", "minimax": "MiniMax", "doubao": "豆包", "custom": "其他兼容服务", "modelscope": "魔搭", "siliconflow": "硅基流动"}
# A key is shared only by an explicit local action and only with the same
# provider. Existing independently configured services remain independent.
SHAREABLE_FROM_READING = {"minimax", "modelscope", "siliconflow"}
SHAREABLE_FROM_READING_ERROR = "只能共用读题侧已支持的 MiniMax、魔搭或硅基流动密钥。"
SHARE_SOURCE_MISSING = "读题这边还没有保存 {name} 密钥，请先在“读题与切题”里填一次。"
ASSISTANT_MESSAGE = "由当前操作软件的豆包工作版或 AI 助手领取任务、看图解题，再通过本地工具写回。无需 API；软件不会自动连接桌面助手。生成结果仍需核对。"
UNAVAILABLE = "标签与答案 API 尚未配置并通过显式测试，已暂停 API 生成；请打开“标签与答案”设置。可明确复用已配置的魔搭、MiniMax 或硅基流动，也可配置独立服务；不会自动回退到其他服务。"
_lock = threading.RLock()
API_FIELDS = ("provider", "model", "base_url", "supports_images", "thinking", "reasoning_effort")


class SettingsError(ValueError):
    pass


class ServiceError(SettingsError):
    pass


class ConnectionError(ServiceError):
    """A credential or transport failure invalidates the API connection probe."""


def path() -> Path:
    explicit = (os.environ.get("QB_LIBRARY_AI_SETTINGS_FILE") or os.environ.get("QB_LIBRARY_AI_FILE") or "").strip()
    return Path(explicit).resolve() if explicit else credential_settings.store.credential_path().with_name("library-ai-settings.json")


def key_path(provider: str | None = None) -> Path:
    provider = provider or _load()["provider"]
    explicit = os.environ.get("QB_LIBRARY_AI_CREDENTIAL_FILE", "").strip()
    target = Path(explicit).resolve() if explicit else path().with_name("library-ai.dat")
    return target if provider == "doubao" else target.with_name(f"{target.stem}-{provider}{target.suffix}")


def _recovery_path() -> Path:
    return path().with_name(f"{path().stem}.recovery-pending")


def _load() -> dict:
    try:
        value = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        value = {}
    if not isinstance(value, dict) or value.get("version") not in (1, 2):
        value = {}
    legacy = value.get("version") == 1
    provider = "doubao" if legacy else value.get("provider", "deepseek")
    if provider not in DEFAULTS:
        provider = "deepseek"
    config = {**DEFAULTS[provider], "version": 2, "mode": "assistant", "provider": provider,
              "thinking": DEFAULTS[provider].get("thinking", True), "reasoning_effort": "high", "revision": "", "key_states": {},
              "on_intake": {"tags": False, "answer": False},
              "key_revision": "", "key_configured": False, "verified": False,
              "verified_at": "", "resolved_model": "", **value}
    config["version"] = 2
    config["mode"] = value.get("mode") if value.get("mode") in ("assistant", "api") else "assistant"
    config["provider"] = provider
    timing = config.get("on_intake")
    config["on_intake"] = {kind: isinstance(timing, dict) and timing.get(kind) is True for kind in ("tags", "answer")}
    if legacy:
        config.update(model=str(value.get("endpoint_id") or ""), base_url=ARK_BASE, supports_images=True)
    states = config.get("key_states")
    config["key_states"] = dict(states) if isinstance(states, dict) else {}
    profiles = config.get("provider_profiles")
    config["provider_profiles"] = {name: {field: profile[field] for field in API_FIELDS if field != "provider" and field in profile} for name, profile in profiles.items()
        if name in DEFAULTS and isinstance(profile, dict)} if isinstance(profiles, dict) else {}
    if legacy:
        config["key_states"]["doubao"] = {"revision": config["key_revision"], "configured": config["key_configured"] is True}
    state = config["key_states"].get(provider, {})
    state = state if isinstance(state, dict) else {}
    config["key_revision"] = state.get("revision", config.get("key_revision", ""))
    config["key_configured"] = state.get("configured", config.get("key_configured", False)) is True
    try:
        pending = _recovery_path().read_text(encoding="ascii").strip()
    except FileNotFoundError:
        pending = ""
    except (OSError, UnicodeError):
        pending = "unreadable"
    if pending:
        config.update(revision=f"recovery-{pending}", verified=False, verified_at="", resolved_model="")
    return config


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


@contextmanager
def _settings_transaction(previous: dict, providers, *, include_features: bool = False):
    """Restore encrypted files on storage failure without reviving old jobs.

    Prepare same-directory recovery files before changing any original. Their
    key contents are already encrypted; no decryption or secret logging occurs.
    A non-secret marker pauses API use until commit or a complete rollback.
    """
    targets = [*(key_path(provider) for provider in dict.fromkeys(providers)),
               *([features.path()] if include_features else []), path()]
    backups: dict[Path, Path | None] = {}
    try:
        originals = {}
        for target in targets:
            try:
                originals[target] = target.read_bytes()
            except FileNotFoundError:
                originals[target] = None
        for target, original in originals.items():
            data = original
            if target == path():
                try:
                    restored = json.loads(original.decode("utf-8")) if original is not None else dict(previous)
                except (ValueError, UnicodeError):
                    restored = dict(previous)
                if not isinstance(restored, dict):
                    restored = dict(previous)
                restored.update(revision=uuid.uuid4().hex, verified=False, verified_at="", resolved_model="")
                data = json.dumps(restored, ensure_ascii=False, indent=2).encode("utf-8")
            if data is None:
                backups[target] = None
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, name = tempfile.mkstemp(prefix=".library-ai-rollback-", dir=target.parent)
            backup = Path(name)
            backups[target] = backup
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        _write(_recovery_path(), uuid.uuid4().hex.encode("ascii"))
    except (OSError, credential_settings.CredentialStoreError):
        for backup in backups.values():
            if backup is not None:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass
        raise SettingsError("无法准备安全保存；原密钥和设置未修改，请稍后重试。") from None
    try:
        yield
        _recovery_path().unlink(missing_ok=True)
    except (OSError, credential_settings.CredentialStoreError, features.FeatureError):
        restored_all = True
        for target, backup in backups.items():
            try:
                if backup is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(backup, target)
            except OSError:
                restored_all = False
        # A restored feature file must also replace the in-process cached
        # switches, including when the attempted save had changed them.
        if include_features:
            with features._lock:
                features._cache.update(path=None, mtime=None, values=None)
        if restored_all:
            try:
                _recovery_path().unlink(missing_ok=True)
            except OSError:
                restored_all = False
        if not restored_all:
            # Keep the encrypted recovery files and the pause marker if even
            # restoration is blocked; never report this as a successful save.
            raise SettingsError("保存失败，旧配置未能全部恢复；API 生成已暂停。请检查本机存储后重新保存并测试连接。") from None
        raise SettingsError("保存失败；原密钥和设置已保留，请重新测试连接。") from None
    else:
        for backup in backups.values():
            if backup is not None:
                try:
                    backup.unlink(missing_ok=True)
                except OSError:
                    pass


def _base_url(raw, provider) -> str:
    if not isinstance(raw, str) or len(raw) > 2000:
        raise SettingsError("请填写有效的 API 服务地址。")
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/chat/completions"):
        value = value[:-len("/chat/completions")]
    try:
        parts = urlsplit(value)
        _port = parts.port
    except ValueError:
        raise SettingsError("请填写有效的 API 服务地址。") from None
    local = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    if (parts.scheme != "https" and not (provider == "custom" and parts.scheme == "http" and local)) or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise SettingsError("API 地址使用 HTTPS；本机自定义服务可用 localhost 的 HTTP 地址，不包含密码或查询参数。")
    if provider == "doubao" and value != ARK_BASE:
        raise SettingsError("豆包 API 请使用火山方舟的官方服务地址。")
    if provider == "deepseek" and value != DEFAULTS["deepseek"]["base_url"]:
        raise SettingsError("DeepSeek 请使用官方服务地址；其他兼容地址请选自定义。")
    if provider == "minimax" and value not in {MINIMAX_BASE, "https://api.minimaxi.com/v1"}:
        raise SettingsError("MiniMax 请使用中国区官方 OpenAI 地址 https://api.minimax.cn/v1；其他兼容地址请选自定义。")
    if provider in {"modelscope", "siliconflow"} and value != DEFAULTS[provider]["base_url"]:
        raise SettingsError(f"{PROVIDER_NAMES[provider]}请使用官方服务地址；其他兼容地址请选自定义。")
    return value


def _profile(config: dict) -> dict:
    return {field: config[field] for field in API_FIELDS if field != "provider"}


def _select_provider(previous: dict, provider: str) -> dict:
    if provider == previous["provider"]:
        return dict(previous)
    profile = previous["provider_profiles"].get(provider)
    selected = profile if isinstance(profile, dict) else DEFAULTS[provider]
    return {**previous, **DEFAULTS[provider], "reasoning_effort": "high",
            **{field: selected[field] for field in API_FIELDS if field != "provider" and field in selected},
            "provider": provider}


def _remember_profiles(config: dict, previous: dict) -> None:
    config["provider_profiles"] = {**previous["provider_profiles"],
        previous["provider"]: _profile(previous), config["provider"]: _profile(config)}


def _validate_profile(config: dict) -> None:
    provider = config["provider"]
    if not isinstance(config["model"], str) or len(config["model"]) > 160 or any(c.isspace() or not c.isprintable() for c in config["model"]):
        raise SettingsError("请完整填写模型 ID，不能含空格或换行。")
    if provider == "doubao" and config["model"] and not ENDPOINT_ID.fullmatch(config["model"]):
        raise SettingsError("豆包模型 Endpoint ID 应以 ep- 开头，请从火山方舟完整复制。")
    config["base_url"] = _base_url(config["base_url"], provider)
    if type(config["thinking"]) is not bool or type(config["supports_images"]) is not bool or config["reasoning_effort"] != "high":
        raise SettingsError("思考与图像能力使用 true 或 false；数学默认思考强度为 high。")
    if provider == "minimax":
        if config["model"] in {MINIMAX_MODEL, *MINIMAX_TEXT_MODELS} and not config["thinking"]:
            raise SettingsError("这个 MiniMax 模型始终开启思考，不能关闭；M3.1 使用 adaptive 思考。")
        if config["model"] in MINIMAX_TEXT_MODELS and config["supports_images"]:
            raise SettingsError("MiniMax M2 系列仅支持文字；含图题请明确选择 M3.1、M3 或其他图文模型。")


def saved_key_configuration(provider: str, config: dict | None = None) -> dict:
    """Select only stored key metadata; never switch or save the active API."""
    if not isinstance(provider, str) or provider not in DEFAULTS:
        raise SettingsError("请选择有效的服务商。")
    current = config if config is not None else _load()
    if provider == current["provider"]:
        return {**current}
    state = current.get("key_states", {}).get(provider, {})
    state = state if isinstance(state, dict) else {}
    return {**current, "provider": provider, "key_configured": state.get("configured") is True,
            "key_revision": state.get("revision", "")}


def saved_key_status(config: dict | None = None) -> dict:
    """Configured/count metadata only; missing/corrupt contents aren't opened.

    A count of one means a configured encrypted file exists, not that it can
    be decrypted or that a remote service has accepted the key.
    """
    current = config if config is not None else _load()
    result = {}
    for provider in KEY_PROVIDER_ORDER:
        selected = saved_key_configuration(provider, current)
        configured = selected["key_configured"] is True and key_path(provider).is_file()
        state = current.get("key_states", {}).get(provider, {})
        state = state if isinstance(state, dict) else {}
        result[provider] = {"configured": configured, "count": int(configured),
                            # “这里用的是读题那份密钥” is a fact the teacher needs
                            # before deleting or replacing one of the two.
                            "shared_with_reading": state.get("shared") == "reading"}
    return result


def share_reading_key(provider: str = "minimax") -> dict:
    """Explicitly select a service and copy its reading key into this API's store.

    The two halves of the app keep separate encrypted files, so the same key
    used to have to be typed twice.  This is the one operation that bridges
    them, and it is never automatic: the teacher presses the button.  The
    reading store may hold up to eight accounts; the API side holds one, so
    the first saved account is taken and the answer says which one. A saved
    target model profile is retained; otherwise that provider's preset is used.
    Other keys, generation switches and task content are not changed.
    """
    if not isinstance(provider, str) or provider not in SHAREABLE_FROM_READING:
        raise SettingsError(SHAREABLE_FROM_READING_ERROR)
    name = PROVIDER_NAMES[provider]
    with _lock:
        current = _load()
        config = _select_provider(current, provider)
        config["mode"] = "api"
        _validate_profile(config)
        if not config["model"] or not config["base_url"]:
            raise SettingsError("共用密钥前，请为这家服务保存有效的地址和模型 ID。")
        try:
            key = credential_settings.reveal_saved_key(provider, 0)
        except (credential_settings.CredentialStoreError, credential_settings.CredentialValidationError):
            raise SettingsError(SHARE_SOURCE_MISSING.format(name=name)) from None
        if not isinstance(key, str) or not key or any(c.isspace() for c in key):
            raise SettingsError(SHARE_SOURCE_MISSING.format(name=name))
        _remember_profiles(config, current)
        with _settings_transaction(current, [provider]):
            config["key_revision"] = uuid.uuid4().hex
            protected = credential_settings.store._transform(json.dumps(
                {"version": 1, "revision": config["key_revision"], "key": key}).encode(), protect=True)
            _write(key_path(provider), protected)
            # A copied key is a brand new credential: the previous “已测通”
            # verdict was about the key that was just replaced.
            config.update(key_configured=True, verified=False, verified_at="", resolved_model="",
                          revision=uuid.uuid4().hex)
            config["key_states"][provider] = {"revision": config["key_revision"], "configured": True,
                                              "shared": "reading"}
            _write_settings(config)
    return public_status()


def public_status() -> dict:
    config = _load()
    keys = saved_key_status(config)
    key_configured = keys[config["provider"]]["configured"]
    configured = bool(key_configured and config.get("model") and config.get("base_url"))
    verified = configured and config.get("verified") is True
    assistant = config["mode"] == "assistant"
    switches = features.load()
    return {key: config[key] for key in ("mode", *API_FIELDS)} | {
        "endpoint_id": config["model"] if config["provider"] == "doubao" else "",
        "key_configured": key_configured, "key_count": int(key_configured), "keys": keys,
        # Which providers the answers API could explicitly select and borrow
        # a key for. Whether the
        # reading side actually holds one is *not* answered here: this function
        # must never open the OCR credential store, so the page is told by the
        # reading side's own status instead.
        "shareable_from_reading": sorted(SHAREABLE_FROM_READING),
        "provider_profiles": {**config["provider_profiles"], config["provider"]: _profile(config)},
        "configured": configured, "verified": verified,
        "api_ready": verified, "ready": assistant or verified,
        "status": "assistant" if assistant else "verified" if verified else "unverified" if configured else "missing",
        "verified_at": str(config.get("verified_at") or "") if verified else "",
        "resolved_model": str(config.get("resolved_model") or "") if verified else "",
        "features": {key: switches[key] for key in sorted(FEATURE_KEYS)},
        "knowledge": _knowledge_summary(switches["knowledge_tags"]),
        "on_intake": config["on_intake"],
        "message": ASSISTANT_MESSAGE if assistant else "独立 API 已通过合成题连接测试；按所选图像与思考设置生成，答案仍需核对。" if verified else UNAVAILABLE,
    }


def _knowledge_summary(enabled: bool) -> dict:
    """目录有多大、放在哪。关着标签功能时不必去读这个文件。"""
    if not enabled:
        return {"total": 0, "chapters": 0, "file": ""}
    try:
        points = knowledge.load()
    except OSError:
        return {"total": 0, "chapters": 0, "file": str(knowledge.path())}
    return {"total": len(points),
            "chapters": len({item["chapter"] for item in points if item["chapter"]}),
            "file": str(knowledge.path())}


def _key_operation(operation, label: str = "API Key") -> tuple[str, str]:
    """Validate one keep/replace/clear instruction and return (action, key)."""

    if not isinstance(operation, dict) or set(operation) - {"action", "value"}:
        raise SettingsError(f"请按保持、替换或清除保存{label}。")
    action = operation.get("action")
    if not isinstance(action, str) or action not in {"keep", "clear", "replace"} or (action != "replace" and "value" in operation):
        raise SettingsError(f"请按保持、替换或清除保存{label}。")
    if action != "replace":
        return action, ""
    raw = operation.get("value")
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 4096:
        raise SettingsError("请填写完整的 API Key。")
    key = raw.strip()
    if any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
        raise SettingsError("API Key 不能包含空格、换行或非英文字符。")
    return action, key


def _write_provider_key(provider: str, action: str, key: str) -> dict:
    """Store or drop one provider's key file and return its stored metadata."""

    if action == "replace":
        revision = uuid.uuid4().hex
        protected = credential_settings.store._transform(json.dumps({"version": 1, "revision": revision, "key": key}).encode(), protect=True)
        _write(key_path(provider), protected)
        return {"revision": revision, "configured": True}
    key_path(provider).unlink(missing_ok=True)
    return {"revision": "", "configured": False}


def save(payload: dict) -> dict:
    allowed = {"features", "mode", *API_FIELDS, "endpoint_id", "key", "keys", "on_intake", "provider_profiles"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise SettingsError("标签与参考答案设置格式不正确。")
    changes = payload.get("features", {})
    if not isinstance(changes, dict) or set(changes) - FEATURE_KEYS or not all(type(v) is bool for v in changes.values()):
        raise SettingsError("标签与参考答案开关只能分别设为 true 或 false。")
    timing = payload.get("on_intake", {})
    if not isinstance(timing, dict) or set(timing) - {"tags", "answer"} or not all(type(v) is bool for v in timing.values()):
        raise SettingsError("录入时生成的开关只能分别设为 true 或 false。")
    action, key = _key_operation(payload.get("key", {"action": "keep"}))
    # Keys for the providers that are not in use right now.  Each service keeps
    # its own encrypted file, so switching provider in the UI must not throw the
    # other one away, and filling in a second service must not require saving
    # twice.  These never touch the active provider's revision or test result.
    extra = payload.get("keys", {})
    if not isinstance(extra, dict) or set(extra) - set(DEFAULTS):
        raise SettingsError("标签与参考答案设置格式不正确。")
    extra_keys = {name: _key_operation(operation, f"{PROVIDER_NAMES[name]}密钥") for name, operation in extra.items()}
    extra_profiles = payload.get("provider_profiles", {})
    profile_fields = set(API_FIELDS) - {"provider"}
    if (not isinstance(extra_profiles, dict) or set(extra_profiles) - set(DEFAULTS)
        or any(not isinstance(profile, dict) or set(profile) - profile_fields for profile in extra_profiles.values())):
        raise SettingsError("备用服务模型配置格式不正确。")
    with _lock:
        previous = _load()
        mode = payload.get("mode", previous["mode"])
        provider = payload.get("provider", "doubao" if "endpoint_id" in payload else previous["provider"])
        if mode not in ("assistant", "api") or not isinstance(provider, str) or provider not in DEFAULTS:
            raise SettingsError("请选择当前 AI 助手或独立 API，以及有效的服务商。")
        switching = provider != previous["provider"]
        config = {**_select_provider(previous, provider), "mode": mode}
        config["on_intake"] = {**previous["on_intake"], **timing}
        for field in API_FIELDS:
            if field in payload:
                config[field] = payload[field]
        if "endpoint_id" in payload:
            if "model" in payload and payload["model"] != payload["endpoint_id"]:
                raise SettingsError("模型 ID 不一致。")
            config["model"] = payload["endpoint_id"]
        _validate_profile(config)
        if action == "replace" and not (config["model"] and config["base_url"]):
            raise SettingsError("保存 API Key 前，请填写服务地址和模型 ID。")
        if provider in extra_keys:
            raise SettingsError(f"当前服务商是{PROVIDER_NAMES[provider]}，它的密钥请在它自己那一栏保存。")
        if provider in extra_profiles:
            raise SettingsError("当前服务的模型配置请使用当前输入栏保存。")
        validated_profiles = {}
        for name, profile in extra_profiles.items():
            selected = {**_select_provider(previous, name), **profile}
            _validate_profile(selected)
            validated_profiles[name] = _profile(selected)
        state = config["key_states"].get(provider, {}) if switching else {"revision": previous["key_revision"], "configured": previous["key_configured"]}
        state = state if isinstance(state, dict) else {}
        config.update(key_revision=state.get("revision", ""), key_configured=state.get("configured") is True)
        changed = action != "keep" or any(previous.get(k) != config.get(k) for k in API_FIELDS) or str(previous["revision"]).startswith("recovery-")
        if changed:
            config.update(revision=uuid.uuid4().hex, verified=False, verified_at="", resolved_model="")
        config.pop("endpoint_id", None)
        _remember_profiles(config, previous)
        config["provider_profiles"].update(validated_profiles)
        with _settings_transaction(previous, [provider, *extra_keys], include_features=bool(changes)):
            if changed:
                _write_settings(config)
            if action == "replace":
                config["key_revision"] = uuid.uuid4().hex
                protected = credential_settings.store._transform(json.dumps({"version": 1, "revision": config["key_revision"], "key": key}).encode(), protect=True)
                _write(key_path(provider), protected)
                config["key_configured"] = True
            elif action == "clear":
                key_path(provider).unlink(missing_ok=True)
                config.update(key_revision="", key_configured=False)
            prior_key_state = previous["key_states"].get(provider)
            config["key_states"][provider] = {"revision": config["key_revision"], "configured": config["key_configured"],
                **({"shared": "reading"} if action == "keep" and isinstance(prior_key_state, dict) and prior_key_state.get("shared") == "reading" else {})}
            # 备用服务商的密钥各自落在自己的文件里：不动当前服务商的版本号和
            # 已测通状态，所以在这里多填一家不会让正在用的连接重新变成未测试。
            for name, (extra_action, extra_key) in extra_keys.items():
                if extra_action == "keep":
                    continue
                config["key_states"][name] = _write_provider_key(name, extra_action, extra_key)
            _write_settings(config)
            if changes:
                features.save(changes)
                # 功能关着的时候，「入库时生成」不能留在开着的状态。
                # 再次打开功能也不会悄悄恢复一次入库请求。
                stale = {kind: False for kind, feature in ON_INTAKE_FEATURE.items() if not features.enabled(feature)}
                if stale and {**config["on_intake"], **stale} != config["on_intake"]:
                    config["on_intake"] = {**config["on_intake"], **stale}
                    _write_settings(config)
    return public_status()


def ensure_ready(kind: str | None = None) -> dict:
    if kind and not features.enabled({"answer": "ai_answer", "tags": "knowledge_tags"}.get(kind, "")):
        raise ServiceError("这个功能已在“标签与参考答案设置”里关掉，已暂停生成。")
    result = public_status()
    if not result["ready"]:
        raise ServiceError(UNAVAILABLE)
    return result


def ensure_api_ready() -> dict:
    """An explicitly selected answer job uses the API without changing settings."""
    result = public_status()
    if result.get("api_ready") is not True:
        raise ServiceError(UNAVAILABLE)
    return {**result, "mode": "api"}


def execution_snapshot(*, explicit_api: bool = False) -> dict:
    config = _load()
    snapshot = {key: config.get(key) for key in ("mode", *API_FIELDS, "revision", "key_revision")}
    if explicit_api:
        snapshot.update(mode="api", explicit_api=True)
    return snapshot


def require_snapshot(snapshot: dict) -> None:
    explicit = isinstance(snapshot, dict) and snapshot.get("explicit_api") is True
    if not snapshot or snapshot != execution_snapshot(explicit_api=explicit) or snapshot.get("mode") != "api":
        raise ServiceError("执行方式或 API 设置已变化，旧 API 任务未执行/写回；请重新排队。")


def _key(config: dict) -> str:
    try:
        value = json.loads(credential_settings.store._transform(key_path(config["provider"]).read_bytes(), protect=False).decode("utf-8"))
        key = value.get("key", "") if isinstance(value, dict) and value.get("version") == 1 else ""
        if not isinstance(key, str) or not key or any(c.isspace() for c in key) or value.get("revision") != config.get("key_revision"):
            raise ValueError
        return key
    except (OSError, ValueError, credential_settings.CredentialStoreError):
        raise ConnectionError("API Key 无法读取，已暂停 API 生成；请在独立设置中重新保存。") from None


def _same_configuration(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in (*API_FIELDS, "revision", "key_revision"))


def _mark_unverified(config: dict) -> None:
    with _lock:
        current = _load()
        if _same_configuration(config, current):
            _write_settings({**current, "verified": False, "verified_at": "", "resolved_model": ""})


def _response_content(message: dict) -> tuple[str, bool]:
    """Compatible reasoning may be separate or wrapped; only return the answer."""
    if not isinstance(message, dict):
        raise ValueError
    text = message.get("content")
    if not isinstance(text, str):
        raise ValueError
    reasoning = message.get("reasoning_content")
    details = message.get("reasoning_details")
    thinking = isinstance(reasoning, str) and bool(reasoning.strip())
    if isinstance(details, list):
        thinking = thinking or any(isinstance(item, dict) and isinstance(item.get("text"), str) and bool(item["text"].strip()) for item in details)
    blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.S)
    thinking = thinking or any(part.strip() for part in blocks)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    if not text or "<think>" in text or "</think>" in text:
        raise ValueError
    return text, thinking


def _needs_reasoning_evidence(config: dict) -> bool:
    # MiniMax's adaptive models choose how much reasoning to expose. A valid
    # final answer to a trivial probe is not invalid merely because one SDK
    # omitted the private reasoning field. The request still follows the
    # model's documented thinking controls; image probes verify their result.
    return config["thinking"] and config["provider"] != "minimax"


def _request(config: dict, prompt: str, image_urls: list[str], max_tokens: int, *, kind=None, explicit_api=False) -> tuple[str, dict]:
    if image_urls and not config["supports_images"]:
        raise ServiceError("这道题有配图，所选 API 未声明支持图片；已暂停，改用当前助手或明确支持图文的模型。")
    if not _same_configuration(config, _load()) or (not explicit_api and (config["mode"] != "api" or _load()["mode"] != "api")):
        raise ServiceError("请求前执行方式或 API 设置已变化，旧任务未发起。")
    if kind:
        ensure_ready(kind)
    key = _key(config)
    content = [{"type": "text", "text": prompt}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls)
    payload = {"model": config["model"], "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens, "stream": False}
    if config["provider"] == "minimax":
        # Official Chinese OpenAI contract (2026-10-03). M3.1 requires
        # adaptive thinking; M3 has no effort control and M2 is text-only.
        # Unknown editable IDs get the common protocol without guessing
        # unsupported reasoning parameters. Never retry a different service.
        payload["max_completion_tokens"] = payload.pop("max_tokens")
        payload["reasoning_split"] = True
        if config["model"] == MINIMAX_MODEL:
            if not config["thinking"]:
                raise ServiceError("MiniMax M3.1 的思考不能关闭，请重新保存模型设置。")
            payload["thinking"] = {"type": "adaptive"}
            payload["reasoning_effort"] = config["reasoning_effort"]
        elif config["model"] == "MiniMax-M3":
            payload["thinking"] = {"type": "adaptive" if config["thinking"] else "disabled"}
        if config["model"] in MINIMAX_TEXT_MODELS and image_urls:
            raise ServiceError("MiniMax M2 系列不能读取图片，已暂停；请明确选择图文模型。")
    elif config["provider"] in {"modelscope", "siliconflow"}:
        # Both official gateways use this boolean extension, including vision
        # requests. Qwen models do not accept DeepSeek/MiniMax's effort format.
        # A model without reasoning cannot pass a thinking-enabled probe.
        payload["enable_thinking"] = config["thinking"]
    elif config["thinking"]:
        payload["thinking"] = {"type": "enabled"}
        if config["provider"] != "doubao":
            payload["reasoning_effort"] = "high"
    elif config["provider"] in ("doubao", "deepseek"):
        payload["thinking"] = {"type": "disabled"}
    if kind:
        ensure_ready(kind)
    try:
        response = requests.post(config["base_url"] + "/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {key}"}, timeout=(15, 180), allow_redirects=False)
        if response.status_code != 200:
            raise ConnectionError(f"独立 API 请求未完成（HTTP {int(response.status_code)}），已暂停，请核对服务配置后显式重试。")
        body = response.json()
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ConnectionError("答案超过本次长度限制，未保存不完整结果。")
        message = choice["message"]
        text, thinking = _response_content(message)
        return text, {"model": str(body.get("model") or config["model"]), "thinking": thinking}
    except ServiceError:
        raise
    except requests.RequestException:
        raise ConnectionError("独立 API 网络请求未完成，已暂停，请稍后显式重试。") from None
    except (ValueError, TypeError, KeyError, IndexError):
        raise ConnectionError("独立 API 没有返回完整可用的答案，未保存结果。") from None


def chat(prompt: str, image_urls: list[str], *, kind: str, max_tokens: int = 12000, explicit_api=False) -> tuple[str, str]:
    if explicit_api and kind is not None:
        raise ServiceError("本次 API 执行仅适用于明确选择的答案解析任务。")
    ensure_api_ready() if explicit_api else ensure_ready(kind)
    config = _load()
    if not explicit_api and config["mode"] != "api":
        raise ServiceError("当前是助手模式：请由当前 AI 助手通过本地工具领取并提交任务，不调用云 API。")
    try:
        if explicit_api:
            text, evidence = _request(config, prompt, image_urls, max_tokens, kind=kind, explicit_api=True)
        else:
            text, evidence = _request(config, prompt, image_urls, max_tokens, kind=kind)
        if _needs_reasoning_evidence(config) and not evidence["thinking"]:
            raise ConnectionError("本次响应未返回所选思考内容，结果未保存；请核对模型思考配置。")
    except ConnectionError:
        _mark_unverified(config)
        raise
    if not _same_configuration(config, _load()) or (not explicit_api and _load()["mode"] != "api"):
        raise ServiceError("生成期间执行方式或 API 设置已变化，旧结果未保存。")
    ensure_api_ready() if explicit_api else ensure_ready(kind)
    return text, f"{config['provider']} API · {evidence['model']}"


def test_connection(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload != {"confirm": True}:
        raise SettingsError("请明确确认运行一次连接测试；这可能产生 API 费用。")
    config = _load()
    if config["mode"] != "api" or not public_status()["configured"]:
        raise ServiceError(UNAVAILABLE)
    urls = []
    prompt = "这是合成连接测试，不是用户试卷。计算 1+1，只输出【答案】2。"
    if config["supports_images"]:
        image = Image.new("RGB", (120, 70), "white")
        drawing = ImageDraw.Draw(image)
        for box in ((15, 20, 35, 40), (70, 20, 90, 40)):
            drawing.ellipse(box, fill="black")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        urls = ["data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")]
        prompt = "这是合成连接测试，不是用户试卷。计算1+1，并数图中黑色圆点。只输出【答案】数值【图示】圆点个数。"
    try:
        text, evidence = _request(config, prompt, urls, 12000)
        expected = r"【答案】\s*\$?2\$?\s*【图示】\s*\$?2\$?" if urls else r"【答案】\s*\$?2\$?"
        if not re.search(expected, text) or (_needs_reasoning_evidence(config) and not evidence["thinking"]):
            raise ServiceError("API 未通过所选合成数学/图像与思考响应测试，API 生成保持暂停；这不是答案质量评估。")
    except ServiceError:
        _mark_unverified(config)
        raise
    with _lock:
        current = _load()
        if not _same_configuration(config, current) or current["mode"] != "api":
            raise ServiceError("测试期间设置已变化，测试结果未沿用，请重新测试。")
        _write_settings({**current, "verified": True, "verified_at": timezone.now().isoformat(), "resolved_model": evidence["model"]})
    return public_status()
