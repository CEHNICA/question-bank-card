"""In-memory account pools for cloud services.

Secrets arrive only in the worker process environment.  This module never
logs, persists, or exposes them through Django responses.

Each account may carry a small, bounded number of simultaneous leases.
Measured against a single MiniMax Token Plan key, eight concurrent vision
requests completed without a single HTTP 429 while per-request latency only
rose from ~3.7 s to ~5 s, so one-request-per-account left most of the paid
throughput unused.  The per-account limit is configurable per service and
adapts downward for the rest of the run whenever the provider answers 429.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


MAX_ACCOUNTS = 8
MAX_ACCOUNT_CONCURRENCY = 8
# Conservative defaults: vision providers tolerate a few parallel requests per
# key; MinerU tasks are long-running uploads and stay at one per token.
DEFAULT_ACCOUNT_CONCURRENCY = {"minimax": 6, "siliconflow": 2, "mineru": 1}
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


def account_concurrency(service: str) -> int:
    """Simultaneous requests allowed on one account of ``service``.

    ``QB_<SERVICE>_ACCOUNT_CONCURRENCY`` overrides the default.  Invalid values
    fall back to the default; valid ones are clamped to 1..8.
    """

    default = DEFAULT_ACCOUNT_CONCURRENCY.get(service, 1)
    raw = os.environ.get(f"QB_{service.upper()}_ACCOUNT_CONCURRENCY", "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    return max(1, min(MAX_ACCOUNT_CONCURRENCY, value))


# Clean requests per parallel slot before a throttled slot is given back.  At
# about five seconds a request this returns one slot every ~20 s whatever the
# current level; a flat count of 20 left a pool knocked down to one slot
# crawling back for minutes (measured: 8 → 1 in one burst, 200 s at 1).
RECOVER_SUCCESSES_PER_SLOT = 4


def recover_after(capacity: int) -> int:
    return RECOVER_SUCCESSES_PER_SLOT * max(1, int(capacity))

# Lower is more urgent.  The worker tags each paper's reading with the
# paper's upload time, so when two papers overlap the older one gets every
# slot it asks for and the newer one only fills the gaps.  Untagged work
# (single-card rereads a user is waiting on, tests) is the most urgent.
_PRIORITY: contextvars.ContextVar[float] = contextvars.ContextVar("qb_lease_priority", default=0.0)


@contextmanager
def lease_priority(value: float) -> Iterator[None]:
    token = _PRIORITY.set(float(value))
    try:
        yield
    finally:
        _PRIORITY.reset(token)


def current_priority() -> float:
    return _PRIORITY.get()


@dataclass
class _State:
    ready_at: float = 0.0
    active: int = 0
    capacity: int = 1
    disabled: bool = False
    disabled_reason: str = ""
    limit: int = 1           # configured per-account concurrency
    successes: int = 0       # clean releases since the last throttle
    epoch: int = 0           # bumped whenever a throttle removes a slot

    @property
    def in_use(self) -> bool:
        """Compatibility view: whether the account has no free lease slot."""

        return self.active >= self.capacity


class AccountLease:
    """One opaque account reservation.  Its repr intentionally hides the value."""

    __slots__ = ("_pool", "_secret", "_slot", "_disable", "_disable_reason", "_delay", "_throttled", "_epoch")

    def __init__(self, pool: "AccountPool", secret: str, slot: int, epoch: int = 0) -> None:
        self._pool = pool
        self._secret = secret
        self._slot = slot
        self._epoch = epoch
        self._disable = False
        self._disable_reason = ""
        self._delay = 0.0
        self._throttled = False

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
        """Keep a rate-limited account unavailable for a bounded interval.

        A cooldown is the provider telling us this account is over its limit,
        so the account also loses one parallel slot for the rest of the run.
        """

        self._delay = max(self._delay, min(60.0, max(0.0, float(seconds))))
        self._throttled = True

    def __repr__(self) -> str:
        return "AccountLease(<redacted>)"


class AccountPool:
    def __init__(self, service: str, secrets: tuple[str, ...], per_account: int = 1) -> None:
        self.service = service
        self._secrets = secrets
        capacity = max(1, min(MAX_ACCOUNT_CONCURRENCY, int(per_account)))
        self._states = {secret: _State(capacity=capacity, limit=capacity) for secret in secrets}
        self._condition = threading.Condition()
        self._cursor = 0
        # priority -> number of requests currently waiting for a slot
        self._waiting: dict[float, int] = {}

    @property
    def capacity(self) -> int:
        """Total simultaneous leases currently allowed across enabled accounts."""

        with self._condition:
            return sum(state.capacity for state in self._states.values() if not state.disabled)

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
        priority = current_priority()
        with self._condition:
            self._waiting[priority] = self._waiting.get(priority, 0) + 1
            try:
                return self._acquire_locked(exclude, priority)
            finally:
                left = self._waiting[priority] - 1
                if left:
                    self._waiting[priority] = left
                else:
                    del self._waiting[priority]
                # A more urgent request leaving the queue may unblock others.
                self._condition.notify_all()

    def _free_slots(self, exclude: frozenset[int], now: float) -> int:
        return sum(
            max(0, state.capacity - state.active)
            for index, secret in enumerate(self._secrets)
            for state in (self._states[secret],)
            if index not in exclude and not state.disabled and state.ready_at <= now
        )

    def _acquire_locked(self, exclude: frozenset[int], priority: float) -> AccountLease:
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
            # Leave free slots to more urgent requests that are waiting.
            ahead = sum(count for level, count in self._waiting.items() if level < priority)
            if ahead and self._free_slots(exclude, now) <= ahead:
                self._condition.wait(timeout=0.5)
                continue
            # Least-loaded account first; the rotating cursor breaks ties so
            # equally idle accounts still share work round-robin.
            best: tuple[int, int, int] | None = None
            for offset in range(len(self._secrets)):
                index = (self._cursor + offset) % len(self._secrets)
                if index in exclude:
                    continue
                state = self._states[self._secrets[index]]
                if state.disabled or state.active >= state.capacity or state.ready_at > now:
                    continue
                rank = (state.active, offset, index)
                if best is None or rank < best:
                    best = rank
            if best is not None:
                index = best[2]
                secret = self._secrets[index]
                self._states[secret].active += 1
                self._cursor = (index + 1) % len(self._secrets)
                return AccountLease(self, secret, index, self._states[secret].epoch)
            ready_times = [
                self._states[secret].ready_at for index, secret in enabled
                if index not in exclude
                and self._states[secret].active < self._states[secret].capacity
                and self._states[secret].ready_at > now
            ]
            timeout = max(0.01, min(ready_times) - now) if ready_times else None
            self._condition.wait(timeout=timeout)

    def _release(self, lease: AccountLease) -> None:
        with self._condition:
            state = self._states[lease._secret]
            state.active = max(0, state.active - 1)
            if lease._disable:
                state.disabled = True
                state.disabled_reason = lease._disable_reason
            else:
                if lease._throttled:
                    state.successes = 0
                    # Requests already in flight when the limit hit all come
                    # back 429 together; that is one signal, not eight.  Only
                    # a request sent after the last cut can cut again.
                    if lease._epoch == state.epoch and state.capacity > 1:
                        state.capacity -= 1
                        state.epoch += 1
                else:
                    # A throttle is usually a burst limit, not a permanent one:
                    # without recovery one bad minute left a long-running worker
                    # (and a 300-page book) on a single request at a time.
                    state.successes += 1
                    if state.capacity < state.limit and state.successes >= recover_after(state.capacity):
                        state.capacity += 1
                        state.successes = 0
                if lease._delay:
                    state.ready_at = max(state.ready_at, time.monotonic() + lease._delay)
            self._condition.notify_all()

    @property
    def spare(self) -> int:
        """Leases that could start right now without waiting."""

        with self._condition:
            now = time.monotonic()
            return sum(
                max(0, state.capacity - state.active) for state in self._states.values()
                if not state.disabled and state.ready_at <= now
            )

    @contextmanager
    def lease(self, *, exclude: set[int] | frozenset[int] | None = None) -> Iterator[AccountLease]:
        lease = self._acquire(frozenset(exclude or ()))
        try:
            yield lease
        finally:
            self._release(lease)


_POOL_LOCK = threading.Lock()
_POOLS: dict[tuple[str, tuple[str, ...], int], AccountPool] = {}


def account_pool(service: str) -> AccountPool:
    secrets = secrets_from_environment(service)
    if not secrets:
        raise AccountPoolError(f"未配置 {service} 账号")
    per_account = account_concurrency(service)
    identity = (service, secrets, per_account)
    with _POOL_LOCK:
        pool = _POOLS.get(identity)
        if pool is None:
            pool = AccountPool(service, secrets, per_account)
            _POOLS[identity] = pool
        return pool


def reset_account_pools() -> None:
    """Forget process-local leases; used after settings changes and in tests."""

    with _POOL_LOCK:
        _POOLS.clear()
