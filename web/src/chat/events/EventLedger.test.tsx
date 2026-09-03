import { fireEvent, render, screen } from "@solidjs/testing-library";
import { describe, expect, it } from "vitest";
import type { HamesEvent } from "../../api/types";
import { EventLedger } from "./EventLedger";

function event(sequence: number): HamesEvent {
  return {
    id: `event-${sequence}`,
    sequence,
    session_id: "session-one",
    run_id: "run-one",
    agent_id: "default",
    type: "tool.completed",
    schema_version: 1,
    created_at: "2026-09-03T12:00:00Z",
    causation_id: null,
    correlation_id: null,
    payload: { name: "shell", summary: `Event ${sequence}` },
    blob_hash: null,
    payload_hash: `hash-${sequence}`,
    redaction_state: "clear",
  };
}

describe("EventLedger", () => {
  it("windows large durable histories instead of mounting every row", () => {
    const events = Array.from({ length: 1_000 }, (_, index) => event(index + 1));
    render(() => (
      <EventLedger
        events={events}
        allEvents={events}
        onSelect={() => undefined}
      />
    ));

    const viewport = document.querySelector<HTMLElement>(".event-ledger-viewport")!;
    Object.defineProperty(viewport, "clientHeight", { configurable: true, value: 300 });
    expect(document.querySelectorAll(".event-ledger-row").length).toBeLessThanOrEqual(21);

    viewport.scrollTop = 15_000;
    fireEvent.scroll(viewport);

    expect(screen.getByText("Event 501")).toBeInTheDocument();
    expect(document.querySelectorAll(".event-ledger-row").length).toBeLessThanOrEqual(30);
    expect(screen.queryByText("Event 1")).not.toBeInTheDocument();
  });
});
