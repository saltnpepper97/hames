"""Durable, provenance-backed layered memory and deterministic retrieval."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hames.ledger import Event, Ledger, Session, new_id, utc_now
from hames.providers.base import JSON_OBJECT, JsonValue

MemoryLayer = Literal["relationship", "semantic", "episodic"]
MemoryStatus = Literal["proposed", "active", "rejected", "superseded", "retracted"]
MemoryVisibility = Literal["global", "agent_private", "workspace", "session_team"]
MemoryOrigin = Literal["automatic", "explicit", "episode"]

_TOKEN = re.compile(r"[\w-]{2,}", re.UNICODE)
_SECRET = re.compile(
    r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk|ghp|xox[baprs])_[A-Za-z0-9_-]{16,}|"
    r"\bAKIA[A-Z0-9]{16}\b)",
    re.IGNORECASE,
)
_ROUTINE_EPISODE_TOOLS = {
    "get_goal",
    "list_dir",
    "memory_search",
    "read_file",
    "scar_list",
    "shell",
    "skill_catalog",
    "task_list",
    "task_update",
}
_ROUTINE_EPISODE_SUMMARY = re.compile(
    r"^(?:shell exited with code 0|read\s|listed\s|searched\s|marked\s|added\s)",
    re.IGNORECASE,
)


def _compact_inline(value: object, limit: int) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{clipped or text[: limit - 1].rstrip()}…"


def _compact_block(value: object, limit: int) -> str:
    if value is None:
        return ""
    lines = [line.rstrip() for line in str(value).strip().splitlines()]
    text = "\n".join(line for line in lines if line.strip())
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-\n")
    return f"{clipped or text[: limit - 1].rstrip()}…"


def _unique_bounded(values: list[str], *, count: int, length: int) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for value in values:
        compact = _compact_inline(value, length)
        key = compact.casefold()
        if not compact or key in seen:
            continue
        selected.append(compact)
        seen.add(key)
        if len(selected) >= count:
            break
    return selected


@dataclass(frozen=True, slots=True)
class _EpisodeProjection:
    summary: str
    value: dict[str, JsonValue]
    provenance_event_ids: list[str]


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryAnchor(MemoryModel):
    kind: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=2048)


def _empty_memory_anchors() -> list[MemoryAnchor]:
    return []


class SemanticDecision(MemoryModel):
    memory_id: str
    replacement_id: str | None = None
    reason: Literal["redundant", "superseded", "transient", "expired"]
    explanation: str = Field(min_length=10, max_length=1000)
    confidence: float = Field(ge=0.9, le=1)


def memory_scope(record: MemoryRecord) -> tuple[object, ...]:
    return (
        record.visibility,
        record.owner_agent_id,
        record.workspace_path,
        record.lineage_root_session_id,
    )


class MemoryRecord(MemoryModel):
    id: str
    layer: MemoryLayer
    status: MemoryStatus
    visibility: MemoryVisibility
    subject: str
    predicate: str
    value: JsonValue
    summary: str
    confidence: float
    importance: float
    owner_agent_id: str | None
    workspace_path: str | None
    lineage_root_session_id: str | None
    source_session_id: str
    source_run_id: str | None
    origin_kind: MemoryOrigin
    valid_from: str | None
    valid_until: str | None
    superseded_by_id: str | None
    created_at: str
    updated_at: str
    anchors: list[MemoryAnchor]
    provenance_event_ids: list[str]


class MemoryCandidate(MemoryModel):
    layer: MemoryLayer
    visibility: MemoryVisibility
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=120)
    value: JsonValue
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    anchors: list[MemoryAnchor] = Field(default_factory=_empty_memory_anchors)
    provenance_event_ids: list[str] = Field(min_length=1)
    supersedes_id: str | None = None
    evidence_basis: Literal["explicit_user", "successful_tool", "assistant_inference"]
    valid_from: str | None = None
    valid_until: str | None = None

    @field_validator("anchors")
    @classmethod
    def unique_anchors(cls, values: list[MemoryAnchor]) -> list[MemoryAnchor]:
        if len({(item.kind, item.value) for item in values}) != len(values):
            raise ValueError("memory anchors must be unique")
        return values


class RetrievedMemory(MemoryModel):
    record: MemoryRecord
    score: float
    estimated_tokens: int


class MemoryJob(MemoryModel):
    id: str
    kind: Literal["extraction", "explicit_capture"]
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    session_id: str
    run_id: str | None
    source_event_id: str
    content: str | None
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryMutation:
    record: MemoryRecord
    events: tuple[Event, ...]


def contains_secret(value: str) -> bool:
    return _SECRET.search(value) is not None


class MemoryStore:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.database = ledger.database

    def create_candidate(
        self,
        *,
        session: Session,
        candidate: MemoryCandidate,
        run_id: str | None,
        origin_kind: MemoryOrigin,
        activate: bool,
        causation_id: str,
    ) -> MemoryMutation:
        self._validate_candidate(session, candidate)
        memory_id = new_id()
        now = utc_now()
        lineage_root = self.lineage_root(session.id)
        workspace = session.working_directory if candidate.visibility == "workspace" else None
        owner = session.agent_id if candidate.visibility == "agent_private" else None
        team = lineage_root if candidate.visibility == "session_team" else None
        events: list[Event] = []
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            proposed = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="memory.proposed",
                payload=self._record_event_payload(memory_id, candidate, "proposed"),
                causation_id=causation_id,
                correlation_id=run_id or memory_id,
            )
            events.append(proposed)
            status: MemoryStatus = "active" if activate else "proposed"
            connection.execute(
                """
                INSERT INTO memory_records(
                    id, layer, status, visibility, subject, predicate, value_json,
                    summary, confidence, importance, owner_agent_id, workspace_path,
                    lineage_root_session_id, source_session_id, source_run_id,
                    origin_kind, valid_from, valid_until, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_id,
                    candidate.layer,
                    status,
                    candidate.visibility,
                    candidate.subject,
                    candidate.predicate,
                    json.dumps(candidate.value, separators=(",", ":"), sort_keys=True),
                    candidate.summary,
                    candidate.confidence,
                    candidate.importance,
                    owner,
                    workspace,
                    team,
                    session.id,
                    run_id,
                    origin_kind,
                    candidate.valid_from,
                    candidate.valid_until,
                    now,
                    now,
                ),
            )
            for anchor in self._effective_anchors(session, candidate, lineage_root):
                connection.execute(
                    "INSERT INTO memory_anchors(memory_id, kind, value) VALUES (?, ?, ?)",
                    (memory_id, anchor.kind, anchor.value),
                )
            for event_id in candidate.provenance_event_ids:
                connection.execute(
                    "INSERT INTO memory_provenance(memory_id, event_id) VALUES (?, ?)",
                    (memory_id, event_id),
                )
            connection.execute(
                "INSERT INTO memory_fts(memory_id, subject, predicate, summary, value) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    memory_id,
                    candidate.subject,
                    candidate.predicate,
                    candidate.summary,
                    json.dumps(candidate.value, sort_keys=True),
                ),
            )
            if activate:
                accepted = self.ledger.append_in_transaction(
                    connection,
                    session_id=session.id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="memory.accepted",
                    payload={
                        "memory_id": memory_id,
                        "previous_status": "proposed",
                        "status": "active",
                        "reason": "policy_auto_accept"
                        if origin_kind == "automatic"
                        else "explicit_user_capture",
                    },
                    causation_id=proposed.id,
                    correlation_id=run_id or memory_id,
                )
                events.append(accepted)
                if candidate.supersedes_id is not None:
                    events.append(
                        self._supersede_on_connection(
                            connection,
                            session,
                            candidate.supersedes_id,
                            memory_id,
                            run_id,
                            accepted.id,
                            now,
                        )
                    )
            connection.commit()
        return MemoryMutation(self.get(memory_id), tuple(events))

    def project_episode(self, session: Session, run_id: str) -> MemoryMutation | None:
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM memory_records WHERE layer = 'episodic' AND source_run_id = ?",
                (run_id,),
            ).fetchone()
        if existing is not None:
            return MemoryMutation(self.get(str(existing["id"])), ())
        projection = self._episode_projection(run_id)
        if projection is None:
            return None
        events = self.ledger.list_run_events(run_id)
        notable_types = {
            "tool.completed",
            "tool.failed",
            "tool.rejected",
            "run.failed",
            "run.cancelled",
            "delegation.completed",
            "delegation.failed",
            "memory.accepted",
            "memory.retracted",
            "memory.deleted",
            "memory.superseded",
        }
        notable = [event for event in events if event.type in notable_types]
        if not notable:
            return None
        terminal = next(
            (event for event in reversed(events) if event.type.startswith("run.")), events[-1]
        )
        candidate = MemoryCandidate(
            layer="episodic",
            visibility="workspace",
            subject=f"run:{run_id}",
            predicate="recorded_outcome",
            value=projection.value,
            summary=projection.summary,
            confidence=1.0,
            importance=0.9 if projection.value.get("failures") else 0.75,
            anchors=[],
            provenance_event_ids=projection.provenance_event_ids,
            evidence_basis="successful_tool",
        )
        mutation = self.create_candidate(
            session=session,
            candidate=candidate,
            run_id=run_id,
            origin_kind="episode",
            activate=True,
            causation_id=terminal.id,
        )
        projected = self.ledger.append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="memory.episode.projected",
            payload={
                "memory_id": mutation.record.id,
                "source_run_id": run_id,
                "reason": "notable_run",
            },
            causation_id=mutation.events[-1].id,
            correlation_id=run_id,
        )
        return MemoryMutation(mutation.record, (*mutation.events, projected))

    def _episode_projection(self, run_id: str) -> _EpisodeProjection | None:
        events = self.ledger.list_run_events(run_id)
        if not events:
            return None
        started = next((event for event in events if event.type == "run.started"), None)
        user_event: Event | None = None
        if started is not None and started.causation_id is not None:
            candidate = self.ledger.get_event(started.causation_id)
            if candidate.type == "user.message":
                user_event = candidate
        assistant_event = next(
            (
                event
                for event in reversed(events)
                if event.type == "assistant.message" and event.payload.get("status") == "completed"
            ),
            None,
        )
        terminal = next(
            (event for event in reversed(events) if event.type.startswith("run.")), events[-1]
        )
        action_events: list[tuple[Event, str]] = []
        for event in events:
            if event.type != "tool.completed":
                continue
            name = str(event.payload.get("name", ""))
            summary = _compact_inline(event.payload.get("summary", ""), 140)
            if (
                not summary
                or name in _ROUTINE_EPISODE_TOOLS
                or _ROUTINE_EPISODE_SUMMARY.match(summary)
            ):
                continue
            if summary.casefold() not in {value.casefold() for _, value in action_events}:
                action_events.append((event, summary))
            if len(action_events) >= 3:
                break
        failure_events = [
            event
            for event in events
            if event.type
            in {"tool.failed", "tool.rejected", "run.failed", "run.cancelled", "delegation.failed"}
        ][:3]
        failures = _unique_bounded(
            [
                str(event.payload.get("message", event.payload.get("summary", event.type)))
                for event in failure_events
            ],
            count=3,
            length=140,
        )
        request = _compact_block(
            "" if user_event is None else user_event.payload.get("content", ""), 320
        )
        outcome = _compact_block(
            "" if assistant_event is None else assistant_event.payload.get("content", ""), 600
        )
        summary_parts = [f"Request: {_compact_inline(request, 160) or '(unavailable)'}"]
        if outcome:
            summary_parts.append(f"Outcome: {_compact_inline(outcome, 220)}")
        elif action_events:
            summary_parts.append("Changes: " + "; ".join(value for _, value in action_events)[:220])
        if failures:
            summary_parts.append("Issues: " + "; ".join(failures)[:160])
        summary = " ".join(summary_parts)[:500]
        provenance = [
            event.id
            for event in [
                user_event,
                *(event for event, _ in action_events),
                *failure_events,
                assistant_event,
                terminal,
            ]
            if event is not None
        ]
        return _EpisodeProjection(
            summary=summary,
            value=JSON_OBJECT.validate_python(
                {
                    "request": request,
                    "actions": [value for _, value in action_events],
                    "outcome": outcome,
                    "failures": failures,
                    "agents": sorted(
                        {event.agent_id for event in events if event.agent_id is not None}
                    ),
                }
            ),
            provenance_event_ids=list(dict.fromkeys(provenance)),
        )

    def transition(
        self,
        *,
        session: Session,
        memory_id: str,
        action: Literal["accept", "reject", "retract"],
        reason: str,
    ) -> MemoryMutation:
        record = self.get_visible(session, memory_id)
        expected, target, event_type = {
            "accept": ("proposed", "active", "memory.accepted"),
            "reject": ("proposed", "rejected", "memory.rejected"),
            "retract": ("active", "retracted", "memory.retracted"),
        }[action]
        if record.status != expected:
            raise ValueError(f"memory must be {expected} before {action}")
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE memory_records SET status = ?, updated_at = ? WHERE id = ?",
                (target, now, memory_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type=event_type,
                payload={
                    "memory_id": memory_id,
                    "previous_status": expected,
                    "status": target,
                    "reason": reason,
                },
                correlation_id=memory_id,
            )
            connection.commit()
        return MemoryMutation(self.get(memory_id), (event,))

    def delete(self, *, session: Session, memory_id: str, reason: str) -> Event:
        """Permanently remove one visible memory and its retrieval metadata."""

        record = self.get_visible(session, memory_id)
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE memory_records SET superseded_by_id = NULL WHERE superseded_by_id = ?",
                (memory_id,),
            )
            connection.execute("DELETE FROM memory_anchors WHERE memory_id = ?", (memory_id,))
            connection.execute("DELETE FROM memory_provenance WHERE memory_id = ?", (memory_id,))
            connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (memory_id,))
            deleted = connection.execute(
                "DELETE FROM memory_records WHERE id = ?",
                (memory_id,),
            )
            if deleted.rowcount != 1:
                connection.execute("ROLLBACK")
                raise ValueError("memory deletion target changed")
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="memory.deleted",
                payload={
                    "memory_id": memory_id,
                    "previous_status": record.status,
                    "status": "deleted",
                    "reason": reason,
                },
                correlation_id=memory_id,
            )
            connection.commit()
        return event

    def promote(
        self,
        *,
        session: Session,
        memory_id: str,
        visibility: MemoryVisibility,
        causation_id: str,
    ) -> MemoryMutation:
        previous = self.get_visible(session, memory_id)
        if previous.status != "active":
            raise ValueError("only an active memory can be promoted")
        if previous.visibility == visibility:
            raise ValueError("memory already has the requested visibility")
        scope_kinds = {"user", "agent", "workspace", "session_lineage"}
        candidate = MemoryCandidate(
            layer=previous.layer,
            visibility=visibility,
            subject=previous.subject,
            predicate=previous.predicate,
            value=previous.value,
            summary=previous.summary,
            confidence=previous.confidence,
            importance=previous.importance,
            anchors=[item for item in previous.anchors if item.kind not in scope_kinds],
            provenance_event_ids=[*previous.provenance_event_ids, causation_id],
            supersedes_id=previous.id,
            evidence_basis="explicit_user",
            valid_from=previous.valid_from,
            valid_until=previous.valid_until,
        )
        mutation = self.create_candidate(
            session=session,
            candidate=candidate,
            run_id=None,
            origin_kind="explicit",
            activate=True,
            causation_id=causation_id,
        )
        promoted = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="memory.promoted",
            payload=self._record_event_payload(mutation.record.id, candidate, "active"),
            causation_id=mutation.events[-1].id,
            correlation_id=mutation.record.id,
        )
        return MemoryMutation(mutation.record, (*mutation.events, promoted))

    def queue_job(
        self,
        *,
        session: Session,
        kind: Literal["extraction", "explicit_capture"],
        source_event_id: str,
        run_id: str | None,
        content: str | None = None,
    ) -> tuple[MemoryJob, Event]:
        job_id = new_id()
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO memory_jobs(
                    id, kind, status, session_id, run_id, source_event_id,
                    content, attempts, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?, ?, 0, ?, ?)
                """,
                (job_id, kind, session.id, run_id, source_event_id, content, now, now),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="memory.job.queued",
                payload={"job_id": job_id, "kind": kind, "status": "pending", "attempts": 0},
                causation_id=source_event_id,
                correlation_id=job_id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def start_job(self, job_id: str) -> tuple[MemoryJob, Event]:
        job = self.get_job(job_id)
        if job.status not in {"pending", "running"}:
            raise ValueError("memory job is not pending")
        session = self.ledger.get_session(job.session_id)
        now = utc_now()
        attempts = job.attempts + 1
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE memory_jobs SET status = 'running', attempts = ?, updated_at = ?, "
                "error_code = NULL, error_message = NULL WHERE id = ?",
                (attempts, now, job_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=job.session_id,
                run_id=job.run_id,
                agent_id=session.agent_id,
                event_type="memory.job.started",
                payload={
                    "job_id": job.id,
                    "kind": job.kind,
                    "status": "running",
                    "attempts": attempts,
                },
                causation_id=job.source_event_id,
                correlation_id=job.id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def finish_job(
        self,
        job_id: str,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        retry: bool = False,
    ) -> tuple[MemoryJob, Event]:
        job = self.get_job(job_id)
        if job.status != "running":
            raise ValueError("memory job is not running")
        session = self.ledger.get_session(job.session_id)
        status = "pending" if retry else "failed" if error_code else "completed"
        event_type = "memory.job.failed" if error_code else "memory.job.completed"
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE memory_jobs SET status = ?, error_code = ?, error_message = ?, "
                "updated_at = ? WHERE id = ?",
                (status, error_code, error_message, now, job_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=job.session_id,
                run_id=job.run_id,
                agent_id=session.agent_id,
                event_type=event_type,
                payload={
                    "job_id": job.id,
                    "kind": job.kind,
                    "status": status,
                    "attempts": job.attempts,
                    "error_code": error_code,
                    "error_message": error_message,
                },
                causation_id=job.source_event_id,
                correlation_id=job.id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def pause_job(self, job_id: str, *, reason: str) -> tuple[MemoryJob, Event]:
        job = self.get_job(job_id)
        if job.status != "running":
            raise ValueError("memory job is not running")
        session = self.ledger.get_session(job.session_id)
        attempts = max(0, job.attempts - 1)
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE memory_jobs SET status = 'pending', attempts = ?, error_code = NULL, "
                "error_message = NULL, updated_at = ? WHERE id = ?",
                (attempts, now, job_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=job.session_id,
                run_id=job.run_id,
                agent_id=session.agent_id,
                event_type="memory.job.paused",
                payload={
                    "job_id": job.id,
                    "kind": job.kind,
                    "status": "pending",
                    "attempts": attempts,
                    "error_code": "maintenance_preempted",
                    "error_message": reason,
                },
                causation_id=job.source_event_id,
                correlation_id=job.id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def get_job(self, job_id: str) -> MemoryJob:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM memory_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return MemoryJob.model_validate(dict(row))

    def list_jobs(self, session_id: str, *, limit: int = 50) -> list[MemoryJob]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [MemoryJob.model_validate(dict(row)) for row in rows]

    def recover_jobs(self) -> list[MemoryJob]:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE memory_jobs SET status = 'pending', updated_at = ? "
                "WHERE status = 'running'",
                (now,),
            )
            rows = connection.execute(
                "SELECT * FROM memory_jobs WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        return [MemoryJob.model_validate(dict(row)) for row in rows]

    def retry_job(self, session_id: str, job_id: str) -> MemoryJob:
        job = self.get_job(job_id)
        if job.session_id != session_id:
            raise KeyError(job_id)
        if job.status != "failed":
            raise ValueError("only a failed memory job can be retried")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE memory_jobs SET status = 'pending', error_code = NULL, "
                "error_message = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
        return self.get_job(job_id)

    def get(self, memory_id: str) -> MemoryRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise KeyError(memory_id)
            return self._record_from_row(connection, row)

    def get_visible(self, session: Session, memory_id: str) -> MemoryRecord:
        record = self.get(memory_id)
        if not self.is_visible(session, record):
            raise KeyError(memory_id)
        return record

    def list_visible(
        self,
        session: Session,
        *,
        status: MemoryStatus | None = "active",
        layer: MemoryLayer | None = None,
        query: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM memory_records ORDER BY created_at DESC"
            ).fetchall()
            records = [self._record_from_row(connection, row) for row in rows]
        values = [
            record
            for record in records
            if self.is_visible(session, record)
            and (status is None or record.status == status)
            and (layer is None or record.layer == layer)
        ]
        if query.strip():
            ids = self._fts_ids(query, limit=max((offset + limit) * 4, 50))
            position = {memory_id: index for index, memory_id in enumerate(ids)}
            values = [record for record in values if record.id in position]
            values.sort(key=lambda record: (position[record.id], record.id))
        return values[offset : offset + limit]

    def reconcile_semantic(
        self,
        session: Session,
        records: list[MemoryRecord],
        decisions: list[SemanticDecision],
        *,
        causation_id: str,
    ) -> tuple[Event, ...]:
        """Apply a bounded review to unchanged records; retain audit history and provenance."""
        snapshot = {record.id: record for record in records}
        retired = {decision.memory_id for decision in decisions}
        if len(retired) != len(decisions):
            raise ValueError("duplicate semantic review decision")
        for decision in decisions:
            previous = snapshot.get(decision.memory_id)
            replacement = snapshot.get(decision.replacement_id or "")
            if (
                previous is None
                or previous.layer != "semantic"
                or not self.is_visible(session, previous)
            ):
                raise ValueError("semantic review target is outside the supplied scope")
            if decision.reason in {"redundant", "superseded"}:
                if (
                    replacement is None
                    or replacement.id in retired
                    or replacement.layer != "semantic"
                    or memory_scope(previous) != memory_scope(replacement)
                ):
                    raise ValueError("semantic replacement must survive within the same scope")
                if (
                    decision.reason == "superseded"
                    and replacement.created_at <= previous.created_at
                ):
                    raise ValueError("supersession requires newer supporting memory")
            elif decision.replacement_id is not None:
                raise ValueError("retraction must not nominate a replacement")
            elif previous.origin_kind == "explicit":
                raise ValueError("explicitly captured facts require a supported replacement")
            if decision.reason == "expired":
                if not previous.valid_until or datetime.fromisoformat(
                    previous.valid_until
                ) > datetime.now(UTC):
                    raise ValueError("expiry requires an elapsed validity boundary")
        events: list[Event] = []
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for decision in decisions:
                previous = snapshot[decision.memory_id]
                involved = [previous]
                if decision.replacement_id:
                    involved.append(snapshot[decision.replacement_id])
                unchanged = True
                for record in involved:
                    row = connection.execute(
                        "SELECT * FROM memory_records WHERE id = ?", (record.id,)
                    ).fetchone()
                    if (
                        row is None
                        or self._record_from_row(connection, row) != record
                        or record.status != "active"
                    ):
                        unchanged = False
                        break
                if not unchanged:
                    continue
                reason = f"dream_semantic_{decision.reason}: {decision.explanation}"
                if decision.replacement_id:
                    event = self._supersede_on_connection(
                        connection,
                        session,
                        previous.id,
                        decision.replacement_id,
                        None,
                        causation_id,
                        now,
                        reason=reason,
                    )
                else:
                    connection.execute(
                        "UPDATE memory_records SET status = 'retracted', updated_at = ? "
                        "WHERE id = ?",
                        (now, previous.id),
                    )
                    event = self.ledger.append_in_transaction(
                        connection,
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="memory.retracted",
                        payload={
                            "memory_id": previous.id,
                            "previous_status": "active",
                            "status": "retracted",
                            "reason": reason,
                        },
                        causation_id=causation_id,
                        correlation_id=previous.id,
                    )
                events.append(event)
            connection.commit()
        return tuple(events)

    def reconcile_recent(
        self,
        session: Session,
        *,
        since: datetime,
        causation_id: str,
    ) -> tuple[Event, ...]:
        """Reconcile duplicate facts and compact recent episodic projections."""

        records = self.list_visible(session, status="active", limit=10_000)
        groups: dict[tuple[object, ...], list[MemoryRecord]] = {}
        episodes = [record for record in records if record.layer == "episodic"]
        for record in records:
            if record.layer in {"episodic", "semantic"}:
                continue
            key = (
                record.layer,
                record.visibility,
                record.owner_agent_id,
                record.workspace_path,
                record.lineage_root_session_id,
                record.subject.casefold(),
                record.predicate.casefold(),
            )
            groups.setdefault(key, []).append(record)
        replacements: list[tuple[MemoryRecord, MemoryRecord]] = []
        for values in groups.values():
            values.sort(key=lambda record: (record.created_at, record.id), reverse=True)
            newest = values[0]
            if datetime.fromisoformat(newest.created_at) < since:
                continue
            replacements.extend((older, newest) for older in values[1:])
        episode_updates: list[tuple[MemoryRecord, _EpisodeProjection]] = []
        for record in episodes:
            if record.source_run_id is None or datetime.fromisoformat(record.updated_at) < since:
                continue
            projection = self._episode_projection(record.source_run_id)
            if projection is None:
                continue
            if (
                record.summary != projection.summary
                or record.value != projection.value
                or set(record.provenance_event_ids) != set(projection.provenance_event_ids)
            ):
                episode_updates.append((record, projection))
        if not replacements and not episode_updates:
            return ()

        now = utc_now()
        events: list[Event] = []
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for previous, replacement in replacements:
                events.append(
                    self._supersede_on_connection(
                        connection,
                        session,
                        previous.id,
                        replacement.id,
                        None,
                        causation_id,
                        now,
                        reason="idle_dream_reconciliation",
                    )
                )
            for record, projection in episode_updates:
                encoded_value = json.dumps(projection.value, separators=(",", ":"), sort_keys=True)
                connection.execute(
                    "UPDATE memory_records SET summary = ?, value_json = ?, updated_at = ? "
                    "WHERE id = ? AND status = 'active'",
                    (projection.summary, encoded_value, now, record.id),
                )
                connection.execute(
                    "DELETE FROM memory_provenance WHERE memory_id = ?", (record.id,)
                )
                connection.executemany(
                    "INSERT INTO memory_provenance(memory_id, event_id) VALUES (?, ?)",
                    [(record.id, event_id) for event_id in projection.provenance_event_ids],
                )
                connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (record.id,))
                connection.execute(
                    "INSERT INTO memory_fts(memory_id, subject, predicate, summary, value) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.subject,
                        record.predicate,
                        projection.summary,
                        json.dumps(projection.value, sort_keys=True),
                    ),
                )
                events.append(
                    self.ledger.append_in_transaction(
                        connection,
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="memory.episode.projected",
                        payload={
                            "memory_id": record.id,
                            "source_run_id": record.source_run_id,
                            "reason": "idle_dream_compaction",
                        },
                        causation_id=causation_id,
                        correlation_id=record.source_run_id,
                    )
                )
            connection.commit()
        return tuple(events)

    def retrieve(
        self,
        session: Session,
        query: str,
        *,
        limit: int,
        token_budget: int,
    ) -> tuple[list[RetrievedMemory], list[RetrievedMemory], int]:
        eligible = self.list_visible(session, status="active", limit=200)
        fts_ids = self._fts_ids(query, limit=200)
        fts_rank = {memory_id: index for index, memory_id in enumerate(fts_ids)}
        now = datetime.now(UTC)
        ranked: list[RetrievedMemory] = []
        for record in eligible:
            anchor = 1.0 if self._has_direct_anchor(session, record) else 0.0
            rank = fts_rank.get(record.id)
            relevance = 0.0 if rank is None else 1.0 / (1.0 + rank)
            age_days = max(
                0.0,
                (now - datetime.fromisoformat(record.updated_at)).total_seconds() / 86_400,
            )
            recency = 1.0 / (1.0 + age_days / 30.0)
            score = 4 * anchor + 3 * relevance + 2 * record.importance + record.confidence + recency
            encoded = json.dumps(
                _canonical_memory_item(record), separators=(",", ":"), sort_keys=True
            ).encode()
            # Include conservative allowance for the surrounding list and comma.
            tokens = max(1, (len(encoded) + 3) // 4 + 1)
            ranked.append(RetrievedMemory(record=record, score=score, estimated_tokens=tokens))
        ranked.sort(key=lambda item: (-item.score, item.record.id))
        selected: list[RetrievedMemory] = []
        omitted: list[RetrievedMemory] = []
        used = 0
        for item in ranked:
            if len(selected) < limit and used + item.estimated_tokens <= token_budget:
                selected.append(item)
                used += item.estimated_tokens
            else:
                omitted.append(item)
        return selected, omitted, len(eligible)

    def is_visible(self, session: Session, record: MemoryRecord) -> bool:
        if record.visibility == "global":
            return True
        if record.visibility == "agent_private":
            return record.owner_agent_id == session.agent_id
        if record.visibility == "workspace":
            return record.workspace_path == session.working_directory
        return record.lineage_root_session_id == self.lineage_root(session.id)

    def lineage_root(self, session_id: str) -> str:
        current = self.ledger.get_session(session_id)
        seen: set[str] = set()
        while current.parent_session_id is not None:
            if current.id in seen:
                raise ValueError("session lineage cycle")
            seen.add(current.id)
            current = self.ledger.get_session(current.parent_session_id)
        return current.id

    def _validate_candidate(self, session: Session, candidate: MemoryCandidate) -> None:
        if contains_secret(candidate.summary) or contains_secret(json.dumps(candidate.value)):
            raise ValueError("memory candidate resembles a credential or private key")
        visible_ids = {event.id for event in self.ledger.replay(session.id)}
        missing = set(candidate.provenance_event_ids) - visible_ids
        if missing:
            raise ValueError(f"memory provenance is not visible: {sorted(missing)[0]}")
        if candidate.importance < 0.65 and candidate.evidence_basis != "explicit_user":
            raise ValueError("memory candidate is below the importance floor")
        if candidate.confidence < 0.60:
            raise ValueError("memory candidate is below the confidence floor")
        if candidate.supersedes_id is not None:
            previous = self.get_visible(session, candidate.supersedes_id)
            if previous.status != "active":
                raise ValueError("only an active memory can be superseded")

    def _effective_anchors(
        self, session: Session, candidate: MemoryCandidate, lineage_root: str
    ) -> list[MemoryAnchor]:
        values = list(candidate.anchors)
        automatic = {
            "global": MemoryAnchor(kind="user", value="local"),
            "agent_private": MemoryAnchor(kind="agent", value=session.agent_id),
            "workspace": MemoryAnchor(kind="workspace", value=session.working_directory),
            "session_team": MemoryAnchor(kind="session_lineage", value=lineage_root),
        }[candidate.visibility]
        if (automatic.kind, automatic.value) not in {(item.kind, item.value) for item in values}:
            values.append(automatic)
        return values

    def _record_event_payload(
        self, memory_id: str, candidate: MemoryCandidate, status: str
    ) -> dict[str, object]:
        return {
            "memory_id": memory_id,
            "layer": candidate.layer,
            "status": status,
            "visibility": candidate.visibility,
            "summary": candidate.summary,
            "confidence": candidate.confidence,
            "importance": candidate.importance,
            "anchors": [item.model_dump(mode="json") for item in candidate.anchors],
            "provenance_event_ids": candidate.provenance_event_ids,
            "supersedes_id": candidate.supersedes_id,
        }

    def _supersede_on_connection(
        self,
        connection: sqlite3.Connection,
        session: Session,
        previous_id: str,
        replacement_id: str,
        run_id: str | None,
        causation_id: str,
        now: str,
        reason: str = "replacement_accepted",
    ) -> Event:
        cursor = connection.execute(
            "UPDATE memory_records SET status = 'superseded', superseded_by_id = ?, "
            "updated_at = ? WHERE id = ? AND status = 'active'",
            (replacement_id, now, previous_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("memory supersession target changed")
        return self.ledger.append_in_transaction(
            connection,
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="memory.superseded",
            payload={
                "memory_id": previous_id,
                "previous_status": "active",
                "status": "superseded",
                "reason": reason,
                "replacement_id": replacement_id,
            },
            causation_id=causation_id,
            correlation_id=run_id or replacement_id,
        )

    def _record_from_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> MemoryRecord:
        values = dict(row)
        encoded_value = str(values.pop("value_json"))
        values["value"] = JSON_OBJECT.validate_json('{"value":' + encoded_value + "}")["value"]
        values["anchors"] = [
            MemoryAnchor.model_validate(dict(anchor))
            for anchor in connection.execute(
                "SELECT kind, value FROM memory_anchors WHERE memory_id = ? ORDER BY kind, value",
                (values["id"],),
            )
        ]
        values["provenance_event_ids"] = [
            str(event["event_id"])
            for event in connection.execute(
                "SELECT event_id FROM memory_provenance WHERE memory_id = ? ORDER BY event_id",
                (values["id"],),
            )
        ]
        return MemoryRecord.model_validate(values)

    def _fts_ids(self, query: str, *, limit: int) -> list[str]:
        terms = list(dict.fromkeys(_TOKEN.findall(query.casefold())))[:20]
        if not terms:
            return []
        expression = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT memory_id FROM memory_fts WHERE memory_fts MATCH ? "
                "ORDER BY bm25(memory_fts), memory_id LIMIT ?",
                (expression, limit),
            ).fetchall()
        return [str(row["memory_id"]) for row in rows]

    def _has_direct_anchor(self, session: Session, record: MemoryRecord) -> bool:
        expected = {
            ("user", "local"),
            ("agent", session.agent_id),
            ("workspace", session.working_directory),
            ("session_lineage", self.lineage_root(session.id)),
        }
        return any((anchor.kind, anchor.value) in expected for anchor in record.anchors)


def retrieval_query_hash(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()


def should_auto_activate(candidate: MemoryCandidate, *, explicit: bool = False) -> bool:
    if explicit:
        return True
    if candidate.evidence_basis not in {"explicit_user", "successful_tool"}:
        return False
    return candidate.confidence >= 0.85 and candidate.importance >= 0.70


def canonical_memory_context(items: list[RetrievedMemory]) -> str:
    payload = [_canonical_memory_item(item.record) for item in items]
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _canonical_memory_item(record: MemoryRecord) -> dict[str, JsonValue]:
    return {
        "id": record.id,
        "layer": record.layer,
        "summary": record.summary,
        "subject": record.subject,
        "predicate": record.predicate,
        "value": record.value,
        "provenance_event_ids": cast(list[JsonValue], record.provenance_event_ids),
    }
