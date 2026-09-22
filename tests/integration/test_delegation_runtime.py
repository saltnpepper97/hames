from __future__ import annotations

# Lifecycle assertions intentionally inspect runtime-owned task registries.
# pyright: reportPrivateUsage=false
import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from hames.agent import AgentRegistry
from hames.config import ContextConfig
from hames.context import compile_context
from hames.gateway import GatewayState, create_app
from hames.paths import HamesPaths
from hames.providers import ModelRequest, StreamEvent, StreamEventKind, ToolCallDelta
from hames.providers.fake import FakeProvider
from hames.tools import AgentControlArguments
from hames.workflows import project_workflow


class DelegationProvider(FakeProvider):
    def __init__(self, *, children: int = 2, child_write: bool = False) -> None:
        super().__init__([])
        self.children = children
        self.child_write = child_write
        self.entered = 0
        self.children_entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        first_user = next(message.content for message in request.messages if message.role == "user")
        tool_results = [message for message in request.messages if message.role == "tool"]
        if first_user == "Review these files" and not tool_results:
            for index in range(self.children):
                yield StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=index,
                        provider_call_id=f"child-{index}",
                        name="spawn_agent",
                        arguments_delta=json.dumps({"task": f"Inspect child {index}"}),
                    ),
                )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
            return
        if first_user != "Review these files" and not tool_results:
            self.entered += 1
            if self.entered >= self.children:
                self.children_entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled += 1
                raise
            if self.child_write:
                yield StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=0,
                        provider_call_id="forbidden-write",
                        name="write_file",
                        arguments_delta=json.dumps({"path": "forbidden.txt", "content": "bad"}),
                    ),
                )
                yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
                return
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Inspection complete.")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")


async def start_parent(
    tmp_path: Path,
    provider: DelegationProvider,
    *,
    limits: str = "",
    restricted: bool = False,
    renamed: bool = False,
    opaque_worker: bool = False,
) -> tuple[GatewayState, str, str]:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    paths.config_file.write_text(
        f"[runtime]\n{limits}\n[memory]\nenabled=false\n[skills]\nenabled=false\n"
        "[evolution]\nenabled=false\n",
        encoding="utf-8",
    )
    state = GatewayState.create(paths, providers={"fake": provider})
    if restricted:
        (paths.agents / "default" / "AGENT.md").write_text(
            "---\nid: default\nname: Default\ntools:\n  deny: [write_file]\n"
            "delegation:\n  allowed_agents: [worker]\n---\nInspect files.\n",
            encoding="utf-8",
        )
        if opaque_worker:
            state.agents.create("Worker")
        else:
            state.agents.create(source="---\nid: worker\nname: Worker\n---\nInspect.\n")
        if renamed:
            state.agents.update("worker", name="Builder")
        # Explicitly select a broader child; the parent's deny must still apply.
        original = provider.stream

        async def with_target(request: ModelRequest) -> AsyncIterator[StreamEvent]:
            async for event in original(request):
                if (
                    event.tool_call is not None
                    and event.tool_call.name == "spawn_agent"
                    and (event.tool_call.provider_call_id or "").startswith("child-")
                ):
                    arguments = json.loads(event.tool_call.arguments_delta)
                    if renamed:
                        # Rename while the parent is streaming with the old target in context.
                        state.agents.update("worker", name="Edward")
                    arguments["agent_id"] = "builder" if renamed else "worker"
                    event = event.model_copy(
                        update={
                            "tool_call": event.tool_call.model_copy(
                                update={"arguments_delta": json.dumps(arguments)}
                            )
                        }
                    )
                yield event

        provider.stream = with_target  # type: ignore[method-assign]
    headers = {"Authorization": f"Bearer {state.token}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/sessions",
            headers=headers,
            json={
                "working_directory": str(tmp_path),
                "provider": "fake",
                "model": "fixture",
            },
        )
        assert response.status_code == 201
        session_id = response.json()["id"]
        assert (await client.put(f"/v1/sessions/{session_id}/trust", headers=headers)).is_success
    run_id = await state.runs.start(session_id, "Review these files")
    return state, session_id, run_id


