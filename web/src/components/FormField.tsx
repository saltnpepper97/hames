import { createUniqueId, splitProps } from "solid-js";
import type { JSX } from "solid-js";
import { Select } from "./Select";
import type { SelectOption } from "./Select";
import { TextArea } from "./TextArea";

interface SharedFieldProps {
  label: string;
  helper?: string;
  error?: string;
}

export type { SelectOption } from "./Select";

export interface SelectFieldProps
  extends SharedFieldProps {
  id?: string;
  value?: string;
  options: readonly SelectOption[];
  placeholder?: string;
  disabled?: boolean;
  required?: boolean;
  name?: string;
  form?: string;
  class?: string;
  onValueChange: (value: string) => void;
}

export function SelectField(props: SelectFieldProps) {
  const [field, selectProps] = splitProps(props, [
    "id",
    "label",
    "helper",
    "error",
    "class",
    "options",
  ]);
  const id = field.id ?? `field-${createUniqueId()}`;
  const descriptionId = `${id}-description`;
  return (
    <div class={`form-field ${field.class ?? ""}`}>
      <label class="form-label" for={id}>{field.label}</label>
      <Select
        {...selectProps}
        id={id}
        options={field.options}
        ariaLabel={field.label}
        ariaInvalid={Boolean(field.error)}
        ariaDescribedBy={field.helper || field.error ? descriptionId : undefined}
      />
      {(field.error || field.helper) && (
        <span id={descriptionId} class="form-helper" classList={{ error: Boolean(field.error) }}>
          {field.error || field.helper}
        </span>
      )}
    </div>
  );
}

export interface TextFieldProps
  extends SharedFieldProps,
    Omit<JSX.InputHTMLAttributes<HTMLInputElement>, "id"> {
  id?: string;
}

export function TextField(props: TextFieldProps) {
  const [field, inputProps] = splitProps(props, ["id", "label", "helper", "error", "class"]);
  const id = field.id ?? `field-${createUniqueId()}`;
  const descriptionId = `${id}-description`;
  return (
    <div class={`form-field ${field.class ?? ""}`}>
      <label class="form-label" for={id}>{field.label}</label>
      <input
        {...inputProps}
        id={id}
        class="text-input"
        data-component="text-input"
        aria-invalid={field.error ? true : undefined}
        aria-describedby={field.helper || field.error ? descriptionId : undefined}
      />
      {(field.error || field.helper) && (
        <span id={descriptionId} class="form-helper" classList={{ error: Boolean(field.error) }}>
          {field.error || field.helper}
        </span>
      )}
    </div>
  );
}

export interface TextAreaFieldProps
  extends SharedFieldProps,
    Omit<JSX.TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  id?: string;
  code?: boolean;
  resizable?: boolean;
}

export function TextAreaField(props: TextAreaFieldProps) {
  const [field, textareaProps] = splitProps(props, [
    "id", "label", "helper", "error", "class", "code", "resizable",
  ]);
  const id = field.id ?? `field-${createUniqueId()}`;
  const descriptionId = `${id}-description`;
  return (
    <div class={`form-field ${field.class ?? ""}`}>
      <label class="form-label" for={id}>{field.label}</label>
      <TextArea
        {...textareaProps}
        id={id}
        class="text-area-input"
        classList={{ code: field.code }}
        resize={field.resizable === false ? "none" : "vertical"}
        resizeLabel={`Resize ${field.label}`}
        aria-invalid={field.error ? true : undefined}
        aria-describedby={field.helper || field.error ? descriptionId : undefined}
      />
      {(field.error || field.helper) && (
        <span id={descriptionId} class="form-helper" classList={{ error: Boolean(field.error) }}>
          {field.error || field.helper}
        </span>
      )}
    </div>
  );
}
