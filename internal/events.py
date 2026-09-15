"""Thread-safe real-time event broker used by the browser SSE stream.

The worker managers in this project run in background threads while FastAPI
serves subscribers on asyncio event loops.  ``EventBroker`` deliberately keeps
those concerns separate: publishers never block on a slow browser, each
subscriber owns a bounded queue, and a short replay window supports
``Last-Event-ID`` reconnects.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, TypeAlias

EventData: TypeAlias = Mapping[str, Any]
EventPublisher: TypeAlias = Callable[[str, str, EventData], None]


@dataclass(frozen=True)
class RealtimeEvent:
    """One wire event with a process-wide monotonically increasing ID."""

    id: int
    type: str
    resource_id: str
    time: str
    data: EventData

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "resourceId": self.resource_id,
            "time": self.time,
            "data": dict(self.data),
        }


@dataclass
class EventSubscription:
    """Replay state and live queue owned by one SSE request."""

    token: str
    queue: asyncio.Queue[RealtimeEvent]
    replay: tuple[RealtimeEvent, ...]
    reset_required: bool
    _close: Callable[[str], None]

    def close(self) -> None:
        self._close(self.token)


@dataclass(frozen=True)
class _Subscriber:
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[RealtimeEvent]


class EventBroker:
    """Publish structured events to bounded SSE subscribers.

    IDs start from the current Unix epoch in milliseconds multiplied by 1000.
    This keeps IDs increasing after ordinary service restarts as well as within
    one process, while leaving room for many events in the same millisecond.
    """

    def __init__(self, replay_limit: int = 4096, subscriber_limit: int = 512) -> None:
        self._replay_limit = max(16, replay_limit)
        self._subscriber_limit = max(8, subscriber_limit)
        self._events: deque[RealtimeEvent] = deque(maxlen=self._replay_limit)
        self._subscribers: dict[str, _Subscriber] = {}
        self._lock = threading.RLock()
        self._next_id = int(time.time_ns() // 1_000_000) * 1000

    def publish(self, event_type: str, resource_id: str, data: EventData) -> None:
        """Publish without waiting for any browser subscriber."""

        event = self._new_event(event_type, resource_id, data)
        with self._lock:
            self._events.append(event)
            subscribers = tuple(self._subscribers.values())
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(
                    self._offer, subscriber.queue, event
                )
            except RuntimeError:
                # The request loop closed between copying the subscriber list
                # and scheduling delivery.  The request cleanup removes it.
                continue

    def subscribe(
        self,
        last_event_id: int = 0,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> EventSubscription:
        """Register one subscriber and calculate its reconnect replay."""

        selected_loop = loop or asyncio.get_running_loop()
        queue: asyncio.Queue[RealtimeEvent] = asyncio.Queue(
            maxsize=self._subscriber_limit
        )
        token = secrets.token_hex(16)
        with self._lock:
            events = tuple(self._events)
            oldest = events[0].id if events else 0
            latest = events[-1].id if events else 0
            reset_required = bool(
                last_event_id > 0
                and (
                    (oldest > 0 and last_event_id < oldest - 1)
                    or (latest > 0 and last_event_id > latest)
                    or (latest == 0 and last_event_id > 0)
                )
            )
            replay = (
                ()
                if last_event_id <= 0 or reset_required
                else tuple(event for event in events if event.id > last_event_id)
            )
            self._subscribers[token] = _Subscriber(selected_loop, queue)
        return EventSubscription(
            token=token,
            queue=queue,
            replay=replay,
            reset_required=reset_required,
            _close=self.unsubscribe,
        )

    def unsubscribe(self, token: str) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def control_event(
        self, event_type: str, resource_id: str = "", data: EventData | None = None
    ) -> RealtimeEvent:
        """Create a connection-local event without broadcasting it."""

        return self._new_event(event_type, resource_id, data or {})

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @property
    def latest_id(self) -> int:
        with self._lock:
            return self._next_id

    def _new_event(
        self, event_type: str, resource_id: str, data: EventData
    ) -> RealtimeEvent:
        normalized_type = event_type.strip() or "message"
        normalized_resource = resource_id.strip()
        with self._lock:
            now_seed = int(time.time_ns() // 1_000_000) * 1000
            self._next_id = max(self._next_id + 1, now_seed)
            event_id = self._next_id
        return RealtimeEvent(
            id=event_id,
            type=normalized_type,
            resource_id=normalized_resource,
            time=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            data=dict(data),
        )

    @staticmethod
    def _offer(queue: asyncio.Queue[RealtimeEvent], event: RealtimeEvent) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # A pathological consumer can race the bounded-queue check; the
            # next task snapshot or reconnect replay will reconcile its state.
            pass