@pytest.mark.asyncio
async def test_default_subagents_run_in_parallel_without_user_delegation_request(
    tmp_path: Path,
) -> None:
    provider = DelegationProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        task = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 3)
        assert provider.entered == 2
        assert not task.done()
        assert "spawn_agent" in {tool.name for tool in provider.requests[0].tools}
        provider.release.set()
        await asyncio.wait_for(asyncio.shield(task), 5)
        events = state.ledger.list_events(session_id)
        children = [event.payload for event in events if event.type == "delegation.completed"]
        assert len(children) == 2
        assert all(
            state.ledger.get_session(item["child_session_id"]).agent_id == "default"
            for item in children
        )
        assert (
            len(
                [
                    message
                    for message in provider.requests[-1].messages
                    if message.role == "tool" and message.tool_name == "spawn_agent"
                ]
            )
            == 2
        )
        assert state.runs._active_child_count == 0
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_child_cannot_use_tools_denied_to_parent(tmp_path: Path) -> None:
    provider = DelegationProvider(children=1, child_write=True)
    provider.release.set()
    state, session_id, run_id = await start_parent(tmp_path, provider, restricted=True)
    try:
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        event = next(
            event
            for event in state.ledger.list_events(session_id)
            if event.type == "delegation.completed"
        )
        child_events = state.ledger.list_events(event.payload["child_session_id"])
        rejected = [item for item in child_events if item.type == "tool.rejected"]
        assert any(item.payload["name"] == "write_file" for item in rejected)
        assert not (tmp_path / "forbidden.txt").exists()
        assert "write_file" not in {tool.name for tool in provider.requests[1].tools}
    finally:
        await state.runs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limits",
    [
        "max_concurrent_child_runs=1",
        "max_child_runs_per_parent_run=1",
    ],
)
async def test_child_limits_reject_excess_parallel_admissions(tmp_path: Path, limits: str) -> None:
    provider = DelegationProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider, limits=limits)
    try:
        task = state.runs._tasks[run_id]
        async with asyncio.timeout(3):
            for _ in range(400):
                if any(
                    event.type == "tool.rejected" for event in state.ledger.list_events(session_id)
                ):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("excess child admission did not reject")
        assert provider.entered <= 1
        provider.release.set()
        await asyncio.wait_for(asyncio.shield(task), 5)
        assert (
            len(
                [
                    event
                    for event in state.ledger.list_events(session_id)
                    if event.type == "delegation.completed"
                ]
            )
            == 1
        )
        assert state.runs._active_child_count == 0
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_parent_cancellation_preserves_parallel_children(tmp_path: Path) -> None:
    provider = DelegationProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        task = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 3)
        children = tuple(state.runs._children_by_parent[run_id])
        assert await state.runs.cancel(run_id)
        await asyncio.wait_for(asyncio.shield(task), 5)
        assert provider.cancelled == 0
        assert state.runs._active_child_count == 2
        for child_run in children:
            assert not any(
                event.type == "run.cancelled" for event in state.ledger.list_run_events(child_run)
            )
        cancelled = next(
            event for event in state.ledger.list_run_events(run_id) if event.type == "run.cancelled"
        )
        assert cancelled.payload["children_preserved"] is True
        provider.release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(state.runs._delegation_tasks)), 5)
        assert state.runs._active_child_count == 0
        assert (
            len(
                [
                    e
                    for e in state.ledger.list_events(session_id)
                    if e.type == "delegation.completed"
                ]
            )
            == 2
        )
    finally:
        await state.runs.close()


