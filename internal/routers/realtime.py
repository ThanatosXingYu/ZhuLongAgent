"""Server-Sent Events route for Codex, attachments, and tool installation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..events import EventBroker, RealtimeEvent


def create_realtime_router(event_broker: EventBroker | None) -> APIRouter:
    """Build the stable ``/api/events`` route around an optional broker."""

    router = APIRouter()

    @router.get("/api/events")
    async def realtime_events(request: Request) -> StreamingResponse:
        if event_broker is None:

            async def unavailable() -> AsyncIterator[str]:
                yield _sse_wire(
                    RealtimeEvent(
                        id=0,
                        type="stream.unavailable",
                        resource_id="",
                        time="",
                        data={"reason": "实时事件服务未启用"},
                    )
                )

            return StreamingResponse(
                unavailable(),
                media_type="text/event-stream",
                headers=_sse_headers(),
            )

        subscription = event_broker.subscribe(_last_event_id(request))

        async def iterator() -> AsyncIterator[str]:
            try:
                yield "retry: 2000\n\n"
                if subscription.reset_required:
                    yield _sse_wire(
                        event_broker.control_event(
                            "stream.reset",
                            data={"reason": "replay-window-missed"},
                        )
                    )
                for event in subscription.replay:
                    yield _sse_wire(event)
                yield _sse_wire(
                    event_broker.control_event(
                        "stream.ready",
                        data={"lastEventId": event_broker.latest_id},
                    )
                )
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(
                            subscription.queue.get(), timeout=15.0
                        )
                    except TimeoutError:
                        yield _sse_wire(
                            event_broker.control_event(
                                "stream.heartbeat",
                                data={"lastEventId": event_broker.latest_id},
                            )
                        )
                        continue
                    yield _sse_wire(event)
            except asyncio.CancelledError:
                raise
            finally:
                subscription.close()

        return StreamingResponse(
            iterator(), media_type="text/event-stream", headers=_sse_headers()
        )

    return router


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id", "") or request.query_params.get(
        "lastEventId", ""
    )
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def _sse_wire(event: RealtimeEvent) -> str:
    data = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.id}\nevent: {event.type}\ndata: {data}\n\n"


def _sse_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
