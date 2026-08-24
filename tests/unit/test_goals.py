from __future__ import annotations

from pathlib import Path

import pytest

from hames.goals import GoalStore
from hames.ledger import Ledger, Session
from hames.paths import HamesPaths


def _fixture(paths: HamesPaths, project: Path) -> tuple[GoalStore, Session]:
    paths.ensure_foundation()
    ledger = Ledger.open(paths.database)
    session = ledger.create_session(
        working_directory=project,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    return GoalStore(ledger), session


def test_goal_projection_reconstructs_progress_pause_and_resume(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    store, session = _fixture(hames_paths, tmp_path)
    goal, _ = store.create(session, "Ship the feature")
    goal, _ = store.transition(session, goal.id, "goal.step.started", run_id="run-1", step=1)
    assert goal.status == "running"
    assert goal.current_run_id == "run-1"
    goal, _ = store.transition(
        session,
        goal.id,
        "goal.progressed",
        run_id="run-1",
        summary="Tests now pass",
        evidence=["pytest passed"],
    )
    assert goal.latest_summary == "Tests now pass"
    goal, _ = store.transition(session, goal.id, "goal.paused", summary="Paused by user")
    assert goal.status == "paused"
    goal, _ = store.transition(session, goal.id, "goal.resumed")
    assert goal.status == "running"
    assert store.current(session.id) == goal


def test_one_current_goal_is_enforced_and_achievement_releases_the_session(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    store, session = _fixture(hames_paths, tmp_path)
    goal, _ = store.create(session, "First")
    with pytest.raises(ValueError, match="already has a current goal"):
        store.create(session, "Second")
    achieved, _ = store.transition(
        session,
        goal.id,
        "goal.achieved",
        summary="Done",
        evidence=["verified"],
    )
    assert achieved.status == "achieved"
    assert store.current(session.id) is None
    next_goal, _ = store.create(session, "Second")
    assert next_goal.objective == "Second"
