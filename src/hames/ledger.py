"""Append-only sessions and events."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from hames.database import Database


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Session(LedgerModel):
    id: str
    created_at: str
    closed_at: str | None
    status: str
    title: str | None
    working_directory: str
    agent_id: str
    provider: str
    model: str
    reasoning_effort: str


class Event(LedgerModel):
    id: str
    sequence: int
    session_id: str
    run_id: str | None
    agent_id: str | None
    type: str
    schema_version: int
    created_at: str
    causation_id: str | None
    correlation_id: str | None
    payload: dict[str, Any]


class Ledger:
    """The only supported write path for M0 sessions and events."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._write_lock = threading.Lock()

    @classmethod
    def open(cls, path: Path) -> Ledger:
        database = Database(path)
        database.migrate()
        return cls(database)

    def create_session(
        self,
        *,
        working_directory: Path,
        agent_id: str,
        provider: str,
        model: str,
        reasoning_effort: str = "",
        title: str | None = None,
    ) -> Session:
        canonical = working_directory.expanduser().resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("working_directory must be a directory")
        session_id = new_id()
        created_at = utc_now()
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(
                    id, created_at, status, title, working_directory, agent_id,
                    provider, model, reasoning_effort
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    created_at,
                    title,
                    str(canonical),
                    agent_id,
                    provider,
                    model,
                    reasoning_effort,
                ),
            )
            self._append_on_connection(
                connection,
                session_id=session_id,
                event_type="session.opened",
                agent_id=agent_id,
                payload={
                    "working_directory": str(canonical),
                    "provider": provider,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                },
            )
            connection.commit()
        return self.get_session(session_id)

    def append(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        run_id: str | None = None,
        agent_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = self._append_on_connection(
                connection,
                session_id=session_id,
                event_type=event_type,
                payload=payload,
                run_id=run_id,
                agent_id=agent_id,
                causation_id=causation_id,
                correlation_id=correlation_id,
            )
            connection.commit()
            return event

    def _append_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        event_type: str,
        payload: Mapping[str, Any],
        run_id: str | None = None,
        agent_id: str | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        event_id = new_id()
        created_at = utc_now()
        encoded = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True)
        cursor = connection.execute(
            """
            INSERT INTO events(
                id, session_id, run_id, agent_id, type, schema_version,
                created_at, causation_id, correlation_id, payload_json
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                run_id,
                agent_id,
                event_type,
                created_at,
                causation_id,
                correlation_id,
                encoded,
            ),
        )
        sequence = cursor.lastrowid
        if sequence is None:
            raise RuntimeError("SQLite did not return an event sequence")
        return Event(
            id=event_id,
            sequence=sequence,
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            type=event_type,
            schema_version=1,
            created_at=created_at,
            causation_id=causation_id,
            correlation_id=correlation_id,
            payload=dict(payload),
        )

    def get_session(self, session_id: str) -> Session:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise KeyError(session_id)
        return Session.model_validate(dict(row))

    def list_sessions(self) -> list[Session]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [Session.model_validate(dict(row)) for row in rows]

    def list_events(self, session_id: str, *, after_sequence: int = 0) -> list[Event]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence
                """,
                (session_id, after_sequence),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def close_session(self, session_id: str, *, status: str = "closed") -> Session:
        if status not in {"closed", "cancelled", "failed"}:
            raise ValueError(f"invalid terminal session status: {status}")
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            closed_at = utc_now()
            cursor = connection.execute(
                "UPDATE sessions SET status = ?, closed_at = ? WHERE id = ? AND status = 'open'",
                (status, closed_at, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
            self._append_on_connection(
                connection,
                session_id=session_id,
                event_type="session.closed",
                payload={"status": status},
            )
            connection.commit()
        return self.get_session(session_id)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> Event:
        values = dict(row)
        payload = json.loads(values.pop("payload_json"))
        values["payload"] = payload
        return Event.model_validate(values)
