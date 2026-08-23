from __future__ import annotations

from pathlib import Path

import pytest

from hames.broker import EventBroker
from hames.config import load_config
from hames.database import MIGRATIONS, Database
from hames.evolution import (
    Scar,
    ScarStore,
    ScarTrigger,
    failure_signature_hash,
    looks_like_correction,
    normalize_failure_signature,
)
from hames.evolution_runtime import EvolutionManager
from hames.ledger import Ledger, Session
from hames.paths import HamesPaths
from hames.skills import SkillDraft, SkillRegistry


@pytest.fixture()
def store(hames_paths: HamesPaths) -> tuple[ScarStore, Ledger]:
    ledger = Ledger.open(hames_paths.database)
    return ScarStore(ledger), ledger


def _session(ledger: Ledger, tmp_path: Path, name: str = "ws") -> Session:
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    return ledger.create_session(
        working_directory=root,
        agent_id="default",
        provider="fake",
        model="fixture",
    )


def _candidate(
    ledger: Ledger,
    session: Session,
    tmp_path: Path,
    *,
    signature: str = "tool shell failed with exit code 42",
) -> tuple[ScarStore, Scar]:
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "that command was wrong"},
    )
    store = ScarStore(ledger)
    mutation = store.record_candidate(
        session=session,
        title="Shell exit 42 misdiagnosed",
        severity="medium",
        failure_signature=signature,
        description="Hames treated exit 42 as success and reported completion.",
        expected_behavior="Exit 42 must be surfaced as a failure with remediation.",
        evidence_event_ids=[evidence.id],
        trigger=ScarTrigger(tool_error_signatures=["shell:exit-42"]),
        run_id=None,
        causation_id=evidence.id,
    )
    assert len(mutation.events) == 1
    assert mutation.events[0].type == "scar.recorded"
    return store, mutation.scar


def test_scar_schema_is_migration_nine_and_upgrades_m8(tmp_path: Path) -> None:
    path = tmp_path / "m8.db"
    Database(path, migrations=MIGRATIONS[:8]).migrate()
    Database(path).migrate()
    assert len(MIGRATIONS) == 9
    with Database(path).connect() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 9
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"scars", "scar_repairs", "scar_evidence"} <= tables


def test_candidate_requires_visible_evidence(store: tuple[ScarStore, Ledger], tmp_path: Path):
    scars, ledger = store
    session = _session(ledger, tmp_path)
    with pytest.raises(ValueError, match="not visible"):
        scars.record_candidate(
            session=session,
            title="Missing evidence",
            severity="low",
            failure_signature="missing evidence failure",
            description="d",
            expected_behavior="e",
            evidence_event_ids=["nonexistent"],
            causation_id=None,
        )


def test_full_lifecycle_through_healing(
    store: tuple[ScarStore, Ledger], hames_paths: HamesPaths, tmp_path: Path
):
    scars, ledger = store
    session = _session(ledger, tmp_path)
    _, scar = _candidate(ledger, session, tmp_path)
    scar_id = scar.id
    assert scar.status == "candidate"

    opened = scars.open(session=session, scar_id=scar_id, reason="evidence sufficient")
    assert opened.scar.status == "open"

    repair, proposed = scars.propose_repair(
        session=session,
        scar_id=scar_id,
        repair_layer="semantic_memory",
        proposal={"memory": {"subject": "exit codes", "value": "42 means retry"}},
        rationale="Stable fact was wrong.",
        risk="low",
        required_authority="none",
        evidence_event_ids=[opened.events[0].id],
    )
    assert repair.version == 1
    assert repair.status == "proposed"
    assert {event.type for event in proposed.events} >= {
        "scar.repair.proposed",
        "scar.repair_proposed",
    }
    assert proposed.scar.status == "repair_proposed"

    with pytest.raises(ValueError, match="must be open or regressed"):
        scars.propose_repair(
            session=session,
            scar_id=scar_id,
            repair_layer="skill",
            proposal={},
            rationale="duplicate",
            risk="low",
            required_authority="none",
            evidence_event_ids=[opened.events[0].id],
        )

    promoted = scars.decide_repair(
        session=session,
        repair_id=repair.id,
        promote=True,
        reason="deterministic checks passed",
    )
    assert promoted.scar.status == "guarded"
    assert promoted.scar.repair_reference == repair.id

    for expected_count in (1, 2, 3):
        counted, event = scars.record_guard_success(
            session=session, scar_id=scar_id, run_id=f"run-{expected_count}", held=True
        )
        assert event.type == "scar.guard.succeeded"
        assert counted.successful_guard_count == expected_count

    healed = scars.mark_healed(session=session, scar_id=scar_id, reason="three clean passes")
    assert healed.scar.status == "healed"


