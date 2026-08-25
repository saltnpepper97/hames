from __future__ import annotations

from pathlib import Path

import pytest

from hames.ledger import Ledger, Session
from hames.paths import HamesPaths
from hames.tasks import TaskStore


def _fixture(paths: HamesPaths, project: Path) -> tuple[TaskStore, Session]:
    paths.ensure_foundation()
    ledger = Ledger.open(paths.database)
    session = ledger.create_session(
        working_directory=project,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    return TaskStore(ledger), session


def test_task_projection_supports_replace_add_reorder_status_and_remove(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    store, session = _fixture(hames_paths, tmp_path)
    tasks, _ = store.replace(session, title="Approved plan", tasks=["First", "Second"])
    assert [item.text for item in tasks.items] == ["First", "Second"]
    tasks, _ = store.add(session, text="Discovered", position=1)
    assert [item.text for item in tasks.items] == ["First", "Discovered", "Second"]

    first_id = tasks.items[0].id
    discovered_id = tasks.items[1].id
    tasks, _ = store.update(session, first_id, status="in_progress")
    tasks, _ = store.update(session, discovered_id, status="in_progress", position=0)
    assert tasks.items[0].id == discovered_id
    assert tasks.items[0].status == "in_progress"
    assert next(item for item in tasks.items if item.id == first_id).status == "pending"

    tasks, _ = store.update(session, discovered_id, status="completed", text="Found work")
    assert tasks.items[0].text == "Found work"
    tasks, _ = store.remove(session, discovered_id)
    assert all(item.id != discovered_id for item in tasks.items)
    assert [item.position for item in tasks.items] == list(range(len(tasks.items)))


def test_task_updates_reject_empty_changes_and_unknown_ids(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    store, session = _fixture(hames_paths, tmp_path)
    tasks, _ = store.add(session, text="Keep me")
    with pytest.raises(ValueError, match="requires"):
        store.update(session, tasks.items[0].id)
    with pytest.raises(KeyError):
        store.remove(session, "missing")


def test_task_projection_repairs_legacy_false_codex_read_only_blocker(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    store, session = _fixture(hames_paths, tmp_path)
    tasks, _ = store.add(session, text="Create the requested file")
    store.update(
        session,
        tasks.items[0].id,
        text=("Create the requested file (blocked: current session filesystem is read-only)."),
        status="blocked",
    )
    store.ledger.close_session(session.id)

    repaired = store.current(session.id)

    assert repaired.items[0].text == "Create the requested file"
    assert repaired.items[0].status == "pending"
