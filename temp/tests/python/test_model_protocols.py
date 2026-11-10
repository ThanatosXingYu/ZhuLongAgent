from __future__ import annotations

import json
from typing import Any
from urllib.request import Request

import pytest

import internal.openai_client as model_module
from internal.openai_client import (
    FunctionCall,
    Message,
    OpenAIClient,
    Tool,
    ToolCall,
    ToolFunction,
    fetch_models,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def read(self, _maximum: int) -> bytes:
        return self._body


def _headers(request: Request) -> dict[str, str]:
    return {name.lower(): value for name, value in request.header_items()}


def test_openai_chat_protocol_keeps_chat_completions_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def open_request(request: Request, **_kwargs: Any) -> FakeResponse:
        captured.append(request)
        return FakeResponse(
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "chat-ok"}}
                ]
            }
        )

    monkeypatch.setattr(model_module, "urlopen", open_request)
    client = OpenAIClient(
        "https://provider.example/v1",
        "secret",
        protocol="openai-chat",
    )
    result = client.complete("demo", [Message("user", "hello")])

    payload = json.loads(captured[0].data or b"{}")
    assert client.endpoint == "https://provider.example/v1/chat/completions"
    assert payload["messages"] == [{"role": "user", "content": "hello"}]
    assert _headers(captured[0])["authorization"] == "Bearer secret"
    assert _headers(captured[0])["user-agent"] == "gcsis-tools/1.0"
    assert result.content == "chat-ok"


def test_openai_responses_protocol_translates_tools_and_function_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def open_request(request: Request, **_kwargs: Any) -> FakeResponse:
        captured.append(request)
        return FakeResponse(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "response-ok"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-1",
                        "name": "read_flag",
                        "arguments": '{"path":"flag"}',
                    },
                ]
            }
        )

    monkeypatch.setattr(model_module, "urlopen", open_request)
    tool = Tool(
        "function",
        ToolFunction(
            "read_flag",
            "Read a local flag",
            {"type": "object", "properties": {}},
        ),
    )
    client = OpenAIClient(
        "https://provider.example/v1",
        "secret",
        protocol="openai-responses",
    )
    result = client.complete(
        "demo",
        [Message("system", "rules"), Message("user", "solve")],
        [tool],
    )

    payload = json.loads(captured[0].data or b"{}")
    assert client.endpoint == "https://provider.example/v1/responses"
    assert payload["instructions"] == "rules"
    assert payload["input"] == [{"role": "user", "content": "solve"}]
    assert payload["tools"][0]["name"] == "read_flag"
    assert "function" not in payload["tools"][0]
    assert result.content == "response-ok"
    assert result.tool_calls == (
        ToolCall("call-1", "function", FunctionCall("read_flag", '{"path":"flag"}')),
    )


def test_responses_protocol_keeps_function_calls_with_output_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_request(_request: Request, **_kwargs: Any) -> FakeResponse:
        return FakeResponse(
            {
                "output_text": "working",
                "output": [
                    {
                        "type": "function_call",
                        "call_id": "call-2",
                        "name": "read_flag",
                        "arguments": '{"path":"flag"}',
                    }
                ],
            }
        )

    monkeypatch.setattr(model_module, "urlopen", open_request)
    result = OpenAIClient(
        "https://provider.example/v1",
        "secret",
        protocol="openai-responses",
    ).complete("demo", [Message("user", "solve")])

    assert result.content == ""
    assert result.tool_calls[0].id == "call-2"


def test_anthropic_protocol_uses_messages_api_and_tool_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def open_request(request: Request, **_kwargs: Any) -> FakeResponse:
        captured.append(request)
        return FakeResponse(
            {
                "content": [
                    {"type": "text", "text": "anthro-ok"},
                    {
                        "type": "tool_use",
                        "id": "tool-1",
                        "name": "read_flag",
                        "input": {"path": "flag"},
                    },
                ]
            }
        )

    monkeypatch.setattr(model_module, "urlopen", open_request)
    tool = Tool(
        "function",
        ToolFunction(
            "read_flag",
            "Read a local flag",
            {"type": "object", "properties": {}},
        ),
    )
    messages = [
        Message("system", "rules"),
        Message("user", "solve"),
        Message(
            "assistant",
            tool_calls=(
                ToolCall(
                    "tool-1",
                    "function",
                    FunctionCall("read_flag", '{"path":"flag"}'),
                ),
            ),
        ),
        Message("tool", "flag{demo}", tool_call_id="tool-1"),
    ]
    client = OpenAIClient(
        "https://api.anthropic.com/v1",
        "secret",
        protocol="anthropic",
    )
    result = client.complete("demo", messages, [tool])

    payload = json.loads(captured[0].data or b"{}")
    headers = _headers(captured[0])
    assert client.endpoint == "https://api.anthropic.com/v1/messages"
    assert payload["system"] == "rules"
    assert payload["max_tokens"] > 0
    assert payload["tools"][0]["input_schema"]["type"] == "object"
    assert payload["messages"][-1]["content"][0]["type"] == "tool_result"
    assert headers["x-api-key"] == "secret"
    assert headers["anthropic-version"]
    assert result.content == "anthro-ok"
    assert result.tool_calls[0].function.name == "read_flag"


def test_fetch_models_uses_anthropic_auth_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def open_request(request: Request, **_kwargs: Any) -> FakeResponse:
        captured.append(request)
        return FakeResponse({"data": [{"id": "claude-demo"}]})

    monkeypatch.setattr(model_module, "urlopen", open_request)

    models = fetch_models(
        "https://api.anthropic.com/v1",
        "secret",
        "anthropic",
    )

    assert models == ["claude-demo"]
    assert _headers(captured[0])["x-api-key"] == "secret"


@pytest.mark.parametrize(
    ("protocol", "response"),
    [
        (
            "openai-chat",
            {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        ),
        (
            "openai-responses",
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]},
        ),
        ("anthropic", {"content": [{"type": "text", "text": "ok"}]}),
    ],
)
def test_streaming_flag_is_forwarded_for_every_protocol(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    response: dict[str, Any],
) -> None:
    captured: list[Request] = []

    def open_request(request: Request, **_kwargs: Any) -> FakeResponse:
        captured.append(request)
        return FakeResponse(response)

    monkeypatch.setattr(model_module, "urlopen", open_request)
    OpenAIClient(
        "https://provider.example/v1",
        "secret",
        protocol=protocol,
        streaming=False,
    ).complete("demo", [Message("user", "hello")])

    payload = json.loads(captured[0].data or b"{}")
    assert payload["stream"] is False
