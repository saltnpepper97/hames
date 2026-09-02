"""In-process live event fan-out; the ledger remains durable truth."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, object]]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, event: dict[str, object]) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers.get(session_id, ()))
        self._deliver(subscribers, event)

    async def publish_all(self, event: dict[str, object]) -> None:
        """Publish one transient runtime event to every connected session."""

        async with self._lock:
            subscribers = tuple(
                queue for session_queues in self._subscribers.values() for queue in session_queues
            )
        self._deliver(subscribers, event)

    @staticmethod
    def _deliver(
        subscribers: tuple[asyncio.Queue[dict[str, object]], ...], event: dict[str, object]
    ) -> None:
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Live deltas are best effort and durable events can be replayed.
                # Evict the oldest item so a slow client cannot fail model work
                # and still has a chance to observe the newest terminal event.
                _ = queue.get_nowait()
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncGenerator[asyncio.Queue[dict[str, object]]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers[session_id].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers[session_id].discard(queue)
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]
