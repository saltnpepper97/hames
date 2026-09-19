import { Show, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export type BadgeVariant =
  | "neutral"
  | "outline"
  | "strong"
  | "destructive"
  | "info"
  | "success"
  | "warning"
  | "error"
  | "blue"
  | "cyan"
  | "green"
  | "orange"
  | "pink"
  | "purple"
  | "red"
  | "teal"
  | "yellow";

export type BadgeSize = "sm" | "md";

export interface BadgeProps extends JSX.HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant;
  size?: BadgeSize;
  start?: JSX.Element;
}

export function Badge(props: BadgeProps) {
  const [local, badgeProps] = splitProps(props, [
    "variant",
    "size",
    "start",
    "children",
    "class",
  ]);
  return (
    <span
      {...badgeProps}
      class={`ui-badge ${local.class ?? ""}`}
      data-component="badge"
      data-variant={local.variant ?? "neutral"}
      data-size={local.size ?? "md"}
      data-has-start={local.start ? "" : undefined}
    >
      <Show when={local.start}>
        <span class="ui-badge-start" data-slot="start" aria-hidden="true">{local.start}</span>
      </Show>
      <span class="ui-badge-label" data-part="label">{local.children}</span>
    </span>
  );
}
