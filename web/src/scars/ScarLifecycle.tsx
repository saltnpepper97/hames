import { For, Show } from "solid-js";
import type { ScarTransition } from "../api/types";
import { scarDate, scarLabel, shortScarId } from "./format";

export function ScarLifecycle(props: { transitions: ScarTransition[] }) {
  return (
    <ol class="scar-lifecycle">
      <For each={props.transitions} fallback={<li class="scar-lineage-empty">No lifecycle events are visible.</li>}>
        {(transition, index) => (
          <li class="scar-lifecycle-item" classList={{ current: index() === props.transitions.length - 1 }}>
            <span class="scar-lifecycle-node" aria-hidden="true" />
            <div class="scar-lifecycle-copy">
              <div>
                <strong>{scarLabel(transition.event_type)}</strong>
                <time datetime={transition.created_at}>{scarDate(transition.created_at)}</time>
              </div>
              <Show when={transition.previous_status && transition.previous_status !== transition.status}>
                <span class="scar-transition-path">
                  {scarLabel(transition.previous_status!)} <span aria-hidden="true">→</span> {scarLabel(transition.status)}
                </span>
              </Show>
              <Show when={transition.reason}><p>{transition.reason}</p></Show>
              <code>{shortScarId(transition.event_id)}</code>
            </div>
          </li>
        )}
      </For>
    </ol>
  );
}
