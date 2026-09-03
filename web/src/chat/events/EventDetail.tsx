import type { HamesEvent } from "../../api/types";
import { Button } from "../../components/Button";
import { eventCategory, eventCategoryLabel, eventTime, eventTypeLabel } from "./eventFormat";

interface EventDetailProps {
  event: HamesEvent;
  onClose: () => void;
}

function display(value: string | null): string {
  return value || "—";
}

export function EventDetail(props: EventDetailProps) {
  return (
    <aside class="event-detail" aria-label="Selected event details">
      <header>
        <div>
          <span class="event-category" data-category={eventCategory(props.event.type)}>
            {eventCategoryLabel(eventCategory(props.event.type))}
          </span>
          <h3>{eventTypeLabel(props.event.type)}</h3>
          <time datetime={props.event.created_at}>{eventTime(props.event.created_at)}</time>
        </div>
        <Button variant="icon" size="small" aria-label="Close event details" onClick={props.onClose}>×</Button>
      </header>
      <dl class="event-detail-metadata">
        <div><dt>Sequence</dt><dd>#{props.event.sequence}</dd></div>
        <div><dt>Run</dt><dd title={display(props.event.run_id)}>{display(props.event.run_id)}</dd></div>
        <div><dt>Agent</dt><dd>{display(props.event.agent_id)}</dd></div>
        <div><dt>Schema</dt><dd>v{props.event.schema_version}</dd></div>
      </dl>
      <div class="event-payload">
        <span class="eyebrow">Payload</span>
        <pre>{JSON.stringify(props.event.payload, null, 2)}</pre>
      </div>
    </aside>
  );
}
