"""Deterministic scripted provider for offline tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable

from hames.providers.base import ModelRequest, ProviderError, ProviderModel, StreamEvent


class FakeProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(
        self,
        events: Iterable[StreamEvent],
        *,
        failure: ProviderError | None = None,
        stall_after: int | None = None,
        turns: Iterable[Iterable[StreamEvent]] | None = None,
    ) -> None:
        self.events = list(events)
        self.requests: list[ModelRequest] = []
        self.failure = failure
        self.stall_after = stall_after
        self.turns = [list(turn) for turn in turns] if turns is not None else None

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider=self.profile_id,
                status="available",
                input_modalities=["text"],
                output_modalities=["text"],
                reasoning_supported=True,
                reasoning_efforts=["low", "medium", "xhigh"],
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        events = self.events
        if self.turns is not None:
            turn_index = len(self.requests) - 1
            if turn_index >= len(self.turns):
                raise ProviderError("fixture_exhausted", "fake provider has no scripted turn")
            events = self.turns[turn_index]
        for index, event in enumerate(events):
            if self.stall_after == index:
                await asyncio.Event().wait()
            yield event
        if self.stall_after == len(events):
            await asyncio.Event().wait()
        if self.failure is not None:
            raise self.failure

    async def aclose(self) -> None:
        return None
