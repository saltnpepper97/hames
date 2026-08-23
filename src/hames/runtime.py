"""M0 conversation run orchestration."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
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


@dataclass(slots=True)
class ToolCallAssembly:
    index: int
    provider_call_id: str | None = None
    name_parts: list[str] = field(default_factory=lambda: list[str]())
    argument_parts: list[str] = field(default_factory=lambda: list[str]())

    def add(self, event: StreamEvent) -> None:
        delta = event.tool_call
        if delta is None:
            raise ProviderError("provider_protocol_error", "tool-call event omitted its payload")
        if (
            delta.provider_call_id
            and self.provider_call_id
            and delta.provider_call_id != self.provider_call_id
        ):
            raise ProviderError("provider_protocol_error", "tool-call ID changed while streaming")
        if delta.provider_call_id:
            self.provider_call_id = delta.provider_call_id
        if delta.name:
            self.name_parts.append(delta.name)
        if delta.arguments_delta:
            self.argument_parts.append(delta.arguments_delta)

    def payload(self) -> dict[str, object]:
        name = "".join(self.name_parts)
        if not name:
            raise ProviderError("malformed_tool_call", "tool call omitted its name")
        raw_arguments = "".join(self.argument_parts) or "{}"
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ProviderError(
                "malformed_tool_call", "tool call arguments are not valid JSON"
            ) from exc
        if not isinstance(arguments, dict):
            raise ProviderError("malformed_tool_call", "tool call arguments must be a JSON object")
        return {
            "index": self.index,
            "provider_call_id": self.provider_call_id,
            "name": name,
            "arguments": arguments,
            "status": "unhandled",
        }


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
        self._session_runs: dict[str, str] = {}

    async def start(self, session_id: str, content: str) -> str:
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        if session.status != "open":
            raise ValueError("session is not open")
        if session_id in self._session_runs:
            raise ValueError("session already has an active run")
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
        self._session_runs[session_id] = run_id
        task.add_done_callback(lambda _: self._finish(run_id, session_id))
        return run_id

    def _finish(self, run_id: str, session_id: str) -> None:
        self._tasks.pop(run_id, None)
        self._session_runs.pop(session_id, None)

    def is_session_active(self, session_id: str) -> bool:
        return session_id in self._session_runs

    @property
    def active_run_count(self) -> int:
        return len(self._tasks)

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
        tool_calls: dict[int, ToolCallAssembly] = {}
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        capsule = await asyncio.to_thread(
            load_agent, self.paths.agents / session.agent_id / "AGENT.md"
        )
        history = await asyncio.to_thread(self.ledger.replay, session_id)
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
        started = False
        completed = False
        usage_seen = False
        finish_reason = "stop"
        try:
            async for stream_event in provider.stream(request):
                if stream_event.kind is StreamEventKind.STARTED:
                    if started or completed:
                        raise ProviderError(
                            "provider_protocol_error",
                            "provider emitted response.started more than once",
                        )
                    started = True
                    await self._append(
                        session_id=session_id,
                        run_id=run_id,
                        agent_id=session.agent_id,
                        event_type="model.response.started",
                        payload={"provider_request_id": stream_event.provider_request_id},
                        causation_id=request_event.id,
                        correlation_id=run_id,
                    )
                    continue
                if not started:
                    raise ProviderError(
                        "provider_protocol_error",
                        f"provider emitted {stream_event.kind.value} before response.started",
                    )
                if completed:
                    raise ProviderError(
                        "provider_protocol_error",
                        f"provider emitted {stream_event.kind.value} after response.completed",
                    )
                if stream_event.kind is StreamEventKind.REASONING_DELTA:
                    reasoning_parts.append(stream_event.text)
                    await self._publish_transient(session_id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.TEXT_DELTA:
                    answer_parts.append(stream_event.text)
                    await self._publish_transient(session_id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.TOOL_CALL_DELTA:
                    if stream_event.tool_call is None:
                        raise ProviderError(
                            "provider_protocol_error", "tool-call event omitted its payload"
                        )
                    assembly = tool_calls.setdefault(
                        stream_event.tool_call.index,
                        ToolCallAssembly(index=stream_event.tool_call.index),
                    )
                    assembly.add(stream_event)
                    await self._publish_transient(session_id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.USAGE:
                    if usage_seen or stream_event.usage is None:
                        raise ProviderError(
                            "provider_protocol_error",
                            "provider emitted invalid or duplicate usage",
                        )
                    usage_seen = True
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
                    finish_reason = stream_event.finish_reason or "stop"
            if not completed:
                raise ProviderError("provider_protocol_error", "provider stream did not complete")
            tool_payloads = [tool_calls[index].payload() for index in sorted(tool_calls)]
            output_status = "interrupted" if tool_payloads else "completed"
            await self._persist_output(
                session_id=session_id,
                run_id=run_id,
                agent_id=session.agent_id,
                reasoning="".join(reasoning_parts),
                answer="".join(answer_parts),
                status=output_status,
                causation_id=request_event.id,
            )
            for payload in tool_payloads:
                await self._append(
                    session_id=session_id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="model.tool_call",
                    payload=payload,
                    causation_id=request_event.id,
                    correlation_id=run_id,
                )
            if tool_payloads:
                await self._append(
                    session_id=session_id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="model.response.failed",
                    payload={
                        "code": "unexpected_tool_call",
                        "message": "model requested tools before tool execution is enabled",
                        "retryable": False,
                        "details": {
                            "tool_calls": [
                                {
                                    "index": payload["index"],
                                    "provider_call_id": payload["provider_call_id"],
                                    "name": payload["name"],
                                }
                                for payload in tool_payloads
                            ]
                        },
                    },
                    causation_id=request_event.id,
                    correlation_id=run_id,
                )
            else:
                await self._append(
                    session_id=session_id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="model.response.completed",
                    payload={"finish_reason": finish_reason},
                    causation_id=request_event.id,
                    correlation_id=run_id,
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
                payload={
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                    "details": exc.details,
                },
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
        payload: dict[str, object]
        if event.tool_call is not None:
            payload = event.tool_call.model_dump(mode="json")
        else:
            payload = {"text": event.text}
        await self.broker.publish(
            session_id,
            {
                "durable": False,
                "session_id": session_id,
                "run_id": run_id,
                "type": event.kind.value,
                "payload": payload,
            },
        )
