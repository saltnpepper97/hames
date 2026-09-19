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
  it("renders adjacent strong event summaries as Markdown", () => {
    const adjacent = event(1);
    adjacent.payload.summary = "**Analyzing...****Designing...**";
    render(() => (
      <EventLedger events={[adjacent]} allEvents={[adjacent]} onSelect={() => undefined} />
    ));

    expect(document.querySelectorAll(".event-summary strong")).toHaveLength(2);
    expect(document.querySelector(".event-summary")).toHaveTextContent("Analyzing... Designing...");
  });

  it("keeps HTML tool output inside a bounded inline preview", () => {
    const output = event(1);
    output.payload = {
      content: '<h1 class="event-timeline-span">Heading</h1><ul><li>Item</li></ul>'
        + '<pre>long\noutput</pre><strong class="event-turn-label">Result</strong>',
    };
    render(() => <EventLedger events={[output]} allEvents={[output]} onSelect={() => undefined} />);

    const summary = document.querySelector(".event-summary")!;
    expect(summary.querySelector("h1, ul, li, pre, .event-timeline-span, .event-turn-label")).toBeNull();
    expect(summary).toHaveTextContent("HeadingItemlong outputResult");
    expect(summary.querySelector("strong")).toHaveTextContent("Result");
  });

  it("previews a tool summary instead of its full document output", () => {
    const output = event(1);
    output.payload = { summary: "Wrote index.html", content: "<h1>Entire generated page</h1>" };
    render(() => <EventLedger events={[output]} allEvents={[output]} onSelect={() => undefined} />);
    expect(document.querySelector(".event-summary")).toHaveTextContent("Wrote index.html");
    expect(screen.queryByText("Entire generated page")).not.toBeInTheDocument();
  });

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
    expect(document.querySelectorAll(".event-kind-dot")).toHaveLength(
      document.querySelectorAll(".event-ledger-row").length,
    );
    expect(document.querySelectorAll(".event-kind > i")).toHaveLength(0);

    viewport.scrollTop = 15_000;
    fireEvent.scroll(viewport);

    expect(screen.getByText("Event 501")).toBeInTheDocument();
    expect(document.querySelectorAll(".event-ledger-row").length).toBeLessThanOrEqual(30);
    expect(screen.queryByText("Event 1")).not.toBeInTheDocument();
  });
});
