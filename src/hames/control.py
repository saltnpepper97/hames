"""Durable trust grants and one-shot approvals."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from hames.database import Database
from hames.ledger import new_id, utc_now
from hames.providers.base import JsonValue


class ControlModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrustGrant(ControlModel):
    id: str
    path: str
    created_at: str


class Approval(ControlModel):
    id: str
    session_id: str
    run_id: str
    agent_id: str
    working_directory: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, JsonValue]
    request_hash: str
    reason: str
    status: str
    allow_session: bool
    approval_scope: str
    created_at: str
    resolved_at: str | None


class ControlStore:
    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = threading.Lock()

    @staticmethod
    def canonical_directory(path: Path) -> str:
        canonical = path.expanduser().resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("trusted path must be a directory")
        return str(canonical)

    def get_trust(self, path: Path) -> TrustGrant | None:
        canonical = self.canonical_directory(path)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM trusted_roots WHERE path = ?", (canonical,)
            ).fetchone()
        return TrustGrant.model_validate(dict(row)) if row is not None else None

    def grant_trust(self, path: Path) -> TrustGrant:
        canonical = self.canonical_directory(path)
        with self._lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM trusted_roots WHERE path = ?", (canonical,)
            ).fetchone()
            if row is None:
                grant_id = new_id()
                created_at = utc_now()
                connection.execute(
                    "INSERT INTO trusted_roots(id, path, created_at) VALUES (?, ?, ?)",
                    (grant_id, canonical, created_at),
                )
                row = connection.execute(
                    "SELECT * FROM trusted_roots WHERE id = ?", (grant_id,)
                ).fetchone()
            connection.commit()
        if row is None:  # pragma: no cover - SQLite invariant
            raise RuntimeError("trust grant was not persisted")
        return TrustGrant.model_validate(dict(row))

    def revoke_trust(self, path: Path) -> bool:
        canonical = self.canonical_directory(path)
        with self._lock, self.database.connect() as connection:
            cursor = connection.execute("DELETE FROM trusted_roots WHERE path = ?", (canonical,))
        return cursor.rowcount == 1

    def create_approval(
        self,
        *,
        session_id: str,
        run_id: str,
        agent_id: str,
        working_directory: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, JsonValue],
        request_hash: str,
        reason: str,
        allow_session: bool = False,
    ) -> Approval:
        approval_id = new_id()
        created_at = utc_now()
        with self._lock, self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO approvals(
                    id, session_id, run_id, agent_id, working_directory,
                    tool_call_id, tool_name, arguments_json, request_hash,
                    reason, status, created_at, allow_session
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id,
                    session_id,
                    run_id,
                    agent_id,
                    working_directory,
                    tool_call_id,
                    tool_name,
                    json.dumps(arguments, separators=(",", ":"), sort_keys=True),
                    request_hash,
                    reason,
                    created_at,
                    int(allow_session),
                ),
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> Approval:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return self._approval(row)

    def resolve_approval(self, approval_id: str, request_hash: str, decision: str) -> Approval:
        if decision not in {"approved", "approved_session", "denied"}:
            raise ValueError("decision must be approved, approved_session, or denied")
        with self._lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if str(row["request_hash"]) != request_hash:
                raise ValueError("approval request hash does not match")
            if str(row["status"]) != "pending":
                raise RuntimeError("approval has already been resolved")
            if decision == "approved_session" and not bool(row["allow_session"]):
                raise ValueError("this approval cannot be granted for the session")
            status = "approved" if decision.startswith("approved") else "denied"
            scope = "session" if decision == "approved_session" else "once"
            connection.execute(
                "UPDATE approvals SET status = ?, approval_scope = ?, resolved_at = ? WHERE id = ?",
                (status, scope, utc_now(), approval_id),
            )
            if decision == "approved_session":
                connection.execute(
                    "INSERT OR IGNORE INTO session_tool_grants(session_id, tool_name, created_at) "
                    "VALUES (?, ?, ?)",
                    (row["session_id"], row["tool_name"], utc_now()),
                )
            connection.commit()
        return self.get_approval(approval_id)

    def has_session_tool_grant(self, session_id: str, tool_name: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM session_tool_grants WHERE session_id = ? AND tool_name = ?",
                (session_id, tool_name),
            ).fetchone()
        return row is not None

    def cancel_pending_for_run(self, run_id: str) -> list[Approval]:
        with self._lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id FROM approvals WHERE run_id = ? AND status = 'pending'", (run_id,)
            ).fetchall()
            connection.execute(
                """
                UPDATE approvals SET status = 'cancelled', resolved_at = ?
                WHERE run_id = ? AND status = 'pending'
                """,
                (utc_now(), run_id),
            )
            connection.commit()
        return [self.get_approval(str(row["id"])) for row in rows]

    @staticmethod
    def _approval(row: sqlite3.Row) -> Approval:
        values: dict[str, object] = {key: row[key] for key in row.keys()}
        values["arguments"] = json.loads(str(values.pop("arguments_json")))
        return Approval.model_validate(values)
