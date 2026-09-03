import { Show, createEffect, createMemo, createSignal } from "solid-js";
import type { HamesEvent } from "../../api/types";
import type { StreamState } from "../sessionStream";
import { EventDetail } from "./EventDetail";
import { EventLedger } from "./EventLedger";
import { EventTimeline } from "./EventTimeline";
import { EventsToolbar } from "./EventsToolbar";
import { eventIsError, eventSummary, eventTypeLabel } from "./eventFormat";

interface EventsViewProps {
  events: readonly HamesEvent[];
  streamState: StreamState;
}

export function EventsView(props: EventsViewProps) {
  const [query, setQuery] = createSignal("");
  const [selectedId, setSelectedId] = createSignal<string>();
  const selected = createMemo(() => props.events.find((event) => event.id === selectedId()));
  const runs = createMemo(() => new Set(
    props.events.flatMap((event) => event.run_id ? [event.run_id] : []),
  ).size);
  const errors = createMemo(() => props.events.filter(eventIsError).length);
  const filtered = createMemo(() => {
    const needle = query().trim().toLowerCase();
    if (!needle) return props.events;
    return props.events.filter((event) =>
      `${event.type} ${eventTypeLabel(event.type)} ${eventSummary(event)} ${event.run_id ?? ""} ${event.agent_id ?? ""} ${JSON.stringify(event.payload)}`
        .toLowerCase()
        .includes(needle),
    );
  });
  const matchIds = createMemo(() => query().trim()
    ? new Set(filtered().map((event) => event.id))
    : undefined,
  );

  createEffect(() => {
    const events = props.events;
    const id = selectedId();
    if (id && !events.some((event) => event.id === id)) setSelectedId();
  });

  const select = (event: HamesEvent) => setSelectedId(event.id);

  return (
    <div class="events-view" data-events-view>
      <EventsToolbar
        count={filtered().length}
        total={props.events.length}
        runs={runs()}
        errors={errors()}
        query={query()}
        streamState={props.streamState}
        onQueryChanged={setQuery}
      />
      <EventTimeline
        events={props.events}
        selectedId={selectedId()}
        matchIds={matchIds()}
        onSelect={select}
      />
      <div class="event-ledger-layout" classList={{ inspecting: Boolean(selected()) }}>
        <EventLedger
          events={filtered()}
          allEvents={props.events}
          selectedId={selectedId()}
          resetKey={query()}
          onSelect={select}
        />
        <Show when={selected()}>
          {(event) => <EventDetail event={event()} onClose={() => setSelectedId()} />}
        </Show>
      </div>
    </div>
  );
}
