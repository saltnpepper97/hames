from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hames.blobs import BlobIntegrityError, BlobStore
from hames.ledger import EventIntegrityError, Ledger
from hames.paths import HamesPaths


def open_ledger(paths: HamesPaths, *, threshold: int = 64) -> Ledger:
    paths.ensure_foundation()
    ledger = Ledger.open(paths.database)
    ledger.blob_threshold_bytes = threshold
    return ledger


def test_blob_store_deduplicates_and_detects_corruption(tmp_path: Path) -> None:
    store = BlobStore(tmp_path / "blobs")
    digest = store.put(b"same content")
    assert store.put(b"same content") == digest
    assert store.read(digest) == b"same content"
    target = store.path_for(digest)
    assert target.stat().st_mode & 0o777 == 0o600
    target.write_bytes(b"corrupt")
    with pytest.raises(BlobIntegrityError, match="corrupt blob"):
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
    assert secret.encode() not in hames_paths.database.read_bytes()


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
