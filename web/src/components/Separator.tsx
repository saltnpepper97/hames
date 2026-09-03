import { Show } from "solid-js";
import type { JSX } from "solid-js";
import { Icon } from "../shell/icons";
import { Button } from "./Button";

interface SeparatorProps {
  label?: string;
  meta?: JSX.Element;
  class?: string;
  expanded?: boolean;
  controls?: string;
  onToggle?: () => void;
}

export function Separator(props: SeparatorProps) {
  return (
    <div
      class={`ui-separator ${props.class ?? ""}`}
      role="separator"
      aria-label={props.label}
    >
      <Show when={props.label && props.onToggle} fallback={
        <Show when={props.label}>
          <span class="ui-separator-label">{props.label}</span>
        </Show>
      }>
        <Button
          variant="bare"
          size="small"
          class="ui-separator-toggle"
          aria-expanded={props.expanded}
          aria-controls={props.controls}
          aria-label={`${props.expanded ? "Collapse" : "Expand"} ${props.label}`}
          onClick={() => props.onToggle?.()}
        >
          <Icon name="action.expand" size={13} />
          <span class="ui-separator-label">{props.label}</span>
        </Button>
      </Show>
      <span class="ui-separator-rule" aria-hidden="true" />
      <Show when={props.meta}>
        <span class="ui-separator-meta">{props.meta}</span>
      </Show>
    </div>
  );
}
