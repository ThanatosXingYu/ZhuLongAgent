"""LLM profile routing, retry policy, failover, and breaker state."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .config import Config, ConfigError
from .openai_client import Message, ModelCallError, OpenAIClient, Tool

_DEFAULT_POLICY: dict[str, dict[str, int]] = {
    "connect": {"attempts": 0, "interval_ms": 0},
    "empty": {"attempts": 0, "interval_ms": 0},
    "stream": {"attempts": 0, "interval_ms": 0},
    "breaker": {"attempts": 0, "interval_ms": 0},
    "intent": {"attempts": 0, "interval_ms": 0},
}
_PROFILE_LAYERS = ("connect", "empty", "stream")
_DEFAULT_RETRY_DELAYS = (1.0, 5.0, 10.0, 10.0, 15.0)
_DEFAULT_RECOVERY_SECONDS = 60
_DEFAULT_BREAKER_ATTEMPTS = 3
_DEFAULT_BREAKER_INTERVALS = (30, 60, 60, 120, 300, 600)
_BIND_PREFIX = "bind:"


@dataclass
class _ProviderState:
    provider_id: str
    name: str
    model: str
    client: OpenAIClient
    protocol: str
    enabled: bool
    pool_exclude: bool = False
    priority: int = 0
    unavailable_until: float = 0.0
    last_error: str = ""
    fails: int = 0
    trips: int = 0
    last_at: float = 0.0
    retry: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class _RetryRule:
    attempts: int = 0
    interval_ms: int = 0


@dataclass(frozen=True)
class _RetryPolicy:
    connect: _RetryRule = _RetryRule()
    empty: _RetryRule = _RetryRule()
    stream: _RetryRule = _RetryRule()
    breaker: _RetryRule = _RetryRule()
    intent: _RetryRule = _RetryRule()


class ModelRouter:
    """BRTEX-compatible active-profile routing with optional failover."""

    def __init__(
        self,
        config: Config | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._lock = threading.RLock()
        self._sleep = sleep
        self._now = now
        self._providers: list[_ProviderState] = []
        self._active_provider_id = ""
        self._pool_enabled = False
        self._bind_fallback = False
        self._strategy = "failover"
        self._delays: list[float] = list(_DEFAULT_RETRY_DELAYS)
        self._recovery_seconds = _DEFAULT_RECOVERY_SECONDS
        self._retry_policy = _RetryPolicy()
        self._cursor = 0
        if config is not None:
            self.configure(config)

    def configure(self, config: Config) -> None:
        profiles = _provider_configs(config)
        policy = _retry_policy(config.model_retry_policy_json)
        states: list[_ProviderState] = []
        with self._lock:
            previous = {provider.provider_id: provider for provider in self._providers}
            for item in profiles:
                provider_id = str(item["id"])
                old = previous.get(provider_id)
                client = (
                    old.client
                    if old is not None and old.client.protocol == str(item["protocol"])
                    else OpenAIClient(
                        str(item["baseUrl"]),
                        str(item.get("apiKey", "")),
                        protocol=str(item["protocol"]),
                    )
                )
                client.configure_profile(item)
                states.append(
                    _ProviderState(
                        provider_id=provider_id,
                        name=str(item.get("name", "") or provider_id),
                        model=str(item["model"]),
                        client=client,
                        protocol=str(item["protocol"]),
                        enabled=bool(item.get("enabled", True)),
                        pool_exclude=bool(item.get("poolExclude", False)),
                        priority=int(item.get("priority", 0) or 0),
                        unavailable_until=old.unavailable_until if old else 0.0,
                        last_error=old.last_error if old else "",
                        fails=old.fails if old else 0,
                        trips=old.trips if old else 0,
                        last_at=old.last_at if old else 0.0,
                        retry=_normalized_profile_retry(item.get("retry")),
                    )
                )
            self._providers = states
            selected_ids = {provider.provider_id for provider in states}
            requested = config.model_active_provider_id
            self._active_provider_id = (
                requested
                if requested in selected_ids
                else next(
                    (provider.provider_id for provider in states if provider.enabled),
                    "",
                )
            )
            self._pool_enabled = config.model_pool_enabled
            self._bind_fallback = config.model_pool_bind_fallback
            self._strategy = config.model_routing_strategy
            self._delays = _parse_delays(config.model_retry_delays)
            self._recovery_seconds = config.model_recovery_interval
            self._retry_policy = policy
            if states:
                self._cursor %= len(states)

    def enabled(self) -> bool:
        with self._lock:
            return bool(self._eligible_providers(self._active_provider_id))

    def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[Tool] | None = None,
        *,
        session_id: str = "",
    ) -> Message:
        bound_id = _bound_profile_id(model)
        with self._lock:
            head_id = bound_id or self._active_provider_id
            eligible = self._eligible_providers(
                head_id, allow_fallback=not bound_id or self._bind_fallback
            )
            if not eligible:
                raise RuntimeError("所有模型提供商暂不可用")
            ordered = self._ordered_providers(eligible, head_id)
            delays = list(self._delays)
            policy = self._retry_policy
            now = self._now()
            available = [
                provider
                for provider in ordered
                if provider.enabled and provider.unavailable_until <= now
            ]
        if not available:
            available = ordered[:1]
        last_error: Exception | None = None
        for provider_index, provider in enumerate(available):
            retry_rule = _provider_retry_rule(provider, policy)
            attempts = _retry_attempts(retry_rule, delays)
            failed: Exception | None = None
            for retry_index in range(attempts):
                try:
                    return self._complete_provider(provider, messages, tools, session_id)
                except Exception as exc:
                    last_error = exc
                    failed = exc
                    if not _can_failover(exc):
                        raise
                    if retry_index + 1 < attempts:
                        if _is_retryable(exc):
                            self._sleep(_retry_delay(retry_rule, delays, retry_index))
                            continue
                        break
            if failed is None:
                continue
            self._record_failure(provider, failed, policy)
            if provider_index + 1 >= len(available):
                break
        raise RuntimeError(
            f"所有模型提供商均失败: {last_error}" if last_error else "模型调用失败"
        ) from last_error

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = self._now()
            ordered = self._ordered_status()
            return {
                "activeProviderId": self._active_provider_id,
                "poolEnabled": self._pool_enabled,
                "bindFallback": self._bind_fallback,
                "strategy": self._strategy,
                "providers": [
                    {
                        "id": provider.provider_id,
                        "profile_id": provider.provider_id,
                        "name": provider.name,
                        "model": provider.model,
                        "protocol": provider.protocol,
                        "format": provider.protocol,
                        "enabled": provider.enabled,
                        "poolExclude": provider.pool_exclude,
                        "excluded": provider.pool_exclude
                        and provider.provider_id != self._active_provider_id,
                        "priority": provider.priority,
                        "isActive": provider.provider_id == self._active_provider_id,
                        "active": provider.provider_id == self._active_provider_id,
                        "available": provider.enabled
                        and provider.unavailable_until <= now,
                        "state": "tripped"
                        if provider.unavailable_until > now
                        else "degraded"
                        if provider.fails > 0
                        else "ok",
                        "fails": provider.fails,
                        "trips": provider.trips,
                        "cooldownSeconds": max(
                            0, int(provider.unavailable_until - now)
                        ),
                        "cooldown_secs": max(
                            0, int(provider.unavailable_until - now)
                        ),
                        "lastError": provider.last_error,
                        "last_error": provider.last_error,
                    }
                    for provider in ordered
                ],
            }

    def reset(self, provider_id: str = "") -> dict[str, Any]:
        with self._lock:
            for provider in self._providers:
                if not provider_id or provider.provider_id == provider_id:
                    provider.unavailable_until = 0.0
                    provider.last_error = ""
                    provider.fails = 0
                    provider.trips = 0
        return self.status()

    def set_retry_policy(self, value: str) -> None:
        policy = _retry_policy(value)
        with self._lock:
            self._retry_policy = policy

    def set_pool_switches(
        self, *, enabled: bool | None = None, bind_fallback: bool | None = None
    ) -> None:
        with self._lock:
            if enabled is not None:
                self._pool_enabled = enabled
            if bind_fallback is not None:
                self._bind_fallback = bind_fallback

    def _complete_provider(
        self,
        provider: _ProviderState,
        messages: list[Message],
        tools: list[Tool] | None,
        session_id: str,
    ) -> Message:
        message = provider.client.complete(
            provider.model, messages, tools, session_id=session_id
        )
        with self._lock:
            provider.unavailable_until = 0.0
            provider.last_error = ""
            provider.fails = 0
        return message

    def _record_failure(
        self, provider: _ProviderState, exc: Exception, policy: _RetryPolicy
    ) -> None:
        with self._lock:
            provider.fails += 1
            provider.last_error = str(exc)
            provider.last_at = self._now()
        if _is_hard_failure(exc):
            self._trip(provider, policy)
            return
        threshold = policy.breaker.attempts
        if threshold < 0:
            return
        if threshold == 0:
            threshold = _DEFAULT_BREAKER_ATTEMPTS
        with self._lock:
            if provider.fails >= threshold:
                self._trip(provider, policy)

    def _trip(self, provider: _ProviderState, policy: _RetryPolicy) -> None:
        intervals = (
            (policy.breaker.interval_ms / 1000,)
            if policy.breaker.interval_ms > 0
            else tuple(float(value) for value in _DEFAULT_BREAKER_INTERVALS)
        )
        seconds = intervals[min(max(provider.trips, 0), len(intervals) - 1)]
        with self._lock:
            provider.trips += 1
            provider.unavailable_until = self._now() + seconds
            provider.fails = 0

    def _ordered_providers(
        self, available: list[_ProviderState], head_id: str
    ) -> list[_ProviderState]:
        active = [provider for provider in available if provider.provider_id == head_id]
        others = [provider for provider in available if provider.provider_id != head_id]
        if self._strategy not in {"round-robin", "priority"} or not others:
            others.sort(key=lambda provider: -provider.priority)
            return active + others
        buckets: dict[int, list[_ProviderState]] = {}
        for provider in others:
            buckets.setdefault(provider.priority, []).append(provider)
        ordered: list[_ProviderState] = []
        for priority in sorted(buckets, reverse=True):
            bucket = buckets[priority]
            start = self._cursor % len(bucket)
            ordered.extend(bucket[start:] + bucket[:start])
            self._cursor = (start + 1) % len(bucket)
        return active + ordered

    def _ordered_status(self) -> list[_ProviderState]:
        active = [
            provider
            for provider in self._providers
            if provider.provider_id == self._active_provider_id
        ]
        others = [
            provider
            for provider in self._providers
            if provider.provider_id != self._active_provider_id
        ]
        others.sort(key=lambda provider: (-provider.priority, provider.provider_id))
        return active + others

    def _eligible_providers(
        self, head_id: str, *, allow_fallback: bool = True
    ) -> list[_ProviderState]:
        head = [
            provider
            for provider in self._providers
            if provider.provider_id == head_id
        ]
        if not self._pool_enabled or not allow_fallback:
            return head
        return head + [
            provider
            for provider in self._providers
            if provider.provider_id != head_id and not provider.pool_exclude
        ]


def set_bound_profile_id(model: str, profile_id: str) -> str:
    """Return the model selector used to pin a call to one profile."""
    if not profile_id:
        return model
    return f"{_BIND_PREFIX}{profile_id}"


def _bound_profile_id(model: str) -> str:
    return model[len(_BIND_PREFIX) :] if model.startswith(_BIND_PREFIX) else ""


def _retry_policy(value: str) -> _RetryPolicy:
    try:
        raw = json.loads(value or "{}")
    except json.JSONDecodeError as exc:
        raise ConfigError("model_retry_policy_json is invalid") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError("model_retry_policy_json is invalid")
    rules: dict[str, _RetryRule] = {}
    for layer in _DEFAULT_POLICY:
        candidate = raw.get(layer, {})
        if not isinstance(candidate, Mapping):
            raise ConfigError("model_retry_policy_json is invalid")
        rules[layer] = _RetryRule(
            int(candidate.get("attempts", 0) or 0),
            int(candidate.get("interval_ms", 0) or 0),
        )
    return _RetryPolicy(**rules)


def _provider_retry_rule(provider: _ProviderState, policy: _RetryPolicy) -> _RetryRule:
    rule = provider.retry.get("stream") if provider.retry else None
    if rule is None:
        return policy.stream
    attempts = rule["attempts"]
    interval = rule["interval_ms"]
    return _RetryRule(
        attempts if attempts != 0 else policy.stream.attempts,
        interval if interval != 0 else policy.stream.interval_ms,
    )


def _normalized_profile_retry(value: Any) -> Mapping[str, Mapping[str, int]]:
    source = value if isinstance(value, Mapping) else {}
    return {
        layer: {
            "attempts": int(
                (source.get(layer) or {}).get("attempts", 0)
                if isinstance(source.get(layer), Mapping)
                else 0
            ),
            "interval_ms": int(
                (source.get(layer) or {}).get("interval_ms", 0)
                if isinstance(source.get(layer), Mapping)
                else 0
            ),
        }
        for layer in _PROFILE_LAYERS
    }


def _retry_attempts(rule: _RetryRule, delays: list[float]) -> int:
    if rule.attempts < 0:
        return 1
    if rule.attempts > 0:
        return rule.attempts + 1
    return len(delays) + 1


def _retry_delay(rule: _RetryRule, delays: list[float], index: int) -> float:
    if rule.interval_ms > 0:
        return rule.interval_ms / 1000
    if rule.attempts > 0:
        return 0
    return delays[min(index, len(delays) - 1)]


def _parse_delays(value: str) -> list[float]:
    delays = [
        float(item.strip())
        for item in str(value or "").split(",")
        if item.strip()
    ]
    return delays or list(_DEFAULT_RETRY_DELAYS)


def _load_profiles(config: Config) -> list[dict[str, Any]]:
    try:
        raw = json.loads(config.model_providers_json or "[]")
    except json.JSONDecodeError:
        raw = []
    if isinstance(raw, list) and raw:
        return [dict(item) for item in raw if isinstance(item, dict)]
    if config.model_base_url and config.model_name:
        return [
            {
                "id": "legacy",
                "name": "默认提供商",
                "baseUrl": config.model_base_url,
                "apiKey": config.model_api_key,
                "model": config.model_name,
                "protocol": config.model_protocol,
                "enabled": True,
                "poolExclude": False,
                "priority": 0,
                "streaming": True,
                "retry": {},
            }
        ]
    return []


def _provider_configs(config: Config) -> list[dict[str, Any]]:
    profiles = _load_profiles(config)
    for profile in profiles:
        profile.setdefault("priority", 0)
        profile.setdefault("streaming", True)
        profile.setdefault("retry", {})
    return profiles


def _is_retryable(error: Exception) -> bool:
    return not isinstance(error, ModelCallError) or error.retryable


def _can_failover(error: Exception) -> bool:
    if not isinstance(error, ModelCallError) or error.status == 0:
        return True
    if error.status == 400:
        return False
    return error.status in {401, 402, 403, 404, 408, 429} or error.status >= 500


def _is_hard_failure(error: Exception) -> bool:
    return isinstance(error, ModelCallError) and error.status in {401, 402, 403, 404}
