import { Show, createUniqueId, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export type SwitchSize = "small" | "medium" | "large";
export type SwitchLabelPosition = "start" | "end";

export interface SwitchProps extends Omit<
  JSX.InputHTMLAttributes<HTMLInputElement>,
  "checked" | "children" | "class" | "onChange" | "role" | "size" | "type"
> {
  checked?: boolean;
  size?: SwitchSize;
  label?: JSX.Element;
  description?: JSX.Element;
  labelPosition?: SwitchLabelPosition;
  class?: string;
  onCheckedChange?: (checked: boolean, event: Event & { currentTarget: HTMLInputElement }) => void;
}

export function Switch(props: SwitchProps) {
  const [local, inputProps] = splitProps(props, [
    "checked",
    "size",
    "label",
    "description",
    "labelPosition",
    "class",
    "disabled",
    "id",
    "onCheckedChange",
    "aria-label",
    "aria-labelledby",
    "aria-describedby",
  ]);
  const generatedId = `switch-${createUniqueId()}`;
  const inputId = () => local.id ?? generatedId;
  const labelId = () => `${inputId()}-label`;
  const descriptionId = () => `${inputId()}-description`;
  const describedBy = () =>
    [local["aria-describedby"], local.description ? descriptionId() : undefined]
      .filter(Boolean)
      .join(" ") || undefined;

  return (
    <label
      class={`ui-switch ${local.class ?? ""}`}
      data-size={local.size ?? "medium"}
      data-checked={local.checked ? "" : undefined}
      data-disabled={local.disabled ? "" : undefined}
      data-label-position={local.labelPosition ?? "end"}
    >
      <span class="ui-switch-control">
        <input
          {...inputProps}
          id={inputId()}
          class="ui-switch-input"
          type="checkbox"
          role="switch"
          checked={local.checked}
          disabled={local.disabled}
          aria-label={local["aria-label"]}
          aria-labelledby={local["aria-labelledby"] ?? (local.label ? labelId() : undefined)}
          aria-describedby={describedBy()}
          data-checked={local.checked ? "" : undefined}
          data-disabled={local.disabled ? "" : undefined}
          onChange={(event) => local.onCheckedChange?.(event.currentTarget.checked, event)}
        />
        <span class="ui-switch-track" aria-hidden="true">
          <span class="ui-switch-thumb" />
        </span>
      </span>

      <Show when={local.label || local.description}>
        <span class="ui-switch-copy">
          <Show when={local.label}>
            <span class="ui-switch-label" id={labelId()}>{local.label}</span>
          </Show>
          <Show when={local.description}>
            <span class="ui-switch-description" id={descriptionId()}>{local.description}</span>
          </Show>
        </span>
      </Show>
    </label>
  );
}
