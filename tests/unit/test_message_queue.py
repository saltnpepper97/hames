from __future__ import annotations

from pathlib import Path

import pytest

from hames.ledger import Ledger
from hames.message_queue import MessageQueueStore, QueueFullError
from hames.paths import HamesPaths


def test_durable_queue_is_bounded_fifo_and_recallable(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    store = MessageQueueStore(ledger)
    first = store.enqueue(
        session.id,
        "first",
        remember=False,
        paste_spans=[],
    ).item
    second = store.enqueue(
        session.id,
        "second pasted",
        remember=True,
        paste_spans=[{"start_byte": 7, "end_byte": 13, "line_count": 1, "byte_count": 6}],
    ).item
    assert first is not None and second is not None
    assert [item.content for item in store.state(session.id).items] == ["first", "second pasted"]

    with pytest.raises(QueueFullError):
        store.enqueue(session.id, "third", remember=False, paste_spans=[])

    recalled = store.take_latest(session.id, reason="editing")
    assert recalled.item is not None
    assert recalled.item.id == second.id
    assert recalled.item.paste_spans[0]["byte_count"] == 6
    promoted = store.take_oldest(session.id, reason="promoted")
    assert promoted.item is not None
    assert promoted.item.id == first.id
    assert promoted.event.type == "queue.promoted"
    assert store.state(session.id).items == []


def test_queue_pause_state_survives_store_recreation(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    store = MessageQueueStore(ledger)
    store.enqueue(session.id, "later", remember=False, paste_spans=[])
    store.set_paused(session.id, True)

    reopened = MessageQueueStore(Ledger.open(hames_paths.database))
    assert reopened.state(session.id).paused is True
    assert reopened.recoverable_sessions() == []
