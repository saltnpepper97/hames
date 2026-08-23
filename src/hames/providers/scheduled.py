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
        self._condition = asyncio.Condition()
        self._active = False
        self._foreground_waiters = 0

    async def list_models(self) -> list[ProviderModel]:
        await self._acquire(maintenance=False)
        try:
            return await self.inner.list_models()
        finally:
            await self._release()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        maintenance = str(request.metadata.get("purpose", "agent")) in {
            "memory_extraction",
            "skill_authoring",
            "skill_evaluation",
        }
        await self._acquire(maintenance=maintenance)
        try:
            async for event in self.inner.stream(request):
                yield event
        finally:
            await self._release()

    async def _acquire(self, *, maintenance: bool) -> None:
        async with self._condition:
            if maintenance:
                while self._active or self._foreground_waiters:
                    await self._condition.wait()
                self._active = True
                return
            self._foreground_waiters += 1
            try:
                while self._active:
                    await self._condition.wait()
                self._active = True
            finally:
                self._foreground_waiters -= 1
                self._condition.notify_all()

    async def _release(self) -> None:
        async with self._condition:
            self._active = False
            self._condition.notify_all()

    async def aclose(self) -> None:
        await self.inner.aclose()
