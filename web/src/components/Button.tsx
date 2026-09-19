import { Show, splitProps } from "solid-js";
import type { JSX } from "solid-js";
import { Spinner } from "./Spinner";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "outline"
  | "ghost"
  | "destructive"
  | "quiet"
  | "icon"
  | "choice"
  | "bare";
export type ButtonSize = "sm" | "md" | "lg" | "small" | "medium" | "large";

export interface ButtonProps extends JSX.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  fullWidth?: boolean;
  start?: JSX.Element;
  end?: JSX.Element;
}

export function Button(props: ButtonProps) {
  const [local, buttonProps] = splitProps(props, [
    "variant",
    "size",
    "loading",
    "fullWidth",
    "start",
    "end",
    "disabled",
    "class",
    "children",
    "type",
  ]);
  const variant = () => local.variant ?? "secondary";
  const size = () => local.size ?? "md";
  const spinnerSize = () => {
    if (size() === "small" || size() === "sm") return "sm";
    if (size() === "large" || size() === "lg") return "lg";
    return "md";
  };

  return (
    <button
      {...buttonProps}
      type={local.type ?? "button"}
      class={`ui-button ui-button-${variant()} ui-button-${size()} ${local.class ?? ""}`}
      disabled={local.disabled || local.loading}
      aria-busy={local.loading || undefined}
      data-component="button"
      data-variant={variant()}
      data-size={size()}
      data-loading={local.loading ? "" : undefined}
      data-full-width={local.fullWidth ? "" : undefined}
    >
      <Show when={local.loading}>
        <Spinner class="ui-button-spinner" size={spinnerSize()} />
      </Show>
      <Show when={local.start}>
        <span class="ui-button-slot" data-slot="start">{local.start}</span>
      </Show>
      {local.children}
      <Show when={local.end}>
        <span class="ui-button-slot" data-slot="end">{local.end}</span>
      </Show>
    </button>
  );
}
