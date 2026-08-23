"""Recoverable autonomous authoring, evaluation, and repair of Skills."""

from __future__ import annotations

import asyncio
import json
import py_compile
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.ledger import Event, Ledger, Session
from hames.providers import ModelRequest, Provider, ProviderError, StreamEventKind, ToolDefinition
from hames.providers.base import JSON_OBJECT, JsonValue, ProviderMessage
from hames.skills import SkillDraft, SkillJob, SkillRegistry, SkillScope, SkillVersion

AUTHOR_SYSTEM = """You autonomously create one reusable Hames Skill from proven workflow
evidence. A Skill is an operational procedure, not a conversational suggestion. Preserve the
successful ordering and verification steps in the evidence. Keep authority narrow: declare only
tools already used or clearly required, never credentials, and never weaken policy. Prefer plain
instructions. Add a script only when deterministic reusable automation materially improves the
workflow. Every script must accept --self-test, perform a harmless offline self-test in its current
directory, and exit nonzero on failure. Submit exactly one candidate through submit_skill_candidate.
"""

EVALUATOR_SYSTEM = """You independently evaluate a candidate Hames Skill against its evidence
and deterministic validation report. Reject vague, unsafe, over-broad, ungrounded, or incomplete
procedures. A passing Skill must preserve the observed workflow, include verification, request no
unnecessary authority, and have scripts that passed their offline self-tests. Submit exactly one
verdict through submit_skill_evaluation. Do not rewrite the Skill.
"""


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillEvaluation(RuntimeModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    summary: str
    findings: list[str] = Field(default_factory=list)


class SkillManager:
    def __init__(
        self,
        *,
        ledger: Ledger,
        config: HamesConfig,
        providers: dict[str, Provider],
        broker: EventBroker,
        registry: SkillRegistry,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.providers = providers
        self.broker = broker
        self.registry = registry
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        for job in await asyncio.to_thread(self.registry.recover_jobs):
            self._queue.put_nowait(job.id)
        self._worker = asyncio.create_task(self._work(), name="hames-skill-worker")

    async def close(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        await asyncio.gather(self._worker, return_exceptions=True)
        self._worker = None

    async def observe_run(self, session_id: str, run_id: str) -> list[SkillJob]:
        if not self.config.skills.enabled or not self.config.skills.autonomous_authoring:
            return []
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        events = await asyncio.to_thread(self.ledger.list_run_events, run_id)
        terminal = next(
            (
                event
                for event in reversed(events)
                if event.type in {"run.completed", "run.failed", "run.cancelled"}
            ),
            None,
        )
        started = next((event for event in events if event.type == "run.started"), None)
        if terminal is None or started is None or started.causation_id is None:
            return []
        user = await asyncio.to_thread(self.ledger.get_event, started.causation_id)
        task_text = str(user.payload.get("content", ""))
        tool_sequence = [
            str(event.payload.get("name", ""))
            for event in events
            if event.type in {"tool.completed", "tool.failed", "tool.rejected"}
            and event.payload.get("name")
        ]
        outcome = cast(
            Literal["completed", "failed", "cancelled"], terminal.type.removeprefix("run.")
        )
        observed, similar = await asyncio.to_thread(
            self.registry.observe_workflow,
            session=session,
            run_id=run_id,
            task_text=task_text,
            tool_sequence=tool_sequence,
            outcome=outcome,
            causation_id=terminal.id,
            similarity_threshold=self.config.skills.task_similarity_threshold,
        )
        await self._publish(observed)
        outcome_events = await asyncio.to_thread(
            self.registry.record_run_outcomes,
            session=session,
            run_id=run_id,
            outcome=outcome,
            tool_calls=len(tool_sequence),
            correction=any(event.type == "skill.authoring.requested" for event in events),
            causation_id=observed.id,
        )
        for event in outcome_events:
            await self._publish(event)
        queued: list[SkillJob] = []
        requests = [event for event in events if event.type == "skill.authoring.requested"]
        for request in requests:
            scope = str(request.payload.get("scope", "workspace"))
            job = await self._queue_job(
                session,
                kind="patch" if request.payload.get("target_skill_id") else "author",
                source=request,
                run_id=run_id,
                goal=str(request.payload.get("goal", task_text)),
                scope="agent"
                if scope.startswith("agent")
                else "global"
                if scope == "global"
                else "workspace",
                target_skill_id=(
                    None
                    if request.payload.get("target_skill_id") is None
                    else str(request.payload["target_skill_id"])
                ),
            )
            if job is not None:
                queued.append(job)
        repeated = (
            outcome == "completed"
            and len(tool_sequence) >= 2
            and len(similar) + 1 >= self.config.skills.repetition_threshold
        )
        if repeated and not self._has_matching_skill(session, task_text, tool_sequence):
            triggered = await self._append(
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="skill.proposal_triggered",
                payload={
                    "goal": task_text,
                    "scope": "workspace",
                    "evidence_event_ids": [user.id, observed.id],
                },
                causation_id=observed.id,
                correlation_id=run_id,
            )
            job = await self._queue_job(
                session,
                kind="author",
                source=triggered,
                run_id=run_id,
                goal=task_text,
                scope="workspace",
            )
            if job is not None:
                queued.append(job)
        if queued:
            await self.start()
            for job in queued:
                self._queue.put_nowait(job.id)
        return queued

    async def author(
        self,
        session: Session,
        *,
        goal: str,
        scope: str = "workspace",
        target_skill_id: str | None = None,
        source_event_id: str | None = None,
    ) -> SkillJob:
        source = (
            await asyncio.to_thread(self.ledger.get_event, source_event_id)
            if source_event_id
            else await self._append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="skill.authoring.requested",
                payload={
                    "goal": goal,
                    "scope": scope,
                    "target_skill_id": target_skill_id,
                    "evidence_event_ids": [],
                },
            )
        )
        job = await self._queue_job(
            session,
            kind="patch" if target_skill_id else "author",
            source=source,
            run_id=source.run_id,
            goal=goal,
            scope="agent"
            if scope.startswith("agent")
            else "global"
            if scope == "global"
            else "workspace",
            target_skill_id=target_skill_id,
        )
        if job is None:
            raise ValueError("Skill authoring job is already queued")
        await self.start()
        self._queue.put_nowait(job.id)
        return job

    async def retry(self, session_id: str, job_id: str) -> SkillJob:
        job = await asyncio.to_thread(self.registry.retry_job, session_id, job_id)
        await self.start()
        self._queue.put_nowait(job.id)
        return job

    async def _queue_job(
        self,
        session: Session,
        *,
        kind: Literal["author", "patch", "revalidate"],
        source: Event,
        run_id: str | None,
        goal: str,
        scope: SkillScope,
        target_skill_id: str | None = None,
    ) -> SkillJob | None:
        try:
            job, event = await asyncio.to_thread(
                self.registry.queue_job,
                session=session,
                kind=kind,
                source_event_id=source.id,
                run_id=run_id,
                goal=goal,
                scope=scope,
                target_skill_id=target_skill_id,
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                return None
            raise
        await self._publish(event)
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
        job, started = await asyncio.to_thread(self.registry.start_job, job_id)
        await self._publish(started)
        try:
            used = await asyncio.to_thread(self.registry.background_model_calls_today)
            if used + 2 > self.config.skills.max_background_model_calls_per_day:
                _, event = await asyncio.to_thread(
                    self.registry.finish_job,
                    job.id,
                    error_code="skill_daily_budget_exhausted",
                    error_message="daily autonomous Skill model-call budget is exhausted",
                    budget_wait=True,
                )
                await self._publish(event)
                return
            session = await asyncio.to_thread(self.ledger.get_session, job.session_id)
            draft = await self._draft(job, session)
            evidence_ids = self._evidence_ids(job)
            mutation = await asyncio.to_thread(
                self.registry.create_draft,
                session=session,
                draft=draft,
                evidence_event_ids=evidence_ids,
                created_by="automatic",
                run_id=job.run_id,
                causation_id=job.source_event_id,
                target_skill_id=job.target_skill_id,
            )
            for event in mutation.events:
                await self._publish(event)
            deterministic = await asyncio.to_thread(self._validate, mutation.version)
            await asyncio.to_thread(
                self.registry.record_evaluation,
                mutation.version.id,
                kind="deterministic",
                passed=bool(deterministic["passed"]),
                score=1.0 if deterministic["passed"] else 0.0,
                report=deterministic,
            )
            validated = await self._append(
                session_id=session.id,
                run_id=job.run_id,
                agent_id=session.agent_id,
                event_type="skill.validated",
                payload={
                    "skill_id": mutation.version.skill_id,
                    "version_id": mutation.version.id,
                    "kind": "deterministic",
                    "status": "passed" if deterministic["passed"] else "failed",
                    "score": 1.0 if deterministic["passed"] else 0.0,
                    "report": deterministic,
                },
                causation_id=mutation.events[-1].id,
                correlation_id=job.id,
            )
            if not deterministic["passed"]:
                await self._reject(
                    session, mutation.version, validated, "deterministic_validation_failed"
                )
            else:
                verdict = await self._evaluate(job, session, mutation.version, deterministic)
                await asyncio.to_thread(
                    self.registry.record_evaluation,
                    mutation.version.id,
                    kind="model",
                    passed=verdict.passed,
                    score=verdict.score,
                    report=verdict.model_dump(mode="json"),
                )
                evaluated = await self._append(
                    session_id=session.id,
                    run_id=job.run_id,
                    agent_id=session.agent_id,
                    event_type="skill.evaluated",
                    payload={
                        "skill_id": mutation.version.skill_id,
                        "version_id": mutation.version.id,
                        "kind": "model",
                        "status": "passed" if verdict.passed else "failed",
                        "score": verdict.score,
                        "report": verdict.model_dump(mode="json"),
                    },
                    causation_id=validated.id,
                    correlation_id=job.id,
                )
                if verdict.passed and verdict.score >= self.config.skills.evaluator_pass_score:
                    if self.config.skills.auto_activate:
                        activated = await asyncio.to_thread(
                            self.registry.activate,
                            session=session,
                            version_id=mutation.version.id,
                            causation_id=evaluated.id,
                        )
                        for event in activated.events:
                            await self._publish(event)
                else:
                    await self._reject(
                        session, mutation.version, evaluated, "independent_evaluation_failed"
                    )
            _, completed = await asyncio.to_thread(self.registry.finish_job, job.id)
            await self._publish(completed)
        except (ProviderError, ValueError, KeyError, OSError) as exc:
            code = exc.code if isinstance(exc, ProviderError) else "skill_authoring_failed"
            retry = job.attempts <= self.config.skills.max_job_retries
            updated, failed = await asyncio.to_thread(
                self.registry.finish_job,
                job.id,
                error_code=code,
                error_message=str(exc),
                retry=retry,
            )
            await self._publish(failed)
            if updated.status == "pending":
                self._queue.put_nowait(job.id)

    async def _reject(
        self, session: Session, version: SkillVersion, cause: Event, reason: str
    ) -> None:
        event = await asyncio.to_thread(
            self.registry.reject,
            session,
            version.id,
            reason=reason,
            causation_id=cause.id,
        )
        await self._publish(event)

    async def _draft(self, job: SkillJob, session: Session) -> SkillDraft:
        evidence = self._evidence(job)
        current: dict[str, Any] | None = None
        if job.target_skill_id:
            active = await asyncio.to_thread(self.registry.active_version, job.target_skill_id)
            if active is not None:
                current = {
                    "slug": active.slug,
                    "description": active.description,
                    "instructions": active.instructions,
                    "metadata": active.metadata.model_dump(mode="json"),
                }
        raw = await self._model_submission(
            job,
            session,
            purpose="skill_authoring",
            system=AUTHOR_SYSTEM,
            content=json.dumps(
                {
                    "goal": job.goal,
                    "scope": self._draft_scope(job, session),
                    "evidence": evidence,
                    "current_skill": current,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            tool=draft_submission_tool(),
        )
        draft = SkillDraft.model_validate(raw).model_copy(
            update={"scope": self._draft_scope(job, session)}
        )
        observed_tools = {
            str(item["payload"].get("name"))
            for item in evidence
            if str(item["type"]).startswith("tool.")
            and isinstance(item.get("payload"), dict)
            and item["payload"].get("name")
        }
        permitted = observed_tools | {"skill_load", "skill_run", "skill_author"}
        if current is not None:
            metadata: object = current.get("metadata")
            if isinstance(metadata, dict):
                typed_metadata = cast(dict[str, object], metadata)
                tools: object = typed_metadata.get("tools", [])
                if isinstance(tools, list):
                    permitted.update(str(item) for item in cast(list[object], tools))
        if job.run_id is not None and not set(draft.tools) <= permitted:
            raise ValueError("Skill candidate requests tools not grounded in workflow evidence")
        return draft

    async def _evaluate(
        self,
        job: SkillJob,
        session: Session,
        version: SkillVersion,
        deterministic: dict[str, Any],
    ) -> SkillEvaluation:
        raw = await self._model_submission(
            job,
            session,
            purpose="skill_evaluation",
            system=EVALUATOR_SYSTEM,
            content=json.dumps(
                {
                    "goal": job.goal,
                    "evidence": self._evidence(job),
                    "candidate": {
                        "metadata": version.metadata.model_dump(mode="json"),
                        "instructions": version.instructions,
                    },
                    "deterministic_validation": deterministic,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
            tool=evaluation_submission_tool(),
        )
        return SkillEvaluation.model_validate(raw)

    async def _model_submission(
        self,
        job: SkillJob,
        session: Session,
        *,
        purpose: str,
        system: str,
        content: str,
        tool: ToolDefinition,
    ) -> dict[str, Any]:
        profile_id = self.config.skills.provider or session.provider
        provider = self.providers.get(profile_id)
        if provider is None:
            raise ValueError(f"unknown Skills provider: {profile_id}")
        model = self.config.skills.model or session.model
        reasoning = self.config.skills.reasoning_effort or session.reasoning_effort
        requested = await self._append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="model.requested",
            payload={
                "provider": profile_id,
                "model": model,
                "reasoning_effort": reasoning,
                "agent_capsule_hash": "skills-v1",
                "purpose": purpose,
            },
            causation_id=job.source_event_id,
            correlation_id=job.id,
        )
        request = ModelRequest(
            model=model,
            system=system,
            messages=[ProviderMessage(role="user", content=content)],
            reasoning_effort=reasoning,
            max_tokens=4096,
            temperature=0,
            tools=[tool],
            metadata={"purpose": purpose, "job_id": job.id},
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
                        causation_id=requested.id,
                        correlation_id=job.id,
                    )
                elif event.kind is StreamEventKind.TOOL_CALL_DELTA:
                    if event.tool_call is None or event.tool_call.index != 0:
                        raise ValueError("Skill maintenance model emitted an invalid tool call")
                    if event.tool_call.name:
                        name_parts.append(event.tool_call.name)
                    argument_parts.append(event.tool_call.arguments_delta)
                elif event.kind is StreamEventKind.USAGE and event.usage is not None:
                    await self._append(
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="model.usage",
                        payload=event.usage.model_dump(mode="json"),
                        causation_id=requested.id,
                        correlation_id=job.id,
                    )
                elif event.kind is StreamEventKind.COMPLETED:
                    completed = True
                    await self._append(
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="model.response.completed",
                        payload={"finish_reason": event.finish_reason or "tool_calls"},
                        causation_id=requested.id,
                        correlation_id=job.id,
                    )
            if not started or not completed or "".join(name_parts) != tool.name:
                raise ValueError("Skill maintenance model did not submit the required tool")
            return dict(JSON_OBJECT.validate_json("".join(argument_parts) or "{}"))
        except (ProviderError, ValueError) as exc:
            await self._append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="model.response.failed",
                payload={
                    "code": exc.code if isinstance(exc, ProviderError) else "skill_model_failed",
                    "message": str(exc),
                    "retryable": isinstance(exc, ProviderError) and exc.retryable,
                    "details": {},
                },
                causation_id=requested.id,
                correlation_id=job.id,
            )
            raise

    def _validate(self, version: SkillVersion) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        bwrap = shutil.which("bwrap")
        for script in version.metadata.scripts:
            path = Path(version.package_path) / script.path
            try:
                if script.interpreter == "python":
                    py_compile.compile(str(path), doraise=True)
                else:
                    subprocess.run(
                        ["/usr/bin/bash", "-n", str(path)],
                        check=True,
                        capture_output=True,
                        timeout=self.config.skills.script_timeout_seconds,
                    )
                if bwrap is None:
                    raise OSError("Skill script isolation is unavailable (bwrap missing)")
                with tempfile.TemporaryDirectory(prefix="hames-skill-test-") as scratch:
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
                        version.package_path,
                        "/skill",
                        "--bind",
                        scratch,
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
                        "/usr/bin/python3" if script.interpreter == "python" else "/usr/bin/bash",
                        f"/skill/{script.path}",
                        "--self-test",
                    ]
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        timeout=self.config.skills.script_timeout_seconds,
                    )
                checks.append(
                    {
                        "script": script.id,
                        "passed": completed.returncode == 0,
                        "exit_code": completed.returncode,
                        "output": (completed.stdout + completed.stderr)[-2000:],
                    }
                )
            except (OSError, subprocess.SubprocessError, py_compile.PyCompileError) as exc:
                checks.append({"script": script.id, "passed": False, "error": str(exc)})
        return {"passed": all(bool(item["passed"]) for item in checks), "script_checks": checks}

    def _evidence(self, job: SkillJob) -> list[dict[str, Any]]:
        if job.run_id is None:
            event = self.ledger.get_event(job.source_event_id)
            return [{"event_id": event.id, "type": event.type, "payload": event.payload}]
        result: list[dict[str, Any]] = []
        events = self.ledger.list_run_events(job.run_id)
        started = next((event for event in events if event.type == "run.started"), None)
        if started and started.causation_id:
            user = self.ledger.get_event(started.causation_id)
            result.append({"event_id": user.id, "type": user.type, "payload": user.payload})
        for event in events:
            if event.type in {
                "tool.completed",
                "tool.failed",
                "tool.rejected",
                "assistant.message",
            }:
                result.append({"event_id": event.id, "type": event.type, "payload": event.payload})
        return result

    def _evidence_ids(self, job: SkillJob) -> list[str]:
        values = [str(item["event_id"]) for item in self._evidence(job)]
        if job.source_event_id not in values:
            values.append(job.source_event_id)
        return values

    def _has_matching_skill(
        self, session: Session, task_text: str, tool_sequence: list[str]
    ) -> bool:
        required = set(tool_sequence)
        return any(
            required <= set(item.tools)
            and item.score >= self.config.skills.task_similarity_threshold
            for item in self.registry.visible(session, query=task_text, limit=100)
        )

    @staticmethod
    def _draft_scope(job: SkillJob, session: Session) -> str:
        return f"agent:{session.agent_id}" if job.scope == "agent" else job.scope

    async def _append(self, **kwargs: Any) -> Event:
        event = await asyncio.to_thread(self.ledger.append, **kwargs)
        await self._publish(event)
        return event

    async def _publish(self, event: Event) -> None:
        await self.broker.publish(
            event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
        )


def draft_submission_tool() -> ToolDefinition:
    script: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "path": {"type": "string"},
            "interpreter": {"type": "string", "enum": ["python", "bash"]},
            "description": {"type": "string"},
        },
        "required": ["id", "path", "interpreter", "description"],
        "additionalProperties": False,
    }
    input_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "scope": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "triggers": {"type": "array", "items": {"type": "string"}},
            "requires": {"type": "array", "items": {"type": "string"}},
            "instructions": {"type": "string"},
            "scripts": {"type": "array", "items": script},
            "files": {"type": "object", "additionalProperties": {"type": "string"}},
            "rationale": {"type": "string"},
        },
        "required": [
            "id",
            "name",
            "description",
            "scope",
            "tools",
            "triggers",
            "requires",
            "instructions",
            "scripts",
            "files",
            "rationale",
        ],
        "additionalProperties": False,
    }
    return ToolDefinition(
        name="submit_skill_candidate",
        description="Submit one complete reusable Skill candidate.",
        input_schema=input_schema,
    )


def evaluation_submission_tool() -> ToolDefinition:
    return ToolDefinition(
        name="submit_skill_evaluation",
        description="Submit one independent Skill evaluation verdict.",
        input_schema={
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["passed", "score", "summary", "findings"],
            "additionalProperties": False,
        },
    )
