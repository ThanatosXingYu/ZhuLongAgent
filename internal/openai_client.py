"""Small OpenAI Chat Completions compatible client used by the solver."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ModelListError(RuntimeError):
    """Raised when an OpenAI-compatible provider cannot list its models."""


def fetch_models(base_url: str, api_key: str = "", timeout: float = 20.0) -> list[str]:
    """Fetch model IDs from an OpenAI-compatible ``/models`` endpoint."""
    normalized = base_url.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelListError("模型地址必须是 http 或 https URL")
    request = Request(
        f"{normalized}/models",
        method="GET",
        headers={"Accept": "application/json", "User-Agent": "gcsis-tools/1.0"},
    )
    if api_key.strip():
        request.add_header("Authorization", f"Bearer {api_key.strip()}")
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-supplied model endpoint.
            status = int(response.status)
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = int(error.code)
        payload = error.read(MAX_RESPONSE_BYTES + 1)
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ModelListError(f"获取模型失败：{exc}") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ModelListError("模型列表响应过大")
    if status < 200 or status >= 300:
        raise ModelListError(f"模型接口返回 HTTP {status}")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelListError("模型接口返回了无效 JSON") from exc
    values: Any
    if isinstance(raw, Mapping):
        values = raw.get("data", raw.get("models", []))
    else:
        values = raw
    if not isinstance(values, list):
        raise ModelListError("模型接口未返回模型列表")
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, Mapping):
            model_id = str(item.get("id", item.get("name", "")) or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id and model_id not in seen and len(model_id) <= 256:
            seen.add(model_id)
            result.append(model_id)
    if not result:
        raise ModelListError("模型接口返回的列表为空")
    return sorted(result, key=str.casefold)


@dataclass(frozen=True)
class FunctionCall:
    name: str = ""
    arguments: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "FunctionCall":
        data = value or {}
        return cls(str(data.get("name", "") or ""), str(data.get("arguments", "") or ""))


@dataclass(frozen=True)
class ToolCall:
    id: str = ""
    type: str = ""
    function: FunctionCall = field(default_factory=FunctionCall)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "type": self.type, "function": self.function.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "ToolCall":
        data = value or {}
        return cls(str(data.get("id", "") or ""), str(data.get("type", "") or ""), FunctionCall.from_dict(data.get("function")))


@dataclass(frozen=True)
class Message:
    role: str
    content: str = ""
    tool_call_id: str = ""
    tool_calls: tuple[ToolCall, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role}
        if self.content:
            result["content"] = self.content
        if self.tool_call_id:
            result["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            result["tool_calls"] = [call.to_dict() for call in self.tool_calls]
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "Message":
        data = value or {}
        raw_calls = data.get("tool_calls")
        calls = tuple(ToolCall.from_dict(item) for item in raw_calls if isinstance(item, Mapping)) if isinstance(raw_calls, list) else ()
        content = data.get("content", "")
        return cls(str(data.get("role", "") or ""), str(content or ""), str(data.get("tool_call_id", "") or ""), calls)


@dataclass(frozen=True)
class ToolFunction:
    name: str
    description: str = ""
    parameters: Mapping[str, Any] = field(default_factory=dict)
    strict: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name, "parameters": dict(self.parameters)}
        if self.description:
            result["description"] = self.description
        if self.strict:
            result["strict"] = True
        return result


@dataclass(frozen=True)
class Tool:
    type: str
    function: ToolFunction

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "function": self.function.to_dict()}


class OpenAIClient:
    def __init__(self, base_url: str, api_key: str = "", timeout: float = 120.0) -> None:
        self.endpoint = ""
        self.api_key = ""
        self.timeout = timeout
        self.configure(base_url, api_key)

    def configure(self, base_url: str, api_key: str = "") -> None:
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions" if base_url.strip() else ""
        self.api_key = api_key.strip()

    def enabled(self) -> bool:
        return bool(self.endpoint)

    def complete(self, model: str, messages: list[Message], tools: list[Tool] | None = None) -> Message:
        if not self.enabled():
            raise RuntimeError("model endpoint is not configured")
        selected_tools = tools or []
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message.to_dict() for message in messages],
        }
        if selected_tools:
            payload["tools"] = [tool.to_dict() for tool in selected_tools]
            payload["tool_choice"] = "auto"
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if self.api_key:
            request.add_header("Authorization", f"Bearer {self.api_key}")
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - endpoint is operator configuration.
                status = int(response.status)
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = int(error.code)
            data = error.read(MAX_RESPONSE_BYTES + 1)
        except (URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise RuntimeError(f"call model: {exc}") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise RuntimeError("model response is too large")
        if status < 200 or status >= 300:
            raise RuntimeError(f"model returned status {status}")
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"decode model response: {exc}") from exc
        choices = result.get("choices") if isinstance(result, Mapping) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError("model response has no choice")
        first = choices[0]
        if not isinstance(first, Mapping) or not isinstance(first.get("message"), Mapping):
            raise RuntimeError("model response has no choice")
        return Message.from_dict(first["message"])
