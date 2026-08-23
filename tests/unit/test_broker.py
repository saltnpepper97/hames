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
