import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import type { JSX, ParentProps } from "solid-js";

interface DropdownSurfaceProps extends ParentProps {
  open: boolean;
  class: string;
  morph?: boolean;
  layout?: string;
  direction?: "forward" | "back";
  role?: JSX.HTMLAttributes<HTMLDivElement>["role"];
  ariaLabel?: string;
}

const EXIT_DURATION_MS = 130;

export function DropdownSurface(props: DropdownSurfaceProps) {
  const [present, setPresent] = createSignal(props.open);
  const [morphHeight, setMorphHeight] = createSignal<string>();
  let exitTimer: ReturnType<typeof setTimeout> | undefined;
  let surface: HTMLDivElement | undefined;
  let content: HTMLDivElement | undefined;
  let contentObserver: ResizeObserver | undefined;

  const measureContent = () => {
    if (!props.morph || !surface || !content) return;
    const styles = getComputedStyle(surface);
    const chrome = [
      styles.paddingTop,
      styles.paddingBottom,
      styles.borderTopWidth,
      styles.borderBottomWidth,
    ].reduce((total, value) => total + (Number.parseFloat(value) || 0), 0);
    const naturalHeight = Math.ceil(content.scrollHeight + chrome);
    const maxHeight = Number.parseFloat(styles.maxHeight);
    const height = Number.isFinite(maxHeight)
      ? Math.min(naturalHeight, maxHeight)
      : naturalHeight;
    if (height > 0) setMorphHeight(`${height}px`);
  };

  const observeContent = () => {
    contentObserver?.disconnect();
    if (!props.morph || !content) return;
    if (typeof ResizeObserver !== "undefined") {
      contentObserver = new ResizeObserver(measureContent);
      contentObserver.observe(content);
    }
    measureContent();
  };

  createEffect(() => {
    clearTimeout(exitTimer);
    if (props.open) {
      setPresent(true);
      return;
    }
    if (present()) {
      exitTimer = setTimeout(() => setPresent(false), EXIT_DURATION_MS);
    }
  });

  createEffect(() => {
    void present();
    void props.layout;
    if (props.morph) queueMicrotask(observeContent);
  });

  onCleanup(() => {
    clearTimeout(exitTimer);
    contentObserver?.disconnect();
  });

  return (
    <Show when={present()}>
      <div
        ref={surface}
        class={`dropdown-surface ${props.class}`}
        data-state={props.open ? "open" : "closed"}
        data-morph={props.morph ? "" : undefined}
        data-layout={props.layout}
        data-direction={props.direction}
        style={{ height: props.morph ? morphHeight() : undefined }}
        role={props.role}
        aria-label={props.ariaLabel}
        aria-hidden={props.open ? undefined : true}
      >
        <div ref={content} class="dropdown-surface-content">
          {props.children}
        </div>
      </div>
    </Show>
  );
}
