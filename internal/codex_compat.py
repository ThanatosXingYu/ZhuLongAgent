"""Translate Codex Responses requests to OpenAI Chat Completions."""

from __future__ import annotations

import json
import logging
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

LOGGER = logging.getLogger(__name__)

RESPONDER_TOOL = "__gcsis_respond"
DEFAULT_EXEC_TOOL: dict[str, Any] = {
    "type": "namespace",
    "name": "functions",
    "description": "",
    "tools": [
        {
            "type": "custom",
            "name": "exec",
            "description": (
                "Run JavaScript in the agent orchestrator. Use the global tools "
                "object to invoke nested tools, for example "
                "`await tools.exec_command({cmd: \"pwd\"})`. Use this tool for all "
                "real work. Do not merely describe future actions."
            ),
            "format": {"type": "text"},
        }
    ],
}
TOOL_USE_INSTRUCTION = (
    "You are executing inside an autonomous agent loop. On every model turn, "
    f"call exactly one tool. Use {RESPONDER_TOOL} with the complete final answer "
    "only after at least one real work tool has returned and the entire task is "
    "actually complete. If work remains, call the appropriate work tool "
    "immediately. Never return a future-action plan as ordinary assistant text."
)


@dataclass(frozen=True)
class ChatBridge:
    model: str
    messages: list[Message]
    tools: list[Tool]
    tool_names: Mapping[str, tuple[str, str]]
    custom_tools: frozenset[str]


def build_chat_bridge(body: Mapping[str, Any], default_model: str) -> ChatBridge:
    model = str(body.get("model", "") or default_model).strip() or default_model
    tools: list[Tool] = []
    tool_names: dict[str, tuple[str, str]] = {}
    custom_tools: set[str] = set()
    raw_tools = _list(body.get("tools")) or [DEFAULT_EXEC_TOOL]
    for item in raw_tools:
        if not isinstance(item, Mapping):
            continue
        tool_type = str(item.get("type", "") or "")
        if tool_type in {"function", "custom"}:
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            tools.append(_chat_tool(name, item, custom=tool_type == "custom"))
            tool_names[name] = ("", name)
            if tool_type == "custom":
                custom_tools.add(name)
            continue
        if tool_type == "namespace":
            namespace = str(item.get("name", "") or "").strip()
            for child in _list(item.get("tools")):
                if not isinstance(child, Mapping):
                    continue
                child_type = str(child.get("type", "") or "")
                if child_type not in {"function", "custom"}:
                    continue
                child_name = str(child.get("name", "") or "").strip()
                if not namespace or not child_name:
                    continue
                chat_name = _namespace_name(namespace, child_name)
                tools.append(
                    _chat_tool(
                        chat_name,
                        child,
                        custom=child_type == "custom",
                    )
                )
                tool_names[chat_name] = (namespace, child_name)
                if child_type == "custom":
                    custom_tools.add(chat_name)

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
            namespace = str(item.get("namespace", "") or "").strip()
            if namespace:
                name = _namespace_name(namespace, name)
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
        if item_type == "custom_tool_call":
            name = str(item.get("name", "") or "")
            namespace = str(item.get("namespace", "") or "").strip()
            if namespace:
                name = _namespace_name(namespace, name)
            call_id = str(item.get("call_id", item.get("id", "")) or "")
            arguments = json.dumps(
                {"input": str(item.get("input", "") or "")},
                ensure_ascii=False,
                separators=(",", ":"),
            )
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
            continue
        if item_type == "custom_tool_call_output":
            call_id = str(item.get("call_id", "") or "")
            messages.append(
                Message(
                    "tool",
                    _message_text(item.get("output")),
                    tool_call_id=call_id,
                )
            )
    if tools:
        has_tool_output = any(message.role == "tool" for message in messages)
        if not has_tool_output:
            custom_work_tools = [
                tool for tool in tools if tool.function.name in custom_tools
            ]
            if custom_work_tools:
                tools = custom_work_tools
        else:
            tools.append(_responder_tool())
        insert_at = 0
        while insert_at < len(messages) and messages[insert_at].role == "system":
            insert_at += 1
        messages.insert(insert_at, Message("system", TOOL_USE_INSTRUCTION))
    LOGGER.info(
        "Codex chat bridge prepared tools=%s custom=%s messages=%d",
        [tool.function.name for tool in tools],
        sorted(custom_tools),
        len(messages),
    )
    return ChatBridge(model, messages, tools, tool_names, frozenset(custom_tools))


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
    return client.complete(
        bridge.model,
        bridge.messages,
        bridge.tools,
        tool_choice="required",
    )


