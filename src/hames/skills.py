"""Immutable, scoped Skill packages and autonomous version lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hames.ledger import Event, Ledger, Session, new_id, utc_now
from hames.providers.base import JSON_OBJECT, JsonValue

SKILL_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")
TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]+")
SkillScope = Literal["global", "workspace", "agent"]
SkillStatus = Literal[
    "draft",
    "verified",
    "active",
    "stale",
    "archived",
    "rejected",
    "quarantined",
    "superseded",
]


class SkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillScript(SkillModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    path: str
    interpreter: Literal["python", "bash"]
    description: str = Field(min_length=1, max_length=300)

    @field_validator("path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        path = PurePath(value)
        if path.is_absolute() or ".." in path.parts or not value.startswith("scripts/"):
            raise ValueError("Skill script path must remain below scripts/")
        suffix = ".py" if value.endswith(".py") else ".sh" if value.endswith(".sh") else ""
        if not suffix:
            raise ValueError("Skill scripts must end in .py or .sh")
        return value


def _empty_scripts() -> list[SkillScript]:
    return []


class SkillMetadata(SkillModel):
    id: str
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    version: int = Field(default=1, ge=1)
    scope: str = "workspace"
    tools: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list, max_length=20)
    requires: list[str] = Field(default_factory=list, max_length=20)
    scripts: list[SkillScript] = Field(default_factory=_empty_scripts, max_length=16)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if SKILL_ID.fullmatch(value) is None:
            raise ValueError("Skill ID must match [a-z][a-z0-9-]{0,62}")
        return value

    @field_validator("scope")
    @classmethod
    def valid_scope(cls, value: str) -> str:
        if value in {"global", "workspace"}:
            return value
        if value.startswith("agent:") and SKILL_ID.fullmatch(value.removeprefix("agent:")):
            return value
        raise ValueError("Skill scope must be global, workspace, or agent:<id>")

    @model_validator(mode="after")
    def unique_lists(self) -> SkillMetadata:
        if len(self.tools) != len(set(self.tools)):
            raise ValueError("Skill tools must be unique")
        if len(self.triggers) != len(set(self.triggers)):
            raise ValueError("Skill triggers must be unique")
        script_ids = [item.id for item in self.scripts]
        script_paths = [item.path for item in self.scripts]
        if len(script_ids) != len(set(script_ids)) or len(script_paths) != len(set(script_paths)):
            raise ValueError("Skill scripts must have unique IDs and paths")
        return self


class SkillDraft(SkillModel):
    id: str
    name: str
    description: str
    scope: str = "workspace"
    tools: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    instructions: str = Field(min_length=1, max_length=32_000)
    scripts: list[SkillScript] = Field(default_factory=_empty_scripts)
    files: dict[str, str] = Field(default_factory=dict)
    rationale: str = ""


class SkillVersion(SkillModel):
    id: str
    skill_id: str
    slug: str
    version: int
    content_hash: str
    status: SkillStatus
    scope: SkillScope
    scope_key: str | None
    name: str
    description: str
    instructions: str
    metadata: SkillMetadata
    package_path: str
    base_version_id: str | None
    created_by: str
    source_session_id: str
    source_run_id: str | None
    created_at: str
    activated_at: str | None
    last_used_at: str | None
    pinned: bool = False


class SkillSummary(SkillModel):
    id: str
    slug: str
    version_id: str
    version: int
    name: str
    description: str
    scope: SkillScope
    scope_key: str | None
    status: SkillStatus
    content_hash: str
    triggers: list[str]
    tools: list[str]
    scripts: list[SkillScript]
    score: float = 0.0
    pinned: bool = False


class SkillJob(SkillModel):
    id: str
    kind: Literal["author", "patch", "revalidate"]
    status: Literal["pending", "running", "completed", "failed", "cancelled", "budget_wait"]
    session_id: str
    run_id: str | None
    source_event_id: str
    target_skill_id: str | None
    goal: str
    scope: SkillScope
    attempts: int
    error_code: str | None
    error_message: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class SkillMutation:
    version: SkillVersion
    events: tuple[Event, ...]


@dataclass(frozen=True, slots=True)
class SkillUsageFailure:
    version_id: str
    skill_id: str
    failures: int


def parse_skill(raw: str) -> tuple[SkillMetadata, str]:
    lines = raw.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md must begin with YAML frontmatter")
    try:
        boundary = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError("SKILL.md has unterminated YAML frontmatter") from exc
    raw_metadata = cast(object, yaml.safe_load("\n".join(lines[1:boundary])))
    metadata = SkillMetadata.model_validate(raw_metadata)
    instructions = "\n".join(lines[boundary + 1 :]).strip()
    if not instructions:
        raise ValueError("Skill instructions cannot be empty")
    return metadata, instructions


def render_skill(metadata: SkillMetadata, instructions: str) -> str:
    payload = metadata.model_dump(mode="json", exclude_none=True)
    header = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n{instructions.strip()}\n"


class SkillRegistry:
    def __init__(
        self,
        root: Path,
        ledger: Ledger,
        *,
        available_tools: set[str],
        max_package_bytes: int = 262_144,
        max_package_files: int = 64,
    ) -> None:
        self.root = root
        self.ledger = ledger
        self.database = ledger.database
        self.available_tools = available_tools | {"skill_load", "skill_run", "skill_author"}
        self.max_package_bytes = max_package_bytes
        self.max_package_files = max_package_files

    def create_draft(
        self,
        *,
        session: Session,
        draft: SkillDraft,
        evidence_event_ids: list[str],
        created_by: Literal["automatic", "agent", "user", "import"],
        run_id: str | None,
        causation_id: str,
        target_skill_id: str | None = None,
    ) -> SkillMutation:
        metadata = SkillMetadata(
            id=draft.id,
            name=draft.name,
            description=draft.description,
            scope=draft.scope,
            tools=draft.tools,
            triggers=draft.triggers,
            requires=draft.requires,
            scripts=draft.scripts,
        )
        self._validate_metadata(metadata, draft.files)
        visible = {event.id for event in self.ledger.replay(session.id)}
        if not evidence_event_ids or not set(evidence_event_ids) <= visible:
            raise ValueError("Skill evidence must be non-empty and visible to the source session")
        scope, scope_key = self._scope(metadata.scope, session)
        skill_id = target_skill_id or self._entity_for(session, metadata.id, scope, scope_key)
        if target_skill_id is not None:
            entity = self._entity(target_skill_id)
            entity_scope = str(entity["scope"])
            entity_key = None if entity["scope_key"] is None else str(entity["scope_key"])
            if str(entity["slug"]) != metadata.id:
                raise ValueError("Skill correction cannot change ID")
            if entity_scope != scope or entity_key != scope_key:
                raise ValueError("Skill correction cannot change scope")
        base = self.active_version(skill_id) if target_skill_id else None
        version_number = self._next_version(skill_id)
        metadata = metadata.model_copy(update={"version": version_number})
        files = dict(draft.files)
        files["SKILL.md"] = render_skill(metadata, draft.instructions)
        if sum(len(value.encode()) for value in files.values()) > self.max_package_bytes:
            raise ValueError("Skill package exceeds configured size limit")
        content_hash = _package_hash(files)
        version_id = new_id()
        package_path = (
            self.root / "packages" / metadata.id / f"{version_number:04d}-{content_hash[:12]}"
        )
        self._write_package(package_path, files)
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if target_skill_id is None:
                connection.execute(
                    "INSERT OR IGNORE INTO skills("
                    "id, slug, scope, scope_key, created_at, updated_at"
                    ") VALUES (?, ?, ?, ?, ?, ?)",
                    (skill_id, metadata.id, scope, scope_key, now, now),
                )
            connection.execute(
                """
                INSERT INTO skill_versions(
                    id, skill_id, version, content_hash, status, name, description,
                    instructions, metadata_json, package_path, base_version_id,
                    created_by, source_session_id, source_run_id, created_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version_id,
                    skill_id,
                    version_number,
                    content_hash,
                    metadata.name,
                    metadata.description,
                    draft.instructions.strip(),
                    json.dumps(metadata.model_dump(mode="json"), separators=(",", ":")),
                    str(package_path),
                    base.id if base else None,
                    created_by,
                    session.id,
                    run_id,
                    now,
                ),
            )
            for event_id in evidence_event_ids:
                connection.execute(
                    "INSERT INTO skill_evidence(version_id, event_id) VALUES (?, ?)",
                    (version_id, event_id),
                )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="skill.drafted",
                payload={
                    "skill_id": skill_id,
                    "version_id": version_id,
                    "slug": metadata.id,
                    "version": version_number,
                    "content_hash": content_hash,
                    "scope": scope,
                    "status": "draft",
                    "evidence_event_ids": evidence_event_ids,
                },
                causation_id=causation_id,
                correlation_id=version_id,
            )
            connection.commit()
        return SkillMutation(self.get(version_id), (event,))

    def activate(
        self,
        *,
        session: Session,
        version_id: str,
        causation_id: str,
        reason: str = "autonomous_validation_passed",
    ) -> SkillMutation:
        candidate = self.get_visible_version(session, version_id)
        if candidate.status not in {"draft", "verified", "stale", "superseded"}:
            raise ValueError("Skill version is not activatable")
        current = self.active_version(candidate.skill_id)
        entity = self._entity(candidate.skill_id)
        if entity["pinned_version_id"] is not None and entity["pinned_version_id"] != version_id:
            raise ValueError("Skill is pinned to another version")
        now = utc_now()
        events: list[Event] = []
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            active_row = connection.execute(
                "SELECT active_version_id FROM skills WHERE id = ?",
                (candidate.skill_id,),
            ).fetchone()
            active_id = None if active_row is None else active_row["active_version_id"]
            if current is None:
                if candidate.base_version_id is not None and reason != "automatic_rollback":
                    raise ValueError("Skill base version is no longer active")
            elif current.id != version_id and candidate.base_version_id != current.id:
                raise ValueError("Skill base version changed; draft a new correction")
            if active_id != (None if current is None else current.id):
                raise ValueError("Skill base version changed during activation")
            if current is not None and current.id != version_id:
                connection.execute(
                    "UPDATE skill_versions SET status = 'superseded' WHERE id = ?",
                    (current.id,),
                )
                events.append(
                    self.ledger.append_in_transaction(
                        connection,
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="skill.superseded",
                        payload={
                            "skill_id": candidate.skill_id,
                            "version_id": current.id,
                            "replacement_version_id": version_id,
                            "reason": reason,
                        },
                        causation_id=causation_id,
                        correlation_id=version_id,
                    )
                )
            connection.execute("DELETE FROM skill_fts WHERE version_id = ?", (version_id,))
            connection.execute(
                "UPDATE skill_versions SET status = 'active', activated_at = ? WHERE id = ?",
                (now, version_id),
            )
            connection.execute(
                "UPDATE skills SET active_version_id = ?, archived = 0, "
                "updated_at = ? WHERE id = ?",
                (version_id, now, candidate.skill_id),
            )
            connection.execute(
                "INSERT INTO skill_fts("
                "version_id, slug, name, description, triggers, instructions"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    version_id,
                    candidate.slug,
                    candidate.name,
                    candidate.description,
                    " ".join(candidate.metadata.triggers),
                    candidate.instructions,
                ),
            )
            events.append(
                self.ledger.append_in_transaction(
                    connection,
                    session_id=session.id,
                    agent_id=session.agent_id,
                    event_type="skill.activated",
                    payload={
                        "skill_id": candidate.skill_id,
                        "version_id": version_id,
                        "slug": candidate.slug,
                        "version": candidate.version,
                        "content_hash": candidate.content_hash,
                        "scope": candidate.scope,
                        "status": "active",
                        "reason": reason,
                    },
                    causation_id=causation_id,
                    correlation_id=version_id,
                )
            )
            connection.commit()
        return SkillMutation(self.get(version_id), tuple(events))

    def record_evaluation(
        self,
        version_id: str,
        *,
        kind: Literal["deterministic", "model", "script"],
        passed: bool,
        score: float,
        report: dict[str, JsonValue],
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO skill_evaluations(id, version_id, kind, status, score, report_json, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    version_id,
                    kind,
                    "passed" if passed else "failed",
                    score,
                    json.dumps(report, separators=(",", ":"), sort_keys=True),
                    utc_now(),
                ),
            )

    def observe_workflow(
        self,
        *,
        session: Session,
        run_id: str,
        task_text: str,
        tool_sequence: list[str],
        outcome: Literal["completed", "failed", "cancelled"],
        causation_id: str,
        similarity_threshold: float,
    ) -> tuple[Event, list[str]]:
        encoded_tools = json.dumps(tool_sequence, separators=(",", ":"))
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "agent": session.agent_id,
                    "task": _tokens(task_text),
                    "tools": tool_sequence,
                    "workspace": session.working_directory,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT run_id, task_text FROM workflow_signatures "
                "WHERE workspace_path = ? AND agent_id = ? AND tool_sequence_json = ? "
                "AND outcome = 'completed' ORDER BY created_at DESC LIMIT 50",
                (session.working_directory, session.agent_id, encoded_tools),
            ).fetchall()
            similar = [
                str(row["run_id"])
                for row in rows
                if task_similarity(task_text, str(row["task_text"])) >= similarity_threshold
            ]
            connection.execute(
                "INSERT OR IGNORE INTO workflow_signatures("
                "run_id, session_id, agent_id, workspace_path, task_text, task_tokens_json, "
                "tool_sequence_json, fingerprint, outcome, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    session.id,
                    session.agent_id,
                    session.working_directory,
                    task_text,
                    json.dumps(_tokens(task_text), separators=(",", ":")),
                    encoded_tools,
                    fingerprint,
                    outcome,
                    utc_now(),
                ),
            )
        event = self.ledger.append(
            session_id=session.id,
            run_id=run_id,
            agent_id=session.agent_id,
            event_type="skill.workflow.observed",
            payload={
                "run_id": run_id,
                "fingerprint": fingerprint,
                "tool_sequence": tool_sequence,
                "outcome": outcome,
                "similar_run_ids": similar,
            },
            causation_id=causation_id,
            correlation_id=run_id,
        )
        return event, similar

    def queue_job(
        self,
        *,
        session: Session,
        kind: Literal["author", "patch", "revalidate"],
        source_event_id: str,
        run_id: str | None,
        goal: str,
        scope: SkillScope,
        target_skill_id: str | None = None,
    ) -> tuple[SkillJob, Event]:
        job_id = new_id()
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO skill_jobs("
                "id, kind, status, session_id, run_id, source_event_id, target_skill_id, "
                "goal, scope, attempts, created_at, updated_at"
                ") VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                (
                    job_id,
                    kind,
                    session.id,
                    run_id,
                    source_event_id,
                    target_skill_id,
                    goal,
                    scope,
                    now,
                    now,
                ),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="skill.job.queued",
                payload={
                    "job_id": job_id,
                    "kind": kind,
                    "status": "pending",
                    "attempts": 0,
                    "target_skill_id": target_skill_id,
                },
                causation_id=source_event_id,
                correlation_id=job_id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def start_job(self, job_id: str) -> tuple[SkillJob, Event]:
        job = self.get_job(job_id)
        if job.status not in {"pending", "running", "budget_wait"}:
            raise ValueError("Skill job is not pending")
        session = self.ledger.get_session(job.session_id)
        attempts = job.attempts + 1
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE skill_jobs SET status = 'running', attempts = ?, updated_at = ?, "
                "error_code = NULL, error_message = NULL WHERE id = ?",
                (attempts, utc_now(), job_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=job.session_id,
                run_id=job.run_id,
                agent_id=session.agent_id,
                event_type="skill.job.started",
                payload={
                    "job_id": job.id,
                    "kind": job.kind,
                    "status": "running",
                    "attempts": attempts,
                    "target_skill_id": job.target_skill_id,
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
        budget_wait: bool = False,
    ) -> tuple[SkillJob, Event]:
        job = self.get_job(job_id)
        if job.status != "running":
            raise ValueError("Skill job is not running")
        session = self.ledger.get_session(job.session_id)
        status = (
            "budget_wait"
            if budget_wait
            else "pending"
            if retry
            else "failed"
            if error_code
            else "completed"
        )
        event_type = "skill.job.failed" if error_code else "skill.job.completed"
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE skill_jobs SET status = ?, error_code = ?, error_message = ?, "
                "updated_at = ? WHERE id = ?",
                (status, error_code, error_message, utc_now(), job_id),
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
                    "target_skill_id": job.target_skill_id,
                    "error_code": error_code,
                    "error_message": error_message,
                },
                causation_id=job.source_event_id,
                correlation_id=job.id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def pause_job(self, job_id: str, *, reason: str) -> tuple[SkillJob, Event]:
        job = self.get_job(job_id)
        if job.status != "running":
            raise ValueError("Skill job is not running")
        session = self.ledger.get_session(job.session_id)
        attempts = max(0, job.attempts - 1)
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE skill_jobs SET status = 'pending', attempts = ?, error_code = NULL, "
                "error_message = NULL, updated_at = ? WHERE id = ?",
                (attempts, utc_now(), job_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=job.session_id,
                run_id=job.run_id,
                agent_id=session.agent_id,
                event_type="skill.job.paused",
                payload={
                    "job_id": job.id,
                    "kind": job.kind,
                    "status": "pending",
                    "attempts": attempts,
                    "target_skill_id": job.target_skill_id,
                    "error_code": "maintenance_preempted",
                    "error_message": reason,
                },
                causation_id=job.source_event_id,
                correlation_id=job.id,
            )
            connection.commit()
        return self.get_job(job_id), event

    def get_job(self, job_id: str) -> SkillJob:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM skill_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return SkillJob.model_validate(dict(row))

    def list_jobs(self, session_id: str, *, limit: int = 50) -> list[SkillJob]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM skill_jobs WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [SkillJob.model_validate(dict(row)) for row in rows]

    def recover_jobs(self) -> list[SkillJob]:
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE skill_jobs SET status = 'pending', updated_at = ? WHERE status = 'running'",
                (utc_now(),),
            )
            rows = connection.execute(
                "SELECT * FROM skill_jobs WHERE status = 'pending' ORDER BY created_at"
            ).fetchall()
        return [SkillJob.model_validate(dict(row)) for row in rows]

    def retry_job(self, session_id: str, job_id: str) -> SkillJob:
        job = self.get_job(job_id)
        if job.session_id != session_id:
            raise KeyError(job_id)
        if job.status not in {"failed", "budget_wait"}:
            raise ValueError("only a failed or budget-waiting Skill job can be retried")
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE skill_jobs SET status = 'pending', error_code = NULL, "
                "error_message = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), job_id),
            )
        return self.get_job(job_id)

    def background_model_calls_today(self) -> int:
        start = datetime.now(UTC).date().isoformat()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT count(*) AS value FROM events WHERE type = 'model.requested' "
                "AND created_at >= ? AND payload_json IS NOT NULL "
                "AND json_extract(payload_json, '$.purpose') IN "
                "('skill_authoring', 'skill_evaluation')",
                (start,),
            ).fetchone()
        return int(row["value"])

    def repeated_failure_versions(self, threshold: int) -> list[SkillUsageFailure]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT v.id AS version_id, v.skill_id, count(*) AS failures
                FROM skill_usage u
                JOIN skill_versions v ON v.id = u.version_id
                WHERE u.correction = 1 OR u.outcome = 'failed'
                GROUP BY v.id, v.skill_id
                HAVING count(*) >= ?
                ORDER BY failures DESC, v.id
                """,
                (threshold,),
            ).fetchall()
        return [
            SkillUsageFailure(
                version_id=str(row["version_id"]),
                skill_id=str(row["skill_id"]),
                failures=int(row["failures"]),
            )
            for row in rows
        ]

    def record_usage(
        self,
        *,
        version_id: str,
        run_id: str,
        session_id: str,
        stage: Literal["catalogued", "loaded", "executed"],
    ) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO skill_usage("
                "version_id, run_id, session_id, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (version_id, run_id, session_id, now, now),
            )
            connection.execute(
                f"UPDATE skill_usage SET {stage} = 1, updated_at = ? "
                "WHERE version_id = ? AND run_id = ?",
                (now, version_id, run_id),
            )
            if stage in {"loaded", "executed"}:
                connection.execute(
                    "UPDATE skill_versions SET last_used_at = ? WHERE id = ?",
                    (now, version_id),
                )

    def record_run_outcomes(
        self,
        *,
        session: Session,
        run_id: str,
        outcome: str,
        tool_calls: int,
        correction: bool,
        causation_id: str,
    ) -> list[Event]:
        now = utc_now()
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT version_id FROM skill_usage WHERE run_id = ? "
                "AND (loaded = 1 OR executed = 1)",
                (run_id,),
            ).fetchall()
            connection.execute(
                "UPDATE skill_usage SET outcome = ?, tool_calls = ?, correction = ?, "
                "updated_at = ? WHERE run_id = ?",
                (outcome, tool_calls, int(correction), now, run_id),
            )
        events: list[Event] = []
        for row in rows:
            version = self.get(str(row["version_id"]))
            events.append(
                self.ledger.append(
                    session_id=session.id,
                    run_id=run_id,
                    agent_id=session.agent_id,
                    event_type="skill.outcome.recorded",
                    payload={
                        "skill_id": version.skill_id,
                        "version_id": version.id,
                        "run_id": run_id,
                        "outcome": outcome,
                        "tool_calls": tool_calls,
                        "correction": correction,
                    },
                    causation_id=causation_id,
                    correlation_id=run_id,
                )
            )
        return events

    def reject(self, session: Session, version_id: str, *, reason: str, causation_id: str) -> Event:
        candidate = self.get_visible_version(session, version_id)
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE skill_versions SET status = 'rejected' WHERE id = ?", (version_id,)
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="skill.rejected",
                payload={
                    "skill_id": candidate.skill_id,
                    "version_id": version_id,
                    "reason": reason,
                },
                causation_id=causation_id,
                correlation_id=version_id,
            )
            connection.commit()
        return event

    def quarantine_and_rollback(
        self, session: Session, version_id: str, *, reason: str, causation_id: str
    ) -> tuple[SkillVersion, tuple[Event, ...]]:
        current = self.get_visible_version(session, version_id)
        if current.status != "active":
            raise ValueError("only the active Skill version can be quarantined")
        history = [item for item in self.history(current.skill_id) if item.id != current.id]
        fallback = next(
            (item for item in history if item.status in {"superseded", "verified", "stale"}), None
        )
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE skill_versions SET status = 'quarantined' WHERE id = ?", (current.id,)
            )
            connection.execute(
                "UPDATE skills SET active_version_id = NULL, updated_at = ? WHERE id = ?",
                (utc_now(), current.skill_id),
            )
            quarantined = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="skill.quarantined",
                payload={
                    "skill_id": current.skill_id,
                    "version_id": current.id,
                    "reason": reason,
                },
                causation_id=causation_id,
                correlation_id=current.id,
            )
            connection.commit()
        if fallback is None:
            return self.get(current.id), (quarantined,)
        activated = self.activate(
            session=session,
            version_id=fallback.id,
            causation_id=quarantined.id,
            reason="automatic_rollback",
        )
        rolled = self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="skill.rolled_back",
            payload={
                "skill_id": current.skill_id,
                "from_version_id": current.id,
                "to_version_id": fallback.id,
                "reason": reason,
            },
            causation_id=activated.events[-1].id,
            correlation_id=current.id,
        )
        return activated.version, (quarantined, *activated.events, rolled)

    def visible(self, session: Session, *, query: str = "", limit: int = 50) -> list[SkillSummary]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT v.*, s.slug, s.scope, s.scope_key, s.pinned_version_id
                FROM skills s JOIN skill_versions v ON v.id = s.active_version_id
                WHERE s.archived = 0 AND v.status = 'active'
                ORDER BY v.activated_at DESC, s.slug
                """
            ).fetchall()
        values = [self._version_from_row(row) for row in rows]
        values = [item for item in values if self._visible(session, item)]
        query_tokens = set(_tokens(query))
        summaries: list[SkillSummary] = []
        for item in values:
            haystack = set(
                _tokens(" ".join([item.slug, item.name, item.description, *item.metadata.triggers]))
            )
            score = 0.0 if not query_tokens else len(query_tokens & haystack) / len(query_tokens)
            summaries.append(self.summary(item, score=score))
        if query_tokens:
            summaries = [item for item in summaries if item.score > 0]
            summaries.sort(key=lambda item: (-item.score, item.slug, item.version))
        return summaries[:limit]

    def get_visible(self, session: Session, slug: str) -> SkillVersion:
        matches = [item for item in self.visible(session) if item.slug == slug]
        if len(matches) != 1:
            raise KeyError(slug)
        return self.get(matches[0].version_id)

    def get_visible_version(self, session: Session, version_id: str) -> SkillVersion:
        value = self.get(version_id)
        if not self._visible(session, value):
            raise KeyError(version_id)
        return value

    def get(self, version_id: str) -> SkillVersion:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT v.*, s.slug, s.scope, s.scope_key, s.pinned_version_id "
                "FROM skill_versions v JOIN skills s ON s.id = v.skill_id WHERE v.id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        value = self._version_from_row(row)
        if _hash_directory(Path(value.package_path)) != value.content_hash:
            raise ValueError(f"Skill package hash mismatch: {version_id}")
        return value

    def active_version(self, skill_id: str) -> SkillVersion | None:
        entity = self._entity(skill_id)
        version_id = entity["active_version_id"]
        return None if version_id is None else self.get(str(version_id))

    def history(self, skill_id: str) -> list[SkillVersion]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT v.*, s.slug, s.scope, s.scope_key, s.pinned_version_id "
                "FROM skill_versions v JOIN skills s ON s.id = v.skill_id "
                "WHERE v.skill_id = ? ORDER BY v.version DESC",
                (skill_id,),
            ).fetchall()
        return [self._version_from_row(row) for row in rows]

    def summary(self, version: SkillVersion, *, score: float = 0.0) -> SkillSummary:
        return SkillSummary(
            id=version.skill_id,
            slug=version.slug,
            version_id=version.id,
            version=version.version,
            name=version.name,
            description=version.description,
            scope=version.scope,
            scope_key=version.scope_key,
            status=version.status,
            content_hash=version.content_hash,
            triggers=version.metadata.triggers,
            tools=version.metadata.tools,
            scripts=version.metadata.scripts,
            score=score,
            pinned=version.pinned,
        )

    def set_pinned(self, session: Session, slug: str, *, pinned: bool) -> SkillVersion:
        version = self.get_visible(session, slug)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE skills SET pinned_version_id = ?, updated_at = ? WHERE id = ?",
                (version.id if pinned else None, utc_now(), version.skill_id),
            )
        return self.get(version.id)

    def set_archived(self, session: Session, slug: str, *, archived: bool) -> SkillVersion:
        version = (
            self.get_visible(session, slug) if archived else self._latest_visible(session, slug)
        )
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE skills SET archived = ?, updated_at = ? WHERE id = ?",
                (int(archived), utc_now(), version.skill_id),
            )
        return self.get(version.id)

    def evidence(self, version_id: str) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT event_id FROM skill_evidence WHERE version_id = ? ORDER BY event_id",
                (version_id,),
            ).fetchall()
        return [str(row["event_id"]) for row in rows]

    def _latest_visible(self, session: Session, slug: str) -> SkillVersion:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT v.*, s.slug, s.scope, s.scope_key, s.pinned_version_id "
                "FROM skill_versions v JOIN skills s ON s.id = v.skill_id "
                "WHERE s.slug = ? ORDER BY v.version DESC",
                (slug,),
            ).fetchall()
        values = [self._version_from_row(row) for row in rows]
        match = next((item for item in values if self._visible(session, item)), None)
        if match is None:
            raise KeyError(slug)
        return match

    def latest_visible(self, session: Session, slug: str) -> SkillVersion:
        return self._latest_visible(session, slug)

    def _validate_metadata(self, metadata: SkillMetadata, files: dict[str, str]) -> None:
        unknown = set(metadata.tools) - self.available_tools
        if unknown:
            raise ValueError(f"Skill declares unknown tool: {sorted(unknown)[0]}")
        if len(files) + 1 > self.max_package_files:
            raise ValueError("Skill package has too many files")
        total = sum(len(value.encode()) for value in files.values())
        if total > self.max_package_bytes:
            raise ValueError("Skill package exceeds configured size limit")
        for path, content in files.items():
            relative = PurePath(path)
            if relative.is_absolute() or ".." in relative.parts or path == "SKILL.md":
                raise ValueError(f"invalid Skill package path: {path}")
            if "\x00" in content:
                raise ValueError("Skill package files must be text")
        for script in metadata.scripts:
            if script.path not in files:
                raise ValueError(f"Skill script is missing: {script.path}")

    def _write_package(self, root: Path, files: dict[str, str]) -> None:
        if root.exists():
            raise FileExistsError(root)
        root.mkdir(mode=0o700, parents=True)
        for relative, content in sorted(files.items()):
            path = root.joinpath(*PurePath(relative).parts)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

    def _entity_for(
        self, session: Session, slug: str, scope: SkillScope, scope_key: str | None
    ) -> str:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchall()
        for row in rows:
            existing_scope = str(row["scope"])
            existing_key = None if row["scope_key"] is None else str(row["scope_key"])
            if existing_scope == scope and existing_key == scope_key:
                return str(row["id"])
            if existing_scope == "global" or scope == "global":
                raise ValueError(f"Skill ID overlaps an existing visible scope: {slug}")
            if existing_scope == "workspace" and existing_key == session.working_directory:
                raise ValueError(f"Skill ID overlaps an existing visible scope: {slug}")
            if existing_scope == "agent" and existing_key == session.agent_id:
                raise ValueError(f"Skill ID overlaps an existing visible scope: {slug}")
        return new_id()

    @staticmethod
    def _scope(raw: str, session: Session) -> tuple[SkillScope, str | None]:
        if raw == "global":
            return "global", None
        if raw == "workspace":
            return "workspace", session.working_directory
        agent_id = raw.removeprefix("agent:")
        if agent_id != session.agent_id:
            raise ValueError("An agent-scoped Skill must belong to the source session agent")
        return "agent", agent_id

    @staticmethod
    def _visible(session: Session, value: SkillVersion) -> bool:
        return (
            value.scope == "global"
            or (value.scope == "workspace" and value.scope_key == session.working_directory)
            or (value.scope == "agent" and value.scope_key == session.agent_id)
        )

    def _next_version(self, skill_id: str) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS value "
                "FROM skill_versions WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()
        return int(row["value"])

    def _entity(self, skill_id: str) -> dict[str, object]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        if row is None:
            raise KeyError(skill_id)
        return dict(row)

    @staticmethod
    def _version_from_row(row: sqlite3.Row) -> SkillVersion:
        values: dict[str, object] = dict(row)
        metadata_json = str(values["metadata_json"])
        metadata = SkillMetadata.model_validate(JSON_OBJECT.validate_json(metadata_json))
        return SkillVersion(
            id=str(values["id"]),
            skill_id=str(values["skill_id"]),
            slug=str(values["slug"]),
            version=int(str(values["version"])),
            content_hash=str(values["content_hash"]),
            status=cast(SkillStatus, values["status"]),
            scope=cast(SkillScope, values["scope"]),
            scope_key=None if values["scope_key"] is None else str(values["scope_key"]),
            name=str(values["name"]),
            description=str(values["description"]),
            instructions=str(values["instructions"]),
            metadata=metadata,
            package_path=str(values["package_path"]),
            base_version_id=(
                None if values["base_version_id"] is None else str(values["base_version_id"])
            ),
            created_by=str(values["created_by"]),
            source_session_id=str(values["source_session_id"]),
            source_run_id=(
                None if values["source_run_id"] is None else str(values["source_run_id"])
            ),
            created_at=str(values["created_at"]),
            activated_at=(None if values["activated_at"] is None else str(values["activated_at"])),
            last_used_at=(None if values["last_used_at"] is None else str(values["last_used_at"])),
            pinned=values.get("pinned_version_id") == values["id"],
        )


def _tokens(value: str) -> list[str]:
    return list(dict.fromkeys(TOKEN.findall(value.casefold())))


def task_similarity(first: str, second: str) -> float:
    left, right = set(_tokens(first)), set(_tokens(second))
    return 0.0 if not left or not right else len(left & right) / len(left | right)


def _package_hash(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(content.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_directory(root: Path) -> str:
    files = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    return _package_hash(files)
