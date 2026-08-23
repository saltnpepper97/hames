"""Bounded single-agent runtime and durable tool loop."""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hames.agent import load_agent
from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.context import compile_context
from hames.control import Approval, ControlStore
from hames.ledger import Event, Ledger, Session, new_id
from hames.paths import HamesPaths
from hames.policy import PolicyDecisionKind, PolicyGate, approval_request_hash
from hames.providers import ModelRequest, Provider, ProviderError, StreamEvent, StreamEventKind
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.tools import ToolContext, ToolRegistry, ToolResult

POLICY_SUMMARY = (
    "Reads, writes, deterministic edits, and ordinary Bash commands are allowed inside the "
    "trusted project or disposable scratch workspace. Path escape, Hames state, and known "
    "secret access are denied. High-risk shell operations require one-shot human approval."
)


class RunFailure(RuntimeError):
    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(slots=True)
class ActiveClock:
    limit: float
    elapsed: float = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit - self.elapsed)

    async def measure(self, awaitable: Any) -> Any:
        if self.remaining <= 0:
            raise RunFailure("active_time_limit", "run active-time limit was exhausted")
        started = time.monotonic()
        try:
            async with asyncio.timeout(self.remaining):
                return await awaitable
        except TimeoutError:
            raise RunFailure("active_time_limit", "run active-time limit was exhausted") from None
        finally:
            self.elapsed += time.monotonic() - started


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
        if delta.provider_call_id and self.provider_call_id not in {None, delta.provider_call_id}:
            raise ProviderError("provider_protocol_error", "tool-call ID changed while streaming")
        self.provider_call_id = delta.provider_call_id or self.provider_call_id
        if delta.name:
            self.name_parts.append(delta.name)
        if delta.arguments_delta:
            self.argument_parts.append(delta.arguments_delta)

    def invocation(self) -> ToolInvocation:
        name = "".join(self.name_parts)
        if not name:
            raise ProviderError("malformed_tool_call", "tool call omitted its name")
        try:
            arguments = JSON_OBJECT.validate_json("".join(self.argument_parts) or "{}")
        except ValueError as exc:
            raise ProviderError(
                "malformed_tool_call", "tool call arguments are not valid JSON"
            ) from exc
        return ToolInvocation(self.index, new_id(), self.provider_call_id, name, arguments)


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    index: int
    tool_call_id: str
    provider_call_id: str | None
    name: str
    arguments: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ModelTurn:
    request_event_id: str
    finish_reason: str
    tool_calls: list[ToolInvocation]


