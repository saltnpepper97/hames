import { Show, createSignal, createUniqueId } from "solid-js";
import type { JSX } from "solid-js";
import { Button } from "./Button";
import { Markdown } from "./Markdown";
import { TextArea } from "./TextArea";

interface MarkdownEditorProps {
  label: string;
  value: string;
  rows?: number;
  helper?: string;
  error?: string;
  resizable?: boolean;
  onInput: JSX.EventHandlerUnion<HTMLTextAreaElement, InputEvent>;
}

export function MarkdownEditor(props: MarkdownEditorProps) {
  const [mode, setMode] = createSignal<"edit" | "preview">("edit");
  const id = `markdown-editor-${createUniqueId()}`;
  const descriptionId = `${id}-description`;

  return (
    <div class="markdown-editor">
      <div class="markdown-editor-toolbar">
        <span class="form-label">{props.label}</span>
        <div class="markdown-editor-tabs" role="tablist" aria-label={`${props.label} view`}>
          <Button
            variant="bare"
            size="small"
            class="markdown-editor-tab"
            classList={{ active: mode() === "edit" }}
            role="tab"
            aria-selected={mode() === "edit"}
            onClick={() => setMode("edit")}
          >
            Edit
          </Button>
          <Button
            variant="bare"
            size="small"
            class="markdown-editor-tab"
            classList={{ active: mode() === "preview" }}
            role="tab"
            aria-selected={mode() === "preview"}
            onClick={() => setMode("preview")}
          >
            Preview
          </Button>
        </div>
      </div>

      <Show
        when={mode() === "edit"}
        fallback={
          <div class="markdown-editor-preview" role="tabpanel" aria-label={`${props.label} preview`}>
            <Show
              when={props.value.trim()}
              fallback={<p class="markdown-editor-empty">Nothing to preview.</p>}
            >
              <Markdown content={props.value} />
            </Show>
          </div>
        }
      >
        <TextArea
          id={id}
          class="markdown-editor-input"
          value={props.value}
          rows={props.rows ?? 14}
          resize={props.resizable === false ? "none" : "vertical"}
          resizeLabel={`Resize ${props.label}`}
          aria-label={props.label}
          aria-invalid={props.error ? true : undefined}
          aria-describedby={props.helper || props.error ? descriptionId : undefined}
          onInput={props.onInput}
        />
      </Show>

      <Show when={props.error || props.helper}>
        <span
          id={descriptionId}
          class="form-helper markdown-editor-helper"
          classList={{ error: Boolean(props.error) }}
        >
          {props.error || props.helper}
        </span>
      </Show>
    </div>
  );
}
