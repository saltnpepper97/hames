from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hames.database import MIGRATIONS, Database, Migration, MigrationError
from hames.event_types import UnknownEventType
from hames.ledger import Ledger
from hames.paths import HamesPaths


def test_migrations_are_idempotent_and_private(hames_paths: HamesPaths) -> None:
    database = Database(hames_paths.database)
    database.migrate()
    database.migrate()
    assert database.path.stat().st_mode & 0o777 == 0o600
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 3


def test_failed_migration_does_not_advance_schema(tmp_path: Path) -> None:
    path = tmp_path / "failed.db"
    migrations = (Migration(1, "broken", "CREATE TABLE valid(id); INVALID SQL;"),)
    with pytest.raises(MigrationError, match="migration 1 failed"):
        Database(path, migrations).migrate()
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_master WHERE name = 'valid'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_events_are_ordered_append_only_and_restart_safe(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    first = ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "hello"},
    )
    second = ledger.append(
        session_id=session.id,
        event_type="assistant.message",
        payload={"content": "hi", "status": "completed"},
        causation_id=first.id,
    )
    assert first.sequence < second.sequence
    assert len(first.payload_hash) == 64
    assert first.redaction_state == "none"

    reopened = Ledger.open(hames_paths.database)
    events = reopened.list_events(session.id)
    assert [event.type for event in events] == [
        "session.opened",
        "user.message",
        "assistant.message",
    ]
    assert reopened.list_events(session.id, after_sequence=first.sequence)[0].id == second.id

    with reopened.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE events SET type = 'changed' WHERE id = ?", (first.id,))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM events WHERE id = ?", (first.id,))


def test_m00_migration_preserves_events(hames_paths: HamesPaths, tmp_path: Path) -> None:
    old_database = Database(hames_paths.database, migrations=MIGRATIONS[:2])
    old_database.migrate()
    with old_database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions(
                id, created_at, status, working_directory, agent_id, provider, model,
                reasoning_effort
            ) VALUES ('old-session', '2026-01-01', 'open', ?, 'default', 'fake', 'fixture', '')
            """,
            (str(tmp_path),),
        )
        connection.execute(
            """
            INSERT INTO events(
                id, session_id, type, schema_version, created_at, payload_json
            ) VALUES ('old-event', 'old-session', 'user.message', 1, '2026-01-01',
                      '{"content":"preserved"}')
            """
        )

    migrated = Database(hames_paths.database)
    migrated.migrate()
    event = Ledger(migrated).list_events("old-session")[0]
    assert event.payload == {"content": "preserved"}
    assert len(event.payload_hash) == 64


def test_unknown_event_append_is_rejected(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    with pytest.raises(UnknownEventType):
        ledger.append(session_id=session.id, event_type="future.unknown", payload={})


def test_session_requires_existing_directory(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    with pytest.raises(FileNotFoundError):
        ledger.create_session(
            working_directory=tmp_path / "missing",
            agent_id="default",
            provider="fake",
            model="fixture",
        )
