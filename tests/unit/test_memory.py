from __future__ import annotations

from pathlib import Path

import pytest

from hames.database import MIGRATIONS, Database
from hames.ledger import Ledger
from hames.memory import MemoryAnchor, MemoryCandidate, MemoryStore
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
    database = Database(tmp_path / "memory.db", migrations=MIGRATIONS[:7])
    database.migrate()
    assert MIGRATIONS[6].id == 7
    with database.connect() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
    assert {"memory_records", "memory_anchors", "memory_provenance", "memory_fts"} <= tables


def test_preempted_memory_job_returns_to_pending_without_spending_an_attempt(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    source = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "remember this"},
    )
    store = MemoryStore(ledger)
    job, _ = store.queue_job(
        session=session,
        kind="extraction",
        source_event_id=source.id,
        run_id="run-memory",
    )
    running, _ = store.start_job(job.id)
    assert running.attempts == 1

    paused, event = store.pause_job(job.id, reason="foreground request")

    assert paused.status == "pending"
    assert paused.attempts == 0
    assert paused.error_code is None
    assert event.type == "memory.job.paused"
    assert event.payload["error_code"] == "maintenance_preempted"


def test_migration_seven_upgrades_an_m5_database(tmp_path: Path) -> None:
    path = tmp_path / "m5.db"
    Database(path, migrations=MIGRATIONS[:6]).migrate()
    Database(path, migrations=MIGRATIONS[:7]).migrate()
    with Database(path, migrations=MIGRATIONS[:7]).connect() as connection:
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


def test_memory_delete_removes_record_and_retrieval_metadata(
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
        payload={"content": "Remember this only until I delete it."},
    )
    store = MemoryStore(ledger)
    mutation = store.create_candidate(
        session=session,
        candidate=_candidate(user.id),
        run_id="run-delete",
        origin_kind="explicit",
        activate=True,
        causation_id=user.id,
    )

    event = store.delete(
        session=session,
        memory_id=mutation.record.id,
        reason="explicit_user_request",
    )

    assert event.type == "memory.deleted"
    with pytest.raises(KeyError):
        store.get(mutation.record.id)
    with ledger.database.connect() as connection:
        assert connection.execute(
            "SELECT count(*) FROM memory_anchors WHERE memory_id = ?",
            (mutation.record.id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM memory_provenance WHERE memory_id = ?",
            (mutation.record.id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM memory_fts WHERE memory_id = ?",
            (mutation.record.id,),
        ).fetchone()[0] == 0


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


def test_episode_projection_is_deterministic_and_skips_routine_chat(
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
        payload={"content": "Inspect the README."},
    )
    started = ledger.append(
        session_id=session.id,
        run_id="notable-run",
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 2, "max_tool_calls": 2, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    tool = ledger.append(
        session_id=session.id,
        run_id="notable-run",
        agent_id=session.agent_id,
        event_type="tool.completed",
        payload={
            "tool_call_id": "call-1",
            "name": "read_file",
            "status": "completed",
            "summary": "read README.md",
            "content": "",
        },
        causation_id=started.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="notable-run",
        agent_id=session.agent_id,
        event_type="assistant.message",
        payload={"content": "The README was inspected.", "status": "completed"},
        causation_id=tool.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="notable-run",
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 2, "tool_calls": 1, "active_seconds": 1.0},
        causation_id=tool.id,
    )
    projected = MemoryStore(ledger).project_episode(session, "notable-run")
    assert projected is not None
    assert projected.record.layer == "episodic"
    assert projected.events[-1].type == "memory.episode.projected"
    assert "read README.md" in projected.record.summary
    restarted_projection = MemoryStore(ledger).project_episode(session, "notable-run")
    assert restarted_projection is not None
    assert restarted_projection.record.id == projected.record.id
    assert restarted_projection.events == ()

    routine_user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Hello"},
    )
    ledger.append(
        session_id=session.id,
        run_id="routine-run",
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 1, "max_tool_calls": 1, "max_active_seconds": 30.0},
        causation_id=routine_user.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="routine-run",
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 1, "tool_calls": 0, "active_seconds": 0.1},
    )
    assert MemoryStore(ledger).project_episode(session, "routine-run") is None


def test_semantic_memory_supports_flexible_and_session_team_anchors(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    root = ledger.create_session(
        working_directory=tmp_path,
        agent_id="builder",
        provider="fake",
        model="fixture",
    )
    evidence = ledger.append(
        session_id=root.id,
        agent_id=root.agent_id,
        event_type="user.message",
        payload={"content": "The gateway listens on a loopback address."},
    )
    store = MemoryStore(ledger)
    semantic = store.create_candidate(
        session=root,
        candidate=MemoryCandidate(
            layer="semantic",
            visibility="session_team",
            subject="hames:gateway",
            predicate="network_scope",
            value="loopback",
            summary="The Hames gateway listens on a loopback address.",
            confidence=1.0,
            importance=0.8,
            anchors=[MemoryAnchor(kind="component", value="gateway")],
            provenance_event_ids=[evidence.id],
            evidence_basis="explicit_user",
        ),
        run_id=None,
        origin_kind="explicit",
        activate=True,
        causation_id=evidence.id,
    ).record
    assert semantic.layer == "semantic"
    assert MemoryAnchor(kind="component", value="gateway") in semantic.anchors

    child = ledger.fork_session(
        root.id,
        fork_event_id=store.get(semantic.id).provenance_event_ids[0],
        agent_id="reviewer",
    )
    unrelated = ledger.create_session(
        working_directory=tmp_path,
        agent_id="builder",
        provider="fake",
        model="fixture",
    )
    assert store.get_visible(child, semantic.id).id == semantic.id
    with pytest.raises(KeyError):
        store.get_visible(unrelated, semantic.id)
