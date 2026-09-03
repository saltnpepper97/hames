import { render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import type { HamesEvent } from "../../api/types";
import { EventTimeline } from "./EventTimeline";

function event(sequence: number, type: string): HamesEvent {
  return {
    id: `event-${sequence}`,
    sequence,
    session_id: "session-one",
    run_id: "run-one",
    agent_id: "default",
    type,
    schema_version: 1,
    created_at: `2026-09-03T12:00:0${sequence}Z`,
    causation_id: null,
    correlation_id: "correlation-one",
    payload: { name: "shell", tool_call_id: "tool-one" },
    blob_hash: null,
    payload_hash: `hash-${sequence}`,
    redaction_state: "clear",
  };
}

describe("EventTimeline", () => {
  it("collapses a noisy tool lifecycle into one execution span", () => {
    const events = [
      event(1, "tool.requested"),
      event(2, "policy.requested"),
      event(3, "policy.decided"),
      event(4, "model.tool_call"),
      event(5, "tool.started"),
      event(6, "tool.completed"),
    ];
    render(() => <EventTimeline events={events} onSelect={() => undefined} />);

    expect(document.querySelectorAll(".event-timeline-span")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Event 6: Tool · Completed" }))
      .toBeInTheDocument();
  });
});
