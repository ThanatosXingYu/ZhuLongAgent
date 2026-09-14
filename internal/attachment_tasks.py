"""Background attachment downloads and remote size discovery."""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Literal

from .download import CatalogResult, DownloadService

DownloadScope = Literal["all", "category", "exercise", "single", "redownload"]
TaskStatus = Literal[
    "running",
    "paused",
    "cancelling",
    "cancelled",
    "completed",
    "completed_with_errors",
    "failed",
]
ProbeStatus = Literal["idle", "running", "completed", "completed_with_errors", "failed"]


class DownloadTaskBusy(RuntimeError):
    """Raised when a second download is requested while one is active."""


class DownloadTaskNotFound(RuntimeError):
    """Raised when a requested attachment task does not exist."""


class _DownloadCancelled(RuntimeError):
    """Stop the worker without treating a user cancellation as a failure."""


@dataclass(frozen=True)
class _Target:
    exercise_id: int
    index: int
    name: str
    size: int


@dataclass
class _DownloadState:
    id: str
    scope: DownloadScope
    label: str
    status: TaskStatus = "running"
    total_files: int = 0
    completed_files: int = 0
    downloaded_files: int = 0
    existing_files: int = 0
    failed_files: int = 0
    total_bytes: int = 0
    downloaded_bytes: int = 0
    discarded_bytes: int = 0
    current_name: str = ""
    current_bytes: int = 0
    current_total_bytes: int = 0
    error: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    started_monotonic: float = 0.0
    finished_monotonic: float = 0.0
    pause_started_monotonic: float = 0.0
    paused_seconds: float = 0.0
    pause_requested: bool = False
    cancel_requested: bool = False


@dataclass
class _ProbeState:
    status: ProbeStatus = "idle"
    total_files: int = 0
    completed_files: int = 0
    resolved_files: int = 0
    failed_files: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0
    error: str = ""


