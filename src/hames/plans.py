"""Durable plan proposal, review, and approval projections."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hames.ledger import Event, Ledger, Session, new_id

PLAN_READY_MARKER = "<!-- hames:plan-ready -->"
_TASK = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.+?)\s*$")
_HEADING = re.compile(r"^#\s+(.+?)\s*$")

PlanStatus = Literal["ready", "requested", "approved", "executing", "completed", "failed"]


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlanRevision(PlanModel):
    id: str
    session_id: str
    revision: int
    title: str
    markdown: str
    tasks: list[str] = Field(default_factory=list)
    source_run_id: str
    supersedes_plan_id: str | None = None
    status: PlanStatus = "ready"
    strategy: Literal["keep", "compact"] | None = None
    execution_run_id: str | None = None
    execution_note: str = ""
    error: str = ""
    created_at: str
    updated_at: str


def _empty_revisions() -> list[PlanRevision]:
    return []


class PlanState(PlanModel):
    session_id: str
    current: PlanRevision | None = None
    revisions: list[PlanRevision] = Field(default_factory=_empty_revisions)


def visible_plan_output(answer: str) -> tuple[str, bool]:
    trimmed = answer.rstrip()
    if not trimmed.endswith(PLAN_READY_MARKER):
        return answer, False
    return trimmed[: -len(PLAN_READY_MARKER)].rstrip(), True


def parse_plan(markdown: str) -> tuple[str, list[str]]:
    title = "Implementation plan"
    tasks: list[str] = []
    in_tasks = False
    for line in markdown.splitlines():
        heading = _HEADING.match(line)
        if heading and title == "Implementation plan":
            title = heading.group(1).strip()[:120] or title
        normalized = line.strip().lower()
        if normalized in {"## tasks", "## task list", "## implementation tasks"}:
            in_tasks = True
            continue
        if in_tasks and normalized.startswith("## "):
            in_tasks = False
        task = _TASK.match(line)
        if task and (in_tasks or not tasks):
            text = " ".join(task.group(1).strip().split())
            if text and text not in tasks:
                tasks.append(text[:500])
    if not tasks:
        tasks = ["Implement and verify the approved plan"]
    return title, tasks


def project_plans(session_id: str, events: list[Event]) -> PlanState:
    revisions: list[PlanRevision] = []
    for event in events:
        if event.session_id != session_id or not event.type.startswith("plan."):
            continue
        if event.type == "plan.proposed":
            revisions.append(
                PlanRevision(
                    id=str(event.payload["plan_id"]),
                    session_id=session_id,
                    revision=int(event.payload["revision"]),
                    title=str(event.payload["title"]),
                    markdown=str(event.payload["markdown"]),
                    tasks=[str(item) for item in event.payload.get("tasks", [])],
                    source_run_id=str(event.payload["source_run_id"]),
                    supersedes_plan_id=str(event.payload.get("supersedes_plan_id", "")) or None,
                    created_at=event.created_at,
                    updated_at=event.created_at,
                )
            )
            continue
        plan_id = str(event.payload.get("plan_id", ""))
        index = next((i for i, plan in enumerate(revisions) if plan.id == plan_id), None)
        if index is None:
            continue
        plan = revisions[index]
        updates: dict[str, object] = {"updated_at": event.created_at}
        if event.type == "plan.execution.requested":
            updates.update(
                status="requested",
                strategy=event.payload.get("strategy"),
                execution_note=str(event.payload.get("execution_note") or ""),
            )
        elif event.type == "plan.approved":
            updates.update(
                status="approved",
                strategy=event.payload.get("strategy"),
                execution_note=str(event.payload.get("execution_note") or plan.execution_note),
                error="",
            )
        elif event.type == "plan.execution.started":
            execution_run_id = event.payload.get("execution_run_id")
            updates.update(
                status="executing",
                execution_run_id=(str(execution_run_id) if execution_run_id is not None else None),
            )
        elif event.type == "plan.execution.completed":
            updates.update(status="completed", error="")
        elif event.type == "plan.execution.failed":
            updates.update(status="failed", error=str(event.payload.get("message", "")))
        revisions[index] = plan.model_copy(update=updates)
    return PlanState(
        session_id=session_id,
        current=revisions[-1] if revisions else None,
        revisions=revisions,
    )


class PlanStore:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def current(self, session_id: str) -> PlanState:
        self.ledger.get_session(session_id)
        return project_plans(session_id, self.ledger.list_events(session_id))

    def propose(
        self, session: Session, *, run_id: str, markdown: str, causation_id: str | None
    ) -> tuple[PlanState, Event]:
        current = self.current(session.id)
        title, tasks = parse_plan(markdown)
        plan_id = new_id()
        event = self.ledger.append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="plan.proposed",
            payload={
                "plan_id": plan_id,
                "revision": len(current.revisions) + 1,
                "title": title,
                "markdown": markdown,
                "tasks": tasks,
                "source_run_id": run_id,
                "supersedes_plan_id": current.current.id if current.current else None,
            },
            causation_id=causation_id,
            correlation_id=plan_id,
        )
        return self.current(session.id), event

    def transition(
        self,
        session: Session,
        plan_id: str,
        event_type: str,
        *,
        strategy: Literal["keep", "compact"] | None = None,
        execution_run_id: str | None = None,
        execution_note: str = "",
        message: str = "",
        causation_id: str | None = None,
    ) -> tuple[PlanState, Event]:
        state = self.current(session.id)
        if state.current is None or state.current.id != plan_id:
            raise ValueError("plan is not current for this session")
        event = self.ledger.append(
            session_id=session.id,
            run_id=execution_run_id,
            agent_id=session.agent_id,
            event_type=event_type,
            payload={
                "plan_id": plan_id,
                "strategy": strategy,
                "execution_run_id": execution_run_id,
                "execution_note": execution_note,
                "message": message,
            },
            causation_id=causation_id,
            correlation_id=plan_id,
        )
        return self.current(session.id), event
