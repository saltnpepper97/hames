import { Show, createEffect, createUniqueId, splitProps } from "solid-js";
import type { JSX } from "solid-js";
import { Icon } from "../shell/icons";

export interface CheckboxProps extends Omit<
  JSX.InputHTMLAttributes<HTMLInputElement>,
  "checked" | "children" | "class" | "onChange" | "type"
> {
  checked?: boolean;
  indeterminate?: boolean;
  label?: JSX.Element;
  description?: JSX.Element;
  class?: string;
  onCheckedChange?: (checked: boolean, event: Event & { currentTarget: HTMLInputElement }) => void;
}

export function Checkbox(props: CheckboxProps) {
  const [local, inputProps] = splitProps(props, [
    "checked",
    "indeterminate",
    "label",
    "description",
    "class",
    "disabled",
    "required",
    "id",
    "onCheckedChange",
    "aria-label",
    "aria-labelledby",
    "aria-describedby",
  ]);
  const generatedId = `checkbox-${createUniqueId()}`;
  const inputId = () => local.id ?? generatedId;
  const labelId = () => `${inputId()}-label`;
  const descriptionId = () => `${inputId()}-description`;
  const describedBy = () =>
    [local["aria-describedby"], local.description ? descriptionId() : undefined]
      .filter(Boolean)
      .join(" ") || undefined;
  const state = () => local.indeterminate ? "indeterminate" : local.checked ? "checked" : "unchecked";
  let input: HTMLInputElement | undefined;

  createEffect(() => {
    if (input) input.indeterminate = Boolean(local.indeterminate);
  });

  return (
    <label
      class={`ui-checkbox ${local.class ?? ""}`}
      data-state={state()}
      data-disabled={local.disabled ? "" : undefined}
      data-required={local.required ? "" : undefined}
    >
      <span class="ui-checkbox-control">
        <input
          {...inputProps}
          ref={input}
          id={inputId()}
          class="ui-checkbox-input"
          type="checkbox"
          checked={local.checked}
          disabled={local.disabled}
          required={local.required}
          aria-label={local["aria-label"]}
          aria-labelledby={local["aria-labelledby"] ?? (local.label ? labelId() : undefined)}
          aria-describedby={describedBy()}
          data-state={state()}
          data-disabled={local.disabled ? "" : undefined}
          onChange={(event) => local.onCheckedChange?.(event.currentTarget.checked, event)}
        />
        <span class="ui-checkbox-box" aria-hidden="true">
          <span class="ui-checkbox-indicator">
            <Icon name="action.selected" size={13} />
          </span>
        </span>
      </span>

      <Show when={local.label || local.description}>
        <span class="ui-checkbox-copy">
          <Show when={local.label}>
            <span class="ui-checkbox-label" id={labelId()}>
              {local.label}
              <Show when={local.required}><span class="ui-checkbox-required" aria-hidden="true">*</span></Show>
            </span>
          </Show>
          <Show when={local.description}>
            <span class="ui-checkbox-description" id={descriptionId()}>{local.description}</span>
          </Show>
        </span>
      </Show>
    </label>
  );
}