class AttachmentTaskManager:
    """Run long attachment work outside request handlers and expose snapshots."""

    def __init__(self, downloads: DownloadService, probe_workers: int = 4) -> None:
        self._downloads = downloads
        self._probe_workers = max(1, min(int(probe_workers), 8))
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._active: _DownloadState | None = None
        self._history: dict[str, _DownloadState] = {}
        self._probe = _ProbeState()
        self._probe_generation = 0

    def start_all(self) -> dict[str, Any]:
        return self._start("all", "全部缺失附件")

    def start_exercise(self, exercise_id: int) -> dict[str, Any]:
        return self._start("exercise", f"题目 #{exercise_id}", exercise_id)

    def start_category(self, category: str) -> dict[str, Any]:
        normalized = category.strip()
        if not normalized:
            raise ValueError("附件类别不能为空")
        return self._start("category", f"{normalized} 类别附件", category=normalized)

    def start_single(self, exercise_id: int, index: int) -> dict[str, Any]:
        return self._start("single", f"题目 #{exercise_id} 附件", exercise_id, index)

    def start_redownload(self, exercise_id: int) -> dict[str, Any]:
        return self._start("redownload", f"题目 #{exercise_id} 重新下载", exercise_id)

    def get(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            state = self._history.get(task_id)
            if state is None:
                raise DownloadTaskNotFound("attachment task not found")
            return self._download_snapshot(state)

    def active(self) -> dict[str, Any] | None:
        with self._lock:
            if self._active is None:
                return None
            return self._download_snapshot(self._active)

    def pause(self, task_id: str) -> dict[str, Any]:
        with self._condition:
            state = self._task(task_id)
            if state.status == "running":
                state.pause_requested = True
                state.status = "paused"
                state.pause_started_monotonic = time.monotonic()
                state.updated_at = time.time()
            return self._download_snapshot(state)

    def resume(self, task_id: str) -> dict[str, Any]:
        with self._condition:
            state = self._task(task_id)
            if state.status == "paused":
                now = time.monotonic()
                state.paused_seconds += max(0.0, now - state.pause_started_monotonic)
                state.pause_started_monotonic = 0.0
                state.pause_requested = False
                state.status = "running"
                state.updated_at = time.time()
                self._condition.notify_all()
            return self._download_snapshot(state)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._condition:
            state = self._task(task_id)
            if state.status in {"running", "paused"}:
                if state.pause_started_monotonic > 0:
                    state.paused_seconds += max(
                        0.0, time.monotonic() - state.pause_started_monotonic
                    )
                state.pause_started_monotonic = 0.0
                state.pause_requested = False
                state.cancel_requested = True
                state.status = "cancelling"
                state.updated_at = time.time()
                self._condition.notify_all()
            return self._download_snapshot(state)

    def start_probe(self, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._probe.status == "running":
                return self._probe_snapshot()
            if not force and self._probe.status in {
                "completed",
                "completed_with_errors",
            }:
                return self._probe_snapshot()
            now = time.time()
            self._probe_generation += 1
            generation = self._probe_generation
            self._probe = _ProbeState(status="running", started_at=now, updated_at=now)
            threading.Thread(
                target=self._run_probe,
                args=(generation,),
                name="attachment-size-probe",
                daemon=True,
            ).start()
            return self._probe_snapshot()

    def probe_status(self) -> dict[str, Any]:
        with self._lock:
            return self._probe_snapshot()

    def reset_probe(self) -> None:
        with self._lock:
            self._probe_generation += 1
            self._probe = _ProbeState()

    def _start(
        self,
        scope: DownloadScope,
        label: str,
        exercise_id: int | None = None,
        index: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._active is not None:
                raise DownloadTaskBusy("attachment download already running")
            now = time.time()
            state = _DownloadState(
                id=uuid.uuid4().hex,
                scope=scope,
                label=label,
                started_at=now,
                updated_at=now,
                started_monotonic=time.monotonic(),
            )
            self._active = state
            self._history[state.id] = state
            self._trim_history()
            threading.Thread(
                target=self._run_download,
                args=(state, exercise_id, index, category),
                name=f"attachment-download-{state.id[:8]}",
                daemon=True,
            ).start()
            return self._download_snapshot(state)

    def _run_download(
        self,
        state: _DownloadState,
        exercise_id: int | None,
        index: int | None,
        category: str | None,
    ) -> None:
        try:
            if state.scope == "redownload" and exercise_id is not None:
                self._downloads.clear_exercise(exercise_id)
            targets = self._download_targets(state.scope, exercise_id, index, category)
            with self._lock:
                state.total_files = len(targets)
                state.total_bytes = sum(
                    target.size for target in targets if target.size > 0
                )
                state.updated_at = time.time()
                state.started_monotonic = time.monotonic()
                if state.pause_requested:
                    state.pause_started_monotonic = state.started_monotonic
            for target in targets:
                self._wait_for_download(state)
                self._download_target(state, target)
            self._wait_for_download(state)
            with self._lock:
                state.status = (
                    "completed_with_errors" if state.failed_files else "completed"
                )
                state.current_name = ""
                state.current_bytes = 0
                state.current_total_bytes = 0
                state.updated_at = time.time()
                state.finished_monotonic = time.monotonic()
        except _DownloadCancelled:
            with self._lock:
                state.status = "cancelled"
                state.current_name = ""
                state.current_bytes = 0
                state.current_total_bytes = 0
                state.updated_at = time.time()
                state.finished_monotonic = time.monotonic()
        except Exception as exc:
            with self._lock:
                state.status = "failed"
                state.error = str(exc) or type(exc).__name__
                state.updated_at = time.time()
                state.finished_monotonic = time.monotonic()
        finally:
            with self._lock:
                if self._active is state:
                    self._active = None

    def _download_target(self, state: _DownloadState, target: _Target) -> None:
        with self._lock:
            state.current_name = target.name
            state.current_bytes = 0
            state.current_total_bytes = target.size
            state.updated_at = time.time()

        def progress(received: int, total: int) -> None:
            with self._lock:
                state.current_bytes = max(0, received)
                if total >= 0:
                    if state.current_total_bytes <= 0:
                        state.total_bytes += total
                    state.current_total_bytes = total
                state.updated_at = time.time()

        completed = False
        try:
            result = self._downloads.download(
                target.exercise_id,
                target.index,
                progress=progress,
                control=lambda: self._wait_for_download(state),
            )
        except _DownloadCancelled:
            raise
        except Exception:
            with self._lock:
                state.failed_files += 1
                completed = True
        else:
            with self._lock:
                if result.existed:
                    state.existing_files += 1
                else:
                    state.downloaded_files += 1
                    state.downloaded_bytes += result.size
                completed = True
        finally:
            with self._lock:
                if completed:
                    state.completed_files += 1
                else:
                    state.discarded_bytes += state.current_bytes
                state.current_name = ""
                state.current_bytes = 0
                state.current_total_bytes = 0
                state.updated_at = time.time()

    def _task(self, task_id: str) -> _DownloadState:
        state = self._history.get(task_id)
        if state is None:
            raise DownloadTaskNotFound("attachment task not found")
        return state

    def _wait_for_download(self, state: _DownloadState) -> None:
        with self._condition:
            while state.pause_requested and not state.cancel_requested:
                self._condition.wait()
            if state.cancel_requested:
                raise _DownloadCancelled("attachment download cancelled")

    def _download_targets(
        self,
        scope: DownloadScope,
        exercise_id: int | None,
        index: int | None,
        category: str | None,
    ) -> list[_Target]:
        catalog = self._downloads.catalog()
        if exercise_id is not None and not any(
            exercise.id == exercise_id for exercise in catalog.exercises
        ):
            raise DownloadTaskNotFound("exercise not found")
        if category is not None and not any(
            _category_label(exercise.category) == category
            for exercise in catalog.exercises
        ):
            raise DownloadTaskNotFound("category not found")
        targets = self._targets_from_catalog(catalog, exercise_id, category)
        if scope == "single":
            selected = [target for target in targets if target.index == index]
            if not selected:
                raise DownloadTaskNotFound("attachment not found")
            return selected
        if scope in {"all", "category", "exercise"}:
            return [
                target for target in targets if not self._target_exists(catalog, target)
            ]
        return targets

    @staticmethod
    def _targets_from_catalog(
        catalog: CatalogResult, exercise_id: int | None, category: str | None
    ) -> list[_Target]:
        targets: list[_Target] = []
        for exercise in catalog.exercises:
            if exercise_id is not None and exercise.id != exercise_id:
                continue
            if category is not None and _category_label(exercise.category) != category:
                continue
            targets.extend(
                _Target(exercise.id, item.index, item.name, item.size)
                for item in exercise.attachments
                if not item.error
            )
        return targets
    @staticmethod
    def _target_exists(catalog: CatalogResult, target: _Target) -> bool:
        return any(
            item.exists
            for exercise in catalog.exercises
            if exercise.id == target.exercise_id
            for item in exercise.attachments
            if item.index == target.index
        )

    def _run_probe(self, generation: int) -> None:
        try:
            catalog = self._downloads.catalog()
            known, targets = self._probe_targets(catalog)
            with self._lock:
                if generation != self._probe_generation:
                    return
                self._probe.total_files = catalog.total_attachments
                self._probe.completed_files = known
                self._probe.resolved_files = known
                self._probe.updated_at = time.time()
            with ThreadPoolExecutor(
                max_workers=self._probe_workers,
                thread_name_prefix="attachment-size",
            ) as pool:
                futures = {
                    pool.submit(
                        self._downloads.probe_size, target.exercise_id, target.index
                    ): target
                    for target in targets
                }
                for future in as_completed(futures):
                    self._finish_probe(future, generation)
            with self._lock:
                if generation != self._probe_generation:
                    return
                self._probe.status = (
                    "completed_with_errors" if self._probe.failed_files else "completed"
                )
                self._probe.updated_at = time.time()
        except Exception as exc:
            with self._lock:
                if generation != self._probe_generation:
                    return
                self._probe.status = "failed"
                self._probe.error = str(exc) or type(exc).__name__
                self._probe.updated_at = time.time()

    @staticmethod
    def _probe_targets(catalog: CatalogResult) -> tuple[int, list[_Target]]:
        known = 0
        targets: list[_Target] = []
        for exercise in catalog.exercises:
            for item in exercise.attachments:
                if item.error:
                    continue
                if item.size > 0:
                    known += 1
                else:
                    targets.append(_Target(exercise.id, item.index, item.name, 0))
        return known, targets

    def _finish_probe(self, future: Future[int], generation: int) -> None:
        try:
            size = future.result()
        except Exception:
            size = -1
        with self._lock:
            if generation != self._probe_generation:
                return
            self._probe.completed_files += 1
            if size >= 0:
                self._probe.resolved_files += 1
            else:
                self._probe.failed_files += 1
            self._probe.updated_at = time.time()

    def _download_snapshot(self, state: _DownloadState) -> dict[str, Any]:
        endpoint = state.finished_monotonic or time.monotonic()
        if state.status == "paused" and state.pause_started_monotonic > 0:
            endpoint = state.pause_started_monotonic
        elapsed = max(
            0.0, endpoint - state.started_monotonic - state.paused_seconds
        )
        transferred = (
            state.downloaded_bytes + state.discarded_bytes + state.current_bytes
        )
        speed = (
            transferred / elapsed
            if state.status not in {"paused", "cancelling", "cancelled"}
            and elapsed > 0
            and transferred > 0
            else 0.0
        )
        remaining = max(0, state.total_bytes - transferred)
        eta = (
            remaining / speed
            if state.status == "running" and speed > 0 and state.total_bytes > 0
            else None
        )
        return {
            "id": state.id,
            "scope": state.scope,
            "label": state.label,
            "status": state.status,
            "totalFiles": state.total_files,
            "completedFiles": state.completed_files,
            "downloadedFiles": state.downloaded_files,
            "existingFiles": state.existing_files,
            "failedFiles": state.failed_files,
            "totalBytes": state.total_bytes,
            "transferredBytes": transferred,
            "currentName": state.current_name,
            "currentBytes": state.current_bytes,
            "currentTotalBytes": state.current_total_bytes,
            "speedBytesPerSecond": speed,
            "etaSeconds": eta,
            "error": state.error,
            "startedAt": round(state.started_at * 1000),
            "updatedAt": round(state.updated_at * 1000),
        }

    def _probe_snapshot(self) -> dict[str, Any]:
        return {
            "status": self._probe.status,
            "totalFiles": self._probe.total_files,
            "completedFiles": self._probe.completed_files,
            "resolvedFiles": self._probe.resolved_files,
            "failedFiles": self._probe.failed_files,
            "error": self._probe.error,
            "startedAt": round(self._probe.started_at * 1000),
            "updatedAt": round(self._probe.updated_at * 1000),
        }

    def _trim_history(self) -> None:
        while len(self._history) > 20:
            oldest = next(iter(self._history))
            self._history.pop(oldest, None)


Manager = AttachmentTaskManager


def _category_label(value: str) -> str:
    return value.strip() or "未分类"
