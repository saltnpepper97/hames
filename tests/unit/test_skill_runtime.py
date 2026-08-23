from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.ledger import Ledger, Session
from hames.paths import HamesPaths
from hames.providers import ModelRequest, ProviderModel, StreamEvent, StreamEventKind, ToolCallDelta
from hames.providers.base import JsonValue
from hames.skill_runtime import SkillManager, draft_submission_tool, evaluation_submission_tool
from hames.skills import SkillDraft, SkillRegistry


class SkillProvider:
    profile_id = "fake"
    adapter = "fake"
    base_url = ""

    def __init__(self, *, pass_evaluation: bool = True) -> None:
        self.pass_evaluation = pass_evaluation
        self.requests: list[ModelRequest] = []

    async def list_models(self) -> list[ProviderModel]:
        return [ProviderModel(id="fixture", provider="fake")]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        purpose = request.metadata["purpose"]
        if purpose == "skill_authoring":
            name = "submit_skill_candidate"
            arguments: dict[str, JsonValue] = {
                "id": "inspect-and-verify",
                "name": "Inspect and Verify",
                "description": (
                    "Inspect project files and verify findings with a directory listing."
                ),
                "scope": "workspace",
                "tools": ["read_file", "list_dir"],
                "triggers": ["inspect project", "verify files"],
                "requires": [],
                "instructions": (
                    "Read the requested file, list the related directory, and verify the answer."
                ),
                "scripts": [],
                "files": {},
                "rationale": "This workflow succeeded repeatedly.",
            }
        else:
            name = "submit_skill_evaluation"
            arguments = {
                "passed": self.pass_evaluation,
                "score": 0.95 if self.pass_evaluation else 0.2,
                "summary": "Grounded and bounded." if self.pass_evaluation else "Not grounded.",
                "findings": [],
            }
        yield StreamEvent(kind=StreamEventKind.STARTED, provider_request_id=f"{purpose}-1")
        yield StreamEvent(
            kind=StreamEventKind.TOOL_CALL_DELTA,
            tool_call=ToolCallDelta(
                index=0,
                provider_call_id=f"{purpose}-call",
                name=name,
                arguments_delta=json.dumps(arguments),
            ),
        )
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")

    async def aclose(self) -> None:
        return None


def _registry(paths: HamesPaths, ledger: Ledger) -> SkillRegistry:
    return SkillRegistry(
        paths.skills,
        ledger,
        available_tools={"read_file", "list_dir", "write_file", "edit_file", "shell"},
    )


def _completed_run(ledger: Ledger, session: Session, run_id: str, task: str) -> None:
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": task},
    )
    cause = ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 2, "max_tool_calls": 4, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    for index, name in enumerate(("read_file", "list_dir"), 1):
        cause = ledger.append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="tool.completed",
            payload={
                "tool_call_id": f"call-{index}",
                "name": name,
                "status": "completed",
                "summary": f"completed {name}",
                "content": "",
            },
            causation_id=cause.id,
        )
    ledger.append(
        session_id=session.id,
        run_id=run_id,
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 2, "tool_calls": 2, "active_seconds": 0.1},
        causation_id=cause.id,
    )


async def _wait_for_job(registry: SkillRegistry, job_id: str) -> str:
    for _ in range(200):
        status = registry.get_job(job_id).status
        if status in {"completed", "failed", "budget_wait"}:
            return status
        await asyncio.sleep(0.01)
    return registry.get_job(job_id).status


def test_skill_maintenance_schemas_are_llama_cpp_compatible() -> None:
    encoded = json.dumps(
        [draft_submission_tool().input_schema, evaluation_submission_tool().input_schema]
    )
    assert "$defs" not in encoded
    assert "$ref" not in encoded


