"""Deterministic replay checks and budgeted model evaluation of repairs."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.evolution import ScarRepair, ScarStore
from hames.ledger import Event, Ledger, Session
from hames.memory import MemoryStore
from hames.providers import (
    ModelRequest,
    Provider,
    ProviderError,
    StreamEventKind,
    ToolDefinition,
)
from hames.providers.base import JSON_OBJECT, JsonValue, ProviderMessage
from hames.rules import PolicyRuleStore
from hames.skills import SkillRegistry

EVALUATION_SYSTEM = """You independently evaluate one proposed repair for a recorded failure.
Judge only whether the repair addresses the documented failure without expanding authority or
ignoring evidence. Reject vague, unsafe, ungrounded, or over-broad repairs. Submit exactly one
verdict through submit_repair_evaluation. Do not rewrite the repair.
"""


class RepairEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    score: float = Field(ge=0, le=1)
    summary: str
    findings: list[str] = Field(default_factory=list)


_AUTHORITY_RANK = {
    "none": 0,
    "memory_write": 1,
    "skill_write": 2,
    "context_write": 2,
    "policy_write": 3,
    "plugin_write": 4,
}


def deterministic_checks(
    *,
    session: Session,
    scar: Any,
    repair: ScarRepair,
    memory: MemoryStore,
    skills: SkillRegistry,
    policy_rules: PolicyRuleStore | None = None,
) -> dict[str, Any]:
    """Run the automatic checks every repair candidate must satisfy."""
    results: list[dict[str, JsonValue]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        results.append({"check": name, "passed": passed, "detail": detail})

    record(
        "evidence_available",
        bool(scar.evidence_event_ids),
        f"{len(scar.evidence_event_ids)} evidence event(s) linked to the scar",
    )
    proposal = repair.proposal
    if repair.repair_layer in {"semantic_memory", "relationship_memory", "episodic_memory"}:
        subject = str(proposal.get("subject", ""))
        predicate = str(proposal.get("predicate", ""))
        matches = [
            item
            for item in memory.list_visible(session, status="active", limit=200)
            if item.subject == subject and item.predicate == predicate
        ]
        record(
            "required_memory_record_available",
            bool(matches),
            f"{len(matches)} active record(s) with subject={subject!r} predicate={predicate!r}",
        )
    if repair.repair_layer == "context_rule":
        description = str(proposal.get("description", "")).strip()
        record(
            "context_rule_specified",
            bool(description),
            "rule requires concrete source guidance" if description else "no rule text proposed",
        )
    if repair.repair_layer == "policy_rule":
        pattern = str(proposal.get("pattern", ""))
        raw_block = proposal.get("must_block", [])
        raw_allow = proposal.get("must_allow", [])
        block_list = [str(item) for item in cast(list[JsonValue], raw_block)]
        allow_list = [str(item) for item in cast(list[JsonValue], raw_allow)]
        blocked_ok = all(_matches(pattern, item) for item in block_list) if pattern else False
        allow_ok = all(not _matches(pattern, item) for item in allow_list) if pattern else False
        record(
            "policy_rule_fixture",
            bool(pattern) and blocked_ok and allow_ok,
            f"block={blocked_ok} allow={allow_ok}",
        )
    if repair.repair_layer == "skill":
        target_version_id = str(proposal.get("target_version_id", ""))
        stale = False
        if target_version_id:
            try:
                version = skills.get(target_version_id)
                stale = version.status not in {"active"}
            except KeyError:
                stale = True
        record(
            "base_version_not_stale",
            not stale,
            "target version is active" if not stale else "target version missing or inactive",
        )
    rank = _AUTHORITY_RANK.get(repair.required_authority, 99)
    scope_broadening = rank > _AUTHORITY_RANK["skill_write"] and repair.created_by == "automatic"
    record(
        "no_unauthorized_scope_broadening",
        not scope_broadening,
        f"required_authority={repair.required_authority} created_by={repair.created_by}",
    )
    passed = all(bool(item["passed"]) for item in results)
    return {"passed": passed, "results": results}


def _matches(pattern: str, command: str) -> bool:
    import re

    return re.search(pattern, command) is not None


class RepairEvaluator:
    """Compose deterministic and optional budgeted model evaluation."""

    def __init__(
        self,
        *,
        ledger: Ledger,
        config: HamesConfig,
        providers: dict[str, Provider],
        broker: EventBroker,
        store: ScarStore,
        memory: MemoryStore,
        skills: SkillRegistry,
        policy_rules: PolicyRuleStore | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.providers = providers
        self.broker = broker
        self.store = store
        self.memory = memory
        self.skills = skills
        self.policy_rules = policy_rules

    async def evaluate(self, session_id: str, repair_id: str) -> dict[str, Any]:
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        repair = await asyncio.to_thread(self.store.get_repair, repair_id)
        scar = await asyncio.to_thread(self.store.get_visible, session, repair.scar_id)

        def _check() -> dict[str, JsonValue]:
            return deterministic_checks(
                session=session,
                scar=scar,
                repair=repair,
                memory=self.memory,
                skills=self.skills,
                policy_rules=self.policy_rules,
            )

        deterministic = await asyncio.to_thread(_check)
        await asyncio.to_thread(
            self.store.append_evaluation_event,
            session=session,
            scar_id=repair.scar_id,
            repair_id=repair_id,
            kind="deterministic",
            status="passed" if deterministic["passed"] else "failed",
            score=1.0 if deterministic["passed"] else 0.0,
            report=deterministic,
        )
        report: dict[str, JsonValue] = {"deterministic": deterministic}
        verdict_passed = bool(deterministic["passed"])
        if verdict_passed and self._model_eval_allowed():
            verdict = await self._model_eval(session, repair=repair)
            report["model"] = verdict.model_dump(mode="json")
            verdict_passed = verdict.passed and verdict.score >= self._pass_score()
        if not verdict_passed:
            decision_mutation = await asyncio.to_thread(
                self.store.decide_repair,
                session=session,
                repair_id=repair_id,
                promote=False,
                reason="evaluation failed",
                checks=report,
            )
            await asyncio.to_thread(
                self.store.append_evaluation_event,
                session=session,
                scar_id=repair.scar_id,
                repair_id=repair_id,
                kind="final",
                status="rejected",
                score=0.0,
                report=report,
            )
            await self._publish(decision_mutation.events)
            return report
        if repair.required_authority in {"none", "memory_write"}:
            decision_mutation = await asyncio.to_thread(
                self.store.decide_repair,
                session=session,
                repair_id=repair_id,
                promote=True,
                reason="evaluation passed within low-risk repair class",
                checks=report,
            )
            await self._publish(decision_mutation.events)
            await asyncio.to_thread(
                self.store.append_evaluation_event,
                session=session,
                scar_id=repair.scar_id,
                repair_id=repair_id,
                kind="final",
                status="promoted",
                score=1.0,
                report=report,
            )
            return report
        await asyncio.to_thread(
            self.store.append_evaluation_event,
            session=session,
            scar_id=repair.scar_id,
            repair_id=repair_id,
            kind="final",
            status="pending_approval",
            score=1.0,
            report=report,
        )
        report["status"] = "pending_approval"
        return report

    def _model_eval_allowed(self) -> bool:
        budget = self.config.evolution.max_background_model_calls_per_day
        used = self.store.count_background_model_calls_today(
            "evolution_review", "evolution_evaluation"
        )
        return used < budget

    def _pass_score(self) -> float:
        return self.config.skills.evaluator_pass_score

    async def _model_eval(self, session: Session, *, repair: ScarRepair) -> RepairEvaluation:
        profile_id = self.config.evolution.provider or session.provider
        provider = self.providers.get(profile_id)
        if provider is None:
            raise ValueError(f"unknown evolution provider: {profile_id}")
        model = self.config.evolution.model or session.model
        reasoning = self.config.evolution.reasoning_effort or session.reasoning_effort
        requested = await self._append(
            session_id=session.id,
            agent_id=session.agent_id,
            event_type="model.requested",
            payload={
                "provider": profile_id,
                "model": model,
                "reasoning_effort": reasoning,
                "agent_capsule_hash": "evolution-evaluator-v1",
                "purpose": "evolution_evaluation",
            },
            correlation_id=repair.id,
        )
        request = ModelRequest(
            model=model,
            system=EVALUATION_SYSTEM,
            messages=[
                ProviderMessage(
                    role="user",
                    content=f"Repair candidate: {repair.rationale}\nProposal details are hashed "
                    "in the ledger; judge conservatively.",
                )
            ],
            reasoning_effort=reasoning,
            max_tokens=1024,
            temperature=0,
            tools=[evaluation_submission_tool()],
            metadata={"purpose": "evolution_evaluation", "repair_id": repair.id},
        )
        name_parts: list[str] = []
        argument_parts: list[str] = []
        started = completed = False
        try:
            async for event in provider.stream(request):
                if event.kind is StreamEventKind.STARTED:
                    started = True
                elif event.kind is StreamEventKind.TOOL_CALL_DELTA:
                    if event.tool_call is None or event.tool_call.index != 0:
                        raise ValueError("evolution evaluator emitted an invalid tool call")
                    if event.tool_call.name:
                        name_parts.append(event.tool_call.name)
                    argument_parts.append(event.tool_call.arguments_delta)
                elif event.kind is StreamEventKind.USAGE and event.usage is not None:
                    await self._append(
                        session_id=session.id,
                        agent_id=session.agent_id,
                        event_type="model.usage",
                        payload=event.usage.model_dump(mode="json"),
                        causation_id=requested.id,
                        correlation_id=repair.id,
                    )
                elif event.kind is StreamEventKind.COMPLETED:
                    completed = True
            if not started or not completed or "".join(name_parts) != "submit_repair_evaluation":
                raise ValueError("evolution evaluator did not submit a verdict")
            raw = "".join(argument_parts) or "{}"
            return RepairEvaluation.model_validate(JSON_OBJECT.validate_json(raw))
        except (ProviderError, ValueError) as exc:
            await self._append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="model.response.failed",
                payload={
                    "code": exc.code if isinstance(exc, ProviderError) else "evaluator_failed",
                    "message": str(exc),
                    "retryable": isinstance(exc, ProviderError) and exc.retryable,
                    "details": {},
                },
                causation_id=requested.id,
                correlation_id=repair.id,
            )
            raise

    async def _append(self, **kwargs: Any) -> Event:
        event = await asyncio.to_thread(self.ledger.append, **kwargs)
        await self.broker.publish(
            event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
        )
        return event

    async def _publish(self, events: tuple[Event, ...]) -> None:
        for event in events:
            await self.broker.publish(
                event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
            )


def evaluation_submission_tool() -> ToolDefinition:
    return ToolDefinition(
        name="submit_repair_evaluation",
        description="Submit one independent repair evaluation verdict.",
        input_schema={
            "type": "object",
            "properties": {
                "passed": {"type": "boolean"},
                "score": {"type": "number", "minimum": 0, "maximum": 1},
                "summary": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["passed", "score", "summary", "findings"],
            "additionalProperties": False,
        },
    )