class UnsafeChildProvider(DelegationProvider):
    def __init__(self, tool: str, arguments: dict[str, object]) -> None:
        super().__init__(children=1, child_write=True)
        self.child_tool = tool
        self.child_arguments = arguments
        self.release.set()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        async for event in super().stream(request):
            call = event.tool_call
            if call is not None:
                if call.name == "spawn_agent":
                    # This is authored by the parent model, not the real user.
                    arguments = {"task": "Delete outdated memories from memory"}
                    call = call.model_copy(update={"arguments_delta": json.dumps(arguments)})
                elif call.name == "write_file":
                    call = call.model_copy(
                        update={
                            "name": self.child_tool,
                            "arguments_delta": json.dumps(self.child_arguments),
                        }
                    )
                event = event.model_copy(update={"tool_call": call})
            yield event


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments", "reason"),
    [
        ("shell", {"command": "sleep 60", "background": True}, "background"),
        ("memory_forget", {"memory_id": "existing-memory"}, "approval"),
    ],
)
async def test_child_cannot_escape_background_or_explicit_user_permission(
    tmp_path: Path, tool: str, arguments: dict[str, object], reason: str
) -> None:
    provider = UnsafeChildProvider(tool, arguments)
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        completed = next(
            event
            for event in state.ledger.list_events(session_id)
            if event.type == "delegation.completed"
        )
        child_id = completed.payload["child_session_id"]
        events = state.ledger.list_events(child_id)
        rejected = next(event for event in events if event.type == "tool.rejected")
        assert rejected.payload["name"] == tool
        assert reason in rejected.payload["summary"].lower()
        assert not any(event.type in {"approval.requested", "terminal.started"} for event in events)
        assert not state.runs.background_terminals(child_id)
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_nested_child_cannot_escape_ancestor_target_allowlist(tmp_path: Path) -> None:
    provider = UnsafeChildProvider("spawn_agent", {"agent_id": "outsider", "task": "Inspect more"})
    state, session_id, run_id = await start_parent(
        tmp_path,
        provider,
        limits="max_delegation_depth=2",
        restricted=True,
    )
    # The immediate child permits outsider, but the ancestor only permits worker.
    (state.paths.agents / "worker" / "AGENT.md").write_text(
        "---\nid: worker\nname: Worker\ndelegation:\n  allowed_agents: [outsider]\n"
        "---\nInspect carefully.\n",
        encoding="utf-8",
    )
    state.agents.create("Outsider")
    try:
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        completed = next(
            event
            for event in state.ledger.list_events(session_id)
            if event.type == "delegation.completed"
        )
        events = state.ledger.list_events(completed.payload["child_session_id"])
        assert any(
            event.type == "tool.rejected" and event.payload["name"] == "spawn_agent"
            for event in events
        )
        assert not any(event.type == "delegation.requested" for event in events)
        assert "spawn_agent" not in {tool.name for tool in provider.requests[1].tools}
    finally:
        await state.runs.close()


