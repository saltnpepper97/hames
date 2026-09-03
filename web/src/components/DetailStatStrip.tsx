import { For } from "solid-js";
import type { JSX } from "solid-js";

export interface DetailStat {
  label: string;
  value: JSX.Element;
}

interface DetailStatStripProps {
  label: string;
  items: DetailStat[];
}

export function DetailStatStrip(props: DetailStatStripProps) {
  return (
    <dl class="detail-stat-strip" aria-label={props.label}>
      <For each={props.items}>{(item) => (
        <div>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      )}</For>
    </dl>
  );
}
