"""Local Codex CLI process runner and bounded background task manager."""

from __future__ import annotations

import builtins
import json
import logging
import os
import queue
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, cast

from .config import DEFAULT_CODEX_MAX_CONCURRENCY, DEFAULT_CODEX_SYSTEM_PROMPT
from .events import EventPublisher
from .solver import PromptResult, safe_writeup_segment

MAX_TASK_EVENTS = 128
DEFAULT_EVENT_PAGE_SIZE = 64
MAX_EVENT_PAGE_SIZE = 256
MAX_TASK_HISTORY = 100
CONTINUE_MESSAGE = "刚才程序意外退出了，请你仔细思考，继续未完成的操作"
API_KEY_ENVIRONMENT = "GCSIS_CODEX_API_KEY"
CODEX_HOME_ENVIRONMENT = "CODEX_HOME"
LOGGER = logging.getLogger("gcsis-tools.codex")


class PromptSourceProtocol(Protocol):
    def prompt(self, exercise_id: int) -> PromptResult: ...


class PurePromptSourceProtocol(PromptSourceProtocol, Protocol):
    def prompt_pure(self, exercise_id: int) -> PromptResult: ...


class CodexError(RuntimeError):
    pass


class CodexUnavailable(CodexError):
    pass


class CodexOutputMissing(CodexError):
    pass


class PromptError(CodexError):
    pass


class TaskNotFound(CodexError):
    pass


ErrUnavailable = CodexUnavailable
ErrOutputMissing = CodexOutputMissing
ErrPrompt = PromptError
ErrTaskNotFound = TaskNotFound


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
            result["startedAt"] = _time_wire(self.started_at)
        if self.finished_at is not None:
            result["finishedAt"] = _time_wire(self.finished_at)
        if self.duration_seconds is not None:
            result["durationSeconds"] = max(0.0, self.duration_seconds)
        if self.repeat_count > 1:
            result["repeatCount"] = self.repeat_count
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result


