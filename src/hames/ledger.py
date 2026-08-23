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
    context_window_tokens: int
    context_window_source: str
    parent_session_id: str | None
    fork_event_id: str | None
    lineage_kind: str
    delegation_depth: int


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
    """The only supported write path for sessions and durable events."""

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
        context_window_tokens: int = 32_768,
        context_window_source: str = "fallback",
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
                    provider, model, reasoning_effort, context_window_tokens,
                    context_window_source, lineage_kind, delegation_depth
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, 'root', 0)
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
                    context_window_tokens,
                    context_window_source,
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
                    "context_window_tokens": context_window_tokens,
                    "context_window_source": context_window_source,
                },
            )
            connection.commit()
        return self.get_session(session_id)

    def create_delegated_session(
        self,
        parent_session_id: str,
        *,
        parent_event_id: str,
        agent_id: str,
        title: str | None = None,
    ) -> Session:
        """Create a child with inherited execution settings but no conversation replay."""

        parent = self.get_session(parent_session_id)
        event = self.get_event(parent_event_id)
        if event.session_id != parent.id or event.type != "delegation.requested":
            raise ValueError("delegation source event must belong to the parent session")
        session_id = new_id()
        created_at = utc_now()
        child_title = title or f"Delegation of {parent.title or parent.id}"
        depth = parent.delegation_depth + 1
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(
                    id, created_at, status, title, working_directory, agent_id,
                    provider, model, reasoning_effort, context_window_tokens,
                    context_window_source, parent_session_id, fork_event_id,
                    lineage_kind, delegation_depth
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'delegation', ?)
                """,
                (
                    session_id,
                    created_at,
                    child_title,
                    parent.working_directory,
                    agent_id,
                    parent.provider,
                    parent.model,
                    parent.reasoning_effort,
                    parent.context_window_tokens,
                    parent.context_window_source,
                    parent.id,
                    parent_event_id,
                    depth,
                ),
            )
            self._append_on_connection(
                connection,
                session_id=session_id,
                event_type="session.opened",
                agent_id=agent_id,
                payload={
                    "working_directory": parent.working_directory,
                    "provider": parent.provider,
                    "model": parent.model,
                    "reasoning_effort": parent.reasoning_effort,
                    "context_window_tokens": parent.context_window_tokens,
                    "context_window_source": parent.context_window_source,
                },
                causation_id=parent_event_id,
                correlation_id=session_id,
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
            row = connection.execute(
                "SELECT status FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            if row["status"] != "open":
                raise ValueError("session is not open")
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

    def list_run_events(self, run_id: str) -> list[Event]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def replay(self, session_id: str, *, after_sequence: int = 0) -> list[Event]:
        events = self._replay(session_id, active=set())
        return [event for event in events if event.sequence > after_sequence]

    def _replay(self, session_id: str, *, active: set[str]) -> list[Event]:
        if session_id in active:
            raise EventIntegrityError(f"session ancestry cycle detected at {session_id}")
        active.add(session_id)
        try:
            session = self.get_session(session_id)
            inherited: list[Event] = []
            if session.parent_session_id is not None and session.lineage_kind == "branch":
                if session.fork_event_id is None:
                    raise EventIntegrityError(f"branch {session.id} has no fork event")
                fork = self.get_event(session.fork_event_id)
                parent_events = self._replay(session.parent_session_id, active=active)
                if not any(event.id == fork.id for event in parent_events):
                    raise EventIntegrityError(
                        f"fork event {fork.id} is not visible in parent {session.parent_session_id}"
                    )
                inherited = [event for event in parent_events if event.sequence <= fork.sequence]
            return [*inherited, *self.list_events(session_id)]
        finally:
            active.remove(session_id)

    def fork_session(
        self,
        parent_session_id: str,
        *,
        fork_event_id: str | None = None,
        title: str | None = None,
        agent_id: str | None = None,
    ) -> Session:
        parent = self.get_session(parent_session_id)
        history = self.replay(parent_session_id)
        if fork_event_id is None:
            target = next(
                (
                    event
                    for event in reversed(history)
                    if event.type == "assistant.message"
                    and event.payload.get("status") == "completed"
                ),
                None,
            )
            if target is None:
                raise ValueError("session has no completed assistant turn to fork")
        else:
            target = next((event for event in history if event.id == fork_event_id), None)
            if target is None:
                raise ValueError("fork event is not visible in the parent session")

        provider, model, reasoning_effort, context_window_tokens, context_window_source = (
            self._settings_at(history, target.sequence)
        )
        session_id = new_id()
        created_at = utc_now()
        branch_title = title or f"Branch of {parent.title or parent.id} @ {target.sequence}"
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sessions(
                    id, created_at, status, title, working_directory, agent_id,
                    provider, model, reasoning_effort, context_window_tokens,
                    context_window_source, parent_session_id, fork_event_id,
                    lineage_kind, delegation_depth
                ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'branch', 0)
                """,
                (
                    session_id,
                    created_at,
                    branch_title,
                    parent.working_directory,
                    agent_id or parent.agent_id,
                    provider,
                    model,
                    reasoning_effort,
                    context_window_tokens,
                    context_window_source,
                    parent.id,
                    target.id,
                ),
            )
            opened = self._append_on_connection(
                connection,
                session_id=session_id,
                event_type="session.opened",
                agent_id=agent_id or parent.agent_id,
                payload={
                    "working_directory": parent.working_directory,
                    "provider": provider,
                    "model": model,
                    "reasoning_effort": reasoning_effort,
                    "context_window_tokens": context_window_tokens,
                    "context_window_source": context_window_source,
                },
                causation_id=target.id,
                correlation_id=session_id,
            )
            self._append_on_connection(
                connection,
                session_id=session_id,
                event_type="session.forked",
                agent_id=agent_id or parent.agent_id,
                payload={
                    "parent_session_id": parent.id,
                    "fork_event_id": target.id,
                    "fork_sequence": target.sequence,
                },
                causation_id=opened.id,
                correlation_id=session_id,
            )
            connection.commit()
        return self.get_session(session_id)

    @staticmethod
    def _settings_at(history: list[Event], sequence: int) -> tuple[str, str, str, int, str]:
        settings: tuple[str, str, str, int, str] | None = None
        for event in history:
            if event.sequence > sequence:
                break
            if event.type in {"session.opened", "session.settings.changed"}:
                settings = (
                    str(event.payload["provider"]),
                    str(event.payload["model"]),
                    str(event.payload["reasoning_effort"]),
                    int(event.payload.get("context_window_tokens", 32_768)),
                    str(event.payload.get("context_window_source", "fallback")),
                )
        if settings is None:
            raise EventIntegrityError("session history has no settings origin")
        return settings

    def resolve_visible_event(self, session_id: str, value: str) -> Event:
        history = self.replay(session_id)
        if value.isdigit():
            sequence = int(value)
            event = next((item for item in history if item.sequence == sequence), None)
        else:
            event = next((item for item in history if item.id == value), None)
        if event is None:
            raise KeyError(value)
        return event

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
        context_window_tokens: int = 32_768,
        context_window_source: str = "fallback",
    ) -> Session:
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE sessions
                SET provider = ?, model = ?, reasoning_effort = ?,
                    context_window_tokens = ?, context_window_source = ?
                WHERE id = ? AND status = 'open'
                """,
                (
                    provider,
                    model,
                    reasoning_effort,
                    context_window_tokens,
                    context_window_source,
                    session_id,
                ),
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
                    "context_window_tokens": context_window_tokens,
                    "context_window_source": context_window_source,
                },
            )
            connection.commit()
        return self.get_session(session_id)

    def update_session_agent(self, session_id: str, *, agent_id: str) -> Session:
        """Select the capsule for future turns without changing historical attribution."""

        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE sessions SET agent_id = ? WHERE id = ? AND status = 'open'",
                (agent_id, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
            self._append_on_connection(
                connection,
                session_id=session_id,
                agent_id=agent_id,
                event_type="session.agent.changed",
                payload={"agent_id": agent_id},
                correlation_id=session_id,
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
