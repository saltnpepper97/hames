"""SQLite connection and migration infrastructure."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class MigrationError(RuntimeError):
    """A durable schema migration could not be applied safely."""


def _sha256_sql(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Migration:
    id: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode()).hexdigest()


MIGRATIONS = (
    Migration(
        1,
        "application metadata",
        """
        CREATE TABLE IF NOT EXISTS app_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """,
    ),
    Migration(
        2,
        "core event ledger",
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            closed_at TEXT,
            status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'cancelled', 'failed')),
            title TEXT,
            working_directory TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            reasoning_effort TEXT NOT NULL
        );

        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            agent_id TEXT,
            type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            causation_id TEXT,
            correlation_id TEXT,
            payload_json TEXT NOT NULL
        );

        CREATE INDEX events_session_sequence_idx ON events(session_id, sequence);
        CREATE INDEX events_run_idx ON events(run_id) WHERE run_id IS NOT NULL;

        CREATE TRIGGER events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        CREATE TRIGGER events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;
        """,
    ),
    Migration(
        3,
        "branching and payload integrity",
        """
        DROP TRIGGER events_no_update;
        DROP TRIGGER events_no_delete;
        DROP INDEX events_session_sequence_idx;
        DROP INDEX events_run_idx;

        ALTER TABLE events RENAME TO events_m00;

        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            agent_id TEXT,
            type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            causation_id TEXT,
            correlation_id TEXT,
            payload_json TEXT,
            blob_hash TEXT,
            payload_hash TEXT NOT NULL,
            redaction_state TEXT NOT NULL CHECK (redaction_state IN ('none', 'redacted')),
            CHECK ((payload_json IS NOT NULL) != (blob_hash IS NOT NULL))
        );

        INSERT INTO events(
            sequence, id, session_id, run_id, agent_id, type, schema_version,
            created_at, causation_id, correlation_id, payload_json, blob_hash,
            payload_hash, redaction_state
        )
        SELECT
            sequence, id, session_id, run_id, agent_id, type, schema_version,
            created_at, causation_id, correlation_id, payload_json, NULL,
            sha256(payload_json), 'none'
        FROM events_m00;

        DROP TABLE events_m00;

        CREATE INDEX events_session_sequence_idx ON events(session_id, sequence);
        CREATE INDEX events_run_idx ON events(run_id) WHERE run_id IS NOT NULL;

        CREATE TRIGGER events_no_update
        BEFORE UPDATE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        CREATE TRIGGER events_no_delete
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'events are append-only');
        END;

        ALTER TABLE sessions
            ADD COLUMN parent_session_id TEXT REFERENCES sessions(id);
        ALTER TABLE sessions
            ADD COLUMN fork_event_id TEXT REFERENCES events(id);
        CREATE INDEX sessions_parent_idx ON sessions(parent_session_id);
        """,
    ),
    Migration(
        4,
        "trusted roots and approvals",
        """
        CREATE TABLE trusted_roots (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );

        CREATE TABLE approvals (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            working_directory TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'approved', 'denied', 'cancelled')
            ),
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );

        CREATE INDEX approvals_run_idx ON approvals(run_id);
        CREATE INDEX approvals_pending_idx ON approvals(status) WHERE status = 'pending';
        """,
    ),
    Migration(
        5,
        "model context capacity",
        """
        ALTER TABLE sessions
            ADD COLUMN context_window_tokens INTEGER NOT NULL DEFAULT 32768;
        ALTER TABLE sessions
            ADD COLUMN context_window_source TEXT NOT NULL DEFAULT 'fallback'
            CHECK (context_window_source IN ('profile', 'provider', 'fallback'));
        """,
    ),
    Migration(
        6,
        "agent lineage",
        """
        ALTER TABLE sessions
            ADD COLUMN lineage_kind TEXT NOT NULL DEFAULT 'root'
            CHECK (lineage_kind IN ('root', 'branch', 'delegation'));
        ALTER TABLE sessions
            ADD COLUMN delegation_depth INTEGER NOT NULL DEFAULT 0;
        UPDATE sessions SET lineage_kind = 'branch' WHERE parent_session_id IS NOT NULL;
        """,
    ),
    Migration(
        7,
        "layered memory",
        """
        CREATE TABLE memory_records (
            id TEXT PRIMARY KEY,
            layer TEXT NOT NULL CHECK (layer IN ('relationship', 'semantic', 'episodic')),
            status TEXT NOT NULL CHECK (
                status IN ('proposed', 'active', 'rejected', 'superseded', 'retracted')
            ),
            visibility TEXT NOT NULL CHECK (
                visibility IN ('global', 'agent_private', 'workspace', 'session_team')
            ),
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            value_json TEXT NOT NULL,
            summary TEXT NOT NULL,
            confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            importance REAL NOT NULL CHECK (importance >= 0 AND importance <= 1),
            owner_agent_id TEXT,
            workspace_path TEXT,
            lineage_root_session_id TEXT REFERENCES sessions(id),
            source_session_id TEXT NOT NULL REFERENCES sessions(id),
            source_run_id TEXT,
            origin_kind TEXT NOT NULL CHECK (
                origin_kind IN ('automatic', 'explicit', 'episode')
            ),
            valid_from TEXT,
            valid_until TEXT,
            superseded_by_id TEXT REFERENCES memory_records(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (visibility != 'agent_private' OR owner_agent_id IS NOT NULL),
            CHECK (visibility != 'workspace' OR workspace_path IS NOT NULL),
            CHECK (visibility != 'session_team' OR lineage_root_session_id IS NOT NULL)
        );

        CREATE TABLE memory_anchors (
            memory_id TEXT NOT NULL REFERENCES memory_records(id),
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (memory_id, kind, value)
        );

        CREATE TABLE memory_provenance (
            memory_id TEXT NOT NULL REFERENCES memory_records(id),
            event_id TEXT NOT NULL REFERENCES events(id),
            PRIMARY KEY (memory_id, event_id)
        );

        CREATE VIRTUAL TABLE memory_fts USING fts5(
            memory_id UNINDEXED,
            subject,
            predicate,
            summary,
            value
        );

        CREATE TABLE memory_jobs (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('extraction', 'explicit_capture')),
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
            ),
            session_id TEXT NOT NULL REFERENCES sessions(id),
            run_id TEXT,
            source_event_id TEXT NOT NULL REFERENCES events(id),
            content TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (kind, session_id, run_id, source_event_id)
        );

        CREATE INDEX memory_status_layer_idx ON memory_records(status, layer);
        CREATE INDEX memory_workspace_idx ON memory_records(workspace_path);
        CREATE INDEX memory_agent_idx ON memory_records(owner_agent_id);
        CREATE INDEX memory_lineage_idx ON memory_records(lineage_root_session_id);
        CREATE INDEX memory_jobs_status_idx ON memory_jobs(status, created_at);

        CREATE TRIGGER memory_records_no_delete
        BEFORE DELETE ON memory_records
        BEGIN
            SELECT RAISE(ABORT, 'memory records cannot be deleted');
        END;
        """,
    ),
)


class Database:
    """Own connection policy and monotonic migrations for one Hames database."""

    def __init__(self, path: Path, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        self.path = path
        self.migrations = migrations

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.create_function(
            "sha256",
            1,
            _sha256_sql,
            deterministic=True,
        )
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def migrate(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["id"]): str(row["checksum"])
                for row in connection.execute("SELECT id, checksum FROM schema_migrations")
            }
            for migration in self.migrations:
                previous = applied.get(migration.id)
                if previous is not None:
                    if previous != migration.checksum:
                        raise MigrationError(f"migration {migration.id} checksum changed")
                    continue
                script = f"""
                BEGIN IMMEDIATE;
                {migration.sql}
                INSERT INTO schema_migrations(id, name, checksum, applied_at)
                VALUES (
                    {migration.id},
                    {self._quote(migration.name)},
                    {self._quote(migration.checksum)},
                    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                );
                COMMIT;
                """
                try:
                    connection.executescript(script)
                except sqlite3.Error as exc:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
                    raise MigrationError(f"migration {migration.id} failed: {exc}") from exc
        finally:
            connection.close()
        self.path.chmod(0o600)

    @staticmethod
    def _quote(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"
