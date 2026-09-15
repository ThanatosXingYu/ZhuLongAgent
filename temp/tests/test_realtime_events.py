from __future__ import annotations

import asyncio
import threading
from typing import Any, cast

from fastapi.testclient import TestClient

from internal.events import EventBroker
from internal.web import create_app


class _Source:
    def auth_status(self) -> Any:
        return type(
            "Auth",
            (),
            {"to_dict": lambda self: {"authenticated": False}},
        )()


def test_event_ids_are_ordered_and_payloads_are_structured() -> None:
    broker = EventBroker()
    captured = []

    async def scenario() -> None:
        subscription = broker.subscribe()
        try:
            broker.publish("codex.task", "task-a", {"status": "running"})
            broker.publish("codex.event", "task-a", {"sequence": 1})
            captured.append(await asyncio.wait_for(subscription.queue.get(), 1))
            captured.append(await asyncio.wait_for(subscription.queue.get(), 1))
        finally:
            subscription.close()

    asyncio.run(scenario())
    assert [event.type for event in captured] == ["codex.task", "codex.event"]
    assert captured[0].id < captured[1].id
    assert captured[0].to_dict() == {
        "id": captured[0].id,
        "type": "codex.task",
        "resourceId": "task-a",
        "time": captured[0].time,
        "data": {"status": "running"},
    }
    assert broker.subscriber_count == 0


def test_reconnect_replays_only_missing_events_without_duplicates() -> None:
    broker = EventBroker(replay_limit=16)
    broker.publish("codex.event", "task-a", {"sequence": 1})
    first_id = broker.latest_id
    broker.publish("codex.event", "task-a", {"sequence": 2})
    broker.publish("codex.task", "task-a", {"status": "completed"})

    async def scenario() -> tuple[list[int], bool]:
        subscription = broker.subscribe(first_id)
        try:
            return [
                event.id for event in subscription.replay
            ], subscription.reset_required
        finally:
            subscription.close()

    replay_ids, reset_required = asyncio.run(scenario())
    assert reset_required is False
    assert len(replay_ids) == 2
    assert replay_ids == sorted(set(replay_ids))
    assert all(event_id > first_id for event_id in replay_ids)


def test_stale_or_future_last_event_id_requests_state_reset() -> None:
    broker = EventBroker(replay_limit=16)
    for sequence in range(20):
        broker.publish("attachment.task", "download", {"sequence": sequence})

    async def scenario(last_event_id: int) -> bool:
        subscription = broker.subscribe(last_event_id)
        try:
            return subscription.reset_required
        finally:
            subscription.close()

    assert asyncio.run(scenario(1)) is True
    assert asyncio.run(scenario(broker.latest_id + 1000)) is True


def test_concurrent_publishers_keep_unique_monotonic_ids() -> None:
    broker = EventBroker(replay_limit=512, subscriber_limit=512)
    received = []

    async def scenario() -> None:
        subscription = broker.subscribe()
        try:

            def publish_batch(worker: int) -> None:
                for step in range(50):
                    broker.publish("tool.install", f"tool-{worker}", {"step": step})

            threads = [
                threading.Thread(target=publish_batch, args=(worker,))
                for worker in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            for _ in range(200):
                received.append(await asyncio.wait_for(subscription.queue.get(), 1))
        finally:
            subscription.close()

    asyncio.run(scenario())
    ids = [event.id for event in received]
    assert len(ids) == 200
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_sse_route_reports_unavailable_without_a_broker() -> None:
    app = create_app(cast(Any, _Source()))
    with TestClient(app) as client:
        response = client.get("/api/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: stream.unavailable" in response.text
    assert '"type":"stream.unavailable"' in response.text
