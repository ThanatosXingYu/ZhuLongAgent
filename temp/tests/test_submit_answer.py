from __future__ import annotations

import json
from typing import Any
from urllib.request import Request

import pytest

from scripts.submit_answer import InputError, build_parser, normalize_server_url, submit_answer


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self._status = status
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def getcode(self) -> int:
        return self._status

    def read(self) -> bytes:
        return self._body


class RecordingOpener:
    def __init__(self) -> None:
        self.request: Request | None = None
        self.timeout = 0.0

    def open(self, request: Request, timeout: float) -> FakeResponse:
        self.request = request
        self.timeout = timeout
        return FakeResponse(200, b'{"ok":true,"data":{"isCorrect":true}}')


def test_submit_answer_uses_running_local_web_api() -> None:
    opener = RecordingOpener()

    status, body = submit_answer("http://127.0.0.1:8080/", 17, "answer", opener=opener)

    assert status == 200
    assert json.loads(body)["data"]["isCorrect"] is True
    assert opener.request is not None
    assert opener.request.full_url == "http://127.0.0.1:8080/api/exercises/17/answer"
    assert json.loads(cast_bytes(opener.request.data)) == {"flag": "answer"}
    assert opener.request.get_header("Content-type") == "application/json"
    assert all("access" not in key.lower() for key in opener.request.headers)
    assert opener.timeout == 30.0


def test_server_url_validation_rejects_non_root_and_credentials() -> None:
    assert normalize_server_url(" http://127.0.0.1:8080/ ") == "http://127.0.0.1:8080"
    for value in (
        "file:///tmp/socket",
        "http://user:password@127.0.0.1:8080",
        "http://127.0.0.1:8080/api",
        "http://127.0.0.1:8080/?debug=1",
    ):
        with pytest.raises(InputError):
            normalize_server_url(value)


def test_parser_defaults_to_loopback_workbench() -> None:
    arguments = build_parser().parse_args(["17", "flag{answer}"])
    assert arguments.server == "http://127.0.0.1:8080"


def cast_bytes(value: Any) -> bytes:
    assert isinstance(value, bytes)
    return value
