import { createEffect, createMemo, createSignal, onCleanup } from "solid-js";
import type { Accessor } from "solid-js";
import { sessionEventStreamUrl } from "../api/client";
import { sessionEventTypes } from "../api/eventTypes";
import type { EventEnvelope, HamesEvent } from "../api/types";
import type { LiveOutput } from "./projection";

export type StreamState = "connecting" | "live" | "reconnecting";

function payloadText(payload: Record<string, unknown>): string {
  return typeof payload.text === "string" ? payload.text : "";
}

export function createSessionStream(sessionId: Accessor<string>) {
  const [events, setEvents] = createSignal<HamesEvent[]>([]);
  const [state, setState] = createSignal<StreamState>("connecting");
  const [liveOutput, setLiveOutput] = createSignal<LiveOutput>();
  const stableSessionId = createMemo(sessionId);

  createEffect(() => {
    const id = stableSessionId();
    setEvents([]);
    setLiveOutput();
    setState("connecting");

    type PendingAction =
      | { kind: "durable"; event: HamesEvent }
      | { kind: "reasoning" | "text"; runId: string; text: string };

    const pending: PendingAction[] = [];
    const seenEventIds = new Set<string>();
    let frame: number | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const flush = () => {
      frame = undefined;
      timer = undefined;
      if (pending.length === 0) return;

      const actions = pending.splice(0);
      let nextEvents = events();
      let nextLive = liveOutput();
      let eventsChanged = false;
      let liveChanged = false;

      for (const action of actions) {
        if (action.kind === "durable") {
          const incoming = action.event;
          if (!seenEventIds.has(incoming.id)) {
            seenEventIds.add(incoming.id);
            nextEvents = [...nextEvents, incoming];
            eventsChanged = true;
          }
          if (incoming.type === "assistant.reasoning" && nextLive?.runId === incoming.run_id) {
            nextLive = { ...nextLive, reasoning: "" };
            liveChanged = true;
          }
          if (incoming.type === "assistant.message" && nextLive?.runId === incoming.run_id) {
            nextLive = { ...nextLive, text: "" };
            liveChanged = true;
          }
          if (
            ["run.completed", "run.failed", "run.cancelled"].includes(incoming.type) &&
            nextLive?.runId === incoming.run_id
          ) {
            nextLive = undefined;
            liveChanged = true;
          }
          continue;
        }

        const current = nextLive?.runId === action.runId
          ? nextLive
          : { runId: action.runId, reasoning: "", text: "" };
        nextLive = {
          ...current,
          [action.kind]: current[action.kind] + action.text,
        };
        liveChanged = true;
      }

      if (eventsChanged) {
        nextEvents.sort((left, right) => left.sequence - right.sequence);
        setEvents(nextEvents);
      }
      if (liveChanged) setLiveOutput(nextLive);
    };

    const scheduleFlush = () => {
      if (frame !== undefined || timer !== undefined) return;
      if (typeof window.requestAnimationFrame === "function") {
        frame = window.requestAnimationFrame(flush);
      } else {
        timer = setTimeout(flush, 0);
      }
    };

    const source = new EventSource(sessionEventStreamUrl(id));
    const receive = (message: MessageEvent<string>) => {
      let envelope: EventEnvelope;
      try {
        envelope = JSON.parse(message.data) as EventEnvelope;
      } catch {
        return;
      }

      if (envelope.durable) {
        pending.push({ kind: "durable", event: envelope.event });
        scheduleFlush();
        return;
      }

      if (envelope.type === "response.reasoning_delta") {
        pending.push({
          kind: "reasoning",
          runId: envelope.run_id,
          text: payloadText(envelope.payload),
        });
        scheduleFlush();
      }
      if (envelope.type === "response.text_delta") {
        pending.push({
          kind: "text",
          runId: envelope.run_id,
          text: payloadText(envelope.payload),
        });
        scheduleFlush();
      }
    };

    for (const type of sessionEventTypes) source.addEventListener(type, receive as EventListener);
    source.onopen = () => setState("live");
    source.onerror = () => setState("reconnecting");

    onCleanup(() => {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      if (timer !== undefined) clearTimeout(timer);
      source.close();
    });
  });

  return {
    events,
    state,
    liveOutput: createMemo(() => liveOutput()),
  };
}
