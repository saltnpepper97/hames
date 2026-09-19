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

    third = store.enqueue(session.id, "third", remember=False, paste_spans=[]).item
    assert third is not None
    with pytest.raises(QueueFullError):
        store.enqueue(session.id, "fourth", remember=False, paste_spans=[])
    store.take(session.id, third.id, reason="removed")
    replacement = store.enqueue(session.id, "replacement", remember=False, paste_spans=[]).item
    assert replacement is not None
    store.take(session.id, replacement.id, reason="removed")

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


def test_priority_enqueue_preserves_existing_turns_and_moves_to_front(
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
    older = store.enqueue(session.id, "older", remember=False, paste_spans=[]).item
    priority = store.enqueue(
        session.id, "send now", remember=False, paste_spans=[], priority=True
    ).item

    assert older is not None and priority is not None
    assert priority.position == 1
    assert [item.content for item in store.state(session.id).items] == ["send now", "older"]


def test_existing_queue_item_can_be_prioritized_without_recreating_it(
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
    first = store.enqueue(session.id, "first", remember=False, paste_spans=[]).item
    second = store.enqueue(session.id, "second", remember=False, paste_spans=[]).item
    assert first is not None and second is not None

    prioritized = store.prioritize(session.id, second.id, reason="send_now")

    assert prioritized.item is not None
    assert prioritized.item.id == second.id
    assert prioritized.item.position == 1
    assert prioritized.event.type == "queue.prioritized"
    assert [item.id for item in prioritized.state.items] == [second.id, first.id]


def test_concurrent_submissions_cannot_exceed_three_pending(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )

    def enqueue(index: int) -> bool:
        store = MessageQueueStore(ledger)
        try:
            store.enqueue(session.id, str(index), remember=False, paste_spans=[])
            return True
        except QueueFullError:
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        accepted = list(executor.map(enqueue, range(8)))
    assert sum(accepted) == 3
    assert len(MessageQueueStore(ledger).state(session.id).items) == 3


def test_edit_preserves_identity_order_attachments_and_rejects_stale_or_dequeued_items(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )
    store = MessageQueueStore(ledger)
    store.enqueue(session.id, "first", remember=False, paste_spans=[])
    original = store.enqueue(
        session.id,
        "second",
        remember=True,
        paste_spans=[{"start_byte": 0, "end_byte": 6, "line_count": 1, "byte_count": 6}],
        attachments=[{"id": "retained-file", "name": "notes.txt"}],
        purpose="plan_note",
    ).item
    assert original is not None
    event = store.edit(session.id, original.id, content="revised", expected_content="second")
    revised = store.state(session.id).items[1]
    assert revised.model_dump(exclude={"content", "paste_spans"}) == original.model_dump(
        exclude={"content", "paste_spans"}
    )
    assert revised.content == "revised" and revised.paste_spans == []
    assert event.type == "queue.updated"
    with pytest.raises(ValueError, match="changed elsewhere"):
        store.edit(session.id, original.id, content="stale", expected_content="second")
    # Empty text is allowed when the queued message still has its original attachment.
    store.edit(session.id, original.id, content="", expected_content="revised")
    store.take(session.id, original.id, reason="promoted")
    with pytest.raises(KeyError):
        store.edit(session.id, original.id, content="too late", expected_content="")
    assert [item.content for item in store.state(session.id).items] == ["first"]
    with pytest.raises(ValueError, match="text or an attachment"):
        store.edit(
            session.id, store.state(session.id).items[0].id, content=" ", expected_content="first"
        )
