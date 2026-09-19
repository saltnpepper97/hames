import { createUniqueId, onCleanup, onMount } from "solid-js";
import type { JSX, ParentProps } from "solid-js";
import { Portal } from "solid-js/web";
import { CloseButton } from "./CloseButton";

interface DialogFrameProps extends ParentProps {
  eyebrow: string;
  title: string;
  class?: string;
  closeLabel?: string;
  footer: JSX.Element;
  onClose: () => void;
}

export function DialogFrame(props: DialogFrameProps) {
  const titleId = `dialog-${createUniqueId()}`;
  let frame: HTMLElement | undefined;
  let closeButton: HTMLButtonElement | undefined;
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === "Escape") {
      event.preventDefault();
      props.onClose();
      return;
    }
    if (event.key !== "Tab" || !frame) return;
    const focusable = [...frame.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )];
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  onMount(() => {
    document.addEventListener("keydown", onKeyDown);
    closeButton?.focus();
  });
  onCleanup(() => {
    document.removeEventListener("keydown", onKeyDown);
    previousFocus?.focus();
  });

  return (
    <Portal>
      <div class="dialog-backdrop" role="presentation" onMouseDown={(event) => {
        if (event.target === event.currentTarget) props.onClose();
      }}>
        <section
          ref={frame}
          class={`dialog-frame ${props.class ?? ""}`}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
        >
          <header class="dialog-header">
            <div>
              <span class="eyebrow">{props.eyebrow}</span>
              <h2 id={titleId}>{props.title}</h2>
            </div>
            <CloseButton
              ref={closeButton}
              class="dialog-close"
              aria-label={props.closeLabel ?? "Close dialog"}
              onClick={props.onClose}
            />
          </header>
          {props.children}
          <footer class="dialog-footer">{props.footer}</footer>
        </section>
      </div>
    </Portal>
  );
}
