from pathlib import Path

from hames.ledger import Ledger
from hames.paths import HamesPaths
from hames.workflows import project_workflow


def test_workflow_projection_keeps_attempts_dependencies_and_failure(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    hames_paths.ensure_foundation()
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    first = ledger.append(
        session_id=session.id,
        run_id="run-one",
        event_type="delegation.requested",
        payload={
            "tool_call_id": "call-one",
            "target_agent_id": "reviewer",
            "task": "Review",
            "delegation_depth": 1,
            "workflow_id": "plan-one",
            "stage_id": "final-review",
            "depends_on": ["builder"],
            "attempt": 1,
        },
    )
    ledger.append(
        session_id=session.id,
        run_id="run-one",
        event_type="delegation.failed",
        payload={
            "child_session_id": "child-one",
            "child_run_id": "child-run-one",
            "target_agent_id": "reviewer",
            "status": "failed",
            "summary": "child agent failed",
            "workflow_id": "plan-one",
            "stage_id": "final-review",
            "attempt": 1,
            "failure_code": "tool_call_limit",
            "failure_message": "run tool-call limit was exhausted",
            "retryable": True,
        },
        causation_id=first.id,
    )
    second = ledger.append(
        session_id=session.id,
        run_id="run-two",
        event_type="delegation.requested",
        payload={
            "tool_call_id": "call-two",
            "target_agent_id": "reviewer",
            "task": "Retry review",
            "delegation_depth": 1,
            "workflow_id": "plan-one",
            "stage_id": "final-review",
            "depends_on": ["builder"],
            "attempt": 2,
        },
    )
    ledger.append(
        session_id=session.id,
        run_id="run-two",
        event_type="delegation.completed",
        payload={
            "child_session_id": "child-two",
            "child_run_id": "child-run-two",
            "target_agent_id": "reviewer",
            "status": "completed",
            "summary": "child agent completed",
            "workflow_id": "plan-one",
            "stage_id": "final-review",
            "attempt": 2,
        },
        causation_id=second.id,
    )

    workflow = project_workflow("plan-one", ledger.list_events(session.id))

    assert [stage.id for stage in workflow.stages] == ["final-review"]
    stage = workflow.stages[0]
    assert [attempt.status for attempt in stage.attempts] == ["failed", "completed"]
    assert stage.attempts[0].failure_code == "tool_call_limit"
    assert stage.latest.attempt == 2
    assert stage.latest.depends_on == ["builder"]
