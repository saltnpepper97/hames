from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from random import Random

import pytest

from hames.database import MIGRATIONS, Database, Migration, MigrationError
from hames.event_types import UnknownEventType
from hames.ledger import Event, Ledger
from hames.paths import HamesPaths


def test_migrations_are_idempotent_and_private(hames_paths: HamesPaths) -> None:
    database = Database(hames_paths.database)
    database.migrate()
    database.migrate()
    assert database.path.stat().st_mode & 0o777 == 0o600
    with database.connect() as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 12


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


def test_session_mode_is_persisted_and_attributed(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    assert session.interaction_mode == "auto"
    updated = ledger.update_session_mode(session.id, mode="plan")
    assert updated.interaction_mode == "plan"
    event = ledger.list_events(session.id)[-1]
    assert event.type == "session.mode.changed"
    assert event.payload == {"mode": "plan"}


def test_session_title_is_normalized_and_attributed(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    event = ledger.update_session_title(
        session.id,
        title="  Refine   the TUI  ",
        run_id="run-title",
        agent_id="default",
        causation_id=ledger.list_events(session.id)[-1].id,
    )
    assert ledger.get_session(session.id).title == "Refine the TUI"
    assert event.type == "session.title.changed"
    assert event.run_id == "run-title"
    assert event.agent_id == "default"
    assert event.payload == {"title": "Refine the TUI"}


def test_recent_open_session_uses_canonical_cwd_and_latest_activity(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    first = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    second = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    ledger.append(
        session_id=first.id,
        event_type="user.message",
        payload={"content": "most recently active"},
    )

    selected = ledger.recent_open_session(tmp_path / ".", active_within_seconds=604_800)
    assert selected is not None
    assert selected.id == first.id

    ledger.close_session(first.id)
    selected = ledger.recent_open_session(tmp_path, active_within_seconds=604_800)
    assert selected is not None
    assert selected.id == second.id
    with pytest.raises(ValueError, match="positive"):
        ledger.recent_open_session(tmp_path, active_within_seconds=0)


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


def test_m03_database_gains_context_capacity_without_losing_sessions(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    old_database = Database(hames_paths.database, migrations=MIGRATIONS[:4])
    old_database.migrate()
    with old_database.connect() as connection:
        connection.execute(
            """
            INSERT INTO sessions(
                id, created_at, status, working_directory, agent_id, provider, model,
                reasoning_effort
            ) VALUES ('m03-session', '2026-01-01', 'open', ?, 'default', 'fake', 'fixture', '')
            """,
            (str(tmp_path),),
        )

    migrated = Database(hames_paths.database)
    migrated.migrate()
    session = Ledger(migrated).get_session("m03-session")
    assert session.context_window_tokens == 32_768
    assert session.context_window_source == "fallback"


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


def test_unknown_persisted_event_remains_readable(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    payload = "{}"
    with ledger.database.connect() as connection:
        connection.execute(
            """
            INSERT INTO events(
                id, session_id, type, schema_version, created_at, payload_json,
                payload_hash, redaction_state
            ) VALUES ('future-event', ?, 'future.unknown', 99, '2026-01-01', ?, sha256(?), 'none')
            """,
            (session.id, payload, payload),
        )
    assert ledger.get_event("future-event").type == "future.unknown"


def test_concurrent_appends_have_stable_unique_sequence(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )

    def append_message(index: int) -> Event:
        return ledger.append(
            session_id=session.id,
            event_type="user.message",
            payload={"content": f"message {index}"},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        events = list(pool.map(append_message, range(40)))
    sequences = [event.sequence for event in events]
    assert len(set(sequences)) == 40
    persisted = ledger.list_events(session.id)
    assert [event.sequence for event in persisted] == sorted(event.sequence for event in persisted)


def test_closed_session_rejects_new_events(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    ledger.close_session(session.id)
    with pytest.raises(ValueError, match="not open"):
        ledger.append(
            session_id=session.id,
            event_type="user.message",
            payload={"content": "too late"},
        )


def test_nested_branch_replay_and_settings_at_fork(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    root = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="first",
    )
    user = ledger.append(
        session_id=root.id,
        event_type="user.message",
        payload={"content": "root question"},
    )
    answer = ledger.append(
        session_id=root.id,
        event_type="assistant.message",
        payload={"content": "root answer", "status": "completed"},
        causation_id=user.id,
    )
    ledger.update_session_settings(
        root.id,
        provider="fake",
        model="later",
        reasoning_effort="xhigh",
    )

    branch = ledger.fork_session(root.id, fork_event_id=answer.id)
    assert branch.parent_session_id == root.id
    assert branch.fork_event_id == answer.id
    assert branch.model == "first"
    branch_answer = ledger.append(
        session_id=branch.id,
        event_type="assistant.message",
        payload={"content": "branch answer", "status": "completed"},
    )
    nested = ledger.fork_session(branch.id)

    replay = ledger.replay(nested.id)
    assert [event.payload.get("content") for event in replay if "content" in event.payload] == [
        "root question",
        "root answer",
        "branch answer",
    ]
    assert nested.fork_event_id == branch_answer.id
    assert all(event.type != "session.settings.changed" for event in replay)


def test_fork_rejects_invisible_target(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    first = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    other = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    foreign = ledger.append(
        session_id=other.id,
        event_type="user.message",
        payload={"content": "foreign"},
    )
    with pytest.raises(ValueError, match="not visible"):
        ledger.fork_session(first.id, fork_event_id=foreign.id)


def test_generated_branch_tree_matches_reference_replay(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    root = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    first = ledger.append(
        session_id=root.id,
        event_type="assistant.message",
        payload={"content": "root", "status": "completed"},
    )
    sessions = [root]
    expected: dict[str, list[Event]] = {root.id: [*ledger.list_events(root.id)]}
    random = Random(20260823)

    for index in range(20):
        parent = random.choice(sessions)
        parent_history = expected[parent.id]
        target = random.choice(parent_history)
        child = ledger.fork_session(parent.id, fork_event_id=target.id)
        child_local = ledger.list_events(child.id)
        expected[child.id] = [
            *[event for event in parent_history if event.sequence <= target.sequence],
            *child_local,
        ]
        message = ledger.append(
            session_id=child.id,
            event_type="assistant.message",
            payload={"content": f"branch {index}", "status": "completed"},
        )
        expected[child.id].append(message)
        sessions.append(child)

    assert first in expected[root.id]
    for session in sessions:
        assert [event.id for event in ledger.replay(session.id)] == [
            event.id for event in expected[session.id]
        ]


def test_session_requires_existing_directory(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    with pytest.raises(FileNotFoundError):
        ledger.create_session(
            working_directory=tmp_path / "missing",
            agent_id="default",
            provider="fake",
            model="fixture",
        )
