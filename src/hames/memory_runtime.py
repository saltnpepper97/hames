"""Recoverable background extraction for important relationship and semantic memory."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.ledger import Event, Ledger, Session
from hames.memory import MemoryCandidate, MemoryJob, MemoryStore, should_auto_activate
from hames.providers import ModelRequest, Provider, ProviderError, StreamEventKind, ToolDefinition
from hames.providers.base import JSON_OBJECT, ProviderMessage

EXTRACTION_SYSTEM = """You extract only important durable memory from one completed Hames
turn. Submit zero or more candidates through submit_memory_candidates. Relationship memory is
about stable user preferences or supported relationships. Semantic memory is a durable known
fact. Never store procedures, secrets, transient mood, guesses, routine chat, or mundane details.
Every candidate must cite only supplied evidence event IDs. Direct user statements use
explicit_user; facts established by successful tools use successful_tool; anything else uses
assistant_inference. Use global visibility only for facts useful across workspaces, otherwise use
workspace. Express value as a concise string and always provide anchors, using an empty list when
none are needed. Episodic memory is created deterministically elsewhere.
"""


class ExtractionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[MemoryCandidate] = Field(default_factory=lambda: list[MemoryCandidate]())


class MemoryManager:
    def __init__(
        self,
        *,
        ledger: Ledger,
        config: HamesConfig,
        providers: dict[str, Provider],
        broker: EventBroker,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.providers = providers
        self.broker = broker
        self.store = MemoryStore(ledger)
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> frozenset[str]:
        if self._worker is not None and not self._worker.done():
            return frozenset()
        recovered = await asyncio.to_thread(self.store.recover_jobs)
        for job in recovered:
            self._queue.put_nowait(job.id)
        self._worker = asyncio.create_task(self._work(), name="hames-memory-worker")
        return frozenset(job.id for job in recovered)

    async def _schedule(self, job_id: str) -> None:
        recovered = await self.start()
        if job_id not in recovered:
            self._queue.put_nowait(job_id)

    async def close(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
        self._worker = None

    async def enqueue_run(self, session_id: str, run_id: str) -> MemoryJob | None:
        if not self.config.memory.enabled or not self.config.memory.automatic_extraction:
            return None
        events = await asyncio.to_thread(self.ledger.list_run_events, run_id)
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.type in {"run.completed", "run.failed", "run.cancelled"}
            ),
            None,
        )
        if terminal is None:
            return None
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        try:
            job, event = await asyncio.to_thread(
                self.store.queue_job,
                session=session,
                kind="extraction",
                source_event_id=terminal.id,
                run_id=run_id,
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return None
            raise
        await self._publish(event)
        await self._schedule(job.id)
        return job

    async def enqueue_capture(self, session: Session, content: str, source: Event) -> MemoryJob:
        job, event = await asyncio.to_thread(
            self.store.queue_job,
            session=session,
            kind="explicit_capture",
            source_event_id=source.id,
            run_id=source.run_id,
            content=content,
        )
        await self._publish(event)
        await self._schedule(job.id)
        return job

    async def retry(self, session_id: str, job_id: str) -> MemoryJob:
        job = await asyncio.to_thread(self.store.retry_job, session_id, job_id)
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        await self._append(
            session_id=session.id,
            run_id=job.run_id,
            agent_id=session.agent_id,
            event_type="memory.job.queued",
            payload={
                "job_id": job.id,
                "kind": job.kind,
                "status": "pending",
                "attempts": job.attempts,
            },
            causation_id=job.source_event_id,
            correlation_id=job.id,
        )
        await self._schedule(job.id)
        return job

    async def _work(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await asyncio.sleep(0.2)
                await self._execute(job_id)
            except asyncio.CancelledError:
                raise
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: str) -> None:
        try:
            job, started = await asyncio.to_thread(self.store.start_job, job_id)
        except ValueError:
            return
        await self._publish(started)
        try:
            candidates = await self._extract(job)
            if len(candidates) > self.config.memory.max_proposals_per_pass:
                raise ValueError("extractor exceeded max_proposals_per_pass")
            session = await asyncio.to_thread(self.ledger.get_session, job.session_id)
            for candidate in candidates:
                normalized = self._normalize(candidate)
                explicit = job.kind == "explicit_capture"
                mutation = await asyncio.to_thread(
                    self.store.create_candidate,
                    session=session,
                    candidate=normalized,
                    run_id=job.run_id,
                    origin_kind="explicit" if explicit else "automatic",
                    activate=should_auto_activate(normalized, explicit=explicit),
                    causation_id=job.source_event_id,
                )
                for event in mutation.events:
                    await self._publish(event)
            _, completed = await asyncio.to_thread(self.store.finish_job, job.id)
            await self._publish(completed)
        except (ProviderError, ValueError, KeyError) as exc:
            if isinstance(exc, ProviderError) and exc.code == "maintenance_preempted":
                _, paused = await asyncio.to_thread(
                    self.store.pause_job, job.id, reason=str(exc)
                )
                await self._publish(paused)
                self._queue.put_nowait(job.id)
                return
            code = exc.code if isinstance(exc, ProviderError) else "memory_extraction_failed"
            retry = job.attempts <= self.config.memory.max_extraction_retries
            updated, failed = await asyncio.to_thread(
                self.store.finish_job,
                job.id,
                error_code=code,
                error_message=str(exc),
                retry=retry,
            )
            await self._publish(failed)
            if updated.status == "pending":
                self._queue.put_nowait(job.id)

    async def _extract(self, job: MemoryJob) -> list[MemoryCandidate]:
        session = await asyncio.to_thread(self.ledger.get_session, job.session_id)
        profile_id = self.config.memory.provider or session.provider
        provider = self.providers.get(profile_id)
        if provider is None:
            raise ValueError(f"unknown memory provider: {profile_id}")
        model = self.config.memory.model or session.model
        reasoning = self.config.memory.reasoning_effort or session.reasoning_effort
        evidence = await asyncio.to_thread(self._evidence, job)
        request_event = await self._append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="model.requested",
            payload={
                "provider": profile_id,
                "model": model,
                "reasoning_effort": reasoning,
                "agent_capsule_hash": "memory-extractor-v1",
                "purpose": "memory_extraction",
            },
            causation_id=job.source_event_id,
            correlation_id=job.id,
        )
        request = ModelRequest(
            model=model,
            system=EXTRACTION_SYSTEM,
            messages=[ProviderMessage(role="user", content=evidence)],
            reasoning_effort=reasoning,
            max_tokens=2048,
            temperature=0,
            tools=[memory_submission_tool(self.config.memory.max_proposals_per_pass)],
            metadata={"purpose": "memory_extraction", "job_id": job.id},
        )
        name_parts: list[str] = []
        argument_parts: list[str] = []
        started = completed = False
        try:
            async for event in provider.stream(request):
                if event.kind is StreamEventKind.STARTED:
                    started = True
                    await self._append(
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="model.response.started",
                        payload={"provider_request_id": event.provider_request_id},
                        causation_id=request_event.id,
                        correlation_id=job.id,
                    )
                elif event.kind is StreamEventKind.TOOL_CALL_DELTA:
                    if event.tool_call is None or event.tool_call.index != 0:
                        raise ValueError("memory extractor emitted an invalid tool call")
                    if event.tool_call.name:
                        name_parts.append(event.tool_call.name)
                    argument_parts.append(event.tool_call.arguments_delta)
                elif event.kind is StreamEventKind.USAGE and event.usage is not None:
                    await self._append(
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="model.usage",
                        payload=event.usage.model_dump(mode="json"),
                        causation_id=request_event.id,
                        correlation_id=job.id,
                    )
                elif event.kind is StreamEventKind.COMPLETED:
                    completed = True
                    await self._append(
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="model.response.completed",
                        payload={"finish_reason": event.finish_reason or "stop"},
                        causation_id=request_event.id,
                        correlation_id=job.id,
                    )
            if not started or not completed:
                raise ValueError("memory extractor stream did not complete")
            if "".join(name_parts) != "submit_memory_candidates":
                raise ValueError("memory extractor did not submit candidates")
            raw = "".join(argument_parts) or "{}"
            return ExtractionSubmission.model_validate(JSON_OBJECT.validate_json(raw)).candidates
        except (ProviderError, ValueError) as exc:
            await self._append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type=(
                    "model.response.preempted"
                    if isinstance(exc, ProviderError) and exc.code == "maintenance_preempted"
                    else "model.response.failed"
                ),
                payload={
                    "code": exc.code
                    if isinstance(exc, ProviderError)
                    else "memory_extraction_failed",
                    "message": str(exc),
                    "retryable": isinstance(exc, ProviderError) and exc.retryable,
                    "details": {},
                },
                causation_id=request_event.id,
                correlation_id=job.id,
            )
            raise

    def _evidence(self, job: MemoryJob) -> str:
        if job.kind == "explicit_capture":
            payload: dict[str, Any] = {
                "explicit_capture": True,
                "content": job.content or "",
                "evidence_event_ids": [job.source_event_id],
            }
            return json.dumps(payload, separators=(",", ":"), sort_keys=True)
        events = self.ledger.list_run_events(job.run_id or "")
        started = next((event for event in events if event.type == "run.started"), None)
        user = (
            self.ledger.get_event(started.causation_id)
            if started and started.causation_id
            else None
        )
        evidence: list[dict[str, Any]] = []
        if user is not None:
            evidence.append({"event_id": user.id, "type": user.type, "payload": user.payload})
        for event in events:
            if event.type in {
                "assistant.message",
                "tool.completed",
                "tool.failed",
                "tool.rejected",
            }:
                payload = dict(event.payload)
                if event.type.startswith("tool."):
                    payload = {
                        "name": payload.get("name"),
                        "status": payload.get("status"),
                        "summary": payload.get("summary"),
                    }
                evidence.append({"event_id": event.id, "type": event.type, "payload": payload})
        return json.dumps({"explicit_capture": False, "evidence": evidence}, separators=(",", ":"))

    @staticmethod
    def _normalize(candidate: MemoryCandidate) -> MemoryCandidate:
        if candidate.layer == "episodic":
            raise ValueError("extractor cannot create episodic memory")
        visibility = "global" if candidate.layer == "relationship" else candidate.visibility
        if visibility not in {"global", "workspace"}:
            visibility = "workspace"
        return candidate.model_copy(update={"visibility": visibility})

    async def _append(self, **kwargs: Any) -> Event:
        event = await asyncio.to_thread(self.ledger.append, **kwargs)
        await self._publish(event)
        return event

    async def _publish(self, event: Event) -> None:
        await self.broker.publish(
            event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
        )


def memory_submission_tool(max_candidates: int = 4) -> ToolDefinition:
    # Keep this boundary schema self-contained. Pydantic's MemoryCandidate schema
    # nests $defs below array items, while llama.cpp resolves $ref from the tool
    # root and rejects that otherwise valid JSON Schema.
    candidate_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "layer": {"type": "string", "enum": ["relationship", "semantic"]},
            "visibility": {
                "type": "string",
                "enum": ["global", "agent_private", "workspace", "session_team"],
            },
            "subject": {"type": "string"},
            "predicate": {"type": "string"},
            "value": {"type": "string"},
            "summary": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "importance": {"type": "number", "minimum": 0, "maximum": 1},
            "anchors": {
                "type": "array",
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "value": {"type": "string"},
                    },
                    "required": ["kind", "value"],
                    "additionalProperties": False,
                },
            },
            "provenance_event_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 8,
            },
            "evidence_basis": {
                "type": "string",
                "enum": ["explicit_user", "successful_tool", "assistant_inference"],
            },
        },
        "required": [
            "layer",
            "visibility",
            "subject",
            "predicate",
            "value",
            "summary",
            "confidence",
            "importance",
            "anchors",
            "provenance_event_ids",
            "evidence_basis",
        ],
        "additionalProperties": False,
    }
    return ToolDefinition(
        name="submit_memory_candidates",
        description="Submit only important durable memory candidates, or an empty list.",
        input_schema={
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "maxItems": max_candidates,
                    "items": candidate_schema,
                }
            },
            "required": ["candidates"],
            "additionalProperties": False,
        },
    )
