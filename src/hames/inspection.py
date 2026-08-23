"""Read-only observability projections derived exclusively from the event ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

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


def session_runs(ledger: Ledger, session_id: str) -> list[RunSummary]:
    history = ledger.replay(session_id)
    run_ids = list(dict.fromkeys(event.run_id for event in history if event.run_id is not None))
    return [_run_summary(run_id, ledger.list_run_events(run_id)) for run_id in run_ids]


def session_usage(ledger: Ledger, session_id: str) -> UsageProjection:
    return _usage(ledger.replay(session_id))


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
