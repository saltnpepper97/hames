from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from hames.providers import (
    ModelRequest,
    ProviderError,
    ProviderMessage,
    ProviderModel,
    StreamEvent,
    StreamEventKind,
)
from hames.providers.scheduled import ScheduledProvider


class BlockingProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.releases: asyncio.Queue[asyncio.Event] = asyncio.Queue()

    async def list_models(self) -> list[ProviderModel]:
        return []

    async def account_rate_limits(self) -> dict[str, object]:
        return {"weekly_window": {"used": 12, "remaining": 88}}

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        purpose = str(request.metadata["purpose"])
        self.started.append(purpose)
        release = asyncio.Event()
        self.releases.put_nowait(release)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        await release.wait()
        yield StreamEvent(kind=StreamEventKind.COMPLETED)

    async def aclose(self) -> None:
        return None


def _request(purpose: str) -> ModelRequest:
    return ModelRequest(
        model="fixture",
        messages=[ProviderMessage(role="user", content="test")],
        system="test",
        metadata={"purpose": purpose},
    )


async def _consume(provider: ScheduledProvider, purpose: str) -> None:
    async for _ in provider.stream(_request(purpose)):
        pass


@pytest.mark.asyncio
async def test_foreground_requests_run_concurrently() -> None:
    inner = BlockingProvider()
    provider = ScheduledProvider(inner)
    first = asyncio.create_task(_consume(provider, "agent"))
    first_release = await inner.releases.get()
    second = asyncio.create_task(_consume(provider, "agent"))

    second_release = await asyncio.wait_for(inner.releases.get(), timeout=1)
    assert inner.started == ["agent", "agent"]

    first_release.set()
    second_release.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_foreground_request_runs_before_waiting_maintenance() -> None:
    inner = BlockingProvider()
    provider = ScheduledProvider(inner)
    first = asyncio.create_task(_consume(provider, "memory_extraction"))
    first_release = await inner.releases.get()
    second = asyncio.create_task(_consume(provider, "skill_authoring"))
    foreground = asyncio.create_task(_consume(provider, "agent"))
    await asyncio.sleep(0)
    first_release.set()
    foreground_release = await asyncio.wait_for(inner.releases.get(), timeout=1)
    assert inner.started == ["memory_extraction", "agent"]
    foreground_release.set()
    second_release = await asyncio.wait_for(inner.releases.get(), timeout=1)
    assert inner.started == ["memory_extraction", "agent", "skill_authoring"]
    second_release.set()
    await asyncio.gather(first, foreground, second)


@pytest.mark.asyncio
async def test_foreground_preempts_long_running_maintenance_after_a_short_grace() -> None:
    inner = BlockingProvider()
    provider = ScheduledProvider(inner, foreground_grace_seconds=0.01)
    maintenance = asyncio.create_task(_consume(provider, "memory_extraction"))
    await inner.releases.get()

    foreground = asyncio.create_task(_consume(provider, "agent"))
    foreground_release = await asyncio.wait_for(inner.releases.get(), timeout=1)

    with pytest.raises(ProviderError, match="yielded to a foreground request") as raised:
        await maintenance
    assert raised.value.code == "maintenance_preempted"
    assert raised.value.retryable is True
    assert inner.started == ["memory_extraction", "agent"]

    foreground_release.set()
    await foreground


@pytest.mark.asyncio
async def test_account_limits_do_not_wait_for_an_active_model_stream() -> None:
    inner = BlockingProvider()
    provider = ScheduledProvider(inner)
    model = asyncio.create_task(_consume(provider, "agent"))
    release = await inner.releases.get()

    limits = await asyncio.wait_for(provider.account_rate_limits(), timeout=0.1)
    assert limits == {"weekly_window": {"used": 12, "remaining": 88}}

    release.set()
    await model


@pytest.mark.asyncio
async def test_model_list_does_not_wait_for_an_active_model_stream() -> None:
    inner = BlockingProvider()
    provider = ScheduledProvider(inner)
    model = asyncio.create_task(_consume(provider, "agent"))
    release = await inner.releases.get()

    models = await asyncio.wait_for(provider.list_models(), timeout=0.1)
    assert models == []

    release.set()
    await model
