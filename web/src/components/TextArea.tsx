import { onCleanup, splitProps } from "solid-js";
import type { JSX } from "solid-js";

export interface TextAreaProps
  extends Omit<JSX.TextareaHTMLAttributes<HTMLTextAreaElement>, "ref"> {
  resize?: "none" | "vertical";
  resizeLabel?: string;
  elementRef?: (element: HTMLTextAreaElement) => void;
}

const DEFAULT_MIN_HEIGHT = 44;

function numericStyle(element: HTMLElement, property: "minHeight" | "maxHeight"): number {
  const value = Number.parseFloat(getComputedStyle(element)[property]);
  return Number.isFinite(value) ? value : property === "minHeight" ? DEFAULT_MIN_HEIGHT : Infinity;
}

export function TextArea(props: TextAreaProps) {
  const [local, textareaProps] = splitProps(props, [
    "resize",
    "resizeLabel",
    "elementRef",
    "class",
  ]);
  const resizable = local.resize === "vertical" && !textareaProps.disabled && !textareaProps.readOnly;
  let textarea!: HTMLTextAreaElement;
  let activePointer: number | undefined;
  let originY = 0;
  let originHeight = 0;
  let previousCursor = "";
  let previousUserSelect = "";

  const setHeight = (height: number) => {
    const minimum = numericStyle(textarea, "minHeight");
    const maximum = Math.min(numericStyle(textarea, "maxHeight"), window.innerHeight * 0.75);
    textarea.style.height = `${Math.round(Math.max(minimum, Math.min(height, maximum)))}px`;
  };

  const finishResize = () => {
    if (activePointer === undefined) return;
    activePointer = undefined;
    document.documentElement.style.cursor = previousCursor;
    document.documentElement.style.userSelect = previousUserSelect;
  };

  const startResize: JSX.EventHandlerUnion<HTMLButtonElement, PointerEvent> = (event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    activePointer = event.pointerId;
    originY = event.clientY;
    originHeight = textarea.getBoundingClientRect().height;
    previousCursor = document.documentElement.style.cursor;
    previousUserSelect = document.documentElement.style.userSelect;
    document.documentElement.style.cursor = "ns-resize";
    document.documentElement.style.userSelect = "none";
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };

  const resize: JSX.EventHandlerUnion<HTMLButtonElement, PointerEvent> = (event) => {
    if (activePointer !== event.pointerId) return;
    setHeight(originHeight + event.clientY - originY);
  };

  const resizeWithKeyboard: JSX.EventHandlerUnion<HTMLButtonElement, KeyboardEvent> = (event) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const direction = event.key === "ArrowDown" ? 1 : -1;
    setHeight(textarea.getBoundingClientRect().height + direction * (event.shiftKey ? 32 : 8));
  };

  onCleanup(finishResize);

  const control = (
    <textarea
      {...textareaProps}
      ref={(element) => {
        textarea = element;
        local.elementRef?.(element);
      }}
      class={local.class ?? ""}
      data-component="text-area"
      data-resize={resizable ? "vertical" : "none"}
    />
  );

  if (!resizable) return control;
  return (
    <div class="text-area-control" data-resizable="vertical">
      {control}
      <button
        type="button"
        class="text-area-resize-handle"
        aria-label={local.resizeLabel ?? "Resize text area"}
        aria-controls={typeof textareaProps.id === "string" ? textareaProps.id : undefined}
        title={local.resizeLabel ?? "Drag to resize"}
        onPointerDown={startResize}
        onPointerMove={resize}
        onPointerUp={() => finishResize()}
        onPointerCancel={() => finishResize()}
        onLostPointerCapture={() => finishResize()}
        onKeyDown={resizeWithKeyboard}
      >
        <svg viewBox="0 0 16 16" aria-hidden="true">
          <path d="M14 4.5 4.5 14M14 8.75 8.75 14M14 13 13 14" />
        </svg>
      </button>
    </div>
  );
}
