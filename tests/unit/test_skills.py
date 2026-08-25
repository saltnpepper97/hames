from __future__ import annotations

from pathlib import Path

import pytest

from hames.database import MIGRATIONS, Database
from hames.ledger import Ledger
from hames.paths import HamesPaths
from hames.skills import SkillDraft, SkillRegistry, parse_skill, task_similarity


def _registry(paths: HamesPaths, ledger: Ledger) -> SkillRegistry:
    return SkillRegistry(
        paths.skills,
        ledger,
        available_tools={"read_file", "list_dir", "write_file", "edit_file", "shell"},
    )


def _draft(
    *, instructions: str = "Read the failure, reproduce it, then run focused tests."
) -> SkillDraft:
    return SkillDraft(
        id="investigate-regression",
        name="Investigate Regression",
        description="Reproduce, narrow, and verify a software regression.",
        scope="workspace",
        tools=["read_file", "shell"],
        triggers=["regression", "failing test"],
        instructions=instructions,
    )


def test_skill_schema_is_migration_eight_and_upgrades_m6(tmp_path: Path) -> None:
    path = tmp_path / "m6.db"
    Database(path, migrations=MIGRATIONS[:7]).migrate()
    Database(path).migrate()
    assert len(MIGRATIONS) == 16
    with Database(path).connect() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 16
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"skills", "skill_versions", "skill_jobs", "skill_usage"} <= tables


