"""Serialize access to a provider shared by interactive and maintenance calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from hames.providers.base import ModelRequest, Provider, ProviderModel, StreamEvent


class SerializedProvider:
    def __init__(self, inner: Provider) -> None:
        self.inner = inner
        self.profile_id = inner.profile_id
        self.adapter = inner.adapter
        self.base_url = inner.base_url
        self._lock = asyncio.Lock()

    async def list_models(self) -> list[ProviderModel]:
        async with self._lock:
            return await self.inner.list_models()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        async with self._lock:
            async for event in self.inner.stream(request):
                yield event

    async def aclose(self) -> None:
        await self.inner.aclose()
