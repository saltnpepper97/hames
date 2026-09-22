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


@pytest.mark.asyncio
async def test_zero_disables_active_deadline_across_work_and_pauses() -> None:
    clock = ActiveClock(0, elapsed=3600)

    async def work() -> None:
        assert clock._timeout is not None  # pyright: ignore[reportPrivateUsage]
        assert clock._timeout.when() is None  # pyright: ignore[reportPrivateUsage]
        with clock.pause():
            await asyncio.sleep(0)
        assert clock._timeout.when() is None  # pyright: ignore[reportPrivateUsage]
        await clock.measure(asyncio.sleep(0.01))

    await clock.measure(work())
    await clock.measure(asyncio.sleep(0.01))
    assert clock.elapsed > 3600
    assert clock.remaining == float("inf")


@pytest.mark.asyncio
async def test_unlimited_clock_still_allows_cancellation() -> None:
    entered = asyncio.Event()

    async def work() -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(ActiveClock(0).measure(work()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
