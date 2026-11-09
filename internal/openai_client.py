"""Small multi-protocol model client used by the solver."""

from __future__ import annotations

import json
import socket
import ssl
import time
from dataclasses import dataclass, field
from http.client import HTTPResponse
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener, urlopen

MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SUPPORTED_PROTOCOLS = frozenset({"openai-responses", "openai-chat", "anthropic"})
DEFAULT_PROTOCOL = "openai-chat"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 8192
USER_AGENT = "gcsis-tools/1.0"


class ModelListError(RuntimeError):
    """Raised when an OpenAI-compatible provider cannot list its models."""


class ModelCallError(RuntimeError):
    """Provider call failure with retryability information."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        retryable: bool = True,
        timed_out: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.timed_out = timed_out


def fetch_models(
    base_url: str,
    api_key: str = "",
    protocol: str = DEFAULT_PROTOCOL,
    timeout: float = 20.0,
) -> list[str]:
    """Fetch model IDs from an OpenAI- or Anthropic-compatible provider."""
    normalized = base_url.strip().rstrip("/")
    selected_protocol = normalize_protocol(protocol)
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ModelListError("模型地址必须是 http 或 https URL")
    request = Request(
        f"{normalized}/models",
        method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
    _add_auth_headers(request, selected_protocol, api_key)
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


def test_model_connection(
    base_url: str,
    api_key: str,
    model: str,
    protocol: str = DEFAULT_PROTOCOL,
    timeout: float = 20.0,
    *,
    proxy: str = "",
    thinking_type: str = "",
    reasoning_effort: str = "",
    streaming: bool = True,
    session_header_key: str = "",
) -> dict[str, Any]:
    started = time.monotonic()
    client = OpenAIClient(
        base_url,
        api_key,
        timeout=timeout,
        protocol=protocol,
        proxy=proxy,
        streaming=streaming,
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
        session_header_key=session_header_key,
    )
    reply = client.complete(
        model,
        [Message("user", "请只回复 OK")],
    )
    return {
        "model": model,
        "reply": (reply.content or "（模型返回了工具调用）").strip()[:500],
        "latencyMs": max(0, round((time.monotonic() - started) * 1000)),
    }


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
    reasoning_content: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"role": self.role}
        if self.content:
            result["content"] = self.content
        if self.reasoning_content:
            result["reasoning_content"] = self.reasoning_content
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
        reasoning = data.get("reasoning_content", "")
        return cls(
            str(data.get("role", "") or ""),
            str(content or ""),
            str(data.get("tool_call_id", "") or ""),
            calls,
            str(reasoning or ""),
        )


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
    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        timeout: float = 120.0,
        protocol: str = DEFAULT_PROTOCOL,
        proxy: str = "",
        streaming: bool = True,
        max_tokens: int = 0,
        max_tokens_field: str = "",
        thinking_type: str = "",
        reasoning_effort: str = "",
        session_header_key: str = "",
    ) -> None:
        self.endpoint = ""
        self.api_key = ""
        self.protocol = DEFAULT_PROTOCOL
        self.timeout = timeout
        self.proxy = ""
        self.streaming = streaming
        self.max_tokens = max_tokens
        self.max_tokens_field = max_tokens_field
        self.thinking_type = thinking_type
        self.reasoning_effort = reasoning_effort
        self.session_header_key = session_header_key
        self.configure(base_url, api_key, protocol)

    def configure(
        self,
        base_url: str,
        api_key: str = "",
        protocol: str = DEFAULT_PROTOCOL,
    ) -> None:
        self.api_key = api_key.strip()
        self.protocol = normalize_protocol(protocol)
        suffix = {
            "openai-chat": "/chat/completions",
            "openai-responses": "/responses",
            "anthropic": "/messages",
        }[self.protocol]
        self.endpoint = f"{base_url.rstrip('/')}{suffix}" if base_url.strip() else ""

    def configure_profile(self, profile: Mapping[str, Any]) -> None:
        self.configure(
            str(profile.get("baseUrl", "") or ""),
            str(profile.get("apiKey", "") or ""),
            str(profile.get("protocol", "") or ""),
        )
        self.proxy = str(profile.get("proxy", "") or "").strip()
        self.streaming = bool(profile.get("streaming", True))
        self.max_tokens = int(profile.get("maxTokens", 0) or 0)
        self.max_tokens_field = str(profile.get("maxTokensField", "") or "")
        self.thinking_type = str(profile.get("thinkingType", "") or "")
        self.reasoning_effort = str(profile.get("reasoningEffort", "") or "")
        self.session_header_key = str(profile.get("sessionHeaderKey", "") or "")

    def enabled(self) -> bool:
        return bool(self.endpoint)

    def complete(
        self,
        model: str,
        messages: list[Message],
        tools: list[Tool] | None = None,
        *,
        tool_choice: str | None = None,
        session_id: str = "",
    ) -> Message:
        if not self.enabled():
            raise RuntimeError("model endpoint is not configured")
        selected_tools = tools or []
        if self.protocol == "openai-chat":
            payload = _chat_payload(
                model,
                messages,
                selected_tools,
                tool_choice=tool_choice,
                max_tokens=self.max_tokens,
                max_tokens_field=self.max_tokens_field,
                thinking_type=self.thinking_type,
                reasoning_effort=self.reasoning_effort,
            )
        elif self.protocol == "openai-responses":
            payload = _responses_payload(
                model,
                messages,
                selected_tools,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )
        else:
            payload = _anthropic_payload(
                model,
                messages,
                selected_tools,
                max_tokens=self.max_tokens,
                thinking_type=self.thinking_type,
                reasoning_effort=self.reasoning_effort,
            )
        request = Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        _add_auth_headers(request, self.protocol, self.api_key)
        if self.session_header_key and session_id:
            request.add_header(
                self.session_header_key,
                session_id,
            )
        try:
            with _open(request, timeout=self.timeout, proxy=self.proxy) as response:  # noqa: S310 - endpoint is operator configuration.
                status = int(response.status)
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = int(error.code)
            data = error.read(MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, socket.timeout) as exc:
            raise ModelCallError(
                f"call model timed out: {exc}", timed_out=True
            ) from exc
        except (URLError, OSError) as exc:
            raise ModelCallError(f"call model: {exc}") from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise RuntimeError("model response is too large")
        if status < 200 or status >= 300:
            raise ModelCallError(
                f"model returned status {status}",
                status=status,
                retryable=status in {408, 429} or status >= 500,
            )
        try:
            result = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelCallError(f"decode model response: {exc}") from exc
        if not isinstance(result, Mapping):
            raise ModelCallError("model response is invalid")
        if self.protocol == "openai-chat":
            return _parse_chat_response(result)
        if self.protocol == "openai-responses":
            return _parse_responses_response(result)
        return _parse_anthropic_response(result)


def normalize_protocol(value: str) -> str:
    protocol = value.strip().lower()
    aliases = {
        "": DEFAULT_PROTOCOL,
        "openai": DEFAULT_PROTOCOL,
        "chat": "openai-chat",
        "responses": "openai-responses",
        "response": "openai-responses",
        "anthro": "anthropic",
        "anthropic-messages": "anthropic",
    }
    normalized = aliases.get(protocol, protocol)
    if normalized not in SUPPORTED_PROTOCOLS:
        raise ValueError(f"unsupported model protocol: {value}")
    return normalized


def _add_auth_headers(request: Request, protocol: str, api_key: str) -> None:
    key = api_key.strip()
    if not key:
        return
    if protocol == "anthropic":
        request.add_header("x-api-key", key)
        request.add_header("anthropic-version", ANTHROPIC_VERSION)
        return
    request.add_header("Authorization", f"Bearer {key}")


def _open(
    request: Request,
    *,
    timeout: float,
    proxy: str = "",
) -> Any:
    if not proxy:
        return urlopen(request, timeout=timeout)
    parsed = urlsplit(proxy)
    if parsed.scheme in {"http", "https"}:
        opener = build_opener(
            ProxyHandler(
                {
                    "http": proxy,
                    "https": proxy,
                }
            )
        )
        return opener.open(request, timeout=timeout)
    if parsed.scheme in {"socks5", "socks5h"}:
        try:
            import socks  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ModelCallError("当前环境缺少 SOCKS 代理支持") from exc
        host = parsed.hostname or ""
        port = parsed.port or 1080
        username = parsed.username or ""
        password = parsed.password or ""
        proxy_type = socks.SOCKS5
        sock = socks.socksocket()
        sock.set_proxy(
            proxy_type,
            host,
            port,
            rdns=parsed.scheme == "socks5h",
            username=username or None,
            password=password or None,
        )
        sock.settimeout(timeout)
        target = urlsplit(request.full_url)
        tls = target.scheme == "https"
        sock.connect(
            (target.hostname or "", target.port or (443 if tls else 80))
        )
        if tls:
            context = ssl.create_default_context()
            wrapped = context.wrap_socket(sock, server_hostname=target.hostname)
        else:
            wrapped = sock
        path = target.path or "/"
        if target.query:
            path += f"?{target.query}"
        lines = [f"{request.get_method()} {path} HTTP/1.1"]
        lines.extend(f"{key}: {value}" for key, value in request.header_items())
        lines.append("Connection: close")
        lines.append("")
        lines.append("")
        wrapped.sendall("\r\n".join(lines).encode("latin-1"))
        data = request.data
        if isinstance(data, bytes):
            wrapped.sendall(data)
        response = HTTPResponse(wrapped)
        response.begin()
        return response
    raise ModelCallError("模型代理协议无效")


def _chat_payload(
    model: str,
    messages: list[Message],
    tools: list[Tool],
    *,
    tool_choice: str | None = None,
    max_tokens: int = 0,
    max_tokens_field: str = "",
    thinking_type: str = "",
    reasoning_effort: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [message.to_dict() for message in messages],
    }
    if tools:
        payload["tools"] = [tool.to_dict() for tool in tools]
        payload["tool_choice"] = tool_choice or "auto"
    if max_tokens > 0:
        field = (
            "max_completion_tokens"
            if max_tokens_field == "max_completion_tokens"
            else "max_tokens"
        )
        payload[field] = max_tokens
    if thinking_type:
        payload["thinking"] = {"type": thinking_type}
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def _responses_payload(
    model: str,
    messages: list[Message],
    tools: list[Tool],
    *,
    max_tokens: int = 0,
    reasoning_effort: str = "",
) -> dict[str, Any]:
    instructions = "\n\n".join(
        message.content
        for message in messages
        if message.role == "system" and message.content
    )
    payload: dict[str, Any] = {
        "model": model,
        "input": _responses_input(messages),
    }
    if instructions:
        payload["instructions"] = instructions
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "name": tool.function.name,
                "description": tool.function.description,
                "parameters": dict(tool.function.parameters),
                **({"strict": True} if tool.function.strict else {}),
            }
            for tool in tools
        ]
        payload["tool_choice"] = "auto"
    if max_tokens > 0:
        payload["max_output_tokens"] = max_tokens
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload


def _responses_input(messages: list[Message]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
            continue
        if message.content:
            items.append({"role": message.role, "content": message.content})
        for call in message.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                }
            )
    return items


def _anthropic_payload(
    model: str,
    messages: list[Message],
    tools: list[Tool],
    *,
    max_tokens: int = 0,
    thinking_type: str = "",
    reasoning_effort: str = "",
) -> dict[str, Any]:
    system = "\n\n".join(
        message.content
        for message in messages
        if message.role == "system" and message.content
    )
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens or ANTHROPIC_MAX_TOKENS,
        "messages": _anthropic_messages(messages),
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = [
            {
                "name": tool.function.name,
                "description": tool.function.description,
                "input_schema": dict(tool.function.parameters),
            }
            for tool in tools
        ]
    if thinking_type in {"disabled", "enabled"}:
        payload["thinking"] = {"type": thinking_type}
    if reasoning_effort:
        payload["output_config"] = {"effort": reasoning_effort}
    return payload


def _anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            result.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_call_id,
                            "content": message.content,
                        }
                    ],
                }
            )
            continue
        content: list[dict[str, Any]] = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        if message.role == "assistant":
            for call in message.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": call.function.arguments}
                content.append(
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.function.name,
                        "input": arguments if isinstance(arguments, Mapping) else {},
                    }
                )
        role = "assistant" if message.role == "assistant" else "user"
        if content:
            result.append({"role": role, "content": content})
    return result


def _parse_chat_response(result: Mapping[str, Any]) -> Message:
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("model response has no choice")
    first = choices[0]
    if not isinstance(first, Mapping) or not isinstance(first.get("message"), Mapping):
        raise RuntimeError("model response has no choice")
    return Message.from_dict(first["message"])


def _parse_responses_response(result: Mapping[str, Any]) -> Message:
    output = result.get("output")
    if not isinstance(output, list):
        output_text = result.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return Message("assistant", output_text.strip())
        raise RuntimeError("model response has no output")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for item in output:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("type", "") or "") == "function_call":
            calls.append(
                ToolCall(
                    str(item.get("call_id", item.get("id", "")) or ""),
                    "function",
                    FunctionCall(
                        str(item.get("name", "") or ""),
                        str(item.get("arguments", "") or ""),
                    ),
                )
            )
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            if str(block.get("type", "") or "") in {"output_text", "text"}:
                text = str(block.get("text", "") or "").strip()
                if text:
                    texts.append(text)
    if not texts and not calls:
        output_text = result.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            texts.append(output_text.strip())
        else:
            raise RuntimeError("model response has no text or tool call")
    return Message("assistant", "\n".join(texts), tool_calls=tuple(calls))


def _parse_anthropic_response(result: Mapping[str, Any]) -> Message:
    content = result.get("content")
    if not isinstance(content, list):
        raise RuntimeError("model response has no content")
    texts: list[str] = []
    calls: list[ToolCall] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        block_type = str(block.get("type", "") or "")
        if block_type == "text":
            text = str(block.get("text", "") or "").strip()
            if text:
                texts.append(text)
        elif block_type == "tool_use":
            calls.append(
                ToolCall(
                    str(block.get("id", "") or ""),
                    "function",
                    FunctionCall(
                        str(block.get("name", "") or ""),
                        json.dumps(
                            block.get("input", {}),
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                )
            )
    if not texts and not calls:
        raise RuntimeError("model response has no text or tool call")
    return Message("assistant", "\n".join(texts), tool_calls=tuple(calls))
