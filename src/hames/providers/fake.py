"""Deterministic scripted provider for offline tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable

from hames.providers.base import ModelRequest, ProviderModel, StreamEvent


class FakeProvider:
    name = "fake"

    def __init__(self, events: Iterable[StreamEvent]) -> None:
        self.events = list(events)
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="fixture",
                provider=self.name,
                status="available",
                input_modalities=["text"],
                output_modalities=["text"],
                reasoning_supported=True,
                reasoning_efforts=["low", "medium", "xhigh"],
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        for event in self.events:
            yield event
