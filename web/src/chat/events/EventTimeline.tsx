import { For, Index, Show, createMemo } from "solid-js";
import type { HamesEvent } from "../../api/types";
import { Button } from "../../components/Button";
import { eventIsError, eventTime, eventTypeLabel } from "./eventFormat";

type TimelineLane = "input" | "agent" | "tools";

interface TimelineSpan {
  event: HamesEvent;
  lane: TimelineLane;
  start: number;
  end: number;
  point: boolean;
}

interface TimelineModel {
  spans: TimelineSpan[];
  turns: number[];
  start: number;
  duration: number;
  mode: "time" | "sequence";
}

interface EventTimelineProps {
  events: readonly HamesEvent[];
  selectedId?: string;
  matchIds?: ReadonlySet<string>;
  onSelect: (event: HamesEvent) => void;
}

const lanes: { id: TimelineLane; label: string }[] = [
  { id: "input", label: "Input" },
  { id: "agent", label: "Model" },
  { id: "tools", label: "Tools" },
];

function stringPayload(event: HamesEvent, key: string): string {
  return typeof event.payload[key] === "string" ? event.payload[key] : "";
}

function numberPayload(event: HamesEvent, key: string): number | undefined {
  const value = event.payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function timelineLane(event: HamesEvent): TimelineLane {
  if (
    event.type.startsWith("user.") ||
    event.type.startsWith("context.") ||
    event.type.startsWith("memory.") ||
    event.type.startsWith("approval.") ||
    event.type.startsWith("question.") ||
    event.type.startsWith("queue.")
  ) return "input";
  if (
    event.type.startsWith("tool.") ||
    event.type.startsWith("terminal.") ||
    event.type.startsWith("skill.job.") ||
    event.type.startsWith("delegation.") ||
    event.type.startsWith("plugin.broker.")
  ) return "tools";
  return "agent";
}

function operationFamily(type: string): string {
  if (type.startsWith("tool.")) return "tool";
  if (type.startsWith("terminal.")) return "terminal";
  if (type.startsWith("model.response.")) return "model.response";
  if (type.startsWith("run.")) return "run";
  if (type.startsWith("context.compaction.")) return "context.compaction";
  if (type.startsWith("plan.execution.")) return "plan.execution";
  if (type.startsWith("memory.job.")) return "memory.job";
  if (type.startsWith("skill.job.")) return "skill.job";
  if (type.startsWith("dream.")) return "dream";
  if (type.startsWith("plugin.broker.")) return "plugin.broker";
  if (type.startsWith("delegation.")) return "delegation";
  return "";
}

function operationIdentity(event: HamesEvent): string {
  const fields = [
    "tool_call_id",
    "terminal_id",
    "compaction_id",
    "plan_id",
    "job_id",
    "dream_id",
    "child_session_id",
    "plugin_id",
  ];
  return fields.map((field) => stringPayload(event, field)).find(Boolean)
    ?? event.correlation_id
    ?? event.run_id
    ?? "session";
}

function operationPhase(event: HamesEvent): "start" | "end" | "point" {
  if (
    event.type.endsWith(".started") ||
    event.type === "tool.requested" ||
    event.type === "plugin.broker.requested" ||
    event.type === "delegation.requested"
  ) return "start";
  if (
    /\.(?:completed|failed|cancelled|stopped|preempted)$/.test(event.type) ||
    event.type === "tool.rejected"
  ) return "end";
  return "point";
}

function belongsOnTimeline(event: HamesEvent, activeStarts: ReadonlySet<string>): boolean {
  if (activeStarts.has(event.id)) return true;
  if (
    event.type === "user.message" ||
    event.type === "context.compiled" ||
    event.type === "memory.retrieved" ||
    event.type === "assistant.reasoning" ||
    event.type === "assistant.message"
  ) return true;

  const family = operationFamily(event.type);
  if (!family) return false;
  const phase = operationPhase(event);
  if (phase !== "end") return false;
  return [
    "run",
    "model.response",
    "tool",
    "terminal",
    "context.compaction",
    "skill.job",
    "plugin.broker",
    "delegation",
  ].includes(family);
}

function deriveTimeline(events: readonly HamesEvent[]): TimelineModel | undefined {
  if (events.length === 0) return undefined;
  const ordered = [...events].sort((left, right) => left.sequence - right.sequence);
  const timestamps = ordered.map((event) => new Date(event.created_at).getTime());
  const usesTime = timestamps.every(Number.isFinite) && Math.max(...timestamps) > Math.min(...timestamps);
  const value = (event: HamesEvent) => usesTime
    ? new Date(event.created_at).getTime()
    : event.sequence;
  const active = new Map<string, HamesEvent>();
  const consumed = new Set<string>();
  const pairedStarts = new Map<string, HamesEvent>();

  for (const event of ordered) {
    const family = operationFamily(event.type);
    if (!family) continue;
    const key = `${family}:${operationIdentity(event)}`;
    const phase = operationPhase(event);
    if (phase === "start") {
      active.set(key, event);
    } else if (phase === "end") {
      const start = active.get(key);
      if (start) {
        pairedStarts.set(event.id, start);
        consumed.add(start.id);
        active.delete(key);
      }
    }
  }
  const activeStarts = new Set([...active.values()].map((event) => event.id));

  const spans = ordered.flatMap<TimelineSpan>((event) => {
    if (consumed.has(event.id) || !belongsOnTimeline(event, activeStarts)) return [];
    const end = value(event);
    const recordedDuration = numberPayload(event, "duration_seconds");
    const paired = pairedStarts.get(event.id);
    let start = paired ? value(paired) : end;
    if (usesTime && recordedDuration !== undefined && recordedDuration > 0) {
      start = end - recordedDuration * 1000;
    }
    return [{
      event,
      lane: timelineLane(event),
      start: Math.min(start, end),
      end: Math.max(start, end),
      point: start === end,
    }];
  });
  const domainValues = spans.flatMap((span) => [span.start, span.end]);
  const start = Math.min(...domainValues);
  const end = Math.max(...domainValues);
  const duration = Math.max(1, end - start);
  return {
    spans,
    turns: ordered.filter((event) => event.type === "user.message").map(value),
    start,
    duration,
    mode: usesTime ? "time" : "sequence",
  };
}

function spanTitle(span: TimelineSpan, mode: TimelineModel["mode"]): string {
  const heading = `#${span.event.sequence} ${eventTypeLabel(span.event.type)}`;
  if (mode === "sequence") return heading;
  const started = new Date(span.start).toISOString();
  if (span.point) return `${heading}\n${eventTime(started)}`;
  const duration = span.end - span.start;
  return `${heading}\n${eventTime(started)} · ${duration < 1000 ? `${Math.round(duration)} ms` : `${(duration / 1000).toFixed(2)} s`}`;
}

export function EventTimeline(props: EventTimelineProps) {
  const model = createMemo(() => deriveTimeline(props.events));
  const percent = (value: number) => {
    const current = model();
    if (!current) return 0;
    const position = (value - current.start) / current.duration;
    return 1.25 + position * 97.5;
  };

  return (
    <section class="event-timeline" aria-label="Event timeline">
      <div class="event-timeline-labels" aria-hidden="true">
        <For each={lanes}>{(lane) => <span>{lane.label}</span>}</For>
      </div>
      <div class="event-timeline-track">
        <Show
          when={model()}
          fallback={<span class="event-timeline-empty">No durable events yet</span>}
        >
          {(current) => (
            <>
              <For each={current().turns}>
                {(turn) => (
                  <i
                    class="event-turn-boundary"
                    style={{ left: `${percent(turn)}%` }}
                    aria-hidden="true"
                  />
                )}
              </For>
              <Index each={current().spans}>
                {(span) => (
                  <Button
                    variant="bare"
                    class="event-timeline-span"
                    classList={{
                      selected: props.selectedId === span().event.id,
                      error: eventIsError(span().event),
                      filtered: Boolean(props.matchIds && !props.matchIds.has(span().event.id)),
                    }}
                    data-lane={span().lane}
                    data-point={span().point || undefined}
                    style={{
                      left: `${percent(span().start)}%`,
                      width: `${Math.max(0, percent(span().end) - percent(span().start))}%`,
                    }}
                    aria-label={`Event ${span().event.sequence}: ${eventTypeLabel(span().event.type)}`}
                    title={spanTitle(span(), current().mode)}
                    onClick={() => props.onSelect(span().event)}
                  />
                )}
              </Index>
            </>
          )}
        </Show>
      </div>
    </section>
  );
}