class RunManager:
    def __init__(
        self,
        *,
        ledger: Ledger,
        paths: HamesPaths,
        config: HamesConfig,
        controls: ControlStore,
        providers: dict[str, Provider],
        broker: EventBroker,
    ) -> None:
        self.ledger = ledger
        self.paths = paths
        self.config = config
        self.controls = controls
        self.providers = providers
        self.broker = broker
        self.tools = ToolRegistry()
        self.policy = PolicyGate(paths.root)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._session_runs: dict[str, str] = {}
        self._approval_waiters: dict[str, asyncio.Future[str]] = {}
        self._scratch_base = Path("/tmp/hames/runs")
        self._prune_scratch()

    async def start(self, session_id: str, content: str) -> str:
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        if session.status != "open":
            raise ValueError("session is not open")
        if session_id in self._session_runs:
            raise ValueError("session already has an active run")
        if session.provider not in self.providers:
            raise KeyError(f"unknown provider: {session.provider}")
        trust = await asyncio.to_thread(self.controls.get_trust, Path(session.working_directory))
        if trust is None:
            raise PermissionError("working directory is not trusted")
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

    async def resolve_approval(
        self, approval_id: str, *, request_hash: str, decision: str
    ) -> Approval:
        waiter = self._approval_waiters.get(approval_id)
        if waiter is None or waiter.done():
            raise RuntimeError("approval is not attached to an active run")
        resolved = await asyncio.to_thread(
            self.controls.resolve_approval, approval_id, request_hash, decision
        )
        await self._append(
            session_id=resolved.session_id,
            run_id=resolved.run_id,
            agent_id=resolved.agent_id,
            event_type="approval.resolved",
            payload={
                "approval_id": resolved.id,
                "request_hash": resolved.request_hash,
                "decision": resolved.status,
            },
            correlation_id=resolved.run_id,
        )
        waiter.set_result(resolved.status)
        return resolved

    async def close(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for provider in self.providers.values():
            await provider.aclose()

    async def _run(self, run_id: str, session_id: str, user_event: Event) -> None:
        scratch_root: Path | None = None
        try:
            session = await asyncio.to_thread(self.ledger.get_session, session_id)
            scratch_root = self._scratch_base / run_id / session.agent_id / "workspace"
            await self._execute_run(run_id, session, user_event, scratch_root)
        except asyncio.CancelledError:
            await self._cancel_approvals(run_id)
            await self._append(
                session_id=session_id,
                run_id=run_id,
                event_type="run.cancelled",
                payload={},
                causation_id=user_event.id,
                correlation_id=run_id,
            )
        except RunFailure as exc:
            await self._append_failure(
                session_id, run_id, user_event.id, exc.code, str(exc), exc.details
            )
        except ProviderError as exc:
            await self._append_failure(
                session_id,
                run_id,
                user_event.id,
                exc.code,
                str(exc),
                dict(exc.details),
                exc.retryable,
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
            await self._append_failure(session_id, run_id, error.id, "runtime_error", str(exc), {})
        finally:
            if scratch_root is not None:
                await asyncio.to_thread(self._remove_scratch, scratch_root)

    async def _append_failure(
        self,
        session_id: str,
        run_id: str,
        causation_id: str,
        code: str,
        message: str,
        details: dict[str, object],
        retryable: bool = False,
    ) -> None:
        await self._append(
            session_id=session_id,
            run_id=run_id,
            event_type="run.failed",
            payload={
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details,
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )

    async def _execute_run(
        self, run_id: str, session: Session, user_event: Event, scratch_root: Path
    ) -> None:
        limits = self.config.runtime
        clock = ActiveClock(limits.max_active_seconds_per_run)
        run_started = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="run.started",
            payload={
                "max_model_turns": limits.max_model_turns_per_user_message,
                "max_tool_calls": limits.max_tool_calls_per_run,
                "max_active_seconds": limits.max_active_seconds_per_run,
            },
            causation_id=user_event.id,
            correlation_id=run_id,
        )
        tool_count = 0
        model_turns = 0
        while True:
            if model_turns >= limits.max_model_turns_per_user_message:
                raise RunFailure("model_turn_limit", "run model-turn limit was exhausted")
            model_turns += 1
            turn = await clock.measure(
                self._model_turn(run_id, session, run_started.id if model_turns == 1 else None)
            )
            if not turn.tool_calls:
                await self._append(
                    session_id=session.id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="run.completed",
                    payload={
                        "model_turns": model_turns,
                        "tool_calls": tool_count,
                        "active_seconds": clock.elapsed,
                    },
                    causation_id=turn.request_event_id,
                    correlation_id=run_id,
                )
                return
            context = ToolContext(
                project_root=Path(session.working_directory),
                scratch_root=scratch_root,
                blobs=self.ledger.blob_store,
                config=self.config.tools,
            )
            for invocation in turn.tool_calls:
                if tool_count >= limits.max_tool_calls_per_run:
                    raise RunFailure("tool_call_limit", "run tool-call limit was exhausted")
                tool_count += 1
                await self._handle_tool(run_id, session, invocation, context, clock)

    async def _model_turn(
        self, run_id: str, session: Session, initial_causation_id: str | None
    ) -> ModelTurn:
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []
        tool_calls: dict[int, ToolCallAssembly] = {}
        capsule = await asyncio.to_thread(
            load_agent, self.paths.agents / session.agent_id / "AGENT.md"
        )
        history = await asyncio.to_thread(self.ledger.replay, session.id)
        definitions = self.tools.definitions()
        context = compile_context(session, history, capsule, definitions, POLICY_SUMMARY)
        previous = history[-1].id if history else initial_causation_id
        context_event = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="context.compiled",
            payload=context.manifest.model_dump(),
            causation_id=previous,
            correlation_id=run_id,
        )
        request_event = await self._append(
            session_id=session.id,
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
            tools=definitions,
        )
        started = completed = usage_seen = False
        finish_reason = "stop"
        try:
            async for stream_event in self.providers[session.provider].stream(request):
                if stream_event.kind is StreamEventKind.STARTED:
                    if started or completed:
                        raise ProviderError(
                            "provider_protocol_error",
                            "provider emitted response.started more than once",
                        )
                    started = True
                    await self._append(
                        session_id=session.id,
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
                    await self._publish_transient(session.id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.TEXT_DELTA:
                    answer_parts.append(stream_event.text)
                    await self._publish_transient(session.id, run_id, stream_event)
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
                    await self._publish_transient(session.id, run_id, stream_event)
                elif stream_event.kind is StreamEventKind.USAGE:
                    if usage_seen or stream_event.usage is None:
                        raise ProviderError(
                            "provider_protocol_error", "provider emitted invalid or duplicate usage"
                        )
                    usage_seen = True
                    await self._append(
                        session_id=session.id,
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
            invocations = [tool_calls[index].invocation() for index in sorted(tool_calls)]
            await self._persist_output(
                session,
                run_id,
                "".join(reasoning_parts),
                "".join(answer_parts),
                "interrupted" if invocations else "completed",
                request_event.id,
                force_message=bool(invocations),
            )
            for invocation in invocations:
                await self._append(
                    session_id=session.id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="model.tool_call",
                    payload={
                        "index": invocation.index,
                        "tool_call_id": invocation.tool_call_id,
                        "provider_call_id": invocation.provider_call_id,
                        "name": invocation.name,
                        "arguments": invocation.arguments,
                        "status": "requested",
                    },
                    causation_id=request_event.id,
                    correlation_id=run_id,
                )
            await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="model.response.completed",
                payload={"finish_reason": finish_reason},
                causation_id=request_event.id,
                correlation_id=run_id,
            )
            return ModelTurn(request_event.id, finish_reason, invocations)
        except asyncio.CancelledError:
            await self._persist_output(
                session,
                run_id,
                "".join(reasoning_parts),
                "".join(answer_parts),
                "interrupted",
                request_event.id,
            )
            raise
        except ProviderError as exc:
            await self._persist_output(
                session,
                run_id,
                "".join(reasoning_parts),
                "".join(answer_parts),
                "interrupted",
                request_event.id,
            )
            await self._append(
                session_id=session.id,
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
            raise

    async def _handle_tool(
        self,
        run_id: str,
        session: Session,
        invocation: ToolInvocation,
        context: ToolContext,
        clock: ActiveClock,
    ) -> None:
        requested = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="tool.requested",
            payload={
                "tool_call_id": invocation.tool_call_id,
                "provider_call_id": invocation.provider_call_id,
                "name": invocation.name,
                "arguments": invocation.arguments,
            },
            correlation_id=run_id,
        )
        try:
            arguments = self.tools.validate(invocation.name, invocation.arguments)
        except ValueError as exc:
            await self._persist_tool_result(
                session, run_id, invocation, _tool_failure(str(exc)), requested.id
            )
            return
        request_hash = approval_request_hash(
            tool_name=invocation.name,
            arguments=invocation.arguments,
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            working_directory=session.working_directory,
        )
        policy_requested = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="policy.requested",
            payload={
                "tool_call_id": invocation.tool_call_id,
                "name": invocation.name,
                "request_hash": request_hash,
            },
            causation_id=requested.id,
            correlation_id=run_id,
        )
        decision = self.policy.decide(invocation.name, arguments, context)
        policy_decided = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="policy.decided",
            payload={
                "tool_call_id": invocation.tool_call_id,
                "decision": decision.decision.value,
                "reason": decision.reason,
                "risk": decision.risk,
            },
            causation_id=policy_requested.id,
            correlation_id=run_id,
        )
        if decision.decision is PolicyDecisionKind.DENY:
            await self._persist_tool_result(
                session,
                run_id,
                invocation,
                ToolResult(status="rejected", summary=decision.reason),
                policy_decided.id,
            )
            return
        if decision.decision is PolicyDecisionKind.REQUIRE_CONFIRMATION:
            approved = await self._request_approval(
                run_id, session, invocation, request_hash, decision.reason, policy_decided.id
            )
            if not approved:
                await self._persist_tool_result(
                    session,
                    run_id,
                    invocation,
                    ToolResult(status="rejected", summary="human denied the requested action"),
                    policy_decided.id,
                )
                return
        tool = self.tools.get(invocation.name)
        if tool is None:
            raise RuntimeError(f"tool disappeared from registry: {invocation.name}")
        started = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="tool.started",
            payload={"tool_call_id": invocation.tool_call_id, "name": invocation.name},
            causation_id=policy_decided.id,
            correlation_id=run_id,
        )
        result = await clock.measure(tool.execute(context, arguments))
        await self._persist_tool_result(session, run_id, invocation, result, started.id)

    async def _request_approval(
        self,
        run_id: str,
        session: Session,
        invocation: ToolInvocation,
        request_hash: str,
        reason: str,
        causation_id: str,
    ) -> bool:
        approval = await asyncio.to_thread(
            self.controls.create_approval,
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            working_directory=session.working_directory,
            tool_call_id=invocation.tool_call_id,
            tool_name=invocation.name,
            arguments=invocation.arguments,
            request_hash=request_hash,
            reason=reason,
        )
        waiter = asyncio.get_running_loop().create_future()
        self._approval_waiters[approval.id] = waiter
        await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="approval.requested",
            payload={
                "approval_id": approval.id,
                "tool_call_id": invocation.tool_call_id,
                "name": invocation.name,
                "arguments": invocation.arguments,
                "request_hash": request_hash,
                "working_directory": session.working_directory,
                "reason": reason,
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )
        try:
            return await waiter == "approved"
        finally:
            self._approval_waiters.pop(approval.id, None)

    async def _cancel_approvals(self, run_id: str) -> None:
        approvals = await asyncio.to_thread(self.controls.cancel_pending_for_run, run_id)
        for approval in approvals:
            waiter = self._approval_waiters.get(approval.id)
            if waiter is not None and not waiter.done():
                waiter.cancel()
            await self._append(
                session_id=approval.session_id,
                run_id=approval.run_id,
                agent_id=approval.agent_id,
                event_type="approval.resolved",
                payload={
                    "approval_id": approval.id,
                    "request_hash": approval.request_hash,
                    "decision": "cancelled",
                },
                correlation_id=approval.run_id,
            )

    async def _persist_tool_result(
        self,
        session: Session,
        run_id: str,
        invocation: ToolInvocation,
        result: ToolResult,
        causation_id: str,
    ) -> Event:
        event_type = {
            "completed": "tool.completed",
            "failed": "tool.failed",
            "rejected": "tool.rejected",
        }[result.status]
        return await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type=event_type,
            payload={
                "tool_call_id": invocation.tool_call_id,
                "name": invocation.name,
                **result.model_dump(mode="json"),
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )

    async def _persist_output(
        self,
        session: Session,
        run_id: str,
        reasoning: str,
        answer: str,
        status: str,
        causation_id: str,
        *,
        force_message: bool = False,
    ) -> None:
        if reasoning:
            await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="assistant.reasoning",
                payload={"content": reasoning, "status": status},
                causation_id=causation_id,
                correlation_id=run_id,
            )
        if answer or status == "completed" or force_message:
            await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="assistant.message",
                payload={"content": answer, "status": status},
                causation_id=causation_id,
                correlation_id=run_id,
            )

    async def _append(self, **kwargs: Any) -> Event:
        event = await asyncio.to_thread(self.ledger.append, **kwargs)
        await self.broker.publish(
            event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
        )
        return event

    async def _publish_transient(self, session_id: str, run_id: str, event: StreamEvent) -> None:
        payload: dict[str, object] = (
            event.tool_call.model_dump(mode="json")
            if event.tool_call is not None
            else {"text": event.text}
        )
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

    def _prune_scratch(self) -> None:
        if self._scratch_base.exists():
            for child in self._scratch_base.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)

    def _remove_scratch(self, workspace: Path) -> None:
        run_root = workspace.parents[1]
        if run_root.parent == self._scratch_base and run_root.is_dir():
            shutil.rmtree(run_root, ignore_errors=True)


def _tool_failure(message: str) -> ToolResult:
    return ToolResult(
        status="failed",
        summary=message,
        structured_data={"error": "tool_validation_error", "message": message},
    )
