import { createMemo, createSignal, onCleanup, onMount } from "solid-js";
import type { ParentProps } from "solid-js";

interface ResizableTableProps extends ParentProps {
  ariaLabel: string;
  class?: string;
  defaultFirstColumn?: number;
  minFirstColumn?: number;
  minSecondColumn?: number;
  storageKey?: string;
}

function storedWidth(key: string | undefined, fallback: number): number {
  if (!key) return fallback;
  try {
    const value = Number.parseFloat(localStorage.getItem(key) ?? "");
    return Number.isFinite(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

export function ResizableTable(props: ResizableTableProps) {
  const defaultWidth = () => props.defaultFirstColumn ?? 132;
  const minimumFirst = () => props.minFirstColumn ?? 88;
  const minimumSecond = () => props.minSecondColumn ?? 160;
  const [requestedWidth, setRequestedWidth] = createSignal(
    storedWidth(props.storageKey, defaultWidth()),
  );
  const [tableWidth, setTableWidth] = createSignal(0);
  const [dragging, setDragging] = createSignal(false);
  let table!: HTMLDivElement;
  let observer: ResizeObserver | undefined;
  let drag: { pointerId: number; startX: number; startWidth: number } | undefined;

  const maximumFirst = createMemo(() => tableWidth() > 0
    ? Math.max(minimumFirst(), tableWidth() - minimumSecond())
    : Math.max(minimumFirst(), defaultWidth()),
  );
  const constrain = (value: number) => Math.round(
    Math.min(maximumFirst(), Math.max(minimumFirst(), value)),
  );
  const firstWidth = createMemo(() => constrain(requestedWidth()));
  const updateWidth = (value: number) => {
    const next = constrain(value);
    setRequestedWidth(next);
    if (props.storageKey) {
      try {
        localStorage.setItem(props.storageKey, String(next));
      } catch {
        // Storage is optional; resizing remains local to this view when unavailable.
      }
    }
  };
  const measure = () => {
    setTableWidth(table.clientWidth);
  };

  const beginDrag = (event: PointerEvent & { currentTarget: HTMLDivElement }) => {
    event.preventDefault();
    drag = { pointerId: event.pointerId, startX: event.clientX, startWidth: firstWidth() };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setDragging(true);
  };
  const moveDrag = (event: PointerEvent) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    updateWidth(drag.startWidth + event.clientX - drag.startX);
  };
  const endDrag = (event: PointerEvent & { currentTarget: HTMLDivElement }) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    drag = undefined;
    setDragging(false);
  };
  const resizeFromKeyboard = (event: KeyboardEvent) => {
    const step = event.shiftKey ? 32 : 12;
    if (event.key === "ArrowLeft") updateWidth(firstWidth() - step);
    else if (event.key === "ArrowRight") updateWidth(firstWidth() + step);
    else if (event.key === "Home") updateWidth(minimumFirst());
    else if (event.key === "End") updateWidth(maximumFirst());
    else return;
    event.preventDefault();
  };

  onMount(() => {
    measure();
    window.addEventListener("resize", measure);
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(measure);
      observer.observe(table);
    }
  });
  onCleanup(() => {
    observer?.disconnect();
    window.removeEventListener("resize", measure);
  });

  return (
    <div
      ref={table}
      class={`resizable-table ${props.class ?? ""}`}
      classList={{ dragging: dragging() }}
      role="table"
      aria-label={props.ariaLabel}
      style={{ "--resizable-first-column": `${firstWidth()}px` }}
    >
      {props.children}
      <div
        class="resizable-table-divider"
        role="separator"
        tabIndex={0}
        aria-label="Resize Event and Content columns"
        aria-orientation="vertical"
        aria-valuemin={minimumFirst()}
        aria-valuemax={maximumFirst()}
        aria-valuenow={firstWidth()}
        title="Drag to resize columns. Double-click to reset."
        onPointerDown={beginDrag}
        onPointerMove={moveDrag}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onLostPointerCapture={() => {
          drag = undefined;
          setDragging(false);
        }}
        onDblClick={() => updateWidth(defaultWidth())}
        onKeyDown={resizeFromKeyboard}
      >
        <span aria-hidden="true" />
      </div>
    </div>
  );
}
