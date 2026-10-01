"""Non-secret model routing and provider model preferences.

Version 1 files contained only ``roles``.  Version 2 adds a concrete model ID
for each provider while keeping the legacy engine keys as provider slots, and
optionally ``plans``: the user's MiniMax membership, which sets how many
requests one key may carry at once.  Older readers ignore ``plans``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from django.conf import settings

from . import provider_catalog
from .account_pool import DEFAULT_MINIMAX_PLAN, MINIMAX_PLANS


DEFAULTS = {
    "primary_engine": "minimax_m3",
    "checker_engine": "auto",
    "arbiter_engine": "primary",
}
DEFAULT_MODELS = {key: spec["default_model"] for key, spec in provider_catalog.VISION.items()}
SUGGESTED_MODELS = {key: list(spec["suggested"]) for key, spec in provider_catalog.VISION.items()}
FREE_MODELS = {key: list(spec["free_models"]) for key, spec in provider_catalog.VISION.items()}
_ENGINES = set(provider_catalog.ENGINES)
CHOICES = {
    # "assistant": no vision model; MinerU's text is the draft and an AI assistant checks it.
    "primary_engine": _ENGINES | {provider_catalog.ASSISTANT_ENGINE},
    "checker_engine": {"auto", *_ENGINES},
    "arbiter_engine": {"primary", "checker", *_ENGINES},
}
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+\-]{0,159}")
DEFAULT_PLANS = {"minimax": DEFAULT_MINIMAX_PLAN}


class PreferenceError(RuntimeError):
    pass


def preference_path() -> Path:
    explicit = os.environ.get("QB_MODEL_PREFERENCES_FILE", "").strip()
    if explicit:
        return Path(explicit).resolve()
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return (Path(local) / "QuestionBankM2" / "model-preferences.json").resolve()
    # Development/Linux fallback. It contains no API keys.
    return (settings.DATA_ROOT / "model-preferences.json").resolve()


def applied_path() -> Path:
    """A non-secret record of the last configuration a worker actually used."""
    path = preference_path()
    suffix = path.suffix or ".json"
    return path.with_name(f"{path.stem}.applied{suffix}")


def normalize(values) -> dict[str, str] | None:
    if not isinstance(values, dict):
        return None
    result = {}
    for key, default in DEFAULTS.items():
        value = values.get(key, default)
        if value not in CHOICES[key]:
            return None
        result[key] = value
    return result


def normalize_models(values, *, defaults: dict[str, str] | None = None) -> dict[str, str] | None:
    """Validate provider model IDs without freezing a fast-changing catalogue.

    IDs are deliberately data, not URLs: whitespace, query strings and shell
    metacharacters are rejected.  A provider may
    publish a new model without requiring an application release.
    """
    # Files saved before a service existed lack its model: it gets the default.
    fallback = {**DEFAULT_MODELS, **(defaults or {})}
    if values is None:
        return {provider: fallback[provider] for provider in DEFAULT_MODELS}
    if not isinstance(values, dict):
        return None
    result: dict[str, str] = {}
    for provider in DEFAULT_MODELS:
        value = values.get(provider, fallback[provider])
        if (not isinstance(value, str) or MODEL_ID.fullmatch(value) is None
                or "://" in value):
            return None
        result[provider] = value
    return result


def normalize_plans(values, *, defaults: dict[str, str] | None = None) -> dict[str, str] | None:
    """Validate the membership choices; ``None`` keeps ``defaults``."""
    fallback = defaults or DEFAULT_PLANS
    if values is None:
        return dict(fallback)
    if not isinstance(values, dict):
        return None
    value = values.get("minimax", fallback["minimax"])
    if value not in MINIMAX_PLANS:
        return None
    return {"minimax": value}


def _stored_plans(values) -> dict[str, str]:
    """Plans read from a file: a choice this version does not know falls back."""
    return normalize_plans(values) or dict(DEFAULT_PLANS)


def normalize_configuration(roles, models=None, plans=None) -> dict[str, dict[str, str]] | None:
    normalized_roles = normalize(roles)
    normalized_models = normalize_models(models)
    normalized_plans = normalize_plans(plans)
    if normalized_roles is None or normalized_models is None or normalized_plans is None:
        return None
    return {"roles": normalized_roles, "models": normalized_models, "plans": normalized_plans}


def load_configuration() -> dict[str, dict[str, str]]:
    path = preference_path()
    if not path.is_file():
        return {"roles": dict(DEFAULTS), "models": dict(DEFAULT_MODELS), "plans": dict(DEFAULT_PLANS)}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PreferenceError("模型设置文件无法读取，请重新保存") from exc
    version = value.get("version") if isinstance(value, dict) else None
    roles = value.get("roles") if version in {1, 2} else None
    models = value.get("models") if version == 2 else None
    plans = _stored_plans(value.get("plans") if version == 2 else None)
    normalized = normalize_configuration(roles, models, plans)
    if normalized is None:
        raise PreferenceError("模型设置文件格式不正确，请重新保存")
    return normalized


def load_applied_configuration() -> dict[str, dict[str, str]] | None:
    """Return the last worker-applied snapshot, or ``None`` before first use."""
    path = applied_path()
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise PreferenceError("已应用的模型设置状态无法读取") from exc
    version = value.get("version") if isinstance(value, dict) else None
    normalized = normalize_configuration(
        value.get("roles") if version == 2 else None,
        value.get("models") if version == 2 else None,
        _stored_plans(value.get("plans") if version == 2 else None),
    )
    if normalized is None:
        raise PreferenceError("已应用的模型设置状态格式不正确")
    return normalized


def load() -> dict[str, str]:
    """Legacy role-only accessor retained for existing callers."""
    return load_configuration()["roles"]


def save_configuration(roles: dict[str, str], models=None, plans=None) -> dict[str, dict[str, str]]:
    if plans is None:
        # Saving models must not reset the membership chosen earlier.
        try:
            plans = load_configuration()["plans"]
        except PreferenceError:
            plans = None
    normalized = normalize_configuration(roles, models, plans)
    if normalized is None:
        raise PreferenceError("模型选择不受支持")
    path = preference_path()
    payload = json.dumps({"version": 2, **normalized}, ensure_ascii=False, indent=2) + "\n"
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix="model-preferences-",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    except OSError as exc:
        raise PreferenceError("模型设置暂时无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return normalized


def save_applied_configuration(configuration: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Atomically record the exact non-secret snapshot used by the worker."""
    normalized = normalize_configuration(
        configuration.get("roles"), configuration.get("models"), configuration.get("plans"),
    ) if isinstance(configuration, dict) else None
    if normalized is None:
        raise PreferenceError("已应用的模型设置格式不正确")
    path = applied_path()
    payload = json.dumps({"version": 2, **normalized}, ensure_ascii=False, indent=2) + "\n"
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", prefix="model-applied-",
            suffix=".tmp", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        temporary.replace(path)
    except OSError as exc:
        raise PreferenceError("模型生效状态暂时无法保存") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return normalized


