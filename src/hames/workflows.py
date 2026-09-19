"""Durable dependency graph projected from delegated stage attempts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hames.ledger import Event

WorkflowAttemptStatus = Literal["running", "completed", "failed", "cancelled"]


def _empty_attempts() -> list[WorkflowStageAttempt]:
    return []


def _empty_stages() -> list[WorkflowStage]:
    return []


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowStageAttempt(WorkflowModel):
    workflow_id: str
    stage_id: str
    attempt: int = Field(ge=1)
    depends_on: list[str] = Field(default_factory=list)
    target_agent_id: str
    task: str
    status: WorkflowAttemptStatus = "running"
    request_event_id: str
    terminal_event_id: str = ""
    child_session_id: str = ""
    child_run_id: str = ""
    summary: str = ""
    failure_code: str = ""
    failure_message: str = ""
    retryable: bool = False


class WorkflowStage(WorkflowModel):
    id: str
    attempts: list[WorkflowStageAttempt] = Field(default_factory=_empty_attempts)

    @property
    def latest(self) -> WorkflowStageAttempt:
        return self.attempts[-1]


class WorkflowState(WorkflowModel):
    workflow_id: str
    stages: list[WorkflowStage] = Field(default_factory=_empty_stages)

    def stage(self, stage_id: str) -> WorkflowStage | None:
        return next((stage for stage in self.stages if stage.id == stage_id), None)


def project_workflow(workflow_id: str, events: list[Event]) -> WorkflowState:
    stages: list[WorkflowStage] = []
    attempts: dict[tuple[str, int], WorkflowStageAttempt] = {}
    for event in events:
        if str(event.payload.get("workflow_id", "")) != workflow_id:
            continue
        stage_id = str(event.payload.get("stage_id", ""))
        if not stage_id:
            continue
        attempt_number = int(event.payload.get("attempt", 0) or 0)
        if event.type == "delegation.requested" and attempt_number > 0:
            attempt = WorkflowStageAttempt(
                workflow_id=workflow_id,
                stage_id=stage_id,
                attempt=attempt_number,
                depends_on=[str(value) for value in event.payload.get("depends_on", [])],
                target_agent_id=str(event.payload.get("target_agent_id", "")),
                task=str(event.payload.get("task", "")),
                request_event_id=event.id,
            )
            stage = next((item for item in stages if item.id == stage_id), None)
            if stage is None:
                stage = WorkflowStage(id=stage_id)
                stages.append(stage)
            stage.attempts.append(attempt)
            attempts[(stage_id, attempt_number)] = attempt
            continue
        if event.type not in {
            "delegation.completed",
            "delegation.failed",
            "delegation.followup.completed",
            "delegation.followup.failed",
        }:
            continue
        attempt = attempts.get((stage_id, attempt_number))
        if attempt is None:
            continue
        raw_status = str(event.payload.get("status", "failed"))
        status: WorkflowAttemptStatus = (
            "completed"
            if event.type in {"delegation.completed", "delegation.followup.completed"}
            else "cancelled"
            if raw_status == "cancelled"
            else "failed"
        )
        updated = attempt.model_copy(
            update={
                "status": status,
                "terminal_event_id": event.id,
                "child_session_id": str(event.payload.get("child_session_id", "")),
                "child_run_id": str(event.payload.get("child_run_id", "")),
                "summary": str(event.payload.get("summary", "")),
                "failure_code": str(event.payload.get("failure_code", "")),
                "failure_message": str(event.payload.get("failure_message", "")),
                "retryable": bool(event.payload.get("retryable", False)),
            }
        )
        stage = next(item for item in stages if item.id == stage_id)
        index = next(i for i, item in enumerate(stage.attempts) if item.attempt == attempt_number)
        stage.attempts[index] = updated
        attempts[(stage_id, attempt_number)] = updated
    return WorkflowState(workflow_id=workflow_id, stages=stages)
