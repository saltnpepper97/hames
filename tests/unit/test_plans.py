from __future__ import annotations

from pathlib import Path

from hames.ledger import Ledger, Session
from hames.paths import HamesPaths
from hames.plans import PLAN_READY_MARKER, PlanStore, parse_plan, visible_plan_output


def _fixture(paths: HamesPaths, project: Path) -> tuple[PlanStore, Session]:
    paths.ensure_foundation()
    ledger = Ledger.open(paths.database)
    session = ledger.create_session(
        working_directory=project,
        agent_id="default",
        provider="fake",
        model="fixture",
        interaction_mode="plan",
    )
    return PlanStore(ledger), session


def test_ready_marker_is_hidden_and_plan_tasks_are_parsed() -> None:
    markdown = "# Improve search\n\n## Tasks\n\n- [ ] Add routing\n- [ ] Verify fallback"
    visible, ready = visible_plan_output(f"{markdown}\n\n{PLAN_READY_MARKER}")
    assert ready is True
    assert visible == markdown
    assert parse_plan(visible) == ("Improve search", ["Add routing", "Verify fallback"])


def test_plan_projection_keeps_revisions_and_tracks_execution(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    store, session = _fixture(hames_paths, tmp_path)
    first, _ = store.propose(
        session,
        run_id="run-1",
        markdown="# First\n\n- [ ] One",
        causation_id=None,
    )
    first_id = first.current.id if first.current else ""
    second, _ = store.propose(
        session,
        run_id="run-2",
        markdown="# Revised\n\n## Tasks\n- [ ] Two",
        causation_id=None,
    )
    assert len(second.revisions) == 2
    assert second.current is not None
    assert second.current.supersedes_plan_id == first_id
    plan_id = second.current.id
    state, _ = store.transition(
        session,
        plan_id,
        "plan.execution.requested",
        strategy="compact",
        execution_note="Preserve compatibility",
    )
    assert state.current is not None
    assert state.current.status == "requested"
    assert state.current.execution_note == "Preserve compatibility"
    state, _ = store.transition(session, plan_id, "plan.approved", strategy="compact")
    state, _ = store.transition(
        session,
        plan_id,
        "plan.execution.started",
        execution_run_id="run-3",
    )
    assert state.current is not None
    assert state.current.status == "executing"
    assert state.current.execution_run_id == "run-3"
    state, _ = store.transition(
        session,
        plan_id,
        "plan.execution.completed",
        execution_run_id="run-3",
    )
    assert state.current is not None
    assert state.current.status == "completed"
