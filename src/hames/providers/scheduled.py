"""Schedule interactive and maintenance calls sharing one provider profile."""

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


class ScheduledProvider:
    def __init__(self, inner: Provider, *, foreground_grace_seconds: float = 0.4) -> None:
        self.inner = inner
        self.profile_id = inner.profile_id
        self.adapter = inner.adapter
        self.base_url = inner.base_url
        self._condition = asyncio.Condition()
        self._active_foreground_tasks: set[asyncio.Task[object]] = set()
        self._active_maintenance_task: asyncio.Task[object] | None = None
        self._preempted_tasks: set[asyncio.Task[object]] = set()
        self._foreground_waiters = 0
        self._foreground_grace_seconds = foreground_grace_seconds

    async def list_models(self) -> list[ProviderModel]:
        # Model discovery uses a separate control request/connection for every
        # supported provider. Do not queue /model behind a potentially long
        # generation stream.
        return await self.inner.list_models()

    def cached_account_rate_limits(self) -> dict[str, JsonValue] | None:
        reader = getattr(self.inner, "cached_account_rate_limits", None)
        if reader is None:
            return None
        return reader()

    async def account_rate_limits(self) -> dict[str, JsonValue] | None:
        reader = getattr(self.inner, "account_rate_limits", None)
        if reader is None:
            return None
        # Codex serves account limits over a separate, short-lived app-server
        # connection. Do not queue this read behind a potentially long model turn:
        # /usage must remain available while that turn is streaming.
        return await reader()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        maintenance = str(request.metadata.get("purpose", "agent")) in MAINTENANCE_PURPOSES
        task = asyncio.current_task()
        await self._acquire(task, maintenance=maintenance)
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
            await self._release(task, maintenance=maintenance)

    async def _acquire(self, task: asyncio.Task[object] | None, *, maintenance: bool) -> None:
        async with self._condition:
            if maintenance:
                while (
                    self._active_foreground_tasks
                    or self._active_maintenance_task is not None
                    or self._foreground_waiters
                ):
                    await self._condition.wait()
                self._active_maintenance_task = task
                return
            self._foreground_waiters += 1
            try:
                deadline: float | None = None
                while self._active_maintenance_task is not None:
                    deadline = deadline or (
                        asyncio.get_running_loop().time() + self._foreground_grace_seconds
                    )
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        maintenance_task = self._active_maintenance_task
                        if maintenance_task not in self._preempted_tasks:
                            self._preempted_tasks.add(maintenance_task)
                            maintenance_task.cancel()
                        await self._condition.wait()
                        continue
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                    except TimeoutError:
                        continue
                if task is not None:
                    self._active_foreground_tasks.add(task)
            finally:
                self._foreground_waiters -= 1
                self._condition.notify_all()

    async def _release(self, task: asyncio.Task[object] | None, *, maintenance: bool) -> None:
        async with self._condition:
            if maintenance:
                if self._active_maintenance_task is task:
                    self._active_maintenance_task = None
            elif task is not None:
                self._active_foreground_tasks.discard(task)
            self._condition.notify_all()

    async def aclose(self) -> None:
        await self.inner.aclose()
