from datetime import UTC, datetime
from pathlib import Path

import pytest

from hames.automations import AutomationDefinition, AutomationStore, next_occurrence
from hames.database import Database


def spec(**changes: object) -> AutomationDefinition:
    return AutomationDefinition.model_validate(
        {
            "title": "Morning review",
            "instructions": "Read and report",
            "working_directory": "/tmp",
            "timezone": "America/Halifax",
            **changes,
        }
    )


def test_local_time_and_weekdays() -> None:
    value = next_occurrence(
        spec(frequency="weekly", weekdays=[0, 2], time="10:00"),
        datetime(2026, 9, 18, 15, tzinfo=UTC),
    )
    assert value == datetime(2026, 9, 21, 13, tzinfo=UTC)


def test_spring_gap_moves_to_first_valid_time_and_fall_does_not_repeat() -> None:
    spring = next_occurrence(spec(time="02:30"), datetime(2026, 3, 8, 0, tzinfo=UTC))
    assert spring == datetime(2026, 3, 8, 6, tzinfo=UTC)
    fall = next_occurrence(spec(time="01:30"), datetime(2026, 11, 1, 4, 45, tzinfo=UTC))
    assert fall == datetime(2026, 11, 2, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "changes",
    [
        {"time": "25:00"},
        {"timezone": "Not/AZone"},
        {"frequency": "weekly", "weekdays": []},
        {"frequency": "once", "date": ""},
    ],
)
def test_invalid_schedule_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        spec(**changes)


def test_durable_claim_no_overlap_and_no_backlog(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.migrate()
    store = AutomationStore(database)
    item = store.save(spec(enabled=True))
    with database.connect() as db:
        db.execute("UPDATE automations SET next_run='2020-01-01T00:00:00+00:00'")
    assert store.enqueue(item["id"], scheduled=True)
    assert store.enqueue(item["id"], scheduled=True) is None
    assert len(AutomationStore(database).history()) == 1
    with pytest.raises(ValueError, match="already"):
        store.enqueue(item["id"])
    store.save(spec(enabled=False), item["id"])
    assert store.history()[0]["status"] == "skipped"
    assert store.enqueue(item["id"])  # Explicit Run now also works while paused.


def test_skip_missed_and_one_time_stops(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.db")
    database.migrate()
    store = AutomationStore(database)
    item = store.save(spec(enabled=True, catch_up=False))
    with database.connect() as db:
        db.execute("UPDATE automations SET next_run='2020-01-01T00:00:00+00:00'")
    store.enqueue(item["id"], scheduled=True)
    assert store.history()[0]["status"] == "skipped"
    assert store.get(item["id"])["next_run"] > datetime.now(UTC).isoformat()