def test_explicit_delegation_opt_out_survives_capsule_roundtrip(tmp_path: Path) -> None:
    paths = HamesPaths.resolve(root=tmp_path / "home")
    paths.ensure_foundation()
    registry = AgentRegistry(paths.agents)
    capsule = registry.create(
        source=("---\nid: solo\nname: Solo\ndelegation:\n  allow: false\n---\nWork locally.\n")
    )
    assert not capsule.metadata.delegation.allow
    assert not registry.load("solo").metadata.delegation.allow
    updated = registry.update("solo", name="Solo renamed")
    assert not updated.metadata.delegation.allow
    assert not registry.load("solo").metadata.delegation.allow


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_failed", [False, True])
async def test_delegation_carries_exact_approved_plan_and_inherits_it(
    tmp_path: Path, retry_failed: bool
) -> None:
    class GatedProvider(DelegationProvider):
        def __init__(self) -> None:
            super().__init__(children=1)
            self.parent_ready = asyncio.Event()

        async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
            await self.parent_ready.wait()
            async for event in super().stream(request):
                yield event

    provider = GatedProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        parent = state.ledger.get_session(session_id)
        markdown = (
            "# Exact implementation plan\n\n"
            + "\n".join(
                f"## File {index}\nEdit src/part_{index}.rs exactly; preserve invariant {index}."
                for index in range(400)
            )
            + "\n\nFinal acceptance: preserve every output binding."
        )
        proposed, _ = state.runs.plans.propose(
            parent, run_id="planner", markdown=markdown, causation_id=None
        )
        assert proposed.current is not None
        state.runs.plans.transition(
            parent,
            proposed.current.id,
            "plan.approved",
            execution_run_id=run_id,
            execution_note="Do not restart the compositor.",
        )
        state.runs.plans.transition(
            parent, proposed.current.id, "plan.execution.started", execution_run_id=run_id
        )
        if retry_failed:
            state.runs.plans.transition(
                parent,
                proposed.current.id,
                "plan.execution.failed",
                execution_run_id="old-run",
                message="Earlier worker could not start",
            )
        provider.parent_ready.set()
        await asyncio.wait_for(provider.children_entered.wait(), 5)
        child_request = provider.requests[-1]
        assert markdown in child_request.system
        assert "Do not restart the compositor." in child_request.system
        child = next(
            item for item in state.ledger.list_sessions() if item.parent_session_id == session_id
        )
        card = next(
            event
            for event in state.ledger.list_events(child.id)
            if event.type == "delegation.task_card"
        )
        assert len(markdown) > 21_655
        assert card.payload["task"] == "Inspect child 0"
        assert card.payload["approved_plan"]["markdown"] == markdown
        assert state.runs._delegation_plan(child, "nested-run") == card.payload["approved_plan"]
        state.ledger.append(
            session_id=child.id,
            event_type="context.compaction.completed",
            payload={
                "compaction_id": "checkpoint",
                "trigger": "automatic",
                "summary": "Short summary deliberately omitting plan details.",
                "cutoff_event_id": card.id,
                "cutoff_sequence": card.sequence,
                "source_event_ids": [card.id],
                "provider": "fake",
                "model": "fixture",
                "reasoning_effort": "",
                "turns_compacted": 1,
                "before_tokens": 1000,
                "after_tokens": 10,
                "passes": 1,
                "partial": False,
            },
        )
        compiled = compile_context(
            child,
            state.ledger.replay(child.id),
            state.agents.load(child.agent_id),
            [],
            "safe reads",
            ContextConfig(),
            run_id="after-compaction",
        )
        assert markdown in compiled.system
        assert "Do not restart the compositor." in compiled.system

        # A later unrelated parent run must not inherit an old approval.
        if not retry_failed:
            assert state.runs._delegation_plan(parent, "unrelated-run") is None
        provider.release.set()
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
    finally:
        provider.parent_ready.set()
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_child_cancel_notifies_parent_before_post_run_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = DelegationProvider(children=1)
    state, session_id, run_id = await start_parent(tmp_path, provider)
    cleanup_entered = asyncio.Event()
    release_cleanup = asyncio.Event()
    original_finalize = state.runs._finalize_plan_execution

    async def delayed_finalize(child_session_id: str, child_run_id: str) -> None:
        if child_session_id != session_id:
            cleanup_entered.set()
            await release_cleanup.wait()
        await original_finalize(child_session_id, child_run_id)

    monkeypatch.setattr(state.runs, "_finalize_plan_execution", delayed_finalize)
    try:
        parent_task = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 5)
        child_run = next(iter(state.runs._children_by_parent[run_id]))
        assert await state.runs.cancel(child_run)
        await asyncio.wait_for(cleanup_entered.wait(), 5)
        # A second Stop must not cancel the cleanup that persists terminal state.
        assert await state.runs.cancel(child_run)
        await asyncio.wait_for(asyncio.shield(parent_task), 5)
        assert not release_cleanup.is_set()
        events = state.ledger.list_run_events(run_id)
        stopping = next(event for event in events if event.type == "delegation.stopping")
        cancelled = next(event for event in events if event.type == "delegation.failed")
        assert stopping.sequence < cancelled.sequence
        assert cancelled.payload["status"] == "cancelled"
        assert "do not restart" in cancelled.payload["summary"]
        assert any(event.type == "run.completed" for event in events)
        assert not any(event.type == "run.cancelled" for event in events)
        assert provider.cancelled == 1
        assert any(
            "Child cancelled by the user" in message.content
            for message in provider.requests[-1].messages
            if message.role == "tool"
        )
    finally:
        release_cleanup.set()
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_inline_coordinator_does_not_timeout_while_worker_has_budget(tmp_path: Path) -> None:
    class InlineProvider(DelegationProvider):
        async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
            first_user = next(m.content for m in request.messages if m.role == "user")
            if first_user == "Review these files":
                yield StreamEvent(kind=StreamEventKind.STARTED)
                await asyncio.sleep(0.2)
                assert request.tool_handler is not None
                result = await request.tool_handler(
                    "spawn_agent", {"task": "Inspect child 0"}, "inline"
                )
                assert result.status == "completed"
                yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Done.")
                yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
            else:
                async for event in super().stream(request):
                    yield event

    provider = InlineProvider(children=1)
    state, _session_id, run_id = await start_parent(
        tmp_path, provider, limits="max_active_seconds_per_run = 0.35"
    )
    try:
        parent = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 3)
        await asyncio.sleep(0.2)
        provider.release.set()
        await asyncio.wait_for(asyncio.shield(parent), 3)
        events = state.ledger.list_run_events(run_id)
        assert any(event.type == "run.completed" for event in events)
        assert not any(event.type == "run.failed" for event in events)
    finally:
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("opaque_worker", [False, True])
async def test_renamed_worker_slug_runs_with_stable_id(tmp_path: Path, opaque_worker: bool) -> None:
    provider = DelegationProvider(children=1)
    state, session_id, run_id = await start_parent(
        tmp_path, provider, restricted=True, renamed=True, opaque_worker=opaque_worker
    )
    try:
        await asyncio.wait_for(provider.children_entered.wait(), 3)
        provider.release.set()
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        events = state.ledger.list_events(session_id)
        requested = next(e for e in events if e.type == "delegation.requested")
        assert requested.payload["target_agent_id"] == state.agents.load("worker").metadata.id
        assert any(e.type == "delegation.completed" for e in events)
        assert (
            state.agents.load("worker").path.parent.name == state.agents.load("worker").metadata.id
        )
        parent_requests = [
            request
            for request in provider.requests
            if next(message.content for message in request.messages if message.role == "user")
            == "Review these files"
        ]
        assert 'permitted targets: Builder (agent_id="Builder").' in parent_requests[0].system
        assert "permitted targets: worker." not in parent_requests[0].system
        tool_result = next(
            message
            for message in parent_requests[-1].messages
            if message.role == "tool" and message.tool_name == "spawn_agent"
        )
        assert json.loads(tool_result.content)["structured_data"]["agent_name"] == "Edward"
        assert "agent_id" not in json.loads(tool_result.content)["structured_data"]
    finally:
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_missing_approved_plan_rejects_child_before_execution(tmp_path: Path) -> None:
    class MissingPlanProvider(DelegationProvider):
        async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
            async for event in super().stream(request):
                if event.tool_call and event.tool_call.name == "spawn_agent":
                    event = event.model_copy(
                        update={
                            "tool_call": event.tool_call.model_copy(
                                update={
                                    "arguments_delta": json.dumps(
                                        {"task": "Execute the approved plan"}
                                    )
                                }
                            )
                        }
                    )
                yield event

    provider = MissingPlanProvider(children=1)
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        assert provider.entered == 0
        events = state.ledger.list_events(session_id)
        assert any(
            e.type == "tool.rejected"
            and "Approved plan is missing" in str(e.payload.get("summary", ""))
            for e in events
        )
        assert not any(e.type == "delegation.requested" for e in events)
    finally:
        await state.runs.close()