def stream_chat_response(
    bridge: ChatBridge,
    *,
    base_url: str,
    api_key: str,
    timeout: float = 300.0,
) -> Iterator[bytes]:
    payload: dict[str, Any] = {
        "model": bridge.model,
        "messages": [message.to_dict() for message in bridge.messages],
        "stream": True,
    }
    if bridge.tools:
        payload["tools"] = [tool.to_dict() for tool in bridge.tools]
        payload["tool_choice"] = "required"
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
    try:
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
    except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as exc:
        yield _failed_response_event(f"model stream interrupted: {exc}")
        return

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

    responder = next(
        (call for call in tool_calls.values() if call["name"] == RESPONDER_TOOL),
        None,
    )
    LOGGER.info(
        "Codex chat upstream completed tool_calls=%s text=%s",
        [call["name"] for call in tool_calls.values()],
        bool(text_parts),
    )
    if responder is not None:
        final_text = _responder_text(responder["arguments"])
        if final_text:
            if text_started:
                # The responder is authoritative; replace any incidental text.
                for index, item in enumerate(output_items):
                    if item.get("type") == "message":
                        output_items.pop(index)
                        output_index -= 1
                        break
            text_item_id = f"msg_{uuid.uuid4().hex}"
            text_item = {
                "id": text_item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": final_text, "annotations": []}
                ],
            }
            yield _sse_event(
                {
                    "type": "response.output_item.added",
                    "output_index": output_index,
                    "item": {**text_item, "status": "in_progress", "content": []},
                }
            )
            yield _sse_event(
                {
                    "type": "response.output_text.done",
                    "item_id": text_item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "text": final_text,
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
        tool_calls = {
            index: call
            for index, call in tool_calls.items()
            if call["name"] != RESPONDER_TOOL
        }

    for _, call in sorted(tool_calls.items()):
        resolved_name = _resolve_tool_name(
            call["name"],
            bridge.tool_names,
            bridge.custom_tools,
        )
        namespace, name = bridge.tool_names.get(
            resolved_name, ("", call["name"])
        )
        if resolved_name in bridge.custom_tools:
            input_value = _custom_tool_input(call["arguments"])
            item = {
                "id": f"ctc_{uuid.uuid4().hex}",
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": call["id"] or f"call_{uuid.uuid4().hex}",
                "name": name,
                "input": input_value,
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
                        "input": "",
                    },
                }
            )
            yield _sse_event(
                {
                    "type": "response.custom_tool_call_input.delta",
                    "item_id": item["id"],
                    "output_index": output_index,
                    "delta": input_value,
                }
            )
            yield _sse_event(
                {
                    "type": "response.custom_tool_call_input.done",
                    "item_id": item["id"],
                    "output_index": output_index,
                    "input": input_value,
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
            continue
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
    custom_tools: frozenset[str] = frozenset(),
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
    response_text = message.content
    responder = next(
        (
            call
            for call in message.tool_calls
            if call.function.name == RESPONDER_TOOL
        ),
        None,
    )
    if responder is not None:
        response_text = _responder_text(responder.function.arguments)
    output_index = 0
    if response_text:
        item_id = f"msg_{uuid.uuid4().hex}"
        part = {"type": "output_text", "text": response_text, "annotations": []}
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
                    "delta": response_text,
                },
                {
                    "type": "response.output_text.done",
                    "item_id": item_id,
                    "output_index": output_index,
                    "content_index": 0,
                    "text": response_text,
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
        if tool_call.function.name == RESPONDER_TOOL:
            continue
        resolved_name = _resolve_tool_name(
            tool_call.function.name,
            tool_names,
            custom_tools,
        )
        namespace, name = tool_names.get(
            resolved_name, ("", tool_call.function.name)
        )
        call_id = tool_call.id or f"call_{uuid.uuid4().hex}"
        if resolved_name in custom_tools:
            input_value = _custom_tool_input(tool_call.function.arguments)
            item_id = f"ctc_{uuid.uuid4().hex}"
            custom_item: dict[str, Any] = {
                "id": item_id,
                "type": "custom_tool_call",
                "status": "completed",
                "call_id": call_id,
                "name": name,
                "input": input_value,
            }
            if namespace:
                custom_item["namespace"] = namespace
            events.extend(
                [
                    {
                        "type": "response.output_item.added",
                        "output_index": output_index,
                        "item": {
                            **custom_item,
                            "status": "in_progress",
                            "input": "",
                        },
                    },
                    {
                        "type": "response.custom_tool_call_input.delta",
                        "item_id": item_id,
                        "output_index": output_index,
                        "delta": input_value,
                    },
                    {
                        "type": "response.custom_tool_call_input.done",
                        "item_id": item_id,
                        "output_index": output_index,
                        "input": input_value,
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": output_index,
                        "item": custom_item,
                    },
                ]
            )
            output.append(custom_item)
            output_index += 1
            continue
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


def _chat_tool(name: str, item: Mapping[str, Any], *, custom: bool = False) -> Tool:
    parameters = item.get("parameters")
    if custom and not isinstance(parameters, Mapping):
        parameters = {
            "type": "object",
            "properties": {
                "input": {
                    "type": "string",
                    "description": "Complete raw input for this custom tool.",
                }
            },
            "required": ["input"],
            "additionalProperties": False,
        }
    return Tool(
        "function",
        ToolFunction(
            name,
            str(item.get("description", "") or ""),
            parameters if isinstance(parameters, Mapping) else {},
            bool(item.get("strict", False)),
        ),
    )


def _responder_tool() -> Tool:
    return Tool(
        "function",
        ToolFunction(
            RESPONDER_TOOL,
            (
                "Return the complete final answer to the user. Use this only "
                "when no additional tool calls are required."
            ),
            {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Complete final response for the user.",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
            False,
        ),
    )


def _responder_text(arguments: Any) -> str:
    raw = _serialize(arguments)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, Mapping):
        message = parsed.get("message")
        if isinstance(message, str):
            return message
    return raw


def _resolve_tool_name(
    name: str,
    tool_names: Mapping[str, tuple[str, str]],
    custom_tools: frozenset[str],
) -> str:
    if name in tool_names:
        return name
    short_matches = [
        chat_name
        for chat_name in custom_tools
        if chat_name.rsplit("__", 1)[-1] == name
    ]
    return short_matches[0] if len(short_matches) == 1 else name


def _custom_tool_input(arguments: Any) -> str:
    raw = _serialize(arguments)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(parsed, Mapping):
        value = parsed.get("input")
        if isinstance(value, str):
            return value
    return raw


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
