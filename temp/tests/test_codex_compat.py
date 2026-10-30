from __future__ import annotations

import json

from internal.codex_compat import build_chat_bridge, build_response_sse
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
    assert [message.role for message in bridge.messages] == ["system", "system", "user"]
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
