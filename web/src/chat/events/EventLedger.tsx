import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
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
  resetKey?: string;
  onSelect: (event: HamesEvent) => void;
}

const rowHeight = 30;
const overscan = 10;

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
  const [scrollTop, setScrollTop] = createSignal(0);
  const [viewportHeight, setViewportHeight] = createSignal(0);
  let viewport!: HTMLDivElement;
  let observer: ResizeObserver | undefined;
  const turns = createMemo(() => {
    let turn = 0;
    const values = new Map<string, number>();
    for (const event of [...props.allEvents].sort((left, right) => left.sequence - right.sequence)) {
      if (event.type === "user.message") turn += 1;
      values.set(event.id, turn);
    }
    return values;
  });
  const visibleWindow = createMemo(() => {
    const count = props.events.length;
    const visible = Math.max(1, Math.ceil(viewportHeight() / rowHeight));
    const lastStart = Math.max(0, count - visible);
    const start = Math.min(lastStart, Math.max(0, Math.floor(scrollTop() / rowHeight) - overscan));
    const end = Math.min(count, start + visible + overscan * 2);
    return { start, end, events: props.events.slice(start, end) };
  });

  createEffect(() => {
    props.resetKey;
    if (!viewport) return;
    viewport.scrollTop = 0;
    setScrollTop(0);
  });

  createEffect(() => {
    const id = props.selectedId;
    if (!id || !viewport) return;
    const index = props.events.findIndex((event) => event.id === id);
    if (index < 0) return;
    const top = index * rowHeight;
    const bottom = top + rowHeight;
    if (top < viewport.scrollTop || bottom > viewport.scrollTop + viewport.clientHeight) {
      viewport.scrollTop = Math.max(0, top - Math.max(0, viewport.clientHeight - rowHeight) / 2);
      setScrollTop(viewport.scrollTop);
    }
  });

  onMount(() => {
    const measure = () => setViewportHeight(viewport.clientHeight);
    measure();
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(measure);
      observer.observe(viewport);
    }
  });
  onCleanup(() => observer?.disconnect());

  return (
    <div class="event-ledger" role="table" aria-label="Durable session events">
      <div class="event-ledger-header" role="row">
        <span role="columnheader">Event</span>
        <span role="columnheader">Content</span>
      </div>
      <div
        class="event-ledger-viewport"
        data-events-scroll
        role="rowgroup"
        ref={viewport}
        onScroll={(event) => setScrollTop(event.currentTarget.scrollTop)}
      >
        <Show
          when={props.events.length > 0}
          fallback={<div class="event-ledger-empty">No events match this view.</div>}
        >
          <div
            class="event-ledger-spacer"
            style={{ height: `${visibleWindow().start * rowHeight}px` }}
            aria-hidden="true"
          />
          <For each={visibleWindow().events}>
            {(event, localIndex) => {
              const index = () => visibleWindow().start + localIndex();
              return (
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
              );
            }}
          </For>
          <div
            class="event-ledger-spacer"
            style={{ height: `${(props.events.length - visibleWindow().end) * rowHeight}px` }}
            aria-hidden="true"
          />
        </Show>
      </div>
    </div>
  );
}
