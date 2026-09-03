import { Show } from "solid-js";
import type { HamesEvent } from "../../api/types";
import { eventCategory, eventCategoryLabel, eventTime, eventTypeLabel } from "./eventFormat";

interface EventDetailProps {
  event?: HamesEvent;
}

function display(value: string | null): string {
  return value || "—";
}

export function EventDetail(props: EventDetailProps) {
  return (
    <aside class="event-detail" aria-label="Selected event details">
      <Show
        when={props.event}
        fallback={<div class="event-detail-empty">Select an event to inspect its durable record.</div>}
      >
        {(event) => (
          <>
            <header>
              <span class="event-category" data-category={eventCategory(event().type)}>
                {eventCategoryLabel(eventCategory(event().type))}
              </span>
              <h3>{eventTypeLabel(event().type)}</h3>
              <time datetime={event().created_at}>{eventTime(event().created_at)}</time>
            </header>
            <dl class="event-detail-metadata">
              <div><dt>Sequence</dt><dd>#{event().sequence}</dd></div>
              <div><dt>Run</dt><dd title={display(event().run_id)}>{display(event().run_id)}</dd></div>
              <div><dt>Agent</dt><dd>{display(event().agent_id)}</dd></div>
              <div><dt>Schema</dt><dd>v{event().schema_version}</dd></div>
            </dl>
            <div class="event-payload">
              <span class="eyebrow">Payload</span>
              <pre>{JSON.stringify(event().payload, null, 2)}</pre>
            </div>
          </>
        )}
      </Show>
    </aside>
  );
}
