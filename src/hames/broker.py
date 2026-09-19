"""In-process live event fan-out; the ledger remains durable truth."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import cast


@dataclass
class LiveResponse:
    run_id: str
    after_sequence: int = 0
    text: list[str] = field(default_factory=lambda: list[str]())
    reasoning: list[str] = field(default_factory=lambda: list[str]())


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, object]]]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._live: dict[str, LiveResponse] = {}

    async def publish(self, session_id: str, event: dict[str, object]) -> None:
        async with self._lock:
            self._remember_live(session_id, event)
            subscribers = tuple(self._subscribers.get(session_id, ()))
            self._deliver(subscribers, event)

    def _remember_live(self, session_id: str, envelope: dict[str, object]) -> None:
        event = envelope.get("event")
        if envelope.get("durable") and isinstance(event, dict):
            event = cast(dict[str, object], event)
            kind = event.get("type")
            run_id = event.get("run_id")
            if kind in {"run.started", "model.response.started"} and isinstance(run_id, str):
                self._live[session_id] = LiveResponse(run_id)
            live = self._live.get(session_id)
            if live is None:
                return
            sequence = event.get("sequence")
            if isinstance(sequence, int):
                live.after_sequence = max(live.after_sequence, sequence)
            if run_id != live.run_id:
                return
            if kind == "assistant.message":
                live.text.clear()
            elif kind == "assistant.reasoning":
                live.reasoning.clear()
            elif kind in {"run.completed", "run.failed", "run.cancelled"}:
                self._live.pop(session_id, None)
            return
        kind = envelope.get("type")
        run_id = envelope.get("run_id")
        payload = envelope.get("payload")
        payload = cast(dict[str, object], payload) if isinstance(payload, dict) else {}
        text = payload.get("text")
        if (
            kind not in {"response.text_delta", "response.reasoning_delta"}
            or not isinstance(run_id, str)
            or not isinstance(text, str)
        ):
            return
        live = self._live.get(session_id)
        if live is None or live.run_id != run_id:
            live = self._live[session_id] = LiveResponse(run_id)
        (live.text if kind == "response.text_delta" else live.reasoning).append(text)

    def _snapshot(self, session_id: str) -> dict[str, object]:
        live = self._live.get(session_id)
        return {
            "durable": False,
            "session_id": session_id,
            "run_id": live.run_id if live else "",
            "type": "response.snapshot",
            "payload": {
                "text": "".join(live.text) if live else "",
                "reasoning": "".join(live.reasoning) if live else "",
                "after_sequence": live.after_sequence if live else 0,
            },
        }

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
    async def subscribe(
        self, session_id: str, *, include_snapshot: bool = False
    ) -> AsyncGenerator[asyncio.Queue[dict[str, object]]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1024)
        async with self._lock:
            self._subscribers[session_id].add(queue)
            if include_snapshot:
                queue.put_nowait(self._snapshot(session_id))
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers[session_id].discard(queue)
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]
