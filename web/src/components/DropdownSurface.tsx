import { Show, createEffect, createSignal, onCleanup } from "solid-js";
import type { JSX, ParentProps } from "solid-js";

interface DropdownSurfaceProps extends ParentProps {
  open: boolean;
  class: string;
  role?: JSX.HTMLAttributes<HTMLDivElement>["role"];
  ariaLabel?: string;
}

const EXIT_DURATION_MS = 130;

export function DropdownSurface(props: DropdownSurfaceProps) {
  const [present, setPresent] = createSignal(props.open);
  let exitTimer: ReturnType<typeof setTimeout> | undefined;

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

  onCleanup(() => clearTimeout(exitTimer));

  return (
    <Show when={present()}>
      <div
        class={`dropdown-surface ${props.class}`}
        data-state={props.open ? "open" : "closed"}
        role={props.role}
        aria-label={props.ariaLabel}
        aria-hidden={props.open ? undefined : true}
      >
        {props.children}
      </div>
    </Show>
  );
}
