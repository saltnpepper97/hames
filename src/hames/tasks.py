"""Event-sourced per-session execution checklists."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hames.ledger import Event, Ledger, Session, new_id

TaskStatus = Literal["pending", "in_progress", "completed", "blocked"]


class TaskModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionTask(TaskModel):
    id: str
    text: str
    status: TaskStatus = "pending"
    position: int = 0
    created_by: str = "agent"


def _empty_tasks() -> list[SessionTask]:
    return []


class SessionTaskList(TaskModel):
    session_id: str
    title: str = "Tasks"
    revision: int = 0
    items: list[SessionTask] = Field(default_factory=_empty_tasks)
    updated_at: str = ""


def project_tasks(session_id: str, events: list[Event]) -> SessionTaskList:
    current = SessionTaskList(session_id=session_id)
    for event in events:
        if event.session_id != session_id:
            continue
        if event.type == "tasks.replaced":
            current = SessionTaskList(
                session_id=session_id,
                title=str(event.payload.get("title", "Tasks")),
                revision=int(event.payload.get("revision", current.revision + 1)),
                items=[SessionTask.model_validate(item) for item in event.payload.get("items", [])],
                updated_at=event.created_at,
            )
        elif event.type == "task.added":
            item = SessionTask.model_validate(event.payload["task"])
            items = list(current.items)
            index = min(max(item.position, 0), len(items))
            items.insert(index, item)
            current = current.model_copy(
                update={"items": _positions(items), "updated_at": event.created_at}
            )
        elif event.type == "task.updated":
            task_id = str(event.payload.get("task_id", ""))
            items = list(current.items)
            index = next((i for i, item in enumerate(items) if item.id == task_id), None)
            if index is None:
                continue
            item = items.pop(index)
            status = event.payload.get("status")
            if status == "in_progress":
                items = [
                    other.model_copy(update={"status": "pending"})
                    if other.status == "in_progress"
                    else other
                    for other in items
                ]
            text = event.payload.get("text")
            item = item.model_copy(
                update={
                    "text": str(text) if text is not None else item.text,
                    "status": status or item.status,
                }
            )
            raw_position = event.payload.get("position")
            position = int(raw_position) if raw_position is not None else index
            items.insert(min(max(position, 0), len(items)), item)
            current = current.model_copy(
                update={"items": _positions(items), "updated_at": event.created_at}
            )
        elif event.type == "task.removed":
            task_id = str(event.payload.get("task_id", ""))
            current = current.model_copy(
                update={
                    "items": _positions([item for item in current.items if item.id != task_id]),
                    "updated_at": event.created_at,
                }
            )
    return current


def _positions(items: list[SessionTask]) -> list[SessionTask]:
    return [item.model_copy(update={"position": index}) for index, item in enumerate(items)]


class TaskStore:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger

    def current(self, session_id: str) -> SessionTaskList:
        self.ledger.get_session(session_id)
        return project_tasks(session_id, self.ledger.list_events(session_id))

    def replace(
        self,
        session: Session,
        *,
        title: str,
        tasks: list[str],
        created_by: str = "plan",
        causation_id: str | None = None,
    ) -> tuple[SessionTaskList, Event]:
        current = self.current(session.id)
        items = [
            SessionTask(
                id=new_id(),
                text=_task_text(text),
                position=index,
                created_by=created_by,
            )
            for index, text in enumerate(tasks)
        ]
        event = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="tasks.replaced",
            payload={
                "title": title.strip()[:120] or "Tasks",
                "revision": current.revision + 1,
                "items": [item.model_dump(mode="json") for item in items],
            },
            causation_id=causation_id,
            correlation_id=session.id,
        )
        return self.current(session.id), event

    def add(
        self,
        session: Session,
        *,
        text: str,
        position: int | None = None,
        created_by: str = "agent",
        causation_id: str | None = None,
    ) -> tuple[SessionTaskList, Event]:
        current = self.current(session.id)
        item = SessionTask(
            id=new_id(),
            text=_task_text(text),
            position=len(current.items) if position is None else position,
            created_by=created_by,
        )
        event = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="task.added",
            payload={"task": item.model_dump(mode="json")},
            causation_id=causation_id,
            correlation_id=session.id,
        )
        return self.current(session.id), event

    def update(
        self,
        session: Session,
        task_id: str,
        *,
        text: str | None = None,
        status: TaskStatus | None = None,
        position: int | None = None,
        causation_id: str | None = None,
    ) -> tuple[SessionTaskList, Event]:
        current = self.current(session.id)
        task = next((item for item in current.items if item.id == task_id), None)
        if task is None:
            raise KeyError(task_id)
        payload: dict[str, object] = {"task_id": task_id}
        if text is not None:
            payload["text"] = _task_text(text)
        if status is not None:
            payload["status"] = status
        if position is not None:
            payload["position"] = position
        if len(payload) == 1:
            raise ValueError("task update requires text, status, or position")
        event = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="task.updated",
            payload=payload,
            causation_id=causation_id,
            correlation_id=session.id,
        )
        return self.current(session.id), event

    def remove(
        self,
        session: Session,
        task_id: str,
        *,
        causation_id: str | None = None,
    ) -> tuple[SessionTaskList, Event]:
        if not any(item.id == task_id for item in self.current(session.id).items):
            raise KeyError(task_id)
        event = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="task.removed",
            payload={"task_id": task_id},
            causation_id=causation_id,
            correlation_id=session.id,
        )
        return self.current(session.id), event


def _task_text(value: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("task text cannot be empty")
    if len(normalized) > 500:
        raise ValueError("task text cannot exceed 500 characters")
    return normalized
