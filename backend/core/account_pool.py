"""In-memory account pools for cloud services.

Secrets arrive only in the worker process environment.  This module never
logs, persists, or exposes them through Django responses.  Every account has a
single lease at a time, so adding accounts increases concurrency without
silently multiplying requests on one account.
"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


MAX_ACCOUNTS = 8
SERVICE_ENVIRONMENT = {
    "mineru": ("MINERU_TOKENS_JSON", "MINERU_TOKEN"),
    "minimax": ("MINIMAX_API_KEYS_JSON", "MINIMAX_API_KEY"),
    "siliconflow": ("SILICONFLOW_API_KEYS_JSON", "SILICONFLOW_API_KEY"),
}


class AccountPoolError(RuntimeError):
    """Safe configuration/exhaustion error that never includes a secret."""


def _normalise_secret(value: object, *, bearer: bool = False) -> str:
    if not isinstance(value, str):
        return ""
    result = value.strip()
    if bearer and result.lower().startswith("bearer "):
        result = result[7:].strip()
    if not result or any(character.isspace() for character in result):
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in result):
        return ""
    return result


def secrets_from_environment(service: str) -> tuple[str, ...]:
    """Read one service's JSON pool, with the legacy single value as fallback."""

    try:
        pool_name, legacy_name = SERVICE_ENVIRONMENT[service]
    except KeyError:
        raise ValueError(f"unknown service: {service}") from None
    raw_pool = os.environ.get(pool_name, "").strip()
    if raw_pool:
        try:
            payload = json.loads(raw_pool)
        except (TypeError, ValueError):
            raise AccountPoolError("账号池配置无法读取，请重新保存 API 配置") from None
        if not isinstance(payload, list):
            raise AccountPoolError("账号池配置格式不正确，请重新保存 API 配置")
        candidates = payload
    else:
        candidates = [os.environ.get(legacy_name, "")]
    result: list[str] = []
    for candidate in candidates:
        secret = _normalise_secret(candidate, bearer=service == "mineru")
        if not secret:
            if candidate:
                raise AccountPoolError("账号池中有格式不正确的凭据，请重新保存 API 配置")
            continue
        if secret not in result:
            result.append(secret)
    if len(result) > MAX_ACCOUNTS:
        raise AccountPoolError(f"每项服务最多保存 {MAX_ACCOUNTS} 个账号")
    return tuple(result)


@dataclass
class _State:
    ready_at: float = 0.0
    in_use: bool = False
    disabled: bool = False
    disabled_reason: str = ""


class AccountLease:
    """One opaque account reservation.  Its repr intentionally hides the value."""

    __slots__ = ("_pool", "_secret", "_slot", "_disable", "_disable_reason", "_delay")

    def __init__(self, pool: "AccountPool", secret: str, slot: int) -> None:
        self._pool = pool
        self._secret = secret
        self._slot = slot
        self._disable = False
        self._disable_reason = ""
        self._delay = 0.0

    @property
    def secret(self) -> str:
        return self._secret

    @property
    def slot(self) -> int:
        """Opaque, process-local account number used only for one-call failover."""

        return self._slot

    def disable(self, reason: str = "invalid") -> None:
        """Remove an invalid/expired account until the worker restarts."""

        self._disable = True
        self._disable_reason = reason

    def cooldown(self, seconds: float) -> None:
        """Keep a rate-limited account unavailable for a bounded interval."""

        self._delay = max(self._delay, min(60.0, max(0.0, float(seconds))))

    def __repr__(self) -> str:
        return "AccountLease(<redacted>)"


class AccountPool:
    def __init__(self, service: str, secrets: tuple[str, ...]) -> None:
        self.service = service
        self._secrets = secrets
        self._states = {secret: _State() for secret in secrets}
        self._condition = threading.Condition()
        self._cursor = 0

    @property
    def size(self) -> int:
        return len(self._secrets)

    @property
    def enabled_size(self) -> int:
        with self._condition:
            return sum(not state.disabled for state in self._states.values())

    @property
    def quota_exhausted(self) -> bool:
        """Whether every configured account was disabled by confirmed quota exhaustion."""

        with self._condition:
            return bool(self._states) and all(
                state.disabled and state.disabled_reason == "quota"
                for state in self._states.values()
            )

    def _acquire(self, exclude: frozenset[int] = frozenset()) -> AccountLease:
        with self._condition:
            while True:
                now = time.monotonic()
                enabled = [
                    (index, secret) for index, secret in enumerate(self._secrets)
                    if not self._states[secret].disabled
                ]
                if not enabled:
                    raise AccountPoolError(f"{self.service} 账号池中没有可用账号")
                if not any(index not in exclude for index, _secret in enabled):
                    raise AccountPoolError(f"{self.service} 本次请求已尝试所有可用账号")
                for offset in range(len(self._secrets)):
                    index = (self._cursor + offset) % len(self._secrets)
                    if index in exclude:
                        continue
                    secret = self._secrets[index]
                    state = self._states[secret]
                    if not state.disabled and not state.in_use and state.ready_at <= now:
                        state.in_use = True
                        self._cursor = (index + 1) % len(self._secrets)
                        return AccountLease(self, secret, index)
                ready_times = [
                    self._states[secret].ready_at for index, secret in enabled
                    if index not in exclude and not self._states[secret].in_use
                    and self._states[secret].ready_at > now
                ]
                timeout = max(0.01, min(ready_times) - now) if ready_times else None
                self._condition.wait(timeout=timeout)

    def _release(self, lease: AccountLease) -> None:
        with self._condition:
            state = self._states[lease._secret]
            state.in_use = False
            if lease._disable:
                state.disabled = True
                state.disabled_reason = lease._disable_reason
            elif lease._delay:
                state.ready_at = max(state.ready_at, time.monotonic() + lease._delay)
            self._condition.notify_all()

    @contextmanager
    def lease(self, *, exclude: set[int] | frozenset[int] | None = None) -> Iterator[AccountLease]:
        lease = self._acquire(frozenset(exclude or ()))
        try:
            yield lease
        finally:
            self._release(lease)


_POOL_LOCK = threading.Lock()
_POOLS: dict[tuple[str, tuple[str, ...]], AccountPool] = {}


def account_pool(service: str) -> AccountPool:
    secrets = secrets_from_environment(service)
    if not secrets:
        raise AccountPoolError(f"未配置 {service} 账号")
    identity = (service, secrets)
    with _POOL_LOCK:
        pool = _POOLS.get(identity)
        if pool is None:
            pool = AccountPool(service, secrets)
            _POOLS[identity] = pool
        return pool


def reset_account_pools() -> None:
    """Forget process-local leases; used after settings changes and in tests."""

    with _POOL_LOCK:
        _POOLS.clear()