def test_regression_reopens_with_second_repair_version(
    store: tuple[ScarStore, Ledger], tmp_path: Path
):
    scars, ledger = store
    session = _session(ledger, tmp_path)
    _, scar = _candidate(ledger, session, tmp_path)
    scars.open(session=session, scar_id=scar.id, reason="evidence sufficient")
    repair_v1, _ = scars.propose_repair(
        session=session,
        scar_id=scar.id,
        repair_layer="context_rule",
        proposal={"require": "operational.current_milestone"},
        rationale="Context rule.",
        risk="medium",
        required_authority="context_write",
        evidence_event_ids=[scar.evidence_event_ids[0]],
    )
    scars.decide_repair(session=session, repair_id=repair_v1.id, promote=True, reason="approved")

    regressed = scars.mark_regressed(session=session, scar_id=scar.id, reason="failure returned")
    assert regressed.scar.status == "regressed"
    assert regressed.scar.regression_count == 1

    repair_v2, _ = scars.propose_repair(
        session=session,
        scar_id=scar.id,
        repair_layer="policy_rule",
        proposal={"deny_shell": ["rm -rf /"]},
        rationale="Stronger guard needed.",
        risk="high",
        required_authority="policy_write",
        evidence_event_ids=[scar.evidence_event_ids[0]],
        created_by="automatic",
    )
    assert repair_v2.version == 2
    assert repair_v2.previous_scar_status == "regressed"

    rejected = scars.decide_repair(
        session=session, repair_id=repair_v2.id, promote=False, reason="weakened protection"
    )
    assert rejected.scar.status == "regressed"
    assert scars.get_repair(repair_v2.id).status == "rejected"

    dismissed = scars.dismiss(session=session, scar_id=scar.id, reason="user override")
    assert dismissed.scar.status == "dismissed"
    with pytest.raises(ValueError, match="cannot move"):
        scars.open(session=session, scar_id=scar.id, reason="reopen after dismissal")


def test_workspace_scars_are_invisible_from_other_workspaces(
    store: tuple[ScarStore, Ledger], tmp_path: Path
):
    scars, ledger = store
    first = _session(ledger, tmp_path, "first")
    other = _session(ledger, tmp_path, "second")
    _, scar = _candidate(ledger, first, tmp_path)
    with pytest.raises(KeyError):
        scars.get_visible(other, scar.id)
    assert scars.list_scars(other) == []
    assert scars.list_scars(first)[0].id == scar.id
    assert scars.find_active_by_signature(other, "tool shell failed with exit code 42") is None
    assert (
        scars.find_active_by_signature(first, "TOOL shell   FAILED with Exit Code 42") is not None
    )


def test_signature_hash_is_whitespace_and_case_insensitive() -> None:
    assert failure_signature_hash("Same   signature") == failure_signature_hash("same signature")


def _manager(
    hames_paths: HamesPaths, ledger: Ledger, tmp_path: Path
) -> tuple[EvolutionManager, ScarStore]:
    config = load_config(hames_paths)
    store = ScarStore(ledger)
    registry = SkillRegistry(
        hames_paths.skills,
        ledger,
        available_tools={"read_file", "list_dir", "write_file", "edit_file", "shell"},
    )
    manager = EvolutionManager(
        ledger=ledger,
        config=config,
        broker=EventBroker(),
        store=store,
        skills=registry,
    )
    return manager, store


