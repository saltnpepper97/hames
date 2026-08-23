from __future__ import annotations

from pathlib import Path

import pytest

from hames.database import MIGRATIONS, Database
from hames.ledger import Ledger
from hames.memory import MemoryCandidate, MemoryStore
from hames.paths import HamesPaths


def _candidate(
    event_id: str,
    *,
    summary: str = "The user prefers concise documentation.",
    visibility: str = "global",
    supersedes_id: str | None = None,
) -> MemoryCandidate:
    return MemoryCandidate.model_validate(
        {
            "layer": "relationship",
            "visibility": visibility,
            "subject": "user:local",
            "predicate": "prefers_documentation_style",
            "value": "concise",
            "summary": summary,
            "confidence": 0.95,
            "importance": 0.9,
            "provenance_event_ids": [event_id],
            "supersedes_id": supersedes_id,
            "evidence_basis": "explicit_user",
        }
    )


def test_memory_schema_has_fts5_and_is_migration_seven(tmp_path: Path) -> None:
    database = Database(tmp_path / "memory.db")
    database.migrate()
    assert len(MIGRATIONS) == 7
    with database.connect() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    assert {"memory_records", "memory_anchors", "memory_provenance", "memory_fts"} <= tables


def test_migration_seven_upgrades_an_m5_database(tmp_path: Path) -> None:
    path = tmp_path / "m5.db"
    Database(path, migrations=MIGRATIONS[:6]).migrate()
    Database(path).migrate()
    with Database(path).connect() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 7
        assert connection.execute("SELECT count(*) FROM memory_records").fetchone()[0] == 0


def test_memory_activation_retrieval_and_retraction(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "I prefer concise documentation."},
    )
    store = MemoryStore(ledger)
    mutation = store.create_candidate(
        session=session,
        candidate=_candidate(user.id),
        run_id="run-1",
        origin_kind="automatic",
        activate=True,
        causation_id=user.id,
    )
    assert mutation.record.status == "active"
    assert [event.type for event in mutation.events] == ["memory.proposed", "memory.accepted"]
    selected, omitted, eligible = store.retrieve(
        session, "documentation style", limit=8, token_budget=2048
    )
    assert eligible == 1
    assert selected[0].record.id == mutation.record.id
    assert omitted == []

    retracted = store.transition(
        session=session,
        memory_id=mutation.record.id,
        action="retract",
        reason="user_forget",
    )
    assert retracted.record.status == "retracted"
    assert store.retrieve(session, "documentation", limit=8, token_budget=2048)[0] == []


def test_workspace_visibility_and_supersession(hames_paths: HamesPaths, tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    ledger = Ledger.open(hames_paths.database)
    first = ledger.create_session(
        working_directory=first_root,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    second = ledger.create_session(
        working_directory=second_root,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    original_event = ledger.append(
        session_id=first.id,
        agent_id=first.agent_id,
        event_type="user.message",
        payload={"content": "Use concise docs in this workspace."},
    )
    store = MemoryStore(ledger)
    original = store.create_candidate(
        session=first,
        candidate=_candidate(original_event.id, visibility="workspace"),
        run_id="run-original",
        origin_kind="automatic",
        activate=True,
        causation_id=original_event.id,
    ).record
    assert store.list_visible(first)[0].id == original.id
    assert store.list_visible(second) == []
    with pytest.raises(KeyError):
        store.get_visible(second, original.id)

    correction = ledger.append(
        session_id=first.id,
        agent_id=first.agent_id,
        event_type="user.message",
        payload={"content": "Actually, use detailed docs."},
    )
    replacement = store.create_candidate(
        session=first,
        candidate=_candidate(
            correction.id,
            summary="The user prefers detailed documentation.",
            visibility="workspace",
            supersedes_id=original.id,
        ),
        run_id="run-correction",
        origin_kind="automatic",
        activate=True,
        causation_id=correction.id,
    )
    assert replacement.record.status == "active"
    assert store.get(original.id).status == "superseded"
    assert replacement.events[-1].type == "memory.superseded"
