import { createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import type { Accessor } from "solid-js";
import { sessionEventStreamUrl } from "../api/client";
import type { EventEnvelope, HamesEvent } from "../api/types";
import type { LiveOutput } from "./projection";

export type StreamState = "connecting" | "live" | "reconnecting";

const eventTypes = [
  "user.message",
  "assistant.reasoning",
  "assistant.message",
  "run.started",
  "run.completed",
  "run.failed",
  "run.cancelled",
  "model.tool_call",
  "tool.requested",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "tool.rejected",
  "approval.requested",
  "approval.resolved",
  "question.requested",
  "question.answered",
  "runtime.notice",
  "runtime.error",
  "queue.enqueued",
  "queue.removed",
  "response.reasoning_delta",
  "response.text_delta",
  "response.tool_call_delta",
] as const;

function payloadText(payload: Record<string, unknown>): string {
  return typeof payload.text === "string" ? payload.text : "";
}

export function createSessionStream(sessionId: Accessor<string>) {
  const [events, setEvents] = createSignal<HamesEvent[]>([]);
  const [state, setState] = createSignal<StreamState>("connecting");
  const [liveOutput, setLiveOutput] = createSignal<LiveOutput>();

  createEffect(() => {
    const id = sessionId();
    setEvents([]);
    setLiveOutput();
    setState("connecting");

    const source = new EventSource(sessionEventStreamUrl(id));
    const receive = (message: MessageEvent<string>) => {
      let envelope: EventEnvelope;
      try {
        envelope = JSON.parse(message.data) as EventEnvelope;
      } catch {
        return;
      }

      if (envelope.durable) {
        const incoming = envelope.event;
        setEvents((current) => {
          if (current.some((event) => event.id === incoming.id)) return current;
          return [...current, incoming].sort((left, right) => left.sequence - right.sequence);
        });
        if (incoming.type === "assistant.reasoning") {
          setLiveOutput((current) =>
            current?.runId === incoming.run_id ? { ...current, reasoning: "" } : current,
          );
        }
        if (incoming.type === "assistant.message") {
          setLiveOutput((current) =>
            current?.runId === incoming.run_id ? { ...current, text: "" } : current,
          );
        }
        if (["run.completed", "run.failed", "run.cancelled"].includes(incoming.type)) {
          setLiveOutput((current) => (current?.runId === incoming.run_id ? undefined : current));
        }
        return;
      }

      if (envelope.type === "response.reasoning_delta") {
        setLiveOutput((current) => ({
          runId: envelope.run_id,
          reasoning: (current?.runId === envelope.run_id ? current.reasoning : "") +
            payloadText(envelope.payload),
          text: current?.runId === envelope.run_id ? current.text : "",
        }));
      }
      if (envelope.type === "response.text_delta") {
        setLiveOutput((current) => ({
          runId: envelope.run_id,
          reasoning: current?.runId === envelope.run_id ? current.reasoning : "",
          text: (current?.runId === envelope.run_id ? current.text : "") +
            payloadText(envelope.payload),
        }));
      }
    };

    for (const type of eventTypes) source.addEventListener(type, receive as EventListener);
    source.onopen = () => setState("live");
    source.onerror = () => setState("reconnecting");

    onCleanup(() => source.close());
  });

  return {
    events,
    state,
    liveOutput: createMemo(() => liveOutput()),
  };
}
