import { Show } from "solid-js";
import type { JSX } from "solid-js";

interface SeparatorProps {
  label?: string;
  meta?: JSX.Element;
  class?: string;
}

export function Separator(props: SeparatorProps) {
  return (
    <div
      class={`ui-separator ${props.class ?? ""}`}
      role="separator"
      aria-label={props.label}
    >
      <Show when={props.label}>
        <span class="ui-separator-label">{props.label}</span>
      </Show>
      <span class="ui-separator-rule" aria-hidden="true" />
      <Show when={props.meta}>
        <span class="ui-separator-meta">{props.meta}</span>
      </Show>
    </div>
  );
}