@pytest.mark.asyncio
async def test_repeated_successful_workflow_autonomously_activates_skill(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    provider = SkillProvider()
    registry = _registry(hames_paths, ledger)
    manager = SkillManager(
        ledger=ledger,
        config=HamesConfig(),
        providers={"fake": provider},
        broker=EventBroker(),
        registry=registry,
    )
    try:
        _completed_run(ledger, session, "run-1", "Inspect the project and verify its files")
        assert await manager.observe_run(session.id, "run-1") == []
        _completed_run(ledger, session, "run-2", "Inspect this project and verify the files")
        jobs = await manager.observe_run(session.id, "run-2")
        assert len(jobs) == 1
        assert await _wait_for_job(registry, jobs[0].id) == "completed"
        active = registry.get_visible(session, "inspect-and-verify")
        assert active.status == "active"
        assert [request.metadata["purpose"] for request in provider.requests] == [
            "skill_authoring",
            "skill_evaluation",
        ]
        types = [event.type for event in ledger.list_events(session.id)]
        assert "skill.workflow.observed" in types
        assert "skill.activated" in types
        assert types.count("skill.job.started") == 1
        handle: Any = manager
        assert handle._worker is not None and not handle._worker.done()
        handle._queue.put_nowait(jobs[0].id)
        await asyncio.sleep(0.4)
        assert handle._worker is not None and not handle._worker.done()
        later = [event.type for event in ledger.list_events(session.id)]
        assert later.count("skill.job.started") == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_independent_evaluator_rejects_candidate(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    source = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Create a reusable inspection workflow."},
    )
    provider = SkillProvider(pass_evaluation=False)
    registry = _registry(hames_paths, ledger)
    manager = SkillManager(
        ledger=ledger,
        config=HamesConfig(),
        providers={"fake": provider},
        broker=EventBroker(),
        registry=registry,
    )
    try:
        job = await manager.author(
            session,
            goal="Create a reusable inspection workflow.",
            source_event_id=source.id,
        )
        assert await _wait_for_job(registry, job.id) == "completed"
        assert registry.visible(session) == []
        with registry.database.connect() as connection:
            status = connection.execute("SELECT status FROM skill_versions").fetchone()[0]
        assert status == "rejected"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_successful_correction_of_loaded_skill_queues_and_activates_patch(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    evidence = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Inspect and verify project files."},
    )
    registry = _registry(hames_paths, ledger)
    first = registry.create_draft(
        session=session,
        draft=SkillDraft(
            id="inspect-and-verify",
            name="Inspect and Verify",
            description="Inspect project files and verify findings with a directory listing.",
            scope="workspace",
            tools=["read_file", "list_dir"],
            triggers=["inspect project"],
            instructions="Read a file and list its directory.",
        ),
        evidence_event_ids=[evidence.id],
        created_by="automatic",
        run_id=None,
        causation_id=evidence.id,
    )
    active = registry.activate(
        session=session, version_id=first.version.id, causation_id=first.events[-1].id
    ).version
    user = ledger.append(
        session_id=session.id,
        agent_id=session.agent_id,
        event_type="user.message",
        payload={"content": "Actually, next time verify the answer after listing files."},
    )
    cause = ledger.append(
        session_id=session.id,
        run_id="correction-run",
        agent_id=session.agent_id,
        event_type="run.started",
        payload={"max_model_turns": 3, "max_tool_calls": 4, "max_active_seconds": 30.0},
        causation_id=user.id,
    )
    cause = ledger.append(
        session_id=session.id,
        run_id="correction-run",
        agent_id=session.agent_id,
        event_type="skill.loaded",
        payload={
            "skill_id": active.skill_id,
            "version_id": active.id,
            "slug": active.slug,
            "version": active.version,
            "content_hash": active.content_hash,
            "reason": "model_selected",
            "score": 1.0,
        },
        causation_id=cause.id,
    )
    for index, name in enumerate(("read_file", "list_dir"), 1):
        cause = ledger.append(
            session_id=session.id,
            run_id="correction-run",
            agent_id=session.agent_id,
            event_type="tool.completed",
            payload={
                "tool_call_id": f"correction-{index}",
                "name": name,
                "status": "completed",
                "summary": f"completed {name}",
                "content": "",
            },
            causation_id=cause.id,
        )
    ledger.append(
        session_id=session.id,
        run_id="correction-run",
        agent_id=session.agent_id,
        event_type="run.completed",
        payload={"model_turns": 3, "tool_calls": 2, "active_seconds": 0.2},
        causation_id=cause.id,
    )
    manager = SkillManager(
        ledger=ledger,
        config=HamesConfig(),
        providers={"fake": SkillProvider()},
        broker=EventBroker(),
        registry=registry,
    )
    try:
        jobs = await manager.observe_run(session.id, "correction-run")
        assert len(jobs) == 1
        assert jobs[0].kind == "patch"
        assert jobs[0].target_skill_id == active.skill_id
        assert await _wait_for_job(registry, jobs[0].id) == "completed"
        replacement = registry.get_visible(session, active.slug)
        assert replacement.version == 2
        assert registry.get(active.id).status == "superseded"
    finally:
        await manager.close()