class ProcessRunner:
    def __init__(self, binary: str = "") -> None:
        self.binary = binary

    def run(
        self,
        config: ProcessConfig,
        prompt: str,
        on_event: Callable[[Event], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProcessResult:
        binary = self.binary or config.binary
        if not binary:
            raise CodexUnavailable("codex is unavailable")
        callback = on_event or (lambda _event: None)
        command = build_command(config, prompt, binary)
        codex_home = _prepare_codex_home(config)
        env = _process_environment(config.api_key, codex_home)
        try:
            process = subprocess.Popen(
                command,
                cwd=config.workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise CodexError(f"start Codex: {exc}") from exc

        redactor = Redactor(config.api_key)
        session_id = ""
        callback(
            Event(
                datetime.now(timezone.utc),
                "process.started",
                f"Codex 子进程已启动（PID {process.pid}）",
                status="running",
                metadata={"pid": process.pid},
            )
        )

        def scan(stream: Any, kind_override: str = "") -> None:
            nonlocal session_id
            try:
                for line in iter(stream.readline, ""):
                    if not line.strip():
                        continue
                    event = parse_event_line(line, datetime.now(timezone.utc), redactor)
                    if kind_override == "stderr":
                        kind = _stderr_event_kind(event.summary)
                        event = replace(
                            event,
                            kind=kind,
                            status=event.status
                            or ("warning" if kind == "warning" else "failed"),
                        )
                    elif kind_override:
                        event = replace(event, kind=kind_override)
                    if event.session_id:
                        session_id = event.session_id
                    callback(event)
            except (OSError, ValueError) as exc:
                callback(
                    Event(
                        datetime.now(timezone.utc),
                        "stream_error",
                        redactor.apply(str(exc)),
                    )
                )

        stdout_thread = threading.Thread(
            target=scan, args=(process.stdout,), daemon=True
        )
        stderr_thread = threading.Thread(
            target=scan, args=(process.stderr, "stderr"), daemon=True
        )
        stdout_thread.start()
        stderr_thread.start()
        if process.stdin is not None:
            try:
                process.stdin.write(prompt)
                process.stdin.close()
            except OSError:
                pass

        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                break
            time.sleep(0.05)
        exit_code = process.wait()
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        callback(
            Event(
                datetime.now(timezone.utc),
                "process.completed",
                f"Codex 子进程已退出（退出码 {exit_code}）",
                status="completed" if exit_code == 0 else "failed",
                exit_code=exit_code,
                metadata={"pid": process.pid},
            )
        )
        if cancel_event is not None and cancel_event.is_set():
            return ProcessResult(exit_code, session_id=session_id, error="canceled")
        if exit_code != 0:
            return ProcessResult(
                exit_code,
                session_id=session_id,
                error=f"Codex exited with status {exit_code}",
            )
        try:
            output = Path(config.output_path).read_text(encoding="utf-8").strip()
        except OSError:
            return ProcessResult(
                exit_code, session_id=session_id, error="codex final output is missing"
            )
        output = redactor.apply(output)
        if not output:
            return ProcessResult(
                exit_code, session_id=session_id, error="codex final output is missing"
            )
        return ProcessResult(exit_code, output, session_id)


def build_command(
    config: ProcessConfig, prompt: str = "", binary: str | None = None
) -> list[str]:
    del prompt
    selected_binary = binary or config.binary

    def quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    command = [selected_binary, "exec"]
    resumed = bool(config.resume_session_id)
    if config.resume_session_id:
        command.extend(
            ["fork" if config.fork_session else "resume", config.resume_session_id]
        )
    command.extend(["--json", "--ignore-user-config"])
    if not resumed:
        command.extend(
            [
                "--sandbox",
                "danger-full-access",
                "--skip-git-repo-check",
                "--cd",
                config.workspace,
            ]
        )
    else:
        command.extend(["-c", 'sandbox_mode="danger-full-access"'])
    if config.model:
        command.extend(["--model", config.model])
    command.extend(
        [
            "-c",
            "model_provider=gcsis-codex",
            "-c",
            f"model_providers.gcsis-codex.name={quote('gcsis-codex')}",
            "-c",
            f"model_providers.gcsis-codex.base_url={quote(config.base_url)}",
            "-c",
            f"model_providers.gcsis-codex.env_key={quote(API_KEY_ENVIRONMENT)}",
            "-c",
            f"model_providers.gcsis-codex.wire_api={quote('responses')}",
            "-o",
            config.output_path,
            "-",
        ]
    )
    return command


def build_interactive_command(
    config: ProcessConfig, session_id: str, binary: str | None = None
) -> list[str]:
    """Build a shell-safe command for interactive ``codex resume``."""
    if not session_id or any(char in session_id for char in "\r\n"):
        raise CodexError("Codex 会话 ID 无效")
    selected_binary = binary or config.binary
    if not selected_binary:
        raise CodexUnavailable("codex is unavailable")

    def quote(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    command = [
        selected_binary,
        "-C",
        str(Path(config.workspace).resolve()),
        "-s",
        "danger-full-access",
        "--no-alt-screen",
    ]
    if config.model:
        command.extend(["-m", config.model])
    command.extend(
        [
            "-c",
            "model_provider=gcsis-codex",
            "-c",
            f"model_providers.gcsis-codex.name={quote('gcsis-codex')}",
            "-c",
            f"model_providers.gcsis-codex.base_url={quote(config.base_url)}",
            "-c",
            f"model_providers.gcsis-codex.env_key={quote(API_KEY_ENVIRONMENT)}",
            "-c",
            f"model_providers.gcsis-codex.wire_api={quote('responses')}",
            "resume",
            session_id,
        ]
    )
    return command


def _launch_system_terminal(script_path: Path, workspace: Path) -> None:
    """Start a terminal emulator without invoking a shell through Python."""
    if os.name == "nt":
        candidates = [["cmd.exe", "/K", str(script_path)]]
    elif sys.platform == "darwin":
        try:
            subprocess.run(
                ["/usr/bin/open", "-a", "Terminal", str(script_path)],
                cwd=str(workspace),
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (
            OSError,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
        ) as exc:
            raise CodexError("无法打开系统终端，请手动运行任务中的恢复脚本") from exc
        return
    else:
        candidates = [
            ["x-terminal-emulator", "-e", "/bin/zsh", str(script_path)],
            ["gnome-terminal", "--", "/bin/zsh", str(script_path)],
            ["konsole", "-e", "/bin/zsh", str(script_path)],
        ]
    for candidate in candidates:
        executable = shutil.which(candidate[0])
        if not executable:
            continue
        try:
            subprocess.Popen(candidate, cwd=str(workspace))
        except OSError:
            continue
        return
    raise CodexError("未找到可用的系统终端，请手动运行任务中的恢复脚本")


def _open_system_path(path: Path) -> None:
    """Open a trusted local directory in the platform file manager."""
    if os.name == "nt":
        command = ["explorer.exe", str(path)]
    elif sys.platform == "darwin":
        command = ["/usr/bin/open", str(path)]
    else:
        executable = shutil.which("xdg-open")
        if not executable:
            raise CodexError("未找到可用的文件管理器")
        command = [executable, str(path)]
    try:
        subprocess.Popen(
            command,
            cwd=str(path if path.is_dir() else path.parent),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise CodexError("无法打开题目目录") from exc


def _prepare_codex_home(config: ProcessConfig) -> str:
    workspace = Path(config.workspace).resolve()
    home = Path(config.home).resolve() if config.home else workspace / ".codex-home"
    try:
        relative = home.relative_to(workspace)
    except ValueError as exc:
        raise CodexError("Codex data directory must stay inside the workspace") from exc
    if relative == Path("."):
        raise CodexError("Codex data directory must not be the workspace root")
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        home.chmod(0o700)
    except OSError as exc:
        raise CodexError(f"prepare Codex data directory: {exc}") from exc
    return str(home)


def _process_environment(api_key: str, codex_home: str) -> dict[str, str]:
    private_keys = {API_KEY_ENVIRONMENT, CODEX_HOME_ENVIRONMENT}
    env = {key: value for key, value in os.environ.items() if key not in private_keys}
    env[CODEX_HOME_ENVIRONMENT] = codex_home
    if api_key:
        env[API_KEY_ENVIRONMENT] = api_key
    return env


def _stderr_event_kind(message: str) -> str:
    """Classify Codex diagnostics without treating warnings as hard failures."""

    normalized = " ".join(message.casefold().split())
    warning_markers = (
        "warning",
        "warn:",
        "not found. defaulting to fallback metadata",
        "this can degrade performance",
        "deprecated",
    )
    if any(marker in normalized for marker in warning_markers):
        return "warning"
    return "stderr"


class Redactor:
    def __init__(self, *secrets: str) -> None:
        unique = {secret for secret in secrets if secret}
        self.secrets = tuple(sorted(unique, key=len, reverse=True))

    def apply(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value


def _usage_int(source: Mapping[str, Any], snake_name: str, camel_name: str) -> int:
    return _nonnegative_int(source.get(snake_name, source.get(camel_name, 0)))


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _token_usage_from_mapping(source: Mapping[str, Any]) -> TokenUsage | None:
    keys = {
        "input_tokens",
        "inputTokens",
        "cached_input_tokens",
        "cachedInputTokens",
        "cache_write_input_tokens",
        "cacheWriteInputTokens",
        "output_tokens",
        "outputTokens",
        "reasoning_output_tokens",
        "reasoningOutputTokens",
        "total_tokens",
        "totalTokens",
    }
    if not any(key in source for key in keys):
        return None
    input_tokens = _usage_int(source, "input_tokens", "inputTokens")
    output_tokens = _usage_int(source, "output_tokens", "outputTokens")
    total_tokens = _usage_int(source, "total_tokens", "totalTokens")
    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=_usage_int(
            source, "cached_input_tokens", "cachedInputTokens"
        ),
        cache_write_input_tokens=_usage_int(
            source, "cache_write_input_tokens", "cacheWriteInputTokens"
        ),
        output_tokens=output_tokens,
        reasoning_output_tokens=_usage_int(
            source, "reasoning_output_tokens", "reasoningOutputTokens"
        ),
        total_tokens=total_tokens or input_tokens + output_tokens,
    )


def _event_token_usage(
    envelope: Mapping[str, Any], item: Mapping[str, Any]
) -> TokenUsage | None:
    containers = (item, envelope)
    for container in containers:
        for key in ("turn_token_usage", "turnTokenUsage", "usage"):
            raw_usage = container.get(key)
            if isinstance(raw_usage, Mapping):
                usage = _token_usage_from_mapping(raw_usage)
                if usage is not None:
                    return usage
    raw_payload = envelope.get("payload")
    if isinstance(raw_payload, Mapping):
        return _event_token_usage(raw_payload, {})
    return None


def _token_usage_from_summary(summary: str) -> TokenUsage | None:
    try:
        raw = json.loads(summary)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, Mapping):
        return None
    raw_item = raw.get("item")
    item = cast(Mapping[str, Any], raw_item) if isinstance(raw_item, Mapping) else {}
    return _event_token_usage(raw, item)


def parse_event_line(
    line: str, at: datetime | None = None, redactor: Redactor | None = None
) -> Event:
    now = at or datetime.now(timezone.utc)
    redact = redactor or Redactor()
    try:
        envelope = json.loads(line)
    except json.JSONDecodeError:
        return Event(now, "stdout", redact.apply(line.strip()))
    if not isinstance(envelope, Mapping):
        return Event(now, "stdout", redact.apply(line.strip()))
    raw_item = envelope.get("item")
    item: Mapping[str, Any] = (
        cast(Mapping[str, Any], raw_item) if isinstance(raw_item, Mapping) else {}
    )
    session_id = _first_nonempty(
        envelope.get("session_id"),
        envelope.get("thread_id"),
        item.get("session_id"),
        item.get("thread_id"),
        envelope.get("id") if envelope.get("type") == "thread.started" else "",
    )
    envelope_type = str(envelope.get("type") or "")
    kind = str(item.get("type") or envelope_type or "stdout")
    usage = _event_token_usage(envelope, item)
    item_id = _first_nonempty(item.get("id"), envelope.get("item_id"))
    status = _first_nonempty(item.get("status"), envelope.get("status"))
    if not status:
        if envelope_type.endswith(".started"):
            status = "in_progress"
        elif envelope_type.endswith(".completed"):
            status = "completed"
        elif envelope_type.endswith(".failed"):
            status = "failed"
    command = redact.apply(
        _first_nonempty(item.get("command"), envelope.get("command"))
    )
    output = redact.apply(
        _first_nonempty(
            item.get("aggregated_output"),
            item.get("output"),
            envelope.get("output"),
        )
    )
    raw_exit_code = item.get(
        "exit_code", item.get("exitCode", envelope.get("exit_code"))
    )
    exit_code: int | None = None
    if raw_exit_code is not None and not isinstance(raw_exit_code, bool):
        try:
            exit_code = int(raw_exit_code)
        except (TypeError, ValueError):
            exit_code = None
    summary = _first_nonempty(
        item.get("text"),
        command,
        item.get("message"),
        output,
        envelope.get("message"),
        line.strip(),
    )
    if kind == "thread.started":
        summary = "Codex 会话已建立"
    elif kind == "turn.started":
        summary = "开始处理请求"
    elif kind == "turn.completed":
        summary = "本轮处理完成"
    return Event(
        now,
        kind,
        redact.apply(str(summary)),
        session_id,
        usage,
        envelope_type=envelope_type,
        item_id=item_id,
        status=status,
        command=command,
        output=output if kind == "command_execution" else "",
        exit_code=exit_code,
        started_at=now if status in {"in_progress", "running", "started"} else None,
        finished_at=now if status in {"completed", "failed", "canceled"} else None,
    )


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if str(value or "").strip():
            return str(value)
    return ""


@dataclass(frozen=True)
class Snapshot:
    id: str
    exercise_id: int
    mode: str
    status: str
    queue_position: int = 0
    active: int = 0
    limit: int = 0
    error: str = ""
    output: str = ""
    writeup_path: str = ""
    challenge_path: str = ""
    events: tuple[Event, ...] = ()
    session_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parent_id: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    completed_turns: int = 0
    title: str = ""
    source_message: str = ""
    log_count: int = 0
    first_event_at: datetime | None = None
    last_event_at: datetime | None = None
    next_event_sequence: int = 1
    current_round: int = 0
    rounds_started: int = 0
    pending_message: str = ""
    pending_status: str = ""
    pending_created_at: datetime | None = None
    pending_started_at: datetime | None = None
    interrupted_at: datetime | None = None
    interruption_count: int = 0
    last_resumed_at: datetime | None = None
    process_id: int = 0
    process_alive: bool = False
    process_started_at: datetime | None = None

    def to_dict(self, include_events: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "exerciseId": self.exercise_id,
            "status": self.status,
            "active": self.active,
            "limit": self.limit,
            "createdAt": _time_wire(self.created_at),
        }
        if self.mode:
            result["mode"] = self.mode
        if self.queue_position:
            result["queuePosition"] = self.queue_position
        if self.error:
            result["error"] = self.error
        if self.output:
            result["output"] = self.output
        if self.writeup_path:
            result["writeupPath"] = self.writeup_path
        if self.challenge_path:
            result["challengePath"] = self.challenge_path
        if include_events and self.events:
            result["events"] = [event.to_dict() for event in self.events]
        if self.session_id:
            result["sessionId"] = self.session_id
        if self.started_at:
            result["startedAt"] = _time_wire(self.started_at)
        if self.finished_at:
            result["finishedAt"] = _time_wire(self.finished_at)
        if self.parent_id:
            result["parentTaskId"] = self.parent_id
        if self.title:
            result["title"] = self.title
        if self.source_message:
            result["sourceMessage"] = self.source_message
        result["logCount"] = self.log_count or len(self.events)
        result["nextEventSequence"] = self.next_event_sequence
        if self.first_event_at:
            result["firstEventAt"] = _time_wire(self.first_event_at)
        elif self.events:
            result["firstEventAt"] = _time_wire(self.events[0].at)
        if self.last_event_at:
            result["lastEventAt"] = _time_wire(self.last_event_at)
        elif self.events:
            result["lastEventAt"] = _time_wire(self.events[-1].at)
        if self.current_round > 0:
            result["currentRound"] = self.current_round
        if self.rounds_started > 0:
            result["roundsStarted"] = self.rounds_started
        if self.pending_message:
            pending: dict[str, Any] = {
                "message": self.pending_message,
                "status": self.pending_status or "waiting",
            }
            if self.pending_created_at:
                pending["createdAt"] = _time_wire(self.pending_created_at)
            if self.pending_started_at:
                pending["startedAt"] = _time_wire(self.pending_started_at)
            result["pendingMessage"] = pending
        if self.interrupted_at:
            result["interruptedAt"] = _time_wire(self.interrupted_at)
        if self.interruption_count > 0:
            result["interruptionCount"] = self.interruption_count
        if self.last_resumed_at:
            result["lastResumedAt"] = _time_wire(self.last_resumed_at)
        if self.process_id > 0:
            result["processId"] = self.process_id
            result["processAlive"] = self.process_alive
        if self.process_started_at:
            result["processStartedAt"] = _time_wire(self.process_started_at)
        usage = self.usage
        completed_turns = self.completed_turns
        if completed_turns <= 0 and usage == TokenUsage():
            usage, completed_turns = _aggregate_event_usage(self.events)
        if completed_turns > 0 or usage != TokenUsage():
            result["usage"] = usage.to_dict()
            result["completedTurns"] = completed_turns
        return result


def _aggregate_event_usage(events: Iterable[Event]) -> tuple[TokenUsage, int]:
    usage = TokenUsage()
    completed_turns = 0
    for event in events:
        if event.kind != "turn.completed" or event.usage is None:
            continue
        usage += event.usage
        completed_turns += 1
    return usage, completed_turns


@dataclass(frozen=True)
class TaskList:
    tasks: tuple[Snapshot, ...]
    active: int
    limit: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": [task.to_dict(include_events=False) for task in self.tasks],
            "active": self.active,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class ManagerConfig:
    process: ProcessConfig
    prompt: PromptSourceProtocol | None
    runner: Any
    max_concurrency: int = DEFAULT_CODEX_MAX_CONCURRENCY
    runs_root: str | Path = "codex-runs"
    writeup_root: str | Path = "writeups"
    workspace_root: str | Path = ""
    system_prompt: str = ""
    tasks_path: str | Path = ""
    auto_resume_interrupted: bool = False
    event_publisher: EventPublisher | None = None


@dataclass
class _Task:
    snapshot: Snapshot
    prompt: str
    config: ProcessConfig
    cancel_event: threading.Event | None = None
    pending_followup: str = ""
    stop_reason: str = ""


class CodexManager:
    def __init__(self, config: ManagerConfig) -> None:
        self.config = config
        self.limit = (
            config.max_concurrency
            if config.max_concurrency > 0
            else DEFAULT_CODEX_MAX_CONCURRENCY
        )
        self.runs_root = (
            Path("codex-runs") if config.runs_root == "" else Path(config.runs_root)
        )
        self.writeup_root = (
            Path("writeups") if config.writeup_root == "" else Path(config.writeup_root)
        )
        self.workspace_root = Path(
            config.workspace_root or config.process.workspace or Path.cwd()
        )
        self.system_prompt = config.system_prompt.strip() or DEFAULT_CODEX_SYSTEM_PROMPT
        self.auto_resume_interrupted = config.auto_resume_interrupted
        self.tasks_path = (
            Path(config.tasks_path)
            if config.tasks_path
            else self.workspace_root / "runtime" / "codex" / "tasks.json"
        )
        self._tasks: dict[str, _Task] = {}
        self._order: list[str] = []
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._condition = threading.Condition()
        self._closed = False
        self._workers: list[threading.Thread] = []
        self._event_publisher = config.event_publisher
        self._published_task_signatures: dict[str, str] = {}
        self._load_tasks()
        if self.enabled():
            self._workers = [
                threading.Thread(target=self._worker, args=(index,), daemon=True)
                for index in range(self.limit)
            ]
            for worker in self._workers:
                worker.start()
            with self._condition:
                for task in self._tasks.values():
                    if task.snapshot.status == "queued":
                        self._queue.put(task.snapshot.id)
                self._auto_resume_interrupted_locked()

    def configure(
        self,
        process: ProcessConfig,
        max_concurrency: int | None = None,
        system_prompt: str | None = None,
        auto_resume_interrupted: bool | None = None,
    ) -> None:
        """Apply provider settings for tasks started after the update."""

        with self._condition:
            was_enabled = self.enabled()
            selected_limit = self.limit if max_concurrency is None else max_concurrency
            if not 1 <= selected_limit <= 16:
                raise ValueError("codex concurrency must be between 1 and 16")
            self.config = replace(
                self.config, process=process, max_concurrency=selected_limit
            )
            if system_prompt is not None:
                self.system_prompt = (
                    system_prompt.strip() or DEFAULT_CODEX_SYSTEM_PROMPT
                )
            if auto_resume_interrupted is not None:
                self.auto_resume_interrupted = auto_resume_interrupted
            self.limit = selected_limit
            desired = selected_limit if self.enabled() else 0
            while len(self._workers) < desired:
                worker = threading.Thread(
                    target=self._worker, args=(len(self._workers),), daemon=True
                )
                self._workers.append(worker)
                worker.start()
            # A task loaded while Codex was unavailable is retained as queued
            # metadata.  Put those tasks back into the worker queue when a
            # later settings update enables Codex; normal reconfiguration does
            # not enqueue them again and therefore cannot duplicate work.
            if not was_enabled and self.enabled():
                for task in self._tasks.values():
                    if task.snapshot.status == "queued":
                        self._queue.put(task.snapshot.id)
            self._auto_resume_interrupted_locked()
            self._condition.notify_all()

    def _auto_resume_interrupted_locked(self) -> None:
        if not self.auto_resume_interrupted or not self.enabled():
            return
        for task in self._tasks.values():
            if task.snapshot.status != "interrupted" or not task.snapshot.session_id:
                continue
            process_alive = _pid_exists(task.snapshot.process_id)
            if process_alive != task.snapshot.process_alive:
                task.snapshot = _replace_snapshot(
                    task.snapshot, process_alive=process_alive
                )
            if process_alive:
                continue
            self._resume_interrupted_locked(task, CONTINUE_MESSAGE)
        self._persist_tasks_locked()

    def enabled(self) -> bool:
        return bool(
            self.config.process.binary
            and self.config.process.base_url
            and self.config.process.model
            and self.config.prompt is not None
            and self.config.runner is not None
        )

    def start(self, exercise_id: int, prompt_override: str = "") -> Snapshot:
        return self._start(exercise_id, "full", prompt_override)

    def start_pure(self, exercise_id: int, prompt_override: str = "") -> Snapshot:
        return self._start(exercise_id, "pure", prompt_override)

    def follow_up(self, task_id: str, message: str) -> Snapshot:
        """Send another message to the same Codex conversation."""

        normalized = _task_message(message)
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            session_id = task.snapshot.session_id.strip()
            if not session_id:
                raise CodexError("Codex 会话尚未建立，暂时不能追加消息")
            if task.snapshot.status == "queued":
                raise CodexError("任务尚未开始，请稍后再追加消息")
            if task.snapshot.status == "running":
                if task.pending_followup or task.snapshot.pending_message:
                    raise CodexError("当前任务已经有一条待处理消息")
                task.pending_followup = normalized
                now = datetime.now(timezone.utc)
                task.snapshot = _replace_snapshot(
                    task.snapshot,
                    pending_message=normalized,
                    pending_status="ending_previous",
                    pending_created_at=now,
                    pending_started_at=None,
                )
                self._append_event_locked(
                    task,
                    Event(
                        now,
                        "user.followup_queued",
                        normalized,
                        status="waiting",
                        metadata={"reason": "switch_round"},
                    ),
                    round_number=max(1, task.snapshot.current_round),
                )
                task.stop_reason = "followup"
                if task.cancel_event is not None:
                    task.cancel_event.set()
                self._persist_tasks_locked()
                return self._snapshot(task)
            if task.snapshot.status == "interrupted":
                self._resume_interrupted_locked(task, normalized)
                self._persist_tasks_locked()
                return self._snapshot(task)
            task.prompt = normalized
            task.config = replace(
                task.config,
                resume_session_id=session_id,
                fork_session=False,
            )
            task.snapshot = _replace_snapshot(
                task.snapshot,
                status="queued",
                queue_position=0,
                active=0,
                error="",
                output="",
                finished_at=None,
                last_resumed_at=datetime.now(timezone.utc),
            )
            self._refresh_queue_positions()
            self._queue.put(task_id)
            self._persist_tasks_locked()
            return self._snapshot(task)

    def continue_task(self, task_id: str) -> Snapshot:
        """Resume a task with the standard recovery instruction."""
        return self.follow_up(task_id, CONTINUE_MESSAGE)

    def resume_interrupted(self, task_id: str) -> Snapshot:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            if task.snapshot.status != "interrupted":
                raise CodexError("只有运行被中断的任务可以恢复执行")
            self._resume_interrupted_locked(task, CONTINUE_MESSAGE)
            self._persist_tasks_locked()
            return self._snapshot(task)

    def _resume_interrupted_locked(self, task: _Task, message: str) -> None:
        session_id = task.snapshot.session_id.strip()
        if not session_id:
            raise CodexError("中断任务没有可恢复的 Codex Session ID")
        if _pid_exists(task.snapshot.process_id):
            task.snapshot = _replace_snapshot(task.snapshot, process_alive=True)
            raise CodexError("原 Codex 子进程仍在运行，请先打开终端处理")
        now = datetime.now(timezone.utc)
        task.prompt = message
        task.config = replace(
            task.config,
            resume_session_id=session_id,
            fork_session=False,
        )
        task.stop_reason = ""
        task.pending_followup = ""
        task.snapshot = _replace_snapshot(
            task.snapshot,
            status="queued",
            queue_position=0,
            active=0,
            error="",
            output="",
            finished_at=None,
            last_resumed_at=now,
            pending_message="",
            pending_status="",
            pending_created_at=None,
            pending_started_at=None,
            process_id=0,
            process_alive=False,
        )
        self._append_event_locked(
            task,
            Event(now, "task.resumed", "正在使用原会话恢复中断任务", status="queued"),
            round_number=max(1, task.snapshot.current_round),
        )
        self._refresh_queue_positions()
        self._queue.put(task.snapshot.id)

    def update_pending_message(self, task_id: str, message: str) -> Snapshot:
        normalized = _task_message(message)
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            if not task.pending_followup or task.snapshot.pending_status not in {
                "ending_previous",
                "waiting",
            }:
                raise CodexError("当前没有可编辑的待处理消息")
            task.pending_followup = normalized
            task.snapshot = _replace_snapshot(task.snapshot, pending_message=normalized)
            self._append_event_locked(
                task,
                Event(
                    datetime.now(timezone.utc),
                    "user.followup_updated",
                    normalized,
                    status="waiting",
                ),
                round_number=max(1, task.snapshot.current_round),
            )
            self._persist_tasks_locked()
            return self._snapshot(task)

    def cancel_pending_message(self, task_id: str) -> Snapshot:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            if not task.pending_followup or task.snapshot.pending_status not in {
                "ending_previous",
                "waiting",
            }:
                raise CodexError("当前没有可取消的待处理消息")
            message = task.pending_followup
            task.pending_followup = ""
            task.stop_reason = "followup_canceled"
            if task.cancel_event is not None:
                task.cancel_event.clear()
            task.snapshot = _replace_snapshot(
                task.snapshot,
                pending_message="",
                pending_status="",
                pending_created_at=None,
                pending_started_at=None,
            )
            self._append_event_locked(
                task,
                Event(
                    datetime.now(timezone.utc),
                    "user.followup_canceled",
                    message,
                    status="canceled",
                ),
                round_number=max(1, task.snapshot.current_round),
            )
            self._persist_tasks_locked()
            return self._snapshot(task)

    def open_terminal(self, task_id: str) -> dict[str, Any]:
        """Open the saved Codex session in the user's system terminal."""
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            session_id = task.snapshot.session_id.strip()
            if not session_id:
                raise CodexError("Codex 会话尚未建立，暂时不能打开对话")
            if task.snapshot.status == "running":
                raise CodexError("任务正在运行，请停止后再打开对话")
            config = task.config

        workspace = Path(config.workspace).resolve()
        if workspace != self.workspace_root.resolve():
            raise CodexError("Codex 工作目录与当前项目不一致")
        runs_root = (
            self.runs_root
            if self.runs_root.is_absolute()
            else workspace / self.runs_root
        )
        script_name = (
            "open-terminal.command" if sys.platform == "darwin" else "open-terminal.zsh"
        )
        script_path = (runs_root / task_id / script_name).resolve()
        try:
            script_path.relative_to(workspace)
        except ValueError as exc:
            raise CodexError("终端启动脚本必须位于工作区内") from exc
        script_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = build_interactive_command(config, session_id, config.binary)
        self._write_terminal_script(script_path, config, command)
        _launch_system_terminal(script_path, workspace)
        try:
            display_path = script_path.relative_to(workspace).as_posix()
        except ValueError:
            display_path = str(script_path)
        return {
            "opened": True,
            "scriptPath": display_path,
            "command": f"/bin/zsh {shlex.quote(str(script_path))}",
        }

    def open_challenge_folder(self, task_id: str) -> dict[str, Any]:
        """Open the task's workspace-local challenge directory."""
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            relative_path = self._challenge_path_locked(task)

        workspace = self.workspace_root.resolve()
        download_root = (workspace / "download").resolve()
        candidate = Path(relative_path)
        directory = (
            candidate if candidate.is_absolute() else workspace / candidate
        ).resolve()
        try:
            relative = directory.relative_to(download_root)
        except ValueError as exc:
            raise CodexError("题目目录必须位于项目 download 目录内") from exc
        if relative == Path("."):
            raise CodexError("题目目录无效")
        try:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise CodexError("无法创建题目目录") from exc
        if directory.resolve() != download_root / relative:
            raise CodexError("题目目录包含不受信任的符号链接")
        _open_system_path(directory)
        return {
            "opened": True,
            "path": directory.relative_to(workspace).as_posix(),
        }

    def _challenge_path_locked(self, task: _Task) -> str:
        if task.snapshot.challenge_path:
            return task.snapshot.challenge_path
        if task.snapshot.parent_id:
            parent = self._tasks.get(task.snapshot.parent_id)
            if parent is not None and parent is not task:
                return self._challenge_path_locked(parent)
        legacy_path = challenge_path_from_writeup(
            task.snapshot.writeup_path,
            task.snapshot.id,
            task.snapshot.mode,
        )
        if legacy_path:
            return legacy_path
        raise CodexError("该任务没有可用的题目目录信息")

    @staticmethod
    def _write_terminal_script(
        script_path: Path, config: ProcessConfig, command: list[str]
    ) -> None:
        home = _prepare_codex_home(config)
        lines = [
            "#!/bin/zsh",
            "set -e",
            f"export {CODEX_HOME_ENVIRONMENT}={shlex.quote(home)}",
        ]
        if config.api_key:
            lines.append(f"export {API_KEY_ENVIRONMENT}={shlex.quote(config.api_key)}")
        lines.extend(
            [
                f"cd {shlex.quote(str(Path(config.workspace).resolve()))}",
                f"exec {shlex.join(command)}",
                "",
            ]
        )
        script_path.write_text("\n".join(lines), encoding="utf-8")
        script_path.chmod(0o700)

    def side(self, task_id: str, message: str) -> Snapshot:
        """Fork a Codex conversation for an isolated side question."""

        normalized = _task_message(message)
        with self._condition:
            parent = self._tasks.get(task_id)
            if parent is None:
                raise TaskNotFound("codex task not found")
            if parent.snapshot.mode == "side":
                raise CodexError("Side 对话不能继续创建 Side 对话")
            session_id = parent.snapshot.session_id.strip()
            if not session_id:
                raise CodexError("Codex 会话尚未建立，暂时不能发起 Side 提问")
            task_id = secrets.token_hex(12)
            now = datetime.now(timezone.utc)
            output_path = self.runs_root / task_id / "final.md"
            snapshot = Snapshot(
                task_id,
                parent.snapshot.exercise_id,
                "side",
                "queued",
                limit=self.limit,
                created_at=now,
                parent_id=parent.snapshot.id,
                challenge_path=self._challenge_path_locked(parent),
                title=_message_summary(normalized),
                source_message=normalized,
            )
            task = _Task(
                snapshot,
                normalized,
                replace(
                    parent.config,
                    output_path=str(output_path),
                    resume_session_id="",
                    fork_session=False,
                ),
            )
            self._tasks[task_id] = task
            self._order.append(task_id)
            self._trim_history()
            self._refresh_queue_positions()
            self._queue.put(task_id)
            self._persist_tasks_locked()
            return self._snapshot(task)

    def _start(
        self, exercise_id: int, mode: str, prompt_override: str = ""
    ) -> Snapshot:
        if exercise_id <= 0:
            raise ValueError("invalid exercise ID")
        if not self.enabled():
            raise CodexUnavailable("codex is unavailable")
        try:
            if mode == "pure":
                if not hasattr(self.config.prompt, "prompt_pure"):
                    raise PromptError("pure prompt source is unavailable")
                prompt_source = cast(PurePromptSourceProtocol, self.config.prompt)
                prompt_result = prompt_source.prompt_pure(exercise_id)
                custom_instructions = prompt_override.strip()
                instructions = (
                    f"{custom_instructions}\n\n{CODEX_PURE_INSTRUCTIONS}"
                    if custom_instructions
                    else CODEX_PURE_INSTRUCTIONS
                )
            else:
                full_prompt_source = self.config.prompt
                if full_prompt_source is None:
                    raise PromptError("prompt source is unavailable")
                prompt_result = full_prompt_source.prompt(exercise_id)
                instructions = prompt_override.strip() or self.system_prompt
        except Exception as exc:
            raise PromptError("codex prompt preparation failed") from exc
        prompt = prompt_result.prompt.strip()
        if not prompt:
            raise PromptError("codex prompt preparation failed: prompt is empty")
        task_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc)
        output_path = self.runs_root / task_id / "final.md"
        writeup_path = writeup_path_for(
            self.workspace_root,
            self.writeup_root,
            prompt_result,
            exercise_id,
            task_id,
            mode,
        )
        challenge_path = challenge_path_for(
            self.workspace_root, prompt_result, exercise_id
        )
        snapshot = Snapshot(
            task_id,
            exercise_id,
            mode,
            "queued",
            limit=self.limit,
            writeup_path=writeup_path,
            challenge_path=challenge_path,
            created_at=now,
            title=prompt_result.exercise_name or f"题目 {exercise_id}",
            source_message=("启动纯解题模式" if mode == "pure" else "启动完整解题模式"),
        )
        with self._condition:
            process = self.config.process
        task = _Task(
            snapshot,
            f"{prompt}\n\n{instructions}",
            ProcessConfig(
                process.binary,
                process.workspace,
                process.base_url,
                process.api_key,
                process.model,
                str(output_path),
                process.home,
            ),
        )
        with self._condition:
            if self._closed:
                raise CodexUnavailable("codex is unavailable")
            self._tasks[task_id] = task
            self._order.append(task_id)
            self._trim_history()
            self._refresh_queue_positions()
            self._queue.put(task_id)
            self._persist_tasks_locked()
            return self._snapshot(task)

    def list(self) -> TaskList:
        with self._condition:
            self._refresh_queue_positions()
            active = self._active_count()
            snapshots = tuple(
                self._snapshot(self._tasks[task_id], active)
                for task_id in reversed(self._order)
                if task_id in self._tasks
            )
            return TaskList(snapshots, active, self.limit)

    def get(self, task_id: str) -> Snapshot:
        with self._condition:
            self._refresh_queue_positions()
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            return self._snapshot(task)

    def events_page(
        self, task_id: str, before: int = 0, limit: int = DEFAULT_EVENT_PAGE_SIZE
    ) -> dict[str, Any]:
        if before < 0:
            raise ValueError("before must be non-negative")
        if not 1 <= limit <= MAX_EVENT_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_EVENT_PAGE_SIZE}")
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            path = self._events_path(task.snapshot.id)
            expected_count = task.snapshot.log_count
        records: deque[Event] = deque(maxlen=limit)
        eligible_count = 0
        for event in read_events(path):
            if before > 0 and event.sequence >= before:
                continue
            eligible_count += 1
            records.append(event)
        rows = [*records]
        return {
            "events": [event.to_dict() for event in rows],
            "hasMore": eligible_count > len(rows),
            "nextBefore": rows[0].sequence if rows else 0,
            "total": expected_count,
        }

    def log_text(self, task_id: str) -> str:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            title = task.snapshot.title or f"题目 {task.snapshot.exercise_id}"
            path = self._events_path(task_id)
        lines = [f"Codex 任务：{title}", f"任务 ID：{task_id}", ""]
        for event in read_events(path):
            prefix = f"[{_time_wire(event.at)}]"
            if event.round_number:
                prefix += f" [轮次 {event.round_number}]"
            prefix += f" [{event.kind}]"
            if event.status:
                prefix += f" [{event.status}]"
            lines.append(f"{prefix} {event.summary}")
            if event.command and event.command != event.summary:
                lines.append(f"命令：{event.command}")
            if event.output:
                lines.append("输出：")
                lines.append(event.output)
            if event.exit_code is not None:
                lines.append(f"退出码：{event.exit_code}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def log_json(self, task_id: str) -> builtins.list[dict[str, Any]]:
        with self._condition:
            if task_id not in self._tasks:
                raise TaskNotFound("codex task not found")
            path = self._events_path(task_id)
        return [event.to_dict() for event in read_events(path)]

    def raw_log_path(self, task_id: str) -> Path:
        with self._condition:
            if task_id not in self._tasks:
                raise TaskNotFound("codex task not found")
            path = self._events_path(task_id).resolve()
        try:
            path.relative_to(self.workspace_root.resolve())
        except ValueError as exc:
            raise CodexError("日志文件必须位于工作区内") from exc
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.touch(exist_ok=True, mode=0o600)
        return path

    def open_log_file(self, task_id: str) -> dict[str, Any]:
        path = self.raw_log_path(task_id)
        _open_system_path(path)
        try:
            display_path = path.relative_to(self.workspace_root.resolve()).as_posix()
        except ValueError:
            display_path = str(path)
        return {"opened": True, "path": display_path}

    def rename(self, task_id: str, title: str) -> Snapshot:
        normalized = title.strip()
        if not normalized:
            raise ValueError("对话标题不能为空")
        if len(normalized) > 120:
            raise ValueError("对话标题不能超过 120 个字符")
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            task.snapshot = _replace_snapshot(task.snapshot, title=normalized)
            self._persist_tasks_locked()
            return self._snapshot(task)

    def mark_finished(self, task_id: str) -> Snapshot:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            if task.snapshot.status != "interrupted":
                raise CodexError("只有运行被中断的任务可以标记结束")
            if _pid_exists(task.snapshot.process_id):
                task.snapshot = _replace_snapshot(task.snapshot, process_alive=True)
                raise CodexError("原 Codex 子进程仍在运行，请先在终端中结束该进程")
            now = datetime.now(timezone.utc)
            task.snapshot = _replace_snapshot(
                task.snapshot,
                status="canceled",
                finished_at=now,
                error="已手动标记结束",
                process_id=0,
                process_alive=False,
            )
            self._append_event_locked(
                task,
                Event(
                    now, "task.finished", "中断任务已手动标记结束", status="canceled"
                ),
                round_number=max(1, task.snapshot.current_round),
            )
            self._persist_tasks_locked()
            return self._snapshot(task)

    def cancel(self, task_id: str) -> Snapshot:
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            now = datetime.now(timezone.utc)
            if task.snapshot.status == "queued":
                task.stop_reason = "user"
                task.snapshot = _replace_snapshot(
                    task.snapshot,
                    status="canceled",
                    finished_at=now,
                    queue_position=0,
                    error="已由用户停止",
                )
                self._append_event_locked(
                    task,
                    Event(
                        now, "task.canceled", "排队任务已由用户停止", status="canceled"
                    ),
                    round_number=max(1, task.snapshot.current_round),
                )
            elif task.snapshot.status == "running" and task.cancel_event is not None:
                task.stop_reason = "user"
                task.cancel_event.set()
            else:
                raise CodexError("当前任务不在可停止状态")
            self._persist_tasks_locked()
            return self._snapshot(task)

    def delete(self, task_id: str) -> None:
        """Remove a terminal task and its workspace-local artifacts."""
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None:
                raise TaskNotFound("codex task not found")
            if task.snapshot.status in {"queued", "running"}:
                raise CodexError("任务正在运行，请先停止任务后再删除")
            self._tasks.pop(task_id, None)
            self._order = [item for item in self._order if item != task_id]
            writeup_path = task.snapshot.writeup_path
            output_path = task.config.output_path
            self._published_task_signatures.pop(task_id, None)
            self._publish("codex.deleted", task_id, {"id": task_id})
            self._persist_tasks_locked()
        self._remove_artifacts(task_id, writeup_path, output_path)

    def _remove_artifacts(
        self, task_id: str, writeup_path: str, output_path: str
    ) -> None:
        workspace = self.workspace_root.resolve()
        run_root = self._events_path(task_id).parent.resolve()
        try:
            run_root.relative_to(workspace)
        except ValueError as exc:
            raise CodexError("任务文件路径必须位于工作区内") from exc
        if run_root != workspace and run_root.exists() and not run_root.is_symlink():
            shutil.rmtree(run_root)
        for raw_path in (writeup_path, output_path):
            if not raw_path:
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = workspace / path
            resolved = path.resolve()
            try:
                resolved.relative_to(workspace)
            except ValueError:
                continue
            if resolved == workspace or resolved.is_symlink() or not resolved.exists():
                continue
            if resolved.is_file():
                resolved.unlink()

    def close(self) -> None:
        """Persist running tasks as interrupted before stopping local workers."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            now = datetime.now(timezone.utc)
            for task in self._tasks.values():
                if task.snapshot.status != "running":
                    continue
                task.stop_reason = "shutdown"
                process_alive = _pid_exists(task.snapshot.process_id)
                task.snapshot = _replace_snapshot(
                    task.snapshot,
                    status="interrupted",
                    active=0,
                    queue_position=0,
                    interrupted_at=now,
                    interruption_count=task.snapshot.interruption_count + 1,
                    process_alive=process_alive,
                    error=(
                        "程序退出，Codex 子进程仍存在但日志连接已断开"
                        if process_alive
                        else "程序退出导致任务运行被中断"
                    ),
                )
                self._append_event_locked(
                    task,
                    Event(
                        now,
                        "task.interrupted",
                        "程序退出，当前运行轮次已中断",
                        status="interrupted",
                        metadata={"reason": "shutdown"},
                    ),
                    round_number=max(1, task.snapshot.current_round),
                )
                if task.cancel_event is not None:
                    task.cancel_event.set()
            self._persist_tasks_locked()
            for _ in self._workers:
                self._queue.put(None)
            self._condition.notify_all()
        for worker in self._workers:
            worker.join(timeout=15)

    def _worker(self, worker_index: int) -> None:
        while True:
            with self._condition:
                while not self._closed and (
                    worker_index >= self.limit or not self.enabled()
                ):
                    self._condition.wait()
                if self._closed:
                    return
            task_id = self._queue.get()
            if task_id is None:
                return
            with self._condition:
                if self._closed:
                    return
                if worker_index >= self.limit or not self.enabled():
                    self._queue.put(task_id)
                    continue
                task = self._tasks.get(task_id)
                if task is None or task.snapshot.status != "queued":
                    continue
                task.cancel_event = threading.Event()
                task.stop_reason = ""
                now = datetime.now(timezone.utc)
                task.snapshot = _replace_snapshot(
                    task.snapshot,
                    status="running",
                    started_at=task.snapshot.started_at or now,
                    queue_position=0,
                )
                self._refresh_queue_positions()
                self._persist_tasks_locked()
            self._run_task(task)
            with self._condition:
                task.cancel_event = None

    def _run_task(self, task: _Task) -> None:
        try:
            Path(task.config.output_path).parent.mkdir(
                parents=True, exist_ok=True, mode=0o700
            )
            redactor = Redactor(self.config.process.api_key)
            while True:
                with self._condition:
                    round_number = task.snapshot.rounds_started + 1
                    now = datetime.now(timezone.utc)
                    pending_processing = bool(task.snapshot.pending_message)
                    task.snapshot = _replace_snapshot(
                        task.snapshot,
                        status="running",
                        current_round=round_number,
                        rounds_started=round_number,
                        pending_status=(
                            "processing"
                            if pending_processing
                            else task.snapshot.pending_status
                        ),
                        pending_started_at=(
                            now
                            if pending_processing
                            else task.snapshot.pending_started_at
                        ),
                    )
                    self._append_event_locked(
                        task,
                        Event(
                            now,
                            "user.message",
                            redactor.apply(task.prompt),
                            status="submitted",
                        ),
                        round_number=round_number,
                    )
                    self._append_event_locked(
                        task,
                        Event(
                            now,
                            "round.started",
                            f"第 {round_number} 轮开始处理",
                            status="running",
                        ),
                        round_number=round_number,
                    )
                    self._persist_tasks_locked()

                def on_event(event: Event) -> None:
                    redacted = replace(
                        event,
                        summary=redactor.apply(event.summary),
                        command=redactor.apply(event.command),
                        output=redactor.apply(event.output),
                    )
                    with self._condition:
                        self._append_event_locked(
                            task, redacted, round_number=round_number
                        )
                        self._persist_tasks_locked()

                current_cancel = task.cancel_event
                result = _invoke_runner(
                    self.config.runner,
                    task.config,
                    task.prompt,
                    on_event,
                    current_cancel,
                )
                session_id = result.session_id or task.snapshot.session_id
                with self._condition:
                    if session_id:
                        task.snapshot = _replace_snapshot(
                            task.snapshot, session_id=session_id
                        )
                    pending = task.pending_followup
                    reason = task.stop_reason
                    if pending:
                        task.pending_followup = ""

                if pending:
                    now = datetime.now(timezone.utc)
                    with self._condition:
                        self._append_event_locked(
                            task,
                            Event(
                                now,
                                "round.interrupted",
                                "收到用户追加消息，当前轮次已结束并切换到下一轮",
                                status="interrupted",
                                metadata={"reason": "followup"},
                            ),
                            round_number=round_number,
                        )
                    if not session_id:
                        self._finish(
                            task,
                            "failed",
                            "Codex 会话未返回 ID，无法继续对话",
                            result.output,
                        )
                        return
                    with self._condition:
                        task.prompt = pending
                        task.config = replace(
                            task.config,
                            resume_session_id=session_id,
                            fork_session=False,
                        )
                        task.cancel_event = threading.Event()
                        task.stop_reason = ""
                        task.snapshot = _replace_snapshot(
                            task.snapshot,
                            status="running",
                            error="",
                            output="",
                            finished_at=None,
                            pending_status="processing",
                            pending_started_at=datetime.now(timezone.utc),
                            process_id=0,
                            process_alive=False,
                        )
                        self._persist_tasks_locked()
                    continue

                if reason == "shutdown":
                    return

                if reason == "followup_canceled" and (
                    result.error
                    or result.exit_code != 0
                    or (current_cancel is not None and current_cancel.is_set())
                ):
                    if not session_id:
                        self._finish(
                            task,
                            "failed",
                            "待处理消息取消时当前轮次已停止，且会话 ID 不可用",
                            result.output,
                        )
                        return
                    with self._condition:
                        self._append_event_locked(
                            task,
                            Event(
                                datetime.now(timezone.utc),
                                "round.interrupted",
                                "待处理消息已取消；正在恢复被提前结束的原轮次",
                                status="interrupted",
                                metadata={"reason": "followup_canceled"},
                            ),
                            round_number=round_number,
                        )
                        task.prompt = CONTINUE_MESSAGE
                        task.config = replace(
                            task.config,
                            resume_session_id=session_id,
                            fork_session=False,
                        )
                        task.cancel_event = threading.Event()
                        task.stop_reason = ""
                        task.snapshot = _replace_snapshot(
                            task.snapshot,
                            status="running",
                            error="",
                            output="",
                            finished_at=None,
                            process_id=0,
                            process_alive=False,
                        )
                        self._persist_tasks_locked()
                    continue

                if reason == "user" or (
                    current_cancel is not None and current_cancel.is_set()
                ):
                    with self._condition:
                        self._append_event_locked(
                            task,
                            Event(
                                datetime.now(timezone.utc),
                                "round.canceled",
                                "本轮已由用户停止",
                                status="canceled",
                            ),
                            round_number=round_number,
                        )
                    self._finish(task, "canceled", "canceled", result.output)
                    return
                if result.error or result.exit_code != 0:
                    with self._condition:
                        self._append_event_locked(
                            task,
                            Event(
                                datetime.now(timezone.utc),
                                "round.failed",
                                result.error
                                or f"Codex exited with status {result.exit_code}",
                                status="failed",
                                exit_code=result.exit_code,
                            ),
                            round_number=round_number,
                        )
                    self._finish(
                        task,
                        "failed",
                        result.error or f"Codex exited with status {result.exit_code}",
                        result.output,
                    )
                    return
                with self._condition:
                    self._append_event_locked(
                        task,
                        Event(
                            datetime.now(timezone.utc),
                            "round.completed",
                            f"第 {round_number} 轮处理完成",
                            status="completed",
                        ),
                        round_number=round_number,
                    )
                self._write_completed(task, result.output)
                self._finish(task, "completed", "", result.output)
                return
        except Exception as exc:
            self._finish(task, "failed", str(exc), "")

    def _write_completed(self, task: _Task, output: str) -> None:
        output = Redactor(self.config.process.api_key).apply(output.strip())
        if not output:
            raise CodexOutputMissing("codex final output is missing")
        if task.snapshot.writeup_path:
            write_atomic(
                task.snapshot.writeup_path,
                self.workspace_root,
                output.encode("utf-8"),
                0o600,
            )

    def _finish(self, task: _Task, status: str, error: str, output: str) -> None:
        with self._condition:
            if task.stop_reason == "shutdown":
                self._persist_tasks_locked()
                self._condition.notify_all()
                return
            now = datetime.now(timezone.utc)
            task.snapshot = _replace_snapshot(
                task.snapshot,
                status=status,
                finished_at=now,
                output=Redactor(self.config.process.api_key).apply(output.strip()),
                error=(
                    Redactor(self.config.process.api_key).apply(error) if error else ""
                ),
                pending_message="",
                pending_status="",
                pending_created_at=None,
                pending_started_at=None,
                process_id=0,
                process_alive=False,
            )
            task.pending_followup = ""
            task.stop_reason = ""
            self._persist_tasks_locked()
            self._condition.notify_all()

    def _events_path(self, task_id: str) -> Path:
        root = self.runs_root
        if not root.is_absolute():
            root = self.workspace_root / root
        return root / task_id / "events.jsonl"

    def _append_event_locked(
        self, task: _Task, event: Event, round_number: int = 0
    ) -> Event:
        now = event.at.astimezone(timezone.utc)
        status = event.status
        started_at = event.started_at
        finished_at = event.finished_at
        duration_seconds = event.duration_seconds
        if event.kind == "command_execution" and status in {
            "completed",
            "failed",
            "canceled",
        }:
            matching = next(
                (
                    previous
                    for previous in reversed(task.snapshot.events)
                    if previous.kind == "command_execution"
                    and (
                        (event.item_id and previous.item_id == event.item_id)
                        or (
                            not event.item_id
                            and previous.command == event.command
                            and previous.status in {"in_progress", "running", "started"}
                        )
                    )
                    and previous.status in {"in_progress", "running", "started"}
                ),
                None,
            )
            if matching is not None:
                started_at = matching.started_at or matching.at
                finished_at = finished_at or now
                duration_seconds = max(0.0, (finished_at - started_at).total_seconds())
        sequence = max(1, task.snapshot.next_event_sequence)
        normalized = replace(
            event,
            at=now,
            sequence=sequence,
            round_number=round_number or event.round_number,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration_seconds,
        )
        path = self._events_path(task.snapshot.id)
        persisted = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        normalized.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            persisted = True
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            LOGGER.warning(
                "无法追加 Codex 任务日志：%s", task.snapshot.id, exc_info=True
            )
        events = (*task.snapshot.events, normalized)[-MAX_TASK_EVENTS:]
        changes: dict[str, Any] = {
            "events": tuple(events),
            "next_event_sequence": sequence + 1,
            "session_id": normalized.session_id or task.snapshot.session_id,
        }
        if persisted:
            changes.update(
                log_count=task.snapshot.log_count + 1,
                first_event_at=task.snapshot.first_event_at or normalized.at,
                last_event_at=normalized.at,
            )
        if normalized.kind == "turn.completed" and normalized.usage is not None:
            changes["usage"] = task.snapshot.usage + normalized.usage
            changes["completed_turns"] = task.snapshot.completed_turns + 1
        if normalized.kind == "process.started":
            raw_pid = normalized.metadata.get("pid", 0)
            pid = raw_pid if isinstance(raw_pid, int) and raw_pid > 0 else 0
            changes.update(
                process_id=pid,
                process_alive=pid > 0,
                process_started_at=normalized.at,
            )
        elif normalized.kind == "process.completed":
            changes.update(process_alive=False, process_id=0)
        task.snapshot = _replace_snapshot(task.snapshot, **changes)
        self._publish(
            "codex.event",
            task.snapshot.id,
            {"taskId": task.snapshot.id, "event": normalized.to_dict()},
        )
        return normalized

    def _hydrate_task_events(self, task: _Task) -> None:
        path = self._events_path(task.snapshot.id)
        persisted = list(read_events(path))
        if not persisted:
            persisted = list(task.snapshot.events)
        normalized: list[Event] = []
        next_sequence = 1
        rewrite = not path.exists()
        for event in persisted:
            sequence = event.sequence
            if sequence < next_sequence:
                sequence = next_sequence
                rewrite = True
            normalized.append(replace(event, sequence=sequence))
            next_sequence = sequence + 1
        if normalized and rewrite:
            write_events(path, normalized)
        recent = tuple(normalized[-MAX_TASK_EVENTS:])
        aggregate_usage, completed_turns = _aggregate_event_usage(normalized)
        task.snapshot = _replace_snapshot(
            task.snapshot,
            events=recent,
            log_count=len(normalized),
            first_event_at=normalized[0].at if normalized else None,
            last_event_at=normalized[-1].at if normalized else None,
            next_event_sequence=next_sequence,
            usage=(aggregate_usage if completed_turns > 0 else task.snapshot.usage),
            completed_turns=(
                completed_turns
                if completed_turns > 0
                else task.snapshot.completed_turns
            ),
        )

    def _load_tasks(self) -> None:
        """Restore task metadata and convert stale running tasks to interrupted."""
        try:
            raw = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, Mapping):
            return
        entries = raw.get("tasks")
        if not isinstance(entries, list):
            return
        restored: list[_Task] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                task = self._task_from_dict(entry)
                self._hydrate_task_events(task)
            except (TypeError, ValueError, OSError):
                LOGGER.warning("忽略无法恢复的 Codex 任务元数据", exc_info=True)
                continue
            restored.append(task)
        restored = restored[-MAX_TASK_HISTORY:]
        with self._condition:
            for task in restored:
                task_id = task.snapshot.id
                self._tasks[task_id] = task
                self._order.append(task_id)
                if task.stop_reason == "startup_interrupted":
                    self._append_event_locked(
                        task,
                        Event(
                            task.snapshot.interrupted_at or datetime.now(timezone.utc),
                            "task.interrupted",
                            task.snapshot.error,
                            status="interrupted",
                            metadata={
                                "reason": "startup",
                                "processAlive": task.snapshot.process_alive,
                            },
                        ),
                        round_number=max(1, task.snapshot.current_round),
                    )
                    task.stop_reason = ""
            self._refresh_queue_positions()
            self._persist_tasks_locked()

    def _task_from_dict(self, entry: Mapping[str, Any]) -> _Task:
        raw_snapshot = entry.get("snapshot")
        if not isinstance(raw_snapshot, Mapping):
            raw_snapshot = entry
        snapshot = _snapshot_from_dict(raw_snapshot)
        startup_interrupted = snapshot.status == "running"
        if startup_interrupted:
            now = datetime.now(timezone.utc)
            process_alive = _pid_exists(snapshot.process_id)
            snapshot = _replace_snapshot(
                snapshot,
                status="interrupted",
                error=(
                    "程序重启后无法继续接管仍在运行的 Codex 子进程"
                    if process_alive
                    else "程序异常退出，原 Codex 子进程已经消失"
                ),
                interrupted_at=now,
                interruption_count=snapshot.interruption_count + 1,
                active=0,
                queue_position=0,
                process_alive=process_alive,
            )
        raw_prompt = entry.get("prompt", "")
        prompt = str(raw_prompt) if raw_prompt is not None else ""
        raw_config = entry.get("config")
        config_data = raw_config if isinstance(raw_config, Mapping) else {}
        current = self.config.process
        output_path = str(config_data.get("outputPath", "") or "")
        if not output_path:
            output_path = str(self.runs_root / snapshot.id / "final.md")
        restored_config = ProcessConfig(
            binary=current.binary,
            workspace=current.workspace,
            base_url=str(config_data.get("baseUrl", "") or current.base_url),
            api_key=current.api_key,
            model=str(config_data.get("model", "") or current.model),
            output_path=output_path,
            home=current.home,
            resume_session_id=str(
                config_data.get("resumeSessionId", "") or snapshot.session_id
            ),
            fork_session=bool(config_data.get("forkSession", False)),
        )
        pending_followup = str(entry.get("pendingFollowup", "") or "")
        stop_reason = (
            "startup_interrupted"
            if startup_interrupted
            else str(entry.get("stopReason", "") or "")
        )
        return _Task(
            snapshot,
            prompt,
            restored_config,
            pending_followup=pending_followup,
            stop_reason=stop_reason,
        )

    def _persist_tasks_locked(self) -> None:
        """Atomically persist metadata; API keys and full events are omitted."""
        payload = {
            "version": 2,
            "tasks": [
                self._task_to_dict(self._tasks[task_id])
                for task_id in self._order
                if task_id in self._tasks
            ],
        }
        try:
            self.tasks_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{self.tasks_path.name}.", dir=str(self.tasks_path.parent)
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(
                        payload,
                        handle,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    handle.write("\n")
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.tasks_path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("无法保存 Codex 任务元数据", exc_info=True)
        self._publish_changed_tasks_locked()

    def _publish_changed_tasks_locked(self) -> None:
        for task_id in self._order:
            task = self._tasks.get(task_id)
            if task is None:
                continue
            wire = task.snapshot.to_dict(include_events=False)
            signature = json.dumps(
                wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            if self._published_task_signatures.get(task_id) == signature:
                continue
            self._published_task_signatures[task_id] = signature
            self._publish("codex.task", task_id, wire)

    def _publish(
        self, event_type: str, resource_id: str, data: Mapping[str, Any]
    ) -> None:
        if self._event_publisher is not None:
            self._event_publisher(event_type, resource_id, data)

    @staticmethod
    def _task_to_dict(task: _Task) -> dict[str, Any]:
        config = task.config
        return {
            "snapshot": task.snapshot.to_dict(include_events=False),
            "prompt": task.prompt,
            "pendingFollowup": task.pending_followup,
            "stopReason": task.stop_reason,
            "config": {
                "workspace": config.workspace,
                "baseUrl": config.base_url,
                "model": config.model,
                "outputPath": config.output_path,
                "home": config.home,
                "resumeSessionId": config.resume_session_id,
                "forkSession": config.fork_session,
            },
        }

    def _snapshot(self, task: _Task, active: int | None = None) -> Snapshot:
        current_active = (
            active
            if active is not None
            else (self._active_count() if task.snapshot.status == "running" else 0)
        )
        process_alive = (
            _pid_exists(task.snapshot.process_id)
            if task.snapshot.process_id > 0
            else False
        )
        if process_alive != task.snapshot.process_alive:
            task.snapshot = _replace_snapshot(
                task.snapshot, process_alive=process_alive
            )
        return _replace_snapshot(
            task.snapshot,
            active=current_active,
            events=tuple(task.snapshot.events),
        )

    def _active_count(self) -> int:
        return sum(task.snapshot.status == "running" for task in self._tasks.values())

    def _refresh_queue_positions(self) -> None:
        position = 0
        for task_id in self._order:
            task = self._tasks.get(task_id)
            if task is not None and task.snapshot.status == "queued":
                position += 1
                task.snapshot = _replace_snapshot(
                    task.snapshot, queue_position=position
                )

    def _trim_history(self) -> None:
        while len(self._order) > MAX_TASK_HISTORY:
            oldest = self._order[0]
            task = self._tasks.get(oldest)
            if task is not None and task.snapshot.status in {
                "queued",
                "running",
                "interrupted",
            }:
                return
            self._tasks.pop(oldest, None)
            self._order.pop(0)


def _invoke_runner(
    runner: object,
    config: ProcessConfig,
    prompt: str,
    on_event: Callable[[Event], None],
    cancel_event: threading.Event | None,
) -> ProcessResult:
    method = getattr(runner, "run")
    try:
        result = method(config, prompt, on_event, cancel_event)
    except TypeError:
        try:
            result = method(config, prompt, on_event)
        except TypeError:
            result = method(config, prompt)
    if isinstance(result, tuple) and len(result) == 2:
        result, runner_error = result
        if isinstance(result, ProcessResult):
            return ProcessResult(
                result.exit_code,
                result.output,
                result.session_id,
                str(runner_error or result.error or ""),
            )
        if runner_error:
            return ProcessResult(-1, error=str(runner_error))
    if isinstance(result, ProcessResult):
        return ProcessResult(
            result.exit_code,
            result.output,
            result.session_id,
            str(result.error) if result.error else "",
        )
    if isinstance(result, Mapping):
        return ProcessResult(
            int(result.get("exit_code", 0)),
            str(result.get("output", "")),
            str(result.get("session_id", "")),
            str(result.get("error", "")),
        )
    return ProcessResult(0, str(result or ""))


def _replace_snapshot(snapshot: Snapshot, **changes: Any) -> Snapshot:
    values = snapshot.__dict__.copy()
    values.update(changes)
    return Snapshot(**values)


def _snapshot_from_dict(raw: Mapping[str, Any]) -> Snapshot:
    task_id = str(raw.get("id", "") or "").strip()
    if len(task_id) != 24 or any(char not in "0123456789abcdef" for char in task_id):
        raise ValueError("invalid persisted Codex task id")
    events: list[Event] = []
    raw_events = raw.get("events")
    if isinstance(raw_events, list):
        for item in raw_events[-MAX_TASK_EVENTS:]:
            if not isinstance(item, Mapping):
                continue
            event = _event_from_dict(item)
            if event is not None:
                events.append(event)
    event_usage, event_completed_turns = _aggregate_event_usage(events)
    raw_usage = raw.get("usage")
    persisted_usage = (
        _token_usage_from_mapping(raw_usage) if isinstance(raw_usage, Mapping) else None
    )
    usage = persisted_usage or event_usage
    completed_turns = _nonnegative_int(raw.get("completedTurns"))
    if completed_turns <= 0:
        completed_turns = event_completed_turns
    created_at = _parse_time(raw.get("createdAt")) or datetime.now(timezone.utc)
    status = str(raw.get("status", "failed") or "failed")
    allowed_statuses = {
        "queued",
        "running",
        "completed",
        "failed",
        "canceled",
        "interrupted",
    }
    if status not in allowed_statuses:
        status = "failed"
    raw_pending = raw.get("pendingMessage")
    pending = raw_pending if isinstance(raw_pending, Mapping) else {}
    process_id = _nonnegative_int(raw.get("processId"))
    log_count = _nonnegative_int(raw.get("logCount")) or len(events)
    next_sequence = _nonnegative_int(raw.get("nextEventSequence"))
    if next_sequence <= 0:
        next_sequence = max((event.sequence for event in events), default=0) + 1
    return Snapshot(
        id=task_id,
        exercise_id=int(raw.get("exerciseId", 0) or 0),
        mode=str(raw.get("mode", "full") or "full"),
        status=status,
        queue_position=int(raw.get("queuePosition", 0) or 0),
        active=int(raw.get("active", 0) or 0),
        limit=int(raw.get("limit", 0) or 0),
        error=str(raw.get("error", "") or ""),
        output=str(raw.get("output", "") or ""),
        writeup_path=str(raw.get("writeupPath", "") or ""),
        challenge_path=str(raw.get("challengePath", "") or ""),
        events=tuple(events),
        session_id=str(raw.get("sessionId", "") or ""),
        created_at=created_at,
        started_at=_parse_time(raw.get("startedAt")),
        finished_at=_parse_time(raw.get("finishedAt")),
        parent_id=str(raw.get("parentTaskId", "") or ""),
        usage=usage,
        completed_turns=completed_turns,
        title=str(raw.get("title", "") or ""),
        source_message=str(raw.get("sourceMessage", "") or ""),
        log_count=log_count,
        first_event_at=_parse_time(raw.get("firstEventAt")),
        last_event_at=_parse_time(raw.get("lastEventAt")),
        next_event_sequence=next_sequence,
        current_round=_nonnegative_int(raw.get("currentRound")),
        rounds_started=_nonnegative_int(raw.get("roundsStarted")),
        pending_message=str(pending.get("message", "") or ""),
        pending_status=str(pending.get("status", "") or ""),
        pending_created_at=_parse_time(pending.get("createdAt")),
        pending_started_at=_parse_time(pending.get("startedAt")),
        interrupted_at=_parse_time(raw.get("interruptedAt")),
        interruption_count=_nonnegative_int(raw.get("interruptionCount")),
        last_resumed_at=_parse_time(raw.get("lastResumedAt")),
        process_id=process_id,
        process_alive=bool(raw.get("processAlive", False)),
        process_started_at=_parse_time(raw.get("processStartedAt")),
    )


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def writeup_path_for(
    workspace_root: Path,
    root: Path,
    prompt: PromptResult,
    exercise_id: int,
    task_id: str,
    mode: str,
) -> str:
    category = safe_path_part(prompt.category, "Uncategorized")
    name = safe_path_part(prompt.exercise_name, f"exercise-{exercise_id}")
    marker = "codex-pure" if mode == "pure" else "codex"
    absolute = root / category / f"{exercise_id}-{name}-{marker}-{task_id}.md"
    try:
        return absolute.relative_to(workspace_root).as_posix()
    except ValueError:
        return absolute.as_posix()


def challenge_path_for(
    workspace_root: Path, prompt: PromptResult, exercise_id: int
) -> str:
    if prompt.attachments:
        attachment_path = Path(prompt.attachments[0].path)
        directory = attachment_path.parent
        absolute = directory if directory.is_absolute() else workspace_root / directory
    else:
        category = safe_writeup_segment(prompt.category, "Uncategorized")
        name = safe_writeup_segment(prompt.exercise_name, "exercise")
        absolute = workspace_root / "download" / category / f"{exercise_id}-{name}"
    try:
        return (
            absolute.resolve(strict=False)
            .relative_to(workspace_root.resolve())
            .as_posix()
        )
    except ValueError:
        return absolute.resolve(strict=False).as_posix()


def challenge_path_from_writeup(writeup_path: str, task_id: str, mode: str) -> str:
    """Recover pre-migration task folders from their trusted writeup names."""
    path = Path(writeup_path)
    if len(path.parts) < 3 or path.parts[0] != "writeups":
        return ""
    marker = "codex-pure" if mode == "pure" else "codex"
    suffix = f"-{marker}-{task_id}.md"
    if not path.name.endswith(suffix):
        return ""
    exercise_directory = path.name[: -len(suffix)]
    if not exercise_directory:
        return ""
    return (Path("download") / path.parts[1] / exercise_directory).as_posix()


def safe_path_part(value: str, fallback: str) -> str:
    value = value.strip()
    chars = [
        char if (char.isascii() and (char.isalnum() or char in "-_.")) else "_"
        for char in value
    ]
    result = "".join(chars).strip("._")
    return result or fallback


def _task_message(value: str) -> str:
    message = value.strip()
    if not message:
        raise ValueError("Codex 消息不能为空")
    if len(message) > 16_000:
        raise ValueError("Codex 消息不能超过 16000 个字符")
    return message


def write_atomic(
    relative_or_absolute: str, workspace_root: Path, data: bytes, mode: int = 0o600
) -> None:
    path = Path(relative_or_absolute)
    if not path.is_absolute():
        path = workspace_root / path
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=".codex-part-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _event_from_dict(raw: Mapping[str, Any]) -> Event | None:
    at = _parse_time(raw.get("at"))
    if at is None:
        return None
    raw_usage = raw.get("usage")
    usage = (
        _token_usage_from_mapping(raw_usage) if isinstance(raw_usage, Mapping) else None
    )
    kind = str(raw.get("kind", "event") or "event")
    summary = str(raw.get("summary", "") or "")
    session_id = str(raw.get("sessionId", "") or "")
    legacy_event = parse_event_line(summary, at)
    if legacy_event.kind == kind and legacy_event.summary != summary:
        summary = legacy_event.summary
        session_id = session_id or legacy_event.session_id
        usage = usage or legacy_event.usage
    if usage is None:
        usage = _token_usage_from_summary(summary)
    raw_exit_code = raw.get("exitCode")
    exit_code: int | None = None
    if raw_exit_code is not None and not isinstance(raw_exit_code, bool):
        try:
            exit_code = int(raw_exit_code)
        except (TypeError, ValueError):
            exit_code = None
    raw_metadata = raw.get("metadata")
    metadata: Mapping[str, Any] = (
        dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    )
    repeat_count = max(1, _nonnegative_int(raw.get("repeatCount")))
    raw_duration = raw.get("durationSeconds")
    duration_seconds: float | None = None
    if raw_duration is not None and not isinstance(raw_duration, bool):
        try:
            duration_seconds = max(0.0, float(raw_duration))
        except (TypeError, ValueError):
            duration_seconds = None
    return Event(
        at=at,
        kind=kind,
        summary=summary,
        session_id=session_id,
        usage=usage,
        sequence=_nonnegative_int(raw.get("sequence")),
        round_number=_nonnegative_int(raw.get("roundNumber")),
        envelope_type=str(raw.get("envelopeType", "") or ""),
        item_id=str(raw.get("itemId", "") or ""),
        status=str(raw.get("status", "") or ""),
        command=str(raw.get("command", "") or ""),
        output=str(raw.get("output", "") or ""),
        exit_code=exit_code,
        started_at=_parse_time(raw.get("startedAt")),
        finished_at=_parse_time(raw.get("finishedAt")),
        duration_seconds=duration_seconds,
        repeat_count=repeat_count,
        metadata=metadata,
    )


def read_events(path: Path) -> Iterable[Event]:
    try:
        handle = path.open("r", encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ()

    def iterator() -> Iterable[Event]:
        with handle:
            for line in handle:
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, Mapping):
                    continue
                event = _event_from_dict(raw)
                if event is not None:
                    yield event

    return iterator()


def _message_summary(message: str, max_length: int = 80) -> str:
    collapsed = " ".join(message.strip().split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[: max(1, max_length - 1)].rstrip() + "…"


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def write_events(path: Path, events: Iterable[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=".events-part-", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for event in events:
                handle.write(
                    json.dumps(
                        event.to_dict(), ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n"
                )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _time_wire(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


CODEX_INSTRUCTIONS = DEFAULT_CODEX_SYSTEM_PROMPT

CODEX_PURE_INSTRUCTIONS = """你是通过本机 Codex CLI 启动的受限 CTF 解题代理。请只处理这一个题目。
这是纯解题模式：禁止启动或回收容器/靶机，禁止阅读任何文档或配置文件（包括 docs/api_doc.md、.env、runtime/config.json、.runtime-config.json），禁止调用竞赛 API，禁止提交 Flag。
只根据任务中给出的题目说明和本地已下载附件进行分析，允许执行本地命令处理附件；需要补充背景知识时可以搜索公开资料，但不要把未经验证的推测当成结论。禁止爆破、穷举、猜测或伪造 Flag；不要攻击任何竞赛平台、登录接口、比赛接口或网关，也不要修改与本题无关的项目文件。
最终回复必须是 Flag 值加上一份可复现的中文 Markdown WP，包含实际命令、利用过程、Flag 推导和结果说明；不要暴露任何凭据，不要编造 Flag。"""


Manager = CodexManager
NewManager = CodexManager
NewProcessRunner = ProcessRunner