class SequentialStageProvider(DelegationProvider):
    def __init__(self, *, fail_child_with_tools: bool = False) -> None:
        super().__init__(children=1)
        self.fail_child_with_tools = fail_child_with_tools
        self.release.set()

    async def stream(self, request: ModelRequest) -> AsyncIterator[StreamEvent]:
        self.requests.append(request)
        yield StreamEvent(kind=StreamEventKind.STARTED)
        first_user = next(message.content for message in request.messages if message.role == "user")
        results = [
            message
            for message in request.messages
            if message.role == "tool" and message.tool_name == "spawn_agent"
        ]
        if first_user == "Review these files":
            if not results:
                arguments = {"task": "Build", "stage_id": "builder"}
            elif len(results) == 1 and not self.fail_child_with_tools:
                arguments = {
                    "task": "Review",
                    "stage_id": "review",
                    "depends_on": ["builder"],
                }
            else:
                yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text="Workflow complete.")
                yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")
                return
            yield StreamEvent(
                kind=StreamEventKind.TOOL_CALL_DELTA,
                tool_call=ToolCallDelta(
                    index=0,
                    provider_call_id=f"stage-{len(results)}",
                    name="spawn_agent",
                    arguments_delta=json.dumps(arguments),
                ),
            )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
            return
        if self.fail_child_with_tools:
            for index in range(2):
                yield StreamEvent(
                    kind=StreamEventKind.TOOL_CALL_DELTA,
                    tool_call=ToolCallDelta(
                        index=index,
                        provider_call_id=f"read-{index}",
                        name="read_file",
                        arguments_delta=json.dumps({"path": "README.md"}),
                    ),
                )
            yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="tool_calls")
            return
        yield StreamEvent(kind=StreamEventKind.TEXT_DELTA, text=f"Completed {first_user}.")
        yield StreamEvent(kind=StreamEventKind.COMPLETED, finish_reason="stop")


