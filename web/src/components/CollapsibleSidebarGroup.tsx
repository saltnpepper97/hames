import { Show, createSignal, createUniqueId } from "solid-js";
import type { JSX } from "solid-js";
import { Separator } from "./Separator";

interface CollapsibleSidebarGroupProps {
  label: string;
  meta?: JSX.Element;
  class?: string;
  defaultExpanded?: boolean;
  children: JSX.Element;
}

export function CollapsibleSidebarGroup(props: CollapsibleSidebarGroupProps) {
  const [expanded, setExpanded] = createSignal(props.defaultExpanded ?? true);
  const contentId = `sidebar-group-${createUniqueId()}`;

  return (
    <section class={`collapsible-sidebar-group ${props.class ?? ""}`}>
      <Separator
        label={props.label}
        meta={props.meta}
        class="collapsible-sidebar-separator"
        expanded={expanded()}
        controls={contentId}
        onToggle={() => setExpanded((current) => !current)}
      />
      <Show when={expanded()}>
        <div id={contentId} class="collapsible-sidebar-content">
          {props.children}
        </div>
      </Show>
    </section>
  );
}
