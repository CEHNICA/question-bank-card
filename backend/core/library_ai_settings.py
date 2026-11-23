"""Optional enrichment by the current assistant or an explicitly configured API.

Saving never contacts a service. API credentials are independent of OCR and
provider-specific DPAPI files; status reads only their non-secret metadata.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import requests
from django.utils import timezone
from PIL import Image, ImageDraw

from . import credential_settings, features, provider_catalog

logger = logging.getLogger(__name__)

ARK_BASE = provider_catalog.ARK_BASE
ARK_URL = ARK_BASE + "/chat/completions"
MINIMAX_BASE = provider_catalog.MINIMAX_API_BASE
MINIMAX_MODEL = provider_catalog.MINIMAX_ANSWER_MODEL
MODELSCOPE_BASE = provider_catalog.MODELSCOPE_BASE
SILICONFLOW_BASE = provider_catalog.SILICONFLOW_BASE
MINIMAX_TEXT_MODELS = set(provider_catalog.MINIMAX_TEXT_MODELS)
# 各解题服务商的默认端点/模型目录统一由 provider_catalog 提供
DEFAULTS = {key: dict(value) for key, value in provider_catalog.ANSWER_DEFAULTS.items()}
ENDPOINT_ID = re.compile(r"ep-[A-Za-z0-9][A-Za-z0-9_-]{3,150}\Z")
FEATURE_KEYS = {"knowledge_tags", "ai_answer"}
# 「入库时生成」的每一项对应哪个功能开关。library_jobs.FEATURE_OF 说的是同一件事
# （那边按任务种类写），两份定义必须一致，test_library_ai_settings 里有一条盯着。
ON_INTAKE_FEATURE = {"tags": "knowledge_tags", "answer": "ai_answer"}
KEY_PROVIDER_ORDER = provider_catalog.ANSWER_ORDER
PROVIDER_NAMES = {key: provider_catalog.PROVIDER_NAMES[key] for key in KEY_PROVIDER_ORDER}
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


class TransientServiceError(ServiceError):
    """上游临时拥堵、网关故障或单次请求超时（不清空已测通状态）。"""


class ResponseFormatError(ServiceError):
    """单题输入被拒、响应残缺或格式不符（仅当前题失败，不清空已测通状态）。"""


class TruncationError(ServiceError):
    """The answer was cut off by the token budget.

    This is a property of *this* request, not of the service: the connection is
    still good, the key still works.  It must never clear the verified flag —
    otherwise one long question silently pauses every AI feature on the site
    until the user manually runs the connection probe again.
    """


class RateLimitError(ServiceError):
    """临时并发或速率受限（HTTP 429），不清空已测通状态。"""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# A truncated answer is retried once with a bigger budget on the same service.
# It never falls back to another model, key or endpoint.
TRUNCATION_RETRY_TOKEN_CAP = 32000
RATE_LIMIT_BACKOFF = (2.0, 5.0)
_TRANSIENT_HTTP_STATUS = frozenset({408, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529})
_SECRET_REDACT = re.compile(r"https?://\S+|(?:sk|nvapi)-[A-Za-z0-9_\-]+|AIza[A-Za-z0-9_\-]+|Bearer\s+\S+")
_AUTH_ERROR_PATTERN = re.compile(
    r"(?:api[ _-]?key|invalid[ _-]?token|unauthorized|authentication|permission[ _-]?denied|access[ _-]?denied)", re.I
)
_PROVIDER_GATES: dict[tuple[str, int], threading.BoundedSemaphore] = {}
_KEY_POOL_CURSOR: dict[str, int] = {}
_KEY_COOLDOWN_UNTIL: dict[tuple[str, str], float] = {}
_PROVIDER_REST_UNTIL: dict[str, float] = {}
_RATE_LIMITED_FREE_PROVIDERS = frozenset({"zhipu", "openrouter"})
AUTO_ROUTE_TEXT_ORDER = (
    "zhipu",
    "nvidia",
    "modelscope",
    "openrouter",
    "gemini",
    "siliconflow",
    "deepseek",
    "minimax",
    "doubao",
    "custom",
)
AUTO_ROUTE_VISION_ORDER = (
    "nvidia",
    "modelscope",
    "gemini",
    "openrouter",
    "siliconflow",
    "minimax",
    "zhipu",
    "deepseek",
    "doubao",
    "custom",
)


def path() -> Path:
    explicit = (os.environ.get("QB_LIBRARY_AI_SETTINGS_FILE") or os.environ.get("QB_LIBRARY_AI_FILE") or "").strip()
    return (
        Path(explicit).resolve()
        if explicit
        else credential_settings.store.credential_path().with_name("library-ai-settings.json")
    )


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
    config = {
        **DEFAULTS[provider],
        "version": 2,
        "mode": "assistant",
        "provider": provider,
        "thinking": DEFAULTS[provider].get("thinking", True),
        "reasoning_effort": "high",
        "revision": "",
        "key_states": {},
        "on_intake": {"tags": False, "answer": False},
        "key_revision": "",
        "key_configured": False,
        "verified": False,
        "verified_at": "",
        "resolved_model": "",
        "unverified_reason": "",
        **value,
    }
    config["version"] = 2
    config["mode"] = value.get("mode") if value.get("mode") in ("assistant", "api") else "assistant"
    config["provider"] = provider
    timing = config.get("on_intake")
    config["on_intake"] = {kind: isinstance(timing, dict) and timing.get(kind) is True for kind in ("tags", "answer")}
    # New tag proposals are only created by an explicit action in the formal library.
    config["on_intake"]["tags"] = False
    if legacy:
        config.update(model=str(value.get("endpoint_id") or ""), base_url=ARK_BASE, supports_images=True)
    states = config.get("key_states")
    config["key_states"] = dict(states) if isinstance(states, dict) else {}
    profiles = config.get("provider_profiles")
    config["provider_profiles"] = (
        {
            name: {field: profile[field] for field in API_FIELDS if field != "provider" and field in profile}
            for name, profile in profiles.items()
            if name in DEFAULTS and isinstance(profile, dict)
        }
        if isinstance(profiles, dict)
        else {}
    )
    if legacy:
        config["key_states"]["doubao"] = {
            "revision": config["key_revision"],
            "configured": config["key_configured"] is True,
        }
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
        config.update(
            revision=f"recovery-{pending}", verified=False, verified_at="", resolved_model="", unverified_reason=""
        )
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
    targets = [
        *(key_path(provider) for provider in dict.fromkeys(providers)),
        *([features.path()] if include_features else []),
        path(),
    ]
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
                restored.update(
                    revision=uuid.uuid4().hex, verified=False, verified_at="", resolved_model="", unverified_reason=""
                )
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
            raise SettingsError(
                "保存失败，旧配置未能全部恢复；API 生成已暂停。请检查本机存储后重新保存并测试连接。"
            ) from None
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
        value = value[: -len("/chat/completions")]
    try:
        parts = urlsplit(value)
        _port = parts.port
    except ValueError:
        raise SettingsError("请填写有效的 API 服务地址。") from None
    local = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    if (
        (parts.scheme != "https" and not (provider == "custom" and parts.scheme == "http" and local))
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise SettingsError("API 地址使用 HTTPS；本机自定义服务可用 localhost 的 HTTP 地址，不包含密码或查询参数。")
    if provider == "doubao" and value != ARK_BASE:
        raise SettingsError("豆包 API 请使用火山方舟的官方服务地址。")
    if provider == "deepseek" and value != DEFAULTS["deepseek"]["base_url"]:
        raise SettingsError("DeepSeek 请使用官方服务地址；其他兼容地址请选自定义。")
    if provider == "nvidia" and value != DEFAULTS["nvidia"]["base_url"]:
        raise SettingsError(
            "英伟达 NIM 请使用官方服务地址 https://integrate.api.nvidia.com/v1；其他兼容地址请选自定义。"
        )
    if provider == "gemini" and value != DEFAULTS["gemini"]["base_url"]:
        raise SettingsError(
            "Google Gemini 请使用官方 OpenAI 兼容地址 https://generativelanguage.googleapis.com/v1beta/openai；其他兼容地址请选自定义。"
        )
    if provider == "zhipu" and value != DEFAULTS["zhipu"]["base_url"]:
        raise SettingsError(
            "智谱 BigModel 请使用官方地址 https://open.bigmodel.cn/api/paas/v4；其他兼容地址请选自定义。"
        )
    if provider == "openrouter" and value != DEFAULTS["openrouter"]["base_url"]:
        raise SettingsError("OpenRouter 请使用官方地址 https://openrouter.ai/api/v1；其他兼容地址请选自定义。")
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
    return {
        **previous,
        **DEFAULTS[provider],
        "reasoning_effort": "high",
        **{field: selected[field] for field in API_FIELDS if field != "provider" and field in selected},
        "provider": provider,
    }


def _remember_profiles(config: dict, previous: dict) -> None:
    config["provider_profiles"] = {
        **previous["provider_profiles"],
        previous["provider"]: _profile(previous),
        config["provider"]: _profile(config),
    }


def _validate_profile(config: dict) -> None:
    provider = config["provider"]
    if (
        not isinstance(config["model"], str)
        or len(config["model"]) > 160
        or any(c.isspace() or not c.isprintable() for c in config["model"])
    ):
        raise SettingsError("请完整填写模型 ID，不能含空格或换行。")
    if provider == "doubao" and config["model"] and not ENDPOINT_ID.fullmatch(config["model"]):
        raise SettingsError("豆包模型 Endpoint ID 应以 ep- 开头，请从火山方舟完整复制。")
    config["base_url"] = _base_url(config["base_url"], provider)
    if (
        type(config["thinking"]) is not bool
        or type(config["supports_images"]) is not bool
        or config["reasoning_effort"] != "high"
    ):
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
    return {
        **current,
        "provider": provider,
        "key_configured": state.get("configured") is True,
        "key_revision": state.get("revision", ""),
    }


def _is_isolated_legacy_key_store() -> bool:
    """当测试或外部显式指定 QB_LIBRARY_AI_CREDENTIAL_FILE 时走独立密钥文件模式。"""

    return bool(os.environ.get("QB_LIBRARY_AI_CREDENTIAL_FILE", "").strip())


def saved_key_status(config: dict | None = None) -> dict:
    """仅返回密钥可用状态与数量元数据，绝不读取/解密凭据文件。"""

    current = config if config is not None else _load()
    isolated = _is_isolated_legacy_key_store()
    env_status = {} if isolated else credential_settings.public_all_environment_status()
    result = {}
    for provider in KEY_PROVIDER_ORDER:
        selected = saved_key_configuration(provider, current)
        state = current.get("key_states", {}).get(provider, {})
        state = state if isinstance(state, dict) else {}
        legacy_configured = selected["key_configured"] is True and key_path(provider).is_file()
        if isolated:
            configured = legacy_configured
            count = int(configured)
        else:
            env_item = env_status.get(provider, {})
            env_count = int(env_item.get("count", 0)) if isinstance(env_item, dict) else 0
            env_configured = bool(env_item.get("configured")) if isinstance(env_item, dict) else False
            if env_configured or env_count > 0:
                configured = True
                count = max(1, env_count)
            elif not current.get("keys_migrated_to_unified") and legacy_configured:
                configured = True
                count = 1
            else:
                configured = False
                count = 0
        result[provider] = {
            "configured": configured,
            "count": count,
            "shared_with_reading": state.get("shared") == "reading",
        }
    return result


def migrate_legacy_keys_to_unified() -> bool:
    """将历史上独立保存的 library-ai*.dat 密钥无损合并进统一 credentials.dat。

    已迁移或处于独立密钥隔离模式时跳过，且保留原始 .dat 文件作为只读备份不删除。
    """

    if _is_isolated_legacy_key_store():
        return False
    with _lock:
        current = _load()
        if current.get("keys_migrated_to_unified") is True:
            return False
        changed_any = False
        for provider in KEY_PROVIDER_ORDER:
            legacy_file = key_path(provider)
            if not legacy_file.is_file():
                continue
            try:
                raw = json.loads(
                    credential_settings.store._transform(legacy_file.read_bytes(), protect=False).decode("utf-8")
                )
                key = raw.get("key", "") if isinstance(raw, dict) and raw.get("version") == 1 else ""
                if isinstance(key, str) and key.strip() and not any(c.isspace() for c in key.strip()):
                    if credential_settings.store.merge_pool_accounts(provider, [key.strip()]):
                        changed_any = True
            except (OSError, ValueError, TypeError, credential_settings.CredentialStoreError):
                # 单份历史凭据损坏时跳过，不阻塞其余服务与页面打开
                continue
        if changed_any:
            credential_settings.refresh_public_environment(include_all=True)
        current["keys_migrated_to_unified"] = True
        if changed_any or any(key_path(p).is_file() for p in KEY_PROVIDER_ORDER):
            _write_settings(current)
        elif not current.get("revision"):
            # 无需落盘无意义文件，保持未创建状态即可
            pass
        else:
            _write_settings(current)
        return changed_any


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
            protected = credential_settings.store._transform(
                json.dumps({"version": 1, "revision": config["key_revision"], "key": key}).encode(), protect=True
            )
            _write(key_path(provider), protected)
            # A copied key is a brand new credential: the previous “已测通”
            # verdict was about the key that was just replaced.
            config.update(
                key_configured=True,
                verified=False,
                verified_at="",
                resolved_model="",
                unverified_reason="",
                revision=uuid.uuid4().hex,
            )
            config["key_states"][provider] = {
                "revision": config["key_revision"],
                "configured": True,
                "shared": "reading",
            }
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
        "key_configured": key_configured,
        "key_count": int(key_configured),
        "keys": keys,
        # Which providers the answers API could explicitly select and borrow
        # a key for. Whether the
        # reading side actually holds one is *not* answered here: this function
        # must never open the OCR credential store, so the page is told by the
        # reading side's own status instead.
        "shareable_from_reading": sorted(SHAREABLE_FROM_READING),
        "provider_profiles": {**config["provider_profiles"], config["provider"]: _profile(config)},
        "configured": configured,
        "verified": verified,
        "api_ready": verified,
        "ready": assistant or verified,
        "status": "assistant" if assistant else "verified" if verified else "unverified" if configured else "missing",
        "verified_at": str(config.get("verified_at") or "") if verified else "",
        "resolved_model": str(config.get("resolved_model") or "") if verified else "",
        "unverified_reason": "" if verified else str(config.get("unverified_reason") or ""),
        "features": {key: switches[key] for key in sorted(FEATURE_KEYS)},
        "knowledge": _knowledge_summary(switches["knowledge_tags"]),
        "on_intake": config["on_intake"],
        "message": ASSISTANT_MESSAGE
        if assistant
        else "独立 API 已通过合成题连接测试；按所选图像与思考设置生成，答案仍需核对。"
        if verified
        else UNAVAILABLE,
    }


def _knowledge_summary(enabled: bool) -> dict:
    """Summarize the live formal-library taxonomy, never the legacy text file."""
    if not enabled:
        return {"total": 0, "chapters": 0, "source": "正式题库标签目录"}
    from .library_tagging import knowledge_directory

    points = knowledge_directory()
    return {
        "total": len(points),
        "chapters": len({item["chapter"] for item in points if item["chapter"]}),
        "source": "正式题库标签目录",
    }


def _key_operation(operation, label: str = "API Key") -> tuple[str, str]:
    """Validate one keep/replace/clear instruction and return (action, key)."""

    if not isinstance(operation, dict) or set(operation) - {"action", "value"}:
        raise SettingsError(f"请按保持、替换或清除保存{label}。")
    action = operation.get("action")
    if (
        not isinstance(action, str)
        or action not in {"keep", "clear", "replace"}
        or (action != "replace" and "value" in operation)
    ):
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
        protected = credential_settings.store._transform(
            json.dumps({"version": 1, "revision": revision, "key": key}).encode(), protect=True
        )
        _write(key_path(provider), protected)
        return {"revision": revision, "configured": True}
    key_path(provider).unlink(missing_ok=True)
    return {"revision": "", "configured": False}


def save(payload: dict) -> dict:
    allowed = {"features", "mode", *API_FIELDS, "endpoint_id", "key", "keys", "on_intake", "provider_profiles"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise SettingsError("标签与参考答案设置格式不正确。")
    changes = payload.get("features", {})
    if (
        not isinstance(changes, dict)
        or set(changes) - FEATURE_KEYS
        or not all(type(v) is bool for v in changes.values())
    ):
        raise SettingsError("标签与参考答案开关只能分别设为 true 或 false。")
    timing = payload.get("on_intake", {})
    if (
        not isinstance(timing, dict)
        or set(timing) - {"tags", "answer"}
        or not all(type(v) is bool for v in timing.values())
    ):
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
    if (
        not isinstance(extra_profiles, dict)
        or set(extra_profiles) - set(DEFAULTS)
        or any(not isinstance(profile, dict) or set(profile) - profile_fields for profile in extra_profiles.values())
    ):
        raise SettingsError("备用服务模型配置格式不正确。")
    with _lock:
        previous = _load()
        mode = payload.get("mode", previous["mode"])
        provider = payload.get("provider", "doubao" if "endpoint_id" in payload else previous["provider"])
        if mode not in ("assistant", "api") or not isinstance(provider, str) or provider not in DEFAULTS:
            raise SettingsError("请选择当前 AI 助手或独立 API，以及有效的服务商。")
        switching = provider != previous["provider"]
        config = {**_select_provider(previous, provider), "mode": mode}
        config["on_intake"] = {**previous["on_intake"], **timing, "tags": False}
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
        state = (
            config["key_states"].get(provider, {})
            if switching
            else {"revision": previous["key_revision"], "configured": previous["key_configured"]}
        )
        state = state if isinstance(state, dict) else {}
        config.update(key_revision=state.get("revision", ""), key_configured=state.get("configured") is True)
        changed = (
            action != "keep"
            or any(previous.get(k) != config.get(k) for k in API_FIELDS)
            or str(previous["revision"]).startswith("recovery-")
        )
        if changed:
            config.update(
                revision=uuid.uuid4().hex, verified=False, verified_at="", resolved_model="", unverified_reason=""
            )
        config.pop("endpoint_id", None)
        _remember_profiles(config, previous)
        config["provider_profiles"].update(validated_profiles)
        with _settings_transaction(previous, [provider, *extra_keys], include_features=bool(changes)):
            if changed:
                _write_settings(config)
            if action == "replace":
                config["key_revision"] = uuid.uuid4().hex
                protected = credential_settings.store._transform(
                    json.dumps({"version": 1, "revision": config["key_revision"], "key": key}).encode(), protect=True
                )
                _write(key_path(provider), protected)
                config["key_configured"] = True
            elif action == "clear":
                key_path(provider).unlink(missing_ok=True)
                config.update(key_revision="", key_configured=False)
            prior_key_state = previous["key_states"].get(provider)
            config["key_states"][provider] = {
                "revision": config["key_revision"],
                "configured": config["key_configured"],
                **(
                    {"shared": "reading"}
                    if action == "keep"
                    and isinstance(prior_key_state, dict)
                    and prior_key_state.get("shared") == "reading"
                    else {}
                ),
            }
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
        # 在统一凭据模式下同步写入 credentials.dat 并刷新状态环境变量，保持两侧完全一致
        if not _is_isolated_legacy_key_store():
            unified_ops: dict[str, dict[str, object]] = {}
            if action == "replace":
                unified_ops[provider] = {"action": "replace", "accounts": [key]}
            elif action == "clear":
                unified_ops[provider] = {"action": "clear"}
            for name, (extra_action, extra_key) in extra_keys.items():
                if extra_action == "replace":
                    unified_ops[name] = {"action": "replace", "accounts": [extra_key]}
                elif extra_action == "clear":
                    unified_ops[name] = {"action": "clear"}
            if unified_ops:
                status_after = credential_settings.save_all_actions(unified_ops)
                credential_settings.apply_public_environment(status_after)
    return public_status()


def _sync_key_states_from_status(config: dict, key_status: dict[str, dict[str, object]]) -> None:
    states = dict(config.get("key_states") or {})
    for name in KEY_PROVIDER_ORDER:
        info = key_status.get(name, {})
        configured = bool(info.get("configured")) if isinstance(info, dict) else False
        old = states.get(name) if isinstance(states.get(name), dict) else {}
        revision = str(old.get("revision") or "") or (uuid.uuid4().hex if configured else "")
        states[name] = {"revision": revision if configured else "", "configured": configured}
    config["key_states"] = states
    active = config.get("provider", "deepseek")
    active_state = states.get(active, {})
    config["key_configured"] = bool(active_state.get("configured"))
    config["key_revision"] = str(active_state.get("revision") or "")


def sync_credential_change(changed_services: set[str], key_status: dict[str, dict[str, object]]) -> None:
    """在统一凭据保存后同步解题配置里的密钥状态；仅当前启用服务变更时重置已测试标记。"""

    with _lock:
        current = _load()
        _sync_key_states_from_status(current, key_status)
        if current.get("provider") in changed_services:
            current.update(
                revision=uuid.uuid4().hex, verified=False, verified_at="", resolved_model="", unverified_reason=""
            )
        current["keys_migrated_to_unified"] = True
        _write_settings(current)


def _validate_unified_payload_shape(payload: dict) -> dict:
    """校验统一接口传入的解题配置结构，并返回清洗后的中间数据。"""

    allowed = {"mode", *API_FIELDS, "endpoint_id", "features", "on_intake", "provider_profiles"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise SettingsError("标签与参考答案设置格式不正确。")
    changes = payload.get("features", {})
    if (
        not isinstance(changes, dict)
        or set(changes) - FEATURE_KEYS
        or not all(type(v) is bool for v in changes.values())
    ):
        raise SettingsError("标签与参考答案开关只能分别设为 true 或 false。")
    timing = payload.get("on_intake", {})
    if (
        not isinstance(timing, dict)
        or set(timing) - {"tags", "answer"}
        or not all(type(v) is bool for v in timing.values())
    ):
        raise SettingsError("录入时生成的开关只能分别设为 true 或 false。")
    extra_profiles = payload.get("provider_profiles", {})
    profile_fields = set(API_FIELDS) - {"provider"}
    if (
        not isinstance(extra_profiles, dict)
        or set(extra_profiles) - set(DEFAULTS)
        or any(not isinstance(profile, dict) or set(profile) - profile_fields for profile in extra_profiles.values())
    ):
        raise SettingsError("备用服务模型配置格式不正确。")
    return {"changes": changes, "timing": timing, "extra_profiles": extra_profiles}


def validate_unified_payload(payload: dict) -> None:
    """在写入任何凭据或配置文件前，预先校验解题配置参数是否合法。"""

    parsed = _validate_unified_payload_shape(payload)
    previous = _load()
    mode = payload.get("mode", previous["mode"])
    provider = payload.get("provider", "doubao" if "endpoint_id" in payload else previous["provider"])
    if mode not in ("assistant", "api") or not isinstance(provider, str) or provider not in DEFAULTS:
        raise SettingsError("请选择当前 AI 助手或独立 API，以及有效的服务商。")
    config = {**_select_provider(previous, provider), "mode": mode}
    for field in API_FIELDS:
        if field in payload:
            config[field] = payload[field]
    if "endpoint_id" in payload:
        if "model" in payload and payload["model"] != payload["endpoint_id"]:
            raise SettingsError("模型 ID 不一致。")
        config["model"] = payload["endpoint_id"]
    _validate_profile(config)
    for name, profile in parsed["extra_profiles"].items():
        if name == provider:
            continue
        _validate_profile({**_select_provider(previous, name), **profile})


def save_from_unified(
    payload: dict, *, changed_services: set[str] | None = None, key_status: dict[str, dict[str, object]] | None = None
) -> dict:
    """供统一 API 配置接口在保存凭据后原子更新解题模式、模型参数与功能开关。"""

    changed_services = set(changed_services or ())
    if key_status is None:
        key_status = credential_settings.public_all_environment_status()
    parsed = _validate_unified_payload_shape(payload)
    changes = parsed["changes"]
    timing = parsed["timing"]
    extra_profiles = parsed["extra_profiles"]

    with _lock:
        previous = _load()
        mode = payload.get("mode", previous["mode"])
        provider = payload.get("provider", "doubao" if "endpoint_id" in payload else previous["provider"])
        if mode not in ("assistant", "api") or not isinstance(provider, str) or provider not in DEFAULTS:
            raise SettingsError("请选择当前 AI 助手或独立 API，以及有效的服务商。")
        config = {**_select_provider(previous, provider), "mode": mode}
        config["on_intake"] = {**previous["on_intake"], **timing, "tags": False}
        for field in API_FIELDS:
            if field in payload:
                config[field] = payload[field]
        if "endpoint_id" in payload:
            if "model" in payload and payload["model"] != payload["endpoint_id"]:
                raise SettingsError("模型 ID 不一致。")
            config["model"] = payload["endpoint_id"]
        _validate_profile(config)
        validated_profiles = {}
        for name, profile in extra_profiles.items():
            if name == provider:
                continue
            selected = {**_select_provider(previous, name), **profile}
            _validate_profile(selected)
            validated_profiles[name] = _profile(selected)
        _sync_key_states_from_status(config, key_status)
        api_changed = (
            provider in changed_services
            or any(previous.get(k) != config.get(k) for k in API_FIELDS)
            or str(previous["revision"]).startswith("recovery-")
        )
        if api_changed:
            config.update(
                revision=uuid.uuid4().hex, verified=False, verified_at="", resolved_model="", unverified_reason=""
            )
        config.pop("endpoint_id", None)
        _remember_profiles(config, previous)
        config["provider_profiles"].update(validated_profiles)
        config["keys_migrated_to_unified"] = True
        with _settings_transaction(previous, [provider], include_features=bool(changes)):
            _write_settings(config)
            if changes:
                features.save(changes)
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
    provider = config["provider"]
    if not _is_isolated_legacy_key_store():
        try:
            unified_key = credential_settings.reveal_saved_key(provider, 0)
            if isinstance(unified_key, str) and unified_key and not any(c.isspace() for c in unified_key):
                return unified_key
        except (credential_settings.CredentialStoreError, credential_settings.CredentialValidationError):
            pass
    try:
        value = json.loads(
            credential_settings.store._transform(key_path(provider).read_bytes(), protect=False).decode("utf-8")
        )
        key = value.get("key", "") if isinstance(value, dict) and value.get("version") == 1 else ""
        if (
            not isinstance(key, str)
            or not key
            or any(c.isspace() for c in key)
            or (_is_isolated_legacy_key_store() and value.get("revision") != config.get("key_revision"))
        ):
            raise ValueError
        return key
    except (OSError, ValueError, credential_settings.CredentialStoreError):
        raise ConnectionError("API Key 无法读取，已暂停 API 生成；请在设置中重新保存。") from None


def _same_configuration(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in (*API_FIELDS, "revision", "key_revision"))


def _mark_unverified(config: dict, *, reason: str = "") -> None:
    cleaned_reason = _sanitize_error_text(reason, limit=160)
    with _lock:
        current = _load()
        # 仅当配置参数与最近一次测通时间戳都一致时才允许置回未验证，
        # 防止用户重新测通前发起的旧后台任务稍后失败把新的已测通状态覆盖掉。
        if _same_configuration(config, current) and str(config.get("verified_at") or "") == str(
            current.get("verified_at") or ""
        ):
            logger.warning(
                "library_ai marked unverified (provider=%s, reason=%s)",
                config.get("provider"),
                cleaned_reason or "unknown",
            )
            _write_settings(
                {
                    **current,
                    "verified": False,
                    "verified_at": "",
                    "resolved_model": "",
                    "unverified_reason": cleaned_reason,
                }
            )


def _response_content(message: dict) -> tuple[str, bool]:
    """Compatible reasoning may be separate or wrapped; only return the answer."""
    if not isinstance(message, dict):
        raise ValueError
    text = message.get("content")
    if not isinstance(text, str):
        raise ValueError
    reasoning = message.get("reasoning_content")
    if not (isinstance(reasoning, str) and reasoning.strip()):
        reasoning = message.get("reasoning")
    details = message.get("reasoning_details")
    thinking = isinstance(reasoning, str) and bool(reasoning.strip())
    if isinstance(details, list):
        thinking = thinking or any(
            isinstance(item, dict) and isinstance(item.get("text"), str) and bool(item["text"].strip())
            for item in details
        )
    blocks = re.findall(r"<(?:think|thought)>(.*?)</(?:think|thought)>", text, flags=re.S)
    thinking = thinking or any(part.strip() for part in blocks)
    text = re.sub(r"<(?:think|thought)>.*?</(?:think|thought)>", "", text, flags=re.S).strip()
    # 部分推理模型因服务端模板预置起始 <think>，响应正文仅出现闭合 </think> 而省略开头标签。
    if "<think>" not in text and "<thought>" not in text:
        orphan_close = re.match(r"^(.*?)</(?:think|thought)>\s*(.+)$", text, flags=re.S)
        if orphan_close:
            if orphan_close.group(1).strip():
                thinking = True
            text = orphan_close.group(2).strip()
    if not text or any(tag in text for tag in ("<think>", "</think>", "<thought>", "</thought>")):
        raise ValueError
    return text, thinking


def _needs_reasoning_evidence(config: dict) -> bool:
    # MiniMax、Google Gemini、英伟达 NIM、智谱与 OpenRouter 在简单或 JSON 提示下
    # 常省略显式 reasoning_content；连接探测仍严格核对数学与看图输出本身。
    return config["thinking"] and config["provider"] not in {"minimax", "gemini", "nvidia", "zhipu", "openrouter"}


def _sanitize_error_text(raw: str, *, limit: int = 160) -> str:
    """脱敏并裁剪错误摘要，绝不泄露 URL、Bearer Token 或任何密钥片段。"""
    cleaned = _SECRET_REDACT.sub("[已脱敏]", str(raw or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit]


def _extract_error_message(payload: object) -> str:
    """从上游 JSON 响应体中安全提取简短可读错误信息（不含密钥或网址）。"""
    if not isinstance(payload, dict):
        return ""
    raw = ""
    err = payload.get("error")
    if isinstance(err, dict):
        raw = str(err.get("message") or err.get("code") or "")
    elif isinstance(err, str):
        raw = err
    if not raw:
        raw = str(payload.get("message") or payload.get("msg") or "")
    return _sanitize_error_text(raw, limit=90)


def _looks_like_auth_error(detail: str) -> bool:
    return bool(detail and _AUTH_ERROR_PATTERN.search(detail))


def _upstream_error_detail(response) -> str:
    """安全提取上游返回的简短错误原因（绝不包含密钥或网址）。"""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return ""
    return _extract_error_message(payload)


def _parse_retry_after(response) -> float | None:
    headers = getattr(response, "headers", None)
    raw = headers.get("Retry-After") if hasattr(headers, "get") else None
    text = raw.strip() if isinstance(raw, str) else ""
    if not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,2})?", text):
        return None
    return min(15.0, max(0.0, float(text)))


def _key_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def _key_pool(config: dict) -> list[str]:
    provider = config["provider"]
    if not _is_isolated_legacy_key_store():
        try:
            values = credential_settings.store.load_credentials()
            accounts = credential_settings.store.credential_pool(values, provider)
            valid = [k for k in accounts if isinstance(k, str) and k and not any(c.isspace() for c in k)]
            if valid:
                return valid
        except (credential_settings.CredentialStoreError, credential_settings.CredentialValidationError):
            pass
    return [_key(config)]


def _acquire_pool_key(config: dict) -> str:
    keys = _key_pool(config)
    if len(keys) == 1:
        return keys[0]
    provider = config["provider"]
    now = time.monotonic()
    with _lock:
        start = _KEY_POOL_CURSOR.get(provider, 0) % len(keys)
        for offset in range(len(keys)):
            idx = (start + offset) % len(keys)
            candidate = keys[idx]
            ready_at = _KEY_COOLDOWN_UNTIL.get((provider, _key_digest(candidate)), 0.0)
            if now >= ready_at:
                _KEY_POOL_CURSOR[provider] = (idx + 1) % len(keys)
                return candidate
        # 若所有密钥均处于短暂冷却，选最早解锁的那个并推进游标。
        best_idx = min(range(len(keys)), key=lambda i: _KEY_COOLDOWN_UNTIL.get((provider, _key_digest(keys[i])), 0.0))
        _KEY_POOL_CURSOR[provider] = (best_idx + 1) % len(keys)
        return keys[best_idx]


def _mark_key_cooldown(provider: str, key: str, seconds: float) -> None:
    until = time.monotonic() + max(0.5, min(60.0, float(seconds)))
    with _lock:
        _KEY_COOLDOWN_UNTIL[(provider, _key_digest(key))] = until


def _has_ready_alternate_key(config: dict) -> bool:
    keys = _key_pool(config)
    if len(keys) <= 1:
        return False
    provider = config["provider"]
    now = time.monotonic()
    with _lock:
        return any(now >= _KEY_COOLDOWN_UNTIL.get((provider, _key_digest(k)), 0.0) for k in keys)


def _is_rate_limited_free_provider(provider: str) -> bool:
    if provider in _RATE_LIMITED_FREE_PROVIDERS:
        return True
    spec = provider_catalog.VISION.get(provider)
    return bool(spec is not None and spec.get("free_models"))


@contextmanager
def _provider_gate(config: dict):
    provider = config.get("provider", "")
    if not _is_rate_limited_free_provider(provider):
        yield
        return
    # 免费/限流服务按密钥池数量收敛在途并发（单账号严格串行，多账号最多 4 并发）。
    count = int(saved_key_status(config).get(provider, {}).get("count") or 1)
    slots = max(1, min(4, count))
    with _lock:
        gate = _PROVIDER_GATES.get((provider, slots))
        if gate is None:
            gate = threading.BoundedSemaphore(slots)
            _PROVIDER_GATES[(provider, slots)] = gate
    with gate:
        yield


def _request_once_with_rate_limit(
    config: dict,
    prompt: str,
    image_urls: list[str],
    max_tokens: int,
    *,
    kind=None,
    explicit_api=False,
    live_snapshot: dict | None = None,
) -> tuple[str, dict]:
    total_attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(total_attempts):
        try:
            return _request(
                config,
                prompt,
                image_urls,
                max_tokens,
                kind=kind,
                explicit_api=explicit_api,
                live_snapshot=live_snapshot,
            )
        except RateLimitError as error:
            if attempt >= total_attempts - 1:
                raise
            # 账号池里还有未冷却的备用 Key 时，无需等待直接切换下一把 Key。
            if _has_ready_alternate_key(config):
                continue
            delay = RATE_LIMIT_BACKOFF[attempt] if error.retry_after is None else min(15.0, error.retry_after)
            if delay > 0:
                time.sleep(delay)
    raise AssertionError("unreachable")


def _request(
    config: dict,
    prompt: str,
    image_urls: list[str],
    max_tokens: int,
    *,
    kind=None,
    explicit_api=False,
    live_snapshot: dict | None = None,
) -> tuple[str, dict]:
    if image_urls and not config["supports_images"]:
        raise ServiceError("这道题有配图，所选 API 未声明支持图片；已暂停，改用当前助手或明确支持图文的模型。")
    expected = live_snapshot if live_snapshot is not None else config
    if not _same_configuration(expected, _load()) or (
        not explicit_api and (expected["mode"] != "api" or _load()["mode"] != "api")
    ):
        raise ServiceError("请求前执行方式或 API 设置已变化，旧任务未发起。")
    if kind:
        ensure_ready(kind)
    key = _acquire_pool_key(config)
    content = [{"type": "text", "text": prompt}]
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in image_urls)
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "stream": False,
    }
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
    elif config["provider"] == "nvidia":
        # 英伟达 NIM 的 Nemotron 系列通过 chat_template_kwargs 控制思考过程开关。
        payload["chat_template_kwargs"] = {"enable_thinking": bool(config["thinking"])}
    elif config["provider"] == "gemini":
        payload["reasoning_effort"] = "high" if config["thinking"] else "none"
    elif config["provider"] == "openrouter":
        if config["thinking"]:
            payload["reasoning_effort"] = "high"
    elif config["thinking"]:
        payload["thinking"] = {"type": "enabled"}
        if config["provider"] not in {"doubao", "zhipu"}:
            payload["reasoning_effort"] = "high"
    elif config["provider"] in ("doubao", "deepseek", "zhipu"):
        payload["thinking"] = {"type": "disabled"}
    if kind:
        ensure_ready(kind)
    try:
        response = requests.post(
            config["base_url"] + "/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {key}"},
            timeout=(15, 180),
            allow_redirects=False,
        )
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            _mark_key_cooldown(config["provider"], key, retry_after if retry_after is not None else 15.0)
            detail = _upstream_error_detail(response)
            reason = f"：{detail}" if detail else ""
            raise RateLimitError(
                f"独立 API 当前触发并发或速率限制（HTTP 429{reason}），已保留已测通状态，请稍后重试或降低并发。",
                retry_after=retry_after,
            )
        if response.status_code != 200:
            code = int(response.status_code)
            detail = _upstream_error_detail(response)
            reason = f"：{detail}" if detail else ""
            if code in _TRANSIENT_HTTP_STATUS:
                raise TransientServiceError(
                    f"独立 API 上游暂时繁忙或超时（HTTP {code}{reason}），已保留已测通状态；该题未保存，请稍后重试。"
                )
            if code in {400, 413, 422} and not _looks_like_auth_error(detail):
                raise ResponseFormatError(
                    f"独立 API 未接受本题请求（HTTP {code}{reason}），已保留已测通状态；该题未保存，请核对题面或配图。"
                )
            raise ConnectionError(f"独立 API 请求未完成（HTTP {code}{reason}），已暂停，请核对服务配置后显式重试。")
        body = response.json()
        if isinstance(body, dict) and body.get("error") and not body.get("choices"):
            detail = _extract_error_message(body)
            reason = f"：{detail}" if detail else ""
            if _looks_like_auth_error(detail):
                raise ConnectionError(
                    f"独立 API 鉴权或账户状态异常（{detail or '未知错误'}），已暂停，请核对密钥后重新测试连接。"
                )
            raise TransientServiceError(
                f"独立 API 上游暂时返回异常{reason}，已保留已测通状态；该题未保存，请稍后重试。"
            )
        choice = body["choices"][0]
        if choice.get("finish_reason") == "length":
            raise TruncationError("答案超过本次长度限制，未保存不完整结果。")
        message = choice["message"]
        text, thinking = _response_content(message)
        return text, {"model": str(body.get("model") or config["model"]), "thinking": thinking}
    except ServiceError:
        raise
    except requests.Timeout:
        raise TransientServiceError("独立 API 本次响应超时，已保留已测通状态；该题未保存，请稍后重试。") from None
    except requests.RequestException:
        raise ConnectionError("独立 API 网络请求未完成，已暂停，请稍后显式重试。") from None
    except (ValueError, TypeError, KeyError, IndexError):
        raise ResponseFormatError(
            "独立 API 本次没有返回完整可用的答案，已保留已测通状态；该题未保存，请重试。"
        ) from None


def _rest_provider(provider: str, seconds: float = 90.0) -> None:
    until = time.monotonic() + max(5.0, min(600.0, float(seconds)))
    with _lock:
        _PROVIDER_REST_UNTIL[provider] = until


def _clear_provider_rest(provider: str) -> None:
    with _lock:
        _PROVIDER_REST_UNTIL.pop(provider, None)


def _auto_route_candidates(config: dict, *, needs_images: bool) -> list[dict]:
    """开启跨服务自动路由时，将所有已配置密钥且能力匹配的商组成统一路由池。"""
    keys = saved_key_status(config)
    order = AUTO_ROUTE_VISION_ORDER if needs_images else AUTO_ROUTE_TEXT_ORDER
    ordered_names = [config["provider"], *[name for name in order if name != config["provider"]]]
    now = time.monotonic()
    fresh: list[dict] = []
    resting: list[dict] = []
    for name in ordered_names:
        if not keys.get(name, {}).get("configured"):
            continue
        candidate = _select_provider(config, name)
        if not candidate.get("model") or not candidate.get("base_url"):
            continue
        if needs_images and not candidate.get("supports_images"):
            continue
        with _lock:
            rest_until = _PROVIDER_REST_UNTIL.get(name, 0.0)
        if now >= rest_until:
            fresh.append(candidate)
        else:
            resting.append(candidate)
    return fresh + resting


def _execute_candidate(
    candidate: dict,
    live_snapshot: dict,
    prompt: str,
    image_urls: list[str],
    max_tokens: int,
    *,
    call_kind: str | None,
    call_explicit: bool,
) -> tuple[str, dict]:
    with _provider_gate(candidate):
        try:
            text, evidence = _request_once_with_rate_limit(
                candidate,
                prompt,
                image_urls,
                max_tokens,
                kind=call_kind,
                explicit_api=call_explicit,
                live_snapshot=live_snapshot,
            )
        except TruncationError:
            # 内容太长不是服务坏了：同一个服务、同一把 key，把额度加大再试一次。
            # 不换模型、不换端点、不动「已验证」状态。
            budget = min(max_tokens * 2, TRUNCATION_RETRY_TOKEN_CAP)
            if budget <= max_tokens:
                raise
            try:
                text, evidence = _request_once_with_rate_limit(
                    candidate,
                    prompt,
                    image_urls,
                    budget,
                    kind=call_kind,
                    explicit_api=call_explicit,
                    live_snapshot=live_snapshot,
                )
            except TruncationError:
                raise TruncationError(
                    "答案过长，两次生成都被长度上限截断，结果未保存；请缩短题干或减少配图后重试。"
                ) from None
    if _needs_reasoning_evidence(candidate) and not evidence["thinking"]:
        raise ResponseFormatError(
            "本次响应未返回所选思考内容，已保留已测通状态；该题未保存，请重试或核对模型思考配置。"
        )
    return text, evidence


def chat(
    prompt: str,
    image_urls: list[str],
    *,
    kind: str | None,
    max_tokens: int = 12000,
    explicit_api=False,
    manual_tag=False,
) -> tuple[str, str]:
    if explicit_api and kind is not None and not manual_tag:
        raise ServiceError("本次 API 执行仅适用于明确选择的答案解析任务。")
    ensure_api_ready() if explicit_api or manual_tag else ensure_ready(kind)
    config = _load()
    if not explicit_api and not manual_tag and config["mode"] != "api":
        raise ServiceError("当前是助手模式：请由当前 AI 助手通过本地工具领取并提交任务，不调用云 API。")
    if explicit_api or manual_tag:
        call_kind = None if manual_tag else kind
        call_explicit = True
    else:
        call_kind = kind
        call_explicit = False

    if not features.enabled("ai_auto_route"):
        try:
            text, evidence = _execute_candidate(
                config, config, prompt, image_urls, max_tokens, call_kind=call_kind, call_explicit=call_explicit
            )
        except ConnectionError as error:
            _mark_unverified(config, reason=str(error))
            raise
        if not _same_configuration(config, _load()) or (
            not explicit_api and not manual_tag and _load()["mode"] != "api"
        ):
            raise ServiceError("生成期间执行方式或 API 设置已变化，旧结果未保存。")
        ensure_api_ready() if explicit_api or manual_tag else ensure_ready(kind)
        return text, f"{config['provider']} API · {evidence['model']}"

    candidates = _auto_route_candidates(config, needs_images=bool(image_urls)) or [config]
    errors: list[tuple[str, Exception]] = []
    chosen_provider = config["provider"]
    text = ""
    evidence: dict = {}
    for candidate in candidates:
        provider_name = candidate["provider"]
        try:
            text, evidence = _execute_candidate(
                candidate, config, prompt, image_urls, max_tokens, call_kind=call_kind, call_explicit=call_explicit
            )
            _clear_provider_rest(provider_name)
            chosen_provider = provider_name
            break
        except RateLimitError as error:
            _rest_provider(provider_name, max(30.0, float(error.retry_after or 90.0)))
            errors.append((provider_name, error))
        except TransientServiceError as error:
            _rest_provider(provider_name, 60.0)
            errors.append((provider_name, error))
        except ResponseFormatError as error:
            _rest_provider(provider_name, 15.0)
            errors.append((provider_name, error))
        except ConnectionError as error:
            _rest_provider(provider_name, 120.0)
            errors.append((provider_name, error))
    else:
        # 池内全部服务商均失败：仅在当前主服务商触发真实连接/鉴权故障时重置验证状态；
        # 限流、5xx 波动、单题超时与残缺响应全部保留已测通状态。
        first_err = errors[0][1]
        if all(isinstance(err, RateLimitError) for _, err in errors):
            raise first_err
        primary_conn_err = next(
            (err for name, err in errors if name == config["provider"] and isinstance(err, ConnectionError)), None
        )
        if primary_conn_err is not None:
            _mark_unverified(config, reason=str(primary_conn_err))
        raise first_err

    if not _same_configuration(config, _load()) or (not explicit_api and not manual_tag and _load()["mode"] != "api"):
        raise ServiceError("生成期间执行方式或 API 设置已变化，旧结果未保存。")
    ensure_api_ready() if explicit_api or manual_tag else ensure_ready(kind)
    return text, f"{chosen_provider} API · {evidence['model']}"


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
    except ServiceError as error:
        probe_reason = str(error).replace("，已保留已测通状态；该题未保存", "，连接测试未通过")
        _mark_unverified(config, reason=probe_reason)
        if probe_reason != str(error):
            raise type(error)(probe_reason) from None
        raise
    with _lock:
        current = _load()
        if not _same_configuration(config, current) or current["mode"] != "api":
            raise ServiceError("测试期间设置已变化，测试结果未沿用，请重新测试。")
        _write_settings(
            {
                **current,
                "verified": True,
                "verified_at": timezone.now().isoformat(),
                "resolved_model": evidence["model"],
                "unverified_reason": "",
            }
        )
    return public_status()
