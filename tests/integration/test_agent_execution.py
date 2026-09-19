from __future__ import annotations

# Tests observe completion and provider requests without running real models.
# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers import ModelRequest, ProviderModel, StreamEvent, StreamEventKind, ToolCallDelta
from hames.providers.fake import FakeProvider


class StageProvider(FakeProvider):
    def __init__(self, stages: list[str] | None = None) -> None:
        super().__init__([])
        self.stages = stages

    async def list_models(self) -> list[ProviderModel]:
        return [
            ProviderModel(
                id="stage-model",
                provider="stage",
                reasoning_supported=True,
                reasoning_efforts=["medium", "xhigh"],
                context_length=65536,
            )
        ]

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        count = sum(message.role == "tool" for message in request.messages)
        if self.stages is not None and count < len(self.stages):
            yield StreamEvent(
                kind=StreamEventKind.TOOL_CALL_DELTA,
                tool_call=ToolCallDelta(
                    index=0,
                    provider_call_id=f"stage-{count}",
                    name="spawn_agent",
                    arguments_delta=json.dumps(
                        {
                            "agent_id": self.stages[count],
                            "task": "Inspect approved fixture plan",
                        }
                    ),
                ),
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
        elif self.stages is not None and count == len(self.stages):
            # This fixture's plan has no checklist, so the report can terminate directly.
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="PASS: checked fixture")
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
        else:
            yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="PASS: checked fixture")
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", [False, True])
@pytest.mark.parametrize("unfinished", [False, True])
async def test_plan_workflow_routes_models_efforts_and_reviewer_authority(
    tmp_path: Path,
    finish: bool,
    unfinished: bool,
) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        "[memory]\nenabled=false\n[skills]\nenabled=false\n[evolution]\nenabled=false\n"
    )
    stages = ["builder", "reviewer"] + (["finisher", "reviewer"] if finish else [])
    coordinator = StageProvider(stages)
    worker = StageProvider()
    state = GatewayState.create(paths, providers={"parent": coordinator, "worker": worker})
    try:
        for agent_id, effort in [
            ("builder", "xhigh"),
            ("reviewer", "xhigh"),
            ("finisher", "medium"),
            ("workflow", "xhigh"),
        ]:
            profile = "parent" if agent_id == "workflow" else "worker"
            authority = "read_only" if agent_id == "reviewer" else "standard"
            delegation = (
                "  allow: true\n  allowed_agents: [builder, reviewer, finisher]\n"
                if agent_id == "workflow"
                else "  allow: false\n"
            )
            state.agents.create(
                source=(
                    f"---\nid: {agent_id}\nname: {agent_id}\nauthority: {authority}\n"
                    f"execution:\n  provider: {profile}\n  model: stage-model\n"
                    f"  reasoning_effort: {effort}\ndelegation:\n{delegation}---\nInspect.\n"
                )
            )
        session = state.ledger.create_session(
            working_directory=tmp_path,
            agent_id="default",
            provider="parent",
            model="planner-model",
            reasoning_effort="medium",
            interaction_mode="plan",
            context_window_tokens=131072,
            context_window_source="provider",
        )
        state.controls.grant_trust(tmp_path)
        state.runs.plans.propose(
            session,
            run_id="planning",
            markdown=(
                "# Approved fixture\n\n- [ ] Needs human decision"
                if unfinished
                else "# Approved fixture\n\nInspect the source without changes."
            ),
            causation_id=None,
        )
        headers = {"Authorization": f"Bearer {state.token}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/v1/sessions/{session.id}/plans/current/execute",
                headers=headers,
                json={"strategy": "keep", "agent_id": "workflow"},
            )
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        task = state.runs._tasks.get(run_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 10)
        events = state.ledger.list_events(session.id)
        completed = [e for e in events if e.type == "delegation.completed"]
        requested = [e for e in events if e.type == "delegation.requested"]
        assert [e.payload["target_agent_id"] for e in requested] == stages
        assert all(
            e.payload["provider"] == "worker" and e.payload["model"] == "stage-model"
            for e in requested
        )
        assert [e.payload["reasoning_effort"] for e in requested] == [
            "medium" if stage == "finisher" else "xhigh" for stage in stages
        ]
        assert len(completed) == len(stages)
        children = [state.ledger.get_session(str(e.payload["child_session_id"])) for e in completed]
        assert [child.agent_id for child in children] == stages
        assert all(
            child.provider == "worker" and child.model == "stage-model" for child in children
        )
        assert all(child.context_window_tokens == 65536 for child in children)
        assert all(child.interaction_mode == "auto" for child in children)
        assert [child.reasoning_effort for child in children] == [
            "medium" if stage == "finisher" else "xhigh" for stage in stages
        ]
        for stage, request in zip(stages, worker.requests, strict=True):
            if stage == "reviewer":
                assert not {"write_file", "edit_file", "shell", "spawn_agent"} & {
                    tool.name for tool in request.tools
                }
        assert coordinator.requests[0].reasoning_effort == "xhigh"
        assert "Approved fixture" in coordinator.requests[0].system
        if unfinished:
            assert any(
                e.type == "run.failed" and e.payload.get("code") == "workflow_needs_attention"
                for e in events
            )
            assert not any(e.type == "run.continuation.requested" for e in events)
            assert any(e.type == "plan.execution.failed" for e in events)
        else:
            assert any(e.type == "plan.execution.completed" for e in events)
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_invalid_worker_selection_leaves_plan_and_session_unchanged(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    provider = StageProvider()
    state = GatewayState.create(paths, providers={"worker": provider})
    try:
        state.agents.create(
            source=(
                "---\nid: workflow\nname: Workflow\ndelegation:\n"
                "  allowed_agents: [bad-worker]\n---\nCoordinate.\n"
            )
        )
        state.agents.create(
            source=(
                "---\nid: bad-worker\nname: Bad\nexecution:\n  provider: worker\n"
                "  model: stage-model\n  reasoning_effort: unsupported\n---\nInspect.\n"
            )
        )
        session = state.ledger.create_session(
            working_directory=tmp_path,
            agent_id="default",
            provider="worker",
            model="stage-model",
            interaction_mode="plan",
        )
        state.controls.grant_trust(tmp_path)
        state.runs.plans.propose(
            session,
            run_id="planning",
            markdown="# Plan",
            causation_id=None,
        )
        with pytest.raises(ValueError, match="unsupported agent effort"):
            await state.runs.execute_plan(session.id, strategy="keep", agent_id="workflow")
        assert state.ledger.get_session(session.id) == session
        plan = (await state.runs.current_plan(session.id)).current
        assert plan is not None and plan.status == "ready"
        assert provider.requests == []
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_agent_default_model_can_be_set_overridden_and_cleared(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        '[runtime]\ndefault_provider="parent"\ndefault_model="stage-model"\n'
        '[providers.parent]\nadapter="codex"\nbase_url="app-server://codex"\nmodel="stage-model"\n'
        "[memory]\nenabled=false\n[skills]\nenabled=false\n[evolution]\nenabled=false\n"
    )
    state = GatewayState.create(
        paths, providers={"parent": StageProvider(), "worker": StageProvider()}
    )
    try:
        state.agents.create(name="Custom")
        headers = {"Authorization": f"Bearer {state.token}"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(state)),
            base_url="http://test",
            headers=headers,
        ) as client:
            selection = {"provider": "worker", "model": "stage-model", "reasoning_effort": "xhigh"}
            saved = await client.patch("/v1/agents/custom", json={"default_model": selection})
            assert saved.status_code == 200, saved.text
            assert saved.json()["default_model"] == selection
            created = await client.post(
                "/v1/sessions", json={"working_directory": str(tmp_path), "agent_id": "custom"}
            )
            assert created.status_code == 201, created.text
            session = created.json()
            assert session["provider"] == "worker"
            assert session["reasoning_effort"] == "xhigh"
            explicit = await client.post(
                "/v1/sessions",
                json={
                    "working_directory": str(tmp_path),
                    "agent_id": "custom",
                    "provider": "parent",
                    "model": "stage-model",
                    "reasoning_effort": "medium",
                },
            )
            assert explicit.status_code == 201, explicit.text
            assert explicit.json()["provider"] == "parent"
            inherited = await client.post(
                "/v1/sessions",
                json={
                    "working_directory": str(tmp_path),
                    "inherit_session_id": explicit.json()["id"],
                },
            )
            assert inherited.status_code == 201, inherited.text
            assert inherited.json()["provider"] == "parent"
            draft = await client.post(
                "/v1/sessions", json={"working_directory": str(tmp_path), "agent_id": "default"}
            )
            switched = await client.put(
                f"/v1/sessions/{draft.json()['id']}/agent", json={"agent_id": "custom"}
            )
            assert switched.status_code == 200, switched.text
            assert switched.json()["provider"] == "worker"
            await client.patch(
                "/v1/agents/custom",
                json={"default_model": {**selection, "reasoning_effort": "medium"}},
            )
            assert state.ledger.get_session(session["id"]).reasoning_effort == "xhigh"
            # The existing chat picker remains an override, not a change to the agent.
            changed = await client.patch(
                f"/v1/sessions/{session['id']}",
                json={"provider": "parent", "model": "stage-model", "reasoning_effort": "medium"},
            )
            assert changed.status_code == 200, changed.text
            assert state.agents.load("custom").metadata.execution is not None
            cleared = await client.patch("/v1/agents/custom", json={"default_model": None})
            assert cleared.status_code == 200, cleared.text
            assert cleared.json()["default_model"] is None
            assert state.ledger.get_session(session["id"]).provider == "parent"
            assert "default_model:" not in state.agents.load("custom").path.read_text()
            following = await client.post(
                "/v1/sessions", json={"working_directory": str(tmp_path), "agent_id": "custom"}
            )
            assert following.status_code == 201, following.text
            assert following.json()["provider"] == "parent"
            bad = await client.patch(
                "/v1/agents/custom",
                json={"default_model": {**selection, "reasoning_effort": "impossible"}},
            )
            assert bad.status_code == 400
            assert state.agents.load("custom").metadata.execution is None
    finally:
        await state.runs.close()
