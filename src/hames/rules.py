"""Versioned declarative context rules and policy rules proposed by evolution.

Rules are inert until a human activates them through the authenticated
gateway. Activated context rules are enforced deterministically by the
context compiler; activated policy rules can only add protection.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hames.ledger import Event, Ledger, Session, new_id, utc_now
from hames.providers.base import JSON_OBJECT

RuleStatus = Literal["proposed", "active", "retired"]
RuleAction = Literal["deny", "confirm"]


class RuleModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextRuleCondition(RuleModel):
    workspace_paths: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)

    @field_validator("workspace_paths")
    @classmethod
    def absolute_paths(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith("/"):
                raise ValueError("condition workspace paths must be absolute")
        return values

    def matches(self, *, working_directory: str, agent_id: str) -> bool:
        if self.workspace_paths and working_directory not in self.workspace_paths:
            return False
        if self.agent_ids and agent_id not in self.agent_ids:
            return False
        return True


class ContextRule(RuleModel):
    id: str
    version: int
    description: str
    condition: ContextRuleCondition
    require_source_types: list[str]
    status: RuleStatus
    scar_id: str | None
    source_session_id: str
    created_by: Literal["automatic", "user"]
    created_at: str
    updated_at: str


class PolicyRule(RuleModel):
    id: str
    action: RuleAction
    scope: str
    pattern: str
    reason: str
    status: RuleStatus
    scar_id: str | None
    source_session_id: str
    created_by: Literal["automatic", "user"]
    created_at: str
    updated_at: str

    @field_validator("pattern")
    @classmethod
    def valid_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"policy rule pattern is not a valid regex: {exc}") from exc
        return value


@dataclass(frozen=True, slots=True)
class RuleMutation:
    rule: ContextRule | PolicyRule
    events: tuple[Event, ...]


class ContextRuleStore:
    """Event-backed store of declarative context requirements."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.database = ledger.database

    def propose(
        self,
        *,
        session: Session,
        description: str,
        require_source_types: list[str],
        condition: ContextRuleCondition | None = None,
        scar_id: str | None = None,
        created_by: Literal["automatic", "user"] = "user",
        causation_id: str | None = None,
    ) -> RuleMutation:
        if not require_source_types:
            raise ValueError("a context rule must require at least one source type")
        if not description.strip():
            raise ValueError("context rule description is required")
        condition = condition or ContextRuleCondition()
        rule_id = new_id()
        now = utc_now()
        events: list[Event] = []
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT max(version) AS version FROM context_rules WHERE scar_id IS ?",
                (scar_id,),
            ).fetchone()
            version = 1 + (int(row["version"]) if row["version"] is not None else 0)
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="context.rule.proposed",
                payload={
                    "rule_id": rule_id,
                    "version": version,
                    "status": "proposed",
                    "condition": JSON_OBJECT.validate_python(
                        condition.model_dump(mode="json", exclude_defaults=True)
                    ),
                    "require_source_types": list(dict.fromkeys(require_source_types)),
                    "reason": "user_proposed" if created_by == "user" else "scar_repair",
                },
                causation_id=causation_id,
                correlation_id=scar_id or rule_id,
            )
            events.append(event)
            connection.execute(
                """
                INSERT INTO context_rules(
                    id, version, condition_json, require_source_types_json, description,
                    status, scar_id, source_session_id, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    version,
                    json.dumps(condition.model_dump(mode="json"), separators=(",", ":")),
                    json.dumps(list(dict.fromkeys(require_source_types)), separators=(",", ":")),
                    description,
                    scar_id,
                    session.id,
                    created_by,
                    now,
                    now,
                ),
            )
            connection.commit()
        return RuleMutation(self.get(rule_id), tuple(events))

    def set_status(
        self,
        *,
        rule_id: str,
        action: Literal["activate", "retire"],
        reason: str,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> RuleMutation:
        rule = self.get(rule_id)
        expected = "proposed"
        target: RuleStatus = "active"
        event_type = "context.rule.activated"
        if action == "retire":
            expected, target, event_type = "active", "retired", "context.rule.retired"
        if rule.status != expected:
            raise ValueError(f"context rule must be {expected} before {action}")
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session_row = connection.execute(
                "SELECT agent_id FROM sessions WHERE id = ?",
                (session_id or rule.source_session_id,),
            ).fetchone()
            connection.execute(
                "UPDATE context_rules SET status = ?, updated_at = ? WHERE id = ?",
                (target, now, rule_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session_id or rule.source_session_id,
                agent_id=(session_row["agent_id"] if session_row else None) or agent_id,
                event_type=event_type,
                payload={
                    "rule_id": rule_id,
                    "version": rule.version,
                    "status": target,
                    "condition": JSON_OBJECT.validate_python(
                        rule.condition.model_dump(mode="json", exclude_defaults=True)
                    ),
                    "require_source_types": rule.require_source_types,
                    "reason": reason,
                },
                correlation_id=rule.scar_id or rule_id,
            )
            connection.commit()
        return RuleMutation(self.get(rule_id), (event,))

    def get(self, rule_id: str) -> ContextRule:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        if row is None:
            raise KeyError(rule_id)
        return self._rule_from_row(row)

    def list_rules(self, *, status: RuleStatus | None = None) -> list[ContextRule]:
        query = "SELECT * FROM context_rules"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._rule_from_row(row) for row in rows]

    def active_matching(self, *, working_directory: str, agent_id: str) -> list[ContextRule]:
        return [
            rule
            for rule in self.list_rules(status="active")
            if rule.condition.matches(working_directory=working_directory, agent_id=agent_id)
        ]

    def _rule_from_row(self, row: sqlite3.Row) -> ContextRule:
        values = dict(row)
        values["condition"] = ContextRuleCondition.model_validate(
            json.loads(values.pop("condition_json"))
        )
        values["require_source_types"] = list(json.loads(values.pop("require_source_types_json")))
        return ContextRule.model_validate(values)


class PolicyRuleStore:
    """Event-backed store of additive declarative policy protections."""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.database = ledger.database

    def propose(
        self,
        *,
        session: Session,
        action: RuleAction,
        pattern: str,
        reason: str,
        scope: str = "shell_command",
        scar_id: str | None = None,
        created_by: Literal["automatic", "user"] = "user",
        causation_id: str | None = None,
    ) -> RuleMutation:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"policy rule pattern is not a valid regex: {exc}") from exc
        if action not in {"deny", "confirm"}:
            raise ValueError(f"unknown policy rule action: {action}")
        if not reason.strip():
            raise ValueError("policy rule reason is required")
        if scope != "shell_command":
            raise ValueError(f"unsupported policy rule scope: {scope}")
        rule_id = new_id()
        now = utc_now()
        events: list[Event] = []
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            event = self.ledger.append_in_transaction(
                connection,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="policy.rule.proposed",
                payload={
                    "rule_id": rule_id,
                    "action": action,
                    "pattern": pattern,
                    "status": "proposed",
                    "reason": reason,
                },
                causation_id=causation_id,
                correlation_id=scar_id or rule_id,
            )
            events.append(event)
            connection.execute(
                """
                INSERT INTO policy_rules(
                    id, action, scope, pattern, reason, status, scar_id,
                    source_session_id, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    action,
                    scope,
                    pattern,
                    reason,
                    scar_id,
                    session.id,
                    created_by,
                    now,
                    now,
                ),
            )
            connection.commit()
        return RuleMutation(self.get(rule_id), tuple(events))

    def set_status(
        self,
        *,
        rule_id: str,
        action: Literal["activate", "retire"],
        reason: str,
    ) -> RuleMutation:
        rule = self.get(rule_id)
        expected = "proposed"
        target: RuleStatus = "active"
        event_type = "policy.rule.activated"
        if action == "retire":
            expected, target, event_type = "active", "retired", "policy.rule.retired"
        if rule.status != expected:
            raise ValueError(f"policy rule must be {expected} before {action}")
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            session_row = connection.execute(
                "SELECT agent_id FROM sessions WHERE id = ?", (rule.source_session_id,)
            ).fetchone()
            connection.execute(
                "UPDATE policy_rules SET status = ?, updated_at = ? WHERE id = ?",
                (target, now, rule_id),
            )
            event = self.ledger.append_in_transaction(
                connection,
                session_id=rule.source_session_id,
                agent_id=session_row["agent_id"] if session_row else None,
                event_type=event_type,
                payload={
                    "rule_id": rule_id,
                    "action": rule.action,
                    "pattern": rule.pattern,
                    "status": target,
                    "reason": reason,
                },
                correlation_id=rule.scar_id or rule_id,
            )
            connection.commit()
        return RuleMutation(self.get(rule_id), (event,))

    def get(self, rule_id: str) -> PolicyRule:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM policy_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        if row is None:
            raise KeyError(rule_id)
        return PolicyRule.model_validate(dict(row))

    def list_rules(self, *, status: RuleStatus | None = None) -> list[PolicyRule]:
        query = "SELECT * FROM policy_rules"
        params: tuple[object, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [PolicyRule.model_validate(dict(row)) for row in rows]
