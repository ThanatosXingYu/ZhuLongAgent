from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from internal.config import Config
from internal.model_router import ModelRouter, set_bound_profile_id
from internal.openai_client import Message, ModelCallError


class FakeClient:
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0
        self.endpoint = ""

    def configure(self, base_url: str, api_key: str = "", protocol: str = "") -> None:
        self.endpoint = f"{base_url}/{protocol}"

    def complete(
        self,
        _model: str,
        _messages: list[Message],
        _tools: list[Any] | None = None,
        *,
        session_id: str = "",
    ) -> Message:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return Message("assistant", self.result)


def _config(strategy: str = "round-robin") -> Config:
    providers = [
        {
            "id": "a",
            "name": "A",
            "baseUrl": "https://a.example/v1",
            "apiKey": "key-a",
            "model": "model-a",
            "protocol": "openai-chat",
            "enabled": True,
            "poolExclude": False,
        },
        {
            "id": "b",
            "name": "B",
            "baseUrl": "https://b.example/v1",
            "apiKey": "key-b",
            "model": "model-b",
            "protocol": "anthropic",
            "enabled": True,
            "poolExclude": False,
        },
    ]
    return Config(
        model_providers_json=json.dumps(providers),
        model_active_provider_id="a",
        model_pool_enabled=True,
        model_routing_strategy=strategy,
        model_retry_delays="1,2",
        model_recovery_interval=60,
    )


def test_model_router_round_robin_keeps_active_first_and_rotates_fallbacks() -> None:
    router = ModelRouter(_config())
    clients = [FakeClient("a"), FakeClient("b")]
    for provider, client in zip(router._providers, clients, strict=True):
        provider.client = client  # type: ignore[assignment]

    first = router.complete("auto", [Message("user", "one")])
    clients[0].result = RuntimeError("active unavailable")
    second = router.complete("auto", [Message("user", "two")])

    assert first.content == "a"
    assert second.content == "b"
    assert [client.calls for client in clients] == [4, 1]


def test_model_router_failover_retries_then_switches_provider() -> None:
    sleeps: list[float] = []
    router = ModelRouter(_config("failover"), sleep=sleeps.append)
    failed = FakeClient(RuntimeError("down"))
    healthy = FakeClient("ok")
    router._providers[0].client = failed  # type: ignore[assignment]
    router._providers[1].client = healthy  # type: ignore[assignment]

    result = router.complete("auto", [Message("user", "solve")])

    assert result.content == "ok"
    assert failed.calls == 3
    assert healthy.calls == 1
    assert sleeps == [1.0, 2.0]
    assert router._providers[0].unavailable_until == 0
    assert router._providers[0].fails == 1


def test_model_router_recovers_provider_after_recovery_window() -> None:
    now = [0.0]
    router = ModelRouter(_config(), sleep=lambda _seconds: None, now=lambda: now[0])
    failed = FakeClient(RuntimeError("temporary"))
    healthy = FakeClient("healthy")
    router._providers[0].client = failed  # type: ignore[assignment]
    router._providers[1].client = healthy  # type: ignore[assignment]

    assert router.complete("auto", [Message("user", "first")]).content == "healthy"
    assert router._providers[0].fails == 1
    assert router.complete("auto", [Message("user", "second")]).content == "healthy"
    assert router._providers[0].fails == 2
    assert router.complete("auto", [Message("user", "third")]).content == "healthy"
    assert router._providers[0].unavailable_until == 30.0
    now[0] = 31.0
    failed.result = "recovered"

    assert router.complete("auto", [Message("user", "after recovery")]).content == "recovered"
    assert failed.calls == 10


def test_model_router_does_not_retry_request_level_failure() -> None:
    router = ModelRouter(_config("failover"), sleep=lambda _seconds: None)
    rejected = FakeClient(ModelCallError("bad request", status=400, retryable=False))
    healthy = FakeClient("ok")
    router._providers[0].client = rejected  # type: ignore[assignment]
    router._providers[1].client = healthy  # type: ignore[assignment]

    with pytest.raises(ModelCallError, match="bad request"):
        router.complete("auto", [Message("user", "solve")])

    assert rejected.calls == 1
    assert healthy.calls == 0
    assert router._providers[0].unavailable_until == 0


