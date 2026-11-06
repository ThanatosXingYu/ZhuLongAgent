#!/usr/bin/env python3
"""Submit one answer through the running local workbench API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, BinaryIO, Callable, TextIO
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

DEFAULT_SERVER_URL = "http://127.0.0.1:8080"
USER_AGENT = "gcsis-tools-python/1.0"


class InputError(ValueError):
    pass


class SubmissionError(RuntimeError):
    pass


def normalize_flag(raw_flag: str) -> str:
    opening_brace = raw_flag.find("{")
    if opening_brace == -1 or not raw_flag.endswith("}"):
        return raw_flag
    return raw_flag[opening_brace + 1 : -1]


def normalize_server_url(raw_url: str) -> str:
    value = raw_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise InputError("server URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InputError("server URL must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise InputError("server URL must not include credentials, a query, or a fragment")
    if port is not None and not 1 <= port <= 65535:
        raise InputError("server URL port is invalid")
    if parsed.path not in {"", "/"}:
        raise InputError("server URL must point to the workbench root")
    return value


def resolve_inputs(
    positional_exercise_id: str | None,
    positional_flag: str | None,
    named_exercise_id: str | None,
    named_flag: str | None,
    prompt: Callable[[str], str] = input,
) -> tuple[int, str]:
    if positional_exercise_id is not None and named_exercise_id is not None:
        raise InputError("exerciseId was provided more than once")
    if positional_flag is not None and named_flag is not None:
        raise InputError("flag was provided more than once")
    raw_exercise_id = named_exercise_id if named_exercise_id is not None else positional_exercise_id
    raw_exercise_id = raw_exercise_id if raw_exercise_id is not None else prompt("exerciseId: ")
    raw_flag = named_flag if named_flag is not None else positional_flag
    raw_flag = raw_flag if raw_flag is not None else prompt("flag: ")
    try:
        exercise_id = int(raw_exercise_id.strip())
    except (AttributeError, ValueError) as exc:
        raise InputError("exerciseId must be a positive integer") from exc
    if exercise_id <= 0:
        raise InputError("exerciseId must be a positive integer")
    flag = normalize_flag(raw_flag)
    if not flag:
        raise InputError("flag must be non-empty")
    if len(flag) > 256:
        raise InputError("flag must be at most 256 characters")
    return exercise_id, flag


def submit_answer(
    server_url: str,
    exercise_id: int,
    flag: str,
    timeout: float = 30.0,
    opener: Any = None,
) -> tuple[int, bytes]:
    url = f"{normalize_server_url(server_url)}/api/exercises/{exercise_id}/answer"
    body = json.dumps({"flag": flag}, ensure_ascii=False, separators=(",", ":")).encode()
    request = Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    client = opener or build_opener()
    try:
        with client.open(request, timeout=timeout) as response:
            return response.getcode(), response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise SubmissionError(f"Answer request failed: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Submit one exercise answer")
    parser.add_argument("exercise_id", nargs="?", help="exercise ID")
    parser.add_argument("answer_flag", nargs="?", help="flag value")
    parser.add_argument("--exercise-id", dest="named_exercise_id", help="exercise ID")
    parser.add_argument("--flag", dest="named_flag", help="flag value")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL, help="local workbench URL")
    return parser


def run(
    argv: list[str] | None = None,
    prompt: Callable[[str], str] = input,
    stdout: BinaryIO | None = None,
    stderr: TextIO = sys.stderr,
) -> int:
    if stdout is None:
        stdout = sys.stdout.buffer
    arguments = build_parser().parse_args(argv)
    try:
        exercise_id, flag = resolve_inputs(arguments.exercise_id, arguments.answer_flag, arguments.named_exercise_id, arguments.named_flag, prompt)
        server_url = normalize_server_url(arguments.server)
    except (InputError, EOFError) as exc:
        print(f"Input/configuration error: {exc}", file=stderr)
        return 2
    try:
        status, response_body = submit_answer(server_url, exercise_id, flag)
    except SubmissionError as exc:
        print(exc, file=stderr)
        return 1
    stdout.write(response_body)
    stdout.flush()
    return 0 if 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(run())
