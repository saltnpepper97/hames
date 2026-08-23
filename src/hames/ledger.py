"""Append-only sessions and events."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from hames.blobs import BlobIntegrityError, BlobStore
from hames.database import Database
from hames.event_types import validate_payload
from hames.redaction import redact


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
    parent_session_id: str | None
    fork_event_id: str | None


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
    blob_hash: str | None
    payload_hash: str
    redaction_state: str


class IntegrityResult(LedgerModel):
    event_id: str
    ok: bool
    payload_hash: str
    blob_hash: str | None
    redaction_state: str


class EventIntegrityError(RuntimeError):
    pass


class Ledger:
    """The only supported write path for M0 sessions and events."""

    def __init__(
        self,
        database: Database,
        *,
        blob_store: BlobStore | None = None,
        blob_threshold_bytes: int = 65_536,
    ) -> None:
        self.database = database
        self.blob_store = blob_store or BlobStore(database.path.parent / "blobs")
        self.blob_threshold_bytes = blob_threshold_bytes
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
        secret_paths: Iterable[str] = (),
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
                secret_paths=secret_paths,
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
        secret_paths: Iterable[str] = (),
    ) -> Event:
        event_id = new_id()
        created_at = utc_now()
        validated = validate_payload(event_type, dict(payload))
        persisted, was_redacted = redact(validated, secret_paths)
        encoded = json.dumps(persisted, separators=(",", ":"), sort_keys=True)
        encoded_bytes = encoded.encode()
        payload_hash = hashlib.sha256(encoded_bytes).hexdigest()
        blob_hash = (
            self.blob_store.put(encoded_bytes)
            if len(encoded_bytes) > self.blob_threshold_bytes
            else None
        )
        inline_payload = None if blob_hash else encoded
        redaction_state = "redacted" if was_redacted else "none"
        cursor = connection.execute(
            """
            INSERT INTO events(
                id, session_id, run_id, agent_id, type, schema_version,
                created_at, causation_id, correlation_id, payload_json, blob_hash,
                payload_hash, redaction_state
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
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
                inline_payload,
                blob_hash,
                payload_hash,
                redaction_state,
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
            payload=persisted,
            blob_hash=blob_hash,
            payload_hash=payload_hash,
            redaction_state=redaction_state,
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

    def get_event(self, event_id: str) -> Event:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise KeyError(event_id)
        return self._event_from_row(row)

    def verify_event(self, event_id: str) -> IntegrityResult:
        event = self.get_event(event_id)
        return IntegrityResult(
            event_id=event.id,
            ok=True,
            payload_hash=event.payload_hash,
            blob_hash=event.blob_hash,
            redaction_state=event.redaction_state,
        )

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

    def update_session_settings(
        self,
        session_id: str,
        *,
        provider: str,
        model: str,
        reasoning_effort: str,
    ) -> Session:
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE sessions
                SET provider = ?, model = ?, reasoning_effort = ?
                WHERE id = ? AND status = 'open'
                """,
                (provider, model, reasoning_effort, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
            self._append_on_connection(
                connection,
                session_id=session_id,
                event_type="session.settings.changed",
                payload={
                    "provider": provider,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                },
            )
            connection.commit()
        return self.get_session(session_id)

    def _event_from_row(self, row: sqlite3.Row) -> Event:
        values = dict(row)
        payload_json = values.pop("payload_json")
        blob_hash = values.get("blob_hash")
        try:
            encoded = (
                self.blob_store.read(str(blob_hash))
                if blob_hash is not None
                else str(payload_json).encode()
            )
        except BlobIntegrityError as exc:
            raise EventIntegrityError(str(exc)) from exc
        actual = hashlib.sha256(encoded).hexdigest()
        if actual != values["payload_hash"]:
            expected = values["payload_hash"]
            raise EventIntegrityError(
                f"event {values['id']} payload hash mismatch: expected {expected}, found {actual}"
            )
        payload = json.loads(encoded)
        values["payload"] = payload
        return Event.model_validate(values)
