from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import defaultdict
from collections.abc import Callable

from backend.app.core.schemas import JsonDict, ReviewEvent

TERMINAL_EVENTS = {"review_completed", "review_failed"}


class InMemoryReviewEventBus:
    def __init__(self, max_events_per_run: int = 200) -> None:
        self.max_events_per_run = max_events_per_run
        self._events: dict[str, list[ReviewEvent]] = defaultdict(list)
        self._lock = threading.RLock()

    def publish(
        self,
        review_run_id: str,
        event_type: str,
        payload: JsonDict | None = None,
    ) -> ReviewEvent:
        with self._lock:
            events = self._events[review_run_id]
            event = ReviewEvent(
                review_run_id=review_run_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload or {},
            )
            events.append(event)
            if len(events) > self.max_events_per_run:
                del events[: len(events) - self.max_events_per_run]
            return event

    def publisher(self, review_run_id: str) -> Callable[[str, JsonDict | None], ReviewEvent]:
        def publish(event_type: str, payload: JsonDict | None = None) -> ReviewEvent:
            return self.publish(review_run_id, event_type, payload)

        return publish

    def snapshot(self, review_run_id: str, after_sequence: int = 0) -> list[ReviewEvent]:
        with self._lock:
            return [
                event
                for event in self._events.get(review_run_id, [])
                if event.sequence > after_sequence
            ]

    def has_run(self, review_run_id: str) -> bool:
        with self._lock:
            return review_run_id in self._events

    async def stream(
        self,
        review_run_id: str,
        after_sequence: int = 0,
        poll_interval_seconds: float = 0.25,
        heartbeat_seconds: float = 15.0,
    ):
        next_sequence = after_sequence + 1
        last_sent_at = time.monotonic()
        while True:
            events = self.snapshot(review_run_id, after_sequence=next_sequence - 1)
            if events:
                for event in events:
                    next_sequence = event.sequence + 1
                    last_sent_at = time.monotonic()
                    yield format_sse_event(event)
                    if event.event_type in TERMINAL_EVENTS:
                        return
            elif time.monotonic() - last_sent_at >= heartbeat_seconds:
                last_sent_at = time.monotonic()
                yield ": keepalive\n\n"

            await asyncio.sleep(poll_interval_seconds)


def format_sse_event(event: ReviewEvent) -> str:
    data = json.dumps(event.to_dict(), ensure_ascii=False)
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
