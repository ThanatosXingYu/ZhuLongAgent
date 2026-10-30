"""Translate Codex Responses requests to OpenAI Chat Completions."""

from __future__ import annotations

import json
import socket
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .openai_client import (
    FunctionCall,
    Message,
    OpenAIClient,
    ModelCallError,
    Tool,
    ToolCall,
    ToolFunction,
)
from .openai_client import USER_AGENT


@dataclass(frozen=True)
class ChatBridge:
    model: str
    messages: list[Message]
    tools: list[Tool]
    tool_names: Mapping[str, tuple[str, str]]


def build_chat_bridge(body: Mapping[str, Any], default_model: str) -> ChatBridge:
    model = str(body.get("model", "") or default_model).strip() or default_model
    messages: list[Message] = []
    instructions = str(body.get("instructions", "") or "").strip()
    if instructions:
        messages.append(Message("system", instructions))
    for item in _list(body.get("input")):
        if not isinstance(item, Mapping):
            continue
        item_type = str(item.get("type", "") or "")
        if item_type == "message":
            role = str(item.get("role", "") or "user")
            role = "system" if role in {"developer", "system"} else role
            content = _message_text(item.get("content"))
            if content:
                messages.append(Message(role, content))
            continue
        if item_type == "function_call":
            name = str(item.get("name", "") or "")
            call_id = str(item.get("call_id", item.get("id", "")) or "")
            arguments = _serialize(item.get("arguments"))
            messages.append(
                Message(
                    "assistant",
                    tool_calls=(
                        ToolCall(
                            call_id,
                            "function",
                            FunctionCall(name, arguments),
                        ),
                    ),
                )
            )
            continue
        if item_type == "function_call_output":
            call_id = str(item.get("call_id", "") or "")
            messages.append(
                Message(
                    "tool",
                    _message_text(item.get("output")),
                    tool_call_id=call_id,
                )
            )

    tools: list[Tool] = []
    tool_names: dict[str, tuple[str, str]] = {}
    for item in _list(body.get("tools")):
        if not isinstance(item, Mapping):
            continue
        tool_type = str(item.get("type", "") or "")
        if tool_type == "function":
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            tools.append(_chat_tool(name, item))
            tool_names[name] = ("", name)
            continue
        if tool_type == "namespace":
            namespace = str(item.get("name", "") or "").strip()
            for child in _list(item.get("tools")):
                if not isinstance(child, Mapping):
                    continue
                child_name = str(child.get("name", "") or "").strip()
                if not namespace or not child_name:
                    continue
                chat_name = _namespace_name(namespace, child_name)
                tools.append(_chat_tool(chat_name, child))
                tool_names[chat_name] = (namespace, child_name)
    return ChatBridge(model, messages, tools, tool_names)


def complete_with_chat(
    bridge: ChatBridge,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 120.0,
) -> Message:
    client = OpenAIClient(
        base_url,
        api_key,
        timeout=timeout,
        protocol="openai-chat",
    )
    return client.complete(bridge.model, bridge.messages, bridge.tools)


