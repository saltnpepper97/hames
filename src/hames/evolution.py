"""Durable scars, repair candidates, and regression protection."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hames.ledger import Event, Ledger, Session, new_id, utc_now
from hames.providers.base import JSON_OBJECT, JsonValue

ScarStatus = Literal[
    "candidate", "open", "repair_proposed", "guarded", "healed", "regressed", "dismissed"
]
ScarSeverity = Literal["low", "medium", "high"]
ScarScope = Literal["global", "workspace", "agent"]
RepairLayer = Literal[
    "semantic_memory",
    "relationship_memory",
    "episodic_memory",
    "skill",
    "policy_rule",
    "context_rule",
    "capability_requirement",
]
RepairAuthority = Literal[
    "none", "memory_write", "skill_write", "policy_write", "context_write", "plugin_write"
]

REPAIR_LAYERS: tuple[str, ...] = (
    "semantic_memory",
    "relationship_memory",
    "episodic_memory",
    "skill",
    "policy_rule",
    "context_rule",
    "capability_requirement",
)

TRANSITIONS: dict[str, frozenset[str]] = {
    "candidate": frozenset({"open", "dismissed"}),
    "open": frozenset({"repair_proposed", "dismissed"}),
    "repair_proposed": frozenset({"guarded", "open", "regressed", "dismissed"}),
    "guarded": frozenset({"healed", "regressed"}),
    "healed": frozenset({"regressed"}),
    "regressed": frozenset({"repair_proposed", "dismissed"}),
    "dismissed": frozenset(),
}

_SIGNATURE_NOISE = re.compile(r"\s+")
_SUMMARY_NOISE = re.compile(r"[\d\x80-\xff]+")
_CORRECTION_MARKERS = (
    "actually",
    "that was wrong",
    "that's wrong",
    "you were wrong",
    "correct that",
    "that is incorrect",
    "fix that",
    "do this instead",
    "not like that",
    "next time",
    "from now on",
)


def looks_like_correction(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _CORRECTION_MARKERS)


def normalize_failure_signature(event: Event) -> str | None:
    """Reduce a runtime event to a stable comparable failure signature."""
    if event.type in {"tool.failed", "tool.rejected"}:
        name = str(event.payload.get("name", "")).strip()
        summary = str(event.payload.get("summary", ""))
        normalized = _SUMMARY_NOISE.sub("#", summary.strip().casefold())
        normalized = _SIGNATURE_NOISE.sub(" ", normalized)[:96]
        return f"tool:{name}:{normalized}"
    if event.type in {"model.response.failed", "run.failed", "runtime.error"}:
        code = str(event.payload.get("code", "")).strip() or "unknown"
        return f"provider:{code}"
    if event.type == "policy.decided":
        decision = str(event.payload.get("decision", ""))
        if decision == "allow":
            return None
        reason = _SIGNATURE_NOISE.sub("_", str(event.payload.get("reason", "")).strip().casefold())
        return f"policy:{reason or decision}"
    return None


def failure_signature_hash(signature: str) -> str:
    normalized = _SIGNATURE_NOISE.sub(" ", signature.strip().casefold())
    return hashlib.sha256(normalized.encode()).hexdigest()


_PREFERENCE_MARKERS = (
    "i prefer",
    "i like",
    "i love",
    "i hate",
    "i always",
    "i never",
    "my name",
    "call me",
    "ask me",
    "remember that i",
)

_MISSING_CAPABILITY_MARKERS = (
    "not found",
    "no such",
    "is not installed",
    "unknown tool",
    "unsupported",
)


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """Weakest-sufficient repair decision derived from one scar."""

    repair_layer: RepairLayer
    proposal: dict[str, JsonValue]
    rationale: str
    risk: Literal["low", "medium", "high"]
    required_authority: RepairAuthority


@dataclass(frozen=True, slots=True)
class ExecutedRepair:
    """Result of carrying out a repair that needs no user approval."""

    reason: str
    checks: dict[str, JsonValue]


def override_plan(scar: Scar, layer: str) -> RepairPlan:
    """Build an explicit user-directed repair plan for one scar."""
    if layer not in REPAIR_LAYERS:
        raise ValueError(f"unknown repair layer: {layer}")
    if layer in {"semantic_memory", "relationship_memory", "episodic_memory"}:
        memory_layer: RepairLayer = layer  # type: ignore[assignment]
        return RepairPlan(
            repair_layer=memory_layer,
            proposal={
                "kind": "memory_record",
                "subject": "user_directed" if layer == "relationship_memory" else "corrected_fact",
                "predicate": "user_correction",
                "value_text": scar.description,
                "summary": scar.title,
            },
            rationale="User directed this scar to a specific memory layer.",
            risk="low",
            required_authority="memory_write",
        )
    if layer == "skill":
        return RepairPlan(
            repair_layer=layer,
            proposal={
                "kind": "skill_patch",
                "target_version_id": (scar.trigger.skill_ids[0] if scar.trigger.skill_ids else ""),
                "goal": scar.expected_behavior,
            },
            rationale="User directed this scar into the Skill authoring pipeline.",
            risk="medium",
            required_authority="skill_write",
        )
    if layer == "policy_rule":
        return RepairPlan(
            repair_layer=layer,
            proposal={
                "kind": "policy_rule",
                "description": scar.expected_behavior,
                "signature": scar.failure_signature,
            },
            rationale="User proposed a declarative safety rule from this scar.",
            risk="high",
            required_authority="policy_write",
        )
    if layer == "context_rule":
        return RepairPlan(
            repair_layer=layer,
            proposal={
                "kind": "context_rule",
                "description": scar.expected_behavior,
                "trigger": json.dumps(
                    scar.trigger.model_dump(mode="json", exclude_defaults=True),
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            },
            rationale="User proposed a versioned context rule from this scar.",
            risk="medium",
            required_authority="context_write",
        )
    return RepairPlan(
        repair_layer="capability_requirement",
        proposal={
            "kind": "capability_requirement",
            "signature": scar.failure_signature,
            "description": scar.description,
        },
        rationale="User recorded a missing-capability requirement from this scar.",
        risk="high",
        required_authority="plugin_write",
    )


def plan_repair(scar: Scar) -> RepairPlan | None:
    """Choose the weakest sufficient repair layer for a scar, or None."""
    if scar.detection == "skill_outcome_regression":
        version_id = scar.trigger.skill_ids[0] if scar.trigger.skill_ids else ""
        return RepairPlan(
            repair_layer="skill",
            proposal={
                "kind": "skill_patch",
                "target_version_id": version_id,
                "goal": scar.expected_behavior,
            },
            rationale=(
                "A repeatable procedure produced failed or corrected runs; the M07 "
                "lifecycle must patch it."
            ),
            risk="medium",
            required_authority="skill_write",
        )
    if scar.detection in {"explicit_correction", "conversational_correction"}:
        lowered = scar.description.casefold()
        if any(marker in lowered for marker in _PREFERENCE_MARKERS):
            return RepairPlan(
                repair_layer="relationship_memory",
                proposal={
                    "kind": "memory_record",
                    "subject": "user",
                    "predicate": "stated_preference",
                    "value_text": scar.description,
                    "summary": scar.title,
                },
                rationale="The correction restates a user preference or relationship fact.",
                risk="low",
                required_authority="memory_write",
            )
        return RepairPlan(
            repair_layer="semantic_memory",
            proposal={
                "kind": "memory_record",
                "subject": "corrected_fact",
                "predicate": "user_correction",
                "value_text": scar.description,
                "summary": scar.title,
            },
            rationale=(
                "A stable fact was stated wrongly or was missing; the user's own wording "
                "is authoritative."
            ),
            risk="low",
            required_authority="memory_write",
        )
    if scar.detection == "repeated_failure":
        lowered = scar.failure_signature.casefold()
        if any(marker in lowered for marker in _MISSING_CAPABILITY_MARKERS):
            return RepairPlan(
                repair_layer="capability_requirement",
                proposal={
                    "kind": "capability_requirement",
                    "signature": scar.failure_signature,
                    "description": scar.description,
                },
                rationale=(
                    "The same missing capability keeps failing; M09 plugin proposals can "
                    "consume this requirement."
                ),
                risk="high",
                required_authority="plugin_write",
            )
        return None
    return None


class EvolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScarTrigger(EvolutionModel):
    workspace_paths: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    intent_labels: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    tool_error_signatures: list[str] = Field(default_factory=list)
    skill_ids: list[str] = Field(default_factory=list)
    context_signatures: list[str] = Field(default_factory=list)

    @field_validator("workspace_paths")
    @classmethod
    def absolute_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith("/"):
                raise ValueError("trigger workspace paths must be absolute")
        return values

    def is_empty(self) -> bool:
        return not any(
            (
                self.workspace_paths,
                self.agent_ids,
                self.intent_labels,
                self.entity_ids,
                self.tool_error_signatures,
                self.skill_ids,
                self.context_signatures,
            )
        )

    def matches_session(self, *, working_directory: str, agent_id: str) -> bool:
        if self.workspace_paths and working_directory not in self.workspace_paths:
            return False
        if self.agent_ids and agent_id not in self.agent_ids:
            return False
        return True


class Scar(EvolutionModel):
    id: str
    title: str
    scope: ScarScope
    status: ScarStatus
    severity: ScarSeverity
    failure_signature: str
    description: str
    trigger: ScarTrigger
    expected_behavior: str
    detection: str
    owner_agent_id: str | None
    workspace_path: str | None
    source_session_id: str
    source_run_id: str | None
    repair_layer: RepairLayer | None
    repair_reference: str | None
    last_triggered_at: str
    successful_guard_count: int
    regression_count: int
    dismissed_reason: str | None
    created_at: str
    updated_at: str
    evidence_event_ids: list[str]


class ScarRepair(EvolutionModel):
    id: str
    scar_id: str
    version: int
    repair_layer: RepairLayer
    base_hash: str
    proposal: dict[str, JsonValue]
    rationale: str
    deterministic_checks: list[dict[str, JsonValue]]
    model_eval_report: dict[str, JsonValue] | None
    risk: Literal["low", "medium", "high"]
    required_authority: RepairAuthority
    status: Literal["proposed", "promoted", "rejected", "superseded"]
    previous_scar_status: Literal["open", "regressed"]
    created_by: Literal["automatic", "user"]
    source_session_id: str
    created_at: str
    decided_at: str | None


@dataclass(frozen=True, slots=True)
class ScarMutation:
    scar: Scar
    events: tuple[Event, ...]


_EVENT_BY_TARGET: dict[str, str] = {
    "open": "scar.opened",
    "guarded": "scar.guarded",
    "healed": "scar.healed",
    "regressed": "scar.regressed",
    "repair_proposed": "scar.repair_proposed",
    "dismissed": "scar.dismissed",
}


class ScarStore:
    """Event-backed projection of scars and their repair candidates.

    Every state change appends typed events in the same transaction that updates
    the materialized rows, mirroring MemoryStore and SkillRegistry discipline.
    """

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.database = ledger.database

    def record_candidate(
        self,
        *,
        session: Session,
        title: str,
        severity: ScarSeverity,
        failure_signature: str,
        description: str,
        expected_behavior: str,
        evidence_event_ids: list[str],
        trigger: ScarTrigger | None = None,
        run_id: str | None = None,
        detection: str = "explicit_correction",
        scope: ScarScope | None = None,
        causation_id: str | None = None,
    ) -> ScarMutation:
        if not title.strip():
            raise ValueError("scar title is required")
        if not failure_signature.strip():
            raise ValueError("failure_signature is required")
        if not expected_behavior.strip():
            raise ValueError("expected_behavior is required")
        resolved_scope = scope or self._scope_for(session)
        trigger = trigger or ScarTrigger()
        visible_ids = {event.id for event in self.ledger.replay(session.id)}
        missing = set(evidence_event_ids) - visible_ids
        if missing:
            raise ValueError(f"scar evidence is not visible: {sorted(missing)[0]}")
        scar_id = new_id()
        now = utc_now()
        events: list[Event] = []
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            recorded = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="scar.recorded",
                payload={
                    "scar_id": scar_id,
                    "title": title,
                    "scope": resolved_scope,
                    "status": "candidate",
                    "severity": severity,
                    "failure_signature": failure_signature,
                    "description": description,
                    "trigger": self._trigger_json(trigger),
                    "expected_behavior": expected_behavior,
                    "evidence_event_ids": evidence_event_ids,
                    "detection": detection,
                },
                causation_id=causation_id,
                correlation_id=run_id or scar_id,
            )
            events.append(recorded)
            connection.execute(
                """
                INSERT INTO scars(
                    id, title, scope, status, severity, failure_signature,
                    signature_hash, description, trigger_json, expected_behavior,
                    detection, owner_agent_id, workspace_path, source_session_id,
                    source_run_id, last_triggered_at, successful_guard_count,
                    regression_count, created_at, updated_at
                ) VALUES (?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    scar_id,
                    title,
                    resolved_scope,
                    severity,
                    failure_signature,
                    failure_signature_hash(failure_signature),
                    description,
                    json.dumps(self._trigger_json(trigger), separators=(",", ":"), sort_keys=True),
                    expected_behavior,
                    detection,
                    session.agent_id if resolved_scope == "agent" else None,
                    session.working_directory if resolved_scope == "workspace" else None,
                    session.id,
                    run_id,
                    now,
                    now,
                    now,
                ),
            )
            for evidence_id in dict.fromkeys(evidence_event_ids):
                connection.execute(
                    "INSERT INTO scar_evidence(scar_id, event_id) VALUES (?, ?)",
                    (scar_id, evidence_id),
                )
            connection.commit()
        return ScarMutation(self.get(scar_id), tuple(events))

    def find_active_by_signature(self, session: Session, signature: str) -> Scar | None:
        """Match any non-dismissed scar with this signature, including healed ones."""
        signature_hash = failure_signature_hash(signature)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scars WHERE signature_hash = ? "
                "AND status != 'dismissed' ORDER BY created_at DESC",
                (signature_hash,),
            ).fetchall()
        for row in rows:
            scar = self._scar_from_row(connection, row)
            if self.is_visible(session, scar):
                return scar
        return None

    def open(self, *, session: Session, scar_id: str, reason: str) -> ScarMutation:
        return self._transition(session, scar_id, "open", reason)

    def dismiss(self, *, session: Session, scar_id: str, reason: str) -> ScarMutation:
        return self._transition(session, scar_id, "dismissed", reason)

    def mark_guarded(self, *, session: Session, scar_id: str, reason: str) -> ScarMutation:
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            mutation = self._transition_on_connection(
                connection, session, scar_id, "guarded", reason, now
            )
            connection.execute(
                "UPDATE scars SET successful_guard_count = 0, updated_at = ? WHERE id = ?",
                (now, scar_id),
            )
            connection.commit()
        return ScarMutation(self.get(scar_id), mutation.events)

    def mark_healed(self, *, session: Session, scar_id: str, reason: str) -> ScarMutation:
        return self._transition(session, scar_id, "healed", reason)

    def mark_regressed(self, *, session: Session, scar_id: str, reason: str) -> ScarMutation:
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            mutation = self._transition_on_connection(
                connection, session, scar_id, "regressed", reason, now
            )
            connection.execute(
                "UPDATE scars SET regression_count = regression_count + 1, updated_at = ? "
                "WHERE id = ?",
                (now, scar_id),
            )
            connection.commit()
        return ScarMutation(self.get(scar_id), mutation.events)

    def record_trigger(
        self,
        *,
        session: Session,
        scar_id: str,
        run_id: str,
        matched_on: list[str],
        causation_id: str | None = None,
    ) -> tuple[Scar, Event]:
        self.get_visible(session, scar_id)
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE scars SET last_triggered_at = ?, updated_at = ? WHERE id = ?",
                (now, now, scar_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="scar.triggered",
                payload={
                    "scar_id": scar_id,
                    "run_id": run_id,
                    "matched_on": matched_on,
                    "regression": False,
                },
                causation_id=causation_id,
                correlation_id=run_id,
            )
            connection.commit()
        return self.get(scar_id), event

    def record_guard_success(
        self, *, session: Session, scar_id: str, run_id: str, held: bool
    ) -> tuple[Scar, Event]:
        scar = self.get_visible(session, scar_id)
        if scar.status != "guarded":
            raise ValueError("only a guarded scar records guard successes")
        now = utc_now()
        count = scar.successful_guard_count + 1
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE scars SET successful_guard_count = ?, updated_at = ? WHERE id = ?",
                (count, now, scar_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="scar.guard.succeeded",
                payload={
                    "scar_id": scar_id,
                    "run_id": run_id,
                    "successful_guard_count": count,
                    "held": held,
                },
                correlation_id=run_id,
            )
            connection.commit()
        return self.get(scar_id), event

    def propose_repair(
        self,
        *,
        session: Session,
        scar_id: str,
        repair_layer: RepairLayer,
        proposal: dict[str, JsonValue],
        rationale: str,
        risk: Literal["low", "medium", "high"],
        required_authority: RepairAuthority,
        evidence_event_ids: list[str],
        base_hash: str = "",
        deterministic_checks: list[dict[str, JsonValue]] | None = None,
        created_by: Literal["automatic", "user"] = "automatic",
        run_id: str | None = None,
        causation_id: str | None = None,
    ) -> tuple[ScarRepair, ScarMutation]:
        if repair_layer not in REPAIR_LAYERS:
            raise ValueError(f"unknown repair layer: {repair_layer}")
        scar = self.get_visible(session, scar_id)
        if scar.status not in {"open", "regressed"}:
            raise ValueError("scar must be open or regressed to propose a repair")
        visible_ids = {event.id for event in self.ledger.replay(session.id)}
        missing = set(evidence_event_ids) - visible_ids
        if missing:
            raise ValueError(f"repair evidence is not visible: {sorted(missing)[0]}")
        repair_id = new_id()
        now = utc_now()
        events: list[Event] = []
        checks = deterministic_checks or []
        JSON_OBJECT.validate_python(proposal)
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT max(version) AS version FROM scar_repairs WHERE scar_id = ?",
                (scar_id,),
            ).fetchone()
            version = 1 + (int(row["version"]) if row["version"] is not None else 0)
            proposed = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="scar.repair.proposed",
                payload={
                    "scar_id": scar_id,
                    "repair_id": repair_id,
                    "version": version,
                    "repair_layer": repair_layer,
                    "risk": risk,
                    "required_authority": required_authority,
                    "rationale": rationale,
                    "proposal": proposal,
                    "evidence_event_ids": evidence_event_ids,
                },
                causation_id=causation_id,
                correlation_id=run_id or scar_id,
            )
            events.append(proposed)
            connection.execute(
                """
                INSERT INTO scar_repairs(
                    id, scar_id, version, repair_layer, base_hash, proposal_json,
                    rationale, deterministic_checks_json, risk, required_authority,
                    status, previous_scar_status, created_by, source_session_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (
                    repair_id,
                    scar_id,
                    version,
                    repair_layer,
                    base_hash,
                    json.dumps(proposal, separators=(",", ":"), sort_keys=True),
                    rationale,
                    json.dumps(checks, separators=(",", ":"), sort_keys=True),
                    risk,
                    required_authority,
                    scar.status,
                    created_by,
                    session.id,
                    now,
                ),
            )
            for evidence_id in dict.fromkeys(evidence_event_ids):
                connection.execute(
                    "INSERT OR IGNORE INTO scar_evidence(scar_id, event_id) VALUES (?, ?)",
                    (scar_id, evidence_id),
                )
            transition = self._transition_on_connection(
                connection,
                session,
                scar_id,
                "repair_proposed",
                f"repair candidate v{version} ({repair_layer})",
                now,
            )
            events.extend(transition.events)
            connection.execute(
                "UPDATE scars SET repair_layer = ?, repair_reference = ?, updated_at = ? "
                "WHERE id = ?",
                (repair_layer, repair_id, now, scar_id),
            )
            connection.commit()
        return self.get_repair(repair_id), ScarMutation(self.get(scar_id), tuple(events))

    def decide_repair(
        self,
        *,
        session: Session,
        repair_id: str,
        promote: bool,
        reason: str,
        checks: dict[str, JsonValue] | None = None,
        causation_id: str | None = None,
    ) -> ScarMutation:
        repair = self.get_repair(repair_id)
        scar = self.get_visible(session, repair.scar_id)
        if repair.status != "proposed":
            raise ValueError("repair candidate was already decided")
        if scar.repair_reference != repair_id:
            raise ValueError("repair candidate is not the active proposal for this scar")
        now = utc_now()
        events: list[Event] = []
        decision = "promoted" if promote else "rejected"
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            decided = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                run_id=None,
                agent_id=session.agent_id,
                event_type=f"scar.repair.{decision}",
                payload={
                    "scar_id": repair.scar_id,
                    "repair_id": repair_id,
                    "decision": decision,
                    "reason": reason,
                    "checks": checks or {},
                },
                causation_id=causation_id,
                correlation_id=repair.scar_id,
            )
            events.append(decided)
            connection.execute(
                "UPDATE scar_repairs SET status = ?, decided_at = ? WHERE id = ?",
                (decision, now, repair_id),
            )
            if promote:
                connection.execute(
                    "UPDATE scars SET successful_guard_count = 0, updated_at = ? WHERE id = ?",
                    (now, repair.scar_id),
                )
                guarded = self._transition_on_connection(
                    connection,
                    session,
                    repair.scar_id,
                    "guarded",
                    reason,
                    now,
                )
                events.extend(guarded.events)
            else:
                reverted = self._transition_on_connection(
                    connection,
                    session,
                    repair.scar_id,
                    repair.previous_scar_status,
                    reason,
                    now,
                )
                events.extend(reverted.events)
            connection.commit()
        return ScarMutation(self.get(repair.scar_id), tuple(events))

    def get(self, scar_id: str) -> Scar:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM scars WHERE id = ?", (scar_id,)).fetchone()
        if row is None:
            raise KeyError(scar_id)
        return self._scar_from_row(connection, row)

    def append_evaluation_event(
        self,
        *,
        session: Session,
        scar_id: str,
        repair_id: str,
        kind: str,
        status: str,
        score: float,
        report: dict[str, JsonValue],
    ) -> Event:
        """Record one evaluation pass against a repair candidate."""
        return self.ledger.append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="scar.repair.evaluated",
            payload={
                "scar_id": scar_id,
                "repair_id": repair_id,
                "kind": kind,
                "status": status,
                "score": score,
                "report": report,
            },
            correlation_id=repair_id,
        )

    def get_visible(self, session: Session, scar_id: str) -> Scar:
        scar = self.get(scar_id)
        if not self.is_visible(session, scar):
            raise KeyError(scar_id)
        return scar

    def get_repair(self, repair_id: str) -> ScarRepair:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scar_repairs WHERE id = ?", (repair_id,)
            ).fetchone()
        if row is None:
            raise KeyError(repair_id)
        return self._repair_from_row(row)

    def repairs_for_scar(self, scar_id: str) -> list[ScarRepair]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM scar_repairs WHERE scar_id = ? ORDER BY version DESC",
                (scar_id,),
            ).fetchall()
        return [self._repair_from_row(row) for row in rows]

    def list_scars(
        self,
        session: Session | None = None,
        *,
        status: ScarStatus | None = None,
        limit: int = 100,
    ) -> list[Scar]:
        with self.database.connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM scars ORDER BY updated_at DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM scars WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            scars = [self._scar_from_row(connection, row) for row in rows]
        if session is None:
            return scars[:limit]
        visible = [scar for scar in scars if self.is_visible(session, scar)]
        return visible[:limit]

    def count_background_model_calls_today(self, *purposes: str) -> int:
        day = utc_now()[:10]
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT count(*) AS total FROM events
                WHERE type = 'model.requested'
                  AND substr(created_at, 1, 10) = ?
                  AND json_extract(payload_json, '$.purpose') IN
                      (SELECT value FROM json_each(?))
                """,
                (day, json.dumps(list(purposes))),
            ).fetchone()
        return int(row["total"])

    def is_visible(self, session: Session, scar: Scar) -> bool:
        if scar.scope == "global":
            return True
        if scar.scope == "agent":
            return scar.owner_agent_id == session.agent_id
        return scar.workspace_path == session.working_directory

    def _transition(
        self,
        session: Session,
        scar_id: str,
        target: ScarStatus,
        reason: str,
    ) -> ScarMutation:
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            mutation = self._transition_on_connection(
                connection, session, scar_id, target, reason, now
            )
            connection.commit()
        return mutation

    def _transition_on_connection(
        self,
        connection: sqlite3.Connection,
        session: Session,
        scar_id: str,
        target: ScarStatus,
        reason: str,
        now: str,
    ) -> ScarMutation:
        current = connection.execute("SELECT * FROM scars WHERE id = ?", (scar_id,)).fetchone()
        if current is None:
            raise KeyError(scar_id)
        previous_status = str(current["status"])
        if target not in TRANSITIONS.get(previous_status, frozenset()):
            raise ValueError(f"scar cannot move from {previous_status} to {target}")
        connection.execute(
            "UPDATE scars SET status = ?, dismissed_reason = ?, updated_at = ? WHERE id = ?",
            (target, reason if target == "dismissed" else None, now, scar_id),
        )
        event = self.ledger.append_in_transaction(
            connection,
            session_id=session.id,
            agent_id=session.agent_id,
            event_type=_EVENT_BY_TARGET[target],
            payload={
                "scar_id": scar_id,
                "previous_status": previous_status,
                "status": target,
                "reason": reason,
                "repair_id": None,
            },
            correlation_id=scar_id,
        )
        updated_row = connection.execute("SELECT * FROM scars WHERE id = ?", (scar_id,)).fetchone()
        return ScarMutation(self._scar_from_row(connection, updated_row), (event,))

    def _scope_for(self, session: Session) -> ScarScope:
        return "workspace"

    def _trigger_json(self, trigger: ScarTrigger) -> dict[str, JsonValue]:
        return JSON_OBJECT.validate_python(trigger.model_dump(mode="json", exclude_defaults=True))

    def _scar_from_row(self, connection: sqlite3.Connection, row: sqlite3.Row) -> Scar:
        values = dict(row)
        values["trigger"] = ScarTrigger.model_validate(json.loads(values.pop("trigger_json")))
        values.pop("signature_hash")
        values["evidence_event_ids"] = [
            str(item["event_id"])
            for item in connection.execute(
                "SELECT event_id FROM scar_evidence WHERE scar_id = ? ORDER BY event_id",
                (values["id"],),
            )
        ]
        return Scar.model_validate(values)

    @staticmethod
    def _repair_from_row(row: sqlite3.Row) -> ScarRepair:
        values = dict(row)
        values["proposal"] = JSON_OBJECT.validate_json(values.pop("proposal_json"))
        values["deterministic_checks"] = list(
            json.loads(values.pop("deterministic_checks_json") or "[]")
        )
        raw_report = values.pop("model_eval_report_json")
        values["model_eval_report"] = (
            JSON_OBJECT.validate_json(raw_report) if raw_report is not None else None
        )
        return ScarRepair.model_validate(values)
