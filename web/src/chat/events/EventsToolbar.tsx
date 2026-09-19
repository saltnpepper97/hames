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
        <span class="events-stat"><b>{props.count === props.total ? props.total : `${props.count}/${props.total}`}</b> events</span>
        <span class="events-stat events-run-count"><b>{props.runs}</b> {props.runs === 1 ? "run" : "runs"}</span>
        <Show when={props.errors > 0}>
          <span class="events-stat events-error-count"><b>{props.errors}</b> {props.errors === 1 ? "error" : "errors"}</span>
        </Show>
        <Show when={streamLabel()}>
          <span class="events-stream-state" data-state={props.streamState}><i aria-hidden="true" />{streamLabel()}</span>
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
