from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from hames.agent import load_agent
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
    plan_repair,
)
from hames.evolution_runtime import EvolutionManager
from hames.ledger import Ledger, Session
from hames.memory import MemoryStore
from hames.paths import HamesPaths
from hames.plugin_runtime import PluginManager
from hames.policy import PolicyGate
from hames.providers import (
    ModelRequest,
    Provider,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
    ToolCallDelta,
)
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
    detection: str = "explicit_correction",
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
        detection=detection,
        causation_id=evidence.id,
    )
    assert len(mutation.events) == 1
    assert mutation.events[0].type == "scar.recorded"
    return store, mutation.scar


def test_scar_schema_is_migration_nine_and_upgrades_m8(tmp_path: Path) -> None:
    path = tmp_path / "m8.db"
    Database(path, migrations=MIGRATIONS[:8]).migrate()
    Database(path).migrate()
    assert len(MIGRATIONS) == 16
    with Database(path).connect() as connection:
        assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 16
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


def test_user_edit_updates_diagnosis_without_rewriting_evidence(
    store: tuple[ScarStore, Ledger], tmp_path: Path
):
    scars, ledger = store
    session = _session(ledger, tmp_path)
    _, scar = _candidate(ledger, session, tmp_path)

    edited = scars.edit(
        session=session,
        scar_id=scar.id,
        title="Shell failures need a clearer diagnosis",
        severity="high",
        description="The failing shell command was retried without inspection.",
        expected_behavior="Inspect the first failure before choosing a retry.",
    )

    assert edited.scar.title == "Shell failures need a clearer diagnosis"
    assert edited.scar.severity == "high"
    assert edited.scar.failure_signature == scar.failure_signature
    assert edited.scar.evidence_event_ids == scar.evidence_event_ids
    assert [event.type for event in edited.events] == ["scar.edited"]
    changes_value = edited.events[0].payload["changes"]
    assert isinstance(changes_value, dict)
    changes = cast(dict[str, object], changes_value)
    assert set(changes) == {"title", "severity", "description", "expected_behavior"}

    unchanged = scars.edit(
        session=session,
        scar_id=scar.id,
        title=edited.scar.title,
        severity="high",
        description=edited.scar.description,
        expected_behavior=edited.scar.expected_behavior,
    )
    assert unchanged.events == ()

    with pytest.raises(ValueError, match="description is required"):
        scars.edit(
            session=session,
            scar_id=scar.id,
            title=edited.scar.title,
            severity="high",
            description="  ",
            expected_behavior=edited.scar.expected_behavior,
        )


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
    plugins = PluginManager(
        paths=hames_paths,
        ledger=ledger,
        config=config,
        events=EventBroker(),
        policy=PolicyGate(hames_paths.root),
    )
    manager = EvolutionManager(
        ledger=ledger,
        config=config,
        broker=EventBroker(),
        store=store,
        skills=registry,
        memory=MemoryStore(ledger),
        plugin_manager=plugins,
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
    assert scar.status == "guarded"
    assert scar.severity == "high"
    assert scar.detection == "explicit_correction"
    assert target.id in scar.evidence_event_ids
    correction_events = [
        event for event in ledger.list_events(session.id) if event.type == "user.correction"
    ]
    assert len(correction_events) == 1
    assert correction_events[0].payload["target_event_id"] == target.id
    memories = manager.memory.list_visible(session, layer="semantic")
    assert any("docs/plan.md" in record.summary for record in memories)

    again = await manager.submit_correction(
        session.id, content="THE MILESTONE FILE IS docs/plan.md"
    )
    assert again.id == scar.id
    assert again.status == "guarded"
    assert again.regression_count == 1
    assert len(manager.store.repairs_for_scar(scar.id)) == 2


async def test_conversational_correction_creates_one_scar(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, _ = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    _run_with_assistant(ledger, session, "run-1", "Actually that output was wrong")
    created = await manager.observe_run(session.id, "run-1")
    assert len(created) == 1
    assert created[0].status == "guarded"
    assert created[0].detection == "conversational_correction"

    _run_with_assistant(ledger, session, "run-2", "actually that output was wrong")
    second = await manager.observe_run(session.id, "run-2")
    assert [scar.id for scar in second] == [created[0].id]
    assert second[0].regression_count == 1
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
    assert [scar.id for scar in again] == []
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
        memory=MemoryStore(ledger),
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


def test_plan_repair_routes_to_weakest_layer(
    store: tuple[ScarStore, Ledger], tmp_path: Path
) -> None:
    _, ledger = store
    session = _session(ledger, tmp_path)
    preference = Scar(
        id="s1",
        title="Correction: I prefer short answers",
        scope="workspace",
        status="open",
        severity="high",
        failure_signature="explicit-correction:i prefer short answers",
        description="I prefer short answers without restating the question.",
        trigger=ScarTrigger(),
        expected_behavior="Answers must be short.",
        detection="explicit_correction",
        owner_agent_id=None,
        workspace_path=str(tmp_path),
        source_session_id=session.id,
        source_run_id=None,
        repair_layer=None,
        repair_reference=None,
        last_triggered_at="2026-01-01",
        successful_guard_count=0,
        regression_count=0,
        dismissed_reason=None,
        created_at="2026-01-01",
        updated_at="2026-01-01",
        evidence_event_ids=[],
    )
    plan = plan_repair(preference)
    assert plan is not None and plan.repair_layer == "relationship_memory"
    factual = preference.model_copy(update={"description": "the milestone file is docs/plan.md"})
    assert plan_repair(factual) is not None
    assert plan_repair(factual).repair_layer == "semantic_memory"  # type: ignore[union-attr]
    skill_scar = preference.model_copy(update={"detection": "skill_outcome_regression"})
    assert plan_repair(skill_scar).repair_layer == "skill"  # type: ignore[union-attr]
    missing_tool = preference.model_copy(
        update={
            "detection": "repeated_failure",
            "failure_signature": "tool:shell:rg: command not found",
        }
    )
    capability_plan = plan_repair(missing_tool)
    assert capability_plan is not None
    assert capability_plan.repair_layer == "capability_requirement"
    opaque = preference.model_copy(
        update={"detection": "repeated_failure", "failure_signature": "tool:shell:timeout #"}
    )
    assert plan_repair(opaque) is None


@pytest.mark.asyncio
async def test_capability_requirement_writes_proposal_without_enabling(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    hames_paths.ensure_foundation()
    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    _, scar = _candidate(
        ledger,
        session,
        tmp_path,
        signature="tool:shell:rg: command not found",
        detection="repeated_failure",
    )
    store.open(session=session, scar_id=scar.id, reason="missing capability")
    routed, _repair = await manager.propose_repair(session.id, scar.id)
    assert routed.status == "repair_proposed"
    assert routed.repair_layer == "capability_requirement"
    assert manager.plugin_manager is not None
    proposals = manager.plugin_manager.list_proposals()
    assert len(proposals) == 1
    assert proposals[0].status == "proposed"
    assert proposals[0].scar_id == scar.id
    assert (hames_paths.plugins / "proposals" / proposals[0].id / "plugin.toml").is_file()
    assert manager.plugin_manager.names() == set()
    with pytest.raises(KeyError):
        manager.plugin_manager.store.get(proposals[0].plugin_id)


async def test_memory_repair_auto_promotes_and_guards(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    scar = await manager.submit_correction(session.id, content="always cite the docs/plan.md file")
    assert scar.status == "guarded"
    assert scar.repair_layer == "semantic_memory"

    memories = manager.memory.list_visible(session, layer="semantic")
    assert any("docs/plan.md" in record.summary for record in memories)

    repairs = store.repairs_for_scar(scar.id)
    assert len(repairs) == 1
    assert repairs[0].status == "promoted"


async def test_unroutable_scar_requires_explicit_layer(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="tool.failed",
        payload={
            "tool_call_id": "c1",
            "name": "shell",
            "status": "failed",
            "summary": "command timed out after 120 seconds",
            "content": "",
        },
    )
    mutation = store.record_candidate(
        session=session,
        title="Repeated timeout",
        severity="medium",
        failure_signature="tool:shell:timed out after # seconds",
        description="Shell commands keep timing out.",
        expected_behavior="Timeouts must be diagnosed and avoided.",
        evidence_event_ids=[evidence.id],
        detection="repeated_failure",
        causation_id=evidence.id,
    )
    store.open(session=session, scar_id=mutation.scar.id, reason="evidence sufficient")
    with pytest.raises(ValueError, match="no autonomous repair"):
        await manager.propose_repair(session.id, mutation.scar.id)

    directed, repair = await manager.propose_repair(
        session.id, mutation.scar.id, layer_override="context_rule"
    )
    assert repair.repair_layer == "context_rule"
    assert repair.required_authority == "context_write"
    assert directed.status == "repair_proposed"


class _ScriptedProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self, responses: dict[str, dict[str, object]]) -> None:
        self.responses = responses
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        import json as _json

        self.requests.append(request)
        purpose = str(request.metadata["purpose"])
        payload = self.responses[purpose]
        name, arguments = next(iter(payload.items()))
        yield StreamEvent(kind=StreamEventKind.STARTED, provider_request_id=f"{purpose}-1")
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id=f"{purpose}-call",
                name=name,
                arguments_delta=_json.dumps(arguments),
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    async def aclose(self) -> None:
        return None


def _open_scar(
    manager: EvolutionManager, store: ScarStore, ledger: Ledger, session: Session, content: str
) -> Scar:
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": content},
    )
    mutation = store.record_candidate(
        session=session,
        title=f"Correction: {content[:60]}",
        severity="high",
        failure_signature=f"explicit-correction:{content.casefold()[:120]}",
        description=content,
        expected_behavior="Hames must incorporate this correction.",
        evidence_event_ids=[evidence.id],
        detection="explicit_correction",
        causation_id=evidence.id,
    )
    store.open(session=session, scar_id=mutation.scar.id, reason="evidence sufficient")
    return store.get(mutation.scar.id)


async def test_failing_model_evaluation_rejects_repair(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.evaluation import RepairEvaluator

    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    scar = _open_scar(manager, store, ledger, session, "cite docs/architecture.md")
    _, repair = await manager.propose_repair(session.id, scar.id, layer_override="context_rule")

    evaluator = RepairEvaluator(
        ledger=ledger,
        config=load_config(hames_paths),
        providers={
            "fake": _ScriptedProvider(
                {
                    "evolution_evaluation": {
                        "submit_repair_evaluation": {
                            "passed": False,
                            "score": 0.2,
                            "summary": "Repair does not address the documented failure.",
                            "findings": ["unrelated"],
                        }
                    }
                }
            )
        },
        broker=EventBroker(),
        store=store,
        memory=manager.memory,
        skills=manager.skills,
    )
    report = await evaluator.evaluate(session.id, repair.id)
    assert report["deterministic"]["passed"] is True
    assert report["model"]["passed"] is False
    assert store.get(scar.id).status == "open"
    assert store.get_repair(repair.id).status == "rejected"
    evaluated_events = [
        event for event in ledger.list_events(session.id) if event.type == "scar.repair.evaluated"
    ]
    assert {str(event.payload["kind"]) for event in evaluated_events} >= {"deterministic", "final"}


async def test_passing_authority_changing_repair_waits_for_approval(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.evaluation import RepairEvaluator

    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    scar = _open_scar(manager, store, ledger, session, "always cite the runbook file")
    _, repair = await manager.propose_repair(session.id, scar.id, layer_override="context_rule")
    evaluator = RepairEvaluator(
        ledger=ledger,
        config=load_config(hames_paths),
        providers={
            "fake": _ScriptedProvider(
                {
                    "evolution_evaluation": {
                        "submit_repair_evaluation": {
                            "passed": True,
                            "score": 0.95,
                            "summary": "Repair addresses the failure safely.",
                            "findings": [],
                        }
                    }
                }
            )
        },
        broker=EventBroker(),
        store=store,
        memory=manager.memory,
        skills=manager.skills,
    )
    report = await evaluator.evaluate(session.id, repair.id)
    assert report["model"]["passed"] is True
    assert report["status"] == "pending_approval"
    assert store.get(scar.id).status == "repair_proposed"
    assert store.get_repair(repair.id).status == "proposed"


async def test_exhausted_evolution_budget_skips_model_eval(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.evaluation import RepairEvaluator

    ledger = Ledger.open(hames_paths.database)
    config = load_config(hames_paths)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    for _index in range(config.evolution.max_background_model_calls_per_day):
        ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="model.requested",
            payload={
                "provider": "fake",
                "model": "fixture",
                "reasoning_effort": "",
                "agent_capsule_hash": "x",
                "purpose": "evolution_review",
            },
        )
    provider = _ScriptedProvider(
        {
            "evolution_evaluation": {
                "submit_repair_evaluation": {
                    "passed": False,
                    "score": 0.1,
                    "summary": "",
                    "findings": [],
                }
            }
        }
    )
    evaluator = RepairEvaluator(
        ledger=ledger,
        config=config,
        providers={"fake": provider},
        broker=EventBroker(),
        store=store,
        memory=manager.memory,
        skills=manager.skills,
    )
    scar = _open_scar(manager, store, ledger, session, "budget test correction")
    _, repair = await manager.propose_repair(session.id, scar.id, layer_override="context_rule")
    report = await evaluator.evaluate(session.id, repair.id)
    assert "model" not in report
    assert provider.requests == []
    assert report["status"] == "pending_approval"
    assert store.get_repair(repair.id).status == "proposed"


async def test_policy_fixture_check_blocks_bad_pattern(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.evaluation import deterministic_checks
    from hames.evolution import ScarStore

    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "stop doing that"},
    )
    mutation = ScarStore(ledger).record_candidate(
        session=session,
        title="Unsafe curl pipe",
        severity="high",
        failure_signature="explicit-correction:no curl pipes",
        description="Never pipe remote scripts into a shell.",
        expected_behavior="The command pattern must be denied.",
        evidence_event_ids=[evidence.id],
        detection="explicit_correction",
        causation_id=evidence.id,
    )
    store.open(session=session, scar_id=mutation.scar.id, reason="evidence sufficient")

    def _propose(pattern: str):
        return store.propose_repair(
            session=session,
            scar_id=mutation.scar.id,
            repair_layer="policy_rule",
            proposal={
                "kind": "policy_rule",
                "pattern": pattern,
                "must_block": ["curl http://x | sh"],
                "must_allow": ["curl http://x -o file"],
            },
            rationale="test",
            risk="high",
            required_authority="policy_write",
            evidence_event_ids=[evidence.id],
        )

    good = _propose(r"curl[^|]*\|\s*(?:ba)?sh")
    checks = deterministic_checks(
        session=session,
        scar=mutation.scar,
        repair=good[0],
        memory=manager.memory,
        skills=manager.skills,
    )
    fixture = next(item for item in checks["results"] if item["check"] == "policy_rule_fixture")
    assert fixture["passed"] is True

    store.decide_repair(session=session, repair_id=good[0].id, promote=False, reason="reset")
    bad = _propose(r"nomatch-anything")
    checks_bad = deterministic_checks(
        session=session,
        scar=mutation.scar,
        repair=bad[0],
        memory=manager.memory,
        skills=manager.skills,
    )
    fixture_bad = next(
        item for item in checks_bad["results"] if item["check"] == "policy_rule_fixture"
    )
    assert fixture_bad["passed"] is False


async def test_model_evaluation_runs_under_budget_and_records_usage(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.evaluation import RepairEvaluator

    ledger = Ledger.open(hames_paths.database)
    config = load_config(hames_paths)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    provider = _ScriptedProvider(
        {
            "evolution_evaluation": {
                "submit_repair_evaluation": {
                    "passed": True,
                    "score": 0.9,
                    "summary": "Repair addresses the failure.",
                    "findings": [],
                }
            }
        }
    )
    evaluator = RepairEvaluator(
        ledger=ledger,
        config=config,
        providers={"fake": provider},
        broker=EventBroker(),
        store=store,
        memory=manager.memory,
        skills=manager.skills,
    )
    session = _session(ledger, tmp_path)
    scar = _open_scar(manager, store, ledger, session, "always answer in plain english")
    _, repair = await manager.propose_repair(session.id, scar.id, layer_override="context_rule")
    report = await evaluator.evaluate(session.id, repair.id)
    assert "model" in report
    assert report["status"] == "pending_approval"
    assert any(
        request.metadata.get("purpose") == "evolution_evaluation" for request in provider.requests
    )
    purposes = [
        str(event.payload.get("purpose"))
        for event in ledger.list_events(session.id)
        if event.type == "model.requested"
    ]
    assert "evolution_evaluation" in purposes


async def test_reviewer_classification_disabled_by_default(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, _ = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    _run_with_assistant(ledger, session, "run-rv1", "please also update the changelog")
    created = await manager.observe_run(session.id, "run-rv1")
    assert created == []


async def test_reviewer_classification_creates_candidate_when_enabled(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    config = load_config(hames_paths)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    provider = _ScriptedProvider(
        {
            "evolution_review": {
                "submit_correction_classification": {
                    "is_correction": True,
                    "confidence": 0.9,
                }
            }
        }
    )
    manager.providers = cast(dict[str, Provider], {"fake": provider})
    manager.config = config.model_copy(
        update={"evolution": config.evolution.model_copy(update={"reviewer_model_enabled": True})}
    )
    session = _session(ledger, tmp_path)
    _run_with_assistant(ledger, session, "run-rv2", "please also update the changelog")
    created = await manager.observe_run(session.id, "run-rv2")
    assert len(created) == 1
    assert created[0].detection == "reviewer_classification"
    assert created[0].severity == "low"
    assert store.get(created[0].id).detection == "reviewer_classification"


async def test_guarded_scar_heals_after_threshold_of_clean_runs(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    config = load_config(hames_paths)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    scar = await manager.submit_correction(session.id, content="cite docs/architecture.md")
    assert scar.status == "guarded"

    for index in range(config.evolution.healing_threshold):
        _run_with_assistant(ledger, session, f"clean-{index}", f"normal task {index}")
        await manager.observe_run(session.id, f"clean-{index}")

    healed = store.get(scar.id)
    assert healed.status == "healed"
    assert healed.successful_guard_count >= config.evolution.healing_threshold


async def test_repeated_correction_after_repair_regresses_and_requeues(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    manager, store = _manager(hames_paths, ledger, tmp_path)
    session = _session(ledger, tmp_path)
    scar = await manager.submit_correction(session.id, content="always cite docs/plan.md")
    assert scar.status == "guarded"
    first_repair = store.repairs_for_scar(scar.id)[0]
    assert first_repair.version == 1

    again = await manager.submit_correction(session.id, content="ALWAYS cite docs/plan.md")
    assert again.id == scar.id
    assert again.status == "guarded"
    regressed_scar = store.get(scar.id)
    assert regressed_scar.regression_count == 1
    repairs = store.repairs_for_scar(scar.id)
    assert len(repairs) == 2
    assert repairs[0].version == 2
    assert repairs[0].status == "promoted"


def test_guarded_scars_injected_into_context_only_when_matching(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.context import compile_context as compile_ctx

    ledger = Ledger.open(hames_paths.database)
    _, _store = _manager(hames_paths, ledger, tmp_path)
    del _store
    session = _session(ledger, tmp_path)
    hames_paths.ensure_foundation()
    capsule = load_agent(hames_paths.default_agent)

    def _compile(scars: list[tuple[str, str, str]]):
        return compile_ctx(
            ledger.get_session(session.id),
            ledger.replay(session.id),
            capsule,
            [],
            "safe",
            load_config(hames_paths).context,
            run_id="ctx-run",
            active_scars=scars,
        )

    empty = _compile([])
    assert not any(s.source_id == "evolution.scar" for s in empty.manifest.selected_sources)
    guarded = _compile([("scar-1", "Cite the plan", "Always cite docs/plan.md")])
    scar_sources = [s for s in guarded.manifest.selected_sources if s.source_id == "evolution.scar"]
    assert len(scar_sources) == 1
    assert scar_sources[0].source_type == "scar"
    assert scar_sources[0].origin == "evolution"


def test_run_manager_selects_only_model_behavior_guards(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.control import ControlStore
    from hames.providers.registry import configured_providers
    from hames.runtime import RunManager

    ledger = Ledger.open(hames_paths.database)
    config = load_config(hames_paths)
    paths = hames_paths
    runs = RunManager(
        ledger=ledger,
        paths=paths,
        config=config,
        controls=ControlStore(Database(hames_paths.database)),
        providers={name: p for name, p in configured_providers(config).items()},
        broker=EventBroker(),
    )
    session = _session(ledger, tmp_path)
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "fix"},
    )
    mutation = runs.scar_store.record_candidate(
        session=session,
        title="Model-behavior guard",
        severity="medium",
        failure_signature="explicit-correction:model behavior",
        description="d",
        expected_behavior="e",
        evidence_event_ids=[evidence.id],
        detection="explicit_correction",
        causation_id=evidence.id,
    )
    runs.scar_store.open(session=session, scar_id=mutation.scar.id, reason="evidence sufficient")
    runs.scar_store.propose_repair(
        session=session,
        scar_id=mutation.scar.id,
        repair_layer="semantic_memory",
        proposal={"kind": "memory_record", "subject": "s", "predicate": "p"},
        rationale="r",
        risk="low",
        required_authority="memory_write",
        evidence_event_ids=[evidence.id],
    )
    repair = runs.scar_store.repairs_for_scar(mutation.scar.id)[0]
    runs.scar_store.decide_repair(session=session, repair_id=repair.id, promote=True, reason="ok")

    selected = runs.guarded_scars_for_context(ledger.get_session(session.id), [])
    assert [item[0] for item in selected] == [mutation.scar.id]

    runs.scar_store.mark_healed(session=session, scar_id=mutation.scar.id, reason="done")
    assert runs.guarded_scars_for_context(ledger.get_session(session.id), []) == []
