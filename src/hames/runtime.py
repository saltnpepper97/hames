"""M0 conversation run orchestration."""

from __future__ import annotations

import asyncio
from typing import Any

from hames.agent import load_agent
from hames.broker import EventBroker
from hames.context import compile_context
from hames.ledger import Event, Ledger, new_id
from hames.paths import HamesPaths
from hames.providers import (
    ModelRequest,
    Provider,
    ProviderError,
    StreamEvent,
    StreamEventKind,
)


class RunManager:
    def __init__(
        self,
        *,
        ledger: Ledger,
        paths: HamesPaths,
        providers: dict[str, Provider],
        broker: EventBroker,
    ) -> None:
        self.ledger = ledger
        self.paths = paths
        self.providers = providers
        self.broker = broker
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self, session_id: str, content: str) -> str:
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        if session.status != "open":
            raise ValueError("session is not open")
        if session.provider not in self.providers:
            raise KeyError(f"unknown provider: {session.provider}")
        user_event = await self._append(
            session_id=session_id,
            event_type="user.message",
            payload={"content": content},
            agent_id=session.agent_id,
        )
        run_id = new_id()
        task = asyncio.create_task(
            self._run(run_id, session_id, user_event), name=f"hames-run-{run_id}"
        )
        self._tasks[run_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(run_id, None))
        return run_id

    async def cancel(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def close(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for provider in self.providers.values():
            await provider.aclose()

    async def _run(self, run_id: str, session_id: str, user_event: Event) -> None:
        try:
            await self._execute_run(run_id, session_id, user_event)
        except asyncio.CancelledError:
            # Cancellation can arrive before provider streaming begins. The
            # provider-loop path records partial output itself; this path still
            # guarantees a terminal event for early cancellation.
            await self._append(
                session_id=session_id,
                run_id=run_id,
                event_type="run.cancelled",
                payload={},
                causation_id=user_event.id,
                correlation_id=run_id,
            )
        except Exception as exc:
            error = await self._append(
                session_id=session_id,
                run_id=run_id,
                event_type="runtime.error",
                payload={"code": "runtime_error", "message": str(exc), "retryable": False},
                causation_id=user_event.id,
                correlation_id=run_id,
            )
            await self._append(
                session_id=session_id,
                run_id=run_id,
                event_type="model.response.failed",
                payload={"code": "runtime_error", "message": str(exc), "retryable": False},
                causation_id=error.id,
                correlation_id=run_id,
            )

    async def _execute_run(self, run_id: str, session_id: str, user_event: Event) -> None:
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        capsule = await asyncio.to_thread(
            load_agent, self.paths.agents / session.agent_id / "AGENT.md"
        )
        history = await asyncio.to_thread(self.ledger.list_events, session_id)
        context = compile_context(session, history, capsule)
        context_event = await self._append(
            session_id=session_id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="context.compiled",
            payload=context.manifest.model_dump(),
            causation_id=user_event.id,
            correlation_id=run_id,
        )
        request_event = await self._append(
            session_id=session_id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="model.requested",
            payload={
                "provider": session.provider,
                "model": session.model,
                "reasoning_effort": session.reasoning_effort,
                "agent_capsule_hash": capsule.content_hash,
            },
            causation_id=context_event.id,
            correlation_id=run_id,
        )
        request = ModelRequest(
            model=session.model,
            messages=context.messages,
            system=context.system,
            reasoning_effort=session.reasoning_effort,
        )
        provider = self.providers[session.provider]
        completed = False
        try:
            async for stream_event in provider.stream(request):
                if stream_event.kind is StreamEventKind.STARTED:
                    await self._append(
                        session_id=session_id,
                        run_id=run_id,
                        agent_id=session.agent_id,
                        event_type="model.response.started",
                        payload={"provider_request_id": stream_event.provider_request_id},
                        causation_id=request_event.id,
                        correlation_id=run_id,
                    )
                elif stream_event.kind is StreamEventKind.REASONING_DELTA:
                    reasoning_parts.append(stream_event.text)
                    await self._publish_transient(session_id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.TEXT_DELTA:
                    answer_parts.append(stream_event.text)
                    await self._publish_transient(session_id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.USAGE and stream_event.usage:
                    await self._append(
                        session_id=session_id,
                        run_id=run_id,
                        agent_id=session.agent_id,
                        event_type="model.usage",
                        payload=stream_event.usage.model_dump(),
                        causation_id=request_event.id,
                        correlation_id=run_id,
                    )
                elif stream_event.kind is StreamEventKind.COMPLETED:
                    completed = True
                    await self._persist_output(
                        session_id=session_id,
                        run_id=run_id,
                        agent_id=session.agent_id,
                        reasoning="".join(reasoning_parts),
                        answer="".join(answer_parts),
                        status="completed",
                        causation_id=request_event.id,
                    )
                    await self._append(
                        session_id=session_id,
                        run_id=run_id,
                        agent_id=session.agent_id,
                        event_type="model.response.completed",
                        payload={"finish_reason": stream_event.finish_reason or "stop"},
                        causation_id=request_event.id,
                        correlation_id=run_id,
                    )
            if not completed:
                raise ProviderError(
                    "incomplete_provider_response", "provider stream did not complete"
                )
        except asyncio.CancelledError:
            await self._persist_output(
                session_id=session_id,
                run_id=run_id,
                agent_id=session.agent_id,
                reasoning="".join(reasoning_parts),
                answer="".join(answer_parts),
                status="interrupted",
                causation_id=request_event.id,
            )
            await self._append(
                session_id=session_id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="run.cancelled",
                payload={},
                causation_id=request_event.id,
                correlation_id=run_id,
            )
        except ProviderError as exc:
            await self._persist_output(
                session_id=session_id,
                run_id=run_id,
                agent_id=session.agent_id,
                reasoning="".join(reasoning_parts),
                answer="".join(answer_parts),
                status="interrupted",
                causation_id=request_event.id,
            )
            await self._append(
                session_id=session_id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="model.response.failed",
                payload={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
                causation_id=request_event.id,
                correlation_id=run_id,
            )

    async def _persist_output(
        self,
        *,
        session_id: str,
        run_id: str,
        agent_id: str,
        reasoning: str,
        answer: str,
        status: str,
        causation_id: str,
    ) -> None:
        if reasoning:
            await self._append(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                event_type="assistant.reasoning",
                payload={"content": reasoning, "status": status},
                causation_id=causation_id,
                correlation_id=run_id,
            )
        if answer or status == "completed":
            await self._append(
                session_id=session_id,
                run_id=run_id,
                agent_id=agent_id,
                event_type="assistant.message",
                payload={"content": answer, "status": status},
                causation_id=causation_id,
                correlation_id=run_id,
            )

    async def _append(self, **kwargs: Any) -> Event:
        event = await asyncio.to_thread(self.ledger.append, **kwargs)
        await self.broker.publish(
            event.session_id,
            {"durable": True, "event": event.model_dump(mode="json")},
        )
        return event

    async def _publish_transient(self, session_id: str, run_id: str, event: StreamEvent) -> None:
        await self.broker.publish(
            session_id,
            {
                "durable": False,
                "session_id": session_id,
                "run_id": run_id,
                "type": event.kind.value,
                "payload": {"text": event.text},
            },
        )
