import { createMemo, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export type SpinnerSize = "sm" | "md" | "lg" | "xl";

export interface SpinnerProps extends Omit<
  JSX.HTMLAttributes<HTMLSpanElement>,
  "aria-hidden" | "children" | "role"
> {
  size?: SpinnerSize | number;
  label?: string;
}

export function Spinner(props: SpinnerProps) {
  const [local, restProps] = splitProps(props, ["size", "label", "class", "style", "aria-label"]);
  const size = () => local.size ?? "md";
  const accessibleLabel = () => local.label ?? local["aria-label"];
  const style = createMemo<JSX.CSSProperties | string | undefined>(() => {
    if (typeof size() !== "number") return local.style;
    const customSize = `${size()}px`;
    if (typeof local.style === "string") {
      return `${local.style}; --_still-spinner-custom-size: ${customSize}`;
    }
    return { ...local.style, "--_still-spinner-custom-size": customSize };
  });

  return (
    <span
      {...restProps}
      class={`still-spinner ${local.class ?? ""}`}
      style={style()}
      data-component="spinner"
      data-size={typeof size() === "number" ? "custom" : size()}
      data-labelled={accessibleLabel() ? "" : undefined}
      role={accessibleLabel() ? "status" : undefined}
      aria-label={accessibleLabel()}
      aria-hidden={accessibleLabel() ? undefined : "true"}
    >
      <svg
        {...({ focusable: "false" } as Record<string, string>)}
        class="still-spinner__svg"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <g transform="rotate(45 12 12)">
          <rect class="still-spinner__track" x="4" y="4" width="16" height="16" rx="5" />
          <rect class="still-spinner__active" x="4" y="4" width="16" height="16" rx="5" />
        </g>
      </svg>
    </span>
  );
}
