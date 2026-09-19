from pathlib import Path
from unittest.mock import patch

import pytest

# pyright: reportPrivateUsage=false
from hames.gateway import GatewayState
from hames.paths import HamesPaths
from hames.providers.fake import FakeProvider


@pytest.mark.asyncio
async def test_explicit_plan_resume_preserves_checklist_and_approved_plan(tmp_path: Path) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )
    session = state.ledger.create_session(
        working_directory=tmp_path, provider="fake", model="fixture", agent_id="default"
    )
    try:
        plans = state.runs.plans
        proposed, _ = plans.propose(
            session,
            run_id="planning",
            markdown="# Exact plan\n\n- [ ] Keep completed work\n- [ ] Retry review",
            causation_id=None,
        )
        assert proposed.current is not None
        plan_id = proposed.current.id
        plans.transition(
            session,
            plan_id,
            "plan.approved",
            strategy="keep",
            execution_run_id="old",
            execution_agent="workflow",
        )
        plans.transition(
            session,
            plan_id,
            "plan.execution.started",
            strategy="keep",
            execution_run_id="old",
            execution_agent="workflow",
        )
        plans.transition(
            session,
            plan_id,
            "plan.execution.attention",
            strategy="keep",
            execution_run_id="old",
            execution_agent="workflow",
            code="tool_call_limit_exceeded",
            message="reviewer exhausted 999 tool calls",
        )
        tasks, _ = state.runs.session_tasks.replace(
            session,
            title="Exact plan",
            tasks=["Keep completed work", "Retry review"],
        )
        tasks, _ = state.runs.session_tasks.update(session, tasks.items[0].id, status="completed")
        original_ids = [item.id for item in tasks.items]

        _, requested = plans.transition(
            session,
            plan_id,
            "plan.execution.requested",
            strategy="keep",
            execution_run_id="continuation",
            execution_agent="workflow",
        )
        _, resumed_tasks, user = await state.runs._prepare_plan_resume(
            session,
            plan_id,
            "continuation",
            requested.id,
            "",
            execution_agent="workflow",
        )

        current = plans.current(session.id).current
        assert current is not None
        assert current.status == "executing"
        assert current.execution_run_id == "continuation"
        assert current.execution_agent == "workflow"
        assert current.error == ""
        assert [item.id for item in resumed_tasks.items] == original_ids
        assert resumed_tasks.items[0].status == "completed"
        assert user.payload["purpose"] == "plan_execution"
        assert "retry only the unfinished stage" in str(user.payload["content"])
        captured = state.runs._delegation_plan(session, "continuation")
        assert captured is not None
        assert captured["markdown"] == proposed.current.markdown

        # Late finalization from the old attempt must not poison the resumed run.
        await state.runs._finalize_plan_execution(session.id, "old")
        unchanged = plans.current(session.id).current
        assert unchanged is not None and unchanged.status == "executing"
        state.ledger.append(
            session_id=session.id,
            run_id="continuation",
            agent_id=session.agent_id,
            event_type="run.completed",
            payload={"model_turns": 1, "tool_calls": 0, "active_seconds": 1.0},
        )
        await state.runs._finalize_plan_execution(session.id, "continuation")
        done = plans.current(session.id).current
        assert done is not None and done.status == "completed" and done.error == ""
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_execute_plan_uses_explicit_resume_path(tmp_path: Path) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )
    session = state.ledger.create_session(
        working_directory=tmp_path, provider="fake", model="fixture", agent_id="default"
    )
    try:
        proposed, _ = state.runs.plans.propose(
            session, run_id="planning", markdown="# Resume me", causation_id=None
        )
        assert proposed.current is not None
        plan_id = proposed.current.id
        state.runs.plans.transition(
            session,
            plan_id,
            "plan.approved",
            strategy="keep",
            execution_run_id="old",
            execution_agent="default",
            execution_note="Preserve the public API",
        )
        state.runs.plans.transition(
            session,
            plan_id,
            "plan.execution.started",
            execution_run_id="old",
            execution_agent="default",
        )
        state.runs.plans.transition(
            session,
            plan_id,
            "plan.execution.attention",
            execution_run_id="old",
            code="provider_disconnected",
            message="computer slept",
        )

        with patch.object(state.runs, "_launch") as launch:
            resumed, _, run_id = await state.runs.execute_plan(session.id, strategy="keep")

        assert resumed.current is not None
        assert resumed.current.status == "executing"
        assert resumed.current.execution_run_id == run_id
        assert resumed.current.execution_note == "Preserve the public API"
        assert any(
            event.type == "plan.execution.resumed" for event in state.ledger.list_events(session.id)
        )
        launch.assert_called_once()
    finally:
        await state.runs.close()


@pytest.mark.asyncio
async def test_completed_followup_reconciles_attention_plan(tmp_path: Path) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )
    session = state.ledger.create_session(
        working_directory=tmp_path, provider="fake", model="fixture", agent_id="default"
    )
    try:
        proposed, _ = state.runs.plans.propose(
            session, run_id="planning", markdown="# Repair later", causation_id=None
        )
        assert proposed.current is not None
        plan_id = proposed.current.id
        state.runs.plans.transition(
            session,
            plan_id,
            "plan.execution.started",
            strategy="keep",
            execution_run_id="execution",
        )
        tasks, _ = state.runs.session_tasks.replace(
            session, title="Repair later", tasks=["Finish verification"]
        )
        state.runs.plans.transition(
            session,
            plan_id,
            "plan.execution.attention",
            strategy="keep",
            execution_run_id="execution",
            code="workflow_needs_attention",
            message="one checklist item remains",
        )

        state.ledger.append(
            session_id=session.id,
            run_id="unrelated",
            agent_id=session.agent_id,
            event_type="run.started",
            payload={
                "max_model_turns": 10,
                "max_tool_calls": 20,
                "max_active_seconds": 60.0,
            },
        )
        state.ledger.append(
            session_id=session.id,
            run_id="unrelated",
            agent_id=session.agent_id,
            event_type="run.completed",
            payload={"model_turns": 1, "tool_calls": 0, "active_seconds": 1.0},
        )
        await state.runs._finalize_plan_execution(session.id, "unrelated")
        unchanged = state.runs.plans.current(session.id).current
        assert unchanged is not None and unchanged.status == "needs_attention"

        state.ledger.append(
            session_id=session.id,
            run_id="followup",
            agent_id=session.agent_id,
            event_type="run.started",
            payload={
                "max_model_turns": 10,
                "max_tool_calls": 20,
                "max_active_seconds": 60.0,
            },
        )
        state.runs.session_tasks.update(session, tasks.items[0].id, status="completed")
        terminal = state.ledger.append(
            session_id=session.id,
            run_id="followup",
            agent_id=session.agent_id,
            event_type="run.completed",
            payload={"model_turns": 1, "tool_calls": 1, "active_seconds": 1.0},
        )

        await state.runs._finalize_plan_execution(session.id, "followup")

        completed = state.runs.plans.current(session.id).current
        assert completed is not None and completed.status == "completed"
        event = state.ledger.list_events(session.id)[-1]
        assert event.type == "plan.execution.completed"
        assert event.causation_id == terminal.id
    finally:
        await state.runs.close()
