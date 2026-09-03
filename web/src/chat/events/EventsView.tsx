import { createEffect, createMemo, createSignal } from "solid-js";
import type { HamesEvent } from "../../api/types";
import { TextField } from "../../components/FormField";
import type { StreamState } from "../sessionStream";
import { EventDetail } from "./EventDetail";
import { EventGraph } from "./EventGraph";
import { EventLedger } from "./EventLedger";
import { eventSummary, eventTypeLabel } from "./eventFormat";

interface EventsViewProps {
  events: readonly HamesEvent[];
  streamState: StreamState;
}

export function EventsView(props: EventsViewProps) {
  const [query, setQuery] = createSignal("");
  const [selectedId, setSelectedId] = createSignal<string>();
  const selected = createMemo(() => props.events.find((event) => event.id === selectedId()));
  const filtered = createMemo(() => {
    const needle = query().trim().toLowerCase();
    if (!needle) return props.events;
    return props.events.filter((event) =>
      `${event.type} ${eventTypeLabel(event.type)} ${eventSummary(event)} ${event.run_id ?? ""} ${event.agent_id ?? ""} ${JSON.stringify(event.payload)}`
        .toLowerCase()
        .includes(needle),
    );
  });

  createEffect(() => {
    const events = props.events;
    if (events.length > 0 && !events.some((event) => event.id === selectedId())) {
      setSelectedId(events.at(-1)?.id);
    }
  });

  const select = (event: HamesEvent) => setSelectedId(event.id);

  return (
    <div class="events-scroll" data-events-scroll>
      <div class="events-content">
        <EventGraph events={props.events} selectedId={selectedId()} onSelect={select} />
        <div class="events-toolbar">
          <div>
            <h2>Events</h2>
            <span>{filtered().length} of {props.events.length}</span>
          </div>
          <TextField
            label="Filter events"
            value={query()}
            placeholder="Type, content, run, or agent"
            onInput={(event) => setQuery(event.currentTarget.value)}
          />
        </div>
        <div class="event-ledger-layout">
          <EventLedger
            events={filtered()}
            selectedId={selectedId()}
            onSelect={select}
          />
          <EventDetail event={selected()} />
        </div>
        <div class="events-stream-note" data-state={props.streamState}>
          {props.streamState === "live"
            ? "Following durable gateway events"
            : props.streamState === "reconnecting"
              ? "Reconnecting to the event stream"
              : "Loading durable history"}
        </div>
      </div>
    </div>
  );
}