def test_model_router_restarts_retry_delays_for_each_provider() -> None:
    sleeps: list[float] = []
    router = ModelRouter(_config("failover"), sleep=sleeps.append)
    router._providers[0].client = FakeClient(RuntimeError("a"))  # type: ignore[assignment]
    router._providers[1].client = FakeClient(RuntimeError("b"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="所有模型提供商均失败"):
        router.complete("auto", [Message("user", "solve")])

    assert sleeps == [1.0, 2.0, 1.0, 2.0]


def test_model_router_pool_disabled_uses_only_active_provider() -> None:
    config = replace(_config("failover"), model_pool_enabled=False)
    router = ModelRouter(config, sleep=lambda _seconds: None)
    active = FakeClient("active")
    excluded = FakeClient("fallback")
    router._providers[0].client = active  # type: ignore[assignment]
    router._providers[1].client = excluded  # type: ignore[assignment]

    assert router.complete("auto", [Message("user", "solve")]).content == "active"
    assert active.calls == 1
    assert excluded.calls == 0


def test_model_router_skips_provider_excluded_from_pool() -> None:
    router = ModelRouter(_config("failover"), sleep=lambda _seconds: None)
    router._providers[0].client = FakeClient(RuntimeError("primary down"))  # type: ignore[assignment]
    fallback = FakeClient("fallback")
    router._providers[1].client = fallback  # type: ignore[assignment]
    router._providers[1].pool_exclude = True

    with pytest.raises(RuntimeError, match="所有模型提供商均失败"):
        router.complete("auto", [Message("user", "solve")])

    assert fallback.calls == 0


def test_bound_profile_uses_fallback_only_when_enabled() -> None:
    config = _config("failover")
    router = ModelRouter(config, sleep=lambda _seconds: None)
    bound = FakeClient(ModelCallError("unauthorized", status=401, retryable=False))
    fallback = FakeClient("fallback")
    router._providers[0].client = bound  # type: ignore[assignment]
    router._providers[1].client = fallback  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="所有模型提供商均失败"):
        router.complete(set_bound_profile_id("auto", "a"), [Message("user", "one")])

    assert bound.calls == 1
    assert fallback.calls == 0

    enabled = replace(config, model_pool_bind_fallback=True)
    router = ModelRouter(enabled, sleep=lambda _seconds: None)
    bound = FakeClient(ModelCallError("unauthorized", status=401, retryable=False))
    fallback = FakeClient("fallback")
    router._providers[0].client = bound  # type: ignore[assignment]
    router._providers[1].client = fallback  # type: ignore[assignment]

    assert router.complete(
        set_bound_profile_id("auto", "a"), [Message("user", "two")]
    ).content == "fallback"
    assert bound.calls == 1
    assert fallback.calls == 1


def test_hard_failure_trips_breaker_immediately_and_reset_restores_it() -> None:
    router = ModelRouter(_config("failover"), sleep=lambda _seconds: None)
    hard = FakeClient(ModelCallError("forbidden", status=403, retryable=False))
    fallback = FakeClient("fallback")
    router._providers[0].client = hard  # type: ignore[assignment]
    router._providers[1].client = fallback  # type: ignore[assignment]

    assert router.complete("auto", [Message("user", "one")]).content == "fallback"
    assert router._providers[0].unavailable_until > 0
    assert router.status()["providers"][0]["state"] == "tripped"

    router.reset("a")
    assert router._providers[0].unavailable_until == 0
    assert router.status()["providers"][0]["state"] == "ok"


def test_model_router_rate_limit_waits_before_second_call() -> None:
    clock = [100.0]
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    router = ModelRouter(_config("failover"), sleep=sleep, now=lambda: clock[0])
    router._providers[0].rate_per_second = 1
    router._providers[0].rate_per_minute = 0
    client = FakeClient("ok")
    router._providers[0].client = client  # type: ignore[assignment]

    assert router.complete("auto", [Message("user", "one")]).content == "ok"
    assert router.complete("auto", [Message("user", "two")]).content == "ok"

    assert client.calls == 2
    assert sleeps == [1.0]


def test_model_router_skips_profile_that_cannot_hold_context() -> None:
    router = ModelRouter(_config("failover"), sleep=lambda _seconds: None)
    small = FakeClient("small")
    large = FakeClient("large")
    router._providers[0].client = small  # type: ignore[assignment]
    router._providers[0].context_window_k = 1
    router._providers[1].client = large  # type: ignore[assignment]
    router._providers[1].context_window_k = 1000

    result = router.complete(
        "auto", [Message("user", "x" * 4000)]
    )

    assert result.content == "large"
    assert small.calls == 0
    assert large.calls == 1


def test_profile_retry_overrides_apply_to_each_failure_layer() -> None:
    sleeps: list[float] = []
    config = _config("failover")
    providers = json.loads(config.model_providers_json)
    providers[0]["retry"] = {
        "connect": {"attempts": 1, "interval_ms": 250},
        "empty": {"attempts": 2, "interval_ms": 500},
        "stream": {"attempts": -1, "interval_ms": 0},
    }
    providers[1]["poolExclude"] = True
    config = replace(config, model_providers_json=json.dumps(providers))
    router = ModelRouter(config, sleep=sleeps.append)

    connect = FakeClient(ModelCallError("timeout", layer="connect"))
    empty = FakeClient(ModelCallError("empty", layer="empty"))
    stream = FakeClient(ModelCallError("stream", layer="stream"))

    for client, expected in ((connect, 2), (empty, 3), (stream, 1)):
        router._providers[0].client = client  # type: ignore[assignment]
        router._providers[0].fails = 0
        router._providers[0].unavailable_until = 0
        with pytest.raises(RuntimeError, match="所有模型提供商均失败"):
            router.complete("auto", [Message("user", "solve")])
        assert client.calls == expected

    assert sleeps == [0.25, 0.5, 0.5]
