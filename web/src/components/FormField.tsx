import { createUniqueId, splitProps } from "solid-js";
import type { JSX } from "solid-js";

interface SharedFieldProps {
  label: string;
  helper?: string;
  error?: string;
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
    <label class={`form-field ${field.class ?? ""}`} for={id}>
      <span class="form-label">{field.label}</span>
      <input
        {...inputProps}
        id={id}
        class="text-input"
        aria-invalid={field.error ? true : undefined}
        aria-describedby={field.helper || field.error ? descriptionId : undefined}
      />
      {(field.error || field.helper) && (
        <span id={descriptionId} class="form-helper" classList={{ error: Boolean(field.error) }}>
          {field.error || field.helper}
        </span>
      )}
    </label>
  );
}

export interface TextAreaFieldProps
  extends SharedFieldProps,
    Omit<JSX.TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  id?: string;
  code?: boolean;
}

export function TextAreaField(props: TextAreaFieldProps) {
  const [field, textareaProps] = splitProps(props, [
    "id", "label", "helper", "error", "class", "code",
  ]);
  const id = field.id ?? `field-${createUniqueId()}`;
  const descriptionId = `${id}-description`;
  return (
    <label class={`form-field ${field.class ?? ""}`} for={id}>
      <span class="form-label">{field.label}</span>
      <textarea
        {...textareaProps}
        id={id}
        class="text-area-input"
        classList={{ code: field.code }}
        aria-invalid={field.error ? true : undefined}
        aria-describedby={field.helper || field.error ? descriptionId : undefined}
      />
      {(field.error || field.helper) && (
        <span id={descriptionId} class="form-helper" classList={{ error: Boolean(field.error) }}>
          {field.error || field.helper}
        </span>
      )}
    </label>
  );
}
