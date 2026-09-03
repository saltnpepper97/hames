import { For, Show, createMemo } from "solid-js";
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
  allEvents: readonly HamesEvent[];
  selectedId?: string;
  onSelect: (event: HamesEvent) => void;
}

function EventLedgerRow(props: {
  event: HamesEvent;
  turn: number;
  groupStart: boolean;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <Button
      variant="bare"
      class="event-ledger-row"
      classList={{ selected: props.selected }}
      data-category={eventCategory(props.event.type)}
      data-turn-start={props.groupStart || undefined}
      role="row"
      aria-selected={props.selected}
      onClick={props.onSelect}
    >
      <span role="cell" class="event-kind">
        <Show when={props.groupStart}>
          <span class="event-turn-label">{props.turn > 0 ? `Turn ${props.turn}` : "Session"}</span>
        </Show>
        <i class="event-turn-rail" aria-hidden="true" />
        <i data-category={eventCategory(props.event.type)} />
        <strong>{eventTypeLabel(props.event.type)}</strong>
      </span>
      <span role="cell" class="event-summary">
        <span>{eventSummary(props.event)}</span>
        <time datetime={props.event.created_at}>{eventTime(props.event.created_at)}</time>
      </span>
    </Button>
  );
}

export function EventLedger(props: EventLedgerProps) {
  const turns = createMemo(() => {
    let turn = 0;
    const values = new Map<string, number>();
    for (const event of [...props.allEvents].sort((left, right) => left.sequence - right.sequence)) {
      if (event.type === "user.message") turn += 1;
      values.set(event.id, turn);
    }
    return values;
  });

  return (
    <div class="event-ledger" role="table" aria-label="Durable session events">
      <div class="event-ledger-header" role="row">
        <span role="columnheader">Event</span>
        <span role="columnheader">Content</span>
      </div>
      <Show
        when={props.events.length > 0}
        fallback={<div class="event-ledger-empty">No events match this view.</div>}
      >
        <For each={props.events}>
          {(event, index) => (
            <EventLedgerRow
              event={event}
              turn={turns().get(event.id) ?? 0}
              groupStart={
                index() === 0 ||
                event.type === "user.message" ||
                turns().get(props.events[index() - 1]?.id ?? "") !== turns().get(event.id)
              }
              selected={event.id === props.selectedId}
              onSelect={() => props.onSelect(event)}
            />
          )}
        </For>
      </Show>
    </div>
  );
}
