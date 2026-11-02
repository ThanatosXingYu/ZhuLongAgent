from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from internal.config import Config
from internal.model_router import ModelRouter
from internal.openai_client import Message, ModelCallError


class FakeClient:
    def __init__(self, result: str | Exception) -> None:
        self.result = result
        self.calls = 0
        self.endpoint = ""

    def configure(self, base_url: str, api_key: str = "", protocol: str = "") -> None:
        self.endpoint = f"{base_url}/{protocol}"

    def complete(
        self, _model: str, _messages: list[Message], _tools: list[Any] | None = None
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


def test_model_router_round_robin_rotates_providers() -> None:
    router = ModelRouter(_config())
    clients = [FakeClient("a"), FakeClient("b")]
    for provider, client in zip(router._providers, clients, strict=True):
        provider.client = client  # type: ignore[assignment]

    first = router.complete("auto", [Message("user", "one")])
    second = router.complete("auto", [Message("user", "two")])

    assert first.content == "a"
    assert second.content == "b"
    assert [client.calls for client in clients] == [1, 1]


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
    assert sleeps == [1.0, 2.0, 2.0]
    assert router._providers[0].unavailable_until > 0


def test_model_router_recovers_provider_after_recovery_window() -> None:
    now = [0.0]
    router = ModelRouter(_config(), sleep=lambda _seconds: None, now=lambda: now[0])
    failed = FakeClient(RuntimeError("temporary"))
    healthy = FakeClient("healthy")
    router._providers[0].client = failed  # type: ignore[assignment]
    router._providers[1].client = healthy  # type: ignore[assignment]

    assert router.complete("auto", [Message("user", "first")]).content == "healthy"
    assert router._providers[0].unavailable_until == 60.0
    assert router.complete("auto", [Message("user", "second")]).content == "healthy"
    now[0] = 61.0
    failed.result = "recovered"

    assert router.complete("auto", [Message("user", "after recovery")]).content == "recovered"
    assert failed.calls == 2


def test_model_router_does_not_retry_request_level_failure() -> None:
    router = ModelRouter(_config("failover"), sleep=lambda _seconds: None)
    rejected = FakeClient(ModelCallError("bad request", status=400, retryable=False))
    healthy = FakeClient("ok")
    router._providers[0].client = rejected  # type: ignore[assignment]
    router._providers[1].client = healthy  # type: ignore[assignment]

    result = router.complete("auto", [Message("user", "solve")])

    assert result.content == "ok"
    assert rejected.calls == 1
    assert healthy.calls == 1
    assert router._providers[0].unavailable_until == 0


def test_model_router_restarts_retry_delays_for_each_provider() -> None:
    sleeps: list[float] = []
    router = ModelRouter(_config("failover"), sleep=sleeps.append)
    router._providers[0].client = FakeClient(RuntimeError("a"))  # type: ignore[assignment]
    router._providers[1].client = FakeClient(RuntimeError("b"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="所有模型提供商均失败"):
        router.complete("auto", [Message("user", "solve")])

    assert sleeps == [1.0, 2.0, 2.0, 1.0, 2.0]


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