def stream_chat_response(
    bridge: ChatBridge,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 100.0,
) -> Iterator[bytes]:
    payload: dict[str, Any] = {
        "model": bridge.model,
        "messages": [message.to_dict() for message in bridge.messages],
        "stream": True,
    }
    if bridge.tools:
        payload["tools"] = [tool.to_dict() for tool in bridge.tools]
        payload["tool_choice"] = "auto"
    request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": USER_AGENT,
        },
    )
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")
    response = None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = urlopen(request, timeout=timeout)  # noqa: S310
            break
        except HTTPError as exc:
            last_error = ModelCallError(
                f"model returned status {exc.code}",
                status=exc.code,
                retryable=exc.code in {408, 429} or exc.code >= 500,
            )
        except (TimeoutError, socket.timeout) as exc:
            last_error = ModelCallError(
                f"call model timed out: {exc}", timed_out=True
            )
        except (URLError, OSError) as exc:
            last_error = ModelCallError(f"call model: {exc}")
        if attempt >= 2 or not isinstance(last_error, ModelCallError) or not last_error.retryable:
            break
        time.sleep(1.0 if attempt == 0 else 3.0)
    if response is None:
        yield _failed_response_event(str(last_error or "model request failed"))
        return

    response_id = f"resp_{uuid.uuid4().hex}"
    created_at = int(time.time())
    yield _sse_event(
        {
            "type": "response.created",
            "response": _response_base(
                response_id, created_at, bridge.model, status="in_progress"
            ),
        }
    )
    yield _sse_event(
        {
            "type": "response.in_progress",
            "response": _response_base(
                response_id, created_at, bridge.model, status="in_progress"
            ),
        }
    )
    text_item_id = f"msg_{uuid.uuid4().hex}"
    text_started = False
    text_parts: list[str] = []
    output_items: list[dict[str, Any]] = []
    output_index = 0
    tool_calls: dict[int, dict[str, str]] = {}
    usage: dict[str, Any] | None = None
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, Mapping) and isinstance(chunk.get("usage"), Mapping):
                usage = dict(chunk["usage"])
            choices = chunk.get("choices") if isinstance(chunk, Mapping) else None
            if not isinstance(choices, list) or not choices:
                continue
            first = choices[0]
            if not isinstance(first, Mapping):
                continue
            delta = first.get("delta")
            if not isinstance(delta, Mapping):
                continue
            if delta.get("reasoning_content"):
                yield b": ping\n\n"
            content = delta.get("content")
            if isinstance(content, str) and content:
                if not text_started:
                    text_started = True
                    yield _sse_event(
                        {
                            "type": "response.output_item.added",
                            "output_index": output_index,
                            "item": {
                                "id": text_item_id,
                                "type": "message",
                                "status": "in_progress",
                                "role": "assistant",
                                "content": [],
                            },
                        }
                    )
                    yield _sse_event(
                        {
                            "type": "response.content_part.added",
                            "item_id": text_item_id,
                            "output_index": output_index,
                            "content_index": 0,
                            "part": {
                                "type": "output_text",
                                "text": "",
                                "annotations": [],
                            },
                        }
                    )
                text_parts.append(content)
                yield _sse_event(
                    {
                        "type": "response.output_text.delta",
                        "item_id": text_item_id,
                        "output_index": output_index,
                        "content_index": 0,
                        "delta": content,
                    }
                )
            raw_tool_calls = delta.get("tool_calls")
            if isinstance(raw_tool_calls, list):
                for raw_call in raw_tool_calls:
                    if not isinstance(raw_call, Mapping):
                        continue
                    index = int(raw_call.get("index", 0) or 0)
                    state = tool_calls.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    call_id = str(raw_call.get("id", "") or "")
                    if call_id:
                        state["id"] = call_id
                    function = raw_call.get("function")
                    if isinstance(function, Mapping):
                        state["name"] += str(function.get("name", "") or "")
                        state["arguments"] += str(
                            function.get("arguments", "") or ""
                        )

    if text_started:
        text = "".join(text_parts)
        text_item = {
            "id": text_item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": text, "annotations": []}
            ],
        }
        yield _sse_event(
            {
                "type": "response.output_text.done",
                "item_id": text_item_id,
                "output_index": output_index,
                "content_index": 0,
                "text": text,
            }
        )
        yield _sse_event(
            {
                "type": "response.content_part.done",
                "item_id": text_item_id,
                "output_index": output_index,
                "content_index": 0,
                "part": {
                    "type": "output_text",
                    "text": text,
                    "annotations": [],
                },
            }
        )
        yield _sse_event(
            {
                "type": "response.output_item.done",
                "output_index": output_index,
                "item": text_item,
            }
        )
        output_items.append(text_item)
        output_index += 1

    for _, call in sorted(tool_calls.items()):
        namespace, name = bridge.tool_names.get(call["name"], ("", call["name"]))
        item = {
            "id": f"fc_{uuid.uuid4().hex}",
            "type": "function_call",
            "status": "completed",
            "call_id": call["id"] or f"call_{uuid.uuid4().hex}",
            "name": name,
            "arguments": call["arguments"],
        }
        if namespace:
            item["namespace"] = namespace
        yield _sse_event(
            {
                "type": "response.output_item.added",
                "output_index": output_index,
                "item": {
                    **item,
                    "status": "in_progress",
                    "arguments": "",
                },
            }
        )
        yield _sse_event(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": item["id"],
                "output_index": output_index,
                "delta": call["arguments"],
            }
        )
        yield _sse_event(
            {
                "type": "response.function_call_arguments.done",
                "item_id": item["id"],
                "output_index": output_index,
                "arguments": call["arguments"],
            }
        )
        yield _sse_event(
            {
                "type": "response.output_item.done",
                "output_index": output_index,
                "item": item,
            }
        )
        output_items.append(item)
        output_index += 1

    completed = _response_base(
        response_id, created_at, bridge.model, status="completed"
    )
    completed["output"] = output_items
    completed["usage"] = _normalized_usage(usage)
    yield _sse_event({"type": "response.completed", "response": completed})


