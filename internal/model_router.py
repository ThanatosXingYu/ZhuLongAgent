"""Multi-provider model routing with retry, failover, and recovery windows."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .openai_client import Message, ModelCallError, OpenAIClient, Tool


@dataclass
class _ProviderState:
    provider_id: str
    name: str
    model: str
    client: OpenAIClient
    enabled: bool
    unavailable_until: float = 0.0
    last_error: str = ""


class ModelRouter:
    """Route solver calls across enabled providers."""

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
        self._strategy = "round-robin"
        self._delays: list[float] = []
        self._recovery_seconds = 60
        self._cursor = 0
        if config is not None:
            self.configure(config)

    def configure(self, config: Config) -> None:
        providers = _provider_configs(config)
        states: list[_ProviderState] = []
        with self._lock:
            previous = {provider.provider_id: provider for provider in self._providers}
            for item in providers:
                provider_id = str(item["id"])
                old = previous.get(provider_id)
                client = (
                    old.client
                    if old is not None
                    and old.client.endpoint.endswith(_protocol_suffix(str(item["protocol"])))
                    else OpenAIClient(
                        str(item["baseUrl"]),
                        str(item.get("apiKey", "")),
                        protocol=str(item["protocol"]),
                    )
                )
                client.configure(
                    str(item["baseUrl"]),
                    str(item.get("apiKey", "")),
                    str(item["protocol"]),
                )
                states.append(
                    _ProviderState(
                        provider_id,
                        str(item.get("name", "") or provider_id),
                        str(item["model"]),
                        client,
                        bool(item.get("enabled", True)),
                        old.unavailable_until if old is not None else 0.0,
                        old.last_error if old is not None else "",
                    )
                )
            self._providers = states
            self._strategy = config.model_routing_strategy
            self._delays = [
                float(value.strip())
                for value in config.model_retry_delays.split(",")
                if value.strip()
            ]
            self._recovery_seconds = config.model_recovery_interval
            if states:
                self._cursor %= len(states)

    def enabled(self) -> bool:
        with self._lock:
            return any(provider.enabled for provider in self._providers)

    def complete(
        self, _model: str, messages: list[Message], tools: list[Tool] | None = None
    ) -> Message:
        with self._lock:
            now = self._now()
            available = [
                provider
                for provider in self._providers
                if provider.enabled and provider.unavailable_until <= now
            ]
            if not available:
                raise RuntimeError("所有模型提供商暂不可用")
            ordered = self._ordered_providers(available)
            strategy = self._strategy
            delays = list(self._delays)
        last_error: Exception | None = None
        if strategy == "round-robin":
            for provider in ordered:
                try:
                    return self._complete_provider(provider, messages, tools)
                except Exception as exc:
                    last_error = exc
                    with self._lock:
                        provider.last_error = str(exc)
                    if _is_retryable(exc):
                        self._mark_unavailable(provider)
        else:
            per_provider_attempts = len(delays) + 1
            for provider_index, provider in enumerate(ordered):
                for retry_index in range(per_provider_attempts):
                    try:
                        return self._complete_provider(provider, messages, tools)
                    except Exception as exc:
                        last_error = exc
                        with self._lock:
                            provider.last_error = str(exc)
                        if not _is_retryable(exc):
                            break
                        self._mark_unavailable_if_exhausted(
                            provider, retry_index, len(delays)
                        )
                        if isinstance(exc, ModelCallError) and exc.timed_out:
                            self._mark_unavailable(provider)
                            break
                        if retry_index < len(delays):
                            self._sleep(delays[retry_index])
                        elif provider_index < len(ordered) - 1:
                            self._sleep(delays[-1] if delays else 0)
        raise RuntimeError(
            f"所有模型提供商均失败: {last_error}" if last_error else "模型调用失败"
        ) from last_error

    def _complete_provider(
        self,
        provider: _ProviderState,
        messages: list[Message],
        tools: list[Tool] | None,
    ) -> Message:
        message = provider.client.complete(provider.model, messages, tools)
        with self._lock:
            provider.unavailable_until = 0.0
            provider.last_error = ""
        return message

    def _mark_unavailable_if_exhausted(
        self, provider: _ProviderState, retry_index: int, retry_count: int
    ) -> None:
        if retry_index >= retry_count:
            self._mark_unavailable(provider)

    def _ordered_providers(self, available: list[_ProviderState]) -> list[_ProviderState]:
        if self._strategy == "failover":
            return available
        start = self._cursor % len(available)
        self._cursor = (start + 1) % len(available)
        return available[start:] + available[:start]

    def _mark_unavailable(self, provider: _ProviderState) -> None:
        provider.unavailable_until = self._now() + self._recovery_seconds


def _provider_configs(config: Config) -> list[dict[str, Any]]:
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
            }
        ]
    return []


def _protocol_suffix(protocol: str) -> str:
    return {
        "openai-chat": "/chat/completions",
        "openai-responses": "/responses",
        "anthropic": "/messages",
    }.get(protocol, "/chat/completions")


def _is_retryable(error: Exception) -> bool:
    return not isinstance(error, ModelCallError) or error.retryable