@pytest.mark.asyncio
async def test_staged_delegation_records_dependencies_and_attaches_evidence(
    tmp_path: Path,
) -> None:
    provider = SequentialStageProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        events = state.ledger.list_events(session_id)
        requested = [event for event in events if event.type == "delegation.requested"]
        completed = [event for event in events if event.type == "delegation.completed"]
        assert [event.payload["stage_id"] for event in requested] == ["builder", "review"]
        assert requested[1].payload["depends_on"] == ["builder"]
        assert requested[1].payload["attempt"] == 1
        assert requested[1].payload["evidence"][0]["event_id"] == completed[0].id
        review_child = state.ledger.get_session(str(completed[1].payload["child_session_id"]))
        card = next(
            event
            for event in state.ledger.list_events(review_child.id)
            if event.type == "delegation.task_card"
        )
        assert card.payload["stage_id"] == "review"
        assert card.payload["evidence"][0]["event_type"] == "delegation.completed"
        parent_requests = [
            request
            for request in provider.requests
            if next(message.content for message in request.messages if message.role == "user")
            == "Review these files"
        ]
        assert "Durable execution stages" in parent_requests[-1].system
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_child_tool_limit_failure_is_returned_to_parent(tmp_path: Path) -> None:
    provider = SequentialStageProvider(fail_child_with_tools=True)
    state, session_id, run_id = await start_parent(
        tmp_path, provider, limits="max_tool_calls_per_run=1"
    )
    try:
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[run_id]), 5)
        failed = next(
            event
            for event in state.ledger.list_events(session_id)
            if event.type == "delegation.failed"
        )
        assert failed.payload["stage_id"] == "builder"
        assert failed.payload["failure_code"] == "tool_call_limit"
        assert failed.payload["failure_message"] == "run tool-call limit was exhausted"
        parent_result = next(
            message
            for message in provider.requests[-1].messages
            if message.role == "tool" and message.tool_name == "spawn_agent"
        )
        structured = json.loads(parent_result.content)["structured_data"]
        assert structured["failure_code"] == "tool_call_limit"
        assert structured["failure_message"] == "run tool-call limit was exhausted"

        provider.fail_child_with_tools = False
        child_session_id = str(failed.payload["child_session_id"])
        followup_run_id = await state.runs.start(child_session_id, "Continue the review")
        await asyncio.wait_for(asyncio.shield(state.runs._tasks[followup_run_id]), 5)
        parent_events = state.ledger.list_events(session_id)
        followup = next(
            event for event in parent_events if event.type == "delegation.followup.completed"
        )
        assert followup.payload["child_run_id"] == followup_run_id
        workflow = project_workflow(run_id, parent_events)
        builder_stage = workflow.stage("builder")
        assert builder_stage is not None
        assert builder_stage.latest.status == "completed"
    finally:
        await state.runs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_delegated_work_with_disabled_active_time_cutoff(
    tmp_path: Path, cancel: bool
) -> None:
    provider = DelegationProvider(children=1)
    state, _session_id, run_id = await start_parent(
        tmp_path, provider, limits="max_active_seconds_per_run = 0"
    )
    try:
        parent_task = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 5)
        child_run = next(iter(state.runs._children_by_parent[run_id]))
        for current_run in [run_id, child_run]:
            started = next(
                e for e in state.ledger.list_run_events(current_run) if e.type == "run.started"
            )
            assert started.payload["max_active_seconds"] == 0
        if cancel:
            assert await state.runs.cancel(run_id)
        else:
            provider.release.set()
        await asyncio.wait_for(asyncio.shield(parent_task), 5)
        provider.release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(state.runs._delegation_tasks)), 5)
        for current_run in [run_id, child_run]:
            events = state.ledger.list_run_events(current_run)
            terminal = "run.cancelled" if cancel and current_run == run_id else "run.completed"
            assert any(e.type == terminal for e in events)
            assert not any(e.type == "run.failed" for e in events)
    finally:
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
async def test_steering_lead_preserves_workers_and_late_results(
    tmp_path: Path, queued: bool
) -> None:
    provider = DelegationProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        parent = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 5)
        result = await state.runs.submit(session_id, "Focus on tests", send_now=not queued)
        if queued:
            assert result.queued is not None
            await state.runs.send_queued_now(session_id, result.queued.id)
        await asyncio.wait_for(asyncio.shield(parent), 5)
        assert provider.cancelled == 0
        assert state.runs._active_child_count == 2
        provider.release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(state.runs._delegation_tasks)), 5)
        # Late worker results belong to the original tool exchange, even after steer.
        session = state.ledger.get_session(session_id)
        context = compile_context(
            session,
            state.ledger.replay(session_id),
            state.agents.load(session.agent_id),
            [],
            "policy",
            ContextConfig(),
            run_id="next",
        )
        pending: set[str] = set()
        for message in context.messages:
            if message.role == "user":
                assert not pending
            pending.update(call.id for call in message.tool_calls)
            if message.role == "tool":
                assert message.tool_call_id in pending
                pending.remove(message.tool_call_id)
        assert not pending
        events = state.ledger.list_events(session_id)
        assert len([e for e in events if e.type == "delegation.completed"]) == 2
        assert not any(e.type == "delegation.failed" for e in events)
    finally:
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_steering_worker_keeps_parent_waiting_for_replacement(tmp_path: Path) -> None:
    provider = DelegationProvider(children=1)
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        parent = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 5)
        child_run = next(iter(state.runs._children_by_parent[run_id]))
        child_session = next(e.session_id for e in state.ledger.list_run_events(child_run))
        child_task = state.runs._tasks[child_run]
        await state.runs.submit(child_session, "Also verify tests", send_now=True)
        await asyncio.wait_for(asyncio.shield(child_task), 5)
        assert not parent.done()
        assert not any(e.type == "delegation.failed" for e in state.ledger.list_events(session_id))
        provider.release.set()
        await asyncio.wait_for(asyncio.shield(parent), 5)
        done = next(
            e for e in state.ledger.list_events(session_id) if e.type == "delegation.completed"
        )
        assert done.payload["child_run_id"] != child_run
        assert done.payload["child_session_id"] == child_session
        assert not any(
            e.type == "delegation.stopping" for e in state.ledger.list_events(session_id)
        )
    finally:
        provider.release.set()
        await state.runs.close()


