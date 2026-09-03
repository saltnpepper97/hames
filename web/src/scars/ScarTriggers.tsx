import { For } from "solid-js";
import type { JsonValue, ScarTrigger } from "../api/types";
import { scarLabel } from "./format";

function values(value: JsonValue): string[] {
  if (Array.isArray(value)) return value.map((item) => typeof item === "string" ? item : JSON.stringify(item));
  if (value === null || value === "") return [];
  return [typeof value === "string" ? value : JSON.stringify(value)];
}

export function ScarTriggers(props: { trigger: ScarTrigger }) {
  const groups = () => Object.entries(props.trigger)
    .map(([key, value]) => ({ key, values: values(value) }))
    .filter((group) => group.values.length > 0);

  return (
    <div class="scar-trigger-groups">
      <For each={groups()} fallback={<p class="scar-lineage-empty">This Scar has no narrowed trigger conditions.</p>}>
        {(group) => (
          <section class="scar-trigger-group">
            <h3>{scarLabel(group.key)}</h3>
            <ul>
              <For each={group.values}>{(value) => <li><code>{value}</code></li>}</For>
            </ul>
          </section>
        )}
      </For>
    </div>
  );
}
