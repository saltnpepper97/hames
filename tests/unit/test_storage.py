from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hames.blobs import BlobIntegrityError, BlobStore
from hames.database import Database
from hames.ledger import EventIntegrityError, Ledger
from hames.paths import HamesPaths


def open_ledger(paths: HamesPaths, *, threshold: int = 64) -> Ledger:
    paths.ensure_foundation()
    ledger = Ledger.open(paths.database)
    ledger.blob_threshold_bytes = threshold
    return ledger


def test_database_context_releases_files_without_waiting_for_gc(tmp_path: Path) -> None:
    import psutil

    database = Database(tmp_path / "connections.db")
    retained: list[sqlite3.Connection] = []
    process = psutil.Process()
    initial_files = process.num_fds()
    for _ in range(400):
        with database.connect() as connection:
            connection.execute("SELECT 1").fetchone()
        retained.append(connection)
    assert process.num_fds() <= initial_files + 2
    for connection in retained:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            connection.execute("SELECT 1")


def test_database_context_commits_and_closes(tmp_path: Path) -> None:
    database = Database(tmp_path / "transactions.db")
    with database.connect() as connection:
        connection.execute("CREATE TABLE example(value TEXT)")
        connection.execute("BEGIN")
        connection.execute("INSERT INTO example VALUES ('committed')")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("SELECT 1")
    with database.connect() as reopened:
        assert reopened.execute("SELECT value FROM example").fetchone()[0] == "committed"


def test_database_context_rolls_back_and_closes_on_error(tmp_path: Path) -> None:
    database = Database(tmp_path / "transactions.db")
    with database.connect() as connection:
        connection.execute("CREATE TABLE example(value TEXT)")
    failed = database.connect()
    with pytest.raises(ValueError, match="failed transaction"):
        with failed:
            failed.execute("BEGIN")
            failed.execute("INSERT INTO example VALUES ('rolled back')")
            raise ValueError("failed transaction")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        failed.execute("SELECT 1")
    with database.connect() as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM example").fetchone()[0] == 0


def test_database_context_closes_when_commit_fails(tmp_path: Path) -> None:
    database = Database(tmp_path / "transactions.db")
    with database.connect() as connection:
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child(parent_id REFERENCES parent(id) DEFERRABLE INITIALLY DEFERRED)"
        )
    failed = database.connect()
    with pytest.raises(sqlite3.IntegrityError):
        with failed:
            failed.execute("BEGIN")
            failed.execute("INSERT INTO child VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        failed.execute("SELECT 1")
    with database.connect() as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0


def test_blob_store_deduplicates_and_detects_corruption(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    digest = store.put(b"same content")
    assert store.put(b"same content") == digest
    assert store.read(digest) == b"same content"
    target = store.path_for(digest)
    assert target.stat().st_mode & 0o777 == 0o600
    assert store.root.stat().st_mode & 0o777 == 0o700
    target.write_bytes(b"corrupt")
    with pytest.raises(BlobIntegrityError, match="corrupt blob"):
        store.read(digest)

    target.unlink()
    with pytest.raises(BlobIntegrityError, match="missing blob"):
        store.read(digest)


def test_large_payload_is_blob_backed_and_verified(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = open_ledger(hames_paths)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    event = ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "large-" * 100},
    )
    assert event.blob_hash is not None
    assert event.payload_hash == event.blob_hash
    assert ledger.verify_event(event.id).ok
    assert ledger.get_event(event.id).payload == event.payload

    path = ledger.blob_store.path_for(event.blob_hash)
    path.write_bytes(b"corrupt")
    with pytest.raises(EventIntegrityError, match="corrupt blob"):
        ledger.verify_event(event.id)


def test_redaction_precedes_blob_persistence(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = open_ledger(hames_paths)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    secret = "never-persist-this-value"
    event = ledger.append(
        session_id=session.id,
        event_type="runtime.notice",
        payload={
            "message": "provider metadata" * 20,
            "details": {
                "headers": {"Authorization": f"Bearer {secret}"},
                "nested": {"credential": secret},
            },
        },
        secret_paths=["/details/nested/credential"],
    )
    assert event.redaction_state == "redacted"
    assert secret not in str(event.payload)
    assert event.blob_hash is not None
    assert secret.encode() not in ledger.blob_store.read(event.blob_hash)
    for database_file in hames_paths.root.glob("hames.db*"):
        assert secret.encode() not in database_file.read_bytes()


def test_inline_payload_hash_mismatch_is_detected(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = open_ledger(hames_paths, threshold=4096)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    event = ledger.append(
        session_id=session.id,
        event_type="user.message",
        payload={"content": "small"},
    )
    with ledger.database.connect() as connection:
        connection.execute("DROP TRIGGER events_no_update")
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE id = ?",
            ('{"content":"changed"}', event.id),
        )
    with pytest.raises(EventIntegrityError, match="payload hash mismatch"):
        ledger.verify_event(event.id)


def test_event_columns_require_exactly_one_storage_location(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = open_ledger(hames_paths)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    with ledger.database.connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO events(
                    id, session_id, type, schema_version, created_at,
                    payload_json, blob_hash, payload_hash, redaction_state
                ) VALUES ('broken', ?, 'runtime.notice', 1, 'now', NULL, NULL, ?, 'none')
                """,
                (session.id, "0" * 64),
            )
