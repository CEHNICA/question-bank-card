"""Secure adapter between Django/worker code and the Windows credential store.

The desktop web process receives only the path of the DPAPI-protected file and
non-secret availability counters.  Plain credentials are loaded only while a
settings request is being saved or by the worker at a task boundary; they are
never returned by this module.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import Mapping

from .account_pool import SERVICE_ENVIRONMENT, reset_account_pools


def _load_store() -> ModuleType:
    try:
        import credential_store

        return credential_store
    except ModuleNotFoundError as exc:
        if exc.name != "credential_store":
            raise

    # ``manage.py`` puts backend/ rather than the project root on sys.path.
    # Loading this one audited sibling by absolute path keeps source mode and
    # the PyInstaller build on the same implementation without widening the
    # import path for unrelated modules.
    source = Path(__file__).resolve().parents[2] / "credential_store.py"
    spec = importlib.util.spec_from_file_location("_question_bank_credential_store", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法载入本机凭据存储。")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


store = _load_store()
CredentialStoreError = store.CredentialStoreError
CredentialValidationError = store.CredentialValidationError
MAX_ACCOUNT_POOL_SIZE = store.MAX_ACCOUNT_POOL_SIZE
SERVICES = tuple(store.ACCOUNT_POOL_FIELDS)


def public_status(values: Mapping[str, object] | None = None) -> dict[str, dict[str, object]]:
    """Read only configured/count fields, never a masked credential."""

    return store.credential_status(values)


def save_actions(changes: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Persist one validated keep/clear/replace transaction with DPAPI."""

    return store.update_credentials(changes)


def _mineru_token_validity(_token: str) -> bool | None:
    """No documented zero-upload authorization probe is currently available.

    The current API management documentation does not specify the previously
    used ``/api/v4/quota`` endpoint. Saving a Token must not create a parse task
    just to test authorization or invent service unavailability from a 404.
    Local format checks remain in the credential store; a real parsing task
    subsequently reports invalid/expired Tokens through the documented codes.
    """
    return None


def verify_mineru_replacement(changes: Mapping[str, object]) -> str:
    """Return verified/unverified/not_requested, rejecting known-bad tokens.

    Only a replacement pool is checked.  Keeping or clearing existing values
    does no network work. An unavailable documented probe must not block a
    user's new value, and the API response must say that it remains unverified.
    """

    if not isinstance(changes, Mapping):
        return "not_requested"
    operation = changes.get("mineru")
    if not isinstance(operation, Mapping) or operation.get("action") != "replace":
        return "not_requested"
    if set(operation) - {"action", "accounts"}:
        return "not_requested"
    accounts = operation.get("accounts")
    if not isinstance(accounts, (list, tuple)):
        return "not_requested"
    _legacy_name, pool_name = store.ACCOUNT_POOL_FIELDS["mineru"]
    try:
        tokens = store.credential_pool({pool_name: accounts}, "mineru")
    except CredentialStoreError as exc:
        raise CredentialValidationError(str(exc)) from None
    if not tokens:
        return "not_requested"
    with ThreadPoolExecutor(max_workers=min(MAX_ACCOUNT_POOL_SIZE, len(tokens))) as executor:
        results = list(executor.map(_mineru_token_validity, tokens))
    invalid = sum(result is False for result in results)
    if invalid:
        raise CredentialValidationError(
            f"有 {invalid} 个 MinerU Token 未通过官网验证，未保存本次更改。请确认复制完整；过期或权限不足时到 mineru.net 的 API 管理页核对并重新生成，有效期以管理页为准。"
        )
    return "verified" if all(result is True for result in results) else "unverified"


def apply_public_environment(status: Mapping[str, Mapping[str, object]]) -> None:
    """Refresh the web process's non-secret availability snapshot."""

    for service in SERVICES:
        item = status.get(service, {})
        count = item.get("count", 0) if isinstance(item, Mapping) else 0
        try:
            count = max(0, min(MAX_ACCOUNT_POOL_SIZE, int(count)))
        except (TypeError, ValueError):
            count = 0
        os.environ[f"QB_{service.upper()}_CONFIGURED"] = "1" if count else "0"
        os.environ[f"QB_{service.upper()}_POOL_SIZE"] = str(count)


def public_environment_status() -> dict[str, dict[str, object]]:
    """Return the launch/save-time non-secret snapshot without decrypting keys."""

    result: dict[str, dict[str, object]] = {}
    for service in SERVICES:
        try:
            count = int(os.environ.get(f"QB_{service.upper()}_POOL_SIZE", "0"))
        except (TypeError, ValueError):
            count = 0
        count = max(0, min(MAX_ACCOUNT_POOL_SIZE, count))
        configured = os.environ.get(f"QB_{service.upper()}_CONFIGURED") == "1"
        # A non-zero count is authoritative even if an old launcher omitted
        # the companion boolean.  Never infer a count from a secret variable.
        result[service] = {"configured": configured or bool(count), "count": count}
    return result


def refresh_public_environment() -> dict[str, dict[str, object]]:
    """Explicitly decrypt once and refresh the non-secret snapshot.

    Startup/recovery code and tests may call this helper.  Ordinary web GETs
    deliberately use :func:`public_environment_status` instead.
    """

    status = public_status()
    apply_public_environment(status)
    return status


def apply_worker_environment() -> dict[str, dict[str, object]] | None:
    """Load one immutable credential snapshot at a worker task boundary.

    Source/server deployments that start the worker directly may intentionally
    provide API keys as environment variables.  Only desktop launcher children
    carry ``QB_CREDENTIAL_HOT_RELOAD=1``; without it their environment is left
    untouched.
    """

    if os.environ.get("QB_CREDENTIAL_HOT_RELOAD") != "1":
        return None
    values = store.load_credentials()
    pools = {service: store.credential_pool(values, service) for service in SERVICES}
    updates: dict[str, str] = {}
    removals: set[str] = set()
    for service, (pool_name, legacy_name) in SERVICE_ENVIRONMENT.items():
        accounts = pools[service]
        if accounts:
            updates[legacy_name] = accounts[0]
            updates[pool_name] = json.dumps(accounts, ensure_ascii=True, separators=(",", ":"))
        else:
            removals.update({legacy_name, pool_name})

    # All decrypting and validation happens above.  Mutate the live worker only
    # after a complete valid snapshot exists, so a read failure keeps the last
    # known-good task configuration.
    for name in removals:
        os.environ.pop(name, None)
    os.environ.update(updates)
    reset_account_pools()
    return public_status(values)
