"""Core Codex process and event models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class ProcessConfig:
    binary: str
    workspace: str
    base_url: str
    api_key: str = ""
    model: str = ""
    output_path: str = ""
    home: str = ""
    resume_session_id: str = ""
    fork_session: bool = False
    protocol: str = "openai-responses"
    model_catalog_path: str = ""


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int = -1
    output: str = ""
    session_id: str = ""
    error: str = ""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens
            + other.cache_write_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens
            + other.reasoning_output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "inputTokens": self.input_tokens,
            "cachedInputTokens": self.cached_input_tokens,
            "cacheWriteInputTokens": self.cache_write_input_tokens,
            "uncachedInputTokens": max(0, self.input_tokens - self.cached_input_tokens),
            "outputTokens": self.output_tokens,
            "reasoningOutputTokens": self.reasoning_output_tokens,
            "totalTokens": self.total_tokens,
        }


@dataclass(frozen=True)
class Event:
    at: datetime
    kind: str
    summary: str
    session_id: str = ""
    usage: TokenUsage | None = None
    sequence: int = 0
    round_number: int = 0
    envelope_type: str = ""
    item_id: str = ""
    status: str = ""
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    repeat_count: int = 1
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "at": self.at.isoformat().replace("+00:00", "Z"),
            "kind": self.kind,
            "summary": self.summary,
        }
        if self.session_id:
            result["sessionId"] = self.session_id
        if self.usage is not None:
            result["usage"] = self.usage.to_dict()
        if self.sequence > 0:
            result["sequence"] = self.sequence
        if self.round_number > 0:
            result["roundNumber"] = self.round_number
        if self.envelope_type:
            result["envelopeType"] = self.envelope_type
        if self.item_id:
            result["itemId"] = self.item_id
        if self.status:
            result["status"] = self.status
        if self.command:
            result["command"] = self.command
        if self.output:
            result["output"] = self.output
        if self.exit_code is not None:
            result["exitCode"] = self.exit_code
        if self.started_at is not None:
            result["startedAt"] = _wire_time(self.started_at)
        if self.finished_at is not None:
            result["finishedAt"] = _wire_time(self.finished_at)
        if self.duration_seconds is not None:
            result["durationSeconds"] = max(0.0, self.duration_seconds)
        if self.repeat_count > 1:
            result["repeatCount"] = self.repeat_count
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


def _wire_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
