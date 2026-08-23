"""Bounded single-agent runtime and durable tool loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hames.agent import AgentCapsule, AgentRegistry, load_agent, permitted_tools
from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.context import (
    ContextBudgetError,
    ContextRuleViolation,
    canonical_request_snapshot,
    compile_context,
)
from hames.control import Approval, ControlStore
from hames.ledger import Event, Ledger, Session, new_id
from hames.memory import MemoryStore, RetrievedMemory, retrieval_query_hash
from hames.paths import HamesPaths
from hames.policy import PolicyDecisionKind, PolicyGate, approval_request_hash
from hames.providers import ModelRequest, Provider, ProviderError, StreamEvent, StreamEventKind
from hames.providers.base import JSON_OBJECT, JsonValue
from hames.rules import ContextRuleStore, PolicyRuleStore
from hames.skills import SkillRegistry, SkillSummary, SkillVersion
from hames.tools import (
    ShellArguments,
    SkillAuthorArguments,
    SkillLoadArguments,
    SkillRunArguments,
    SpawnAgentArguments,
    ToolArguments,
    ToolContext,
    ToolRegistry,
    ToolResult,
)

if TYPE_CHECKING:
    from hames.evolution_runtime import EvolutionManager
    from hames.memory_runtime import MemoryManager
    from hames.skill_runtime import SkillManager

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
    allowed_tools: frozenset[str]
    capsule: AgentCapsule


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
        self.skills = SkillRegistry(
            paths.skills,
            ledger,
            available_tools=self.tools.names(),
            max_package_bytes=config.skills.max_package_bytes,
            max_package_files=config.skills.max_package_files,
        )
        self.agents = AgentRegistry(paths.agents)
        self.memory = MemoryStore(ledger)
        self.policy = PolicyGate(paths.root)
        self.context_rules = ContextRuleStore(ledger)
        self.policy_rules = PolicyRuleStore(ledger)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._session_runs: dict[str, str] = {}
        self._approval_waiters: dict[str, asyncio.Future[str]] = {}
        self._children_by_parent: dict[str, set[str]] = {}
        self._child_count_by_parent: dict[str, int] = {}
        self._scratch_base = Path("/tmp/hames/runs")
        self.memory_manager: MemoryManager | None = None
        self.skill_manager: SkillManager | None = None
        self.evolution_manager: EvolutionManager | None = None
        self._skill_catalogs: dict[str, list[SkillSummary]] = {}
        self._loaded_skills: dict[str, dict[str, SkillVersion]] = {}
        self._prune_scratch()

    def attach_memory_manager(self, manager: MemoryManager) -> None:
        self.memory_manager = manager

    def attach_skill_manager(self, manager: SkillManager) -> None:
        self.skill_manager = manager

    def attach_evolution_manager(self, manager: EvolutionManager) -> None:
        self.evolution_manager = manager

    async def start(self, session_id: str, content: str, *, remember: bool = False) -> str:
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
            payload={"content": content, "remember": remember},
            agent_id=session.agent_id,
        )
        return self._launch(session_id, user_event)

    def _launch(self, session_id: str, user_event: Event) -> str:
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
        self._children_by_parent.pop(run_id, None)
        self._child_count_by_parent.pop(run_id, None)
        self._skill_catalogs.pop(run_id, None)
        self._loaded_skills.pop(run_id, None)

    def is_session_active(self, session_id: str) -> bool:
        return session_id in self._session_runs

    async def finish_terminal_session(self, session_id: str) -> bool:
        """Wait for post-terminal bookkeeping, but never wait on a live model/tool run."""

        run_id = self._session_runs.get(session_id)
        if run_id is None:
            return True
        terminal = any(
            event.type in {"run.completed", "run.failed", "run.cancelled"}
            for event in await asyncio.to_thread(self.ledger.list_run_events, run_id)
        )
        if not terminal:
            return False
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.shield(task)
        return True

    def is_working_directory_active(self, working_directory: str) -> bool:
        return any(
            self.ledger.get_session(session_id).working_directory == working_directory
            for session_id in self._session_runs
        )

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
        if self.memory_manager is not None:
            await self.memory_manager.close()
        if self.skill_manager is not None:
            await self.skill_manager.close()
        for provider in self.providers.values():
            await provider.aclose()

    async def _run(self, run_id: str, session_id: str, user_event: Event) -> None:
        scratch_root: Path | None = None
        session: Session | None = None
        try:
            session = await asyncio.to_thread(self.ledger.get_session, session_id)
            scratch_root = self._scratch_base / run_id / session.agent_id / "workspace"
            await self._execute_run(run_id, session, user_event, scratch_root)
        except asyncio.CancelledError:
            await self._cancel_children(run_id)
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
        except ContextBudgetError as exc:
            await self._append_failure(
                session_id,
                run_id,
                user_event.id,
                "context_budget_exceeded",
                str(exc),
                exc.details,
            )
        except ContextRuleViolation as exc:
            await self._append_failure(
                session_id,
                run_id,
                user_event.id,
                "context_rule_violation",
                str(exc),
                dict(exc.details),
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
            if self.config.memory.enabled:
                await self._project_episode(session_id, run_id)
            if self.memory_manager is not None and session is not None:
                if bool(user_event.payload.get("remember", False)):
                    await self.memory_manager.enqueue_capture(
                        session, str(user_event.payload.get("content", "")), user_event
                    )
                else:
                    await self.memory_manager.enqueue_run(session_id, run_id)
            if self.skill_manager is not None and session is not None:
                await self.skill_manager.observe_run(session_id, run_id)
            if self.evolution_manager is not None and session is not None:
                try:
                    await self.evolution_manager.observe_run(session_id, run_id)
                except (KeyError, ValueError) as exc:
                    await self._append(
                        session_id=session_id,
                        run_id=run_id,
                        event_type="runtime.notice",
                        payload={
                            "code": "evolution_observation_failed",
                            "message": str(exc),
                            "details": {},
                        },
                    )
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
        memories, retrieval_event = await self._retrieve_memories(
            session, run_id, str(user_event.payload.get("content", "")), run_started.id
        )
        catalog, skill_event = await self._retrieve_skills(
            session,
            run_id,
            str(user_event.payload.get("content", "")),
            retrieval_event.id if retrieval_event is not None else run_started.id,
        )
        self._skill_catalogs[run_id] = catalog
        self._loaded_skills[run_id] = {}
        tool_count = 0
        model_turns = 0
        while True:
            if model_turns >= limits.max_model_turns_per_user_message:
                raise RunFailure("model_turn_limit", "run model-turn limit was exhausted")
            model_turns += 1
            turn = await clock.measure(
                self._model_turn(
                    run_id,
                    session,
                    skill_event.id
                    if model_turns == 1 and skill_event is not None
                    else retrieval_event.id
                    if model_turns == 1 and retrieval_event is not None
                    else run_started.id
                    if model_turns == 1
                    else None,
                    memories,
                )
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
                await self._handle_tool(
                    run_id,
                    session,
                    invocation,
                    context,
                    clock,
                    turn.allowed_tools,
                    turn.capsule,
                )

    async def _model_turn(
        self,
        run_id: str,
        session: Session,
        initial_causation_id: str | None,
        memories: list[RetrievedMemory],
    ) -> ModelTurn:
        reasoning_parts: list[str] = []
        answer_parts: list[str] = []
        tool_calls: dict[int, ToolCallAssembly] = {}
        capsule = await asyncio.to_thread(
            load_agent, self.paths.agents / session.agent_id / "AGENT.md"
        )
        history = await asyncio.to_thread(self.ledger.replay, session.id)
        allowed_tools = permitted_tools(capsule, set(self.tools.names()))
        if (
            not capsule.metadata.delegation.allow
            or session.delegation_depth >= self.config.runtime.max_delegation_depth
        ):
            allowed_tools = frozenset(allowed_tools - {"spawn_agent"})
        definitions = self.tools.definitions(allowed_tools)
        active_context_rules = await asyncio.to_thread(
            self.context_rules.active_matching,
            working_directory=session.working_directory,
            agent_id=session.agent_id,
        )
        context = compile_context(
            session,
            history,
            capsule,
            definitions,
            POLICY_SUMMARY,
            self.config.context,
            run_id=run_id,
            memories=memories,
            skill_catalog=self._skill_catalogs.get(run_id, []),
            loaded_skills=list(self._loaded_skills.get(run_id, {}).values()),
            skill_catalog_budget_tokens=self.config.skills.catalog_budget_tokens,
            loaded_skill_budget_tokens=self.config.skills.loaded_budget_tokens,
            context_rules=active_context_rules,
        )
        snapshot = canonical_request_snapshot(
            model=session.model,
            system=context.system,
            messages=context.messages,
            tools=context.tools,
            reasoning_effort=session.reasoning_effort,
            max_tokens=self.config.context.output_reserve_tokens,
        )
        request_hash = await asyncio.to_thread(self.ledger.blob_store.put, snapshot)
        context.manifest.request_hash = request_hash
        context.manifest.request_snapshot_blob_hash = request_hash
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
            max_tokens=self.config.context.output_reserve_tokens,
            tools=context.tools,
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
            return ModelTurn(request_event.id, finish_reason, invocations, allowed_tools, capsule)
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

    async def _retrieve_memories(
        self,
        session: Session,
        run_id: str,
        query: str,
        causation_id: str,
    ) -> tuple[list[RetrievedMemory], Event | None]:
        if (
            not self.config.memory.enabled
            or self.config.context.retrieved_context_limit_tokens == 0
        ):
            return [], None
        selected, omitted, eligible_count = await asyncio.to_thread(
            self.memory.retrieve,
            session,
            query,
            limit=self.config.memory.max_retrieved_records,
            token_budget=self.config.context.retrieved_context_limit_tokens,
        )

        def item(value: RetrievedMemory) -> dict[str, object]:
            return {
                "memory_id": value.record.id,
                "layer": value.record.layer,
                "score": value.score,
                "estimated_tokens": value.estimated_tokens,
                "provenance_event_ids": value.record.provenance_event_ids,
            }

        event = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="memory.retrieved",
            payload={
                "query_hash": retrieval_query_hash(query),
                "selected": [item(value) for value in selected],
                "omitted": [item(value) for value in omitted],
                "eligible_count": eligible_count,
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )
        return selected, event

    async def _retrieve_skills(
        self,
        session: Session,
        run_id: str,
        query: str,
        causation_id: str,
    ) -> tuple[list[SkillSummary], Event | None]:
        if not self.config.skills.enabled:
            return [], None
        selected = await asyncio.to_thread(
            self.skills.visible,
            session,
            query=query,
            limit=self.config.skills.max_catalog_entries,
        )
        event = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="skill.catalogued",
            payload={
                "query_hash": hashlib.sha256(query.encode()).hexdigest(),
                "skills": [
                    {
                        "skill_id": item.id,
                        "version_id": item.version_id,
                        "slug": item.slug,
                        "version": item.version,
                        "content_hash": item.content_hash,
                        "scope": item.scope,
                        "score": item.score,
                    }
                    for item in selected
                ],
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )
        for item in selected:
            await asyncio.to_thread(
                self.skills.record_usage,
                version_id=item.version_id,
                run_id=run_id,
                session_id=session.id,
                stage="catalogued",
            )
        return selected, event

    async def _project_episode(self, session_id: str, run_id: str) -> None:
        try:
            session = await asyncio.to_thread(self.ledger.get_session, session_id)
            mutation = await asyncio.to_thread(self.memory.project_episode, session, run_id)
            if mutation is not None:
                for event in mutation.events:
                    await self.broker.publish(
                        event.session_id,
                        {"durable": True, "event": event.model_dump(mode="json")},
                    )
        except (KeyError, ValueError) as exc:
            await self._append(
                session_id=session_id,
                run_id=run_id,
                event_type="runtime.notice",
                payload={
                    "code": "episode_projection_skipped",
                    "message": str(exc),
                    "details": {},
                },
                correlation_id=run_id,
            )

    async def _handle_tool(
        self,
        run_id: str,
        session: Session,
        invocation: ToolInvocation,
        context: ToolContext,
        clock: ActiveClock,
        allowed_tools: frozenset[str],
        capsule: AgentCapsule,
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
        active_policy_rules = (
            await asyncio.to_thread(self.policy_rules.list_rules, status="active")
            if isinstance(arguments, ShellArguments)
            else []
        )
        decision = self.policy.decide(
            invocation.name,
            arguments,
            context,
            allowed_tools=allowed_tools,
            declarative_rules=active_policy_rules,
        )
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
        if invocation.name == "spawn_agent":
            started = await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="tool.started",
                payload={"tool_call_id": invocation.tool_call_id, "name": invocation.name},
                causation_id=policy_decided.id,
                correlation_id=run_id,
            )
            result = await self._delegate(
                run_id,
                session,
                invocation,
                arguments,
                capsule,
                started.id,
            )
            await self._persist_tool_result(session, run_id, invocation, result, started.id)
            return
        if invocation.name in {"skill_load", "skill_author", "skill_run"}:
            started = await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="tool.started",
                payload={"tool_call_id": invocation.tool_call_id, "name": invocation.name},
                causation_id=policy_decided.id,
                correlation_id=run_id,
            )
            result = await self._handle_skill_tool(
                run_id, session, arguments, invocation.name, context, started.id, clock
            )
            await self._persist_tool_result(session, run_id, invocation, result, started.id)
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

    async def _handle_skill_tool(
        self,
        run_id: str,
        session: Session,
        arguments: ToolArguments,
        tool_name: str,
        context: ToolContext,
        causation_id: str,
        clock: ActiveClock,
    ) -> ToolResult:
        if isinstance(arguments, SkillLoadArguments):
            try:
                skill = await asyncio.to_thread(self.skills.get_visible, session, arguments.id)
            except (KeyError, ValueError) as exc:
                return ToolResult(status="rejected", summary=f"Skill cannot be loaded: {exc}")
            self._loaded_skills.setdefault(run_id, {})[skill.slug] = skill
            await asyncio.to_thread(
                self.skills.record_usage,
                version_id=skill.id,
                run_id=run_id,
                session_id=session.id,
                stage="loaded",
            )
            event = await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="skill.loaded",
                payload={
                    "skill_id": skill.skill_id,
                    "version_id": skill.id,
                    "slug": skill.slug,
                    "version": skill.version,
                    "content_hash": skill.content_hash,
                    "reason": "model_selected",
                    "score": next(
                        (
                            item.score
                            for item in self._skill_catalogs.get(run_id, [])
                            if item.slug == skill.slug
                        ),
                        0.0,
                    ),
                },
                causation_id=causation_id,
                correlation_id=run_id,
            )
            return ToolResult(
                status="completed",
                summary=f"loaded Skill {skill.slug} v{skill.version}",
                content=skill.instructions,
                structured_data={"event_id": event.id, "content_hash": skill.content_hash},
            )
        if isinstance(arguments, SkillAuthorArguments):
            event = await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="skill.authoring.requested",
                payload={
                    "goal": arguments.goal,
                    "scope": arguments.scope,
                    "target_skill_id": arguments.target_skill_id,
                    "evidence_event_ids": [causation_id],
                },
                causation_id=causation_id,
                correlation_id=run_id,
            )
            return ToolResult(
                status="completed",
                summary="autonomous Skill authoring will run after this turn settles",
                structured_data={"event_id": event.id},
            )
        if not isinstance(arguments, SkillRunArguments):
            return ToolResult(status="failed", summary=f"invalid {tool_name} arguments")
        skill = self._loaded_skills.get(run_id, {}).get(arguments.id)
        if skill is None:
            return ToolResult(status="rejected", summary="Skill must be loaded before script use")
        script = next(
            (item for item in skill.metadata.scripts if item.id == arguments.script), None
        )
        if script is None:
            return ToolResult(status="rejected", summary="Skill does not declare that script")
        executed = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="skill.executed",
            payload={
                "skill_id": skill.skill_id,
                "version_id": skill.id,
                "slug": skill.slug,
                "script": script.id,
                "tool_name": "skill_run",
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )
        await asyncio.to_thread(
            self.skills.record_usage,
            version_id=skill.id,
            run_id=run_id,
            session_id=session.id,
            stage="executed",
        )
        result = await clock.measure(
            self._execute_skill_script(
                skill, script.path, script.interpreter, arguments.args, context
            )
        )
        if result.status == "failed":
            try:
                _, events = await asyncio.to_thread(
                    self.skills.quarantine_and_rollback,
                    session,
                    skill.id,
                    reason="declared_script_failed",
                    causation_id=executed.id,
                )
                for event in events:
                    await self.broker.publish(
                        event.session_id,
                        {"durable": True, "event": event.model_dump(mode="json")},
                    )
                await self._append(
                    session_id=session.id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="skill.authoring.requested",
                    payload={
                        "goal": f"Correct failed script {script.id}: {result.summary}",
                        "scope": skill.scope,
                        "target_skill_id": skill.skill_id,
                        "evidence_event_ids": [executed.id],
                    },
                    causation_id=events[-1].id,
                    correlation_id=run_id,
                )
            except (KeyError, ValueError):
                pass
        return result

    async def _execute_skill_script(
        self,
        skill: SkillVersion,
        script_path: str,
        interpreter: str,
        args: list[str],
        context: ToolContext,
    ) -> ToolResult:
        started = time.monotonic()
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            return ToolResult(
                status="rejected", summary="Skill script isolation is unavailable (bwrap missing)"
            )
        scratch = context.root_for("scratch")
        command = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--ro-bind",
            str(Path(skill.package_path)),
            "/skill",
            "--ro-bind",
            str(context.project_root),
            "/project",
            "--bind",
            str(scratch),
            "/workspace",
            "--chdir",
            "/workspace",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin",
            "--setenv",
            "HOME",
            "/workspace",
            "/usr/bin/python3" if interpreter == "python" else "/usr/bin/bash",
            f"/skill/{script_path}",
            *args,
        ]
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.config.skills.script_timeout_seconds
            )
            output = (stdout + stderr).decode("utf-8", errors="replace")
            bounded = output[: self.config.tools.model_result_char_limit]
            return ToolResult(
                status="completed" if process.returncode == 0 else "failed",
                summary=(
                    f"Skill script {script_path} completed"
                    if process.returncode == 0
                    else f"Skill script {script_path} exited {process.returncode}"
                ),
                content=bounded,
                truncated=len(output) > len(bounded),
                structured_data={"exit_code": process.returncode},
                duration_seconds=time.monotonic() - started,
            )
        except TimeoutError:
            if process is not None:
                process.kill()
                await process.communicate()
            return ToolResult(
                status="failed",
                summary=f"Skill script exceeded {self.config.skills.script_timeout_seconds}s",
                duration_seconds=time.monotonic() - started,
            )
        except OSError as exc:
            return ToolResult(
                status="failed",
                summary=f"Skill script failed: {exc}",
                duration_seconds=time.monotonic() - started,
            )

    async def _delegate(
        self,
        run_id: str,
        session: Session,
        invocation: ToolInvocation,
        arguments: ToolArguments,
        capsule: AgentCapsule,
        causation_id: str,
    ) -> ToolResult:
        """Run one explicitly-scoped child and return its durable terminal outcome."""

        if not isinstance(arguments, SpawnAgentArguments):
            return ToolResult(status="failed", summary="invalid spawn_agent arguments")
        if not capsule.metadata.delegation.allow:
            return ToolResult(status="rejected", summary="agent delegation is not permitted")
        if session.delegation_depth >= self.config.runtime.max_delegation_depth:
            return ToolResult(status="rejected", summary="delegation depth limit was reached")
        if arguments.agent_id not in capsule.metadata.delegation.allowed_agents:
            return ToolResult(status="rejected", summary="target agent is not permitted")
        count = self._child_count_by_parent.get(run_id, 0)
        if count >= self.config.runtime.max_child_runs_per_parent_run:
            return ToolResult(status="rejected", summary="child-run limit was reached")
        try:
            await asyncio.to_thread(self.agents.load, arguments.agent_id)
        except (FileNotFoundError, ValueError) as exc:
            return ToolResult(status="rejected", summary=f"unknown child agent: {exc}")

        evidence: list[dict[str, str]] = []
        for event_ref in arguments.evidence_event_ids:
            try:
                event = await asyncio.to_thread(
                    self.ledger.resolve_visible_event, session.id, event_ref
                )
            except KeyError:
                return ToolResult(
                    status="rejected",
                    summary=f"evidence event is not visible: {event_ref}",
                )
            if event.type not in {
                "user.message",
                "assistant.message",
                "tool.completed",
                "tool.failed",
                "tool.rejected",
            }:
                return ToolResult(
                    status="rejected",
                    summary=f"event {event.id} is not valid delegation evidence",
                )
            evidence.append(
                {
                    "event_id": event.id,
                    "event_type": event.type,
                    "payload_hash": event.payload_hash,
                    "content": json.dumps(event.payload, separators=(",", ":"), sort_keys=True),
                }
            )

        requested = await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="delegation.requested",
            payload={
                "tool_call_id": invocation.tool_call_id,
                "target_agent_id": arguments.agent_id,
                "task": arguments.task,
                "evidence": evidence,
                "delegation_depth": session.delegation_depth + 1,
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )
        child = await asyncio.to_thread(
            self.ledger.create_delegated_session,
            session.id,
            parent_event_id=requested.id,
            agent_id=arguments.agent_id,
        )
        await self._append(
            session_id=child.id,
            agent_id=child.agent_id,
            event_type="delegation.task_card",
            payload={
                "parent_session_id": session.id,
                "parent_run_id": run_id,
                "parent_event_id": requested.id,
                "target_agent_id": child.agent_id,
                "task": arguments.task,
                "evidence": evidence,
                "delegation_depth": child.delegation_depth,
            },
            causation_id=requested.id,
            correlation_id=child.id,
        )
        self._child_count_by_parent[run_id] = count + 1
        child_run_id = await self.start(child.id, arguments.task)
        self._children_by_parent.setdefault(run_id, set()).add(child_run_id)
        child_task = self._tasks[child_run_id]
        started = time.monotonic()
        try:
            await asyncio.shield(child_task)
        except asyncio.CancelledError:
            await self._cancel_children(run_id)
            raise

        events = await asyncio.to_thread(self.ledger.list_run_events, child_run_id)
        completed = any(event.type == "run.completed" for event in events)
        message = next(
            (
                str(event.payload.get("content", ""))
                for event in reversed(events)
                if event.type == "assistant.message" and event.payload.get("status") == "completed"
            ),
            "",
        )
        status = "completed" if completed else "failed"
        summary = "child agent completed" if completed else "child agent did not complete"
        terminal = "delegation.completed" if completed else "delegation.failed"
        await self._append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type=terminal,
            payload={
                "child_session_id": child.id,
                "child_run_id": child_run_id,
                "target_agent_id": child.agent_id,
                "status": status,
                "summary": summary,
                "duration_seconds": time.monotonic() - started,
            },
            causation_id=requested.id,
            correlation_id=run_id,
        )
        return ToolResult(
            status=status,
            summary=summary,
            content=message,
            structured_data={
                "child_session_id": child.id,
                "child_run_id": child_run_id,
                "agent_id": child.agent_id,
                "requested_result_format": arguments.requested_result_format,
            },
            duration_seconds=time.monotonic() - started,
        )

    async def _cancel_children(self, parent_run_id: str) -> None:
        child_run_ids = tuple(self._children_by_parent.get(parent_run_id, set()))
        tasks: list[asyncio.Task[None]] = []
        for child_run_id in child_run_ids:
            task = self._tasks.get(child_run_id)
            if task is not None and not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

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
            stale_before = time.time() - 86_400
            for child in self._scratch_base.iterdir():
                if child.is_dir() and child.stat().st_mtime < stale_before:
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