@pytest.mark.asyncio
async def test_explicit_worker_control_is_scoped_and_requires_user_instruction(
    tmp_path: Path,
) -> None:
    provider = DelegationProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        parent = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 5)
        session = state.ledger.get_session(session_id)
        workers = await state.runs._worker_sessions(session_id)
        assert len(workers) == 2
        rejected = await state.runs._control_workers(
            session,
            run_id,
            AgentControlArguments(
                action="stop", child_session_id=workers[0].id, user_instruction="Stop Default"
            ),
        )
        assert rejected.status == "rejected"
        assert provider.cancelled == 0
        invalid = await state.runs._control_workers(
            session, run_id, AgentControlArguments(action="wait", child_session_id=session_id)
        )
        assert invalid.status == "rejected"
        for text in [
            "Do not stop Default",
            "If needed stop Default",
            "Stop all workers except Default",
            "Stop some unrelated agent",
        ]:
            state.ledger.append(
                session_id=session_id,
                run_id=run_id,
                event_type="user.message",
                payload={"content": text},
            )
            rejected = await state.runs._control_workers(
                session,
                run_id,
                AgentControlArguments(
                    action="stop", child_session_id=workers[0].id, user_instruction=text
                ),
            )
            assert rejected.status == "rejected"
            assert provider.cancelled == 0
        state.ledger.append(
            session_id=session_id,
            run_id=run_id,
            event_type="user.message",
            payload={"content": "Stop Default"},
        )
        stopped = await state.runs._control_workers(
            session,
            run_id,
            AgentControlArguments(
                action="stop", child_session_id=workers[0].id, user_instruction="Stop Default"
            ),
        )
        assert stopped.status == "completed"
        first_task = state.runs._tasks.get(state.runs._session_runs.get(workers[0].id, ""))
        if first_task is not None:
            await asyncio.wait_for(asyncio.shield(first_task), 5)
        assert provider.cancelled == 1
        assert state.runs.is_session_active(workers[1].id)
        assert not parent.done()
        state.ledger.append(
            session_id=session_id,
            run_id=run_id,
            event_type="user.message",
            payload={"content": "Stop all workers"},
        )
        stopped = await state.runs._control_workers(
            session,
            run_id,
            AgentControlArguments(
                action="stop", all_workers=True, user_instruction="Stop all workers"
            ),
        )
        assert stopped.status == "completed"
        await asyncio.wait_for(asyncio.shield(parent), 5)
        assert provider.cancelled == 2
        assert any(e.type == "run.completed" for e in state.ledger.list_run_events(run_id))
    finally:
        provider.release.set()
        await state.runs.close()