def _run_with_assistant(
    ledger: Ledger,
    session: Session,
    run_id: str,
    task: str,
    *,
    assistant_status: str = "completed",
) -> None:
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": task},
    )
    cause = ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 2, "max_tool_calls": 4, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    cause = ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="assistant.message",
        payload={"content": "Here is the result.", "status": assistant_status},
        causation_id=cause.id,
    )
    ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 1, "tool_calls": 0, "active_seconds": 0.05},
        causation_id=cause.id,
    )


def _failed_run(ledger: Ledger, session: Session, run_id: str, task: str) -> None:
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": task},
    )
    cause = ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 2, "max_tool_calls": 4, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    cause = ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="tool.failed",
        payload={
            "tool_call_id": f"call-{run_id}",
            "name": "shell",
            "status": "failed",
            "summary": "command failed with exit code 42 (see log entry 12345)",
            "content": "",
        },
        causation_id=cause.id,
    )
    ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="run.failed",
        payload={"code": "tool_failed", "message": "shell exited 42", "retryable": False},
        causation_id=cause.id,
    )


def test_correction_language_detection() -> None:
    assert looks_like_correction("Actually the file is docs/plan.md")
    assert looks_like_correction("that was wrong, try again")
    assert not looks_like_correction("great work, thanks")


def test_failure_signature_normalizes_volatile_detail(
    store: tuple[ScarStore, Ledger], tmp_path: Path
) -> None:
    _, ledger = store
    session = _session(ledger, tmp_path)
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "run it"},
    )
    first = ledger.append(
        session_id=session.id,
        run_id="r1",
        agent_id=session.agent_id,
        event_type="tool.failed",
        payload={
            "tool_call_id": "c1",
            "name": "shell",
            "status": "failed",
            "summary": "command failed with exit code 42 (log 1001)",
            "content": "",
        },
        causation_id=user.id,
    )
    second = ledger.append(
        session_id=session.id,
        run_id="r2",
        agent_id=session.agent_id,
        event_type="tool.failed",
        payload={
            "tool_call_id": "c2",
            "name": "shell",
            "status": "failed",
            "summary": "command failed with exit code 42 (log 9999)",
            "content": "",
        },
        causation_id=user.id,
    )
    assert normalize_failure_signature(first) == normalize_failure_signature(second)
    assert (
        normalize_failure_signature(first) == "tool:shell:command failed with exit code # (log #)"
    )
    policy_event = ledger.append(
        session_id=session.id,
        run_id="r3",
        agent_id=session.agent_id,
        event_type="policy.decided",
        payload={"tool_call_id": "c3", "decision": "deny", "reason": "secret", "risk": "high"},
        causation_id=user.id,
    )
    assert normalize_failure_signature(policy_event) == "policy:secret"


async def test_explicit_correction_opens_scar_and_deduplicates(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, _ = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    target = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="assistant.message",
        payload={"content": "The milestone file is plan.txt", "status": "completed"},
    )
    scar = await manager.submit_correction(
        session.id,
        content="the milestone file is docs/plan.md",
        target_event_id=target.id,
    )
    assert scar.status == "open"
    assert scar.severity == "high"
    assert scar.detection == "explicit_correction"
    assert target.id in scar.evidence_event_ids
    correction_events = [
        event for event in ledger.list_events(session.id) if event.type == "user.correction"
    ]
    assert len(correction_events) == 1
    assert correction_events[0].payload["target_event_id"] == target.id

    again = await manager.submit_correction(
        session.id, content="THE MILESTONE FILE IS docs/plan.md"
    )
    assert again.id == scar.id
    assert again.last_triggered_at >= scar.last_triggered_at