def build_response_sse(
    message: Message,
    *,
    model: str,
    tool_names: Mapping[str, tuple[str, str]],
) -> bytes:
    response_id = f"resp_{uuid.uuid4().hex}"
    created_at = int(time.time())
    output: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = [
        {
            "type": "response.created",
            "response": _response_base(
                response_id, created_at, model, status="in_progress"
            ),
        },
        {
            "type": "response.in_progress",
            "response": _response_base(
                response_id, created_at, model, status="in_progress"
            ),
        },
    ]
    output_index = 0
    if message.content:
        item_id = f"msg_{uuid.uuid4().hex}"
        part = {"type": "output_text", "text": message.content, "annotations": []}
        text_item = {
            "id": item_id,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [part],
        }
        events.extend(
            [
                {
                    "type": "response.output_item.added",
                    "output_index": output_index,
                    "item": {**text_item, "status": "in_progress", "content": []},
                },
                {
                    "type": "response.content_part.added",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "delta": message.content,
                },
                {
                    "type": "response.output_text.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "text": message.content,
                },
                {
                    "type": "response.content_part.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "part": part,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": output_index,
                    "item": text_item,
                },
            ]
        )
        output.append(text_item)
        output_index += 1

    for tool_call in message.tool_calls:
        namespace, name = tool_names.get(
            tool_call.function.name, ("", tool_call.function.name)
        )
        call_id = tool_call.id or f"call_{uuid.uuid4().hex}"
        item_id = f"fc_{uuid.uuid4().hex}"
        function_item: dict[str, Any] = {
            "id": item_id,
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "arguments": tool_call.function.arguments,
        }
        if namespace:
            function_item["namespace"] = namespace
        events.extend(
            [
                {
                    "type": "response.output_item.added",
                    "output_index": output_index,
                    "item": {
                        **function_item,
                        "status": "in_progress",
                        "arguments": "",
                    },
                },
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": item_id,
                    "output_index": output_index,
                    "delta": tool_call.function.arguments,
                },
                {
                    "type": "response.function_call_arguments.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "arguments": tool_call.function.arguments,
                },
                {
                    "type": "response.output_item.done",
                    "output_index": output_index,
                    "item": function_item,
                },
            ]
        )
        output.append(function_item)
        output_index += 1

    completed = _response_base(response_id, created_at, model, status="completed")
    completed["output"] = output
    completed["usage"] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }
    events.append({"type": "response.completed", "response": completed})
    return "".join(
        f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
        for event in events
    ).encode("utf-8")


def _sse_event(event: Mapping[str, Any]) -> bytes:
    return (
        "data: "
        + json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        + "\n\n"
    ).encode("utf-8")


def _failed_response_event(message: str) -> bytes:
    return _sse_event(
        {
            "type": "response.failed",
            "response": {
                "id": f"resp_{uuid.uuid4().hex}",
                "object": "response",
                "status": "failed",
                "error": {"code": "upstream_error", "message": message},
            },
        }
    )


def _normalized_usage(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = value or {}
    input_tokens = int(source.get("prompt_tokens", source.get("input_tokens", 0)) or 0)
    output_tokens = int(
        source.get("completion_tokens", source.get("output_tokens", 0)) or 0
    )
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(
            source.get("total_tokens", input_tokens + output_tokens) or 0
        ),
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }


def _response_base(
    response_id: str, created_at: int, model: str, *, status: str
) -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "status": status,
        "model": model,
        "output": [],
    }


def _chat_tool(name: str, item: Mapping[str, Any]) -> Tool:
    parameters = item.get("parameters")
    return Tool(
        "function",
        ToolFunction(
            name,
            str(item.get("description", "") or ""),
            parameters if isinstance(parameters, Mapping) else {},
            bool(item.get("strict", False)),
        ),
    )


def _namespace_name(namespace: str, name: str) -> str:
    return f"{namespace}__{name}".replace("-", "_")[:64]


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            str(part.get("text", "") or "")
            for part in value
            if isinstance(part, Mapping)
            and str(part.get("type", "") or "") in {"input_text", "output_text", "text"}
        )
    return ""


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {}, ensure_ascii=False, separators=(",", ":"))


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