def test_builtin_skills_are_discovered_loadable_and_read_only(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    registry = _registry(hames_paths, ledger)

    expected = {"linux-gui-testing", "visual-verification", "web-app-debugging"}
    catalog = registry.visible(session)
    assert expected <= {item.slug for item in catalog}

    for slug in expected:
        loaded = registry.get_visible(session, slug)
        metadata, instructions = parse_skill(
            (Path(loaded.package_path) / "SKILL.md").read_text(encoding="utf-8")
        )
        assert loaded.skill_id == f"builtin:{slug}"
        assert loaded.created_by == "builtin"
        assert loaded.scope == "global"
        assert loaded.status == "active"
        assert not loaded.pinned
        assert metadata.id == slug
        assert instructions == loaded.instructions
        assert registry.get(loaded.id) == loaded
        assert registry.history(loaded.skill_id) == [loaded]

    wayland = registry.visible(session, query="Wayland compositor")
    assert wayland[0].slug == "linux-gui-testing"

    web = registry.get_visible(session, "web-app-debugging")
    registry.record_usage(
        version_id=web.id,
        run_id="builtin-run",
        session_id=session.id,
        stage="loaded",
    )
    with ledger.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM skill_usage").fetchone()[0] == 0

    with pytest.raises(ValueError, match="Built-in Skill is read-only"):
        registry.set_archived(session, "visual-verification", archived=True)
    with pytest.raises(ValueError, match="Built-in Skill is read-only"):
        registry.create_draft(
            session=session,
            draft=SkillDraft(
                id="web-app-debugging",
                name="Replacement",
                description="Attempt to replace a bundled package.",
                scope="global",
                tools=["read_file"],
                triggers=["web app"],
                instructions="Replace the bundled package.",
            ),
            evidence_event_ids=[],
            created_by="user",
            run_id=None,
            causation_id="fixture",
        )


def test_preempted_skill_job_returns_to_pending_without_spending_an_attempt(
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
        payload={"content": "make this reusable"},
    )
    registry = _registry(hames_paths, ledger)
    job, _ = registry.queue_job(
        session=session,
        kind="author",
        source_event_id=source.id,
        run_id="run-skill",
        goal="Create a reusable workflow",
        scope="workspace",
    )
    running, _ = registry.start_job(job.id)
    assert running.attempts == 1

    paused, event = registry.pause_job(job.id, reason="foreground request")

    assert paused.status == "pending"
    assert paused.attempts == 0
    assert paused.error_code is None
    assert event.type == "skill.job.paused"
    assert event.payload["error_code"] == "maintenance_preempted"


def test_skill_parse_version_activation_and_scope(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    first = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    other_root = tmp_path / "other"
    other_root.mkdir()
    second = ledger.create_session(
        working_directory=other_root,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    evidence = ledger.append(
        session_id=first.id,
        agent_id=first.agent_id,
        event_type="user.message",
        payload={"content": "Fix this regression and run focused tests."},
    )
    registry = _registry(hames_paths, ledger)
    drafted = registry.create_draft(
        session=first,
        draft=_draft(),
        evidence_event_ids=[evidence.id],
        created_by="automatic",
        run_id="run-1",
        causation_id=evidence.id,
    )
    metadata, instructions = parse_skill(
        (Path(drafted.version.package_path) / "SKILL.md").read_text(encoding="utf-8")
    )
    assert metadata.version == 1
    assert instructions.startswith("Read the failure")
    active = registry.activate(
        session=first,
        version_id=drafted.version.id,
        causation_id=drafted.events[-1].id,
    )
    assert active.version.status == "active"
    assert registry.get_visible(first, "investigate-regression").id == active.version.id
    with pytest.raises(KeyError):
        registry.get_visible(second, "investigate-regression")

    correction = ledger.append(
        session_id=first.id,
        agent_id=first.agent_id,
        event_type="user.message",
        payload={"content": "Always reproduce before editing."},
    )
    replacement = registry.create_draft(
        session=first,
        draft=_draft(instructions="Reproduce before editing, then run focused tests."),
        evidence_event_ids=[correction.id],
        created_by="automatic",
        run_id="run-2",
        causation_id=correction.id,
        target_skill_id=active.version.skill_id,
    )
    activated = registry.activate(
        session=first,
        version_id=replacement.version.id,
        causation_id=replacement.events[-1].id,
    )
    assert activated.version.version == 2
    assert registry.get(active.version.id).status == "superseded"
    assert [item.version for item in registry.history(active.version.skill_id)] == [2, 1]


def test_skill_rollback_pin_and_package_integrity(hames_paths: HamesPaths, tmp_path: Path) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Use this workflow repeatedly."},
    )
    registry = _registry(hames_paths, ledger)
    first = registry.create_draft(
        session=session,
        draft=_draft(),
        evidence_event_ids=[evidence.id],
        created_by="agent",
        run_id=None,
        causation_id=evidence.id,
    )
    first_active = registry.activate(
        session=session, version_id=first.version.id, causation_id=first.events[-1].id
    )
    second = registry.create_draft(
        session=session,
        draft=_draft(instructions="A broken replacement."),
        evidence_event_ids=[evidence.id],
        created_by="agent",
        run_id=None,
        causation_id=evidence.id,
        target_skill_id=first_active.version.skill_id,
    )
    second_active = registry.activate(
        session=session, version_id=second.version.id, causation_id=second.events[-1].id
    )
    fallback, events = registry.quarantine_and_rollback(
        session,
        second_active.version.id,
        reason="script_test_failed",
        causation_id=second_active.events[-1].id,
    )
    assert fallback.id == first_active.version.id
    assert registry.get(second_active.version.id).status == "quarantined"
    assert events[-1].type == "skill.rolled_back"
    assert registry.set_pinned(session, fallback.slug, pinned=True).pinned
    assert not registry.set_pinned(session, fallback.slug, pinned=False).pinned

    skill_file = Path(fallback.package_path) / "SKILL.md"
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        registry.get(fallback.id)


def test_skill_activation_rejects_a_stale_correction(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Correct this repeated workflow."},
    )
    registry = _registry(hames_paths, ledger)
    original = registry.create_draft(
        session=session,
        draft=_draft(),
        evidence_event_ids=[evidence.id],
        created_by="automatic",
        run_id="run-1",
        causation_id=evidence.id,
    )
    active = registry.activate(
        session=session,
        version_id=original.version.id,
        causation_id=original.events[-1].id,
    )
    first = registry.create_draft(
        session=session,
        draft=_draft(instructions="First correction."),
        evidence_event_ids=[evidence.id],
        created_by="automatic",
        run_id="run-2",
        causation_id=evidence.id,
        target_skill_id=active.version.skill_id,
    )
    stale = registry.create_draft(
        session=session,
        draft=_draft(instructions="Stale correction."),
        evidence_event_ids=[evidence.id],
        created_by="automatic",
        run_id="run-3",
        causation_id=evidence.id,
        target_skill_id=active.version.skill_id,
    )
    registry.activate(
        session=session,
        version_id=first.version.id,
        causation_id=first.events[-1].id,
    )
    with pytest.raises(ValueError, match="base version changed"):
        registry.activate(
            session=session,
            version_id=stale.version.id,
            causation_id=stale.events[-1].id,
        )


def test_task_similarity_is_deterministic() -> None:
    assert task_similarity("fix the rust regression", "investigate the rust regression") == 0.6
    assert task_similarity("hello", "unrelated") == 0.0
