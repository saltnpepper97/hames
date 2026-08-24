"""Event-sourced autonomous goal state and lifecycle mutations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hames.ledger import Event, Ledger, Session, new_id

GoalStatus = Literal["running", "yielded", "paused", "achieved", "blocked", "cancelled"]
GoalReportStatus = Literal["progress", "achieved", "blocked"]

TERMINAL_GOAL_STATUSES = frozenset({"achieved", "cancelled"})


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    objective: str
    status: GoalStatus
    step_count: int = 0
    current_run_id: str | None = None
    latest_summary: str = ""
    latest_evidence: list[str] = Field(default_factory=list)
    latest_signature: str = ""
    repeated_no_progress: int = 0
    created_at: str
    updated_at: str


def project_goals(events: list[Event]) -> list[Goal]:
    goals: dict[str, Goal] = {}
    order: list[str] = []
    for event in events:
        if not event.type.startswith("goal."):
            continue
        goal_id = str(event.payload.get("goal_id", ""))
        if not goal_id:
            continue
        if event.type == "goal.created":
            goals[goal_id] = Goal(
                id=goal_id,
                session_id=event.session_id,
                objective=str(event.payload["objective"]),
                status="running",
                created_at=event.created_at,
                updated_at=event.created_at,
            )
            order.append(goal_id)
            continue
        goal = goals.get(goal_id)
        if goal is None:
            continue
        updates: dict[str, object] = {"updated_at": event.created_at}
        if event.type == "goal.step.started":
            updates.update(
                status="running",
                step_count=int(event.payload.get("step", goal.step_count + 1)),
                current_run_id=str(event.payload.get("run_id", "")) or None,
            )
        elif event.type == "goal.progressed":
            updates.update(
                status="running",
                latest_summary=str(event.payload.get("summary", "")),
                latest_evidence=list(event.payload.get("evidence", [])),
                repeated_no_progress=int(event.payload.get("repeated_no_progress", 0)),
                latest_signature=str(event.payload.get("signature", "")),
            )
        elif event.type == "goal.yielded":
            updates.update(status="yielded", current_run_id=None)
        elif event.type == "goal.resumed":
            updates.update(status="running", current_run_id=None)
        elif event.type == "goal.paused":
            updates.update(
                status="paused",
                current_run_id=None,
                latest_summary=str(event.payload.get("summary", goal.latest_summary)),
            )
        elif event.type in {"goal.achieved", "goal.blocked", "goal.cancelled"}:
            updates.update(
                status=event.type.removeprefix("goal."),
                current_run_id=None,
                latest_summary=str(event.payload.get("summary", goal.latest_summary)),
                latest_evidence=list(event.payload.get("evidence", goal.latest_evidence)),
            )
        goals[goal_id] = goal.model_copy(update=updates)
    return [goals[goal_id] for goal_id in order]


class GoalStore:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def list(self, session_id: str) -> list[Goal]:
        self.ledger.get_session(session_id)
        return project_goals(self.ledger.list_events(session_id))

    def current(self, session_id: str) -> Goal | None:
        return next(
            (
                goal
                for goal in reversed(self.list(session_id))
                if goal.status not in TERMINAL_GOAL_STATUSES
            ),
            None,
        )

    def create(self, session: Session, objective: str) -> tuple[Goal, Event]:
        if self.current(session.id) is not None:
            raise ValueError("session already has a current goal")
        goal_id = new_id()
        event = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="goal.created",
            payload={"goal_id": goal_id, "objective": objective, "status": "running"},
            correlation_id=goal_id,
        )
        goal = self.current(session.id)
        assert goal is not None
        return goal, event

    def transition(
        self,
        session: Session,
        goal_id: str,
        event_type: str,
        *,
        run_id: str | None = None,
        step: int = 0,
        summary: str = "",
        evidence: list[str] | None = None,
        reason: str = "",
        signature: str = "",
        repeated_no_progress: int = 0,
        causation_id: str | None = None,
    ) -> tuple[Goal, Event]:
        current = self.current(session.id)
        if current is None or current.id != goal_id:
            raise ValueError("goal is not current for this session")
        event = self.ledger.append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type=event_type,
            payload={
                "goal_id": goal_id,
                "objective": current.objective,
                "status": event_type.removeprefix("goal.").removeprefix("step."),
                "step": step,
                "run_id": run_id or "",
                "summary": summary,
                "evidence": evidence or [],
                "reason": reason,
                "signature": signature,
                "repeated_no_progress": repeated_no_progress,
            },
            causation_id=causation_id,
            correlation_id=goal_id,
        )
        updated = next(goal for goal in self.list(session.id) if goal.id == goal_id)
        return updated, event
