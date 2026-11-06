from __future__ import annotations

import json
from typing import Any

import pytest

import internal.codex_compat as compat_module
from internal.codex_compat import (
    build_chat_bridge,
    build_response_sse,
    stream_chat_response,
)
from internal.openai_client import FunctionCall, Message, ToolCall


def test_codex_bridge_converts_responses_messages_and_tools() -> None:
    bridge = build_chat_bridge(
        {
            "model": "deepseek-v4.1-flash",
            "instructions": "system rules",
            "input": [
                {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "developer rules"}],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "solve"}],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "description": "run",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        },
        "fallback",
    )

    assert bridge.model == "deepseek-v4.1-flash"
    assert [message.role for message in bridge.messages] == [
        "system",
        "system",
        "system",
        "user",
    ]
    assert bridge.messages[-1].content == "solve"
    assert bridge.tools[0].function.name == "exec_command"


def test_codex_bridge_emits_text_and_function_call_sse() -> None:
    stream = build_response_sse(
        Message(
            "assistant",
            "working",
            tool_calls=(
                ToolCall(
                    "call-1",
                    "function",
                    FunctionCall("exec_command", '{"cmd":"printf ok"}'),
                ),
            ),
        ),
        model="deepseek-v4.1-flash",
        tool_names={"exec_command": ("", "exec_command")},
    ).decode("utf-8")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.splitlines()
        if line.startswith("data: ")
    ]
    event_types = [event["type"] for event in events]

    assert "response.output_text.delta" in event_types
    assert "response.function_call_arguments.done" in event_types
    assert event_types[-1] == "response.completed"
    completed = events[-1]["response"]
    assert [item["type"] for item in completed["output"]] == [
        "message",
        "function_call",
    ]
    assert completed["output"][1]["name"] == "exec_command"


def test_codex_bridge_streams_chat_deltas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def __iter__(self):
            chunks = [
                {
                    "choices": [
                        {"delta": {"reasoning_content": "thinking"}}
                    ]
                },
                {"choices": [{"delta": {"content": "STREAM"}}]},
                {"choices": [{"delta": {"content": "_OK"}}]},
            ]
            for chunk in chunks:
                yield (
                    "data: "
                    + json.dumps(chunk, separators=(",", ":"))
                    + "\n\n"
                ).encode()
            yield b"data: [DONE]\n\n"

    monkeypatch.setattr(compat_module, "urlopen", lambda *_args, **_kwargs: Response())
    bridge = build_chat_bridge(
        {
            "model": "deepseek-v4.1-flash",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
        },
        "fallback",
    )
    stream = b"".join(
        stream_chat_response(
            bridge,
            base_url="https://provider.example/v1",
            api_key="secret",
        )
    ).decode()
    assert stream.startswith(": codex-proxy-connected")
    events = [
        json.loads(line.removeprefix("data: "))
        for line in stream.splitlines()
        if line.startswith("data: ")
    ]
    deltas = [
        event["delta"]
        for event in events
        if event["type"] == "response.output_text.delta"
    ]

    assert deltas == ["STREAM", "_OK"]
    assert events[-1]["type"] == "response.completed"


def test_codex_stream_falls_back_to_next_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def __iter__(self):
            yield (
                'data: {"choices":[{"delta":{"content":"fallback ok"}}]}\n\n'
            ).encode()
            yield b"data: [DONE]\n\n"

    def fake_urlopen(request: Any, **_kwargs: Any) -> Response:
        calls.append(request.full_url)
        if len(calls) == 1:
            raise OSError("TLS handshake timeout")
        return Response()

    monkeypatch.setattr(compat_module, "urlopen", fake_urlopen)
    bridge = build_chat_bridge(
        {
            "model": "deepseek-v4.1-flash",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "hello"}],
                }
            ],
        },
        "fallback",
    )

    stream = b"".join(
        stream_chat_response(
            bridge,
            base_url="https://primary.example/v1",
            api_key="primary",
            fallback_upstreams=(("https://fallback.example/v1", "fallback"),),
        )
    ).decode()

    assert len(calls) == 2
    assert "fallback.example" in calls[1]
    assert "fallback ok" in stream
