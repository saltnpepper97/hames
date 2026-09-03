import { For, Show } from "solid-js";
import type { HamesEvent } from "../../api/types";
import { Button } from "../../components/Button";
import {
  eventCategory,
  eventSummary,
  eventTime,
  eventTypeLabel,
} from "./eventFormat";

interface EventLedgerProps {
  events: readonly HamesEvent[];
  selectedId?: string;
  onSelect: (event: HamesEvent) => void;
}

function EventLedgerRow(props: {
  event: HamesEvent;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <Button
      variant="bare"
      class="event-ledger-row"
      classList={{ selected: props.selected }}
      role="row"
      aria-selected={props.selected}
      onClick={props.onSelect}
    >
      <span role="cell" class="event-sequence">{props.event.sequence}</span>
      <span role="cell" class="event-kind">
        <i data-category={eventCategory(props.event.type)} />
        <strong>{eventTypeLabel(props.event.type)}</strong>
      </span>
      <span role="cell" class="event-summary">{eventSummary(props.event)}</span>
      <time role="cell" datetime={props.event.created_at}>{eventTime(props.event.created_at)}</time>
    </Button>
  );
}

export function EventLedger(props: EventLedgerProps) {
  return (
    <div class="event-ledger" role="table" aria-label="Durable session events">
      <div class="event-ledger-header" role="row">
        <span role="columnheader">#</span>
        <span role="columnheader">Event</span>
        <span role="columnheader">Content</span>
        <span role="columnheader">Time</span>
      </div>
      <Show
        when={props.events.length > 0}
        fallback={<div class="event-ledger-empty">No events match this view.</div>}
      >
        <For each={props.events}>
          {(event) => (
            <EventLedgerRow
              event={event}
              selected={event.id === props.selectedId}
              onSelect={() => props.onSelect(event)}
            />
          )}
        </For>
      </Show>
    </div>
  );
}
