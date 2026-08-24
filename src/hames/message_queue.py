"""Durable, bounded per-session foreground message queues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from hames.ledger import Event, Ledger, new_id, utc_now


class QueueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueuedMessage(QueueModel):
    id: str
    session_id: str
    content: str
    remember: bool
    paste_spans: list[dict[str, int]]
    created_at: str
    position: int


class QueueState(QueueModel):
    session_id: str
    paused: bool
    items: list[QueuedMessage]


@dataclass(frozen=True, slots=True)
class QueueMutation:
    state: QueueState
    event: Event
    item: QueuedMessage | None = None


class QueueFullError(ValueError):
    pass


class MessageQueueStore:
    MAX_PENDING = 2

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.database = ledger.database

    def state(self, session_id: str) -> QueueState:
        self.ledger.get_session(session_id)
        with self.database.connect() as connection:
            paused_row = connection.execute(
                "SELECT paused FROM session_queue_state WHERE session_id = ?", (session_id,)
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM session_queue WHERE session_id = ? ORDER BY ordinal", (session_id,)
            ).fetchall()
        return QueueState(
            session_id=session_id,
            paused=bool(paused_row["paused"]) if paused_row is not None else False,
            items=[self._item(row, position=index + 1) for index, row in enumerate(rows)],
        )

    def enqueue(
        self,
        session_id: str,
        content: str,
        *,
        remember: bool,
        paste_spans: list[dict[str, int]],
        priority: bool = False,
    ) -> QueueMutation:
        session = self.ledger.get_session(session_id)
        if session.status != "open":
            raise ValueError("session is not open")
        queue_id = new_id()
        created_at = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM session_queue WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
            )
            if count >= self.MAX_PENDING:
                connection.rollback()
                raise QueueFullError("session queue already contains two messages")
            boundary = "MIN" if priority else "MAX"
            step = -1 if priority else 1
            ordinal = int(
                connection.execute(
                    f"SELECT COALESCE({boundary}(ordinal), 0) + ? "
                    "FROM session_queue WHERE session_id = ?",
                    (step, session_id),
                ).fetchone()[0]
            )
            position = 1 if priority else count + 1
            connection.execute(
                "INSERT INTO session_queue(id, session_id, ordinal, content, remember, "
                "paste_spans_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    queue_id,
                    session_id,
                    ordinal,
                    content,
                    int(remember),
                    json.dumps(paste_spans, separators=(",", ":"), sort_keys=True),
                    created_at,
                ),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=session.agent_id,
                event_type="queue.enqueued",
                payload={
                    "queue_id": queue_id,
                    "position": position,
                    "content": content,
                    "remember": remember,
                    "paste_spans": paste_spans,
                },
                correlation_id=queue_id,
            )
            connection.commit()
        state = self.state(session_id)
        item = next(item for item in state.items if item.id == queue_id)
        return QueueMutation(state=state, event=event, item=item)

    def take(self, session_id: str, queue_id: str, *, reason: str) -> QueueMutation:
        return self._take(session_id, queue_id=queue_id, newest=False, reason=reason)

    def take_latest(self, session_id: str, *, reason: str) -> QueueMutation:
        return self._take(session_id, queue_id=None, newest=True, reason=reason)

    def take_oldest(self, session_id: str, *, reason: str) -> QueueMutation:
        return self._take(session_id, queue_id=None, newest=False, reason=reason)

    def _take(
        self, session_id: str, *, queue_id: str | None, newest: bool, reason: str
    ) -> QueueMutation:
        session = self.ledger.get_session(session_id)
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if queue_id is not None:
                row = connection.execute(
                    "SELECT * FROM session_queue WHERE session_id = ? AND id = ?",
                    (session_id, queue_id),
                ).fetchone()
            else:
                direction = "DESC" if newest else "ASC"
                row = connection.execute(
                    "SELECT * FROM session_queue WHERE session_id = ? "
                    f"ORDER BY ordinal {direction} LIMIT 1",
                    (session_id,),
                ).fetchone()
            if row is None:
                connection.rollback()
                raise KeyError(queue_id or session_id)
            item_id = str(row["id"])
            position = int(
                connection.execute(
                    "SELECT COUNT(*) FROM session_queue WHERE session_id = ? AND ordinal <= ?",
                    (session_id, int(row["ordinal"])),
                ).fetchone()[0]
            )
            item = self._item(row, position=position)
            event_type = "queue.promoted" if reason == "promoted" else "queue.removed"
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=session.agent_id,
                event_type=event_type,
                payload={"queue_id": item_id, "reason": reason},
                correlation_id=item_id,
            )
            connection.execute("DELETE FROM session_queue WHERE id = ?", (item_id,))
            connection.commit()
        return QueueMutation(state=self.state(session_id), event=event, item=item)

    def clear(self, session_id: str, *, reason: str = "cleared") -> tuple[QueueMutation, ...]:
        mutations = []
        for item in list(self.state(session_id).items):
            mutations.append(self.take(session_id, item.id, reason=reason))
        return tuple(mutations)

    def set_paused(self, session_id: str, paused: bool) -> QueueMutation:
        session = self.ledger.get_session(session_id)
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO session_queue_state(session_id, paused, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET paused = excluded.paused, "
                "updated_at = excluded.updated_at",
                (session_id, int(paused), now),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=session.agent_id,
                event_type="queue.paused" if paused else "queue.resumed",
                payload={"paused": paused},
                correlation_id=session_id,
            )
            connection.commit()
        return QueueMutation(state=self.state(session_id), event=event)

    def recoverable_sessions(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT q.session_id FROM session_queue q "
                "JOIN sessions s ON s.id = q.session_id "
                "LEFT JOIN session_queue_state qs ON qs.session_id = q.session_id "
                "WHERE s.status = 'open' AND COALESCE(qs.paused, 0) = 0"
            ).fetchall()
        return [str(row["session_id"]) for row in rows]

    @staticmethod
    def _item(row: Any, *, position: int) -> QueuedMessage:
        return QueuedMessage(
            id=str(row["id"]),
            session_id=str(row["session_id"]),
            content=str(row["content"]),
            remember=bool(row["remember"]),
            paste_spans=list(json.loads(str(row["paste_spans_json"]))),
            created_at=str(row["created_at"]),
            position=position,
        )
