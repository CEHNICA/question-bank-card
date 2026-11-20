"""统一管理桌面端所有 API 凭据、读题模型偏好与解题/标签 AI 配置的协调服务层。

设计目的：
1. 前端只需与单个接口交互（GET/POST /api/settings/api-config），不再拆成三套独立保存；
2. 全部服务元数据均来自 provider_catalog，凭据全部落入同一个 DPAPI 加密池；
3. 读取接口（GET）绝不触发 DPAPI 解密，仅基于非涉密状态快照组装视图状态；
4. 写操作先校验全部三段参数，再依次落盘，确保异常输入不会导致部分配置撕裂。
"""

from __future__ import annotations

import logging
from typing import Any

from . import (
    credential_settings,
    features,
    library,
    library_ai_settings,
    preferences,
    provider_catalog,
    reader_test,
    readers,
)

logger = logging.getLogger(__name__)


class ApiConfigError(ValueError):
    """统一 API 配置校验或业务执行失败异常。"""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _safe_saved_preferences() -> dict[str, Any]:
    try:
        return preferences.load_configuration()
    except preferences.PreferenceError:
        return {
            "roles": dict(preferences.DEFAULTS),
            "models": dict(preferences.DEFAULT_MODELS),
            "plans": dict(preferences.DEFAULT_PLANS),
        }


def _reading_snapshot() -> dict[str, Any]:
    try:
        applied_preferences = preferences.load_applied_configuration()
    except preferences.PreferenceError:
        applied_preferences = None
    saved = _safe_saved_preferences()
    saved_roles = saved["roles"]
    engines = readers.engine_settings(applied_preferences)
    readiness = readers.primary_readiness(saved)
    saved_view = {
        "primary": saved_roles["primary_engine"],
        "checker": saved_roles["checker_engine"],
        "arbiter": saved_roles["arbiter_engine"],
        "models": dict(saved["models"]),
        "plans": dict(saved["plans"]),
    }
    current_applied = {**engines["selected"], "models": engines["models"], "plans": engines["plans"]}
    return {
        **saved_view,
        "applied": current_applied,
        "pending_change": saved_view != current_applied,
        "readiness": {
            key: readiness[key] for key in ("ready", "reason", "selected", "label", "used", "fallback_available")
        },
        "assistant_mode": readers.assistant_mode(saved),
    }


def public_payload(*, include_backlog: bool = True) -> dict[str, Any]:
    """返回统一 API 配置页面所需的非涉密完整状态视图（不触发凭据解密）。"""

    library_ai_settings.migrate_legacy_keys_to_unified()
    credentials = credential_settings.public_all_environment_status()
    answering = dict(library_ai_settings.public_status())
    if include_backlog:
        answering["backlog"] = library.generation_backlog()
    return {
        "catalog": provider_catalog.catalog_payload(),
        "max_accounts": credential_settings.MAX_ACCOUNT_POOL_SIZE,
        "credentials": credentials,
        "reading": _reading_snapshot(),
        "answering": answering,
        "features": features.describe(),
    }


def _validate_reading_section(raw: object) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    if not isinstance(raw, dict):
        raise ApiConfigError("读题模型配置格式不正确")
    allowed = {"primary", "checker", "arbiter", "models", "plans"}
    if set(raw) - allowed:
        raise ApiConfigError("读题模型配置包含不支持的字段")
    current = _safe_saved_preferences()
    roles = {
        "primary_engine": raw.get("primary", current["roles"]["primary_engine"]),
        "checker_engine": raw.get("checker", preferences.DEFAULTS["checker_engine"]),
        "arbiter_engine": raw.get("arbiter", preferences.DEFAULTS["arbiter_engine"]),
    }
    normalized_roles = preferences.normalize(roles)
    if normalized_roles is None:
        raise ApiConfigError("模型选择不受支持")
    raw_models = raw.get("models", current["models"])
    normalized_models = preferences.normalize_models(raw_models, defaults=current["models"])
    if normalized_models is None:
        raise ApiConfigError("模型 ID 格式不正确：只能使用 1–160 位字母、数字及 . _ : / + -，且不能填写网址")
    normalized_plans = preferences.normalize_plans(raw.get("plans"), defaults=current["plans"])
    if normalized_plans is None:
        raise ApiConfigError("MiniMax 会员档位不受支持")
    return normalized_roles, normalized_models, normalized_plans


