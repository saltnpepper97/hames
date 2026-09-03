import { For, Show } from "solid-js";
import type { ScarEvaluation, ScarRepair } from "../api/types";
import { scarDate, scarLabel, shortScarId } from "./format";

function score(value: number): string {
  return value >= 0 && value <= 1 ? `${Math.round(value * 100)}%` : String(value);
}

export function ScarRepairCard(props: { repair: ScarRepair; evaluations: ScarEvaluation[] }) {
  const evaluations = () => props.evaluations.filter((item) => item.repair_id === props.repair.id);
  return (
    <article class="scar-repair-card">
      <header>
        <div>
          <span class="eyebrow">Repair {props.repair.version}</span>
          <h3>{scarLabel(props.repair.repair_layer)}</h3>
        </div>
        <div class="scar-repair-badges">
          <span data-status={props.repair.status}>{scarLabel(props.repair.status)}</span>
          <span data-risk={props.repair.risk}>{scarLabel(props.repair.risk)} risk</span>
        </div>
      </header>
      <p class="scar-repair-rationale">{props.repair.rationale}</p>
      <dl>
        <div><dt>Authority</dt><dd>{scarLabel(props.repair.required_authority)}</dd></div>
        <div><dt>Created by</dt><dd>{scarLabel(props.repair.created_by)}</dd></div>
        <div><dt>Created</dt><dd>{scarDate(props.repair.created_at)}</dd></div>
        <div><dt>Repair ID</dt><dd><code>{shortScarId(props.repair.id)}</code></dd></div>
      </dl>
      <details class="scar-payload-disclosure">
        <summary>Repair proposal</summary>
        <pre>{JSON.stringify(props.repair.proposal, null, 2)}</pre>
      </details>
      <Show when={evaluations().length > 0}>
        <div class="scar-evaluations">
          <For each={evaluations()}>{(evaluation) => (
            <details class="scar-evaluation">
              <summary>
                <span>{scarLabel(evaluation.kind)} evaluation</span>
                <strong>{scarLabel(evaluation.status)} · {score(evaluation.score)}</strong>
              </summary>
              <pre>{JSON.stringify(evaluation.report, null, 2)}</pre>
            </details>
          )}</For>
        </div>
      </Show>
    </article>
  );
}
