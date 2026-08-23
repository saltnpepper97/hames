from __future__ import annotations

from pathlib import Path

import pytest

from hames.database import MIGRATIONS, Database
from hames.evolution import Scar, ScarStore, ScarTrigger, failure_signature_hash
from hames.ledger import Ledger, Session
from hames.paths import HamesPaths


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
