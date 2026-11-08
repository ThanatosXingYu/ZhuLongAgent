from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from temp.scripts import benchmark_elfstudy as benchmark


@pytest.mark.parametrize(
    ("statuses", "times", "expected_status", "expected_cancels", "has_error"),
    [
        (["completed"], [], "completed", 0, False),
        (["running", "running", "canceled"], [10, 11], "canceled", 1, False),
        (["running", "running"], [10, 190], "running", 1, True),
    ],
)
def test_wait_for_task_without_model_calls(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[str],
    times: list[int],
    expected_status: str,
    expected_cancels: int,
    has_error: bool,
) -> None:
    snapshots = iter(statuses)
    clock = iter(times)
    canceled: list[str] = []
    manager: Any = SimpleNamespace(
        get=lambda _task_id: SimpleNamespace(status=next(snapshots)),
        cancel=canceled.append,
    )
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(benchmark.time, "sleep", lambda _seconds: None)

    snapshot, error = benchmark.wait_for_task(manager, "task-test", deadline=10)

    assert snapshot.status == expected_status
    assert canceled == ["task-test"] * expected_cancels
    assert bool(error) is has_error
    assert benchmark.ROOT == Path(__file__).resolve().parents[3]
