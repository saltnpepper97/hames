import { Show } from "solid-js";
import type { StreamState } from "../sessionStream";
import { TextField } from "../../components/FormField";

interface EventsToolbarProps {
  count: number;
  total: number;
  runs: number;
  errors: number;
  query: string;
  streamState: StreamState;
  onQueryChanged: (query: string) => void;
}

export function EventsToolbar(props: EventsToolbarProps) {
  const streamLabel = () => props.streamState === "reconnecting"
    ? "Reconnecting"
    : props.streamState === "connecting" ? "Loading" : "";

  return (
    <header class="events-toolbar">
      <div class="events-toolbar-summary">
        <strong>Events</strong>
        <span>{props.count === props.total ? props.total : `${props.count} of ${props.total}`}</span>
        <span>{props.runs} {props.runs === 1 ? "run" : "runs"}</span>
        <Show when={props.errors > 0}>
          <span class="events-error-count">{props.errors} {props.errors === 1 ? "error" : "errors"}</span>
        </Show>
        <Show when={streamLabel()}>
          <span class="events-stream-state" data-state={props.streamState}>{streamLabel()}</span>
        </Show>
      </div>
      <TextField
        label="Filter events"
        type="search"
        value={props.query}
        placeholder="Filter events"
        onInput={(event) => props.onQueryChanged(event.currentTarget.value)}
      />
    </header>
  );
}
