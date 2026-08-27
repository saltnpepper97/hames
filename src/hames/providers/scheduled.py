"""Serialize access to a provider shared by interactive and maintenance calls."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from hames.providers.base import (
    JsonValue,
    ModelRequest,
    Provider,
    ProviderError,
    ProviderModel,
    StreamEvent,
)

MAINTENANCE_PURPOSES = {
    "memory_extraction",
    "skill_authoring",
    "skill_evaluation",
    "evolution_review",
    "evolution_evaluation",
}


class SerializedProvider:
    def __init__(self, inner: Provider, *, foreground_grace_seconds: float = 0.4) -> None:
        self.inner = inner
        self.profile_id = inner.profile_id
        self.adapter = inner.adapter
        self.base_url = inner.base_url
        self._condition = asyncio.Condition()
        self._active = False
        self._active_maintenance = False
        self._active_task: asyncio.Task[object] | None = None
        self._preempted_tasks: set[asyncio.Task[object]] = set()
        self._foreground_waiters = 0
        self._foreground_grace_seconds = foreground_grace_seconds

    async def list_models(self) -> list[ProviderModel]:
        await self._acquire(maintenance=False)
        try:
            return await self.inner.list_models()
        finally:
            await self._release()

    def cached_account_rate_limits(self) -> dict[str, JsonValue] | None:
        reader = getattr(self.inner, "cached_account_rate_limits", None)
        if reader is None:
            return None
        return reader()

    async def account_rate_limits(self) -> dict[str, JsonValue] | None:
        reader = getattr(self.inner, "account_rate_limits", None)
        if reader is None:
            return None
        await self._acquire(maintenance=False)
        try:
            return await reader()
        finally:
            await self._release()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        maintenance = str(request.metadata.get("purpose", "agent")) in MAINTENANCE_PURPOSES
        await self._acquire(maintenance=maintenance)
        task = asyncio.current_task()
        try:
            async for event in self.inner.stream(request):
                yield event
        except asyncio.CancelledError:
            if task is not None and task in self._preempted_tasks:
                self._preempted_tasks.discard(task)
                task.uncancel()
                raise ProviderError(
                    "maintenance_preempted",
                    "background model work yielded to a foreground request",
                    retryable=True,
                ) from None
            raise
        finally:
            await self._release(task)

    async def _acquire(self, *, maintenance: bool) -> None:
        async with self._condition:
            if maintenance:
                while self._active or self._foreground_waiters:
                    await self._condition.wait()
                self._activate(maintenance=True)
                return
            self._foreground_waiters += 1
            try:
                deadline: float | None = None
                while self._active:
                    if self._active_maintenance and self._active_task is not None:
                        deadline = deadline or (
                            asyncio.get_running_loop().time() + self._foreground_grace_seconds
                        )
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            if self._active_task not in self._preempted_tasks:
                                self._preempted_tasks.add(self._active_task)
                                self._active_task.cancel()
                            await self._condition.wait()
                            continue
                        try:
                            await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                        except TimeoutError:
                            continue
                    else:
                        await self._condition.wait()
                self._activate(maintenance=False)
            finally:
                self._foreground_waiters -= 1
                self._condition.notify_all()

    def _activate(self, *, maintenance: bool) -> None:
        self._active = True
        self._active_maintenance = maintenance
        self._active_task = asyncio.current_task()

    async def _release(self, task: asyncio.Task[object] | None = None) -> None:
        async with self._condition:
            if task is not None and self._active_task is not task:
                return
            self._active = False
            self._active_maintenance = False
            self._active_task = None
            self._condition.notify_all()

    async def aclose(self) -> None:
        await self.inner.aclose()
