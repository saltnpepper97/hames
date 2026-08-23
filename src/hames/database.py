"""SQLite connection and migration infrastructure."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path


class MigrationError(RuntimeError):
    """A durable schema migration could not be applied safely."""


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
)


class Database:
    """Own connection policy and monotonic migrations for one Hames database."""

    def __init__(self, path: Path, migrations: tuple[Migration, ...] = MIGRATIONS) -> None:
        self.path = path
        self.migrations = migrations

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
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
