import { Show, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "icon" | "choice" | "bare";
export type ButtonSize = "small" | "medium" | "large";

export interface ButtonProps extends JSX.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export function Button(props: ButtonProps) {
  const [local, buttonProps] = splitProps(props, [
    "variant",
    "size",
    "loading",
    "disabled",
    "class",
    "children",
    "type",
  ]);
  const variant = () => local.variant ?? "secondary";
  const size = () => local.size ?? "medium";

  return (
    <button
      {...buttonProps}
      type={local.type ?? "button"}
      class={`ui-button ui-button-${variant()} ui-button-${size()} ${local.class ?? ""}`}
      disabled={local.disabled || local.loading}
      aria-busy={local.loading || undefined}
    >
      <Show when={local.loading}>
        <span class="ui-button-spinner" aria-hidden="true" />
      </Show>
      {local.children}
    </button>
  );
}
