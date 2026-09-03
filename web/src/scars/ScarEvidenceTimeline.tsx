import { For } from "solid-js";
import type { TimelineItem } from "../api/types";
import { scarDate, scarLabel, shortScarId } from "./format";

export function ScarEvidenceTimeline(props: { evidence: TimelineItem[] }) {
  return (
    <div class="scar-evidence-list">
      <For each={props.evidence} fallback={<p class="scar-lineage-empty">No visible source events remain.</p>}>
        {(item) => (
          <article class="scar-evidence-item">
            <header>
              <div>
                <span class="scar-evidence-channel">{scarLabel(item.channel)}</span>
                <strong>{item.summary || scarLabel(item.event_type)}</strong>
              </div>
              <time datetime={item.created_at}>{scarDate(item.created_at)}</time>
            </header>
            <div class="scar-evidence-meta">
              <code>{item.event_type}</code>
              <span>event {shortScarId(item.event_id)}</span>
              <span>{item.run_id ? `run ${shortScarId(item.run_id)}` : "outside a run"}</span>
            </div>
            <details class="scar-payload-disclosure">
              <summary>Event payload</summary>
              <pre>{JSON.stringify(item.payload, null, 2)}</pre>
            </details>
          </article>
        )}
      </For>
    </div>
  );
}
