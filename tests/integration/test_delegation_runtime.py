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
    tmp_path: Path, provider: DelegationProvider, *, limits: str = "", restricted: bool = False
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
        state.agents.create("Worker")
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
                    arguments["agent_id"] = "worker"
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
async def test_parent_cancellation_cancels_parallel_children(tmp_path: Path) -> None:
    provider = DelegationProvider()
    state, session_id, run_id = await start_parent(tmp_path, provider)
    try:
        task = state.runs._tasks[run_id]
        await asyncio.wait_for(provider.children_entered.wait(), 3)
        children = tuple(state.runs._children_by_parent[run_id])
        assert await state.runs.cancel(run_id)
        await asyncio.wait_for(asyncio.shield(task), 5)
        assert provider.cancelled == 2
        assert state.runs._active_child_count == 0
        for child_run in children:
            assert any(
                event.type == "run.cancelled" for event in state.ledger.list_run_events(child_run)
            )
        assert any(event.type == "run.cancelled" for event in state.ledger.list_events(session_id))
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
async def test_delegation_carries_exact_approved_plan_and_inherits_it(tmp_path: Path) -> None:
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
