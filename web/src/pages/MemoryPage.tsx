import { For, Show } from "solid-js";
import type { MemoryRecord, MemoryValue } from "../api/types";
import { DetailHeading } from "../components/DetailHeading";
import { DetailStatStrip } from "../components/DetailStatStrip";
import { Markdown } from "../components/Markdown";
import { Separator } from "../components/Separator";
import { useMemoryDirectory } from "../memory/MemoryDirectory";

interface MemoryPageProps {
  memoryId: string;
}

function label(value: string): string {
  const words = value.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function percentage(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function valueText(value: MemoryValue): string {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function MemoryValueView(props: { value: MemoryValue }) {
  return typeof props.value === "string"
    ? <Markdown content={props.value} />
    : <pre class="memory-json-value">{valueText(props.value)}</pre>;
}

function MemoryDetail(props: { record: MemoryRecord }) {
  const record = () => props.record;
  return (
    <section class="page memory-page" aria-labelledby="memory-title">
      <DetailHeading
        id="memory-title"
        class="memory-heading"
        eyebrow={<>{label(record().layer)} memory</>}
        title={label(record().predicate)}
        summary={record().summary}
        context={<code>{record().subject}</code>}
      />

      <DetailStatStrip
        label="Memory status"
        items={[
          { label: "Status", value: label(record().status) },
          { label: "Visibility", value: label(record().visibility) },
          { label: "Confidence", value: percentage(record().confidence) },
          { label: "Importance", value: percentage(record().importance) },
        ]}
      />

      <Separator label="Stored value" />
      <div class="memory-value-panel">
        <MemoryValueView value={record().value} />
      </div>

      <Separator label="Context" />
      <dl class="memory-metadata">
        <div><dt>Origin</dt><dd>{label(record().origin_kind)}</dd></div>
        <div><dt>Updated</dt><dd>{formatDate(record().updated_at)}</dd></div>
        <div><dt>Agent</dt><dd>{record().owner_agent_id ?? "Shared"}</dd></div>
        <div><dt>Workspace</dt><dd>{record().workspace_path ?? "All workspaces"}</dd></div>
      </dl>

      <Show when={record().anchors.length > 0 || record().provenance_event_ids.length > 0}>
        <Separator label="Provenance" />
        <div class="memory-provenance">
          <Show when={record().anchors.length > 0}>
            <section>
              <h2>Anchors</h2>
              <ul>
                <For each={record().anchors}>{(anchor) => (
                  <li><span>{label(anchor.kind)}</span><code>{anchor.value}</code></li>
                )}</For>
              </ul>
            </section>
          </Show>
          <Show when={record().provenance_event_ids.length > 0}>
            <section>
              <h2>Source events</h2>
              <ul>
                <For each={record().provenance_event_ids}>{(eventId) => <li><code>{eventId}</code></li>}</For>
              </ul>
            </section>
          </Show>
        </div>
      </Show>
    </section>
  );
}

export function MemoryPage(props: MemoryPageProps) {
  const directory = useMemoryDirectory();
  const record = () => directory.records().find((candidate) => candidate.id === props.memoryId);
  return (
    <Show
      when={record()}
      keyed
      fallback={
        <section class="page memory-route-state">
          <Show when={directory.loading()}>
            <div class="memory-detail-loading"><span /><span /><span /></div>
          </Show>
          <Show when={directory.loaded() && !directory.loading()}>
            <div class="error-state">
              <div><span class="eyebrow">Memory</span><h2>This memory is not available.</h2><p>It may no longer be active or visible in this workspace.</p></div>
            </div>
          </Show>
        </section>
      }
    >
      {(selected) => <MemoryDetail record={selected} />}
    </Show>
  );
}
