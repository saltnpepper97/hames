from __future__ import annotations

import pytest

from hames.broker import EventBroker


@pytest.mark.asyncio
async def test_slow_subscriber_cannot_fail_publishers() -> None:
    broker = EventBroker()
    async with broker.subscribe("session") as queue:
        for sequence in range(1100):
            await broker.publish("session", {"sequence": sequence})

        newest = None
        while not queue.empty():
            newest = queue.get_nowait()
        assert newest == {"sequence": 1099}


@pytest.mark.asyncio
async def test_reconnect_snapshot_preserves_prefix_and_orders_new_deltas() -> None:
    broker = EventBroker()

    async def durable(kind: str, sequence: int) -> None:
        await broker.publish(
            "s",
            {
                "durable": True,
                "event": {"id": str(sequence), "type": kind, "sequence": sequence, "run_id": "r"},
            },
        )

    async def delta(text: str) -> None:
        await broker.publish(
            "s",
            {
                "durable": False,
                "run_id": "r",
                "type": "response.text_delta",
                "payload": {"text": text},
            },
        )

    await durable("run.started", 1)
    await delta("Earlier response")
    await durable("assistant.message", 2)
    await durable("model.response.started", 3)
    await delta("## A complete ")
    async with broker.subscribe("s", include_snapshot=True) as queue:
        snapshot = queue.get_nowait()
        assert snapshot["type"] == "response.snapshot"
        assert snapshot["payload"] == {
            "text": "## A complete ",
            "reasoning": "",
            "after_sequence": 3,
        }
        await delta("heading")
        assert queue.get_nowait()["payload"] == {"text": "heading"}
    async with broker.subscribe("s", include_snapshot=True) as queue:
        assert queue.get_nowait()["payload"] == {
            "text": "## A complete heading",
            "reasoning": "",
            "after_sequence": 3,
        }
    await durable("assistant.message", 4)
    await durable("run.completed", 5)
    async with broker.subscribe("s", include_snapshot=True) as queue:
        snapshot = queue.get_nowait()
        assert snapshot["run_id"] == ""
        assert snapshot["payload"] == {"text": "", "reasoning": "", "after_sequence": 0}
