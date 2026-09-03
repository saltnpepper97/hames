import { For, createMemo } from "solid-js";
import type { HamesEvent } from "../../api/types";
import { Button } from "../../components/Button";
import {
  eventCategory,
  eventCategoryLabel,
  eventDuration,
  eventIsError,
  eventTypeLabel,
} from "./eventFormat";
import type { EventCategory } from "./eventFormat";

interface EventGraphProps {
  events: readonly HamesEvent[];
  selectedId?: string;
  onSelect: (event: HamesEvent) => void;
}

const categories: EventCategory[] = ["message", "reasoning", "tool", "decision", "runtime"];

function eventPositions(events: readonly HamesEvent[]): Map<string, number> {
  if (events.length < 2) return new Map(events.map((event) => [event.id, 50]));
  const times = events.map((item) => new Date(item.created_at).getTime());
  const validTimes = times.every(Number.isFinite);
  const minimumTime = validTimes ? Math.min(...times) : 0;
  const maximumTime = validTimes ? Math.max(...times) : 0;
  if (validTimes && maximumTime > minimumTime) {
    return new Map(events.map((event) => [
      event.id,
      2 + ((new Date(event.created_at).getTime() - minimumTime) / (maximumTime - minimumTime)) * 96,
    ]));
  }
  const minimumSequence = Math.min(...events.map((item) => item.sequence));
  const maximumSequence = Math.max(...events.map((item) => item.sequence));
  if (maximumSequence === minimumSequence) {
    return new Map(events.map((event) => [event.id, 50]));
  }
  return new Map(events.map((event) => [
    event.id,
    2 + ((event.sequence - minimumSequence) / (maximumSequence - minimumSequence)) * 96,
  ]));
}

export function EventGraph(props: EventGraphProps) {
  const runs = createMemo(() => new Set(props.events.flatMap((event) => event.run_id ? [event.run_id] : [])).size);
  const errors = createMemo(() => props.events.filter(eventIsError).length);
  const positions = createMemo(() => eventPositions(props.events));

  return (
    <section class="event-overview" aria-labelledby="event-overview-title">
      <div class="event-overview-heading">
        <div>
          <span class="eyebrow">Session activity</span>
          <h2 id="event-overview-title">Event map</h2>
        </div>
        <dl class="event-overview-stats">
          <div><dt>Events</dt><dd>{props.events.length}</dd></div>
          <div><dt>Runs</dt><dd>{runs()}</dd></div>
          <div><dt>Elapsed</dt><dd>{eventDuration(props.events)}</dd></div>
          <div><dt>Errors</dt><dd>{errors()}</dd></div>
        </dl>
      </div>
      <div class="event-graph" aria-label="Events over session time">
        <For each={categories}>
          {(category) => (
            <div class="event-graph-lane" data-category={category}>
              <span>{eventCategoryLabel(category)}</span>
              <div class="event-graph-track">
                <For each={props.events.filter((event) => eventCategory(event.type) === category)}>
                  {(event) => (
                    <Button
                      variant="bare"
                      class="event-graph-marker"
                      classList={{ selected: props.selectedId === event.id, error: eventIsError(event) }}
                      style={{ left: `${positions().get(event.id) ?? 50}%` }}
                      aria-label={`Event ${event.sequence}: ${eventTypeLabel(event.type)}`}
                      title={`#${event.sequence} ${event.type}`}
                      onClick={() => props.onSelect(event)}
                    />
                  )}
                </For>
              </div>
            </div>
          )}
        </For>
      </div>
    </section>
  );
}
