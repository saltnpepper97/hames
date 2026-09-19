import asyncio

import pytest

from hames.runtime import ActiveClock, RunFailure


@pytest.mark.asyncio
async def test_delegate_wait_does_not_consume_parent_budget() -> None:
    clock = ActiveClock(0.1)

    async def coordinator() -> None:
        await asyncio.sleep(0.01)
        with clock.pause():
            await asyncio.sleep(0.15)
        await asyncio.sleep(0.01)

    await clock.measure(coordinator())
    assert clock.elapsed < 0.1
    assert clock.remaining > 0


@pytest.mark.asyncio
async def test_active_work_still_times_out_after_wait() -> None:
    clock = ActiveClock(0.05)

    async def coordinator() -> None:
        with clock.pause():
            await asyncio.sleep(0.08)
        await asyncio.sleep(0.1)

    with pytest.raises(RunFailure, match="active-time"):
        await clock.measure(coordinator())


@pytest.mark.asyncio
async def test_nested_measure_is_not_charged_twice() -> None:
    clock = ActiveClock(1)
    await clock.measure(clock.measure(asyncio.sleep(0.03)))
    assert 0.02 < clock.elapsed < 0.055


@pytest.mark.asyncio
async def test_upstream_timeout_is_not_an_active_budget_failure() -> None:
    async def upstream() -> None:
        raise TimeoutError("provider connection timed out")

    with pytest.raises(TimeoutError, match="provider connection"):
        await ActiveClock(10).measure(upstream())
