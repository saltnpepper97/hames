"""Run-observation and explicit-correction pathways that create Scars."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, cast

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.evolution import (
    ExecutedRepair,
    RepairPlan,
    Scar,
    ScarMutation,
    ScarRepair,
    ScarStore,
    ScarTrigger,
    failure_signature_hash,
    looks_like_correction,
    normalize_failure_signature,
    override_plan,
    plan_repair,
)
from hames.ledger import Event, Ledger, Session
from hames.memory import MemoryCandidate, MemoryLayer, MemoryStore, MemoryVisibility
from hames.providers import (
    ModelRequest,
    Provider,
    ProviderError,
    ProviderMessage,
    StreamEventKind,
)
from hames.providers.base import JSON_OBJECT, ToolDefinition
from hames.skill_runtime import SkillManager
from hames.skills import SkillRegistry

if TYPE_CHECKING:
    from hames.plugin_runtime import PluginManager

FAILURE_EVENT_TYPES = {
    "tool.failed",
    "tool.rejected",
    "model.response.failed",
    "run.failed",
    "runtime.error",
    "policy.decided",
}

_TERMINAL_TYPES = {"run.completed", "run.failed", "run.cancelled"}

MODEL_BEHAVIOR_REPAIR_LAYERS = {
    "semantic_memory",
    "relationship_memory",
    "episodic_memory",
    "skill",
}

REVIEWER_SYSTEM = """You classify one user message sent to a coding agent. Decide whether the
message corrects a previous result, contradicting earlier output or instructing a different
approach for next time. Ordinary new tasks, questions, and praise are not corrections. Submit
exactly one classification through submit_correction_classification.
"""


class EvolutionManager:
    """Deterministic detectors turning corrections and repeated failures into Scars."""

    def __init__(
        self,
        *,
        ledger: Ledger,
        config: HamesConfig,
        broker: EventBroker,
        store: ScarStore,
        skills: SkillRegistry,
        memory: MemoryStore,
        providers: dict[str, Provider] | None = None,
        skill_manager: SkillManager | None = None,
        plugin_manager: PluginManager | None = None,
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.broker = broker
        self.store = store
        self.skills = skills
        self.memory = memory
        self.providers = providers or {}
        self.skill_manager = skill_manager
        self.plugin_manager = plugin_manager

    async def propose_repair(
        self,
        session_id: str,
        scar_id: str,
        *,
        layer_override: str | None = None,
    ) -> tuple[Scar, ScarRepair]:
        """Route an open scar to the weakest sufficient repair layer."""
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        scar = await asyncio.to_thread(self.store.get_visible, session, scar_id)
        if layer_override is not None:
            plan = override_plan(scar, layer_override)
        else:
            plan = plan_repair(scar)
        if plan is None:
            raise ValueError(
                "no autonomous repair is available for this scar; "
                "propose one explicitly with a repair_layer"
            )

        def _record() -> tuple[ScarRepair, ScarMutation]:
            return self.store.propose_repair(
                session=session,
                scar_id=scar_id,
                repair_layer=plan.repair_layer,
                proposal=plan.proposal,
                rationale=plan.rationale,
                risk=plan.risk,
                required_authority=plan.required_authority,
                evidence_event_ids=list(scar.evidence_event_ids),
                created_by="automatic" if layer_override is None else "user",
            )

        repair, mutation = await asyncio.to_thread(_record)
        await self._publish(mutation.events)
        executed = await self._execute_repair(session, scar, plan, mutation.events[0].id)
        if executed is not None:
            promoted = await asyncio.to_thread(
                self.store.decide_repair,
                session=session,
                repair_id=repair.id,
                promote=True,
                reason=executed.reason,
                checks=executed.checks,
                causation_id=mutation.events[-1].id,
            )
            await self._publish(promoted.events)
            return promoted.scar, repair
        return mutation.scar, repair

    async def _execute_repair(
        self,
        session: Session,
        scar: Scar,
        plan: RepairPlan,
        proposal_event_id: str,
    ) -> ExecutedRepair | None:
        """Carry out the weakest repairs that need no approval. None = still pending."""
        if plan.repair_layer == "skill":
            await self._dispatch_skill_patch(session, scar, plan)
            return None
        if plan.repair_layer == "capability_requirement":
            await self._dispatch_plugin_proposal(session, scar)
            return None
        if plan.required_authority != "memory_write":
            return None
        if not self.config.evolution.auto_promote_memory_repairs:
            return None
        visibility = {
            "global": "global",
            "workspace": "workspace",
            "agent": "agent_private",
        }[scar.scope]
        layer = {
            "semantic_memory": "semantic",
            "relationship_memory": "relationship",
            "episodic_memory": "episodic",
        }.get(plan.repair_layer)
        if layer is None:
            return None
        candidate = MemoryCandidate(
            layer=cast(MemoryLayer, layer),
            visibility=cast(MemoryVisibility, visibility),
            subject=str(plan.proposal.get("subject", "corrected_fact")),
            predicate=str(plan.proposal.get("predicate", "user_correction")),
            value={"text": plan.proposal.get("value_text", scar.description)},
            summary=str(plan.proposal.get("summary", scar.title)),
            confidence=1.0,
            importance=0.8,
            anchors=[],
            provenance_event_ids=list(scar.evidence_event_ids),
            evidence_basis="explicit_user",
        )
        memory_mutation = await asyncio.to_thread(
            self.memory.create_candidate,
            session=session,
            candidate=candidate,
            run_id=None,
            origin_kind="explicit",
            activate=True,
            causation_id=proposal_event_id,
        )
        await self._publish(memory_mutation.events)
        return ExecutedRepair(
            reason="memory repair grounded in direct user correction",
            checks={"memory_id": memory_mutation.record.id},
        )

    async def _dispatch_skill_patch(self, session: Session, scar: Scar, plan: RepairPlan) -> None:
        if self.skill_manager is None:
            return
        target_version_id = str(plan.proposal.get("target_version_id", ""))
        target_skill_id: str | None = None
        if target_version_id:
            try:
                version = await asyncio.to_thread(self.skills.get, target_version_id)
                target_skill_id = version.skill_id
            except KeyError:
                target_skill_id = None
        scope = "agent" if scar.scope == "agent" else scar.scope
        await self.skill_manager.author(
            session,
            goal=str(plan.proposal.get("goal", scar.expected_behavior)),
            scope=scope,
            target_skill_id=target_skill_id,
        )

    async def _dispatch_plugin_proposal(self, session: Session, scar: Scar) -> None:
        if self.plugin_manager is None:
            return
        await self.plugin_manager.propose_from_scar(
            session=session,
            scar_id=scar.id,
            title=scar.title,
            description=scar.description,
            expected_behavior=scar.expected_behavior,
            evidence_event_ids=list(scar.evidence_event_ids),
        )

    async def observe_run(self, session_id: str, run_id: str) -> list[Scar]:
        """Inspect one finished run and open scars for detected problems."""
        if not self.config.evolution.enabled:
            return []
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        events = await asyncio.to_thread(self.ledger.list_run_events, run_id)
        terminal = next(
            (event for event in reversed(events) if event.type in _TERMINAL_TYPES), None
        )
        started = next((event for event in events if event.type == "run.started"), None)
        if terminal is None or started is None or started.causation_id is None:
            return []
        user = await asyncio.to_thread(self.ledger.get_event, started.causation_id)
        task_text = str(user.payload.get("content", ""))
        created: list[Scar] = []
        pending: list[Event] = []
        scar, emitted = await self._conversational_correction(
            session, run_id, user, task_text, events, terminal
        )
        if scar is None and terminal.type == "run.completed":
            scar, emitted = await self._reviewer_classification(
                session, run_id, task_text, events, terminal
            )
        if scar is not None:
            created.append(scar)
        pending.extend(emitted)
        new_scars, triggers, emitted = await self._repeated_failures(session, run_id, events)
        created.extend(new_scars)
        pending.extend([*triggers, *emitted])
        skill_scars, skill_emitted = await self._skill_regressions(session, run_id, events)
        created.extend(skill_scars)
        pending.extend(skill_emitted)
        for index, opened in enumerate(created):
            created[index] = await self._auto_route(session, opened)
        guard_events = await self._update_guards(session, run_id, events, terminal, task_text)
        pending.extend(guard_events)
        await self._publish(pending)
        return created

    async def _update_guards(
        self,
        session: Session,
        run_id: str,
        events: list[Event],
        terminal: Event,
        task_text: str,
    ) -> list[Event]:
        """Count comparable guard successes and reopen regressed scars."""
        completed = terminal.type == "run.completed"
        run_signatures = {
            signature
            for signature in (normalize_failure_signature(event) for event in events)
            if signature is not None
        }
        guarded = await asyncio.to_thread(self.store.list_scars, session, status="guarded")
        healed = await asyncio.to_thread(self.store.list_scars, session, status="healed")
        failing_versions: set[str] = set()
        if any(event.type == "skill.loaded" for event in events):
            failing_versions = {
                item.version_id
                for item in await asyncio.to_thread(self.skills.repeated_failure_versions, 1)
            }
        pending_events: list[Event] = []
        for scar in [*guarded, *healed]:
            if not scar.trigger.matches_session(
                working_directory=session.working_directory, agent_id=session.agent_id
            ):
                continue
            recurred = scar.failure_signature in run_signatures or (
                scar.detection == "skill_outcome_regression"
                and bool(set(scar.trigger.skill_ids) & failing_versions)
            )
            if recurred:
                mutation = await asyncio.to_thread(
                    self.store.mark_regressed,
                    session=session,
                    scar_id=scar.id,
                    reason="failure returned during a matching run",
                )
                pending_events.extend(mutation.events)
                requeued = await self._requeue_repair(session, mutation.scar)
                pending_events.extend(requeued)
                continue
            if not completed or scar.status != "guarded":
                continue
            counted, guard_event = await asyncio.to_thread(
                self.store.record_guard_success,
                session=session,
                scar_id=scar.id,
                run_id=run_id,
                held=True,
            )
            pending_events.append(guard_event)
            if (
                counted.status == "guarded"
                and counted.successful_guard_count >= self.config.evolution.healing_threshold
            ):
                healed_mutation = await asyncio.to_thread(
                    self.store.mark_healed,
                    session=session,
                    scar_id=scar.id,
                    reason=(
                        f"healing_threshold={self.config.evolution.healing_threshold} "
                        "comparable successes"
                    ),
                )
                pending_events.extend(healed_mutation.events)
        return pending_events

    async def _auto_route(self, session: Session, scar: Scar) -> Scar:
        """Route a freshly opened scar to its repair; unroutable scars stay open."""
        if scar.status != "open":
            return scar
        try:
            routed, _ = await self.propose_repair(session.id, scar.id)
        except ValueError:
            return scar
        return routed

    async def _requeue_repair(self, session: Session, scar: Scar) -> list[Event]:
        """Open a fresh repair candidate version for an autonomously repairable scar."""
        if scar.detection == "repeated_failure" and plan_repair(scar) is None:
            return []
        plan = plan_repair(scar)
        if plan is None and scar.repair_layer is not None:
            try:
                plan = override_plan(scar, scar.repair_layer)
            except ValueError:
                return []
        if plan is None:
            return []
        try:
            _, mutation = await asyncio.to_thread(
                self.store.propose_repair,
                session=session,
                scar_id=scar.id,
                repair_layer=plan.repair_layer,
                proposal=plan.proposal,
                rationale=plan.rationale,
                risk=plan.risk,
                required_authority=plan.required_authority,
                evidence_event_ids=list(scar.evidence_event_ids),
                created_by="automatic",
            )
        except ValueError:
            return []
        executed = await self._execute_repair(session, scar, plan, mutation.events[0].id)
        if executed is None:
            return list(mutation.events)
        promoted = await asyncio.to_thread(
            self.store.decide_repair,
            session=session,
            repair_id=mutation.events[0].payload["repair_id"],
            promote=True,
            reason=executed.reason,
            checks=executed.checks,
            causation_id=mutation.events[-1].id,
        )
        return [*mutation.events, *promoted.events]

    async def submit_correction(
        self,
        session_id: str,
        *,
        content: str,
        target_event_id: str | None = None,
    ) -> Scar:
        """Record an explicit user correction as a high-severity opened Scar."""
        session = await asyncio.to_thread(self.ledger.get_session, session_id)
        if not content.strip():
            raise ValueError("correction content is required")
        target: Event | None = None
        if target_event_id is not None:
            target = await asyncio.to_thread(self.ledger.get_event, target_event_id)
            if target.session_id != session_id:
                raise KeyError(target_event_id)

        def _record() -> tuple[Scar, tuple[Event, ...]]:
            correction_event = self.ledger.append(
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="user.correction",
                payload={"content": content, "target_event_id": target_event_id},
                causation_id=target.id if target is not None else None,
            )
            signature = f"explicit-correction:{content.strip().casefold()[:120]}"
            existing = self.store.find_active_by_signature(session, signature)
            if existing is not None and existing.status in {"guarded", "healed"}:
                mutation = self.store.mark_regressed(
                    session=session,
                    scar_id=existing.id,
                    reason="the same correction returned after its repair",
                )
                return mutation.scar, mutation.events
            if existing is not None:
                triggered, trigger_event = self.store.record_trigger(
                    session=session,
                    scar_id=existing.id,
                    run_id=correction_event.run_id or "",
                    matched_on=["explicit_correction"],
                    causation_id=correction_event.id,
                )
                return triggered, (trigger_event,)
            mutation = self.store.record_candidate(
                session=session,
                title=f"Correction: {content.strip()[:72]}",
                severity="high",
                failure_signature=signature,
                description=content.strip(),
                expected_behavior=(
                    "Hames must incorporate this correction in equivalent future situations."
                ),
                evidence_event_ids=[*([target.id] if target else []), correction_event.id],
                trigger=ScarTrigger(workspace_paths=[session.working_directory]),
                run_id=None,
                detection="explicit_correction",
                causation_id=correction_event.id,
            )
            opened = self.store.open(
                session=session,
                scar_id=mutation.scar.id,
                reason="explicit user correction",
            )
            return opened.scar, (*mutation.events, *opened.events)

        scar, events = await asyncio.to_thread(_record)
        if scar.status == "regressed":
            requeued = await self._requeue_repair(session, scar)
            events = (*events, *requeued)
            scar = await asyncio.to_thread(self.store.get, scar.id)
        await self._publish(events)
        return await self._auto_route(session, scar)

    async def _conversational_correction(
        self,
        session: Session,
        run_id: str,
        user: Event,
        task_text: str,
        events: list[Event],
        terminal: Event,
    ) -> tuple[Scar | None, tuple[Event, ...]]:
        if not self.config.evolution.conversational_detection:
            return None, ()
        if not looks_like_correction(task_text):
            return None, ()
        assistant = next(
            (
                event
                for event in reversed(events)
                if event.type == "assistant.message" and event.payload.get("status") == "completed"
            ),
            None,
        )
        evidence_ids = [user.id]
        if assistant is not None:
            evidence_ids.append(assistant.id)
        evidence_ids.append(terminal.id)

        def _record() -> tuple[tuple[Scar, tuple[Event, ...]], bool]:
            signature = f"conversational-correction:{task_text.strip().casefold()[:120]}"
            existing = self.store.find_active_by_signature(session, signature)
            if existing is not None and existing.status in {"guarded", "healed"}:
                mutation = self.store.mark_regressed(
                    session=session,
                    scar_id=existing.id,
                    reason="the same correction returned after its repair",
                )
                return (mutation.scar, mutation.events), True
            if existing is not None:
                triggered, trigger_event = self.store.record_trigger(
                    session=session,
                    scar_id=existing.id,
                    run_id=run_id,
                    matched_on=["conversational_correction"],
                    causation_id=terminal.id,
                )
                return (triggered, (trigger_event,)), False
            mutation = self.store.record_candidate(
                session=session,
                title=f"Conversational correction: {task_text.strip()[:64]}",
                severity="medium",
                failure_signature=signature,
                description=(
                    f"The user corrected the prior result mid-conversation: {task_text[:500]}"
                ),
                expected_behavior=(
                    "Equivalent future requests must reflect the corrected expectation."
                ),
                evidence_event_ids=evidence_ids,
                trigger=ScarTrigger(workspace_paths=[session.working_directory]),
                run_id=run_id,
                detection="conversational_correction",
                causation_id=terminal.id,
            )
            opened = self.store.open(
                session=session,
                scar_id=mutation.scar.id,
                reason="explicit contradiction language in user message",
            )
            return (opened.scar, (*mutation.events, *opened.events)), True

        result, created = await asyncio.to_thread(_record)
        if not created:
            return None, ()
        scar, emitted = result
        if scar.status == "regressed":
            requeued = await self._requeue_repair(session, scar)
            emitted = (*emitted, *requeued)
        return scar, emitted

    async def _reviewer_classification(
        self,
        session: Session,
        run_id: str,
        task_text: str,
        events: list[Event],
        terminal: Event,
    ) -> tuple[Scar | None, tuple[Event, ...]]:
        """Optional reviewer-model pass for messages without explicit correction markers."""
        if not self.config.evolution.reviewer_model_enabled or not self.providers:
            return None, ()
        used = await asyncio.to_thread(
            self.store.count_background_model_calls_today, "evolution_review"
        )
        if used >= self.config.evolution.max_background_model_calls_per_day:
            return None, ()
        verdict = await self._classify_with_reviewer(session, run_id, task_text)
        if verdict is None:
            return None, ()
        assistant = next(
            (
                event
                for event in reversed(events)
                if event.type == "assistant.message" and event.payload.get("status") == "completed"
            ),
            terminal,
        )
        if not verdict:
            recorded = await asyncio.to_thread(
                self.ledger.append,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="correction.verdict",
                payload={"content": task_text[:500], "is_correction": False},
                causation_id=terminal.id,
                correlation_id=run_id,
            )
            await self._publish((recorded,))
            return None, ()

        def _record() -> tuple[Scar, tuple[Event, ...]]:
            signature = f"reviewed-correction:{task_text.strip().casefold()[:120]}"
            existing = self.store.find_active_by_signature(session, signature)
            if existing is not None:
                triggered, trigger_event = self.store.record_trigger(
                    session=session,
                    scar_id=existing.id,
                    run_id=run_id,
                    matched_on=["reviewer_classification"],
                    causation_id=terminal.id,
                )
                return triggered, (trigger_event,)
            mutation = self.store.record_candidate(
                session=session,
                title=f"Reviewed correction: {task_text.strip()[:60]}",
                severity="low",
                failure_signature=signature,
                description=(
                    "The reviewer model classified this message as correcting the prior "
                    f"result: {task_text[:500]}"
                ),
                expected_behavior=(
                    "Equivalent future requests must reflect the corrected expectation."
                ),
                evidence_event_ids=[assistant.id, terminal.id],
                trigger=ScarTrigger(workspace_paths=[session.working_directory]),
                run_id=run_id,
                detection="reviewer_classification",
                causation_id=terminal.id,
            )
            return mutation.scar, mutation.events

        scar, emitted = await asyncio.to_thread(_record)
        return scar, emitted

    async def _classify_with_reviewer(
        self, session: Session, run_id: str, task_text: str
    ) -> bool | None:
        profile_id = self.config.evolution.provider or session.provider
        provider = self.providers.get(profile_id)
        if provider is None:
            return None
        model = self.config.evolution.model or session.model
        reasoning = self.config.evolution.reasoning_effort or session.reasoning_effort
        requested = await self._append_request(
            session_id=session.id,
            agent_id=session.agent_id,
            payload={
                "provider": profile_id,
                "model": model,
                "reasoning_effort": reasoning,
                "agent_capsule_hash": "evolution-reviewer-v1",
                "purpose": "evolution_review",
            },
            correlation_id=run_id,
        )
        request = ModelRequest(
            model=model,
            system=REVIEWER_SYSTEM,
            messages=[ProviderMessage(role="user", content=task_text[:1000])],
            reasoning_effort=reasoning,
            max_tokens=512,
            temperature=0,
            tools=[classification_tool()],
            metadata={"purpose": "evolution_review", "run_id": run_id},
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
                        raise ValueError("evolution reviewer emitted an invalid tool call")
                    if event.tool_call.name:
                        name_parts.append(event.tool_call.name)
                    argument_parts.append(event.tool_call.arguments_delta)
                elif event.kind is StreamEventKind.COMPLETED:
                    completed = True
            if (
                not started
                or not completed
                or "".join(name_parts) != "submit_correction_classification"
            ):
                raise ValueError("evolution reviewer did not submit a classification")
            raw = JSON_OBJECT.validate_json("".join(argument_parts) or "{}")
            is_correction = bool(raw.get("is_correction", False))
            recorded = await asyncio.to_thread(
                self.ledger.append,
                session_id=session.id,
                run_id=run_id,
                agent_id=session.agent_id,
                event_type="correction.verdict",
                payload={"content": task_text[:500], "is_correction": is_correction},
                causation_id=requested.id,
                correlation_id=run_id,
            )
            await self._publish((recorded,))
            return is_correction
        except (ProviderError, ValueError):
            return None

    async def _append_request(
        self,
        *,
        session_id: str,
        agent_id: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> Event:
        event = await asyncio.to_thread(
            self.ledger.append,
            session_id=session_id,
            agent_id=agent_id,
            event_type="model.requested",
            payload=payload,
            correlation_id=correlation_id,
        )
        await self._publish((event,))
        return event

    async def _repeated_failures(
        self, session: Session, run_id: str, events: list[Event]
    ) -> tuple[list[Scar], list[Event], list[Event]]:
        run_signatures: dict[str, Event] = {}
        for event in events:
            signature = normalize_failure_signature(event)
            if signature is not None:
                run_signatures.setdefault(signature, event)
        if not run_signatures:
            return [], [], []
        recent = await asyncio.to_thread(
            self.ledger.list_recent_workspace_events,
            FAILURE_EVENT_TYPES,
            working_directory=session.working_directory,
            limit=200,
        )
        counts: dict[str, int] = {}
        for event in recent:
            signature = normalize_failure_signature(event)
            if signature is not None:
                counts[signature] = counts.get(signature, 0) + 1
        threshold = self.config.evolution.recurrence_threshold
        scars: list[Scar] = []
        triggers: list[Event] = []
        emitted: list[Event] = []
        for signature, sample in sorted(run_signatures.items()):
            if counts.get(signature, 0) < threshold:
                continue
            existing = await asyncio.to_thread(
                self.store.find_active_by_signature, session, signature
            )
            if existing is not None:
                _, trigger_event = await asyncio.to_thread(
                    self.store.record_trigger,
                    session=session,
                    scar_id=existing.id,
                    run_id=run_id,
                    matched_on=[f"failure_signature:{signature[:48]}"],
                    causation_id=sample.id,
                )
                triggers.append(trigger_event)
                continue
            scar, mutation_events = await asyncio.to_thread(
                self._open_repeated_failure_scar, session, run_id, signature, sample
            )
            scars.append(scar)
            emitted.extend(mutation_events)
        return scars, triggers, emitted

    def _open_repeated_failure_scar(
        self, session: Session, run_id: str, signature: str, sample: Event
    ) -> tuple[Scar, tuple[Event, ...]]:
        mutation = self.store.record_candidate(
            session=session,
            title=f"Repeated failure: {signature[:72]}",
            severity="medium",
            failure_signature=signature,
            description=(
                f"This failure signature recurred {self.config.evolution.recurrence_threshold} "
                f"times in this workspace. Sample: {sample.type} "
                f"({str(sample.payload.get('summary', ''))[:200]})."
            ),
            expected_behavior=(
                "The underlying cause must be repaired so the signature stops recurring."
            ),
            evidence_event_ids=[sample.id],
            trigger=ScarTrigger(tool_error_signatures=[failure_signature_hash(signature)]),
            run_id=run_id,
            detection="repeated_failure",
            causation_id=sample.id,
        )
        opened = self.store.open(
            session=session,
            scar_id=mutation.scar.id,
            reason=f"recurrence_threshold={self.config.evolution.recurrence_threshold}",
        )
        return opened.scar, (*mutation.events, *opened.events)

    async def _skill_regressions(
        self, session: Session, run_id: str, events: list[Event]
    ) -> tuple[list[Scar], tuple[Event, ...]]:
        loaded = [
            event
            for event in events
            if event.type == "skill.loaded" and event.payload.get("version_id")
        ]
        if not loaded:
            return [], ()
        failing = await asyncio.to_thread(
            self.skills.repeated_failure_versions, self.config.evolution.recurrence_threshold
        )
        failing_versions = {item.version_id: item.failures for item in failing}
        scars: list[Scar] = []
        emitted: list[Event] = []
        for event in loaded:
            version_id = str(event.payload["version_id"])
            failures = failing_versions.get(version_id)
            if failures is None:
                continue
            signature = f"skill-outcome:{version_id}"
            existing = await asyncio.to_thread(
                self.store.find_active_by_signature, session, signature
            )
            if existing is not None:
                continue
            scar, mutation_events = await asyncio.to_thread(
                self._open_skill_regression_scar,
                session,
                run_id,
                version_id,
                signature,
                failures,
            )
            scars.append(scar)
            emitted.extend(mutation_events)
        return scars, tuple(emitted)

    def _open_skill_regression_scar(
        self, session: Session, run_id: str, version_id: str, signature: str, failures: int
    ) -> tuple[Scar, tuple[Event, ...]]:
        mutation = self.store.record_candidate(
            session=session,
            title="Skill version repeatedly associated with failures",
            severity="high",
            failure_signature=signature,
            description=(
                f"Skill version {version_id} was loaded on {failures} runs whose outcomes "
                "failed or drew corrections."
            ),
            expected_behavior="The procedure must stop producing failed or corrected runs.",
            evidence_event_ids=[],
            trigger=ScarTrigger(skill_ids=[version_id]),
            run_id=run_id,
            detection="skill_outcome_regression",
            causation_id=None,
        )
        opened = self.store.open(
            session=session,
            scar_id=mutation.scar.id,
            reason="repeated skill-associated failures",
        )
        return opened.scar, (*mutation.events, *opened.events)

    async def _publish(self, events: Iterable[Event]) -> None:
        for event in events:
            await self.broker.publish(
                event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
            )


def classification_tool() -> ToolDefinition:
    return ToolDefinition(
        name="submit_correction_classification",
        description="Submit one correction classification for a user message.",
        input_schema={
            "type": "object",
            "properties": {
                "is_correction": {"type": "boolean"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["is_correction", "confidence"],
            "additionalProperties": False,
        },
    )