def save(values: dict[str, str]) -> dict[str, str]:
    """Legacy role-only writer; preserve any already selected model IDs."""
    try:
        models = load_configuration()["models"]
    except PreferenceError:
        models = dict(DEFAULT_MODELS)
    return save_configuration(values, models)["roles"]


def apply_to_environment(configuration: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """Apply one immutable preference snapshot at a worker task boundary."""
    selected = configuration or load_configuration()
    roles = selected["roles"]
    # A snapshot written before a service existed lacks its model: use the default.
    models = normalize_models(selected.get("models")) or dict(DEFAULT_MODELS)
    plans = _stored_plans(selected.get("plans"))
    os.environ.update({
        "QB_PRIMARY_ENGINE": roles["primary_engine"],
        "QB_CHECKER_ENGINE": roles["checker_engine"],
        "QB_ARBITER_ENGINE": roles["arbiter_engine"],
        **{provider_catalog.model_environment(provider): models[provider] for provider in DEFAULT_MODELS},
        "QB_MINIMAX_PLAN": plans["minimax"],
    })
    return {"roles": dict(roles), "models": dict(models), "plans": dict(plans)}


def apply_and_record(configuration: dict[str, dict[str, str]] | None = None) -> dict[str, dict[str, str]]:
    """Apply a task snapshot and publish it only if both operations succeed."""
    keys = (
        "QB_PRIMARY_ENGINE", "QB_CHECKER_ENGINE", "QB_ARBITER_ENGINE", "QB_MINIMAX_PLAN",
        *(provider_catalog.model_environment(provider) for provider in DEFAULT_MODELS),
    )
    previous = {key: os.environ.get(key) for key in keys}
    try:
        previous_applied = load_applied_configuration()
    except PreferenceError:
        previous_applied = None
    selected = apply_to_environment(configuration)
    try:
        return save_applied_configuration(selected)
    except PreferenceError:
        # Do not let the worker use a configuration that /api/status cannot
        # report.  A valid applied snapshot is more authoritative than the
        # startup environment, which may already contain newly saved values.
        if previous_applied is not None:
            apply_to_environment(previous_applied)
        else:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        raise