def save_payload(payload: object) -> dict[str, Any]:
    """在单次提交中原子校验并保存凭据池、读题偏好与解题 AI 设置。"""

    if not isinstance(payload, dict) or set(payload) - {"credentials", "reading", "answering"}:
        raise ApiConfigError("API 配置提交格式不正确")

    has_credentials = "credentials" in payload
    has_reading = "reading" in payload
    has_answering = "answering" in payload
    if not (has_credentials or has_reading or has_answering):
        raise ApiConfigError("请至少包含一项要保存的 API 配置")

    cred_ops = payload.get("credentials") if has_credentials else None
    if has_credentials and not isinstance(cred_ops, dict):
        raise ApiConfigError("凭据配置格式不正确")

    reading_tuple = _validate_reading_section(payload["reading"]) if has_reading else None
    if has_answering:
        answering_raw = payload["answering"]
        if not isinstance(answering_raw, dict):
            raise ApiConfigError("解题与标签配置格式不正确")
        try:
            library_ai_settings.validate_unified_payload(answering_raw)
        except library_ai_settings.SettingsError as exc:
            raise ApiConfigError(str(exc), status_code=400) from None

    mineru_verification = "not_requested"
    changed_services: set[str] = set()
    cred_status: dict[str, dict[str, object]]
    if cred_ops is not None:
        # 仅保留显式提交的服务操作；空字典表示全部保持不变
        unknown_services = set(cred_ops) - set(credential_settings.ALL_SERVICES)
        if unknown_services:
            raise ApiConfigError("凭据设置中包含不支持的服务。")
        try:
            mineru_verification = credential_settings.verify_mineru_replacement(cred_ops)
            for svc, op in cred_ops.items():
                if isinstance(op, dict) and op.get("action") in {"replace", "clear"}:
                    changed_services.add(svc)
            cred_status = credential_settings.save_all_actions(cred_ops)
            credential_settings.apply_public_environment(cred_status)
        except credential_settings.CredentialValidationError as exc:
            raise ApiConfigError(str(exc), status_code=400) from None
        except credential_settings.CredentialStoreError as exc:
            raise ApiConfigError(str(exc), status_code=500) from None
    else:
        cred_status = credential_settings.public_all_environment_status()

    if reading_tuple is not None:
        normalized_roles, normalized_models, normalized_plans = reading_tuple
        try:
            preferences.save_configuration(normalized_roles, normalized_models, normalized_plans)
        except preferences.PreferenceError as exc:
            raise ApiConfigError(str(exc), status_code=500) from None

    if has_answering:
        try:
            library_ai_settings.save_from_unified(
                payload["answering"], changed_services=changed_services, key_status=cred_status
            )
        except library_ai_settings.ServiceError as exc:
            raise ApiConfigError(str(exc), status_code=409) from None
        except library_ai_settings.SettingsError as exc:
            raise ApiConfigError(str(exc), status_code=400) from None
    elif changed_services:
        library_ai_settings.sync_credential_change(changed_services, cred_status)

    if mineru_verification == "verified":
        message = "API 配置已加密保存，MinerU Token 已通过官网验证；新任务立即生效。"
    elif mineru_verification in {"unverified", "unavailable"}:
        message = "API 配置已加密保存；本次 MinerU Token 尚未核验，实际可用性以下一次解析返回为准。"
    else:
        message = "API 配置已保存；新任务与后续生成按新配置执行，运行中任务不会中途切换。"

    return {
        **public_payload(),
        "restart_required": False,
        "mineru_verification": mineru_verification,
        "message": message,
    }


def test_connection(payload: object) -> dict[str, Any]:
    """根据明确用户确认运行一次合成连接探测（读题视觉服务或解题独立 API）。"""

    if not isinstance(payload, dict) or payload.get("confirm") is not True:
        raise ApiConfigError("请明确确认运行一次连接测试；这可能产生少量 API 额度消耗。", status_code=400)
    target = payload.get("target")
    if target == "reading":
        service = payload.get("service")
        if not isinstance(service, str) or service not in reader_test.SERVICE_URLS:
            raise ApiConfigError("请先选择一家看图读题服务再测试。", status_code=400)
        try:
            result = reader_test.test_reading_connection(service)
        except reader_test.ReadingTestError as exc:
            raise ApiConfigError(str(exc), status_code=400) from None
        return {"target": "reading", **result, "state": public_payload()}
    if target == "answering":
        try:
            answering_status = library_ai_settings.test_connection({"confirm": True})
        except library_ai_settings.ServiceError as exc:
            raise ApiConfigError(str(exc), status_code=409) from None
        except library_ai_settings.SettingsError as exc:
            raise ApiConfigError(str(exc), status_code=400) from None
        return {
            "target": "answering",
            "ok": bool(answering_status.get("verified")),
            "message": answering_status.get("message", "独立 API 已通过合成题连接测试。"),
            "state": public_payload(),
        }
    raise ApiConfigError("请选择要测试的模块：reading 或 answering。", status_code=400)
