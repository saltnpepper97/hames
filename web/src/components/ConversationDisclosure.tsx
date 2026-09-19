import { Show } from "solid-js";
import type { JSX, ParentProps } from "solid-js";
import { Icon } from "../shell/icons";
import type { SemanticIconName } from "../shell/icons";

export type ConversationDisclosureState = "idle" | "running" | "success" | "warning" | "error";

interface ConversationDisclosureProps extends ParentProps {
  class?: string;
  icon: SemanticIconName;
  title: string;
  summary?: JSX.Element;
  state?: ConversationDisclosureState;
  statusLabel?: string;
  open?: boolean;
  onToggle?: JSX.EventHandler<HTMLDetailsElement, Event>;
}

export function ConversationDisclosure(props: ConversationDisclosureProps): JSX.Element {
  return (
    <details
      class={`conversation-disclosure ${props.class ?? ""}`}
      data-state={props.state ?? "idle"}
      open={props.open || undefined}
      onToggle={props.onToggle}
    >
      <summary>
        <span class="disclosure-leading">
          <Icon name={props.icon} size={15} />
        </span>
        <span class="disclosure-title">{props.title}</span>
        <Show when={props.summary}>
          <span class="disclosure-separator" aria-hidden="true" />
          <span class="disclosure-summary">{props.summary}</span>
        </Show>
        <Show when={props.statusLabel}>
          <span class="disclosure-status">{props.statusLabel}</span>
        </Show>
        <Icon name="action.expand" size={13} class="disclosure-chevron" />
      </summary>
      <div class="disclosure-body">{props.children}</div>
    </details>
  );
}
