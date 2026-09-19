from pathlib import Path
from typing import Literal

import pytest

# pyright: reportPrivateUsage=false
from hames.gateway import GatewayState
from hames.paths import HamesPaths
from hames.providers.fake import FakeProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,status,approved,purpose,resumes,content",
    [
        ("auto", "failed", True, "turn", True, "Finish this up"),
        ("plan", "failed", True, "turn", False, "continue"),
        ("auto", "ready", False, "turn", False, "continue"),
        ("auto", "completed", True, "turn", False, "continue"),
        ("auto", "failed", False, "turn", False, "continue"),
        ("auto", "failed", True, "plan_note", False, "continue"),
        ("auto", "failed", True, "turn", False, "Why does it say failed?"),
        ("auto", "failed", True, "turn", False, "Explain the implementation"),
        ("auto", "failed", True, "turn", True, "shit can you finish this up?"),
    ],
)
async def test_plan_continuation_links_only_unfinished_approved_execution(
    tmp_path: Path,
    mode: Literal["auto", "plan"],
    status: str,
    approved: bool,
    purpose: str,
    resumes: bool,
    content: str,
) -> None:
    state = GatewayState.create(
        HamesPaths.resolve(root=tmp_path), providers={"fake": FakeProvider([])}
    )
    session = state.ledger.create_session(
        working_directory=tmp_path, provider="fake", model="fixture", agent_id="default"
    )
    session = state.ledger.update_session_mode(session.id, mode=mode)
    try:
        plans = state.runs.plans
        proposed, _ = plans.propose(
            session, run_id="planning", markdown="# Exact plan\nKeep this text.", causation_id=None
        )
        assert proposed.current is not None
        plan_id = proposed.current.id
        if approved:
            plans.transition(session, plan_id, "plan.approved", execution_run_id="old")
        if status != "ready":
            plans.transition(session, plan_id, "plan.execution.started", execution_run_id="old")
            plans.transition(
                session,
                plan_id,
                f"plan.execution.{status}",
                execution_run_id="old",
                message="old timeout",
            )
        user = state.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="user.message",
            payload={"content": content, "purpose": purpose},
        )
        await state.runs._resume_failed_plan(session, "continuation", user)
        current = plans.current(session.id).current
        assert current is not None
        assert current.status == ("executing" if resumes else status)
        if resumes:
            assert current.execution_run_id == "continuation"
            assert current.error == ""
            captured = state.runs._delegation_plan(session, "continuation")
            assert captured is not None
            assert captured["markdown"] == proposed.current.markdown
            # Late finalization from the old attempt must not poison the new run.
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
