"""Read-only observability projections derived exclusively from the event ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from hames.context import ContextManifest
from hames.ledger import Event, Ledger, Session
from hames.providers.base import JSON_OBJECT, JsonValue


class InspectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageProjection(InspectionModel):
    estimated_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    provider_reported_cost: float = 0.0
    model_requests: int = 0


class AgentUsageProjection(InspectionModel):
    agent_id: str
    session_count: int = 0
    usage: UsageProjection = Field(default_factory=UsageProjection)
    tool_calls: int = 0
    tool_duration_seconds: float = 0.0
    child_wall_seconds: float = 0.0
    errors: int = 0


class TimelineItem(InspectionModel):
    sequence: int
    event_id: str
    session_id: str
    run_id: str | None
    created_at: str
    event_type: str
    channel: str
    summary: str
    payload: dict[str, JsonValue]


class ContextInspection(InspectionModel):
    event_id: str
    session_id: str
    run_id: str
    manifest: ContextManifest
    request_snapshot: dict[str, JsonValue]


class RunSummary(InspectionModel):
    run_id: str
    session_id: str
    status: str
    started_at: str | None
    completed_at: str | None
    model_requests: int
    tool_calls: int
    usage: UsageProjection


class RunInspection(RunSummary):
    timeline: list[TimelineItem]
    contexts: list[ContextInspection]


class ScarRepairView(InspectionModel):
    id: str
    version: int
    repair_layer: str
    risk: str
    required_authority: str
    status: str
    previous_scar_status: str
    rationale: str
    proposal: dict[str, JsonValue]
    created_by: str
    created_at: str
    decided_at: str | None


class ScarEvaluationView(InspectionModel):
    event_id: str
    repair_id: str
    kind: str
    status: str
    score: float
    report: dict[str, JsonValue]
    created_at: str


class ScarTransitionView(InspectionModel):
    event_id: str
    event_type: str
    previous_status: str | None
    status: str
    reason: str
    created_at: str


class ScarInspection(InspectionModel):
    scar_id: str
    session_id: str
    title: str
    scope: str
    status: str
    severity: str
    detection: str
    failure_signature: str
    description: str
    expected_behavior: str
    trigger: dict[str, JsonValue]
    repair_layer: str | None
    repair_reference: str | None
    successful_guard_count: int
    regression_count: int
    created_at: str
    updated_at: str
    evidence_timeline: list[TimelineItem]
    transitions: list[ScarTransitionView]
    repairs: list[ScarRepairView]
    evaluations: list[ScarEvaluationView]
    explanation: str


def session_runs(ledger: Ledger, session_id: str) -> list[RunSummary]:
    history = ledger.replay(session_id)
    run_ids = list(dict.fromkeys(event.run_id for event in history if event.run_id is not None))
    return [_run_summary(run_id, ledger.list_run_events(run_id)) for run_id in run_ids]


def session_usage(ledger: Ledger, session_id: str) -> UsageProjection:
    return _usage(ledger.replay(session_id))


def agent_usage(ledger: Ledger, agent_id: str) -> AgentUsageProjection:
    """Aggregate only locally-owned events so branch replay cannot double count usage."""

    events = [
        event
        for session in ledger.list_sessions()
        for event in ledger.list_events(session.id)
        if event.agent_id == agent_id
    ]
    usage = _usage(events)
    tool_events = [
        event
        for event in events
        if event.type in {"tool.completed", "tool.failed", "tool.rejected"}
    ]
    delegation_events = [event for event in events if event.type == "delegation.completed"]
    return AgentUsageProjection(
        agent_id=agent_id,
        session_count=len({event.session_id for event in events}),
        usage=usage,
        tool_calls=len(tool_events),
        tool_duration_seconds=sum(
            float(event.payload.get("duration_seconds", 0.0)) for event in tool_events
        ),
        child_wall_seconds=sum(
            float(event.payload.get("duration_seconds", 0.0)) for event in delegation_events
        ),
        errors=sum(
            event.type in {"run.failed", "runtime.error", "delegation.failed"} for event in events
        ),
    )


def inspect_run(ledger: Ledger, run_id: str) -> RunInspection:
    events = ledger.list_run_events(run_id)
    if not events:
        raise KeyError(run_id)
    summary = _run_summary(run_id, events)
    timeline_events = list(events)
    started = next((event for event in events if event.type == "run.started"), None)
    if started is not None and started.causation_id:
        try:
            user_event = ledger.get_event(started.causation_id)
        except KeyError:
            pass
        else:
            if user_event.type == "user.message":
                timeline_events.insert(0, user_event)
    contexts = [
        inspect_context(ledger, event.id) for event in events if event.type == "context.compiled"
    ]
    return RunInspection(
        **summary.model_dump(),
        timeline=[_timeline(event) for event in timeline_events],
        contexts=contexts,
    )


def inspect_scar(
    ledger: Ledger,
    store: Any,
    session_id: str,
    scar_id: str,
) -> ScarInspection:
    """Full lineage of one scar: evidence, transitions, repairs, evaluations."""
    from hames.evolution import Scar, ScarStore  # local import avoids module cycles

    assert isinstance(store, ScarStore)
    session = ledger.get_session(session_id)
    scar = store.get_visible(session, scar_id)
    assert isinstance(scar, Scar)
    evidence: list[TimelineItem] = []
    for event_id in scar.evidence_event_ids:
        try:
            event = ledger.get_event(event_id)
        except KeyError:
            continue
        evidence.append(_timeline(event))
    scar_events = [
        event
        for event in ledger.list_events(session.id)
        if event.type.startswith("scar.") and event.payload.get("scar_id") == scar_id
    ]
    transitions = [
        ScarTransitionView(
            event_id=event.id,
            event_type=event.type,
            previous_status=cast(str | None, event.payload.get("previous_status")),
            status=str(event.payload.get("status", "")),
            reason=str(event.payload.get("reason", "")),
            created_at=event.created_at,
        )
        for event in scar_events
        if event.type
        in {
            "scar.recorded",
            "scar.edited",
            "scar.opened",
            "scar.dismissed",
            "scar.repair_proposed",
            "scar.guarded",
            "scar.healed",
            "scar.regressed",
        }
    ]
    evaluations = [
        ScarEvaluationView(
            event_id=event.id,
            repair_id=str(event.payload.get("repair_id", "")),
            kind=str(event.payload.get("kind", "")),
            status=str(event.payload.get("status", "")),
            score=float(event.payload.get("score", 0.0)),
            report=JSON_OBJECT.validate_python(event.payload.get("report", {})),
            created_at=event.created_at,
        )
        for event in scar_events
        if event.type == "scar.repair.evaluated"
    ]
    repairs = [
        ScarRepairView(
            id=repair.id,
            version=repair.version,
            repair_layer=repair.repair_layer,
            risk=repair.risk,
            required_authority=repair.required_authority,
            status=repair.status,
            previous_scar_status=repair.previous_scar_status,
            rationale=repair.rationale,
            proposal=repair.proposal,
            created_by=repair.created_by,
            created_at=repair.created_at,
            decided_at=repair.decided_at,
        )
        for repair in store.repairs_for_scar(scar_id)
    ]
    detection_reasons = {
        "explicit_correction": (
            "The user explicitly corrected Hames with /correct; the user's own statement is "
            "the authoritative diagnosis."
        ),
        "conversational_correction": (
            "The user's message contained explicit contradiction or correction language "
            "referring to the prior result."
        ),
        "reviewer_classification": (
            "The reviewer model classified the user message as correcting a prior result "
            "(low severity pending stronger evidence)."
        ),
        "repeated_failure": (
            "The same normalized failure signature recurred past the configured threshold "
            "in this workspace."
        ),
        "skill_outcome_regression": (
            "A loaded Skill version was repeatedly associated with failed or corrected runs."
        ),
    }
    return ScarInspection(
        scar_id=scar.id,
        session_id=session.id,
        title=scar.title,
        scope=scar.scope,
        status=scar.status,
        severity=scar.severity,
        detection=scar.detection,
        failure_signature=scar.failure_signature,
        description=scar.description,
        expected_behavior=scar.expected_behavior,
        trigger=JSON_OBJECT.validate_python(scar.trigger.model_dump(mode="json")),
        repair_layer=scar.repair_layer,
        repair_reference=scar.repair_reference,
        successful_guard_count=scar.successful_guard_count,
        regression_count=scar.regression_count,
        created_at=scar.created_at,
        updated_at=scar.updated_at,
        evidence_timeline=evidence,
        transitions=transitions,
        repairs=repairs,
        evaluations=evaluations,
        explanation=detection_reasons.get(scar.detection, f"Detected via {scar.detection}."),
    )


def inspect_context(ledger: Ledger, event_id: str) -> ContextInspection:
    event = ledger.get_event(event_id)
    if event.type != "context.compiled" or event.run_id is None:
        raise ValueError("event is not a compiled context manifest")
    manifest = ContextManifest.model_validate(event.payload)
    encoded = ledger.blob_store.read(manifest.request_snapshot_blob_hash)
    actual_hash = hashlib.sha256(encoded).hexdigest()
    if actual_hash != manifest.request_hash:
        raise ValueError("context request snapshot hash does not match its manifest")
    snapshot = JSON_OBJECT.validate_json(encoded)
    return ContextInspection(
        event_id=event.id,
        session_id=event.session_id,
        run_id=event.run_id,
        manifest=manifest,
        request_snapshot=snapshot,
    )


def export_transcript(
    ledger: Ledger, session: Session, format: Literal["markdown", "jsonl"]
) -> str:
    events = ledger.replay(session.id)
    if format == "jsonl":
        header: dict[str, JsonValue] = {
            "record_type": "hames.audit.header",
            "derived": True,
            "provenance_authority": "event-ledger",
            "session": cast(dict[str, JsonValue], session.model_dump(mode="json")),
        }
        lines = [json.dumps(header, separators=(",", ":"), sort_keys=True)]
        for event in events:
            item: dict[str, JsonValue] = {
                "record_type": "hames.audit.event",
                "derived": True,
                "channel": _channel(event.type),
                "event": cast(dict[str, JsonValue], event.model_dump(mode="json")),
            }
            lines.append(json.dumps(item, separators=(",", ":"), sort_keys=True))
        return "\n".join(lines) + "\n"

    lines = [
        "# Hames audit transcript",
        "",
        "> Derived view only. The Hames event ledger is the provenance authority.",
        "",
        f"- Session: `{session.id}`",
        f"- Workspace: `{session.working_directory}`",
        f"- Provider/model: `{session.provider}` / `{session.model}`",
        f"- Parent session: `{session.parent_session_id or 'none'}`",
        "",
    ]
    for event in events:
        lines.extend(
            [
                f"## {event.sequence} · {_channel(event.type)} · `{event.type}`",
                "",
                f"Event `{event.id}` · session `{event.session_id}` · "
                f"run `{event.run_id or 'none'}`",
                "",
            ]
        )
        content = event.payload.get("content")
        if isinstance(content, str) and content:
            lines.extend([content, ""])
        lines.extend(
            [
                "```json",
                json.dumps(event.payload, indent=2, sort_keys=True, ensure_ascii=False),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def _run_summary(run_id: str, events: list[Event]) -> RunSummary:
    if not events:
        raise KeyError(run_id)
    terminal = next((event for event in reversed(events) if event.type.startswith("run.")), None)
    status = {
        "run.completed": "completed",
        "run.failed": "failed",
        "run.cancelled": "cancelled",
    }.get(terminal.type if terminal else "", "active")
    started = next((event for event in events if event.type == "run.started"), None)
    return RunSummary(
        run_id=run_id,
        session_id=events[0].session_id,
        status=status,
        started_at=started.created_at if started else None,
        completed_at=terminal.created_at if terminal and status != "active" else None,
        model_requests=sum(event.type == "model.requested" for event in events),
        tool_calls=sum(event.type == "model.tool_call" for event in events),
        usage=_usage(events),
    )


def _usage(events: list[Event]) -> UsageProjection:
    result = UsageProjection()
    for event in events:
        if event.type == "context.compiled":
            result.estimated_input_tokens += int(event.payload.get("estimated_input_tokens", 0))
        elif event.type == "model.requested":
            result.model_requests += 1
        elif event.type == "model.usage":
            result.input_tokens += int(event.payload.get("input_tokens", 0))
            result.output_tokens += int(event.payload.get("output_tokens", 0))
            result.cached_input_tokens += int(event.payload.get("cached_input_tokens") or 0)
            result.reasoning_tokens += int(event.payload.get("reasoning_tokens") or 0)
            result.provider_reported_cost += float(event.payload.get("provider_reported_cost") or 0)
    return result


def _timeline(event: Event) -> TimelineItem:
    return TimelineItem(
        sequence=event.sequence,
        event_id=event.id,
        session_id=event.session_id,
        run_id=event.run_id,
        created_at=event.created_at,
        event_type=event.type,
        channel=_channel(event.type),
        summary=_summary(event),
        payload=JSON_OBJECT.validate_python(event.payload),
    )


def _channel(event_type: str) -> str:
    if event_type == "user.message":
        return "user"
    if event_type == "assistant.reasoning":
        return "thinking"
    if event_type == "assistant.message":
        return "answer"
    if event_type.startswith("tool.") or event_type == "model.tool_call":
        return "tool"
    if event_type.startswith("policy.") or event_type.startswith("approval."):
        return "policy"
    if event_type == "context.compiled":
        return "context"
    if event_type.startswith("memory."):
        return "memory"
    if event_type.startswith("skill."):
        return "skill"
    if (
        event_type.startswith("scar.")
        or event_type.startswith("context.rule.")
        or event_type.startswith("policy.rule.")
        or event_type == "correction.verdict"
        or event_type == "user.correction"
    ):
        return "evolution"
    if event_type == "model.usage":
        return "usage"
    if event_type.endswith("failed") or event_type == "runtime.error":
        return "failure"
    return "lifecycle"


def _summary(event: Event) -> str:
    for key in ("summary", "message", "content", "finish_reason", "decision", "status"):
        value = event.payload.get(key)
        if isinstance(value, str) and value:
            return value[:240]
    if event.type == "context.compiled":
        selected = event.payload.get("selected_sources", [])
        omitted = event.payload.get("omitted_sources", [])
        return f"selected {len(selected)} sources; omitted {len(omitted)}"
    return event.type