async def test_conversational_correction_creates_one_scar(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, _ = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    _run_with_assistant(ledger, session, "run-1", "Actually that output was wrong")
    created = await manager.observe_run(session.id, "run-1")
    assert len(created) == 1
    assert created[0].status == "open"
    assert created[0].detection == "conversational_correction"

    _run_with_assistant(ledger, session, "run-2", "actually that output was wrong")
    second = await manager.observe_run(session.id, "run-2")
    assert second == []
    scars = manager.store.list_scars(session)
    assert len(scars) == 1

    _run_with_assistant(ledger, session, "run-3", "totally fine request")
    third = await manager.observe_run(session.id, "run-3")
    assert third == []


async def test_repeated_failures_open_scar_after_threshold(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, _ = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    for index in (1, 2):
        _failed_run(ledger, session, f"run-{index}", f"do thing {index}")
    _failed_run(ledger, session, "run-3", "do thing 3")
    created = await manager.observe_run(session.id, "run-3")
    assert len(created) == 2
    assert {scar.detection for scar in created} == {"repeated_failure"}
    assert all(scar.status == "open" for scar in created)
    assert sum(scar.failure_signature.startswith("tool:shell:") for scar in created) == 1
    assert sum(scar.failure_signature == "provider:tool_failed" for scar in created) == 1

    _failed_run(ledger, session, "run-4", "do thing 4")
    again = await manager.observe_run(session.id, "run-4")
    assert again == []
    assert len(manager.store.list_scars(session)) == 2
    triggered = [
        event for event in ledger.list_events(session.id) if event.type == "scar.triggered"
    ]
    assert len(triggered) == 2


async def test_skill_regression_references_exact_version(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    config = load_config(hames_paths)
    store = ScarStore(ledger)
    registry = SkillRegistry(
        hames_paths.skills,
        ledger,
        available_tools={"read_file", "list_dir", "write_file", "edit_file", "shell"},
    )
    manager = EvolutionManager(
        ledger=ledger,
        config=config,
        broker=EventBroker(),
        store=store,
        skills=registry,
    )
    session = _session(ledger, tmp_path)
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "deploy checklist"},
    )
    draft = registry.create_draft(
        session=session,
        draft=SkillDraft(
            id="deploy-checklist",
            name="Deploy Checklist",
            description="Run the deploy checklist.",
            scope="workspace",
            tools=["read_file"],
            triggers=["deploy"],
            instructions="Follow the deploy steps.",
        ),
        evidence_event_ids=[evidence.id],
        created_by="user",
        run_id=None,
        causation_id=evidence.id,
    )
    activated = registry.activate(
        session=session, version_id=draft.version.id, causation_id=evidence.id
    )
    for index in range(config.evolution.recurrence_threshold):
        registry.record_usage(
            version_id=draft.version.id,
            run_id=f"usage-run-{index}",
            session_id=session.id,
            stage="loaded",
        )
        registry.record_run_outcomes(
            session=session,
            run_id=f"usage-run-{index}",
            outcome="failed",
            tool_calls=1,
            correction=False,
            causation_id=activated.events[-1].id,
        )

    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "deploy again"},
    )
    cause = ledger.append(
        session_id=session.id,
        run_id="regression-run",
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 1, "max_tool_calls": 1, "max_active_seconds": 5.0},
        causation_id=user.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="regression-run",
        agent_id=session.agent_id,
        event_type="skill.loaded",
        payload={
            "skill_id": draft.version.skill_id,
            "version_id": draft.version.id,
            "slug": "deploy-checklist",
            "version": 1,
            "content_hash": draft.version.content_hash,
            "reason": "matched",
        },
        causation_id=cause.id,
    )
    ledger.append(
        session_id=session.id,
        run_id="regression-run",
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 1, "tool_calls": 0, "active_seconds": 0.01},
        causation_id=cause.id,
    )
    created = await manager.observe_run(session.id, "regression-run")
    assert len(created) == 1
    scar = created[0]
    assert scar.detection == "skill_outcome_regression"
    assert scar.severity == "high"
    assert scar.trigger.skill_ids == [draft.version.id]
