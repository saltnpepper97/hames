"""Run-observation and explicit-correction pathways that create Scars."""

import asyncio
from collections.abc import Iterable

from hames.broker import EventBroker
from hames.config import HamesConfig
from hames.evolution import (
    Scar,
    ScarStore,
    ScarTrigger,
    failure_signature_hash,
    looks_like_correction,
    normalize_failure_signature,
)
from hames.ledger import Event, Ledger, Session
from hames.skills import SkillRegistry

FAILURE_EVENT_TYPES = {
    "tool.failed",
    "tool.rejected",
    "model.response.failed",
    "run.failed",
    "runtime.error",
    "policy.decided",
}

_TERMINAL_TYPES = {"run.completed", "run.failed", "run.cancelled"}


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
    ) -> None:
        self.ledger = ledger
        self.config = config
        self.broker = broker
        self.store = store
        self.skills = skills

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
        if scar is not None:
            created.append(scar)
        pending.extend(emitted)
        new_scars, triggers, emitted = await self._repeated_failures(session, run_id, events)
        created.extend(new_scars)
        pending.extend([*triggers, *emitted])
        skill_scars, skill_emitted = await self._skill_regressions(session, run_id, events)
        created.extend(skill_scars)
        pending.extend(skill_emitted)
        await self._publish(pending)
        return created

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
        await self._publish(events)
        return scar

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
        return scar, emitted

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
